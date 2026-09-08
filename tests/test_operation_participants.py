from copy import deepcopy

from witdem.analytics.participants import operation_participant_inputs, operation_participant_rows
from witdem.dashboard.schemas import WorkflowOperationsResponse


def test_direct_participant_inputs_preserve_scope_zero_and_missing_usage():
    base = {"operation_id": "same", "family": "inference", "duration_seconds": 2, "provider_id": "provider"}
    operations = [{**base, "execution_id": "a", "model_id": "model-a"},
                  {**base, "execution_id": "b", "model_id": "model-b", "duration_seconds": None},
                  {**base, "execution_id": "c", "entity_kind": "execution"},
                  {**base, "execution_id": "d", "plane": "control"},
                  {**base, "execution_id": "e", "plane": "business"}]
    measurements = [
        {"execution_id": "a", "operation_id": "same", "measurement_key": "cost.usd", "value": 0,
         "measurement_status": "measured"},
        {"execution_id": "b", "operation_id": "same", "measurement_key": "tokens.total", "value": 12,
         "measurement_status": "measured"},
        {"execution_id": "b", "operation_id": "same", "measurement_key": "cost.usd", "value": 99,
         "measurement_status": "missing"},
    ]
    before = deepcopy((operations, measurements))
    inputs = operation_participant_inputs(operations, measurements)
    provider = inputs[0]
    assert provider == {"dimension": "provider", "id": "provider", "calls": 2, "time": 2.,
                        "cost_sum": 0., "cost_count": 1, "tokens_sum": 12., "tokens_count": 1}
    rows = operation_participant_rows(inputs)
    assert rows == [
        {"dimension": "provider", "id": "provider", "calls": 2, "time": 2., "cost": 0., "tokens": 12.},
        {"dimension": "model", "id": "model-a", "calls": 1, "time": 2., "cost": 0., "tokens": None},
        {"dimension": "model", "id": "model-b", "calls": 1, "time": 0., "cost": None, "tokens": 12.},
    ]
    assert (operations, measurements) == before
    separate = [operation_participant_inputs([item], measurements) for item in operations[:2]]
    for field in ("calls", "time", "cost_sum", "cost_count", "tokens_sum", "tokens_count"):
        assert sum(items[0][field] for items in separate) == provider[field]


def test_participants_are_an_additive_response_field():
    base = {"workflow_id": "review", "summary": {"total_operations": 0, "execution_containers": 0,
                                                 "failed_operations": 0},
            "measurement_coverage": {"measured": 0, "missing": 0, "not_applicable": 0,
                                     "applicable": 0, "coverage": None}}
    assert WorkflowOperationsResponse.model_validate(base).participants is None
    assert WorkflowOperationsResponse.model_validate({**base, "participants": []}).participants == []
