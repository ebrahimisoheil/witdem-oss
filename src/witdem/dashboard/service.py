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
    projection = repo.workflow_projection(execution_id)
    if projection is None and workflow is not None:
        projection = project_execution(workflow, execution=summary, graph=graph)
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
        summary.get("known_cost")
        if summary.get("known_cost") is not None
        else summary.get("measured_cost")
    )
    graph = repo.replay(execution_id).model_dump(mode="json")
    semantic_records = [record.to_dict() for record in repo.semantic_replay_records(execution_id)]
    workflow = _resolve_workflow(repo, summary, graph, semantic_records)
    return project_execution(workflow, execution=summary, graph=graph) if workflow is not None else None


def materialize_workflow_projections(database: Path, execution_ids: list[str] | None = None) -> dict[str, int]:
    """Build projections outside dashboard request handling and persist them atomically."""

    projections: list[dict[str, Any]] = []
    with repository(database) as repo:
        selected = execution_ids or [str(row["execution_id"]) for row in repo.execution_rows(limit=None)]
        for execution_id in selected:
            projection = _projection_for_execution(repo, execution_id)
            if projection is not None:
                projections.append(projection)
    from witdem.ingest.live_db import store_workflow_projection

    for projection in projections:
        store_workflow_projection(database, projection)
    return {"requested": len(execution_ids or projections), "materialized": len(projections)}


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
    projection_catalog = {
        str(row["workflow_id"]): row for row in repo.workflow_projection_catalog()
    }
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
    row["workflow_models"] = sorted(
        {str(model) for node in active_nodes for model in node.get("models", [])}
    )
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
                "analytics": _workflow_projection_analytics(replays),
                "execution_count": int((catalog_row or {}).get("execution_count") or len(executions)),
            }
        ),
    )


def _sum_optional(values: Iterator[Any]) -> float | None:
    known = [float(value) for value in values if value is not None]
    return sum(known) if known else None


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
                    "total_tokens": 0.0,
                    "token_measured": False,
                    "failures": 0,
                    "extra_attempts": 0,
                },
            )
            stage["calls"] += int(node.get("attempts") or 0)
            stage["executions"].add(execution_id)
            stage["time_seconds"] += float(node.get("duration_seconds") or 0)
            if node.get("known_cost") is not None:
                stage["known_costs"].append(float(node["known_cost"]))
            if node.get("total_tokens") is not None:
                stage["total_tokens"] += float(node["total_tokens"])
                stage["token_measured"] = True
            stage["failures"] += int(node.get("state") == "failed")
            stage["extra_attempts"] += max(0, int(node.get("attempts") or 0) - 1)

            for call in node.get("model_calls", []):
                if not isinstance(call, dict):
                    continue
                for dimension, field in (("models", "model"), ("providers", "provider")):
                    label = call.get(field)
                    if not label:
                        continue
                    key = str(label)
                    attribution[dimension][key]["calls"].append(call)
                    seen[dimension].add(key)
        for dimension, labels in seen.items():
            for label in labels:
                attribution[dimension][label]["runs"].add(execution_id)
                attribution[dimension][label].setdefault("states", []).append((failed, recovered))

    def performance(dimension: str) -> list[dict[str, Any]]:
        result = []
        for label, bucket in attribution[dimension].items():
            calls = bucket["calls"]
            states = bucket.get("states", [])
            costs = [call.get("known_cost") for call in calls]
            tokens = [call.get("total_tokens") for call in calls]
            runs = len(bucket["runs"])
            failures = sum(int(failed) for failed, _ in states)
            recovered = sum(int(recovered) for _, recovered in states)
            result.append(
                {
                    "label": label,
                    "runs": runs,
                    "completed": runs - failures,
                    "failed": failures,
                    "recovered": recovered,
                    "measured_cost": _sum_optional(iter(costs)),
                    "time_per_positive_run": None,
                    "total_tokens": _sum_optional(iter(tokens)),
                    "failure_rate": failures / runs if runs else 0.0,
                    "cost_coverage": sum(value is not None for value in costs) / len(costs) if costs else 0.0,
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
                "known_cost": sum(stage["known_costs"]) if stage["known_costs"] else None,
                "total_tokens": stage["total_tokens"] if stage["token_measured"] else None,
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


def compare(repo: AnalyticsRepository, dimension: str, filters: FilterState) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        jsonable_encoder({"dimension": dimension, "items": repo.get_comparison_insights(dimension, filters)}),
    )


def workflows(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_workflow_insights(filters, limit=10)))


def issues(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_issue_insights(filters)))
