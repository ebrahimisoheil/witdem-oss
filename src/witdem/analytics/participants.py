"""Mergeable inputs for the dashboard's direct work-participant chart."""

from collections import defaultdict
from typing import Any


def operation_participant_inputs(
    operations: list[dict[str, Any]], measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group work by explicit participant identity without attributing child usage.

    Inputs are public classified facts and their direct measurements. Measurement
    counts distinguish measured zero from absent usage when contributions are
    removed/replaced. Like the existing chart, missing elapsed time contributes
    zero to active time; this is not an elapsed-time coverage assertion.
    """
    meters: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for item in measurements:
        if item.get("measurement_status") == "measured" and item.get("value") is not None:
            meters[(item.get("execution_id"), item.get("operation_id"))].append(item)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in operations:
        family = operation.get("family")
        plane = operation.get("plane") or ("control" if family in {"orchestration", "agent_control"} else "work")
        if (operation.get("entity_kind") or "operation") != "operation" or plane != "work":
            continue
        for dimension in ("provider", "model", "implementation"):
            identity = operation.get(f"{dimension}_id")
            if not identity:
                continue
            row = groups.setdefault((dimension, str(identity)), {
                "dimension": dimension, "id": str(identity), "calls": 0, "time": 0.0,
                "cost_sum": 0.0, "cost_count": 0, "tokens_sum": 0.0, "tokens_count": 0,
            })
            row["calls"] += 1
            row["time"] += float(operation.get("duration_seconds") or 0)
            for item in meters.get((operation.get("execution_id"), operation.get("operation_id")), []):
                metric = {"cost.usd": "cost", "tokens.total": "tokens"}.get(str(item.get("measurement_key")))
                if metric is not None:
                    row[f"{metric}_sum"] += float(item["value"])
                    row[f"{metric}_count"] += 1
    return list(groups.values())


def operation_participant_rows(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Present grouped inputs with measured zero intact and missing usage null."""
    return [{"dimension": item["dimension"], "id": item["id"], "calls": item["calls"], "time": item["time"],
             "cost": item["cost_sum"] if item["cost_count"] else None,
             "tokens": item["tokens_sum"] if item["tokens_count"] else None} for item in inputs]
