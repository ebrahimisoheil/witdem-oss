"""Live heterogeneous company corpus for Product Factory evaluation."""

from __future__ import annotations

import html
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, TypeAdapter

from product_factory_app.config import ModelSettings, Settings
from product_factory_app.domain.models import (
    CompanyResearchRequest,
    Evidence,
    PimIdentificationState,
    ResearchMode,
    ResearchRun,
)
from product_factory_app.experiments.harness import (
    DEFAULT_LIVE_POLICY,
    LIVE_CANDIDATES,
    OrchestrationPolicy,
    measurement,
)
from product_factory_app.persistence.store import RunStore
from product_factory_app.providers.factory import build_chat_generator, build_research_agent, metadata_for
from product_factory_app.research.sources import ResearchSourceError, build_research_tool
from product_factory_app.service import ResearchService
from witdem.analytics import derived_termination_category


@dataclass(frozen=True)
class LiveResearchSeed:
    """One public URL used by the live heterogeneous source."""

    url: str
    title: str
    source_type: str
    evidence_type: str
    pass_available: int = 1
    qualification_dimension: str | None = None
    reliability: float = 0.7


@dataclass(frozen=True)
class LiveCompanyCase:
    """One live company input and its public seed URLs."""

    company_name: str
    website_url: str
    cohort: str
    expected_shape: str
    seeds: tuple[LiveResearchSeed, ...]


DEFAULT_HETEROGENEOUS_LIVE_COMPANIES: tuple[LiveCompanyCase, ...] = (
    LiveCompanyCase(
        company_name="The Paper Store",
        website_url="https://www.thepaperstore.com/",
        cohort="known_pim_large_retail",
        expected_shape="Known Akeneo PIM, very large catalog, fragmented source data, PIM seed delayed to pass 2.",
        seeds=(
            LiveResearchSeed(
                "https://www.thepaperstore.com/",
                "The Paper Store official website",
                "official",
                "official_company_page",
                qualification_dimension="product_catalog",
                reliability=0.78,
            ),
            LiveResearchSeed(
                "https://www.akeneo.com/customer-story/the-paper-store/",
                "Akeneo customer story",
                "vendor",
                "vendor_case_study",
                pass_available=2,
                qualification_dimension="pim_stack",
                reliability=0.9,
            ),
        ),
    ),
    LiveCompanyCase(
        company_name="BTC Europe",
        website_url="https://www.btc-europe.com/",
        cohort="known_pim_very_large_b2b",
        expected_shape="Known Pimcore PIM, very large data estate, DACH B2B distribution.",
        seeds=(
            LiveResearchSeed(
                "https://www.btc-europe.com/",
                "BTC Europe official website",
                "official",
                "official_company_page",
                qualification_dimension="product_catalog",
                reliability=0.78,
            ),
            LiveResearchSeed(
                "https://pimcore.com/en/customers/btc-europe-gmbh_c424299",
                "Pimcore BTC Europe case study",
                "vendor",
                "vendor_case_study",
                qualification_dimension="pim_stack",
                reliability=0.9,
            ),
        ),
    ),
    LiveCompanyCase(
        company_name="Beauty Works",
        website_url="https://beautyworksonline.com/",
        cohort="known_pim_smaller_catalog",
        expected_shape="Known Pimcore PIM, smaller catalog, B2B/B2C, scattered source data.",
        seeds=(
            LiveResearchSeed(
                "https://beautyworksonline.com/",
                "Beauty Works official website",
                "official",
                "official_company_page",
                qualification_dimension="product_catalog",
                reliability=0.78,
            ),
            LiveResearchSeed(
                "https://pimcore.com/en/customers/beauty-works_c485183",
                "Pimcore Beauty Works case study",
                "vendor",
                "vendor_case_study",
                qualification_dimension="pim_stack",
                reliability=0.9,
            ),
        ),
    ),
    LiveCompanyCase(
        company_name="Wahl Clipper",
        website_url="https://www.wahlpro.com/",
        cohort="known_pim_sparse_scale",
        expected_shape="Known Salsify PIM, product-data consistency evidence, sparse public catalog scale.",
        seeds=(
            LiveResearchSeed(
                "https://www.wahlpro.com/",
                "Wahl Professional official website",
                "official",
                "official_company_page",
                qualification_dimension="product_catalog",
                reliability=0.78,
            ),
            LiveResearchSeed(
                "https://www.salsify.com/resources/case-study/wahl-clipper-pim-drives-data-consistency",
                "Salsify Wahl Clipper case study",
                "vendor",
                "vendor_case_study",
                pass_available=2,
                qualification_dimension="pim_stack",
                reliability=0.88,
            ),
        ),
    ),
    LiveCompanyCase(
        company_name="IKEA",
        website_url="https://www.ikea.com/global/en/",
        cohort="very_large_multi_market_sparse_pim",
        expected_shape="Very large multi-market company with sparse public PIM/source-data evidence.",
        seeds=(
            LiveResearchSeed(
                "https://www.ikea.com/global/en/",
                "IKEA global website",
                "official",
                "official_company_page",
                qualification_dimension="company_identity",
                reliability=0.82,
            ),
            LiveResearchSeed(
                "https://www.ikea.com/global/en/our-business/how-we-work/",
                "IKEA how we work",
                "official",
                "official_company_page",
                qualification_dimension="catalog_complexity",
                reliability=0.82,
            ),
            LiveResearchSeed(
                "https://www.ikea.com/global/en/sitemap/",
                "IKEA sitemap",
                "official",
                "sitemap",
                pass_available=2,
                qualification_dimension="catalog_scale",
                reliability=0.7,
            ),
        ),
    ),
    LiveCompanyCase(
        company_name="Bellroy",
        website_url="https://bellroy.com/",
        cohort="smaller_multi_market_sparse_pim",
        expected_shape="Smaller specialty catalog, multi-market ecommerce, sparse public PIM evidence.",
        seeds=(
            LiveResearchSeed(
                "https://bellroy.com/",
                "Bellroy official website",
                "official",
                "official_company_page",
                qualification_dimension="product_catalog",
                reliability=0.78,
            ),
            LiveResearchSeed(
                "https://bellroy.com/responsible-business",
                "Bellroy responsible business",
                "official",
                "official_company_page",
                qualification_dimension="catalog_complexity",
                reliability=0.74,
            ),
            LiveResearchSeed(
                "https://bellroy.com/sitemap",
                "Bellroy sitemap",
                "official",
                "sitemap",
                pass_available=2,
                qualification_dimension="catalog_scale",
                reliability=0.68,
            ),
        ),
    ),
)

