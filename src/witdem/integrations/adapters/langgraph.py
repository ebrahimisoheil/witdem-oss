"""LangGraph executed control-flow enrichment on top of LangChain evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.runtime import NormalizedExecutionGraph
from witdem.integrations.adapters.langchain import LangChainAdapter, _callback_rows, _mapping
from witdem.integrations.mapping import graph_from_spans, normalize_raw_spans


class LangGraphAdapter(LangChainAdapter):
    runtime_name = "langgraph"

    def detect(self, spans: Sequence[Mapping[str, Any]]) -> bool:
        for raw in spans:
            row = _mapping(raw)
            attrs_value = row.get("attributes")
            attrs: Mapping[str, Any] = attrs_value if isinstance(attrs_value, Mapping) else {}
            metadata_value = row.get("metadata")
            metadata: Mapping[str, Any] = metadata_value if isinstance(metadata_value, Mapping) else {}
            keys = set(attrs) | set(metadata) | set(row)
            name = str(row.get("name") or "").casefold()
            declared_runtime = str(
                attrs.get("witdem.runtime.name")
                or attrs.get("witdem.runtime")
                or attrs.get("product_factory.runtime")
                or ""
            ).casefold()
            if name.startswith("langgraph.") or declared_runtime == "langgraph":
                return True
            if any(
                str(key).startswith("langgraph_")
                or str(key).startswith("langgraph.")
                or str(key) in {"namespace", "checkpoint_ns", "task_id", "send_to", "from_node", "to_node"}
                for key in keys
            ):
                return True
        return False

    @staticmethod
    def _enrich_rows(
        records: Sequence[Mapping[str, Any] | Any],
    ) -> tuple[Sequence[Mapping[str, Any]], list[Mapping[str, Any]]]:
        source_rows = [dict(_mapping(record)) for record in records]
        callback_mode = any(row.get("run_id") and (row.get("event") or row.get("event_name")) for row in source_rows)
        rows = _callback_rows(records) if callback_mode else source_rows
        original_by_id = {str(row.get("run_id")): row for row in source_rows if row.get("run_id")}
        explicit_links: list[Mapping[str, Any]] = []
        for row in rows:
            original = original_by_id.get(str(row.get("span_id")), row)
            attrs = dict(row.get("attributes") or {})
            metadata = dict(original.get("metadata") or {}) if isinstance(original.get("metadata"), Mapping) else {}
            for key in (
                "langgraph_node",
                "langgraph_step",
                "langgraph_triggers",
                "langgraph_path",
                "langgraph_checkpoint_ns",
                "checkpoint_ns",
                "task_id",
                "namespace",
                "subgraph_namespace",
                "interrupt",
                "resume",
                "send_to",
                "retry_attempt",
            ):
                if original.get(key) is not None:
                    attrs[key] = original[key]
                elif metadata.get(key) is not None:
                    attrs[key] = metadata[key]
            # Preserve the LangChain callback classification for nested model
            # and tool runs; only an unclassified graph task becomes a
            # component operation.
            attrs.setdefault("witdem.runtime.kind", attrs.get("witdem.operation.kind") or "component")
            row["attributes"] = attrs
            if original.get("from_node") and original.get("to_node"):
                explicit_links.append(
                    {
                        "source_id": str(original["from_node"]),
                        "target_id": str(original["to_node"]),
                        "relation": str(original.get("relation") or "graph_edge"),
                        "attributes": {"source": "langgraph.executed_edge"},
                    }
                )
            send_to = original.get("send_to")
            if isinstance(send_to, Sequence) and not isinstance(send_to, (str, bytes, bytearray)):
                for target in send_to:
                    explicit_links.append(
                        {
                            "source_id": str(row.get("span_id")),
                            "target_id": str(target),
                            "relation": "send_fanout",
                            "attributes": {"source": "langgraph.Send"},
                        }
                    )
        return rows, explicit_links

    def normalize(
        self,
        spans: Sequence[Mapping[str, Any]],
        *,
        execution_id: str | None = None,
        runtime_id: str | None = None,
        providers: Sequence[Mapping[str, Any]] | None = None,
    ) -> NormalizedExecutionGraph:
        rows, explicit_links = self._enrich_rows(spans)
        normalized = normalize_raw_spans(rows)
        return graph_from_spans(
            normalized,
            execution_id=execution_id,
            runtime=runtime_id or self.runtime_name,
            telemetry_path="langgraph.callback" if rows and rows[0].get("span_id") else "otel",
            explicit_links=explicit_links,
        )
