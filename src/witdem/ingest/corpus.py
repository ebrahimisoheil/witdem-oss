"""Durable, immutable ingest corpus batches.

The corpus is the acknowledgement boundary for both OTLP and SDK traffic. A
batch is accepted only after its decoded records and optional original wire
payload have been flushed, fsynced, and made visible by an atomically-written
commit manifest. Canonical and serving databases are rebuildable derivatives;
they are deliberately outside this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from witdem.config import storage_root
from witdem.protocol import CORPUS_SCHEMA_VERSION

Signal = Literal["otel_traces", "sdk_records"]
BatchStatus = Literal["accepted", "transforming", "ready", "failed", "superseded"]

_write_lock = threading.Lock()


def maintenance_lock_path() -> Path:
    """Return the interprocess lock shared by ingest, ELT, and maintenance."""

    return storage_root() / ".maintenance.lock"


@contextmanager
def maintenance_lock(*, timeout: float = 30.0) -> Iterator[None]:
    """Prevent corpus mutation and ELT publication during maintenance."""

    path = maintenance_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path), timeout=timeout):
        yield


@dataclass(frozen=True)
class CorpusCommit:
    ingest_id: str
    signal: Signal
    received_at: str
    records_path: str
    raw_path: str | None
    record_count: int
    execution_ids: tuple[str, ...]
    sha256: str
    raw_sha256: str | None
    metadata: dict[str, Any]
    schema_version: str = CORPUS_SCHEMA_VERSION


def corpus_root() -> Path:
    return storage_root() / "corpus"


def _relative(path: Path) -> str:
    return path.relative_to(corpus_root()).as_posix()


def _fsync_directory(path: Path) -> None:
    """Persist directory entries where the platform supports directory fsync."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()


def commit_batch(
    signal: Signal,
    records: Sequence[Mapping[str, Any]],
    *,
    execution_ids: Sequence[str] = (),
    raw_payload: bytes | None = None,
    raw_extension: str = "bin",
    metadata: Mapping[str, Any] | None = None,
) -> CorpusCommit:
    """Commit one immutable batch and return its durable acknowledgement."""

    received = datetime.now(timezone.utc)
    ingest_id = uuid.uuid4().hex
    partition = Path(signal) / received.strftime("%Y/%m/%d")
    records_path = corpus_root() / partition / f"{ingest_id}.jsonl"
    raw_path = corpus_root() / partition / f"{ingest_id}.{raw_extension.lstrip('.')}" if raw_payload else None
    records_payload = b"".join(_json_bytes(record) for record in records)
    commit = CorpusCommit(
        ingest_id=ingest_id,
        signal=signal,
        received_at=received.isoformat(),
        records_path=_relative(records_path),
        raw_path=_relative(raw_path) if raw_path else None,
        record_count=len(records),
        execution_ids=tuple(sorted({str(value) for value in execution_ids if value})),
        sha256=hashlib.sha256(records_payload).hexdigest(),
        raw_sha256=hashlib.sha256(raw_payload).hexdigest() if raw_payload else None,
        metadata=dict(metadata or {}),
    )
    manifest_path = corpus_root() / "committed" / f"{ingest_id}.json"
    state_path = corpus_root() / "state" / f"{ingest_id}.json"
    with maintenance_lock(), _write_lock:
        _atomic_write(records_path, records_payload)
        if raw_path is not None and raw_payload is not None:
            _atomic_write(raw_path, raw_payload)
        # The manifest is the commit marker and is therefore always written last.
        _atomic_write(manifest_path, _json_bytes(asdict(commit)))
        _atomic_write(
            state_path,
            _json_bytes(
                {
                    "ingest_id": ingest_id,
                    "status": "accepted",
                    "updated_at": received.isoformat(),
                    "attempts": 0,
                    "error": None,
                }
            ),
        )
    return commit


def read_commit(ingest_id: str) -> CorpusCommit | None:
    path = corpus_root() / "committed" / f"{ingest_id}.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    value["execution_ids"] = tuple(value.get("execution_ids") or ())
    return CorpusCommit(**value)


def read_state(ingest_id: str) -> dict[str, Any] | None:
    path = corpus_root() / "state" / f"{ingest_id}.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, Mapping) else None


def update_state(
    ingest_id: str,
    status: BatchStatus,
    *,
    error: str | None = None,
    transform_run_id: str | None = None,
) -> dict[str, Any]:
    if read_commit(ingest_id) is None:
        raise KeyError(f"unknown ingest batch: {ingest_id}")
    with _write_lock:
        current = read_state(ingest_id) or {"ingest_id": ingest_id, "attempts": 0}
        attempts = int(current.get("attempts") or 0) + (1 if status == "transforming" else 0)
        value = {
            **current,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "attempts": attempts,
            "error": error,
            "transform_run_id": transform_run_id,
        }
        _atomic_write(corpus_root() / "state" / f"{ingest_id}.json", _json_bytes(value))
    return value


def list_commits(*, statuses: set[str] | None = None) -> list[CorpusCommit]:
    directory = corpus_root() / "committed"
    if not directory.exists():
        return []
    commits: list[CorpusCommit] = []
    for path in sorted(directory.glob("*.json")):
        commit = read_commit(path.stem)
        if commit is None:
            continue
        state = read_state(commit.ingest_id) or {}
        if statuses is None or state.get("status") in statuses:
            commits.append(commit)
    return sorted(commits, key=lambda item: (item.received_at, item.ingest_id))


def read_records(commit: CorpusCommit) -> list[dict[str, Any]]:
    path = corpus_root() / commit.records_path
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records
