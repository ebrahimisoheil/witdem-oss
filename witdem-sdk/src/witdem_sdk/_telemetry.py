"""Unified Witdem telemetry and semantic-record setup."""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from types import TracebackType
from typing import Any, TypeVar, cast
from uuid import uuid4

from opentelemetry import baggage
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace import Span as SDKSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer
from typing_extensions import Self

from witdem_sdk._config import api_key as configured_api_key
from witdem_sdk._config import configure_records_endpoint, records_endpoint
from witdem_sdk._contract import (
    ContractResult,
    DescriptiveContractSpec,
    WitdemProjectConfig,
    contract_definition,
    evaluate_contract,
    load_project_config,
)
from witdem_sdk._transport import DeliveryStatus
from witdem_sdk._transport import flush as flush_records

_EXECUTION_ID_KEY = "witdem.execution_id"
_CONFIGURED_PROVIDER_IDS: set[int] = set()
_CONFIGURED_EXPORTER_CONFIGS: set[tuple[int, str, tuple[tuple[str, str], ...]]] = set()
_PROVIDER_EXPORTER_CONFIG: dict[int, tuple[str, tuple[tuple[str, str], ...]]] = {}
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


class _ExecutionIdSpanProcessor(SpanProcessor):
    def on_start(self, span: SDKSpan, parent_context: otel_context.Context | None = None) -> None:
        execution_id = baggage.get_baggage(_EXECUTION_ID_KEY, parent_context)
        if isinstance(execution_id, str):
            span.set_attribute(_EXECUTION_ID_KEY, execution_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def _telemetry_endpoint(explicit: str | None) -> str:
    base = explicit or records_endpoint()
    return f"{base.rstrip('/')}/v1/traces"


def _telemetry_headers(explicit_api_key: str | None) -> dict[str, str] | None:
    token = explicit_api_key or configured_api_key()
    return {"Authorization": f"Bearer {token}"} if token else None


def _set_attributes(span: Span, attributes: Mapping[str, Any] | None) -> None:
    for key, value in (attributes or {}).items():
        if value is not None:
            span.set_attribute(key, value)


@dataclass
class Operation:
    """A model/tool operation whose observed response facts can be recorded."""

    span: Span

    def usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_creation_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        audio_input_tokens: int | None = None,
        audio_output_tokens: int | None = None,
        image_input_tokens: int | None = None,
        image_output_tokens: int | None = None,
        video_input_tokens: int | None = None,
        video_output_tokens: int | None = None,
        search_queries: int | None = None,
        meters: Mapping[str, int | float] | None = None,
    ) -> Self:
        values: dict[str, Any] = {
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.usage.total_tokens": total_tokens,
            "gen_ai.usage.cache_read.input_tokens": cache_read_tokens,
            "gen_ai.usage.cache_creation.input_tokens": cache_creation_tokens,
            "gen_ai.usage.reasoning.output_tokens": reasoning_tokens,
            "gen_ai.usage.audio.input_tokens": audio_input_tokens,
            "gen_ai.usage.audio.output_tokens": audio_output_tokens,
            "gen_ai.usage.image.input_tokens": image_input_tokens,
            "gen_ai.usage.image.output_tokens": image_output_tokens,
            "gen_ai.usage.video.input_tokens": video_input_tokens,
            "gen_ai.usage.video.output_tokens": video_output_tokens,
            "gen_ai.usage.search_queries": search_queries,
        }
        for name, value in (meters or {}).items():
            normalized = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
            if not normalized or not all(character.isalnum() or character in "._" for character in normalized):
                raise ValueError(f"invalid usage meter name: {name!r}")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"usage meter {name!r} must be a non-negative number")
            values[f"gen_ai.usage.{normalized}"] = value
        _set_attributes(self.span, values)
        return self

    def response_model(self, model: str | None) -> Self:
        if model:
            self.span.set_attribute("gen_ai.response.model", model)
        return self

    def cost(self, amount_usd: float, *, source: str = "provider_reported") -> Self:
        self.span.set_attribute("gen_ai.cost.usd", amount_usd)
        self.span.set_attribute("gen_ai.cost.source", source)
        return self


