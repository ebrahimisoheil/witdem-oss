from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

import witdem_sdk
import witdem_sdk._telemetry as telemetry
from witdem_sdk._config import records_endpoint


def test_configure_uses_one_endpoint_for_traces_and_records(monkeypatch: Any) -> None:
    captured: dict[str, str] = {}

    class CapturingExporter:
        def __init__(self, *, endpoint: str) -> None:
            captured["endpoint"] = endpoint

        def export(self, spans: Any) -> Any:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    monkeypatch.setenv("WITDEM_ENDPOINT", "http://witdem.example:9999")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", CapturingExporter)
    client = witdem_sdk.configure(service_name="example", runtime="anthropic")
    try:
        assert captured["endpoint"] == "http://witdem.example:9999/v1/traces"
    finally:
        client.shutdown()


def test_explicit_endpoint_applies_to_traces_and_records(monkeypatch: Any) -> None:
    captured: dict[str, str] = {}

    class CapturingExporter:
        def __init__(self, *, endpoint: str) -> None:
            captured["traces"] = endpoint

        def export(self, spans: Any) -> Any:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", CapturingExporter)
    client = witdem_sdk.configure(service_name="example", endpoint="http://explicit.example:4318/")
    try:
        assert captured["traces"] == "http://explicit.example:4318/v1/traces"
        assert records_endpoint() == "http://explicit.example:4318"
    finally:
        client.shutdown()


def test_execution_model_and_tool_emit_canonical_attributes(monkeypatch: Any) -> None:
    spans: list[Any] = []

    class CapturingProcessor(SpanProcessor):
        def on_start(self, span: Any, parent_context: Any = None) -> None:
            return None

        def on_end(self, span: Any) -> None:
            spans.append(span)

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    from opentelemetry.sdk.trace import TracerProvider

    client.service_name = "example"
    client.runtime = "anthropic"
    client._provider = TracerProvider()
    client._provider.add_span_processor(telemetry._ExecutionIdSpanProcessor())
    client._provider.add_span_processor(CapturingProcessor())
    client._tracer = client._provider.get_tracer("example")
    client._closed = False
    monkeypatch.setattr(witdem_sdk, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(witdem_sdk, "outcome", lambda *args, **kwargs: None)

    with client.execution(execution_id="exec-1"):
        with client.model("claude.messages", provider="anthropic", model="claude-haiku-4-5") as call:
            call.response_model("claude-haiku-4-5-20251001").usage(input_tokens=10, output_tokens=2)
        with client.tool("search", call_id="tool-1"):
            pass

    model = next(span for span in spans if span.name == "claude.messages")
    tool = next(span for span in spans if span.name == "tool.search")
    execution = next(span for span in spans if span.name == "example")
    assert execution.attributes["witdem.execution.name"] == "example"
    assert model.attributes["witdem.execution_id"] == "exec-1"
    assert model.attributes["gen_ai.provider.name"] == "anthropic"
    assert model.attributes["gen_ai.usage.input_tokens"] == 10
    assert tool.attributes["gen_ai.tool.name"] == "search"
    assert tool.attributes["gen_ai.cost.usd"] == 0.0


def test_anthropic_integration_captures_provider_tool_use_id() -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from witdem_sdk.integrations.anthropic import instrument_anthropic

    attributes: dict[str, Any] = {}
    operation = SimpleNamespace(
        span=SimpleNamespace(
            set_attribute=lambda key, value: attributes.__setitem__(key, value),
            record_exception=lambda error: None,
        ),
        response_model=lambda model: operation,
        usage=lambda **usage: operation,
    )

    @contextmanager
    def model(*args: Any, **kwargs: Any):
        yield operation

    response = SimpleNamespace(
        model="claude-haiku-4-5-20251001",
        usage=SimpleNamespace(input_tokens=4, output_tokens=2),
        content=[SimpleNamespace(type="tool_use", id="toolu_provider_123")],
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    wrapped = instrument_anthropic(client, witdem=SimpleNamespace(model=model))

    assert wrapped.messages.create(model="claude-haiku-4-5") is response
    assert attributes["gen_ai.tool.call.id"] == "toolu_provider_123"
    assert attributes["witdem.anthropic.tool_use.ids"] == ["toolu_provider_123"]


def test_closing_one_client_does_not_shutdown_the_reusable_global_provider(monkeypatch: Any) -> None:
    shutdown_calls = 0

    class Provider:
        def force_flush(self, timeout_millis: int) -> bool:
            return True

        def shutdown(self) -> None:
            nonlocal shutdown_calls
            shutdown_calls += 1

    client = witdem_sdk.Witdem.__new__(witdem_sdk.Witdem)
    client._closed = False
    client._provider = Provider()
    client._owns_provider = True
    client._endpoint_was_overridden = False
    client._previous_api_key = None
    monkeypatch.setattr(telemetry, "flush_records", lambda timeout: True)

    client.shutdown()

    assert shutdown_calls == 0
    assert client._closed is True


def test_equivalent_configure_is_idempotent_for_one_provider(monkeypatch: Any) -> None:
    provider = TracerProvider()
    exporters: list[str] = []

    class CapturingExporter:
        def __init__(self, *, endpoint: str) -> None:
            exporters.append(endpoint)

        def export(self, spans: Any) -> Any:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", CapturingExporter)
    first = witdem_sdk.configure(
        service_name="example",
        endpoint="http://witdem.example:4318",
        tracer_provider=provider,
    )
    second = witdem_sdk.configure(
        service_name="example",
        endpoint="http://witdem.example:4318",
        tracer_provider=provider,
    )
    try:
        assert exporters == ["http://witdem.example:4318/v1/traces"]
    finally:
        second.shutdown()
        first.shutdown()


def test_conflicting_repeated_configure_fails_instead_of_adding_an_exporter(monkeypatch: Any) -> None:
    provider = TracerProvider()

    class CapturingExporter:
        def __init__(self, *, endpoint: str) -> None:
            self.endpoint = endpoint

        def export(self, spans: Any) -> Any:
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    monkeypatch.setattr(telemetry, "OTLPSpanExporter", CapturingExporter)
    client = witdem_sdk.configure(
        service_name="example",
        endpoint="http://first.example:4318",
        tracer_provider=provider,
    )
    try:
        with pytest.raises(ValueError, match="already configured with a different"):
            witdem_sdk.configure(
                service_name="example",
                endpoint="http://second.example:4318",
                tracer_provider=provider,
            )
    finally:
        client.shutdown()


def test_disabled_mode_requires_explicit_execution_without_active_trace(monkeypatch: Any) -> None:
    client = witdem_sdk.configure("semantic-only", telemetry_mode="disabled")
    monkeypatch.setattr(witdem_sdk, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(witdem_sdk, "outcome", lambda *args, **kwargs: None)
    try:
        with pytest.raises(ValueError, match="requires execution_id"), client.execution():
            pass
        with client.execution(execution_id="explicit-1") as execution_id:
            assert execution_id == "explicit-1"
    finally:
        client.shutdown()


def test_existing_mode_does_not_install_processor_or_exporter(monkeypatch: Any) -> None:
    provider = TracerProvider()
    installed: list[Any] = []
    original = provider.add_span_processor

    def capture_processor(processor: Any) -> None:
        installed.append(processor)
        original(processor)

    monkeypatch.setattr(provider, "add_span_processor", capture_processor)

    client = witdem_sdk.configure("existing", telemetry_mode="existing", tracer_provider=provider)
    try:
        assert installed == []
    finally:
        client.shutdown()
