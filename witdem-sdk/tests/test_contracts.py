from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from witdem_sdk._contract import evaluate_contract, load_project_config
from witdem_sdk.cli import init_project, main


def _write_config(path: Path) -> Path:
    target = path / ".witdem" / "witdem.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(
        """version: 1
service:
  name: support-agent
  runtime: langgraph
contracts:
  - name: support_case
    status_field: status
    result:
      name: Support answer
      description: The answer returned to the customer.
      required_fields:
        - answer
    decision:
      name: Support route
      description: Whether the agent used the expected support route.
      expected: true
      observed_field: evaluation.trajectory_match
    product_goal:
      name: Correct support answer
      description: Return an answer using the expected support route.
      subject_field: case_id
    dimensions:
      case_id: case_id
""",
        encoding="utf-8",
    )
    return target


def test_contract_evaluates_complete_business_meaning(tmp_path: Path) -> None:
    config = load_project_config(_write_config(tmp_path), required=True)
    assert config is not None
    result = evaluate_contract(
        "support_case",
        config.contracts["support_case"],
        {"status": "completed", "answer": "Invoice total is $1.98", "evaluation": {"trajectory_match": True}},
    )
    assert result.application_status == "completed"
    assert result.artifact_valid is True
    assert result.observed_status is True
    assert result.decision_correct is True
    assert result.product_goal_achieved is True
    assert result.attributes["closest_blocker"] == "none"


def test_contract_failure_is_reported_not_inferred_as_success(tmp_path: Path) -> None:
    config = load_project_config(_write_config(tmp_path), required=True)
    assert config is not None
    result = evaluate_contract(
        "support_case",
        config.contracts["support_case"],
        {"status": "completed", "answer": "A response", "evaluation": {"trajectory_match": False}},
    )
    assert result.artifact_valid is True
    assert result.observed_status is False
    assert result.decision_correct is False
    assert result.product_goal_achieved is False
    assert result.attributes["closest_blocker"] == "application contract not achieved"


def test_decision_correctness_is_unknown_without_an_independent_expectation(tmp_path: Path) -> None:
    path = tmp_path / "witdem.yaml"
    path.write_text(
        """version: 1
service:
  name: research-agent
contracts:
  - name: research_report
    application_outcome:
      status: $.status
    artifact:
      valid:
        non_empty: $.report
    decision:
      name: Editorial decision
      observed: $.status
    product_goal:
      achieved: $.approved
""",
        encoding="utf-8",
    )
    config = load_project_config(path, required=True)
    assert config is not None

    result = evaluate_contract(
        "research_report",
        config.contracts["research_report"],
        {"status": "revision_limit_reached", "report": "Draft", "approved": False},
    )

    assert result.expected_status is None
    assert result.observed_status == "revision_limit_reached"
    assert result.decision_correct is None
    assert result.product_goal_achieved is False
    assert result.attributes["decision_correct"] is None


def test_contract_context_supports_dataclasses_and_coalesce() -> None:
    from witdem_sdk._contract import evaluate, result_context

    @dataclass
    class Result:
        score: float | None

    context = result_context(Result(score=None))
    assert evaluate({"coalesce": ["$.score", 0.0]}, context) == 0.0


def test_compact_contract_supplies_standard_goal_defaults(tmp_path: Path) -> None:
    path = tmp_path / "witdem.yaml"
    path.write_text(
        """version: 1
service:
  name: example
contracts:
  - name: answer
    result:
      required_fields:
        - answer
    metrics:
      - name: answer_characters
        field: answer
        measure: length
        unit: characters
""",
        encoding="utf-8",
    )
    config = load_project_config(path, required=True)
    assert config is not None
    spec = config.contracts["answer"]
    result = evaluate_contract("answer", spec, {"answer": "done"})
    assert result.application_status == "completed"
    assert result.artifact_valid is True
    assert result.product_goal_achieved is True
    assert spec.metrics[0].name == "answer_characters"


