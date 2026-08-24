"""Telemetry-only mode: every scenario runs deterministically end to end,
and each one's physical shape (spans emitted) matches what its own
scenarios/*.py docstring promises. See docs/architecture.md for the
"physical shape" concept these tests exercise directly at the span level.

Haystack wraps every component's ``run()`` call in a real OTel span named
"haystack.component.run" tagged with "haystack.component.name" == the name
given to ``pipeline.add_component(...)`` (verified directly against
haystack/core/pipeline/base.py's ``_create_component_span`` in the installed
package, not guessed) -- so component-level span counts below are exact,
not approximate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry.trace import StatusCode

from agent_demo.api import app

client = TestClient(app)

# scenario -> expected WorkflowResult shape (see workflow.py's run_workflow)
SCENARIO_EXPECTATIONS: dict[str, dict[str, object]] = {
    "simple_success": {
        "status": "success",
        "route": "direct_answer",
        "tool_call_count": 0,
        "correction_count": 0,
        "turn_count": 1,
    },
    "tool_calling": {
        "status": "success",
        "route": "tool_assisted",
        "tool_call_count": 1,
        "correction_count": 0,
        "turn_count": 2,
    },
    "correction_loop": {
        "status": "success",
        "route": "direct_answer",
        "tool_call_count": 0,
        "correction_count": 2,
        "turn_count": 3,
    },
    "failure_recovery": {
        "status": "success",
        "route": "fallback_recovery",
        "tool_call_count": 2,
        "correction_count": 0,
        "turn_count": 3,
    },
    "terminal_failure": {
        "status": "failed",
        "route": "terminal_failure",
        "tool_call_count": 2,
        "correction_count": 0,
        "turn_count": 2,
    },
    "nested": {
        "status": "success",
        "route": "tool_assisted",
        "tool_call_count": 1,
        "correction_count": 0,
        "turn_count": 2,
    },
}


def _component_spans(spans, component_name: str):
    return [s for s in spans if s.attributes.get("haystack.component.name") == component_name]


@pytest.mark.parametrize("scenario", sorted(SCENARIO_EXPECTATIONS))
def test_scenario_result_matches_expected_shape(scenario: str) -> None:
    expected = SCENARIO_EXPECTATIONS[scenario]
    response = client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
    assert response.status_code == 200
    body = response.json()

    assert body["scenario"] == scenario
    assert body["mode"] == "telemetry_only"
    assert body["sdk_enriched"] is False
    assert body["status"] == expected["status"]
    assert body["route"] == expected["route"]
    assert body["tool_call_count"] == expected["tool_call_count"]
    assert body["correction_count"] == expected["correction_count"]
    assert body["turn_count"] == expected["turn_count"]
    assert (body["final_answer"] is not None) == (expected["status"] == "success")


@pytest.mark.parametrize("scenario", sorted(SCENARIO_EXPECTATIONS))
def test_scenario_is_deterministic_across_runs(scenario: str) -> None:
    keys = ("status", "route", "final_answer", "quality_score", "tool_call_count", "correction_count", "turn_count")
    first = client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"}).json()
    second = client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"}).json()
    for key in keys:
        assert first[key] == second[key], f"{scenario}: {key!r} differed between two identical runs"


def test_unknown_scenario_returns_400() -> None:
    response = client.post("/run", json={"scenario": "does_not_exist", "mode": "telemetry_only"})
    assert response.status_code == 400


@pytest.mark.parametrize("scenario", sorted(SCENARIO_EXPECTATIONS))
def test_scenario_span_shape(scenario: str, span_exporter) -> None:
    expected = SCENARIO_EXPECTATIONS[scenario]
    response = client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
    execution_id = response.json()["execution_id"]
    spans = span_exporter.get_finished_spans()
    assert spans, "expected at least one span to be captured for this run"

    # docs/architecture.md: use the canonical witdem.execution_id baggage key so
    # every span -- root, component, and nested tool child spans alike --
    # carries a stable, Witdem-correlatable execution_id.
    assert all(s.attributes.get("witdem.execution_id") == execution_id for s in spans), [
        (s.name, s.attributes.get("witdem.execution_id")) for s in spans
    ]

    assert len(_component_spans(spans, "execute_tool")) == expected["tool_call_count"]
    assert len(_component_spans(spans, "generate_turn")) == expected["turn_count"]

    expected_evaluate_spans = 0 if expected["status"] == "failed" else int(expected["correction_count"]) + 1
    assert len(_component_spans(spans, "evaluate_answer")) == expected_evaluate_spans


def test_tool_calling_scenario_tool_span_is_ok(span_exporter) -> None:
    client.post("/run", json={"scenario": "tool_calling", "mode": "telemetry_only"})
    tool_spans = _component_spans(span_exporter.get_finished_spans(), "execute_tool")
    assert len(tool_spans) == 1
    assert tool_spans[0].status.status_code == StatusCode.OK


def test_failure_recovery_scenario_tool_spans_error_then_ok(span_exporter) -> None:
    client.post("/run", json={"scenario": "failure_recovery", "mode": "telemetry_only"})
    tool_spans = sorted(
        _component_spans(span_exporter.get_finished_spans(), "execute_tool"),
        key=lambda s: s.start_time,
    )
    assert len(tool_spans) == 2
    assert tool_spans[0].status.status_code == StatusCode.ERROR
    assert tool_spans[1].status.status_code == StatusCode.OK


def test_terminal_failure_scenario_tool_spans_all_error_and_no_evaluate(span_exporter) -> None:
    response = client.post("/run", json={"scenario": "terminal_failure", "mode": "telemetry_only"})
    assert response.json()["status"] == "failed"
    assert response.json()["final_answer"] is None

    spans = span_exporter.get_finished_spans()
    tool_spans = _component_spans(spans, "execute_tool")
    assert len(tool_spans) == 2
    assert all(s.status.status_code == StatusCode.ERROR for s in tool_spans)
    assert _component_spans(spans, "evaluate_answer") == []


def test_nested_scenario_has_two_extra_nested_child_spans(span_exporter) -> None:
    client.post("/run", json={"scenario": "nested", "mode": "telemetry_only"})
    spans = span_exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert "tool.multi_step_lookup.lookup_step" in names
    assert "tool.multi_step_lookup.calculate_step" in names

    tool_span = _component_spans(spans, "execute_tool")[0]
    lookup_step = next(s for s in spans if s.name == "tool.multi_step_lookup.lookup_step")
    calculate_step = next(s for s in spans if s.name == "tool.multi_step_lookup.calculate_step")

    # Both children are nested directly under the execute_tool component span
    # -- this is the one scenario whose span tree goes deeper than the flat
    # root -> component shape every other scenario has.
    assert lookup_step.parent is not None
    assert lookup_step.parent.span_id == tool_span.context.span_id
    assert calculate_step.parent is not None
    assert calculate_step.parent.span_id == tool_span.context.span_id


def test_only_nested_scenario_produces_deeply_nested_tool_spans(span_exporter) -> None:
    """Cross-scenario check that "nested" is genuinely structurally distinct."""

    client.post("/run", json={"scenario": "tool_calling", "mode": "telemetry_only"})
    names = {s.name for s in span_exporter.get_finished_spans()}
    assert "tool.multi_step_lookup.lookup_step" not in names
    assert "tool.multi_step_lookup.calculate_step" not in names
