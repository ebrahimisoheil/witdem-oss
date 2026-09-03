from __future__ import annotations

from pathlib import Path

import witdem_sdk
from witdem_sdk import configure
from witdem_sdk._contract import load_project_config


def _write_config(root: Path) -> Path:
    (root / "workflows").mkdir()
    (root / "workflows" / "answer.yml").write_text(
        """version: 2
id: answer-flow
name: Answer flow
match:
  execution_names: [Answer request]
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
        """version: 2
service:
  name: answer-service
telemetry:
  mode: disabled
workflows: [workflows/answer.yml]
""",
        encoding="utf-8",
    )
    return project


def test_sdk_loads_vendor_neutral_workflow_and_emits_definition(tmp_path: Path, monkeypatch) -> None:
    path = _write_config(tmp_path)
    config = load_project_config(path, required=True)
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(witdem_sdk, "submit_record", sent.append)

    with (
        configure(config_path=str(path), runtime="test-runtime") as client,
        client.execution(execution_id="execution-1"),
    ):
        pass

    assert config is not None
    assert config.default_workflow == "answer-flow"
    definition = next(record for record in sent if record["name"] == "workflow.definition")
    attributes = definition["attributes"]
    assert isinstance(attributes, dict)
    assert attributes["workflow_id"] == "answer-flow"
    assert "framework" not in str(attributes).casefold()
