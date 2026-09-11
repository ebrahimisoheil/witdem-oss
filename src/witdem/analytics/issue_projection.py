"""Versioned compact run-level issue inputs for serving-store adapters.

Project during publication, not during an interactive dashboard request. These
inputs retain all investigations; cohort selection, ranking and display limits
belong to the read reducer. No raw operation/evaluation attributes are retained.
Names and display labels are selected metadata, not a general PII sanitizer.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from witdem.analytics.core import Operation
from witdem.analytics.issues import failure_for_operations, quality_gap_contribution, retry_contributions


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
