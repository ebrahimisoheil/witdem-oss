from __future__ import annotations

import asyncio
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

    def set_tag(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_tags(self, values: Mapping[str, Any]) -> None:
        self.attributes.update(values)

    def set_content_tag(self, key: str, value: Any) -> None:
        return None

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

    with tracer.trace("haystack.agent.step", tags={"haystack.agent.step": 1}) as final_step, tracer.trace(
        "haystack.agent.step.llm",
        tags={},
        parent_span=final_step,
    ):
        pass

    assert recorder.spans[0].attributes["witdem.agent.step.name"] == "list_metadata_fields"
    assert recorder.spans[0].attributes["witdem.agent.step.tools"] == ("list_metadata_fields",)
    assert recorder.spans[3].attributes["witdem.agent.step.name"] == "final_answer"
    assert recorder.spans[3].attributes["witdem.agent.step.action"] == "final_answer"


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
