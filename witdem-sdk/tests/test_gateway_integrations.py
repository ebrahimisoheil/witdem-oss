from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class _Response:
    def model_dump(self) -> dict[str, Any]:
        return {
            "model": "openai/gpt-5-mini",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
                "cost": 0.0012,
                "cost_details": {"upstream_inference_cost": 0.001},
                "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
            "openrouter_metadata": {
                "requested": "openrouter/auto",
                "strategy": "fallback",
                "attempt": 2,
                "is_byok": False,
                "endpoints": {
                    "available": [
                        {"provider": "First Provider", "selected": False},
                        {"provider": "OpenAI", "selected": True},
                    ]
                },
                "attempts": [
                    {"provider": "First Provider", "status": 429},
                    {"provider": "OpenAI", "status": 200},
                ],
            },
        }


def test_litellm_callback_emits_correlated_route_usage_and_authoritative_cost(monkeypatch: Any) -> None:
    from witdem_sdk.integrations import litellm as integration

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(integration.trace, "get_tracer", provider.get_tracer)
    callback = integration.WitdemLiteLLMCallback()
    kwargs = {
        "litellm_call_id": "call-1",
        "model": "openrouter/auto",
        "custom_llm_provider": "openrouter",
        "response_cost": 0.0012,
        "litellm_params": {
            "metadata": {
                "previous_models": [
                    {"model": "openrouter/first", "messages": "must not be recorded"},
                ]
            }
        },
    }
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("execution"):
        callback.log_pre_api_call("openrouter/auto", [], kwargs)
        callback.log_success_event(kwargs, _Response(), None, None)

    spans = {span.name: span for span in exporter.get_finished_spans()}
    model_span = spans["litellm.chat"]
    assert model_span.parent is not None
    assert model_span.attributes["gen_ai.provider.name"] == "openai"
    assert model_span.attributes["witdem.gateway.id"] == "openrouter"
    assert model_span.attributes["witdem.route.strategy"] == "fallback"
    assert model_span.attributes["witdem.route.attempt_count"] == 2
    assert model_span.attributes["witdem.retry.attempt"] == 2
    assert model_span.attributes["witdem.route.previous_models"] == ("openrouter/first",)
    assert model_span.attributes["gen_ai.usage.total_tokens"] == 16
    assert model_span.attributes["gen_ai.usage.cache_read.input_tokens"] == 3
    assert model_span.attributes["gen_ai.cost.usd"] == 0.0012
    assert model_span.attributes["gen_ai.cost.source"] == "openrouter_reported"


def test_litellm_ocr_reports_pages_without_inventing_tokens(monkeypatch: Any) -> None:
    from witdem_sdk.integrations import litellm as integration

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(integration.trace, "get_tracer", provider.get_tracer)
    callback = integration.WitdemLiteLLMCallback()
    kwargs = {
        "litellm_call_id": "ocr-call",
        "model": "provider/document-reader",
        "custom_llm_provider": "provider",
        "call_type": "ocr",
    }
    response = {
        "model": "document-reader",
        "usage_info": {"pages_processed": 3, "doc_size_bytes": 1200},
    }

    callback.log_pre_api_call("provider/document-reader", [], kwargs)
    callback.log_success_event(kwargs, response, None, None)

    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["witdem.operation.type"] == "ocr"
    assert attributes["gen_ai.usage.ocr_pages"] == 3
    assert attributes["gen_ai.usage.input_bytes"] == 1200
    assert "gen_ai.usage.total_tokens" not in attributes


def test_openrouter_observer_keeps_content_out_and_extracts_route_facts() -> None:
    from witdem_sdk.integrations.openrouter import observe_response

    observed = observe_response(_Response())

    assert observed["response_model"] == "openai/gpt-5-mini"
    assert observed["route_provider"] == "openai"
    assert observed["route_attempt"] == 2
    assert observed["cache_read_tokens"] == 3
    assert observed["reasoning_tokens"] == 1
    assert observed["cost_usd"] == 0.0012
    assert "choices" not in observed

    completed_event = SimpleNamespace(response=_Response())
    assert observe_response(completed_event)["cost_usd"] == 0.0012


