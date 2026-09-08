import pytest
from pydantic import ValidationError

from witdem.dashboard.schemas import OperationPagination, WorkflowOperationsResponse


def test_operation_pagination_is_additive_and_preserves_truncation_metadata():
    base = {"workflow_id": "review", "summary": {"total_operations": 510, "execution_containers": 0,
                                                "failed_operations": 0},
            "measurement_coverage": {"measured": 0, "missing": 0, "not_applicable": 0,
                                     "applicable": 0, "coverage": None}}
    assert WorkflowOperationsResponse.model_validate(base).pagination is None
    result = WorkflowOperationsResponse.model_validate({**base, "pagination": {
        "revision": 1, "types_total": 30, "page_size": 25, "type_page_size": 10, "detail_limit": 20},
        "participants": []})
    assert result.summary.total_operations == 510
    assert result.pagination.summary_scope == "all_projected_executions"
    assert result.pagination.measurements_scope == "not_included"
    assert result.operations == []


@pytest.mark.parametrize("changes", [{"type_page_size": 26}, {"page_size": 101}, {"revision": 0},
                                    {"participant_metric": "unsupported"}, {"schema_version": "future"}])
def test_operation_page_contract_rejects_unsupported_bounds(changes):
    with pytest.raises(ValidationError):
        OperationPagination.model_validate({"revision": 1, "types_total": 30, "page_size": 25,
                                            "type_page_size": 10, "detail_limit": 20, **changes})
