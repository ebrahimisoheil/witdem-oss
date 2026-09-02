"""Public, domain-neutral evidence-bundle contracts and diagnostic helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from witdem.analytics.core import Evaluation, Event, Execution, Link, Operation, Outcome
from witdem.protocol import EVIDENCE_BUNDLE_SCHEMA_VERSION


class EvidenceContract(BaseModel):
    """Strict base for the versioned evidence export contract."""

    model_config = ConfigDict(extra="forbid")


class EvaluationAssessment(EvidenceContract):
    """Existing explicit pass diagnostic linked to one canonical evaluation."""

    evaluation_id: str
    passed: bool | None = None


class EvidenceBundleDiagnostics(EvidenceContract):
    """Existing OSS diagnostics emitted alongside canonical records."""

    failure_explanation: dict[str, Any] = Field(default_factory=dict)
    operation_summary: dict[str, Any] = Field(default_factory=dict)
    operation_measurements: list[dict[str, Any]] = Field(default_factory=list)
    measurement_coverage: dict[str, Any] = Field(default_factory=dict)
    evaluation_assessments: list[EvaluationAssessment] = Field(default_factory=list)
    workflow_discrepancies: dict[str, Any] | None = None


class EvidenceBundle(EvidenceContract):
    """Portable v1 export of one execution's canonical OSS evidence."""

    schema_version: Literal["1.0"] = EVIDENCE_BUNDLE_SCHEMA_VERSION
    execution: Execution
    operations: list[Operation] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    diagnostics: EvidenceBundleDiagnostics


def operation_profile_inputs(
    operations: list[dict[str, Any]], measurements: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exclude execution containers while retaining every actual operation."""

    profile_operations = [
        operation for operation in operations if str(operation.get("entity_kind") or "operation") != "execution"
    ]
    operation_ids = {str(operation.get("operation_id") or "") for operation in profile_operations}
    return profile_operations, [
        measurement
        for measurement in measurements
        if str(measurement.get("operation_id") or "") in operation_ids
    ]


def operation_summary(operations: list[dict[str, Any]], measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the existing per-execution operation diagnostic summary."""

    execution_containers = sum(
        str(operation.get("entity_kind") or "operation") == "execution" for operation in operations
    )
    operations, measurements = operation_profile_inputs(operations, measurements)
    measured_by_operation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for measurement in measurements:
        measured_by_operation[str(measurement.get("operation_id") or "")].append(measurement)
    operations_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in operations:
        parent_id = str(operation.get("parent_operation_id") or "")
        if parent_id:
            operations_by_parent[parent_id].append(operation)
    groups: dict[str, dict[str, Any]] = {}
    for operation in operations:
        operation_type = str(operation.get("operation_type") or "unknown")
        family = str(operation.get("family") or "custom")
        plane = operation.get("plane") or ("control" if family in {"orchestration", "agent_control"} else "work")
        bucket = groups.setdefault(
            operation_type,
            {
                "type": operation_type,
                "family": family,
                "plane": plane,
                "operations": 0,
                "failed": 0,
                "active_seconds": 0.0,
                "roles": set(),
                "interfaces": set(),
                "providers": set(),
                "models": set(),
                "implementations": set(),
                "model_applicability": "not_applicable",
                "linked_children": {},
                "measurements": defaultdict(float),
            },
        )
        bucket["operations"] += 1
        bucket["failed"] += int(str(operation.get("status") or "").casefold() in {"error", "failed"})
        bucket["active_seconds"] += float(operation.get("duration_seconds") or 0.0)
        if operation.get("model_applicability") == "applicable":
            bucket["model_applicability"] = "applicable"
        for key, target in (
            ("role", "roles"),
            ("interface", "interfaces"),
            ("provider_id", "providers"),
            ("model_id", "models"),
            ("implementation_id", "implementations"),
        ):
            if operation.get(key):
                bucket[target].add(str(operation[key]))
        for child in operations_by_parent.get(str(operation.get("operation_id") or ""), []):
            child_type = str(child.get("operation_type") or "unknown")
            child_bucket = bucket["linked_children"].setdefault(
                child_type,
                {
                    "type": child_type,
                    "family": child.get("family"),
                    "operations": 0,
                    "providers": set(),
                    "models": set(),
                    "implementations": set(),
                },
            )
            child_bucket["operations"] += 1
            for key, target in (
                ("provider_id", "providers"),
                ("model_id", "models"),
                ("implementation_id", "implementations"),
            ):
                if child.get(key):
                    child_bucket[target].add(str(child[key]))
        for measurement in measured_by_operation.get(str(operation.get("operation_id") or ""), []):
            if measurement.get("measurement_status") == "measured" and measurement.get("value") is not None:
                bucket["measurements"][str(measurement["measurement_key"])] += float(measurement["value"])
    items = []
    for bucket in groups.values():
        linked_children = [
            {
                **child,
                "providers": sorted(child["providers"]),
                "models": sorted(child["models"]),
                "implementations": sorted(child["implementations"]),
            }
            for child in bucket.pop("linked_children").values()
        ]
        items.append(
            {
                **bucket,
                "roles": sorted(bucket["roles"]),
                "interfaces": sorted(bucket["interfaces"]),
                "providers": sorted(bucket["providers"]),
                "models": sorted(bucket["models"]),
                "implementations": sorted(bucket["implementations"]),
                "linked_children": sorted(linked_children, key=lambda child: str(child["type"])),
                "measurements": dict(sorted(bucket["measurements"].items())),
            }
        )
    return {
        "total_operations": len(operations),
        "execution_containers": execution_containers,
        "failed_operations": sum(
            int(str(item.get("status") or "").casefold() in {"error", "failed"}) for item in operations
        ),
        "types": sorted(items, key=lambda item: (-int(item["operations"]), str(item["type"]))),
    }


def measurement_coverage(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the existing per-execution measurement coverage diagnostic."""

    counts = {"measured": 0, "missing": 0, "not_applicable": 0}
    for measurement in measurements:
        status = str(measurement.get("measurement_status") or "missing")
        counts[status if status in counts else "missing"] += 1
    applicable = counts["measured"] + counts["missing"]
    return {
        **counts,
        "applicable": applicable,
        "coverage": counts["measured"] / applicable if applicable else None,
    }


def explicit_evaluation_pass(result: dict[str, Any]) -> bool | None:
    """Return the existing explicit evaluation pass diagnostic."""

    raw_attributes = result.get("attributes")
    attributes: dict[str, Any] = raw_attributes if isinstance(raw_attributes, dict) else {}
    passed = attributes.get("passed")
    if isinstance(passed, bool):
        return passed
    target = attributes.get("target")
    direction = str(attributes.get("direction") or "").casefold()
    value = result.get("score") if result.get("score") is not None else result.get("value")
    if isinstance(value, (int, float)) and isinstance(target, (int, float)):
        if direction in {"higher_is_better", "min", "at_least", ">="}:
            return float(value) >= float(target)
        if direction in {"lower_is_better", "max", "at_most", "<="}:
            return float(value) <= float(target)
        if direction in {"equal", "=="}:
            return float(value) == float(target)
    return None


__all__ = [
    "EvaluationAssessment",
    "EvidenceBundle",
    "EvidenceBundleDiagnostics",
]
