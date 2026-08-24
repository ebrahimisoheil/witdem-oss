"""Shared business contract used by every Product Factory runtime."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OutcomeStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    NOT_REACHED = "not_reached"


class CompanyIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    website: str


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    dimension: str
    claim: str
    reliability: float = Field(ge=0, le=1)
    score_signal: float = Field(ge=0, le=1)
    conflicting: bool = False


class CasePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualification_threshold: float = Field(default=0.80, ge=0, le=1)
    escalation_margin: float = Field(default=0.05, ge=0, le=0.5)
    minimum_profile_completeness: float = Field(default=0.85, ge=0, le=1)
    minimum_evidence_coverage: float = Field(default=0.80, ge=0, le=1)


class GroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_claim_ids: list[str]
    expected_dimensions: list[str]


class CaseDefinition(BaseModel):
    """Full checked-in case; ground truth is stripped before runtime execution."""

    model_config = ConfigDict(extra="forbid")
    case_id: str
    company: CompanyIdentity
    expected_status: OutcomeStatus
    expected_targeted_research: bool
    policy: CasePolicy
    pass_one: list[EvidenceItem]
    targeted_research: dict[str, list[EvidenceItem]]
    ground_truth: GroundTruth


class RuntimeCase(BaseModel):
    """Agent-visible case data. Deliberately contains no expected result or truth labels."""

    model_config = ConfigDict(extra="forbid")
    case_id: str
    company: CompanyIdentity
    policy: CasePolicy
    pass_one: list[EvidenceItem]
    targeted_research: dict[str, list[EvidenceItem]]


class EvidenceCritique(BaseModel):
    missing_dimensions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    research_queries: list[str] = Field(default_factory=list)

    @field_validator("missing_dimensions", "conflicts", "research_queries", mode="before")
    @classmethod
    def normalize_provider_items(cls, value: Any) -> list[str]:
        """Accept semantically valid structured items returned by live models."""

        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            raise ValueError("critique fields must be a list")
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(item)
                continue
            if isinstance(item, dict):
                preferred = next(
                    (
                        item.get(key)
                        for key in ("dimension", "query", "name", "description", "issue")
                        if item.get(key)
                    ),
                    None,
                )
                normalized.append(str(preferred) if preferred is not None else json.dumps(item, sort_keys=True))
                continue
            normalized.append(str(item))
        return normalized


class ProfileArtifact(BaseModel):
    company_name: str
    summary: str
    dimensions: dict[str, float]
    evidence_ids: list[str]
    completeness: float = Field(default=0, ge=0, le=1)

    @field_validator("dimensions", mode="before")
    @classmethod
    def normalize_dimension_scores(cls, value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            raise ValueError("dimensions must be an object")
        normalized: dict[str, float] = {}
        for name, raw_score in value.items():
            candidate = raw_score
            if isinstance(raw_score, dict):
                candidate = next(
                    (
                        raw_score.get(key)
                        for key in ("score", "value", "strength", "proposed_strength", "score_signal")
                        if isinstance(raw_score.get(key), int | float) and not isinstance(raw_score.get(key), bool)
                    ),
                    None,
                )
            if isinstance(candidate, int | float) and not isinstance(candidate, bool):
                normalized[str(name)] = min(1.0, max(0.0, float(candidate)))
        return normalized

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def normalize_evidence_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item.get("id")) if isinstance(item, dict) and item.get("id") else str(item) for item in value]

    @field_validator("completeness", mode="before")
    @classmethod
    def ignore_structured_model_completeness(cls, value: Any) -> float:
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


class RuntimeOutput(BaseModel):
    runtime_id: str
    model_profile: str
    terminal: bool = True
    profile: ProfileArtifact | None = None
    critique: EvidenceCritique = Field(default_factory=EvidenceCritique)
    targeted_research_performed: bool = False
    evidence_used: list[EvidenceItem] = Field(default_factory=list)
    actual_models: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
    topology: list[str] = Field(default_factory=list)
    error: str | None = None


class DecisionResult(BaseModel):
    observed_status: OutcomeStatus
    qualification_score: float | None = None
    threshold_margin: float | None = None
    evidence_coverage: float = Field(default=0, ge=0, le=1)
    artifact_valid: bool = False
    decision_evidence_sufficient: bool = False
    closest_blocker: str


class GoalAssessment(BaseModel):
    decision_correct: bool
    product_goal_achieved: bool
    required_path_observed: bool
    closest_blocker: str


class ProductFactoryResult(BaseModel):
    contract_version: str = "1.0"
    execution_id: str
    trace_id: str | None = None
    dashboard_url: str
    case_id: str
    runtime_id: str
    model_profile: str
    expected_status: OutcomeStatus
    runtime: RuntimeOutput
    decision: DecisionResult
    goal: GoalAssessment
    delivery_status: dict[str, Any] = Field(default_factory=dict)
    analytics_status: str = "not_requested"
    analytics_batches: list[dict[str, Any]] = Field(default_factory=list)
    analytics_fact: dict[str, Any] = Field(default_factory=dict)
    latency_seconds: float | None = None
    measured_cost_usd: float | None = None
    cost_unavailable_reason: str | None = None

    def goal_attributes(self, threshold: float, *, targeted_research_required: bool = False) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "case_id": self.case_id,
            "runtime_id": self.runtime_id,
            "model_profile": self.model_profile,
            "expected_status": self.expected_status.value,
            "observed_status": self.decision.observed_status.value,
            "decision_correct": self.goal.decision_correct,
            "product_goal_achieved": self.goal.product_goal_achieved,
            "artifact_valid": self.decision.artifact_valid,
            "decision_evidence_sufficient": self.decision.decision_evidence_sufficient,
            "required_path_observed": self.goal.required_path_observed,
            "closest_blocker": self.goal.closest_blocker,
            "threshold": threshold,
            "threshold_margin": self.decision.threshold_margin,
            "targeted_research_performed": self.runtime.targeted_research_performed,
            "targeted_research_required": targeted_research_required,
        }
