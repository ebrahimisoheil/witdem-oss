from __future__ import annotations

from witdem.ingest.correlate import _execution_key
from witdem.ingest.raw_store import _grouping_key


def test_canonical_execution_id_wins_over_trace_id() -> None:
    span = {
        "trace_id": "trace-id",
        "attributes": {"witdem.execution_id": "canonical-id"},
    }

    assert _execution_key(span) == "canonical-id"
    assert _grouping_key(span) == "canonical-id"


def test_trace_id_is_used_when_execution_id_is_absent() -> None:
    span = {"trace_id": "trace-id", "attributes": {}}

    assert _execution_key(span) == "trace-id"
    assert _grouping_key(span) == "trace-id"
