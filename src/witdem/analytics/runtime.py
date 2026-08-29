"""Domain-neutral projections and insights over Haystack/OpenTelemetry spans.

The functions in this module are deliberately read-only.  Raw span JSON remains
the source artifact; the models here are an analytical projection that can be
recomputed when reporting rules change.  Product Factory labels are accepted as
optional inputs and are never needed to build the physical graph.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from witdem.analytics.core import Event, Execution, Link, Operation
from witdem.analytics.cost import (
    PRICE_SNAPSHOT_VERSION,
    PRICING_CATALOG_VERSION,
    cost_unavailable_reason,
    estimate_chat_cost,
    resolve_pricing_model,
)
from witdem.analytics.derived import derived_termination_category
from witdem.analytics.identity import (
    canonical_loop_signature,
    canonical_operation_key,
    canonical_path_signature,
    canonical_stage_key,
    canonical_tool_key,
    display_operation,
    display_stage,
)

Provenance = Literal[
    "observed_span",
    "observed_provider_response",
    "observed_invocation_configuration",
    "inferred_role_manifest",
    "calculated_observed_usage_price_snapshot",
    "calculated_from_observed_usage",
    "unknown",
]


class NormalizedExecutionGraph(BaseModel):
    """A domain-neutral analytical projection of one runtime execution."""

    model_config = ConfigDict(extra="forbid")

    execution: Execution
    operations: list[Operation] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    raw_span_count: int = Field(default=0, ge=0)


class ReplayNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    operation_key: str
    tool_key: str | None = None
    stage_key: str | None = None
    kind: str
    name: str
    runtime_name: str | None = None
    display_name: str | None = None
    semantic_stage: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    duration_seconds: float | None = None
    status: str | None = None
    role: str | None = None
    provider: str | None = None
    provider_provenance: Provenance | None = None
    model: str | None = None
    model_provenance: Provenance | None = None
    input_tokens: int | float | None = None
    output_tokens: int | float | None = None
    total_tokens: int | float | None = None
    usage_provenance: Provenance | None = None
    known_cost: float | None = None
    currency: str | None = None
    cost_provenance: Provenance | None = None
    pricing_snapshot: str | None = None
    cost_complete: bool | None = None
    tool: str | None = None
    tool_name: str | None = None
    attempt: int | None = None
    parent: str | None = None
    parent_operation_id: str | None = None
    failure_type: str | None = None
    failure_reason: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ReplayEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    relation: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ReplayTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    kind: str
    name: str
    operation_id: str | None = None
    status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplayMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    type: str
    label: str
    operation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReplayGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution: Execution
    nodes: list[ReplayNode] = Field(default_factory=list)
    edges: list[ReplayEdge] = Field(default_factory=list)
    timeline: list[ReplayTimelineEntry] = Field(default_factory=list)
    markers: list[ReplayMarker] = Field(default_factory=list)


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
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


def _status(span: Mapping[str, Any]) -> str | None:
    raw = span.get("status")
    if isinstance(raw, Mapping):
        raw = raw.get("status_code")
    if raw is None:
        return None
    value = str(raw).split(".")[-1].lower()
    return {"unset": "unset", "ok": "ok", "error": "error"}.get(value, value)


def _attribute(span: Mapping[str, Any], *names: str) -> Any:
    attributes = span.get("attributes")
    if not isinstance(attributes, Mapping):
        attributes = {}
    for name in names:
        if attributes.get(name) is not None:
            return attributes[name]
    return None


def _attribute_with_source(span: Mapping[str, Any], *names: str) -> tuple[Any, str | None]:
    attributes = span.get("attributes")
    if not isinstance(attributes, Mapping):
        attributes = {}
    for name in names:
        if attributes.get(name) is not None:
            return attributes[name], name
    return None, None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _usage_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Read only exact usage attributes emitted by the provider boundary."""

    aliases = {
        "input_tokens": (
            "gen_ai.usage.input_tokens",
            "usage.input_tokens",
            "prompt_tokens",
        ),
        "output_tokens": (
            "gen_ai.usage.output_tokens",
            "usage.output_tokens",
            "completion_tokens",
        ),
        "total_tokens": ("gen_ai.usage.total_tokens", "usage.total_tokens", "total_tokens"),
    }
    usage: dict[str, Any] = {}
    for target, names in aliases.items():
        for name in names:
            value = _number(attributes.get(name))
            if value is not None:
                usage[target] = value
                break
    if "total_tokens" not in usage and {"input_tokens", "output_tokens"} <= usage.keys():
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["usage_provenance"] = "calculated_from_observed_usage"
    elif usage:
        usage["usage_provenance"] = str(attributes.get("pf.usage_provenance") or "observed_span")
    return usage


def _provenance(value: Any, default: Provenance = "unknown") -> Provenance:
    allowed = {
        "observed_span",
        "observed_provider_response",
        "observed_invocation_configuration",
        "inferred_role_manifest",
        "calculated_observed_usage_price_snapshot",
        "calculated_from_observed_usage",
        "unknown",
    }
    text = str(value) if value is not None else default
    return cast(Provenance, text) if text in allowed else default


def _display_name(span: Mapping[str, Any], kind: str) -> str:
    raw_name = str(span.get("name") or "operation")
    if kind == "component":
        return str(_attribute(span, "haystack.component.name") or raw_name)
    if kind == "tool":
        return str(_attribute(span, "haystack.tool.name") or raw_name)
    if kind == "agent_step":
        return str(_attribute(span, "witdem.agent.step.name") or raw_name)
    return raw_name


def _span_failed(span: Mapping[str, Any]) -> bool:
    status = span.get("status")
    if not isinstance(status, Mapping):
        return False
    return str(status.get("status_code") or "").casefold().endswith("error")


