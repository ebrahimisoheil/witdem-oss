"""Small source-backed real-company corpus for qualification stress runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, TypeAdapter

from product_factory_app.config import Settings
from product_factory_app.domain.models import CompanyResearchRequest, Evidence, ResearchMode, ResearchRun
from product_factory_app.persistence.store import RunStore
from product_factory_app.service import ResearchService


@dataclass(frozen=True)
class RealCompanyEvidence:
    """One curated public evidence item for a real company."""

    pass_available: int
    claim: str
    excerpt: str
    source_url: str
    source_title: str
    source_type: str
    evidence_type: str
    observed_fact: str
    qualification_dimension: str
    reliability: float
    relevance: float


@dataclass(frozen=True)
class RealCompanyCase:
    """A real company and the public evidence used to stress qualification paths."""

    company_name: str
    website_url: str
    expected_shape: str
    evidence: tuple[RealCompanyEvidence, ...]


class RealCompanyCorpusSource:
    """Return source-backed evidence records for one real-company corpus case."""

    def __init__(self, case: RealCompanyCase) -> None:
        self.case = case

    def collect(self, request: CompanyResearchRequest, pass_number: int) -> list[Evidence]:
        return [
            Evidence(
                claim=item.claim,
                excerpt=item.excerpt,
                source_url=item.source_url,
                source_title=item.source_title,
                source_type=item.source_type,
                evidence_type=item.evidence_type,
                observed_fact=item.observed_fact,
                qualification_dimension=item.qualification_dimension,
                reliability=item.reliability,
                relevance=item.relevance,
            )
            for item in self.case.evidence
            if item.pass_available <= pass_number
        ]

    def instrumentation(self) -> dict[str, float]:
        return {"fixture_cost_usd": 0.0, "fixture_latency_seconds": 0.0}


REAL_COMPANY_CASES: tuple[RealCompanyCase, ...] = (
    RealCompanyCase(
        company_name="The Paper Store",
        website_url="https://www.thepaperstore.com/",
        expected_shape="known Akeneo PIM, very large catalog, fragmented source data, targeted PIM on pass 2",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim="The Paper Store operates a specialty retail product catalog across stores and ecommerce.",
                excerpt=(
                    "Akeneo describes The Paper Store as a specialty retail business with multiple locations, "
                    "an ecommerce site, and a nationwide family of brands."
                ),
                source_url="https://www.akeneo.com/customer-story/the-paper-store/",
                source_title="The Paper Store | Akeneo",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="The company has a retail product catalog and multiple brands.",
                qualification_dimension="product_catalog",
                reliability=0.9,
                relevance=0.9,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="The Paper Store has a very large catalog of 500,000 SKUs.",
                excerpt="Akeneo states that The Paper Store transformed a 500,000-SKU catalog.",
                source_url="https://www.akeneo.com/customer-story/the-paper-store/",
                source_title="The Paper Store | Akeneo",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="500,000 SKUs.",
                qualification_dimension="catalog_scale",
                reliability=0.9,
                relevance=0.95,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim=(
                    "The Paper Store had fragmented spreadsheets, ERP data, supplier onboarding, "
                    "and UPC-to-SKU complexity."
                ),
                excerpt=(
                    "The case study describes hot data migrated out of spreadsheets and ERP, many suppliers, "
                    "and complex UPC-to-SKU mapping."
                ),
                source_url="https://www.akeneo.com/customer-story/the-paper-store/",
                source_title="The Paper Store | Akeneo",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact=(
                    "Fragmented spreadsheets, ERP source data, supplier onboarding, and SKU mapping complexity."
                ),
                qualification_dimension="source_data_complexity",
                reliability=0.9,
                relevance=0.95,
            ),
            RealCompanyEvidence(
                pass_available=2,
                claim="The Paper Store adopted Akeneo PIM and SDM.",
                excerpt="Akeneo says The Paper Store adopted Akeneo PIM and SDM to centralize product data.",
                source_url="https://www.akeneo.com/customer-story/the-paper-store/",
                source_title="The Paper Store | Akeneo",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Akeneo PIM and SDM are used.",
                qualification_dimension="pim_stack",
                reliability=0.9,
                relevance=0.95,
            ),
        ),
    ),
    RealCompanyCase(
        company_name="BTC Europe",
        website_url="https://www.btc-europe.com/",
        expected_shape="known Pimcore PIM, very large data objects, multiple source systems",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim=(
                    "BTC Europe distributes chemical products in the DACH market and has a very large "
                    "product-data operation."
                ),
                excerpt="Pimcore describes BTC Europe as a BASF subsidiary distributing chemical products in DACH.",
                source_url="https://pimcore.com/en/customers/btc-europe-gmbh_c424299",
                source_title="Central PIM for Consolidation of group-wide Data Sources",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="BTC Europe distributes chemical products.",
                qualification_dimension="product_catalog",
                reliability=0.9,
                relevance=0.88,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="BTC Europe uses Pimcore PIM as a central product information management system.",
                excerpt="The Pimcore case study says BTC's PIM is the single source of truth for product information.",
                source_url="https://pimcore.com/en/customers/btc-europe-gmbh_c424299",
                source_title="Central PIM for Consolidation of group-wide Data Sources",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Pimcore PIM is used.",
                qualification_dimension="pim_stack",
                reliability=0.9,
                relevance=0.95,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="BTC Europe has a very large catalog data estate with 2.5M data objects.",
                excerpt="The case study reports 2.5M data objects.",
                source_url="https://pimcore.com/en/customers/btc-europe-gmbh_c424299",
                source_title="Central PIM for Consolidation of group-wide Data Sources",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="2.5M data objects.",
                qualification_dimension="catalog_scale",
                reliability=0.9,
                relevance=0.92,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="BTC Europe consolidated SAP/R3, Kafka, data warehouse, Excel lists, and product hierarchies.",
                excerpt=(
                    "The case study mentions 2x SAP/R3, Kafka Event Bus, DataWarehouse, Excel lists, "
                    "and product hierarchies."
                ),
                source_url="https://pimcore.com/en/customers/btc-europe-gmbh_c424299",
                source_title="Central PIM for Consolidation of group-wide Data Sources",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Multiple source systems and Excel imports were consolidated.",
                qualification_dimension="source_data_complexity",
                reliability=0.9,
                relevance=0.95,
            ),
        ),
    ),
    RealCompanyCase(
        company_name="Beauty Works",
        website_url="https://beautyworksonline.com/",
        expected_shape="known Pimcore PIM, smaller catalog, B2B/B2C, Excel source complexity",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim="Beauty Works sells luxury hair products through B2C and B2B platforms.",
                excerpt="Pimcore describes Beauty Works as a luxury hair solutions brand with B2C and B2B platforms.",
                source_url="https://pimcore.com/en/customers/beauty-works_c485183",
                source_title="Improving Data Management and Responsiveness Across B2B and B2C Platforms with Pimcore",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Beauty Works has B2C and B2B product platforms.",
                qualification_dimension="product_catalog",
                reliability=0.9,
                relevance=0.86,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="Beauty Works implemented Pimcore PIM/MDM.",
                excerpt="The Pimcore case study lists products used as PIM/MDM.",
                source_url="https://pimcore.com/en/customers/beauty-works_c485183",
                source_title="Improving Data Management and Responsiveness Across B2B and B2C Platforms with Pimcore",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Pimcore PIM/MDM is used.",
                qualification_dimension="pim_stack",
                reliability=0.9,
                relevance=0.95,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="Beauty Works has a smaller catalog of 3,200 products and 80 product attributes.",
                excerpt="The Pimcore case study lists 3,200 products and 80 product attributes.",
                source_url="https://pimcore.com/en/customers/beauty-works_c485183",
                source_title="Improving Data Management and Responsiveness Across B2B and B2C Platforms with Pimcore",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="3,200 products and 80 attributes.",
                qualification_dimension="catalog_scale",
                reliability=0.9,
                relevance=0.85,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="Beauty Works had scattered product data across NetSuite and Excel sheets.",
                excerpt="The case study describes product data scattered across NetSuite and various Excel sheets.",
                source_url="https://pimcore.com/en/customers/beauty-works_c485183",
                source_title="Improving Data Management and Responsiveness Across B2B and B2C Platforms with Pimcore",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Product data was scattered across NetSuite and Excel sheets.",
                qualification_dimension="source_data_complexity",
                reliability=0.9,
                relevance=0.92,
            ),
        ),
    ),
    RealCompanyCase(
        company_name="Wahl Clipper",
        website_url="https://wahlusa.com/",
        expected_shape="known Salsify PIM, global teams, duplicate data, catalog scale unresolved",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim="Wahl Clipper sells professional, consumer, and animal grooming products globally.",
                excerpt="Salsify describes Wahl as a world leader in clipping and personal care products.",
                source_url="https://www.salsify.com/resources/case-study/wahl-clipper-pim-drives-data-consistency",
                source_title="Wahl Clipper Implements Product Information Management on a Global Scale",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Wahl sells grooming products across professional, consumer, and animal markets.",
                qualification_dimension="product_catalog",
                reliability=0.9,
                relevance=0.87,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="Wahl Clipper implemented Salsify for product information management.",
                excerpt="The Salsify case study says Wahl implemented Salsify and later reviewed instances globally.",
                source_url="https://www.salsify.com/resources/case-study/wahl-clipper-pim-drives-data-consistency",
                source_title="Wahl Clipper Implements Product Information Management on a Global Scale",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Salsify is used for PIM.",
                qualification_dimension="pim_stack",
                reliability=0.9,
                relevance=0.95,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim=(
                    "Wahl Clipper had independent systems across 19 global teams with duplicate and inconsistent data."
                ),
                excerpt=(
                    "The case study describes 19 global teams, independent systems, duplicate work, "
                    "data inconsistencies, and separate instances."
                ),
                source_url="https://www.salsify.com/resources/case-study/wahl-clipper-pim-drives-data-consistency",
                source_title="Wahl Clipper Implements Product Information Management on a Global Scale",
                source_type="vendor",
                evidence_type="vendor_case_study",
                observed_fact="Global teams had duplicate and inconsistent product data.",
                qualification_dimension="source_data_complexity",
                reliability=0.9,
                relevance=0.94,
            ),
        ),
    ),
    RealCompanyCase(
        company_name="IKEA",
        website_url="https://www.ikea.com/global/en/",
        expected_shape="very large multi-market company, public PIM evidence sparse",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim="IKEA sells a wide range of home furnishing products.",
                excerpt="IKEA states its business idea is to offer a wide range of home furnishing products.",
                source_url="https://www.ikea.com/global/en/our-business/how-we-work/",
                source_title="How we work - IKEA Global",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="IKEA offers a wide range of home furnishing products.",
                qualification_dimension="product_catalog",
                reliability=0.88,
                relevance=0.86,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="IKEA operates across 504 stores in 63 markets.",
                excerpt="IKEA Global reports 504 IKEA stores in 63 markets.",
                source_url="https://www.ikea.com/global/en/our-business/how-we-work/",
                source_title="How we work - IKEA Global",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="504 stores in 63 markets.",
                qualification_dimension="company_identity",
                reliability=0.88,
                relevance=0.92,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="IKEA has multilingual market sites across many countries.",
                excerpt="IKEA's global sitemap lists markets and languages across Africa, Asia, and other regions.",
                source_url="https://www.ikea.com/global/en/sitemap/",
                source_title="Sitemap - IKEA Global",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="IKEA operates multilingual market sites.",
                qualification_dimension="catalog_complexity",
                reliability=0.84,
                relevance=0.78,
            ),
            RealCompanyEvidence(
                pass_available=2,
                claim="No reliable public PIM vendor evidence was found for IKEA in this corpus.",
                excerpt=(
                    "The corpus includes official IKEA market and product-range evidence but no PIM vendor reference."
                ),
                source_url="https://www.ikea.com/global/en/",
                source_title="IKEA Global",
                source_type="official",
                evidence_type="targeted_research_summary",
                observed_fact="PIM vendor remains unknown in public corpus evidence.",
                qualification_dimension="pim_stack",
                reliability=0.58,
                relevance=0.7,
            ),
        ),
    ),
    RealCompanyCase(
        company_name="Bellroy",
        website_url="https://bellroy.com/",
        expected_shape="smaller/specialty catalog, multi-market, public PIM and source-data evidence sparse",
        evidence=(
            RealCompanyEvidence(
                pass_available=1,
                claim=(
                    "Bellroy sells wallets, bags, phone cases, luggage, work bags, travel gear, tech, and accessories."
                ),
                excerpt="Bellroy's homepage and collections list wallets, bags, phone cases, luggage, and accessories.",
                source_url="https://bellroy.com/",
                source_title="Bellroy | Considered Carry Goods",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="Bellroy has a specialty carry-goods product catalog.",
                qualification_dimension="product_catalog",
                reliability=0.88,
                relevance=0.86,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim="Bellroy has a specialty catalog with many product categories and collection variants.",
                excerpt="Bellroy's sitemap lists products, collections, colorways, categories, and value sets.",
                source_url="https://bellroy.com/sitemap",
                source_title="Bellroy Sitemap",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="Product categories, collections, and colorways are public.",
                qualification_dimension="catalog_complexity",
                reliability=0.84,
                relevance=0.78,
            ),
            RealCompanyEvidence(
                pass_available=1,
                claim=(
                    "Bellroy ships to 187 countries, translates content into seven languages, "
                    "and works with about 1000 retailers."
                ),
                excerpt=(
                    "Bellroy says it ships directly to 187 countries, translates content into seven languages, "
                    "and partners with approximately 1000 retailers globally."
                ),
                source_url="https://bellroy.com/responsible-business",
                source_title="Our approach to business as a force for good | Bellroy",
                source_type="official",
                evidence_type="official_company_page",
                observed_fact="Bellroy has multi-country, multilingual, retailer complexity.",
                qualification_dimension="company_identity",
                reliability=0.88,
                relevance=0.9,
            ),
            RealCompanyEvidence(
                pass_available=2,
                claim="No reliable public PIM vendor evidence was found for Bellroy in this corpus.",
                excerpt=(
                    "The corpus includes official catalog, market, and retailer evidence but no public "
                    "PIM vendor reference."
                ),
                source_url="https://bellroy.com/",
                source_title="Bellroy",
                source_type="official",
                evidence_type="targeted_research_summary",
                observed_fact="PIM vendor remains unknown in public corpus evidence.",
                qualification_dimension="pim_stack",
                reliability=0.58,
                relevance=0.68,
            ),
        ),
    ),
)


def run_real_company_corpus(
    settings: Settings, cases: tuple[RealCompanyCase, ...] = REAL_COMPANY_CASES
) -> list[ResearchRun]:
    """Run the source-backed real-company corpus through the normal service."""

    store = RunStore(settings.data_dir)
    service = ResearchService(settings, store, enable_telemetry=True)
    runs: list[ResearchRun] = []
    for case in cases:
        request = CompanyResearchRequest(
            company_name=case.company_name,
            website_url=TypeAdapter(AnyHttpUrl).validate_python(case.website_url),
            research_mode=ResearchMode.WEBSITE,
            scenario=f"real-company-corpus:{case.company_name}",
        )
        run = service.run(request, source=RealCompanyCorpusSource(case))
        runs.append(run)
    return runs


def summarize_real_company_run(run: ResearchRun, case: RealCompanyCase) -> dict[str, Any]:
    """Return a compact report row for one real-company run."""

    profile = run.profile
    state = run.state
    assessment = run.assessment
    return {
        "company": case.company_name,
        "expected_shape": case.expected_shape,
        "status": run.manifest.status.value,
        "accepted": bool(run.acceptance and run.acceptance.status.value == "accepted"),
        "research_passes": state.research_pass if state else None,
        "routes": state.targeted_research_routes if state else [],
        "termination_reason": state.termination_reason if state else None,
        "dimension_statuses": [item.model_dump(mode="json") for item in assessment.dimension_statuses]
        if assessment
        else [],
        "pim_vendor": profile.pim_stack.pim_vendor if profile else None,
        "pim_state": profile.pim_stack.state.value if profile else None,
        "pim_confidence": profile.pim_stack.confidence if profile else None,
        "pim_evidence_reliability": profile.pim_stack.evidence_reliability if profile else None,
        "catalog_scale": profile.catalog_scale_estimate.scale_bucket.value if profile else None,
        "catalog_range": profile.catalog_scale_estimate.estimated_range if profile else None,
        "fit_band": profile.qualification.fit_band.value if profile else None,
        "fit_score": profile.qualification.fit_score if profile else None,
    }


def write_real_company_report(runs: list[ResearchRun], data_dir: Path, output: Path) -> Path:
    """Write a concise markdown report for the real-company corpus."""

    rows = [summarize_real_company_run(run, case) for run, case in zip(runs, REAL_COMPANY_CASES, strict=True)]
    lines = [
        "# Real Company Qualification Corpus",
        "",
        f"Generated from `{data_dir}`.",
        "",
        "## Companies",
        "",
        "| Company | Expected shape | Status | Accepted | Passes | Routes | PIM | PIM confidence | "
        "PIM reliability | Scale | Fit |",
        "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        routes = ", ".join(row["routes"]) if row["routes"] else "-"
        pim = f"{row['pim_vendor'] or 'unknown'} ({row['pim_state']})"
        scale = f"{row['catalog_scale']} {row['catalog_range'] or ''}".strip()
        fit = f"{row['fit_band']} {row['fit_score']}"
        lines.append(
            f"| {row['company']} | {row['expected_shape']} | {row['status']} | {row['accepted']} | "
            f"{row['research_passes']} | {routes} | {pim} | {row['pim_confidence']} | "
            f"{row['pim_evidence_reliability']} | {scale} | {fit} |"
        )
    lines.extend(
        [
            "",
            "## What This Stressed",
            "",
            "- Known PIM: The Paper Store, BTC Europe, Beauty Works, and Wahl Clipper.",
            "- Unknown or sparse public PIM: IKEA and Bellroy.",
            "- Very large catalog/data estate: The Paper Store, BTC Europe, and IKEA.",
            "- Smaller/specialty catalog: Beauty Works and Bellroy.",
            "- Multi-market complexity: IKEA, Bellroy, Beauty Works, and Wahl Clipper.",
            "- Public evidence sparsity: IKEA and Bellroy targeted PIM routes, plus unresolved "
            "source-data/catalog-scale gaps where public evidence was not enough.",
            "- Separate reliability/confidence: report rows include PIM confidence and source reliability "
            "in persisted profiles.",
            "",
            "## Source URLs",
            "",
        ]
    )
    for case in REAL_COMPANY_CASES:
        urls = sorted({item.source_url for item in case.evidence})
        lines.append(f"- {case.company_name}: " + ", ".join(urls))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
