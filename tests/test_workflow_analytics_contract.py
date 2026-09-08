from copy import deepcopy

import pytest

from witdem.analytics.workflow_analytics import workflow_projection_analytics
from witdem.dashboard import service


def replay(execution_id, *, provider="provider-a", cost=0, tokens=0, duration=2, state="completed"):
    return {
        "execution": {"execution_id": execution_id, "runtime_outcome": "failed" if state == "failed" else "completed"},
        "nodes": [{
            "id": "draft", "name": "Draft", "state": state, "attempts": 2 if state == "recovered" else 1,
            "duration_seconds": duration, "known_cost": cost, "total_tokens": tokens,
            "cost_eligible_operations": 1, "cost_measured_operations": int(cost is not None),
            "token_eligible_operations": 1, "token_measured_operations": int(tokens is not None),
            "model_calls": [{"provider": provider, "model": "shared-name", "known_cost": cost,
                             "total_tokens": tokens, "duration_seconds": duration}],
        }],
    }


def test_public_analytics_retains_existing_service_import_and_does_not_mutate_inputs():
    assert service._workflow_projection_analytics is workflow_projection_analytics
    data = [replay("first"), replay("second", cost=None, tokens=None, duration=8, state="failed")]
    before = deepcopy(data)
    result = workflow_projection_analytics(data)
    assert data == before
    model = result["models"][0]
    assert model["runs"] == 2 and model["calls"] == 2 and model["failed"] == 1
    assert model["measured_cost"] is None and model["total_tokens"] is None
    assert model["cost_coverage"] == 0.5
    assert model["p50_call_seconds"] == 5
    assert model["p95_call_seconds"] == pytest.approx(7.7)
    stage = result["stages"][0]
    assert stage["known_cost"] is None and stage["total_tokens"] is None
    assert stage["calls"] == 2 and stage["executions"] == 2


def test_public_analytics_keeps_zero_measurements_and_provider_qualified_models():
    result = workflow_projection_analytics([
        replay("first"), replay("second", provider="provider-b", state="recovered"),
    ])
    assert {item["participant_id"] for item in result["models"]} == {
        "provider-a::shared-name", "provider-b::shared-name",
    }
    assert all(item["measured_cost"] == 0 and item["total_tokens"] == 0 for item in result["models"])
    assert len(result["providers"]) == 2
    assert result["stages"][0]["extra_attempts"] == 1


def test_public_analytics_empty_and_inactive_inputs():
    empty = {"models": [], "providers": [], "stages": []}
    assert workflow_projection_analytics([]) == empty
    assert workflow_projection_analytics([replay("inactive", state="inactive")]) == empty