def test_openrouter_client_proxy_requests_metadata_and_records_selected_provider() -> None:
    from witdem_sdk.integrations.openrouter import instrument_openrouter

    observed_kwargs: dict[str, Any] = {}

    def create(**kwargs: Any) -> _Response:
        observed_kwargs.update(kwargs)
        return _Response()

    operation = SimpleNamespace(
        span=SimpleNamespace(
            attributes={},
            set_attribute=lambda key, value: operation.span.attributes.__setitem__(key, value),
        ),
        response_model=lambda model: operation.span.attributes.__setitem__("response_model", model) or operation,
        usage=lambda **usage: operation.span.attributes.__setitem__("usage", usage) or operation,
        cost=lambda value, source: operation.span.attributes.__setitem__("cost", (value, source)) or operation,
    )

    class Context:
        def __enter__(self) -> Any:
            return operation

        def __exit__(self, *args: Any) -> None:
            return None

    witdem = SimpleNamespace(model=lambda *args, **kwargs: Context())
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    response = instrument_openrouter(client, witdem=witdem).chat.completions.create(model="openrouter/auto")

    assert isinstance(response, _Response)
    assert observed_kwargs["extra_headers"]["X-OpenRouter-Metadata"] == "enabled"
    assert operation.span.attributes["gen_ai.provider.name"] == "openai"
    assert operation.span.attributes["cost"] == (0.0012, "openrouter_reported")


def test_openrouter_client_proxy_keeps_sync_and_async_streams_open_until_usage_arrives() -> None:
    from witdem_sdk.integrations.openrouter import instrument_openrouter

    operations: list[Any] = []
    exits: list[tuple[Any, ...]] = []

    class Context:
        def __enter__(self) -> Any:
            span = SimpleNamespace(
                attributes={},
                set_attribute=lambda key, value: span.attributes.__setitem__(key, value),
                record_exception=lambda exc: None,
            )
            operation = SimpleNamespace(
                span=span,
                response_model=lambda model: span.attributes.__setitem__("response_model", model) or operation,
                usage=lambda **usage: span.attributes.__setitem__("usage", usage) or operation,
                cost=lambda value, source: span.attributes.__setitem__("cost", (value, source)) or operation,
            )
            operations.append(operation)
            return operation

        def __exit__(self, *args: Any) -> None:
            exits.append(args)

    witdem = SimpleNamespace(model=lambda *args, **kwargs: Context())
    sync_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: iter((SimpleNamespace(), _Response())))
        )
    )

    stream = instrument_openrouter(sync_client, witdem=witdem).chat.completions.create(
        model="openrouter/auto", stream=True
    )
    assert len(exits) == 0
    assert list(stream)[-1].model_dump()["usage"]["total_tokens"] == 16
    assert len(exits) == 1
    assert operations[0].span.attributes["usage"]["total_tokens"] == 16

    class AsyncStream:
        def __init__(self) -> None:
            self._values = iter((SimpleNamespace(), _Response()))

        def __aiter__(self) -> AsyncStream:
            return self

        async def __anext__(self) -> Any:
            try:
                return next(self._values)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    async def create(**kwargs: Any) -> AsyncStream:
        return AsyncStream()

    async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def collect() -> list[Any]:
        observed = await instrument_openrouter(async_client, witdem=witdem).chat.completions.create(
            model="openrouter/auto", stream=True
        )
        return [item async for item in observed]

    assert asyncio.run(collect())[-1].model_dump()["usage"]["total_tokens"] == 16
    assert len(exits) == 2
    assert operations[1].span.attributes["cost"] == (0.0012, "openrouter_reported")


def test_smolagents_proxy_preserves_streaming_and_reports_the_final_value(monkeypatch: Any) -> None:
    from witdem_sdk.integrations import smolagents

    lifecycle: list[str] = []
    reports: list[Any] = []

    class Invocation:
        def __enter__(self) -> Any:
            lifecycle.append("enter")
            return "witdem"

        def __exit__(self, *args: Any) -> None:
            lifecycle.append("exit")

    integration_settings = SimpleNamespace(
        invocation=lambda: Invocation(),
        report=lambda result, witdem: reports.append((result, witdem)),
    )
    monkeypatch.setattr(smolagents, "enable_smolagents", lambda: lifecycle.append("enable"))
    agent = SimpleNamespace(run=lambda *args, **kwargs: iter(("step", "final")))
    observed = smolagents.InstrumentedSmolagent(
        agent,
        service_name=None,
        execution_name=None,
        endpoint=None,
        config_path=None,
        attributes=None,
        report_result=None,
    )
    observed._settings = integration_settings

    assert list(observed.run("task", stream=True)) == ["step", "final"]
    assert reports == [("final", "witdem")]
    assert lifecycle == ["enter", "enable", "exit"]
