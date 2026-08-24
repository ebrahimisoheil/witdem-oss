"""Shared mapping from normalized input facts to the existing Witdem graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from witdem.analytics.core import Execution, Link, Operation
from witdem.analytics.cost import (
    PRICE_SNAPSHOT_VERSION,
    PRICING_CATALOG_VERSION,
    cost_unavailable_reason,
    estimate_chat_cost,
    resolve_pricing_model,
)
from witdem.analytics.runtime import NormalizedExecutionGraph
from witdem.integrations.models.normalized_operation import NormalizedOperation
from witdem.integrations.models.normalized_span import NormalizedSpan
from witdem.integrations.normalizers.genai import GenAIDialectNormalizer
from witdem.integrations.normalizers.openinference import OpenInferenceNormalizer
from witdem.integrations.normalizers.otel import OTelEnvelopeNormalizer


def _first(attrs: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if attrs.get(key) is not None:
            return attrs[key]
    return None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _semantic_operation(span: NormalizedSpan) -> NormalizedOperation:
    attrs = span.attributes
    if "openinference.span.kind" in attrs or any(
        str(key).startswith(("llm.", "tool.", "retriever.", "agent.")) for key in attrs
    ):
        return OpenInferenceNormalizer().normalize(span)
    if any(key.startswith("gen_ai.") for key in attrs):
        return GenAIDialectNormalizer().normalize(span)
    return NormalizedOperation(
        source_id=str(span.span_id or span.trace_id or span.name),
        trace_id=span.trace_id,
        parent_source_id=span.parent_span_id,
        name=span.name,
        kind="operation",
        status=span.status,
        started_at=span.start_time,
        ended_at=span.end_time,
        attributes=dict(attrs),
        source={"dialect": "otel", "version": "otel-envelope-0.1"},
        span=span,
    )


def _operation_from_normalized(
    item: NormalizedOperation,
    *,
    runtime: str,
    telemetry_path: str,
    kind_override: str | None = None,
    name_override: str | None = None,
) -> Operation:
    attrs = dict(item.attributes)
    source = dict(item.source)
    source.update(
        {
            "runtime": runtime,
            "telemetry_path": telemetry_path,
            "source_id": item.source_id,
            "trace_id": item.trace_id,
            "parent_source_id": item.parent_source_id,
        }
    )
    attrs["witdem.source.runtime"] = runtime
    attrs["witdem.source.telemetry_path"] = telemetry_path
    attrs["witdem.source"] = source
    if item.provider is not None:
        attrs["provider"] = item.provider
    model = item.response_model or item.request_model
    if model is not None:
        attrs["model"] = model
    for key, value in item.usage.items():
        attrs[key] = value
    if item.tool_name is not None:
        attrs["tool.name"] = item.tool_name
    if item.tool_call_id is not None:
        attrs["tool.call.id"] = item.tool_call_id
    if item.agent_name is not None:
        attrs["agent.name"] = item.agent_name
    explicit_cost = _first(attrs, "cost_usd", "pf.cost.amount", "gen_ai.cost.usd", "pf.cost_usd")
    if isinstance(explicit_cost, (int, float)) and not isinstance(explicit_cost, bool):
        attrs["cost_usd"] = float(explicit_cost)
        attrs.setdefault(
            "cost_source",
            _first(attrs, "gen_ai.cost.source", "cost.source", "pf.cost.source") or "telemetry",
        )
    elif item.provider is not None and model is not None:
        estimated_cost = estimate_chat_cost(item.provider, model, item.usage)
        if estimated_cost is not None:
            resolution = resolve_pricing_model(item.provider, model)
            attrs["cost_usd"] = estimated_cost
            attrs["cost_source"] = "provider_price_snapshot"
            attrs["cost_price_snapshot"] = PRICE_SNAPSHOT_VERSION
            attrs["cost_pricing_catalog_version"] = PRICING_CATALOG_VERSION
            attrs["cost_pricing_source"] = resolution.source
            attrs["cost_pricing_model"] = resolution.pricing_model
            attrs["cost_model_match"] = resolution.match
    resolved_kind = kind_override or item.kind
    if resolved_kind == "model" and "cost_usd" not in attrs:
        reason = cost_unavailable_reason(item.provider, model, item.usage)
        if reason is not None:
            attrs["cost_unavailable_reason"] = reason
    if item.span is not None:
        if item.span.exception is not None:
            attrs["exception"] = item.span.exception
        if item.span.status_description:
            attrs["error.message"] = item.span.status_description
        if item.span.resource:
            attrs["otel.resource"] = dict(item.span.resource)
        if item.span.instrumentation_scope:
            attrs["otel.instrumentation_scope"] = dict(item.span.instrumentation_scope)
    return Operation(
        operation_id=item.source_id,
        execution_id=str(item.trace_id or "runtime-execution"),
        trace_id=item.trace_id,
        span_id=item.source_id,
        parent_span_id=item.parent_source_id,
        kind=resolved_kind,
        name=name_override or item.name,
        status=item.status,
        started_at=_timestamp(item.started_at),
        ended_at=_timestamp(item.ended_at),
        attempt=item.attempt,
        attributes=attrs,
    )


def graph_from_spans(
    spans: Sequence[NormalizedSpan],
    *,
    execution_id: str | None,
    runtime: str,
    telemetry_path: str,
    operation_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    explicit_links: Sequence[Mapping[str, Any]] = (),
    execution_attributes: Mapping[str, Any] | None = None,
) -> NormalizedExecutionGraph:
    """Map only observed normalized spans and explicit runtime relations."""

    if not spans:
        raise ValueError("cannot build a Witdem graph from no spans")
    inferred_execution_id = execution_id or next(
        (span.trace_id for span in spans if span.trace_id),
        next((span.span_id for span in spans if span.span_id), "runtime-execution"),
    )
    operations: list[Operation] = []
    for span in spans:
        item = _semantic_operation(span)
        override = dict((operation_overrides or {}).get(str(item.source_id), {}))
        operations.append(
            _operation_from_normalized(
                item,
                runtime=runtime,
                telemetry_path=telemetry_path,
                kind_override=str(override.get("kind") or item.attributes.get("witdem.runtime.kind") or "") or None,
                name_override=str(override.get("name") or item.attributes.get("witdem.runtime.name") or "") or None,
            )
        )
    operations = [operation.model_copy(update={"execution_id": inferred_execution_id}) for operation in operations]
    operation_ids = {operation.operation_id for operation in operations}
    links: list[Link] = []
    for operation in operations:
        if operation.parent_span_id and operation.parent_span_id in operation_ids:
            links.append(
                Link(
                    execution_id=inferred_execution_id,
                    source_id=operation.parent_span_id,
                    target_id=operation.operation_id,
                    relation="parent",
                    attributes={"source": "otel.parent_span_id", "runtime": runtime},
                )
            )
    for span in spans:
        target_id = str(span.span_id or "")
        if not target_id:
            continue
        for source_link in span.links:
            if source_link.span_id:
                links.append(
                    Link(
                        execution_id=inferred_execution_id,
                        source_id=str(source_link.span_id),
                        target_id=target_id,
                        relation="otel_link",
                        attributes={
                            "source": "otel.links",
                            "trace_id": source_link.trace_id,
                            **dict(source_link.attributes),
                        },
                    )
                )
    for raw_link in explicit_links:
        source_value = raw_link.get("source_id") or raw_link.get("source")
        target_value = raw_link.get("target_id") or raw_link.get("target")
        source_id = str(source_value) if source_value else None
        link_target_id = str(target_value) if target_value else None
        if not source_id or not link_target_id:
            continue
        link_attrs = dict(raw_link.get("attributes") or {})
        link_attrs.setdefault("source", "runtime.explicit_relation")
        links.append(
            Link(
                execution_id=inferred_execution_id,
                source_id=str(source_id),
                target_id=link_target_id,
                relation=str(raw_link.get("relation") or "runtime_relation"),
                attributes=link_attrs,
            )
        )
    starts = [operation.started_at for operation in operations if operation.started_at]
    ends = [operation.ended_at for operation in operations if operation.ended_at]
    root_statuses = [operation.status for operation in operations if operation.parent_span_id is None]
    status = "error" if "error" in root_statuses else ("ok" if root_statuses and all(root_statuses) else None)
    attrs = {"witdem.source.runtime": runtime, "witdem.source.telemetry_path": telemetry_path}
    root_span = next((span for span in spans if span.parent_span_id is None), None)
    if root_span is not None:
        explicit_name = root_span.attributes.get("witdem.execution.name")
        if explicit_name:
            attrs["witdem.execution.name"] = str(explicit_name)
        elif root_span.name:
            attrs["execution.name"] = str(root_span.name)
    attrs.update(dict(execution_attributes or {}))
    execution = Execution(
        execution_id=inferred_execution_id,
        runtime_id=runtime,
        started_at=min(starts) if starts else None,
        ended_at=max(ends) if ends else None,
        status=status,
        attributes=attrs,
    )
    return NormalizedExecutionGraph(execution=execution, operations=operations, links=links, raw_span_count=len(spans))


def normalize_raw_spans(spans: Sequence[Mapping[str, Any]], *, include_content: bool = False) -> list[NormalizedSpan]:
    return OTelEnvelopeNormalizer(include_content=include_content).normalize_many(spans)
