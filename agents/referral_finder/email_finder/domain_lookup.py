from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

HUNTER_DOMAIN_SEARCH = "https://api.hunter.io/v2/domain-search"


def get_company_domain_and_pattern(company: str) -> tuple[str | None, str | None]:
    """
    Use Hunter.io's free domain-search endpoint to find:
      - The company's email domain (e.g. "stripe.com")
      - The most common email pattern (e.g. "{first}.{last}@domain.com")

    This endpoint does NOT count toward the 25/month email-finder cap.
    Returns (domain, pattern) where pattern uses our {first}/{last} token format,
    or (None, None) if lookup fails or no data is found.
    """
    try:
        resp = requests.get(
            HUNTER_DOMAIN_SEARCH,
            params={"company": company, "limit": 1},
            timeout=10,
        )
        if resp.status_code == 401:
            logger.debug("Hunter domain search: no API key provided, skipping")
            return None, None
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json().get("data", {})
    except Exception as exc:
        logger.debug(f"Hunter domain search failed for {company!r}: {exc}")
        return None, None

    domain = data.get("domain")
    if not domain:
        return None, None

    # Hunter returns patterns like "first.last", "first", "flast", etc.
    raw_pattern = data.get("pattern")
    if not raw_pattern:
        return domain, None

    hunter_pattern = _hunter_pattern_to_template(raw_pattern)
    return domain, hunter_pattern


def _hunter_pattern_to_template(pattern: str) -> str:
    """
    Convert Hunter.io pattern strings to our {first}/{last} template format.
    Hunter patterns:
      "first.last"  → "{first}.{last}"
      "flast"       → "{f}{last}"
      "firstl"      → "{first}{l}"
      "first"       → "{first}"
      "last"        → "{last}"
    """
    mapping = {
        "first.last": "{first}.{last}",
        "firstlast":  "{first}{last}",
        "first_last": "{first}_{last}",
        "last.first": "{last}.{first}",
        "flast":      "{f}{last}",
        "first":      "{first}",
        "last":       "{last}",
        "f.last":     "{f}.{last}",
        "firstl":     "{first}{l}",
    }
    return mapping.get(pattern, f"{{{pattern}}}")
