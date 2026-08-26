"""Explicit LangChain callback integration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from witdem_sdk.integrations._common import IntegrationSettings, ResultReporter, settings

try:
    from langchain_core.callbacks import BaseCallbackHandler  # type: ignore[import-not-found,unused-ignore]
except ImportError as exc:  # pragma: no cover - exercised by clean extra-install tests
    raise ImportError("WitdemCallbackHandler requires witdem-sdk[langchain]") from exc


def _usage_mapping(value: Any) -> Mapping[str, Any] | None:
    """Return provider usage as a mapping without depending on its SDK type."""

    if isinstance(value, Mapping):
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                converted = method()
            except TypeError:
                continue
            if isinstance(converted, Mapping):
                return converted
    if value is not None:
        fields = {
            key: getattr(value, key)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "prompt_token_count",
                "candidates_token_count",
                "total_token_count",
                "cached_content_token_count",
                "thoughts_token_count",
            )
            if getattr(value, key, None) is not None
        }
        if fields:
            return fields
    return None


def _usage_value(usage: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = usage.get(key)
        if value is not None:
            return value
    return None


def _nested_usage_value(usage: Mapping[str, Any], group: str, *keys: str) -> Any:
    nested = _usage_mapping(usage.get(group))
    return _usage_value(nested, *keys) if nested is not None else None


class WitdemCallbackHandler(BaseCallbackHandler):  # type: ignore[misc,unused-ignore]
    def __init__(
        self,
        witdem: Any,
        *,
        provider: str | None = None,
        model: str | None = None,
        capture_content: bool = False,
    ) -> None:
        super().__init__()
        self.witdem = witdem
        self.capture_content = capture_content
        self.provider = provider
        self.model = model
        self._tracer = trace.get_tracer("witdem_sdk.integrations.langchain")
        self._operations: dict[str, Any] = {}

    def _start(
        self,
        run_id: Any,
        name: str,
        kind: str,
        *,
        parent_run_id: Any = None,
        **attributes: Any,
    ) -> None:
        parent = self._operations.get(str(parent_run_id)) if parent_run_id is not None else None
        context = trace.set_span_in_context(parent) if parent is not None else None
        span = self._tracer.start_span(name, context=context, kind=SpanKind.INTERNAL)
        span.set_attribute("witdem.runtime.kind", kind)
        span.set_attribute("langchain.run_id", str(run_id))
        if parent_run_id is not None:
            span.set_attribute("langchain.parent_run_id", str(parent_run_id))
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        self._operations[str(run_id)] = span

    def _end(self, run_id: Any, error: BaseException | None = None, output: Any = None) -> None:
        span = self._operations.pop(str(run_id), None)
        if span is not None:
            if error is not None:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
            span.end()

    def on_chain_start(
        self, serialized: Any, inputs: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any
    ) -> None:
        self._start(run_id, "langchain.chain", "component", parent_run_id=parent_run_id)

    def on_chain_end(self, outputs: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, output=outputs)

    def on_chain_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, error=error)

    def on_llm_start(
        self, serialized: Any, prompts: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any
    ) -> None:
        model = (serialized or {}).get("kwargs", {}).get("model_name") if isinstance(serialized, dict) else None
        self._start(
            run_id,
            "langchain.llm",
            "model",
            parent_run_id=parent_run_id,
            **{
                "gen_ai.framework.name": "langchain",
                "gen_ai.provider.name": self.provider,
                "gen_ai.request.model": model or self.model or "unknown",
            },
        )

    def on_chat_model_start(
        self, serialized: Any, messages: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any
    ) -> None:
        model = (serialized or {}).get("kwargs", {}).get("model") if isinstance(serialized, dict) else None
        self._start(
            run_id,
            "langchain.chat_model",
            "model",
            parent_run_id=parent_run_id,
            **{
                "gen_ai.framework.name": "langchain",
                "gen_ai.provider.name": self.provider,
                "gen_ai.request.model": model or self.model or "unknown",
            },
        )

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._record_usage(run_id, response)
        self._end(run_id, output=response)

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, error=error)

    def on_tool_start(
        self, serialized: Any, input_str: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any
    ) -> None:
        name = serialized.get("name", "tool") if isinstance(serialized, dict) else "tool"
        self._start(run_id, f"langchain.tool.{name}", "tool", parent_run_id=parent_run_id)

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, output=output)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, error=error)

    def on_retriever_start(
        self, serialized: Any, query: Any, *, run_id: Any, parent_run_id: Any = None, **kwargs: Any
    ) -> None:
        self._start(run_id, "langchain.retriever", "retriever", parent_run_id=parent_run_id)

    def on_retriever_end(self, documents: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, output=documents)

    def on_retriever_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self._end(run_id, error=error)

    async def aon_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        self.on_chain_start(serialized, inputs, **kwargs)

    async def aon_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        self.on_chain_end(outputs, **kwargs)

    async def aon_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        self.on_chain_error(error, **kwargs)

    async def aon_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self.on_llm_start(serialized, prompts, **kwargs)

    async def aon_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self.on_chat_model_start(serialized, messages, **kwargs)

    async def aon_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.on_llm_end(response, **kwargs)

    async def aon_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.on_llm_error(error, **kwargs)

    async def aon_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        self.on_tool_start(serialized, input_str, **kwargs)

    async def aon_tool_end(self, output: Any, **kwargs: Any) -> None:
        self.on_tool_end(output, **kwargs)

    async def aon_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self.on_tool_error(error, **kwargs)

    async def aon_retriever_start(self, serialized: Any, query: Any, **kwargs: Any) -> None:
        self.on_retriever_start(serialized, query, **kwargs)

    async def aon_retriever_end(self, documents: Any, **kwargs: Any) -> None:
        self.on_retriever_end(documents, **kwargs)

    async def aon_retriever_error(self, error: BaseException, **kwargs: Any) -> None:
        self.on_retriever_error(error, **kwargs)

    def _record_usage(self, run_id: Any, response: Any) -> None:
        active = self._operations.get(str(run_id))
        llm_output = _usage_mapping(getattr(response, "llm_output", None)) or {}
        generations = getattr(response, "generations", None) or []
        first_generation = generations[0][0] if generations and generations[0] else None
        message = getattr(first_generation, "message", None)
        response_metadata = _usage_mapping(getattr(message, "response_metadata", None)) or {}
        usage = next(
            (
                candidate
                for candidate in (
                    _usage_mapping(llm_output.get("token_usage")),
                    _usage_mapping(llm_output.get("usage_metadata")),
                    _usage_mapping(getattr(response, "usage_metadata", None)),
                    _usage_mapping(getattr(message, "usage_metadata", None)),
                    _usage_mapping(response_metadata.get("token_usage")),
                    _usage_mapping(response_metadata.get("usage_metadata")),
                )
                if candidate is not None
            ),
            None,
        )
        if active is None or usage is None:
            return

        input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens", "prompt_token_count")
        output_tokens = _usage_value(
            usage, "completion_tokens", "output_tokens", "candidates_token_count"
        )
        total_tokens = _usage_value(usage, "total_tokens", "total_token_count")
        cache_read_tokens = _usage_value(
            usage, "cache_read_tokens", "cache_read_input_tokens", "cached_content_token_count"
        )
        if cache_read_tokens is None:
            cache_read_tokens = _nested_usage_value(usage, "input_token_details", "cache_read")
        reasoning_tokens = _usage_value(usage, "reasoning_tokens", "thoughts_token_count")
        if reasoning_tokens is None:
            reasoning_tokens = _nested_usage_value(usage, "output_token_details", "reasoning")

        for key, value in (
            ("gen_ai.usage.input_tokens", input_tokens),
            ("gen_ai.usage.output_tokens", output_tokens),
            ("gen_ai.usage.total_tokens", total_tokens),
            ("gen_ai.usage.cache_read.input_tokens", cache_read_tokens),
            ("gen_ai.usage.reasoning.output_tokens", reasoning_tokens),
        ):
            if value is not None:
                active.set_attribute(key, value)

        model = _usage_value(response_metadata, "model_name", "model_version", "model")
        if model:
            active.set_attribute("gen_ai.response.model", str(model))


def _with_callback(config: Any, callback: WitdemCallbackHandler) -> dict[str, Any]:
    resolved = dict(config or {})
    callbacks = resolved.get("callbacks")
    if callbacks is None:
        resolved["callbacks"] = [callback]
    elif isinstance(callbacks, (list, tuple)):
        resolved["callbacks"] = [*callbacks, callback]
    else:
        add_handler = getattr(callbacks, "add_handler", None)
        if not callable(add_handler):
            raise TypeError("LangChain config callbacks must be a sequence or callback manager")
        add_handler(callback)
    return resolved


def _already_instrumented(config: Any) -> bool:
    callbacks = config.get("callbacks") if isinstance(config, Mapping) else None
    if isinstance(callbacks, (list, tuple)):
        return any(isinstance(callback, WitdemCallbackHandler) for callback in callbacks)
    return any(isinstance(callback, WitdemCallbackHandler) for callback in getattr(callbacks, "handlers", ()))


def _replace_config(
    args: tuple[Any, ...], kwargs: dict[str, Any], config: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if len(args) > 1:
        return (args[0], config, *args[2:]), kwargs
    return args, {**kwargs, "config": config}


class InstrumentedRunnable:
    """Transparent LangChain runnable with an SDK-owned invocation lifecycle."""

    def __init__(
        self,
        runnable: Any,
        *,
        integration_settings: IntegrationSettings,
        provider: str | None,
        model: str | None,
        capture_content: bool,
    ) -> None:
        self._runnable = runnable
        self._settings = integration_settings
        self._provider = provider
        self._model = model
        self._capture_content = capture_content

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)

    def _callback(self, witdem: Any) -> WitdemCallbackHandler:
        return WitdemCallbackHandler(
            witdem,
            provider=self._provider,
            model=self._model,
            capture_content=self._capture_content,
        )

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        if _already_instrumented(config):
            return self._runnable.invoke(*args, **kwargs)
        with self._settings.invocation() as witdem:
            resolved = _with_callback(config, self._callback(witdem))
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved)
            result = self._runnable.invoke(*resolved_args, **resolved_kwargs)
            self._settings.report(result, witdem)
            return result

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        if _already_instrumented(config):
            return await self._runnable.ainvoke(*args, **kwargs)
        with self._settings.invocation() as witdem:
            resolved = _with_callback(config, self._callback(witdem))
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved)
            result = await self._runnable.ainvoke(*resolved_args, **resolved_kwargs)
            self._settings.report(result, witdem)
            return result

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        if _already_instrumented(config):
            yield from self._runnable.stream(*args, **kwargs)
            return
        with self._settings.invocation() as witdem:
            resolved = _with_callback(config, self._callback(witdem))
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved)
            yield from self._runnable.stream(*resolved_args, **resolved_kwargs)

    async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        config = args[1] if len(args) > 1 else kwargs.get("config")
        if _already_instrumented(config):
            async for item in self._runnable.astream(*args, **kwargs):
                yield item
            return
        with self._settings.invocation() as witdem:
            resolved = _with_callback(config, self._callback(witdem))
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved)
            async for item in self._runnable.astream(*resolved_args, **resolved_kwargs):
                yield item


def instrument(
    runnable: Any,
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> InstrumentedRunnable:
    """Wrap a LangChain runnable with automatic Witdem instrumentation."""

    if isinstance(runnable, InstrumentedRunnable):
        return runnable
    return InstrumentedRunnable(
        runnable,
        integration_settings=settings(
            service_name=service_name,
            execution_name=execution_name,
            endpoint=endpoint,
            config_path=config_path,
            attributes=attributes,
            report_result=report_result,
        ),
        provider=provider,
        model=model,
        capture_content=capture_content,
    )
