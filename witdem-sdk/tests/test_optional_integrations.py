from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _fake_operation() -> tuple[Any, dict[str, Any], list[BaseException], list[dict[str, int]]]:
    attributes: dict[str, Any] = {}
    errors: list[BaseException] = []
    usage: list[dict[str, int]] = []
    operation = SimpleNamespace(
        span=SimpleNamespace(
            set_attribute=lambda key, value: attributes.__setitem__(key, value),
            record_exception=errors.append,
        ),
        response_model=lambda model: operation,
        usage=lambda **values: (usage.append(values), operation)[1],
    )
    return operation, attributes, errors, usage


def test_anthropic_sync_and_async_proxy_preserve_results_and_usage() -> None:
    from witdem_sdk.integrations.anthropic import instrument_anthropic

    response = SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        usage=SimpleNamespace(
            input_tokens=4,
            output_tokens=2,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=1,
        ),
        content=[SimpleNamespace(type="tool_use", id="toolu_real")],
    )

    for create, invoke in (
        (lambda **kwargs: response, lambda wrapped: wrapped.messages.create(model="claude-haiku-4-5")),
        (
            (lambda: None),
            lambda wrapped: asyncio.run(wrapped.messages.create(model="claude-haiku-4-5")),
        ),
    ):
        operation, attributes, _, observed_usage = _fake_operation()

        @contextmanager
        def model(*args: Any, _operation: Any = operation, **kwargs: Any):
            yield _operation

        if create() is None:
            async def async_create(**kwargs: Any) -> Any:
                return response

            messages = SimpleNamespace(create=async_create)
        else:
            messages = SimpleNamespace(create=create)
        wrapped = instrument_anthropic(SimpleNamespace(messages=messages), witdem=SimpleNamespace(model=model))
        assert invoke(wrapped) is response
        assert attributes["gen_ai.tool.call.id"] == "toolu_real"
        assert observed_usage[-1]["cache_read_tokens"] == 3
        assert observed_usage[-1]["cache_creation_tokens"] == 1


def test_openai_agents_registration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("agents")
    import agents

    from witdem_sdk.integrations import openai_agents

    added: list[Any] = []
    removed: list[Any] = []
    monkeypatch.setattr(agents, "add_trace_processor", added.append)
    monkeypatch.setattr(agents, "remove_trace_processor", removed.append, raising=False)
    witdem = SimpleNamespace(flush=lambda: True, event=lambda *args, **kwargs: None)

    first = openai_agents.install_openai_agents(witdem)
    second = openai_agents.install_openai_agents(witdem)
    assert first is second
    assert len(added) == 1
    first.uninstall()
    assert removed == added
    first.uninstall()
    assert len(removed) == 1


def test_openai_processor_correlates_each_native_trace_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    from witdem_sdk import _correlation
    from witdem_sdk.integrations.openai_agents import WitdemTraceProcessor

    execution_ids = iter(("execution-a", "execution-b"))
    monkeypatch.setattr(_correlation, "resolve_correlation", lambda explicit: (next(execution_ids), None, None))
    events: list[tuple[str, str]] = []
    witdem = SimpleNamespace(
        flush=lambda: True,
        event=lambda name, value, *, execution_id: events.append((name, execution_id)),
    )
    processor = WitdemTraceProcessor(witdem)
    first = SimpleNamespace(trace_id="trace-a")
    second = SimpleNamespace(trace_id="trace-b")

    processor.on_trace_start(first)
    processor.on_trace_end(first)
    processor.on_trace_start(second)
    processor.on_trace_end(second)

    assert events == [
        ("agent.trace.started", "execution-a"),
        ("agent.trace.completed", "execution-a"),
        ("agent.trace.started", "execution-b"),
        ("agent.trace.completed", "execution-b"),
    ]


