"""Deterministic dashboard read models.

The UI consumes these records instead of inventing metric definitions in page
functions.  The functions intentionally operate on the repository's small
dictionary boundary so they are also easy to exercise against live, empty,
telemetry-only, and synthetic corpora.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Any, Literal

BusinessMode = Literal["runtime", "business", "mixed"]

_SUCCESS_OUTCOMES = {"accepted", "successful", "succeeded", "success", "valid"}
_UNSUCCESSFUL_OUTCOMES = {"rejected", "unsuccessful", "invalid", "failed", "failure"}
_RECOVERED_OUTCOMES = {"recovered"}
_RUNTIME_OUTCOMES = {"recovered", "failed", "succeeded", "completed", "running"}


@dataclass(frozen=True)
class OutcomeSemantics:
    mode: BusinessMode
    positive_label: str
    negative_label: str
    positive_key: str
    negative_key: str
    explanation: str


def outcome_semantics(rows: Iterable[Mapping[str, Any]], business_available: bool = False) -> OutcomeSemantics:
    """Choose the richest truthful outcome vocabulary for one population.

    A business vocabulary is used only when every final row in the population
    has an application outcome. Mixed populations stay in runtime language so
    completion is never silently presented as business success.
    """

    materialized = list(rows)
    final_rows = [row for row in materialized if row.get("status") != "running"]
    business_rows = [row for row in final_rows if _business_outcome(row) in _SUCCESS_OUTCOMES | _UNSUCCESSFUL_OUTCOMES]
    if business_available and final_rows and len(business_rows) == len(final_rows):
        return OutcomeSemantics(
            "business",
            "Successful runs",
            "Unsuccessful runs",
            "business_successful_runs",
            "business_unsuccessful_runs",
            "Application outcomes are available for every final run in this view.",
        )
    if business_available and business_rows:
        return OutcomeSemantics(
            "mixed",
            "Completed / successful where reported",
            "Failed / unsuccessful where reported",
            "completed_or_business_successful_runs",
            "failed_or_business_unsuccessful_runs",
            "Some runs have application outcomes; runtime completion remains separate for the rest.",
        )
    return OutcomeSemantics(
        "runtime",
        "Completed runs",
        "Failed runs",
        "completed_runs",
        "failed_runs",
        "No application outcome is available, so this view uses observed runtime completion.",
    )


def _known(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _sum_known(rows: Iterable[Mapping[str, Any]], field: str = "known_cost") -> float | None:
    values = [value for row in rows if (value := _known(row.get(field))) is not None]
    return sum(values) if values else None


def _average_known(rows: Sequence[Mapping[str, Any]], field: str, denominator: int) -> float | None:
    total = _sum_known(rows, field)
    return total / denominator if total is not None and denominator else None


def _human_time_markers(rows: Sequence[Mapping[str, Any]]) -> tuple[float | None, float | None, int]:
    """Return the usual and slow-run duration markers without UI jargon."""

    values = sorted(float(row["duration_seconds"]) for row in rows if _known(row.get("duration_seconds")) is not None)
    if not values:
        return None, None, 0
    slow_index = max(0, ceil(len(values) * 0.95) - 1)
    return float(median(values)), values[slow_index], len(values)


def _runtime_completed(row: Mapping[str, Any]) -> bool:
    return row.get("status") != "running" and int(row.get("failure_count") or 0) == 0


def _runtime_failed(row: Mapping[str, Any]) -> bool:
    return (
        row.get("status") != "running"
        and int(row.get("failure_count") or 0) > 0
        and _runtime_outcome(row) not in _RECOVERED_OUTCOMES
    )


def _business_outcome(row: Mapping[str, Any]) -> Any:
    value = row.get("business_outcome")
    if value is not None and str(value).strip():
        return str(value).strip().casefold()
    # Rows produced by the current repository always carry this key.  When it
    # is present but empty, ``outcome`` is the runtime fallback and must not be
    # reinterpreted as an application-owned success definition.
    if "business_outcome" in row:
        return None
    value = row.get("outcome")
    return value if value in _SUCCESS_OUTCOMES | _UNSUCCESSFUL_OUTCOMES else None


def _runtime_outcome(row: Mapping[str, Any]) -> Any:
    value = row.get("runtime_outcome")
    if value in _RUNTIME_OUTCOMES:
        return value
    value = row.get("outcome")
    return value if value in _RUNTIME_OUTCOMES else None


def _business_successful(row: Mapping[str, Any]) -> bool:
    return _business_outcome(row) in _SUCCESS_OUTCOMES


def _business_unsuccessful(row: Mapping[str, Any]) -> bool:
    return _business_outcome(row) in _UNSUCCESSFUL_OUTCOMES


def _positive(row: Mapping[str, Any], semantics: OutcomeSemantics) -> bool:
    if semantics.mode == "business":
        return _business_successful(row)
    if semantics.mode == "mixed":
        business = _business_outcome(row)
        return _business_successful(row) if business is not None else _runtime_completed(row)
    return _runtime_completed(row)


def _negative(row: Mapping[str, Any], semantics: OutcomeSemantics) -> bool:
    if semantics.mode == "business":
        return _business_unsuccessful(row)
    if semantics.mode == "mixed":
        business = _business_outcome(row)
        return _business_unsuccessful(row) if business is not None else _runtime_failed(row)
    return _runtime_failed(row)


def _per_run(rows: list[Mapping[str, Any]], predicate: Any) -> dict[str, Any]:
    selected = [row for row in rows if predicate(row)]
    measured = [row for row in selected if _known(row.get("known_cost")) is not None]
    return {
        "runs": len(selected),
        "measured_runs": len(measured),
        "unmeasured_runs": len(selected) - len(measured),
        "measured_cost": _sum_known(measured),
        "cost_per_run": _average_known(measured, "known_cost", len(measured)),
        "time_seconds": _sum_known(selected, "duration_seconds"),
        "time_per_run": (
            _average_known(selected, "duration_seconds", len(selected))
            if selected and all(row.get("duration_seconds") is not None for row in selected)
            else None
        ),
    }


def dashboard_metrics(rows: Iterable[Mapping[str, Any]], *, business_available: bool = False) -> dict[str, Any]:
    """Return the shared KPI/read model for a filtered execution population."""

    materialized = [dict(row) for row in rows]
    semantics = outcome_semantics(materialized, business_available)
    total = len(materialized)
    running = sum(row.get("status") == "running" for row in materialized)
    completed = sum(_runtime_completed(row) for row in materialized)
    failed = sum(_runtime_failed(row) for row in materialized)
    recovered = sum(_runtime_outcome(row) in _RECOVERED_OUTCOMES for row in materialized)
    extra = sum(int(row.get("repeated_work") or 0) > 0 for row in materialized)
    measured = [row for row in materialized if _known(row.get("known_cost")) is not None]
    measured_operations = sum(int(row.get("measured_cost_operations") or 0) for row in materialized)
    unmeasured_operations = sum(int(row.get("unmeasured_cost_operations") or 0) for row in materialized)
    known_total = _sum_known(measured)
    operation_total = measured_operations + unmeasured_operations
    model_cost = _sum_known(materialized, "model_cost")
    tool_cost = _sum_known(materialized, "tool_cost")
    usual_time, slow_time, timed_runs = _human_time_markers(materialized)
    business_reported = sum(_business_outcome(row) is not None for row in materialized)
    business_successful = sum(_business_successful(row) for row in materialized)
    observed_failures = sum(int(row.get("failure_count") or 0) > 0 for row in materialized)
    recovery_population = failed + recovered

    def group(
        predicate: Any,
        field: str = "known_cost",
        token_field: str = "total_tokens",
    ) -> dict[str, Any]:
        selected = [row for row in materialized if predicate(row)]
        known = [row for row in selected if _known(row.get(field)) is not None]
        total_cost = _sum_known(known, field)
        token_rows = [row for row in selected if _known(row.get(token_field)) is not None]
        total_tokens = _sum_known(token_rows, token_field)
        return {
            "runs": len(selected),
            "measured_runs": len(known),
            "unmeasured_runs": len(selected) - len(known),
            "measured_cost": total_cost,
            "cost_per_run": _average_known(known, field, len(known)),
            "time_per_run": (
                _average_known(selected, "duration_seconds", len(selected))
                if selected and all(row.get("duration_seconds") is not None for row in selected)
                else None
            ),
            "input_tokens": _sum_known(selected, "input_tokens"),
            "output_tokens": _sum_known(selected, "output_tokens"),
            "total_tokens": total_tokens,
            "token_runs": len(token_rows),
            "tokens_per_run": total_tokens / len(token_rows) if total_tokens is not None and token_rows else None,
        }

    positive = group(lambda row: _positive(row, semantics))
    negative = group(lambda row: _negative(row, semantics))
    completed_group = group(_runtime_completed)
    failed_group = group(_runtime_failed)
    recovered_group = group(lambda row: _runtime_outcome(row) in _RECOVERED_OUTCOMES)
    extra_group = group(
        lambda row: int(row.get("repeated_work") or 0) > 0,
        "extra_work_cost",
        "extra_work_tokens",
    )
    return {
        "semantics": semantics,
        "total_runs": total,
        "running_runs": running,
        "completed_runs": completed,
        "failed_runs": failed,
        "observed_failure_runs": observed_failures,
        "recovered_runs": recovered,
        "extra_work_runs": extra,
        "business_successful_runs": business_successful,
        "business_unsuccessful_runs": sum(_business_unsuccessful(row) for row in materialized),
        "business_reported_runs": business_reported,
        "runtime_failure_rate": failed / total if total else 0.0,
        "recovery_population_runs": recovery_population,
        "recovery_rate": recovered / recovery_population if recovery_population else None,
        "extra_work_rate": extra / total if total else 0.0,
        "business_acceptance_rate": business_successful / business_reported if business_reported else None,
        "business_coverage": business_reported / total if total else 0.0,
        "measured_cost": known_total,
        "model_cost": model_cost,
        "tool_cost": tool_cost,
        "input_tokens": _sum_known(materialized, "input_tokens"),
        "output_tokens": _sum_known(materialized, "output_tokens"),
        "total_tokens": _sum_known(materialized, "total_tokens"),
        "token_runs": sum(_known(row.get("total_tokens")) is not None for row in materialized),
        "measured_runs": len(measured),
        "unmeasured_runs": total - len(measured),
        "cost_coverage": len(measured) / total if total else 0.0,
        "measured_cost_per_run": known_total / len(measured) if known_total is not None and measured else None,
        "measured_cost_operations": measured_operations,
        "unmeasured_cost_operations": unmeasured_operations,
        "cost_operation_coverage": measured_operations / operation_total if operation_total else 0.0,
        "cost_complete": bool(measured_operations and unmeasured_operations == 0),
        "positive": positive,
        "negative": negative,
        "completed": completed_group,
        "failed": failed_group,
        "recovered": recovered_group,
        "extra_work": extra_group,
        "duration_seconds": _sum_known(materialized, "duration_seconds"),
        "timed_runs": timed_runs,
        "usual_time_seconds": usual_time,
        "slow_time_seconds": slow_time,
        "time_per_run": (
            _average_known(materialized, "duration_seconds", total)
            if materialized and all(row.get("duration_seconds") is not None for row in materialized)
            else None
        ),
    }


def aggregate_performance(
    rows: Iterable[Mapping[str, Any]],
    *,
    dimension: str,
    business_available: bool = False,
) -> list[dict[str, Any]]:
    """Aggregate run-level performance by a display-ready entity dimension."""

    materialized = [dict(row) for row in rows]
    semantics = outcome_semantics(materialized, business_available)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in materialized:
        labels = row.get(dimension) or row.get({"workflow": "workflows", "stage": "stages"}.get(dimension, dimension))
        if labels is None:
            labels = "Unknown"
        if isinstance(labels, str) and ", " in labels and dimension in {"provider", "model"}:
            labels = labels.split(", ")[0]
        buckets[str(labels)].append(row)
    result = []
    for label, grouped in buckets.items():
        metrics = dashboard_metrics(grouped, business_available=business_available)
        positive = metrics["positive"]
        negative = metrics["negative"]
        result.append(
            {
                "label": label,
                "runs": len(grouped),
                "calls": sum(
                    int(row.get("model_calls" if dimension == "model" else "operation_count") or 0) for row in grouped
                ),
                "completed": metrics["completed_runs"],
                "successful": metrics["business_successful_runs"],
                "failed": metrics["failed_runs"],
                "recovered": metrics["recovered_runs"],
                "extra_work": metrics["extra_work_runs"],
                "measured_cost": metrics["measured_cost"],
                "cost_per_positive_run": positive["cost_per_run"],
                "time_per_positive_run": positive["time_per_run"],
                "failed_run_cost": negative["measured_cost"],
                "total_tokens": metrics["total_tokens"],
                "tokens_per_positive_run": positive["tokens_per_run"],
                "failed_run_tokens": negative["total_tokens"],
                "failure_rate": metrics["failed_runs"] / len(grouped) if grouped else 0.0,
                "extra_work_rate": metrics["extra_work_runs"] / len(grouped) if grouped else 0.0,
                "cost_coverage": metrics["cost_coverage"],
                "semantics": semantics.mode,
            }
        )
    return sorted(result, key=lambda item: (-int(item["runs"]), str(item["label"])))
