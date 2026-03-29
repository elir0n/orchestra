from __future__ import annotations

import argparse
import importlib
import logging
import pkgutil
import sys
from typing import TYPE_CHECKING, Any, Type

from shared.base_agent import BaseAgent
from shared.config import OrchestraConfig
from shared.models import AgentResult, AgentRunContext, AgentStatus
from shared.notifications import NotificationService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Discovers all agents under the `agents/` package and dispatches
    CLI invocations to the correct one.
    """

    def __init__(self, config: OrchestraConfig, notifications: NotificationService) -> None:
        self.config = config
        self.notifications = notifications
        self._registry: dict[str, Type[BaseAgent]] = {}  # type: ignore[type-arg]
        self._discover_agents()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover_agents(self) -> None:
        try:
            import agents
        except ImportError:
            logger.warning("No agents/ package found.")
            return

        for finder, module_name, is_pkg in pkgutil.iter_modules(agents.__path__):  # type: ignore[union-attr]
            if not is_pkg:
                continue
            try:
                mod = importlib.import_module(f"agents.{module_name}.agent")
            except ImportError as exc:
                logger.warning(f"Could not import agents.{module_name}.agent: {exc}")
                continue
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseAgent)
                    and obj is not BaseAgent
                    and hasattr(obj, "name")
                ):
                    self._registry[obj.name] = obj
                    logger.debug(f"Registered agent: {obj.name}")

    # ------------------------------------------------------------------
    # CLI building
    # ------------------------------------------------------------------

    def build_cli(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="orchestra",
            description="Orchestra — multi-agent orchestrator",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the agent but skip sending the notification email",
        )
        subparsers = parser.add_subparsers(dest="agent_name", required=True)

        for agent_name, agent_cls in sorted(self._registry.items()):
            sub = subparsers.add_parser(
                agent_name,
                help=getattr(agent_cls, "description", ""),
            )
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Skip sending the notification email",
            )
            agent_cls.build_arg_parser(sub)

        return parser

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def run_from_args(self, args: Any) -> None:
        agent_name = args.agent_name
        agent_cls = self._registry.get(agent_name)
        if agent_cls is None:
            print(f"Unknown agent: {agent_name}", file=sys.stderr)
            sys.exit(1)

        agent_config = agent_cls.config_from_args(args)
        agent_config = self._inject_secrets(agent_cls, agent_config)

        ctx = AgentRunContext(
            dry_run=getattr(args, "dry_run", False),
        )

        agent = agent_cls(config=agent_config, ctx=ctx)
        agent.setup()
        result: AgentResult = AgentResult(status=AgentStatus.FAILURE)
        try:
            result = agent.run(input=agent_config, ctx=ctx)
            result.finalize()
        except Exception as exc:
            logger.exception(f"Agent {agent_name} raised an unhandled exception")
            result = AgentResult(
                status=AgentStatus.FAILURE,
                errors=[str(exc)],
            ).finalize()
        finally:
            agent.teardown()

        self._print_summary(agent_name, result)

    def _inject_secrets(self, agent_cls: Type[BaseAgent], config: Any) -> Any:  # type: ignore[type-arg]
        """
        Inject env vars prefixed with AGENTNAME_ into the config object.
        E.g., REFERRAL_FINDER_GITHUB_TOKEN → config.github_token
        """
        import os
        prefix = agent_cls.name.upper().replace("-", "_") + "_"
        for key, val in os.environ.items():
            if key.startswith(prefix):
                field_name = key[len(prefix):].lower()
                if hasattr(config, field_name):
                    setattr(config, field_name, val)
        return config

    def _print_summary(self, agent_name: str, result: AgentResult) -> None:  # type: ignore[type-arg]
        status_icon = "OK" if result.is_success() else "FAILED"
        print(f"\n[{status_icon}] Agent '{agent_name}' finished with status: {result.status.value}")
        if result.metrics:
            for k, v in result.metrics.items():
                print(f"  {k}: {v}")
        if result.errors:
            print(f"  Errors ({len(result.errors)}):")
            for err in result.errors:
                print(f"    - {err}")
