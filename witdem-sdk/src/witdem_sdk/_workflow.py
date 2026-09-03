"""Workflow declaration models used by the lightweight SDK boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowDefinition(BaseModel):
    """Validated without importing a framework-specific graph type."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    match: dict[str, list[str]] = Field(default_factory=dict)
    ignore_observed: list[dict[str, Any]] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(min_length=1)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_minimum_shape(self) -> WorkflowDefinition:
        nodes = [node for stage in self.stages for node in stage.get("nodes", [])]
        node_ids = [str(node.get("id") or "") for node in nodes if isinstance(node, Mapping)]
        stage_ids = [str(stage.get("id") or "") for stage in self.stages]
        if any(not value for value in node_ids + stage_ids):
            raise ValueError("every workflow node and stage requires an id")
        if len(node_ids) != len(set(node_ids)) or len(stage_ids) != len(set(stage_ids)):
            raise ValueError("workflow node and stage ids must be unique")
        if not node_ids:
            raise ValueError("workflow stages must contain at least one node")
        return self

    @property
    def template_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


def load_workflow_definitions(raw: Mapping[str, Any], project_path: Path) -> dict[str, WorkflowDefinition]:
    references = raw.get("workflows", [])
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        raise ValueError("workflows must be a YAML list of workflow file paths")
    result: dict[str, WorkflowDefinition] = {}
    for reference in references:
        path = (project_path.parent / reference).resolve()
        definition = WorkflowDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if definition.id in result:
            raise ValueError(f"duplicate workflow id {definition.id!r}")
        result[definition.id] = definition
    return result
