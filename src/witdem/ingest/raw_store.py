"""Raw OTel span persistence.

Plain JSONL files on disk, one file per execution/trace grouping key, under a
configurable root directory. This is the "raw-ish, preserve everything" layer
described in ``telemetry/otel.py``'s ``JsonlSpanExporter`` docstring: spans are
stored as-is (whatever mapping ``ingest.otlp_http`` handed us), never reshaped
into a business schema, and unknown attributes are never discarded. Canonical
analytics are derived elsewhere (``ingest.correlate``) by reading these files
back in full, not by inspecting anything here.

Public operations:

    append_spans(spans) -> None
    read_execution_spans(execution_id) -> list[dict[str, Any]]
    list_execution_ids() -> list[str]
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from witdem.config import storage_root
from witdem.ingest.corpus import CorpusCommit, commit_batch

logger = logging.getLogger(__name__)

_SUBDIR = "raw_spans"
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_KEY_LENGTH = 200

# One process-wide lock guards every append. A single lock is sufficient; it
# only needs to keep
# individual JSON lines from interleaving across concurrent requests, not to
# maximize throughput.
_write_lock = threading.Lock()


def _root_dir() -> Path:
    """Resolve the storage root fresh on every call.

    Reading the environment on every call (rather than caching at import
    time) lets tests repoint storage per-test via ``monkeypatch.setenv``
    without needing to reload this module, and lets an operator repoint the
    directory without restarting -- reads always reflect the current value.

    ``_SUBDIR`` ("raw_spans") is appended unconditionally so raw spans and
    SDK records cannot collide when they share one ``WITDEM_DATA_DIR``.
    """

    return storage_root() / _SUBDIR


def _grouping_key(span: Mapping[str, Any]) -> str | None:
    """The key ``append_spans``/``read_execution_spans`` file/look up by.

    Prefer ``attributes["witdem.execution_id"]``, then use the trace id. This is computed per-span (not per
    batch) because one OTLP export can legitimately carry resource_spans for
    several executions/traces at once. Returns ``None`` when a span has
    neither -- callers should skip such spans rather than raise, since a
    single malformed span must not take down persistence for the rest of an
    otherwise well-formed batch.
    """

    attributes = span.get("attributes")
    if isinstance(attributes, Mapping):
        execution_id = attributes.get("witdem.execution_id")
        if isinstance(execution_id, str) and execution_id:
            return execution_id
    trace_id = span.get("trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    return None


def _safe_key(key: str) -> str:
    """Sanitize a grouping key for use as a filename component.

    Raw spans may originate from any OTLP client, including a hostile one --
    this is an ingestion boundary -- so a grouping key (an arbitrary
    attribute value) must never be interpreted as a path. Unsafe characters
    are replaced with ``_`` and the result is length-capped. This only
    changes the on-disk filename; the ``key`` string inside persisted rows is
    untouched. Sanitizing an already-sanitized key is a no-op, so results
    from ``list_execution_ids()`` always round-trip through
    ``read_execution_spans`` even though they may not equal the original
    unsanitized attribute value for adversarial/malformed input.
    """

    sanitized = _UNSAFE_KEY_CHARS.sub("_", key).lstrip(".") or "_"
    return sanitized[:_MAX_KEY_LENGTH]


def _path_for_key(key: str) -> Path:
    return _root_dir() / f"{_safe_key(key)}.jsonl"


def append_spans(spans: Sequence[Mapping[str, Any]]) -> None:
    """Append ``spans`` to their grouping-key JSONL file(s).

    Spans are grouped per-span and each group appended to its own file, so a
    single call can fan out across several files when the batch mixes
    executions/traces. Duplicate appends (retried OTLP exports) are expected
    and safe: this layer never de-duplicates -- that happens at the canonical
    layer (``ingest.correlate``), not here (docs/architecture.md) -- so calling
    this twice with the same spans simply doubles the lines on disk without
    raising or corrupting state.
    """

    if not spans:
        return

    by_key: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    skipped = 0
    for span in spans:
        key = _grouping_key(span)
        if key is None:
            skipped += 1
            continue
        by_key[key].append(span)
    if skipped:
        logger.warning("append_spans: skipped %d span(s) with no execution_id or trace_id to group by", skipped)
    if not by_key:
        return

    root = _root_dir()
    root.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        for key, group in by_key.items():
            path = _path_for_key(key)
            with path.open("a", encoding="utf-8") as handle:
                for span in group:
                    handle.write(json.dumps(span, default=str) + "\n")
    logger.debug("append_spans: wrote %d span(s) across %d execution/trace file(s)", len(spans), len(by_key))


def commit_spans(
    spans: Sequence[Mapping[str, Any]],
    *,
    raw_payload: bytes | None = None,
    content_encoding: str | None = None,
) -> CorpusCommit:
    """Durably commit an immutable OTLP batch to the authoritative corpus."""

    execution_ids = [key for span in spans if (key := _grouping_key(span)) is not None]
    return commit_batch(
        "otel_traces",
        spans,
        execution_ids=execution_ids,
        raw_payload=raw_payload,
        raw_extension="otlp.pb",
        metadata={"content_encoding": content_encoding or "identity"},
    )


def read_execution_spans(execution_id: str) -> list[dict[str, Any]]:
    """Read back every span persisted so far under ``execution_id``.

    ``execution_id`` is the same grouping key ``append_spans`` files spans
    under -- either a real Witdem execution-id value or a bare ``trace_id``
    fallback (docs/architecture.md). A missing file just means "no spans (yet)"
    and returns an empty list rather than raising, since this is the normal
    state before the first export for an execution arrives.
    """

    path = _path_for_key(execution_id)
    if not path.exists():
        return []
    spans: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            spans.append(json.loads(line))
    return spans


def list_execution_ids() -> list[str]:
    """List every grouping key with at least one persisted span.

    Used for restart reprocessing (docs/architecture.md): the
    service re-derives canonical analytics for every key returned here.
    """

    root = _root_dir()
    if not root.exists():
        return []
    return sorted(path.stem for path in root.glob("*.jsonl") if path.is_file())
