from __future__ import annotations

import pytest

from witdem.analytics.cost import estimate_chat_cost, resolve_pricing_model
from witdem.ingest.correlate import _configured_runtime
from witdem.integrations.adapters.claude import ClaudeAdapter
from witdem.integrations.adapters.langchain import LangChainAdapter
from witdem.integrations.adapters.langgraph import LangGraphAdapter
from witdem.integrations.adapters.openai_agents import OpenAIAgentsAdapter
from witdem.integrations.mapping import graph_from_spans
from witdem.integrations.normalizers.genai import GenAIDialectNormalizer
from witdem.integrations.normalizers.openinference import OpenInferenceNormalizer
from witdem.integrations.normalizers.otel import OTelEnvelopeNormalizer


def _span(span_id: str, name: str, *, parent: str | None = None, attributes: dict | None = None) -> dict:
    return {
        "trace_id": "trace-1",
        "span_id": span_id,
        "parent_span_id": parent,
        "name": name,
        "start_time_unix_nano": 1_700_000_000_000_000_000,
        "end_time_unix_nano": 1_700_000_000_100_000_000,
        "status": {"status_code": "OK"},
        "attributes": attributes or {},
        "events": [],
        "links": [],
        "resource": {"service.name": "fixture"},
        "instrumentation_scope": {"name": "fixture", "version": "1"},
    }


def test_otel_normalizer_extracts_envelope_and_rejects_malformed_span() -> None:
    normalized = OTelEnvelopeNormalizer().normalize(_span("s1", "root"))
    assert normalized.trace_id == "trace-1"
    assert normalized.span_id == "s1"
    assert normalized.status == "ok"
    assert normalized.duration_seconds == pytest.approx(0.1)
    assert normalized.resource["service.name"] == "fixture"
    with pytest.raises(ValueError, match="span_id or trace_id"):
        OTelEnvelopeNormalizer().normalize({"name": "missing identity"})


def test_otel_normalizer_keeps_unknown_attributes_and_hides_content_by_default() -> None:
    normalized = OTelEnvelopeNormalizer().normalize(
        _span("s1", "model", attributes={"unknown.future.key": "kept", "gen_ai.prompt": "secret"})
    )
    assert normalized.attributes == {"unknown.future.key": "kept"}


def test_genai_normalizer_maps_provider_models_tokens_and_tool_identity() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "s1",
            "execute_tool",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-test",
                "gen_ai.usage.input_tokens": 4,
                "gen_ai.usage.output_tokens": 3,
                "gen_ai.usage.cache_read.input_tokens": 2,
                "gen_ai.usage.audio.input_tokens": 11,
                "gen_ai.usage.vendor.future_meter": 4.5,
                "gen_ai.tool.name": "search",
                "gen_ai.tool.call.id": "call-1",
            },
        )
    )
    operation = GenAIDialectNormalizer().normalize(span)
    assert operation.kind == "tool"
    assert operation.provider == "openai"
    assert operation.request_model == "gpt-test"
    assert operation.usage["total_tokens"] == 7
    assert operation.usage["audio_input_tokens"] == 11
    assert operation.usage["vendor_future_meter"] == 4.5
    assert operation.tool_call_id == "call-1"


def test_genai_explicit_chat_beats_framework_name_containing_agent() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "s1",
            "openai_agents.model",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.response.model": "gpt-test",
            },
        )
    )
    assert GenAIDialectNormalizer().normalize(span).kind == "model"


def test_genai_normalizer_prefers_routed_provider_and_gateway_usage_aliases() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "route",
            "openrouter.chat",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openrouter",
                "witdem.gateway.name": "openrouter",
                "witdem.route.provider": "together",
                "openrouter.request.model": "openrouter/auto",
                "openrouter.response.model": "meta-llama/llama-4",
                "openrouter.usage.prompt_tokens": 8,
                "openrouter.usage.completion_tokens": 3,
                "openrouter.usage.cost": 0.0007,
            },
        )
    )

    normalized = GenAIDialectNormalizer().normalize(span)
    operation = graph_from_spans(
        [span], execution_id="execution", runtime="openrouter", telemetry_path="otel"
    ).operations[0]

    assert normalized.provider == "together"
    assert normalized.request_model == "openrouter/auto"
    assert normalized.response_model == "meta-llama/llama-4"
    assert normalized.usage["total_tokens"] == 11
    assert operation.attributes["cost_usd"] == 0.0007
    assert operation.attributes["cost_source"] == "openrouter_reported"


