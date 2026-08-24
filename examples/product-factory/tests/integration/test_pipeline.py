import pytest
from product_factory_app.domain.models import CompanyResearchRequest
from product_factory_app.research.sources import FixtureResearchSource
from product_factory_app.workflow.pipeline import build_research_pipeline


@pytest.mark.integration
def test_haystack_pipeline_runs_named_components() -> None:
    request = CompanyResearchRequest(company_name="Acme")
    pipeline, components = build_research_pipeline(FixtureResearchSource("success"))
    state = components.initializer.run(request)["state"]
    result = pipeline.run(
        {"research_company": {"state": state}},
        include_outputs_from={
            "research_company",
            "assess_evidence",
            "extract_company_profile",
            "validate_company_profile",
        },
    )
    assert result["assess_evidence"]["assessment"].sufficient is True
    assert result["validate_company_profile"]["validation"].valid is True
