"""Small experiment harness built on top of the working application service."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import AnyHttpUrl, TypeAdapter

from product_factory_app.config import ModelSettings, Settings
from product_factory_app.domain.models import AcceptanceStatus, CompanyResearchRequest, ResearchMode, ResearchRun
from product_factory_app.experiments.cost import estimate_chat_cost
from product_factory_app.persistence.store import RunStore
from product_factory_app.providers.factory import (
    build_chat_generator,
    build_research_agent,
    metadata_for,
)
from product_factory_app.research.sources import (
    FixtureResearchSource,
    WebsiteResearchSource,
    build_research_tool,
)
from product_factory_app.service import ResearchService


@dataclass(frozen=True)
class OrchestrationPolicy:
    """A named workflow policy for comparison, not a new workflow framework."""

    name: str
    max_research_passes: int


@dataclass(frozen=True)
class DeterministicCandidate:
    """Fixture candidate configuration used to exercise the existing service path."""

    name: str
    scenario: str
    max_research_passes: int
    cost_per_pass_usd: float
    latency_seconds: float


DETERMINISTIC_CANDIDATES = (
    DeterministicCandidate("quality-first", "success", 1, 0.020, 0.0015),
    DeterministicCandidate("balanced", "incomplete", 2, 0.003, 0.0005),
    DeterministicCandidate("cheap-but-incomplete", "incomplete", 1, 0.001, 0.0001),
)


@dataclass
class RepairableFixtureGenerator:
    """Return one malformed profile followed by a valid deterministic profile."""

    calls: int = 0

    def run(self, *, messages: list[object]) -> dict[str, list[Any]]:
        self.calls += 1
        if self.calls == 1:
            reply = type("Reply", (), {"text": "not-json", "meta": {"usage": {"total_tokens": 5}}})()
            return {"replies": [reply]}
        prompt = str(messages[0])
        evidence_id = re.search(r"\[([a-f0-9]{32})\]", prompt)
        if evidence_id is None:
            raise AssertionError("deterministic profile fixture did not receive evidence IDs")
        profile = {
            "company_name": "Experiment Commerce",
            "summary": "Experiment Commerce sells goods.",
            "products": ["goods"],
            "catalog_scale": "tens of thousands of variants",
            "catalog_complexity": "complex",
            "markets": ["Germany"],
            "product_factory_fit": "high",
            "evidence_ids": [evidence_id.group(1)],
            "findings": [
                {
                    "field": "summary",
                    "value": "Experiment Commerce sells goods.",
                    "evidence_ids": [evidence_id.group(1)],
                }
            ],
        }
        reply = type(
            "Reply",
            (),
            {"text": json.dumps(profile), "meta": {"usage": {"total_tokens": 10}}},
        )()
        return {"replies": [reply]}


POLICIES = (
    OrchestrationPolicy("single-pass", 1),
    OrchestrationPolicy("bounded-research-loop", 2),
)

DEFAULT_LIVE_POLICY = OrchestrationPolicy("bounded-research-loop", 2)


LIVE_CANDIDATES = (
    ("openai-strong", ModelSettings(provider="openai", model="gpt-5.4-mini")),
    ("mistral-economical", ModelSettings(provider="mistral", model="mistral-small-2603")),
    ("deepseek-economical", ModelSettings(provider="deepseek", model="deepseek-v4-flash")),
)


def quality_score(run: ResearchRun) -> float:
    """Calculate a transparent initial quality score for deterministic comparison."""

    if run.acceptance and run.acceptance.quality_score is not None:
        return run.acceptance.quality_score
    if run.profile is None or run.validation is None or not run.validation.valid:
        return 0.0
    profile = run.profile
    checks = [
        bool(profile.summary),
        bool(profile.products),
        bool(profile.markets),
        bool(profile.catalog_complexity),
        bool(profile.evidence_ids),
        profile.product_factory_fit.value != "unknown",
        profile.confidence >= 0.5,
    ]
    return sum(checks) / len(checks)


def measurement(
    run: ResearchRun,
    policy: OrchestrationPolicy,
    scenario: str,
    *,
    settings: Settings | None = None,
    experiment_id: str = "unspecified",
) -> dict[str, Any]:
    """Return comparable success/cost/performance fields for one execution."""

    cost_parts: list[float] = []
    model_cost_known = bool(run.manifest.providers)
    for provider in run.manifest.providers:
        if provider.role == "research":
            available = run.metrics.get("research_usage_available", 0) > 0
            usage = {
                "prompt_tokens": run.metrics.get("research_prompt_tokens"),
                "completion_tokens": run.metrics.get("research_completion_tokens"),
            }
        elif provider.role == "extraction":
            available = run.metrics.get("extraction_usage_available", 0) > 0
            usage = {
                "prompt_tokens": run.metrics.get("prompt_tokens"),
                "completion_tokens": run.metrics.get("completion_tokens"),
            }
        else:
            continue
        if not available:
            calls = run.metrics.get(
                "research_model_calls" if provider.role == "research" else "extraction_model_calls",
                0,
            )
            if calls <= 0:
                continue
        estimated = estimate_chat_cost(provider.provider, provider.model, usage) if available else None
        if estimated is None:
            model_cost_known = False
        else:
            cost_parts.append(estimated)
    fixture_instrumentation = run.metrics.get("fixture_instrumentation_available", 0) > 0
    cost: float | None
    if not run.manifest.providers and fixture_instrumentation:
        cost = float(run.metrics.get("fixture_cost_usd", 0.0))
        model_cost: float | None = 0.0
        total_cost: float | None = cost
        total_cost_known = True
        cost_scope = "whole_deterministic_fixture_path"
    else:
        model_cost = sum(cost_parts) if model_cost_known else None
        cost = model_cost
        total_cost = None
        total_cost_known = False
        cost_scope = "model_calls_only"
    accepted = bool(run.acceptance and run.acceptance.status is AcceptanceStatus.ACCEPTED)
    time_to_acceptance = (
        (run.manifest.accepted_at - run.manifest.started_at).total_seconds()
        if accepted and run.manifest.accepted_at is not None
        else None
    )

    return {
        "experiment_id": experiment_id,
        "execution_id": run.manifest.execution_id,
        "workflow_version": run.manifest.workflow_version,
        "policy": policy.name,
        "scenario": scenario,
        "status": run.manifest.status.value,
        "execution_completed": run.execution_completed,
        "result_valid": run.result_valid,
        "accepted_result": accepted,
        "accepted_success": accepted,
        "accepted_at": run.manifest.accepted_at.isoformat() if run.manifest.accepted_at else None,
        "quality_score": quality_score(run),
        "cost_usd": cost,
        "model_cost_usd": model_cost,
        "total_cost_usd": total_cost,
        "cost_scope": cost_scope,
        "cost_known": cost is not None,
        "total_cost_known": total_cost_known,
        "duration_seconds": run.metrics.get("duration_seconds"),
        "time_to_acceptance_seconds": time_to_acceptance,
        "model_calls": run.metrics.get("model_calls", 0),
        "tool_calls": run.metrics.get("tool_calls", 0),
        "research_passes": run.state.research_pass if run.state else None,
        "research_retries": max((run.state.research_pass if run.state else 1) - 1, 0),
        "source_failures": run.state.source_failures if run.state else 0,
        "profile_repairs": run.state.profile_repair_count if run.state else 0,
        "retries": max((run.state.research_pass if run.state else 1) - 1, 0),
        "termination_reason": run.state.termination_reason if run.state else None,
        "configuration": {
            "policy": {
                "name": policy.name,
                "max_research_passes": policy.max_research_passes,
            },
            "max_profile_repairs": settings.max_profile_repairs if settings else None,
            "providers": [item.model_dump(mode="json") for item in run.manifest.providers],
            "tools": run.manifest.tools,
        },
    }


def run_deterministic_corpus(settings: Settings) -> list[dict[str, Any]]:
    """Run comparable fixture scenarios after the application is already working."""

    store = RunStore(settings.data_dir)
    records: list[dict[str, Any]] = []
    scenarios = ("success", "incomplete", "repair", "recovery", "terminal")
    for policy in POLICIES:
        policy_settings = settings.model_copy(update={"max_research_passes": policy.max_research_passes})
        service = ResearchService(policy_settings, store, enable_telemetry=True)
        for scenario in scenarios:
            started = perf_counter()
            request = CompanyResearchRequest(
                company_name="Experiment Commerce",
                research_mode=ResearchMode.FIXTURE,
                scenario=f"experiment:{policy.name}:{scenario}",
            )
            generator = RepairableFixtureGenerator() if scenario == "repair" else None
            run = service.run(
                request,
                source=FixtureResearchSource(scenario, cost_per_pass_usd=0.002),
                extraction_generator=generator,
            )
            record = measurement(
                run,
                policy,
                scenario,
                settings=policy_settings,
                experiment_id="deterministic-corpus-v1",
            )
            record["wall_clock_seconds"] = perf_counter() - started
            records.append(record)

    candidate_records: list[dict[str, Any]] = []
    for candidate in DETERMINISTIC_CANDIDATES:
        candidate_settings = settings.model_copy(update={"max_research_passes": candidate.max_research_passes})
        service = ResearchService(candidate_settings, store, enable_telemetry=True)
        request = CompanyResearchRequest(
            company_name="Experiment Commerce",
            research_mode=ResearchMode.FIXTURE,
            scenario=f"experiment:candidate:{candidate.name}",
        )
        started = perf_counter()
        run = service.run(
            request,
            source=FixtureResearchSource(
                candidate.scenario,
                cost_per_pass_usd=candidate.cost_per_pass_usd,
                latency_seconds=candidate.latency_seconds,
            ),
        )
        record = measurement(
            run,
            OrchestrationPolicy(candidate.name, candidate.max_research_passes),
            candidate.scenario,
            settings=candidate_settings,
            experiment_id="deterministic-candidates-v1",
        )
        record.update(
            {
                "candidate": candidate.name,
                "candidate_configuration": {
                    "scenario": candidate.scenario,
                    "max_research_passes": candidate.max_research_passes,
                    "cost_per_pass_usd": candidate.cost_per_pass_usd,
                    "latency_seconds": candidate.latency_seconds,
                },
                "wall_clock_seconds": perf_counter() - started,
            }
        )
        candidate_records.append(record)

    winner = select_winner(candidate_records)
    for record in candidate_records:
        record["expected_winner"] = winner["candidate"] if winner else None
    records.extend(candidate_records)
    for record in records:
        store.append_jsonl("experiments/runs.jsonl", record)
    return records


def select_winner(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Apply the current experiment rule: acceptance, quality, cost, then time."""

    accepted = [record for record in records if record.get("accepted_result")]
    if not accepted:
        return None

    def key(record: dict[str, Any]) -> tuple[float, float, float, float]:
        cost = record.get("total_cost_usd")
        duration = record.get("time_to_acceptance_seconds")
        return (
            -float(record.get("quality_score") or 0),
            float(cost if cost is not None else float("inf")),
            float(duration if duration is not None else float("inf")),
            float(record.get("research_passes") or float("inf")),
        )

    return min(accepted, key=key)


