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

from witdem.analytics.contracts import MetadataSnapshot
from witdem.analytics.repository import AnalyticsRepository, create_backend
from witdem.analytics.repository.state import FilterState
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
    }


def metadata(repo: AnalyticsRepository) -> dict[str, Any]:
    return _metadata_payload(repo.get_metadata_snapshot())


def overview(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    snapshot = repo.get_overview_snapshot(filters)
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
                "paths": [],
                "contracts": snapshot.contracts,
                "metadata": _metadata_payload(snapshot.metadata),
            }
        ),
    )


def runs(repo: AnalyticsRepository, filters: FilterState, page: int = 1, page_size: int = 10) -> dict[str, Any]:
    rows = repo.execution_rows(filters, limit=None)
    size = max(1, min(page_size, 100))
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    current = max(1, min(page, pages))
    start = (current - 1) * size
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "items": rows[start : start + size],
                "count": total,
                "page": current,
                "page_size": size,
                "pages": pages,
            }
        ),
    )


def run_detail(repo: AnalyticsRepository, execution_id: str) -> dict[str, Any] | None:
    fact = repo.execution_fact(execution_id)
    rows = [row for row in repo.execution_rows(limit=None) if str(row["execution_id"]) == execution_id]
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
    projection = (
        project_execution(workflow, execution=summary, graph=graph) if workflow is not None else None
    )
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "summary": summary,
                "outcomes": repo.execution_outcomes(execution_id),
                "graph": graph,
                "semantic_records": semantic_records,
                "workflow_replay": projection,
                "canonical_url": (
                    f"/workflows/{workflow.id}/executions/{execution_id}" if workflow is not None else None
                ),
            }
        ),
    )


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
    execution = dict(summary)
    graph_execution = graph.get("execution")
    if isinstance(graph_execution, dict):
        execution["attributes"] = graph_execution.get("attributes", {})
        execution["runtime_id"] = execution.get("runtime_id") or graph_execution.get("runtime_id")
    from witdem.workflows import WorkflowRegistry

    return WorkflowRegistry(definitions.values()).match(execution)


def workflow_catalog(repo: AnalyticsRepository) -> dict[str, Any]:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    runs_by_workflow: dict[str, list[dict[str, Any]]] = {workflow_id: [] for workflow_id in definitions}
    for row in repo.execution_rows(limit=None):
        execution_id = str(row["execution_id"])
        association = repo.execution_workflow(execution_id)
        workflow_id = str(association.get("workflow_id")) if association else None
        if workflow_id not in definitions:
            detail = run_detail(repo, execution_id)
            replay = detail.get("workflow_replay") if detail else None
            workflow_id = str(replay["workflow"]["id"]) if replay else None
        if workflow_id in runs_by_workflow:
            runs_by_workflow[workflow_id].append(row)
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
                        "execution_count": len(runs_by_workflow[definition.id]),
                        "latest_execution": (
                            runs_by_workflow[definition.id][0]
                            if runs_by_workflow[definition.id]
                            else None
                        ),
                    }
                    for definition in definitions.values()
                ]
            }
        ),
    )


def workflow_detail(repo: AnalyticsRepository, workflow_id: str) -> dict[str, Any] | None:
    definitions = {**_persisted_definitions(repo), **load_registry().definitions}
    definition = definitions.get(workflow_id)
    if definition is None:
        return None
    executions = []
    for row in repo.execution_rows(limit=None):
        execution_id = str(row["execution_id"])
        association = repo.execution_workflow(execution_id)
        detail = run_detail(repo, execution_id)
        replay = detail.get("workflow_replay") if detail else None
        associated = association and str(association.get("workflow_id")) == workflow_id
        projected = replay and replay["workflow"]["id"] == workflow_id
        if not associated and not projected:
            continue
        nodes = replay.get("nodes", []) if replay else []
        active_nodes = [node for node in nodes if node.get("state") != "inactive"]
        attempts = sum(int(node.get("attempts") or 0) for node in active_nodes)
        models = sorted({str(model) for node in active_nodes for model in node.get("models", [])})
        providers = sorted(
            {str(provider) for node in active_nodes for provider in node.get("providers", [])}
        )
        executions.append(
            {
                **row,
                "workflow_active_steps": len(active_nodes),
                "workflow_total_steps": len(nodes) or len(definition.nodes),
                "workflow_attempts": attempts,
                "workflow_retry_attempts": sum(
                    max(0, int(node.get("attempts") or 0) - 1) for node in active_nodes
                ),
                "workflow_recovered_steps": sum(
                    1 for node in active_nodes if node.get("state") == "recovered"
                ),
                "workflow_failed_steps": sum(
                    1 for node in active_nodes if node.get("state") == "failed"
                ),
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


def compare(repo: AnalyticsRepository, dimension: str, filters: FilterState) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        jsonable_encoder({"dimension": dimension, "items": repo.get_comparison_insights(dimension, filters)}),
    )


def workflows(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_workflow_insights(filters, limit=10)))


def issues(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_issue_insights(filters)))
