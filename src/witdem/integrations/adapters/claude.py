"""Claude Code / Agent SDK OTel runtime adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.runtime import NormalizedExecutionGraph
from witdem.integrations.mapping import graph_from_spans, normalize_raw_spans


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _claude_kind(name: str, attrs: Mapping[str, Any]) -> str:
    explicit = str(attrs.get("witdem.operation.kind") or "").casefold()
    if explicit in {"model", "tool", "agent", "agent_step", "component", "workflow", "pipeline"}:
        return explicit
    operation = str(attrs.get("gen_ai.operation.name") or "").casefold()
    if operation in {"chat", "generate_content", "text_completion", "embeddings", "generate"}:
        return "model"
    if operation in {"execute_tool", "tool"} or attrs.get("gen_ai.tool.name"):
        return "tool"
    lowered = name.casefold()
    if "llm_request" in lowered or "generation" in lowered or "model" in lowered:
        return "model"
    if "blocked_on_user" in lowered:
        return "other"
    if "tool.execution" in lowered or lowered.endswith(".tool") or attrs.get("claude_code.tool_name"):
        return "tool"
    if "subagent" in lowered or "agent" in lowered or "interaction" in lowered:
        return "agent"
    if "hook" in lowered:
        return "other"
    return "component"


def _status(row: Mapping[str, Any], attrs: Mapping[str, Any]) -> dict[str, Any]:
    # Explicit Claude terminal facts take precedence over an incomplete OTel
    # UNSET status, while the original status remains in source attributes.
    success = attrs.get("claude_code.success", attrs.get("success"))
    is_error = attrs.get("claude_code.is_error", attrs.get("is_error"))
    error = attrs.get("claude_code.error", attrs.get("error"))
    if success is False or is_error is True or error:
        return {"status_code": "error", "description": str(error or "claude runtime reported failure")}
    if success is True:
        return {"status_code": "ok"}
    return dict(row.get("status") or {"status_code": "unset"})


class ClaudeAdapter:
    runtime_name = "claude"

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        for raw in spans:
            row = _mapping(raw)
            attrs = _mapping(row.get("attributes"))
            scope = _mapping(row.get("instrumentation_scope"))
            name = str(row.get("name") or "").casefold()
            if any(str(key).startswith("claude_code.") for key in attrs):
                return True
            if "claude" in str(scope.get("name") or "").casefold() or name.startswith("claude_code."):
                return True
            if attrs.get("gen_ai.system") == "anthropic" and attrs.get("openinference.span.kind"):
                return True
        return False

    @staticmethod
    def detect_capabilities(spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        names = [str(_mapping(span).get("name") or "") for span in spans]
        attrs = [_mapping(_mapping(span).get("attributes")) for span in spans]
        native = any(name.startswith("claude_code.") for name in names) or any(
            any(str(key).startswith("claude_code.") for key in row) for row in attrs
        )
        return {
            "runtime": "claude",
            "telemetry_path": "native_otel" if native else "openinference_otel",
            "native_trace": native,
            "interaction_seen": any("interaction" in name for name in names),
            "tool_seen": any("tool" in name for name in names),
            "subagent_seen": any("subagent" in name for name in names),
            "blocked_on_user_seen": any("blocked_on_user" in name for name in names),
            "content_capture_disabled": True,
            "warning": (
                None
                if native and any("interaction" in name for name in names)
                else "hierarchy_incomplete_or_unverified"
            ),
        }

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        rows: list[dict[str, Any]] = []
        explicit_links: list[dict[str, Any]] = []
        for raw in spans:
            row = dict(_mapping(raw))
            attrs = dict(_mapping(row.get("attributes")))
            name = str(row.get("name") or "claude_code.interaction")
            kind = _claude_kind(name, attrs)
            attrs["witdem.runtime.kind"] = kind
            attrs["witdem.claude.capabilities"] = self.detect_capabilities(spans)
            aliases = {
                # Claude Code currently emits the native ``claude_code.*``
                # names for some versions and the unprefixed names for
                # others. Support both forms without overwriting a native
                # GenAI semantic-convention value already present.
                "gen_ai.provider.name": attrs.get("claude_code.provider") or attrs.get("provider"),
                "gen_ai.request.model": attrs.get("claude_code.model") or attrs.get("model"),
                "gen_ai.usage.input_tokens": attrs.get("claude_code.input_tokens")
                if attrs.get("claude_code.input_tokens") is not None
                else attrs.get("input_tokens"),
                "gen_ai.usage.output_tokens": attrs.get("claude_code.output_tokens")
                if attrs.get("claude_code.output_tokens") is not None
                else attrs.get("output_tokens"),
                "gen_ai.usage.cache_read.input_tokens": attrs.get("claude_code.cache_read_tokens")
                if attrs.get("claude_code.cache_read_tokens") is not None
                else attrs.get("cache_read_tokens"),
                "gen_ai.usage.cache_creation.input_tokens": attrs.get("claude_code.cache_creation_tokens")
                if attrs.get("claude_code.cache_creation_tokens") is not None
                else attrs.get("cache_creation_tokens"),
                "gen_ai.tool.name": attrs.get("claude_code.tool_name") or attrs.get("tool_name"),
                "gen_ai.tool.call.id": attrs.get("claude_code.tool_use_id") or attrs.get("tool_use_id"),
            }
            for key, value in aliases.items():
                if value is not None:
                    attrs.setdefault(key, value)
            if attrs.get("claude_code.session_id") or attrs.get("session.id"):
                attrs["claude.session_id"] = attrs.get("claude_code.session_id") or attrs.get("session.id")
            if attrs.get("claude_code.agent_id") and attrs.get("claude_code.parent_agent_id"):
                explicit_links.append(
                    {
                        "source_id": str(attrs["claude_code.parent_agent_id"]),
                        "target_id": str(attrs["claude_code.agent_id"]),
                        "relation": "subagent",
                        "attributes": {"source": "claude.explicit_parent_agent_id"},
                    }
                )
            row["attributes"] = attrs
            row["status"] = _status(row, attrs)
            row.setdefault("name", name)
            rows.append(row)
        normalized = normalize_raw_spans(rows)
        capabilities = self.detect_capabilities(spans)
        return graph_from_spans(
            normalized,
            execution_id=execution_id,
            runtime=runtime_id or self.runtime_name,
            telemetry_path=str(capabilities["telemetry_path"]),
            explicit_links=explicit_links,
            execution_attributes={"claude.capabilities": capabilities},
        )
