"""Haystack/OpenTelemetry runtime adapter.

Thin wrapper around the existing, already-tested
``analytics.runtime.normalize_haystack_spans`` (see ``docs/architecture.md`` §3).
All framework-specific *detection* knowledge (attribute/scope naming) lives
here, isolated behind the ``RuntimeAdapter`` boundary; ``normalize()`` itself
does not reimplement or modify the 650-line, already-tested normalizer it
wraps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.runtime import NormalizedExecutionGraph, normalize_haystack_spans

_HAYSTACK_ATTRIBUTE_PREFIX = "haystack."
_HAYSTACK_SCOPE_HINTS = ("haystack", "opentelemetry-haystack")


class HaystackAdapter:
    """``RuntimeAdapter`` for Haystack-instrumented OpenTelemetry spans.

    ``detect()`` looks for the same physical conventions
    ``normalize_haystack_spans`` itself reads span-by-span: any
    ``haystack.*``-prefixed attribute key (e.g. ``haystack.component.name``,
    ``haystack.tool.name``, ``haystack.agent.step``), or an instrumentation
    scope name that mentions "haystack" / "opentelemetry-haystack".
    """

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        for span in spans:
            attributes = span.get("attributes")
            if isinstance(attributes, Mapping) and any(
                str(key).startswith(_HAYSTACK_ATTRIBUTE_PREFIX) for key in attributes
            ):
                return True
            scope = span.get("instrumentation_scope")
            if isinstance(scope, Mapping):
                name = str(scope.get("name") or "").casefold()
                if any(hint in name for hint in _HAYSTACK_SCOPE_HINTS):
                    return True
        return False

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        return normalize_haystack_spans(
            spans,
            execution_id=execution_id,
            runtime_id=runtime_id,
            providers=providers,
        )