DEFAULT_ROLE_ISOLATION_COMPANY = DEFAULT_HETEROGENEOUS_LIVE_COMPANIES[1]


class HeterogeneousLiveResearchSource:
    """Fetch configured public seed URLs for one live company case."""

    def __init__(self, case: LiveCompanyCase) -> None:
        self.case = case

    @staticmethod
    def _page_text(url: str) -> str:
        response = urlopen(Request(url, headers={"User-Agent": "ProductFactoryAgent/0.1"}), timeout=20)
        raw = response.read(400_000).decode("utf-8", errors="replace")
        return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()

    @staticmethod
    def _snippet(text: str, terms: tuple[str, ...]) -> str:
        lowered = text.lower()
        for term in terms:
            position = lowered.find(term)
            if position >= 0:
                return text[max(0, position - 320) : position + 720]
        return text[:1000]

    def collect(self, request: CompanyResearchRequest, pass_number: int) -> list[Evidence]:
        available = [seed for seed in self.case.seeds if seed.pass_available <= pass_number]
        evidence: list[Evidence] = []
        failures: list[str] = []
        for seed in available:
            try:
                text = self._page_text(seed.url)
            except Exception as exc:  # pragma: no cover - live network dependent
                failures.append(f"{seed.url}: {exc}")
                continue
            evidence.extend(self._evidence_from_seed(seed, text, request.company_name))
        if not evidence:
            detail = "; ".join(failures) if failures else "no seeds available"
            raise ResearchSourceError(f"no live heterogeneous evidence collected for {request.company_name}: {detail}")
        if failures:
            evidence.append(
                Evidence(
                    claim=f"Some configured public sources could not be fetched for {request.company_name}.",
                    excerpt="; ".join(failures)[:1200],
                    source_url=str(request.website_url or self.case.website_url),
                    source_title="Live source fetch summary",
                    source_type="source_fetch_summary",
                    evidence_type="source_fetch_summary",
                    observed_fact="One or more configured live seed URLs failed to fetch.",
                    qualification_dimension="company_identity",
                    reliability=0.4,
                    relevance=0.4,
                    supports_claim=False,
                )
            )
        return evidence

    def _evidence_from_seed(self, seed: LiveResearchSeed, text: str, company_name: str) -> list[Evidence]:
        lowered = text.lower()
        evidence = [
            Evidence(
                claim=f"{company_name} has public evidence at {seed.title}.",
                excerpt=text[:1200],
                source_url=seed.url,
                source_title=seed.title,
                source_type=seed.source_type,
                evidence_type=seed.evidence_type,
                observed_fact="Configured public seed URL was reachable.",
                qualification_dimension=seed.qualification_dimension,
                reliability=seed.reliability,
                relevance=0.62,
            )
        ]
        signal_definitions = (
            (
                "product_catalog",
                f"{company_name} has public product or catalog evidence.",
                ("product", "products", "catalog", "catalogue", "collection", "assortment", "shop"),
                0.78,
            ),
            (
                "pim_stack",
                f"{company_name} has public PIM/product-information technology evidence.",
                (
                    "pim",
                    "product information management",
                    "product information",
                    "akeneo",
                    "pimcore",
                    "salsify",
                    "inriver",
                    "contentserv",
                    "syndigo",
                    "stibo",
                    "informatica pim",
                ),
                0.9 if seed.source_type == "vendor" else 0.76,
            ),
            (
                "catalog_scale",
                f"{company_name} has public catalog-size or product-count evidence.",
                ("sku", "skus", "products", "data objects", "product count", "catalog"),
                0.8,
            ),
            (
                "catalog_complexity",
                f"{company_name} has public catalog-complexity evidence.",
                (
                    "variants",
                    "attributes",
                    "categories",
                    "brand",
                    "brands",
                    "market",
                    "markets",
                    "global",
                    "international",
                    "b2b",
                    "b2c",
                    "languages",
                    "countries",
                    "suppliers",
                ),
                0.76,
            ),
            (
                "source_data_complexity",
                f"{company_name} has public source-data complexity evidence.",
                (
                    "spreadsheet",
                    "spreadsheets",
                    "excel",
                    "csv",
                    "pdf",
                    "feeds",
                    "erp",
                    "netsuite",
                    "sap",
                    "supplier",
                    "suppliers",
                    "duplicate",
                    "inconsistent",
                    "scattered",
                    "fragmented",
                    "manual",
                    "enrichment",
                    "onboarding",
                ),
                0.82 if seed.source_type == "vendor" else 0.7,
            ),
        )
        for dimension, claim, terms, relevance in signal_definitions:
            if any(term in lowered for term in terms):
                evidence.append(
                    Evidence(
                        claim=claim,
                        excerpt=self._snippet(text, terms),
                        source_url=seed.url,
                        source_title=seed.title,
                        source_type=seed.source_type,
                        evidence_type=seed.evidence_type,
                        observed_fact=self._snippet(text, terms)[:280],
                        qualification_dimension=dimension,
                        reliability=seed.reliability,
                        relevance=relevance,
                    )
                )
        return evidence


