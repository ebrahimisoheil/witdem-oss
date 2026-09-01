"""Explicit Anthropic client instrumentation."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from witdem_sdk.integrations._common import ResultReporter, settings


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    values: dict[str, int] = {}
    for target, names in {
        "input_tokens": ("input_tokens",),
        "output_tokens": ("output_tokens",),
        "cache_read_tokens": ("cache_read_input_tokens",),
        "cache_creation_tokens": ("cache_creation_input_tokens",),
    }.items():
        for name in names:
            value = getattr(usage, name, None) if usage is not None else None
            if isinstance(value, int):
                values[target] = value
                break
    if "input_tokens" in values and "output_tokens" in values:
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    return values


def _model(response: Any) -> str | None:
    value = getattr(response, "model", None)
    return str(value) if value else None


def _record_tool_use_ids(operation: Any, response: Any) -> None:
    ids = [
        str(block.id)
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", None) == "tool_use" and getattr(block, "id", None)
    ]
    if not ids:
        return
    operation.span.set_attribute("gen_ai.tool.call.id", ids[0])
    operation.span.set_attribute("witdem.anthropic.tool_use.ids", ids)
    operation.span.set_attribute("witdem.anthropic.tool_use.count", len(ids))


class _MessagesProxy:
    def __init__(self, messages: Any, witdem: Any, capture_content: bool) -> None:
        self._messages = messages
        self._witdem = witdem
        self._capture_content = capture_content

    def __getattr__(self, name: str) -> Any:
        if name != "create":
            return getattr(self._messages, name)

        original = self._messages.create

        def create(*args: Any, **kwargs: Any) -> Any:
            model_name = str(kwargs.get("model", "anthropic.model"))
            with self._witdem.model(
                "anthropic.messages.create",
                provider="anthropic",
                model=model_name,
                attributes={"witdem.execution.source": "anthropic_sdk", "witdem.client.library": "anthropic"},
            ) as operation:
                try:
                    response = original(*args, **kwargs)
                    operation.response_model(_model(response))
                    _record_tool_use_ids(operation, response)
                    usage = _usage(response)
                    if usage:
                        operation.usage(**usage)
                    if self._capture_content:
                        operation.span.set_attribute("witdem.capture_content", True)
                    return response
                except BaseException as exc:
                    operation.span.record_exception(exc)
                    raise

        async def acreate(*args: Any, **kwargs: Any) -> Any:
            model_name = str(kwargs.get("model", "anthropic.model"))
            with self._witdem.model(
                "anthropic.messages.create",
                provider="anthropic",
                model=model_name,
                attributes={"witdem.execution.source": "anthropic_sdk", "witdem.client.library": "anthropic"},
            ) as operation:
                try:
                    response = await original(*args, **kwargs)
                    operation.response_model(_model(response))
                    _record_tool_use_ids(operation, response)
                    usage = _usage(response)
                    if usage:
                        operation.usage(**usage)
                    if self._capture_content:
                        operation.span.set_attribute("witdem.capture_content", True)
                    return response
                except BaseException as exc:
                    operation.span.record_exception(exc)
                    raise

        return acreate if inspect.iscoroutinefunction(original) else create


class _ClientProxy:
    def __init__(self, client: Any, witdem: Any, capture_content: bool) -> None:
        self._client = client
        self.messages = _MessagesProxy(client.messages, witdem, capture_content)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def instrument_anthropic(client: Any, *, witdem: Any, capture_content: bool = False) -> Any:
    """Return a proxy around a sync or async Anthropic client."""

    if not hasattr(client, "messages"):
        raise TypeError("instrument_anthropic expects an Anthropic client with a messages attribute")
    return _ClientProxy(client, witdem, capture_content)


def instrument(
    function: Callable[..., Any],
    *,
    client: Any,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> Callable[..., Any]:
    """Wrap an Anthropic workload and inject an instrumented client."""

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
                observed_client = instrument_anthropic(client, witdem=witdem, capture_content=capture_content)
                result = await function(observed_client, *args, **kwargs)
                integration_settings.report(result, witdem)
                return result

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            observed_client = instrument_anthropic(client, witdem=witdem, capture_content=capture_content)
            result = function(observed_client, *args, **kwargs)
            integration_settings.report(result, witdem)
            return result

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
