from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from witdem.analytics.evidence import evaluation_profile
from witdem.dashboard import service
from witdem.dashboard.schemas import WorkflowEvaluationsResponse


def test_evaluation_profile_preserves_order_assessment_and_deduplication():
    first = {"execution_id": "a", "evaluation_id": "old", "name": "quality", "score": .9,
             "attributes": {"target": .8, "direction": "higher_is_better"}}
    rows = [first, {**first, "evaluation_id": "other-subject", "subject_id": "node"},
            {**first, "evaluation_id": "replacement", "score": .1},
            {**first, "execution_id": "b", "attributes": {"passed": False}},
            {**first, "definition_version": "2", "attributes": {}},
            {**first, "name": "declaration", "attributes": {"passed": True, "target": 1, "direction": ">="}}]
    original = deepcopy(rows)
    profile = evaluation_profile(rows)
    assert rows == original
    assert [item["evaluation_id"] for item in profile["results"]] == [
        "replacement", "other-subject", "old", "old", "old",
    ]
    assert [item["passed"] for item in profile["results"]] == [False, True, False, None, True]
    assert profile["summary"] == {"reported": 5, "passed": 2, "needs_attention": 2,
                                  "unassessed": 1, "executions": 2}


def test_profile_retains_legacy_empty_key_fallbacks():
    rows = [{"evaluation_id": "a"}, {"evaluation_id": "b", "subject_id": "execution",
                                     "definition_version": "unversioned"}]
    assert [item["evaluation_id"] for item in evaluation_profile(rows)["results"]] == ["b"]
    assert evaluation_profile([]) == {"summary": {"reported": 0, "passed": 0, "needs_attention": 0,
                                                  "unassessed": 0, "executions": 0}, "results": []}


def test_dashboard_uses_public_profile_without_changing_response(monkeypatch):
    monkeypatch.setattr(service, "_persisted_definitions", lambda repo: {"review": object()})
    monkeypatch.setattr(service, "load_registry", lambda: SimpleNamespace(definitions={}))
    repo = Mock()
    repo.workflow_evaluations.return_value = [{"evaluation_id": "a", "execution_id": "run", "name": "q"}]
    repo.workflow_evaluation_campaigns.return_value = [{"campaign_id": "offline"}]
    expected = {"workflow_id": "review", **evaluation_profile(repo.workflow_evaluations.return_value),
                "campaigns": [{"campaign_id": "offline"}]}
    assert service.workflow_evaluations(repo, "review") == expected
    WorkflowEvaluationsResponse.model_validate(expected)
    assert service.workflow_evaluations(repo, "missing") is None
