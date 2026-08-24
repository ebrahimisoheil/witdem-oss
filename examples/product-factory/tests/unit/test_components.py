from product_factory_app.domain.models import (
    CatalogScaleBucket,
    CatalogScaleEstimate,
    CompanyProfile,
    CompanyResearchRequest,
    Evidence,
    PimIdentificationState,
    ProductInformationStack,
    ResearchDimensionState,
    ResearchState,
)
from product_factory_app.research.sources import FixtureResearchSource
from product_factory_app.workflow.components import (
    AssessEvidence,
    ExtractCompanyProfile,
    ResearchCompany,
    ValidateCompanyProfile,
)


class FailingAgent:
    def run(self, **_: object) -> None:
        raise RuntimeError("provider timeout")


def test_assessor_requests_more_evidence_when_catalog_is_missing() -> None:
    state = ResearchState(
        request=CompanyResearchRequest(company_name="Acme"),
        evidence=FixtureResearchSource("incomplete").collect(CompanyResearchRequest(company_name="Acme"), 1),
    )
    result = AssessEvidence().run(state)
    assert result["assessment"].sufficient is False
    assert "catalog_scale" in result["assessment"].missing_signals


def test_assessor_routes_missing_pim_to_targeted_research() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=FixtureResearchSource("targeted_pim_loop").collect(request, 1),
    )

    result = AssessEvidence().run(state)

    assert result["assessment"].sufficient is False
    assert "pim_stack" in result["assessment"].missing_signals
    assert "target_pim_stack" in result["state"].targeted_research_routes
    pim_status = next(item for item in result["assessment"].dimension_statuses if item.dimension == "pim_stack")
    assert pim_status.status == ResearchDimensionState.MISSING
    assert pim_status.next_route == "target_pim_stack"


def test_assessor_accepts_unresolved_pim_after_targeted_attempt() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=FixtureResearchSource("targeted_pim_loop").collect(request, 1),
        route_attempts={"target_pim_stack": 1},
    )

    result = AssessEvidence().run(state)

    assert result["assessment"].sufficient is True
    assert result["state"].termination_reason == "evidence_sufficient_with_uncertainty"
    pim_status = next(item for item in result["assessment"].dimension_statuses if item.dimension == "pim_stack")
    assert pim_status.status == ResearchDimensionState.UNKNOWN_ACCEPTABLE


def test_assessor_accepts_unresolved_catalog_scale_after_targeted_attempt() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=FixtureResearchSource("catalog_size_loop").collect(request, 1),
        route_attempts={"target_catalog_scale": 1},
    )

    result = AssessEvidence().run(state)

    assert result["assessment"].sufficient is True
    assert result["state"].termination_reason == "evidence_sufficient_with_uncertainty"
    scale_status = next(item for item in result["assessment"].dimension_statuses if item.dimension == "catalog_scale")
    assert scale_status.status == ResearchDimensionState.UNKNOWN_ACCEPTABLE
    assert scale_status.reason_code == "catalog_scale_unknown_acceptable_after_targeted_research"


def test_assessor_does_not_accept_unknown_pim_without_core_qualification_support() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=[
            Evidence(
                claim="No reliable public PIM vendor evidence was found for Acme.",
                excerpt="Targeted PIM research did not find a public technology reference.",
                source_url="https://fixture.local/no-public-pim",
                evidence_type="targeted_research_summary",
                qualification_dimension="pim_stack",
                reliability=0.62,
            )
        ],
    )

    result = AssessEvidence().run(state)

    assert result["assessment"].sufficient is False
    assert "catalog_scale" in result["assessment"].missing_signals


def test_research_component_recovers_from_source_failure() -> None:
    source = FixtureResearchSource("recovery")
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(request=request)
    state = ResearchCompany(source).run(state)["state"]
    assert state.evidence == []
    assert state.source_failures == 1
    assert state.last_failure
    state = ResearchCompany(source).run(state)["state"]
    assert state.evidence


def test_research_component_records_agent_provider_failure_for_recovery() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(request=request)

    result = ResearchCompany(FixtureResearchSource("success"), agent=FailingAgent()).run(state)["state"]

    assert result.source_failures == 1
    assert result.last_failure and "provider failed" in result.last_failure


