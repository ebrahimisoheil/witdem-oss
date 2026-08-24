"""Anthropic workload only; no Witdem-specific imports."""

from __future__ import annotations

import os


def run(client) -> str:
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=128,
        messages=[{"role": "user", "content": "Give one concise observability tip."}],
    )
    return next((str(block.text) for block in response.content if getattr(block, "type", None) == "text"), "")
