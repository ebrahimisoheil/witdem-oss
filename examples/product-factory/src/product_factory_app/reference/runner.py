"""Single-case orchestration, telemetry, and deterministic assessment."""

from __future__ import annotations

import asyncio
import os
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from opentelemetry import trace

from product_factory_app.reference.cases import load_case, runtime_case
from product_factory_app.reference.contracts import ProductFactoryResult, RuntimeOutput
from product_factory_app.reference.gateways import DeterministicGateway, LiveGateway, ModelGateway
from product_factory_app.reference.policy import assess_goal, decide
from product_factory_app.reference.profiles import MODEL_PROFILES, RUNTIME_DEFAULT_PROFILE
from product_factory_app.reference.runtimes import RUNTIMES


def _trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


async def _wait_for_analytics(
    execution_id: str, *, timeout: float = 60.0
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Wait until durable corpus batches have reached the serving schema."""

    endpoint = os.getenv("WITDEM_ENDPOINT", "http://localhost:4318").rstrip("/")
    deadline = perf_counter() + timeout
    last_status = "pending"
    batches: list[dict[str, Any]] = []
    serving_fact: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=3.0) as http:
        while perf_counter() < deadline:
            try:
                response = await http.get(f"{endpoint}/ingestion/v1/executions/{execution_id}")
                if response.status_code == 200:
                    payload = response.json()
                    last_status = str(payload.get("status") or "pending")
                    batches = list(payload.get("batches") or [])
                    serving_fact = dict(payload.get("serving_fact") or {})
                    if last_status in {"ready", "failed"}:
                        return last_status, batches, serving_fact
            except httpx.HTTPError:
                last_status = "unreachable"
            await asyncio.sleep(0.2)
    return ("timeout" if last_status == "pending" else last_status), batches, serving_fact


async def run_case(
    case_id: str,
    runtime_id: str,
    profile: str | None = None,
    *,
    live: bool = False,
    telemetry: bool = True,
    gateway: ModelGateway | None = None,
) -> ProductFactoryResult:
    started_clock = perf_counter()
    case = load_case(case_id)
    visible_case = runtime_case(case)
    if runtime_id not in RUNTIMES:
        raise ValueError(f"Unknown runtime {runtime_id!r}; choose from {', '.join(RUNTIMES)}")
    resolved_profile = profile or RUNTIME_DEFAULT_PROFILE[runtime_id]
    if resolved_profile not in MODEL_PROFILES:
        raise ValueError(f"Unknown model profile {resolved_profile!r}")
    execution_id = uuid4().hex
    dashboard = os.getenv("WITDEM_DASHBOARD_URL", "http://localhost:8501")
    selected_gateway: ModelGateway = gateway or (LiveGateway() if live else DeterministicGateway())
    client: Any = None
    result: ProductFactoryResult | None = None
    captured_trace_id: str | None = None

    if telemetry:
        from witdem_sdk import configure

        client = configure(
            runtime=runtime_id,
            resource_attributes={"product_factory.case_id": case_id, "product_factory.model_profile": resolved_profile},
        )

    def observe(stage: str) -> None:
        if client is not None:
            with client.operation(
                f"product_factory.{stage}",
                kind="workflow_stage",
                attributes={"product_factory.stage": stage, "product_factory.runtime": runtime_id},
            ):
                pass

    try:
        if client is not None:
            with client.execution(
                "product_factory.qualify_company",
                execution_id=execution_id,
                attributes={
                    "product_factory.case_id": case_id,
                    "product_factory.runtime": runtime_id,
                    "product_factory.model_profile": resolved_profile,
                },
            ):
                captured_trace_id = _trace_id()
                try:
                    runtime_output = await RUNTIMES[runtime_id]().execute(
                        visible_case,
                        profile=resolved_profile,
                        gateway=selected_gateway,
                        observe=observe,
                        witdem=client,
                    )
                except Exception as exc:
                    runtime_output = RuntimeOutput(
                        runtime_id=runtime_id,
                        model_profile=resolved_profile,
                        terminal=False,
                        error=f"{type(exc).__name__}: {exc}",
                        actual_models=dict(selected_gateway.actual_models),
                        usage=dict(selected_gateway.usage),
                    )
                decision = decide(case, runtime_output)
                goal = assess_goal(case, runtime_output, decision)
                result = ProductFactoryResult(
                    execution_id=execution_id,
                    trace_id=captured_trace_id,
                    dashboard_url=f"{dashboard}/?execution_id={execution_id}",
                    case_id=case_id,
                    runtime_id=runtime_id,
                    model_profile=resolved_profile,
                    expected_status=case.expected_status,
                    runtime=runtime_output,
                    decision=decision,
                    goal=goal,
                    cost_unavailable_reason="pending_server_analytics",
                )
                client.report(
                    contract="company_qualification",
                    result=decision.observed_status.value,
                    result_valid=decision.artifact_valid,
                    decision=decision.observed_status.value,
                    expected_decision=case.expected_status.value,
                    decision_correct=goal.decision_correct,
                    requirements={
                        "runtime_terminal": runtime_output.terminal,
                        "valid_profile": decision.artifact_valid,
                        "sufficient_evidence": decision.decision_evidence_sufficient,
                        "required_path": goal.required_path_observed,
                        "correct_decision": goal.decision_correct,
                    },
                    metrics={"qualification_score": decision.qualification_score},
                    dimensions={
                        "case_id": case_id,
                        "runtime_id": runtime_id,
                        "model_profile": resolved_profile,
                    },
                    evidence_sufficient=decision.decision_evidence_sufficient,
                    required_path_observed=goal.required_path_observed,
                    threshold=case.policy.qualification_threshold,
                    threshold_margin=decision.threshold_margin,
                    attributes={
                        "targeted_research_performed": runtime_output.targeted_research_performed,
                        "targeted_research_required": case.expected_targeted_research,
                    },
                )
        else:
            runtime_output = await RUNTIMES[runtime_id]().execute(
                visible_case,
                profile=resolved_profile,
                gateway=selected_gateway,
                observe=observe,
                witdem=client,
            )
            decision = decide(case, runtime_output)
            goal = assess_goal(case, runtime_output, decision)
            result = ProductFactoryResult(
                execution_id=execution_id,
                dashboard_url=f"{dashboard}/?execution_id={execution_id}",
                case_id=case_id,
                runtime_id=runtime_id,
                model_profile=resolved_profile,
                expected_status=case.expected_status,
                runtime=runtime_output,
                decision=decision,
                goal=goal,
                cost_unavailable_reason="telemetry_disabled",
            )
    except Exception as exc:
        runtime_output = RuntimeOutput(
            runtime_id=runtime_id,
            model_profile=resolved_profile,
            terminal=False,
            error=f"{type(exc).__name__}: {exc}",
            actual_models=dict(selected_gateway.actual_models),
            usage=dict(selected_gateway.usage),
        )
        decision = decide(case, runtime_output)
        goal = assess_goal(case, runtime_output, decision)
        result = ProductFactoryResult(
            execution_id=execution_id,
            trace_id=captured_trace_id,
            dashboard_url=f"{dashboard}/?execution_id={execution_id}",
            case_id=case_id,
            runtime_id=runtime_id,
            model_profile=resolved_profile,
            expected_status=case.expected_status,
            runtime=runtime_output,
            decision=decision,
            goal=goal,
            cost_unavailable_reason="runtime_failure",
        )
    finally:
        if isinstance(selected_gateway, LiveGateway):
            await selected_gateway.aclose()
        if client is not None:
            # Native framework processors can enqueue hundreds of records for
            # one rich run. Let the bounded queue drain before validating the
            # cell; a five-second snapshot can incorrectly report healthy,
            # still-in-flight delivery as a failed experiment.
            client.flush(timeout=60)
            if result is not None:
                result.delivery_status = client.delivery_status().__dict__
            client.shutdown()
            if result is not None and live:
                (
                    result.analytics_status,
                    result.analytics_batches,
                    result.analytics_fact,
                ) = await _wait_for_analytics(execution_id)
                if result.analytics_fact.get("measured_cost") is not None:
                    result.measured_cost_usd = float(result.analytics_fact["measured_cost"])
                    result.cost_unavailable_reason = None
                elif result.analytics_status == "ready":
                    result.cost_unavailable_reason = "unmeasured_serving_fact"
    assert result is not None
    result.latency_seconds = perf_counter() - started_clock
    return result
