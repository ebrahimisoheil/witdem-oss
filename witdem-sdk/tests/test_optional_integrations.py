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


def test_openai_sync_and_async_proxies_preserve_results_usage_and_tool_ids() -> None:
    from witdem_sdk.integrations.openai import instrument_openai

    response = SimpleNamespace(
        model="gpt-5.4-2026-03-05",
        usage=SimpleNamespace(
            input_tokens=9,
            output_tokens=3,
            total_tokens=12,
            input_tokens_details=SimpleNamespace(cached_tokens=4),
            output_tokens_details=SimpleNamespace(reasoning_tokens=2),
        ),
        output=[SimpleNamespace(type="function_call", call_id="call_real")],
    )

    for create, invoke in (
        (lambda **kwargs: response, lambda wrapped: wrapped.responses.create(model="gpt-5.4")),
        (
            (lambda: None),
            lambda wrapped: asyncio.run(wrapped.responses.create(model="gpt-5.4")),
        ),
    ):
        operation, attributes, _, observed_usage = _fake_operation()

        @contextmanager
        def model(*args: Any, _operation: Any = operation, **kwargs: Any):
            yield _operation

        if create() is None:

            async def async_create(**kwargs: Any) -> Any:
                return response

            responses = SimpleNamespace(create=async_create)
        else:
            responses = SimpleNamespace(create=create)
        wrapped = instrument_openai(SimpleNamespace(responses=responses), witdem=SimpleNamespace(model=model))
        assert invoke(wrapped) is response
        assert attributes["gen_ai.tool.call.id"] == "call_real"
        assert observed_usage[-1] == {
            "input_tokens": 9,
            "output_tokens": 3,
            "total_tokens": 12,
            "cache_read_tokens": 4,
            "reasoning_tokens": 2,
        }


def test_openai_embeddings_emit_canonical_vector_measurements() -> None:
    from witdem_sdk.integrations.openai import instrument_openai

    response = SimpleNamespace(
        model="text-embedding-3-small",
        usage=SimpleNamespace(prompt_tokens=5, total_tokens=5),
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])],
    )
    operation, _, _, observed_usage = _fake_operation()
    observed_context: dict[str, Any] = {}

    @contextmanager
    def canonical_operation(name: str, **kwargs: Any):
        observed_context.update({"name": name, **kwargs})
        yield operation

    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: response))
    wrapped = instrument_openai(client, witdem=SimpleNamespace(operation=canonical_operation))

    assert wrapped.embeddings.create(model="text-embedding-3-small", input=["contract"]) is response
    assert observed_context["operation_type"] == "embedding"
    assert observed_context["provider_id"] == "openai"
    assert observed_context["execution_source"] == "openai_sdk"
    assert observed_usage[-1] == {
        "input_tokens": 5,
        "total_tokens": 5,
        "meters": {"vectors.output": 1, "vector.dimensions": 3},
    }


