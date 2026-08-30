"""Declared workflow templates and execution-to-template projection.

The workflow template is application-owned structure. Runtime operations are
evidence projected onto that structure; they never create presentation stages.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from witdem.config import storage_root

WORKFLOW_MANIFEST_SCHEMA_VERSION = 1
WORKFLOW_COMPILER_VERSION = "1"
WORKFLOW_PROJECTOR_VERSION = "2"


class WorkflowMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_names: list[str] = Field(default_factory=list)
    service_names: list[str] = Field(default_factory=list)
    execution_names: list[str] = Field(default_factory=list)


class NodeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)


class NodeDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str = Field(min_length=1)
    type: Literal["next", "branch", "convergence", "fallback"] | None = None
    route: str | None = None
    label: str | None = None


class RetrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    via: str | None = None
    max_attempts: int | None = Field(default=None, ge=2)


class OperationDeclaration(BaseModel):
    """Optional vendor-neutral analytics expectations for a workflow node."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    expects: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = value.strip().casefold()
        from witdem.analytics.operations import OPERATION_FAMILIES

        if normalized not in OPERATION_FAMILIES and not normalized.startswith("x."):
            raise ValueError("operation type must be a well-known type or use x.<namespace>.<name>")
        return normalized

    @field_validator("expects", "optional")
    @classmethod
    def normalize_measurements(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip().casefold() for value in values]
        if any(not value for value in normalized):
            raise ValueError("operation measurement names cannot be empty")
        return list(dict.fromkeys(normalized))


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    kind: str | None = None
    match: NodeMatch = Field(default_factory=NodeMatch)
    depends_on: list[NodeDependency] = Field(default_factory=list)
    retry: RetrySpec | None = None
    operation: OperationDeclaration | None = None

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: Any) -> Any:
        if value is None:
            return []
        return [{"node": item} if isinstance(item, str) else item for item in value]


class WorkflowStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    nodes: list[WorkflowNode] = Field(min_length=1)


class WorkflowTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: str = Field(alias="from", min_length=1)
    target: str = Field(alias="to", min_length=1)
    type: Literal["next", "branch", "convergence", "loop", "fallback"] = "next"
    label: str | None = None
    route: str | None = None


class WorkflowOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    from_nodes: list[str] = Field(default_factory=list, alias="from")


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str | None = None
    evaluations: list[str] = Field(min_length=1)


