from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    pass


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise ConfigError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in the values."
        )
    return val


@dataclass
class OrchestraConfig:
    """Global config shared by all agents."""

    anthropic_api_key: str = field(default_factory=lambda: _require("ANTHROPIC_API_KEY"))

    # Operator notification (where results are sent)
    notify_email: str = field(default_factory=lambda: _require("NOTIFY_EMAIL"))
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _require("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _require("SMTP_PASSWORD"))

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    @classmethod
    def from_env(cls) -> "OrchestraConfig":
        return cls()
