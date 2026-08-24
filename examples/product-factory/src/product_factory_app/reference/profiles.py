"""Versioned role-to-model profiles for the live experiment."""

from __future__ import annotations

from typing import Final

ROLE_NAMES: Final = ("research", "evidence_critic", "profile_extractor", "qualification_analyst")

MODEL_PROFILES: Final[dict[str, dict[str, str]]] = {
    "mixed-v1": {
        "research": "deepseek-v4-flash",
        "evidence_critic": "claude-haiku-4-5-20251001",
        "profile_extractor": "gpt-5.4-mini-2026-03-17",
        "qualification_analyst": "mistral-small-2603",
    },
    "openai-mini": dict.fromkeys(ROLE_NAMES, "gpt-5.4-mini-2026-03-17"),
    "openai-full": dict.fromkeys(ROLE_NAMES, "gpt-5.4-2026-03-05"),
    "anthropic-haiku": dict.fromkeys(ROLE_NAMES, "claude-haiku-4-5-20251001"),
    "anthropic-sonnet": dict.fromkeys(ROLE_NAMES, "claude-sonnet-5"),
    "deepseek-v4-flash": dict.fromkeys(ROLE_NAMES, "deepseek-v4-flash"),
    "mistral-small": dict.fromkeys(ROLE_NAMES, "mistral-small-2603"),
}

RUNTIME_DEFAULT_PROFILE: Final = {
    "langchain": "mixed-v1",
    "langgraph": "mixed-v1",
    "haystack": "mixed-v1",
    "openai_agents": "openai-mini",
    "anthropic_messages": "anthropic-haiku",
}
