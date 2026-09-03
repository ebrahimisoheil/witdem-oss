from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from witdem_sdk._contract import contract_definition, load_project_config
from witdem_sdk._errors import WitdemSDKError
from witdem_sdk.cli import init_project, main


def _write_project(root: Path, *, default_contract: str | None = None) -> Path:
    contract_dir = root / "contracts"
    contract_dir.mkdir(parents=True)
    (contract_dir / "support.yml").write_text(
        """version: 2
id: support
name: Support resolution
description: Resolve one customer request.
result:
  name: Support result
  values:
    answered: A useful answer was returned.
    escalated:
      description: Human assistance was requested.
      tone: warning
decision:
  name: Support route
  values:
    answer: Answer directly.
    escalate: Escalate to a person.
goal:
  name: Correct support resolution
  requirements:
    useful_answer:
      name: The answer is useful
      failure:
        label: No useful answer was returned
        investigate:
          stage: answer
          node: respond
    correct_route:
      name: The expected route was selected
      failure:
        label: The selected route was incorrect
        description: Compare the observed and expected support routes.
        investigate:
          stage: routing
evaluations:
  reference_coverage:
    name: Reference answer coverage
    target: 0.8
    direction: higher_is_better
    unit: ratio
metrics:
  retries:
    name: Retry count
    unit: retries
dimensions:
  case_id:
    name: Case ID
""",
        encoding="utf-8",
    )
    default = f"default_contract: {default_contract}\n" if default_contract else ""
    project = root / "witdem.yml"
    project.write_text(
        f"""version: 2
service:
  name: support-agent
telemetry:
  mode: disabled
contracts: [contracts/support.yml]
{default}""",
        encoding="utf-8",
    )
    return project


def _client(path: Path, monkeypatch: pytest.MonkeyPatch):
    import witdem_sdk

    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    client.project_config = load_project_config(path, required=True)
    records: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    for method in ("event", "evaluation", "decision", "outcome", "metric"):
        monkeypatch.setattr(
            client,
            method,
            lambda *args, _method=method, **kwargs: records.append((_method, args, kwargs)),
        )
    return client, records


def test_sdk_help_is_available_without_the_analytics_package(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "validate" in capsys.readouterr().out


def test_v2_loads_contracts_from_dedicated_files(tmp_path: Path) -> None:
    config = load_project_config(_write_project(tmp_path), required=True)
    assert config is not None
    assert config.version == 2
    assert config.default_contract == "support"
    assert config.contracts["support"].name == "Support resolution"
    assert set(config.contracts["support"].goal.requirements) == {"useful_answer", "correct_route"}


def test_v1_is_intentionally_rejected(tmp_path: Path) -> None:
    path = tmp_path / "witdem.yml"
    path.write_text("version: 1\nservice: {name: old}\ncontracts: []\n", encoding="utf-8")
    with pytest.raises(WitdemSDKError, match="version.*Input should be 2"):
        load_project_config(path, required=True)


def test_inline_contracts_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "witdem.yml"
    path.write_text("version: 2\nservice: {name: inline}\ncontracts: {support: {version: 2}}\n", encoding="utf-8")
    with pytest.raises(WitdemSDKError, match="list of contract file paths"):
        load_project_config(path, required=True)


def test_contract_ids_must_be_unique(tmp_path: Path) -> None:
    path = _write_project(tmp_path)
    contract = tmp_path / "contracts" / "support.yml"
    duplicate = tmp_path / "contracts" / "duplicate.yml"
    duplicate.write_text(contract.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "contracts: [contracts/support.yml]",
            "contracts: [contracts/support.yml, contracts/duplicate.yml]",
        ),
        encoding="utf-8",
    )
    with pytest.raises(WitdemSDKError, match="duplicate contract id 'support'"):
        load_project_config(path, required=True)


def test_report_derives_goal_result_and_authored_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, records = _client(_write_project(tmp_path), monkeypatch)
    reported = client.report(
        result="answered",
        result_valid=True,
        decision="escalate",
        expected_decision="answer",
        requirements={"useful_answer": True, "correct_route": False},
        evaluations={"reference_coverage": 0.92},
        metrics={"retries": 1},
        dimensions={"case_id": "case-1"},
    )
    assert reported.product_goal_achieved is False
    assert reported.attributes["closest_blocker"] == "correct_route"
    assert reported.attributes["failed_requirement_ids"] == ["correct_route"]
    definition = records[0][1][1]
    assert definition["protocol_version"] == "2.0"
    assert definition["product_goal"]["requirements"]["correct_route"]["failure"]["label"] == (
        "The selected route was incorrect"
    )
    requirement_record = next(
        item
        for item in records
        if item[0] == "evaluation" and item[2].get("attributes", {}).get("requirement_id") == "correct_route"
    )
    assert requirement_record[2]["label"] == "failed"
    assert requirement_record[2]["attributes"]["passed"] is False
    assert requirement_record[2]["attributes"]["investigation_stage"] == "routing"


def test_unknown_requirement_marks_evidence_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(_write_project(tmp_path), monkeypatch)
    reported = client.report(
        result="answered",
        requirements={"useful_answer": True, "correct_route": None},
    )
    assert reported.product_goal_achieved is False
    assert reported.attributes["unknown_requirement_ids"] == ["correct_route"]
    assert reported.attributes["decision_evidence_sufficient"] is False


