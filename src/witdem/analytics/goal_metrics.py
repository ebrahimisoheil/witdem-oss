"""Versioned, additive product-goal measurements owned by OSS.

Serving adapters may persist these counters, but must not infer goals from
runtime completion or treat partial operation measurements as complete totals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from witdem.analytics.contracts.read_models import ProductGoalSummary
from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.serving import build_serving_rows


class GoalContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)

    schema_version: Literal["1"] = "1"
    total_runs: int = Field(default=1, ge=0)
    reported_runs: int = Field(default=0, ge=0)
    achieved_runs: int = Field(default=0, ge=0)
    decision_correct_runs: int = Field(default=0, ge=0)
    false_acceptances: int = Field(default=0, ge=0)
    false_rejections: int = Field(default=0, ge=0)
    escalation_errors: int = Field(default=0, ge=0)
    targeted_research_runs: int = Field(default=0, ge=0)
    targeted_research_successes: int = Field(default=0, ge=0)
    cost_sum: float = 0
    cost_measured_achieved_runs: int = Field(default=0, ge=0)
    time_sum: float = 0
    time_measured_achieved_runs: int = Field(default=0, ge=0)
    tokens_sum: float = 0
    token_measured_achieved_runs: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_consistent_counts(self) -> GoalContribution:
        if not self.achieved_runs <= self.reported_runs <= self.total_runs:
            raise ValueError("goal counts exceed their execution population")
        for name in ("decision_correct_runs", "false_acceptances", "false_rejections", "escalation_errors",
                     "targeted_research_runs"):
            if getattr(self, name) > self.reported_runs:
                raise ValueError("goal diagnostic count exceeds reported goals")
        if self.targeted_research_successes > min(self.targeted_research_runs, self.achieved_runs):
            raise ValueError("research successes exceed eligible achieved goals")
        if max(self.cost_measured_achieved_runs, self.time_measured_achieved_runs,
               self.token_measured_achieved_runs) > self.achieved_runs:
            raise ValueError("measured achieved goals exceed achieved goals")
        return self


def goal_contribution(
    attributes: Mapping[str, Any] | None,
    *,
    duration_seconds: float | None,
    complete_cost: float | None,
    complete_tokens: float | None,
) -> GoalContribution:
    """Project one selected goal record; None means no reported goal.

    Record selection belongs to the canonical reader. Empty reported attributes
    remain distinct from a missing record. Measurement inputs must be complete
    eligible-operation totals, not measured subtotals; zero is measured.
    """
    if attributes is None:
        return GoalContribution()
    achieved = attributes.get("product_goal_achieved") is True
    research = attributes.get("targeted_research_required") is True
    observed, expected = attributes.get("observed_status"), attributes.get("expected_status")
    return GoalContribution(
        reported_runs=1, achieved_runs=int(achieved),
        decision_correct_runs=int(attributes.get("decision_correct") is True),
        false_acceptances=int(observed == "accepted" and expected != "accepted"),
        false_rejections=int(observed == "rejected" and expected != "rejected"),
        escalation_errors=int((observed == "escalated") != (expected == "escalated")),
        targeted_research_runs=int(research),
        targeted_research_successes=int(research and achieved and (
            attributes.get("targeted_research_performed") is True or attributes.get("required_path_observed") is True
        )),
        cost_sum=complete_cost if achieved and complete_cost is not None else 0,
        cost_measured_achieved_runs=int(achieved and complete_cost is not None),
        time_sum=duration_seconds if achieved and duration_seconds is not None else 0,
        time_measured_achieved_runs=int(achieved and duration_seconds is not None),
        tokens_sum=complete_tokens if achieved and complete_tokens is not None else 0,
        token_measured_achieved_runs=int(achieved and complete_tokens is not None),
    )


def summarize_goal_contributions(contributions: Iterable[GoalContribution]) -> ProductGoalSummary:
    """Reduce additive counters; never average per-execution averages."""
    totals = GoalContribution(total_runs=0).model_dump(exclude={"schema_version"})
    for contribution in contributions:
        for name, value in contribution.model_dump(exclude={"schema_version"}).items():
            totals[name] += value
    means: dict[str, Any] = {}
    for field, count, output in (
        ("cost_sum", "cost_measured_achieved_runs", "cost_per_achieved_goal"),
        ("time_sum", "time_measured_achieved_runs", "time_per_achieved_goal"),
        ("tokens_sum", "token_measured_achieved_runs", "tokens_per_achieved_goal"),
    ):
        value = totals.pop(field)
        means[output] = value / totals[count] if totals[count] else None
    return ProductGoalSummary(**totals, **means)


def project_goal_contribution(bundle: EvidenceBundle) -> GoalContribution:
    """Project a canonical bundle using the same goal selection as OSS serving.

    Call before acquiring serving-store write locks. This consumes only one
    execution and returns scalar counters; no content or record attributes leave
    the projection. Historical adapters must track whether projection happened,
    rather than filling absent projections with zero-goal contributions.
    """
    # Local import avoids a repository/module initialization cycle. Measurement
    # completeness remains the exact helper used by the OSS repository reader.
    from witdem.analytics.repository.analytics_repository import _complete_operation_total

    outcomes = sorted(bundle.outcomes, key=lambda outcome: outcome.timestamp)
    goal = next((outcome.attributes for outcome in reversed(outcomes) if outcome.name == "product_goal"), {})
    if not goal:
        return GoalContribution()
    # Goal selection and execution duration depend only on execution/semantics.
    # Do not build unrelated path/operation projections merely to select a goal.
    rows = build_serving_rows(
        bundle.execution, [], [], [*bundle.events, *bundle.evaluations, *outcomes],
        transformed_at=datetime(1970, 1, 1, tzinfo=timezone.utc), transform_version="goal-contribution-1",
    )
    fact = rows["execution_facts"][0]
    attributes = {
        "expected_status": fact.get("expected_outcome"), "observed_status": fact.get("application_outcome"), **goal,
    } if fact["product_goal_reported"] else None
    measured_operations = bundle.operations if goal.get("product_goal_achieved") is True else []
    return goal_contribution(
        attributes, duration_seconds=fact["duration_seconds"],
        complete_cost=_complete_operation_total(measured_operations, "cost")[1],
        complete_tokens=_complete_operation_total(measured_operations, "tokens")[1],
    )
