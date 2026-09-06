"""Coordinated Duckle worker from immutable corpus to serving tables."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

from witdem.config import storage_root
from witdem.elt.publisher import publish_staging_row
from witdem.ingest import corpus, live_db

DEFAULT_MAX_PENDING_BATCHES = 1_000


def _span_execution_id(span: Mapping[str, Any]) -> str | None:
    attributes = span.get("attributes")
    if isinstance(attributes, Mapping):
        value = attributes.get("witdem.execution_id")
        if value:
            return str(value)
    return str(span["trace_id"]) if span.get("trace_id") else None


def _dedupe_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    passthrough: list[dict[str, Any]] = []
    for span in spans:
        span_id = span.get("span_id")
        if not span_id:
            passthrough.append(span)
            continue
        key = str(span_id)
        if key not in by_id:
            order.append(key)
        by_id[key] = span
    return [by_id[key] for key in order] + passthrough


def _execution_bundles(execution_ids: set[str]) -> list[dict[str, Any]]:
    all_commits = corpus.list_commits()
    by_execution: dict[str, dict[str, Any]] = {
        execution_id: {"spans": [], "sdk_by_id": {}, "source_ingest_ids": []}
        for execution_id in execution_ids
    }
    for commit in all_commits:
        relevant = execution_ids.intersection(commit.execution_ids)
        if not relevant:
            continue
        for execution_id in relevant:
            by_execution[execution_id]["source_ingest_ids"].append(commit.ingest_id)
        for record in corpus.read_records(commit):
            record_execution_id = (
                _span_execution_id(record)
                if commit.signal == "otel_traces"
                else str(record.get("execution_id"))
            )
            if record_execution_id not in relevant:
                continue
            values = by_execution[record_execution_id]
            if commit.signal == "otel_traces":
                values["spans"].append(record)
            else:
                sdk_by_id = values["sdk_by_id"]
                key = str(record.get("event_id") or f"anonymous:{len(sdk_by_id)}")
                # Enrichment happens only in the rebuildable Duckle input.
                # The immutable corpus record remains byte-for-byte intact.
                sdk_by_id[key] = {**record, "_witdem_received_at": commit.received_at}
    bundles: list[dict[str, Any]] = []
    for execution_id in sorted(execution_ids):
        values = by_execution[execution_id]
        bundles.append(
            {
                "execution_id": execution_id,
                "source_ingest_ids_json": json.dumps(sorted(values["source_ingest_ids"])),
                "spans_json": json.dumps(
                    _dedupe_spans(values["spans"]), sort_keys=True, default=str
                ),
                "sdk_records_json": json.dumps(
                    list(values["sdk_by_id"].values()), sort_keys=True, default=str
                ),
            }
        )
    return bundles


def _pending_batch_limit(explicit: int | None = None) -> int:
    raw = explicit if explicit is not None else os.getenv(
        "WITDEM_ELT_MAX_PENDING_BATCHES", str(DEFAULT_MAX_PENDING_BATCHES)
    )
    try:
        limit = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("WITDEM_ELT_MAX_PENDING_BATCHES must be a positive integer") from exc
    if limit < 1:
        raise ValueError("WITDEM_ELT_MAX_PENDING_BATCHES must be a positive integer")
    return limit


def _bounded_pending(
    pending: list[corpus.CorpusCommit],
    limit: int,
) -> list[corpus.CorpusCommit]:
    """Soft-limit work without splitting already-visible execution batches."""

    if len(pending) <= limit:
        return pending

    parents: dict[str, str] = {}

    def find(value: str) -> str:
        parent = parents.setdefault(value, value)
        while parent != parents[parent]:
            parents[parent] = parents[parents[parent]]
            parent = parents[parent]
        while value != parent:
            previous = parents[value]
            parents[value] = parent
            value = previous
        return parent

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for commit in pending:
        if not commit.execution_ids:
            continue
        first = commit.execution_ids[0]
        find(first)
        for execution_id in commit.execution_ids[1:]:
            union(first, execution_id)

    groups: dict[tuple[str, str], list[corpus.CorpusCommit]] = {}
    for commit in pending:
        key = (
            ("execution", find(commit.execution_ids[0]))
            if commit.execution_ids
            else ("batch", commit.ingest_id)
        )
        groups.setdefault(key, []).append(commit)

    selected_ids: set[str] = set()
    for group in groups.values():
        if selected_ids and len(selected_ids) + len(group) > limit:
            break
        selected_ids.update(commit.ingest_id for commit in group)
        if len(selected_ids) >= limit:
            break
    return [commit for commit in pending if commit.ingest_id in selected_ids]


def pipeline_path() -> Path:
    return Path(__file__).with_name("workspace") / "pipelines" / "normalize.pipeline.json"


def duckle_executable() -> str | None:
    """Resolve Duckle in the active interpreter environment or on PATH."""

    adjacent = Path(sys.executable).parent / ("duckle.exe" if os.name == "nt" else "duckle")
    if adjacent.is_file():
        return str(adjacent)
    return shutil.which("duckle")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def run_pending(
    *,
    rebuild: bool = False,
    maintenance_lock_held: bool = False,
    max_batches: int | None = None,
) -> dict[str, Any]:
    """Run one bounded Duckle transformation over pending or all batches."""

    if not maintenance_lock_held:
        with corpus.maintenance_lock():
            return run_pending(
                rebuild=rebuild,
                maintenance_lock_held=True,
                max_batches=max_batches,
            )

    selected = corpus.list_commits() if rebuild else corpus.list_commits(statuses={"accepted", "failed"})
    if not rebuild:
        selected = [
            commit for commit in selected if int((corpus.read_state(commit.ingest_id) or {}).get("attempts") or 0) < 3
        ]
        selected = _bounded_pending(selected, _pending_batch_limit(max_batches))
    if not selected:
        return {"status": "idle", "batches": 0, "executions": 0}
    execution_ids = {execution_id for commit in selected for execution_id in commit.execution_ids}
    transform_run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc)
    run_dir = storage_root() / "elt" / "runs" / transform_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    pipeline_target = run_dir / "normalize.pipeline.json"
    shutil.copy2(pipeline_path(), pipeline_target)
    _write_jsonl(run_dir / "input.jsonl", _execution_bundles(execution_ids))
    for commit in selected:
        corpus.update_state(commit.ingest_id, "transforming", transform_run_id=transform_run_id)
    transform_record = {
        "transform_run_id": transform_run_id,
        "started_at": started_at,
        "ended_at": None,
        "status": "running",
        "engine": "duckle",
        "engine_version": version("duckle"),
        "input_batches": len(selected),
        "affected_executions": len(execution_ids),
        "error": None,
    }
    live_db.record_transform_run(transform_record)
    executable = duckle_executable()
    if executable is None:
        error = "Duckle executable is unavailable; install the pinned witdem-analytics dependencies"
        for commit in selected:
            corpus.update_state(commit.ingest_id, "failed", error=error, transform_run_id=transform_run_id)
        live_db.record_transform_run(
            {**transform_record, "ended_at": datetime.now(timezone.utc), "status": "failed", "error": error}
        )
        raise RuntimeError(error)
    environment = dict(os.environ)
    environment["DUCKLE_PYTHON_BIN"] = sys.executable
    result = subprocess.run(
        [
            executable,
            "--pipeline",
            str(pipeline_target),
            "--workspace",
            str(run_dir),
            "--log-dir",
            str(run_dir / "logs"),
            "--name",
            "witdem-normalize",
            "--manifest",
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or f"Duckle exited {result.returncode}").strip()
        for commit in selected:
            corpus.update_state(commit.ingest_id, "failed", error=error, transform_run_id=transform_run_id)
        live_db.record_transform_run(
            {**transform_record, "ended_at": datetime.now(timezone.utc), "status": "failed", "error": error}
        )
        raise RuntimeError(error)
    output_path = run_dir / "output.jsonl"
    published: list[str] = []
    try:
        with output_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    published.append(publish_staging_row(json.loads(line)))
    except Exception as exc:
        for commit in selected:
            corpus.update_state(commit.ingest_id, "failed", error=str(exc), transform_run_id=transform_run_id)
        live_db.record_transform_run(
            {**transform_record, "ended_at": datetime.now(timezone.utc), "status": "failed", "error": str(exc)}
        )
        raise
    from witdem.config import db_path
    from witdem.dashboard.service import materialize_workflow_projections

    try:
        materialize_workflow_projections(db_path(), sorted(set(published)))
    except Exception as exc:
        for commit in selected:
            corpus.update_state(commit.ingest_id, "failed", error=str(exc), transform_run_id=transform_run_id)
        live_db.record_transform_run(
            {**transform_record, "ended_at": datetime.now(timezone.utc), "status": "failed", "error": str(exc)}
        )
        raise
    for commit in selected:
        corpus.update_state(commit.ingest_id, "ready", transform_run_id=transform_run_id)
    live_db.record_transform_run({**transform_record, "ended_at": datetime.now(timezone.utc), "status": "ready"})
    return {
        "status": "ready",
        "transform_run_id": transform_run_id,
        "batches": len(selected),
        "executions": len(set(published)),
        "stdout": result.stdout,
    }
