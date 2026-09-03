"""Unified Witdem telemetry and semantic-record setup."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    WitdemProjectConfig,
    contract_definition,
    load_project_config,
)
from witdem_sdk._transport import DeliveryStatus
from witdem_sdk._transport import flush as flush_records

_EXECUTION_ID_KEY = "witdem.execution_id"
_WORKFLOW_ID_KEY = "witdem.workflow.id"
_EVALUATION_CAMPAIGN_KEY = "witdem.evaluation.campaign_id"
_EVALUATION_CASE_KEY = "witdem.evaluation.case_id"
_CONFIGURED_PROVIDER_IDS: set[int] = set()
_CONFIGURED_EXPORTER_CONFIGS: set[tuple[int, str, tuple[tuple[str, str], ...]]] = set()
_PROVIDER_EXPORTER_CONFIG: dict[int, tuple[str, tuple[tuple[str, str], ...]]] = {}
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


class _ExecutionIdSpanProcessor(SpanProcessor):
    def on_start(self, span: SDKSpan, parent_context: otel_context.Context | None = None) -> None:
        execution_id = baggage.get_baggage(_EXECUTION_ID_KEY, parent_context)
        if isinstance(execution_id, str):
            span.set_attribute(_EXECUTION_ID_KEY, execution_id)
        workflow_id = baggage.get_baggage(_WORKFLOW_ID_KEY, parent_context)
        if isinstance(workflow_id, str):
            span.set_attribute(_WORKFLOW_ID_KEY, workflow_id)

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
    measurements: list[dict[str, Any]] = field(default_factory=list)

    def measure(
        self,
        name: str,
        value: int | float,
        *,
        unit: str,
        aggregation: str = "sum",
        scope: str = "operation",
        provenance: str = "application_reported",
    ) -> Self:
        """Record one typed, vendor-neutral operation measurement."""

        key = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
        if not key or not all(character.isalnum() or character in "._" for character in key):
            raise ValueError(f"invalid measurement name: {name!r}")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"measurement {name!r} must be a non-negative number")
        if aggregation not in {"sum", "average", "maximum", "latest"}:
            raise ValueError("aggregation must be sum, average, maximum, or latest")
        item = {
            "key": key,
            "value": value,
            "unit": str(unit),
            "aggregation": aggregation,
            "scope": str(scope),
            "provenance": str(provenance),
        }
        self.measurements = [existing for existing in self.measurements if existing.get("key") != key]
        self.measurements.append(item)
        self.span.set_attribute("witdem.measurements", json.dumps(self.measurements, separators=(",", ":")))
        return self

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
        for name, meter_value in (meters or {}).items():
            normalized = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
            if not normalized or not all(character.isalnum() or character in "._" for character in normalized):
                raise ValueError(f"invalid usage meter name: {name!r}")
            if not isinstance(meter_value, (int, float)) or isinstance(meter_value, bool) or meter_value < 0:
                raise ValueError(f"usage meter {name!r} must be a non-negative number")
            values[f"gen_ai.usage.{normalized}"] = meter_value
        _set_attributes(self.span, values)
        canonical: dict[str, tuple[int | None, str]] = {
            "tokens.input": (input_tokens, "token"),
            "tokens.output": (output_tokens, "token"),
            "tokens.total": (total_tokens, "token"),
            "tokens.cache_read": (cache_read_tokens, "token"),
            "tokens.cache_creation": (cache_creation_tokens, "token"),
            "tokens.reasoning": (reasoning_tokens, "token"),
            "tokens.audio_input": (audio_input_tokens, "token"),
            "tokens.audio_output": (audio_output_tokens, "token"),
            "tokens.image_input": (image_input_tokens, "token"),
            "tokens.image_output": (image_output_tokens, "token"),
            "tokens.video_input": (video_input_tokens, "token"),
            "tokens.video_output": (video_output_tokens, "token"),
            "queries": (search_queries, "query"),
        }
        for name, (canonical_value, unit) in canonical.items():
            if canonical_value is not None:
                self.measure(name, canonical_value, unit=unit, provenance="provider_reported")
        for name, meter_value in (meters or {}).items():
            self.measure(name, meter_value, unit="unit")
        return self

    def response_model(self, model: str | None) -> Self:
        if model:
            self.span.set_attribute("gen_ai.response.model", model)
        return self

    def cost(self, amount_usd: float, *, source: str = "provider_reported") -> Self:
        self.span.set_attribute("gen_ai.cost.usd", amount_usd)
        self.span.set_attribute("gen_ai.cost.source", source)
        self.measure("cost.usd", amount_usd, unit="USD", provenance=source)
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
        workflow: str | None = None,
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
        definition = None
        project_config = getattr(self, "project_config", None)
        if project_config is not None:
            workflow_id = workflow or project_config.default_workflow
            if workflow_id is None and len(project_config.workflow_definitions) == 1:
                workflow_id = next(iter(project_config.workflow_definitions))
            if workflow_id is not None:
                definition = project_config.workflow_definitions.get(workflow_id)
                if definition is None:
                    raise ValueError(f"witdem_sdk: unknown workflow {workflow_id!r}")
        elif workflow is not None:
            raise ValueError("witdem_sdk: workflow= requires a project configuration")
        workflow_id = definition.id if definition is not None else None
        context = baggage.set_baggage(_EXECUTION_ID_KEY, resolved_id)
        if workflow_id:
            context = baggage.set_baggage(_WORKFLOW_ID_KEY, workflow_id, context=context)
        token = otel_context.attach(context)
        try:
            with self._tracer.start_as_current_span(resolved_name, kind=SpanKind.INTERNAL) as span:
                span.set_attribute(_EXECUTION_ID_KEY, resolved_id)
                span.set_attribute("witdem.execution.name", resolved_name)
                span.set_attribute("witdem.runtime.kind", "workflow")
                span.set_attribute("witdem.runtime.name", self.runtime)
                span.set_attribute("witdem.runtime", self.runtime)
                if definition is not None:
                    span.set_attribute(_WORKFLOW_ID_KEY, definition.id)
                    span.set_attribute("witdem.workflow.template_hash", definition.template_hash)
                _set_attributes(span, attributes)
                from witdem_sdk import event, outcome

                if definition is not None:
                    event(
                        "workflow.definition",
                        {
                            "workflow_id": definition.id,
                            "template_hash": definition.template_hash,
                            "definition": definition.model_dump(mode="json"),
                        },
                    )
                event(
                    "execution.started",
                    {
                        "service": self.service_name,
                        **(
                            {"workflow_id": definition.id, "template_hash": definition.template_hash}
                            if definition is not None
                            else {}
                        ),
                    },
                )
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
            span.set_attribute("witdem.operation.type", "text_generation")
            span.set_attribute("witdem.operation.interface", "model_api")
            span.set_attribute("witdem.operation.role", "application")
            span.set_attribute("witdem.operation.input_modalities", ["text"])
            span.set_attribute("witdem.operation.output_modalities", ["text"])
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
            span.set_attribute("witdem.operation.type", "tool")
            span.set_attribute("witdem.operation.interface", "tool")
            span.set_attribute("witdem.operation.role", "application")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("gen_ai.cost.usd", cost_usd)
            if call_id:
                span.set_attribute("gen_ai.tool.call.id", call_id)
            _set_attributes(span, attributes)
            operation = Operation(span)
            operation.measure("tool.calls", 1, unit="call", provenance="runtime_reported")
            operation.cost(cost_usd, source="application_reported")
            yield operation

    def operation(
        self,
        name: str | None = None,
        *,
        kind: str = "component",
        type: str | None = None,
        family: str | None = None,
        operation_type: str | None = None,
        subtype: str | None = None,
        interface: str = "unknown",
        role: str = "application",
        input_modalities: list[str] | tuple[str, ...] = (),
        output_modalities: list[str] | tuple[str, ...] = (),
        provider: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        model_id: str | None = None,
        implementation: str | None = None,
        implementation_id: str | None = None,
        framework: str | None = None,
        framework_id: str | None = None,
        execution_source: str | None = None,
        gateway: str | None = None,
        vendor: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Any:
        """Create a canonical operation context, or decorate a callable.

        ``type``/``provider``/``model`` remain compatibility aliases for the
        explicit ``operation_type``/``provider_id``/``model_id`` contract.
        """

        resolved_type = operation_type or type
        resolved_provider = provider_id or provider
        resolved_model = model_id or model
        resolved_implementation = implementation_id or implementation
        resolved_framework = framework_id or framework

        @contextmanager
        def operation_context(resolved_name: str) -> Iterator[Operation]:
            with self._tracer.start_as_current_span(resolved_name, kind=SpanKind.INTERNAL) as span:
                span.set_attribute("witdem.runtime.kind", kind)
                if family:
                    span.set_attribute("witdem.operation.family", family)
                if resolved_type:
                    span.set_attribute("witdem.operation.type", resolved_type)
                if subtype:
                    span.set_attribute("witdem.operation.subtype", subtype)
                span.set_attribute("witdem.operation.interface", interface)
                span.set_attribute("witdem.operation.role", role)
                if input_modalities:
                    span.set_attribute("witdem.operation.input_modalities", list(input_modalities))
                if output_modalities:
                    span.set_attribute("witdem.operation.output_modalities", list(output_modalities))
                if resolved_provider:
                    span.set_attribute("gen_ai.provider.name", resolved_provider)
                if resolved_model:
                    span.set_attribute("gen_ai.request.model", resolved_model)
                if resolved_implementation:
                    span.set_attribute("witdem.implementation.id", resolved_implementation)
                if resolved_framework:
                    span.set_attribute("witdem.framework.id", resolved_framework)
                if execution_source:
                    span.set_attribute("witdem.execution.source", execution_source)
                if gateway:
                    span.set_attribute("witdem.gateway.id", gateway)
                if vendor:
                    span.set_attribute("witdem.vendor.id", vendor)
                _set_attributes(span, attributes)
                yield Operation(span)

        if name is not None:
            return operation_context(name)

        def decorator(function: _CallableT) -> _CallableT:
            if inspect.iscoroutinefunction(function):
                @wraps(function)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    with operation_context(function.__name__):
                        return await function(*args, **kwargs)

                return cast(_CallableT, async_wrapper)

            @wraps(function)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with operation_context(function.__name__):
                    return function(*args, **kwargs)

            return cast(_CallableT, wrapper)

        return decorator

    @contextmanager
    def evaluation_campaign(
        self,
        campaign_id: str,
        *,
        suite_id: str,
        dataset_id: str,
        dataset_version: str,
        candidate_version: str,
        baseline_version: str | None = None,
    ) -> Iterator[str]:
        """Attach framework-neutral offline-campaign identity to evaluations."""

        attributes = {
            _EVALUATION_CAMPAIGN_KEY: campaign_id,
            "witdem.evaluation.suite_id": suite_id,
            "witdem.evaluation.dataset_id": dataset_id,
            "witdem.evaluation.dataset_version": dataset_version,
            "witdem.evaluation.candidate_version": candidate_version,
            "witdem.evaluation.baseline_version": baseline_version,
        }
        context = otel_context.get_current()
        for key, value in attributes.items():
            if value is not None:
                context = baggage.set_baggage(key, value, context=context)
        token = otel_context.attach(context)
        try:
            yield campaign_id
        finally:
            otel_context.detach(token)

    @contextmanager
    def evaluation_case(self, case_id: str) -> Iterator[str]:
        """Attach a dataset-case identity inside an evaluation campaign."""

        context = baggage.set_baggage(_EVALUATION_CASE_KEY, case_id)
        token = otel_context.attach(context)
        try:
            yield case_id
        finally:
            otel_context.detach(token)

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

        campaign_attributes = {
            key: baggage.get_baggage(key)
            for key in (
                _EVALUATION_CAMPAIGN_KEY,
                _EVALUATION_CASE_KEY,
                "witdem.evaluation.suite_id",
                "witdem.evaluation.dataset_id",
                "witdem.evaluation.dataset_version",
                "witdem.evaluation.candidate_version",
                "witdem.evaluation.baseline_version",
            )
            if baggage.get_baggage(key) is not None
        }
        evaluation(
            name,
            score=score,
            label=label,
            value=value,
            attributes={**campaign_attributes, **dict(attributes or {})},
            execution_id=execution_id,
        )

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
        requirements: Mapping[str, bool | None],
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
        if spec.result.values and result not in spec.result.values:
            allowed = ", ".join(spec.result.values)
            raise ValueError(f"witdem_sdk: result {result!r} is not declared; expected one of: {allowed}")
        if spec.decision is None and (decision is not None or expected_decision is not None):
            raise ValueError(f"witdem_sdk: contract {contract_name!r} does not declare a decision")
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
                f"witdem_sdk: expected_decision {expected_decision!r} is not declared; expected one of: {allowed}"
            )
        undeclared_dimensions = sorted(set(dimensions or {}) - set(spec.dimensions))
        if undeclared_dimensions:
            declared = ", ".join(spec.dimensions) or "none"
            unknown = ", ".join(undeclared_dimensions)
            raise ValueError(
                f"witdem_sdk: dimensions not declared in contract {contract_name!r}: {unknown}; "
                f"declared dimensions: {declared}"
            )
        undeclared_evaluations = sorted(set(evaluations or {}) - set(spec.evaluations))
        if undeclared_evaluations:
            raise ValueError(
                f"witdem_sdk: evaluations not declared in contract {contract_name!r}: "
                + ", ".join(undeclared_evaluations)
            )
        undeclared_metrics = sorted(set(metrics or {}) - set(spec.metrics))
        if undeclared_metrics:
            raise ValueError(
                f"witdem_sdk: metrics not declared in contract {contract_name!r}: "
                + ", ".join(undeclared_metrics)
            )
        declared_requirements = set(spec.product_goal.requirements)
        reported_requirements = set(requirements)
        missing_requirements = sorted(declared_requirements - reported_requirements)
        unknown_requirements = sorted(reported_requirements - declared_requirements)
        if missing_requirements or unknown_requirements:
            details: list[str] = []
            if missing_requirements:
                details.append(f"missing: {', '.join(missing_requirements)}")
            if unknown_requirements:
                details.append(f"undeclared: {', '.join(unknown_requirements)}")
            raise ValueError(f"witdem_sdk: goal requirements do not match the contract ({'; '.join(details)})")
        invalid_requirements = [
            key for key, value in requirements.items() if value is not True and value is not False and value is not None
        ]
        if invalid_requirements:
            raise ValueError(
                "witdem_sdk: goal requirement values must be true, false, or null: "
                + ", ".join(sorted(invalid_requirements))
            )
        failed_requirements = [key for key, value in requirements.items() if value is False]
        unknown_goal_requirements = [key for key, value in requirements.items() if value is None]
        product_goal_achieved = all(value is True for value in requirements.values())
        evidence_sufficient = evidence_sufficient and not unknown_goal_requirements
        closest_blocker = next(
            (key for key in spec.product_goal.requirements if requirements[key] is not True),
            "none",
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
                    "contract_version": "2.0",
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
            "contract_version": "2.0",
            "expected_status": expected_decision,
            "observed_status": decision,
            "decision_correct": decision_correct,
            "product_goal_achieved": product_goal_achieved,
            "artifact_valid": result_valid,
            "decision_evidence_sufficient": evidence_sufficient,
            "required_path_observed": required_path_observed,
            "closest_blocker": closest_blocker,
            "failed_requirement_ids": failed_requirements,
            "unknown_requirement_ids": unknown_goal_requirements,
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
        for key, value in requirements.items():
            requirement = spec.product_goal.requirements[key]
            self.evaluation(
                requirement.name,
                value=value,
                label="passed" if value is True else "failed" if value is False else "unknown",
                attributes={
                    **shared,
                    "passed": value if isinstance(value, bool) else None,
                    "evaluation_key": f"goal_requirement.{key}",
                    "evaluation_description": requirement.description,
                    "requirement_id": key,
                    "requirement_failure_label": requirement.failure.label,
                    "requirement_failure_description": requirement.failure.description,
                    "investigation_stage": (
                        requirement.failure.investigate.stage if requirement.failure.investigate else None
                    ),
                    "investigation_node": (
                        requirement.failure.investigate.node if requirement.failure.investigate else None
                    ),
                },
                execution_id=execution_id,
            )
        for key, value in (evaluations or {}).items():
            definition_spec = spec.evaluations.get(key)
            assert definition_spec is not None
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
            assert metric_definition is not None
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
                    "outcome_description": (spec.decision.values.get(str(decision)) if spec.decision else None),
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
        """Reject the removed v1 expression-contract completion path."""

        raise ValueError(
            "witdem_sdk: Witdem.complete(...) was removed with configuration v1; "
            "report named contract facts with Witdem.report(...)"
        )

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
    resolved_runtime = runtime
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
