from datetime import datetime, timezone

from witdem.analytics.core import Event, Execution, Operation
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_replay_graph


def test_contract_lifecycle_event_does_not_rename_root_operation() -> None:
    operation = Operation(
        operation_id="root",
        execution_id="run-1",
        trace_id="trace-1",
        span_id="span-1",
        kind="workflow",
        name="langgraph",
    )
    event = Event(
        event_id="event-1",
        execution_id="run-1",
        trace_id="trace-1",
        span_id="span-1",
        timestamp=datetime.now(timezone.utc),
        type="event",
        name="contract.completed",
    )
    replay = derive_replay_graph(
        NormalizedExecutionGraph(
            execution=Execution(execution_id="run-1"),
            operations=[operation],
        ),
        events=[event],
    )

    assert replay.nodes[0].display_name == "Langgraph"
    assert replay.nodes[0].semantic_stage is None


def test_business_event_can_still_name_a_semantic_stage() -> None:
    operation = Operation(
        operation_id="stage",
        execution_id="run-1",
        trace_id="trace-1",
        span_id="span-2",
        kind="operation",
        name="step",
    )
    event = Event(
        event_id="event-2",
        execution_id="run-1",
        trace_id="trace-1",
        span_id="span-2",
        timestamp=datetime.now(timezone.utc),
        type="event",
        name="profile.validation",
    )
    replay = derive_replay_graph(
        NormalizedExecutionGraph(
            execution=Execution(execution_id="run-1"),
            operations=[operation],
        ),
        events=[event],
    )

    assert replay.nodes[0].display_name == "Profile validation"
    assert replay.nodes[0].semantic_stage == "profile.validation"
