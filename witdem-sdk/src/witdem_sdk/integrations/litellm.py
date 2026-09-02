"""LiteLLM callback instrumentation with no prompt or response capture."""

from __future__ import annotations

import inspect
import json
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import wraps
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from witdem_sdk import configure
from witdem_sdk.integrations._common import ResultReporter, settings

_CALL_SEMANTICS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "chat": ("text_generation", ("text",), ("text",)),
    "completion": ("text_generation", ("text",), ("text",)),
    "text_completion": ("text_generation", ("text",), ("text",)),
    "embedding": ("embedding", ("text",), ("vector",)),
    "embeddings": ("embedding", ("text",), ("vector",)),
    "aembedding": ("embedding", ("text",), ("vector",)),
    "rerank": ("reranking", ("text", "document"), ("document",)),
    "reranking": ("reranking", ("text", "document"), ("document",)),
    "moderation": ("moderation", ("text",), ("structured",)),
    "ocr": ("ocr", ("document",), ("text",)),
}


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    observed = getattr(value, name, None)
    if observed is not None:
        return observed
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, Mapping):
            return dumped.get(name, default)
    return default


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _nanoseconds(value: Any) -> int | None:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value * 1_000_000_000)
    return None


def _call_id(kwargs: Mapping[str, Any]) -> str:
    return str(kwargs.get("litellm_call_id") or kwargs.get("litellm_trace_id") or id(kwargs))


def _provider(model: str, kwargs: Mapping[str, Any]) -> str:
    params = _mapping(kwargs.get("litellm_params"))
    value = kwargs.get("custom_llm_provider") or params.get("custom_llm_provider")
    if value:
        return str(value).casefold()
    prefix, separator, _ = model.partition("/")
    return prefix.casefold() if separator else "unknown"


