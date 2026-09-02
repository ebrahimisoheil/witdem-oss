"""Rebuildable serving projections derived from canonical models."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from witdem.analytics.core import Evaluation, Event, Execution, Link, Operation, Outcome
from witdem.analytics.identity import display_execution, display_model, display_operation, model_value
from witdem.analytics.operations import operation_identity
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_repeated_patterns

SERVING_DDL = """
CREATE SCHEMA IF NOT EXISTS serving;
CREATE SCHEMA IF NOT EXISTS witdem_control;

CREATE TABLE IF NOT EXISTS serving.execution_facts (
    execution_id VARCHAR PRIMARY KEY,
    trace_id VARCHAR,
    display_name VARCHAR,
    runtime_id VARCHAR,
    case_id VARCHAR,
    model_profile VARCHAR,
    providers VARCHAR,
    provider_adapters VARCHAR,
    models VARCHAR,
    workflows VARCHAR,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds DOUBLE,
    runtime_status VARCHAR,
    failure_count BIGINT,
    failure_location VARCHAR,
    application_outcome VARCHAR,
    expected_outcome VARCHAR,
    decision_correct BOOLEAN,
    product_goal_reported BOOLEAN,
    product_goal_achieved BOOLEAN,
    assurance_status VARCHAR,
    artifact_valid BOOLEAN,
    evidence_sufficient BOOLEAN,
    closest_blocker VARCHAR,
    threshold DOUBLE,
    threshold_margin DOUBLE,
    targeted_research_required BOOLEAN,
    targeted_research_performed BOOLEAN,
    model_calls BIGINT,
    tool_calls BIGINT,
    operation_count BIGINT,
    input_tokens DOUBLE,
    output_tokens DOUBLE,
    total_tokens DOUBLE,
    measured_cost DOUBLE,
    cost_coverage DOUBLE,
    pricing_version VARCHAR,
    repeated_pattern_count BIGINT,
    extra_work_seconds DOUBLE,
    extra_work_tokens DOUBLE,
    extra_work_cost DOUBLE,
    adapter_name VARCHAR,
    adapter_version VARCHAR,
    provider_adapter VARCHAR,
    source_ingest_ids VARCHAR,
    transformed_at TIMESTAMP,
    transform_version VARCHAR
);