def _enrich_haystack_agent_steps(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Name native Haystack Agent iterations from their observed children.

    Haystack emits the stable parent name ``haystack.agent.step`` plus a
    zero-based index. Its direct tool children contain the useful action name.
    A successful model-only step is the terminal answer step. This structural
    enrichment also upgrades retained telemetry produced before the SDK began
    writing the equivalent attributes.
    """

    enriched: list[dict[str, Any]] = [dict(row) for row in rows]
    children: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in enriched:
        parent = row.get("parent_span_id")
        if parent:
            children[str(parent)].append(row)
    for row in enriched:
        if str(row.get("name") or "").casefold() != "haystack.agent.step":
            continue
        attributes = dict(row.get("attributes") or {})
        if attributes.get("witdem.agent.step.name"):
            continue
        direct_children = children.get(str(row.get("span_id") or ""), [])
        tool_names = [
            str(name)
            for child in direct_children
            if str(child.get("name") or "").casefold().endswith(".tool")
            and (name := _attribute(child, "haystack.tool.name"))
        ]
        if tool_names:
            unique_tools = list(dict.fromkeys(tool_names))
            attributes["witdem.agent.step.name"] = (
                tool_names[0] if len(tool_names) == 1 else f"{len(tool_names)} tool calls"
            )
            attributes["witdem.agent.step.action"] = "tool_call"
            attributes["witdem.agent.step.tools"] = unique_tools
            attributes["witdem.agent.step.name_provenance"] = "observed_child_tool"
        else:
            model_children = [
                child
                for child in direct_children
                if str(child.get("name") or "").casefold().endswith(".llm")
            ]
            if model_children:
                failed = _span_failed(row) or any(_span_failed(child) for child in model_children)
                attributes["witdem.agent.step.name"] = "failed_step" if failed else "final_answer"
                attributes["witdem.agent.step.action"] = "failed" if failed else "final_answer"
                attributes["witdem.agent.step.name_provenance"] = "observed_tool_free_model_step"
            else:
                step = attributes.get("haystack.agent.step")
                number = int(step) + 1 if isinstance(step, (int, float)) and not isinstance(step, bool) else 1
                attributes["witdem.agent.step.name"] = f"step_{number}"
                attributes["witdem.agent.step.name_provenance"] = "observed_step_index"
        row["attributes"] = attributes
    result: list[Mapping[str, Any]] = []
    result.extend(enriched)
    return result


def _kind(name: str, attributes: Mapping[str, Any] | None = None) -> str:
    normalized = str((attributes or {}).get("witdem.operation.kind") or "").casefold()
    if normalized in {"workflow", "pipeline", "agent", "agent_step", "component", "tool", "model", "operation"}:
        return normalized
    lowered = name.casefold()
    if lowered == "research.execution" or lowered.endswith(".execution"):
        return "workflow"
    if "pipeline" in lowered:
        return "pipeline"
    if "component" in lowered:
        return "component"
    if lowered.endswith(".agent.run") or lowered == "agent":
        return "agent"
    if "tool" in lowered:
        return "tool"
    if "llm" in lowered or "model" in lowered or lowered.endswith(".chat"):
        return "model"
    if "agent.step" in lowered or lowered.endswith(".step"):
        return "agent_step"
    return "operation"


def _role(name: str, kind: str, attributes: Mapping[str, Any], ancestors: Sequence[str]) -> str | None:
    explicit = attributes.get("role") or attributes.get("pf.role")
    if explicit:
        return str(explicit)
    own = name.casefold()
    if any(token in own for token in ("validat", "verification")):
        return "verification"
    if any(token in own for token in ("extract", "profile")):
        return "extraction"
    ancestor_text = " ".join(ancestor for ancestor in ancestors if ancestor != "research.execution").casefold()
    text = " ".join([own, ancestor_text])
    if any(token in text for token in ("research", "agent", "tool", "evidence")):
        return "research"
    return None


def _attempt(span: Mapping[str, Any], occurrence: int) -> int | None:
    value = _attribute(
        span,
        "haystack.agent.step",
        "pf.research_pass",
        "pf.validation_attempt",
        "haystack.component.visits",
    )
    if isinstance(value, bool):
        return occurrence
    if isinstance(value, (int, float)):
        return max(1, int(value))
    return occurrence if occurrence > 1 else None


def _metadata_by_role(providers: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for provider in providers or []:
        role = provider.get("role")
        if role:
            result[str(role)] = provider
    return result


def _merge_haystack_usage_summary(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Merge SDK aggregate usage into native Haystack model spans.

    Haystack exposes aggregate usage on the Agent result but omits it from its
    native LLM spans. The SDK records that result as a temporary summary span;
    this adapter applies identity to every physical model call, applies the
    aggregate usage once, and removes the temporary span from the graph.
    """

    summaries = [row for row in rows if _attribute(row, "witdem.haystack.usage_summary") is True]
    if not summaries:
        return rows
    physical = [row for row in rows if row not in summaries]
    model_rows = [
        row
        for row in physical
        if _kind(str(row.get("name") or "operation"), row.get("attributes")) == "model"
    ]
    if not model_rows:
        return rows

    summary = summaries[-1]
    provider = _attribute(summary, "provider", "gen_ai.provider.name")
    model = _attribute(summary, "model", "gen_ai.response.model", "gen_ai.request.model")
    for row in model_rows:
        attributes = dict(row.get("attributes") or {})
        if provider is not None:
            attributes.setdefault("gen_ai.provider.name", provider)
            attributes.setdefault("pf.provider_provenance", "observed_invocation_configuration")
        if model is not None:
            attributes.setdefault("gen_ai.request.model", model)
            attributes.setdefault("pf.model_provenance", "observed_invocation_configuration")
        cast(dict[str, Any], row)["attributes"] = attributes

    usage_target = max(
        model_rows,
        key=lambda row: _timestamp(row.get("start_time_unix_nano")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    target_attributes = dict(usage_target.get("attributes") or {})
    for name in (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
    ):
        value = _attribute(summary, name)
        if value is not None:
            target_attributes[name] = value
    target_attributes["pf.usage_provenance"] = "observed_provider_response"
    target_attributes["witdem.haystack.aggregate_usage"] = True
    cast(dict[str, Any], usage_target)["attributes"] = target_attributes
    return physical


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, Sequence) and not isinstance(decoded, (str, bytes, bytearray)):
            return tuple(str(item) for item in decoded if isinstance(item, str) and item)
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value if isinstance(item, str) and item)
    return ()


def _topology_edges(value: Any) -> tuple[dict[str, Any], ...]:
    edges: list[dict[str, Any]] = []
    for encoded in _string_values(value):
        try:
            edge = json.loads(encoded)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(edge, Mapping):
            continue
        required = ("source_component", "source_socket", "target_component", "target_socket")
        if all(isinstance(edge.get(key), str) and edge.get(key) for key in required):
            edges.append(dict(edge))
    return tuple(edges)


def _operation_time(operation: Operation, *, end: bool = False) -> datetime:
    value = operation.ended_at if end else operation.started_at
    return value or operation.started_at or operation.ended_at or datetime.min.replace(tzinfo=timezone.utc)


def _append_explicit_haystack_links(
    links: list[Link],
    operations: Sequence[Operation],
    execution_id: str,
) -> bool:
    """Resolve emitted Haystack sockets to concrete operation instances.

    Static incident edges are attached to every component span by the SDK.
    Runtime input/output socket-name lists identify the active subset without
    exposing any values. For repeated components, the nearest preceding source
    instance owns the activation; existing repeat links still express the
    vertical retry relationship.
    """

    by_component: dict[str, list[Operation]] = defaultdict(list)
    for operation in operations:
        component_id = operation.attributes.get("witdem.haystack.component.id")
        if isinstance(component_id, str) and component_id:
            by_component[component_id].append(operation)
    if not by_component:
        return False
    for instances in by_component.values():
        instances.sort(key=_operation_time)

    added = False
    existing = {(link.source_id, link.target_id, link.relation) for link in links}
    for target in operations:
        target_component = target.attributes.get("witdem.haystack.component.id")
        active_inputs = set(_string_values(target.attributes.get("witdem.haystack.runtime.input_sockets")))
        incoming = _topology_edges(target.attributes.get("witdem.haystack.topology.incoming"))
        if not isinstance(target_component, str) or not active_inputs or not incoming:
            continue
        for edge in incoming:
            if edge["target_component"] != target_component or edge["target_socket"] not in active_inputs:
                continue
            candidates = []
            for source in by_component.get(str(edge["source_component"]), []):
                if source.operation_id == target.operation_id:
                    continue
                emitted = set(
                    _string_values(source.attributes.get("witdem.haystack.runtime.emitted_sockets"))
                )
                if edge["source_socket"] not in emitted:
                    continue
                if _operation_time(source, end=True) <= _operation_time(target):
                    candidates.append(source)
            if not candidates:
                continue
            source = max(candidates, key=lambda operation: _operation_time(operation, end=True))
            retry = bool(edge.get("retry")) or (
                bool(edge.get("cycle"))
                and str(edge.get("source_component")) == str(edge.get("target_component"))
            )
            relation = "workflow_retry" if retry else "workflow"
            key = (source.operation_id, target.operation_id, relation)
            if key in existing:
                continue
            links.append(
                Link(
                    execution_id=execution_id,
                    source_id=source.operation_id,
                    target_id=target.operation_id,
                    relation=relation,
                    attributes={
                        "framework": "haystack",
                        "explicit": True,
                        "source_component": edge["source_component"],
                        "source_socket": edge["source_socket"],
                        "target_component": edge["target_component"],
                        "target_socket": edge["target_socket"],
                        "cycle": bool(edge.get("cycle")),
                        "retry": bool(edge.get("retry")),
                    },
                )
            )
            existing.add(key)
            added = True
    return added


def normalize_haystack_spans(
    spans: Sequence[Mapping[str, Any]],
    *,
    execution_id: str | None = None,
    runtime_id: str | None = None,
    providers: Sequence[Mapping[str, Any]] | None = None,
) -> NormalizedExecutionGraph:
    """Normalize stored Haystack/OpenTelemetry span envelopes.

    The adapter understands only physical runtime conventions.  Domain labels,
    semantic events, and Product Factory configuration are not required.
    """

    rows = _enrich_haystack_agent_steps(_merge_haystack_usage_summary([dict(span) for span in spans]))
    requested_id = execution_id
    def span_execution_id(row: Mapping[str, Any]) -> Any:
        return _attribute(row, "witdem.execution_id")

    if execution_id:
        matching = [row for row in rows if span_execution_id(row) == execution_id]
        if matching:
            traces = {str(row.get("trace_id")) for row in matching if row.get("trace_id")}
            rows = [
                row
                for row in rows
                if span_execution_id(row) == execution_id or row.get("trace_id") in traces
            ]
    if not rows:
        raise ValueError("no Haystack/OpenTelemetry spans matched the execution")
    inferred_execution_id = requested_id or next(
        (str(span_execution_id(row)) for row in rows if span_execution_id(row)),
        None,
    )
    trace_ids = {str(row.get("trace_id")) for row in rows if row.get("trace_id")}
    trace_id = next(iter(trace_ids), None)
    inferred_execution_id = inferred_execution_id or trace_id or "runtime-execution"

    rows.sort(key=lambda row: _timestamp(row.get("start_time_unix_nano")) or datetime.min.replace(tzinfo=timezone.utc))
    by_span = {str(row.get("span_id")): row for row in rows if row.get("span_id")}
    operation_ids = set(by_span)
    occurrences: dict[tuple[str, str], int] = defaultdict(int)
    operations: list[Operation] = []
    for index, row in enumerate(rows):
        source_name = str(row.get("name") or "operation")
        kind = _kind(source_name, row.get("attributes") if isinstance(row.get("attributes"), Mapping) else None)
        name = _display_name(row, kind)
        key = (kind, name)
        occurrences[key] += 1
        span_id = str(row.get("span_id") or f"span-{index + 1}")
        attributes = dict(row.get("attributes") or {})
        attributes["runtime.name"] = source_name
        attributes["runtime.kind"] = str(row.get("kind")) if row.get("kind") is not None else None
        source_status = row.get("status")
        if source_status is not None:
            attributes["runtime.status"] = source_status
        role = _role(name, kind, attributes, _ancestor_names(row, by_span))
        if role:
            attributes.setdefault("role", role)
        provider, _ = _attribute_with_source(row, "provider", "gen_ai.system", "gen_ai.provider.name", "llm.provider")
        model, _ = _attribute_with_source(row, "model", "gen_ai.response.model", "gen_ai.request.model", "llm.model")
        provider_provenance = (
            _provenance(_attribute(row, "pf.provider_provenance")) if provider is not None else "unknown"
        )
        model_provenance = _provenance(_attribute(row, "pf.model_provenance")) if model is not None else "unknown"
        if provider is not None and _attribute(row, "pf.provider_provenance") is None:
            provider_provenance = "observed_span"
        if model is not None and _attribute(row, "pf.model_provenance") is None:
            model_provenance = "observed_span"
        role_metadata = _metadata_by_role(providers).get(role or "")
        if role_metadata:
            if provider is None and role_metadata.get("provider") is not None:
                provider = role_metadata.get("provider")
                provider_provenance = "inferred_role_manifest"
            if model is None and role_metadata.get("model") is not None:
                model = role_metadata.get("model")
                model_provenance = "inferred_role_manifest"
            attributes.setdefault("provider_source", "run_manifest")
        if provider is not None:
            attributes.setdefault("provider", provider)
            attributes.setdefault("provider_provenance", provider_provenance)
        if model is not None:
            attributes.setdefault("model", model)
            attributes.setdefault("model_provenance", model_provenance)
        usage = _usage_attributes(attributes)
        attributes.update({key: value for key, value in usage.items() if key != "usage_provenance"})
        if usage.get("usage_provenance"):
            attributes.setdefault("usage_provenance", usage["usage_provenance"])
        known_cost = _number(
            _attribute(row, "pf.cost.amount", "cost.amount", "cost_usd", "gen_ai.cost.usd", "pf.cost_usd")
        )
        cost_currency = _attribute(row, "pf.cost.currency", "cost.currency", "currency") or "USD"
        cost_source = _attribute(row, "pf.cost.source", "cost.source", "gen_ai.cost.source")
        model_usage = {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            **{
                key: value
                for key, value in usage.items()
                if key not in {"input_tokens", "output_tokens", "usage_provenance"}
                and isinstance(value, (int, float))
            },
        }
        if (
            known_cost is None
            and kind == "model"
            and provider is not None
            and model is not None
            and provider_provenance
            in {"observed_span", "observed_provider_response", "observed_invocation_configuration"}
            and model_provenance in {"observed_span", "observed_provider_response", "observed_invocation_configuration"}
            and all(isinstance(value, (int, float)) for value in model_usage.values())
        ):
            known_cost = estimate_chat_cost(str(provider), str(model), model_usage, context=attributes)
            if known_cost is not None:
                pricing_resolution = resolve_pricing_model(str(provider), str(model))
                cost_source = "price_snapshot"
                attributes["cost_pricing_snapshot"] = PRICE_SNAPSHOT_VERSION
                attributes["cost_pricing_catalog_version"] = PRICING_CATALOG_VERSION
                attributes["cost_pricing_source"] = pricing_resolution.source
                attributes["cost_provenance"] = "calculated_observed_usage_price_snapshot"
                attributes["cost_pricing_model"] = pricing_resolution.pricing_model
                attributes["cost_model_resolution"] = pricing_resolution.match
                if pricing_resolution.version is not None:
                    attributes["cost_model_version"] = pricing_resolution.version
        if known_cost is not None:
            attributes.setdefault("cost_usd", float(known_cost) if str(cost_currency).upper() == "USD" else known_cost)
            attributes.setdefault("cost_currency", str(cost_currency))
            attributes.setdefault("cost_scope", "operation")
            attributes.setdefault("cost_known", True)
            attributes.setdefault("cost_complete", True)
            if kind != "model":
                attributes.setdefault("cost_provenance", "observed_span")
                if cost_source:
                    attributes.setdefault("cost_source", str(cost_source))
        else:
            attributes.setdefault("cost_known", False)
            attributes.setdefault("cost_complete", False)
            reason = cost_unavailable_reason(
                str(provider) if provider is not None else None,
                str(model) if model is not None else None,
                model_usage if kind == "model" else None,
                context=attributes,
            )
            if reason is not None:
                attributes.setdefault("cost_unavailable_reason", reason)
        operations.append(
            Operation(
                operation_id=span_id,
                execution_id=inferred_execution_id,
                trace_id=str(row.get("trace_id")) if row.get("trace_id") else trace_id,
                span_id=span_id,
                parent_span_id=str(row.get("parent_span_id")) if row.get("parent_span_id") else None,
                kind=kind,
                name=name,
                status=_status(row),
                started_at=_timestamp(row.get("start_time_unix_nano")),
                ended_at=_timestamp(row.get("end_time_unix_nano")),
                attempt=_attempt(row, occurrences[key]),
                attributes=attributes,
            )
        )

    operation_map = {operation.span_id: operation for operation in operations if operation.span_id}
    links: list[Link] = []
    for operation in operations:
        if operation.parent_span_id and operation.parent_span_id in operation_ids:
            links.append(
                Link(
                    execution_id=inferred_execution_id,
                    source_id=operation.parent_span_id,
                    target_id=operation.operation_id,
                    relation="parent",
                )
            )
    _append_explicit_haystack_links(links, operations, inferred_execution_id)
    _append_repeat_links(links, operations, inferred_execution_id)
    starts = [operation.started_at for operation in operations if operation.started_at]
    ends = [operation.ended_at for operation in operations if operation.ended_at]
    root = next((operation for operation in operations if operation.kind == "workflow"), None)
    status = root.status if root else _aggregate_status(operations)
    execution = Execution(
        execution_id=inferred_execution_id,
        runtime_id=runtime_id,
        started_at=min(starts) if starts else None,
        ended_at=max(ends) if ends else None,
        status=status,
        attributes={"trace_ids": sorted(trace_ids), "raw_span_count": len(rows)},
    )
    # Ensure links only point to normalized operations when a source artifact had
    # a parent that was not part of the selected trace.
    links = [link for link in links if link.source_id in operation_map and link.target_id in operation_map]
    return NormalizedExecutionGraph(
        execution=execution,
        operations=operations,
        links=links,
        raw_span_count=len(rows),
    )


def _ancestor_names(span: Mapping[str, Any], by_span: Mapping[str, Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    parent = span.get("parent_span_id")
    seen: set[str] = set()
    while parent and str(parent) not in seen:
        seen.add(str(parent))
        ancestor = by_span.get(str(parent))
        if ancestor is None:
            break
        names.append(str(_attribute(ancestor, "haystack.component.name") or ancestor.get("name") or ""))
        parent = ancestor.get("parent_span_id")
    return names


def _aggregate_status(operations: Sequence[Operation]) -> str | None:
    statuses = {operation.status for operation in operations}
    if "error" in statuses:
        return "error"
    if "ok" in statuses:
        return "ok"
    return next(iter(statuses), None)


def _append_repeat_links(links: list[Link], operations: Sequence[Operation], execution_id: str) -> None:
    """Add conservative sibling repetition edges for identical physical work."""

    previous: dict[str, Operation] = {}
    for operation in operations:
        key = canonical_operation_key(operation)
        prior = previous.get(key)
        if prior and prior.parent_span_id == operation.parent_span_id:
            links.append(
                Link(
                    execution_id=execution_id,
                    source_id=prior.operation_id,
                    target_id=operation.operation_id,
                    relation="repeat",
                    attributes={"basis": "same_kind_and_name"},
                )
            )
        previous[key] = operation


def _duration(operation: Operation) -> float | None:
    if operation.started_at is None or operation.ended_at is None:
        return None
    return max(0.0, (operation.ended_at - operation.started_at).total_seconds())


def _value(operation: Operation, *keys: str) -> Any:
    for key in keys:
        if operation.attributes.get(key) is not None:
            return operation.attributes[key]
    return None


def _provider_fields(operation: Operation) -> dict[str, str | None]:
    return {
        "role": str(_value(operation, "role", "product_factory.role", "pf.role", "gen_ai.operation.role"))
        if _value(operation, "role", "product_factory.role", "pf.role", "gen_ai.operation.role") is not None
        else None,
        "provider": str(_value(operation, "provider")) if _value(operation, "provider") is not None else None,
        "model": str(_value(operation, "model")) if _value(operation, "model") is not None else None,
    }


def _failure_reason(operation: Operation) -> str | None:
    status = _value(operation, "runtime.status")
    if isinstance(status, Mapping):
        description = status.get("description")
        if description:
            return str(description)
    for key in ("failure.reason", "failure_message", "error", "exception.message"):
        value = _value(operation, key)
        if value:
            return str(value)
    return None


def _known_cost(operation: Operation) -> float | None:
    cost = _value(operation, "cost_usd", "pf.cost.amount", "gen_ai.cost.usd", "pf.cost_usd")
    return float(cost) if isinstance(cost, (int, float)) else None


def _operation_sequence(graph: NormalizedExecutionGraph) -> list[Operation]:
    return sorted(
        [
            operation
            for operation in graph.operations
            if operation.kind not in {"workflow", "pipeline", "agent"}
            and "tracing.auto_enable" not in operation.name.casefold()
        ],
        key=lambda operation: operation.started_at or datetime.min.replace(tzinfo=timezone.utc),
    )


def derive_repeated_patterns(graph: NormalizedExecutionGraph) -> list[dict[str, Any]]:
    """Detect conservative contiguous repeated physical patterns."""

    sequence = _operation_sequence(graph)
    result: list[dict[str, Any]] = []
    max_pattern = min(4, len(sequence) // 2)
    for width in range(max_pattern, 0, -1):
        index = 0
        while index + width * 2 <= len(sequence):
            first_operations = sequence[index : index + width]
            second_operations = sequence[index + width : index + width * 2]
            first_keys = [canonical_operation_key(operation) for operation in first_operations]
            second_keys = [canonical_operation_key(operation) for operation in second_operations]
            if first_keys != second_keys:
                index += 1
                continue
            # Equal labels are not evidence of replay. Frameworks commonly emit
            # several required components under one generic name (for example,
            # ``langchain.chain``). Count a physical repetition only when the
            # normalized telemetry identifies a later attempt explicitly.
            if not any((operation.attempt or 1) > 1 for operation in second_operations):
                index += 1
                continue
            iterations = 2
            while index + width * (iterations + 1) <= len(sequence):
                candidate = [
                    canonical_operation_key(operation)
                    for operation in sequence[index + width * iterations : index + width * (iterations + 1)]
                ]
                if candidate != first_keys:
                    break
                iterations += 1
            first = [display_operation(operation) for operation in first_operations]
            result.append(
                {
                    "pattern": first,
                    "pattern_keys": first_keys,
                    "loop_signature": canonical_loop_signature(first_keys),
                    "iterations": iterations,
                    "first_occurrence": index,
                    "last_occurrence": index + width * iterations - 1,
                    "eventual_status": sequence[index + width * iterations - 1].status,
                    "operation_ids": [
                        operation.operation_id for operation in sequence[index : index + width * iterations]
                    ],
                }
            )
            index += width * iterations
    # A longer pattern subsumes its contained shorter pattern at the same start.
    unique: dict[tuple[tuple[str, ...], int], dict[str, Any]] = {}
    for item in result:
        key = (tuple(item["pattern_keys"]), item["first_occurrence"])
        unique[key] = item
    return list(unique.values())


def _semantic_stage(operation: Operation, events: Sequence[Event], mapping: Mapping[str, str] | None) -> str | None:
    # SDK lifecycle records can share the application's active root span. They
    # describe the reporting boundary, not the physical operation, and must
    # never rename that span in the replay/serving projection.
    control_prefixes = ("execution.", "contract.")
    names = [
        event.name
        for event in events
        if event.span_id == operation.span_id
        and event.type == "event"
        and not event.name.startswith(control_prefixes)
    ]
    if not names:
        return None
    name = names[-1]
    return (mapping or {}).get(name, name)


def derive_replay_graph(
    graph: NormalizedExecutionGraph,
    *,
    events: Sequence[Event] = (),
    semantic_stage_map: Mapping[str, str] | None = None,
) -> ReplayGraph:
    """Build a future-viewer-ready replay graph from the physical projection."""

    nodes = []
    for operation in graph.operations:
        fields = _provider_fields(operation)
        known_cost = _known_cost(operation)
        semantic_stage = _semantic_stage(operation, events, semantic_stage_map)
        display_name = display_operation(operation, semantic_stage)
        tool_name = (
            str(_value(operation, "haystack.tool.name", "tool"))
            if _value(operation, "haystack.tool.name", "tool")
            else None
        )
        failure_reason = _failure_reason(operation)
        nodes.append(
            ReplayNode(
                id=operation.operation_id,
                operation_key=canonical_operation_key(operation, semantic_stage),
                tool_key=canonical_tool_key(operation) if operation.kind == "tool" else None,
                stage_key=canonical_stage_key(operation, semantic_stage),
                kind=operation.kind,
                name=display_name,
                runtime_name=(
                    str(_value(operation, "runtime.name")) if _value(operation, "runtime.name") else operation.name
                ),
                display_name=display_name,
                semantic_stage=semantic_stage,
                start=operation.started_at,
                end=operation.ended_at,
                duration_seconds=_duration(operation),
                status=operation.status,
                role=fields["role"],
                provider=fields["provider"],
                provider_provenance=_provenance(_value(operation, "provider_provenance"))
                if fields["provider"]
                else None,
                model=fields["model"],
                model_provenance=_provenance(_value(operation, "model_provenance")) if fields["model"] else None,
                input_tokens=_value(operation, "input_tokens"),
                output_tokens=_value(operation, "output_tokens"),
                total_tokens=_value(operation, "total_tokens"),
                usage_provenance=_provenance(_value(operation, "usage_provenance"))
                if _value(operation, "usage_provenance")
                else None,
                known_cost=known_cost,
                currency=(
                    str(_value(operation, "cost_currency", "pf.cost.currency") or "USD")
                    if known_cost is not None
                    else None
                ),
                cost_provenance=_provenance(_value(operation, "cost_provenance")) if known_cost is not None else None,
                pricing_snapshot=str(_value(operation, "cost_pricing_snapshot"))
                if _value(operation, "cost_pricing_snapshot")
                else None,
                cost_complete=(
                    bool(_value(operation, "cost_complete"))
                    if known_cost is not None
                    else (
                        False
                        if operation.kind in {"model", "tool"}
                        or any(
                            _value(operation, key) is not None
                            for key in ("pf.cost.amount", "cost_usd", "gen_ai.cost.usd", "pf.cost_usd")
                        )
                        else None
                    )
                ),
                tool=tool_name,
                tool_name=tool_name,
                attempt=operation.attempt,
                parent=operation.parent_span_id,
                parent_operation_id=operation.parent_span_id,
                failure_type=("status_error" if operation.status == "error" else None),
                failure_reason=failure_reason,
                attributes={
                    **operation.attributes,
                    "technical.trace_id": operation.trace_id,
                    "technical.span_id": operation.span_id,
                },
            )
        )
    edges = [
        ReplayEdge(source=link.source_id, target=link.target_id, relation=link.relation, attributes=link.attributes)
        for link in graph.links
    ]
    timeline: list[ReplayTimelineEntry] = []
    for operation in graph.operations:
        if operation.started_at:
            timeline.append(
                ReplayTimelineEntry(
                    timestamp=operation.started_at,
                    kind="operation.start",
                    name=display_operation(operation),
                    operation_id=operation.operation_id,
                    status=operation.status,
                )
            )
        if operation.ended_at:
            timeline.append(
                ReplayTimelineEntry(
                    timestamp=operation.ended_at,
                    kind="operation.end",
                    name=display_operation(operation),
                    operation_id=operation.operation_id,
                    status=operation.status,
                )
            )
    markers: list[ReplayMarker] = []
    for operation in graph.operations:
        if operation.status == "error":
            markers.append(_marker(operation, "failure", "operation failed"))
        if _value(operation, "pf.validation_valid") is False:
            markers.append(_marker(operation, "failure", "validation failed"))
        if _value(operation, "pf.correction_feedback") is True:
            markers.append(_marker(operation, "repair", "repair attempt"))
    for event in events:
        if event.type in {"decision", "acceptance", "recovery", "outcome"}:
            marker_type = "acceptance" if event.type == "acceptance" else event.type
            markers.append(
                ReplayMarker(
                    timestamp=event.timestamp,
                    type=marker_type,
                    label=event.name,
                    operation_id=event.span_id,
                    payload=event.payload,
                )
            )
        timeline.append(
            ReplayTimelineEntry(
                timestamp=event.timestamp,
                kind=f"event.{event.type}",
                name=event.name,
                operation_id=event.span_id,
                payload=event.payload,
            )
        )
    timeline.sort(key=lambda item: item.timestamp)
    markers.sort(key=lambda item: item.timestamp)
    return ReplayGraph(execution=graph.execution, nodes=nodes, edges=edges, timeline=timeline, markers=markers)


def _marker(operation: Operation, marker_type: str, label: str) -> ReplayMarker:
    return ReplayMarker(
        timestamp=operation.started_at or operation.ended_at or graph_time_fallback(),
        type=marker_type,
        label=label,
        operation_id=operation.operation_id,
        payload={"status": operation.status, "name": display_operation(operation)},
    )


def graph_time_fallback() -> datetime:
    return datetime.now(timezone.utc)


def derive_failure_stage(
    graph: NormalizedExecutionGraph,
    *,
    run: Mapping[str, Any] | None = None,
    events: Sequence[Event] = (),
    semantic_stage_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Attribute the meaningful break point using physical and semantic facts."""

    operations = _operation_sequence(graph)
    all_operations = sorted(
        graph.operations,
        key=lambda operation: operation.started_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    candidates = [operation for operation in operations if _value(operation, "pf.validation_valid") is False]
    if not candidates:
        candidates = [operation for operation in operations if operation.status == "error"]
    if not candidates:
        # Frameworks often report a caught orchestration failure only on their
        # workflow/graph wrapper. Keep leaf failures preferred, but do not make
        # wrapper-only failures disappear from attribution.
        candidates = [operation for operation in all_operations if operation.status == "error"]
    if not candidates:
        candidates = [operation for operation in operations if _value(operation, "pf.assessment_sufficient") is False]
    chosen = candidates[-1] if candidates else None
    error = str((run or {}).get("manifest", {}).get("error") or "")
    if not error:
        for event in reversed(events):
            if event.type == "outcome" and event.payload.get("error"):
                error = str(event.payload["error"])
                break
    if chosen is None:
        return {"primary_break_point": None, "reason": error or None, "evidence": []}
    attribution_operation = chosen
    if _value(chosen, "pf.validation_valid") is False:
        chosen_index = operations.index(chosen)
        attribution_operation = next(
            (
                operation
                for operation in reversed(operations[:chosen_index])
                if "extract" in operation.name.casefold()
                and (_value(operation, "provider") or _value(operation, "model"))
            ),
            chosen,
        )
    fields = _provider_fields(attribution_operation)
    chosen_display = display_operation(chosen)
    evidence = [f"{chosen_display} status={chosen.status}"]
    if _value(chosen, "pf.validation_valid") is False:
        evidence.append("runtime validation attribute reported false")
    if error:
        evidence.append(error)
    event_names = [event.name for event in events if event.span_id == chosen.operation_id]
    semantic_stage_value = (semantic_stage_map or {}).get(event_names[-1], event_names[-1]) if event_names else None
    semantic_stage_value = semantic_stage_value or chosen.name
    semantic_stage = display_stage(chosen, semantic_stage_value)
    validation = (run or {}).get("validation")
    validation_errors = validation.get("errors", []) if isinstance(validation, Mapping) else []
    run_state = (run or {}).get("state")
    repair_attempted = (
        bool(run_state.get("profile_repair_count", 0))
        if isinstance(run_state, Mapping)
        else any(_value(operation, "pf.correction_feedback") is True for operation in operations)
    )
    repair_result = None
    if repair_attempted:
        repair_result = (
            "succeeded"
            if any(_value(operation, "pf.validation_valid") is True for operation in operations)
            else "failed"
        )
    return {
        "primary_break_point": chosen_display,
        "primary_break_point_key": canonical_operation_key(chosen),
        "primary_failure_stage": semantic_stage,
        "primary_failure_stage_key": canonical_stage_key(chosen, semantic_stage_value),
        "operation_id": chosen.operation_id,
        "reason": error or f"{chosen_display} reported {chosen.status or 'an unsuccessful result'}",
        "failure_type": "validation_failure" if _value(chosen, "pf.validation_valid") is False else "operation_failure",
        "failure_message": _failure_reason(chosen) or error or None,
        "role": fields["role"],
        "provider": fields["provider"],
        "provider_provenance": _provenance(_value(attribution_operation, "provider_provenance"))
        if fields["provider"]
        else None,
        "model": fields["model"],
        "model_provenance": _provenance(_value(attribution_operation, "model_provenance")) if fields["model"] else None,
        "attribution_operation_id": attribution_operation.operation_id,
        "validation_errors": validation_errors,
        "repair_attempted": repair_attempted,
        "repair_result": repair_result,
        "evidence": evidence,
    }


def derive_repair_burden(
    graph: NormalizedExecutionGraph,
    *,
    events: Sequence[Event] = (),
) -> dict[str, Any]:
    operations = _operation_sequence(graph)
    repairs = [operation for operation in operations if _value(operation, "pf.correction_feedback") is True]
    repair_ids = {operation.operation_id for operation in repairs}
    for event in events:
        if event.type == "step" and event.payload.get("correction_feedback") and event.span_id:
            repair_ids.add(event.span_id)
    repair_operations = [operation for operation in operations if operation.operation_id in repair_ids]
    repair_cycle_operations = list(repair_operations)
    for repair in repair_operations:
        index = operations.index(repair)
        following = operations[index + 1 :]
        validation = next((operation for operation in following if "validat" in operation.name.casefold()), None)
        if validation is not None:
            repair_cycle_operations.append(validation)
    duration = sum(_duration(operation) or 0.0 for operation in repair_cycle_operations)
    validations = [operation for operation in operations if "validat" in operation.name.casefold()]
    successful = bool(
        repair_operations and any(_value(operation, "pf.validation_valid") is True for operation in validations)
    )
    return {
        "repair_count": len(repair_operations),
        "repair_cycles": len(repair_operations),
        "time_seconds": duration,
        "cost_known": False,
        "cost_usd": None,
        "success": successful if repair_operations else None,
        "operation_ids": [operation.operation_id for operation in repair_operations],
    }


def derive_role_contributions(graph: NormalizedExecutionGraph) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for operation in graph.operations:
        fields = _provider_fields(operation)
        key = (fields["role"] or "unknown", fields["provider"] or "unknown", fields["model"] or "unknown")
        entry = groups.setdefault(
            key,
            {
                "role": key[0],
                "provider": key[1],
                "model": key[2],
                "duration_seconds": 0.0,
                "cost_known": True,
                "cost_usd": 0.0,
                "unknown_cost_operations": 0,
                "operation_count": 0,
            },
        )
        entry["operation_count"] += 1
        entry["duration_seconds"] += _duration(operation) or 0.0
        cost = _known_cost(operation)
        billable = operation.kind in {"model", "tool"} or any(
            operation.attributes.get(key) is not None
            for key in ("pf.cost.amount", "cost_usd", "gen_ai.cost.usd", "pf.cost_usd")
        )
        if isinstance(cost, (int, float)):
            entry["cost_usd"] += float(cost)
        elif billable:
            entry["cost_known"] = False
            entry["unknown_cost_operations"] += 1
    for entry in groups.values():
        if not entry["cost_known"]:
            entry["cost_usd"] = None
    return sorted(groups.values(), key=lambda item: (item["role"], item["provider"], item["model"]))


def derive_time_contributions(
    graph: NormalizedExecutionGraph,
    *,
    events: Sequence[Event] = (),
    semantic_stage_map: Mapping[str, str] | None = None,
) -> dict[str, list[dict[str, Any]] | float]:
    """Aggregate observed operation duration without turning missing time into zero."""

    dimensions: dict[str, dict[tuple[str, ...], float]] = {
        "role": defaultdict(float),
        "kind": defaultdict(float),
        "provider_model": defaultdict(float),
        "semantic_stage": defaultdict(float),
    }
    semantic_stage_labels: dict[str, str] = {}
    repair_time = 0.0
    for operation in graph.operations:
        duration = _duration(operation)
        if duration is None:
            continue
        fields = _provider_fields(operation)
        dimensions["role"][(fields["role"] or "unknown",)] += duration
        dimensions["kind"][(operation.kind,)] += duration
        dimensions["provider_model"][(fields["provider"] or "unknown", fields["model"] or "unknown")] += duration
        stage = _semantic_stage(operation, events, semantic_stage_map)
        stage_key = canonical_stage_key(operation, stage)
        semantic_stage_labels.setdefault(stage_key, display_stage(operation, stage))
        dimensions["semantic_stage"][(stage_key,)] += duration
        if _value(operation, "pf.correction_feedback") is True:
            repair_time += duration

    def rows(name: str) -> list[dict[str, Any]]:
        result = []
        for key, seconds in sorted(dimensions[name].items()):
            value = key[0] if len(key) == 1 else list(key)
            stage_value = key[0] if key else "unknown"
            result.append(
                {
                    "key": semantic_stage_labels.get(stage_value, stage_value) if name == "semantic_stage" else value,
                    "canonical_key": stage_value if name == "semantic_stage" else None,
                    "duration_seconds": seconds,
                }
            )
        return result

    return {
        "time_by_role": rows("role"),
        "time_by_operation_kind": rows("kind"),
        "time_by_provider_model": rows("provider_model"),
        "time_by_semantic_stage": rows("semantic_stage"),
        "repair_time_seconds": repair_time,
    }


def derive_cost_summary(
    graph: NormalizedExecutionGraph,
    *,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report known operation cost and unknown-cost coverage explicitly."""

    buckets = {"model": 0.0, "tool": 0.0, "other": 0.0}
    unknown: list[str] = []
    sources: list[str] = []
    for operation in graph.operations:
        billable = operation.kind in {"model", "tool"} or any(
            operation.attributes.get(key) is not None
            for key in ("pf.cost.amount", "cost_usd", "gen_ai.cost.usd", "pf.cost_usd")
        )
        if not billable:
            continue
        cost = _known_cost(operation)
        if cost is None:
            unknown.append(operation.operation_id)
        else:
            buckets[operation.kind if operation.kind in {"model", "tool"} else "other"] += cost
    metrics = (run or {}).get("metrics")
    if isinstance(metrics, Mapping) and metrics.get("fixture_instrumentation_available"):
        fixture_cost = metrics.get("fixture_cost_usd")
        if isinstance(fixture_cost, (int, float)):
            buckets["other"] += float(fixture_cost)
            sources.append("run.metrics.fixture_cost_usd")
    known_total = sum(buckets.values())
    return {
        "known_model_cost_usd": buckets["model"],
        "known_tool_cost_usd": buckets["tool"],
        "known_other_cost_usd": buckets["other"],
        "known_total_cost_usd": known_total,
        "total_cost_complete": not unknown,
        "unknown_cost_operation_ids": unknown,
        "unmeasured_operation_count": len(unknown),
        "known_cost_sources": sources,
        "currency": "USD",
    }


def derive_partial_stage_success(graph: NormalizedExecutionGraph) -> dict[str, bool | None]:
    operations = _operation_sequence(graph)
    research = [operation for operation in operations if operation.name.casefold() in {"research_company", "research"}]
    extraction = [operation for operation in operations if "extract" in operation.name.casefold()]
    validation = [operation for operation in operations if "validat" in operation.name.casefold()]
    return {
        "research_succeeded": any(operation.status != "error" for operation in research) if research else None,
        "extraction_succeeded": any(operation.status != "error" for operation in extraction) if extraction else None,
        "validation_succeeded": any(_value(operation, "pf.validation_valid") is True for operation in validation)
        if validation
        else None,
    }


def derive_termination_decomposition(
    graph: NormalizedExecutionGraph,
    *,
    run: Mapping[str, Any] | None = None,
    events: Sequence[Event] = (),
) -> str:
    """Reuse the existing report categorization against a normalized execution."""

    manifest = (run or {}).get("manifest", {})
    state = (run or {}).get("state") or {}
    outcome = next((event.payload for event in reversed(events) if event.type == "outcome"), {})
    record = {
        "accepted_result": (
            (run or {}).get("acceptance", {}).get("status") == "accepted"
            if isinstance((run or {}).get("acceptance"), Mapping)
            else False
        ),
        "execution_completed": (run or {}).get(
            "execution_completed", outcome.get("execution_completed", graph.execution.status != "error")
        ),
        "result_valid": (run or {}).get("result_valid", outcome.get("result_valid", False)),
        "terminal_error": manifest.get("error") or outcome.get("error"),
        "source_failures": state.get("source_failures", 0),
        "targeted_routes": state.get("targeted_research_routes", []),
        "dimension_statuses": ((run or {}).get("assessment") or {}).get("dimension_statuses", []),
    }
    return derived_termination_category(record)


def derive_runtime_insights(
    graph: NormalizedExecutionGraph,
    *,
    run: Mapping[str, Any] | None = None,
    events: Sequence[Event] = (),
    semantic_stage_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return deterministic, recomputable insights for one execution."""

    return {
        "execution_path": [display_operation(operation) for operation in _operation_sequence(graph)],
        "execution_path_keys": [canonical_operation_key(operation) for operation in _operation_sequence(graph)],
        "path_signature": canonical_path_signature(_operation_sequence(graph)),
        "repeated_patterns": derive_repeated_patterns(graph),
        "failure": derive_failure_stage(graph, run=run, events=events, semantic_stage_map=semantic_stage_map),
        "termination_category": derive_termination_decomposition(graph, run=run, events=events),
        "repair": derive_repair_burden(graph, events=events),
        "role_contributions": derive_role_contributions(graph),
        "time_contributions": derive_time_contributions(graph, events=events, semantic_stage_map=semantic_stage_map),
        "cost_summary": derive_cost_summary(graph, run=run),
        "partial_stage_success": derive_partial_stage_success(graph),
    }


def find_similar_executions(target: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Find recurrence candidates using explainable rule-based dimensions."""

    target_failure = target.get("failure") or {}
    target_patterns = {
        tuple(item.get("pattern_keys") or item.get("pattern", [])) for item in target.get("repeated_patterns", [])
    }
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        failure = candidate.get("failure") or {}
        reasons: list[str] = []
        target_break = target_failure.get("primary_break_point_key") or target_failure.get("primary_break_point")
        candidate_break = failure.get("primary_break_point_key") or failure.get("primary_break_point")
        if target_break and target_break == candidate_break:
            reasons.append("same failure stage")
        if target_failure.get("role") and target_failure.get("role") == failure.get("role"):
            reasons.append("same role")
        patterns = {
            tuple(item.get("pattern_keys") or item.get("pattern", []))
            for item in candidate.get("repeated_patterns", [])
        }
        if target_patterns & patterns:
            reasons.append("same repeated path")
        if reasons:
            matches.append({"execution_id": candidate.get("execution_id"), "reasons": reasons})
    return matches
