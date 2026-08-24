"""Deterministic and URL-based research sources used by the workflow."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from time import sleep
from typing import Protocol
from urllib.request import Request, urlopen

from haystack.tools import Tool  # type: ignore[attr-defined]
from pydantic import AnyHttpUrl, TypeAdapter

from product_factory_app.domain.models import CompanyResearchRequest, Evidence


class ResearchSourceError(RuntimeError):
    """Raised when a research source cannot return evidence."""


class ResearchAgentError(ResearchSourceError):
    """Raised when a live research agent fails after incurring provider usage."""

    def __init__(self, message: str, usage: dict[str, float]) -> None:
        super().__init__(message)
        self.usage = usage


class ResearchSource(Protocol):
    """Boundary for a source that returns evidence for one research pass."""

    def collect(self, request: CompanyResearchRequest, pass_number: int) -> list[Evidence]: ...


@dataclass
class FixtureResearchSource:
    """Predictable research source for deterministic tests and local runs."""

    scenario: str = "success"
    cost_per_pass_usd: float = 0.0
    latency_seconds: float = 0.0

    def collect(self, request: CompanyResearchRequest, pass_number: int) -> list[Evidence]:
        if self.latency_seconds > 0:
            sleep(self.latency_seconds)
        company = request.company_name
        url = str(request.website_url or f"https://fixture.local/{company.lower().replace(' ', '-')}")
        if self.scenario in {"recovery", "tool_failure"} and pass_number == 1:
            raise ResearchSourceError("fixture source unavailable on first pass")
        if self.scenario in {"terminal", "terminal_failure"}:
            raise ResearchSourceError("fixture source permanently unavailable")
        base = [
            Evidence(
                claim=f"{company} sells a broad range of physical consumer products.",
                excerpt=f"{company} operates a product catalog spanning several product families.",
                source_url=url,
                source_title="Company overview",
                source_type="fixture",
                evidence_type="official_company_page",
                observed_fact="Company operates a product catalog.",
                qualification_dimension="product_catalog",
                relevance=0.85,
                reliability=0.85,
            ),
            Evidence(
                claim=f"{company} serves multiple markets and supports international commerce.",
                excerpt="The company lists operations across Germany, France, and the United Kingdom.",
                source_url=url,
                source_title="Markets",
                source_type="fixture",
                evidence_type="official_company_page",
                observed_fact="Company serves Germany, France, and the United Kingdom.",
                qualification_dimension="company_identity",
                relevance=0.8,
                reliability=0.8,
            ),
        ]
        if self.scenario in {"incomplete", "missing_evidence"} and pass_number == 1:
            return base[:1]
        if self.scenario in {"unknown_pim"}:
            return base + [
                Evidence(
                    claim=f"{company} has a large catalog with 50,000 SKUs and regional assortments.",
                    excerpt=(
                        "The public catalog references 50,000 SKUs, regional assortments, variants, "
                        "and localized attributes."
                    ),
                    source_url=f"{url}/catalog",
                    source_title="Catalog profile",
                    source_type="fixture",
                    evidence_type="catalog_page",
                    observed_fact="Large catalog with variants and localized attributes.",
                    qualification_dimension="catalog_scale",
                    relevance=0.86,
                    reliability=0.82,
                ),
                Evidence(
                    claim=f"{company} receives supplier catalogs, PDFs, CSV files, and product feeds for enrichment.",
                    excerpt=(
                        "Supplier onboarding describes PDFs, CSV files, product feeds, inconsistent attributes, "
                        "and manual enrichment."
                    ),
                    source_url=f"{url}/supplier-onboarding",
                    source_title="Supplier onboarding",
                    source_type="fixture",
                    evidence_type="supplier_workflow",
                    observed_fact="Supplier source data requires enrichment.",
                    qualification_dimension="source_data_complexity",
                    relevance=0.9,
                    reliability=0.8,
                ),
                Evidence(
                    claim=f"No reliable public PIM vendor evidence was found for {company}.",
                    excerpt=(
                        "Targeted PIM research did not find a case study, job posting, partner page, "
                        "or technology reference naming a PIM."
                    ),
                    source_url="https://fixture.local/no-public-pim",
                    source_title="Targeted PIM research summary",
                    source_type="fixture",
                    evidence_type="targeted_research_summary",
                    observed_fact="PIM vendor remains unknown.",
                    qualification_dimension="pim_stack",
                    relevance=0.7,
                    reliability=0.62,
                ),
            ]
        if self.scenario in {"targeted_pim_loop"}:
            if pass_number == 1:
                return base + [
                    Evidence(
                        claim=f"{company} has a complex catalog with tens of thousands of variants.",
                        excerpt="The catalog includes variants, multilingual attributes, and regional assortments.",
                        source_url=f"{url}/catalog",
                        source_title="Catalog information",
                        source_type="fixture",
                        evidence_type="catalog_page",
                        observed_fact="Complex large catalog without public PIM identification.",
                        qualification_dimension="catalog_scale",
                        relevance=0.84,
                        reliability=0.82,
                    ),
                    Evidence(
                        claim=f"{company} processes supplier CSV files, PDFs, and feeds for product enrichment.",
                        excerpt=(
                            "Supplier documentation references CSV files, PDF catalogs, product feeds, "
                            "and manual enrichment."
                        ),
                        source_url=f"{url}/suppliers",
                        source_title="Supplier workflow",
                        source_type="fixture",
                        evidence_type="supplier_workflow",
                        observed_fact="Supplier source data is fragmented.",
                        qualification_dimension="source_data_complexity",
                        relevance=0.86,
                        reliability=0.8,
                    ),
                ]
            return base + [
                Evidence(
                    claim=f"A vendor case study confirms {company} uses Salsify as its PIM.",
                    excerpt=f"The case study says {company} uses Salsify for product information management.",
                    source_url="https://fixture.local/salsify-case-study",
                    source_title="Salsify customer story",
                    source_type="fixture",
                    evidence_type="vendor_case_study",
                    observed_fact="Salsify is used as the PIM.",
                    qualification_dimension="pim_stack",
                    relevance=0.95,
                    reliability=0.9,
                ),
                Evidence(
                    claim=f"{company} has a complex catalog with tens of thousands of variants.",
                    excerpt="The catalog includes variants, multilingual attributes, and regional assortments.",
                    source_url=f"{url}/catalog",
                    source_title="Catalog information",
                    source_type="fixture",
                    evidence_type="catalog_page",
                    observed_fact="Complex large catalog.",
                    qualification_dimension="catalog_scale",
                    relevance=0.84,
                    reliability=0.82,
                ),
                Evidence(
                    claim=f"{company} processes supplier CSV files, PDFs, and feeds for product enrichment.",
                    excerpt=(
                        "Supplier documentation references CSV files, PDF catalogs, product feeds, "
                        "and manual enrichment."
                    ),
                    source_url=f"{url}/suppliers",
                    source_title="Supplier workflow",
                    source_type="fixture",
                    evidence_type="supplier_workflow",
                    observed_fact="Supplier source data is fragmented.",
                    qualification_dimension="source_data_complexity",
                    relevance=0.86,
                    reliability=0.8,
                ),
            ]
        if self.scenario in {"catalog_size_loop"}:
            if pass_number == 1:
                return base + [
                    Evidence(
                        claim=f"An implementation partner says {company} uses Akeneo PIM.",
                        excerpt=(
                            f"The partner page names Akeneo as {company}'s product information management platform."
                        ),
                        source_url="https://fixture.local/akeneo-partner",
                        source_title="Implementation partner",
                        source_type="fixture",
                        evidence_type="partner_reference",
                        observed_fact="Akeneo PIM is used.",
                        qualification_dimension="pim_stack",
                        relevance=0.88,
                        reliability=0.82,
                    ),
                    Evidence(
                        claim=f"{company} has complex multilingual product attributes.",
                        excerpt="The catalog contains variants, localized attributes, and regional assortments.",
                        source_url=f"{url}/attributes",
                        source_title="Attribute model",
                        source_type="fixture",
                        evidence_type="catalog_page",
                        observed_fact="Catalog is complex, but scale is not stated.",
                        qualification_dimension="catalog_complexity",
                        relevance=0.82,
                        reliability=0.78,
                    ),
                    Evidence(
                        claim=f"{company} processes supplier spreadsheets and PDF catalogs.",
                        excerpt=(
                            "Supplier workflow pages mention Excel files, PDF catalogs, and inconsistent attributes."
                        ),
                        source_url=f"{url}/suppliers",
                        source_title="Supplier workflow",
                        source_type="fixture",
                        evidence_type="supplier_workflow",
                        observed_fact="Supplier source data is messy.",
                        qualification_dimension="source_data_complexity",
                        relevance=0.84,
                        reliability=0.78,
                    ),
                ]
            return base + [
                Evidence(
                    claim=f"An implementation partner says {company} uses Akeneo PIM.",
                    excerpt=f"The partner page names Akeneo as {company}'s product information management platform.",
                    source_url="https://fixture.local/akeneo-partner",
                    source_title="Implementation partner",
                    source_type="fixture",
                    evidence_type="partner_reference",
                    observed_fact="Akeneo PIM is used.",
                    qualification_dimension="pim_stack",
                    relevance=0.88,
                    reliability=0.82,
                ),
                Evidence(
                    claim=f"{company} publishes a product count of 100,000 SKUs.",
                    excerpt=(
                        "A catalog operations page states the team manages 100,000 SKUs across localized "
                        "regional assortments."
                    ),
                    source_url=f"{url}/catalog-operations",
                    source_title="Catalog operations",
                    source_type="fixture",
                    evidence_type="catalog_operations_page",
                    observed_fact="100,000 SKUs.",
                    qualification_dimension="catalog_scale",
                    relevance=0.93,
                    reliability=0.86,
                ),
                Evidence(
                    claim=f"{company} processes supplier spreadsheets and PDF catalogs.",
                    excerpt="Supplier workflow pages mention Excel files, PDF catalogs, and inconsistent attributes.",
                    source_url=f"{url}/suppliers",
                    source_title="Supplier workflow",
                    source_type="fixture",
                    evidence_type="supplier_workflow",
                    observed_fact="Supplier source data is messy.",
                    qualification_dimension="source_data_complexity",
                    relevance=0.84,
                    reliability=0.78,
                ),
            ]
        if self.scenario in {"conflicting_pim", "conflicting_pim_evidence"}:
            return base + [
                Evidence(
                    claim=f"Conflicting public evidence says {company} uses Akeneo PIM.",
                    excerpt="A historical partner page names Akeneo in a product-information implementation.",
                    source_url="https://fixture.local/akeneo-historical",
                    source_title="Historical partner reference",
                    source_type="fixture",
                    evidence_type="partner_reference",
                    observed_fact="Akeneo appears in a historical reference.",
                    qualification_dimension="pim_stack",
                    relevance=0.75,
                    reliability=0.58,
                ),
                Evidence(
                    claim=f"Conflicting public evidence says {company} uses Salsify PIM.",
                    excerpt="A job posting asks for Salsify PIM experience for catalog operations.",
                    source_url="https://fixture.local/salsify-job",
                    source_title="Catalog job posting",
                    source_type="fixture",
                    evidence_type="job_posting",
                    observed_fact="Salsify appears in a catalog operations job posting.",
                    qualification_dimension="pim_stack",
                    relevance=0.78,
                    reliability=0.62,
                ),
                Evidence(
                    claim=f"{company} has 50,000 SKUs and a complex catalog with variants.",
                    excerpt="Marketplace data describes 50,000 SKUs, variants, and multilingual product content.",
                    source_url="https://fixture.local/marketplace",
                    source_title="Marketplace profile",
                    source_type="fixture",
                    evidence_type="marketplace_listing",
                    observed_fact="Large complex catalog.",
                    qualification_dimension="catalog_scale",
                    relevance=0.84,
                    reliability=0.72,
                ),
                Evidence(
                    claim=f"{company} receives supplier spreadsheets, CSV files, and PDF catalogs.",
                    excerpt=(
                        "Supplier onboarding mentions spreadsheets, CSV files, PDFs, "
                        "and incomplete product information."
                    ),
                    source_url=f"{url}/supplier-onboarding",
                    source_title="Supplier onboarding",
                    source_type="fixture",
                    evidence_type="supplier_workflow",
                    observed_fact="Messy supplier source data.",
                    qualification_dimension="source_data_complexity",
                    relevance=0.86,
                    reliability=0.78,
                ),
            ]
        if self.scenario in {"weak_fit", "small_simple"}:
            return [
                Evidence(
                    claim=f"{company} sells a small catalog of 500 SKUs in one market.",
                    excerpt="The company presents a small catalog and simple product operation in Germany.",
                    source_url=url,
                    source_title="Company overview",
                    source_type="fixture",
                    evidence_type="official_company_page",
                    observed_fact="Small one-market catalog.",
                    qualification_dimension="catalog_scale",
                    relevance=0.75,
                    reliability=0.82,
                ),
                Evidence(
                    claim=f"{company} has a simple product operation with no public PIM evidence.",
                    excerpt="The product pages do not show supplier feeds, enrichment workflow, or PIM references.",
                    source_url=url,
                    source_title="Product pages",
                    source_type="fixture",
                    evidence_type="official_company_page",
                    observed_fact="Simple product-information operation.",
                    qualification_dimension="catalog_complexity",
                    relevance=0.66,
                    reliability=0.72,
                ),
                Evidence(
                    claim=f"No reliable public PIM vendor evidence was found for {company}.",
                    excerpt=(
                        "Targeted PIM research did not find a case study, partner page, job posting, "
                        "or public technology reference."
                    ),
                    source_url="https://fixture.local/no-public-pim",
                    source_title="Targeted PIM research summary",
                    source_type="fixture",
                    evidence_type="targeted_research_summary",
                    observed_fact="PIM vendor remains unknown.",
                    qualification_dimension="pim_stack",
                    relevance=0.62,
                    reliability=0.62,
                ),
            ]
        if self.scenario in {"conflict", "conflicting_sources"}:
            return base + [
                Evidence(
                    claim=f"{company} has approximately 500 SKUs.",
                    excerpt="A partner page describes a catalog of around 500 SKUs.",
                    source_url="https://fixture.local/partner",
                    source_title="Partner page",
                    source_type="fixture",
                    reliability=0.45,
                ),
                Evidence(
                    claim=f"{company} has approximately 50,000 SKUs.",
                    excerpt="A marketplace profile describes a catalog of around 50,000 SKUs.",
                    source_url="https://fixture.local/marketplace",
                    source_title="Marketplace profile",
                    source_type="fixture",
                    reliability=0.55,
                ),
            ]
        if self.scenario not in {"incomplete", "missing_evidence", "recovery", "tool_failure"}:
            base.extend(
                [
                    Evidence(
                        claim=f"{company} has a complex catalog with tens of thousands of variants.",
                        excerpt="The catalog includes many variants, localizations, and attribute combinations.",
                        source_url=url,
                        source_title="Catalog information",
                        source_type="fixture",
                        evidence_type="catalog_page",
                        observed_fact="Catalog includes many variants and localized attributes.",
                        qualification_dimension="catalog_complexity",
                        relevance=0.84,
                        reliability=0.82,
                    ),
                    Evidence(
                        claim=f"A case study confirms {company} uses Akeneo PIM for product-information management.",
                        excerpt="Product information is maintained in Akeneo PIM as a centralized catalog workflow.",
                        source_url=url,
                        source_title="Technology information",
                        source_type="fixture",
                        evidence_type="vendor_case_study",
                        observed_fact="Akeneo PIM supports centralized product information.",
                        qualification_dimension="pim_stack",
                        relevance=0.9,
                        reliability=0.86,
                    ),
                    Evidence(
                        claim=f"{company} receives supplier spreadsheets, CSV files, PDF catalogs, and product feeds.",
                        excerpt=(
                            "Supplier onboarding mentions spreadsheets, CSV files, PDF catalogs, feeds, "
                            "and incomplete product information."
                        ),
                        source_url=f"{url}/supplier-onboarding",
                        source_title="Supplier onboarding",
                        source_type="fixture",
                        evidence_type="supplier_workflow",
                        observed_fact="Supplier source data is fragmented and inconsistent.",
                        qualification_dimension="source_data_complexity",
                        relevance=0.88,
                        reliability=0.8,
                    ),
                ]
            )
        if self.scenario in {"incomplete", "missing_evidence", "recovery", "tool_failure"}:
            base.extend(
                [
                    Evidence(
                        claim=f"{company} has a complex catalog with tens of thousands of variants.",
                        excerpt="The catalog includes many variants, localizations, and attribute combinations.",
                        source_url=url,
                        source_title="Catalog information",
                        source_type="fixture",
                        evidence_type="catalog_page",
                        observed_fact="Catalog includes many variants and localized attributes.",
                        qualification_dimension="catalog_complexity",
                        relevance=0.84,
                        reliability=0.82,
                    ),
                    Evidence(
                        claim=f"A case study confirms {company} uses Akeneo PIM for product-information management.",
                        excerpt="Product information is maintained in Akeneo PIM as a centralized catalog workflow.",
                        source_url=url,
                        source_title="Technology information",
                        source_type="fixture",
                        evidence_type="vendor_case_study",
                        observed_fact="Akeneo PIM supports centralized product information.",
                        qualification_dimension="pim_stack",
                        relevance=0.9,
                        reliability=0.86,
                    ),
                    Evidence(
                        claim=f"{company} receives supplier spreadsheets, CSV files, PDF catalogs, and product feeds.",
                        excerpt=(
                            "Supplier onboarding mentions spreadsheets, CSV files, PDF catalogs, feeds, "
                            "and incomplete product information."
                        ),
                        source_url=f"{url}/supplier-onboarding",
                        source_title="Supplier onboarding",
                        source_type="fixture",
                        evidence_type="supplier_workflow",
                        observed_fact="Supplier source data is fragmented and inconsistent.",
                        qualification_dimension="source_data_complexity",
                        relevance=0.88,
                        reliability=0.8,
                    ),
                ]
            )
        return base

    def instrumentation(self) -> dict[str, float]:
        """Return deterministic source instrumentation for the current run."""

        return {
            "fixture_cost_usd": self.cost_per_pass_usd,
            "fixture_latency_seconds": self.latency_seconds,
        }


class WebsiteResearchSource:
    """Small no-key URL fetcher for live smoke tests and supplied company sites."""

    @staticmethod
    def _snippet(text: str, terms: tuple[str, ...]) -> str:
        lowered = text.lower()
        for term in terms:
            position = lowered.find(term)
            if position >= 0:
                return text[max(0, position - 240) : position + 560]
        return text[:800]

    def collect(self, request: CompanyResearchRequest, pass_number: int) -> list[Evidence]:
        if request.website_url is None:
            raise ResearchSourceError("website research requires --website")
        url = str(request.website_url)
        try:
            response = urlopen(Request(url, headers={"User-Agent": "ProductFactoryAgent/0.1"}), timeout=15)
            raw = response.read(250_000).decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - depends on the live network
            raise ResearchSourceError(f"could not fetch {url}: {exc}") from exc
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()
        excerpt = text[:4000]
        evidence = [
            Evidence(
                claim=f"{request.company_name} has publicly available company information at its supplied website.",
                excerpt=excerpt,
                source_url=url,
                source_title="Supplied company website",
                source_type="website",
                reliability=0.65,
            )
        ]
        signal_definitions = (
            (
                f"{request.company_name} presents products or product collections on its public website.",
                ("product", "shop", "collection", "store"),
                0.7,
            ),
            (
                f"{request.company_name} presents a product-range or catalog signal on its public website.",
                ("catalog", "catalogue", "product range", "collection", "variety", "products"),
                0.62,
            ),
            (
                f"{request.company_name} publishes a public signal about serving multiple markets or locations.",
                ("market", "country", "countries", "global", "worldwide", "international", "locations"),
                0.62,
            ),
        )
        for claim, terms, reliability in signal_definitions:
            if any(term in text.lower() for term in terms):
                evidence.append(
                    Evidence(
                        claim=claim,
                        excerpt=self._snippet(text, terms),
                        source_url=url,
                        source_title="Supplied company website",
                        source_type="website",
                        reliability=reliability,
                    )
                )
        return evidence


def build_research_tool(source: ResearchSource) -> Tool:
    """Wrap a source as a Haystack Tool for Agent-based research."""

    def collect(company_name: str, website_url: str | None = None, pass_number: int = 1) -> str:
        request = CompanyResearchRequest(
            company_name=company_name,
            website_url=TypeAdapter(AnyHttpUrl).validate_python(website_url) if website_url else None,
            research_mode="website" if website_url else "fixture",  # type: ignore[arg-type]
        )
        evidence = source.collect(request, pass_number)
        return json.dumps([item.model_dump(mode="json") for item in evidence], ensure_ascii=False)

    return Tool(
        name="collect_company_evidence",
        description="Collect public company and catalog evidence for a research pass.",
        parameters={
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "Company to research."},
                "website_url": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Optional company website.",
                },
                "pass_number": {"type": "integer", "description": "Current research pass."},
            },
            "required": ["company_name", "pass_number"],
            "additionalProperties": False,
        },
        function=collect,
    )
