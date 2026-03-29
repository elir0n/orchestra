from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# In-memory cache per run: company_domain → pattern string (e.g. "{first}.{last}")
_cache: dict[str, str] = {}

# company name (lowercase) → domain (e.g. "stripe" → "stripe.com")
_company_domain_map: dict[str, str] = {}

# Optional persistent cache file
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "email_patterns.json")


def _load_persistent() -> None:
    """Load saved patterns from disk into memory on first use."""
    global _cache, _company_domain_map
    if _cache:
        return
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE) as f:
                data = json.load(f)
            _cache.update(data.get("patterns", {}))
            _company_domain_map.update(data.get("company_domains", {}))
            logger.debug(f"Loaded {len(_cache)} email patterns from cache")
        except Exception:
            pass


def get_pattern(domain: str) -> str | None:
    _load_persistent()
    return _cache.get(domain)


def get_domain_for_company(company: str) -> str | None:
    _load_persistent()
    return _company_domain_map.get(company.lower())


def save_pattern(domain: str, pattern: str, company: str | None = None) -> None:
    _load_persistent()
    _cache[domain] = pattern
    if company:
        _company_domain_map[company.lower()] = domain
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump({"patterns": _cache, "company_domains": _company_domain_map}, f, indent=2)
    except Exception as exc:
        logger.warning(f"Could not persist email pattern cache: {exc}")


def apply_pattern(pattern: str, first: str, last: str) -> str:
    """
    Apply a pattern like '{first}.{last}' to produce an email username.
    Supported tokens: {first}, {last}, {f} (first initial), {l} (last initial)
    """
    return (
        pattern
        .replace("{first}", first.lower())
        .replace("{last}", last.lower())
        .replace("{f}", first[0].lower() if first else "")
        .replace("{l}", last[0].lower() if last else "")
    )


def email_to_pattern(email: str, first: str, last: str) -> str | None:
    """
    Reverse-engineer the pattern from a known email address.
    E.g. alex.chen@stripe.com → '{first}.{last}'
    """
    if "@" not in email:
        return None
    username = email.split("@")[0].lower()
    f, l = first.lower(), last.lower()

    patterns = [
        (f"{f}.{l}", "{first}.{last}"),
        (f"{f}{l}", "{first}{last}"),
        (f"{f[0]}{l}", "{f}{last}"),
        (f"{f[0]}.{l}", "{f}.{last}"),
        (f"{f}", "{first}"),
        (f"{l}.{f}", "{last}.{first}"),
        (f"{l}{f}", "{last}{first}"),
        (f"{f}{l[0]}", "{first}{l}"),
    ]
    for candidate, template in patterns:
        if candidate == username:
            return template
    return None
