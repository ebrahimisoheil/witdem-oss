"""End-to-end tests of the 5 public witdem_sdk functions.

Covers: correlation -> exact wire-payload construction -> (mocked)
transport. The HTTP transport is always mocked (``monkeypatch``) -- no real
Witdem server is needed and no real network call is ever made.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from opentelemetry import baggage
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span

import witdem_sdk
import witdem_sdk._transport as transport
from witdem_sdk._correlation import EXECUTION_ID_BAGGAGE_KEY
from witdem_sdk._errors import WitdemSDKError

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


class _Captured:
    """Records every ``httpx.post`` call made by the transport layer."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(200, request=httpx.Request("POST", url))


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    sink = _Captured()
    monkeypatch.setattr(transport.httpx, "post", sink)
    return sink


@pytest.fixture
def active_span_with_baggage() -> Iterator[Span]:
    """Enter a real span whose context carries witdem.execution_id baggage --
    the normal case this SDK is built for: an external app already using
    product_factory_agent.telemetry.otel's baggage convention."""

    tracer = TracerProvider().get_tracer("witdem_sdk.tests")
    ctx = baggage.set_baggage(EXECUTION_ID_BAGGAGE_KEY, "exec-123")
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span("outer") as span:
            yield span
    finally:
        otel_context.detach(token)


def _assert_common_envelope(body: dict[str, Any], *, kind: str, name: str) -> None:
    assert body["version"] == "1.0"
    assert body["kind"] == kind
    assert _HEX32.match(body["event_id"])
    assert body["execution_id"] == "exec-123"
    assert body["trace_id"] is not None and _HEX32.match(body["trace_id"])
    assert body["span_id"] is not None and _HEX16.match(body["span_id"])
    assert body["name"] == name


# --- event() ----------------------------------------------------------------


def test_event_sends_expected_wire_payload(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.event("thing_happened", {"a": 1, "b": "two"})

    assert len(captured.calls) == 1
    call = captured.calls[0]
    assert call["url"] == "http://localhost:4318/sdk/v1/records"
    body = call["json"]
    _assert_common_envelope(body, kind="event", name="thing_happened")
    assert body["value"] is None
    assert body["attributes"] == {"a": 1, "b": "two"}


def test_event_with_no_payload_sends_empty_attributes(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.event("thing_happened")

    body = captured.calls[0]["json"]
    assert body["attributes"] == {}


# --- decision() ---------------------------------------------------------------


def test_decision_sends_expected_wire_payload(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.decision("answer_route", "escalate", attributes={"reason": "ambiguous"})

    body = captured.calls[0]["json"]
    _assert_common_envelope(body, kind="decision", name="answer_route")
    assert body["value"] == "escalate"
    assert body["attributes"] == {"reason": "ambiguous"}


# --- evaluation() ---------------------------------------------------------------


def test_evaluation_with_score_and_label(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.evaluation("answer_quality", score=0.87, label="good", value="approved")

    body = captured.calls[0]["json"]
    _assert_common_envelope(body, kind="evaluation", name="answer_quality")
    assert body["value"] == "approved"
    assert body["attributes"] == {"score": 0.87, "label": "good"}


def test_evaluation_omits_unset_score_and_label(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.evaluation("answer_quality")

    body = captured.calls[0]["json"]
    assert body["value"] is None
    assert body["attributes"] == {}


def test_evaluation_with_only_score(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.evaluation("answer_quality", score=0.5)

    assert captured.calls[0]["json"]["attributes"] == {"score": 0.5}


def test_explicit_evaluation_fields_override_attribute_names(
    captured: _Captured, active_span_with_baggage: Span
) -> None:
    witdem_sdk.evaluation(
        "goal",
        score=0.9,
        label="pass",
        attributes={"score": 0.1, "label": "fail", "case_id": "case-1"},
    )
    assert captured.calls[0]["json"]["attributes"] == {
        "score": 0.9,
        "label": "pass",
        "case_id": "case-1",
    }


# --- outcome() ---------------------------------------------------------------


def test_outcome_sends_expected_wire_payload(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.outcome("completed_answer", status="success", value={"records": 3})

    body = captured.calls[0]["json"]
    _assert_common_envelope(body, kind="outcome", name="completed_answer")
    assert body["value"] == {"records": 3}
    assert body["attributes"] == {"status": "success"}


def test_outcome_omits_unset_status(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.outcome("completed_answer")

    assert captured.calls[0]["json"]["attributes"] == {}


def test_explicit_outcome_status_overrides_attribute_status(
    captured: _Captured, active_span_with_baggage: Span
) -> None:
    witdem_sdk.outcome(
        "product_goal",
        status="achieved",
        attributes={"status": "failed", "product_goal_achieved": True},
    )
    assert captured.calls[0]["json"]["attributes"] == {
        "status": "achieved",
        "product_goal_achieved": True,
    }


# --- metric() ---------------------------------------------------------------


def test_metric_sends_expected_wire_payload(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.metric("records_processed", 42, attributes={"unit": "records"})

    body = captured.calls[0]["json"]
    _assert_common_envelope(body, kind="metric", name="records_processed")
    assert body["value"] == 42
    assert body["attributes"] == {"unit": "records"}


# --- correlation / error path, exercised through the public API -------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: witdem_sdk.event("x"),
        lambda: witdem_sdk.decision("x", "y"),
        lambda: witdem_sdk.evaluation("x"),
        lambda: witdem_sdk.outcome("x"),
        lambda: witdem_sdk.metric("x", 1),
    ],
)
def test_every_function_raises_without_context_or_execution_id(captured: _Captured, call: Any) -> None:
    with pytest.raises(WitdemSDKError):
        call()

    assert captured.calls == []  # never attempts to send an uncorrelated record


def test_explicit_execution_id_works_with_no_active_span(captured: _Captured) -> None:
    witdem_sdk.event("thing_happened", execution_id="exec-explicit")

    body = captured.calls[0]["json"]
    assert body["execution_id"] == "exec-explicit"
    assert body["trace_id"] is None
    assert body["span_id"] is None


def test_explicit_execution_id_overrides_active_baggage(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.event("thing_happened", execution_id="exec-explicit-override")

    body = captured.calls[0]["json"]
    assert body["execution_id"] == "exec-explicit-override"


def test_each_call_gets_a_fresh_event_id(captured: _Captured, active_span_with_baggage: Span) -> None:
    witdem_sdk.event("thing_happened")
    witdem_sdk.event("thing_happened")

    first, second = (call["json"]["event_id"] for call in captured.calls)
    assert first != second


def test_endpoint_is_configurable_via_env_var(
    monkeypatch: pytest.MonkeyPatch, captured: _Captured, active_span_with_baggage: Span
) -> None:
    monkeypatch.setenv("WITDEM_ENDPOINT", "http://witdem.example:9999")

    witdem_sdk.metric("records_processed", 1)

    assert captured.calls[0]["url"] == "http://witdem.example:9999/sdk/v1/records"


def test_public_functions_return_none(captured: _Captured, active_span_with_baggage: Span) -> None:
    assert witdem_sdk.event("x") is None
    assert witdem_sdk.decision("x", "y") is None
    assert witdem_sdk.evaluation("x") is None
    assert witdem_sdk.outcome("x") is None
    assert witdem_sdk.metric("x", 1) is None
