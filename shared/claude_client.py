from __future__ import annotations

import anthropic


def make_client(api_key: str) -> anthropic.Anthropic:
    """Return a configured Anthropic client."""
    return anthropic.Anthropic(api_key=api_key)
