import pytest
from product_factory_app.domain.models import CompanyProfile, CompanyResearchRequest
from pydantic import ValidationError


def test_company_request_normalizes_name() -> None:
    request = CompanyResearchRequest(company_name="  Acme  ")
    assert request.company_name == "Acme"


def test_company_request_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        CompanyResearchRequest(company_name="  ")


def test_profile_is_machine_readable() -> None:
    profile = CompanyProfile(company_name="Acme", summary="A company", products=["goods"])
    assert profile.model_dump(mode="json")["company_name"] == "Acme"


def test_profile_schema_is_strict_for_provider_structured_output() -> None:
    schema = CompanyProfile.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["ProfileFinding"]["additionalProperties"] is False
