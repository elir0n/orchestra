#!/usr/bin/env python3
"""Orchestra — multi-agent orchestrator CLI entry point."""
from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

load_dotenv()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    from shared.config import ConfigError, OrchestraConfig
    from shared.notifications import NotificationService
    from shared.orchestrator import Orchestrator

    try:
        config = OrchestraConfig.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    configure_logging(config.log_level)

    notifications = NotificationService.from_config(config)
    orchestrator = Orchestrator(config=config, notifications=notifications)

    parser = orchestrator.build_cli()
    args = parser.parse_args()
    orchestrator.run_from_args(args)


if __name__ == "__main__":
    main()
