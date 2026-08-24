"""Canonical correlation and incremental analytics.

The ingestion contract is documented in ``docs/architecture.md``:
``ingest.otlp_http``
calls ``on_spans_received`` after persisting raw spans; ``ingest.sdk_ingest``
calls ``on_sdk_record_received`` after persisting a raw SDK record. Neither
caller depends on the correlation implementation.

``raw_store`` and ``sdk_store`` are imported inside the functions that use
them so the correlation logic remains independently testable.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from witdem.adapters.registry import detect_adapter
from witdem.analytics.core import Evaluation, Event, Operation, Outcome
from witdem.ingest import live_db

logger = logging.getLogger(__name__)

#: SDK record ``kind`` values that map onto the generic ``Event`` model;
#: ``type`` on the resulting ``Event`` mirrors ``kind``
#: itself in all three cases ("event"/"decision"/"metric").
_EVENT_KINDS = frozenset({"event", "decision", "metric"})


def _execution_key(span: Mapping[str, Any]) -> str | None:
    """Return the execution grouping key for one raw span.

    Prefer the canonical ``witdem.execution_id`` span attribute, then use the trace id.
    """

    attributes = span.get("attributes")
    if isinstance(attributes, Mapping):
        value = attributes.get("witdem.execution_id")
        if value:
            return str(value)
    trace_id = span.get("trace_id")
    return str(trace_id) if trace_id else None


def _group_spans_by_execution(spans: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for span in spans:
        key = _execution_key(span)
        if key is None:
            logger.debug(
                "on_spans_received: span %s has neither witdem.execution_id nor trace_id; skipped",
                span.get("span_id"),
            )
            continue
        groups[key].append(span)
    return groups


def _configured_runtime(spans: Sequence[Mapping[str, Any]]) -> str | None:
    """Read the application runtime identity from OTel resource metadata."""

    for span in spans:
        attributes = span.get("attributes")
        if isinstance(attributes, Mapping):
            value = attributes.get("witdem.runtime") or attributes.get("witdem.runtime.name")
            if value:
                return str(value)
    for span in spans:
        resource = span.get("resource")
        if isinstance(resource, Mapping):
            value = resource.get("witdem.runtime") or resource.get("witdem.example")
            if value:
                return str(value)
    return None


def _derive_execution_status(operations: Sequence[Operation]) -> str:
    """Derive ``"running" | "completed" | "failed"`` from the root operation.

    The root is the operation with no
    ``parent_span_id`` in the *currently known* set of operations -- which
    may not include the true root yet if spans are still arriving out of
    order. An execution is never inferred ``"failed"`` merely because it
    looks incomplete:

      * no root observed yet, or a root observed but not yet ended -> ``"running"``
      * a root that has ended with an error status -> ``"failed"``
      * a root that has ended with any other (ok/unset/unknown) status -> ``"completed"``

    This intentionally overrides whatever status ``normalize_haystack_spans``
    itself assigned to ``Execution.status`` -- that function's root concept
    (first operation whose ``kind == "workflow"``) and status vocabulary
    (raw OTel ``"ok"``/``"error"``/``"unset"``, aggregated across *all*
    operations when no such root is found) are aimed at fully-offline,
    already-complete traces, not this running/completed/failed lifecycle for
    a live, possibly-partial execution.
    """

    roots = [operation for operation in operations if operation.parent_span_id is None]
    if not roots:
        return "running"
    if any(root.ended_at is None for root in roots):
        return "running"
    if any(root.status == "error" for root in roots):
        return "failed"
    return "completed"


def _dedupe_spans_by_span_id(spans: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Collapse repeated deliveries of the same span down to one entry per
    ``span_id``, keeping the *last* delivery and otherwise preserving order.

    ``ingest.raw_store`` deliberately never deduplicates on write or read --
    see its own module docstring and
    ``tests/unit/test_raw_store.py::test_duplicate_append_is_safe_and_not_deduped``,
    which asserts a retried OTLP export leaves the same span twice in
    ``read_execution_spans``'s result, with the comment "dedup happens at
    the canonical layer, not here". This is that
    canonical-layer step. Without it, re-normalizing an execution whose raw
    spans contain a retried delivery would silently double- (or N-times-)
    count every affected operation in the live DuckDB on each
    re-normalization, since ``normalize_haystack_spans`` builds one
    ``Operation`` per input row and ``upsert_operations_and_links``'s
    delete-then-insert has no per-row uniqueness constraint to catch it.
    Re-deriving from the same raw spans is idempotent because
    ``operation_id`` is the stable ``span_id`` -- that
    only holds if duplicate deliveries of one span are collapsed first,
    which is what this function does). Spans with no ``span_id`` at all are
    passed through unchanged, since there is nothing to dedupe them against.
    """

    deduped: dict[str, Mapping[str, Any]] = {}
    order: list[str] = []
    passthrough: list[Mapping[str, Any]] = []
    for span in spans:
        span_id = span.get("span_id")
        if not span_id:
            passthrough.append(span)
            continue
        key = str(span_id)
        if key not in deduped:
            order.append(key)
        deduped[key] = span  # last delivery wins; retried spans are expected to be identical anyway
    return [deduped[key] for key in order] + passthrough


