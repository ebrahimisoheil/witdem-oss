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
    return cast(
        dict[str, Any],
        jsonable_encoder(
            {
                "summary": summary,
                "outcomes": repo.execution_outcomes(execution_id),
                "graph": repo.replay(execution_id).model_dump(mode="json"),
                "semantic_records": [record.to_dict() for record in repo.semantic_replay_records(execution_id)],
            }
        ),
    )


def compare(repo: AnalyticsRepository, dimension: str, filters: FilterState) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        jsonable_encoder({"dimension": dimension, "items": repo.get_comparison_insights(dimension, filters)}),
    )


def workflows(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_workflow_insights(filters, limit=10)))


def issues(repo: AnalyticsRepository, filters: FilterState) -> dict[str, Any]:
    return cast(dict[str, Any], jsonable_encoder(repo.get_issue_insights(filters)))
