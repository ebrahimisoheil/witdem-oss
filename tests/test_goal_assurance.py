from copy import deepcopy

import pytest

from witdem.analytics.assurance import (
    accumulate_goal_assurance,
    finalize_goal_assurance,
    goal_assurance_state,
    summarize_goal_assurance,
)
from witdem.analytics.repository.analytics_repository import AnalyticsRepository
from witdem.analytics.repository.state import FilterState


def population():
    states = [
        {"product_goal_achieved": True, "assurance_status": "assured", "evidence_sufficient": False},
        {"product_goal_achieved": True, "evidence_sufficient": False},
        {"product_goal_achieved": True},
        {"product_goal_achieved": False, "assurance_status": "assured"},
        {"product_goal_achieved": None},
        {"product_goal_achieved": True, "product_goal_reported": False},
        {"product_goal_achieved": True, "assurance_status": "unknown", "evidence_sufficient": True},
    ]
    rows = [{"execution_id": str(index), "product_goal_reported": True,
             "contract_hash": "v1" if index % 2 == 0 else "v2", **state}
            for index, state in enumerate(states)]
    definitions = {
        "v1": {"contract_hash": "v1", "contract_name": "First contract",
               "product_goal": {"name": "Approve", "description": "Check request"}},
        "v2": {"contract_hash": "v2", "contract_name": "Second contract",
               "product_goal": {"name": "APPROVE", "description": "CHECK REQUEST"}},
    }
    evaluations = {
        "0": [{"name": "Quality", "score": 0.6, "attributes": {
            "evaluation_key": "quality", "passed": False, "target": 0.5, "direction": "higher_is_better",
        }}],
        "1": [{"name": "Quality", "score": 0, "attributes":
               '{"evaluation_key":"quality","target":0.5,"direction":"higher_is_better"}'}],
        "2": [{"name": "Quality", "attributes": {"evaluation_key": "quality", "passed": True}}],
        "3": [{"name": "Ignored on unachieved goal", "attributes": {"passed": False}}],
        "5": [{"name": "Ignored on unreported goal", "attributes": {"passed": False}}],
    }
    return rows, evaluations, definitions


def test_shared_accumulator_preserves_goal_grouping_denominators_and_explicit_assurance():
    inputs = population()
    original = deepcopy(inputs)
    portfolio, summary = summarize_goal_assurance(*inputs)
    assert inputs == original
    assert summary == {
        "reported_runs": 6, "achieved_runs": 4, "assured_runs": 2, "attention_runs": 1,
        "not_achieved_runs": 2, "unassessed_runs": 1,
        "assurance_rate": 0.5, "attention_rate": 0.25, "assessment_coverage": 0.75,
    }
    assert len(portfolio) == 1
    goal = portfolio[0]
    assert goal["goal_id"] == "approve::check request"
    assert goal["goal_name"] == "Approve"
    assert goal["contract_name"] == "First contract"
    assert goal["contract_hashes"] == ["v1", "v2"]
    assert goal["contract_count"] == 2
    assert goal["contract_hash"] is None
    assert goal["single_execution_id"] is None
    assert goal["success_rate"] == 4 / 6
    quality = goal["evaluations"][0]
    assert quality["reported_runs"] == 3
    assert quality["passed_runs"] == 1
    assert quality["attention_runs"] == 2
    assert quality["average_score"] == 0.3
    assert goal["top_attention"] == quality
    groups, counts = accumulate_goal_assurance(*inputs)
    before = deepcopy((groups, counts))
    assert groups[0]["evaluations"]["quality"]["score_total"] == 0.6
    assert groups[0]["evaluations"]["quality"]["score_runs"] == 2
    assert finalize_goal_assurance(groups, counts) == (portfolio, summary)
    assert (groups, counts) == before
    assert finalize_goal_assurance(groups, counts) == (portfolio, summary)


def test_repository_delegates_without_changing_selected_population(monkeypatch):
    rows, evaluations, definitions = population()
    repository = object.__new__(AnalyticsRepository)
    repository._serving_tables = {"semantic_facts"}
    filters = FilterState(provider="selected")

    def execution_rows(selected, limit):
        assert selected == filters and limit is None
        return rows

    monkeypatch.setattr(repository, "execution_rows", execution_rows)
    monkeypatch.setattr(repository, "contract_definitions", lambda selected: list(definitions.values()))
    monkeypatch.setattr(repository, "_latest_evaluation_facts", lambda allowed: [
        {**fact, "execution_id": execution_id}
        for execution_id, facts in evaluations.items() for fact in facts if execution_id in allowed
    ])
    assert repository.goal_assurance(filters) == summarize_goal_assurance(rows, evaluations, definitions)


@pytest.mark.parametrize("achievement", [False, None, "true", 1])
def test_legacy_non_true_achievement_is_not_assured(achievement):
    # Preserve the existing portfolio classification, not the separate explicit
    # false-only execution filter. Changing this is a semantic change, not extraction.
    assert goal_assurance_state({"product_goal_achieved": achievement, "assurance_status": "assured"}) == "not_achieved"


def test_unversioned_single_goal_and_no_reported_population():
    rows = [{"execution_id": "one", "product_goal_reported": True, "product_goal_achieved": False}]
    portfolio, summary = summarize_goal_assurance(rows, {}, {})
    assert portfolio[0]["goal_name"] == "Business goal"
    assert portfolio[0]["contract_hash"] == "unversioned"
    assert portfolio[0]["single_execution_id"] == "one"
    assert portfolio[0]["top_attention"] is None
    assert summary["assessment_coverage"] == 0
    assert summarize_goal_assurance([], {}, {})[0] == []