def _process_execution(execution_id: str, spans: Sequence[Mapping[str, Any]]) -> None:
    """Normalize ``spans`` for one execution and upsert into the live DuckDB.

    Shared by ``on_spans_received`` (one execution's worth of a batch) and
    ``reprocess_execution`` (the full persisted span set on restart) -- both
    need the identical dedupe + normalize + status-override + upsert
    sequence.
    """

    spans = _dedupe_spans_by_span_id(spans)
    adapter = detect_adapter(spans)
    graph = adapter.normalize(
        spans,
        execution_id=execution_id,
        runtime_id=_configured_runtime(spans),
    )
    status = _derive_execution_status(graph.operations)
    execution = graph.execution.model_copy(update={"status": status})
    live_db.upsert_graph(execution, graph.operations, graph.links)
    logger.debug(
        "on_spans_received: execution=%s status=%s operations=%d links=%d raw_span_count=%d",
        execution_id,
        status,
        len(graph.operations),
        len(graph.links),
        graph.raw_span_count,
    )


def on_spans_received(spans: Sequence[Mapping[str, Any]]) -> None:
    """Incrementally (re)derive canonical analytics for the execution(s) in ``spans``.

    ``spans`` are raw span envelopes already persisted by the caller (same
    shape as ``telemetry.otel.JsonlSpanExporter``). Spans are grouped by
    execution key (``_execution_key``), and for each affected execution the
    FULL span set persisted so far is re-read via
    ``raw_store.read_execution_spans`` (not just this batch -- the adapter
    needs the whole trace-so-far to derive a correct graph, per
    the persisted corpus), normalized via a ``RuntimeAdapter`` picked by
    ``adapters.registry.detect_adapter``, and upserted into the live DuckDB
    tables via ``ingest.live_db``.

    A batch containing only child spans (no root yet) is valid: the
    execution's status simply stays ``"running"`` until a root span is seen
    and has ended (see ``_derive_execution_status``). Re-normalizing
    repeatedly as more spans arrive converges because ``operation_id`` is
    always the stable span id.
    """

    groups = _group_spans_by_execution(spans)
    for execution_id, batch in groups.items():
        from witdem.ingest import raw_store

        full_spans: Sequence[Mapping[str, Any]] = raw_store.read_execution_spans(execution_id)
        if not full_spans:
            # Defensive fallback only -- in production raw persistence always
            # happens before this function is called (docs/architecture.md
            # §7a), so raw_store should already contain at least this batch.
            # This keeps on_spans_received usable in isolation (e.g. tests,
            # or if raw_store's own persistence is momentarily behind) rather
            # than silently dropping a batch we were, in fact, handed.
            logger.debug(
                "on_spans_received: raw_store returned no spans for %s; using the %d span(s) from this batch",
                execution_id,
                len(batch),
            )
            full_spans = batch
        _process_execution(execution_id, full_spans)


def build_semantic_record(record: Mapping[str, Any]) -> Event | Evaluation | Outcome:
    """Map one SDK wire record onto its canonical model.

    ``kind: "decision"`` and ``kind: "metric"`` both become the generic
    ``Event`` model (``type="decision"`` / ``type="metric"``); plain
    ``kind: "event"`` becomes ``Event(type="event", ...)`` for the same
    reason. The SDK-supplied ``event_id`` is used directly as the resulting
    model's own id field (``event_id``/``evaluation_id``/``outcome_id``) so
    retries/replays are idempotent -- never the
    model's random-uuid default.

    The wire envelope is deliberately flat (version/kind/event_id/
    execution_id/trace_id/span_id/name/value/attributes); any
    richer per-kind fields the canonical models carry (``Evaluation.source``/
    ``score``/``confidence``/``label``/``definition_version``/``subject_id``,
    ``Outcome.status``) are not part of that envelope, so they are read out
    of ``record["attributes"]`` when the caller supplied them there, with a
    conservative default otherwise (``source`` defaults to ``"sdk"`` since
    the model requires a non-empty string; everything else defaults to
    ``None``, its normal "unknown" value).
    """

    kind = str(record.get("kind") or "").strip().casefold()
    event_id = str(record["event_id"])
    execution_id = str(record["execution_id"])
    trace_id = record.get("trace_id")
    span_id = record.get("span_id")
    name = str(record.get("name") or kind)
    received_at = record.get("_witdem_received_at")

    attributes = dict(record.get("attributes") or {})
    # Preserve provenance so SDK facts remain distinguishable from OTel facts.
    attributes["witdem.source"] = "sdk"

    if kind in _EVENT_KINDS:
        payload = dict(attributes)
        payload["value"] = record.get("value")
        return Event(
            event_id=event_id,
            execution_id=execution_id,
            trace_id=str(trace_id) if trace_id else None,
            span_id=str(span_id) if span_id else None,
            type=kind,
            name=name,
            payload=payload,
            **({"timestamp": received_at} if received_at else {}),
        )
    if kind == "evaluation":
        return Evaluation(
            evaluation_id=event_id,
            execution_id=execution_id,
            subject_id=attributes.get("subject_id") or (str(span_id) if span_id else None),
            name=name,
            value=record.get("value"),
            label=attributes.get("label"),
            score=attributes.get("score"),
            source=str(attributes.get("source") or "sdk"),
            confidence=attributes.get("confidence"),
            definition_version=attributes.get("definition_version"),
            attributes=attributes,
        )
    if kind == "outcome":
        return Outcome(
            outcome_id=event_id,
            execution_id=execution_id,
            name=name,
            status=attributes.get("status"),
            value=record.get("value"),
            attributes=attributes,
            **({"timestamp": received_at} if received_at else {}),
        )
    raise ValueError(f"unsupported SDK record kind: {record.get('kind')!r}")