CREATE TABLE IF NOT EXISTS serving.operation_facts (
    operation_id VARCHAR,
    execution_id VARCHAR,
    trace_id VARCHAR,
    span_id VARCHAR,
    parent_operation_id VARCHAR,
    sequence_number BIGINT,
    kind VARCHAR,
    canonical_key VARCHAR,
    display_name VARCHAR,
    semantic_stage VARCHAR,
    role VARCHAR,
    provider VARCHAR,
    canonical_model VARCHAR,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds DOUBLE,
    status VARCHAR,
    failure_reason VARCHAR,
    input_tokens DOUBLE,
    output_tokens DOUBLE,
    total_tokens DOUBLE,
    measured_cost DOUBLE,
    cost_unavailable_reason VARCHAR,
    attempt BIGINT,
    is_framework_wrapper BOOLEAN,
    is_skipped BOOLEAN,
    is_repeated BOOLEAN,
    repeat_group VARCHAR,
    adapter_name VARCHAR,
    adapter_version VARCHAR,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS serving.semantic_facts (
    record_id VARCHAR,
    execution_id VARCHAR,
    trace_id VARCHAR,
    span_id VARCHAR,
    record_type VARCHAR,
    name VARCHAR,
    value VARCHAR,
    status VARCHAR,
    score DOUBLE,
    label VARCHAR,
    observed_at TIMESTAMP,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS serving.execution_edges (
    execution_id VARCHAR,
    source_operation_id VARCHAR,
    target_operation_id VARCHAR,
    relation VARCHAR,
    source_kind VARCHAR,
    target_kind VARCHAR,
    is_framework_edge BOOLEAN,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS serving.path_facts (
    execution_id VARCHAR,
    path_id VARCHAR,
    path_type VARCHAR,
    path_signature VARCHAR,
    display_path VARCHAR,
    iteration_count BIGINT,
    operation_ids VARCHAR,
    duration_seconds DOUBLE,
    total_tokens DOUBLE,
    measured_cost DOUBLE
);

CREATE TABLE IF NOT EXISTS witdem_control.transform_runs (
    transform_run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    status VARCHAR,
    engine VARCHAR,
    engine_version VARCHAR,
    input_batches BIGINT,
    affected_executions BIGINT,
    error VARCHAR
);
"""


def _duration(start: datetime | None, end: datetime | None) -> float | None:
    return max(0.0, (end - start).total_seconds()) if start is not None and end is not None else None


def _number(attributes: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _text(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _semantic_attributes(record: Event | Evaluation | Outcome) -> dict[str, Any]:
    if isinstance(record, Event):
        return dict(record.payload)
    return dict(record.attributes)


def _repeat_details(
    graph: NormalizedExecutionGraph,
) -> tuple[list[dict[str, Any]], set[str], float, float | None, float | None]:
    patterns = derive_repeated_patterns(graph)
    operations = {operation.operation_id: operation for operation in graph.operations}
    repeated_ids: set[str] = set()
    extra_time = 0.0
    extra_tokens = 0.0
    tokens_seen = False
    extra_cost = 0.0
    cost_complete = True
    for pattern in patterns:
        width = len(pattern.get("pattern_keys", []))
        for operation_id in pattern.get("operation_ids", [])[width:]:
            operation = operations.get(str(operation_id))
            if operation is None:
                continue
            repeated_ids.add(operation.operation_id)
            extra_time += _duration(operation.started_at, operation.ended_at) or 0.0
            token_value = _number(operation.attributes, "total_tokens")
            if token_value is not None:
                extra_tokens += token_value
                tokens_seen = True
            cost_value = _number(operation.attributes, "cost_usd", "gen_ai.cost.usd", "pf.cost_usd")
            if cost_value is not None:
                extra_cost += cost_value
            elif operation.kind in {"model", "tool"}:
                cost_complete = False
    return (
        patterns,
        repeated_ids,
        extra_time,
        extra_tokens if tokens_seen and patterns else None,
        extra_cost if cost_complete and patterns else None,
    )


def build_serving_rows(
    execution: Execution,
    operations: Sequence[Operation],
    links: Sequence[Link],
    semantics: Sequence[Event | Evaluation | Outcome],
    *,
    transformed_at: datetime,
    transform_version: str,
) -> dict[str, list[dict[str, Any]]]:
    """Produce all clean serving rows for one canonical execution."""

    graph = NormalizedExecutionGraph(execution=execution, operations=list(operations), links=list(links))
    patterns, repeated_ids, extra_time, extra_tokens, extra_cost = _repeat_details(graph)
    operation_by_id = {operation.operation_id: operation for operation in operations}
    ordered = sorted(operations, key=lambda item: item.started_at.isoformat() if item.started_at else "")
    model_operations = [
        operation
        for operation in operations
        if operation.kind == "model"
        or (model_value(operation) is not None and operation_identity(operation)["family"] in {"inference", "media"})
    ]
    model_identity_operations = [operation for operation in operations if model_value(operation) is not None]
    tool_operations = [
        operation
        for operation in operations
        if operation.kind == "tool"
        or (
            operation_identity(operation)["entity_kind"] == "operation"
            and operation_identity(operation)["type"] in {"tool", "tool_execution"}
        )
    ]
    failures = [operation for operation in operations if operation.status == "error"]
    goal: dict[str, Any] = {}
    application_outcome: str | None = None
    for record in semantics:
        attributes = _semantic_attributes(record)
        if isinstance(record, Outcome) and record.name == "product_goal":
            goal = attributes
        if isinstance(record, Outcome) and record.name == "application_outcome":
            application_outcome = record.status or (str(record.value) if record.value is not None else None)
    input_values = [_number(operation.attributes, "input_tokens") for operation in model_operations]
    output_values = [_number(operation.attributes, "output_tokens") for operation in model_operations]
    total_values = [_number(operation.attributes, "total_tokens") for operation in model_operations]
    costs = [
        _number(operation.attributes, "cost_usd", "gen_ai.cost.usd", "pf.cost_usd") for operation in model_operations
    ]
    priced = sum(value is not None for value in costs)
    execution_attributes = dict(execution.attributes)
    root_trace = next((operation.trace_id for operation in operations if operation.trace_id), None)
    operation_rows: list[dict[str, Any]] = []
    for index, operation in enumerate(ordered, start=1):
        parent = next(
            (candidate for candidate in operations if candidate.span_id == operation.parent_span_id),
            operation_by_id.get(str(operation.parent_span_id or "")),
        )
        wrapper = bool(parent and operation.kind in {"workflow", "pipeline", "agent"} and parent.kind == operation.kind)
        attributes = dict(operation.attributes)
        operation_rows.append(
            {
                "operation_id": operation.operation_id,
                "execution_id": execution.execution_id,
                "trace_id": operation.trace_id,
                "span_id": operation.span_id,
                "parent_operation_id": operation.parent_span_id,
                "sequence_number": index,
                "kind": operation.kind,
                "canonical_key": operation.name,
                "display_name": display_operation(operation),
                "semantic_stage": _text(attributes, "witdem.semantic_stage", "semantic_stage"),
                "role": _text(attributes, "role", "product_factory.role", "gen_ai.operation.role", "pf.role"),
                "provider": _text(attributes, "provider", "gen_ai.provider.name", "gen_ai.system"),
                "canonical_model": display_model(operation) if model_value(operation) else None,
                "started_at": operation.started_at,
                "ended_at": operation.ended_at,
                "duration_seconds": _duration(operation.started_at, operation.ended_at),
                "status": operation.status,
                "failure_reason": _text(attributes, "failure.reason", "exception.message", "error"),
                "input_tokens": _number(attributes, "input_tokens"),
                "output_tokens": _number(attributes, "output_tokens"),
                "total_tokens": _number(attributes, "total_tokens"),
                "measured_cost": _number(attributes, "cost_usd", "gen_ai.cost.usd", "pf.cost_usd"),
                "cost_unavailable_reason": _text(attributes, "cost_unavailable_reason"),
                "attempt": operation.attempt,
                "is_framework_wrapper": wrapper,
                "is_skipped": attributes.get("witdem.stage.status") == "skipped"
                or (
                    goal.get("targeted_research_performed") is False
                    and "targeted research" in display_operation(operation).casefold()
                ),
                "is_repeated": operation.operation_id in repeated_ids,
                "repeat_group": next(
                    (
                        str(item.get("loop_signature"))
                        for item in patterns
                        if operation.operation_id in item.get("operation_ids", [])
                    ),
                    None,
                ),
                "adapter_name": _text(attributes, "witdem.adapter.name"),
                "adapter_version": _text(attributes, "witdem.adapter.version"),
                "provider_adapter": _text(attributes, "witdem.provider_adapter.name"),
                "attributes": json.dumps(attributes, sort_keys=True, default=str),
            }
        )
    semantic_rows: list[dict[str, Any]] = []
    for record in semantics:
        attributes = _semantic_attributes(record)
        semantic_rows.append(
            {
                "record_id": record.event_id
                if isinstance(record, Event)
                else record.evaluation_id
                if isinstance(record, Evaluation)
                else record.outcome_id,
                "execution_id": execution.execution_id,
                "trace_id": record.trace_id if isinstance(record, Event) else None,
                "span_id": record.span_id if isinstance(record, Event) else None,
                "record_type": record.type
                if isinstance(record, Event)
                else "evaluation"
                if isinstance(record, Evaluation)
                else "outcome",
                "name": record.name,
                "value": json.dumps(record.value, default=str)
                if not isinstance(record, Event)
                else json.dumps(record.payload.get("value"), default=str),
                "status": record.label
                if isinstance(record, Evaluation)
                else record.status
                if isinstance(record, Outcome)
                else None,
                "score": record.score if isinstance(record, Evaluation) else None,
                "label": record.label if isinstance(record, Evaluation) else None,
                "observed_at": record.timestamp if isinstance(record, (Event, Outcome)) else None,
                "attributes": json.dumps(attributes, sort_keys=True, default=str),
            }
        )
    edge_rows: list[dict[str, Any]] = []
    for link in links:
        source_operation = operation_by_id.get(link.source_id)
        target_operation = operation_by_id.get(link.target_id)
        edge_rows.append(
            {
                "execution_id": execution.execution_id,
                "source_operation_id": link.source_id,
                "target_operation_id": link.target_id,
                "relation": link.relation,
                "source_kind": source_operation.kind if source_operation else None,
                "target_kind": target_operation.kind if target_operation else None,
                "is_framework_edge": bool(
                    source_operation and source_operation.kind in {"workflow", "pipeline", "agent"}
                ),
                "attributes": json.dumps(link.attributes, sort_keys=True, default=str),
            }
        )
    path_rows = [
        {
            "execution_id": execution.execution_id,
            "path_id": f"{execution.execution_id}:{index}",
            "path_type": "repeated",
            "path_signature": item.get("loop_signature"),
            "display_path": " → ".join(item.get("pattern", [])),
            "iteration_count": item.get("iterations"),
            "operation_ids": json.dumps(item.get("operation_ids", [])),
            "duration_seconds": extra_time,
            "total_tokens": extra_tokens,
            "measured_cost": extra_cost,
        }
        for index, item in enumerate(patterns, start=1)
    ]
    execution_row = {
        "execution_id": execution.execution_id,
        "trace_id": root_trace,
        "display_name": display_execution(
            execution.model_copy(
                update={
                    "runtime_id": goal.get("runtime_id") or execution.runtime_id,
                    "attributes": {**execution_attributes, **goal},
                }
            ),
            operations,
        ),
        "runtime_id": goal.get("runtime_id") or execution.runtime_id,
        "case_id": goal.get("case_id") or execution_attributes.get("case_id"),
        "model_profile": goal.get("model_profile") or execution_attributes.get("model_profile"),
        "providers": ", ".join(
            sorted(
                {
                    value
                    for operation in operations
                    if (value := _text(operation.attributes, "provider", "gen_ai.provider.name", "gen_ai.system"))
                }
            )
        )
        or None,
        "provider_adapters": execution_attributes.get("witdem.provider_adapters"),
        "models": ", ".join(sorted({display_model(operation) for operation in model_identity_operations})) or None,
        "workflows": ", ".join(
            sorted(
                {display_operation(operation) for operation in operations if operation.kind in {"workflow", "pipeline"}}
            )
        )
        or None,
        "started_at": execution.started_at,
        "ended_at": execution.ended_at,
        "duration_seconds": _duration(execution.started_at, execution.ended_at),
        "runtime_status": execution.status,
        "failure_count": len(failures),
        "failure_location": display_operation(failures[-1]) if failures else None,
        "application_outcome": application_outcome or goal.get("observed_status"),
        "expected_outcome": goal.get("expected_status"),
        "decision_correct": goal.get("decision_correct"),
        "product_goal_reported": bool(goal),
        "product_goal_achieved": goal.get("product_goal_achieved"),
        "assurance_status": goal.get("assurance_status"),
        "artifact_valid": goal.get("artifact_valid"),
        "evidence_sufficient": goal.get("decision_evidence_sufficient"),
        "closest_blocker": goal.get("closest_blocker"),
        "threshold": goal.get("threshold"),
        "threshold_margin": goal.get("threshold_margin"),
        "targeted_research_required": goal.get("targeted_research_required"),
        "targeted_research_performed": goal.get("targeted_research_performed"),
        "model_calls": len(model_operations),
        "tool_calls": len(tool_operations),
        "operation_count": len(operations),
        "input_tokens": sum(value for value in input_values if value is not None)
        if any(value is not None for value in input_values)
        else None,
        "output_tokens": sum(value for value in output_values if value is not None)
        if any(value is not None for value in output_values)
        else None,
        "total_tokens": sum(value for value in total_values if value is not None)
        if any(value is not None for value in total_values)
        else None,
        "measured_cost": sum(value for value in costs if value is not None)
        if any(value is not None for value in costs)
        else None,
        "cost_coverage": priced / len(model_operations) if model_operations else None,
        "pricing_version": next(
            (
                _text(operation.attributes, "cost_pricing_snapshot")
                for operation in model_operations
                if _text(operation.attributes, "cost_pricing_snapshot")
            ),
            None,
        ),
        "repeated_pattern_count": len(patterns),
        "extra_work_seconds": extra_time,
        "extra_work_tokens": extra_tokens,
        "extra_work_cost": extra_cost,
        "adapter_name": execution_attributes.get("witdem.adapter.name"),
        "adapter_version": execution_attributes.get("witdem.adapter.version"),
        "source_ingest_ids": execution_attributes.get("witdem.source_ingest_ids"),
        "transformed_at": transformed_at,
        "transform_version": transform_version,
    }
    return {
        "execution_facts": [execution_row],
        "operation_facts": operation_rows,
        "semantic_facts": semantic_rows,
        "execution_edges": edge_rows,
        "path_facts": path_rows,
    }
