"""sdk_enriched mode: mode-gated calls into the public witdem_sdk surface.

witdem_sdk (built concurrently at ../../../witdem-sdk by a different agent) may not be
installed yet when these tests run -- see pyproject.toml's
``[project.optional-dependencies].enriched`` and enrichment.py's guarded
import. The graceful-failure behavior (a clear 503, never a crash) is always
tested regardless of whether witdem_sdk is installed; the true end-to-end
success path is skipped with a clear reason until it is, per the task
brief's explicit guidance for this timing dependency.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_demo.api import app
from agent_demo.enrichment import WITDEM_SDK_AVAILABLE

client = TestClient(app)

requires_witdem_sdk = pytest.mark.skipif(
    not WITDEM_SDK_AVAILABLE,
    reason=(
        "witdem_sdk is not installed yet (concurrent build in ../../../witdem-sdk); "
        "install the 'enriched' extra once it lands, e.g. "
        "`pip install -e '.[enriched]'` or `uv sync --extra enriched`."
    ),
)


def test_sdk_enriched_mode_without_witdem_sdk_fails_gracefully() -> None:
    if WITDEM_SDK_AVAILABLE:
        pytest.skip("witdem_sdk IS installed in this environment; see test_sdk_enriched_mode_succeeds_end_to_end")
    response = client.post("/run", json={"scenario": "simple_success", "mode": "sdk_enriched"})
    assert response.status_code == 503
    assert "witdem_sdk" in response.json()["detail"]


def test_telemetry_only_mode_is_unaffected_by_witdem_sdk_availability() -> None:
    """telemetry_only must succeed identically whether or not witdem_sdk is installed."""

    response = client.post("/run", json={"scenario": "simple_success", "mode": "telemetry_only"})
    assert response.status_code == 200
    assert response.json()["sdk_enriched"] is False


@requires_witdem_sdk
@pytest.mark.enriched
@pytest.mark.parametrize(
    "scenario",
    ["simple_success", "tool_calling", "correction_loop", "failure_recovery", "terminal_failure", "nested"],
)
def test_sdk_enriched_mode_succeeds_end_to_end(scenario: str) -> None:
    response = client.post("/run", json={"scenario": scenario, "mode": "sdk_enriched"})
    assert response.status_code == 200
    body = response.json()
    assert body["sdk_enriched"] is True
    assert body["scenario"] == scenario
