"""Generic OTel adapter for spans without a runtime-specific dialect."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.runtime import NormalizedExecutionGraph
from witdem.integrations.mapping import graph_from_spans, normalize_raw_spans


class OTelAdapter:
    """Map generic OTel/GenAI/OpenInference spans through shared normalizers."""

    runtime_name = "otel"

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        return bool(spans) and any("trace_id" in span or "span_id" in span for span in spans)

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        normalized = normalize_raw_spans(spans)
        return graph_from_spans(
            normalized,
            execution_id=execution_id,
            runtime=runtime_id or self.runtime_name,
            telemetry_path="otel",
        )
