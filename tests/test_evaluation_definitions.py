from witdem.analytics.evidence import evaluation_definition_inputs
from witdem.dashboard.schemas import WorkflowEvaluationsResponse


def test_definition_inputs_preserve_json_numeric_and_first_declaration_rules():
    rows = [
        {"name": "q", "score": .2, "passed": False, "attributes": {"target": True, "direction": ""}},
        {"name": "q", "score": .8, "passed": True, "attributes": {"target": .7, "direction": ">="}},
        {"name": "q", "score": None, "passed": None, "attributes": {"target": .9}},
        {"name": "other", "score": None, "passed": None, "attributes": {}},
    ]
    groups = evaluation_definition_inputs(rows)
    assert groups == [
        {"name": "q", "reported": 3, "passed": 1, "needs_attention": 1, "unassessed": 1,
         "score_sum": 1.0, "score_count": 2, "target": .7, "direction": ""},
        {"name": "other", "reported": 1, "passed": 0, "needs_attention": 0, "unassessed": 1,
         "score_sum": 0., "score_count": 0, "target": None, "direction": None},
    ]
    assert rows[0]["attributes"]["target"] is True


def test_paginated_response_is_additive_to_legacy_contract():
    old = {"workflow_id": "review", "summary": {"reported": 0, "passed": 0, "needs_attention": 0,
                                                "unassessed": 0, "executions": 0}, "results": [], "campaigns": []}
    assert WorkflowEvaluationsResponse.model_validate(old).pagination is None
    page = WorkflowEvaluationsResponse.model_validate({
        **old, "definition_groups": [], "campaigns_status": "unavailable",
        "pagination": {"revision": 1, "results_total": 0, "definitions_total": 0,
                       "page_size": 25, "definition_page_size": 25}})
    assert page.pagination.summary_scope == "all_projected_executions"
    assert page.campaigns_status == "unavailable"
