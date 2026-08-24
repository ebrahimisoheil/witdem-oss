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
    handle = SimpleNamespace(disable=lambda: lifecycle.append("disable"))
    monkeypatch.setattr(
        haystack,
        "enable_haystack",
        lambda witdem, capture_content=False: lifecycle.append("enable") or handle,
    )
    pipeline = haystack.instrument(SimpleNamespace(run=lambda value: {"answer": value}), service_name="service")
    assert pipeline.run("done") == {"answer": "done"}
    assert lifecycle == ["enable", "disable"]


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
