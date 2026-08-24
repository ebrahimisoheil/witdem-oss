"""Raw SDK record persistence.

Plain JSONL files on disk, one file per ``execution_id``, under a
configurable root directory -- kept in an entirely separate directory from
raw OTel span storage (``ingest.raw_store``, ``data/live/raw_spans`` by
default). This directory separation is itself the provenance signal
used by the analytics layer: whether a record was OTel-observed or
SDK-supplied is recoverable just by which store a row came from, with no
extra "source" field needed. Canonical analytics are derived elsewhere
(``ingest.correlate``) by reading these files back in full, not by
inspecting anything here.

Unlike ``ingest.raw_store`` (which never de-duplicates -- a retried OTLP
export simply doubles the lines on disk, left for the canonical layer to
dedupe), this store deduplicates on ``event_id`` itself at append time so
retried SDK sends do not double-count. The SDK client's one hard reliability
requirement (a bounded local retry, see ``witdem_sdk._transport``) can
otherwise resend the exact same record.

Public operations:

    append_record(record) -> None
    read_execution_records(execution_id) -> list[dict[str, Any]]
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from witdem.config import storage_root
from witdem.ingest.corpus import CorpusCommit, commit_batch

logger = logging.getLogger(__name__)

_SUBDIR = "sdk_records"
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_KEY_LENGTH = 200

# One process-wide lock guards every append, covering both the dedupe check
# and the write so the two can never race across concurrent requests.
# Local-demo scale (docs/architecture.md) makes a single lock the simplest
# correct choice -- matches ingest.raw_store's own reasoning.
_write_lock = threading.Lock()


def _root_dir() -> Path:
    """Resolve the storage root fresh on every call.

    Reading the environment on every call (rather than caching at import
    time) lets tests repoint storage per-test via ``monkeypatch.setenv``
    without needing to reload this module -- matches ``ingest.raw_store``.

    ``_SUBDIR`` ("sdk_records") is appended unconditionally, whether
    ``WITDEM_DATA_DIR`` is set or not -- see ``ingest.raw_store._root_dir``'s
    docstring for the collision this fixes (an operator pointing this module
    and ``ingest.raw_store`` at one shared configured directory used to make
    raw span files and SDK record files collide on the same
    ``{execution_id}.jsonl`` name). Always appending the subdirectory makes
    that impossible regardless of configuration, while leaving the default
    resolved path unchanged.
    """

    return storage_root() / _SUBDIR


def _safe_key(key: str) -> str:
    """Sanitize an ``execution_id`` for use as a filename component.

    SDK records originate from any caller of the public SDK client -- this
    is an ingestion boundary just like ``ingest.raw_store``'s -- so an
    ``execution_id`` must never be interpreted as a path. Unsafe characters
    are replaced with ``_`` and the result is length-capped. This only
    changes the on-disk filename; the ``execution_id`` string inside
    persisted rows is untouched.
    """

    sanitized = _UNSAFE_KEY_CHARS.sub("_", key).lstrip(".") or "_"
    return sanitized[:_MAX_KEY_LENGTH]


def _path_for_execution(execution_id: str) -> Path:
    return _root_dir() / f"{_safe_key(execution_id)}.jsonl"


def _read_lines(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def append_record(record: Mapping[str, Any]) -> None:
    """Persist one validated SDK record, deduped by ``event_id``.

    Filed under its ``execution_id`` (docs/architecture.md requires this field on
    every record; ``ingest.sdk_ingest`` only ever calls this with an
    already-validated record). A retried SDK send carries the same
    ``event_id`` -- the idempotency key per docs/architecture.md -- so it is
    skipped rather than double-persisted; this achieves "upsert, not
    insert-only" without needing an update-in-place, since SDK records are
    immutable once written.

    Records with no ``event_id`` (defensively tolerated, though the wire
    contract requires one) are always appended, since there is nothing to
    dedupe against.

    Not safe across multiple OS processes sharing one data directory; fine
    for the single-process local v1 service this targets (same scope note
    as ``ingest.raw_store``).
    """

    execution_id = record.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise ValueError("record must contain a non-empty string 'execution_id'")
    event_id = record.get("event_id")

    path = _path_for_execution(execution_id)
    with _write_lock:
        if (
            event_id is not None
            and path.exists()
            and any(existing.get("event_id") == event_id for existing in _read_lines(path))
        ):
            logger.debug("append_record: skipping duplicate event_id %r for execution %r", event_id, execution_id)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, default=str) + "\n")


def commit_record(record: Mapping[str, Any], *, raw_payload: bytes | None = None) -> CorpusCommit:
    """Durably commit one SDK record to the authoritative corpus."""

    execution_id = str(record.get("execution_id") or "")
    return commit_batch(
        "sdk_records",
        [record],
        execution_ids=[execution_id] if execution_id else [],
        raw_payload=raw_payload,
        raw_extension="json",
    )


def read_execution_records(execution_id: str) -> list[dict[str, Any]]:
    """Read back every SDK record persisted so far under ``execution_id``.

    A missing file just means "no SDK records (yet)" and returns an empty
    list rather than raising -- the normal state for a telemetry-only
    execution, or before the first SDK call for an enriched one arrives.
    """

    path = _path_for_execution(execution_id)
    if not path.exists():
        return []
    return _read_lines(path)


def list_execution_ids() -> list[str]:
    root = _root_dir()
    if not root.exists():
        return []
    return sorted(path.stem for path in root.glob("*.jsonl") if path.is_file())
