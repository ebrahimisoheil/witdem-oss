"""Declarative application-result contracts for frictionless SDK enrichment."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from witdem_sdk._errors import WitdemSDKError

_CONFIG_ENV = "WITDEM_CONFIG"
_DEFAULT_RELATIVE_PATH = Path(".witdem/witdem.yaml")


class ApplicationOutcomeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Any = "completed"


class ArtifactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Result"
    description: str | None = None
    valid: Any


class DecisionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Application decision"
    description: str | None = None
    outcomes: dict[str, str] = Field(default_factory=dict)
    expected: Any | None = None
    observed: Any
    correct: Any | None = None
    reason: Any | None = None


class ProductGoalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Product goal"
    description: str | None = None
    subject: Any | None = None
    achieved: Any
    evidence_sufficient: Any = True
    required_path_observed: Any = True
    closest_blocker: Any = "none"
    threshold: Any | None = None
    threshold_margin: Any | None = None


class EvaluationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    unit: str | None = None
    target: Any | None = None
    direction: str | None = None
    score: Any | None = None
    label: Any | None = None
    value: Any | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    unit: str | None = None
    value: Any
    attributes: dict[str, Any] = Field(default_factory=dict)


class NamedDescriptionSpec(BaseModel):
    """Human-facing metadata with no evaluation or extraction behavior."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None


class ResultDefinitionSpec(NamedDescriptionSpec):
    values: dict[str, str] = Field(default_factory=dict)


class DecisionDefinitionSpec(NamedDescriptionSpec):
    values: dict[str, str] = Field(default_factory=dict)


class ProductGoalDefinitionSpec(NamedDescriptionSpec):
    pass


class EvaluationDefinitionSpec(NamedDescriptionSpec):
    unit: str | None = None
    target: float | bool | str | None = None
    direction: str | None = None


class MetricDefinitionSpec(NamedDescriptionSpec):
    unit: str | None = None


class DimensionDefinitionSpec(NamedDescriptionSpec):
    pass


