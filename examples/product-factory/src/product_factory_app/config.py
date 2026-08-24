"""Centralized, validated application configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from product_factory_app.domain.config import DomainConfig, load_product_factory_config
from product_factory_app.domain.models import ResearchMode

ProviderName = Literal["openai", "mistral", "deepseek"]


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _provider_env(name: str, default: ProviderName) -> ProviderName:
    value = _env(name, default)
    if value not in {"openai", "mistral", "deepseek"}:
        raise ValueError(f"{name} must be one of openai, mistral, deepseek; got {value!r}")
    return cast(ProviderName, value)


class ModelSettings(BaseModel):
    """Configuration for one model role."""

    provider: ProviderName
    model: str


class Settings(BaseModel):
    """Application settings loaded from environment variables once at startup."""

    scenario: str = "company-research"
    workflow_version: str = "0.1.0"
    research_mode: ResearchMode = ResearchMode.FIXTURE
    data_dir: Path = Path("data")
    content_tracing_enabled: bool = False
    otlp_endpoint: str | None = None
    witdem_sdk_enabled: bool = False
    domain_config: DomainConfig = Field(default_factory=load_product_factory_config)
    max_research_passes: int = Field(
        default_factory=lambda: load_product_factory_config().policies.research.max_research_passes,
        ge=1,
        le=10,
    )
    max_profile_repairs: int = Field(default=1, ge=0, le=5)
    max_model_calls: int = Field(default=12, ge=1, le=100)
    acceptance_min_quality_score: float = Field(
        default_factory=lambda: load_product_factory_config().policies.acceptance.minimum_quality,
        ge=0,
        le=1,
    )
    experiment_max_estimated_cost_usd: float = Field(default=20.0, gt=0)
    enabled_tools: list[str] = Field(default_factory=lambda: ["collect_company_evidence"])
    known_pim_vendors: list[str] = Field(default_factory=lambda: list(load_product_factory_config().vendors.pim))
    research_model: ModelSettings = ModelSettings(provider="openai", model="gpt-5.4-mini")
    extraction_model: ModelSettings = ModelSettings(provider="openai", model="gpt-5.4-mini")
    verification_model: ModelSettings = ModelSettings(provider="openai", model="gpt-5.4-mini")
    embedding_model: str = "voyage-4-lite"

    @model_validator(mode="after")
    def validate_model_names(self) -> Settings:
        for role, model_settings in {
            "research": self.research_model,
            "extraction": self.extraction_model,
            "verification": self.verification_model,
        }.items():
            if not model_settings.provider or not model_settings.model:
                raise ValueError(f"{role} model provider and model are required")
        return self

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        """Load and validate configuration from one obvious location."""

        if env_file:
            load_dotenv(env_file, override=False)
        research_provider = _provider_env("PF_RESEARCH_MODEL_PROVIDER", "openai")
        research_model = _env("PF_RESEARCH_MODEL", "gpt-5.4-mini")
        extraction_provider = _provider_env("PF_EXTRACTION_MODEL_PROVIDER", research_provider)
        extraction_model = _env("PF_EXTRACTION_MODEL", research_model)
        verification_provider = _provider_env("PF_VERIFICATION_MODEL_PROVIDER", extraction_provider)
        verification_model = _env("PF_VERIFICATION_MODEL", extraction_model)
        return cls(
            scenario=_env("PF_SCENARIO", "company-research"),
            workflow_version=_env("PF_WORKFLOW_VERSION", "0.1.0"),
            research_mode=ResearchMode(_env("PF_RESEARCH_PROVIDER", "fixture")),
            data_dir=Path(_env("PF_DATA_DIR", "data")),
            content_tracing_enabled=_bool_env("PF_CONTENT_TRACING_ENABLED", False),
            otlp_endpoint=(
                os.getenv("PF_OTLP_ENDPOINT")
                or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                or None
            ),
            witdem_sdk_enabled=_bool_env("PF_WITDEM_SDK_ENABLED", False),
            max_research_passes=int(
                _env("PF_MAX_RESEARCH_PASSES", str(load_product_factory_config().policies.research.max_research_passes))
            ),
            max_profile_repairs=int(_env("PF_MAX_PROFILE_REPAIRS", "1")),
            max_model_calls=int(_env("PF_MAX_MODEL_CALLS", "12")),
            acceptance_min_quality_score=float(_env("PF_ACCEPTANCE_MIN_QUALITY_SCORE", "0.75")),
            experiment_max_estimated_cost_usd=float(_env("PF_EXPERIMENT_MAX_ESTIMATED_COST_USD", "20")),
            known_pim_vendors=[
                item.strip()
                for item in _env(
                    "PF_KNOWN_PIM_VENDORS",
                    "Akeneo,Pimcore,Salsify,inriver,Contentserv,Syndigo,Stibo Systems,Informatica PIM,SAP",
                ).split(",")
                if item.strip()
            ],
            research_model=ModelSettings(provider=research_provider, model=research_model),
            extraction_model=ModelSettings(provider=extraction_provider, model=extraction_model),
            verification_model=ModelSettings(provider=verification_provider, model=verification_model),
            embedding_model=_env("PF_EMBEDDING_MODEL", "voyage-4-lite"),
        )
