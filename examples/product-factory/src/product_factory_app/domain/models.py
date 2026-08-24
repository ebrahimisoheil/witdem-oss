"""Typed domain contracts for company research and execution artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return a timezone-aware timezone.utc timestamp."""

    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    """Lifecycle status for a research execution."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RECOVERED = "recovered"
    FAILED = "failed"


class ResearchMode(StrEnum):
    """Available research source modes."""

    FIXTURE = "fixture"
    WEBSITE = "website"


class CompanyResearchRequest(BaseModel):
    """Input contract for a single company research run."""

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1)
    website_url: AnyHttpUrl | None = None
    research_mode: ResearchMode = ResearchMode.FIXTURE
    scenario: str = "company-research"
    requested_focus: list[str] = Field(default_factory=list)
    execution_id: str = Field(default_factory=lambda: uuid4().hex)

    @field_validator("company_name")
    @classmethod
    def normalize_company_name(cls, value: str) -> str:
        """Normalize accidental surrounding whitespace."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("company_name cannot be blank")
        return normalized


class Evidence(BaseModel):
    """A source-backed claim collected during research."""

    model_config = ConfigDict(extra="allow")

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    claim: str
    excerpt: str
    source_url: str
    source_title: str | None = None
    source_type: str = "unknown"
    evidence_type: str = "source_claim"
    observed_fact: str | None = None
    qualification_dimension: str | None = None
    relevance: float = Field(default=0.5, ge=0, le=1)
    reliability: float = Field(default=0.5, ge=0, le=1)
    collected_at: datetime = Field(default_factory=utc_now)
    supports_claim: bool = True


class ResearchState(BaseModel):
    """Research problem state passed between stages and persisted for inspection.

    Research components may update evidence, source attempts, missing signals,
    conflicts, notes, and validation bookkeeping. Telemetry identifiers do not
    belong here; they live in the run manifest and semantic event envelope.
    """

    model_config = ConfigDict(extra="allow")

    request: CompanyResearchRequest
    evidence: list[Evidence] = Field(default_factory=list)
    research_pass: int = 0
    attempted_sources: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    validation_attempts: int = 0
    profile_repair_count: int = 0
    source_failures: int = 0
    last_failure: str | None = None
    termination_reason: str | None = None
    targeted_research_routes: list[str] = Field(default_factory=list)
    route_attempts: dict[str, int] = Field(default_factory=dict)


class ResearchDimensionState(StrEnum):
    """Deterministic state for one qualification research dimension."""

    SUFFICIENT = "sufficient"
    UNKNOWN_ACCEPTABLE = "unknown_acceptable"
    MISSING = "missing"
    WEAK = "weak"
    CONFLICTING = "conflicting"


class ResearchDimensionStatus(BaseModel):
    """Explicit sufficiency decision for one qualification dimension."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    status: ResearchDimensionState
    reason_code: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_reliability: float = Field(default=0.0, ge=0, le=1)
    finding_confidence: float = Field(default=0.0, ge=0, le=1)
    next_route: str | None = None


class EvidenceAssessment(BaseModel):
    """Decision describing whether another research pass is useful."""

    sufficient: bool
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    missing_signals: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    dimension_statuses: list[ResearchDimensionStatus] = Field(default_factory=list)


class FitLevel(StrEnum):
    """Coarse Product Factory relevance classification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class PimIdentificationState(StrEnum):
    """Confidence state for a PIM/product-information classification."""

    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class CatalogScaleBucket(StrEnum):
    """Coarse catalog scale bucket that avoids fake precision."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    VERY_LARGE = "very_large"
    UNKNOWN = "unknown"


class ProductInformationStack(BaseModel):
    """Vendor-neutral PIM/product-information stack classification."""

    model_config = ConfigDict(extra="forbid")

    pim_vendor: str | None = None
    pim_product: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_reliability: float = Field(default=0.0, ge=0, le=1)
    state: PimIdentificationState = PimIdentificationState.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None


class CatalogScaleEstimate(BaseModel):
    """Evidence-backed catalog scale estimate."""

    model_config = ConfigDict(extra="forbid")

    scale_bucket: CatalogScaleBucket = CatalogScaleBucket.UNKNOWN
    estimated_range: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_reliability: float = Field(default=0.0, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainty: str | None = None


class QualificationDimensionScore(BaseModel):
    """Deterministic contribution to Product Factory fit."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    score: float = Field(ge=0, le=1)
    evidence_reliability: float = Field(default=0.0, ge=0, le=1)
    finding_confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str


