from __future__ import annotations

from pathlib import Path

import duckdb

from witdem.ingest import live_db
from witdem.ingest.live_db import initialize_analytics_store


def test_initialize_analytics_store_creates_empty_canonical_tables(tmp_path: Path) -> None:
    path = initialize_analytics_store(tmp_path / "analytics.duckdb")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        tables = {str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()}
        counts = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    finally:
        connection.close()
    assert tables == {
        "executions",
        "operations",
        "links",
        "events",
        "evaluations",
        "outcomes",
        "workflow_templates",
        "execution_workflows",
        "workflow_execution_projections",
        "workflow_execution_nodes",
    }
    assert counts == {table: 0 for table in tables}


def test_initializer_does_not_read_the_synthetic_corpus(tmp_path: Path) -> None:
    path = initialize_analytics_store(tmp_path / "live" / "analytics.duckdb")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone() == (0,)
    finally:
        connection.close()


def test_semantic_only_records_create_and_complete_an_execution(tmp_path: Path, monkeypatch) -> None:
    path = initialize_analytics_store(tmp_path / "semantic.duckdb")
    monkeypatch.setattr(live_db, "_default_db_path", lambda: path)

    live_db.ensure_semantic_execution("semantic-1")
    live_db.ensure_semantic_execution(
        "semantic-1",
        runtime_id="langgraph",
        terminal_status="completed",
        attributes={"runtime_id": "langgraph", "case_id": "clear-qualification", "prompt": "must not persist"},
    )

    connection = duckdb.connect(str(path), read_only=True)
    try:
        row = connection.execute(
            "SELECT execution_id, runtime_id, status, attributes FROM executions WHERE execution_id = 'semantic-1'"
        ).fetchone()
    finally:
        connection.close()
    assert row[:3] == ("semantic-1", "langgraph", "completed")
    assert '"case_id":"clear-qualification"' in row[3]
    assert "prompt" not in row[3]
