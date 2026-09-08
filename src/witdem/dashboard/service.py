"""Repository-backed dashboard read service.

This module is deliberately unaware of HTTP and DuckDB.  It translates the
stable analytics repository into frontend-oriented JSON contracts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from fastapi.encoders import jsonable_encoder

import witdem.analytics.evidence as evidence_contracts
from witdem.analytics.contracts import MetadataSnapshot
from witdem.analytics.operation_facts import workflow_operation_facts as _operation_facts
from witdem.analytics.repository import AnalyticsRepository, create_backend
from witdem.analytics.repository.state import FilterState
from witdem.analytics.workflow_analytics import workflow_projection_analytics as _workflow_projection_analytics
from witdem.update import check_updates, installed_versions
from witdem.workflows import WorkflowDefinition, definition_from_record, load_registry, project_execution

_explicit_evaluation_pass = evidence_contracts.explicit_evaluation_pass
_measurement_coverage = evidence_contracts.measurement_coverage
_operation_profile_inputs = evidence_contracts.operation_profile_inputs
_operation_summary = evidence_contracts.operation_summary


def filters_from_values(
    *,
    workflow: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    tool: str | None = None,
    stage: str | None = None,
    contract_hash: str | None = None,
    goal_status: str | None = None,
    assurance_status: str | None = None,
    application_outcome: str | None = None,
    blocker: str | None = None,
    evaluation_key: str | None = None,
    evaluation_status: str | None = None,
    cost_status: str | None = None,
    token_status: str | None = None,
    operation_type: str | None = None,
    operation_status: str | None = None,
    failure_location: str | None = None,
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
        goal_status=goal_status,
        assurance_status=assurance_status,
        application_outcome=application_outcome,
        blocker=blocker,
        evaluation_key=evaluation_key,
        evaluation_status=evaluation_status,
        cost_status=cost_status,
        token_status=token_status,
        operation_type=operation_type,
        operation_status=operation_status,
        failure_location=failure_location,
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
        all_operation_facts, all_operation_measurements = repo.operation_health_facts(filters)
        operation_facts, operation_measurements = _operation_profile_inputs(
            all_operation_facts, all_operation_measurements
        )
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
                "operation_health": _operation_summary(all_operation_facts, all_operation_measurements),
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
    all_operation_facts, all_measurements = repo.execution_operation_facts(execution_id)
    operation_facts, measurements = _operation_profile_inputs(all_operation_facts, all_measurements)
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
                "operation_summary": _operation_summary(all_operation_facts, all_measurements),
                "measurements": measurements,
                "measurement_coverage": _measurement_coverage(measurements),
                "evaluation_results": evaluation_results,
                "canonical_url": (
                    f"/workflows/{workflow.id}/executions/{execution_id}" if workflow is not None else None
                ),
            }
        ),
    )


def evidence_bundle(repo: AnalyticsRepository, execution_id: str) -> evidence_contracts.EvidenceBundle | None:
    """Return the public neutral evidence export for one execution."""

    try:
        return repo.export_evidence_bundle(execution_id)
    except KeyError:
        return None


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
        selected_ids = set(selected)
        with repo._overview_read_session():
            operations_by_execution = repo.operations_by_execution_ids(selected_ids)
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
            participant_facts = repo.build_participant_facts(
                selected_ids,
                operations_by_execution=operations_by_execution,
            )
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
                        "framework": None,
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
    for projected_row in repo.workflow_projection_rows(workflow_id, limit=100):
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
                "execution_window": {
                    "schema_version": "v1alpha1",
                    "limit": 100,
                    "included_count": len(executions),
                    "total_count": int((catalog_row or {}).get("execution_count") or len(executions)),
                    "order": "projected_at_desc",
                },
            }
        ),
    )




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
    all_operations, all_measurements = repo.workflow_operation_facts(workflow_id)
    operations, measurements = _operation_profile_inputs(all_operations, all_measurements)
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "workflow_id": workflow_id,
                "summary": _operation_summary(all_operations, all_measurements),
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
    profile = evidence_contracts.evaluation_profile(repo.workflow_evaluations(workflow_id))
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "workflow_id": workflow_id,
                **profile,
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


def _measurement_alerts(operations: list[dict[str, Any]], measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operations, measurements = _operation_profile_inputs(operations, measurements)
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


def compare(repo: AnalyticsRepository, dimension: str, filters: FilterState) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        jsonable_encoder({"dimension": dimension, "items": repo.get_comparison_insights(dimension, filters)}),
    )


def workflows(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_workflow_insights(filters, limit=10)))


def issues(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    result = dict(repo.get_issue_insights(filters))
    operations, measurements = repo.operation_health_facts(filters)
    result["operation_failures"] = [
        item for item in _operation_summary(operations, measurements)["types"] if int(item["failed"]) > 0
    ]
    result["missing_required_measurements"] = _measurement_alerts(operations, measurements)
    return cast(dict[str, Any], jsonable_encoder(result))
