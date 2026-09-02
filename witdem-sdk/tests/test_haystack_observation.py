from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import pytest

from witdem_sdk.integrations import haystack as integration

haystack = pytest.importorskip("haystack")
Pipeline = haystack.Pipeline
component = haystack.component
ChatMessage = pytest.importorskip("haystack.dataclasses").ChatMessage
haystack_tracing = pytest.importorskip("haystack.tracing")


class _Span:
    def __init__(self, name: str, tags: Mapping[str, Any]) -> None:
        self.name = name
        self.attributes = dict(tags)
        self.content: list[tuple[str, Any]] = []

    def set_tag(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_tags(self, values: Mapping[str, Any]) -> None:
        self.attributes.update(values)

    def set_content_tag(self, key: str, value: Any) -> None:
        self.content.append((key, value))

    def raw_span(self) -> _Span:
        return self

    def get_correlation_data_for_logs(self) -> dict[str, Any]:
        return {}


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []

    @contextmanager
    def trace(
        self,
        operation_name: str,
        tags: dict[str, Any] | None = None,
        parent_span: Any = None,
    ) -> Iterator[_Span]:
        span = _Span(operation_name, tags or {})
        self.spans.append(span)
        yield span

    def current_span(self) -> None:
        return None


@component
class OpenAIGenerator:
    def __init__(self) -> None:
        self.model = "gpt-5.4-mini"

    @component.output_types(replies=list[ChatMessage])
    def run(self, prompt: str) -> dict[str, list[ChatMessage]]:
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text=f"OpenAI: {prompt}",
                    meta={
                        "model": self.model,
                        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                    },
                )
            ]
        }

    @component.output_types(replies=list[ChatMessage])
    async def run_async(self, prompt: str) -> dict[str, list[ChatMessage]]:
        await asyncio.sleep(0)
        return self.run(prompt)


@component
class AnthropicGenerator:
    def __init__(self) -> None:
        self.model = "claude-haiku-4-5-20251001"

    @component.output_types(replies=list[ChatMessage])
    def run(self, prompt: str) -> dict[str, list[ChatMessage]]:
        return {
            "replies": [
                ChatMessage.from_assistant(
                    text=f"Anthropic: {prompt}",
                    meta={
                        "model": self.model,
                        "usage": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
                    },
                )
            ]
        }

    @component.output_types(replies=list[ChatMessage])
    async def run_async(self, prompt: str) -> dict[str, list[ChatMessage]]:
        await asyncio.sleep(0)
        return self.run(prompt)


def _pipeline() -> Pipeline:
    pipeline = Pipeline()
    pipeline.add_component("openai", OpenAIGenerator())
    pipeline.add_component("anthropic", AnthropicGenerator())
    return pipeline


@pytest.mark.parametrize("asynchronous", [False, True])
def test_observes_each_model_in_sync_and_async_multi_provider_pipelines(asynchronous: bool) -> None:
    pipeline = _pipeline()
    by_component, identities = integration._configured_identities(pipeline)
    recorder = _Tracer()
    tracer = integration._ObservedTracer(
        recorder,
        by_component=by_component,
        identities=identities,
    )
    haystack_tracing.enable_tracing(tracer)
    try:
        inputs = {"openai": {"prompt": "one"}, "anthropic": {"prompt": "two"}}
        result = asyncio.run(pipeline.run_async(inputs)) if asynchronous else pipeline.run(inputs)
    finally:
        haystack_tracing.disable_tracing()

    assert set(result) == {"openai", "anthropic"}
    model_spans = [span for span in recorder.spans if "gen_ai.usage.total_tokens" in span.attributes]
    assert len(model_spans) == 2
    assert {span.attributes["gen_ai.provider.name"] for span in model_spans} == {
        "openai",
        "anthropic",
    }
    assert {span.attributes["gen_ai.response.model"] for span in model_spans} == {
        "gpt-5.4-mini",
        "claude-haiku-4-5-20251001",
    }
    assert sum(span.attributes["gen_ai.usage.total_tokens"] for span in model_spans) == 23
    assert {span.attributes["witdem.operation.type"] for span in model_spans} == {"text_generation"}
    assert {span.attributes["witdem.framework.id"] for span in model_spans} == {"haystack"}


def test_names_agent_steps_from_observed_tool_children_and_terminal_model_step() -> None:
    recorder = _Tracer()
    tracer = integration._ObservedTracer(recorder, by_component={}, identities=())

    with tracer.trace("haystack.agent.step", tags={"haystack.agent.step": 0}) as tool_step:
        with tracer.trace(
            "haystack.agent.step.llm",
            tags={},
            parent_span=tool_step,
        ):
            pass
        with tracer.trace(
            "haystack.agent.step.tool",
            tags={"haystack.tool.name": "list_metadata_fields"},
            parent_span=tool_step,
        ):
            pass

    with (
        tracer.trace("haystack.agent.step", tags={"haystack.agent.step": 1}) as final_step,
        tracer.trace(
            "haystack.agent.step.llm",
            tags={},
            parent_span=final_step,
        ),
    ):
        pass

    assert recorder.spans[0].attributes["witdem.agent.step.name"] == "list_metadata_fields"
    assert recorder.spans[0].attributes["witdem.agent.step.tools"] == ("list_metadata_fields",)
    assert recorder.spans[3].attributes["witdem.agent.step.name"] == "final_answer"
    assert recorder.spans[3].attributes["witdem.agent.step.action"] == "final_answer"
    tool_span = next(span for span in recorder.spans if span.name == "haystack.agent.step.tool")
    model_span = next(span for span in recorder.spans if span.name == "haystack.agent.step.llm")
    assert tool_span.attributes["witdem.operation.type"] == "tool_execution"
    assert model_span.attributes["witdem.operation.type"] == "text_generation"


