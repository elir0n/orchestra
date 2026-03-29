from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

OutputT = TypeVar("OutputT")


class AgentStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"      # ran but with some failures
    FAILURE = "failure"
    SKIPPED = "skipped"


@dataclass
class AgentRunContext:
    """Injected by the Orchestrator into every agent run."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    dry_run: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult(Generic[OutputT]):
    status: AgentStatus
    data: OutputT | None = None
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    started_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    finished_at: datetime.datetime | None = None

    def is_success(self) -> bool:
        return self.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def finalize(self) -> "AgentResult[OutputT]":
        self.finished_at = datetime.datetime.utcnow()
        return self
