"""Repository-backed dashboard read service.

This module is deliberately unaware of HTTP and DuckDB.  It translates the
stable analytics repository into frontend-oriented JSON contracts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from fastapi.encoders import jsonable_encoder

from witdem.analytics.contracts import MetadataSnapshot
from witdem.analytics.core import Operation
from witdem.analytics.operations import OPERATION_FAMILIES, operation_identity, operation_measurements
from witdem.analytics.repository import AnalyticsRepository, create_backend
from witdem.analytics.repository.state import FilterState
from witdem.update import check_updates, installed_versions
from witdem.workflows import WorkflowDefinition, definition_from_record, load_registry, project_execution


def filters_from_values(
    *,
    workflow: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    tool: str | None = None,
    stage: str | None = None,
    contract_hash: str | None = None,
    has_repeated_work: bool = False,
    has_failure: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FilterState:
    return FilterState(
        workflow=workflow,
        status=status,
        provider=provider,
        model=model,
        tool=tool,
        stage=stage,
        contract_hash=contract_hash,
        has_repeated_work=has_repeated_work,
        has_failure=has_failure,
        start_date=start_date,
        end_date=end_date,
    )


@contextmanager
def repository(database: Path) -> Iterator[AnalyticsRepository]:
    repo = create_backend(database).create_repository()
    try:
        yield repo
    finally:
        repo.close()


def _metadata_payload(snapshot: MetadataSnapshot) -> dict[str, Any]:
    capabilities = snapshot.capabilities
    return {
        "product": "Witdem AI",
        "capabilities": asdict(capabilities),
        "mode": (
            "runtime + business meaning"
            if capabilities.domain_enriched
            else "runtime + enriched telemetry"
            if capabilities.enriched
            else "telemetry only"
        ),
        "filters": {field: list(values) for field, values in snapshot.filters.items()},
        "contracts": list(snapshot.contracts),
        "versions": installed_versions(),
        "update": check_updates(offline=True),
    }


def metadata(repo: AnalyticsRepository) -> dict[str, Any]:
    return _metadata_payload(repo.get_metadata_snapshot())


def overview(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    with repo._overview_read_session():
        snapshot = repo.get_overview_snapshot(filters)
        operation_facts, operation_measurements = repo.operation_health_facts()
    goals = snapshot.goals
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "execution": snapshot.execution.to_dict(),
                "goals": {
                    **goals.to_dict(),
                    "coverage": goals.coverage,
                    "success_rate": goals.success_rate,
                    "decision_correctness_rate": goals.decision_correctness_rate,
                },
                "costs": snapshot.costs.to_dict(),
                "cost_unavailable": snapshot.cost_unavailable,
                "models": [item.to_dict() for item in snapshot.models],
                "providers": [item.to_dict() for item in snapshot.providers],
                "workflows": [item.to_dict() for item in snapshot.workflows],
                "stages": snapshot.stages,
                "runtime_breakdown": snapshot.runtime_breakdown,
                "outcome_breakdown": snapshot.outcome_breakdown,
                "failures": [item.to_dict() for item in snapshot.failures],
                "evaluations": snapshot.evaluations,
                "goal_misses": snapshot.goal_misses,
                "goal_trend": snapshot.goal_trend,
                "goal_portfolio": snapshot.goal_portfolio,
                "assurance_summary": snapshot.assurance_summary,
                "operation_health": _operation_summary(operation_facts, operation_measurements),
                "operation_measurement_coverage": _measurement_coverage(operation_measurements),
                "operation_measurement_alerts": _measurement_alerts(operation_facts, operation_measurements),
                "paths": [],
                "contracts": snapshot.contracts,
                "metadata": _metadata_payload(snapshot.metadata),
            }
        ),
    )


def runs(
    repo: AnalyticsRepository,
    filters: FilterState,
    page: int = 1,
    page_size: int = 10,
    *,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    rows = repo.execution_rows(filters, limit=None)
    if workflow_id:
        execution_ids = repo.workflow_execution_ids(workflow_id)
        rows = [row for row in rows if str(row["execution_id"]) in execution_ids]
    size = max(1, min(page_size, 100))
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(page, pages))
    start = (current - 1) * size
    page_rows = rows[start : start + size]
    for row in page_rows:
        association = repo.execution_workflow(str(row["execution_id"]))
        row["canonical_url"] = (
            f"/workflows/{association['workflow_id']}/executions/{row['execution_id']}"
            if association is not None
            else None
        )
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "items": page_rows,
                "count": total,
                "page": current,
                "page_size": size,
                "pages": pages,
            }
        ),
    )


def run_detail(repo: AnalyticsRepository, execution_id: str) -> dict[str, Any] | None:
    fact = repo.execution_fact(execution_id)
    rows = (
        []
        if fact is not None
        else [row for row in repo.execution_rows(limit=None) if str(row["execution_id"]) == execution_id]
    )
    execution_row = rows[0] if rows else None
    if fact is None and execution_row is None:
        return None
    summary = {**(fact or {}), **(execution_row or {})}
    summary["runtime_outcome"] = summary.get("runtime_outcome") or summary.get("runtime_status")
    summary["known_cost"] = (
        summary.get("known_cost") if summary.get("known_cost") is not None else summary.get("measured_cost")
    )
    summary["total_tokens"] = (
        summary.get("total_tokens") if summary.get("total_tokens") is not None else summary.get("token_usage")
    )
    graph = repo.replay(execution_id).model_dump(mode="json")
    semantic_records = [record.to_dict() for record in repo.semantic_replay_records(execution_id)]
    workflow = _resolve_workflow(repo, summary, graph, semantic_records)
    # A replay is valid only while the execution has an authored YAML
    # association. Never surface a stale projection for an unrelated run.
    projection = repo.workflow_projection(execution_id) if workflow is not None else None
    if projection is None and workflow is not None:
        projection = project_execution(workflow, execution=summary, graph=graph)
    operation_facts, measurements = repo.execution_operation_facts(execution_id)
    evaluation_results = []
    for record in semantic_records:
        if str(record.get("kind") or "").casefold() != "evaluation":
            continue
        evaluation_results.append({**record, "passed": _explicit_evaluation_pass(record)})
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "summary": summary,
                "outcomes": repo.execution_outcomes(execution_id),
                "graph": graph,
                "semantic_records": semantic_records,
                "workflow_replay": projection,
                "operation_summary": _operation_summary(operation_facts, measurements),
                "measurements": measurements,
                "measurement_coverage": _measurement_coverage(measurements),
                "evaluation_results": evaluation_results,
                "canonical_url": (
                    f"/workflows/{workflow.id}/executions/{execution_id}" if workflow is not None else None
                ),
            }
        ),
    )


def _projection_for_execution(repo: AnalyticsRepository, execution_id: str) -> dict[str, Any] | None:
    fact = repo.execution_fact(execution_id)
    rows = (
        []
        if fact is not None
        else [row for row in repo.execution_rows(limit=None) if str(row["execution_id"]) == execution_id]
    )
    execution_row = rows[0] if rows else None
    if fact is None and execution_row is None:
        return None
    summary = {**(fact or {}), **(execution_row or {})}
    summary["runtime_outcome"] = summary.get("runtime_outcome") or summary.get("runtime_status")
    summary["known_cost"] = (
        summary.get("known_cost") if summary.get("known_cost") is not None else summary.get("measured_cost")
    )
    graph = repo.replay(execution_id).model_dump(mode="json")
    semantic_records = [record.to_dict() for record in repo.semantic_replay_records(execution_id)]
    workflow = _resolve_workflow(repo, summary, graph, semantic_records)
    return project_execution(workflow, execution=summary, graph=graph) if workflow is not None else None


def materialize_workflow_projections(database: Path, execution_ids: list[str] | None = None) -> dict[str, int]:
    """Build projections outside dashboard request handling and persist them atomically."""

    projections: list[dict[str, Any]] = []
    participant_facts: list[dict[str, Any]] = []
    operation_classifications: list[dict[str, Any]] = []
    operation_measurement_facts: list[dict[str, Any]] = []
    projected_execution_ids: list[str] = []
    with repository(database) as repo:
        selected = execution_ids or [str(row["execution_id"]) for row in repo.execution_rows(limit=None)]
        operations_by_execution = repo.operations_by_execution()
        for execution_id in selected:
            projection = _projection_for_execution(repo, execution_id)
            if projection is not None:
                projections.append(projection)
                projected_execution_ids.append(execution_id)
                classifications, measurements = _operation_facts(
                    projection,
                    operations_by_execution.get(execution_id, []),
                )
                operation_classifications.extend(classifications)
                operation_measurement_facts.extend(measurements)
        participant_facts = repo.build_participant_facts(set(selected))
    from witdem.ingest.live_db import (
        delete_workflow_projections,
        store_operation_facts,
        store_participant_facts,
        store_workflow_projection,
    )

    for projection in projections:
        store_workflow_projection(database, projection)
    delete_workflow_projections(database, sorted(set(selected) - set(projected_execution_ids)))
    store_participant_facts(database, selected, participant_facts)
    # The Duckle publisher already materializes vendor-neutral operation facts
    # for every execution. Replace only executions that gained a YAML workflow
    # projection, because this pass adds workflow/node attribution. Deleting
    # facts for executions without a declared workflow would erase their live
    # operation and measurement data from execution detail pages.
    store_operation_facts(
        database,
        projected_execution_ids,
        operation_classifications,
        operation_measurement_facts,
    )
    return {
        "requested": len(execution_ids or projections),
        "materialized": len(projections),
        "operations": len(operation_classifications),
        "measurements": len(operation_measurement_facts),
    }


def _operation_facts(
    projection: dict[str, Any], operations: list[Operation]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workflow = dict(projection.get("workflow") or {})
    execution = dict(projection.get("execution") or {})
    execution_id = str(execution.get("execution_id") or "")
    workflow_id = str(workflow.get("id") or "")
    template_hash = str(workflow.get("template_hash") or "")
    operation_nodes: dict[str, tuple[str | None, str | None, list[str], list[str]]] = {}
    for node in projection.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        declaration = dict(node.get("operation") or {})
        node_id = str(node.get("id") or "")
        for observed in [*(node.get("observations") or []), *(node.get("model_calls") or [])]:
            if not isinstance(observed, dict):
                continue
            observed_id = str(observed.get("id") or observed.get("operation_id") or "")
            if observed_id:
                operation_nodes[observed_id] = (
                    node_id,
                    str(declaration.get("type")) if declaration.get("type") else None,
                    list(declaration.get("expects") or []),
                    list(declaration.get("optional") or []),
                )
    classifications: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    operation_id_by_span = {operation.span_id: operation.operation_id for operation in operations if operation.span_id}
    for operation in operations:
        identity = operation_identity(operation)
        assigned_node_id, declared_type, expected, optional = operation_nodes.get(
            operation.operation_id, (None, None, [], [])
        )
        if declared_type and identity["type"] in {"component", "unknown", "x.witdem.unclassified"}:
            identity = {**identity, "type": declared_type, "family": OPERATION_FAMILIES.get(declared_type, "custom")}
        if declared_type and identity["type"] != declared_type:
            expected = []
            optional = []
        attributes = operation.attributes
        duration = None
        if operation.started_at is not None and operation.ended_at is not None:
            duration = max(0.0, (operation.ended_at - operation.started_at).total_seconds())
        classifications.append(
            {
                "operation_id": operation.operation_id,
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "template_hash": template_hash,
                "node_id": assigned_node_id,
                "taxonomy_version": identity["taxonomy_version"],
                "family": identity["family"],
                "operation_type": identity["type"],
                "subtype": identity["subtype"],
                "interface": identity["interface"],
                "role": identity["role"],
                "input_modalities": identity["input_modalities"],
                "output_modalities": identity["output_modalities"],
                "provider_id": _explicit_attribute(attributes, "gen_ai.provider.name", "provider"),
                "model_id": _explicit_attribute(attributes, "gen_ai.response.model", "gen_ai.request.model", "model"),
                "gateway_id": _explicit_attribute(attributes, "witdem.gateway.id", "gateway"),
                "vendor_id": _explicit_attribute(attributes, "witdem.vendor.id", "model_vendor"),
                "runtime_id": _explicit_attribute(attributes, "witdem.runtime.id", "runtime"),
                "framework_id": _explicit_attribute(attributes, "witdem.framework.id", "framework"),
                "implementation_id": _explicit_attribute(attributes, "witdem.implementation.id", "implementation"),
                "execution_source": _explicit_attribute(
                    attributes, "witdem.execution.source", "witdem.client.library", "otel.scope.name"
                ),
                "parent_operation_id": (
                    operation_id_by_span.get(operation.parent_span_id) if operation.parent_span_id else None
                ),
                "duration_seconds": duration,
                "status": operation.status,
                "attributes": {
                    "trace_id": operation.trace_id,
                    "span_id": operation.span_id,
                    "attempt": operation.attempt,
                },
            }
        )
        for measurement in operation_measurements(operation, expected=expected, optional=optional):
            measurements.append(
                {
                    "operation_id": operation.operation_id,
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "template_hash": template_hash,
                    "node_id": assigned_node_id,
                    "registry_version": measurement["registry_version"],
                    "measurement_key": measurement["key"],
                    "value": measurement["value"],
                    "unit": measurement["unit"],
                    "aggregation": measurement["aggregation"],
                    "scope": measurement["scope"],
                    "measurement_status": measurement["status"],
                    "provenance": measurement["provenance"],
                    "applicability_source": measurement["applicability_source"],
                    "attempt": operation.attempt,
                }
            )
    return classifications, measurements


def _explicit_attribute(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _persisted_definitions(repo: AnalyticsRepository) -> dict[str, WorkflowDefinition]:
    result: dict[str, WorkflowDefinition] = {}
    for row in repo.workflow_templates():
        definition = row.get("definition")
        if isinstance(definition, dict):
            try:
                parsed = WorkflowDefinition.model_validate(definition)
            except ValueError:
                # Persisted templates may predate the dependency-first schema.
                # Ignore only the unreadable revision; configured declarations
                # and valid historical revisions remain available.
                continue
            result[parsed.id] = parsed
    return result


def _resolve_workflow(
    repo: AnalyticsRepository,
    summary: dict[str, Any],
    graph: dict[str, Any],
    semantic_records: list[dict[str, Any]],
) -> WorkflowDefinition | None:
    emitted = definition_from_record(semantic_records)
    if emitted is not None:
        return emitted
    association = repo.execution_workflow(str(summary.get("execution_id") or ""))
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    if association and str(association.get("workflow_id")) in definitions:
        return definitions[str(association["workflow_id"])]
    # Associations belong to ingestion and must come from an emitted YAML
    # definition/identity or a configured YAML match. Guessing here from a
    # generic runtime name made arbitrary LangGraph runs appear under whichever
    # persisted workflow happened to mention ``langgraph``.
    return None


def workflow_catalog(repo: AnalyticsRepository) -> dict[str, Any]:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    projection_catalog = {str(row["workflow_id"]): row for row in repo.workflow_projection_catalog()}
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "items": [
                    {
                        "version": definition.version,
                        "id": definition.id,
                        "name": definition.name,
                        "description": definition.description,
                        "framework": definition.framework,
                        "template_hash": definition.template_hash,
                        "stage_count": len(definition.stages),
                        "node_count": len(definition.nodes),
                        "execution_count": int(
                            (projection_catalog.get(definition.id) or {}).get("execution_count") or 0
                        ),
                        "latest_execution": _workflow_execution_summary(
                            (projection_catalog.get(definition.id) or {}).get("latest_projection")
                        ),
                    }
                    for definition in definitions.values()
                ]
            }
        ),
    )


def _workflow_execution_summary(replay: Any) -> dict[str, Any] | None:
    if not isinstance(replay, dict):
        return None
    row = dict(replay.get("execution") or {})
    nodes = replay.get("nodes", [])
    active_nodes = [node for node in nodes if isinstance(node, dict) and node.get("state") != "inactive"]
    row["workflow_models"] = sorted({str(model) for node in active_nodes for model in node.get("models", [])})
    row["workflow_providers"] = sorted(
        {str(provider) for node in active_nodes for provider in node.get("providers", [])}
    )
    return row


def workflow_detail(repo: AnalyticsRepository, workflow_id: str) -> dict[str, Any] | None:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    definition = definitions.get(workflow_id)
    if definition is None:
        return None
    executions = []
    replays: list[dict[str, Any]] = []
    catalog_row = next(
        (row for row in repo.workflow_projection_catalog() if str(row["workflow_id"]) == workflow_id),
        None,
    )
    for projected_row in repo.workflow_projection_rows(workflow_id):
        replay = projected_row.get("projection")
        if not isinstance(replay, dict) or replay.get("workflow", {}).get("id") != workflow_id:
            continue
        replays.append(replay)
        row = dict(replay.get("execution") or {})
        nodes = replay.get("nodes", []) if replay else []
        active_nodes = [node for node in nodes if node.get("state") != "inactive"]
        attempts = sum(int(node.get("attempts") or 0) for node in active_nodes)
        models = sorted({str(model) for node in active_nodes for model in node.get("models", [])})
        providers = sorted({str(provider) for node in active_nodes for provider in node.get("providers", [])})
        executions.append(
            {
                **row,
                "workflow_active_steps": len(active_nodes),
                "workflow_total_steps": len(nodes) or len(definition.nodes),
                "workflow_attempts": attempts,
                "workflow_retry_attempts": sum(max(0, int(node.get("attempts") or 0) - 1) for node in active_nodes),
                "workflow_recovered_steps": sum(1 for node in active_nodes if node.get("state") == "recovered"),
                "workflow_failed_steps": sum(1 for node in active_nodes if node.get("state") == "failed"),
                "workflow_models": models,
                "workflow_providers": providers,
            }
        )
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "workflow": {
                    **definition.api_dict(),
                    "template_hash": definition.template_hash,
                },
                "executions": executions,
                "analytics": _workflow_projection_analytics(replays),
                "execution_count": int((catalog_row or {}).get("execution_count") or len(executions)),
            }
        ),
    )


def _sum_optional(values: Iterator[Any]) -> float | None:
    known = [float(value) for value in values if value is not None]
    return sum(known) if known else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _workflow_projection_analytics(replays: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate workflow-specific charts from materialized replay projections.

    Workflow matching is intentionally independent of an application's optional
    ``workflow`` telemetry attribute.  Using the matched projections here keeps
    the workflow page accurate for YAML-only and historical integrations.
    """

    attribution: dict[str, dict[str, dict[str, Any]]] = {
        "models": defaultdict(lambda: {"runs": set(), "calls": []}),
        "providers": defaultdict(lambda: {"runs": set(), "calls": []}),
    }
    stages: dict[str, dict[str, Any]] = {}
    for replay in replays:
        execution = dict(replay.get("execution") or {})
        execution_id = str(execution.get("execution_id") or "")
        nodes = [dict(node) for node in replay.get("nodes", []) if isinstance(node, dict)]
        failed = str(execution.get("runtime_outcome") or execution.get("status") or "").casefold() in {
            "error",
            "failed",
        } or any(node.get("state") == "failed" for node in nodes)
        recovered = not failed and any(node.get("state") == "recovered" for node in nodes)

        seen: dict[str, set[str]] = {"models": set(), "providers": set()}
        for node in nodes:
            if node.get("state") == "inactive":
                continue
            stage = stages.setdefault(
                str(node.get("name") or node.get("id") or "Unknown step"),
                {
                    "calls": 0,
                    "executions": set(),
                    "time_seconds": 0.0,
                    "known_costs": [],
                    "cost_eligible": 0,
                    "cost_measured": 0,
                    "total_tokens": 0.0,
                    "token_eligible": 0,
                    "token_measured": 0,
                    "failures": 0,
                    "extra_attempts": 0,
                },
            )
            stage["calls"] += int(node.get("attempts") or 0)
            stage["executions"].add(execution_id)
            stage["time_seconds"] += float(node.get("duration_seconds") or 0)
            if node.get("known_cost") is not None:
                stage["known_costs"].append(float(node["known_cost"]))
            stage["cost_eligible"] += int(node.get("cost_eligible_operations") or 0)
            stage["cost_measured"] += int(node.get("cost_measured_operations") or 0)
            if node.get("total_tokens") is not None:
                stage["total_tokens"] += float(node["total_tokens"])
            stage["token_eligible"] += int(node.get("token_eligible_operations") or 0)
            stage["token_measured"] += int(node.get("token_measured_operations") or 0)
            stage["failures"] += int(node.get("state") == "failed")
            stage["extra_attempts"] += max(0, int(node.get("attempts") or 0) - 1)

            for call in node.get("model_calls", []):
                if not isinstance(call, dict):
                    continue
                provider = str(call.get("provider") or "").strip() or None
                model = str(call.get("model") or "").strip() or None
                raw_attributes = call.get("attributes")
                attributes: dict[str, Any] = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
                identities = {
                    "models": (
                        f"{provider or 'unknown-provider'}::{model}",
                        model,
                        provider,
                        model,
                        attributes.get("model_family"),
                        attributes.get("model_vendor") or attributes.get("vendor"),
                    ),
                    "providers": (
                        provider,
                        provider,
                        provider,
                        None,
                        None,
                        attributes.get("model_vendor") or attributes.get("vendor"),
                    ),
                }
                for dimension, identity in identities.items():
                    key, label, provider_id, model_id, model_family, vendor_id = identity
                    if not label:
                        continue
                    participant_id = str(key)
                    bucket = attribution[dimension][participant_id]
                    bucket["label"] = str(label)
                    bucket["participant_id"] = participant_id
                    bucket["provider_id"] = provider_id
                    bucket["model_id"] = model_id
                    bucket["model_family"] = model_family
                    bucket["vendor_id"] = vendor_id
                    bucket["calls"].append(call)
                    seen[dimension].add(participant_id)
        for dimension, labels in seen.items():
            for label in labels:
                attribution[dimension][label]["runs"].add(execution_id)
                attribution[dimension][label].setdefault("states", []).append((failed, recovered))

    def performance(dimension: str) -> list[dict[str, Any]]:
        result = []
        for participant_id, bucket in attribution[dimension].items():
            calls = bucket["calls"]
            states = bucket.get("states", [])
            costs = [call.get("known_cost") for call in calls]
            tokens = [call.get("total_tokens") for call in calls]
            durations = [float(call.get("duration_seconds") or 0.0) for call in calls]
            runs = len(bucket["runs"])
            failures = sum(int(failed) for failed, _ in states)
            recovered = sum(int(recovered) for _, recovered in states)
            cost_measured = sum(value is not None for value in costs)
            token_measured = sum(value is not None for value in tokens)
            positive_runs = runs - failures
            result.append(
                {
                    "participant_id": participant_id,
                    "dimension": dimension.removesuffix("s"),
                    "label": bucket["label"],
                    "provider_id": bucket.get("provider_id"),
                    "model_id": bucket.get("model_id"),
                    "model_family": bucket.get("model_family"),
                    "vendor_id": bucket.get("vendor_id"),
                    "runs": runs,
                    "calls": len(calls),
                    "completed": runs - failures - recovered,
                    "successful": 0,
                    "failed": failures,
                    "recovered": recovered,
                    "extra_work": recovered,
                    "measured_cost": _sum_optional(iter(costs)) if cost_measured == len(costs) else None,
                    "cost_per_positive_run": None,
                    "time_per_positive_run": sum(durations) / positive_runs if positive_runs else None,
                    "failed_run_cost": None,
                    "total_tokens": _sum_optional(iter(tokens)) if token_measured == len(tokens) else None,
                    "tokens_per_positive_run": None,
                    "failed_run_tokens": None,
                    "failure_rate": failures / runs if runs else 0.0,
                    "extra_work_rate": recovered / runs if runs else 0.0,
                    "cost_coverage": cost_measured / len(costs) if costs else 0.0,
                    "semantics": "cohort+direct-attribution",
                    "active_seconds": sum(durations),
                    "p50_call_seconds": _percentile(durations, 0.50),
                    "p95_call_seconds": _percentile(durations, 0.95),
                    "cost_eligible_operations": len(costs),
                    "cost_measured_operations": cost_measured,
                    "token_eligible_operations": len(tokens),
                    "token_measured_operations": token_measured,
                }
            )
        return sorted(result, key=lambda item: (-int(item["runs"]), str(item["label"])))

    stage_rows = []
    for label, stage in stages.items():
        executions = len(stage["executions"])
        total_time = float(stage["time_seconds"])
        stage_rows.append(
            {
                "label": label,
                "calls": stage["calls"],
                "executions": executions,
                "usual_seconds": total_time / executions if executions else None,
                "time_seconds": total_time,
                "known_cost": (
                    sum(stage["known_costs"])
                    if stage["cost_eligible"] > 0 and stage["cost_measured"] == stage["cost_eligible"]
                    else None
                ),
                "total_tokens": (
                    stage["total_tokens"]
                    if stage["token_eligible"] > 0 and stage["token_measured"] == stage["token_eligible"]
                    else None
                ),
                "cost_eligible_operations": stage["cost_eligible"],
                "cost_measured_operations": stage["cost_measured"],
                "token_eligible_operations": stage["token_eligible"],
                "token_measured_operations": stage["token_measured"],
                "failures": stage["failures"],
                "extra_attempts": stage["extra_attempts"],
            }
        )
    stage_rows.sort(key=lambda item: (-float(item["time_seconds"]), str(item["label"])))
    return {"models": performance("models"), "providers": performance("providers"), "stages": stage_rows}


