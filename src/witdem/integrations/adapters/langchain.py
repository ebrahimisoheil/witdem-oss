"""LangChain Runnable/callback runtime adapter."""

from __future__ import annotations

from collections import OrderedDict
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
    return values if isinstance(values, Mapping) else {}


def _kind(event: str, row: Mapping[str, Any]) -> str:
    explicit = row.get("kind") or row.get("run_type")
    if explicit:
        return str(explicit).casefold()
    event = event.casefold()
    if "chat_model" in event or "llm" in event or "model" in event:
        return "model"
    if "tool" in event:
        return "tool"
    if "retriever" in event:
        return "component"
    if "agent" in event:
        return "agent"
    return "component"


def _callback_rows(records: Sequence[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
    """Pair start/end callback events by ``run_id`` without retaining content."""

    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for raw in records:
        row = dict(_mapping(raw))
        run_id = str(row.get("run_id") or row.get("id") or "")
        if not run_id:
            continue
        event = str(row.get("event") or row.get("event_name") or "on_chain_start")
        current = grouped.setdefault(run_id, {"run_id": run_id})
        current.update(
            {
                "trace_id": (
                    row.get("trace_id") or row.get("root_run_id") or row.get("execution_id") or current.get("trace_id")
                ),
                "parent_run_id": row.get("parent_run_id") or current.get("parent_run_id"),
                "parent_ids": list(row.get("parent_ids") or []),
                "name": (
                    row.get("name") or row.get("serialized", {}).get("name")
                    if isinstance(row.get("serialized"), Mapping)
                    else row.get("name")
                ),
                "event": event,
                "kind": _kind(event, row),
                "start_time": current.get("start_time") or row.get("start_time") or row.get("timestamp"),
                "end_time": row.get("end_time") or row.get("finished_at") or current.get("end_time"),
                "error": row.get("error") or current.get("error"),
            }
        )
        tags = row.get("tags")
        if tags:
            current["tags"] = list(tags) if isinstance(tags, Sequence) and not isinstance(tags, str) else tags
        metadata = row.get("metadata")
        if isinstance(metadata, Mapping):
            current.setdefault("metadata", {}).update(dict(metadata))
        if row.get("parent_ids"):
            current["parent_ids"] = list(row["parent_ids"])
        current["last_event"] = event
    result: list[dict[str, Any]] = []
    for row in grouped.values():
        parent_ids = list(row.get("parent_ids") or [])
        parent = row.get("parent_run_id") or (parent_ids[-1] if parent_ids else None)
        trace_id = row.get("trace_id") or (parent_ids[0] if parent_ids else row["run_id"])
        attrs: dict[str, Any] = {
            "langchain.run_id": row["run_id"],
            "langchain.parent_ids": parent_ids,
            "langchain.run_type": row.get("kind"),
            "witdem.runtime.kind": row.get("kind"),
        }
        if row.get("tags") is not None:
            attrs["langchain.tags"] = row["tags"]
        if row.get("metadata"):
            attrs["langchain.metadata"] = row["metadata"]
            metadata = row["metadata"]
            if isinstance(metadata, Mapping):
                for key, value in metadata.items():
                    if str(key).startswith(("gen_ai.", "llm.", "tool.", "openinference.")):
                        attrs[str(key)] = value
        if row.get("error"):
            attrs["error.message"] = str(row["error"])
        result.append(
            {
                "trace_id": str(trace_id),
                "span_id": row["run_id"],
                "parent_span_id": str(parent) if parent else None,
                "name": str(row.get("name") or row.get("kind") or "runnable"),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "status": {"status_code": "error"} if row.get("error") else {"status_code": "ok"},
                "attributes": attrs,
            }
        )
    return result


class LangChainAdapter:
    """Normalize OTel or callback/event LangChain evidence."""

    runtime_name = "langchain"

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        for raw in spans:
            row = _mapping(raw)
            attrs_value = row.get("attributes")
            attrs: Mapping[str, Any] = attrs_value if isinstance(attrs_value, Mapping) else {}
            scope_value = row.get("instrumentation_scope")
            scope: Mapping[str, Any] = scope_value if isinstance(scope_value, Mapping) else {}
            event = str(row.get("event") or row.get("event_name") or "").casefold()
            if any(str(key).startswith("langchain.") for key in attrs):
                return True
            if "langchain" in str(scope.get("name") or "").casefold() or event.startswith("on_"):
                return True
            if row.get("run_id") or row.get("parent_run_id") or row.get("parent_ids"):
                return True
        return False

    def _raw_rows(self, records: Sequence[Mapping[str, Any] | Any]) -> Sequence[Mapping[str, Any]]:
        rows = [dict(_mapping(record)) for record in records]
        if any(row.get("run_id") and (row.get("event") or row.get("event_name")) for row in rows):
            return _callback_rows(records)
        return rows

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        rows = self._raw_rows(spans)
        normalized = normalize_raw_spans(rows)
        overrides = {
            str(row.get("span_id")): {"kind": row.get("attributes", {}).get("witdem.runtime.kind")}
            for row in rows
            if (
                row.get("span_id")
                and isinstance(row.get("attributes"), Mapping)
                and row["attributes"].get("witdem.runtime.kind")
            )
        }
        return graph_from_spans(
            normalized,
            execution_id=execution_id,
            runtime=runtime_id or self.runtime_name,
            telemetry_path="langchain.callback" if rows and rows[0].get("run_id") else "otel",
            operation_overrides=overrides,
        )
