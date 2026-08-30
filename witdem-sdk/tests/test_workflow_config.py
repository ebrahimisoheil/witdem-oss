from __future__ import annotations

from pathlib import Path

import witdem_sdk
from witdem_sdk import configure
from witdem_sdk._contract import load_project_config


def _write_config(root: Path) -> Path:
    (root / "workflows").mkdir()
    definition = root / "workflows/answer.yml"
    definition.write_text(
        """version: 1
id: answer-flow
name: Answer flow
framework: langgraph
match:
  runtime_names: [langgraph/answer]
stages:
  - id: answer
    name: Answer
    nodes:
      - id: respond
        name: Respond
        match:
          names: [respond]
outcomes: []
""",
        encoding="utf-8",
    )
    project = root / "witdem.yml"
    project.write_text(
        """version: 1
service:
  name: answer-service
  runtime: langgraph/answer
telemetry:
  mode: disabled
workflows:
  - id: answer-flow
    definition: workflows/answer.yml
default_workflow: answer-flow
""",
        encoding="utf-8",
    )
    return project


def test_sdk_loads_workflows_and_emits_definition_as_nested_yaml_shape(tmp_path: Path, monkeypatch) -> None:
    path = _write_config(tmp_path)
    config = load_project_config(path, required=True)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(witdem_sdk, "submit_record", sent.append)

    with configure(config_path=str(path)) as client, client.execution(execution_id="execution-1"):
        pass

    assert config is not None
    assert set(config.workflow_definitions) == {"answer-flow"}
    definition = next(record for record in sent if record["name"] == "workflow.definition")
    attributes = definition["attributes"]
    assert isinstance(attributes, dict)
    assert isinstance(attributes["definition"], dict)
    assert attributes["workflow_id"] == "answer-flow"
