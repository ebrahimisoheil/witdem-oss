"""All six scenarios must produce structurally different physical shapes.
This test makes that an explicit, direct assertion (the
per-scenario span-count checks in test_scenarios_telemetry_only.py imply it,
but don't say so in one place): no two scenarios' span-name sequences may
collide.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_demo.api import app

client = TestClient(app)

ALL_SCENARIOS = [
    "simple_success",
    "tool_calling",
    "correction_loop",
    "failure_recovery",
    "terminal_failure",
    "nested",
]


def _shape_signature(spans) -> tuple[str, ...]:
    """The ordered sequence of component/tool-child span names for one run.

    Deliberately just names (not statuses/counts already covered elsewhere)
    -- this is the closest local analog to canonical path/loop signatures.
    """

    ordered = sorted(spans, key=lambda s: s.start_time)
    return tuple(s.attributes.get("haystack.component.name") or s.name for s in ordered)


def test_every_scenario_has_a_unique_span_shape_signature(span_exporter) -> None:
    signatures: dict[str, tuple[str, ...]] = {}
    for scenario in ALL_SCENARIOS:
        span_exporter.clear()
        client.post("/run", json={"scenario": scenario, "mode": "telemetry_only"})
        signatures[scenario] = _shape_signature(span_exporter.get_finished_spans())

    seen: dict[tuple[str, ...], str] = {}
    collisions = []
    for scenario, signature in signatures.items():
        if signature in seen:
            collisions.append((scenario, seen[signature], signature))
        else:
            seen[signature] = scenario

    assert not collisions, f"scenarios with colliding physical shapes: {collisions}\nall signatures: {signatures}"
