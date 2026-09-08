"""Public storage-independent analytics over matched workflow projections.

This preserves the dashboard calculation contract. Callers own tenant selection,
input uniqueness and storage; this reducer does not make whole-history scans
incremental or constant-time.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import Any


def _sum_optional(values: Iterator[Any]) -> float | None:
    known = [float(value) for value in values if value is not None]
    return sum(known) if known else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def workflow_projection_analytics(replays: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate workflow-specific charts from materialized replay projections.

    Workflow matching is intentionally independent of an application's optional
    ``workflow`` telemetry attribute.  Using the matched projections here keeps
    the workflow page accurate for YAML-only and historical integrations.
    """

    attribution: dict[str, dict[str, dict[str, Any]]] = {
        "models": defaultdict(lambda: {"runs": set(), "calls": []}),
        "providers": defaultdict(lambda: {"runs": set(), "calls": []}),
    }
    stages: dict[str, dict[str, Any]] = {}
    for replay in replays:
        execution = dict(replay.get("execution") or {})
        execution_id = str(execution.get("execution_id") or "")
        nodes = [dict(node) for node in replay.get("nodes", []) if isinstance(node, dict)]
        failed = str(execution.get("runtime_outcome") or execution.get("status") or "").casefold() in {
            "error",
            "failed",
        } or any(node.get("state") == "failed" for node in nodes)
        recovered = not failed and any(node.get("state") == "recovered" for node in nodes)

        seen: dict[str, set[str]] = {"models": set(), "providers": set()}
        for node in nodes:
            if node.get("state") == "inactive":
                continue
            stage = stages.setdefault(
                str(node.get("name") or node.get("id") or "Unknown step"),
                {
                    "calls": 0,
                    "executions": set(),
                    "time_seconds": 0.0,
                    "known_costs": [],
                    "cost_eligible": 0,
                    "cost_measured": 0,
                    "total_tokens": 0.0,
                    "token_eligible": 0,
                    "token_measured": 0,
                    "failures": 0,
                    "extra_attempts": 0,
                },
            )
            stage["calls"] += int(node.get("attempts") or 0)
            stage["executions"].add(execution_id)
            stage["time_seconds"] += float(node.get("duration_seconds") or 0)
            if node.get("known_cost") is not None:
                stage["known_costs"].append(float(node["known_cost"]))
            stage["cost_eligible"] += int(node.get("cost_eligible_operations") or 0)
            stage["cost_measured"] += int(node.get("cost_measured_operations") or 0)
            if node.get("total_tokens") is not None:
                stage["total_tokens"] += float(node["total_tokens"])
            stage["token_eligible"] += int(node.get("token_eligible_operations") or 0)
            stage["token_measured"] += int(node.get("token_measured_operations") or 0)
            stage["failures"] += int(node.get("state") == "failed")
            stage["extra_attempts"] += max(0, int(node.get("attempts") or 0) - 1)

            for call in node.get("model_calls", []):
                if not isinstance(call, dict):
                    continue
                provider = str(call.get("provider") or "").strip() or None
                model = str(call.get("model") or "").strip() or None
                raw_attributes = call.get("attributes")
                attributes: dict[str, Any] = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
                identities = {
                    "models": (
                        f"{provider or 'unknown-provider'}::{model}",
                        model,
                        provider,
                        model,
                        attributes.get("model_family"),
                        attributes.get("model_vendor") or attributes.get("vendor"),
                    ),
                    "providers": (
                        provider,
                        provider,
                        provider,
                        None,
                        None,
                        attributes.get("model_vendor") or attributes.get("vendor"),
                    ),
                }
                for dimension, identity in identities.items():
                    key, label, provider_id, model_id, model_family, vendor_id = identity
                    if not label:
                        continue
                    participant_id = str(key)
                    bucket = attribution[dimension][participant_id]
                    bucket["label"] = str(label)
                    bucket["participant_id"] = participant_id
                    bucket["provider_id"] = provider_id
                    bucket["model_id"] = model_id
                    bucket["model_family"] = model_family
                    bucket["vendor_id"] = vendor_id
                    bucket["calls"].append(call)
                    seen[dimension].add(participant_id)
        for dimension, labels in seen.items():
            for label in labels:
                attribution[dimension][label]["runs"].add(execution_id)
                attribution[dimension][label].setdefault("states", []).append((failed, recovered))

    def performance(dimension: str) -> list[dict[str, Any]]:
        result = []
        for participant_id, bucket in attribution[dimension].items():
            calls = bucket["calls"]
            states = bucket.get("states", [])
            costs = [call.get("known_cost") for call in calls]
            tokens = [call.get("total_tokens") for call in calls]
            durations = [float(call.get("duration_seconds") or 0.0) for call in calls]
            runs = len(bucket["runs"])
            failures = sum(int(failed) for failed, _ in states)
            recovered = sum(int(recovered) for _, recovered in states)
            cost_measured = sum(value is not None for value in costs)
            token_measured = sum(value is not None for value in tokens)
            positive_runs = runs - failures
            result.append(
                {
                    "participant_id": participant_id,
                    "dimension": dimension.removesuffix("s"),
                    "label": bucket["label"],
                    "provider_id": bucket.get("provider_id"),
                    "model_id": bucket.get("model_id"),
                    "model_family": bucket.get("model_family"),
                    "vendor_id": bucket.get("vendor_id"),
                    "runs": runs,
                    "calls": len(calls),
                    "completed": runs - failures - recovered,
                    "successful": 0,
                    "failed": failures,
                    "recovered": recovered,
                    "extra_work": recovered,
                    "measured_cost": _sum_optional(iter(costs)) if cost_measured == len(costs) else None,
                    "cost_per_positive_run": None,
                    "time_per_positive_run": sum(durations) / positive_runs if positive_runs else None,
                    "failed_run_cost": None,
                    "total_tokens": _sum_optional(iter(tokens)) if token_measured == len(tokens) else None,
                    "tokens_per_positive_run": None,
                    "failed_run_tokens": None,
                    "failure_rate": failures / runs if runs else 0.0,
                    "extra_work_rate": recovered / runs if runs else 0.0,
                    "cost_coverage": cost_measured / len(costs) if costs else 0.0,
                    "semantics": "cohort+direct-attribution",
                    "active_seconds": sum(durations),
                    "p50_call_seconds": _percentile(durations, 0.50),
                    "p95_call_seconds": _percentile(durations, 0.95),
                    "cost_eligible_operations": len(costs),
                    "cost_measured_operations": cost_measured,
                    "token_eligible_operations": len(tokens),
                    "token_measured_operations": token_measured,
                }
            )
        return sorted(result, key=lambda item: (-int(item["runs"]), str(item["label"])))

    stage_rows = []
    for label, stage in stages.items():
        executions = len(stage["executions"])
        total_time = float(stage["time_seconds"])
        stage_rows.append(
            {
                "label": label,
                "calls": stage["calls"],
                "executions": executions,
                "usual_seconds": total_time / executions if executions else None,
                "time_seconds": total_time,
                "known_cost": (
                    sum(stage["known_costs"])
                    if stage["cost_eligible"] > 0 and stage["cost_measured"] == stage["cost_eligible"]
                    else None
                ),
                "total_tokens": (
                    stage["total_tokens"]
                    if stage["token_eligible"] > 0 and stage["token_measured"] == stage["token_eligible"]
                    else None
                ),
                "cost_eligible_operations": stage["cost_eligible"],
                "cost_measured_operations": stage["cost_measured"],
                "token_eligible_operations": stage["token_eligible"],
                "token_measured_operations": stage["token_measured"],
                "failures": stage["failures"],
                "extra_attempts": stage["extra_attempts"],
            }
        )
    stage_rows.sort(key=lambda item: (-float(item["time_seconds"]), str(item["label"])))
    return {"models": performance("models"), "providers": performance("providers"), "stages": stage_rows}
