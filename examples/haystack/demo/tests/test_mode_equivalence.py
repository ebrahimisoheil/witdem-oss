"""Core invariant (task requirement 2 / docs/architecture.md): physical
execution must be equivalent between modes for the same scenario. Mode only
ever adds witdem_sdk calls in api.py's ``/run`` handler, strictly *after*
``workflow.run_workflow`` returns -- it must never change which pipeline
components ran, how many times, or in what order.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_demo.api import app
from agent_demo.enrichment import WITDEM_SDK_AVAILABLE

client = TestClient(app)

ALL_SCENARIOS = [
    "simple_success",
    "tool_calling",
    "correction_loop",
    "failure_recovery",
    "terminal_failure",
    "nested",
]


def _span_shape(spans):
    """A comparable summary of a run's physical shape: which named component
    (or nested tool child) spans ran, in what order, with what status --
    ignoring timestamps, span/trace ids, and execution_id, which are
    expected to differ run to run.
    """

    return [
        (s.attributes.get("haystack.component.name") or s.name, s.status.status_code)
        for s in sorted(spans, key=lambda s: s.start_time)
    ]


@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_telemetry_only_shape_is_stable_across_repeated_runs(scenario: str, span_exporter) -> None:
    """Same-mode determinism: a prerequisite for, and always-on proxy for,
    the true cross-mode equivalence check below (which needs witdem_sdk
    installed to fully exercise sdk_enriched mode).
    """

    client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
    first_shape = _span_shape(span_exporter.get_finished_spans())
    span_exporter.clear()

    client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
    second_shape = _span_shape(span_exporter.get_finished_spans())

    assert first_shape == second_shape


@pytest.mark.skipif(
    not WITDEM_SDK_AVAILABLE,
    reason="witdem_sdk not installed yet (concurrent build in ../../../witdem-sdk); see enrichment.py",
)
@pytest.mark.enriched
@pytest.mark.parametrize("scenario", ALL_SCENARIOS)
def test_physical_shape_equivalent_between_modes(scenario: str, span_exporter) -> None:
    client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
    telemetry_only_shape = _span_shape(span_exporter.get_finished_spans())
    span_exporter.clear()

    client.post("/run", json={"scenario": scenario, "mode": "sdk_enriched"})
    sdk_enriched_shape = _span_shape(span_exporter.get_finished_spans())

    assert telemetry_only_shape == sdk_enriched_shape
