"""Explicit OpenTelemetry setup for Haystack and application spans."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentelemetry import baggage, trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.semconv.resource import ResourceAttributes

_CONFIGURED = False
_EXPORTER: JsonlSpanExporter | None = None
_TRACER_PROVIDER: TracerProvider | None = None


def record_cost(
    amount: float,
    *,
    currency: str = "USD",
    category: str = "tool",
    source: str = "provider_usage",
    operation_id: str | None = None,
) -> bool:
    """Annotate the current operation with one known cost.

    This is intentionally an optional integration hook.  Callers that already
    have a billable amount can use it inside their tool/component span; callers
    without one leave cost unknown.  ``operation_id`` is recorded as a
    correlation hint, while the active span remains the authoritative scope.
    """

    if not isinstance(amount, (int, float)) or amount < 0:
        raise ValueError("cost amount must be a non-negative number")
    span = trace.get_current_span()
    if isinstance(span, trace.NonRecordingSpan):
        return False
    span.set_attribute("pf.cost.amount", float(amount))
    span.set_attribute("pf.cost.currency", currency)
    span.set_attribute("pf.cost.category", category)
    span.set_attribute("pf.cost.source", source)
    if operation_id:
        span.set_attribute("pf.cost.operation_id", operation_id)
    return True


class JsonlSpanExporter(SpanExporter):
    """Write a JSON-safe, minimally serialized representation of each source OTel span.

    This exporter preserves source span attributes, events, resources, timing, and
    parent/child identifiers. It does not derive business events or reshape spans
    into an analytics schema; semantic events remain a separate artifact.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _span_value(span: ReadableSpan) -> dict[str, Any]:
        context = span.get_span_context()
        parent = span.parent
        scope = span.instrumentation_scope
        return {
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
                    "name": event.name,
                    "timestamp": event.timestamp,
                    "attributes": dict(event.attributes or {}),
                }
                for event in span.events
            ],
            "resource": dict(span.resource.attributes),
            "instrumentation_scope": {
                "name": scope.name if scope else None,
                "version": scope.version if scope else None,
            },
        }

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                for span in spans:
                    handle.write(json.dumps(self._span_value(span), default=str) + "\n")
            return SpanExportResult.SUCCESS
        except OSError:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


class ExecutionIdSpanProcessor(SpanProcessor):
    """Copy the application execution key from OTel baggage to every child span."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        execution_id = baggage.get_baggage("witdem.execution_id", parent_context)
        if isinstance(execution_id, str):
            span.set_attribute("witdem.execution_id", execution_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _otlp_trace_endpoint(endpoint: str) -> str:
    """Turn a base OTLP/HTTP endpoint into the standard traces URL."""

    normalized = endpoint.rstrip("/")
    return normalized if normalized.endswith("/v1/traces") else f"{normalized}/v1/traces"


def configure_tracing(
    data_dir: Path,
    content_tracing_enabled: bool = False,
    *,
    otlp_endpoint: str | None = None,
) -> trace.Tracer:
    """Configure local tracing plus optional standard OTLP/HTTP export."""

    global _CONFIGURED, _EXPORTER, _TRACER_PROVIDER
    if _CONFIGURED:
        if _EXPORTER is not None:
            _EXPORTER.path = data_dir / "telemetry" / "spans.jsonl"
            _EXPORTER.path.parent.mkdir(parents=True, exist_ok=True)
        return trace.get_tracer("witdem")
    os.environ["HAYSTACK_CONTENT_TRACING_ENABLED"] = "true" if content_tracing_enabled else "false"
    provider = TracerProvider(resource=Resource.create({ResourceAttributes.SERVICE_NAME: "witdem"}))
    _EXPORTER = JsonlSpanExporter(data_dir / "telemetry" / "spans.jsonl")
    provider.add_span_processor(ExecutionIdSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=_otlp_trace_endpoint(otlp_endpoint)),
                schedule_delay_millis=200,
                max_export_batch_size=64,
            )
        )
    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider

    try:
        from haystack import tracing as haystack_tracing  # type: ignore[import-not-found]
        from haystack_integrations.tracing.opentelemetry import OpenTelemetryTracer  # type: ignore[import-not-found]

        haystack_tracing.enable_tracing(OpenTelemetryTracer(trace.get_tracer("witdem.haystack")))
    except ImportError:
        # Unit tests for domain and persistence do not need Haystack installed.
        pass

    _CONFIGURED = True
    return trace.get_tracer("witdem")


def force_flush_tracing(timeout_millis: int = 30_000) -> bool:
    """Flush pending OTLP batches before a short-lived application exits."""

    return _TRACER_PROVIDER.force_flush(timeout_millis) if _TRACER_PROVIDER is not None else True