def test_report_requires_exact_declared_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(_write_project(tmp_path), monkeypatch)
    with pytest.raises(ValueError, match="missing: correct_route"):
        client.report(result="answered", requirements={"useful_answer": True})
    with pytest.raises(ValueError, match="undeclared: invented"):
        client.report(
            result="answered",
            requirements={"useful_answer": True, "correct_route": True, "invented": True},
        )


def test_report_rejects_undeclared_values_and_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(_write_project(tmp_path), monkeypatch)
    requirements = {"useful_answer": True, "correct_route": True}
    with pytest.raises(ValueError, match="result 'unknown'.*not declared"):
        client.report(result="unknown", requirements=requirements)
    with pytest.raises(ValueError, match="dimensions not declared.*tier"):
        client.report(result="answered", requirements=requirements, dimensions={"tier": "gold"})


def test_contract_definition_hash_is_stable_and_vendor_neutral(tmp_path: Path) -> None:
    config = load_project_config(_write_project(tmp_path), required=True)
    assert config is not None
    first_hash, definition = contract_definition(config, "support", config.contracts["support"])
    second_hash, _ = contract_definition(config, "support", config.contracts["support"])
    assert first_hash == second_hash
    assert "runtime" not in definition["service"]
    assert "framework" not in str(definition).casefold()


def test_cuad_v2_contract_has_resolvable_authored_diagnostics() -> None:
    repository = Path(__file__).resolve().parents[2]
    project = repository / "examples" / "integrations" / "cuad" / "witdem.yml"
    config = load_project_config(project, required=True)
    assert config is not None
    assert config.version == 2
    assert config.default_contract == "review"
    assert config.default_workflow == "contract-review"

    contract = config.contracts["review"]
    workflow = config.workflow_definitions["contract-review"]
    locations = {
        requirement_id: (
            requirement.failure.label,
            requirement.failure.investigate.stage if requirement.failure.investigate else None,
            requirement.failure.investigate.node if requirement.failure.investigate else None,
        )
        for requirement_id, requirement in contract.goal.requirements.items()
    }
    assert locations == {
        "evidence_retrieved": ("No relevant contract evidence was retrieved", "evidence", "retrieve"),
        "structured_review": ("The generated review was not valid structured JSON", "validation", "validate"),
    }
    assert workflow.version == 2


def test_contract_rejects_an_unresolvable_investigation_node(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "support.yml").write_text(
        """version: 2
id: support
name: Support
stages:
  - id: answer
    name: Answer
    nodes:
      - id: respond
        name: Respond
  - id: routing
    name: Routing
    nodes:
      - id: route
        name: Route
""",
        encoding="utf-8",
    )
    contract = tmp_path / "contracts" / "support.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace("node: respond", "node: missing"),
        encoding="utf-8",
    )
    project.write_text(
        project.read_text(encoding="utf-8") + "workflows: [workflows/support.yml]\n",
        encoding="utf-8",
    )
    with pytest.raises(WitdemSDKError, match="node 'missing'.*outside stage 'answer'"):
        load_project_config(project, required=True)


def test_default_contract_must_exist(tmp_path: Path) -> None:
    with pytest.raises(WitdemSDKError, match="default_contract 'missing' does not exist"):
        load_project_config(_write_project(tmp_path, default_contract="missing"), required=True)


def test_init_and_validate_create_a_clean_v2_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = init_project(tmp_path, service_name="example-agent")
    assert target.read_text(encoding="utf-8").startswith("version: 2")
    assert (tmp_path / ".witdem" / "contracts" / "application-run.yml").is_file()
    skill = tmp_path / ".witdem" / "skills" / "witdem" / "SKILL.md"
    assert skill.is_file()
    assert skill.read_text(encoding="utf-8").startswith("---\nname: witdem\n")
    assert (skill.parent / "agents" / "openai.yaml").is_file()
    config = load_project_config(target, required=True)
    assert config is not None
    assert config.default_contract == "application_run"
    assert main(["validate", "--config", str(target)]) == 0
    assert "Valid Witdem configuration" in capsys.readouterr().out


def test_init_can_expose_the_canonical_skill_to_agents(tmp_path: Path) -> None:
    assert main([
        "init",
        "--directory",
        str(tmp_path),
        "--service-name",
        "example-agent",
        "--expose-agent-skill",
    ]) == 0

    link = tmp_path / ".agents" / "skills" / "witdem"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / ".witdem" / "skills" / "witdem").resolve()
    assert (link / "SKILL.md").is_file()


def test_init_protects_and_force_replaces_a_modified_skill(tmp_path: Path) -> None:
    skill = tmp_path / ".witdem" / "skills" / "witdem" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("developer-owned\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="already exists"):
        init_project(tmp_path, service_name="example-agent")
    assert skill.read_text(encoding="utf-8") == "developer-owned\n"

    init_project(tmp_path, service_name="example-agent", force=True)
    assert skill.read_text(encoding="utf-8").startswith("---\nname: witdem\n")


def test_init_refuses_a_symlinked_canonical_skill_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    skill_dir = tmp_path / ".witdem" / "skills" / "witdem"
    skill_dir.parent.mkdir(parents=True)
    skill_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SystemExit, match="must be a directory"):
        init_project(tmp_path, service_name="example-agent", force=True)
    assert not tuple(outside.iterdir())
