from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class JobFinderConfig:
    # --- Required (set via CLI or env) ---
    master_cv_path: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_MASTER_CV_PATH", "")
    )
    format_cv_path: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_FORMAT_CV_PATH", "")
    )
    role: str = ""

    # --- Optional with defaults (overridable via env or CLI) ---
    location: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_LOCATION", "Israel")
    )
    startup: bool = False
    job_types: list[str] = field(
        default_factory=lambda: [
            t.strip() for t in
            os.environ.get("JOB_FINDER_JOB_TYPE", "full-time").split(",")
            if t.strip()
        ]
    )
    max_jobs: int = 5
    output_dir: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_OUTPUT_DIR", "./tailored_cvs")
    )
    dry_run: bool = False
    remote: bool = False    # --remote: filter for remote/WFH-friendly roles
    referral: bool = True   # --no-referral: skip auto referral search
    claude_model: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    )

    sites: list[str] | None = None  # None = all sites; set via --sites
    seen_jobs_path: str = field(
        default_factory=lambda: os.path.expanduser(
            os.environ.get("JOB_FINDER_SEEN_JOBS_PATH", "~/.orchestra/job_finder_seen.json")
        )
    )
    ignore_seen: bool = False
    reset_seen: bool = False

    # --- Secrets: injected from env by Orchestrator (prefix: JOB_FINDER_) ---
    tavily_api_key: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_TAVILY_API_KEY", "")
    )
