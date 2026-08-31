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
import queue
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import Future
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
_writer_init_lock = threading.Lock()
_writer: _CommitWriter | None = None
_writer_pid: int | None = None


class CorpusBackpressureError(RuntimeError):
    """Raised when the bounded durable-ingest queue cannot accept more work."""


def maintenance_lock_path() -> Path:
    """Return the interprocess lock shared by ingest, ELT, and maintenance."""

    return storage_root() / ".maintenance.lock"


def ingest_lock_path() -> Path:
    """Return the interprocess lock used only for short corpus mutations."""

    return storage_root() / ".ingest.lock"


@contextmanager
def maintenance_lock(*, timeout: float = 30.0) -> Iterator[None]:
    """Prevent corpus mutation and ELT publication during maintenance."""

    path = maintenance_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path), timeout=timeout):
        yield


@contextmanager
def ingest_lock(*, timeout: float = 30.0) -> Iterator[None]:
    """Serialize receiver writes without coupling them to long ELT transforms."""

    path = ingest_lock_path()
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
    _atomic_replace(path, payload)
    _fsync_directory(path.parent)


def _atomic_replace(path: Path, payload: bytes) -> None:
    """Replace one file durably; callers decide when to sync its directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class _PendingCommit:
    commit: CorpusCommit
    root: Path
    records_payload: bytes
    raw_payload: bytes | None
    completion: Future[CorpusCommit]


class _CommitWriter:
    """One bounded writer that durably publishes concurrently submitted batches in groups."""

    def __init__(self) -> None:
        self.capacity = _positive_int_env("WITDEM_INGEST_QUEUE_SIZE", 2048)
        self.max_group_size = _positive_int_env("WITDEM_INGEST_GROUP_SIZE", 64)
        self.group_window = _nonnegative_float_env("WITDEM_INGEST_GROUP_WINDOW_MS", 2.0) / 1000.0
        self.enqueue_timeout = _nonnegative_float_env("WITDEM_INGEST_ENQUEUE_TIMEOUT", 5.0)
        self.queue: queue.Queue[_PendingCommit] = queue.Queue(maxsize=self.capacity)
        self.thread = threading.Thread(target=self._run, name="witdem-corpus-writer", daemon=True)
        self.thread.start()

    def submit(self, pending: _PendingCommit) -> CorpusCommit:
        try:
            self.queue.put(pending, timeout=self.enqueue_timeout)
        except queue.Full as exc:
            raise CorpusBackpressureError(
                f"durable ingestion queue remained full for {self.enqueue_timeout:g} seconds"
            ) from exc
        return pending.completion.result()

    def _run(self) -> None:
        while True:
            first = self.queue.get()
            group = [first]
            deadline = time.monotonic() + self.group_window
            while len(group) < self.max_group_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    group.append(self.queue.get(timeout=remaining))
                except queue.Empty:
                    break
            try:
                _persist_group(group)
            finally:
                for _pending in group:
                    self.queue.task_done()


def _commit_writer() -> _CommitWriter:
    """Return the process-local writer, recreating it safely after a fork."""

    global _writer, _writer_pid
    pid = os.getpid()
    if _writer is not None and _writer_pid == pid:
        return _writer
    with _writer_init_lock:
        if _writer is None or _writer_pid != pid:
            _writer = _CommitWriter()
            _writer_pid = pid
    return _writer


def _persist_group(group: Sequence[_PendingCommit]) -> None:
    """Persist a group while preserving records -> manifest -> state publication order."""

    unfinished = list(group)
    try:
        with ingest_lock(), _write_lock:
            data_ready: list[_PendingCommit] = []
            data_directories: set[Path] = set()
            for pending in unfinished:
                try:
                    records_path = pending.root / pending.commit.records_path
                    _atomic_replace(records_path, pending.records_payload)
                    data_directories.add(records_path.parent)
                    if pending.commit.raw_path is not None and pending.raw_payload is not None:
                        raw_path = pending.root / pending.commit.raw_path
                        _atomic_replace(raw_path, pending.raw_payload)
                        data_directories.add(raw_path.parent)
                    data_ready.append(pending)
                except Exception as exc:
                    pending.completion.set_exception(exc)
            for directory in data_directories:
                _fsync_directory(directory)

            manifest_ready: list[_PendingCommit] = []
            manifest_directories: set[Path] = set()
            for pending in data_ready:
                try:
                    manifest_path = pending.root / "committed" / f"{pending.commit.ingest_id}.json"
                    _atomic_replace(manifest_path, _json_bytes(asdict(pending.commit)))
                    manifest_directories.add(manifest_path.parent)
                    manifest_ready.append(pending)
                except Exception as exc:
                    pending.completion.set_exception(exc)
            for directory in manifest_directories:
                _fsync_directory(directory)

            state_ready: list[_PendingCommit] = []
            state_directories: set[Path] = set()
            for pending in manifest_ready:
                try:
                    state_path = pending.root / "state" / f"{pending.commit.ingest_id}.json"
                    _atomic_replace(
                        state_path,
                        _json_bytes(
                            {
                                "ingest_id": pending.commit.ingest_id,
                                "status": "accepted",
                                "updated_at": pending.commit.received_at,
                                "attempts": 0,
                                "error": None,
                            }
                        ),
                    )
                    state_directories.add(state_path.parent)
                    state_ready.append(pending)
                except Exception as exc:
                    pending.completion.set_exception(exc)
            for directory in state_directories:
                _fsync_directory(directory)
        for pending in state_ready:
            pending.completion.set_result(pending.commit)
    except Exception as exc:
        for pending in unfinished:
            if not pending.completion.done():
                pending.completion.set_exception(exc)


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
    pending = _PendingCommit(
        commit=commit,
        root=corpus_root(),
        records_payload=records_payload,
        raw_payload=raw_payload,
        completion=Future(),
    )
    return _commit_writer().submit(pending)


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
