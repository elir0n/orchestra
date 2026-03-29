from __future__ import annotations

import abc
import logging
from typing import Any, Generic, TypeVar

from shared.models import AgentResult, AgentRunContext

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class BaseAgent(abc.ABC, Generic[InputT, OutputT]):
    """
    All Orchestra agents implement this interface.

    Subclasses must declare:
      - name:        unique kebab-case identifier used as the CLI subcommand
      - description: one-line human description shown in --help

    The Orchestrator discovers agents by scanning the agents/ package for
    subclasses of BaseAgent and dispatches CLI invocations by matching `name`.
    """

    name: str           # class variable, set on the subclass
    description: str    # class variable
    version: str = "0.1.0"

    def __init__(self, config: Any, ctx: AgentRunContext) -> None:
        self.config = config
        self.ctx = ctx
        self.logger = logging.getLogger(f"orchestra.agents.{self.name}")

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Called once before run(). Use for connection setup, validation."""
        pass

    def teardown(self) -> None:
        """Called once after run(), even on failure. Use for cleanup."""
        pass

    # ------------------------------------------------------------------
    # Core interface — must implement
    # ------------------------------------------------------------------

    @classmethod
    @abc.abstractmethod
    def build_arg_parser(cls, subparser: Any) -> None:
        """Add this agent's CLI arguments to the provided argparse subparser."""
        ...

    @classmethod
    @abc.abstractmethod
    def config_from_args(cls, args: Any) -> Any:
        """Convert parsed argparse namespace into this agent's config dataclass."""
        ...

    @abc.abstractmethod
    def run(self, input: InputT, ctx: AgentRunContext) -> AgentResult[OutputT]:
        """
        Execute the agent's main logic.
        Must return an AgentResult — never raise unhandled exceptions.
        """
        ...

    # ------------------------------------------------------------------
    # Optional hooks for future orchestration patterns
    # ------------------------------------------------------------------

    def can_chain_from(self, other: "BaseAgent") -> bool:  # type: ignore[type-arg]
        """Return True if this agent can accept output from `other` as input."""
        return False

    def estimated_duration_seconds(self) -> int | None:
        """Hint for scheduling. None means unknown."""
        return None