def test_shared_mapping_estimates_anthropic_cost_from_observed_usage() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "model",
            "claude.messages",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.response.model": "claude-haiku-4-5-20251001",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
            },
        )
    )
    operation = graph_from_spans(
        [span], execution_id="execution", runtime="anthropic", telemetry_path="otel"
    ).operations[0]
    assert operation.kind == "model"
    assert operation.attributes["provider"] == "anthropic"
    assert operation.attributes["model"] == "claude-haiku-4-5-20251001"
    assert operation.attributes["total_tokens"] == 120
    assert operation.attributes["cost_usd"] == pytest.approx(0.0002)
    assert operation.attributes["cost_source"] == "provider_price_snapshot"


def test_shared_mapping_preserves_explicit_zero_tool_cost() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "tool",
            "tool.lookup_weather",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "lookup_weather",
                "gen_ai.cost.usd": 0.0,
            },
        )
    )
    operation = graph_from_spans(
        [span], execution_id="execution", runtime="anthropic", telemetry_path="otel"
    ).operations[0]
    assert operation.kind == "tool"
    assert operation.attributes["cost_usd"] == 0.0


def test_shared_mapping_preserves_provider_reported_cost_provenance() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "model",
            "provider.model",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.response.model": "provider-model",
                "gen_ai.cost.usd": 0.0123,
                "gen_ai.cost.source": "provider_reported",
            },
        )
    )
    operation = graph_from_spans(
        [span], execution_id="execution", runtime="provider", telemetry_path="sdk"
    ).operations[0]
    assert operation.attributes["cost_usd"] == pytest.approx(0.0123)
    assert operation.attributes["cost_source"] == "provider_reported"


def test_shared_mapping_preserves_the_root_execution_name() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "root",
            "Chinook support · LangGraph · acct-e1",
            attributes={
                "witdem.execution.name": "Chinook support · LangGraph · acct-e1",
                "witdem.runtime.name": "langgraph",
            },
        )
    )

    graph = graph_from_spans(
        [span], execution_id="chinook-run", runtime="langgraph", telemetry_path="otel"
    )

    assert graph.execution.attributes["witdem.execution.name"] == (
        "Chinook support · LangGraph · acct-e1"
    )


def test_shared_mapping_prices_dated_gpt_4o_mini_response_model() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "model",
            "langchain.chat",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.response.model": "gpt-4o-mini-2024-07-18",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
            },
        )
    )
    operation = graph_from_spans(
        [span], execution_id="execution", runtime="langchain", telemetry_path="otel"
    ).operations[0]
    assert operation.attributes["cost_usd"] == pytest.approx(0.000027)
    assert operation.attributes["cost_model_match"] == "explicit_alias"


