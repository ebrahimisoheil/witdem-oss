"""Public workflow operation facts projected from canonical operations."""

from typing import Any

from witdem.analytics.core import Operation
from witdem.analytics.operations import (
    OPERATION_FAMILIES,
    descendant_measurement_keys,
    operation_identity,
    operation_measurements,
)

WORKFLOW_OPERATION_FACTS_VERSION = "1"


def workflow_operation_facts(
    projection: dict[str, Any], operations: list[Operation]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reuse public identity/declaration rules and direct measurement attribution.

    The returned facts are rebuildable diagnostics, not a new evidence export.
    Canonical operations and the public workflow projection are never mutated.
    """
    workflow = dict(projection.get("workflow") or {})
    execution = dict(projection.get("execution") or {})
    execution_id = str(execution.get("execution_id") or "")
    workflow_id = str(workflow.get("id") or "")
    template_hash = str(workflow.get("template_hash") or "")
    operation_nodes: dict[str, tuple[str | None, str | None, list[str], list[str]]] = {}
    for node in projection.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        declaration = dict(node.get("operation") or {})
        node_id = str(node.get("id") or "")
        for observed in [*(node.get("observations") or []), *(node.get("model_calls") or [])]:
            if not isinstance(observed, dict):
                continue
            observed_id = str(observed.get("id") or observed.get("operation_id") or "")
            if observed_id:
                operation_nodes[observed_id] = (
                    node_id,
                    str(declaration.get("type")) if declaration.get("type") else None,
                    list(declaration.get("expects") or []),
                    list(declaration.get("optional") or []),
                )
    classifications: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    descendant_measurements = descendant_measurement_keys(operations)
    operation_id_by_span = {operation.span_id: operation.operation_id for operation in operations if operation.span_id}
    for operation in operations:
        identity = operation_identity(operation)
        assigned_node_id, declared_type, expected, optional = operation_nodes.get(
            operation.operation_id, (None, None, [], [])
        )
        if declared_type and identity["type"] in {"component", "unknown", "x.witdem.unclassified"}:
            identity = {**identity, "type": declared_type, "family": OPERATION_FAMILIES.get(declared_type, "custom")}
        if declared_type and identity["type"] != declared_type:
            expected = []
            optional = []
        attributes = operation.attributes
        duration = None
        if operation.started_at is not None and operation.ended_at is not None:
            duration = max(0.0, (operation.ended_at - operation.started_at).total_seconds())
        classifications.append(
            {
                "operation_id": operation.operation_id,
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "template_hash": template_hash,
                "node_id": assigned_node_id,
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
                "provider_id": _explicit_attribute(attributes, "gen_ai.provider.name", "provider"),
                "model_id": _explicit_attribute(attributes, "gen_ai.response.model", "gen_ai.request.model", "model"),
                "gateway_id": _explicit_attribute(attributes, "witdem.gateway.id", "gateway"),
                "vendor_id": _explicit_attribute(attributes, "witdem.vendor.id", "model_vendor"),
                "runtime_id": _explicit_attribute(attributes, "witdem.runtime.id", "runtime"),
                "framework_id": _explicit_attribute(attributes, "witdem.framework.id", "framework"),
                "implementation_id": _explicit_attribute(attributes, "witdem.implementation.id", "implementation"),
                "execution_source": _explicit_attribute(
                    attributes, "witdem.execution.source", "witdem.client.library", "otel.scope.name"
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
        for measurement in operation_measurements(operation, expected=expected, optional=optional):
            if (identity["family"] in {"orchestration", "custom"} and measurement["status"] == "measured"
                    and measurement["key"] in descendant_measurements.get(operation.operation_id, set())):
                continue
            measurements.append(
                {
                    "operation_id": operation.operation_id,
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "template_hash": template_hash,
                    "node_id": assigned_node_id,
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
    return classifications, measurements


def _explicit_attribute(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = attributes.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None
