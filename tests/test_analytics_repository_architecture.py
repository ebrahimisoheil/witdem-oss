from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from witdem.analytics.contracts import (
    CostSummary,
    ExecutionSummary,
    FailureSummary,
    ModelSummary,
    PathSummary,
    PerformanceSummary,
    ProductGoalSummary,
    ProviderSummary,
)
from witdem.analytics.repository.analytics_repository import AnalyticsRepository, _serving_runtime_outcome
from witdem.analytics.repository.sql_loader import QueryNotFoundError, QueryTemplateError, load_query
from witdem.ingest.live_db import initialize_analytics_store

ROOT = Path(__file__).resolve().parents[1]
QUERY_ROOT = ROOT / "src" / "witdem" / "analytics" / "queries"


def test_completed_execution_with_a_failed_child_is_recovered_not_terminal() -> None:
    assert _serving_runtime_outcome("completed", 1) == "recovered"
    assert _serving_runtime_outcome("failed", 1) == "failed"
    assert _serving_runtime_outcome("completed", 0) == "completed"


def test_sql_catalog_contains_the_analytics_families() -> None:
    expected = {
        "execution_health",
        "cost_summary",
        "performance",
        "failure_patterns",
        "providers",
        "models",
        "path_frequency",
        "loops",
        "success_metrics",
    }
    assert {path.stem for path in QUERY_ROOT.rglob("*.sql")} >= expected


def test_every_sql_artifact_loads() -> None:
    for path in QUERY_ROOT.rglob("*.sql"):
        query_name = path.relative_to(QUERY_ROOT).with_suffix("").as_posix()
        fragments = {"alias": "e"} if path.stem.startswith("filter_") else None
        if query_name == "overview/execution_health":
            fragments = {"where": "TRUE", "limit_clause": ""}
        if query_name == "execution/execution_population":
            fragments = {"where": "TRUE"}
        assert load_query(query_name, fragments=fragments)


def test_sql_loader_renders_structural_fragments_and_keeps_values_bound() -> None:
    query = load_query("overview/execution_health", fragments={"where": "TRUE", "limit_clause": ""})
    assert "FROM executions" in query
    assert "?" in load_query("shared/filter_provider", fragments={"alias": "e"})
    assert "provider_name" not in query

    with pytest.raises(QueryNotFoundError):
        load_query("does/not/exist")
    with pytest.raises(QueryTemplateError):
        load_query("overview/execution_health")


def test_repository_is_analytics_owned_and_returns_semantic_columns(tmp_path: Path) -> None:
    database = initialize_analytics_store(tmp_path / "analytics.duckdb")
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "INSERT INTO executions VALUES ('run-1', 'test', ?, ?, 'completed', '1.0', '{}')",
            ["2026-08-23T12:00:00+00:00", "2026-08-23T12:00:01+00:00"],
        )
        connection.execute(
            "INSERT INTO operations VALUES "
            "('op-1', 'run-1', NULL, 'span-1', NULL, 'model', 'model', 'ok', ?, ?, NULL, ?)",
            [
                "2026-08-23T12:00:00+00:00",
                "2026-08-23T12:00:01+00:00",
                json.dumps({"cost_usd": 0.01, "total_tokens": 100}),
            ],
        )
    finally:
        connection.close()

    repository = AnalyticsRepository(database)
    try:
        assert repository.__class__.__module__ == "witdem.analytics.repository.analytics_repository"
        rows = repository.execution_rows(limit=1)
        assert rows
        assert {
            "execution_id",
            "duration_seconds",
            "operation_count",
            "known_cost",
            "provider",
            "model",
            "failure_location",
        } <= rows[0].keys()
        execution_summary = repository.get_execution_summary()
        assert isinstance(execution_summary, ExecutionSummary)
        assert execution_summary.total_runs == 1
        assert execution_summary.failed_runs == 0
        assert isinstance(repository.get_cost_summary(), CostSummary)
        assert repository.get_cost_summary().measured_cost is not None
        assert all(isinstance(row, ProviderSummary) for row in repository.get_provider_breakdown())
        assert all(isinstance(row, ModelSummary) for row in repository.get_model_breakdown())
        assert all(isinstance(row, PerformanceSummary) for row in repository.get_performance_summary())
        assert all(isinstance(row, FailureSummary) for row in repository.get_failure_summary())
        assert all(isinstance(row, PathSummary) for row in repository.get_path_summary())
        assert isinstance(repository.get_product_goal_summary(), ProductGoalSummary)
    finally:
        repository.close()