def _available_live_candidates(provider_names: tuple[str, ...] | None = None) -> tuple[tuple[str, ModelSettings], ...]:
    if not provider_names:
        return LIVE_CANDIDATES
    allowed = set(provider_names)
    return tuple((name, settings) for name, settings in LIVE_CANDIDATES if settings.provider in allowed)


def run_live_heterogeneous_corpus(
    settings: Settings,
    *,
    companies: tuple[LiveCompanyCase, ...] = DEFAULT_HETEROGENEOUS_LIVE_COMPANIES,
    candidates: tuple[tuple[str, ModelSettings], ...] = LIVE_CANDIDATES,
    policy: OrchestrationPolicy = DEFAULT_LIVE_POLICY,
    experiment_id: str = "live-heterogeneous-v1",
) -> list[dict[str, Any]]:
    """Run a provider/company matrix through the normal live service path."""

    store = RunStore(settings.data_dir)
    records: list[dict[str, Any]] = []
    estimated_model_spend = 0.0
    for candidate_name, model_settings in candidates:
        for case in companies:
            if estimated_model_spend >= settings.experiment_max_estimated_cost_usd:
                return records
            source = HeterogeneousLiveResearchSource(case)
            policy_settings = settings.model_copy(
                update={
                    "research_model": model_settings,
                    "extraction_model": model_settings,
                    "verification_model": model_settings,
                    "max_research_passes": policy.max_research_passes,
                    "research_mode": ResearchMode.WEBSITE,
                }
            )
            request = CompanyResearchRequest(
                company_name=case.company_name,
                website_url=TypeAdapter(AnyHttpUrl).validate_python(case.website_url),
                research_mode=ResearchMode.WEBSITE,
                scenario=f"live:{experiment_id}:{candidate_name}:{_slug(case.company_name)}",
            )
            service = ResearchService(policy_settings, store, enable_telemetry=True)
            started = perf_counter()
            run = service.run(
                request,
                source=source,
                extraction_generator=build_chat_generator(model_settings, structured=True),
                research_agent=build_research_agent(model_settings, build_research_tool(source)),
                providers=[
                    metadata_for(model_settings, "research"),
                    metadata_for(model_settings, "extraction"),
                ],
            )
            record = measurement(
                run,
                policy,
                scenario=request.scenario,
                settings=policy_settings,
                experiment_id=experiment_id,
            )
            record.update(live_heterogeneous_observation(run, case))
            record.update(
                {
                    "candidate": candidate_name,
                    "provider": model_settings.provider,
                    "model": model_settings.model,
                    "research_tool": "HeterogeneousLiveResearchSource",
                    "spend_limit_usd": settings.experiment_max_estimated_cost_usd,
                    "wall_clock_seconds": perf_counter() - started,
                }
            )
            records.append(record)
            store.append_jsonl("experiments/runs.jsonl", record)
            if record["model_cost_usd"] is not None:
                estimated_model_spend += float(record["model_cost_usd"])
    return records


