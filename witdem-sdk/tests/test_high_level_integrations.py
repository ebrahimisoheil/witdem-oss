from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from witdem_sdk.integrations import _common


class _FakeOperation:
    def __init__(self) -> None:
        self.observed: dict[str, Any] = {}
        self.span = SimpleNamespace(set_attribute=lambda key, value: self.observed.__setitem__(key, value))

    def response_model(self, value: str | None) -> _FakeOperation:
        self.observed["response_model"] = value
        return self

    def usage(self, **values: Any) -> _FakeOperation:
        self.observed["usage"] = values
        return self

    def cost(self, value: float, *, source: str = "provider_reported") -> _FakeOperation:
        self.observed["cost"] = (value, source)
        return self


class _FakeWitdem:
    def __init__(self) -> None:
        self.executions = 0
        self.reports: list[dict[str, Any]] = []
        self.operations: list[_FakeOperation] = []
        self.events: list[tuple[str, Any]] = []
        self.completed: list[tuple[Any, str | None]] = []
        self.project_config: Any = None

    def __enter__(self) -> _FakeWitdem:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    @contextmanager
    def execution(self, name: str | None, *, attributes: Mapping[str, Any]) -> Iterator[str]:
        self.executions += 1
        yield "execution-id"

    @contextmanager
    def model(self, *args: Any, **kwargs: Any) -> Iterator[_FakeOperation]:
        operation = _FakeOperation()
        self.operations.append(operation)
        yield operation

    @contextmanager
    def operation(self, *args: Any, attributes: Mapping[str, Any], **kwargs: Any) -> Iterator[_FakeOperation]:
        operation = _FakeOperation()
        operation.observed.update(attributes)
        self.operations.append(operation)
        yield operation

    def report(self, **values: Any) -> None:
        self.reports.append(values)

    def event(self, name: str, value: Any, **kwargs: Any) -> None:
        self.events.append((name, value))

    def flush(self) -> bool:
        return True

    def complete(self, result: Any, *, contract: str | None = None) -> None:
        self.completed.append((result, contract))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _FakeWitdem:
    value = _FakeWitdem()
    monkeypatch.setattr(_common, "configure", lambda *args, **kwargs: value)
    return value


def test_generic_instrument_records_provider_result_and_business_result(client: _FakeWitdem) -> None:
    from witdem_sdk.integrations.generic import instrument

    observed = instrument(
        lambda: SimpleNamespace(answer="done", model="model-v2", tokens=7),
        operation_name="provider.generate",
        provider="provider",
        model="model-v1",
        observe_result=lambda result: {"response_model": result.model, "total_tokens": result.tokens},
        report_result=lambda result: {"result": result.answer, "product_goal_achieved": True},
        service_name="service",
    )

    assert observed().answer == "done"
    assert client.executions == 1
    assert client.operations[0].observed == {
        "response_model": "model-v2",
        "usage": {"total_tokens": 7},
    }
    assert client.reports == [{"result": "done", "product_goal_achieved": True}]
    assert (
        instrument(
            observed,
            operation_name="ignored",
            provider="ignored",
            model="ignored",
        )
        is observed
    )


def test_instrument_automatically_completes_a_yaml_selector_contract(client: _FakeWitdem) -> None:
    from witdem_sdk.integrations.generic import instrument

    client.project_config = SimpleNamespace(
        default_contract="answer",
        contracts={"answer": object()},
    )
    observed = instrument(
        lambda: {"answer": "done"},
        operation_name="provider.generate",
        provider="provider",
        model="model",
        service_name="service",
    )

    result = observed()

    assert client.completed == [(result, "answer")]


def test_anthropic_instrument_injects_one_client_for_the_whole_workload(client: _FakeWitdem) -> None:
    from witdem_sdk.integrations.anthropic import instrument

    response = SimpleNamespace(
        model="claude-snapshot",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        content=[],
    )
    anthropic = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))

    def workload(observed_client: Any) -> str:
        observed_client.messages.create(model="claude")
        observed_client.messages.create(model="claude")
        return "done"

    observed = instrument(workload, client=anthropic, service_name="service")

    assert observed() == "done"
    assert client.executions == 1
    assert len(client.operations) == 2


