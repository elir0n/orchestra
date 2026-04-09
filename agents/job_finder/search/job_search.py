from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported sites registry
# ---------------------------------------------------------------------------

SUPPORTED_SITES: dict[str, str] = {
    # Israeli tech-focused
    "hiremetech":  "hiremetech.com",
    "comeet":      "comeet.com/jobs",
    # Israeli general (has large tech sections)
    "drushim":     "drushim.co.il",
    "alljobs":     "alljobs.co.il",
    "jobmaster":   "jobmaster.co.il",
    # Global startup / tech ATS
    "wellfound":   "wellfound.com/jobs",
    "greenhouse":  "boards.greenhouse.io",
    "lever":       "jobs.lever.co",
    "ashbyhq":     "jobs.ashbyhq.com",
    "workable":    "apply.workable.com",
}

ALL_SITES: list[str] = list(SUPPORTED_SITES.keys())


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class JobPosting:
    title: str
    company: str
    location: str
    url: str
    description: str       # full or partial job description
    job_type: str | None   # "full-time" / "part-time" / "contract"
    is_startup: bool       # heuristic from description + title


# ---------------------------------------------------------------------------
# Tech-job filter
# ---------------------------------------------------------------------------

_TECH_JOB_RE = re.compile(
    r"\b("
    r"software|developer|engineer|programmer|backend|front.?end|full.?stack|"
    r"devops|sre|platform|infrastructure|cloud|cyber|security|"
    r"data\s+scientist|data\s+engineer|machine\s+learning|ml\s+engineer|"
    r"ai\s+engineer|embedded|firmware|mobile|ios|android|"
    r"tech\s+lead|architect|cto|qa\s+engineer|test\s+engineer|"
    r"python|java(?:script)?|typescript|golang|rust|c\+\+"
    r")\b",
    re.IGNORECASE,
)


def _is_tech_job(title: str, description: str) -> bool:
    """Return True only if the posting is clearly a software/tech role."""
    return bool(_TECH_JOB_RE.search(title) or _TECH_JOB_RE.search(description[:600]))


# ---------------------------------------------------------------------------
# Individual-job-URL check  (skip category / search listing pages)
# ---------------------------------------------------------------------------

_LISTING_PATH_RE = re.compile(
    r"/(jobs?|careers?|positions?|openings?|search|category|cat\d+|browse|find)"
    r"/?(\?.*)?$",
    re.IGNORECASE,
)


def _is_individual_job_url(url: str) -> bool:
    """Return False when the URL looks like a listing/search page, not a single job."""
    path = urlparse(url).path
    return not bool(_LISTING_PATH_RE.fullmatch(path.rstrip("/")))


# ---------------------------------------------------------------------------
# Startup heuristic
# ---------------------------------------------------------------------------

_STARTUP_KEYWORDS: list[tuple[str, int]] = [
    (r"startup", 3),
    (r"start.up", 3),
    (r"early.stage", 2),
    (r"\bseed\b", 2),
    (r"series [ab]", 2),
    (r"pre.ipo", 2),
    (r"founding team", 3),
    (r"founding engineer", 3),
    (r"\bequity\b", 1),
    (r"stock option", 1),
    (r"\besop\b", 2),
    (r"scale.up", 1),
    (r"growth.stage", 1),
    (r"venture.backed", 2),
    (r"vc.backed", 2),
]

_STARTUP_THRESHOLD = 3


def _is_startup(text: str) -> bool:
    lower = text.lower()
    score = 0
    for pattern, weight in _STARTUP_KEYWORDS:
        if re.search(pattern, lower):
            score += weight
        if score >= _STARTUP_THRESHOLD:
            return True
    return False


# ---------------------------------------------------------------------------
# Description enrichment
# ---------------------------------------------------------------------------

_MIN_DESCRIPTION_LENGTH = 300
_FETCH_TIMEOUT = 8


def _fetch_description(url: str, tavily_content: str) -> str:
    if len(tavily_content) >= _MIN_DESCRIPTION_LENGTH:
        return tavily_content

    try:
        resp = requests.get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-finder-bot/1.0)"},
        )
        resp.raise_for_status()
        from bs4 import BeautifulSoup  # type: ignore[import]
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        cleaned = "\n".join(lines)
        return cleaned[:8000] if cleaned else tavily_content
    except Exception as exc:
        logger.debug(f"Failed to fetch {url}: {exc}")
        return tavily_content


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

def _extract_company(title: str, url: str, snippet: str) -> str:
    match = re.search(r"\bat\s+([A-Z][^\|\-\n]{2,40})", title)
    if match:
        return match.group(1).strip().rstrip(".,")

    match = re.search(
        r"^([A-Z][A-Za-z0-9 &\-]{2,40})\s+is\s+(?:looking|hiring|seeking)",
        snippet,
    )
    if match:
        return match.group(1).strip()

    try:
        hostname = urlparse(url).hostname or ""
        parts = hostname.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
    except Exception:
        pass

    return "Unknown"


