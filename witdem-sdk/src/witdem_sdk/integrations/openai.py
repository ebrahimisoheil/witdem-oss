"""Explicit instrumentation for the direct OpenAI Python SDK."""

from __future__ import annotations

import inspect
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
        dumped = dump()
        return dumped if isinstance(dumped, Mapping) else {}
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return attributes
    return {}


def _payload(response: Any) -> Any:
    nested = _value(response, "response")
    return nested if nested is not None else response


def observe_response(response: Any) -> Mapping[str, Any]:
    """Extract content-free identity, usage, and tool-call facts."""

    payload = _payload(response)
    usage = _mapping(_value(payload, "usage", {}))
    input_details = _mapping(usage.get("input_tokens_details") or usage.get("prompt_tokens_details"))
    output_details = _mapping(usage.get("output_tokens_details") or usage.get("completion_tokens_details"))
    data = _value(payload, "data", [])
    vectors = data if isinstance(data, (list, tuple)) else []
    first_vector = _value(vectors[0], "embedding", []) if vectors else []
    return {
        "response_model": _value(payload, "model"),
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": input_details.get("cached_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "audio_input_tokens": input_details.get("audio_tokens"),
        "audio_output_tokens": output_details.get("audio_tokens"),
        "output_vectors": len(vectors) if vectors else None,
        "vector_dimensions": len(first_vector) if isinstance(first_vector, (list, tuple)) else None,
        "tool_call_ids": _tool_call_ids(payload),
    }


def _tool_call_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    for item in _value(payload, "output", []) or []:
        item_type = _value(item, "type")
        item_id = _value(item, "call_id") or _value(item, "id")
        if item_type in {"function_call", "tool_call"} and item_id:
            ids.append(str(item_id))
    for choice in _value(payload, "choices", []) or []:
        message = _value(choice, "message")
        for call in _value(message, "tool_calls", []) or []:
            item_id = _value(call, "id")
            if item_id:
                ids.append(str(item_id))
    return list(dict.fromkeys(ids))


def _record(operation: Any, response: Any, *, embedding: bool) -> None:
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
            "reasoning_tokens",
            "audio_input_tokens",
            "audio_output_tokens",
        )
        if isinstance(observed.get(name), int)
    }
    meters: dict[str, int] = {}
    if embedding:
        if isinstance(observed.get("output_vectors"), int):
            meters["vectors.output"] = observed["output_vectors"]
        if isinstance(observed.get("vector_dimensions"), int):
            meters["vector.dimensions"] = observed["vector_dimensions"]
        if meters:
            usage["meters"] = meters
    if usage:
        operation.usage(**usage)
    tool_ids = observed.get("tool_call_ids")
    if isinstance(tool_ids, list) and tool_ids:
        operation.span.set_attribute("gen_ai.tool.call.id", tool_ids[0])
        operation.span.set_attribute("witdem.openai.tool_call.ids", tool_ids)
        operation.span.set_attribute("witdem.openai.tool_call.count", len(tool_ids))


def _context(witdem: Any, operation_name: str, model: str, *, embedding: bool) -> Any:
    attributes = {
        "witdem.execution.source": "openai_sdk",
        "witdem.client.library": "openai",
        "witdem.capture_content": False,
    }
    if embedding:
        return witdem.operation(
            operation_name,
            family="inference",
            operation_type="embedding",
            subtype="embeddings",
            interface="model_api",
            provider_id="openai",
            model_id=model,
            input_modalities=("text",),
            output_modalities=("vector",),
            execution_source="openai_sdk",
            attributes={"witdem.client.library": "openai", "gen_ai.operation.name": "embeddings"},
        )
    return witdem.model(operation_name, provider="openai", model=model, attributes=attributes)


