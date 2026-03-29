from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


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

def _build_queries(role: str, location: str, job_type: str, startup: bool) -> list[str]:
    startup_suffix = " startup" if startup else ""
    type_suffix = ""
    if job_type == "part-time":
        type_suffix = ' "part time" OR "משרה חלקית"'
    elif job_type == "contract":
        type_suffix = ' "contract" OR "freelance"'

    year = datetime.date.today().year

    return [
        f'site:drushim.co.il "{role}"{startup_suffix}{type_suffix}',
        f'site:alljobs.co.il "{role}"{startup_suffix}{type_suffix}',
        f'site:jobmaster.co.il "{role}"{startup_suffix}{type_suffix}',
        f'"{role}" "{location}" job hiring {year} -site:linkedin.com{startup_suffix}{type_suffix}',
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def search_jobs(
    role: str,
    api_key: str,
    location: str = "Israel",
    job_type: str = "full-time",
    startup: bool = False,
    max_jobs: int = 5,
) -> list[JobPosting]:
    from tavily import TavilyClient

    client = TavilyClient(api_key=api_key)
    queries = _build_queries(role, location, job_type, startup)

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

            norm = _norm_url(url)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)

            description = _fetch_description(url, snippet)
            company = _extract_company(title, url, description)
            detected_type = _detect_job_type(description, job_type)
            is_startup = _is_startup(description + " " + title)

            if startup and not is_startup:
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
                is_startup=is_startup,
            )
            postings.append(posting)
            logger.info(f"  Found: {title} at {company} (startup={is_startup})")

    logger.info(f"Total jobs collected: {len(postings)}")
    return postings
