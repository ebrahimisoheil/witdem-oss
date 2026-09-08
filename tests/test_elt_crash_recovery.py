import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from witdem.analytics.repository import AnalyticsRepository
from witdem.elt.worker import run_pending
from witdem.ingest import corpus, live_db


def test_interrupted_attempts_do_not_bypass_retry_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    commit = corpus.commit_batch("otel_traces", [], execution_ids=["exhausted"])
    for _ in range(3):
        corpus.update_state(commit.ingest_id, "transforming", transform_run_id="interrupted")
    assert run_pending()["status"] == "idle"
    state = corpus.read_state(commit.ingest_id)
    assert state["status"] == "failed"
    assert state["attempts"] == 3
    assert state["transform_run_id"] == "interrupted"
    assert "interrupted" in state["error"]


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="SIGKILL process recovery qualification")
def test_killed_worker_replays_publication_before_marking_ready(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)
    span = {
        "trace_id": "c" * 32, "span_id": "d" * 16, "parent_span_id": None,
        "name": "crash-recovery-test", "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000, "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {"witdem.execution_id": "crash-recovery"}, "events": [],
        "resource": {"service.name": "crash-recovery-test"},
        "instrumentation_scope": {"name": "test", "version": "1"},
    }
    commit = corpus.commit_batch("otel_traces", [span], execution_ids=["crash-recovery"])
    child = subprocess.run(
        [sys.executable, "-c", """
import os
import signal
from witdem.elt.worker import run_pending
def kill_after_publication(ids):
    assert ids == ("crash-recovery",)
    os.kill(os.getpid(), signal.SIGKILL)
run_pending(on_publish=kill_after_publication)
"""],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    assert corpus.read_state(commit.ingest_id)["status"] == "transforming"
    repository = AnalyticsRepository(database)
    try:
        original = repository.export_evidence_bundle("crash-recovery")
    finally:
        repository.close()
    seen: list[tuple[str, ...]] = []
    assert run_pending(on_publish=seen.append)["status"] == "ready"
    assert seen == [("crash-recovery",)]
    state = corpus.read_state(commit.ingest_id)
    assert state["status"] == "ready"
    assert state["attempts"] == 2
    repository = AnalyticsRepository(database)
    try:
        assert repository.export_evidence_bundle("crash-recovery") == original
    finally:
        repository.close()
    assert run_pending(on_publish=seen.append)["status"] == "idle"
    assert len(seen) == 1