def workflow_execution(repo: AnalyticsRepository, workflow_id: str, execution_id: str) -> dict[str, Any] | None:
    detail = run_detail(repo, execution_id)
    if detail is None:
        return None
    replay = detail.get("workflow_replay")
    if not replay or replay["workflow"]["id"] != workflow_id:
        return None
    return detail


def workflow_operations(repo: AnalyticsRepository, workflow_id: str) -> dict[str, Any] | None:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    if workflow_id not in definitions:
        return None
    operations, measurements = repo.workflow_operation_facts(workflow_id)
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "workflow_id": workflow_id,
                "summary": _operation_summary(operations, measurements),
                "measurement_coverage": _measurement_coverage(measurements),
                "operations": operations,
                "measurements": measurements,
            }
        ),
    )


def workflow_evaluations(repo: AnalyticsRepository, workflow_id: str) -> dict[str, Any] | None:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    if workflow_id not in definitions:
        return None
    results = repo.workflow_evaluations(workflow_id)
    deduplicated: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for result in results:
        key = (
            str(result.get("execution_id") or ""),
            str(result.get("subject_id") or "execution"),
            str(result.get("name") or ""),
            str(result.get("definition_version") or "unversioned"),
        )
        deduplicated[key] = result
    final = [
        {**result, "passed": _explicit_evaluation_pass(result)}
        for result in deduplicated.values()
    ]
    passed = sum(_explicit_evaluation_pass(item) is True for item in final)
    attention = sum(_explicit_evaluation_pass(item) is False for item in final)
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "workflow_id": workflow_id,
                "summary": {
                    "reported": len(final),
                    "passed": passed,
                    "needs_attention": attention,
                    "unassessed": len(final) - passed - attention,
                    "executions": len({str(item.get("execution_id") or "") for item in final}),
                },
                "results": final,
                "campaigns": repo.workflow_evaluation_campaigns(workflow_id),
            }
        ),
    )


