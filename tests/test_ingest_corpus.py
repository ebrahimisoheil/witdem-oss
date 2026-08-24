from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from witdem.api import app
from witdem.ingest import corpus, raw_store, sdk_store


def test_commit_batch_publishes_immutable_data_before_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    record = {"execution_id": "run-1", "span_id": "span-1", "attributes": {"provider": "openai"}}

    commit = corpus.commit_batch(
        "otel_traces",
        [record],
        execution_ids=["run-1"],
        raw_payload=b"protobuf-wire-body",
        raw_extension="otlp.pb",
    )

    records_path = corpus.corpus_root() / commit.records_path
    raw_path = corpus.corpus_root() / str(commit.raw_path)
    manifest_path = corpus.corpus_root() / "committed" / f"{commit.ingest_id}.json"
    assert json.loads(records_path.read_text())["span_id"] == "span-1"
    assert raw_path.read_bytes() == b"protobuf-wire-body"
    assert manifest_path.exists()
    assert commit.sha256 == hashlib.sha256(records_path.read_bytes()).hexdigest()
    assert commit.raw_sha256 == hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert corpus.read_state(commit.ingest_id)["status"] == "accepted"  # type: ignore[index]


def test_batch_state_is_recoverable_and_filterable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    commit = corpus.commit_batch("sdk_records", [{"execution_id": "run-2"}], execution_ids=["run-2"])

    corpus.update_state(commit.ingest_id, "transforming", transform_run_id="transform-1")
    state = corpus.update_state(commit.ingest_id, "ready", transform_run_id="transform-1")

    assert state["attempts"] == 1
    assert [item.ingest_id for item in corpus.list_commits(statuses={"ready"})] == [commit.ingest_id]
    assert corpus.list_commits(statuses={"accepted"}) == []


def test_ingestion_status_endpoint_reports_commit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    commit = corpus.commit_batch("sdk_records", [{"execution_id": "run-3"}], execution_ids=["run-3"])

    response = TestClient(app).get(f"/ingestion/v1/batches/{commit.ingest_id}")

    assert response.status_code == 200
    assert response.json()["analytics"]["status"] == "accepted"
    assert response.json()["execution_ids"] == ["run-3"]
    execution = TestClient(app).get("/ingestion/v1/executions/run-3")
    assert execution.status_code == 200
    assert execution.json()["status"] == "pending"


def test_duckle_commits_do_not_create_parallel_jsonl_stores(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    raw_store.commit_spans(
        [{"trace_id": "trace-1", "span_id": "span-1", "attributes": {"witdem.execution_id": "run-4"}}]
    )
    sdk_store.commit_record(
        {"event_id": "event-1", "execution_id": "run-4", "kind": "event", "name": "reported"}
    )

    assert not (tmp_path / "raw_spans").exists()
    assert not (tmp_path / "sdk_records").exists()
    assert len(corpus.list_commits()) == 2
