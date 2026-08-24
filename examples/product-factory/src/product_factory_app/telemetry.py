"""Application-owned OpenTelemetry setup for Product Factory runs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import baggage, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

_provider: TracerProvider | None = None
_exporter: JsonlSpanExporter | None = None


class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                for span in spans:
                    context = span.get_span_context()
                    parent = span.parent
                    handle.write(
                        json.dumps(
                            {
                                "artifact_type": "otel.span",
                                "schema_version": "0.1.0",
                                "captured_at": datetime.now(timezone.utc).isoformat(),
                                "trace_id": f"{context.trace_id:032x}" if context else None,
                                "span_id": f"{context.span_id:016x}" if context else None,
                                "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                                "name": span.name,
                                "kind": str(span.kind),
                                "start_time_unix_nano": span.start_time,
                                "end_time_unix_nano": span.end_time,
                                "status": {
                                    "status_code": str(span.status.status_code),
                                    "description": span.status.description,
                                },
                                "attributes": dict(span.attributes or {}),
                                "events": [
                                    {
                                        "name": item.name,
                                        "timestamp": item.timestamp,
                                        "attributes": dict(item.attributes or {}),
                                    }
                                    for item in span.events
                                ],
                                "resource": dict(span.resource.attributes),
                            },
                            default=str,
                        )
                        + "\n"
                    )
            return SpanExportResult.SUCCESS
        except OSError:
            return SpanExportResult.FAILURE


class ExecutionIdSpanProcessor(SpanProcessor):
    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        execution_id = baggage.get_baggage("witdem.execution_id", parent_context)
        if isinstance(execution_id, str):
            span.set_attribute("witdem.execution_id", execution_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def configure_tracing(
    data_dir: Path, content_tracing_enabled: bool = False, *, otlp_endpoint: str | None = None
) -> trace.Tracer:
    del content_tracing_enabled, otlp_endpoint
    global _provider, _exporter
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": "witdem-product-factory"}))
        _exporter = JsonlSpanExporter(data_dir / "telemetry" / "spans.jsonl")
        _provider.add_span_processor(ExecutionIdSpanProcessor())
        _provider.add_span_processor(SimpleSpanProcessor(_exporter))
        trace.set_tracer_provider(_provider)
    elif _exporter is not None:
        _exporter.path = data_dir / "telemetry" / "spans.jsonl"
        _exporter.path.parent.mkdir(parents=True, exist_ok=True)
    return trace.get_tracer("product_factory_app")


def force_flush_tracing(timeout_millis: int = 30_000) -> bool:
    return _provider.force_flush(timeout_millis) if _provider is not None else True
