from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytest.importorskip("langgraph")

from witdem_sdk.integrations import langgraph


class _FakeWitdem:
    def __init__(self) -> None:
        self.executions: list[tuple[str | None, dict[str, Any]]] = []
        self.reports: list[dict[str, Any]] = []
        self.closed = 0

    def __enter__(self) -> _FakeWitdem:
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed += 1

    @contextmanager
    def execution(self, name: str | None, *, attributes: Mapping[str, Any]) -> Iterator[str]:
        self.executions.append((name, dict(attributes)))
        yield "execution-id"

    def report(self, **values: Any) -> None:
        self.reports.append(values)


class _FakeGraph:
    graph_name = "research"

    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []

    def invoke(self, value: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.configs.append(dict(config or {}))
        return {"value": value, "approved": True}

    async def ainvoke(self, value: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.configs.append(dict(config or {}))
        return {"value": value, "approved": False}

    def stream(self, value: Any, config: dict[str, Any] | None = None) -> Iterator[int]:
        self.configs.append(dict(config or {}))
        yield value
        yield value + 1

    async def astream(self, value: Any, config: dict[str, Any] | None = None) -> AsyncIterator[int]:
        self.configs.append(dict(config or {}))
        yield value
        yield value + 1


@pytest.fixture
def configured(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_FakeWitdem, list[tuple[tuple[Any, ...], dict[str, Any]]]]:
    client = _FakeWitdem()
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_configure(*args: Any, **kwargs: Any) -> _FakeWitdem:
        calls.append((args, kwargs))
        return client

    monkeypatch.setattr(langgraph, "configure", fake_configure)
    return client, calls


def test_instrument_invoke_owns_setup_and_reports_explicit_result(
    configured: tuple[_FakeWitdem, list[tuple[tuple[Any, ...], dict[str, Any]]]],
) -> None:
    client, calls = configured
    graph = _FakeGraph()
    wrapped = langgraph.instrument(
        graph,
        service_name="research-agent",
        execution_name="research-report",
        provider="openai",
        model="gpt-4o",
        attributes={"entrypoint": "api"},
        report_result=lambda state: {
            "result": "approved",
            "product_goal_achieved": state["approved"],
        },
    )

    result = wrapped.invoke("question", {"tags": ["test"]})

    assert result == {"value": "question", "approved": True}
    assert wrapped.graph_name == "research"
    assert calls == [(("research-agent",), {"endpoint": None, "config_path": None})]
    assert client.executions == [("research-report", {"entrypoint": "api"})]
    assert client.reports == [{"result": "approved", "product_goal_achieved": True}]
    assert graph.configs[0]["tags"] == ["test"]
    assert len(graph.configs[0]["callbacks"]) == 1
    assert isinstance(graph.configs[0]["callbacks"][0], langgraph.WitdemLangGraphCallback)
    assert client.closed == 1


def test_instrument_supports_async_invoke_and_streaming(
    configured: tuple[_FakeWitdem, list[tuple[tuple[Any, ...], dict[str, Any]]]],
) -> None:
    client, _ = configured
    graph = _FakeGraph()
    wrapped = langgraph.instrument(graph, service_name="research-agent")

    assert asyncio.run(wrapped.ainvoke("question"))["approved"] is False
    assert list(wrapped.stream(3)) == [3, 4]

    async def collect() -> list[int]:
        return [item async for item in wrapped.astream(5)]

    assert asyncio.run(collect()) == [5, 6]
    assert len(client.executions) == 3
    assert client.closed == 3
    assert all(len(config["callbacks"]) == 1 for config in graph.configs)


def test_instrument_is_idempotent_and_respects_an_existing_witdem_callback(
    configured: tuple[_FakeWitdem, list[tuple[tuple[Any, ...], dict[str, Any]]]],
) -> None:
    client, calls = configured
    graph = _FakeGraph()
    wrapped = langgraph.instrument(graph, service_name="research-agent")

    assert langgraph.instrument(wrapped) is wrapped

    callback = langgraph.WitdemLangGraphCallback(client)
    result = wrapped.invoke("question", {"callbacks": [callback]})

    assert result["approved"] is True
    assert calls == []
    assert client.executions == []
    assert graph.configs[0]["callbacks"] == [callback]


def test_instrument_runs_a_compiled_langgraph(
    configured: tuple[_FakeWitdem, list[tuple[tuple[Any, ...], dict[str, Any]]]],
) -> None:
    from langgraph.graph import END, StateGraph

    class State(TypedDict):
        count: int

    builder = StateGraph(State)
    builder.add_node("increment", lambda state: {"count": state["count"] + 1})
    builder.set_entry_point("increment")
    builder.add_edge("increment", END)

    wrapped = langgraph.instrument(builder.compile(), service_name="counter")

    assert wrapped.invoke({"count": 1}) == {"count": 2}


def test_langgraph_node_is_current_parent_for_direct_sdk_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph.graph import END, StateGraph

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("witdem_sdk.integrations.langchain.trace.get_tracer", provider.get_tracer)
    direct_tracer = provider.get_tracer("direct-provider")

    class State(TypedDict):
        count: int

    def increment(state: State) -> State:
        with direct_tracer.start_as_current_span("direct.openai"):
            return {"count": state["count"] + 1}

    builder = StateGraph(State)
    builder.add_node("increment", increment)
    builder.set_entry_point("increment")
    builder.add_edge("increment", END)
    callback = langgraph.WitdemLangGraphCallback(SimpleNamespace())

    assert builder.compile().invoke({"count": 1}, config={"callbacks": [callback]}) == {"count": 2}

    spans = {span.name: span for span in exporter.get_finished_spans()}
    node = next(span for span in spans.values() if span.attributes.get("langgraph.node") == "increment")
    assert spans["direct.openai"].parent is not None
    assert spans["direct.openai"].parent.span_id == node.context.span_id