def run_live_role_isolation_experiment(
    settings: Settings,
    *,
    case: LiveCompanyCase = DEFAULT_ROLE_ISOLATION_COMPANY,
    candidates: tuple[tuple[str, ModelSettings], ...] = LIVE_CANDIDATES,
    policy: OrchestrationPolicy = DEFAULT_LIVE_POLICY,
    experiment_id: str = "live-role-isolation-v1",
) -> list[dict[str, Any]]:
    """Run a research-provider by extraction-provider role-isolation matrix."""

    store = RunStore(settings.data_dir)
    records: list[dict[str, Any]] = []
    estimated_model_spend = 0.0
    for research_name, research_settings in candidates:
        for extraction_name, extraction_settings in candidates:
            if estimated_model_spend >= settings.experiment_max_estimated_cost_usd:
                return records
            source = HeterogeneousLiveResearchSource(case)
            role_settings = settings.model_copy(
                update={
                    "research_model": research_settings,
                    "extraction_model": extraction_settings,
                    "verification_model": extraction_settings,
                    "max_research_passes": policy.max_research_passes,
                    "research_mode": ResearchMode.WEBSITE,
                }
            )
            request = CompanyResearchRequest(
                company_name=case.company_name,
                website_url=TypeAdapter(AnyHttpUrl).validate_python(case.website_url),
                research_mode=ResearchMode.WEBSITE,
                scenario=(
                    f"live:{experiment_id}:research-{research_settings.provider}:"
                    f"extract-{extraction_settings.provider}:{_slug(case.company_name)}"
                ),
            )
            service = ResearchService(role_settings, store, enable_telemetry=True)
            started = perf_counter()
            run = service.run(
                request,
                source=source,
                extraction_generator=build_chat_generator(extraction_settings, structured=True),
                research_agent=build_research_agent(research_settings, build_research_tool(source)),
                providers=[
                    metadata_for(research_settings, "research"),
                    metadata_for(extraction_settings, "extraction"),
                ],
            )
            record = measurement(
                run,
                policy,
                scenario=request.scenario,
                settings=role_settings,
                experiment_id=experiment_id,
            )
            record.update(live_heterogeneous_observation(run, case))
            record.update(
                {
                    "candidate": f"research:{research_name}|extraction:{extraction_name}",
                    "research_candidate": research_name,
                    "extraction_candidate": extraction_name,
                    "research_provider": research_settings.provider,
                    "research_model": research_settings.model,
                    "extraction_provider": extraction_settings.provider,
                    "extraction_model": extraction_settings.model,
                    "role_pair": f"{research_settings.provider}->{extraction_settings.provider}",
                    "provider": f"{research_settings.provider}->{extraction_settings.provider}",
                    "model": f"{research_settings.model}->{extraction_settings.model}",
                    "research_tool": "HeterogeneousLiveResearchSource",
                    "spend_limit_usd": settings.experiment_max_estimated_cost_usd,
                    "wall_clock_seconds": perf_counter() - started,
                }
            )
            records.append(record)
            store.append_jsonl("experiments/runs.jsonl", record)
            if record["model_cost_usd"] is not None:
                estimated_model_spend += float(record["model_cost_usd"])
    return records


