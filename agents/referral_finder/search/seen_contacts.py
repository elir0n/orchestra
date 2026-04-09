from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from agents.referral_finder.search.linkedin_search import Person

logger = logging.getLogger(__name__)


class SeenContactsCache:
    """Persists LinkedIn profiles already processed across runs to prevent duplicate outreach."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._seen: dict[str, dict] = {}  # linkedin_url → {name, company, seen_at}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._seen = data.get("seen", {})
            logger.debug(f"Loaded {len(self._seen)} seen contact(s) from {self._path}")
        except Exception as exc:
            logger.warning(f"Could not load seen-contacts cache from {self._path}: {exc}")

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"seen": self._seen}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Could not save seen-contacts cache to {self._path}: {exc}")

    def reset(self) -> None:
        self._seen = {}
        self.save()
        logger.info(f"Seen-contacts cache reset ({self._path})")

    # ------------------------------------------------------------------
    # Lookup / mutation
    # ------------------------------------------------------------------

    def _key(self, url: str) -> str:
        """Normalize LinkedIn URL to a stable key."""
        return url.lower().rstrip("/")

    def contains(self, person: Person) -> bool:
        return self._key(person.linkedin_url) in self._seen

    def add(self, person: Person) -> None:
        self._seen[self._key(person.linkedin_url)] = {
            "name": person.name,
            "company": person.company,
            "seen_at": datetime.date.today().isoformat(),
        }

    def __len__(self) -> int:
        return len(self._seen)