class WorkflowDefinition(BaseModel):
    """Framework-neutral presentation contract stored as ordinary YAML."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal[1] = 1
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    framework: str | None = None
    match: WorkflowMatch = Field(default_factory=WorkflowMatch)
    ignore_observed: list[NodeMatch] = Field(default_factory=list)
    stages: list[WorkflowStage] = Field(min_length=1)
    outcomes: list[WorkflowOutcome] = Field(default_factory=list)
    evaluation_suites: dict[str, EvaluationSuite] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> WorkflowDefinition:
        node_ids = [node.id for node in self.nodes]
        stage_ids = [stage.id for stage in self.stages]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("workflow node ids must be unique")
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("workflow stage ids must be unique")
        for stage in self.stages:
            unknown_stages = set(stage.depends_on) - set(stage_ids)
            if unknown_stages:
                raise ValueError(f"stage {stage.id!r} depends on unknown stages: {', '.join(sorted(unknown_stages))}")
        for node in self.nodes:
            unknown = {dependency.node for dependency in node.depends_on} - set(node_ids)
            if unknown:
                raise ValueError(f"node {node.id!r} depends on unknown nodes: {', '.join(sorted(unknown))}")
            if node.retry and node.retry.via and node.retry.via not in set(node_ids):
                raise ValueError(f"node {node.id!r} retries via unknown node {node.retry.via!r}")
        _validate_dag(self.nodes)
        for outcome in self.outcomes:
            unknown = set(outcome.from_nodes) - set(node_ids)
            if unknown:
                raise ValueError(f"outcome {outcome.id!r} references unknown nodes: {', '.join(sorted(unknown))}")
        for suite_id, suite in self.evaluation_suites.items():
            if not suite_id.strip():
                raise ValueError("evaluation suite ids cannot be empty")
            if suite.workflow is not None and suite.workflow != self.id:
                raise ValueError(
                    f"evaluation suite {suite_id!r} references workflow {suite.workflow!r}, expected {self.id!r}"
                )
        return self

    @property
    def nodes(self) -> list[WorkflowNode]:
        return [node for stage in self.stages for node in stage.nodes]

    @property
    def transitions(self) -> list[WorkflowTransition]:
        transitions: list[WorkflowTransition] = []
        for node in self.nodes:
            for dependency in node.depends_on:
                edge_type = dependency.type or ("convergence" if len(node.depends_on) > 1 else "next")
                transitions.append(
                    WorkflowTransition.model_validate(
                        {
                            "from": dependency.node,
                            "to": node.id,
                            "type": edge_type,
                            "route": dependency.route,
                            "label": dependency.label,
                        }
                    )
                )
            if node.retry and node.retry.via:
                transitions.append(
                    WorkflowTransition.model_validate(
                        {
                            "from": node.retry.via,
                            "to": node.id,
                            "type": "loop",
                            "label": "retry",
                        }
                    )
                )
        return transitions

    def api_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", by_alias=True)
        payload["stages"] = [
            {
                **stage.model_dump(mode="json", exclude={"nodes"}),
                "nodes": [node.id for node in stage.nodes],
            }
            for stage in self.stages
        ]
        payload["nodes"] = [node.model_dump(mode="json") for node in self.nodes]
        payload["transitions"] = [edge.model_dump(mode="json", by_alias=True) for edge in self.transitions]
        return payload

    @property
    def template_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


class WorkflowReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class WorkflowRegistry:
    def __init__(self, definitions: Iterable[WorkflowDefinition] = ()) -> None:
        self.definitions = {definition.id: definition for definition in definitions}

    def get(self, workflow_id: str) -> WorkflowDefinition | None:
        return self.definitions.get(workflow_id)

    def match(self, execution: Mapping[str, Any]) -> WorkflowDefinition | None:
        explicit = _first(execution, "workflow_id", "witdem.workflow.id")
        if explicit and str(explicit) in self.definitions:
            return self.definitions[str(explicit)]
        attributes = _mapping(execution.get("attributes"))
        runtime = str(execution.get("runtime_id") or attributes.get("witdem.runtime") or "").casefold()
        service = str(attributes.get("service.name") or "").casefold()
        name = str(
            execution.get("display_name")
            or attributes.get("witdem.execution.name")
            or attributes.get("execution.name")
            or ""
        ).casefold()
        matches = [
            definition
            for definition in self.definitions.values()
            if (
                runtime in _folded(definition.match.runtime_names)
                or service in _folded(definition.match.service_names)
                or name in _folded(definition.match.execution_names)
            )
        ]
        return matches[0] if len(matches) == 1 else None


def discover_project_config(start: Path | None = None) -> Path | None:
    explicit = os.getenv("WITDEM_CONFIG")
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for relative in (Path("witdem.yml"), Path("witdem.yaml"), Path(".witdem/witdem.yaml")):
            candidate = directory / relative
            if candidate.is_file():
                return candidate
    return None


def load_registry(path: str | Path | None = None) -> WorkflowRegistry:
    resolved = Path(path).expanduser().resolve() if path else discover_project_config()
    if resolved is None:
        return WorkflowRegistry()
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    references = raw.get("workflows", []) if isinstance(raw, Mapping) else []
    if isinstance(references, Mapping):
        references = [{"id": key, **dict(value)} for key, value in references.items()]
    definitions: list[WorkflowDefinition] = []
    for item in references:
        reference = WorkflowReference.model_validate(item)
        definition_path = (resolved.parent / reference.definition).resolve()
        definition = WorkflowDefinition.model_validate(yaml.safe_load(definition_path.read_text(encoding="utf-8")))
        if definition.id != reference.id:
            raise ValueError(f"workflow reference {reference.id!r} points to definition with id {definition.id!r}")
        definitions.append(definition)
    return WorkflowRegistry(definitions)


def _dependency_order(definition: WorkflowDefinition) -> list[str]:
    dependencies = {node.id: {item.node for item in node.depends_on} for node in definition.nodes}
    order: list[str] = []
    while len(order) < len(dependencies):
        ready = sorted(
            node_id for node_id, required in dependencies.items() if node_id not in order and required <= set(order)
        )
        if not ready:  # validation normally prevents this; keep the compiler defensive.
            raise ValueError(f"workflow {definition.id!r} has no valid dependency order")
        order.extend(ready)
    return order


def _normalized_layout(definition: WorkflowDefinition) -> dict[str, dict[str, float]]:
    """Return stable viewport-independent coordinates for browser fitting."""

    order = _dependency_order(definition)
    level: dict[str, int] = {}
    for node_id in order:
        node = next(item for item in definition.nodes if item.id == node_id)
        level[node_id] = 0 if not node.depends_on else 1 + max(level[item.node] for item in node.depends_on)
    columns: dict[int, list[str]] = defaultdict(list)
    for node_id in order:
        columns[level[node_id]].append(node_id)
    width = max(columns, default=0) or 1
    result: dict[str, dict[str, float]] = {}
    for column, node_ids in columns.items():
        denominator = max(1, len(node_ids) - 1)
        for index, node_id in enumerate(node_ids):
            result[node_id] = {
                "x": column / width,
                "y": 0.5 if len(node_ids) == 1 else index / denominator,
            }
    return result


def compiled_manifest(definition: WorkflowDefinition) -> dict[str, Any]:
    api = definition.api_dict()
    return {
        "schema_version": WORKFLOW_MANIFEST_SCHEMA_VERSION,
        "compiler_version": WORKFLOW_COMPILER_VERSION,
        "workflow_id": definition.id,
        "template_hash": definition.template_hash,
        "definition": api,
        "match_index": {
            "workflow": definition.match.model_dump(mode="json"),
            "nodes": {node.id: node.match.model_dump(mode="json") for node in definition.nodes},
        },
        "dependency_order": _dependency_order(definition),
        "layouts": {
            "logic": _normalized_layout(definition),
            "goal": {
                stage.id: {"x": index / max(1, len(definition.stages) - 1), "y": 0.5}
                for index, stage in enumerate(definition.stages)
            },
        },
    }


def workflow_manifest_path(definition: WorkflowDefinition, root: Path | None = None) -> Path:
    base = root or storage_root()
    return base / "compiled" / "workflows" / definition.id / f"{definition.template_hash}.json"


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(dict(value), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def compile_definition(
    definition: WorkflowDefinition,
    *,
    force: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    target = workflow_manifest_path(definition, root)
    expected = compiled_manifest(definition)
    current: Any = None
    if target.is_file():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
    valid = current == expected
    if force or not valid:
        _atomic_json(target, expected)
    return {
        "workflow_id": definition.id,
        "template_hash": definition.template_hash,
        "path": str(target),
        "status": "current" if valid and not force else "compiled",
    }


def compile_registry(
    path: str | Path | None = None,
    *,
    check: bool = False,
    force: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Compile configured YAML workflows into disposable hashed manifests."""

    registry = load_registry(path)
    results: list[dict[str, Any]] = []
    stale = False
    for definition in registry.definitions.values():
        target = workflow_manifest_path(definition, root)
        expected = compiled_manifest(definition)
        current: Any = None
        if target.is_file():
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = None
        valid = current == expected
        if not valid:
            stale = True
            if not check:
                result = compile_definition(definition, root=root)
            else:
                result = {
                    "workflow_id": definition.id,
                    "template_hash": definition.template_hash,
                    "path": str(target),
                    "status": "stale",
                }
        elif force and not check:
            result = compile_definition(definition, force=True, root=root)
        else:
            result = {
                "workflow_id": definition.id,
                "template_hash": definition.template_hash,
                "path": str(target),
                "status": "current" if valid else "stale",
            }
        results.append(result)
    return {
        "status": "stale" if check and stale else "ok",
        "compiler_version": WORKFLOW_COMPILER_VERSION,
        "workflows": results,
    }


