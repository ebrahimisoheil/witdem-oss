"""Correlation-logic tests: fake/real active span + baggage context.

Uses a real ``opentelemetry-sdk`` ``TracerProvider`` to create genuine spans
(a dev/test-only dependency -- the runtime package itself only needs
``opentelemetry-api``). Fully offline: no Witdem server, no network.
"""

from __future__ import annotations

import pytest
from opentelemetry import baggage
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer

from witdem_sdk._correlation import (
    EXECUTION_ID_BAGGAGE_KEY,
    resolve_correlation,
)
from witdem_sdk._errors import WitdemSDKError


def _tracer() -> Tracer:
    return TracerProvider().get_tracer("witdem_sdk.tests")


def test_no_span_no_baggage_no_explicit_id_raises() -> None:
    with pytest.raises(WitdemSDKError):
        resolve_correlation(None)


def test_error_message_names_the_baggage_key() -> None:
    with pytest.raises(WitdemSDKError, match="witdem.execution_id"):
        resolve_correlation(None)


def test_explicit_execution_id_without_active_span_succeeds() -> None:
    execution_id, trace_id, span_id = resolve_correlation("exec-explicit")

    assert execution_id == "exec-explicit"
    assert trace_id is None
    assert span_id is None


def test_active_span_and_baggage_autofill_everything() -> None:
    tracer = _tracer()
    ctx = baggage.set_baggage(EXECUTION_ID_BAGGAGE_KEY, "exec-from-baggage")
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span("outer") as span:
            execution_id, trace_id, span_id = resolve_correlation(None)
            span_context = span.get_span_context()
    finally:
        otel_context.detach(token)

    assert execution_id == "exec-from-baggage"
    assert trace_id == format(span_context.trace_id, "032x")
    assert span_id == format(span_context.span_id, "016x")
    assert trace_id is not None and len(trace_id) == 32
    assert span_id is not None and len(span_id) == 16


def test_explicit_execution_id_overrides_baggage() -> None:
    tracer = _tracer()
    ctx = baggage.set_baggage(EXECUTION_ID_BAGGAGE_KEY, "exec-from-baggage")
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span("outer"):
            execution_id, _trace_id, _span_id = resolve_correlation("exec-explicit-override")
    finally:
        otel_context.detach(token)

    assert execution_id == "exec-explicit-override"


def test_active_span_without_baggage_and_no_explicit_id_still_raises() -> None:
    """An active span alone is not enough -- execution_id must resolve too."""

    tracer = _tracer()
    with pytest.raises(WitdemSDKError), tracer.start_as_current_span("outer"):
        resolve_correlation(None)


def test_non_string_baggage_value_is_ignored() -> None:
    """Baggage values are always strings on the wire; a non-string value
    (defensive: should not normally happen) must not be treated as a usable
    execution_id."""

    ctx = baggage.set_baggage(EXECUTION_ID_BAGGAGE_KEY, "")
    token = otel_context.attach(ctx)
    try:
        with pytest.raises(WitdemSDKError):
            resolve_correlation(None)
    finally:
        otel_context.detach(token)
