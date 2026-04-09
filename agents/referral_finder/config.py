from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ReferralFinderConfig:
    # --- Required (set via CLI or env) ---
    company: str = ""          # single company (legacy); ignored when companies is set
    companies: list[str] = field(default_factory=list)   # --companies / --companies-file

    your_name: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_YOUR_NAME", "")
    )
    your_background: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_YOUR_BACKGROUND", "")
    )

    # --- Optional with defaults (overridable via env or CLI) ---
    roles: list[str] = field(
        default_factory=lambda: ["Software Engineer", "Senior Engineer", "Tech Lead", "Staff Engineer"]
    )
    include_recruiters: bool = False   # --include-recruiters: also search HR/Talent/Recruiter roles

    target_role: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_TARGET_ROLE", "")
    )  # the specific role you are applying for — injected into email

    location: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_LOCATION", "Israel")
    )
    university: str = field(
        default_factory=lambda: os.environ.get("REFERRAL_FINDER_UNIVERSITY", "Bar Ilan University")
    )
    min_results: int = 3
    max_results: int = 15

    seniority: list[str] = field(default_factory=list)
    # e.g. ["junior", "mid", "senior", "staff", "lead"] — empty = no filter

    min_email_confidence: str = "low"   # "low" | "medium" | "high"

    output_file: str = ""   # path to write JSON results; "" = disabled

    seen_contacts_path: str = field(
        default_factory=lambda: os.path.expanduser(
            os.environ.get("REFERRAL_FINDER_SEEN_CONTACTS_PATH", "~/.orchestra/referral_finder_seen.json")
        )
    )
    ignore_seen: bool = False
    reset_seen: bool = False

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
