from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from witdem.dashboard import service
from witdem.dashboard.schemas import WorkflowDetailResponse
from witdem.workflows import WorkflowDefinition


@pytest.mark.parametrize("total,included", [(0, 0), (2, 2), (510, 100)])
def test_workflow_detail_declares_its_bounded_window(monkeypatch, total, included):
    definition = WorkflowDefinition.model_validate({
        "version": 2, "id": "review", "name": "Review",
        "stages": [{"id": "work", "name": "Work", "nodes": [{"id": "draft", "name": "Draft"}]}],
    })
    monkeypatch.setattr(service, "_persisted_definitions", lambda repo: {"review": definition})
    monkeypatch.setattr(service, "load_registry", lambda: SimpleNamespace(definitions={}))
    repo = Mock()
    repo.workflow_projection_catalog.return_value = [{"workflow_id": "review", "execution_count": total}]
    repo.workflow_projection_rows.return_value = [{"projection": {
        "workflow": {"id": "review"}, "execution": {"execution_id": f"run-{index}"}, "nodes": [],
    }} for index in range(included)]
    payload = service.workflow_detail(repo, "review")
    repo.workflow_projection_rows.assert_called_once_with("review", limit=100)
    assert payload["execution_count"] == total
    assert payload["execution_window"] == {"schema_version": "v1alpha1", "limit": 100,
                                           "included_count": included, "total_count": total,
                                           "order": "projected_at_desc"}
    assert len(payload["executions"]) == included
    assert WorkflowDetailResponse.model_validate(payload).execution_window.total_count == total
    # Older servers/fixtures remain valid; absence does not imply an all-time view.
    legacy = {key: value for key, value in payload.items() if key != "execution_window"}
    assert WorkflowDetailResponse.model_validate(legacy).execution_window is None
