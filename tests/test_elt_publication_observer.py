from pathlib import Path

import pytest

from witdem.analytics.repository import AnalyticsRepository
from witdem.elt.worker import run_pending
from witdem.ingest import corpus, live_db


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["exception", "deferred-result"])
def test_observer_handoff_precedes_ready_and_is_retried(monkeypatch, tmp_path: Path, failure: str) -> None:
    database = tmp_path / "live.duckdb"
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)
    span = {
        "trace_id": "a" * 32, "span_id": "b" * 16, "parent_span_id": None,
        "name": "observer-test", "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000, "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"}, "attributes": {"witdem.execution_id": "observed"},
        "events": [], "resource": {"service.name": "observer-test"},
        "instrumentation_scope": {"name": "test", "version": "1"},
    }
    commit = corpus.commit_batch("otel_traces", [span], execution_ids=["observed"])
    seen = []

    def observe(ids: tuple[str, ...]) -> None:
        assert ids == ("observed",)
        assert corpus.read_state(commit.ingest_id)["status"] == "transforming"
        repository = AnalyticsRepository(database)
        try:
            bundle = repository.export_evidence_bundle(ids[0])
            seen.append(bundle)
            assert bundle.execution.execution_id == "observed"
        finally:
            repository.close()

    def fail(ids: tuple[str, ...]) -> object:
        observe(ids)
        if failure == "deferred-result":
            return object()
        raise RuntimeError("PRIVATE_ADAPTER_DIAGNOSTIC")

    with pytest.raises(RuntimeError, match="execution publication observer failed"):
        run_pending(on_publish=fail)
    state = corpus.read_state(commit.ingest_id)
    assert state["status"] == "failed"
    assert "PRIVATE_ADAPTER_DIAGNOSTIC" not in state["error"]
    assert run_pending(on_publish=observe)["status"] == "ready"
    assert corpus.read_state(commit.ingest_id)["status"] == "ready"
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert run_pending(on_publish=observe)["status"] == "idle"
    assert len(seen) == 2


@pytest.mark.integration
def test_observer_preserves_bounded_pending_processing(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "live.duckdb"
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)
    commits = []
    for index, execution_id in enumerate(("first", "second"), start=1):
        span = {
            "trace_id": str(index) * 32, "span_id": str(index) * 16, "parent_span_id": None,
            "name": "bounded-observer-test", "kind": "SpanKind.INTERNAL",
            "start_time_unix_nano": index * 1_000_000_000,
            "end_time_unix_nano": (index + 1) * 1_000_000_000,
            "status": {"status_code": "StatusCode.OK"},
            "attributes": {"witdem.execution_id": execution_id}, "events": [],
            "resource": {"service.name": "observer-test"},
            "instrumentation_scope": {"name": "test", "version": "1"},
        }
        commits.append(corpus.commit_batch("otel_traces", [span], execution_ids=[execution_id]))
    seen: list[tuple[str, ...]] = []
    assert run_pending(max_batches=1, on_publish=seen.append)["status"] == "ready"
    assert len(seen) == 1 and len(seen[0]) == 1
    statuses = [corpus.read_state(commit.ingest_id)["status"] for commit in commits]
    assert sorted(statuses) == ["accepted", "ready"]
    assert run_pending(max_batches=1, on_publish=seen.append)["status"] == "ready"
    assert sorted(seen) == [("first",), ("second",)]
    assert run_pending(max_batches=1, on_publish=seen.append)["status"] == "idle"
    assert len(seen) == 2