def run_live_matrix(
    settings: Settings,
    *,
    company_name: str,
    website_url: str,
    candidates: tuple[tuple[str, ModelSettings], ...] = LIVE_CANDIDATES,
) -> list[dict[str, Any]]:
    """Run a small, explicitly requested model matrix against the same application contract."""

    store = RunStore(settings.data_dir)
    records: list[dict[str, Any]] = []
    source = WebsiteResearchSource()
    estimated_spend = 0.0
    for candidate_name, model_settings in candidates:
        candidate_settings = settings.model_copy(
            update={
                "research_model": model_settings,
                "extraction_model": model_settings,
                "verification_model": model_settings,
            }
        )
        for policy in POLICIES:
            if estimated_spend >= settings.experiment_max_estimated_cost_usd:
                return records
            policy_settings = candidate_settings.model_copy(update={"max_research_passes": policy.max_research_passes})
            request = CompanyResearchRequest(
                company_name=company_name,
                website_url=TypeAdapter(AnyHttpUrl).validate_python(website_url),
                research_mode=ResearchMode.WEBSITE,
                scenario=f"live:{candidate_name}:{policy.name}",
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
                scenario=f"{candidate_name}:{policy.name}",
                settings=policy_settings,
                experiment_id="live-matrix-v1",
            )
            record.update(
                {
                    "candidate": candidate_name,
                    "provider": model_settings.provider,
                    "model": model_settings.model,
                    "wall_clock_seconds": perf_counter() - started,
                }
            )
            records.append(record)
            store.append_jsonl("experiments/runs.jsonl", record)
            if record["model_cost_usd"] is not None:
                estimated_spend += float(record["model_cost_usd"])
    return records