def test_client_complete_emits_canonical_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import witdem_sdk

    config_path = _write_config(tmp_path)
    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    client.project_config = load_project_config(config_path, required=True)
    records: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(client, "event", lambda *args, **kwargs: records.append(("event", args, kwargs)))
    monkeypatch.setattr(client, "evaluation", lambda *args, **kwargs: records.append(("evaluation", args, kwargs)))
    monkeypatch.setattr(client, "decision", lambda *args, **kwargs: records.append(("decision", args, kwargs)))
    monkeypatch.setattr(client, "outcome", lambda *args, **kwargs: records.append(("outcome", args, kwargs)))

    completed = client.complete(
        {"status": "completed", "answer": "done", "evaluation": {"trajectory_match": True}},
        contract="support_case",
    )

    assert completed.product_goal_achieved is True
    assert [record[0] for record in records] == [
        "event",
        "event",
        "evaluation",
        "decision",
        "outcome",
        "outcome",
    ]
    assert records[0][1] == ("contract.definition", records[0][1][1])
    assert records[2][1] == ("Support answer validity",)
    assert records[4][1] == ("application_outcome",)
    assert records[5][1] == ("product_goal",)
    assert records[5][2]["attributes"]["artifact_valid"] is True
    assert records[5][2]["attributes"]["goal_subject"] is None
    assert records[5][2]["attributes"]["contract_hash"]

    client.complete(
        {"status": "completed", "answer": "done again", "evaluation": {"trajectory_match": True}},
        contract="support_case",
    )
    assert sum(record[0] == "event" and record[1][0] == "contract.definition" for record in records) == 1


def test_client_complete_forwards_configured_attributes_to_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import witdem_sdk

    config_path = _write_config(tmp_path)
    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    client.project_config = load_project_config(config_path, required=True)
    outcomes: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(client, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(client, "evaluation", lambda *args, **kwargs: None)
    monkeypatch.setattr(client, "decision", lambda *args, **kwargs: None)
    monkeypatch.setattr(client, "outcome", lambda *args, **kwargs: outcomes.append((args, kwargs)))

    client.complete(
        {
            "case_id": "acct-e1",
            "status": "completed",
            "answer": "done",
            "evaluation": {"trajectory_match": True},
        }
    )

    assert outcomes[-1][1]["attributes"]["case_id"] == "acct-e1"


def test_init_and_validate_create_a_loadable_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = init_project(tmp_path, service_name="example-agent", runtime="langgraph")
    assert target == tmp_path / ".witdem" / "witdem.yaml"
    generated = target.read_text(encoding="utf-8")
    assert not any(token in generated for token in ("$.", "{", "}", "choose:", "coalesce:"))
    assert "result:" in generated
    assert "artifact:" not in generated
    assert load_project_config(target, required=True).service.name == "example-agent"  # type: ignore[union-attr]
    assert main(["validate", "--config", str(target)]) == 0
    assert "Valid Witdem configuration" in capsys.readouterr().out


def test_metadata_only_contract_reports_explicit_business_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import witdem_sdk

    path = tmp_path / "witdem.yaml"
    path.write_text(
        """version: 1
service:
  name: support-agent
contracts:
  - name: support_case
    description: Resolve one customer support request.
    result:
      name: Support result
      description: What was delivered to the customer.
      values:
        answered: A usable answer was returned.
        escalated: Human assistance was requested.
    decision:
      name: Support route
      description: The route selected by the agent.
      values:
        database_lookup: Business data was queried.
        escalation: Human review was requested.
    product_goal:
      name: Correct support resolution
      description: Return a correct answer through the appropriate route.
    evaluations:
      reference_coverage:
        name: Reference answer coverage
        description: Share of the expected answer covered.
        unit: ratio
        target: 0.8
        direction: higher_is_better
    dimensions:
      question_type:
        name: Question type
""",
        encoding="utf-8",
    )
    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    client.project_config = load_project_config(path, required=True)
    records: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    for method in ("event", "evaluation", "decision", "outcome", "metric"):
        monkeypatch.setattr(
            client,
            method,
            lambda *args, _method=method, **kwargs: records.append((_method, args, kwargs)),
        )

    reported = client.report(
        contract="support_case",
        result="answered",
        result_valid=True,
        decision="database_lookup",
        expected_decision="database_lookup",
        product_goal_achieved=True,
        evaluations={"reference_coverage": 0.92},
        dimensions={"question_type": "billing"},
    )

    assert reported.product_goal_achieved is True
    assert reported.decision_correct is True
    assert reported.attributes["question_type"] == "billing"
    definition = records[0][1][1]
    assert definition["result"]["values"]["answered"] == "A usable answer was returned."
    assert definition["dimensions"][0]["key"] == "question_type"
    assert any(
        kind == "evaluation"
        and args == ("Reference answer coverage",)
        and kwargs["score"] == 0.92
        for kind, args, kwargs in records
    )
    assert records[-1][0] == "outcome"
    assert records[-1][1] == ("product_goal",)


def test_metadata_only_contract_rejects_expression_style_completion(tmp_path: Path) -> None:
    target = init_project(tmp_path, service_name="example-agent", runtime="langgraph")
    config = load_project_config(target, required=True)
    assert config is not None
    with pytest.raises(Exception, match="Witdem.report"):
        evaluate_contract("application_run", config.contracts["application_run"], {"result": "done"})