def test_openai_instrument_injects_one_client_for_the_whole_workload(client: _FakeWitdem) -> None:
    from witdem_sdk.integrations.openai import instrument

    response = SimpleNamespace(
        model="gpt-5.4-2026-03-05",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
        output=[],
    )
    openai = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))

    def workload(observed_client: Any) -> str:
        observed_client.responses.create(model="gpt-5.4")
        observed_client.responses.create(model="gpt-5.4")
        return "done"

    observed = instrument(workload, client=openai, service_name="service")

    assert observed() == "done"
    assert client.executions == 1
    assert len(client.operations) == 2


def test_openai_agents_instrument_owns_registration(client: _FakeWitdem, monkeypatch: pytest.MonkeyPatch) -> None:
    from witdem_sdk.integrations import openai_agents

    lifecycle: list[str] = []
    handle = SimpleNamespace(uninstall=lambda: lifecycle.append("uninstall"))
    monkeypatch.setattr(
        openai_agents,
        "install_openai_agents",
        lambda witdem, capture_content=False: lifecycle.append("install") or handle,
    )
    observed = openai_agents.instrument(
        lambda: "done",
        service_name="service",
        report_result=lambda result: {"result": result, "product_goal_achieved": True},
    )

    assert observed() == "done"
    assert lifecycle == ["install", "uninstall"]
    assert client.reports[0]["result"] == "done"


