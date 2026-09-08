import pytest
from pydantic import ValidationError

from witdem.dashboard.schemas import OperationFact, WorkflowOperationsResponse


@pytest.mark.parametrize("status", [None, "unset", "ok", "error", "running", "vendor-specific"])
def test_operation_status_preserves_reported_and_unknown_values(status):
    fact = dict(operation_id="op", execution_id="run", family="inference", operation_type="text_generation",
                interface="model_api", role="application", status=status, duration_seconds=None)
    response = WorkflowOperationsResponse.model_validate({
        "workflow_id": "review", "summary": {"total_operations": 1, "execution_containers": 0,
                                               "failed_operations": int(status == "error")},
        "measurement_coverage": {"measured": 0, "missing": 0, "not_applicable": 0,
                                 "applicable": 0, "coverage": None},
        "operations": [fact],
    })
    assert response.model_dump(mode="json")["operations"][0]["status"] == status
    assert response.operations[0].duration_seconds is None


def test_operation_status_schema_is_nullable_and_rejects_non_string_values():
    assert {item["type"] for item in OperationFact.model_json_schema()["properties"]["status"]["anyOf"]} == {
        "string", "null",
    }
    with pytest.raises(ValidationError):
        OperationFact(operation_id="op", execution_id="run", family="inference", operation_type="text_generation",
                      interface="model_api", role="application", status=123)