class _ObservedStream:
    def __init__(self, stream: Any, operation: Any, context: Any, *, embedding: bool) -> None:
        self._stream = stream
        self._iterator = iter(stream)
        self._operation = operation
        self._context = context
        self._embedding = embedding
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
            _record(self._operation, self._last, embedding=self._embedding)
        close = getattr(self._stream, "close", None)
        try:
            if callable(close):
                close()
        finally:
            self._context.__exit__(exc_type, exc, traceback)


class _ObservedAsyncStream:
    def __init__(self, stream: Any, operation: Any, context: Any, *, embedding: bool) -> None:
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._operation = operation
        self._context = context
        self._embedding = embedding
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
            _record(self._operation, self._last, embedding=self._embedding)
        close = getattr(self._stream, "aclose", None)
        try:
            if callable(close):
                await close()
        finally:
            self._context.__exit__(exc_type, exc, traceback)


class _CreateProxy:
    def __init__(self, create: Any, witdem: Any, operation_name: str, *, embedding: bool = False) -> None:
        self._create = create
        self._witdem = witdem
        self._operation_name = operation_name
        self._embedding = embedding

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "openai.model")
        context = _context(self._witdem, self._operation_name, model, embedding=self._embedding)
        operation = context.__enter__()
        try:
            response = self._create(*args, **kwargs)
            if kwargs.get("stream"):
                return _ObservedStream(response, operation, context, embedding=self._embedding)
            _record(operation, response, embedding=self._embedding)
            context.__exit__(None, None, None)
            return response
        except BaseException as exc:
            operation.span.record_exception(exc)
            context.__exit__(type(exc), exc, exc.__traceback__)
            raise


class _AsyncCreateProxy(_CreateProxy):
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model") or "openai.model")
        context = _context(self._witdem, self._operation_name, model, embedding=self._embedding)
        operation = context.__enter__()
        try:
            response = await self._create(*args, **kwargs)
            if kwargs.get("stream"):
                return _ObservedAsyncStream(response, operation, context, embedding=self._embedding)
            _record(operation, response, embedding=self._embedding)
            context.__exit__(None, None, None)
            return response
        except BaseException as exc:
            operation.span.record_exception(exc)
            context.__exit__(type(exc), exc, exc.__traceback__)
            raise


class _EndpointProxy:
    def __init__(self, endpoint: Any, witdem: Any, operation_name: str, *, embedding: bool = False) -> None:
        self._endpoint = endpoint
        proxy = _AsyncCreateProxy if inspect.iscoroutinefunction(endpoint.create) else _CreateProxy
        self.create = proxy(endpoint.create, witdem, operation_name, embedding=embedding)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._endpoint, name)


class _ChatProxy:
    def __init__(self, chat: Any, witdem: Any) -> None:
        self._chat = chat
        self.completions = _EndpointProxy(chat.completions, witdem, "openai.chat.completions")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._chat, name)


class _ClientProxy:
    def __init__(self, client: Any, witdem: Any) -> None:
        self._client = client
        if hasattr(client, "responses"):
            self.responses = _EndpointProxy(client.responses, witdem, "openai.responses")
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            self.chat = _ChatProxy(client.chat, witdem)
        if hasattr(client, "embeddings"):
            self.embeddings = _EndpointProxy(client.embeddings, witdem, "openai.embeddings", embedding=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def instrument_openai(client: Any, *, witdem: Any) -> Any:
    """Return a content-safe proxy around a sync or async OpenAI client."""

    supported = (
        hasattr(client, "responses")
        or (hasattr(client, "chat") and hasattr(client.chat, "completions"))
        or hasattr(client, "embeddings")
    )
    if not supported:
        raise TypeError("OpenAI instrumentation expects responses, chat.completions, or embeddings")
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
    """Wrap an OpenAI workload and inject one instrumented direct SDK client."""

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
                result = await function(instrument_openai(client, witdem=witdem), *args, **kwargs)
                integration_settings.report(result, witdem)
                return result

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            result = function(instrument_openai(client, witdem=witdem), *args, **kwargs)
            integration_settings.report(result, witdem)
            return result

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