def test_openai_sync_stream_stays_open_and_records_final_response_event() -> None:
    from witdem_sdk.integrations.openai import instrument_openai

    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            model="gpt-5.4-2026-03-05",
            usage=SimpleNamespace(input_tokens=8, output_tokens=2, total_tokens=10),
            output=[SimpleNamespace(type="function_call", call_id="call_stream")],
        ),
    )
    operation, attributes, _, observed_usage = _fake_operation()
    lifecycle: list[str] = []

    @contextmanager
    def model(*args: Any, **kwargs: Any):
        lifecycle.append("entered")
        try:
            yield operation
        finally:
            lifecycle.append("exited")

    stream = iter((SimpleNamespace(type="response.output_text.delta"), completed))
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    wrapped = instrument_openai(client, witdem=SimpleNamespace(model=model))

    observed_stream = wrapped.responses.create(model="gpt-5.4", stream=True)
    assert lifecycle == ["entered"]
    assert list(observed_stream)[-1] is completed
    assert lifecycle == ["entered", "exited"]
    assert observed_usage[-1] == {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
    assert attributes["gen_ai.tool.call.id"] == "call_stream"


def test_openai_async_stream_stays_open_and_records_final_chat_chunk() -> None:
    from witdem_sdk.integrations.openai import instrument_openai

    final_chunk = SimpleNamespace(
        model="gpt-5.4-2026-03-05",
        usage=SimpleNamespace(prompt_tokens=6, completion_tokens=4, total_tokens=10),
        choices=[],
    )
    operation, _, _, observed_usage = _fake_operation()
    lifecycle: list[str] = []

    @contextmanager
    def model(*args: Any, **kwargs: Any):
        lifecycle.append("entered")
        try:
            yield operation
        finally:
            lifecycle.append("exited")

    class AsyncStream:
        def __init__(self) -> None:
            self._items = iter((SimpleNamespace(model="gpt-5.4", usage=None), final_chunk))
            self.closed = False

        def __aiter__(self) -> AsyncStream:
            return self

        async def __anext__(self) -> Any:
            try:
                return next(self._items)
            except StopIteration as error:
                raise StopAsyncIteration from error

        async def aclose(self) -> None:
            self.closed = True

    native_stream = AsyncStream()

    async def create(**kwargs: Any) -> AsyncStream:
        return native_stream

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    wrapped = instrument_openai(client, witdem=SimpleNamespace(model=model))

    async def consume() -> list[Any]:
        stream = await wrapped.chat.completions.create(
            model="gpt-5.4",
            stream=True,
            stream_options={"include_usage": True},
        )
        assert lifecycle == ["entered"]
        return [chunk async for chunk in stream]

    chunks = asyncio.run(consume())
    assert chunks[-1] is final_chunk
    assert native_stream.closed is True
    assert lifecycle == ["entered", "exited"]
    assert observed_usage[-1] == {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}


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


@pytest.mark.parametrize(
    ("span_kind", "family", "operation_type"),
    [
        ("HandoffSpanData", "agent_control", "handoff"),
        ("GuardrailSpanData", "quality", "guardrail"),
        ("TaskSpanData", "orchestration", "workflow"),
    ],
)
def test_openai_agents_control_spans_emit_canonical_semantics(
    span_kind: str, family: str, operation_type: str
) -> None:
    from witdem_sdk.integrations.openai_agents import WitdemTraceProcessor

    observed: dict[str, Any] = {}

    @contextmanager
    def operation(name: str, **kwargs: Any):
        observed.update({"name": name, **kwargs})
        yield SimpleNamespace()

    data = type(span_kind, (), {"name": operation_type})()
    manager = WitdemTraceProcessor(SimpleNamespace(operation=operation))._operation_manager(
        SimpleNamespace(span_data=data)
    )
    with manager:
        pass

    assert observed["family"] == family
    assert observed["operation_type"] == operation_type
    assert observed["interface"] == "framework"
    assert observed["framework_id"] == "openai_agents"


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
    assert spans["langchain.retriever"].attributes["witdem.operation.type"] == "retrieval"
    assert spans["langchain.retriever"].attributes["witdem.operation.interface"] == "library"
    assert spans["langchain.chat_model"].attributes["gen_ai.usage.input_tokens"] == 5
    assert spans["langchain.chat_model"].attributes["witdem.operation.type"] == "text_generation"
    assert spans["langchain.chat_model"].attributes["gen_ai.operation.name"] == "chat"
    assert spans["langchain.chat_model"].attributes["gen_ai.provider.name"] == "openai"
    assert spans["langchain.chat_model"].attributes["gen_ai.response.model"] == "model-a-snapshot"
    assert spans["langchain.tool.lookup"].attributes["witdem.operation.type"] == "tool_execution"


def test_langchain_callbacks_extract_native_gemini_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langchain_core")
    from witdem_sdk.integrations import langchain

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(langchain.trace, "get_tracer", provider.get_tracer)
    handler = langchain.WitdemCallbackHandler(
        SimpleNamespace(),
        provider="google",
        model="gemini-3.7-flash",
    )

    handler.on_chat_model_start({"kwargs": {"model": "gemini-3.7-flash"}}, [], run_id="gemini-model")
    native_usage = SimpleNamespace(
        prompt_token_count=11,
        candidates_token_count=7,
        total_token_count=21,
        cached_content_token_count=2,
        thoughts_token_count=3,
    )
    message = SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "model_name": "gemini-3.7-flash-001",
            "usage_metadata": native_usage,
        },
    )
    response = SimpleNamespace(llm_output={}, generations=[[SimpleNamespace(message=message)]])

    handler.on_llm_end(response, run_id="gemini-model")

    span = exporter.get_finished_spans()[0]
    assert span.attributes["gen_ai.usage.input_tokens"] == 11
    assert span.attributes["gen_ai.usage.output_tokens"] == 7
    assert span.attributes["gen_ai.usage.total_tokens"] == 21
    assert span.attributes["gen_ai.usage.cache_read.input_tokens"] == 2
    assert span.attributes["gen_ai.usage.reasoning.output_tokens"] == 3
    assert span.attributes["gen_ai.response.model"] == "gemini-3.7-flash-001"


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
    assert span.attributes["witdem.operation.type"] == "component"
    assert span.attributes["witdem.framework.id"] == "langgraph"
    assert span.attributes["witdem.execution.source"] == "langgraph"
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
