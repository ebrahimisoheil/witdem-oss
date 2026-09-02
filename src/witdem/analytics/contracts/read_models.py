"""Typed analytics results shared by UI, API, and future frontend clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from witdem.analytics.repository.state import Capabilities


@dataclass(frozen=True, slots=True)
class _ReadModel:
    """Common serialization boundary for frontend/API adapters."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SemanticReplayRecord(_ReadModel):
    record_id: str
    kind: str
    name: str
    timestamp: datetime | None
    status: str | None
    value: Any
    attributes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionSummary(_ReadModel):
    total_runs: int
    successful_runs: int
    failed_runs: int
    running_runs: int
    recovered_runs: int
    extra_work_runs: int
    avg_duration_seconds: float | None
    measured_cost: float | None
    cost_coverage: float
    business_successful_runs: int
    business_unsuccessful_runs: int
    business_reported_runs: int
    terminal_runs: int
    unknown_runs: int
    attention_runs: int
    runtime_success_rate: float


@dataclass(frozen=True, slots=True)
class MeasurementCoverage(_ReadModel):
    """Completeness for a measurement that is not applicable to every run."""

    total_runs: int
    applicable_runs: int
    complete_runs: int
    partial_runs: int
    missing_runs: int
    not_applicable_runs: int
    eligible_operations: int
    measured_operations: int

    @property
    def coverage(self) -> float:
        return self.complete_runs / self.applicable_runs if self.applicable_runs else 0.0

    @property
    def operation_coverage(self) -> float:
        return self.measured_operations / self.eligible_operations if self.eligible_operations else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "coverage": self.coverage, "operation_coverage": self.operation_coverage}


@dataclass(frozen=True, slots=True)
class ProductGoalSummary(_ReadModel):
    total_runs: int
    reported_runs: int
    achieved_runs: int
    decision_correct_runs: int
    false_acceptances: int
    false_rejections: int
    escalation_errors: int
    targeted_research_runs: int
    targeted_research_successes: int
    cost_per_achieved_goal: float | None
    cost_measured_achieved_runs: int
    time_per_achieved_goal: float | None
    time_measured_achieved_runs: int
    tokens_per_achieved_goal: float | None
    token_measured_achieved_runs: int

    @property
    def cost_coverage(self) -> float:
        return self.cost_measured_achieved_runs / self.achieved_runs if self.achieved_runs else 0.0

    @property
    def time_coverage(self) -> float:
        return self.time_measured_achieved_runs / self.achieved_runs if self.achieved_runs else 0.0

    @property
    def token_coverage(self) -> float:
        return self.token_measured_achieved_runs / self.achieved_runs if self.achieved_runs else 0.0

    @property
    def coverage(self) -> float:
        return self.reported_runs / self.total_runs if self.total_runs else 0.0

    @property
    def success_rate(self) -> float:
        return self.achieved_runs / self.reported_runs if self.reported_runs else 0.0

    @property
    def decision_correctness_rate(self) -> float:
        return self.decision_correct_runs / self.reported_runs if self.reported_runs else 0.0


@dataclass(frozen=True, slots=True)
class CostSummary(_ReadModel):
    measured_cost: float | None
    model_cost: float | None
    tool_cost: float | None
    cost_coverage: float
    measured_cost_per_run: float | None
    input_tokens: float | None
    output_tokens: float | None
    total_tokens: float | None
    token_runs: int
    cost: MeasurementCoverage
    tokens: MeasurementCoverage

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cost"] = self.cost.to_dict()
        payload["tokens"] = self.tokens.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class OverviewSnapshot(_ReadModel):
    """One coherent read model for the dashboard overview request."""

    execution: ExecutionSummary
    goals: ProductGoalSummary
    costs: CostSummary
    cost_unavailable: dict[str, int]
    models: tuple[ModelSummary, ...]
    providers: tuple[ProviderSummary, ...]
    workflows: tuple[PerformanceSummary, ...]
    stages: tuple[dict[str, Any], ...]
    runtime_breakdown: dict[str, int]
    outcome_breakdown: dict[str, int]
    failures: tuple[FailureSummary, ...]
    evaluations: tuple[dict[str, Any], ...]
    goal_misses: tuple[dict[str, Any], ...]
    goal_trend: tuple[dict[str, Any], ...]
    goal_portfolio: tuple[dict[str, Any], ...]
    assurance_summary: dict[str, int | float]
    contracts: tuple[dict[str, Any], ...]
    metadata: MetadataSnapshot


@dataclass(frozen=True, slots=True)
class MetadataSnapshot(_ReadModel):
    """One coherent read model for dashboard capabilities and filter values."""

    capabilities: Capabilities
    filters: dict[str, tuple[str, ...]]
    contracts: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PerformanceSummary(_ReadModel):
    label: str
    runs: int
    calls: int
    completed: int
    successful: int
    failed: int
    recovered: int
    extra_work: int
    measured_cost: float | None
    cost_per_positive_run: float | None
    time_per_positive_run: float | None
    failed_run_cost: float | None
    total_tokens: float | None
    tokens_per_positive_run: float | None
    failed_run_tokens: float | None
    failure_rate: float
    extra_work_rate: float
    cost_coverage: float
    semantics: str
    participant_id: str
    dimension: str
    provider_id: str | None
    model_id: str | None
    model_family: str | None
    vendor_id: str | None
    active_seconds: float
    p50_call_seconds: float | None
    p95_call_seconds: float | None
    cost_eligible_operations: int
    cost_measured_operations: int
    token_eligible_operations: int
    token_measured_operations: int


@dataclass(frozen=True, slots=True)
class ProviderSummary(PerformanceSummary):
    """Performance summary grouped by provider."""


@dataclass(frozen=True, slots=True)
class ModelSummary(PerformanceSummary):
    """Performance summary grouped by model."""


@dataclass(frozen=True, slots=True)
class FailureSummary(_ReadModel):
    failure_location: str
    failure_key: str
    kind: str
    failures: int
    executions: int
    terminal_runs: int
    recovered_runs: int
    unknown_outcome_runs: int
    providers: str | None
    models: str | None
    time_seconds: float
    known_cost: float | None
    total_tokens: float | None
    affected_run_time_seconds: float
    affected_run_cost: float | None
    affected_run_tokens: float | None


@dataclass(frozen=True, slots=True)
class PathSummary(_ReadModel):
    path: str
    steps: tuple[str, ...]
    path_signature: str
    executions: int
    completed: int
    failures: int
    failure_reports: int
    time_seconds: float
    usual_seconds: float | None
    known_cost: float | None
    total_tokens: float | None