def definition_from_record(records: Sequence[Mapping[str, Any]]) -> WorkflowDefinition | None:
    for record in reversed(records):
        if record.get("name") != "workflow.definition":
            continue
        attributes = _mapping(record.get("attributes"))
        definition = attributes.get("definition")
        if isinstance(definition, Mapping):
            try:
                return WorkflowDefinition.model_validate(definition)
            except ValueError:
                # Definitions are immutable execution evidence. Older records
                # may use a superseded schema, so keep looking for a usable
                # declaration instead of breaking replay for the whole run.
                continue
    return None


def project_execution(
    definition: WorkflowDefinition,
    *,
    execution: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Project observed operations onto a stable declared template."""

    observed = [dict(item) for item in graph.get("nodes", []) if isinstance(item, Mapping)]
    by_id: dict[str, dict[str, Any]] = {}
    for item in observed:
        by_id[str(item.get("id"))] = item
        span_id = _mapping(item.get("attributes")).get("technical.span_id")
        if span_id:
            by_id[str(span_id)] = item
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_to_declared: dict[str, str] = {}
    for item in observed:
        matching = [node for node in definition.nodes if _node_matches(node, item)]
        if len(matching) == 1:
            node_id = matching[0].id
            matches[node_id].append(item)
            observed_to_declared[str(item.get("id"))] = node_id
            span_id = _mapping(item.get("attributes")).get("technical.span_id")
            if span_id:
                observed_to_declared[str(span_id)] = node_id

    # A model call is evidence owned by its nearest declared step. It remains
    # a node only when the template explicitly declares and matches it.
    owned_models: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in observed:
        if str(item.get("kind")) != "model" or str(item.get("id")) in observed_to_declared:
            continue
        parent = str(item.get("parent_operation_id") or item.get("parent") or "")
        visited: set[str] = set()
        while parent and parent not in visited:
            visited.add(parent)
            owner = observed_to_declared.get(parent)
            if owner:
                owned_models[owner].append(item)
                break
            parent_item = by_id.get(parent)
            parent = str((parent_item or {}).get("parent_operation_id") or (parent_item or {}).get("parent") or "")

    projected_nodes = [
        _project_node(node, matches.get(node.id, []), owned_models.get(node.id, [])) for node in definition.nodes
    ]
    projected_by_id = {node["id"]: node for node in projected_nodes}
    stage_nodes: dict[str, list[dict[str, Any]]] = {
        stage.id: [projected_by_id[node.id] for node in stage.nodes] for stage in definition.stages
    }
    stages = []
    for stage in definition.stages:
        nodes = stage_nodes[stage.id]
        states = {str(node["state"]) for node in nodes}
        state = (
            "failed"
            if "failed" in states
            else "recovered"
            if "recovered" in states
            else "completed"
            if states - {"inactive"}
            else "inactive"
        )
        stages.append(
            {
                **stage.model_dump(mode="json", exclude={"nodes"}),
                "nodes": [node.id for node in stage.nodes],
                "state": state,
                "active_nodes": sum(node["state"] != "inactive" for node in nodes),
                "duration_seconds": _sum_known(nodes, "duration_seconds"),
                "known_cost": _sum_known(nodes, "known_cost"),
                "total_tokens": _sum_known(nodes, "total_tokens"),
            }
        )

    declared_edges = {(edge.source, edge.target) for edge in definition.transitions}
    observed_edges: set[tuple[str, str]] = set()
    for edge in graph.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        source = observed_to_declared.get(str(edge.get("source")))
        target = observed_to_declared.get(str(edge.get("target")))
        if source and target and source != target:
            observed_edges.add((source, target))
    unexpected_operations = [
        {
            "id": str(item.get("id")),
            "name": str(item.get("display_name") or item.get("name") or "Observed operation"),
            "kind": str(item.get("kind") or "operation"),
        }
        for item in observed
        if str(item.get("id")) not in observed_to_declared
        and str(item.get("kind")) not in {"model", "workflow", "pipeline", "agent"}
        and not any(_observation_matches(rule, item) for rule in definition.ignore_observed)
    ]
    unexpected_edges = sorted(observed_edges - declared_edges)
    return {
        "workflow": {
            **definition.api_dict(),
            "template_hash": definition.template_hash,
            "layouts": compiled_manifest(definition)["layouts"],
        },
        "execution": dict(execution),
        "stages": stages,
        "nodes": projected_nodes,
        "transitions": [edge.model_dump(mode="json", by_alias=True) for edge in definition.transitions],
        "outcomes": [outcome.model_dump(mode="json", by_alias=True) for outcome in definition.outcomes],
        "discrepancies": {
            "unexpected_operations": unexpected_operations,
            "unexpected_transitions": [{"from": source, "to": target} for source, target in unexpected_edges],
        },
    }


def _project_node(
    node: WorkflowNode,
    attempts: Sequence[Mapping[str, Any]],
    models: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    statuses = [str(item.get("status") or "").casefold() for item in attempts]
    latest_status = statuses[-1] if statuses else ""
    state = (
        "inactive"
        if not attempts
        else "failed"
        if latest_status in {"error", "failed"}
        else "recovered"
        if set(statuses[:-1]) & {"error", "failed"} or (node.retry is not None and len(attempts) > 1)
        else "completed"
    )
    provider_values = {str(item.get("provider")) for item in models if item.get("provider")}
    model_values = {str(item.get("model")) for item in models if item.get("model")}
    route = next(
        (
            value
            for item in reversed(attempts)
            for key in ("witdem.route", "route", "emitted_route")
            if (value := _mapping(item.get("attributes")).get(key)) is not None
        ),
        None,
    )
    attributed = [*attempts, *models]
    cost_eligible = [
        item
        for item in attributed
        if str(item.get("kind")) == "model"
        or (
            str(item.get("kind")) == "tool"
            and (
                item.get("known_cost") is not None
                or _mapping(item.get("attributes")).get("cost_unavailable_reason") is not None
                or _mapping(item.get("attributes")).get("cost_applicable") is True
            )
        )
    ]
    cost_measured = [item for item in cost_eligible if isinstance(item.get("known_cost"), (int, float))]
    token_eligible = [item for item in attributed if str(item.get("kind")) == "model"]
    token_measured = [item for item in token_eligible if isinstance(item.get("total_tokens"), (int, float))]
    return {
        **node.model_dump(mode="json"),
        "state": state,
        "attempts": len(attempts),
        "duration_seconds": _active_wall_seconds(attributed),
        "known_cost": _sum_known(cost_measured, "known_cost"),
        "total_tokens": _sum_known(token_measured, "total_tokens"),
        "cost_eligible_operations": len(cost_eligible),
        "cost_measured_operations": len(cost_measured),
        "token_eligible_operations": len(token_eligible),
        "token_measured_operations": len(token_measured),
        "providers": sorted(provider_values),
        "models": sorted(model_values),
        "emitted_route": route,
        "observations": [dict(item) for item in attempts],
        "model_calls": [dict(item) for item in models],
    }


def _node_matches(node: WorkflowNode, item: Mapping[str, Any]) -> bool:
    match = node.match
    names = match.names or [node.id]
    return _observation_matches(match, item, names=names)


def _observation_matches(
    match: NodeMatch,
    item: Mapping[str, Any],
    *,
    names: Sequence[str] | None = None,
) -> bool:
    expected_names = list(names) if names is not None else match.names
    attributes = _mapping(item.get("attributes"))
    candidates = {
        str(item.get("name") or "").casefold(),
        str(item.get("runtime_name") or "").casefold(),
        str(item.get("display_name") or "").casefold(),
        str(item.get("operation_key") or "").casefold(),
        str(item.get("semantic_stage") or "").casefold(),
        str(attributes.get("haystack.component.name") or "").casefold(),
        str(attributes.get("langgraph.node") or attributes.get("langgraph_node") or "").casefold(),
        str(attributes.get("langchain.run.name") or "").casefold(),
    }
    if expected_names and not (candidates & _folded(expected_names)):
        return False
    if match.kinds and str(item.get("kind") or "").casefold() not in _folded(match.kinds):
        return False
    return all(attributes.get(key) == value for key, value in match.attributes.items())


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _folded(values: Sequence[str]) -> set[str]:
    return {str(value).casefold() for value in values if str(value)}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    attributes = _mapping(mapping.get("attributes"))
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
        if attributes.get(key) is not None:
            return attributes[key]
    return None


def _sum_known(items: Sequence[Mapping[str, Any]], field: str) -> float | None:
    values = [float(item[field]) for item in items if isinstance(item.get(field), (int, float))]
    return sum(values) if values else None


def _active_wall_seconds(items: Sequence[Mapping[str, Any]]) -> float | None:
    intervals: list[tuple[datetime, datetime]] = []
    for item in items:
        start = item.get("start") or item.get("started_at")
        end = item.get("end") or item.get("ended_at")
        try:
            start_value = (
                start if isinstance(start, datetime) else datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            )
            end_value = end if isinstance(end, datetime) else datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        intervals.append((start_value, end_value))
    if intervals:
        intervals.sort()
        total = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                total += max(0.0, (end - start).total_seconds())
                start, end = next_start, next_end
        return total + max(0.0, (end - start).total_seconds())
    # Older replay records may have only durations. Preserve a truthful value
    # while avoiding model/child double counting by preferring declared attempts.
    attempt_total = _sum_known(items, "duration_seconds")
    return attempt_total


def _validate_dag(nodes: Sequence[WorkflowNode]) -> None:
    dependencies = {node.id: {item.node for item in node.depends_on} for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"depends_on relationships must form a DAG; cycle includes {node_id!r}")
        visiting.add(node_id)
        for dependency in dependencies[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in dependencies:
        visit(node_id)