def live_heterogeneous_observation(run: ResearchRun, case: LiveCompanyCase) -> dict[str, Any]:
    """Summarize one live heterogeneous run for empirical reporting."""

    statuses = run.assessment.dimension_statuses if run.assessment else []
    profile = run.profile
    evidence = run.state.evidence if run.state else []
    pim_stack = profile.pim_stack if profile else None
    catalog_scale = profile.catalog_scale_estimate if profile else None
    source_counts = Counter(item.source_type for item in evidence)
    pim_evidence = [
        item
        for item in evidence
        if item.qualification_dimension == "pim_stack"
        or any(vendor.lower() in f"{item.claim} {item.excerpt}".lower() for vendor in ("akeneo", "pimcore", "salsify"))
    ]
    strongest_pim = max(pim_evidence, key=lambda item: (item.reliability, item.relevance), default=None)
    source_data_status = next((item for item in statuses if item.dimension == "source_data_complexity"), None)
    return {
        "company_name": case.company_name,
        "website_url": case.website_url,
        "cohort": case.cohort,
        "expected_shape": case.expected_shape,
        "seed_urls": [seed.url for seed in case.seeds],
        "source_types": dict(sorted(source_counts.items())),
        "dimension_statuses": [item.model_dump(mode="json") for item in statuses],
        "unknown_acceptable_dimensions": [
            item.dimension for item in statuses if item.status.value == "unknown_acceptable"
        ],
        "targeted_routes": list(run.state.targeted_research_routes if run.state else []),
        "pim_vendor": pim_stack.pim_vendor if pim_stack else None,
        "pim_product": pim_stack.pim_product if pim_stack else None,
        "pim_state": pim_stack.state.value if pim_stack else PimIdentificationState.UNKNOWN.value,
        "pim_confidence": pim_stack.confidence if pim_stack else 0.0,
        "pim_evidence_reliability": pim_stack.evidence_reliability if pim_stack else 0.0,
        "strongest_pim_source_type": strongest_pim.source_type if strongest_pim else None,
        "strongest_pim_evidence_type": strongest_pim.evidence_type if strongest_pim else None,
        "catalog_scale_bucket": catalog_scale.scale_bucket.value if catalog_scale else "unknown",
        "catalog_estimated_range": catalog_scale.estimated_range if catalog_scale else None,
        "catalog_confidence": catalog_scale.confidence if catalog_scale else 0.0,
        "catalog_evidence_reliability": catalog_scale.evidence_reliability if catalog_scale else 0.0,
        "source_data_status": source_data_status.status.value if source_data_status else None,
        "source_data_confidence": source_data_status.finding_confidence if source_data_status else 0.0,
        "source_data_reliability": source_data_status.evidence_reliability if source_data_status else 0.0,
        "source_data_signals": list(profile.source_data_complexity_signals if profile else []),
        "fit_band": profile.qualification.fit_band.value if profile else None,
        "fit_score": profile.qualification.fit_score if profile else None,
        "terminal_error": run.manifest.error,
    }


def _termination_decomposition_rows(records: list[dict[str, Any]]) -> str:
    counts = Counter(derived_termination_category(item) for item in records)
    lines = ["| Derived category | Count |", "|---|---:|"]
    for category in (
        "accepted",
        "valid_but_not_accepted",
        "bounded_uncertainty",
        "operational_failure",
        "validation_failure",
        "extraction_failure",
        "other_failure",
        "other",
    ):
        if counts.get(category, 0):
            lines.append(f"| {category} | {counts[category]} |")
    return "\n".join(lines)


