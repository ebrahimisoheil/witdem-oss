from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from witdem.analytics import EvidenceBundle
from witdem.analytics.repository import AnalyticsRepository
from witdem.dashboard.app import create_dashboard_app
from witdem.ingest import live_db
from witdem.protocol import EVIDENCE_BUNDLE_SCHEMA_VERSION

FIXTURE = Path(__file__).with_name("fixtures") / "evidence-bundle-v1-oldest.json"


def _seed_execution(database: Path) -> None:
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "run-evidence",
                "runtime",
                "2026-01-01T10:00:00+00:00",
                "2026-01-01T10:00:03+00:00",
                "failed",
                "0.1.0",
                '{"workflow_id":"support"}',
            ],
        )
        connection.execute(
            "INSERT INTO serving.execution_facts (execution_id, runtime_id, runtime_status) VALUES (?, ?, ?)",
            ["run-evidence", "conflicting-serving-runtime", "completed"],
        )
        connection.executemany(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    "operation-b",
                    "run-evidence",
                    "trace-1",
                    "span-b",
                    "span-a",
                    "tool",
                    "deliver",
                    "error",
                    "2026-01-01T10:00:02+00:00",
                    "2026-01-01T10:00:03+00:00",
                    1,
                    '{"error":"unavailable"}',
                ],
                [
                    "operation-a",
                    "run-evidence",
                    "trace-1",
                    "span-a",
                    None,
                    "model",
                    "draft",
                    "ok",
                    "2026-01-01T10:00:00+00:00",
                    "2026-01-01T10:00:01+00:00",
                    1,
                    '{"provider":"example","model":"example-1"}',
                ],
            ],
        )
        connection.execute(
            "INSERT INTO links VALUES (?, ?, ?, ?, ?, ?)",
            ["link-1", "run-evidence", "operation-a", "operation-b", "workflow", "{}"],
        )
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "event-1",
                "run-evidence",
                "trace-1",
                "span-b",
                "2026-01-01T10:00:03+00:00",
                "step",
                "delivery_failed",
                '{"error":"unavailable"}',
                "0.1.0",
            ],
        )
        connection.execute(
            "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "evaluation-1",
                "run-evidence",
                "operation-a",
                "quality",
                "0.9",
                "accepted",
                0.9,
                "offline",
                0.95,
                "1",
                '{"target":0.8,"direction":"higher_is_better"}',
            ],
        )
        connection.execute(
            "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "outcome-1",
                "run-evidence",
                "delivered",
                "missed",
                "false",
                "2026-01-01T10:00:03+00:00",
                '{"source":"ticketing"}',
            ],
        )
        connection.execute(
            "INSERT INTO workflow_execution_projections VALUES (?, ?, ?, ?, ?, ?)",
            [
                "run-evidence",
                "support",
                "template-1",
                "2",
                '{"discrepancies":{"unexpected_operations":[{"id":"operation-b"}],"unexpected_transitions":[]}}',
                "2026-01-01T10:00:04+00:00",
            ],
        )
    finally:
        connection.close()


def test_oldest_supported_evidence_bundle_v1_fixture_is_accepted() -> None:
    bundle = EvidenceBundle.model_validate_json(FIXTURE.read_text(encoding="utf-8"))

    assert bundle.schema_version == EVIDENCE_BUNDLE_SCHEMA_VERSION
    assert bundle.execution.execution_id == "evidence-v1"
    assert bundle.diagnostics.evaluation_assessments[0].passed is True


def test_repository_exports_canonical_records_and_existing_diagnostics(tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    _seed_execution(database)
    repository = AnalyticsRepository(database)
    try:
        first = repository.export_evidence_bundle("run-evidence")
        second = repository.export_evidence_bundle("run-evidence")
    finally:
        repository.close()

    assert first.model_dump_json() == second.model_dump_json()
    assert first.execution.runtime_id == "runtime"
    assert first.execution.status == "failed"
    assert [operation.operation_id for operation in first.operations] == ["operation-a", "operation-b"]
    assert [evaluation.evaluation_id for evaluation in first.evaluations] == ["evaluation-1"]
    assert [outcome.outcome_id for outcome in first.outcomes] == ["outcome-1"]
    assert first.diagnostics.failure_explanation["operation_id"] == "operation-b"
    assert first.diagnostics.evaluation_assessments[0].passed is True
    assert first.diagnostics.workflow_discrepancies == {
        "unexpected_operations": [{"id": "operation-b"}],
        "unexpected_transitions": [],
    }


def test_repository_rejects_unknown_execution(tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    repository = AnalyticsRepository(database)
    try:
        with pytest.raises(KeyError, match="missing"):
            repository.export_evidence_bundle("missing")
    finally:
        repository.close()


def test_evidence_bundle_http_contract_and_unknown_execution(tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    _seed_execution(database)
    client = TestClient(create_dashboard_app(database))

    response = client.get("/api/v1/runs/run-evidence/evidence-bundle")
    missing = client.get("/api/v1/runs/missing/evidence-bundle")
    openapi = client.get("/api/openapi.json").json()

    assert response.status_code == 200
    assert EvidenceBundle.model_validate(response.json()).execution.execution_id == "run-evidence"
    assert missing.status_code == 404
    response_schema = openapi["paths"]["/api/v1/runs/{execution_id}/evidence-bundle"]["get"]["responses"]["200"]
    assert response_schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EvidenceBundle"
    }


def test_evidence_bundle_has_only_neutral_top_level_sections() -> None:
    schema = EvidenceBundle.model_json_schema()

    assert set(schema["properties"]) == {
        "schema_version",
        "execution",
        "operations",
        "links",
        "events",
        "evaluations",
        "outcomes",
        "diagnostics",
    }
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["schema_version"] == "1.0"