def workflow_evaluation_campaigns(repo: AnalyticsRepository, workflow_id: str) -> dict[str, Any] | None:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    if workflow_id not in definitions:
        return None
    return {"workflow_id": workflow_id, "campaigns": repo.workflow_evaluation_campaigns(workflow_id)}


def evaluation_campaign(repo: AnalyticsRepository, campaign_id: str) -> dict[str, Any] | None:
    return repo.evaluation_campaign(campaign_id)


def _operation_summary(operations: list[dict[str, Any]], measurements: list[dict[str, Any]]) -> dict[str, Any]:
    measured_by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for measurement in measurements:
        measured_by_operation[str(measurement.get("operation_id") or "")].append(measurement)
    groups: dict[str, dict[str, Any]] = {}
    for operation in operations:
        operation_type = str(operation.get("operation_type") or "unknown")
        bucket = groups.setdefault(
            operation_type,
            {
                "type": operation_type,
                "family": operation.get("family"),
                "operations": 0,
                "failed": 0,
                "active_seconds": 0.0,
                "roles": set(),
                "interfaces": set(),
                "providers": set(),
                "models": set(),
                "implementations": set(),
                "measurements": defaultdict(float),
            },
        )
        bucket["operations"] += 1
        bucket["failed"] += int(str(operation.get("status") or "").casefold() in {"error", "failed"})
        bucket["active_seconds"] += float(operation.get("duration_seconds") or 0.0)
        for key, target in (
            ("role", "roles"),
            ("interface", "interfaces"),
            ("provider_id", "providers"),
            ("model_id", "models"),
            ("implementation_id", "implementations"),
        ):
            if operation.get(key):
                bucket[target].add(str(operation[key]))
        for measurement in measured_by_operation.get(str(operation.get("operation_id") or ""), []):
            if measurement.get("measurement_status") == "measured" and measurement.get("value") is not None:
                bucket["measurements"][str(measurement["measurement_key"])] += float(measurement["value"])
    items = []
    for bucket in groups.values():
        items.append(
            {
                **bucket,
                "roles": sorted(bucket["roles"]),
                "interfaces": sorted(bucket["interfaces"]),
                "providers": sorted(bucket["providers"]),
                "models": sorted(bucket["models"]),
                "implementations": sorted(bucket["implementations"]),
                "measurements": dict(sorted(bucket["measurements"].items())),
            }
        )
    return {
        "total_operations": len(operations),
        "failed_operations": sum(
            int(str(item.get("status") or "").casefold() in {"error", "failed"}) for item in operations
        ),
        "types": sorted(items, key=lambda item: (-int(item["operations"]), str(item["type"]))),
    }