class DescriptiveContractSpec(BaseModel):
    """A metadata-only business glossary.

    Values are supplied explicitly through ``Witdem.report``.  This model is
    intentionally unable to contain paths, expressions, or extraction logic.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    result: ResultDefinitionSpec
    decision: DecisionDefinitionSpec | None = None
    product_goal: ProductGoalDefinitionSpec
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


def _field(path: Any) -> Any:
    if path is None or not isinstance(path, str):
        return path
    return path if path.startswith("$") else f"$.{path}"


def _required_fields(fields: Any) -> Any:
    if not isinstance(fields, list) or not fields:
        raise ValueError("result.required_fields must contain at least one field name")
    checks = [{"non_empty": _field(item)} for item in fields]
    return checks[0] if len(checks) == 1 else {"all": checks}


def _dbt_contract_to_internal(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compile the public metadata-only contract into the private evaluator model."""

    raw = dict(value)
    dbt_keys = {"status_field", "dimensions"}
    result = raw.get("result")
    decision = raw.get("decision")
    goal = raw.get("product_goal")
    metrics = raw.get("metrics")
    is_dbt_style = bool(dbt_keys & set(raw)) or (
        isinstance(result, Mapping) and bool({"required_fields", "validity_field"} & set(result))
    ) or (
        isinstance(decision, Mapping)
        and bool({"expected_field", "observed_field", "correctness_field"} & set(decision))
    ) or isinstance(metrics, list) and any(
        isinstance(item, Mapping) and "field" in item for item in metrics
    )
    if not is_dbt_style:
        return raw

    internal: dict[str, Any] = {"description": raw.get("description")}
    if status_field := raw.get("status_field"):
        internal["application_outcome"] = {"status": _field(status_field)}

    if not isinstance(result, Mapping):
        raise ValueError("result must define required_fields or validity_field")
    if result.get("validity_field") is not None:
        artifact_valid = _field(result["validity_field"])
    else:
        artifact_valid = _required_fields(result.get("required_fields"))
    internal["artifact"] = {
        "name": str(result.get("name") or "Result"),
        "description": result.get("description"),
        "valid": artifact_valid,
    }

    if decision is None:
        internal["decision"] = {
            "name": "Result validity",
            "expected": True,
            "observed": "$.witdem.artifact_valid",
        }
    elif isinstance(decision, Mapping):
        expected = (
            _field(decision["expected_field"])
            if decision.get("expected_field") is not None
            else decision.get("expected", True)
        )
        if decision.get("observed_field") is None:
            observed = "$.witdem.artifact_valid"
        else:
            observed = _field(decision["observed_field"])
        internal["decision"] = {
            "name": str(decision.get("name") or "Application decision"),
            "description": decision.get("description"),
            "outcomes": dict(decision.get("outcomes") or {}),
            "expected": expected,
            "observed": observed,
        }
        if decision.get("correctness_field") is not None:
            internal["decision"]["correct"] = _field(decision["correctness_field"])
        if decision.get("reason_field") is not None:
            internal["decision"]["reason"] = _field(decision["reason_field"])
    else:
        raise ValueError("decision must be a mapping")

    if goal is None:
        internal["product_goal"] = {
            "achieved": _automatic_goal(),
            "closest_blocker": _automatic_blocker(),
        }
    elif isinstance(goal, Mapping):
        internal["product_goal"] = {
            "name": str(goal.get("name") or "Product goal"),
            "description": goal.get("description"),
            "subject": _field(goal["subject_field"]) if goal.get("subject_field") else None,
            "achieved": (
                _field(goal["achieved_field"])
                if goal.get("achieved_field") is not None
                else _automatic_goal()
            ),
            "evidence_sufficient": _field(goal.get("evidence_sufficient_field", True)),
            "required_path_observed": _field(goal.get("required_path_field", True)),
            "closest_blocker": (
                _field(goal["blocker_field"])
                if goal.get("blocker_field") is not None
                else _automatic_blocker()
            ),
        }
        if goal.get("threshold_field") is not None:
            internal["product_goal"]["threshold"] = _field(goal["threshold_field"])
        if goal.get("margin_field") is not None:
            internal["product_goal"]["threshold_margin"] = _field(goal["margin_field"])
    else:
        raise ValueError("product_goal must be a mapping")

    dimensions = raw.get("dimensions") or {}
    if not isinstance(dimensions, Mapping):
        raise ValueError("dimensions must map output names to result fields")
    internal["attributes"] = {str(name): _field(path) for name, path in dimensions.items()}

    internal_evaluations: list[dict[str, Any]] = []
    for item in raw.get("evaluations") or []:
        if not isinstance(item, Mapping) or not item.get("name"):
            raise ValueError("each evaluation requires a name")
        evaluation: dict[str, Any] = {
            "name": str(item["name"]),
            "description": item.get("description"),
            "unit": item.get("unit"),
            "target": item.get("target"),
            "direction": item.get("direction"),
        }
        if item.get("score_field") is not None:
            evaluation["score"] = _field(item["score_field"])
        elif item.get("field") is not None:
            evaluation["value"] = _field(item["field"])
        else:
            raise ValueError("each evaluation requires field or score_field")
        internal_evaluations.append(evaluation)
    internal["evaluations"] = internal_evaluations

    internal_metrics: list[dict[str, Any]] = []
    for item in metrics or []:
        if not isinstance(item, Mapping) or not item.get("name") or not item.get("field"):
            raise ValueError("each metric requires name and field")
        expression: Any = _field(item["field"])
        if item.get("measure") == "length":
            expression = {"length": expression}
        elif item.get("measure") not in {None, "value"}:
            raise ValueError("metric.measure must be 'value' or 'length'")
        metric: dict[str, Any] = {
            "name": str(item["name"]),
            "description": item.get("description"),
            "unit": item.get("unit"),
            "value": expression,
        }
        if item.get("unit") is not None:
            metric["attributes"] = {"unit": item["unit"]}
        internal_metrics.append(metric)
    internal["metrics"] = internal_metrics
    return internal


class ContractSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    application_outcome: ApplicationOutcomeSpec = Field(default_factory=ApplicationOutcomeSpec)
    artifact: ArtifactSpec
    decision: DecisionSpec
    product_goal: ProductGoalSpec
    attributes: dict[str, Any] = Field(default_factory=dict)
    evaluations: list[EvaluationSpec] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def expand_compact_contract(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        raw = _dbt_contract_to_internal(value)
        if "status" in raw and "application_outcome" not in raw:
            raw["application_outcome"] = {"status": raw.pop("status")}
        artifact = raw.get("artifact")
        if artifact is not None and not (isinstance(artifact, Mapping) and "valid" in artifact):
            raw["artifact"] = {"valid": artifact}
        if "goal" in raw and "product_goal" not in raw:
            goal = raw.pop("goal")
            if goal == "auto":
                goal = None
            if isinstance(goal, Mapping) and any(
                key in goal for key in ("achieved", "evidence", "path", "blocker", "threshold", "margin")
            ):
                raw["product_goal"] = {
                    "achieved": goal.get("achieved", _automatic_goal()),
                    "evidence_sufficient": goal.get("evidence", True),
                    "required_path_observed": goal.get("path", True),
                    "closest_blocker": goal.get("blocker", _automatic_blocker()),
                    "threshold": goal.get("threshold"),
                    "threshold_margin": goal.get("margin"),
                }
            elif goal is not None:
                raw["product_goal"] = {"achieved": goal, "closest_blocker": _automatic_blocker()}
        raw.setdefault(
            "product_goal",
            {"achieved": _automatic_goal(), "closest_blocker": _automatic_blocker()},
        )
        evaluations = raw.get("evaluations")
        if isinstance(evaluations, Mapping):
            raw["evaluations"] = [
                ({"name": str(name), **dict(spec)} if isinstance(spec, Mapping) else {"name": str(name), "value": spec})
                for name, spec in evaluations.items()
            ]
        metrics = raw.get("metrics")
        if isinstance(metrics, Mapping):
            raw["metrics"] = [
                (
                    {"name": str(name), **dict(spec)}
                    if isinstance(spec, Mapping) and "value" in spec
                    else {"name": str(name), "value": spec}
                )
                for name, spec in metrics.items()
            ]
        return raw


def _automatic_goal() -> dict[str, Any]:
    return {"all": ["$.witdem.artifact_valid", "$.witdem.decision_correct"]}


def _automatic_blocker() -> dict[str, Any]:
    return {
        "choose": {
            "when": {"all": ["$.witdem.artifact_valid", "$.witdem.decision_correct"]},
            "then": "none",
            "else": "application contract not achieved",
        }
    }


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    runtime: str | None = None


class TelemetrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    mode: str = "auto"
    capture_content: bool = False


class WitdemProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    service: ServiceSpec
    telemetry: TelemetrySpec = Field(default_factory=TelemetrySpec)
    contracts: dict[str, DescriptiveContractSpec | ContractSpec] = Field(default_factory=dict)
    default_contract: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_contract_catalog(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        raw = dict(value)
        contracts = raw.get("contracts")
        if isinstance(contracts, list):
            catalog: dict[str, Any] = {}
            for item in contracts:
                if not isinstance(item, Mapping) or not item.get("name"):
                    raise ValueError("each contract requires a name")
                contract = dict(item)
                name = str(contract.pop("name"))
                if name in catalog:
                    raise ValueError(f"duplicate contract name: {name}")
                catalog[name] = contract
            raw["contracts"] = catalog
            if catalog and not raw.get("default_contract"):
                raw["default_contract"] = next(iter(catalog))
        return raw


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
    config: WitdemProjectConfig, name: str, spec: DescriptiveContractSpec | ContractSpec
) -> tuple[str, dict[str, Any]]:
    """Return stable, non-executable business metadata for one contract."""

    if isinstance(spec, DescriptiveContractSpec):
        descriptive_definition: dict[str, Any] = {
            "protocol_version": "1.0",
            "service": {
                "name": config.service.name,
                "description": config.service.description,
                "runtime": config.service.runtime,
            },
            "contract": {"name": name, "description": spec.description},
            "result": spec.result.model_dump(mode="json"),
            "decision": spec.decision.model_dump(mode="json") if spec.decision else None,
            "product_goal": spec.product_goal.model_dump(mode="json"),
            "evaluations": [
                {"key": key, **item.model_dump(mode="json")}
                for key, item in spec.evaluations.items()
            ],
            "metrics": [
                {"key": key, **item.model_dump(mode="json")}
                for key, item in spec.metrics.items()
            ],
            "dimensions": [
                {"key": key, **item.model_dump(mode="json")}
                for key, item in spec.dimensions.items()
            ],
        }
        payload = json.dumps(
            descriptive_definition, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return sha256(payload.encode("utf-8")).hexdigest(), descriptive_definition

    definition: dict[str, Any] = {
        "protocol_version": "1.0",
        "service": {
            "name": config.service.name,
            "description": config.service.description,
            "runtime": config.service.runtime,
        },
        "contract": {"name": name, "description": spec.description},
        "result": {"name": spec.artifact.name, "description": spec.artifact.description},
        "decision": {
            "name": spec.decision.name,
            "description": spec.decision.description,
            "outcomes": spec.decision.outcomes,
        },
        "product_goal": {
            "name": spec.product_goal.name,
            "description": spec.product_goal.description,
        },
        "evaluations": [
            {
                "name": item.name,
                "description": item.description,
                "unit": item.unit,
                "target": item.target,
                "direction": item.direction,
            }
            for item in spec.evaluations
        ],
        "metrics": [
            {"name": item.name, "description": item.description, "unit": item.unit}
            for item in spec.metrics
        ],
        "dimensions": sorted(spec.attributes),
    }
    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest(), definition


def discover_config(start: Path | None = None) -> Path | None:
    explicit = os.getenv(_CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / _DEFAULT_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return None


def load_project_config(path: str | Path | None = None, *, required: bool = False) -> WitdemProjectConfig | None:
    resolved = Path(path).expanduser().resolve() if path is not None else discover_config()
    if resolved is None:
        if required:
            raise WitdemSDKError("no .witdem/witdem.yaml found; run 'witdem init'")
        return None
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        return WitdemProjectConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise WitdemSDKError(f"invalid Witdem configuration at {resolved}: {exc}") from exc


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def result_context(result: Any) -> dict[str, Any]:
    """Return the JSON-shaped context used by every contract expression."""

    plain_result = _plain(result)
    try:
        json.dumps(plain_result)
    except (TypeError, ValueError) as exc:
        raise WitdemSDKError(f"contract result is not JSON-serializable: {exc}") from exc
    return dict(plain_result) if isinstance(plain_result, Mapping) else {"result": plain_result}


def _path(root: Any, expression: str) -> Any:
    if expression == "$":
        return root
    if not expression.startswith("$."):
        return expression
    current = root
    for part in expression[2:].split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def evaluate(expression: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(expression, str):
        return _path(context, expression)
    if isinstance(expression, list):
        return [evaluate(item, context) for item in expression]
    if not isinstance(expression, Mapping):
        return expression
    if set(expression) == {"all"}:
        return all(bool(evaluate(item, context)) for item in expression["all"])
    if set(expression) == {"any"}:
        return any(bool(evaluate(item, context)) for item in expression["any"])
    if set(expression) == {"not"}:
        return not bool(evaluate(expression["not"], context))
    if set(expression) == {"exists"}:
        return evaluate(expression["exists"], context) is not None
    if set(expression) == {"non_empty"}:
        return bool(evaluate(expression["non_empty"], context))
    if set(expression) == {"length"}:
        value = evaluate(expression["length"], context)
        try:
            return len(value) if value is not None else 0
        except TypeError as exc:
            raise WitdemSDKError("contract length expression requires a sized value") from exc
    if set(expression) == {"equals"}:
        values = evaluate(expression["equals"], context)
        return isinstance(values, list) and len(values) == 2 and values[0] == values[1]
    if set(expression) == {"choose"}:
        choice = expression["choose"]
        if not isinstance(choice, Mapping) or "when" not in choice:
            raise WitdemSDKError("contract choose expression requires when, then, and else")
        branch = choice.get("then") if bool(evaluate(choice["when"], context)) else choice.get("else")
        return evaluate(branch, context)
    if set(expression) == {"coalesce"}:
        values = expression["coalesce"]
        if not isinstance(values, list):
            raise WitdemSDKError("contract coalesce expression requires a list")
        for item in values:
            value = evaluate(item, context)
            if value is not None:
                return value
        return None
    return {str(key): evaluate(value, context) for key, value in expression.items()}


def evaluate_contract(
    name: str, spec: DescriptiveContractSpec | ContractSpec, result: Any
) -> ContractResult:
    if isinstance(spec, DescriptiveContractSpec):
        raise WitdemSDKError(
            "metadata-only contracts require Witdem.report(...); "
            "Witdem.complete(...) requires an expression contract"
        )
    context = result_context(result)
    application_status = str(evaluate(spec.application_outcome.status, context) or "unknown")
    artifact_valid = bool(evaluate(spec.artifact.valid, context))
    context["witdem"] = {"artifact_valid": artifact_valid, "application_status": application_status}
    expected_status = evaluate(spec.decision.expected, context)
    observed_status = evaluate(spec.decision.observed, context)
    context["witdem"].update({"expected_status": expected_status, "observed_status": observed_status})
    if spec.decision.correct is not None:
        decision_correct = bool(evaluate(spec.decision.correct, context))
    elif expected_status is not None and observed_status is not None:
        decision_correct = expected_status == observed_status
    else:
        decision_correct = None
    context["witdem"]["decision_correct"] = decision_correct
    product_goal_achieved = bool(evaluate(spec.product_goal.achieved, context))
    attributes = {
        "contract_version": "1.0",
        "contract_name": name,
        "expected_status": expected_status,
        "observed_status": observed_status,
        "decision_correct": decision_correct,
        "product_goal_achieved": product_goal_achieved,
        "artifact_valid": artifact_valid,
        "decision_evidence_sufficient": bool(evaluate(spec.product_goal.evidence_sufficient, context)),
        "required_path_observed": bool(evaluate(spec.product_goal.required_path_observed, context)),
        "closest_blocker": evaluate(spec.product_goal.closest_blocker, context),
    }
    if spec.product_goal.subject is not None:
        attributes["goal_subject"] = evaluate(spec.product_goal.subject, context)
    if spec.decision.reason is not None:
        attributes["decision_reason"] = evaluate(spec.decision.reason, context)
    threshold = evaluate(spec.product_goal.threshold, context) if spec.product_goal.threshold is not None else None
    margin = (
        evaluate(spec.product_goal.threshold_margin, context)
        if spec.product_goal.threshold_margin is not None
        else None
    )
    if threshold is not None:
        attributes["threshold"] = threshold
    if margin is not None:
        attributes["threshold_margin"] = margin
    return ContractResult(
        contract=name,
        application_status=application_status,
        artifact_valid=artifact_valid,
        expected_status=expected_status,
        observed_status=observed_status,
        decision_correct=decision_correct,
        product_goal_achieved=product_goal_achieved,
        attributes=attributes,
    )
