from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class JobFinderConfig:
    # --- Required (set via CLI) ---
    master_cv_path: str = ""
    format_cv_path: str = ""
    role: str = ""

    # --- Optional with defaults ---
    location: str = "Israel"
    startup: bool = False
    job_type: str = "full-time"
    max_jobs: int = 5
    output_dir: str = "./tailored_cvs"
    dry_run: bool = False
    claude_model: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    )

    # --- Secrets: injected from env by Orchestrator (prefix: JOB_FINDER_) ---
    tavily_api_key: str = field(
        default_factory=lambda: os.environ.get("JOB_FINDER_TAVILY_API_KEY", "")
    )