@pytest.mark.parametrize(
    ("provider", "model", "canonical"),
    [
        ("openai", "gpt-5.4-mini-2026-03-17", "gpt-5.4-mini"),
        ("openai", "openai/gpt-5.4-mini", "gpt-5.4-mini"),
        ("openai", "gpt-5.4-2026-03-05", "gpt-5.4"),
        ("anthropic", "claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("anthropic", "claude-sonnet-5", "claude-sonnet-5"),
        ("anthropic", "claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("openai", "gpt-5.6-sol", "gpt-5.6-sol"),
        ("openai", "gpt-5.6-terra", "gpt-5.6-terra"),
        ("openai", "gpt-5.6-luna", "gpt-5.6-luna"),
        ("deepseek", "deepseek-v4-flash", "deepseek-v4-flash"),
        ("mistral", "mistral-small-2603", "mistral-small-2603"),
    ],
)
def test_product_factory_models_have_exact_pricing(provider: str, model: str, canonical: str) -> None:
    resolution = resolve_pricing_model(provider, model)
    assert resolution.pricing_model == canonical
    assert estimate_chat_cost(provider, model, {"input_tokens": 100, "output_tokens": 20}) is not None


def test_unknown_model_keeps_cost_unavailable_with_a_specific_reason() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "model",
            "unknown.model",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.response.model": "future-model-without-price",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
            },
        )
    )
    operation = graph_from_spans([span], execution_id="execution", runtime="test", telemetry_path="otel").operations[0]
    assert operation.attributes.get("cost_usd") is None
    assert operation.attributes["cost_unavailable_reason"] == "unknown_model"


def test_configured_runtime_comes_from_otel_resource() -> None:
    assert _configured_runtime([{"resource": {"witdem.runtime": "anthropic/tool_loop"}}]) == "anthropic/tool_loop"


def test_openinference_normalizer_maps_retriever_and_token_details() -> None:
    span = OTelEnvelopeNormalizer().normalize(
        _span(
            "s1",
            "retrieve",
            attributes={
                "openinference.span.kind": "RETRIEVER",
                "retriever.name": "docs",
                "llm.provider": "provider",
                "llm.token_count.prompt": 5,
                "llm.token_count.completion": 2,
            },
        )
    )
    operation = OpenInferenceNormalizer().normalize(span)
    assert operation.kind == "component"
    assert operation.name == "docs"
    assert operation.usage["total_tokens"] == 7


def test_langchain_callbacks_pair_runs_and_preserve_parent_ids_without_content() -> None:
    adapter = LangChainAdapter()
    records = [
        {"run_id": "root", "event": "on_chain_start", "name": "pipeline", "trace_id": "lc-trace"},
        {
            "run_id": "model",
            "parent_run_id": "root",
            "parent_ids": ["root"],
            "event": "on_chat_model_end",
            "name": "chat",
            "metadata": {"tier": "fast"},
            "data": {"secret": "must not be copied"},
            "error": None,
        },
    ]
    graph = adapter.normalize(records)
    assert graph.execution.execution_id == "lc-trace"
    assert {operation.kind for operation in graph.operations} == {"component", "model"}
    assert any(link.source_id == "root" and link.target_id == "model" for link in graph.links)
    model = next(operation for operation in graph.operations if operation.operation_id == "model")
    assert "secret" not in str(model.attributes)
    assert model.attributes["langchain.metadata"] == {"tier": "fast"}


def test_langgraph_maps_only_executed_nodes_and_explicit_fanout() -> None:
    adapter = LangGraphAdapter()
    records = [
        {
            "run_id": "node-a",
            "event": "on_chain_end",
            "name": "node_a",
            "trace_id": "graph-trace",
            "metadata": {"langgraph_node": "node_a", "langgraph_step": 1},
        },
        {
            "run_id": "node-b",
            "event": "on_chain_end",
            "name": "node_b",
            "trace_id": "graph-trace",
            "parent_ids": ["node-a"],
            "langgraph_node": "node_b",
        },
        {
            "run_id": "node-c",
            "event": "on_chain_end",
            "name": "node_c",
            "trace_id": "graph-trace",
            "send_to": ["node-b", "node-c"],
        },
        {
            "run_id": "nested-model",
            "event": "on_chat_model_end",
            "name": "chat",
            "trace_id": "graph-trace",
            "parent_ids": ["node-a"],
            "metadata": {"langgraph_node": "node_a"},
        },
    ]
    graph = adapter.normalize(records)
    assert len(graph.operations) == 4
    assert any(operation.attributes.get("langgraph_node") == "node_a" for operation in graph.operations)
    assert any(operation.kind == "model" for operation in graph.operations)
    assert any(link.relation == "send_fanout" for link in graph.links)


