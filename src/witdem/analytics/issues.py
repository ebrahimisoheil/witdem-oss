"""OSS-owned issue interpretation, independent of a serving database.

Inputs are selected execution rows, public Operations and normalized evaluation
facts (attributes must be mappings). Run-level availability is distinct from
required operation-level measurement coverage. This reducer is a semantic
reference, not a promise of bounded memory for arbitrarily large populations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from witdem.analytics.core import Execution, Operation
from witdem.analytics.identity import canonical_operation_key, display_operation
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_failure_stage


def failure_for_operations(operations: list[Operation]) -> dict[str, Any]:
    """Use the public runtime graph's failure attribution."""
    if not operations:
        return {}
    graph = NormalizedExecutionGraph(
        execution=Execution(execution_id=operations[0].execution_id), operations=operations,
    )
    return derive_failure_stage(graph)


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def summarize_issue_insights(
    rows: list[dict[str, Any]],
    operations_by_execution: Mapping[str, list[Operation]],
    evaluations: Iterable[dict[str, Any]],
    *,
    cost_unavailable: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve OSS run-linked issue semantics for a selected population.

    Summary counts precede the existing ten-item investigation display limits.
    Percentile ties (including single-row populations) retain current behavior.
    Callers own consistent snapshots, filtering and storage; this function does
    no I/O and does not mutate its inputs.
    """
    run_by_id = {str(row["execution_id"]): row for row in rows}
    failures: list[dict[str, Any]] = []
    retry_groups: dict[str, dict[str, Any]] = {}
    for execution_id, row in run_by_id.items():
        operations = operations_by_execution.get(execution_id, [])
        failure = failure_for_operations(operations)
        if failure.get("primary_break_point"):
            failures.append(
                {
                    "execution_id": execution_id,
                    "display_name": row.get("display_name"),
                    "failure_location": failure["primary_break_point"],
                    "runtime_outcome": row.get("runtime_outcome") or row.get("status"),
                    "duration_seconds": row.get("duration_seconds"),
                    "known_cost": row.get("known_cost"),
                }
            )
        for operation in operations:
            if (operation.attempt or 1) <= 1:
                continue
            label = display_operation(operation)
            item = retry_groups.setdefault(
                canonical_operation_key(operation),
                {"label": label, "extra_attempts": 0, "execution_ids": set()},
            )
            item["extra_attempts"] += 1
            item["execution_ids"].add(execution_id)
    retries = []
    for item in retry_groups.values():
        execution_ids = sorted(item.pop("execution_ids"))
        item["affected_runs"] = len(execution_ids)
        item["runs"] = [
            {"execution_id": execution_id, "display_name": run_by_id[execution_id].get("display_name")}
            for execution_id in execution_ids
        ]
        retries.append(item)
    retries.sort(key=lambda item: (-int(item["extra_attempts"]), str(item["label"])))

    quality_gaps: list[dict[str, Any]] = []
    for fact in evaluations:
        execution_id = str(fact["execution_id"])
        if execution_id not in run_by_id or fact.get("score") is None:
            continue
        attributes = fact.get("attributes") or {}
        target = attributes.get("target")
        direction = str(attributes.get("direction") or "higher_is_better")
        if not isinstance(target, (int, float)):
            continue
        score = float(fact["score"])
        missed = score < float(target) if direction != "lower_is_better" else score > float(target)
        if missed:
            quality_gaps.append(
                {
                    "execution_id": execution_id,
                    "display_name": run_by_id[execution_id].get("display_name"),
                    "name": fact.get("name") or attributes.get("evaluation_key") or "Evaluation",
                    "score": score,
                    "target": float(target),
                    "direction": direction,
                }
            )

    thresholds = {
        "duration_seconds": _percentile(
            [float(row["duration_seconds"]) for row in rows if row.get("duration_seconds") is not None], 0.95
        ),
        "known_cost": _percentile(
            [float(row["known_cost"]) for row in rows if row.get("known_cost") is not None], 0.95
        ),
        "total_tokens": _percentile(
            [float(row["total_tokens"]) for row in rows if row.get("total_tokens") is not None], 0.95
        ),
    }
    outliers = []
    for row in rows:
        reasons = [
            metric
            for metric, threshold in thresholds.items()
            if threshold is not None and row.get(metric) is not None and float(row[metric]) >= threshold
        ]
        if reasons:
            outliers.append(
                {
                    "execution_id": row["execution_id"],
                    "display_name": row.get("display_name"),
                    "reasons": reasons,
                    "duration_seconds": row.get("duration_seconds"),
                    "known_cost": row.get("known_cost"),
                    "total_tokens": row.get("total_tokens"),
                }
            )
    outliers.sort(key=lambda item: (-len(item["reasons"]), -float(item.get("duration_seconds") or 0)))
    return {
        "summary": {
            "runs": len(rows),
            "terminal_failures": sum(
                int(row.get("failure_count") or 0) > 0 and row.get("runtime_outcome") != "recovered"
                for row in rows
            ),
            "recovered_runs": sum(row.get("runtime_outcome") == "recovered" for row in rows),
            "extra_attempts": sum(int(item["extra_attempts"]) for item in retries),
            "quality_gaps": len(quality_gaps),
        },
        "failures": failures,
        "retries": retries[:10],
        "quality_gaps": quality_gaps[:10],
        "outliers": outliers[:10],
        "measurement": {
            "cost": sum(row.get("known_cost") is not None for row in rows),
            "tokens": sum(row.get("total_tokens") is not None for row in rows),
            "business_goal": sum(row.get("product_goal_achieved") is not None for row in rows),
            "total": len(rows),
            "cost_unavailable": dict(cost_unavailable),
        },
    }
