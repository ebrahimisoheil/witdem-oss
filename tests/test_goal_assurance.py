from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from witdem.analytics.assurance import (
    accumulate_goal_assurance,
    finalize_goal_assurance,
    goal_assurance_state,
    project_goal_assurance,
    project_goal_portfolio,
    summarize_goal_assurance,
)
from witdem.analytics.core import Evaluation, Event, Outcome
from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.repository.analytics_repository import AnalyticsRepository
from witdem.analytics.repository.state import FilterState
from witdem.ingest import live_db


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


@pytest.mark.parametrize("achieved,assurance", [(False, "assured"), (True, "assured"),
                                             (True, "needs_attention"), (True, None), (None, None)])
def test_bundle_projection_matches_actual_serving_portfolio(tmp_path, monkeypatch, achieved, assurance):
    database = tmp_path / "portfolio.duckdb"
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / "fixtures/evidence-bundle-v1-oldest.json").read_text()
    )
    execution_id = bundle.execution.execution_id
    start = bundle.execution.started_at
    events = [Event(execution_id=execution_id, timestamp=start + timedelta(seconds=index),
                    type="event", name="contract.definition", payload={
                        "contract_hash": "contract-" + str(index), "contract_name": "Review",
                        "product_goal": {"name": "Old goal" if index == 0 else "Review safely"},
                        "prompt": "NEVER_PERSIST_THIS",
                    }) for index in range(2)]
    outcomes = [Outcome(execution_id=execution_id, name="product_goal", timestamp=start,
                        attributes={"product_goal_achieved": achieved, "assurance_status": assurance,
                                    "prompt": "NEVER_PERSIST_THIS"})]
    evaluations = [Evaluation(execution_id=execution_id, evaluation_id=str(index), name="Quality", source="test",
                              score=score, attributes={"evaluation_key": "quality", "target": 0.5,
                                                       "direction": "higher_is_better", "prompt": "NEVER_PERSIST_THIS"})
                   for index, score in enumerate((0.9, 0.2))]
    bundle = bundle.model_copy(update={"events": events, "outcomes": outcomes, "evaluations": evaluations})
    live_db.publish_transformed_bundle(bundle.execution, bundle.operations, bundle.links,
                                       [*events, *evaluations, *outcomes])
    reader = AnalyticsRepository(database)
    try:
        actual = finalize_goal_assurance(*project_goal_assurance(bundle))
        assert actual == reader.goal_assurance()
        assert "NEVER_PERSIST_THIS" not in repr(actual)
        assert actual[0][0]["goal_name"] == "Review safely"
        if achieved is True:
            assert actual[0][0]["evaluations"][0]["average_score"] == 0.2
    finally:
        reader.close()


@pytest.mark.parametrize("reported", [False, True])
@pytest.mark.parametrize("definition_kind", ["versioned", "unversioned", "absent"])
def test_contract_context_preserves_unreported_population(tmp_path, monkeypatch, reported, definition_kind):
    database = tmp_path / "context.duckdb"
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / "fixtures/evidence-bundle-v1-oldest.json").read_text()
    )
    execution_id = bundle.execution.execution_id
    start = bundle.execution.started_at
    latest = start + timedelta(seconds=10)
    definition = {"contract_name": "Selected", "product_goal": {"name": "Review"},
                  "prompt": "NEVER_EXPOSE_CONTEXT_CONTENT", "response": "NEVER_EXPOSE_CONTEXT_CONTENT"}
    if definition_kind == "versioned":
        definition["contract_hash"] = "selected-contract"
    # Reverse observation order deliberately: selection must not use the last
    # event in the bundle. No-hash latest definitions must not retain old hashes.
    events = [] if definition_kind == "absent" else [
        Event(execution_id=execution_id, timestamp=latest, type="event", name="contract.definition",
              payload=definition),
        Event(execution_id=execution_id, timestamp=start, type="event", name="contract.definition",
              payload={"contract_hash": "stale-contract", "contract_name": "Stale"}),
    ]
    outcomes = [Outcome(execution_id=execution_id, timestamp=start, name="product_goal",
                        attributes={"product_goal_achieved": True})] if reported else []
    bundle = bundle.model_copy(update={"events": events, "evaluations": [], "outcomes": outcomes})
    original = bundle.model_dump_json()
    projection = project_goal_portfolio(bundle)
    assert bundle.model_dump_json() == original
    assert (projection.groups, projection.counts) == project_goal_assurance(bundle)
    assert projection.counts["reported_runs"] == int(reported)
    assert bool(projection.groups) is reported
    assert "NEVER_EXPOSE_CONTEXT_CONTENT" not in repr(projection)
    if definition_kind == "absent":
        assert projection.contract is None
    else:
        assert projection.contract.contract_hash == ("selected-contract" if definition_kind == "versioned" else None)
        assert projection.contract.contract_name == "Selected"
        assert projection.contract.observed_at == latest
    live_db.publish_transformed_bundle(bundle.execution, bundle.operations, bundle.links, [*events, *outcomes])
    reader = AnalyticsRepository(database)
    try:
        assert finalize_goal_assurance(projection.groups, projection.counts) == reader.goal_assurance()
        selected = reader.execution_rows(FilterState(contract_hash="selected-contract"), limit=None)
        assert len(selected) == int(definition_kind == "versioned")
        assert reader.execution_rows(FilterState(contract_hash="stale-contract"), limit=None) == []
        contracts = reader.contract_definitions()
        assert len(contracts) == int(definition_kind == "versioned")
        if contracts:
            assert contracts[0]["run_count"] == 1
            assert contracts[0]["contract_hash"] == projection.contract.contract_hash
    finally:
        reader.close()


def test_portfolio_context_rejects_foreign_execution_records():
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / "fixtures/evidence-bundle-v1-oldest.json").read_text()
    )
    event = Event(execution_id="another-execution", timestamp=bundle.execution.started_at,
                  type="event", name="contract.definition", payload={"contract_hash": "foreign"})
    with pytest.raises(ValueError, match="another execution"):
        project_goal_portfolio(bundle.model_copy(update={"events": [event]}))
