"""Versioned compact run-level issue inputs for serving-store adapters.

Project during publication, not during an interactive dashboard request. These
inputs retain all investigations; cohort selection, ranking and display limits
belong to the read reducer. No raw operation/evaluation attributes are retained.
Names and display labels are selected metadata, not a general PII sanitizer.
"""

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from witdem.analytics.core import Evaluation, Event, Link, Operation, Outcome
from witdem.analytics.evidence import EvidenceBundle, operation_summary, required_measurement_alerts
from witdem.analytics.issues import failure_for_operations, quality_gap_contribution, retry_contributions
from witdem.analytics.operation_facts import workflow_operation_facts
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_replay_graph
from witdem.analytics.serving import build_serving_rows, serving_runtime_outcome
from witdem.workflows import WorkflowDefinition, project_execution


class IssueProjectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IssueRetryContribution(IssueProjectionRecord):
    key: str
    label: str
    extra_attempts: int = Field(ge=1)


class IssueQualityGap(IssueProjectionRecord):
    name: str
    score: float
    target: float
    direction: str


class IssueExecutionProjection(IssueProjectionRecord):
    """Explicit v1 run contributions, not a whole-population Issues response.

    All evidence fields are required, including nullable measurements. Unknown
    versions and extra fields fail validation. Outlier flags are deliberately
    absent: a selected cohort must calculate its own percentile thresholds.
    Operation-type failures and required-meter diagnostics are separate inputs.
    """

    schema_version: Literal["1"]
    execution_id: str
    display_name: str | None
    runtime_outcome: str | None
    terminal_failure: bool
    recovered: bool
    failure_location: str | None
    duration_seconds: float | None
    known_cost: float | None
    total_tokens: float | None
    product_goal_achieved: bool | None
    retries: list[IssueRetryContribution]
    quality_gaps: list[IssueQualityGap]


class IssueLinkedChildren(IssueProjectionRecord):
    type: str
    family: str | None
    operations: int = Field(ge=0)
    providers: list[str]
    models: list[str]
    implementations: list[str]


class IssueOperationType(IssueProjectionRecord):
    type: str
    family: str
    plane: str
    operations: int = Field(ge=0)
    failed: int = Field(ge=0)
    active_seconds: float
    roles: list[str]
    interfaces: list[str]
    providers: list[str]
    models: list[str]
    implementations: list[str]
    model_applicability: str
    linked_children: list[IssueLinkedChildren]
    measurements: dict[str, float]

    @model_validator(mode="after")
    def validate_failure_count(self) -> "IssueOperationType":
        if self.failed > self.operations:
            raise ValueError("failed operation count exceeds total operations")
        return self


class IssueRequiredMeasurement(IssueProjectionRecord):
    operation_type: str
    measurement_key: str
    operations: int = Field(ge=1)
    executions: Literal[1]
    workflow_ids: list[str]


class IssueOperationProjection(IssueProjectionRecord):
    """Per-execution operation contributions, including non-failing types.

    Keep all types: filtering to failures before aggregation would omit successful
    operations of the same type from another execution. Distinct execution counts
    for each required-meter group can be added after execution deduplication.
    """

    schema_version: Literal["1"]
    execution_id: str
    total_operations: int = Field(ge=0)
    execution_containers: int = Field(ge=0)
    failed_operations: int = Field(ge=0)
    operation_types: list[IssueOperationType]
    required_measurements: list[IssueRequiredMeasurement]

    @model_validator(mode="after")
    def validate_contributions(self) -> "IssueOperationProjection":
        types = [item.type for item in self.operation_types]
        if len(types) != len(set(types)):
            raise ValueError("operation type contributions must be unique")
        if sum(item.operations for item in self.operation_types) != self.total_operations:
            raise ValueError("operation contributions do not match total operations")
        if sum(item.failed for item in self.operation_types) != self.failed_operations:
            raise ValueError("operation contributions do not match failed operations")
        type_counts = {item.type: item.operations for item in self.operation_types}
        groups = [(item.operation_type, item.measurement_key) for item in self.required_measurements]
        if len(groups) != len(set(groups)):
            raise ValueError("required measurement contributions must be unique")
        if any(item.operation_type not in type_counts for item in self.required_measurements):
            raise ValueError("required measurement references an absent operation type")
        return self


def project_issue_operations(
    execution_id: str,
    operations: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
) -> IssueOperationProjection:
    """Project public operation facts and explicit meter states using OSS rules.

    No attributes, prompt/response content or individual meter values survive
    except the existing aggregate measured totals. Inputs must belong to one
    execution; orphan meters and execution containers follow the public helpers.
    """
    if any(operation.get("execution_id") != execution_id for operation in operations):
        raise ValueError("issue operation facts must belong to the selected execution")
    summary = operation_summary(operations, measurements)
    return IssueOperationProjection(
        schema_version="1", execution_id=execution_id,
        total_operations=summary["total_operations"],
        execution_containers=summary["execution_containers"],
        failed_operations=summary["failed_operations"],
        operation_types=[IssueOperationType.model_validate(item) for item in summary["types"]],
        required_measurements=[
            IssueRequiredMeasurement.model_validate(item)
            for item in required_measurement_alerts(operations, measurements)
        ],
    )


