"""OpenInference semantic-convention input dialect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from witdem.integrations.models.normalized_operation import NormalizedOperation
from witdem.integrations.models.normalized_span import NormalizedSpan

OPENINFERENCE_DIALECT_VERSION = "openinference-0.1"

_KINDS = {
    "LLM": "model",
    "TOOL": "tool",
    "AGENT": "agent",
    "CHAIN": "component",
    "RETRIEVER": "component",
    "GUARDRAIL": "component",
    "EMBEDDING": "model",
    "RERANKER": "component",
}


def _first(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if attributes.get(key) is not None:
            return attributes[key]
    return None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class OpenInferenceNormalizer:
    """Map OpenInference keys into the shared normalized operation facts."""

    version = OPENINFERENCE_DIALECT_VERSION

    def normalize(self, span: NormalizedSpan) -> NormalizedOperation:
        attrs = span.attributes
        oi_kind = str(_first(attrs, "openinference.span.kind") or "").upper()
        provider = _first(attrs, "llm.provider", "llm.system", "gen_ai.provider.name")
        model = _first(attrs, "llm.model_name", "gen_ai.request.model", "gen_ai.response.model")
        tool_name = _first(attrs, "tool.name", "gen_ai.tool.name")
        tool_call_id = _first(attrs, "tool.call.id", "gen_ai.tool.call.id")
        usage: dict[str, int | float] = {}
        aliases = {
            "input_tokens": ("llm.token_count.prompt", "llm.token_count.input", "gen_ai.usage.input_tokens"),
            "output_tokens": (
                "llm.token_count.completion",
                "llm.token_count.output",
                "gen_ai.usage.output_tokens",
            ),
            "total_tokens": ("llm.token_count.total", "gen_ai.usage.total_tokens"),
            "cache_read_tokens": (
                "llm.token_count.prompt_details.cache_read",
                "gen_ai.usage.cache_read.input_tokens",
            ),
            "cache_creation_tokens": (
                "llm.token_count.prompt_details.cache_write",
                "gen_ai.usage.cache_creation.input_tokens",
            ),
            "reasoning_tokens": (
                "llm.token_count.completion_details.reasoning",
                "gen_ai.usage.reasoning.output_tokens",
            ),
        }
        for target, names in aliases.items():
            value = _number(_first(attrs, *names))
            if value is not None:
                usage[target] = value
        if "total_tokens" not in usage and {"input_tokens", "output_tokens"} <= usage.keys():
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        kind = _KINDS.get(oi_kind, "operation")
        name = str(tool_name or _first(attrs, "retriever.name", "agent.name") or span.name)
        normalized_attrs = dict(attrs)
        normalized_attrs.update(
            {
                "witdem.telemetry.dialect": "openinference",
                "witdem.telemetry.dialect_version": self.version,
                "openinference.kind": oi_kind or None,
            }
        )
        return NormalizedOperation(
            source_id=str(span.span_id or span.trace_id or span.name),
            trace_id=span.trace_id,
            parent_source_id=span.parent_span_id,
            name=name,
            kind=kind,
            status=span.status,
            started_at=span.start_time,
            ended_at=span.end_time,
            provider=str(provider) if provider is not None else None,
            request_model=str(model) if model is not None else None,
            response_model=str(model) if model is not None else None,
            tool_name=str(tool_name) if tool_name is not None else None,
            tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
            agent_name=str(_first(attrs, "agent.name")) if _first(attrs, "agent.name") is not None else None,
            usage=usage,
            attributes=normalized_attrs,
            source={"dialect": "openinference", "version": self.version},
            span=span,
        )


def normalize_openinference_span(span: NormalizedSpan) -> NormalizedOperation:
    return OpenInferenceNormalizer().normalize(span)
