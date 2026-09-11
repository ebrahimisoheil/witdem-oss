"""Reference cohort reducer for versioned issue projections.

This is an in-memory semantic oracle for serving adapters, not a scalable SQL
implementation. Callers select a consistently ordered, deduplicated population;
storage adapters must qualify their bounded reads against these results.
"""

from collections.abc import Mapping
from typing import Any

from witdem.analytics.issue_projection import IssueExecutionProjection, IssueOperationProjection
from witdem.analytics.issues import _percentile


def reduce_issue_projections(
    executions: list[IssueExecutionProjection],
    operations: list[IssueOperationProjection],
    *,
    cost_unavailable: Mapping[str, Any],
) -> dict[str, Any]:
    """Require matching complete projections before reporting a clean population."""
    ids = [item.execution_id for item in executions]
    operation_ids = [item.execution_id for item in operations]
    if len(ids) != len(set(ids)) or len(operation_ids) != len(set(operation_ids)):
        raise ValueError("issue projections require unique execution identities")
    if set(ids) != set(operation_ids):
        raise ValueError("issue operation projection coverage is incomplete or mismatched")
    rows = {item.execution_id: item for item in executions}
    retries: dict[str, dict[str, Any]] = {}
    failures = []
    quality_gaps: list[dict[str, Any]] = []
    for item in executions:
        if item.failure_location:
            failures.append({
                "execution_id": item.execution_id, "display_name": item.display_name,
                "failure_location": item.failure_location, "runtime_outcome": item.runtime_outcome,
                "duration_seconds": item.duration_seconds, "known_cost": item.known_cost,
            })
        for retry in item.retries:
            group = retries.setdefault(retry.key, {"label": retry.label, "extra_attempts": 0, "execution_ids": set()})
            group["extra_attempts"] += retry.extra_attempts
            group["execution_ids"].add(item.execution_id)
        quality_gaps.extend({"execution_id": item.execution_id, "display_name": item.display_name, **gap.model_dump()}
                            for gap in item.quality_gaps)
    retry_items = []
    for group in retries.values():
        members = sorted(group["execution_ids"])
        retry_items.append({
            "label": group["label"], "extra_attempts": group["extra_attempts"], "affected_runs": len(members),
            "runs": [{"execution_id": identity, "display_name": rows[identity].display_name} for identity in members],
        })
    retry_items.sort(key=lambda item: (-int(item["extra_attempts"]), str(item["label"])))
    thresholds = {
        metric: _percentile([float(value) for item in executions
                             if (value := getattr(item, metric)) is not None], 0.95)
        for metric in ("duration_seconds", "known_cost", "total_tokens")
    }
    outliers: list[dict[str, Any]] = []
    for item in executions:
        reasons = [metric for metric, threshold in thresholds.items()
                   if threshold is not None and (value := getattr(item, metric)) is not None and value >= threshold]
        if reasons:
            outliers.append({
                "execution_id": item.execution_id, "display_name": item.display_name, "reasons": reasons,
                "duration_seconds": item.duration_seconds, "known_cost": item.known_cost,
                "total_tokens": item.total_tokens,
            })
    outliers.sort(key=lambda item: (-len(item["reasons"]), -float(item.get("duration_seconds") or 0)))
    types: dict[str, dict[str, Any]] = {}
    alerts: dict[tuple[str, str], dict[str, Any]] = {}
    for projection in operations:
        for contribution in projection.operation_types:
            value = contribution.model_dump()
            if contribution.type not in types:
                types[contribution.type] = value
                continue
            group = types[contribution.type]
            for field in ("operations", "failed", "active_seconds"):
                group[field] += value[field]
            for field in ("roles", "interfaces", "providers", "models", "implementations"):
                group[field] = sorted(set(group[field]) | set(value[field]))
            if value["model_applicability"] == "applicable":
                group["model_applicability"] = "applicable"
            for meter, amount in value["measurements"].items():
                group["measurements"][meter] = group["measurements"].get(meter, 0) + amount
            children = {child["type"]: child for child in group["linked_children"]}
            for child in value["linked_children"]:
                if child["type"] not in children:
                    children[child["type"]] = child
                else:
                    target = children[child["type"]]
                    target["operations"] += child["operations"]
                    for field in ("providers", "models", "implementations"):
                        target[field] = sorted(set(target[field]) | set(child[field]))
            group["linked_children"] = sorted(children.values(), key=lambda child: str(child["type"]))
        for alert in projection.required_measurements:
            key = (alert.operation_type, alert.measurement_key)
            if key not in alerts:
                alerts[key] = alert.model_dump()
            else:
                alerts[key]["operations"] += alert.operations
                alerts[key]["executions"] += 1
                alerts[key]["workflow_ids"] = sorted(set(alerts[key]["workflow_ids"]) | set(alert.workflow_ids))
    return {
        "summary": {
            "runs": len(executions), "terminal_failures": sum(item.terminal_failure for item in executions),
            "recovered_runs": sum(item.recovered for item in executions),
            "extra_attempts": sum(item["extra_attempts"] for item in retry_items), "quality_gaps": len(quality_gaps),
        },
        "failures": failures, "retries": retry_items[:10], "quality_gaps": quality_gaps[:10], "outliers": outliers[:10],
        "measurement": {
            "cost": sum(item.known_cost is not None for item in executions),
            "tokens": sum(item.total_tokens is not None for item in executions),
            "business_goal": sum(item.product_goal_achieved is not None for item in executions),
            "total": len(executions), "cost_unavailable": dict(cost_unavailable),
        },
        "operation_failures": sorted((item for item in types.values() if item["failed"] > 0),
                                     key=lambda item: (-int(item["operations"]), str(item["type"]))),
        "missing_required_measurements": sorted(
            alerts.values(), key=lambda item: (-int(item["operations"]), str(item["operation_type"])),
        ),
    }
