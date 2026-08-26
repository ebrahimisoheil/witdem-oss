"""OpenRouter's OpenAI-compatible client with authoritative route and cost enrichment."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from witdem_sdk.integrations._common import ResultReporter, settings


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
        observed = dump()
        return observed if isinstance(observed, Mapping) else {}
    return {}


def _metadata(response: Any) -> Mapping[str, Any]:
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


def _selected_provider(metadata: Mapping[str, Any]) -> str | None:
    endpoints = metadata.get("endpoints")
    available = endpoints.get("available") if isinstance(endpoints, Mapping) else None
    if isinstance(available, list):
        for endpoint in available:
            if isinstance(endpoint, Mapping) and endpoint.get("selected") and endpoint.get("provider"):
                return str(endpoint["provider"]).casefold().replace(" ", "_")
    return None


def observe_response(response: Any) -> Mapping[str, Any]:
    """Return content-free usage, route, and billing facts from an OpenRouter response."""

    nested_response = _value(response, "response")
    payload = nested_response if nested_response is not None else response
    usage = _mapping(_value(payload, "usage", {}))
    prompt_details = _mapping(usage.get("prompt_tokens_details"))
    completion_details = _mapping(usage.get("completion_tokens_details"))
    metadata = _metadata(payload)
    cost_details = _mapping(usage.get("cost_details"))
    return {
        "response_model": _value(payload, "model"),
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": prompt_details.get("cached_tokens"),
        "cache_creation_tokens": prompt_details.get("cache_write_tokens"),
        "reasoning_tokens": completion_details.get("reasoning_tokens"),
        "audio_input_tokens": prompt_details.get("audio_tokens"),
        "audio_output_tokens": completion_details.get("audio_tokens"),
        "cost_usd": usage.get("cost"),
        "upstream_cost_usd": cost_details.get("upstream_inference_cost"),
        "route_provider": _selected_provider(metadata),
        "route_requested_model": metadata.get("requested"),
        "route_strategy": metadata.get("strategy"),
        "route_region": metadata.get("region"),
        "route_attempt": metadata.get("attempt"),
        "route_is_byok": metadata.get("is_byok"),
        "route_attempts": metadata.get("attempts"),
    }


def _record(operation: Any, response: Any) -> None:
    observed = observe_response(response)
    response_model = observed.get("response_model")
    if response_model:
        operation.response_model(str(response_model))
    usage = {
        name: observed[name]
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "reasoning_tokens",
            "audio_input_tokens",
            "audio_output_tokens",
        )
        if isinstance(observed.get(name), (int, float)) and not isinstance(observed.get(name), bool)
    }
    if usage:
        operation.usage(**usage)
    cost = observed.get("cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        operation.cost(float(cost), source="openrouter_reported")
    attrs = {
        "witdem.gateway.name": "openrouter",
        "witdem.route.provider": observed.get("route_provider"),
        "witdem.route.requested_model": observed.get("route_requested_model"),
        "witdem.route.strategy": observed.get("route_strategy"),
        "witdem.route.region": observed.get("route_region"),
        "witdem.route.attempt": observed.get("route_attempt"),
        "witdem.route.is_byok": observed.get("route_is_byok"),
        "witdem.route.upstream_cost_usd": observed.get("upstream_cost_usd"),
    }
    for key, value in attrs.items():
        if value is not None:
            operation.span.set_attribute(key, value)
    provider = observed.get("route_provider")
    if provider:
        operation.span.set_attribute("gen_ai.provider.name", str(provider))
    attempts = observed.get("route_attempts")
    if isinstance(attempts, list):
        operation.span.set_attribute("witdem.route.attempt_count", len(attempts))
        operation.span.set_attribute("witdem.route.attempts", json.dumps(attempts, default=str, separators=(",", ":")))


def _headers(kwargs: dict[str, Any]) -> None:
    current = kwargs.get("extra_headers")
    resolved = dict(current) if isinstance(current, Mapping) else {}
    resolved.setdefault("X-OpenRouter-Metadata", "enabled")
    kwargs["extra_headers"] = resolved


class _ObservedStream:
    def __init__(self, stream: Any, operation: Any, context: Any) -> None:
        self._stream = stream
        self._iterator = iter(stream)
        self._operation = operation
        self._context = context
        self._last: Any = None
        self._closed = False

    def __iter__(self) -> _ObservedStream:
        return self

    def __next__(self) -> Any:
        try:
            self._last = next(self._iterator)
            return self._last
        except StopIteration:
            self.close()
            raise
        except BaseException as exc:
            self._operation.span.record_exception(exc)
            self.close(type(exc), exc, exc.__traceback__)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def close(self, exc_type: Any = None, exc: Any = None, traceback: Any = None) -> None:
        if self._closed:
            return
        self._closed = True
        if self._last is not None:
            _record(self._operation, self._last)
        close = getattr(self._stream, "close", None)
        try:
            if callable(close):
                close()
        finally:
            self._context.__exit__(exc_type, exc, traceback)


class _ObservedAsyncStream:
    def __init__(self, stream: Any, operation: Any, context: Any) -> None:
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._operation = operation
        self._context = context
        self._last: Any = None
        self._closed = False

    def __aiter__(self) -> _ObservedAsyncStream:
        return self

    async def __anext__(self) -> Any:
        try:
            self._last = await self._iterator.__anext__()
            return self._last
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException as exc:
            self._operation.span.record_exception(exc)
            await self.aclose(type(exc), exc, exc.__traceback__)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    async def aclose(self, exc_type: Any = None, exc: Any = None, traceback: Any = None) -> None:
        if self._closed:
            return
        self._closed = True
        if self._last is not None:
            _record(self._operation, self._last)
        close = getattr(self._stream, "aclose", None)
        try:
            if callable(close):
                await close()
        finally:
            self._context.__exit__(exc_type, exc, traceback)


class _CreateProxy:
    def __init__(self, create: Any, witdem: Any, operation_name: str) -> None:
        self._create = create
        self._witdem = witdem
        self._operation_name = operation_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _headers(kwargs)
        model = str(kwargs.get("model") or "openrouter.model")
        context = self._witdem.model(
            self._operation_name,
            provider="openrouter",
            model=model,
            attributes={"witdem.gateway.name": "openrouter", "witdem.capture_content": False},
        )
        operation = context.__enter__()
        try:
            response = self._create(*args, **kwargs)
            if kwargs.get("stream"):
                return _ObservedStream(response, operation, context)
            _record(operation, response)
            context.__exit__(None, None, None)
            return response
        except BaseException as exc:
            operation.span.record_exception(exc)
            context.__exit__(type(exc), exc, exc.__traceback__)
            raise


class _AsyncCreateProxy(_CreateProxy):
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        _headers(kwargs)
        model = str(kwargs.get("model") or "openrouter.model")
        context = self._witdem.model(
            self._operation_name,
            provider="openrouter",
            model=model,
            attributes={"witdem.gateway.name": "openrouter", "witdem.capture_content": False},
        )
        operation = context.__enter__()
        try:
            response = await self._create(*args, **kwargs)
            if kwargs.get("stream"):
                return _ObservedAsyncStream(response, operation, context)
            _record(operation, response)
            context.__exit__(None, None, None)
            return response
        except BaseException as exc:
            operation.span.record_exception(exc)
            context.__exit__(type(exc), exc, exc.__traceback__)
            raise


class _EndpointProxy:
    def __init__(self, endpoint: Any, witdem: Any, operation_name: str) -> None:
        self._endpoint = endpoint
        create = endpoint.create
        proxy_type = _AsyncCreateProxy if inspect.iscoroutinefunction(create) else _CreateProxy
        self.create = proxy_type(create, witdem, operation_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._endpoint, name)


class _ChatProxy:
    def __init__(self, chat: Any, witdem: Any) -> None:
        self._chat = chat
        self.completions = _EndpointProxy(chat.completions, witdem, "openrouter.chat.completions")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class _ClientProxy:
    def __init__(self, client: Any, witdem: Any) -> None:
        self._client = client
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            self.chat = _ChatProxy(client.chat, witdem)
        if hasattr(client, "responses"):
            self.responses = _EndpointProxy(client.responses, witdem, "openrouter.responses")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def instrument_openrouter(client: Any, *, witdem: Any) -> Any:
    """Return a content-safe proxy for an OpenAI-compatible OpenRouter client."""

    if not (
        (hasattr(client, "chat") and hasattr(client.chat, "completions"))
        or hasattr(client, "responses")
    ):
        raise TypeError("OpenRouter instrumentation expects an OpenAI-compatible client")
    return _ClientProxy(client, witdem)


def instrument(
    function: Callable[..., Any],
    *,
    client: Any,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> Callable[..., Any]:
    """Wrap one workload and inject an instrumented OpenRouter client."""

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
                result = await function(instrument_openrouter(client, witdem=witdem), *args, **kwargs)
                integration_settings.report(result, witdem)
                return result

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            result = function(instrument_openrouter(client, witdem=witdem), *args, **kwargs)
            integration_settings.report(result, witdem)
            return result

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