def test_claude_agent_observer_emits_aggregate_model_usage_and_real_tool_id() -> None:
    from witdem_sdk.integrations.claude_agent import instrument_claude_agent

    operations: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def model(name: str, **kwargs: Any):
        observed: dict[str, Any] = {"name": name, **kwargs}
        operation = SimpleNamespace(
            response_model=lambda value: observed.update(response_model=value) or operation,
            usage=lambda **value: observed.update(usage=value) or operation,
            cost=lambda value: observed.update(cost=value) or operation,
        )
        yield operation
        operations.append(("model", observed))

    events: list[tuple[str, dict[str, Any]]] = []

    observer = instrument_claude_agent(
        SimpleNamespace(model=model, event=lambda name, value: events.append((name, value))),
        model="claude-sonnet-4-6",
    )
    class ToolUseBlock:
        id = "toolu_real"
        name = "get_invoices"

    class AssistantMessage:
        model = "claude-sonnet-4-6-snapshot"
        content = [ToolUseBlock()]

    observer.observe(AssistantMessage())

    class ResultMessage:
        model_usage = {
            "claude-sonnet-4-6-snapshot": {
                "inputTokens": 12,
                "outputTokens": 3,
                "costUSD": 0.001,
            }
        }

    observer.observe(ResultMessage())

    assert operations[0][0] == "model"
    assert operations[0][1]["usage"]["total_tokens"] == 15
    assert operations[0][1]["response_model"] == "claude-sonnet-4-6-snapshot"
    assert operations[0][1]["cost"] == 0.001
    assert events == [
        (
            "claude_agent.tool_use",
            {
                "tool_name": "get_invoices",
                "tool_use_id": "toolu_real",
                "integration": "claude_agent_sdk",
            },
        )
    ]


def test_langchain_callbacks_cover_usage_retrievers_and_async(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langchain_core")
    from witdem_sdk.integrations import langchain

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(langchain.trace, "get_tracer", provider.get_tracer)
    handler = langchain.WitdemCallbackHandler(
        SimpleNamespace(),
        provider="openai",
        model="model-a",
        capture_content=False,
    )

    handler.on_retriever_start({}, "query", run_id="retriever")
    handler.on_retriever_end([], run_id="retriever")
    handler.on_chat_model_start({"kwargs": {"model": "model-a"}}, [], run_id="model")
    message = SimpleNamespace(
        usage_metadata={"input_tokens": 5, "output_tokens": 2},
        response_metadata={"model_name": "model-a-snapshot"},
    )
    response = SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=message)]])
    handler.on_llm_end(response, run_id="model")
    asyncio.run(handler.aon_tool_start({"name": "lookup"}, "input", run_id="tool"))
    asyncio.run(handler.aon_tool_end("output", run_id="tool"))

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["langchain.retriever"].attributes["witdem.runtime.kind"] == "retriever"
    assert spans["langchain.chat_model"].attributes["gen_ai.usage.input_tokens"] == 5
    assert spans["langchain.chat_model"].attributes["gen_ai.provider.name"] == "openai"
    assert spans["langchain.chat_model"].attributes["gen_ai.response.model"] == "model-a-snapshot"
    assert "langchain.tool.lookup" in spans


def test_langgraph_marks_nodes_and_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langgraph")
    from witdem_sdk.integrations import langchain
    from witdem_sdk.integrations.langgraph import WitdemLangGraphCallback

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(langchain.trace, "get_tracer", provider.get_tracer)
    handler = WitdemLangGraphCallback(SimpleNamespace())
    handler.on_chain_start({}, {}, run_id="node", metadata={"langgraph_node": "research", "langgraph_step": 2})
    handler.on_chain_end({}, run_id="node")
    span = exporter.get_finished_spans()[0]
    assert span.name == "research"
    assert span.attributes["witdem.runtime.kind"] == "graph_node"
    assert span.attributes["langgraph.step"] == 2


def test_haystack_registration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("haystack")
    import haystack.tracing
    import haystack_integrations.tracing.opentelemetry as haystack_otel

    from witdem_sdk.integrations import haystack as integration

    enabled: list[Any] = []
    disabled: list[bool] = []

    class FakeTracer:
        def __init__(self, tracer: Any) -> None:
            self.tracer = tracer

        def disable(self) -> None:
            disabled.append(True)

    monkeypatch.setattr(haystack.tracing, "enable_tracing", enabled.append)
    monkeypatch.setattr(haystack_otel, "OpenTelemetryTracer", FakeTracer)
    witdem = SimpleNamespace()
    first = integration.enable_haystack(witdem)
    second = integration.enable_haystack(witdem)
    assert first is second
    assert len(enabled) == 1
    first.disable()
    assert disabled == [True]