def test_langchain_and_haystack_proxies_preserve_native_invocation(
    client: _FakeWitdem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langchain_core")
    from witdem_sdk.integrations import haystack, langchain

    runnable = SimpleNamespace(invoke=lambda value, config=None: (value, config))
    chain = langchain.instrument(runnable, service_name="service")
    value, config = chain.invoke("input")
    assert value == "input"
    assert isinstance(config["callbacks"][0], langchain.WitdemCallbackHandler)

    lifecycle: list[str] = []
    handle = SimpleNamespace(disable=lambda: lifecycle.append("disable"), usage_observations=0)
    monkeypatch.setattr(
        haystack,
        "enable_haystack",
        lambda witdem, capture_content=False, pipeline=None: lifecycle.append("enable") or handle,
    )
    pipeline = haystack.instrument(SimpleNamespace(run=lambda value: {"answer": value}), service_name="service")
    assert pipeline.run("done") == {"answer": "done"}
    assert lifecycle == ["enable", "disable"]


def test_haystack_exposes_final_message_text_to_yaml_contracts(
    client: _FakeWitdem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from witdem_sdk.integrations import haystack

    monkeypatch.setattr(haystack, "_require_supported_haystack", lambda: None)

    class Message:
        text = "grounded answer"

        def to_dict(self) -> dict[str, Any]:
            return {"content": [{"reasoning": "hidden"}, {"text": self.text}]}

    client.project_config = SimpleNamespace(
        default_contract="answer",
        contracts={"answer": object()},
    )
    monkeypatch.setattr(
        haystack,
        "enable_haystack",
        lambda witdem, capture_content=False, pipeline=None: SimpleNamespace(
            disable=lambda: None, usage_observations=0
        ),
    )
    native_result = {"last_message": Message(), "token_usage": {"total_tokens": 7}}
    observed = haystack.instrument(SimpleNamespace(run=lambda: native_result), service_name="service")

    assert observed.run() is native_result
    reported, contract = client.completed[0]
    assert contract == "answer"
    assert reported["last_message"]["text"] == "grounded answer"
    assert reported["last_message"]["content"][0] == {"reasoning": "hidden"}
    assert reported["token_usage"] == {"total_tokens": 7}


def test_haystack_records_agent_usage_and_generator_identity(
    client: _FakeWitdem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from witdem_sdk.integrations import haystack

    monkeypatch.setattr(haystack, "_require_supported_haystack", lambda: None)

    class OpenAIResponsesChatGenerator:
        def to_dict(self) -> dict[str, Any]:
            return {
                "type": "haystack.components.generators.chat.openai_responses.OpenAIResponsesChatGenerator",
                "init_parameters": {"model": "gpt-5.4"},
            }

    pipeline = SimpleNamespace(
        chat_generator=OpenAIResponsesChatGenerator(),
        run=lambda: {"token_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}},
    )
    monkeypatch.setattr(
        haystack,
        "enable_haystack",
        lambda witdem, capture_content=False, pipeline=None: SimpleNamespace(
            disable=lambda: None, usage_observations=0
        ),
    )

    haystack.instrument(pipeline, service_name="service").run()

    assert client.operations[0].observed == {
        "witdem.haystack.usage_summary": True,
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-5.4",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 20,
        "gen_ai.usage.total_tokens": 120,
        "pf.provider_provenance": "observed_invocation_configuration",
        "pf.model_provenance": "observed_invocation_configuration",
        "pf.usage_provenance": "observed_provider_response",
    }


def test_haystack_observes_each_native_llm_response_without_capturing_content() -> None:
    from witdem_sdk.integrations import haystack

    class Span:
        def __init__(self) -> None:
            self.attributes: dict[str, Any] = {}
            self.content: list[tuple[str, Any]] = []

        def set_tag(self, key: str, value: Any) -> None:
            self.attributes[key] = value

        def set_tags(self, values: Mapping[str, Any]) -> None:
            self.attributes.update(values)

        def set_content_tag(self, key: str, value: Any) -> None:
            self.content.append((key, value))

        def raw_span(self) -> Span:
            return self

        def get_correlation_data_for_logs(self) -> dict[str, Any]:
            return {}

    class Tracer:
        def __init__(self) -> None:
            self.span = Span()

        @contextmanager
        def trace(self, *args: Any, **kwargs: Any) -> Iterator[Span]:
            yield self.span

        def current_span(self) -> None:
            return None

    base = Tracer()
    tracer = haystack._ObservedTracer(
        base,
        by_component={},
        identities=(("openai", "gpt-5.4"),),
    )
    response = {
        "replies": [
            SimpleNamespace(
                meta={
                    "model": "gpt-5.4-2026-03-05",
                    "usage": {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
                }
            )
        ]
    }

    with tracer.trace("haystack.agent.step.llm") as span:
        span.set_content_tag("haystack.agent.step.llm.output", response)

    assert tracer.usage_observations == 1
    assert base.span.attributes["gen_ai.provider.name"] == "openai"
    assert base.span.attributes["gen_ai.response.model"] == "gpt-5.4-2026-03-05"
    assert base.span.attributes["gen_ai.usage.total_tokens"] == 35


def test_haystack_version_gate_has_an_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from witdem_sdk.integrations import haystack

    monkeypatch.setattr(haystack, "package_version", lambda package: "2.31.0")
    with pytest.raises(RuntimeError, match=r"requires haystack-ai>=3\.0,<4; found 2\.31\.0"):
        haystack._require_supported_haystack()
    with pytest.raises(RuntimeError, match=r"requires haystack-ai>=3\.0,<4; found 2\.31\.0"):
        haystack.instrument(SimpleNamespace(run=lambda: None))

    monkeypatch.setattr(haystack, "package_version", lambda package: "3.0.0")
    haystack._require_supported_haystack()


def test_haystack_async_generator_preserves_native_stream_and_reports_final_result(
    client: _FakeWitdem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from witdem_sdk.integrations import haystack

    monkeypatch.setattr(haystack, "_require_supported_haystack", lambda: None)

    client.project_config = SimpleNamespace(default_contract="answer", contracts={"answer": object()})
    lifecycle: list[str] = []
    monkeypatch.setattr(
        haystack,
        "enable_haystack",
        lambda witdem, capture_content=False, pipeline=None: SimpleNamespace(
            disable=lambda: lifecycle.append("disable"), usage_observations=1
        ),
    )

    class Pipeline:
        async def run_async_generator(self) -> AsyncIterator[dict[str, Any]]:
            yield {"retriever": {"documents": ["evidence"]}}
            yield {"answer": {"text": "done"}}

    async def collect() -> list[dict[str, Any]]:
        observed = haystack.instrument(Pipeline(), service_name="service")
        return [item async for item in observed.run_async_generator()]

    outputs = asyncio.run(collect())

    assert outputs[-1] == {"answer": {"text": "done"}}
    assert client.completed == [(outputs[-1], "answer")]
    assert lifecycle == ["disable"]


def test_claude_agent_instrument_observes_an_async_stream(client: _FakeWitdem) -> None:
    from witdem_sdk.integrations.claude_agent import instrument

    class ResultMessage:
        model_usage = {"claude-snapshot": {"inputTokens": 2, "outputTokens": 1}}

    async def messages() -> AsyncIterator[Any]:
        yield ResultMessage()

    async def collect() -> list[Any]:
        return [
            message
            async for message in instrument(
                messages(),
                model="claude",
                service_name="service",
                report_result=lambda result: {"result": "done", "product_goal_achieved": True},
            )
        ]

    assert len(asyncio.run(collect())) == 1
    assert client.operations[0].observed["usage"]["total_tokens"] == 3
    assert client.reports[0]["result"] == "done"
