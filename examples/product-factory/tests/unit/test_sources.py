import pytest
from product_factory_app.domain.models import CompanyResearchRequest
from product_factory_app.research.sources import FixtureResearchSource, ResearchSourceError, WebsiteResearchSource


def test_fixture_success_has_catalog_evidence() -> None:
    evidence = FixtureResearchSource("success").collect(CompanyResearchRequest(company_name="Acme"), 1)
    assert any("complex catalog" in item.claim for item in evidence)


def test_fixture_recovery_fails_then_succeeds() -> None:
    source = FixtureResearchSource("recovery")
    request = CompanyResearchRequest(company_name="Acme")
    with pytest.raises(ResearchSourceError):
        source.collect(request, 1)
    assert source.collect(request, 2)


def test_fixture_terminal_always_fails() -> None:
    with pytest.raises(ResearchSourceError):
        FixtureResearchSource("terminal").collect(CompanyResearchRequest(company_name="Acme"), 1)


def test_website_source_extracts_live_research_signals_without_inventing_provider_data(monkeypatch) -> None:
    class FakeResponse:
        def read(self, _: int) -> bytes:
            return b"Global products and collections for customers in many countries."

    monkeypatch.setattr(
        "product_factory_app.research.sources.urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    evidence = WebsiteResearchSource().collect(
        CompanyResearchRequest(company_name="Acme", website_url="https://acme.example"),
        1,
    )

    claims = " ".join(item.claim.lower() for item in evidence)
    assert "products" in claims
    assert "product-range" in claims
    assert "markets" in claims