class Witdem:
    """One configured client for both traces and business-semantic records."""

    def __init__(
        self,
        *,
        service_name: str,
        runtime: str | None = None,
        endpoint: str | None = None,
        telemetry_mode: str = "auto",
        tracer_provider: TracerProvider | None = None,
        resource_attributes: Mapping[str, Any] | None = None,
        api_key_value: str | None = None,
        project_config: WitdemProjectConfig | None = None,
    ) -> None:
        self.service_name = service_name
        self.runtime = runtime or service_name
        self.project_config = project_config
        if telemetry_mode not in {"auto", "existing", "disabled"}:
            raise ValueError("telemetry_mode must be 'auto', 'existing', or 'disabled'")
        self.telemetry_mode = telemetry_mode
        self._owns_provider = False
        provider = tracer_provider or otel_trace.get_tracer_provider()
        if telemetry_mode == "auto":
            if tracer_provider is not None and not isinstance(provider, TracerProvider):
                raise TypeError("witdem_sdk: the explicitly passed tracer provider cannot accept Witdem processors")
            if not isinstance(provider, TracerProvider):
                if not isinstance(provider, otel_trace.ProxyTracerProvider):
                    raise TypeError(
                        "witdem_sdk: an incompatible global tracer provider is already installed; "
                        "use telemetry_mode='existing' or pass a compatible SDK TracerProvider"
                    )
                provider = TracerProvider(
                    resource=Resource.create(
                        {
                            "service.name": service_name,
                            "witdem.runtime": self.runtime,
                            **dict(resource_attributes or {}),
                        }
                    )
                )
                otel_trace.set_tracer_provider(provider)
                self._owns_provider = True
        elif telemetry_mode == "disabled":
            provider = otel_trace.NoOpTracerProvider()
        self._provider = provider if isinstance(provider, TracerProvider) else None
        provider_id = id(self._provider) if self._provider is not None else None
        if self._provider is not None and telemetry_mode != "existing" and provider_id is not None:
            if provider_id not in _CONFIGURED_PROVIDER_IDS:
                self._provider.add_span_processor(_ExecutionIdSpanProcessor())
                _CONFIGURED_PROVIDER_IDS.add(provider_id)
            if telemetry_mode == "auto":
                exporter_kwargs: dict[str, Any] = {"endpoint": _telemetry_endpoint(endpoint)}
                headers = _telemetry_headers(api_key_value)
                if headers:
                    exporter_kwargs["headers"] = headers
                exporter_config = (
                    provider_id,
                    str(exporter_kwargs["endpoint"]),
                    tuple(sorted((str(key), str(value)) for key, value in headers.items())) if headers else (),
                )
                requested_config = (exporter_config[1], exporter_config[2])
                configured = _PROVIDER_EXPORTER_CONFIG.get(provider_id)
                if configured is not None and configured != requested_config:
                    raise ValueError(
                        "witdem_sdk: this tracer provider is already configured with a different "
                        "Witdem endpoint or API key; reuse the original configuration or pass another provider"
                    )
                if exporter_config not in _CONFIGURED_EXPORTER_CONFIGS:
                    self._provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs)))
                    _CONFIGURED_EXPORTER_CONFIGS.add(exporter_config)
                    _PROVIDER_EXPORTER_CONFIG[provider_id] = requested_config
        self._tracer: Tracer = provider.get_tracer(service_name)
        self._endpoint_was_overridden = endpoint is not None
        self._previous_endpoint = configure_records_endpoint(endpoint) if endpoint is not None else None
        from witdem_sdk._transport import configure_api_key

        self._previous_api_key = configure_api_key(api_key_value) if api_key_value is not None else None
        self._closed = False
        self._reported_contract_definitions: set[str] = set()

    @contextmanager
    def execution(
        self,
        name: str | None = None,
        *,
        execution_id: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[str]:
        """Create one correlated execution for traces and SDK records."""

        resolved_name = name or self.service_name
        resolved_id = execution_id
        if resolved_id is None and getattr(self, "telemetry_mode", "auto") == "disabled":
            active_context = otel_trace.get_current_span().get_span_context()
            if not active_context.is_valid:
                raise ValueError(
                    "witdem_sdk: telemetry_mode='disabled' requires execution_id when there is no active trace"
                )
            resolved_id = f"{active_context.trace_id:032x}"
        resolved_id = resolved_id or uuid4().hex
        context = baggage.set_baggage(_EXECUTION_ID_KEY, resolved_id)
        token = otel_context.attach(context)
        try:
            with self._tracer.start_as_current_span(resolved_name, kind=SpanKind.INTERNAL) as span:
                span.set_attribute(_EXECUTION_ID_KEY, resolved_id)
                span.set_attribute("witdem.execution.name", resolved_name)
                span.set_attribute("witdem.runtime.kind", "workflow")
                span.set_attribute("witdem.runtime.name", self.runtime)
                span.set_attribute("witdem.runtime", self.runtime)
                _set_attributes(span, attributes)
                from witdem_sdk import event, outcome

                event("execution.started", {"service": self.service_name})
                try:
                    yield resolved_id
                except BaseException as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    outcome("execution.completed", status="error")
                    raise
                else:
                    outcome("execution.completed", status="success")
        finally:
            otel_context.detach(token)

    @contextmanager
    def model(
        self,
        name: str,
        *,
        provider: str,
        model: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Operation]:
        with self._tracer.start_as_current_span(name, kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", provider)
            span.set_attribute("gen_ai.request.model", model)
            _set_attributes(span, attributes)
            yield Operation(span)

    @contextmanager
    def tool(
        self,
        name: str,
        *,
        call_id: str | None = None,
        cost_usd: float = 0.0,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Operation]:
        with self._tracer.start_as_current_span(f"tool.{name}", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("gen_ai.cost.usd", cost_usd)
            if call_id:
                span.set_attribute("gen_ai.tool.call.id", call_id)
            _set_attributes(span, attributes)
            yield Operation(span)

    @contextmanager
    def operation(
        self,
        name: str,
        *,
        kind: str = "component",
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Operation]:
        with self._tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
            span.set_attribute("witdem.runtime.kind", kind)
            _set_attributes(span, attributes)
            yield Operation(span)

    def event(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> None:
        from witdem_sdk import event

        event(name, payload, attributes=attributes, execution_id=execution_id)

    def decision(
        self,
        name: str,
        value: Any,
        *,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> None:
        from witdem_sdk import decision

        decision(name, value, attributes=attributes, execution_id=execution_id)

    def evaluation(
        self,
        name: str,
        *,
        score: float | None = None,
        label: str | None = None,
        value: Any = None,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> None:
        from witdem_sdk import evaluation

        evaluation(name, score=score, label=label, value=value, attributes=attributes, execution_id=execution_id)

    def outcome(
        self,
        name: str,
        *,
        status: str | None = None,
        value: Any = None,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> None:
        from witdem_sdk import outcome

        outcome(name, status=status, value=value, attributes=attributes, execution_id=execution_id)

    def metric(
        self,
        name: str,
        value: Any,
        *,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> None:
        from witdem_sdk import metric

        metric(name, value, attributes=attributes, execution_id=execution_id)

    def report(
        self,
        *,
        result: str,
        product_goal_achieved: bool,
        contract: str | None = None,
        result_valid: bool = True,
        decision: str | bool | None = None,
        expected_decision: str | bool | None = None,
        decision_correct: bool | None = None,
        evaluations: Mapping[str, Any] | None = None,
        metrics: Mapping[str, Any] | None = None,
        dimensions: Mapping[str, Any] | None = None,
        evidence_sufficient: bool = True,
        required_path_observed: bool = True,
        closest_blocker: str = "none",
        threshold: float | None = None,
        threshold_margin: float | None = None,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> ContractResult:
        """Report explicit business meaning for a metadata-only contract.

        The YAML supplies names and descriptions.  This call supplies only the
        values known by the application; it never evaluates paths or rules.
        Framework integrations continue to report runtime telemetry
        automatically.
        """

        config = self.project_config
        if config is None:
            raise ValueError("witdem_sdk: no project contract loaded; run 'witdem-sdk init' or pass config_path")
        contract_name = contract or config.default_contract
        if not contract_name:
            raise ValueError("witdem_sdk: specify contract= or set default_contract in .witdem/witdem.yaml")
        spec = config.contracts.get(contract_name)
        if spec is None:
            raise ValueError(f"witdem_sdk: unknown contract {contract_name!r}")
        if not isinstance(spec, DescriptiveContractSpec):
            raise ValueError(
                "witdem_sdk: Witdem.report(...) requires a metadata-only contract; "
                "use Witdem.complete(...) for expression contracts"
            )

        if spec.result.values and result not in spec.result.values:
            allowed = ", ".join(spec.result.values)
            raise ValueError(f"witdem_sdk: result {result!r} is not declared; expected one of: {allowed}")
        if (
            decision is not None
            and spec.decision
            and spec.decision.values
            and str(decision) not in spec.decision.values
        ):
            allowed = ", ".join(spec.decision.values)
            raise ValueError(f"witdem_sdk: decision {decision!r} is not declared; expected one of: {allowed}")
        if (
            expected_decision is not None
            and spec.decision
            and spec.decision.values
            and str(expected_decision) not in spec.decision.values
        ):
            allowed = ", ".join(spec.decision.values)
            raise ValueError(
                f"witdem_sdk: expected_decision {expected_decision!r} is not declared; "
                f"expected one of: {allowed}"
            )
        undeclared_dimensions = sorted(set(dimensions or {}) - set(spec.dimensions))
        if undeclared_dimensions:
            declared = ", ".join(spec.dimensions) or "none"
            unknown = ", ".join(undeclared_dimensions)
            raise ValueError(
                f"witdem_sdk: dimensions not declared in contract {contract_name!r}: {unknown}; "
                f"declared dimensions: {declared}"
            )
        if decision_correct is None and expected_decision is not None and decision is not None:
            decision_correct = decision == expected_decision

        definition_hash, definition = contract_definition(config, contract_name, spec)
        reported = getattr(self, "_reported_contract_definitions", None)
        if reported is None:
            reported = set()
            self._reported_contract_definitions = reported
        if definition_hash not in reported:
            self.event(
                "contract.definition",
                definition,
                attributes={
                    "contract_name": contract_name,
                    "contract_hash": definition_hash,
                    "contract_version": "1.0",
                },
                execution_id=execution_id,
            )
            reported.add(definition_hash)

        shared = {
            **dict(dimensions or {}),
            **dict(attributes or {}),
            "contract_name": contract_name,
            "contract_hash": definition_hash,
            "contract_description": spec.description,
            "result_name": spec.result.name,
            "result_description": spec.result.description,
            "decision_description": spec.decision.description if spec.decision else None,
            "product_goal_name": spec.product_goal.name,
            "product_goal_description": spec.product_goal.description,
        }
        goal_attributes = {
            **shared,
            "contract_version": "1.0",
            "expected_status": expected_decision,
            "observed_status": decision,
            "decision_correct": decision_correct,
            "product_goal_achieved": product_goal_achieved,
            "artifact_valid": result_valid,
            "decision_evidence_sufficient": evidence_sufficient,
            "required_path_observed": required_path_observed,
            "closest_blocker": closest_blocker,
        }
        if threshold is not None:
            goal_attributes["threshold"] = threshold
        if threshold_margin is not None:
            goal_attributes["threshold_margin"] = threshold_margin

        self.event(
            "contract.completed",
            {
                **shared,
                "application_status": result,
                "artifact_valid": result_valid,
                "decision_correct": decision_correct,
                "product_goal_achieved": product_goal_achieved,
            },
            execution_id=execution_id,
        )
        self.evaluation(
            f"{spec.result.name} validity",
            score=1.0 if result_valid else 0.0,
            label="valid" if result_valid else "invalid",
            attributes=shared,
            execution_id=execution_id,
        )
        for key, value in (evaluations or {}).items():
            definition_spec = spec.evaluations.get(key)
            if definition_spec is None:
                raise ValueError(f"witdem_sdk: evaluation {key!r} is not declared in contract {contract_name!r}")
            evaluation_attributes = {
                **shared,
                "evaluation_key": key,
                "evaluation_description": definition_spec.description,
                "unit": definition_spec.unit,
                "target": definition_spec.target,
                "direction": definition_spec.direction,
            }
            if isinstance(value, bool):
                self.evaluation(
                    definition_spec.name,
                    value=value,
                    label="yes" if value else "no",
                    attributes=evaluation_attributes,
                    execution_id=execution_id,
                )
            elif isinstance(value, (int, float)):
                self.evaluation(
                    definition_spec.name,
                    score=float(value),
                    value=value,
                    attributes=evaluation_attributes,
                    execution_id=execution_id,
                )
            else:
                self.evaluation(
                    definition_spec.name,
                    value=value,
                    label=str(value) if value is not None else None,
                    attributes=evaluation_attributes,
                    execution_id=execution_id,
                )
        for key, value in (metrics or {}).items():
            metric_definition = spec.metrics.get(key)
            if metric_definition is None:
                raise ValueError(f"witdem_sdk: metric {key!r} is not declared in contract {contract_name!r}")
            self.metric(
                metric_definition.name,
                value,
                attributes={
                    **shared,
                    "metric_key": key,
                    "metric_description": metric_definition.description,
                    "unit": metric_definition.unit,
                },
                execution_id=execution_id,
            )
        if decision is not None:
            self.decision(
                spec.decision.name if spec.decision else "Application decision",
                decision,
                attributes={
                    **shared,
                    "expected_status": expected_decision,
                    "observed_status": decision,
                    "decision_correct": decision_correct,
                    "outcome_description": (
                        spec.decision.values.get(str(decision)) if spec.decision else None
                    ),
                },
                execution_id=execution_id,
            )
        self.outcome(
            "application_outcome",
            status=result,
            attributes=shared,
            execution_id=execution_id,
        )
        self.outcome(
            "product_goal",
            status="achieved" if product_goal_achieved else "failed",
            attributes=goal_attributes,
            execution_id=execution_id,
        )
        return ContractResult(
            contract=contract_name,
            application_status=result,
            artifact_valid=result_valid,
            expected_status=expected_decision,
            observed_status=decision,
            decision_correct=decision_correct,
            product_goal_achieved=product_goal_achieved,
            attributes=goal_attributes,
        )

    def complete(
        self,
        result: Any,
        *,
        contract: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> ContractResult:
        """Evaluate one configured business contract and emit canonical meaning.

        Framework integrations continue to own physical telemetry. This method
        is the single application-side handoff for result validity, decision
        correctness, and product-goal success.
        """

        config = self.project_config
        if config is None:
            raise ValueError("witdem_sdk: no project contract loaded; run 'witdem-sdk init' or pass config_path")
        contract_name = contract or config.default_contract
        if not contract_name:
            raise ValueError("witdem_sdk: specify contract= or set default_contract in .witdem/witdem.yaml")
        spec = config.contracts.get(contract_name)
        if spec is None:
            raise ValueError(f"witdem_sdk: unknown contract {contract_name!r}")
        if isinstance(spec, DescriptiveContractSpec):
            raise ValueError(
                "witdem_sdk: this metadata-only contract requires Witdem.report(...); "
                "Witdem.complete(...) requires an expression contract"
            )
        evaluated = evaluate_contract(contract_name, spec, result)
        from witdem_sdk._contract import evaluate, result_context

        context = result_context(result)
        configured_attributes = {
            key: evaluate(value, context) for key, value in spec.attributes.items()
        }
        definition_hash, definition = contract_definition(config, contract_name, spec)
        reported = getattr(self, "_reported_contract_definitions", None)
        if reported is None:
            reported = set()
            self._reported_contract_definitions = reported
        if definition_hash not in reported:
            self.event(
                "contract.definition",
                definition,
                attributes={
                    "contract_name": contract_name,
                    "contract_hash": definition_hash,
                    "contract_version": "1.0",
                },
                execution_id=execution_id,
            )
            reported.add(definition_hash)
        shared = {
            **configured_attributes,
            **dict(attributes or {}),
            "contract_name": contract_name,
            "contract_hash": definition_hash,
            "contract_description": spec.description,
            "result_name": spec.artifact.name,
            "result_description": spec.artifact.description,
            "decision_description": spec.decision.description,
            "product_goal_name": spec.product_goal.name,
            "product_goal_description": spec.product_goal.description,
        }
        self.event(
            "contract.completed",
            {
                **shared,
                "application_status": evaluated.application_status,
                "artifact_valid": evaluated.artifact_valid,
                "decision_correct": evaluated.decision_correct,
                "product_goal_achieved": evaluated.product_goal_achieved,
            },
            execution_id=execution_id,
        )
        self.evaluation(
            f"{spec.artifact.name} validity",
            score=1.0 if evaluated.artifact_valid else 0.0,
            label="valid" if evaluated.artifact_valid else "invalid",
            attributes=shared,
            execution_id=execution_id,
        )
        semantic_outcome = spec.product_goal.semantic_outcome
        semantic_score = evaluated.attributes.get("semantic_score")
        if semantic_outcome is not None and isinstance(semantic_score, (int, float)):
            self.evaluation(
                semantic_outcome.name,
                score=float(semantic_score),
                label=str(evaluated.attributes.get("assurance_status")),
                attributes={
                    **shared,
                    "evaluation_description": semantic_outcome.description,
                    "unit": "ratio",
                    "target": semantic_outcome.assurance_threshold,
                    "direction": "higher_is_better",
                    "achievement_threshold": semantic_outcome.threshold,
                },
                execution_id=execution_id,
            )
        for evaluation_spec in spec.evaluations:
            self.evaluation(
                evaluation_spec.name,
                score=(
                    float(evaluate(evaluation_spec.score, context))
                    if evaluation_spec.score is not None
                    else None
                ),
                label=(
                    str(evaluate(evaluation_spec.label, context))
                    if evaluation_spec.label is not None
                    else None
                ),
                value=(
                    evaluate(evaluation_spec.value, context)
                    if evaluation_spec.value is not None
                    else None
                ),
                attributes={
                    **shared,
                    "evaluation_description": evaluation_spec.description,
                    "unit": evaluation_spec.unit,
                    "target": evaluation_spec.target,
                    "direction": evaluation_spec.direction,
                    **{
                        key: evaluate(value, context)
                        for key, value in evaluation_spec.attributes.items()
                    },
                },
                execution_id=execution_id,
            )
        for metric_spec in spec.metrics:
            self.metric(
                metric_spec.name,
                evaluate(metric_spec.value, context),
                attributes={
                    **shared,
                    "metric_description": metric_spec.description,
                    "unit": metric_spec.unit,
                    **{
                        key: evaluate(value, context)
                        for key, value in metric_spec.attributes.items()
                    },
                },
                execution_id=execution_id,
            )
        self.decision(
            spec.decision.name,
            evaluated.observed_status,
            attributes={
                **shared,
                "expected_status": evaluated.expected_status,
                "observed_status": evaluated.observed_status,
                "decision_correct": evaluated.decision_correct,
                "decision_reason": evaluated.attributes.get("decision_reason"),
                "outcome_description": spec.decision.outcomes.get(str(evaluated.observed_status)),
            },
            execution_id=execution_id,
        )
        self.outcome(
            "application_outcome",
            status=evaluated.application_status,
            attributes=shared,
            execution_id=execution_id,
        )
        self.outcome(
            "product_goal",
            status="achieved" if evaluated.product_goal_achieved else "failed",
            attributes={**shared, **evaluated.attributes, **dict(attributes or {})},
            execution_id=execution_id,
        )
        return evaluated

    def observe(self, *, contract: str | None = None, name: str | None = None) -> Callable[[_CallableT], _CallableT]:
        """Decorate a sync or async workload and complete its business contract."""

        def decorate(function: _CallableT) -> _CallableT:
            execution_name = name or function.__name__.replace("_", " ").title()
            if inspect.iscoroutinefunction(function):

                @wraps(function)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    with self.execution(execution_name):
                        result = await function(*args, **kwargs)
                        self.complete(result, contract=contract)
                        return result

                return cast(_CallableT, async_wrapper)

            @wraps(function)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.execution(execution_name):
                    result = function(*args, **kwargs)
                    self.complete(result, contract=contract)
                    return result

            return cast(_CallableT, wrapper)

        return decorate

    def flush(self, timeout: float | None = None) -> bool:
        from witdem_sdk._transport import _flush_timeout

        resolved_timeout = _flush_timeout(timeout)
        traces_flushed = (
            self._provider.force_flush(timeout_millis=int(resolved_timeout * 1000)) if self._provider else True
        )
        records_flushed = flush_records(timeout=resolved_timeout)
        return traces_flushed and records_flushed

    def shutdown(self) -> None:
        if self._closed:
            return
        self.flush()
        # A provider installed globally by the SDK is reusable by later
        # equivalent configure() calls in the same process. TracerProvider
        # owns process-exit shutdown; closing one client must not poison it.
        if self._endpoint_was_overridden:
            configure_records_endpoint(self._previous_endpoint)
        if self._previous_api_key is not None:
            from witdem_sdk._transport import configure_api_key

            configure_api_key(self._previous_api_key)
        self._closed = True

    def delivery_status(self) -> DeliveryStatus:
        from witdem_sdk._transport import delivery_status

        return delivery_status()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()


def configure(
    service_name: str | None = None,
    *,
    runtime: str | None = None,
    endpoint: str | None = None,
    telemetry_mode: str | None = None,
    tracer_provider: TracerProvider | None = None,
    resource_attributes: Mapping[str, Any] | None = None,
    api_key: str | None = None,
    config_path: str | None = None,
) -> Witdem:
    """Configure Witdem from explicit values and optional project YAML."""

    project_config = load_project_config(config_path)
    resolved_service = service_name or (project_config.service.name if project_config else None)
    if not resolved_service:
        raise ValueError("witdem_sdk: service_name is required when no .witdem/witdem.yaml is available")
    resolved_runtime = runtime or (project_config.service.runtime if project_config else None)
    resolved_endpoint = endpoint
    if resolved_endpoint is None and not os.getenv("WITDEM_ENDPOINT") and project_config is not None:
        resolved_endpoint = project_config.telemetry.endpoint
    resolved_mode = telemetry_mode or (project_config.telemetry.mode if project_config else "auto")

    return Witdem(
        service_name=resolved_service,
        runtime=resolved_runtime,
        endpoint=resolved_endpoint,
        telemetry_mode=resolved_mode,
        tracer_provider=tracer_provider,
        resource_attributes=resource_attributes,
        api_key_value=api_key,
        project_config=project_config,
    )