def on_sdk_record_received(record: Mapping[str, Any]) -> None:
    """Correlate one validated SDK record (event/decision/evaluation/outcome/metric)
    into the same execution as its OTel spans and upsert into the live DuckDB tables.

    Defensively skips (logging a warning, never raising) anything that is not
    shaped like an SDK wire record at all -- i.e. missing ``event_id``, the
    one field every real record has (docs/architecture.md) and no raw OTel
    span dict ever does. In the normal request path this never triggers:
    ``ingest.sdk_ingest``'s router only ever calls this after strict
    pydantic validation already guarantees the shape. It exists for
    ``reprocess_execution``'s replay path, which reads whatever
    ``ingest.sdk_store.read_execution_records`` returns back off disk.

    This guard was originally added to survive a real cross-module bug found
    during integration testing: ``ingest.raw_store`` and ``ingest.sdk_store``
    used to both resolve the identical ``WITDEM_DATA_DIR`` env var to the
    *same* directory whenever it was explicitly configured (each only applied
    its own distinguishing subdirectory when the var was left unset), so
    pointing both at one shared directory made raw span files and SDK record
    files collide on the same ``{execution_id}.jsonl`` name, and
    ``read_execution_records`` would return raw span dicts here instead --
    crashing ``reprocess_execution`` outright (``KeyError: 'event_id'``).
    That root cause is now fixed (both modules' ``_root_dir()`` append their
    fixed subdirectory unconditionally, so the collision is no longer
    possible regardless of configuration -- see
    ``tests/integration/test_live_data_dir_collision_hazard.py``). This guard
    stays as cheap defense-in-depth against any other future source of a
    malformed replayed record, not because the collision it was originally
    written for can still happen.
    """

    if "event_id" not in record:
        logger.warning(
            "on_sdk_record_received: skipping a record with no 'event_id' key "
            "(got keys=%s) -- this is not a valid SDK wire record, most likely "
            "raw OTel span data read back from a storage-directory collision "
            "between ingest.raw_store and ingest.sdk_store (see this function's "
            "docstring). execution_id=%r",
            sorted(record.keys()),
            record.get("execution_id"),
        )
        return

    semantic_record = build_semantic_record(record)
    attributes = dict(record.get("attributes") or {})
    terminal_status: str | None = None
    if record.get("kind") == "outcome" and record.get("name") == "execution.completed":
        terminal_status = "failed" if attributes.get("status") == "error" else "completed"
    live_db.ensure_semantic_execution(
        str(record["execution_id"]),
        runtime_id=str(attributes.get("runtime_id")) if attributes.get("runtime_id") else None,
        terminal_status=terminal_status,
        attributes=attributes,
    )
    live_db.upsert_semantic(semantic_record)
    logger.debug(
        "on_sdk_record_received: kind=%s id=%s execution=%s",
        record.get("kind"),
        record.get("event_id"),
        record.get("execution_id"),
    )


def reprocess_execution(execution_id: str) -> None:
    """Re-read persisted raw spans + SDK records for ``execution_id`` and re-derive
    canonical analytics. Used on service restart and idempotent with
    ``on_spans_received``/``on_sdk_record_received`` (same deterministic ids
    throughout: span-derived operation ids, and SDK-supplied event/evaluation/
    outcome ids), so this never duplicates rows.
    """

    from witdem.ingest import raw_store, sdk_store

    spans = raw_store.read_execution_spans(execution_id)
    if spans:
        _process_execution(execution_id, spans)
    else:
        logger.debug("reprocess_execution: no raw spans persisted for %s", execution_id)

    records = sdk_store.read_execution_records(execution_id)
    for record in records:
        on_sdk_record_received(record)
