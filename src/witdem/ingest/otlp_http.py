"""OTLP/HTTP trace receiver.

The one real standard ingestion path: ``POST /v1/traces`` accepts a standard
OTLP/HTTP protobuf export request
(``opentelemetry.proto.collector.trace.v1.trace_service_pb2.ExportTraceServiceRequest``),
exactly as any standard OTel exporter would send it
(``opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`` and
equivalents in other languages) -- there is no parallel "send spans as JSON"
endpoint here. Each proto ``Span`` is converted into the exact raw span dict
shape consumed by runtime adapters and committed to the immutable corpus.
"""

from __future__ import annotations

import gzip
import logging
import os
from collections.abc import Iterable
from typing import Any

import fastapi
from google.protobuf.message import DecodeError  # type: ignore[import-untyped]  # protobuf ships no py.typed marker
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span
from opentelemetry.trace import SpanKind as SdkSpanKind
from opentelemetry.trace import StatusCode as SdkStatusCode
from starlette.concurrency import run_in_threadpool

from witdem.auth import require_api_key
from witdem.ingest import corpus, raw_store
from witdem.integrations.normalizers.otel import sanitize_otel_span

logger = logging.getLogger(__name__)

router = fastapi.APIRouter()

_OTLP_MEDIA_TYPE = "application/x-protobuf"

# proto Span.SpanKind has an extra SPAN_KIND_UNSPECIFIED (0) that the SDK's
# own SpanKind enum does not, so ints are off-by-one -- map explicitly rather
# than by arithmetic. UNSPECIFIED maps to INTERNAL per the proto's own
# docstring ("Implementations MAY assume SpanKind to be INTERNAL when
# receiving UNSPECIFIED").
_SPAN_KIND_TO_SDK: dict[int, SdkSpanKind] = {
    Span.SPAN_KIND_UNSPECIFIED: SdkSpanKind.INTERNAL,
    Span.SPAN_KIND_INTERNAL: SdkSpanKind.INTERNAL,
    Span.SPAN_KIND_SERVER: SdkSpanKind.SERVER,
    Span.SPAN_KIND_CLIENT: SdkSpanKind.CLIENT,
    Span.SPAN_KIND_PRODUCER: SdkSpanKind.PRODUCER,
    Span.SPAN_KIND_CONSUMER: SdkSpanKind.CONSUMER,
}


def _span_kind_str(value: int) -> str:
    """Stringify a proto ``Span.kind`` the same way
    ``telemetry.otel.JsonlSpanExporter._span_value`` stringifies a live SDK
    span's ``kind`` (``str(SpanKind.INTERNAL)`` -> ``"SpanKind.INTERNAL"``),
    so ``analytics.runtime.normalize_haystack_spans`` sees an identical
    representation regardless of which producer the span came from.
    """

    return str(_SPAN_KIND_TO_SDK.get(value, SdkSpanKind.INTERNAL))


def _status_code_str(value: int) -> str:
    """Stringify a proto ``Status.code``.

    Proto ``Status.StatusCode`` values (0/1/2 for UNSET/OK/ERROR) line up 1:1
    with ``opentelemetry.trace.StatusCode``, so this produces the same
    ``"StatusCode.OK"``-style string ``JsonlSpanExporter._span_value``
    produces from a live SDK span. ``analytics.runtime._status`` only looks
    at the text after the last ``.``, lowercased, so this format is exactly
    what it expects.
    """

    try:
        return str(SdkStatusCode(value))
    except ValueError:
        logger.debug("otlp_http: unknown status code %r on span, treating as UNSET", value)
        return str(SdkStatusCode.UNSET)


