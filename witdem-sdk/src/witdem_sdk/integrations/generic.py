"""High-level instrumentation for provider calls without a native adapter."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from witdem_sdk.integrations._common import ResultReporter, settings

ResultObserver = Callable[[Any], Mapping[str, Any] | None]


def _record_result(operation: Any, result: Any, observer: ResultObserver | None) -> None:
    observed = observer(result) if observer is not None else _default_observation(result)
    if observed is None:
        return
    values = dict(observed)
    response_model = values.get("response_model")
    if response_model is not None:
        operation.response_model(str(response_model))
    usage = {
        name: values[name]
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "reasoning_tokens",
            "audio_input_tokens",
            "audio_output_tokens",
            "image_input_tokens",
            "image_output_tokens",
            "video_input_tokens",
            "video_output_tokens",
            "search_queries",
        )
        if values.get(name) is not None
    }
    meters = values.get("meters")
    if isinstance(meters, Mapping):
        usage["meters"] = dict(meters)
    if usage:
        operation.usage(**usage)
    cost = values.get("cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        operation.cost(float(cost), source=str(values.get("cost_source", "provider_reported")))


def _default_observation(result: Any) -> Mapping[str, Any]:
    """Read conventional provider result fields without application glue code."""

    def value(*names: str) -> Any:
        for name in names:
            candidate = result.get(name) if isinstance(result, Mapping) else getattr(result, name, None)
            if candidate is not None:
                return candidate
        return None

    return {
        "response_model": value("response_model", "model"),
        "input_tokens": value("input_tokens", "prompt_tokens"),
        "output_tokens": value("output_tokens", "completion_tokens"),
        "total_tokens": value("total_tokens"),
        "cache_read_tokens": value("cache_read_tokens"),
        "cache_creation_tokens": value("cache_creation_tokens"),
        "reasoning_tokens": value("reasoning_tokens", "output_reasoning_tokens"),
        "audio_input_tokens": value("audio_input_tokens", "input_audio_tokens"),
        "audio_output_tokens": value("audio_output_tokens", "output_audio_tokens"),
        "image_input_tokens": value("image_input_tokens", "input_image_tokens"),
        "image_output_tokens": value("image_output_tokens", "output_image_tokens"),
        "video_input_tokens": value("video_input_tokens", "input_video_tokens"),
        "video_output_tokens": value("video_output_tokens", "output_video_tokens"),
        "search_queries": value("search_queries", "web_search_queries", "grounding_queries"),
        "meters": value("meters", "usage_meters"),
        "cost_usd": value("cost_usd"),
        "cost_source": value("cost_source") or "provider_reported",
    }


def instrument(
    function: Callable[..., Any],
    *,
    operation_name: str,
    provider: str,
    model: str,
    observe_result: ResultObserver | None = None,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    operation_attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> Callable[..., Any]:
    """Wrap one sync or async provider call in a model operation and execution."""

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
                with witdem.model(
                    operation_name,
                    provider=provider,
                    model=model,
                    attributes=operation_attributes,
                ) as operation:
                    result = await function(*args, **kwargs)
                    _record_result(operation, result, observe_result)
                integration_settings.report(result, witdem)
                return result

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            with witdem.model(
                operation_name,
                provider=provider,
                model=model,
                attributes=operation_attributes,
            ) as operation:
                result = function(*args, **kwargs)
                _record_result(operation, result, observe_result)
            integration_settings.report(result, witdem)
            return result

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
