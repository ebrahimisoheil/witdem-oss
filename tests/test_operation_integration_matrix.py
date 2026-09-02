from __future__ import annotations

import json
from typing import Any

import pytest

from witdem.elt.adapter_stage import transform_bundle


def _classify(name: str, attributes: dict[str, Any], *, scope: str = "fixture") -> dict[str, Any]:
    span = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "name": name,
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {"witdem.execution_id": "matrix-run", **attributes},
        "instrumentation_scope": {"name": scope, "version": "1"},
    }
    result = transform_bundle({"execution_id": "matrix-run", "spans_json": json.dumps([span])})
    return json.loads(result["operation_classifications_json"])[0]


@pytest.mark.parametrize(
    ("path", "name", "scope", "attributes", "expected"),
    [
        (
            "direct custom Python",
            "retrieve_contracts",
            "application",
            {
                "witdem.operation.family": "knowledge",
                "witdem.operation.type": "retrieval",
                "witdem.operation.subtype": "hybrid_search",
                "witdem.operation.interface": "mcp",
                "gen_ai.provider.name": "contract-server",
                "witdem.implementation.id": "lancedb",
                "witdem.execution.source": "custom_python",
            },
            ("knowledge", "retrieval", "mcp", "contract-server", "lancedb", "custom_python"),
        ),
        (
            "raw OpenTelemetry GenAI",
            "embed",
            "opentelemetry.instrumentation.openai",
            {
                "gen_ai.operation.name": "embeddings",
                "gen_ai.provider.name": "provider-a",
                "gen_ai.request.model": "opaque-model",
            },
            ("inference", "embedding", "model_api", "provider-a", None, None),
        ),
        (
            "Anthropic SDK",
            "anthropic.messages.create",
            "witdem_sdk.integrations.anthropic",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": "claude-test",
                "witdem.execution.source": "anthropic_sdk",
            },
            ("inference", "text_generation", "model_api", "anthropic", None, "anthropic_sdk"),
        ),
        (
            "OpenAI SDK",
            "openai.responses",
            "witdem_sdk.integrations.openai",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-test",
                "witdem.execution.source": "openai_sdk",
            },
            ("inference", "text_generation", "model_api", "openai", None, "openai_sdk"),
        ),
        (
            "Claude Agent SDK",
            "claude_agent.model",
            "witdem_sdk.integrations.claude_agent",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "witdem.execution.source": "claude_agent_sdk",
                "witdem.framework.id": "claude_agent_sdk",
            },
            ("inference", "text_generation", "model_api", "anthropic", None, "claude_agent_sdk"),
        ),
        (
            "LangChain",
            "langchain.retriever",
            "witdem_sdk.integrations.langchain",
            {
                "witdem.operation.type": "retrieval",
                "witdem.operation.interface": "library",
                "witdem.framework.id": "langchain",
                "witdem.execution.source": "langchain",
            },
            ("knowledge", "retrieval", "library", None, None, "langchain"),
        ),
        (
            "LangGraph",
            "langchain.chat_model",
            "witdem_sdk.integrations.langchain",
            {
                "gen_ai.operation.name": "chat",
                "witdem.operation.type": "text_generation",
                "witdem.operation.interface": "model_api",
                "witdem.framework.id": "langgraph",
                "witdem.execution.source": "langgraph",
            },
            ("inference", "text_generation", "model_api", None, None, "langgraph"),
        ),
        (
            "Haystack",
            "haystack.component.run",
            "witdem.haystack",
            {
                "haystack.component.fully_qualified_type": (
                    "haystack.components.embedders.SentenceTransformersTextEmbedder"
                ),
                "witdem.operation.type": "embedding",
                "witdem.operation.interface": "model_api",
                "witdem.framework.id": "haystack",
                "witdem.execution.source": "haystack",
            },
            ("inference", "embedding", "model_api", None, None, "haystack"),
        ),
        (
            "OpenAI Agents",
            "openai_agents.handoff",
            "witdem_sdk.integrations.openai_agents",
            {
                "witdem.operation.family": "agent_control",
                "witdem.operation.type": "handoff",
                "witdem.operation.interface": "framework",
                "witdem.framework.id": "openai_agents",
                "witdem.execution.source": "openai_agents",
            },
            ("agent_control", "handoff", "framework", None, None, "openai_agents"),
        ),
        (
            "Smolagents OpenInference",
            "rerank",
            "openinference.instrumentation.smolagents",
            {
                "openinference.span.kind": "RERANKER",
                "witdem.execution.source": "smolagents",
            },
            ("inference", "reranking", "model_api", None, None, "smolagents"),
        ),
        (
            "OpenRouter SDK",
            "openrouter.chat",
            "witdem_sdk.integrations.openrouter",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openrouter",
                "witdem.execution.source": "openrouter_sdk",
            },
            ("inference", "text_generation", "model_api", "openrouter", None, "openrouter_sdk"),
        ),
        (
            "LiteLLM",
            "litellm.embedding",
            "witdem_sdk.integrations.litellm",
            {
                "call_type": "embedding",
                "gen_ai.provider.name": "provider-a",
                "witdem.client.library": "litellm",
            },
            ("inference", "embedding", "model_api", "provider-a", None, "litellm"),
        ),
        (
            "local model",
            "encode_image",
            "application",
            {
                "witdem.operation.type": "embedding",
                "witdem.operation.interface": "local",
                "gen_ai.request.model": "clip-local",
                "witdem.execution.source": "python",
            },
            ("inference", "embedding", "local", None, None, "python"),
        ),
    ],
)
def test_supported_paths_share_one_canonical_contract(
    path: str,
    name: str,
    scope: str,
    attributes: dict[str, Any],
    expected: tuple[str, str, str, str | None, str | None, str | None],
) -> None:
    fact = _classify(name, attributes, scope=scope)

    assert (
        fact["family"],
        fact["operation_type"],
        fact["interface"],
        fact["provider_id"],
        fact["implementation_id"],
        fact["execution_source"],
    ) == expected, path


def test_untyped_model_from_any_sdk_remains_unknown() -> None:
    fact = _classify(
        "client.model_call",
        {
            "gen_ai.provider.name": "any-provider",
            "gen_ai.request.model": "any-model",
        },
        scope="any.sdk",
    )

    assert fact["operation_type"] == "unknown"
    assert fact["family"] == "custom"
