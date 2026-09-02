"""Adapter stage executed by Duckle's Python transformation process."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from witdem import __version__
from witdem.adapters.providers import normalize_provider_spans
from witdem.adapters.registry import detect_adapter
from witdem.analytics.core import Execution, Operation
from witdem.analytics.operations import operation_identity, operation_measurements


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


def _descendant_measurement_keys(operations: Sequence[Operation]) -> dict[str, set[str]]:
    """Return measured keys reported below each operation in the span tree.

    Frameworks commonly repeat a model call's aggregate token usage on agent,
    chain, or workflow wrapper spans. Those values are useful only when the
    child call did not report them; otherwise treating both as direct facts
    double-counts usage in operation analytics.
    """

    operation_by_span = {operation.span_id: operation for operation in operations if operation.span_id}
    operation_by_id = {operation.operation_id: operation for operation in operations}
    children: dict[str, list[Operation]] = {operation.operation_id: [] for operation in operations}
    for operation in operations:
        parent = operation_by_span.get(operation.parent_span_id or "") or operation_by_id.get(
            operation.parent_span_id or ""
        )
        if parent is not None:
            children[parent.operation_id].append(operation)

    direct = {
        operation.operation_id: {
            str(item["key"])
            for item in operation_measurements(operation)
            if item.get("status") == "measured"
        }
        for operation in operations
    }
    cache: dict[str, set[str]] = {}

    def collect(operation_id: str) -> set[str]:
        if operation_id in cache:
            return cache[operation_id]
        observed: set[str] = set()
        for child in children.get(operation_id, []):
            observed.update(direct.get(child.operation_id, set()))
            observed.update(collect(child.operation_id))
        cache[operation_id] = observed
        return observed

    return {operation.operation_id: collect(operation.operation_id) for operation in operations}


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
    operation_classifications = []
    operation_measurement_facts = []
    descendant_measurements = _descendant_measurement_keys(operations)
    operation_id_by_span = {operation.span_id: operation.operation_id for operation in operations if operation.span_id}
    for operation in operations:
        identity = operation_identity(operation)
        duration = (
            max(0.0, (operation.ended_at - operation.started_at).total_seconds())
            if operation.started_at is not None and operation.ended_at is not None
            else None
        )
        operation_classifications.append(
            {
                "operation_id": operation.operation_id,
                "execution_id": operation.execution_id,
                "taxonomy_version": identity["taxonomy_version"],
                "entity_kind": identity["entity_kind"],
                "plane": identity["plane"],
                "family": identity["family"],
                "operation_type": identity["type"],
                "subtype": identity["subtype"],
                "interface": identity["interface"],
                "role": identity["role"],
                "model_applicability": identity["model_applicability"],
                "input_modalities": identity["input_modalities"],
                "output_modalities": identity["output_modalities"],
                "provider_id": _first(operation.attributes, "gen_ai.provider.name", "provider"),
                "model_id": _first(
                    operation.attributes,
                    "gen_ai.response.model",
                    "gen_ai.request.model",
                    "model",
                ),
                "gateway_id": _first(operation.attributes, "witdem.gateway.id", "gateway"),
                "vendor_id": _first(operation.attributes, "witdem.vendor.id", "model_vendor"),
                "runtime_id": _first(operation.attributes, "witdem.runtime.id", "runtime"),
                "framework_id": _first(operation.attributes, "witdem.framework.id", "framework"),
                "implementation_id": _first(operation.attributes, "witdem.implementation.id", "implementation"),
                "execution_source": _first(
                    operation.attributes, "witdem.execution.source", "witdem.client.library", "otel.scope.name"
                ),
                "parent_operation_id": (
                    operation_id_by_span.get(operation.parent_span_id) if operation.parent_span_id else None
                ),
                "duration_seconds": duration,
                "status": operation.status,
                "attributes": {
                    "trace_id": operation.trace_id,
                    "span_id": operation.span_id,
                    "attempt": operation.attempt,
                },
            }
        )
        measurements = operation_measurements(operation)
        if identity["family"] in {"orchestration", "custom"}:
            duplicated = descendant_measurements.get(operation.operation_id, set())
            measurements = [
                measurement
                for measurement in measurements
                if not (measurement["status"] == "measured" and measurement["key"] in duplicated)
            ]
        for measurement in measurements:
            operation_measurement_facts.append(
                {
                    "operation_id": operation.operation_id,
                    "execution_id": operation.execution_id,
                    "registry_version": measurement["registry_version"],
                    "measurement_key": measurement["key"],
                    "value": measurement["value"],
                    "unit": measurement["unit"],
                    "aggregation": measurement["aggregation"],
                    "scope": measurement["scope"],
                    "measurement_status": measurement["status"],
                    "provenance": measurement["provenance"],
                    "applicability_source": measurement["applicability_source"],
                    "attempt": operation.attempt,
                }
            )
    return {
        "execution_id": execution_id,
        "adapter_name": adapter_name,
        "adapter_version": __version__,
        "execution_json": execution.model_dump_json(),
        "operations_json": json.dumps([item.model_dump(mode="json") for item in operations], default=str),
        "links_json": json.dumps([item.model_dump(mode="json") for item in links], default=str),
        "sdk_records_json": json.dumps(sdk_records, default=str),
        "operation_classifications_json": json.dumps(operation_classifications, default=str),
        "operation_measurements_json": json.dumps(operation_measurement_facts, default=str),
    }


def _first(attributes: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None
