"""Recomputable interpretations over persisted facts.

These functions are intentionally outside workflow state and semantic event
emission.  They can be rerun when reporting rules change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.core import Event, Operation
from witdem.analytics.identity import (
    canonical_loop_signature,
    canonical_operation_key,
    display_operation,
)


def derived_termination_category(record: Mapping[str, Any]) -> str:
    """Classify a persisted corpus row without changing workflow semantics."""

    if record.get("accepted_result"):
        return "accepted"
    if record.get("execution_completed") and record.get("result_valid"):
        return "valid_but_not_accepted"

    error = str(record.get("terminal_error") or "").lower()
    source_failures = int(record.get("source_failures") or 0)
    if source_failures > 0 or "could not fetch" in error or ("source" in error and "failed" in error):
        return "operational_failure"
    if "profile validation failed" in error:
        return "validation_failure"
    if "structured profile extraction failed" in error or ("extract" in error and "json" in error):
        return "extraction_failure"
    if _is_bounded_uncertainty_record(record, error):
        return "bounded_uncertainty"
    if not record.get("execution_completed"):
        return "other_failure"
    return "other"


def _is_bounded_uncertainty_record(record: Mapping[str, Any], error: str) -> bool:
    if "research incomplete after" not in error:
        return False
    if int(record.get("source_failures") or 0) > 0:
        return False
    if not record.get("targeted_routes") and not record.get("dimension_statuses"):
        return False
    return bool(_terminal_unresolved_dimensions(record))


def _terminal_unresolved_dimensions(record: Mapping[str, Any]) -> list[str]:
    unresolved: list[str] = []
    for item in record.get("dimension_statuses") or []:
        if isinstance(item, Mapping) and item.get("status") in {"missing", "weak", "conflicting"}:
            unresolved.append(str(item.get("dimension", "unknown")))
    return unresolved


def derive_execution_path(events: Sequence[Event]) -> list[str]:
    """Return the ordered names of semantic events for timeline reporting."""

    return [event.name for event in sorted(events, key=lambda item: item.timestamp)]


def derive_loop_patterns(operations: Sequence[Operation]) -> list[dict[str, Any]]:
    """Find repeated canonical operations without asserting why they repeated."""

    counts: dict[str, dict[str, Any]] = {}
    for operation in operations:
        key = canonical_operation_key(operation)
        item = counts.setdefault(key, {"name": display_operation(operation), "key": key, "count": 0})
        item["count"] += 1
    return [
        {**item, "loop_signature": canonical_loop_signature([key])}
        for key, item in sorted(counts.items())
        if item["count"] > 1
    ]


def reconstruct_replay(events: Sequence[Event], operations: Sequence[Operation]) -> dict[str, Any]:
    """Build a small replay-ready representation from stored core records."""

    execution_ids = {event.execution_id for event in events} | {operation.execution_id for operation in operations}
    if len(execution_ids) > 1:
        raise ValueError("replay records must belong to one execution")
    edges = [
        {
            "from": child.parent_span_id,
            "to": child.span_id,
            "relation": "parent",
        }
        for child in operations
        if child.parent_span_id and child.span_id
    ]
    return {
        "execution_id": next(iter(execution_ids), None),
        "operations": [operation.model_dump(mode="json") for operation in operations],
        "events": [event.model_dump(mode="json") for event in sorted(events, key=lambda item: item.timestamp)],
        "edges": edges,
        "path": derive_execution_path(events),
        "loops": derive_loop_patterns(operations),
    }
