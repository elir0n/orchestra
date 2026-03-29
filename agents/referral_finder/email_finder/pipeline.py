from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.referral_finder.email_finder import domain_lookup, github, pattern_cache, smtp_verify
from agents.referral_finder.search.linkedin_search import Person

logger = logging.getLogger(__name__)


@dataclass
class EmailResult:
    email: str | None
    source: str  # "github", "cache", "domain_pattern", "smtp", "not_found"
    confidence: str  # "high", "medium", "low"


def find_email(
    person: Person,
    github_token: str = "",
    smtp_from_domain: str = "example.com",
) -> EmailResult:
    """
    Multi-tier email finding pipeline. Tries each tier in order and
    returns as soon as a confident result is found.
    """
    first = person.first_name
    last = person.last_name
    company = person.company

    # --- Tier 1: GitHub API ---
    logger.debug(f"[{person.name}] Tier 1: GitHub search")
    email = github.find_email_via_github(first, last, company, github_token)
    if email:
        # Learn the pattern for future candidates at this company
        domain = email.split("@")[-1]
        pat = pattern_cache.email_to_pattern(email, first, last)
        if pat:
            pattern_cache.save_pattern(domain, pat, company=company)
        return EmailResult(email=email, source="github", confidence="high")

    # --- Tier 2: Known pattern cache ---
    domain_from_cache = pattern_cache.get_domain_for_company(company)
    cached_pattern = pattern_cache.get_pattern(domain_from_cache) if domain_from_cache else None
    if cached_pattern and domain_from_cache:
        logger.debug(f"[{person.name}] Tier 2: applying cached pattern")
        username = pattern_cache.apply_pattern(cached_pattern, first, last)
        email = f"{username}@{domain_from_cache}"
        # Optionally verify
        verified = smtp_verify.verify_email(email, smtp_from_domain)
        if verified is True:
            return EmailResult(email=email, source="cache", confidence="high")
        elif verified is None:
            # Inconclusive server, still return with medium confidence
            return EmailResult(email=email, source="cache", confidence="medium")
        # verified is False → try next tier

    # --- Tier 3: Hunter.io domain search (free endpoint) ---
    logger.debug(f"[{person.name}] Tier 3: Hunter.io domain lookup")
    domain, hunter_pattern = domain_lookup.get_company_domain_and_pattern(company)
    if domain and hunter_pattern:
        pattern_cache.save_pattern(domain, hunter_pattern, company=company)
        username = pattern_cache.apply_pattern(hunter_pattern, first, last)
        email = f"{username}@{domain}"
        verified = smtp_verify.verify_email(email, smtp_from_domain)
        if verified is True:
            return EmailResult(email=email, source="domain_pattern", confidence="high")
        elif verified is None:
            return EmailResult(email=email, source="domain_pattern", confidence="medium")
    elif domain and not hunter_pattern:
        # Domain found but no pattern — go straight to SMTP permutation scan
        pass

    # --- Tier 4: Permutation + SMTP verify ---
    if domain:
        logger.debug(f"[{person.name}] Tier 4: SMTP permutation scan on {domain}")
        permutations = smtp_verify.generate_permutations(first, last, domain)
        for candidate_email in permutations:
            result = smtp_verify.verify_email(candidate_email, smtp_from_domain)
            if result is True:
                discovered_pattern = pattern_cache.email_to_pattern(candidate_email, first, last)
                if discovered_pattern:
                    pattern_cache.save_pattern(domain, discovered_pattern, company=company)
                return EmailResult(email=candidate_email, source="smtp", confidence="medium")

    # --- Tier 5: Not found ---
    return EmailResult(email=None, source="not_found", confidence="low")