def _detect_job_type(text: str, requested: str) -> str:
    lower = text.lower()
    if re.search(r"part.time|משרה חלקית", lower):
        return "part-time"
    if re.search(r"contract|freelance", lower):
        return "contract"
    if re.search(r"full.time", lower):
        return "full-time"
    return requested


def _norm_url(url: str) -> str:
    parsed = urlparse(url.lower())
    return parsed.netloc + parsed.path.rstrip("/")


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

# Added to general boards when no specific role is given, to focus on tech.
_TECH_BROADENER = (
    '(software OR developer OR engineer OR devops OR backend OR frontend '
    'OR "data scientist" OR "machine learning" OR embedded OR cyber)'
)


def _build_queries(
    role: str,
    location: str,
    job_types: list[str],
    startup: bool,
    remote: bool = False,
    sites: list[str] | None = None,
) -> list[str]:
    if sites is None:
        sites = ALL_SITES

    startup_suffix = " startup" if startup else ""

    type_terms: list[str] = []
    for jt in job_types:
        if jt == "part-time":
            type_terms += ['"part time"', '"משרה חלקית"']
        elif jt == "contract":
            type_terms += ['"contract"', '"freelance"']
    type_suffix = (" " + " OR ".join(type_terms)) if type_terms else ""

    remote_suffix = ' remote OR "work from home"' if remote else ""
    base = f"{startup_suffix}{type_suffix}{remote_suffix}"

    role_term = f'"{role}" ' if role else ""
    # When no role given, broaden to tech-only on general boards
    tech_filter = "" if role else f"{_TECH_BROADENER} "

    year = datetime.date.today().year
    queries: list[str] = []

    for site_key in sites:
        domain = SUPPORTED_SITES.get(site_key)
        if not domain:
            logger.warning(f"Unknown site key '{site_key}' — skipping.")
            continue

        if site_key == "hiremetech":
            # Student-first, then junior — this board is Israel tech only by nature
            queries.append(f'site:{domain} {role_term}{tech_filter}student OR internship{base}')
            queries.append(f'site:{domain} {role_term}{tech_filter}junior OR "entry level"{base}')
        elif site_key in ("comeet", "greenhouse", "lever", "ashbyhq", "workable"):
            # ATS sites: add location to narrow to individual postings
            queries.append(f'site:{domain} {role_term}{tech_filter}"{location}"{base}')
        else:
            # General boards: drushim, alljobs, jobmaster, wellfound
            queries.append(f'site:{domain} {role_term}{tech_filter}{base}')

    # Fallback broad web query (always appended)
    queries.append(
        f'{role_term}{tech_filter}"{location}" job hiring {year} -site:linkedin.com{base}'
    )

    return queries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def search_jobs(
    role: str,
    api_key: str,
    location: str = "Israel",
    job_types: list[str] | None = None,
    startup: bool = False,
    max_jobs: int = 5,
    remote: bool = False,
    sites: list[str] | None = None,
) -> list[JobPosting]:
    from tavily import TavilyClient

    if job_types is None:
        job_types = ["full-time"]

    client = TavilyClient(api_key=api_key)
    queries = _build_queries(role, location, job_types, startup, remote, sites)

    seen_urls: set[str] = set()
    seen_company_title: set[str] = set()
    postings: list[JobPosting] = []

    for query in queries:
        if len(postings) >= max_jobs:
            break

        logger.info(f"Tavily query: {query}")
        try:
            response = client.search(query=query, search_depth="basic", max_results=10)
        except Exception as exc:
            logger.warning(f"Tavily search failed for '{query}': {exc}")
            continue

        for result in response.get("results", []):
            if len(postings) >= max_jobs:
                break

            url = result.get("url", "")
            title = result.get("title", "")
            snippet = result.get("content", "")

            # Skip listing / search pages — we need individual job descriptions
            if not _is_individual_job_url(url):
                logger.debug(f"Skipping listing page: {url}")
                continue

            norm = _norm_url(url)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)

            description = _fetch_description(url, snippet)
            company = _extract_company(title, url, description)
            detected_type = _detect_job_type(description, job_types[0])
            is_startup_flag = _is_startup(description + " " + title)

            # Skip non-tech postings
            if not _is_tech_job(title, description):
                logger.debug(f"Skipping non-tech posting: {title}")
                continue

            if startup and not is_startup_flag:
                logger.debug(f"Skipping non-startup: {title}")
                continue

            dedup_key = f"{company.lower()}|{title.lower()[:60]}"
            if dedup_key in seen_company_title:
                continue
            seen_company_title.add(dedup_key)

            posting = JobPosting(
                title=title,
                company=company,
                location=location,
                url=url,
                description=description,
                job_type=detected_type,
                is_startup=is_startup_flag,
            )
            postings.append(posting)
            logger.info(f"  Found: {title} at {company} (startup={is_startup_flag})")

    logger.info(f"Total jobs collected: {len(postings)}")
    return postings