def test_profile_payload_normalizes_common_provider_json_variants() -> None:
    payload = ExtractCompanyProfile._normalize_profile_payload(
        """```json
        {
          "company_name": "Acme",
          "summary": "Acme sells products.",
          "products": ["goods"],
          "catalog_complexity": "complex",
          "markets": ["Germany"],
          "product_factory_fit": "high",
          "qualification": {"fit_score": 0.93, "fit_band": "high"},
          "evidence_ids": ["e1"],
          "findings": [{"profile_field": "products", "value": ["goods"], "evidence_ids": ["e1"], "confidence": "high"}],
          "confidence": "medium"
        }
        ```"""
    )

    assert payload["findings"][0]["field"] == "products"
    assert payload["findings"][0]["value"] == '["goods"]'
    assert payload["confidence"] == 0.5
    assert payload["qualification"]["fit_score"] == 93.0


def test_deterministic_profile_classifies_vendor_neutral_pim_and_scale() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=FixtureResearchSource("targeted_pim_loop").collect(request, 2),
    )

    profile = ExtractCompanyProfile().run(state)["profile"]

    assert profile.pim_stack.pim_vendor == "Salsify"
    assert profile.pim_stack.state == PimIdentificationState.CONFIRMED
    assert profile.pim_stack.confidence == 0.91
    assert profile.pim_stack.evidence_reliability == 0.9
    assert profile.catalog_scale_estimate.scale_bucket == CatalogScaleBucket.VERY_LARGE
    assert profile.catalog_scale_estimate.evidence_reliability > 0
    assert profile.qualification.fit_score > 70
    pim_score = next(
        item
        for item in profile.qualification.dimension_scores
        if item.dimension == "pim_product_information_infrastructure"
    )
    assert pim_score.finding_confidence == profile.pim_stack.confidence
    assert pim_score.evidence_reliability == profile.pim_stack.evidence_reliability


def test_validation_rejects_nested_unknown_evidence_reference() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    state = ResearchState(
        request=request,
        evidence=FixtureResearchSource("success").collect(request, 1),
    )
    evidence_ids = [item.evidence_id for item in state.evidence]
    profile = CompanyProfile(
        company_name="Acme",
        summary="Acme sells products.",
        products=["products"],
        markets=["Germany"],
        catalog_scale="50k+ SKUs",
        catalog_complexity="complex",
        pim_stack=ProductInformationStack(
            pim_vendor="Akeneo",
            pim_product="Akeneo",
            confidence=0.91,
            state=PimIdentificationState.CONFIRMED,
            evidence_ids=["missing"],
        ),
        catalog_scale_estimate=CatalogScaleEstimate(
            scale_bucket=CatalogScaleBucket.VERY_LARGE,
            estimated_range="50k+ SKUs",
            confidence=0.74,
            evidence_ids=evidence_ids[:1],
        ),
        source_data_complexity_signals=["supplier files"],
        evidence_ids=evidence_ids,
    )
    ExtractCompanyProfile._complete_deterministic_fields(profile, state)

    validation = ValidateCompanyProfile().run(profile, state)["validation"]

    assert validation.valid is False
    assert "nested qualification fields reference unknown evidence IDs" in validation.errors


def test_validation_rejects_high_confidence_pim_without_strong_source_support() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    weak_pim_evidence = Evidence(
        claim="A job posting says Acme uses Akeneo PIM.",
        excerpt="The posting asks for Akeneo experience.",
        source_url="https://fixture.local/job",
        source_type="fixture",
        evidence_type="job_posting",
        qualification_dimension="pim_stack",
        reliability=0.62,
    )
    state = ResearchState(
        request=request,
        evidence=[
            weak_pim_evidence,
            *FixtureResearchSource("unknown_pim").collect(request, 1),
        ],
    )
    evidence_ids = [item.evidence_id for item in state.evidence]
    profile = CompanyProfile(
        company_name="Acme",
        summary="Acme sells products.",
        products=["products"],
        markets=["Germany"],
        catalog_scale="50k+ SKUs",
        catalog_complexity="complex",
        pim_stack=ProductInformationStack(
            pim_vendor="Akeneo",
            pim_product="Akeneo",
            confidence=0.91,
            state=PimIdentificationState.CONFIRMED,
            evidence_ids=[weak_pim_evidence.evidence_id],
        ),
        catalog_scale_estimate=CatalogScaleEstimate(
            scale_bucket=CatalogScaleBucket.VERY_LARGE,
            estimated_range="50k+ SKUs",
            confidence=0.74,
            evidence_ids=evidence_ids[1:2],
        ),
        source_data_complexity_signals=["supplier files"],
        evidence_ids=evidence_ids,
    )
    ExtractCompanyProfile._complete_deterministic_fields(profile, state)

    validation = ValidateCompanyProfile().run(profile, state)["validation"]

    assert validation.valid is False
    assert "high-confidence PIM identification requires strong source support" in validation.errors
