"""Vendor-neutral application contracts and project configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from witdem_sdk._errors import WitdemSDKError

_CONFIG_ENV = "WITDEM_CONFIG"
_DEFAULT_RELATIVE_PATH = Path(".witdem/witdem.yaml")

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EvaluationDirection = Literal["higher_is_better", "lower_is_better"]


class NamedDescriptionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    description: str | None = None


class BusinessValueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    tone: Literal["success", "warning", "failure", "neutral"] | None = None


class ResultDefinitionSpec(NamedDescriptionSpec):
    values: dict[str, str | BusinessValueSpec] = Field(default_factory=dict)


class DecisionDefinitionSpec(NamedDescriptionSpec):
    values: dict[str, str | BusinessValueSpec] = Field(default_factory=dict)


class InvestigationSpec(BaseModel):
    """An authored place to begin investigating a failed requirement."""

    model_config = ConfigDict(extra="forbid")

    workflow: NonEmptyText | None = None
    stage: NonEmptyText
    node: NonEmptyText | None = None


class RequirementFailureSpec(BaseModel):
    """Contract-authored diagnostic copy; Witdem never invents this text."""

    model_config = ConfigDict(extra="forbid")

    label: NonEmptyText
    description: str | None = None
    investigate: InvestigationSpec | None = None


class GoalRequirementSpec(NamedDescriptionSpec):
    failure: RequirementFailureSpec


class ProductGoalDefinitionSpec(NamedDescriptionSpec):
    requirements: dict[str, GoalRequirementSpec] = Field(min_length=1)


class EvaluationDefinitionSpec(NamedDescriptionSpec):
    unit: str | None = None
    target: float | bool | str | None = None
    direction: EvaluationDirection | None = None

    @model_validator(mode="after")
    def validate_target(self) -> EvaluationDefinitionSpec:
        if self.direction is not None and self.target is None:
            raise ValueError("direction requires target")
        return self


class MetricDefinitionSpec(NamedDescriptionSpec):
    unit: str | None = None


class DimensionDefinitionSpec(NamedDescriptionSpec):
    pass


class DescriptiveContractSpec(BaseModel):
    """A v2 business definition populated explicitly through ``Witdem.report``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    id: NonEmptyText
    name: NonEmptyText
    description: str | None = None
    result: ResultDefinitionSpec
    decision: DecisionDefinitionSpec | None = None
    goal: ProductGoalDefinitionSpec
    evaluations: dict[str, EvaluationDefinitionSpec] = Field(default_factory=dict)
    metrics: dict[str, MetricDefinitionSpec] = Field(default_factory=dict)
    dimensions: dict[str, DimensionDefinitionSpec] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_named_catalogs(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        raw = dict(value)
        for catalog_name in ("evaluations", "metrics", "dimensions"):
            catalog = raw.get(catalog_name)
            if not isinstance(catalog, Mapping):
                continue
            normalized: dict[str, Any] = {}
            for key, item in catalog.items():
                if isinstance(item, str):
                    normalized[str(key)] = {"name": item}
                elif isinstance(item, Mapping):
                    normalized[str(key)] = {"name": str(item.get("name") or key), **dict(item)}
                else:
                    raise ValueError(f"{catalog_name}.{key} must be a name or metadata mapping")
            raw[catalog_name] = normalized
        return raw

    @property
    def product_goal(self) -> ProductGoalDefinitionSpec:
        """Canonical runtime vocabulary behind the shorter public ``goal`` field."""

        return self.goal


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonEmptyText
    description: str | None = None


class TelemetrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    mode: Literal["auto", "existing", "disabled"] = "auto"
    capture_content: bool = False


class WitdemProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    service: ServiceSpec
    telemetry: TelemetrySpec = Field(default_factory=TelemetrySpec)
    contracts: dict[str, DescriptiveContractSpec] = Field(default_factory=dict)
    default_contract: str | None = None
    workflows: list[str] = Field(default_factory=list)
    default_workflow: str | None = None

    _workflow_definitions: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def workflow_definitions(self) -> dict[str, Any]:
        return dict(self._workflow_definitions)

    @field_validator("contracts", mode="before")
    @classmethod
    def validate_contract_catalog(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return value
        validated: dict[str, DescriptiveContractSpec] = {}
        for name, item in value.items():
            contract_id = str(name).strip()
            if not contract_id:
                raise ValueError("contract ids cannot be empty")
            try:
                contract = DescriptiveContractSpec.model_validate(item)
            except ValidationError as exc:
                details = "; ".join(
                    f"contracts.{contract_id}.{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                    for error in exc.errors(include_url=False)
                )
                raise ValueError(details) from exc
            if contract.id != contract_id:
                raise ValueError(f"contract catalog key {contract_id!r} does not match file id {contract.id!r}")
            validated[contract_id] = contract
        return validated

    @model_validator(mode="after")
    def validate_defaults(self) -> WitdemProjectConfig:
        if self.default_contract is None and len(self.contracts) == 1:
            self.default_contract = next(iter(self.contracts))
        if self.default_contract is not None and self.default_contract not in self.contracts:
            available = ", ".join(self.contracts) or "none"
            raise ValueError(
                f"default_contract {self.default_contract!r} does not exist; available contracts: {available}"
            )
        return self


@dataclass(frozen=True)
class ContractResult:
    contract: str
    application_status: str
    artifact_valid: bool
    expected_status: Any | None
    observed_status: Any | None
    decision_correct: bool | None
    product_goal_achieved: bool
    attributes: dict[str, Any]


def contract_definition(
    config: WitdemProjectConfig, name: str, spec: DescriptiveContractSpec
) -> tuple[str, dict[str, Any]]:
    """Return stable, non-executable business metadata for one contract."""

    definition: dict[str, Any] = {
        "protocol_version": "2.0",
        "service": {
            "name": config.service.name,
            "description": config.service.description,
        },
        "contract": {"id": name, "name": spec.name, "description": spec.description},
        "result": spec.result.model_dump(mode="json"),
        "decision": spec.decision.model_dump(mode="json") if spec.decision else None,
        "product_goal": spec.goal.model_dump(mode="json"),
        "evaluations": [{"key": key, **item.model_dump(mode="json")} for key, item in spec.evaluations.items()],
        "metrics": [{"key": key, **item.model_dump(mode="json")} for key, item in spec.metrics.items()],
        "dimensions": [{"key": key, **item.model_dump(mode="json")} for key, item in spec.dimensions.items()],
    }
    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest(), definition


def evaluate_contract(*args: Any, **kwargs: Any) -> ContractResult:
    """Reject the removed v1 expression evaluator with an actionable error."""

    raise WitdemSDKError(
        "expression contracts were removed with configuration v1; "
        "report named contract facts with Witdem.report(...)"
    )


def discover_config(start: Path | None = None) -> Path | None:
    explicit = os.getenv(_CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for relative in (Path("witdem.yml"), Path("witdem.yaml"), _DEFAULT_RELATIVE_PATH):
            candidate = directory / relative
            if candidate.is_file():
                return candidate
    return None


def _read_yaml(path: Path, *, kind: str) -> Mapping[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{kind} file {path} must contain a YAML mapping")
    return loaded


def _load_contract_definitions(raw: Mapping[str, Any], project_path: Path) -> dict[str, Any]:
    references = raw.get("contracts", [])
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        raise ValueError("contracts must be a YAML list of contract file paths")
    definitions: dict[str, Any] = {}
    for reference in references:
        path = (project_path.parent / reference).resolve()
        loaded = _read_yaml(path, kind="contract")
        contract_id = str(loaded.get("id") or "").strip()
        if not contract_id:
            raise ValueError(f"contract file {reference!r} requires id")
        if contract_id in definitions:
            raise ValueError(f"duplicate contract id {contract_id!r}")
        definitions[contract_id] = dict(loaded)
    return definitions


def _validate_investigation_links(config: WitdemProjectConfig) -> None:
    if not config._workflow_definitions:
        return
    for contract in config.contracts.values():
        for requirement_id, requirement in contract.goal.requirements.items():
            hint = requirement.failure.investigate
            if hint is None:
                continue
            workflow_id = hint.workflow or config.default_workflow
            if not workflow_id:
                raise ValueError(
                    f"contract {contract.id!r} requirement {requirement_id!r} must name an investigation workflow"
                )
            workflow = config._workflow_definitions.get(workflow_id)
            if workflow is None:
                raise ValueError(
                    f"contract {contract.id!r} requirement {requirement_id!r} references unknown workflow "
                    f"{workflow_id!r}"
                )
            stage = next((item for item in workflow.stages if item.get("id") == hint.stage), None)
            if stage is None:
                raise ValueError(
                    f"contract {contract.id!r} requirement {requirement_id!r} references unknown stage "
                    f"{hint.stage!r}"
                )
            if hint.node is not None and hint.node not in {
                str(node.get("id")) for node in stage.get("nodes", []) if isinstance(node, Mapping)
            }:
                raise ValueError(
                    f"contract {contract.id!r} requirement {requirement_id!r} references node {hint.node!r} "
                    f"outside stage {hint.stage!r}"
                )


def load_project_config(path: str | Path | None = None, *, required: bool = False) -> WitdemProjectConfig | None:
    resolved = Path(path).expanduser().resolve() if path is not None else discover_config()
    if resolved is None:
        if required:
            raise WitdemSDKError("no .witdem/witdem.yaml found; run 'witdem-sdk init'")
        return None
    try:
        raw = _read_yaml(resolved, kind="project")
        project = dict(raw)
        project["contracts"] = _load_contract_definitions(raw, resolved)
        config = WitdemProjectConfig.model_validate(project)

        from witdem_sdk._workflow import load_workflow_definitions

        config._workflow_definitions = load_workflow_definitions(raw, resolved)
        if config.default_workflow is None and len(config._workflow_definitions) == 1:
            config.default_workflow = next(iter(config._workflow_definitions))
        if config.default_workflow and config.default_workflow not in config._workflow_definitions:
            raise ValueError(f"default_workflow {config.default_workflow!r} is not registered")
        _validate_investigation_links(config)
        return config
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            problems = []
            for error in exc.errors(include_url=False):
                location = ".".join(str(part) for part in error["loc"])
                message = str(error["msg"]).removeprefix("Value error, ")
                problems.append(f"  - {location or 'document'}: {message}")
            detail = "\n" + "\n".join(problems)
        else:
            detail = f": {exc}"
        raise WitdemSDKError(f"invalid Witdem configuration at {resolved}{detail}") from exc
