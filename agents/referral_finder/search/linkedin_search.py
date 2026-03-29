from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Person:
    name: str
    first_name: str
    last_name: str
    linkedin_url: str
    company: str
    role_hint: str  # extracted from the search snippet


def _parse_name(title: str) -> tuple[str, str, str] | None:
    """
    Parse LinkedIn result title formats:
      "First Last - Title at Company | LinkedIn"
      "First Last | Title | LinkedIn"
      "First Last - LinkedIn"

    Returns (full_name, first_name, last_name) or None if parsing fails.
    """
    # Strip trailing " | LinkedIn" or "- LinkedIn"
    title = re.sub(r"\s*[\|\-]\s*LinkedIn.*$", "", title, flags=re.IGNORECASE).strip()

    # Split on " - " or " | " to isolate the name part
    parts = re.split(r"\s*[\-\|]\s*", title)
    if not parts:
        return None

    name = parts[0].strip()
    if not name or len(name) < 3:
        return None

    # Split name into first / last (take first word as first_name, rest as last_name)
    name_parts = name.split()
    if len(name_parts) < 2:
        return None

    first_name = name_parts[0]
    last_name = name_parts[-1]
    return name, first_name, last_name


def _extract_role_hint(snippet: str, title: str) -> str:
    """Try to extract the person's role from the snippet or title."""
    # LinkedIn title format: "Name - Role at Company"
    match = re.search(r"\s*-\s*(.+?)\s+at\s+", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Fallback: use the first sentence of the snippet
    return snippet[:80].strip() if snippet else ""


def find_candidates(
    company: str,
    roles: list[str],
    api_key: str,
    max_results: int = 15,
    location: str = "Israel",
) -> list[Person]:
    """
    Search LinkedIn profiles via Tavily for people at `company` in any of the given `roles`,
    filtered to a specific location.
    Returns a deduplicated list of Person objects.
    """
    from tavily import TavilyClient

    client = TavilyClient(api_key=api_key)
    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    candidates: list[Person] = []

    for role in roles:
        if len(candidates) >= max_results:
            break

        query = f'site:linkedin.com/in "{company}" "{role}" "{location}"'
        logger.info(f"Searching: {query}")

        try:
            response = client.search(
                query=query,
                search_depth="basic",
                max_results=10,
                include_domains=["linkedin.com"],
            )
        except Exception as exc:
            logger.warning(f"Tavily search failed for role '{role}': {exc}")
            continue

        results = response.get("results", [])
        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            snippet = result.get("content", "")

            # Only process linkedin.com/in/ profile URLs
            if "linkedin.com/in/" not in url:
                continue

            # Deduplicate by URL and name
            if url in seen_urls:
                continue

            parsed = _parse_name(title)
            if parsed is None:
                logger.debug(f"Could not parse name from title: {title!r}")
                continue

            full_name, first_name, last_name = parsed
            name_key = full_name.lower()
            if name_key in seen_names:
                continue

            seen_urls.add(url)
            seen_names.add(name_key)

            role_hint = _extract_role_hint(snippet, title)
            candidates.append(
                Person(
                    name=full_name,
                    first_name=first_name,
                    last_name=last_name,
                    linkedin_url=url,
                    company=company,
                    role_hint=role_hint,
                )
            )
            logger.info(f"  Found: {full_name} — {role_hint}")

            if len(candidates) >= max_results:
                break

    logger.info(f"Total candidates found: {len(candidates)}")
    return candidates
