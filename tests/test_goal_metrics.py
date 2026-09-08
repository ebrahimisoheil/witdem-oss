from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from witdem.analytics.core import Outcome
from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.goal_metrics import (
    GoalContribution,
    goal_contribution,
    project_goal_contribution,
    summarize_goal_contributions,
)
from witdem.analytics.repository import AnalyticsRepository
from witdem.ingest import live_db


def test_goal_means_use_measured_counts_and_do_not_infer_success():
    missing = goal_contribution(None, duration_seconds=10, complete_cost=100, complete_tokens=1000)
    partial = goal_contribution({"product_goal_achieved": True}, duration_seconds=10,
                                complete_cost=None, complete_tokens=None)
    zero = goal_contribution({"product_goal_achieved": True}, duration_seconds=0,
                             complete_cost=0, complete_tokens=0)
    summary = summarize_goal_contributions([missing, partial, zero])
    assert (summary.total_runs, summary.reported_runs, summary.achieved_runs) == (3, 2, 2)
    assert summary.time_per_achieved_goal == 5
    assert summary.cost_per_achieved_goal == summary.tokens_per_achieved_goal == 0
    assert summary.cost_measured_achieved_runs == summary.token_measured_achieved_runs == 1
    assert summary.coverage == 2 / 3
    assert summary.cost_coverage == 0.5
    assert summarize_goal_contributions([]).cost_per_achieved_goal is None


@pytest.mark.parametrize("attributes, expected", [
    ({}, (0, 0, 0, 0, 0, 0)),
    ({"product_goal_achieved": "true", "decision_correct": 1}, (0, 0, 0, 0, 0, 0)),
    ({"observed_status": "accepted", "expected_status": "rejected"}, (0, 1, 0, 0, 0, 0)),
    ({"observed_status": "rejected", "expected_status": "accepted"}, (0, 0, 1, 0, 0, 0)),
    ({"observed_status": "escalated", "expected_status": "accepted"}, (0, 0, 0, 1, 0, 0)),
    ({"product_goal_achieved": True, "targeted_research_required": True,
      "required_path_observed": True, "decision_correct": True}, (1, 0, 0, 0, 1, 1)),
])
def test_explicit_goal_semantics(attributes, expected):
    contribution = goal_contribution(attributes, duration_seconds=None, complete_cost=None, complete_tokens=None)
    assert contribution.reported_runs == 1
    assert (contribution.decision_correct_runs, contribution.false_acceptances, contribution.false_rejections,
            contribution.escalation_errors, contribution.targeted_research_runs,
            contribution.targeted_research_successes) == expected


@pytest.mark.parametrize("goal, measured", [(None, False), ({}, False),
    ({"product_goal_achieved": False}, True), ({"product_goal_achieved": True}, False),
    ({"product_goal_achieved": True, "decision_correct": True}, True)])
def test_bundle_contribution_matches_actual_oss_serving_reader(tmp_path, monkeypatch, goal, measured):
    database = tmp_path / "goals.duckdb"
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / "fixtures/evidence-bundle-v1-oldest.json").read_text()
    )
    outcomes = [Outcome(outcome_id="old-goal", execution_id=bundle.execution.execution_id,
                        name="product_goal", timestamp=bundle.execution.started_at,
                        attributes={"product_goal_achieved": True})] if goal is not None else []
    if goal is not None:
        outcomes.append(Outcome(outcome_id="latest-goal", execution_id=bundle.execution.execution_id,
                                name="product_goal", timestamp=bundle.execution.started_at + timedelta(seconds=1),
                                attributes=goal))
    operation = bundle.operations[0].model_copy(update={"kind": "model", "attributes": {
        "provider": "test", "model": "test", "witdem.operation.type": "text_generation",
        **({"cost_usd": 0, "total_tokens": 0} if measured else {}),
    }})
    bundle = bundle.model_copy(update={"outcomes": outcomes, "operations": [operation]})
    live_db.publish_transformed_bundle(bundle.execution, bundle.operations, bundle.links,
                                       [*bundle.events, *bundle.evaluations, *outcomes])
    reader = AnalyticsRepository(database)
    try:
        expected = reader.get_product_goal_summary()
        # Evidence input order must not make an older goal supersede a correction.
        shuffled = bundle.model_copy(update={"outcomes": list(reversed(outcomes))})
        actual = summarize_goal_contributions([project_goal_contribution(shuffled)])
        assert actual == expected
        assert actual.reported_runs == int(bool(goal))
    finally:
        reader.close()


def test_contribution_contract_rejects_unknown_versions_and_content():
    with pytest.raises(ValidationError):
        GoalContribution(schema_version="2")
    with pytest.raises(ValidationError):
        GoalContribution(prompt="must not leave projection")
    with pytest.raises(ValidationError):
        GoalContribution(reported_runs=2)
    with pytest.raises(ValidationError):
        GoalContribution(cost_measured_achieved_runs=1)
    with pytest.raises(ValidationError):
        GoalContribution(total_runs=True)