def run_live_corpus(
    settings: Settings,
    *,
    companies: tuple[tuple[str, str], ...],
    model_settings: ModelSettings | None = None,
    policy: OrchestrationPolicy = DEFAULT_LIVE_POLICY,
    experiment_id: str = "live-experiment-v1",
) -> list[dict[str, Any]]:
    """Run a small one-provider live corpus through the normal service path."""

    model_settings = model_settings or settings.research_model
    policy_settings = settings.model_copy(
        update={
            "research_model": model_settings,
            "extraction_model": model_settings,
            "verification_model": model_settings,
            "max_research_passes": policy.max_research_passes,
        }
    )
    store = RunStore(settings.data_dir)
    source = WebsiteResearchSource()
    records: list[dict[str, Any]] = []
    estimated_model_spend = 0.0
    for company_name, website_url in companies:
        if estimated_model_spend >= settings.experiment_max_estimated_cost_usd:
            break
        request = CompanyResearchRequest(
            company_name=company_name,
            website_url=TypeAdapter(AnyHttpUrl).validate_python(website_url),
            research_mode=ResearchMode.WEBSITE,
            scenario=f"live:{experiment_id}:{company_name}",
        )
        started = perf_counter()
        service = ResearchService(policy_settings, store, enable_telemetry=True)
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
        record.update(
            {
                "company_name": company_name,
                "website_url": website_url,
                "provider": model_settings.provider,
                "model": model_settings.model,
                "research_tool": "WebsiteResearchSource",
                "spend_limit_usd": settings.experiment_max_estimated_cost_usd,
                "wall_clock_seconds": perf_counter() - started,
            }
        )
        records.append(record)
        store.append_jsonl("experiments/runs.jsonl", record)
        if record["model_cost_usd"] is not None:
            estimated_model_spend += float(record["model_cost_usd"])
    return records
