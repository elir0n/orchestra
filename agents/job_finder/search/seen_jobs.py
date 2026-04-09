from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from agents.job_finder.search.job_search import JobPosting, _norm_url

logger = logging.getLogger(__name__)


class SeenJobsCache:
    """Persists a set of already-processed job URLs across runs."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._seen: dict[str, dict] = {}  # norm_url → {title, company, seen_at}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._seen = data.get("seen", {})
            logger.debug(f"Loaded {len(self._seen)} seen job(s) from {self._path}")
        except Exception as exc:
            logger.warning(f"Could not load seen-jobs cache from {self._path}: {exc}")

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"seen": self._seen}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Could not save seen-jobs cache to {self._path}: {exc}")

    def reset(self) -> None:
        self._seen = {}
        self.save()
        logger.info(f"Seen-jobs cache reset ({self._path})")

    # ------------------------------------------------------------------
    # Lookup / mutation
    # ------------------------------------------------------------------

    def contains(self, url: str) -> bool:
        return _norm_url(url) in self._seen

    def add(self, job: JobPosting) -> None:
        key = _norm_url(job.url)
        self._seen[key] = {
            "title": job.title,
            "company": job.company,
            "seen_at": datetime.date.today().isoformat(),
        }

    def __len__(self) -> int:
        return len(self._seen)
