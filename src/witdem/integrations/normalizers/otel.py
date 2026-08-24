"""Generic OpenTelemetry envelope normalization.

This module knows nothing about GenAI or a particular runtime.  It accepts the
JSON-safe span shape already produced by Witdem's OTLP receiver/exporter and is
also tolerant of common OTLP/SDK naming variants used by fixtures and custom
exporters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from witdem.integrations.models.normalized_span import NormalizedLink, NormalizedSpan

OTEL_ENVELOPE_VERSION = "otel-envelope-0.1"

_CONTENT_KEYS = frozenset(
    {
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "llm.input_messages",
        "llm.output_messages",
        "input.value",
        "output.value",
        "tool.input",
        "tool.output",
        "tool.arguments",
        "tool.result",
        "input",
        "output",
    }
)


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 1e17:
            number /= 1e9
        elif number > 1e14:
            number /= 1e6
        elif number > 1e11:
            number /= 1e3
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status(value: Any) -> tuple[str | None, str | None]:
    description: str | None = None
    if isinstance(value, Mapping):
        description = str(value.get("description") or value.get("message") or "") or None
        value = value.get("status_code", value.get("code"))
    if value is None:
        return None, description
    normalized = str(value).rsplit(".", 1)[-1].casefold()
    return {"unset": "unset", "ok": "ok", "error": "error"}.get(normalized, normalized), description


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _attributes(value: Any, *, include_content: bool) -> dict[str, Any]:
    source = _mapping(value)
    if include_content:
        return dict(source)
    return {str(key): item for key, item in source.items() if str(key) not in _CONTENT_KEYS}


def _events(value: Any, *, include_content: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return result
    for item in value:
        row = _mapping(item)
        if not row:
            continue
        result.append(
            {
                "name": str(row.get("name") or row.get("event") or "event"),
                "timestamp": _timestamp(row.get("timestamp", row.get("time_unix_nano"))),
                "attributes": _attributes(row.get("attributes"), include_content=include_content),
            }
        )
    return result


def _links(value: Any, *, include_content: bool) -> list[NormalizedLink]:
    result: list[NormalizedLink] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return result
    for item in value:
        row = _mapping(item)
        if not row:
            continue
        result.append(
            NormalizedLink(
                trace_id=str(row.get("trace_id")) if row.get("trace_id") else None,
                span_id=str(row.get("span_id")) if row.get("span_id") else None,
                attributes=_attributes(row.get("attributes"), include_content=include_content),
            )
        )
    return result


def _exception(events: Sequence[Mapping[str, Any]], status_description: str | None) -> dict[str, Any] | None:
    for event in events:
        if str(event.get("name", "")).casefold() in {"exception", "error", "exception.raised"}:
            return dict(event)
    return {"description": status_description} if status_description else None


class OTelEnvelopeNormalizer:
    """Normalize generic OTel span envelopes without semantic assumptions."""

    version = OTEL_ENVELOPE_VERSION

    def __init__(self, *, include_content: bool = False) -> None:
        self.include_content = include_content

    def normalize(self, span: Mapping[str, Any] | Any) -> NormalizedSpan:
        row = _mapping(span)
        if not row:
            raise ValueError("OTel span must be a mapping")
        attributes = _attributes(row.get("attributes"), include_content=self.include_content)
        events = _events(row.get("events"), include_content=self.include_content)
        status, description = _status(row.get("status"))
        if status is None and row.get("status_code") is not None:
            status, description = _status(row.get("status_code"))
        normalized = NormalizedSpan(
            trace_id=str(row.get("trace_id")) if row.get("trace_id") else None,
            span_id=str(row.get("span_id")) if row.get("span_id") else None,
            parent_span_id=str(row.get("parent_span_id")) if row.get("parent_span_id") else None,
            name=str(row.get("name") or "operation"),
            start_time=_timestamp(row.get("start_time", row.get("start_time_unix_nano"))),
            end_time=_timestamp(row.get("end_time", row.get("end_time_unix_nano"))),
            status=status,
            status_description=description,
            events=events,
            links=_links(row.get("links"), include_content=self.include_content),
            attributes=attributes,
            resource=dict(_mapping(row.get("resource"))),
            instrumentation_scope=dict(_mapping(row.get("instrumentation_scope", row.get("scope")))),
        )
        normalized.exception = _exception(events, description)
        if normalized.span_id is None and normalized.trace_id is None:
            raise ValueError("OTel span must contain span_id or trace_id")
        return normalized

    def normalize_many(self, spans: Sequence[Mapping[str, Any] | Any]) -> list[NormalizedSpan]:
        return [self.normalize(span) for span in spans]


def normalize_otel_span(span: Mapping[str, Any] | Any, *, include_content: bool = False) -> NormalizedSpan:
    return OTelEnvelopeNormalizer(include_content=include_content).normalize(span)


def normalize_otel_spans(
    spans: Sequence[Mapping[str, Any] | Any], *, include_content: bool = False
) -> list[NormalizedSpan]:
    return OTelEnvelopeNormalizer(include_content=include_content).normalize_many(spans)
