from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

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
    sdk_store.commit_record({"event_id": "event-1", "execution_id": "run-4", "kind": "event", "name": "reported"})

    assert not (tmp_path / "raw_spans").exists()
    assert not (tmp_path / "sdk_records").exists()
    assert len(corpus.list_commits()) == 2


def test_parallel_batches_are_all_durably_acknowledged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))

    def commit(index: int) -> corpus.CorpusCommit:
        return corpus.commit_batch(
            "sdk_records",
            [{"event_id": f"event-{index}", "execution_id": f"run-{index}"}],
            execution_ids=[f"run-{index}"],
        )

    with ThreadPoolExecutor(max_workers=32) as executor:
        commits = list(executor.map(commit, range(128)))

    assert len({item.ingest_id for item in commits}) == 128
    assert len(corpus.list_commits(statuses={"accepted"})) == 128
    for item in commits:
        assert corpus.read_state(item.ingest_id)["status"] == "accepted"  # type: ignore[index]
        assert (corpus.corpus_root() / item.records_path).exists()


def test_regular_ingest_is_independent_of_projection_maintenance(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with corpus.maintenance_lock():
            future = executor.submit(
                corpus.commit_batch,
                "sdk_records",
                [{"event_id": "during-elt", "execution_id": "run-during-elt"}],
                execution_ids=["run-during-elt"],
            )
            commit = future.result(timeout=2)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert corpus.read_state(commit.ingest_id)["status"] == "accepted"  # type: ignore[index]


def test_sdk_ingest_does_not_block_the_async_request_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def slow_commit(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        started.set()
        assert release.wait(timeout=2)
        return SimpleNamespace(ingest_id="slow-ingest")

    monkeypatch.setattr(sdk_store, "commit_record", slow_commit)
    payload = {
        "version": "1.0",
        "kind": "event",
        "event_id": "event-slow",
        "execution_id": "run-slow",
        "name": "slow",
        "attributes": {},
    }

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ingest = asyncio.create_task(client.post("/sdk/v1/records", json=payload))
            assert await asyncio.to_thread(started.wait, 1)
            before = time.monotonic()
            health = await client.get("/readiness")
            elapsed = time.monotonic() - before
            release.set()
            response = await ingest
        assert health.status_code == 200
        assert elapsed < 0.1
        assert response.status_code == 200

    asyncio.run(exercise())


def test_ingest_backpressure_is_retryable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))

    def reject(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise corpus.CorpusBackpressureError("queue full")

    monkeypatch.setattr(sdk_store, "commit_record", reject)
    response = TestClient(app).post(
        "/sdk/v1/records",
        json={
            "version": "1.0",
            "kind": "event",
            "event_id": "event-busy",
            "execution_id": "run-busy",
            "name": "busy",
            "attributes": {},
        },
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


def test_parallel_otlp_batches_are_all_durably_acknowledged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WITDEM_CAPTURE_CONTENT", raising=False)

    def payload(index: int) -> bytes:
        request = ExportTraceServiceRequest()
        scope_spans = request.resource_spans.add().scope_spans.add()
        span = scope_spans.spans.add()
        span.trace_id = (index + 1).to_bytes(16, "big")
        span.span_id = (index + 1).to_bytes(8, "big")
        span.name = f"parallel-{index}"
        span.start_time_unix_nano = 1_000_000_000
        span.end_time_unix_nano = 1_001_000_000
        return request.SerializeToString()

    async def exercise() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10) as client:
            return await asyncio.gather(
                *(
                    client.post(
                        "/v1/traces",
                        content=payload(index),
                        headers={"content-type": "application/x-protobuf"},
                    )
                    for index in range(64)
                )
            )

    responses = asyncio.run(exercise())

    assert all(response.status_code == 200 for response in responses)
    commits = corpus.list_commits(statuses={"accepted"})
    assert len(commits) == 64
    assert all(commit.signal == "otel_traces" for commit in commits)
