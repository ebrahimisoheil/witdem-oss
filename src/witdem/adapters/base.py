"""Public runtime adapter protocol used by built-in and external adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from witdem.analytics.runtime import NormalizedExecutionGraph


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Detects and normalizes one raw-runtime span convention.

    ``spans`` are raw span envelopes in the same shape
    ``telemetry.otel.JsonlSpanExporter`` produces (trace_id, span_id,
    parent_span_id, name, kind, attributes, status, start_time_unix_nano,
    end_time_unix_nano, events, resource, instrumentation_scope) —
    ``ingest.otlp_http`` reconstructs this same shape from OTLP protobuf.
    """

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool: ...

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph: ...