def _litellm_metadata(kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    params = _mapping(kwargs.get("litellm_params"))
    for candidate in (
        kwargs.get("metadata"),
        params.get("metadata"),
        params.get("litellm_metadata"),
    ):
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _usage(response: Any) -> Mapping[str, Any]:
    return _mapping(_value(response, "usage", {}))


def _usage_info(response: Any) -> Mapping[str, Any]:
    return _mapping(_value(response, "usage_info", {}))


def _router_metadata(response: Any) -> Mapping[str, Any]:
    response_mapping = _mapping(response)
    direct = response_mapping.get("openrouter_metadata")
    if isinstance(direct, Mapping):
        return direct
    provider_fields = response_mapping.get("provider_specific_fields")
    if isinstance(provider_fields, Mapping):
        nested = provider_fields.get("openrouter_metadata")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _selected_route(metadata: Mapping[str, Any]) -> str | None:
    endpoints = metadata.get("endpoints")
    available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
    if isinstance(available, list):
        for endpoint in available:
            if isinstance(endpoint, Mapping) and endpoint.get("selected") and endpoint.get("provider"):
                return str(endpoint["provider"]).casefold().replace(" ", "_")
    return None


def _set_response_attributes(span: Span, kwargs: Mapping[str, Any], response: Any) -> None:
    response_model = _value(response, "model")
    if response_model:
        span.set_attribute("gen_ai.response.model", str(response_model))
    usage = _usage(response)
    aliases = {
        "gen_ai.usage.input_tokens": ("prompt_tokens", "input_tokens"),
        "gen_ai.usage.output_tokens": ("completion_tokens", "output_tokens"),
        "gen_ai.usage.total_tokens": ("total_tokens",),
    }
    for target, names in aliases.items():
        observed = next((usage.get(name) for name in names if usage.get(name) is not None), None)
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            span.set_attribute(target, observed)
    usage_info = _usage_info(response)
    response_data = _value(response, "data")
    if isinstance(response_data, (list, tuple)):
        span.set_attribute("gen_ai.usage.output_vectors", len(response_data))
        span.set_attribute("gen_ai.usage.input_items", len(response_data))
        if response_data:
            first_vector = _value(response_data[0], "embedding")
            if isinstance(first_vector, (list, tuple)):
                span.set_attribute("gen_ai.usage.vector_dimensions", len(first_vector))
    pages_processed = usage_info.get("pages_processed")
    if isinstance(pages_processed, (int, float)) and not isinstance(pages_processed, bool):
        span.set_attribute("witdem.operation.type", "ocr")
        span.set_attribute("witdem.operation.interface", "model_api")
        span.set_attribute("witdem.operation.input_modalities", ["document"])
        span.set_attribute("witdem.operation.output_modalities", ["text"])
        span.set_attribute("gen_ai.usage.ocr_pages", pages_processed)
        span.set_attribute(
            "witdem.measurements",
            json.dumps(
                [
                    {
                        "key": "pages.processed",
                        "value": pages_processed,
                        "unit": "page",
                        "aggregation": "sum",
                        "scope": "operation",
                        "provenance": "provider_reported",
                    }
                ],
                separators=(",", ":"),
            ),
        )
    document_bytes = usage_info.get("doc_size_bytes")
    if isinstance(document_bytes, (int, float)) and not isinstance(document_bytes, bool):
        span.set_attribute("gen_ai.usage.input_bytes", document_bytes)
    prompt_details = _mapping(usage.get("prompt_tokens_details"))
    completion_details = _mapping(usage.get("completion_tokens_details"))
    for key, observed in {
        "gen_ai.usage.cache_read.input_tokens": prompt_details.get("cached_tokens"),
        "gen_ai.usage.cache_creation.input_tokens": prompt_details.get("cache_write_tokens"),
        "gen_ai.usage.reasoning.output_tokens": completion_details.get("reasoning_tokens"),
        "gen_ai.usage.audio.input_tokens": prompt_details.get("audio_tokens"),
        "gen_ai.usage.audio.output_tokens": completion_details.get("audio_tokens"),
    }.items():
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            span.set_attribute(key, observed)

    metadata = _router_metadata(response)
    selected_provider = _selected_route(metadata)
    initial_provider = _provider(str(kwargs.get("model") or ""), kwargs)
    if initial_provider == "openrouter":
        span.set_attribute("witdem.gateway.id", "openrouter")
    if selected_provider:
        span.set_attribute("witdem.route.provider", selected_provider)
        span.set_attribute("gen_ai.provider.name", selected_provider)
    for source, target in {
        "requested": "witdem.route.requested_model",
        "strategy": "witdem.route.strategy",
        "region": "witdem.route.region",
        "attempt": "witdem.route.attempt",
        "is_byok": "witdem.route.is_byok",
    }.items():
        observed = metadata.get(source)
        if observed is not None:
            span.set_attribute(target, observed)
    attempts = metadata.get("attempts")
    if isinstance(attempts, list):
        span.set_attribute("witdem.route.attempt_count", len(attempts))
        span.set_attribute("witdem.route.attempts", json.dumps(attempts, default=str, separators=(",", ":")))

    cost = kwargs.get("response_cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        span.set_attribute("gen_ai.cost.usd", float(cost))
        source = "openrouter_reported" if initial_provider == "openrouter" else "litellm_reported"
        span.set_attribute("gen_ai.cost.source", source)
    cost_details = _mapping(usage.get("cost_details"))
    upstream_cost = cost_details.get("upstream_inference_cost")
    if isinstance(upstream_cost, (int, float)) and not isinstance(upstream_cost, bool):
        span.set_attribute("witdem.route.upstream_cost_usd", float(upstream_cost))


class WitdemLiteLLMCallback:
    """LiteLLM custom logger that emits one correlated GenAI span per attempt."""

    def __init__(self) -> None:
        self._spans: dict[str, list[Span]] = {}
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._tracer = trace.get_tracer("witdem_sdk.integrations.litellm")

    def log_pre_api_call(self, model: str, messages: Any, kwargs: Mapping[str, Any]) -> None:
        del messages
        provider = _provider(model, kwargs)
        params = _mapping(kwargs.get("litellm_params"))
        call_type = str(kwargs.get("call_type") or params.get("call_type") or "chat").casefold()
        span = self._tracer.start_span(
            f"litellm.{call_type}",
            kind=SpanKind.CLIENT,
            start_time=_nanoseconds(kwargs.get("start_time")),
        )
        explicit_type = kwargs.get("witdem.operation.type") or _litellm_metadata(kwargs).get("witdem.operation.type")
        semantics = _CALL_SEMANTICS.get(call_type)
        if semantics is not None:
            operation_type, input_modalities, output_modalities = semantics
            span.set_attribute("gen_ai.operation.name", "embeddings" if operation_type == "embedding" else call_type)
            span.set_attribute("witdem.operation.type", str(explicit_type or operation_type))
            span.set_attribute("witdem.operation.input_modalities", input_modalities)
            span.set_attribute("witdem.operation.output_modalities", output_modalities)
        elif explicit_type:
            span.set_attribute("witdem.operation.type", str(explicit_type))
        span.set_attribute("witdem.adapter.operation.type", call_type)
        span.set_attribute("witdem.operation.interface", "model_api")
        span.set_attribute("witdem.operation.role", "application")
        span.set_attribute("gen_ai.provider.name", provider)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("witdem.client.library", "litellm")
        span.set_attribute("witdem.gateway.id", "litellm")
        span.set_attribute("witdem.capture_content", False)
        trace_id = kwargs.get("litellm_trace_id") or _litellm_metadata(kwargs).get("litellm_trace_id")
        if trace_id:
            span.set_attribute("witdem.route.id", str(trace_id))
        previous = _litellm_metadata(kwargs).get("previous_models")
        if isinstance(previous, list):
            span.set_attribute("witdem.retry.attempt", len(previous) + 1)
            span.set_attribute("witdem.route.failed_attempt_count", len(previous))
            previous_names = [
                str(item.get("model") or item.get("model_name"))
                for item in previous
                if isinstance(item, Mapping) and (item.get("model") or item.get("model_name"))
            ]
            if previous_names:
                span.set_attribute("witdem.route.previous_models", previous_names)
        with self._lock:
            self._spans.setdefault(_call_id(kwargs), []).append(span)

    def _finish(self, kwargs: Mapping[str, Any], response: Any, end_time: Any, error: bool) -> None:
        key = _call_id(kwargs)
        with self._lock:
            spans = self._spans.get(key, [])
            span = spans[-1] if spans else None
        if span is None:
            return
        try:
            if error:
                span.set_status(Status(StatusCode.ERROR, str(response)))
                if isinstance(response, BaseException):
                    span.record_exception(response)
            else:
                _set_response_attributes(span, kwargs, response)
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:  # telemetry enrichment must never break a LiteLLM call
            span.set_attribute("witdem.telemetry.enrichment_error", type(exc).__name__)
        finally:
            span.end(end_time=_nanoseconds(end_time))
            with self._idle:
                spans = self._spans.get(key, [])
                if span in spans:
                    spans.remove(span)
                if not spans:
                    self._spans.pop(key, None)
                self._idle.notify_all()

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        """Wait briefly for LiteLLM's non-blocking success/failure logger."""

        deadline = time.monotonic() + timeout
        with self._idle:
            while self._spans:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
        return True

    def log_success_event(self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any) -> None:
        del start_time
        self._finish(kwargs, response_obj, end_time, False)

    def log_failure_event(self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any) -> None:
        del start_time
        self._finish(kwargs, response_obj, end_time, True)

    async def async_log_pre_api_call(self, model: str, messages: Any, kwargs: Mapping[str, Any]) -> None:
        self.log_pre_api_call(model, messages, kwargs)

    async def async_log_success_event(
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self, kwargs: Mapping[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


_REGISTRATION_LOCK = threading.Lock()
_REGISTRATION: _Registration | None = None


class _Registration:
    def __init__(self, module: Any, callback: Any) -> None:
        self._module = module
        self.callback = callback
        self._references = 1

    def retain(self) -> _Registration:
        self._references += 1
        return self

    def uninstall(self) -> None:
        global _REGISTRATION
        with _REGISTRATION_LOCK:
            self._references -= 1
            if self._references > 0:
                return
            callbacks = list(getattr(self._module, "callbacks", []) or [])
            self._module.callbacks = [callback for callback in callbacks if callback is not self.callback]
            if _REGISTRATION is self:
                _REGISTRATION = None

    def flush(self, timeout: float = 2.0) -> bool:
        return bool(self.callback.wait_for_idle(timeout))


def install_litellm() -> _Registration:
    """Append a Witdem callback without replacing existing LiteLLM callbacks."""

    global _REGISTRATION
    with _REGISTRATION_LOCK:
        if _REGISTRATION is not None:
            return _REGISTRATION.retain()
        try:
            import litellm  # type: ignore[import-not-found,unused-ignore]
            from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found,unused-ignore]
        except ImportError as exc:
            raise ImportError("LiteLLM instrumentation requires 'witdem-sdk[litellm]'") from exc

        class RegisteredCallback(WitdemLiteLLMCallback, CustomLogger):  # type: ignore[misc,unused-ignore]
            def __init__(self) -> None:
                CustomLogger.__init__(self, turn_off_message_logging=True)
                WitdemLiteLLMCallback.__init__(self)

        callback = RegisteredCallback()
        litellm.callbacks = [*(getattr(litellm, "callbacks", []) or []), callback]
        _REGISTRATION = _Registration(litellm, callback)
        return _REGISTRATION


class LiteLLMInstrumentation:
    """Persistent standalone LiteLLM setup returned by ``instrument()``."""

    def __init__(self, witdem: Any, registration: _Registration) -> None:
        self.witdem = witdem
        self.registration = registration

    def shutdown(self) -> None:
        self.registration.flush()
        self.registration.uninstall()
        self.witdem.shutdown()

    def __enter__(self) -> LiteLLMInstrumentation:
        return self

    def __exit__(self, *args: Any) -> None:
        self.shutdown()


def instrument(
    function: Callable[..., Any] | None = None,
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> Callable[..., Any] | LiteLLMInstrumentation:
    """Instrument embedded LiteLLM globally or wrap one application workload."""

    if function is None:
        witdem = configure(service_name, endpoint=endpoint, config_path=config_path)
        return LiteLLMInstrumentation(witdem, install_litellm())
    if getattr(function, "__witdem_instrumented__", False):
        return function
    integration_settings = settings(
        service_name=service_name,
        execution_name=execution_name,
        endpoint=endpoint,
        config_path=config_path,
        attributes=attributes,
        report_result=report_result,
    )

    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with integration_settings.invocation() as witdem:
                registration = install_litellm()
                try:
                    result = await function(*args, **kwargs)
                    integration_settings.report(result, witdem)
                    return result
                finally:
                    registration.flush()
                    registration.uninstall()

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            registration = install_litellm()
            try:
                result = function(*args, **kwargs)
                integration_settings.report(result, witdem)
                return result
            finally:
                registration.flush()
                registration.uninstall()

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
