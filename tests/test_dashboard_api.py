from __future__ import annotations

import json

import duckdb
from fastapi.testclient import TestClient
from filelock import Timeout as FileLockTimeout

from witdem.analytics.core import Operation
from witdem.analytics.repository import AnalyticsRepository
from witdem.analytics.repository.analytics_repository import (
    _complete_operation_total,
    _cost_eligible,
    _goal_assurance_state,
    runtime_state,
)
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
    openapi = client.get("/api/openapi.json").json()
    assert openapi["info"]["title"] == "Witdem Dashboard API"
    assert openapi["paths"]["/api/v1/runs/{execution_id}"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/RunDetailResponse"}
    operation = openapi["components"]["schemas"]["OperationFact"]["properties"]
    assert operation["family"]["type"] == "string"
    assert operation["interface"]["type"] == "string"
    assert operation["plane"]["anyOf"][0]["enum"] == ["control", "work", "business"]
    summary = openapi["components"]["schemas"]["RunSummary"]["properties"]
    assert summary["model_calls"]["default"] == 0
    assert summary["tool_calls"]["default"] == 0
    run_filters = {item["name"] for item in openapi["paths"]["/api/v1/runs"]["get"]["parameters"]}
    assert {
        "goal_status",
        "assurance_status",
        "application_outcome",
        "blocker",
        "evaluation_key",
        "evaluation_status",
        "cost_status",
        "token_status",
        "operation_type",
        "operation_status",
        "failure_location",
    } <= run_filters
    assert client.get("/").status_code == 200


def test_evaluation_pass_uses_explicit_direction_and_target() -> None:
    assert (
        service._explicit_evaluation_pass(
            {"value": 0.9, "attributes": {"target": 0.7, "direction": "higher_is_better"}}
        )
        is True
    )
    assert (
        service._explicit_evaluation_pass({"value": 0.9, "attributes": {"target": 0.7, "direction": "lower_is_better"}})
        is False
    )
    assert (
        service._explicit_evaluation_pass({"value": 1.0, "status": "valid", "attributes": {"label": "valid"}}) is None
    )


def test_dashboard_rejects_unknown_run(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    response = TestClient(create_dashboard_app(database)).get("/api/v1/runs/not-found")
    assert response.status_code == 404


def test_legacy_run_url_has_no_second_execution_view(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    response = TestClient(create_dashboard_app(database)).get(
        "/runs/not-found",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/runs?unavailable_replay=not-found"


def test_removed_graph_chunk_recovers_an_open_legacy_tab(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    response = TestClient(create_dashboard_app(database, static_dir=static_dir)).get(
        "/assets/advanced-workflow-graph-old-build.js"
    )

    assert response.status_code == 200
    assert "window.location.reload()" in response.text
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


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
    assert query_count <= 14
    assert lock_count == 1


def test_overview_snapshot_uses_direct_participant_and_applicability_contract(tmp_path) -> None:
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

    optimized = service.overview(AnalyticsRepository(database), FilterState())

    assert optimized["runtime_breakdown"] == {"completed": 1}
    assert optimized["outcome_breakdown"] == {}
    assert optimized["costs"]["measured_cost"] == 0.01
    assert optimized["costs"]["cost"] == {
        "total_runs": 1,
        "applicable_runs": 1,
        "complete_runs": 1,
        "partial_runs": 0,
        "missing_runs": 0,
        "not_applicable_runs": 0,
        "eligible_operations": 1,
        "measured_operations": 1,
        "coverage": 1.0,
        "operation_coverage": 1.0,
    }
    assert optimized["models"][0]["measured_cost"] == 0.01
    assert optimized["models"][0]["active_seconds"] == 1.0
    assert optimized["providers"][0]["participant_id"] == "test"


def test_semantic_wrapper_does_not_make_measured_child_cost_partial() -> None:
    wrapper = Operation(
        operation_id="wrapper",
        execution_id="run-1",
        kind="component",
        name="Fallback generator",
        attributes={
            "witdem.operation.type": "text_generation",
            "cost_unavailable_reason": "missing_provider",
        },
    )
    child = Operation(
        operation_id="model-call",
        execution_id="run-1",
        parent_span_id="wrapper",
        kind="model",
        name="chat",
        attributes={"provider": "openai", "model": "gpt-5.4", "cost_usd": 0.25},
    )

    assert _cost_eligible(wrapper) is False
    assert _complete_operation_total([wrapper, child], "cost") == (True, 0.25)


def test_explicit_non_model_cost_applicability_remains_authoritative() -> None:
    operation = Operation(
        operation_id="tool-call",
        execution_id="run-1",
        kind="tool",
        name="paid API",
        attributes={
            "witdem.operation.type": "api_call",
            "cost_applicable": True,
            "cost_unavailable_reason": "missing_provider",
        },
    )

    assert _cost_eligible(operation) is True
    assert _complete_operation_total([operation], "cost") == (True, None)


def test_serving_drilldowns_filter_goal_assurance_evaluation_and_failures(tmp_path, monkeypatch) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    repository = AnalyticsRepository(database)
    execution_facts = [
        {
            "execution_id": "achieved",
            "runtime_status": "completed",
            "failure_count": 0,
            "product_goal_reported": True,
            "product_goal_achieved": True,
            "evidence_sufficient": True,
            "application_outcome": "approved",
            "closest_blocker": None,
            "failure_location": None,
            "model_calls": 1,
            "cost_coverage": 1.0,
        },
        {
            "execution_id": "failed",
            "runtime_status": "failed",
            "failure_count": 1,
            "product_goal_reported": True,
            "product_goal_achieved": False,
            "evidence_sufficient": False,
            "application_outcome": "manual_review_required",
            "closest_blocker": "policy_check_failed",
            "failure_location": "retriever",
            "model_calls": 0,
            "cost_coverage": 0.0,
        },
    ]
    operation_facts = [
        {
            "operation_id": "model-call",
            "execution_id": "achieved",
            "kind": "model",
            "canonical_key": "generation",
            "status": "ok",
            "sequence_number": 1,
            "attributes": json.dumps(
                {"witdem.operation.type": "text_generation", "cost_usd": 0.25, "total_tokens": 10}
            ),
        },
        {
            "operation_id": "retrieval",
            "execution_id": "failed",
            "kind": "operation",
            "canonical_key": "retriever",
            "status": "error",
            "sequence_number": 2,
            "attributes": json.dumps({"witdem.operation.type": "retrieval"}),
        },
    ]
    evaluations = [
        {
            "execution_id": "achieved",
            "name": "Grounding",
            "score": 0.9,
            "attributes": json.dumps(
                {"evaluation_key": "grounding", "target": 0.8, "direction": "higher_is_better"}
            ),
        }
    ]

    monkeypatch.setattr(repository, "_serving_contracts_by_execution", lambda: {})
    monkeypatch.setattr(repository, "_latest_evaluation_facts", lambda _allowed=None: evaluations)
    monkeypatch.setattr(
        repository,
        "_query",
        lambda sql, _params=(): operation_facts if "serving.operation_facts" in sql else execution_facts,
    )

    achieved = repository._serving_execution_rows(
        FilterState(
            goal_status="achieved",
            assurance_status="assured",
            application_outcome="approved",
            evaluation_key="grounding",
            evaluation_status="passed",
            cost_status="complete",
        ),
        limit=None,
    )
    failed = repository._serving_execution_rows(
        FilterState(
            status="failed",
            goal_status="not_achieved",
            blocker="policy_check_failed",
            failure_location="retriever",
            operation_type="retrieval",
            operation_status="failed",
            has_failure=True,
        ),
        limit=None,
    )

    assert [row["execution_id"] for row in achieved] == ["achieved"]
    assert [row["execution_id"] for row in failed] == ["failed"]


def test_goal_assurance_respects_declared_evaluation_targets() -> None:
    assert (
        AnalyticsRepository._evaluation_met_target(
            {"score": 0.8333},
            {"target": 1.0, "direction": "higher_is_better"},
        )
        is False
    )
    assert (
        AnalyticsRepository._evaluation_met_target(
            {"score": 0.2},
            {"target": 0.5, "direction": "lower_is_better"},
        )
        is True
    )
    assert AnalyticsRepository._evaluation_met_target({"score": 1.0}, {}) is None
    assert AnalyticsRepository._evaluation_met_target({"label": "valid"}, {}) is None
    assert AnalyticsRepository._evaluation_met_target({"label": "anything"}, {"passed": True}) is True
    assert _goal_assurance_state({"product_goal_achieved": False, "assurance_status": "assured"}) == "not_achieved"
    assert _goal_assurance_state({"product_goal_achieved": True, "assurance_status": "assured"}) == "assured"
    assert (
        _goal_assurance_state({"product_goal_achieved": True, "assurance_status": "needs_attention"})
        == "needs_attention"
    )
    assert _goal_assurance_state({"product_goal_achieved": True}) == "unassessed"


def test_goal_portfolio_exposes_an_execution_id_only_for_single_run_goals(monkeypatch) -> None:
    repository = object.__new__(AnalyticsRepository)
    repository._serving_tables = set()
    rows = [
        {
            "execution_id": "latest-run",
            "product_goal_reported": True,
            "product_goal_achieved": False,
            "contract_hash": "goal-v1",
            "contract_name": "Resolve request",
        },
        {
            "execution_id": "older-run",
            "product_goal_reported": True,
            "product_goal_achieved": False,
            "contract_hash": "goal-v1",
            "contract_name": "Resolve request",
        },
        {
            "execution_id": "single-run",
            "product_goal_reported": True,
            "product_goal_achieved": False,
            "contract_hash": "review-v1",
            "contract_name": "Review request",
        },
    ]
    monkeypatch.setattr(repository, "execution_rows", lambda _filters, limit=None: rows)
    monkeypatch.setattr(repository, "contract_definitions", lambda _filters: [])

    portfolio, _summary = repository.goal_assurance(FilterState())

    goals = {item["goal_name"]: item for item in portfolio}
    assert goals["Resolve request"]["single_execution_id"] is None
    assert goals["Review request"]["single_execution_id"] == "single-run"


def test_operation_health_facts_are_scoped_to_filtered_executions(monkeypatch) -> None:
    repository = object.__new__(AnalyticsRepository)
    repository._tables = {"operation_classification_facts"}
    monkeypatch.setattr(repository, "execution_rows", lambda _filters, limit=None: [{"execution_id": "selected"}])
    monkeypatch.setattr(
        repository,
        "_query",
        lambda _sql: [
            {"operation_id": "kept", "execution_id": "selected", "operation_type": "retrieval"},
            {"operation_id": "excluded", "execution_id": "other", "operation_type": "embedding"},
        ],
    )

    operations, measurements = repository.operation_health_facts(FilterState(provider="selected-provider"))

    assert [operation["operation_id"] for operation in operations] == ["kept"]
    assert measurements == []


def test_runtime_states_are_mutually_exclusive_and_running_is_not_attention() -> None:
    assert runtime_state({"runtime_outcome": "running", "failure_count": 4}) == "running"
    assert runtime_state({"runtime_outcome": "recovered"}) == "recovered"
    assert runtime_state({"runtime_outcome": "completed", "failure_count": 1}) == "failed"
    assert runtime_state({"runtime_outcome": "completed", "failure_count": 0}) == "completed"
    assert runtime_state({"runtime_outcome": "future-state"}) == "unknown"


def test_participant_attribution_is_direct_neutral_and_materialized(tmp_path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    connection = duckdb.connect(str(database))
    try:
        for execution_id, start, end in (
            ("idle", "2026-08-23T11:59:00+00:00", "2026-08-23T11:59:01+00:00"),
            ("complete", "2026-08-23T12:00:00+00:00", "2026-08-23T12:00:20+00:00"),
            ("partial", "2026-08-23T12:01:00+00:00", "2026-08-23T12:01:04+00:00"),
        ):
            connection.execute(
                "INSERT INTO executions VALUES (?, 'runtime-any', ?, ?, 'completed', '1.0', '{}')",
                [execution_id, start, end],
            )
        operations = [
            (
                "alpha-outer",
                "complete",
                "2026-08-23T12:00:00+00:00",
                "2026-08-23T12:00:10+00:00",
                {
                    "provider": "alpha-gateway",
                    "model": "shared-model",
                    "model_vendor": "vendor-any",
                    "cost_usd": 0.01,
                    "total_tokens": 10,
                },
            ),
            (
                "alpha-inner",
                "complete",
                "2026-08-23T12:00:02+00:00",
                "2026-08-23T12:00:04+00:00",
                {"provider": "alpha-gateway", "model": "shared-model", "cost_usd": 0.02, "total_tokens": 20},
            ),
            (
                "beta",
                "complete",
                "2026-08-23T12:00:10+00:00",
                "2026-08-23T12:00:15+00:00",
                {"provider": "beta-gateway", "model": "shared-model", "cost_usd": 0.5, "total_tokens": 40},
            ),
            (
                "alpha-unknown",
                "partial",
                "2026-08-23T12:01:00+00:00",
                "2026-08-23T12:01:04+00:00",
                {"provider": "alpha-gateway", "model": "shared-model", "total_tokens": 7},
            ),
            (
                "alpha-partial-measured",
                "partial",
                "2026-08-23T12:01:01+00:00",
                "2026-08-23T12:01:02+00:00",
                {"provider": "alpha-gateway", "model": "shared-model", "cost_usd": 0.01, "total_tokens": 3},
            ),
        ]
        for operation_id, execution_id, start, end, attributes in operations:
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, NULL, ?, NULL, 'model', 'call', 'ok', ?, ?, NULL, ?)",
                [operation_id, execution_id, operation_id, start, end, json.dumps(attributes)],
            )
    finally:
        connection.close()

    service.materialize_workflow_projections(database)
    payload = service.overview(AnalyticsRepository(database), FilterState())
    models = {item["participant_id"]: item for item in payload["models"]}

    assert set(models) == {
        "alpha-gateway::model:shared_model",
        "beta-gateway::model:shared_model",
    }
    assert models["alpha-gateway::model:shared_model"]["active_seconds"] == 14.0
    assert models["alpha-gateway::model:shared_model"]["vendor_id"] == "vendor-any"
    assert models["alpha-gateway::model:shared_model"]["measured_cost"] == 0.04
    assert models["alpha-gateway::model:shared_model"]["cost_coverage"] == 3 / 4
    assert models["beta-gateway::model:shared_model"]["measured_cost"] == 0.5
    assert payload["costs"]["cost"]["applicable_runs"] == 2
    assert payload["costs"]["cost"]["complete_runs"] == 1
    assert payload["costs"]["cost"]["partial_runs"] == 1
    assert payload["costs"]["cost"]["not_applicable_runs"] == 1
    assert payload["costs"]["cost"]["operation_coverage"] == 0.8

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM participant_execution_facts").fetchone() == (6,)
    finally:
        connection.close()