class ProductFactoryQualification(BaseModel):
    """Explainable Product Factory fit result."""

    model_config = ConfigDict(extra="forbid")

    fit_score: float = Field(default=0.0, ge=0, le=100)
    fit_band: FitLevel = FitLevel.UNKNOWN
    fit_reasons: list[str] = Field(default_factory=list)
    fit_risks: list[str] = Field(default_factory=list)
    dimension_scores: list[QualificationDimensionScore] = Field(default_factory=list)


class GroundTruthLabels(BaseModel):
    """Optional manually verified labels for evaluation runs."""

    model_config = ConfigDict(extra="forbid")

    known_pim_vendor: str | None = None
    known_catalog_scale: str | None = None
    known_fit_band: FitLevel | None = None
    known_markets: list[str] = Field(default_factory=list)


class ProfileFinding(BaseModel):
    """A profile-level finding linked back to the evidence that supports it."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=lambda: uuid4().hex)
    field: str
    value: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence_reliability: float = Field(default=0.0, ge=0, le=1)
    uncertainty: str | None = None


class CompanyProfile(BaseModel):
    """Structured, evidence-backed Product Factory company profile."""

    model_config = ConfigDict(extra="forbid")

    company_name: str
    summary: str
    products: list[str] = Field(default_factory=list)
    catalog_scale: str | None = None
    catalog_complexity: str | None = None
    markets: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    commerce_technology: list[str] = Field(default_factory=list)
    pim_technology: list[str] = Field(default_factory=list)
    pim_stack: ProductInformationStack = Field(default_factory=ProductInformationStack)
    catalog_scale_estimate: CatalogScaleEstimate = Field(default_factory=CatalogScaleEstimate)
    product_data_complexity_signals: list[str] = Field(default_factory=list)
    source_data_complexity_signals: list[str] = Field(default_factory=list)
    product_factory_fit: FitLevel = FitLevel.UNKNOWN
    qualification: ProductFactoryQualification = Field(default_factory=ProductFactoryQualification)
    fit_rationale: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    findings: list[ProfileFinding] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ValidationResult(BaseModel):
    """Result of deterministic profile validation."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AcceptanceStatus(StrEnum):
    """Application-level acceptance status, distinct from execution status."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class AcceptanceResult(BaseModel):
    """Whether a completed, valid result meets Product Factory requirements."""

    status: AcceptanceStatus
    reason_code: str
    quality_score: float | None = Field(default=None, ge=0, le=1)
    required_signal_coverage: float | None = Field(default=None, ge=0, le=1)
    evidence_coverage: float | None = Field(default=None, ge=0, le=1)
    details: list[str] = Field(default_factory=list)


class ProviderMetadata(BaseModel):
    """Provider/model configuration captured with a run."""

    provider: str
    model: str
    role: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class RunManifest(BaseModel):
    """Persisted metadata for navigating one execution."""

    execution_id: str
    artifact_schema_version: str = "0.2.0"
    trace_id: str | None = None
    scenario: str
    workflow_version: str
    status: RunStatus
    started_at: datetime
    accepted_at: datetime | None = None
    finished_at: datetime | None = None
    request: CompanyResearchRequest
    providers: list[ProviderMetadata] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    result_path: str | None = None


class ResearchRun(BaseModel):
    """Complete application artifact returned by the service."""

    manifest: RunManifest
    state: ResearchState | None = None
    assessment: EvidenceAssessment | None = None
    profile: CompanyProfile | None = None
    validation: ValidationResult | None = None
    execution_completed: bool = False
    result_valid: bool = False
    acceptance: AcceptanceResult | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
