from __future__ import annotations

import json

import duckdb
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from filelock import Timeout as FileLockTimeout

from witdem.analytics.repository import AnalyticsRepository
from witdem.analytics.repository.state import FilterState
from witdem.dashboard import service
from witdem.dashboard.app import create_dashboard_app
from witdem.ingest import live_db


def test_dashboard_serves_api_and_spa(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    client = TestClient(create_dashboard_app(database))

    assert client.get("/health").json() == {"status": "ok"}
    overview = client.get("/api/v1/overview")
    assert overview.status_code == 200
    assert overview.json()["execution"]["total_runs"] == 0
    assert client.get("/api/v1/runs").json() == {
        "items": [],
        "count": 0,
        "page": 1,
        "page_size": 10,
        "pages": 1,
    }
    assert "Witdem Dashboard API" in client.get("/api/openapi.json").text
    assert client.get("/").status_code == 200


def test_dashboard_rejects_unknown_run(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    response = TestClient(create_dashboard_app(database)).get("/api/v1/runs/not-found")
    assert response.status_code == 404


def test_runs_are_server_paginated_ten_at_a_time(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    connection = duckdb.connect(str(database))
    try:
        for index in range(12):
            connection.execute(
                "INSERT INTO executions VALUES (?, 'test', ?, ?, 'completed', '1.0', '{}')",
                [
                    f"run-{index:02d}",
                    f"2026-08-23T12:00:{index:02d}+00:00",
                    f"2026-08-23T12:00:{index + 1:02d}+00:00",
                ],
            )
    finally:
        connection.close()

    client = TestClient(create_dashboard_app(database))
    first = client.get("/api/v1/runs").json()
    second = client.get("/api/v1/runs?page=2").json()

    assert first["count"] == 12
    assert first["page_size"] == 10
    assert len(first["items"]) == 10
    assert second["page"] == 2
    assert len(second["items"]) == 2


def test_insight_endpoints_have_actionable_empty_contracts(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    client = TestClient(create_dashboard_app(database))

    assert client.get("/api/v1/compare/model").json() == {"dimension": "model", "items": []}
    assert client.get("/api/v1/workflows").json() == {"items": [], "stages": [], "paths": []}
    issues = client.get("/api/v1/issues").json()
    assert issues["summary"] == {
        "runs": 0,
        "terminal_failures": 0,
        "recovered_runs": 0,
        "extra_attempts": 0,
        "quality_gaps": 0,
    }
    assert issues["failures"] == []
    assert issues["retries"] == []
    assert issues["outliers"] == []


def test_dashboard_reports_database_contention_as_retryable(tmp_path, monkeypatch) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)

    def busy_repository(_database):
        raise FileLockTimeout(str(database.with_suffix(".duckdb.lock")))

    monkeypatch.setattr(service, "repository", busy_repository)
    response = TestClient(create_dashboard_app(database)).get("/api/v1/runs")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "Retrying shortly" in response.json()["detail"]


def test_overview_uses_one_read_session_and_reuses_shared_populations(tmp_path, monkeypatch) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    repo = service.create_backend(database).create_repository()
    query_count = 0
    lock_count = 0
    original_query = repo._query
    original_file_lock = repo._file_lock

    def counted_query(sql, params=()):
        nonlocal query_count
        query_count += 1
        return original_query(sql, params)

    def counted_file_lock():
        nonlocal lock_count
        lock_count += 1
        return original_file_lock()

    monkeypatch.setattr(repo, "_query", counted_query)
    monkeypatch.setattr(repo, "_file_lock", counted_file_lock)

    result = service.overview(repo, FilterState())

    assert result["execution"]["total_runs"] == 0
    assert query_count <= 10
    assert lock_count == 1


def test_overview_snapshot_preserves_the_public_payload(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
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
                json.dumps({"provider": "test", "model": "small", "cost_usd": 0.01, "total_tokens": 100}),
            ],
        )
    finally:
        connection.close()

    filters = FilterState()
    baseline_repo = AnalyticsRepository(database)
    rows = baseline_repo.execution_rows(filters, limit=None)
    runtime_breakdown: dict[str, int] = {}
    outcome_breakdown: dict[str, int] = {}
    for row in rows:
        runtime = str(row.get("runtime_outcome") or row.get("runtime_status") or row.get("status") or "unknown")
        runtime_breakdown[runtime] = runtime_breakdown.get(runtime, 0) + 1
        outcome = row.get("application_outcome") or row.get("business_outcome") or row.get("outcome")
        if outcome:
            label = str(outcome)
            outcome_breakdown[label] = outcome_breakdown.get(label, 0) + 1
    goals = baseline_repo.get_product_goal_summary(filters)
    baseline = jsonable_encoder(
        {
            "execution": baseline_repo.get_execution_summary(filters).to_dict(),
            "goals": {
                **goals.to_dict(),
                "coverage": goals.coverage,
                "success_rate": goals.success_rate,
                "decision_correctness_rate": goals.decision_correctness_rate,
            },
            "costs": baseline_repo.get_cost_summary(filters).to_dict(),
            "cost_unavailable": baseline_repo.cost_unavailable_reasons(filters),
            "models": [item.to_dict() for item in baseline_repo.get_model_breakdown(filters)],
            "providers": [item.to_dict() for item in baseline_repo.get_provider_breakdown(filters)],
            "workflows": [item.to_dict() for item in baseline_repo.get_performance_summary("workflow", filters)],
            "stages": baseline_repo.entity_summary("stages", filters),
            "runtime_breakdown": runtime_breakdown,
            "outcome_breakdown": outcome_breakdown,
            "failures": [item.to_dict() for item in baseline_repo.get_failure_summary(filters)[:8]],
            "evaluations": baseline_repo.evaluation_summary(filters),
            "goal_misses": baseline_repo.goal_miss_summary(filters),
            "goal_trend": baseline_repo.goal_trend(filters),
            "goal_portfolio": baseline_repo.goal_assurance(filters)[0],
            "assurance_summary": baseline_repo.goal_assurance(filters)[1],
            "paths": [],
            "contracts": baseline_repo.contract_definitions(filters),
        }
    )

    optimized = service.overview(AnalyticsRepository(database), filters)
    assert {key: value for key, value in optimized.items() if key != "metadata"} == baseline
    assert optimized["metadata"] == service.metadata(AnalyticsRepository(database))


def test_goal_assurance_respects_declared_evaluation_targets() -> None:
    assert AnalyticsRepository._evaluation_met_target(
        {"score": 0.8333},
        {"target": 1.0, "direction": "higher_is_better"},
    ) is False
    assert AnalyticsRepository._evaluation_met_target(
        {"score": 0.2},
        {"target": 0.5, "direction": "lower_is_better"},
    ) is True