def write_live_role_isolation_report(data_dir: Path, output: Path) -> Path:
    """Write a report for a persisted role-isolation matrix."""

    records = [
        item
        for item in _read_jsonl(data_dir / "experiments" / "runs.jsonl")
        if item.get("experiment_id") == "live-role-isolation-v1"
    ]
    report = _format_role_isolation_report(records, data_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


def _format_role_isolation_report(records: list[dict[str, Any]], data_dir: Path) -> str:
    completed = sum(bool(item.get("execution_completed")) for item in records)
    valid = sum(bool(item.get("result_valid")) for item in records)
    accepted = sum(bool(item.get("accepted_result")) for item in records)
    terminal = sum(not bool(item.get("execution_completed")) for item in records)
    derived_bounded = sum(derived_termination_category(item) == "bounded_uncertainty" for item in records)
    repairs = sum(int(item.get("profile_repairs") or 0) for item in records)
    total_model_cost = sum(float(item.get("model_cost_usd") or 0) for item in records)
    return f"""# Live Role-Isolation Evaluation

Generated from `{data_dir}`.

## Corpus

- Executions: {len(records)}
- Completed / valid / accepted: {completed} / {valid} / {accepted}
- Terminal executions: {terminal}
- Derived bounded-uncertainty outcomes: {derived_bounded}
- Profile repairs: {repairs}
- Measured model cost: ${total_model_cost:.6f}
- Total workflow cost: unknown; website/tool billing is not instrumented.

## Derived Termination Decomposition

{_termination_decomposition_rows(records)}

Future insight: termination decomposition is derived here from corpus ingestion/reporting facts such as pass
exhaustion, unresolved dimensions, source failures, validation state, acceptance state, and terminal error. It is not
a new workflow event.

## Role Pair Outcomes

{_role_pair_rows(records)}

## Research Role Summary

{_role_summary_rows(records, "research_provider")}

## Extraction Role Summary

{_role_summary_rows(records, "extraction_provider")}
"""


def _role_pair_rows(records: list[dict[str, Any]]) -> str:
    lines = [
        "| Research | Extraction | Status | Valid | Accepted | Derived Outcome | Repairs | PIM | Scale | Error |",
        "|---|---|---|---:|---:|---|---:|---|---|---|",
    ]
    for item in sorted(
        records, key=lambda record: (record.get("research_provider"), record.get("extraction_provider"))
    ):
        pim = f"{item.get('pim_vendor') or 'unknown'} ({item.get('pim_state') or 'unknown'})"
        scale = f"{item.get('catalog_scale_bucket') or 'unknown'} {item.get('catalog_estimated_range') or ''}".strip()
        lines.append(
            "| {research} | {extraction} | {status} | {valid} | {accepted} | {derived} | {repairs} | "
            "{pim} | {scale} | {error} |".format(
                research=item.get("research_provider", "unknown"),
                extraction=item.get("extraction_provider", "unknown"),
                status=item.get("status", "unknown"),
                valid="yes" if item.get("result_valid") else "no",
                accepted="yes" if item.get("accepted_result") else "no",
                derived=derived_termination_category(item),
                repairs=item.get("profile_repairs") or 0,
                pim=pim,
                scale=scale,
                error=(item.get("terminal_error") or "-").replace("|", "\\|"),
            )
        )
    return "\n".join(lines)


def _role_summary_rows(records: list[dict[str, Any]], role_key: str) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(role_key, "unknown"))].append(record)
    lines = [
        "| Provider | Executions | Completed | Valid | Accepted | Terminal | Derived Bounded Uncertainty | Repairs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider, items in sorted(grouped.items()):
        lines.append(
            "| {provider} | {executions} | {completed} | {valid} | {accepted} | {terminal} | "
            "{bounded} | {repairs} |".format(
                provider=provider,
                executions=len(items),
                completed=sum(bool(item.get("execution_completed")) for item in items),
                valid=sum(bool(item.get("result_valid")) for item in items),
                accepted=sum(bool(item.get("accepted_result")) for item in items),
                terminal=sum(not bool(item.get("execution_completed")) for item in items),
                bounded=sum(derived_termination_category(item) == "bounded_uncertainty" for item in items),
                repairs=sum(int(item.get("profile_repairs") or 0) for item in items),
            )
        )
    return "\n".join(lines)


def write_live_heterogeneous_report(data_dir: Path, output: Path) -> Path:
    """Write an empirical report for a persisted heterogeneous live corpus."""

    records = _read_jsonl(data_dir / "experiments" / "runs.jsonl")
    live_records = [item for item in records if item.get("experiment_id") == "live-heterogeneous-v1"]
    if not live_records:
        live_records = records
    report = _format_live_heterogeneous_report(live_records, data_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return output


def _format_live_heterogeneous_report(records: list[dict[str, Any]], data_dir: Path) -> str:
    providers = sorted({str(item.get("provider")) for item in records if item.get("provider")})
    companies = sorted({str(item.get("company_name")) for item in records if item.get("company_name")})
    completed = sum(bool(item.get("execution_completed")) for item in records)
    valid = sum(bool(item.get("result_valid")) for item in records)
    accepted = sum(bool(item.get("accepted_result")) for item in records)
    terminal = sum(not bool(item.get("execution_completed")) for item in records)
    bounded_uncertainty = sum(derived_termination_category(item) == "bounded_uncertainty" for item in records)
    repairs = sum(int(item.get("profile_repairs") or 0) for item in records)
    pim_routes = sum("target_pim_stack" in item.get("targeted_routes", []) for item in records)
    unknown_acceptable = sum("pim_stack" in item.get("unknown_acceptable_dimensions", []) for item in records)
    catalog_unknown_acceptable = sum(
        "catalog_scale" in item.get("unknown_acceptable_dimensions", []) for item in records
    )
    source_discovered = sum(
        bool(item.get("source_data_signals")) or item.get("source_data_status") in {"sufficient", "weak"}
        for item in records
    )
    total_model_cost = sum(float(item.get("model_cost_usd") or 0) for item in records)
    provider_rows = _provider_summary_rows(records)
    run_rows = _run_rows(records)
    disagreement_rows = _disagreement_rows(records)
    source_rows = _source_type_rows(records)
    source_data_question = (
        f"{source_discovered}/{len(records)} executions found sufficient or weak source-data signals."
    )
    catalog_scale_question = (
        f"{catalog_unknown_acceptable}/{len(records)} executions ended with catalog scale unknown acceptable."
    )
    return f"""# Live Heterogeneous Company Evaluation

Generated from `{data_dir}`.

## Corpus

- Providers: {", ".join(providers) or "none"}
- Companies: {", ".join(companies) or "none"}
- Executions: {len(records)}
- Completed / valid / accepted: {completed} / {valid} / {accepted}
- Terminal executions: {terminal}
- Derived bounded-uncertainty outcomes: {bounded_uncertainty}
- Profile repairs: {repairs}
- `target_pim_stack` triggered: {pim_routes}
- PIM `unknown_acceptable`: {unknown_acceptable}
- Catalog scale `unknown_acceptable`: {catalog_unknown_acceptable}
- Source-data complexity discovered: {source_discovered}
- Measured model cost: ${total_model_cost:.6f}
- Total workflow cost: unknown; website/tool billing is not instrumented.

## Provider Outcomes

{provider_rows}

## Derived Termination Decomposition

{_termination_decomposition_rows(records)}

Future insight: termination decomposition is derived here from corpus ingestion/reporting facts such as pass
exhaustion, unresolved dimensions, source failures, validation state, acceptance state, and terminal error. It is not
a new workflow event.

## Company Runs

{run_rows}

## Empirical Questions

- Targeted PIM research frequency: {pim_routes}/{len(records)} executions triggered `target_pim_stack`.
- Unknown acceptable PIM frequency: {unknown_acceptable}/{len(records)} executions ended with PIM unknown acceptable.
- Unknown acceptable catalog-scale frequency: {catalog_scale_question}
- Source-data public discoverability: {source_data_question}
- Provider repairs and terminal failures are shown in the provider table.
- Catalog-size stability and provider disagreement are shown in the company disagreement table.
- Strongest PIM evidence source types are shown in the source-type table.

## Provider Disagreement By Company

{disagreement_rows}

## Strongest PIM Evidence Source Types

{source_rows}
"""


def _provider_summary_rows(records: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("provider", "unknown"))].append(record)
    lines = [
        "| Provider | Executions | Completed | Valid | Accepted | Repairs | Terminal | "
        "Avg accepted seconds | Model cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider, items in sorted(grouped.items()):
        accepted_times = [
            float(item["time_to_acceptance_seconds"])
            for item in items
            if item.get("accepted_result") and item.get("time_to_acceptance_seconds") is not None
        ]
        row_template = (
            "| {provider} | {executions} | {completed} | {valid} | {accepted} | {repairs} | "
            "{terminal} | {avg_time} | ${cost:.6f} |"
        )
        lines.append(
            row_template.format(
                provider=provider,
                executions=len(items),
                completed=sum(bool(item.get("execution_completed")) for item in items),
                valid=sum(bool(item.get("result_valid")) for item in items),
                accepted=sum(bool(item.get("accepted_result")) for item in items),
                repairs=sum(int(item.get("profile_repairs") or 0) for item in items),
                terminal=sum(not bool(item.get("execution_completed")) for item in items),
                avg_time=f"{sum(accepted_times) / len(accepted_times):.2f}" if accepted_times else "-",
                cost=sum(float(item.get("model_cost_usd") or 0) for item in items),
            )
        )
    return "\n".join(lines)


def _run_rows(records: list[dict[str, Any]]) -> str:
    lines = [
        "| Provider | Company | Status | Valid | Accepted | Passes | Routes | PIM | Scale | Source Data | Repairs |",
        "|---|---|---|---:|---:|---:|---|---|---|---|---:|",
    ]
    for item in sorted(records, key=lambda record: (str(record.get("provider")), str(record.get("company_name")))):
        pim = f"{item.get('pim_vendor') or 'unknown'} ({item.get('pim_state') or 'unknown'})"
        scale = f"{item.get('catalog_scale_bucket') or 'unknown'} {item.get('catalog_estimated_range') or ''}".strip()
        row_template = (
            "| {provider} | {company} | {status} | {valid} | {accepted} | {passes} | "
            "{routes} | {pim} | {scale} | {source_data} | {repairs} |"
        )
        lines.append(
            row_template.format(
                provider=item.get("provider", "unknown"),
                company=item.get("company_name", "unknown"),
                status=item.get("status", "unknown"),
                valid="yes" if item.get("result_valid") else "no",
                accepted="yes" if item.get("accepted_result") else "no",
                passes=item.get("research_passes") or "-",
                routes=", ".join(item.get("targeted_routes") or []) or "-",
                pim=pim,
                scale=scale,
                source_data=item.get("source_data_status") or "-",
                repairs=item.get("profile_repairs") or 0,
            )
        )
    return "\n".join(lines)


def _disagreement_rows(records: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("company_name", "unknown"))].append(record)
    lines = [
        "| Company | Providers | PIM Results | Catalog Scale Results | Fit Results |",
        "|---|---|---|---|---|",
    ]
    for company, items in sorted(grouped.items()):
        lines.append(
            "| {company} | {providers} | {pim} | {scale} | {fit} |".format(
                company=company,
                providers=", ".join(sorted(str(item.get("provider")) for item in items)),
                pim="; ".join(
                    f"{item.get('provider')}: {item.get('pim_vendor') or 'unknown'} ({item.get('pim_state')})"
                    for item in sorted(items, key=lambda record: str(record.get("provider")))
                ),
                scale="; ".join(_provider_scale_summary(item) for item in _sort_by_provider(items)),
                fit="; ".join(
                    f"{item.get('provider')}: {item.get('fit_band') or 'none'} {item.get('fit_score') or ''}".strip()
                    for item in _sort_by_provider(items)
                ),
            )
        )
    return "\n".join(lines)


def _source_type_rows(records: list[dict[str, Any]]) -> str:
    counts = Counter(
        str(item.get("strongest_pim_source_type")) for item in records if item.get("strongest_pim_source_type")
    )
    if not counts:
        return "No PIM evidence source type was identified."
    lines = ["| Source type | Count |", "|---|---:|"]
    lines.extend(f"| {source_type} | {count} |" for source_type, count in sorted(counts.items()))
    return "\n".join(lines)


def _sort_by_provider(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda record: str(record.get("provider")))


def _provider_scale_summary(item: dict[str, Any]) -> str:
    return (
        f"{item.get('provider')}: "
        f"{item.get('catalog_scale_bucket') or 'unknown'} "
        f"{item.get('catalog_estimated_range') or ''}"
    ).strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def candidates_for_provider_names(provider_names: tuple[str, ...] | None) -> tuple[tuple[str, ModelSettings], ...]:
    """Return configured live candidates filtered by provider name."""

    return _available_live_candidates(provider_names)