def test_openai_native_adapter_maps_typed_spans_and_handoff() -> None:
    records = [
        {
            "trace_id": "oa-trace",
            "span_id": "agent-a",
            "span_type": "AgentSpanData",
            "name": "triage",
        },
        {
            "trace_id": "oa-trace",
            "span_id": "generation",
            "parent_id": "agent-a",
            "span_type": "GenerationSpanData",
            "data": {"model": "gpt-test", "usage": {"input_tokens": 4, "output_tokens": 2}},
        },
        {
            "trace_id": "oa-trace",
            "span_id": "response",
            "parent_id": "agent-a",
            "span_type": "response",
            "data": {"usage": {"input_tokens": 3, "output_tokens": 1}},
        },
        {
            "trace_id": "oa-trace",
            "span_id": "handoff",
            "parent_id": "agent-a",
            "span_type": "HandoffSpanData",
            "data": {"from_agent": "triage", "to_agent": "specialist"},
        },
        {
            "trace_id": "oa-trace",
            "span_id": "agent-b",
            "span_type": "AgentSpanData",
            "name": "specialist",
        },
    ]
    graph = OpenAIAgentsAdapter().normalize(records)
    assert {operation.kind for operation in graph.operations} >= {"agent", "model", "other"}
    assert any(link.relation == "handoff" and link.source_id == "agent-a" for link in graph.links)


def test_claude_adapter_exposes_capability_diagnostics_and_runtime_status() -> None:
    spans = [
        _span("interaction", "claude_code.interaction", attributes={"claude_code.session_id": "session-1"}),
        _span(
            "llm",
            "claude_code.llm_request",
            parent="interaction",
            attributes={
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-test",
                "claude_code.success": False,
                "claude_code.error": "provider failed",
                "claude_code.input_tokens": 10,
            },
        ),
        _span("tool", "claude_code.tool", parent="interaction", attributes={"claude_code.tool_name": "search"}),
    ]
    graph = ClaudeAdapter().normalize(spans)
    model = next(operation for operation in graph.operations if operation.operation_id == "llm")
    assert model.kind == "model"
    assert model.status == "error"
    assert graph.execution.attributes["claude.capabilities"]["interaction_seen"] is True


def test_claude_adapter_maps_current_unprefixed_usage_dialect() -> None:
    spans = [
        _span("interaction", "claude_code.interaction"),
        _span(
            "llm",
            "claude_code.llm_request",
            parent="interaction",
            attributes={
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-haiku-4-5-20251001",
                "success": True,
                "input_tokens": 546,
                "output_tokens": 14,
            },
        ),
    ]
    graph = ClaudeAdapter().normalize(spans)
    model = next(operation for operation in graph.operations if operation.operation_id == "llm")
    assert model.status == "ok"
    assert model.attributes["input_tokens"] == 546
    assert model.attributes["output_tokens"] == 14
    assert model.attributes["total_tokens"] == 560


def test_claude_adapter_preserves_explicit_sdk_model_and_tool_kinds() -> None:
    spans = [
        _span(
            "model",
            "claude_agent.model",
            attributes={
                "witdem.operation.kind": "model",
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": "claude-test",
            },
        ),
        _span(
            "tool",
            "tool.get_invoices",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "get_invoices",
            },
        ),
    ]
    graph = ClaudeAdapter().normalize(spans)
    assert {operation.operation_id: operation.kind for operation in graph.operations} == {
        "model": "model",
        "tool": "tool",
    }


def test_graph_mapping_preserves_explicit_otel_links_without_reparenting() -> None:
    rows = [_span("child", "child", attributes={"links": []})]
    rows[0]["links"] = [{"trace_id": "other", "span_id": "external", "attributes": {"reason": "fanout"}}]
    normalized = OTelEnvelopeNormalizer().normalize_many(rows)
    graph = graph_from_spans(normalized, execution_id="trace-1", runtime="fixture", telemetry_path="otel")
    assert any(link.relation == "otel_link" and link.source_id == "external" for link in graph.links)
