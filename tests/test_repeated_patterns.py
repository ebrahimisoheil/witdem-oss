from __future__ import annotations

from datetime import datetime, timedelta, timezone

from witdem.analytics.core import Execution, Operation
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_repeated_patterns


def _operation(
    operation_id: str,
    *,
    attempt: int | None = None,
    offset: int = 0,
    name: str = "langchain.chain",
) -> Operation:
    started = datetime(2026, 8, 24, tzinfo=timezone.utc) + timedelta(seconds=offset)
    return Operation(
        operation_id=operation_id,
        execution_id="run-repeat",
        kind="component",
        name=name,
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        attempt=attempt,
    )


def test_equal_generic_framework_names_are_not_repeated_work() -> None:
    graph = NormalizedExecutionGraph(
        execution=Execution(execution_id="run-repeat"),
        operations=[_operation("first"), _operation("second", offset=1)],
    )

    assert derive_repeated_patterns(graph) == []


def test_explicit_later_attempt_is_repeated_work() -> None:
    graph = NormalizedExecutionGraph(
        execution=Execution(execution_id="run-repeat"),
        operations=[_operation("first", attempt=1), _operation("second", attempt=2, offset=1)],
    )

    patterns = derive_repeated_patterns(graph)

    assert len(patterns) == 1
    assert patterns[0]["iterations"] == 2
    assert patterns[0]["operation_ids"] == ["first", "second"]


def test_framework_tracing_setup_is_never_repeated_work() -> None:
    graph = NormalizedExecutionGraph(
        execution=Execution(execution_id="run-repeat"),
        operations=[
            _operation("first", attempt=1, name="haystack.tracing.auto_enable"),
            _operation("second", attempt=2, offset=1, name="haystack.tracing.auto_enable"),
        ],
    )

    assert derive_repeated_patterns(graph) == []
