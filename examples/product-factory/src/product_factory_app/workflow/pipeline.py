"""Readable Haystack graph construction for one bounded research pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from haystack import Pipeline

from product_factory_app.analytics.semantic import SemanticAnalytics
from product_factory_app.domain.config import DomainConfig
from product_factory_app.research.sources import ResearchSource
from product_factory_app.workflow.components import (
    AssessEvidence,
    ExtractCompanyProfile,
    InitializeResearch,
    ResearchCompany,
    ValidateCompanyProfile,
)


@dataclass
class WorkflowComponents:
    """Component references used by the service for bounded correction passes."""

    initializer: InitializeResearch
    researcher: ResearchCompany
    assessor: AssessEvidence
    extractor: ExtractCompanyProfile
    validator: ValidateCompanyProfile


def build_research_pipeline(
    source: ResearchSource,
    *,
    analytics: SemanticAnalytics | None = None,
    extraction_generator: Any | None = None,
    research_agent: Any | None = None,
    known_pim_vendors: list[str] | None = None,
    domain_config: DomainConfig | None = None,
) -> tuple[Pipeline, WorkflowComponents]:
    """Build a single-pass Haystack pipeline with business-purpose component names."""

    components = _build_components(
        source,
        analytics=analytics,
        extraction_generator=extraction_generator,
        research_agent=research_agent,
        known_pim_vendors=known_pim_vendors,
        domain_config=domain_config,
    )
    pipeline = _build_full_pipeline(components)
    return pipeline, components


def _build_components(
    source: ResearchSource,
    *,
    analytics: SemanticAnalytics | None,
    extraction_generator: Any | None,
    research_agent: Any | None,
    known_pim_vendors: list[str] | None,
    domain_config: DomainConfig | None,
) -> WorkflowComponents:
    return WorkflowComponents(
        initializer=InitializeResearch(),
        researcher=ResearchCompany(source, analytics=analytics, agent=research_agent),
        assessor=AssessEvidence(
            analytics=analytics,
            known_pim_vendors=known_pim_vendors,
            domain_config=domain_config,
        ),
        extractor=ExtractCompanyProfile(
            extraction_generator,
            analytics=analytics,
            known_pim_vendors=known_pim_vendors,
            domain_config=domain_config,
        ),
        validator=ValidateCompanyProfile(analytics=analytics, domain_config=domain_config),
    )


def build_workflow_components(
    source: ResearchSource,
    *,
    analytics: SemanticAnalytics | None = None,
    extraction_generator: Any | None = None,
    research_agent: Any | None = None,
    known_pim_vendors: list[str] | None = None,
    domain_config: DomainConfig | None = None,
) -> WorkflowComponents:
    """Create a fresh component set for one pipeline instance."""

    return _build_components(
        source,
        analytics=analytics,
        extraction_generator=extraction_generator,
        research_agent=research_agent,
        known_pim_vendors=known_pim_vendors,
        domain_config=domain_config,
    )


def _build_full_pipeline(components: WorkflowComponents) -> Pipeline:
    pipeline = Pipeline(max_runs_per_component=3)
    pipeline.add_component("research_company", components.researcher)
    pipeline.add_component("assess_evidence", components.assessor)
    pipeline.add_component("extract_company_profile", components.extractor)
    pipeline.add_component("validate_company_profile", components.validator)
    pipeline.connect("research_company.state", "assess_evidence.state")
    pipeline.connect("assess_evidence.state", "extract_company_profile.state")
    pipeline.connect("extract_company_profile.profile", "validate_company_profile.profile")
    pipeline.connect("assess_evidence.state", "validate_company_profile.state")
    return pipeline


def build_research_assessment_pipeline(components: WorkflowComponents) -> Pipeline:
    """Build only the research and assessment phase of the bounded workflow."""

    pipeline = Pipeline(max_runs_per_component=3)
    pipeline.add_component("research_company", components.researcher)
    pipeline.add_component("assess_evidence", components.assessor)
    pipeline.connect("research_company.state", "assess_evidence.state")
    return pipeline


def build_profile_pipeline(components: WorkflowComponents) -> Pipeline:
    """Build the extraction and validation phase for a sufficient state."""

    pipeline = Pipeline(max_runs_per_component=3)
    pipeline.add_component("extract_company_profile", components.extractor)
    pipeline.add_component("validate_company_profile", components.validator)
    pipeline.connect("extract_company_profile.profile", "validate_company_profile.profile")
    return pipeline