def _measurement_coverage(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"measured": 0, "missing": 0, "not_applicable": 0}
    for measurement in measurements:
        status = str(measurement.get("measurement_status") or "missing")
        counts[status if status in counts else "missing"] += 1
    applicable = counts["measured"] + counts["missing"]
    return {
        **counts,
        "applicable": applicable,
        "coverage": counts["measured"] / applicable if applicable else None,
    }


def _measurement_alerts(operations: list[dict[str, Any]], measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operation_map = {str(item.get("operation_id") or ""): item for item in operations}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for measurement in measurements:
        if measurement.get("measurement_status") != "missing":
            continue
        operation = operation_map.get(str(measurement.get("operation_id") or ""), {})
        key = (
            str(operation.get("operation_type") or "unknown"),
            str(measurement.get("measurement_key") or "unknown"),
        )
        bucket = groups.setdefault(
            key,
            {
                "operation_type": key[0],
                "measurement_key": key[1],
                "operations": 0,
                "executions": set(),
                "workflow_ids": set(),
            },
        )
        bucket["operations"] += 1
        bucket["executions"].add(str(operation.get("execution_id") or ""))
        if operation.get("workflow_id"):
            bucket["workflow_ids"].add(str(operation["workflow_id"]))
    return [
        {
            **bucket,
            "executions": len(bucket["executions"]),
            "workflow_ids": sorted(bucket["workflow_ids"]),
        }
        for bucket in sorted(groups.values(), key=lambda item: (-int(item["operations"]), str(item["operation_type"])))
    ]


def _explicit_evaluation_pass(result: dict[str, Any]) -> bool | None:
    raw_attributes = result.get("attributes")
    attributes: dict[str, Any] = raw_attributes if isinstance(raw_attributes, dict) else {}
    passed = attributes.get("passed")
    if isinstance(passed, bool):
        return passed
    target = attributes.get("target")
    direction = str(attributes.get("direction") or "").casefold()
    value = result.get("score") if result.get("score") is not None else result.get("value")
    if isinstance(value, (int, float)) and isinstance(target, (int, float)):
        if direction in {"higher_is_better", "min", "at_least", ">="}:
            return float(value) >= float(target)
        if direction in {"lower_is_better", "max", "at_most", "<="}:
            return float(value) <= float(target)
        if direction in {"equal", "=="}:
            return float(value) == float(target)
    return None


def compare(repo: AnalyticsRepository, dimension: str, filters: FilterState) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        jsonable_encoder({"dimension": dimension, "items": repo.get_comparison_insights(dimension, filters)}),
    )


def workflows(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_workflow_insights(filters, limit=10)))


def issues(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    result = dict(repo.get_issue_insights(filters))
    operations, measurements = repo.operation_health_facts()
    result["operation_failures"] = [
        item for item in _operation_summary(operations, measurements)["types"] if int(item["failed"]) > 0
    ]
    result["missing_required_measurements"] = _measurement_alerts(operations, measurements)
    return cast(dict[str, Any], jsonable_encoder(result))