def test_names_agent_steps_when_haystack_relies_on_the_active_parent_context() -> None:
    recorder = _Tracer()
    tracer = integration._ObservedTracer(recorder, by_component={}, identities=())

    with tracer.trace("haystack.agent.step", tags={"haystack.agent.step": 0}) as step:
        assert tracer.current_span() is step
        with tracer.trace("haystack.agent.step.llm", tags={}, parent_span=tracer.current_span()):
            pass
        with tracer.trace(
            "haystack.agent.step.tool",
            tags={"haystack.tool.name": "get_metadata_field_values"},
            parent_span=tracer.current_span(),
        ):
            pass

    assert recorder.spans[0].attributes["witdem.agent.step.name"] == "get_metadata_field_values"
    assert recorder.spans[0].attributes["witdem.agent.step.action"] == "tool_call"


def _branched_pipeline() -> Pipeline:
    @component
    class Dispatcher:
        @component.output_types(legal=list[str], finance=list[str], inactive=list[str])
        def run(self, value: str) -> dict[str, list[str]]:
            return {"legal": [value], "finance": [value]}

        @component.output_types(legal=list[str], finance=list[str], inactive=list[str])
        async def run_async(self, value: str) -> dict[str, list[str]]:
            await asyncio.sleep(0)
            return self.run(value)

    @component
    class Reviewer:
        @component.output_types(values=list[str])
        def run(self, values: list[str]) -> dict[str, list[str]]:
            return {"values": values}

        @component.output_types(values=list[str])
        async def run_async(self, values: list[str]) -> dict[str, list[str]]:
            await asyncio.sleep(0)
            return self.run(values)

    ListJoiner = pytest.importorskip("haystack.components.joiners").ListJoiner
    pipeline = Pipeline()
    pipeline.add_component("dispatcher", Dispatcher())
    pipeline.add_component("legal", Reviewer())
    pipeline.add_component("finance", Reviewer())
    pipeline.add_component("inactive", Reviewer())
    pipeline.add_component("joiner", ListJoiner(list[str]))
    pipeline.connect("dispatcher.legal", "legal.values")
    pipeline.connect("dispatcher.finance", "finance.values")
    pipeline.connect("dispatcher.inactive", "inactive.values")
    pipeline.connect("legal.values", "joiner.values")
    pipeline.connect("finance.values", "joiner.values")
    pipeline.connect("inactive.values", "joiner.values")
    return pipeline


@pytest.mark.parametrize("asynchronous", [False, True])
def test_exports_active_haystack_fanout_and_join_sockets_without_content(asynchronous: bool) -> None:
    pipeline = _branched_pipeline()
    topology = integration._pipeline_topology(pipeline)
    recorder = _Tracer()
    tracer = integration._ObservedTracer(
        recorder,
        by_component={},
        identities=(),
        topology=topology,
        capture_content=False,
    )
    haystack_tracing.enable_tracing(tracer)
    secret = "private contract content"
    try:
        inputs = {"dispatcher": {"value": secret}}
        result = asyncio.run(pipeline.run_async(inputs, concurrency_limit=4)) if asynchronous else pipeline.run(inputs)
    finally:
        haystack_tracing.disable_tracing()

    assert "joiner" in result
    component_spans = {
        str(span.attributes.get("haystack.component.name")): span
        for span in recorder.spans
        if span.name == "haystack.component.run"
    }
    assert set(component_spans) == {"dispatcher", "legal", "finance", "joiner"}
    emitted = component_spans["dispatcher"].attributes["witdem.haystack.runtime.emitted_sockets"]
    assert emitted == ("legal", "finance")
    assert "inactive" not in emitted
    outgoing = [
        json.loads(value)
        for value in component_spans["dispatcher"].attributes["witdem.haystack.topology.outgoing"]
    ]
    assert {(edge["source_socket"], edge["target_component"], edge["target_socket"]) for edge in outgoing} == {
        ("legal", "legal", "values"),
        ("finance", "finance", "values"),
        ("inactive", "inactive", "values"),
    }
    join_inputs = [
        json.loads(value)
        for value in component_spans["joiner"].attributes["witdem.haystack.topology.incoming"]
    ]
    assert {(edge["source_component"], edge["source_socket"]) for edge in join_inputs} == {
        ("legal", "values"),
        ("finance", "values"),
        ("inactive", "values"),
    }
    serialized_attributes = json.dumps([span.attributes for span in recorder.spans], default=str)
    assert secret not in serialized_attributes
    assert all(not span.content for span in recorder.spans)


def test_marks_haystack_feedback_socket_relationships() -> None:
    class Socket:
        def __init__(self, name: str) -> None:
            self.name = name

    class Graph:
        def nodes(self, data: bool = False) -> list[tuple[str, dict[str, Any]]]:
            return [("router", {"instance": object()}), ("worker", {"instance": object()})]

        def edges(self, data: bool = False) -> list[tuple[str, str, dict[str, Any]]]:
            return [
                ("router", "worker", {"from_socket": Socket("retry_context"), "to_socket": Socket("context")}),
                ("worker", "router", {"from_socket": Socket("context"), "to_socket": Socket("context")}),
            ]

        def successors(self, node: str) -> list[str]:
            return ["worker"] if node == "router" else ["router"]

    topology = integration._pipeline_topology(type("Pipeline", (), {"graph": Graph()})())
    edge = topology["router"]["outgoing"][0]

    assert edge["cycle"] is True
    assert edge["retry"] is True
    assert topology["worker"]["outgoing"][0]["retry"] is False
