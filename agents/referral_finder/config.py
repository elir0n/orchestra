from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ReferralFinderConfig:
    # --- Required (set via CLI) ---
    company: str = ""
    your_name: str = ""
    your_background: str = ""

    # --- Optional with defaults ---
    roles: list[str] = field(
        default_factory=lambda: ["Software Engineer", "Senior Engineer", "Tech Lead", "Staff Engineer"]
    )
    location: str = "Israel"
    university: str = "Bar Ilan University"   # used as a warm-connection hint in the email if matched
    min_results: int = 3
    max_results: int = 15
    claude_model: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    )

    # --- Secrets: injected from env by Orchestrator (prefix: REFERRAL_FINDER_) ---
    tavily_api_key: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_TAVILY_API_KEY", "")
    )
    github_token: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_GITHUB_TOKEN", "")
    )
    smtp_from_domain: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_SMTP_FROM_DOMAIN", "example.com")
    )
