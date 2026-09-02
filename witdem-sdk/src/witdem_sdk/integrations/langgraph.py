"""LangGraph integration using the LangChain callback contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import baggage

from witdem_sdk import Witdem, configure
from witdem_sdk.integrations._common import report_business_result
from witdem_sdk.integrations.langchain import WitdemCallbackHandler

ResultReporter = Callable[[Any], Mapping[str, Any] | None]


class WitdemLangGraphCallback(WitdemCallbackHandler):
    """Callback handler for graph runs, nodes, tools, and model calls."""

    def _start(
        self,
        run_id: Any,
        name: str,
        kind: str,
        *,
        parent_run_id: Any = None,
        **attributes: Any,
    ) -> None:
        super()._start(run_id, name, kind, parent_run_id=parent_run_id, **attributes)
        span = self._operations.get(str(run_id))
        if span is not None:
            span.set_attribute("witdem.framework.id", "langgraph")
            span.set_attribute("witdem.execution.source", "langgraph")

    def on_chain_start(
        self,
        serialized: Any,
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        raw_metadata = kwargs.get("metadata")
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        graph_node = metadata.get("langgraph_node")
        name = (
            str(graph_node)
            if graph_node
            else (serialized or {}).get("name", "langgraph.graph")
            if isinstance(serialized, dict)
            else "langgraph.graph"
        )
        kind = "graph_node" if graph_node else "workflow"
        self._start(
            run_id,
            name,
            kind,
            parent_run_id=str(parent_run_id) if parent_run_id else None,
            **{
                "witdem.operation.family": "orchestration",
                "witdem.operation.type": "component" if graph_node else "workflow",
                "witdem.operation.interface": "framework",
                "langgraph.node": graph_node,
                "langgraph.step": metadata.get("langgraph_step"),
                "langgraph.checkpoint_ns": metadata.get("langgraph_checkpoint_ns"),
            },
        )


def _callbacks(config: Any) -> Any:
    return config.get("callbacks") if isinstance(config, Mapping) else None


def _already_instrumented(config: Any) -> bool:
    callbacks = _callbacks(config)
    if isinstance(callbacks, (list, tuple)):
        return any(isinstance(callback, WitdemCallbackHandler) for callback in callbacks)
    handlers = getattr(callbacks, "handlers", ())
    return any(isinstance(callback, WitdemCallbackHandler) for callback in handlers)


def _with_callback(config: Any, callback: WitdemLangGraphCallback) -> dict[str, Any]:
    resolved = dict(config or {})
    callbacks = resolved.get("callbacks")
    if callbacks is None:
        resolved["callbacks"] = [callback]
    elif isinstance(callbacks, (list, tuple)):
        resolved["callbacks"] = [*callbacks, callback]
    else:
        add_handler = getattr(callbacks, "add_handler", None)
        if not callable(add_handler):
            raise TypeError("LangGraph config callbacks must be a sequence or callback manager")
        add_handler(callback)
    return resolved


def _config_argument(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return args[1] if len(args) > 1 else kwargs.get("config")


def _replace_config(
    args: tuple[Any, ...], kwargs: dict[str, Any], config: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if len(args) > 1:
        return (args[0], config, *args[2:]), kwargs
    return args, {**kwargs, "config": config}


class InstrumentedLangGraph:
    """Transparent graph proxy that owns Witdem setup for each invocation."""

    def __init__(
        self,
        graph: Any,
        *,
        service_name: str | None,
        execution_name: str | None,
        endpoint: str | None,
        config_path: str | None,
        provider: str | None,
        model: str | None,
        capture_content: bool,
        attributes: Mapping[str, Any] | None,
        report_result: ResultReporter | None,
    ) -> None:
        self._graph = graph
        self._service_name = service_name
        self._execution_name = execution_name
        self._endpoint = endpoint
        self._config_path = config_path
        self._provider = provider
        self._model = model
        self._capture_content = capture_content
        self._attributes = dict(attributes or {})
        self._report_result = report_result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    @contextmanager
    def _invocation(self, config: Any) -> Iterator[tuple[Witdem, dict[str, Any]]]:
        with configure(
            self._service_name,
            endpoint=self._endpoint,
            config_path=self._config_path,
        ) as witdem:
            callback = WitdemLangGraphCallback(
                witdem,
                provider=self._provider,
                model=self._model,
                capture_content=self._capture_content,
            )
            resolved_config = _with_callback(config, callback)
            if baggage.get_baggage("witdem.execution_id"):
                yield witdem, resolved_config
            else:
                with witdem.execution(self._execution_name, attributes=self._attributes):
                    yield witdem, resolved_config

    def _report(self, result: Any, witdem: Witdem) -> None:
        report_business_result(result, witdem, self._report_result)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        config = _config_argument(args, kwargs)
        if _already_instrumented(config):
            return self._graph.invoke(*args, **kwargs)
        with self._invocation(config) as (witdem, resolved_config):
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved_config)
            result = self._graph.invoke(*resolved_args, **resolved_kwargs)
            self._report(result, witdem)
            return result

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        config = _config_argument(args, kwargs)
        if _already_instrumented(config):
            return await self._graph.ainvoke(*args, **kwargs)
        with self._invocation(config) as (witdem, resolved_config):
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved_config)
            result = await self._graph.ainvoke(*resolved_args, **resolved_kwargs)
            self._report(result, witdem)
            return result

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        config = _config_argument(args, kwargs)
        if _already_instrumented(config):
            yield from self._graph.stream(*args, **kwargs)
            return
        with self._invocation(config) as (_, resolved_config):
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved_config)
            yield from self._graph.stream(*resolved_args, **resolved_kwargs)

    async def astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        config = _config_argument(args, kwargs)
        if _already_instrumented(config):
            async for item in self._graph.astream(*args, **kwargs):
                yield item
            return
        with self._invocation(config) as (_, resolved_config):
            resolved_args, resolved_kwargs = _replace_config(args, kwargs, resolved_config)
            async for item in self._graph.astream(*resolved_args, **resolved_kwargs):
                yield item


def instrument(
    graph: Any,
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
) -> InstrumentedLangGraph:
    """Wrap a compiled LangGraph with automatic Witdem runtime instrumentation.

    The wrapper owns SDK configuration, execution correlation, callback
    injection, flushing, and error recording. ``report_result`` may return
    explicit keyword arguments for :meth:`Witdem.report`; it is never inferred.
    """

    if isinstance(graph, InstrumentedLangGraph):
        return graph
    return InstrumentedLangGraph(
        graph,
        service_name=service_name,
        execution_name=execution_name,
        endpoint=endpoint,
        config_path=config_path,
        provider=provider,
        model=model,
        capture_content=capture_content,
        attributes=attributes,
        report_result=report_result,
    )
