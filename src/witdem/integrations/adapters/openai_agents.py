"""Native OpenAI Agents tracing adapter.

The adapter accepts the native trace/span objects as well as small mapping
fixtures.  A tracing processor is included so applications can collect the
native objects directly without routing them through OpenInference first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.runtime import NormalizedExecutionGraph
from witdem.integrations.mapping import graph_from_spans, normalize_raw_spans


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    for method in ("model_dump", "dict"):
        converter = getattr(value, method, None)
        if callable(converter):
            try:
                result = converter()
            except TypeError:
                continue
            if isinstance(result, Mapping):
                return result
    values = getattr(value, "__dict__", None)
    result = dict(values) if isinstance(values, Mapping) else {}
    result.setdefault("__class__", type(value).__name__)
    for key in (
        "trace_id",
        "span_id",
        "id",
        "parent_id",
        "span_data",
        "data",
        "span_type",
        "type",
        "name",
        "started_at",
        "ended_at",
        "workflow_name",
        "group_id",
        "usage",
        "model",
        "agent_name",
        "tool_name",
        "from_agent",
        "to_agent",
        "turn",
        "input",
        "output",
        "response",
        "sdk_span_type",
    ):
        if key not in result and hasattr(value, key):
            result[key] = getattr(value, key)
    return result


def _value(row: Mapping[str, Any], data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
        if data.get(key) is not None:
            return data[key]
    return None


def _span_rows(records: Sequence[Mapping[str, Any] | Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trace_info: dict[str, Any] = {}
    for raw in records:
        row = dict(_mapping(raw))
        if isinstance(row.get("trace"), Mapping):
            trace_info.update(dict(row["trace"]))
        if row.get("spans") and isinstance(row["spans"], Sequence):
            trace_info.update({key: row.get(key) for key in ("trace_id", "workflow_name", "group_id") if row.get(key)})
            records_to_read = row["spans"]
            for nested in records_to_read:
                nested_row = dict(_mapping(nested))
                nested_row.setdefault("trace_id", row.get("trace_id"))
                rows.append(nested_row)
            continue
        if row:
            rows.append(row)
    result: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    for row in rows:
        data = dict(_mapping(row.get("span_data") or row.get("data")))
        span_type = str(_value(row, data, "span_type", "type", "kind", "name") or data.get("__class__") or "span")
        lowered = span_type.casefold().replace("spandata", "")
        if "agent" in lowered and "handoff" not in lowered:
            kind = "agent"
        elif "generation" in lowered or "response" in lowered or "model" in lowered:
            kind = "model"
        elif "function" in lowered or "tool" in lowered:
            kind = "tool"
        elif "guardrail" in lowered:
            kind = "component"
        elif "handoff" in lowered:
            kind = "other"
        elif "task" in lowered or "turn" in lowered:
            kind = "component"
        else:
            kind = str(row.get("kind") or "component")
        name = str(
            _value(row, data, "name", "agent_name", "model", "tool_name", "function_name", "workflow_name") or span_type
        )
        span_id = str(_value(row, data, "span_id", "id") or f"openai-span-{len(result) + 1}")
        trace_id = _value(row, data, "trace_id") or trace_info.get("trace_id")
        parent_id = _value(row, data, "parent_id", "parent_span_id")
        attrs: dict[str, Any] = {
            "openai_agents.span_type": span_type,
            "witdem.runtime.kind": kind,
            "openai_agents.native": True,
        }
        for key in (
            "agent_name",
            "from_agent",
            "to_agent",
            "tool_name",
            "call_id",
            "model",
            "provider",
            "usage",
            "error",
            "workflow_name",
        ):
            value = _value(row, data, key)
            if value is not None:
                attrs[f"openai_agents.{key}"] = value
        model = _value(row, data, "model")
        if model is not None:
            attrs["gen_ai.request.model"] = model
        provider = _value(row, data, "provider")
        if provider is not None:
            attrs["gen_ai.provider.name"] = provider
        tool_name = _value(row, data, "tool_name", "function_name")
        if tool_name is not None:
            attrs["gen_ai.tool.name"] = tool_name
        usage = _value(row, data, "usage")
        if isinstance(usage, Mapping):
            usage_aliases = {
                "input_tokens": "gen_ai.usage.input_tokens",
                "prompt_tokens": "gen_ai.usage.input_tokens",
                "output_tokens": "gen_ai.usage.output_tokens",
                "completion_tokens": "gen_ai.usage.output_tokens",
                "total_tokens": "gen_ai.usage.total_tokens",
                "cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
                "cached_tokens": "gen_ai.usage.cache_read.input_tokens",
                "reasoning_tokens": "gen_ai.usage.reasoning.output_tokens",
            }
            for key, target in usage_aliases.items():
                if usage.get(key) is not None:
                    attrs[target] = usage[key]
        error = _value(row, data, "error", "exception")
        result.append(
            {
                "trace_id": str(trace_id) if trace_id else span_id,
                "span_id": span_id,
                "parent_span_id": str(parent_id) if parent_id else None,
                "name": name,
                "start_time": _value(row, data, "start_time", "started_at", "started_at_unix_nano"),
                "end_time": _value(row, data, "end_time", "ended_at", "ended_at_unix_nano"),
                "status": {"status_code": "error", "description": str(error)} if error else {"status_code": "ok"},
                "attributes": attrs,
            }
        )
        if kind == "other" and (_value(row, data, "from_agent") or _value(row, data, "to_agent")):
            handoffs.append(
                {
                    "source_ref": str(_value(row, data, "from_agent") or ""),
                    "target_ref": str(_value(row, data, "to_agent") or ""),
                    "handoff_id": span_id,
                }
            )
    trace_info.setdefault("trace_id", result[0]["trace_id"] if result else None)
    trace_info["handoffs"] = handoffs
    return result, trace_info


class OpenAIAgentsAdapter:
    runtime_name = "openai-agents"

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        for raw in spans:
            row = _mapping(raw)
            text = " ".join(str(row.get(key) or "") for key in ("span_type", "type", "span_data", "data")).casefold()
            if row.get("openai_agents") or row.get("span_data") or "agents" in text:
                return True
        return False

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        rows, trace_info = _span_rows(spans)
        normalized = normalize_raw_spans(rows)
        explicit_links: list[dict[str, Any]] = []
        for handoff in trace_info.get("handoffs", []):

            def resolve(reference: str) -> str | None:
                if reference in {item.span_id for item in normalized if item.span_id}:
                    return reference
                for item in normalized:
                    attrs = item.attributes
                    if reference in {
                        item.name,
                        str(attrs.get("openai_agents.agent_name") or ""),
                    }:  # explicit native identity
                        return str(item.span_id)
                return None

            source_id = resolve(handoff["source_ref"])
            target_id = resolve(handoff["target_ref"])
            if source_id and target_id:
                explicit_links.append(
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation": "handoff",
                        "attributes": {
                            "source": "openai_agents.native_handoff",
                            "handoff_span_id": handoff["handoff_id"],
                            "from_agent": handoff["source_ref"],
                            "to_agent": handoff["target_ref"],
                        },
                    }
                )
        execution_attributes = {
            "openai_agents.trace_id": trace_info.get("trace_id"),
            "openai_agents.workflow_name": trace_info.get("workflow_name"),
            "openai_agents.group_id": trace_info.get("group_id"),
            "witdem.capability.native_trace": True,
        }
        return graph_from_spans(
            normalized,
            execution_id=execution_id or trace_info.get("trace_id"),
            runtime=runtime_id or self.runtime_name,
            telemetry_path="openai_agents.native_processor",
            explicit_links=explicit_links,
            execution_attributes=execution_attributes,
        )


class OpenAIAgentsTracingProcessor:
    """Small native ``TracingProcessor``-compatible collector.

    The official SDK calls ``on_trace_start``, ``on_span_start`` and
    ``on_span_end``.  This class intentionally avoids importing the optional
    SDK, so Witdem remains installable without OpenAI Agents.
    """

    def __init__(self) -> None:
        self._trace: Any = None
        self._spans: dict[str, Any] = {}

    def on_trace_start(self, trace: Any) -> None:
        self._trace = trace

    def on_trace_end(self, trace: Any) -> None:
        self._trace = trace

    def on_span_start(self, span: Any) -> None:
        row = _mapping(span)
        span_id = str(row.get("span_id") or row.get("id") or id(span))
        self._spans[span_id] = span

    def on_span_end(self, span: Any) -> None:
        row = _mapping(span)
        span_id = str(row.get("span_id") or row.get("id") or id(span))
        self._spans[span_id] = span

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None

    def graph(self) -> NormalizedExecutionGraph:
        trace_row = dict(_mapping(self._trace))
        trace_id = trace_row.get("trace_id")
        records = [{"trace": trace_row, "spans": list(self._spans.values()), "trace_id": trace_id}]
        return OpenAIAgentsAdapter().normalize(records, execution_id=str(trace_id) if trace_id else None)


WitdemTracingProcessor = OpenAIAgentsTracingProcessor
