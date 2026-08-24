"""Adapter stage executed by Duckle's Python transformation process."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from witdem import __version__
from witdem.adapters.providers import normalize_provider_spans
from witdem.adapters.registry import detect_adapter
from witdem.analytics.core import Execution


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _execution_status(operations: Sequence[Any]) -> str:
    roots = [operation for operation in operations if operation.parent_span_id is None]
    if not roots or any(root.ended_at is None for root in roots):
        return "running"
    return "failed" if any(root.status == "error" for root in roots) else "completed"


def _sdk_execution(execution_id: str, records: Sequence[Mapping[str, Any]]) -> Execution:
    attributes: dict[str, Any] = {"witdem.source": "sdk"}
    terminal = False
    for record in records:
        observed = record.get("attributes")
        if isinstance(observed, Mapping):
            for key in ("case_id", "display_name", "model_profile", "runtime_id", "service.name"):
                if observed.get(key) is not None:
                    attributes[key] = observed[key]
        if record.get("kind") == "outcome" and record.get("name") == "execution.completed":
            terminal = True
    return Execution(
        execution_id=execution_id,
        runtime_id=str(attributes.get("runtime_id") or "sdk"),
        status="completed" if terminal else "running",
        attributes=attributes,
    )


def transform_bundle(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one complete execution bundle through the adapter registry.

    Nested canonical collections are JSON strings so Duckle can retain a
    stable scalar output schema while still providing run logs, validation,
    and lineage around the adapter process. The publisher validates every
    nested model before making it visible to readers.
    """

    execution_id = str(row.get("execution_id") or "")
    if not execution_id:
        raise ValueError("execution bundle requires execution_id")
    spans = _records(row.get("spans_json") or row.get("spans"))
    spans, provider_adapters = normalize_provider_spans(spans)
    sdk_records = _records(row.get("sdk_records_json") or row.get("sdk_records"))
    raw_source_ids = row.get("source_ingest_ids_json") or row.get("source_ingest_ids") or "[]"
    parsed_source_ids = json.loads(raw_source_ids) if isinstance(raw_source_ids, str) else raw_source_ids
    source_ids = [str(value) for value in parsed_source_ids] if isinstance(parsed_source_ids, Sequence) else []
    source_ids_json = json.dumps(source_ids, sort_keys=True)
    if spans:
        adapter = detect_adapter(spans)
        runtime_id = str(row.get("runtime_id") or "") or None
        graph = adapter.normalize(spans, execution_id=execution_id, runtime_id=runtime_id)
        adapter_name = type(adapter).__name__.removesuffix("Adapter").casefold()
        provenance = {
            "witdem.adapter.name": adapter_name,
            "witdem.adapter.version": __version__,
            "witdem.transform.engine": "duckle",
            "witdem.source_ingest_ids": source_ids_json,
            "witdem.provider_adapters": json.dumps(provider_adapters),
        }
        execution = graph.execution.model_copy(
            update={
                "status": _execution_status(graph.operations),
                "attributes": {**graph.execution.attributes, **provenance},
            }
        )
        operations = [
            operation.model_copy(update={"attributes": {**operation.attributes, **provenance}})
            for operation in graph.operations
        ]
        links = graph.links
    else:
        sdk_execution = _sdk_execution(execution_id, sdk_records)
        execution = sdk_execution.model_copy(
            update={
                "attributes": {
                    **sdk_execution.attributes,
                    "witdem.adapter.name": "sdk",
                    "witdem.adapter.version": __version__,
                    "witdem.transform.engine": "duckle",
                    "witdem.source_ingest_ids": source_ids_json,
                }
            }
        )
        operations = []
        links = []
        adapter_name = "sdk"
    return {
        "execution_id": execution_id,
        "adapter_name": adapter_name,
        "adapter_version": __version__,
        "execution_json": execution.model_dump_json(),
        "operations_json": json.dumps([item.model_dump(mode="json") for item in operations], default=str),
        "links_json": json.dumps([item.model_dump(mode="json") for item in links], default=str),
        "sdk_records_json": json.dumps(sdk_records, default=str),
    }
