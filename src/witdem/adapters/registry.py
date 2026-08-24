"""Runtime adapter registry (see ``docs/architecture.md`` §3).

Used by the OTLP ingestion path (``ingest.correlate``) to select the first
matching runtime-specific adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from witdem.adapters.base import RuntimeAdapter
from witdem.adapters.haystack_adapter import HaystackAdapter
from witdem.integrations.adapters.claude import ClaudeAdapter
from witdem.integrations.adapters.langchain import LangChainAdapter
from witdem.integrations.adapters.langgraph import LangGraphAdapter
from witdem.integrations.adapters.openai_agents import OpenAIAgentsAdapter
from witdem.integrations.adapters.otel import OTelAdapter

logger = logging.getLogger(__name__)

_REGISTERED_ADAPTERS: tuple[RuntimeAdapter, ...] = (
    LangGraphAdapter(),
    OpenAIAgentsAdapter(),
    ClaudeAdapter(),
    LangChainAdapter(),
    HaystackAdapter(),
    OTelAdapter(),
)

_FALLBACK_ADAPTER: RuntimeAdapter = OTelAdapter()


def detect_adapter(spans: Sequence[Mapping[str, Any]]) -> RuntimeAdapter:
    """Return the first registered adapter whose ``detect()`` is True.

    Falls back to :class:`OTelAdapter` (with a debug log noting the
    fallback) when no registered adapter positively matches -- see
    ``docs/architecture.md`` §3 and the module docstring above.
    """

    for adapter in _REGISTERED_ADAPTERS:
        if adapter.detect(spans):
            return adapter
    logger.debug(
        "detect_adapter: no adapter positively matched %d spans; falling back to %s",
        len(spans),
        type(_FALLBACK_ADAPTER).__name__,
    )
    return _FALLBACK_ADAPTER
