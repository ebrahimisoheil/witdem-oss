from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from witdem.analytics.repository import AnalyticsRepository
from witdem.api import app
from witdem.elt.worker import run_pending
from witdem.ingest import corpus, live_db


@pytest.mark.integration
def test_duckle_worker_publishes_canonical_and_serving_rows(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)
    span = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": None,
        "name": "company.qualification",
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {"witdem.execution_id": "run-duckle", "custom.value": "raw-preserved"},
        "events": [],
        "resource": {"service.name": "worker-test"},
        "instrumentation_scope": {"name": "custom.instrumentation", "version": "1"},
    }
    commit = corpus.commit_batch("otel_traces", [span], execution_ids=["run-duckle"])
    corpus.commit_batch(
        "sdk_records",
        [
            {
                "version": "1.0",
                "kind": "event",
                "event_id": "contract-definition",
                "execution_id": "run-duckle",
                "name": "contract.definition",
                "attributes": {
                    "contract_hash": "contract-1",
                    "contract_name": "company_qualification",
                    "contract": {
                        "name": "company_qualification",
                        "description": "Decide whether the company qualifies.",
                    },
                    "result": {"name": "Company profile"},
                    "product_goal": {"name": "Correct company qualification"},
                },
            },
            {
                "version": "1.0",
                "kind": "outcome",
                "event_id": "application-outcome",
                "execution_id": "run-duckle",
                "name": "application_outcome",
                "value": "accepted",
                "attributes": {"status": "accepted"},
            },
            {
                "version": "1.0",
                "kind": "outcome",
                "event_id": "product-goal",
                "execution_id": "run-duckle",
                "name": "product_goal",
                "value": "accepted",
                "attributes": {
                    "status": "achieved",
                    "case_id": "clear-qualification",
                    "expected_status": "accepted",
                    "observed_status": "accepted",
                    "decision_correct": True,
                    "product_goal_achieved": True,
                    "artifact_valid": True,
                    "decision_evidence_sufficient": True,
                    "closest_blocker": "none",
                    "targeted_research_required": True,
                    "required_path_observed": True,
                },
            },
            {
                "version": "1.0",
                "kind": "evaluation",
                "event_id": "qualification-score",
                "execution_id": "run-duckle",
                "name": "Qualification score",
                "value": 0.91,
                "attributes": {
                    "score": 0.91,
                    "evaluation_key": "qualification_score",
                    "evaluation_description": "Evidence-backed fit score.",
                    "unit": "ratio",
                    "target": 0.8,
                    "direction": "higher_is_better",
                },
            },
        ],
        execution_ids=["run-duckle"],
    )

    result = run_pending()

    assert result["status"] == "ready"
    assert corpus.read_state(commit.ingest_id)["status"] == "ready"  # type: ignore[index]
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone() == (1,)
        fact = connection.execute(
            "SELECT display_name, repeated_pattern_count, extra_work_seconds, adapter_name, source_ingest_ids "
            "FROM serving.execution_facts WHERE execution_id = 'run-duckle'"
        ).fetchone()
        assert fact[:4] == ("Company qualification", 0, 0.0, "otel")
        assert commit.ingest_id in fact[4]
        operation = connection.execute(
            "SELECT is_framework_wrapper, is_repeated, attributes "
            "FROM serving.operation_facts WHERE execution_id = 'run-duckle'"
        ).fetchone()
        assert operation is not None
        assert operation[:2] == (False, False)
        assert "raw-preserved" in operation[2]
        assert connection.execute("SELECT status, engine FROM witdem_control.transform_runs").fetchone() == (
            "ready",
            "duckle",
        )
    finally:
        connection.close()
    repository = AnalyticsRepository(database)
    try:
        rows = repository.execution_rows(limit=None)
        assert len(rows) == 1
        assert rows[0]["display_name"] == "Company qualification"
        assert rows[0]["repeated_work"] == 0
        assert rows[0]["extra_work_cost"] is None
        assert repository.get_product_goal_summary().achieved_runs == 1
        assert repository.get_product_goal_summary().targeted_research_successes == 1
        assert repository.product_goal_rows()[0]["expected_status"] == "accepted"
        assert repository.execution_rows(limit=None)[0]["contract_hash"] == "contract-1"
        assert repository.goal_miss_summary() == []
        assert repository.goal_trend()[0]["success_rate"] == 1.0
        evaluation = repository.evaluation_summary()[0]
        assert evaluation["key"] == "qualification_score"
        assert evaluation["average_score"] == pytest.approx(0.91)
        assert evaluation["target"] == 0.8
        assert repository.contract_definitions() == [
            {
                "contract_hash": "contract-1",
                "contract_name": "company_qualification",
                "contract": {
                    "name": "company_qualification",
                    "description": "Decide whether the company qualifies.",
                },
                "result": {"name": "Company profile"},
                "product_goal": {"name": "Correct company qualification"},
                "run_count": 1,
            }
        ]
    finally:
        repository.close()

    first_connection = duckdb.connect(str(database), read_only=True)
    try:
        first_timestamps = first_connection.execute(
            "SELECT outcome_id, timestamp FROM outcomes ORDER BY outcome_id"
        ).fetchall()
    finally:
        first_connection.close()
    assert run_pending(rebuild=True)["status"] == "ready"
    second_connection = duckdb.connect(str(database), read_only=True)
    try:
        assert (
            second_connection.execute("SELECT outcome_id, timestamp FROM outcomes ORDER BY outcome_id").fetchall()
            == first_timestamps
        )
        assert second_connection.execute("SELECT COUNT(*) FROM serving.execution_facts").fetchone() == (1,)
    finally:
        second_connection.close()


@pytest.mark.integration
def test_sdk_ack_precedes_duckle_analytics_publication(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)

    response = TestClient(app).post(
        "/sdk/v1/records",
        json={
            "version": "1.0",
            "kind": "outcome",
            "event_id": "sdk-ack-1",
            "execution_id": "run-ack",
            "name": "execution.completed",
            "value": "success",
            "attributes": {"runtime_id": "langgraph"},
        },
    )

    assert response.status_code == 200
    ingest_id = response.json()["ingest_id"]
    assert response.json()["analytics_status"] == "pending"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone() == (0,)
    finally:
        connection.close()

    run_pending()

    status = TestClient(app).get(f"/ingestion/v1/batches/{ingest_id}").json()
    assert status["analytics"]["status"] == "ready"
    execution_status = TestClient(app).get("/ingestion/v1/executions/run-ack").json()
    assert execution_status["status"] == "ready"
    assert execution_status["serving_fact"]["runtime_id"] == "langgraph"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT status FROM executions WHERE execution_id = 'run-ack'").fetchone() == (
            "completed",
        )
        timestamp = connection.execute("SELECT timestamp FROM outcomes WHERE outcome_id = 'sdk-ack-1'").fetchone()
        assert timestamp is not None
        observed = datetime.fromisoformat(str(timestamp[0]).replace("Z", "+00:00"))
        committed = datetime.fromisoformat(corpus.read_commit(ingest_id).received_at)  # type: ignore[union-attr]
        assert observed == committed
    finally:
        connection.close()