def _decode_any_value(value: AnyValue) -> Any:
    """Unwrap one protobuf ``AnyValue`` oneof into a plain JSON-safe value."""

    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    if kind == "array_value":
        return [_decode_any_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return _decode_attributes(value.kvlist_value.values)
    if kind == "bytes_value":
        return value.bytes_value.hex()
    # ``string_value_strindex`` (profiling-signal-only, per the proto's own
    # docstring) or an unset oneof: nothing meaningful to unwrap.
    return None


def _decode_attributes(pairs: Iterable[KeyValue]) -> dict[str, Any]:
    return {pair.key: _decode_any_value(pair.value) for pair in pairs}


def _decode_resource(resource_spans: ResourceSpans) -> dict[str, Any]:
    if not resource_spans.HasField("resource"):
        return {}
    return _decode_attributes(resource_spans.resource.attributes)


def _decode_scope(scope_spans: ScopeSpans) -> dict[str, str | None]:
    if not scope_spans.HasField("scope"):
        return {"name": None, "version": None}
    scope = scope_spans.scope
    return {"name": scope.name or None, "version": scope.version or None}


def _decode_span(
    span: Span,
    *,
    resource_attributes: dict[str, Any],
    instrumentation_scope: dict[str, str | None],
) -> dict[str, Any]:
    """Convert one proto ``Span`` into the exact dict shape docs/architecture.md
    pins -- this matches ``telemetry.otel.JsonlSpanExporter._span_value``
    key-for-key so ``analytics.runtime.normalize_haystack_spans`` (via the
    Haystack adapter) needs no changes to read either producer's spans.
    """

    return {
        "trace_id": span.trace_id.hex(),
        "span_id": span.span_id.hex(),
        "parent_span_id": span.parent_span_id.hex() if span.parent_span_id else None,
        "name": span.name,
        "kind": _span_kind_str(span.kind),
        "start_time_unix_nano": span.start_time_unix_nano,
        "end_time_unix_nano": span.end_time_unix_nano,
        "status": {
            "status_code": _status_code_str(span.status.code),
            "description": span.status.message or None,
        },
        "attributes": _decode_attributes(span.attributes),
        "events": [
            {
                "name": event.name,
                "timestamp": event.time_unix_nano,
                "attributes": _decode_attributes(event.attributes),
            }
            for event in span.events
        ],
        "resource": resource_attributes,
        "instrumentation_scope": instrumentation_scope,
    }


def decode_export_request(request: ExportTraceServiceRequest) -> list[dict[str, Any]]:
    """Decode every span across every resource_spans/scope_spans entry in one
    OTLP export request.

    A single export can legitimately batch many traces/executions in one
    call (docs/architecture.md requires handling that, not just the first
    resource_spans entry), so this always decodes the full nested structure.
    """

    spans: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        resource_attributes = _decode_resource(resource_spans)
        for scope_spans in resource_spans.scope_spans:
            instrumentation_scope = _decode_scope(scope_spans)
            for span in scope_spans.spans:
                spans.append(
                    _decode_span(
                        span,
                        resource_attributes=resource_attributes,
                        instrumentation_scope=instrumentation_scope,
                    )
                )
    return spans


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _decompress_if_needed(body: bytes, content_encoding: str) -> bytes:
    encoding = content_encoding.strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding == "gzip":
        try:
            return gzip.decompress(body)
        except OSError as exc:
            raise fastapi.HTTPException(status_code=400, detail=f"invalid gzip-encoded body: {exc}") from exc
    raise fastapi.HTTPException(status_code=415, detail=f"unsupported content-encoding {encoding!r}")


@router.post("/v1/traces")
async def export_traces(
    request: fastapi.Request,
    background_tasks: fastapi.BackgroundTasks,
) -> fastapi.Response:
    """Standard OTLP/HTTP trace export endpoint (docs/architecture.md).

    Never persists request headers (Authorization, API keys, etc.) --
    only the decoded span bodies below ever reach disk (docs/architecture.md).
    """

    require_api_key(request)
    media_type = _media_type(request.headers.get("content-type", ""))
    if media_type and media_type != _OTLP_MEDIA_TYPE:
        raise fastapi.HTTPException(
            status_code=415,
            detail=f"unsupported content-type {media_type!r}; expected {_OTLP_MEDIA_TYPE!r}",
        )

    wire_body = await request.body()
    content_encoding = request.headers.get("content-encoding", "")
    body = _decompress_if_needed(wire_body, content_encoding)

    export_request = ExportTraceServiceRequest()
    try:
        export_request.ParseFromString(body)
    except DecodeError as exc:
        raise fastapi.HTTPException(status_code=400, detail=f"invalid OTLP/HTTP protobuf body: {exc}") from exc

    spans = decode_export_request(export_request)
    capture_content = os.getenv("WITDEM_CAPTURE_CONTENT", "").strip().casefold() in {"1", "true", "yes"}
    spans = [sanitize_otel_span(span, include_content=capture_content) for span in spans]
    logger.debug(
        "otlp_http: decoded %d span(s) from %d resource_spans entr(y/ies)",
        len(spans),
        len(export_request.resource_spans),
    )

    try:
        await run_in_threadpool(
            raw_store.commit_spans,
            spans,
            raw_payload=wire_body if capture_content else None,
            content_encoding=content_encoding,
        )
    except corpus.CorpusBackpressureError as exc:
        raise fastapi.HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    response = ExportTraceServiceResponse()
    return fastapi.Response(content=response.SerializeToString(), media_type=_OTLP_MEDIA_TYPE, status_code=200)
