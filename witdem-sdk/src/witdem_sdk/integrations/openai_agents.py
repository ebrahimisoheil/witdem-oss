"""OpenAI Agents SDK native trace-processor integration."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from functools import wraps
from typing import Any, cast

from witdem_sdk.integrations._common import ResultReporter, settings

_REGISTRATIONS: dict[int, _Registration] = {}


class WitdemTraceProcessor:
    def __init__(self, witdem: Any, capture_content: bool = False) -> None:
        self.witdem = witdem
        self.capture_content = capture_content
        self._execution_ids: dict[str, str] = {}
        self._observed: set[tuple[str, str]] = set()
        self._operations: dict[str, tuple[AbstractContextManager[Any], Any]] = {}

    def on_trace_start(self, trace: Any) -> None:
        native_trace_id = self._native_trace_id(trace)
        try:
            from witdem_sdk._correlation import resolve_correlation

            self._execution_ids[native_trace_id] = resolve_correlation(None)[0]
        except Exception:
            pass
        self._emit("agent.trace.started", trace)

    def on_trace_end(self, trace: Any) -> None:
        self._emit("agent.trace.completed", trace)
        self._execution_ids.pop(self._native_trace_id(trace), None)

    def on_span_start(self, span: Any) -> None:
        self._emit("agent.span.started", span)
        try:
            manager = self._operation_manager(span)
            operation = manager.__enter__()
            self._operations[str(span.span_id)] = (manager, operation)
        except Exception:
            # Framework tracing must never make an application call fail.
            return

    def on_span_end(self, span: Any) -> None:
        self._emit("agent.span.completed", span)
        active = self._operations.pop(str(getattr(span, "span_id", "")), None)
        if active is None:
            return
        manager, operation = active
        try:
            self._finish_operation(operation, span)
            error = getattr(span, "error", None)
            if error:
                message = error.get("message") if isinstance(error, dict) else str(error)
                operation.span.set_status(self._error_status(str(message)))
        finally:
            manager.__exit__(None, None, None)

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        self.witdem.flush()

    @staticmethod
    def _error_status(message: str) -> Any:
        from opentelemetry.trace import Status, StatusCode

        return Status(StatusCode.ERROR, message)

    def _operation_manager(self, span: Any) -> AbstractContextManager[Any]:
        data = getattr(span, "span_data", None)
        kind = type(data).__name__
        name = str(getattr(data, "name", None) or getattr(data, "type", None) or kind)
        if kind in {"GenerationSpanData", "ResponseSpanData"}:
            model = self._model(data) or "openai.model"
            return cast(
                AbstractContextManager[Any],
                self.witdem.model(
                    "openai_agents.model",
                    provider="openai",
                    model=model,
                    attributes={"openai_agents.span_kind": kind},
                ),
            )
        if kind == "FunctionSpanData":
            return cast(
                AbstractContextManager[Any],
                self.witdem.tool(
                    name,
                    attributes={"openai_agents.span_kind": kind},
                ),
            )
        runtime_kind = {
            "AgentSpanData": "agent",
            "TaskSpanData": "workflow",
            "TurnSpanData": "agent_step",
            "HandoffSpanData": "handoff",
            "GuardrailSpanData": "guardrail",
        }.get(kind, "component")
        return cast(
            AbstractContextManager[Any],
            self.witdem.operation(
                f"openai_agents.{name}",
                kind=runtime_kind,
                attributes={"openai_agents.span_kind": kind},
            ),
        )

    @staticmethod
    def _model(data: Any) -> str | None:
        direct = getattr(data, "model", None)
        response = getattr(data, "response", None)
        observed = getattr(response, "model", None)
        value = observed or direct
        return str(value) if value else None

    @staticmethod
    def _usage(data: Any) -> dict[str, int]:
        usage = getattr(data, "usage", None)
        response = getattr(data, "response", None)
        usage = usage or getattr(response, "usage", None)
        values: dict[str, int] = {}
        aliases = {
            "input_tokens": ("input_tokens", "inputTokens"),
            "output_tokens": ("output_tokens", "outputTokens"),
            "total_tokens": ("total_tokens", "totalTokens"),
        }
        for target, names in aliases.items():
            for name in names:
                value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
                if isinstance(value, int):
                    values[target] = value
                    break
        if "total_tokens" not in values and {"input_tokens", "output_tokens"} <= values.keys():
            values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
        return values

    def _finish_operation(self, operation: Any, span: Any) -> None:
        data = getattr(span, "span_data", None)
        model = self._model(data)
        if model:
            operation.response_model(model)
        usage = self._usage(data)
        if usage:
            operation.usage(**usage)

    @staticmethod
    def _native_trace_id(value: Any) -> str:
        return str(getattr(value, "trace_id", None) or getattr(value, "parent_trace_id", None) or id(value))

    def _emit(self, name: str, value: Any) -> None:
        native_trace_id = self._native_trace_id(value)
        observed_id = str(getattr(value, "span_id", None) or native_trace_id)
        observed_key = (name, observed_id)
        if observed_key in self._observed:
            return
        self._observed.add(observed_key)
        attributes: dict[str, Any] = {"integration": "openai_agents"}
        span_data = getattr(value, "span_data", None)
        if span_data is not None:
            attributes["openai_agents.span_kind"] = type(span_data).__name__
            for field in ("name", "type", "tool_name", "from_agent", "to_agent", "model"):
                observed = getattr(span_data, field, None)
                if isinstance(observed, (str, int, float, bool)):
                    attributes[f"openai_agents.{field}"] = observed
        if self.capture_content:
            attributes["witdem.capture_content"] = True
        try:
            execution_id = self._execution_ids.get(native_trace_id)
            if execution_id is None:
                from witdem_sdk._correlation import resolve_correlation

                execution_id = resolve_correlation(None)[0]
                self._execution_ids[native_trace_id] = execution_id
            self.witdem.event(name, attributes, execution_id=execution_id)
        except Exception:
            # Native processor callbacks must never break an agent run when
            # the framework creates a trace outside a Witdem execution context.
            return


class _Registration:
    def __init__(self, remove: Any, key: int) -> None:
        self._remove = remove
        self._key = key

    def uninstall(self) -> None:
        if self._remove is not None:
            self._remove()
            self._remove = None
            _REGISTRATIONS.pop(self._key, None)


def install_openai_agents(witdem: Any, *, capture_content: bool = False) -> _Registration:
    """Register Witdem through the Agents SDK's supported processor hook."""

    key = id(witdem)
    existing = _REGISTRATIONS.get(key)
    if existing is not None:
        if existing._remove is None:
            _REGISTRATIONS.pop(key, None)
        else:
            return existing

    try:
        import agents  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise ImportError("install_openai_agents requires witdem-sdk[openai] and openai-agents") from exc
    processor = WitdemTraceProcessor(witdem, capture_content)
    add = getattr(agents, "add_trace_processor", None)
    remove = getattr(agents, "remove_trace_processor", None)
    if add is None:
        tracing = getattr(agents, "tracing", None)
        add = getattr(tracing, "add_trace_processor", None) if tracing else None
        remove = getattr(tracing, "remove_trace_processor", None) if tracing else None
    if add is None:
        raise RuntimeError("the installed OpenAI Agents SDK does not expose add_trace_processor")
    add(processor)
    registration = _Registration(lambda: remove(processor) if remove is not None else None, key)
    _REGISTRATIONS[key] = registration
    return registration


def instrument(
    function: Callable[..., Any],
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> Callable[..., Any]:
    """Wrap one OpenAI Agents workload with setup, tracing, and cleanup."""

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
                handle = install_openai_agents(witdem, capture_content=capture_content)
                try:
                    result = await function(*args, **kwargs)
                    integration_settings.report(result, witdem)
                    return result
                finally:
                    handle.uninstall()

        async_wrapper.__dict__["__witdem_instrumented__"] = True
        return async_wrapper

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with integration_settings.invocation() as witdem:
            handle = install_openai_agents(witdem, capture_content=capture_content)
            try:
                result = function(*args, **kwargs)
                integration_settings.report(result, witdem)
                return result
            finally:
                handle.uninstall()

    wrapper.__dict__["__witdem_instrumented__"] = True
    return wrapper
