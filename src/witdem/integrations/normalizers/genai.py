"""OTel GenAI semantic-convention input dialect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from witdem.analytics.operations import OPERATION_FAMILIES, OTEL_OPERATION_TYPES
from witdem.integrations.models.normalized_operation import NormalizedOperation
from witdem.integrations.models.normalized_span import NormalizedSpan

GENAI_DIALECT_VERSION = "otel-genai-development-0.1"


def _first(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attributes.get(key)
        if value is not None:
            return value
    return None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class GenAIDialectNormalizer:
    """Interpret current OpenTelemetry GenAI semantic-convention keys."""

    version = GENAI_DIALECT_VERSION

    def normalize(self, span: NormalizedSpan) -> NormalizedOperation:
        attrs = span.attributes
        operation_name = str(_first(attrs, "gen_ai.operation.name") or "").casefold()
        provider = _first(
            attrs,
            "witdem.route.provider",
            "openrouter.provider.name",
            "litellm.provider",
            "gen_ai.provider.name",
        )
        request_model = _first(attrs, "gen_ai.request.model", "openrouter.request.model", "litellm.request.model")
        response_model = _first(attrs, "gen_ai.response.model", "openrouter.response.model", "litellm.response.model")
        tool_name = _first(attrs, "gen_ai.tool.name")
        tool_call_id = _first(attrs, "gen_ai.tool.call.id")
        agent_name = _first(attrs, "gen_ai.agent.name")
        usage: dict[str, int | float] = {}
        usage_aliases = {
            "input_tokens": (
                "gen_ai.usage.input_tokens",
                "openrouter.usage.prompt_tokens",
                "litellm.usage.prompt_tokens",
            ),
            "output_tokens": (
                "gen_ai.usage.output_tokens",
                "openrouter.usage.completion_tokens",
                "litellm.usage.completion_tokens",
            ),
            "cache_read_tokens": (
                "gen_ai.usage.cache_read.input_tokens",
                "openrouter.usage.cached_tokens",
            ),
            "cache_creation_tokens": (
                "gen_ai.usage.cache_creation.input_tokens",
                "openrouter.usage.cache_write_tokens",
            ),
            "reasoning_tokens": (
                "gen_ai.usage.reasoning.output_tokens",
                "openrouter.usage.reasoning_tokens",
            ),
            "total_tokens": (
                "gen_ai.usage.total_tokens",
                "openrouter.usage.total_tokens",
                "litellm.usage.total_tokens",
            ),
        }
        for target, aliases in usage_aliases.items():
            value = _number(_first(attrs, *aliases))
            if value is not None:
                usage[target] = value
        for key, raw_value in attrs.items():
            if not key.startswith("gen_ai.usage."):
                continue
            value = _number(raw_value)
            if value is None:
                continue
            target = key.removeprefix("gen_ai.usage.").replace(".", "_")
            usage.setdefault(target, value)
        if "total_tokens" not in usage and {"input_tokens", "output_tokens"} <= usage.keys():
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

        kind = self.kind_for(span, operation_name=operation_name, tool_name=tool_name, agent_name=agent_name)
        normalized_attrs = dict(attrs)
        operation_type = str(
            normalized_attrs.get("witdem.operation.type") or OTEL_OPERATION_TYPES.get(operation_name) or ""
        )
        if operation_type:
            normalized_attrs.setdefault("witdem.operation.type", operation_type)
            normalized_attrs.setdefault("witdem.operation.family", OPERATION_FAMILIES.get(operation_type, "custom"))
            normalized_attrs.setdefault(
                "witdem.operation.interface",
                "tool"
                if operation_type == "tool"
                else "model_api"
                if operation_type in OPERATION_FAMILIES
                else "unknown",
            )
            normalized_attrs.setdefault("witdem.operation.role", "application")
        normalized_attrs.update(
            {
                "witdem.telemetry.dialect": "otel.genai",
                "witdem.telemetry.dialect_version": self.version,
            }
        )
        return NormalizedOperation(
            source_id=str(span.span_id or span.trace_id or span.name),
            trace_id=span.trace_id,
            parent_source_id=span.parent_span_id,
            name=str(tool_name or agent_name or span.name),
            kind=kind,
            status=span.status,
            started_at=span.start_time,
            ended_at=span.end_time,
            provider=str(provider) if provider is not None else None,
            request_model=str(request_model) if request_model is not None else None,
            response_model=str(response_model) if response_model is not None else None,
            tool_name=str(tool_name) if tool_name is not None else None,
            tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
            agent_name=str(agent_name) if agent_name is not None else None,
            usage=usage,
            attributes=normalized_attrs,
            source={"dialect": "otel.genai", "version": self.version},
            span=span,
        )

    @staticmethod
    def kind_for(
        span: NormalizedSpan,
        *,
        operation_name: str = "",
        tool_name: Any = None,
        agent_name: Any = None,
    ) -> str:
        name = span.name.casefold()
        if tool_name is not None or operation_name in {"execute_tool", "tool"} or "tool" in name:
            return "tool"
        if operation_name in {"chat", "generate_content", "text_completion", "embeddings", "generate", "ocr"}:
            return "model"
        if agent_name is not None or operation_name in {"invoke_agent", "agent"} or "agent" in name:
            return "agent"
        if any(token in name for token in ("llm", "chat", "completion", "generation", "embedding", "model")):
            return "model"
        return "operation"


def normalize_genai_span(span: NormalizedSpan) -> NormalizedOperation:
    return GenAIDialectNormalizer().normalize(span)