def project_issue_execution(
    row: Mapping[str, Any],
    operations: list[Operation],
    evaluations: Iterable[Mapping[str, Any]],
) -> IssueExecutionProjection:
    """Project one normalized public execution row without truncating inputs.

    Cross-execution operations are rejected, not accidentally attributed. Other
    executions' evaluation facts are ignored, as in the public cohort reducer.
    """
    execution_id = str(row["execution_id"])
    if any(operation.execution_id != execution_id for operation in operations):
        raise ValueError("issue projection operations must belong to the selected execution")
    failure = failure_for_operations(operations)
    gaps = []
    for fact in evaluations:
        if str(fact["execution_id"]) != execution_id:
            continue
        contribution = quality_gap_contribution(fact)
        if contribution is not None:
            gaps.append(IssueQualityGap.model_validate(contribution))
    return IssueExecutionProjection(
        schema_version="1",
        execution_id=execution_id,
        display_name=row.get("display_name"),
        runtime_outcome=row.get("runtime_outcome") or row.get("status"),
        terminal_failure=int(row.get("failure_count") or 0) > 0 and row.get("runtime_outcome") != "recovered",
        recovered=row.get("runtime_outcome") == "recovered",
        failure_location=failure.get("primary_break_point") or None,
        duration_seconds=row.get("duration_seconds"),
        known_cost=row.get("known_cost"),
        total_tokens=row.get("total_tokens"),
        product_goal_achieved=row.get("product_goal_achieved"),
        retries=[IssueRetryContribution(key=key, **item) for key, item in retry_contributions(operations).items()],
        quality_gaps=gaps,
    )


def project_bundle_issue_execution(bundle: EvidenceBundle) -> IssueExecutionProjection:
    """Derive compact run issues through the OSS canonical serving projection.

    This publication-time function performs no I/O. It does not use Enterprise
    counters or reconstruct a dashboard database. Operation-type diagnostics are
    projected separately from public workflow operation facts, which may require
    the authored workflow definition; they must not be assumed empty here.
    """
    identity = bundle.execution.execution_id
    records: list[Event | Evaluation | Outcome] = [
        *bundle.events, *bundle.evaluations, *sorted(bundle.outcomes, key=lambda item: item.timestamp),
    ]
    members: list[Operation | Link | Event | Evaluation | Outcome] = [*bundle.operations, *bundle.links, *records]
    if any(item.execution_id != identity for item in members):
        raise ValueError("issue bundle records must belong to the selected execution")
    serving = build_serving_rows(
        bundle.execution, bundle.operations, bundle.links, records,
        transformed_at=datetime(1970, 1, 1, tzinfo=timezone.utc), transform_version="issue-projection-1",
    )
    fact = serving["execution_facts"][0]
    status = str(fact.get("runtime_status") or "")
    row = {
        **fact, "status": status,
        "runtime_outcome": serving_runtime_outcome(status, int(fact.get("failure_count") or 0)),
        "known_cost": fact.get("measured_cost"),
    }
    evaluations = [
        {**item, "attributes": json.loads(item["attributes"])}
        for item in serving["semantic_facts"] if item["record_type"] == "evaluation"
    ]
    return project_issue_execution(row, bundle.operations, evaluations)


def project_bundle_issue_operations(
    bundle: EvidenceBundle, *, definition: WorkflowDefinition | None,
) -> IssueOperationProjection:
    """Project operation diagnostics with an explicitly resolved OSS definition.

    The caller resolves the authoritative authored/registry definition. Explicit
    None supports genuinely undeclared workflows, not an unresolved declaration.
    Unsupported declarations must not silently lose their required meters.
    """
    identity = bundle.execution.execution_id
    if any(operation.execution_id != identity for operation in bundle.operations):
        raise ValueError("issue bundle operations must belong to the selected execution")
    if any(event.execution_id != identity for event in bundle.events):
        raise ValueError("issue bundle events must belong to the selected execution")
    if any(link.execution_id != identity for link in bundle.links):
        raise ValueError("issue bundle links must belong to the selected execution")
    if definition is None and any(event.name == "workflow.definition" for event in bundle.events):
        raise ValueError("an authored workflow declaration requires explicit resolution")
    execution = {"execution_id": identity}
    if definition is None:
        projection: dict[str, Any] = {"execution": execution, "nodes": []}
    else:
        graph = derive_replay_graph(
            NormalizedExecutionGraph(execution=bundle.execution, operations=bundle.operations, links=bundle.links),
            events=bundle.events,
        )
        projection = project_execution(definition, execution=execution, graph=graph.model_dump(mode="json"))
    facts, measurements = workflow_operation_facts(projection, bundle.operations)
    return project_issue_operations(identity, facts, measurements)
