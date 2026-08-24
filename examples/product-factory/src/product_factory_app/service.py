"""Application service that runs and persists company research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from opentelemetry import baggage, context, trace

from product_factory_app.analytics.semantic import SemanticAnalytics
from product_factory_app.config import Settings
from product_factory_app.domain.evaluation import evaluate_profile_acceptance
from product_factory_app.domain.models import (
    AcceptanceStatus,
    CompanyResearchRequest,
    ProviderMetadata,
    ResearchRun,
    ResearchState,
    RunManifest,
    RunStatus,
    ValidationResult,
)
from product_factory_app.persistence.store import RunStore
from product_factory_app.research.sources import (
    FixtureResearchSource,
    ResearchSource,
    WebsiteResearchSource,
)
from product_factory_app.telemetry import configure_tracing, force_flush_tracing
from product_factory_app.workflow.pipeline import (
    build_profile_pipeline,
    build_research_assessment_pipeline,
    build_workflow_components,
)


def _trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.trace_id else None


def _merge_usage(total: dict[str, Any], current: dict[str, Any]) -> None:
    """Accumulate numeric provider usage without assuming every provider reports it."""

    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "model_calls",
        "tool_calls",
        "fixture_cost_usd",
        "fixture_latency_seconds",
    ):
        value = current.get(key)
        if isinstance(value, (int, float)):
            total[key] = float(total.get(key, 0)) + float(value)


def _run_metrics(
    *,
    started_at: datetime,
    started_clock: float,
    research_usage: dict[str, Any],
    extraction_usage: dict[str, Any],
    state: ResearchState | None,
    acceptance_quality_score: float = 0.0,
    accepted_result: bool = False,
    accepted_at: datetime | None = None,
) -> dict[str, float]:
    """Build the same inspectable runtime metrics for success and failure artifacts."""

    research_model_calls = float(research_usage.get("model_calls", 0))
    extraction_model_calls = float(extraction_usage.get("model_calls", 0))
    time_to_acceptance = (accepted_at - started_at).total_seconds() if accepted_at is not None else 0.0
    return {
        "duration_seconds": perf_counter() - started_clock,
        "time_to_acceptance_seconds": time_to_acceptance,
        "acceptance_timestamp_available": float(accepted_at is not None),
        "research_prompt_tokens": float(research_usage.get("prompt_tokens", 0)),
        "research_completion_tokens": float(research_usage.get("completion_tokens", 0)),
        "research_total_tokens": float(research_usage.get("total_tokens", 0)),
        "research_model_calls": research_model_calls,
        "research_tool_calls": float(research_usage.get("tool_calls", 0)),
        "research_usage_available": float(bool(research_usage)),
        "prompt_tokens": float(extraction_usage.get("prompt_tokens", 0)),
        "completion_tokens": float(extraction_usage.get("completion_tokens", 0)),
        "total_tokens": float(extraction_usage.get("total_tokens", 0)),
        "extraction_model_calls": extraction_model_calls,
        "extraction_usage_available": float(bool(extraction_usage)),
        "model_calls": research_model_calls + extraction_model_calls,
        "tool_calls": float(research_usage.get("tool_calls", 0)),
        "fixture_cost_usd": float(research_usage.get("fixture_cost_usd", 0)),
        "fixture_latency_seconds": float(research_usage.get("fixture_latency_seconds", 0)),
        "fixture_instrumentation_available": float(
            "fixture_cost_usd" in research_usage or "fixture_latency_seconds" in research_usage
        ),
        "research_passes": float(state.research_pass if state else 0),
        "source_failures": float(state.source_failures if state else 0),
        "profile_repairs": float(state.profile_repair_count if state else 0),
        "acceptance_quality_score": acceptance_quality_score,
        "accepted_result": float(accepted_result),
    }


@dataclass
class ResearchService:
    """Run the normal workflow with bounded research and validation passes."""

    settings: Settings
    store: RunStore
    enable_telemetry: bool = True

    def __post_init__(self) -> None:
        self.tracer = (
            configure_tracing(
                self.settings.data_dir,
                content_tracing_enabled=self.settings.content_tracing_enabled,
                otlp_endpoint=self.settings.otlp_endpoint,
            )
            if self.enable_telemetry
            else trace.get_tracer("product_factory_app")
        )

    def run(
        self,
        request: CompanyResearchRequest,
        *,
        source: ResearchSource | None = None,
        extraction_generator: Any | None = None,
        research_agent: Any | None = None,
        providers: list[ProviderMetadata] | None = None,
    ) -> ResearchRun:
        """Execute, observe, and persist one complete company-research run."""

        started = datetime.now(timezone.utc)
        started_clock = perf_counter()
        source = source or self._source_for(request)
        analytics = (
            SemanticAnalytics(
                self.store,
                request.execution_id,
                self.settings.workflow_version,
                domain_config=self.settings.domain_config,
                sdk_enabled=self.settings.witdem_sdk_enabled,
            )
            if self.enable_telemetry
            else None
        )
        manifest = RunManifest(
            execution_id=request.execution_id,
            scenario=request.scenario,
            workflow_version=self.settings.workflow_version,
            status=RunStatus.RUNNING,
            started_at=started,
            request=request,
            providers=providers or [],
            tools=list(self.settings.enabled_tools),
            configuration={
                "max_research_passes": self.settings.max_research_passes,
                "max_profile_repairs": self.settings.max_profile_repairs,
                "max_model_calls": self.settings.max_model_calls,
                "acceptance_min_quality_score": self.settings.acceptance_min_quality_score,
                "content_tracing_enabled": self.settings.content_tracing_enabled,
                "otlp_endpoint_configured": bool(self.settings.otlp_endpoint),
                "witdem_sdk_enabled": self.settings.witdem_sdk_enabled,
                "known_pim_vendors": self.settings.known_pim_vendors,
                "domain": self.settings.domain_config.domain,
                "domain_config_version": self.settings.domain_config.version,
                "domain_policies": self.settings.domain_config.policies.model_dump(mode="json"),
            },
        )
        state: ResearchState | None = None
        profile = None
        assessment = None
        validation: ValidationResult | None = None
        research_usage: dict[str, Any] = {}
        extraction_usage: dict[str, Any] = {}
        extraction_error = ""
        execution_trace_id: str | None = None
        execution_context_token = context.attach(baggage.set_baggage("witdem.execution_id", request.execution_id))
        try:
            with self.tracer.start_as_current_span("research.execution") as span:
                if analytics:
                    analytics.bind_trace_context(span.get_span_context())
                execution_trace_id = _trace_id()
                span.set_attribute("witdem.execution_id", request.execution_id)
                span.set_attribute("pf.scenario", request.scenario)
                if analytics:
                    analytics.execution(
                        scenario=request.scenario,
                        workflow_version=self.settings.workflow_version,
                    )
                components = build_workflow_components(
                    source,
                    analytics=analytics,
                    extraction_generator=extraction_generator,
                    research_agent=research_agent,
                    known_pim_vendors=self.settings.known_pim_vendors,
                    domain_config=self.settings.domain_config,
                )
                assessment_pipeline = build_research_assessment_pipeline(components)
                profile_components = build_workflow_components(
                    source,
                    analytics=analytics,
                    extraction_generator=extraction_generator,
                    research_agent=research_agent,
                    known_pim_vendors=self.settings.known_pim_vendors,
                    domain_config=self.settings.domain_config,
                )
                profile_pipeline = build_profile_pipeline(profile_components)
                state = components.initializer.run(request)["state"]
                for _ in range(self.settings.max_research_passes):
                    outputs = assessment_pipeline.run(
                        {"research_company": {"state": state}},
                        include_outputs_from={
                            "research_company",
                            "assess_evidence",
                        },
                    )
                    _merge_usage(research_usage, outputs["research_company"].get("usage", {}))
                    state = outputs["assess_evidence"]["state"]
                    assessment = outputs["assess_evidence"]["assessment"]
                    if assessment.sufficient:
                        break
                if assessment is None or not assessment.sufficient:
                    if state is not None:
                        state.termination_reason = "research_pass_limit_exhausted"
                    raise RuntimeError(
                        f"research incomplete after {self.settings.max_research_passes} passes: "
                        f"{state.missing_signals if state else ['unknown']}"
                    )
                profile_outputs = profile_pipeline.run(
                    {
                        "extract_company_profile": {"state": state},
                        "validate_company_profile": {"state": state},
                    },
                    include_outputs_from={"extract_company_profile", "validate_company_profile"},
                )
                profile = profile_outputs["extract_company_profile"]["profile"]
                validation = profile_outputs["validate_company_profile"]["validation"]
                _merge_usage(extraction_usage, profile_outputs["extract_company_profile"].get("usage", {}))
                extraction_error = profile_outputs["extract_company_profile"].get("error", "")
                for _ in range(self.settings.max_profile_repairs):
                    if validation and validation.valid:
                        break
                    feedback_items = validation.errors if validation else ["profile validation failed"]
                    if extraction_error:
                        feedback_items = [*feedback_items, extraction_error]
                    feedback = "; ".join(feedback_items)
                    state.profile_repair_count += 1
                    correction = profile_pipeline.run(
                        {
                            "extract_company_profile": {
                                "state": state,
                                "correction_feedback": feedback,
                            },
                            "validate_company_profile": {"state": state},
                        },
                        include_outputs_from={"extract_company_profile", "validate_company_profile"},
                    )
                    profile = correction["extract_company_profile"]["profile"]
                    _merge_usage(extraction_usage, correction["extract_company_profile"].get("usage", {}))
                    extraction_error = correction["extract_company_profile"].get("error", "")
                    validation = correction["validate_company_profile"]["validation"]
                if validation is None or not validation.valid or profile is None:
                    raise RuntimeError(
                        "profile validation failed: "
                        + "; ".join(validation.errors if validation else ["no validation result"])
                    )
                acceptance = evaluate_profile_acceptance(
                    profile,
                    validation,
                    state,
                    minimum_quality_score=self.settings.acceptance_min_quality_score,
                )
                state.termination_reason = (
                    "accepted_result" if acceptance.status is AcceptanceStatus.ACCEPTED else "valid_result_not_accepted"
                )
                if acceptance.status is AcceptanceStatus.ACCEPTED:
                    manifest.accepted_at = datetime.now(timezone.utc)
                manifest.status = RunStatus.RECOVERED if state.research_pass > 1 else RunStatus.SUCCEEDED
                manifest.trace_id = execution_trace_id
                manifest.finished_at = datetime.now(timezone.utc)
                manifest.result_path = str(self.store.run_dir(request.execution_id) / "result.json")
                if analytics:
                    if manifest.accepted_at is not None:
                        analytics.acceptance(
                            accepted_at=manifest.accepted_at.isoformat(),
                            quality_score=acceptance.quality_score,
                            reason_code=acceptance.reason_code,
                        )
                    analytics.evaluate(
                        "profile_quality",
                        value=acceptance.quality_score,
                        source="product_factory_acceptance",
                        score=float(acceptance.quality_score or 0),
                        confidence=float(acceptance.quality_score or 0),
                    )
                    analytics.evaluate(
                        "qualification_score",
                        value=profile.qualification.fit_score,
                        source="product_factory_scoring",
                        score=min(max(profile.qualification.fit_score / 100.0, 0.0), 1.0),
                        confidence=profile.confidence,
                    )
                    analytics.outcome(
                        manifest.status.value,
                        research_passes=state.research_pass,
                        execution_completed=True,
                        result_valid=True,
                        accepted=acceptance.status is AcceptanceStatus.ACCEPTED,
                        acceptance_reason=acceptance.reason_code,
                        validation_warnings=len(validation.warnings),
                    )
                result = ResearchRun(
                    manifest=manifest,
                    state=state,
                    assessment=assessment,
                    profile=profile,
                    validation=validation,
                    execution_completed=True,
                    result_valid=True,
                    acceptance=acceptance,
                    metrics=_run_metrics(
                        started_at=started,
                        started_clock=started_clock,
                        research_usage=research_usage,
                        extraction_usage=extraction_usage,
                        state=state,
                        acceptance_quality_score=float(acceptance.quality_score or 0),
                        accepted_result=acceptance.status is AcceptanceStatus.ACCEPTED,
                        accepted_at=manifest.accepted_at,
                    ),
                )
                self.store.save_run(result)
                return result
        except Exception as exc:
            manifest.status = RunStatus.FAILED
            manifest.trace_id = execution_trace_id
            manifest.finished_at = datetime.now(timezone.utc)
            manifest.error = str(exc)
            acceptance = evaluate_profile_acceptance(
                profile,
                validation,
                state,
                minimum_quality_score=self.settings.acceptance_min_quality_score,
            )
            if state is not None:
                state.termination_reason = (
                    "research_pass_limit_exhausted"
                    if state.source_failures >= self.settings.max_research_passes
                    else "terminal_failure"
                )
            if analytics:
                analytics.outcome(
                    "failed",
                    execution_completed=False,
                    result_valid=False,
                    accepted=False,
                    error=str(exc),
                )
            result = ResearchRun(
                manifest=manifest,
                state=state,
                assessment=assessment,
                profile=profile,
                validation=validation,
                execution_completed=False,
                result_valid=False,
                acceptance=acceptance,
                metrics=_run_metrics(
                    started_at=started,
                    started_clock=started_clock,
                    research_usage=research_usage,
                    extraction_usage=extraction_usage,
                    state=state,
                    acceptance_quality_score=float(acceptance.quality_score or 0),
                    accepted_result=False,
                    accepted_at=None,
                ),
            )
            self.store.save_run(result)
            return result
        finally:
            manifest.finished_at = manifest.finished_at or datetime.now(timezone.utc)
            context.detach(execution_context_token)
            force_flush_tracing()

    def _source_for(self, request: CompanyResearchRequest) -> ResearchSource:
        if request.research_mode.value == "website":
            return WebsiteResearchSource()
        return FixtureResearchSource(request.scenario)
