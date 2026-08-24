"""Typed loading and validation for domain vocabulary and policy parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DimensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    required: bool = True


class NamedValuesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[str] = Field(min_length=1)
    multi_select: bool = False

    @field_validator("values")
    @classmethod
    def unique_values(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("configured values must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("configured values cannot be blank")
        return values


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)


class OutcomeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)


class VendorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pim: list[str] = Field(min_length=1)

    @field_validator("pim")
    @classmethod
    def unique_vendors(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(value.casefold() for value in values)):
            raise ValueError("configured vendors must be unique")
        return values


class ResearchPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_targeted_attempts_per_dimension: int = Field(default=1, ge=1, le=10)
    max_research_passes: int = Field(default=2, ge=1, le=10)


class PimPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_unknown: bool = True
    high_confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    require_strong_source_for_confirmed: bool = True


class CatalogScalePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_unknown: bool = True


class AcceptancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_quality: float = Field(default=0.75, ge=0, le=1)


class QualificationWeights(BaseModel):
    """The five existing scoring dimensions, with a validated total."""

    model_config = ConfigDict(extra="forbid")

    pim_product_information_infrastructure: float = Field(ge=0, le=1)
    catalog_scale: float = Field(ge=0, le=1)
    catalog_complexity: float = Field(ge=0, le=1)
    multi_market_multilingual: float = Field(ge=0, le=1)
    source_data_complexity: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def total_is_one(self) -> QualificationWeights:
        total = sum(
            (
                self.pim_product_information_infrastructure,
                self.catalog_scale,
                self.catalog_complexity,
                self.multi_market_multilingual,
                self.source_data_complexity,
            )
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"qualification weights must sum to 1.0; got {total}")
        return self


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research: ResearchPolicy = Field(default_factory=ResearchPolicy)
    pim_stack: PimPolicy = Field(default_factory=PimPolicy)
    catalog_scale: CatalogScalePolicy = Field(default_factory=CatalogScalePolicy)
    acceptance: AcceptancePolicy = Field(default_factory=AcceptancePolicy)
    qualification: QualificationWeights


class DomainConfig(BaseModel):
    """A validated domain ontology plus typed policy parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str = Field(min_length=1)
    version: int = Field(ge=1)
    dimensions: dict[str, DimensionConfig]
    decisions: dict[str, NamedValuesConfig]
    dimension_statuses: NamedValuesConfig
    evaluations: dict[str, EvaluationConfig]
    outcomes: dict[str, OutcomeConfig]
    vendors: VendorConfig
    policies: PolicyConfig

    def validate_decision(self, name: str, value: str) -> None:
        """Reject a decision name or value outside the active ontology."""

        definition = self.decisions.get(name)
        if definition is None:
            raise ValueError(f"unknown domain decision: {name}")
        selected_values = value.split(",") if definition.multi_select else [value]
        if not selected_values or any(item not in definition.values for item in selected_values):
            raise ValueError(f"invalid value {value!r} for domain decision {name!r}")

    def validate_dimension(self, name: str) -> None:
        if name not in self.dimensions:
            raise ValueError(f"unknown domain dimension: {name}")

    def validate_evaluation(self, name: str) -> None:
        if name not in self.evaluations:
            raise ValueError(f"unknown domain evaluation: {name}")

    def validate_outcome(self, name: str) -> None:
        if name not in self.outcomes:
            raise ValueError(f"unknown domain outcome: {name}")


def load_domain_config(path: str | Path) -> DomainConfig:
    """Load YAML, validate it into typed models, and fail at startup on errors."""

    config_path = Path(path)
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"unable to read domain configuration {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"domain configuration {config_path} must contain a YAML mapping")
    try:
        return DomainConfig.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"invalid domain configuration {config_path}: {exc}") from exc


def product_factory_config_path() -> Path:
    """Return the repository-owned Product Factory configuration path."""

    return Path(__file__).resolve().parents[3] / "config" / "domains" / "product_factory.yaml"


def load_product_factory_config() -> DomainConfig:
    return load_domain_config(product_factory_config_path())


def is_strong_pim_claim(confidence: float, config: DomainConfig) -> bool:
    """Apply the typed high-confidence PIM policy without creating a rule DSL."""

    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return (
        config.policies.pim_stack.require_strong_source_for_confirmed
        and confidence >= config.policies.pim_stack.high_confidence_threshold
    )


def targeted_attempt_is_allowed(route: str, attempts: int, config: DomainConfig) -> bool:
    """Keep targeted research bounded by a typed policy parameter."""

    if attempts < 0:
        raise ValueError("attempts cannot be negative")
    if not route.startswith("target_"):
        raise ValueError(f"invalid targeted route: {route}")
    return attempts < config.policies.research.max_targeted_attempts_per_dimension


def unknown_is_allowed(dimension: str, config: DomainConfig) -> bool:
    """Return the configured unknown policy for dimensions that support it."""

    config.validate_dimension(dimension)
    if dimension == "pim_stack":
        return config.policies.pim_stack.allow_unknown
    if dimension == "catalog_scale":
        return config.policies.catalog_scale.allow_unknown
    return False
