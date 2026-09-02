"""Read-only analytics repository backed by package-owned SQL definitions.

This module exposes semantic analytics methods to the dashboard API and other
read consumers. SQL is loaded from the analytics query package and values are
always bound as DuckDB parameters.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar, cast

import duckdb
from filelock import FileLock

from witdem.analytics.contracts import (
    CostSummary,
    ExecutionSummary,
    FailureSummary,
    MeasurementCoverage,
    MetadataSnapshot,
    ModelSummary,
    OverviewSnapshot,
    PathSummary,
    PerformanceSummary,
    ProductGoalSummary,
    ProviderSummary,
    SemanticReplayRecord,
)
from witdem.analytics.core import Evaluation, Event, Execution, Link, Operation, Outcome
from witdem.analytics.evidence import (
    EvaluationAssessment,
    EvidenceBundle,
    EvidenceBundleDiagnostics,
    explicit_evaluation_pass,
    measurement_coverage,
    operation_profile_inputs,
    operation_summary,
)
from witdem.analytics.identity import (
    canonical_model_key,
    canonical_operation_key,
    canonical_path_signature,
    canonical_stage_key,
    canonical_tool_key,
    display_canonical_key,
    display_execution,
    display_model,
    display_operation,
    display_path,
    display_stage,
    display_tool,
    model_value,
)
from witdem.analytics.operations import operation_identity, token_measurement_applicable
from witdem.analytics.read_model import aggregate_performance, dashboard_metrics, runtime_state
from witdem.analytics.repository.sql_loader import load_query
from witdem.analytics.repository.state import Capabilities, FilterState
from witdem.analytics.runtime import (
    NormalizedExecutionGraph,
    ReplayGraph,
    derive_failure_stage,
    derive_repeated_patterns,
    derive_replay_graph,
)

_CACHE_MISS = object()
_CacheValue = TypeVar("_CacheValue")


def _cohort_key(samples: Iterable[Mapping[str, Any]]) -> str:
    """Return a stable, opaque identity for a participant's involved-run cohort."""

    execution_ids = sorted({str(sample.get("execution_id") or "") for sample in samples})
    return sha256("\0".join(execution_ids).encode()).hexdigest()[:16]


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, list, Mapping)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return value


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _operation_from_row(row: Mapping[str, Any]) -> Operation:
    fields = {
        name: row.get(name)
        for name in (
            "operation_id",
            "execution_id",
            "trace_id",
            "span_id",
            "parent_span_id",
            "kind",
            "name",
            "status",
            "started_at",
            "ended_at",
            "attempt",
            "attributes",
        )
    }
    fields["attributes"] = _json(fields.get("attributes"))
    return Operation.model_validate(fields)


def _operation_from_serving_fact(row: Mapping[str, Any]) -> Operation:
    """Rehydrate the public operation model from the clean serving contract."""

    return Operation.model_validate(
        {
            "operation_id": row.get("operation_id"),
            "execution_id": row.get("execution_id"),
            "trace_id": row.get("trace_id"),
            "span_id": row.get("span_id"),
            "parent_span_id": row.get("parent_operation_id"),
            "kind": row.get("kind"),
            "name": row.get("canonical_key") or row.get("display_name"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "attempt": row.get("attempt"),
            "attributes": _json(row.get("attributes")),
        }
    )


def _operation_duration(operation: Operation) -> float:
    if operation.started_at is None or operation.ended_at is None:
        return 0.0
    return max(0.0, (operation.ended_at - operation.started_at).total_seconds())


def _active_seconds(operations: Iterable[Operation]) -> float:
    """Return wall time covered by operations, merging nested/overlapping spans."""

    intervals = sorted(
        (operation.started_at, operation.ended_at)
        for operation in operations
        if operation.started_at is not None and operation.ended_at is not None
    )
    if not intervals:
        return 0.0
    total = 0.0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            if next_end > end:
                end = next_end
            continue
        total += max(0.0, (end - start).total_seconds())
        start, end = next_start, next_end
    return total + max(0.0, (end - start).total_seconds())


def _cost_eligible(operation: Operation) -> bool:
    if operation.kind == "model":
        return True
    if any(isinstance(operation.attributes.get(key), (int, float)) for key in ("cost_usd", "gen_ai.cost.usd")):
        return True
    return operation.attributes.get("cost_applicable") is True


def _token_eligible(operation: Operation) -> bool:
    identity = operation_identity(operation)
    return identity["family"] in {"inference", "media"} and token_measurement_applicable(
        str(identity["type"]), operation.attributes
    )


def _complete_operation_total(operations: Iterable[Operation], measurement: str) -> tuple[bool, float | None]:
    eligible = [
        operation
        for operation in operations
        if (_cost_eligible(operation) if measurement == "cost" else _token_eligible(operation))
    ]
    if not eligible:
        return False, None
    field = "cost_usd" if measurement == "cost" else "total_tokens"
    if not all(isinstance(operation.attributes.get(field), (int, float)) for operation in eligible):
        return True, None
    return True, sum(float(operation.attributes[field]) for operation in eligible)


def _operation_measurement_state(operations: Iterable[Operation], measurement: str) -> str:
    eligible = [
        operation
        for operation in operations
        if (_cost_eligible(operation) if measurement == "cost" else _token_eligible(operation))
    ]
    if not eligible:
        return "not_applicable"
    field = "cost_usd" if measurement == "cost" else "total_tokens"
    measured = sum(isinstance(operation.attributes.get(field), (int, float)) for operation in eligible)
    if measured == len(eligible):
        return "complete"
    return "partial" if measured else "missing"


def _participant_identity(
    operation: Operation, dimension: str
) -> tuple[str, str, str | None, str | None, str | None, str | None] | None:
    provider = (
        str(
            operation.attributes.get("provider")
            or operation.attributes.get("gen_ai.provider.name")
            or operation.attributes.get("gen_ai.system")
            or ""
        ).strip()
        or None
    )
    model = str(model_value(operation) or "").strip() or None
    vendor = (
        str(
            operation.attributes.get("model_vendor")
            or operation.attributes.get("vendor")
            or operation.attributes.get("witdem.vendor.id")
            or ""
        ).strip()
        or None
    )
    if dimension == "provider":
        if provider is None:
            return None
        return provider, provider, provider, None, None, vendor
    if model is None:
        return None
    family = canonical_model_key(operation)
    participant_id = f"{provider or 'unknown-provider'}::{family}"
    return participant_id, display_model(operation), provider, model, family, vendor


def _goal_assurance_state(row: Mapping[str, Any]) -> str:
    if row.get("product_goal_achieved") is not True:
        return "not_achieved"
    explicit = str(row.get("assurance_status") or "").strip().casefold()
    if explicit in {"assured", "needs_attention"}:
        return explicit
    evidence_sufficient = row.get("evidence_sufficient")
    if evidence_sufficient is True:
        return "assured"
    if evidence_sufficient is False:
        return "needs_attention"
    return "unassessed"


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _average(values: Iterable[float]) -> float | None:
    observed = [float(value) for value in values]
    return sum(observed) / len(observed) if observed else None


def _execution_from_row(row: Mapping[str, Any]) -> Execution:
    fields = {
        name: row.get(name)
        for name in ("execution_id", "runtime_id", "started_at", "ended_at", "status", "schema_version", "attributes")
    }
    fields["attributes"] = _json(fields.get("attributes"))
    return Execution.model_validate(fields)


def _performance_contract(row: Mapping[str, Any]) -> PerformanceSummary:
    return PerformanceSummary(
        label=str(row["label"]),
        runs=int(row["runs"]),
        calls=int(row["calls"]),
        completed=int(row["completed"]),
        successful=int(row["successful"]),
        failed=int(row["failed"]),
        recovered=int(row["recovered"]),
        extra_work=int(row["extra_work"]),
        measured_cost=float(row["measured_cost"]) if row.get("measured_cost") is not None else None,
        cost_per_positive_run=(
            float(row["cost_per_positive_run"]) if row.get("cost_per_positive_run") is not None else None
        ),
        time_per_positive_run=(
            float(row["time_per_positive_run"]) if row.get("time_per_positive_run") is not None else None
        ),
        failed_run_cost=float(row["failed_run_cost"]) if row.get("failed_run_cost") is not None else None,
        total_tokens=float(row["total_tokens"]) if row.get("total_tokens") is not None else None,
        tokens_per_positive_run=(
            float(row["tokens_per_positive_run"]) if row.get("tokens_per_positive_run") is not None else None
        ),
        failed_run_tokens=float(row["failed_run_tokens"]) if row.get("failed_run_tokens") is not None else None,
        failure_rate=float(row["failure_rate"]),
        extra_work_rate=float(row["extra_work_rate"]),
        cost_coverage=float(row["cost_coverage"]),
        semantics=str(row["semantics"]),
        participant_id=str(row.get("participant_id") or row["label"]),
        dimension=str(row.get("dimension") or "unknown"),
        provider_id=str(row["provider_id"]) if row.get("provider_id") is not None else None,
        model_id=str(row["model_id"]) if row.get("model_id") is not None else None,
        model_family=str(row["model_family"]) if row.get("model_family") is not None else None,
        vendor_id=str(row["vendor_id"]) if row.get("vendor_id") is not None else None,
        active_seconds=float(row.get("active_seconds") or 0.0),
        p50_call_seconds=(float(row["p50_call_seconds"]) if row.get("p50_call_seconds") is not None else None),
        p95_call_seconds=(float(row["p95_call_seconds"]) if row.get("p95_call_seconds") is not None else None),
        cost_eligible_operations=int(row.get("cost_eligible_operations") or 0),
        cost_measured_operations=int(row.get("cost_measured_operations") or 0),
        token_eligible_operations=int(row.get("token_eligible_operations") or 0),
        token_measured_operations=int(row.get("token_measured_operations") or 0),
    )


class AnalyticsRepository:
    """Read-only repository that works with synthetic and real analytics DBs."""

    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser()
        if self.database.is_dir():
            self.database = self.database / "analytics.duckdb"
        self._connection_lock = RLock()
        self._read_connection: duckdb.DuckDBPyConnection | None = None
        self._snapshot_cache: dict[tuple[object, ...], Any] | None = None
        with self._file_lock():
            connection = duckdb.connect(str(self.database), read_only=True)
            try:
                self._tables = {str(row[0]) for row in connection.execute(load_query("shared/metadata")).fetchall()}
                self._serving_tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'serving'"
                    ).fetchall()
                }
            finally:
                connection.close()

    def _file_lock(self) -> FileLock:
        return FileLock(str(self.database.with_suffix(self.database.suffix + ".lock")), timeout=5)

    def close(self) -> None:
        """Compatibility hook; queries use short-lived read connections."""

    @contextmanager
    def _overview_read_session(self) -> Iterator[None]:
        """Hold one DuckDB connection and one file lock for a coherent snapshot."""

        if self._read_connection is not None:
            yield
            return
        with self._connection_lock, self._file_lock():
            connection = duckdb.connect(str(self.database), read_only=True)
            self._read_connection = connection
            self._snapshot_cache = {}
            try:
                yield
            finally:
                self._snapshot_cache = None
                self._read_connection = None
                connection.close()

    def _cached(self, key: tuple[object, ...]) -> object:
        if self._snapshot_cache is None:
            return _CACHE_MISS
        return self._snapshot_cache.get(key, _CACHE_MISS)

    def _remember(self, key: tuple[object, ...], value: _CacheValue) -> _CacheValue:
        if self._snapshot_cache is not None:
            self._snapshot_cache[key] = value
        return value

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        if self._read_connection is not None:
            return _row_dicts(self._read_connection.execute(sql, list(params)))
        with self._connection_lock, self._file_lock():
            connection = duckdb.connect(str(self.database), read_only=True)
            try:
                return _row_dicts(connection.execute(sql, list(params)))
            finally:
                connection.close()

    def execution_fact(self, execution_id: str) -> dict[str, Any] | None:
        """Return the clean one-row serving projection when available."""

        if "execution_facts" not in self._serving_tables:
            return None
        rows = self._query(
            "SELECT * FROM serving.execution_facts WHERE execution_id = ? LIMIT 1",
            [execution_id],
        )
        return rows[0] if rows else None

    def operation_facts(self, execution_id: str) -> list[dict[str, Any]]:
        if "operation_facts" not in self._serving_tables:
            return []
        return self._query(
            "SELECT * FROM serving.operation_facts WHERE execution_id = ? ORDER BY sequence_number",
            [execution_id],
        )

    def _serving_is_complete(self) -> bool:
        cached = self._cached(("serving_is_complete",))
        if cached is not _CACHE_MISS:
            return bool(cached)
        if "execution_facts" not in self._serving_tables:
            return bool(self._remember(("serving_is_complete",), False))
        rows = self._query(
            "SELECT (SELECT COUNT(*) FROM serving.execution_facts) AS serving_count, "
            "(SELECT COUNT(*) FROM executions) AS canonical_count"
        )
        complete = bool(rows and rows[0]["canonical_count"] and rows[0]["serving_count"] == rows[0]["canonical_count"])
        return bool(self._remember(("serving_is_complete",), complete))

    def _serving_execution_rows(
        self, filters: FilterState = FilterState(), limit: int | None = 500
    ) -> list[dict[str, Any]]:
        cache_key = ("serving_execution_rows", filters.as_key(), limit)
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(list[dict[str, Any]], cached)
        rows = self._query("SELECT * FROM serving.execution_facts ORDER BY started_at DESC NULLS LAST")
        contracts = self._serving_contracts_by_execution()
        needs_operations = any(
            (
                filters.cost_status,
                filters.token_status,
                filters.operation_type,
                filters.operation_status,
            )
        )
        operations_by_execution: dict[str, list[Operation]] = {}
        if needs_operations:
            for operation_row in self._query("SELECT * FROM serving.operation_facts ORDER BY sequence_number"):
                operation = _operation_from_serving_fact(operation_row)
                operations_by_execution.setdefault(operation.execution_id, []).append(operation)
        evaluations_by_execution: dict[str, list[dict[str, Any]]] = {}
        if filters.evaluation_key or filters.evaluation_status:
            for evaluation in self._latest_evaluation_facts():
                evaluations_by_execution.setdefault(str(evaluation["execution_id"]), []).append(evaluation)

        def includes(values: Any, wanted: str | None) -> bool:
            if not wanted:
                return True
            return wanted in {item.strip() for item in str(values or "").split(",")}

        selected: list[dict[str, Any]] = []
        for fact in rows:
            contract = contracts.get(str(fact["execution_id"]), {})
            if filters.contract_hash and contract.get("contract_hash") != filters.contract_hash:
                continue
            status = str(fact.get("runtime_status") or "")
            failures = int(fact.get("failure_count") or 0)
            runtime_outcome = _serving_runtime_outcome(status, failures)
            if filters.status == "running" and status != "running":
                continue
            if filters.status == "failed" and runtime_outcome != "failed":
                continue
            if filters.status == "completed" and runtime_outcome not in {"completed", "recovered"}:
                continue
            if filters.status == "recovered" and runtime_outcome != "recovered":
                continue
            if filters.has_failure and failures == 0:
                continue
            if filters.start_date and fact.get("started_at") and fact["started_at"].date() < filters.start_date:
                continue
            if filters.end_date and fact.get("started_at") and fact["started_at"].date() > filters.end_date:
                continue
            if not includes(fact.get("providers"), filters.provider):
                continue
            if not includes(fact.get("models"), filters.model):
                continue
            if not includes(fact.get("workflows"), filters.workflow):
                continue
            if filters.has_repeated_work is True and not int(fact.get("repeated_pattern_count") or 0):
                continue
            goal_reported = fact.get("product_goal_reported") is True
            goal_achieved = fact.get("product_goal_achieved")
            if filters.goal_status == "reported" and not goal_reported:
                continue
            if filters.goal_status == "unreported" and goal_reported:
                continue
            if filters.goal_status == "achieved" and goal_achieved is not True:
                continue
            if filters.goal_status == "not_achieved" and goal_achieved is not False:
                continue
            if filters.assurance_status and _goal_assurance_state(fact) != filters.assurance_status:
                continue
            if filters.application_outcome and fact.get("application_outcome") != filters.application_outcome:
                continue
            if filters.blocker and fact.get("closest_blocker") != filters.blocker:
                continue
            if filters.failure_location and fact.get("failure_location") != filters.failure_location:
                continue
            operations = operations_by_execution.get(str(fact["execution_id"]), [])
            if filters.cost_status and _operation_measurement_state(operations, "cost") != filters.cost_status:
                continue
            if filters.token_status and _operation_measurement_state(operations, "tokens") != filters.token_status:
                continue
            if filters.operation_type or filters.operation_status:
                matching_operations = [
                    operation
                    for operation in operations
                    if not filters.operation_type
                    or str(operation_identity(operation)["type"]) == filters.operation_type
                ]
                if filters.operation_status == "failed":
                    matching_operations = [
                        operation for operation in matching_operations if operation.status in {"error", "failed"}
                    ]
                elif filters.operation_status == "completed":
                    matching_operations = [
                        operation for operation in matching_operations if operation.status not in {"error", "failed"}
                    ]
                if not matching_operations:
                    continue
            if filters.evaluation_key or filters.evaluation_status:
                matching_evaluations = []
                for evaluation in evaluations_by_execution.get(str(fact["execution_id"]), []):
                    attributes = _json(evaluation.get("attributes"))
                    key = str(attributes.get("evaluation_key") or evaluation.get("name") or "Evaluation")
                    if filters.evaluation_key and key != filters.evaluation_key:
                        continue
                    met = self._evaluation_met_target(evaluation, attributes)
                    if filters.evaluation_status == "passed" and met is not True:
                        continue
                    if filters.evaluation_status == "failed" and met is not False:
                        continue
                    if filters.evaluation_status == "unassessed" and met is not None:
                        continue
                    matching_evaluations.append(evaluation)
                if not matching_evaluations:
                    continue
            model_calls = int(fact.get("model_calls") or 0)
            coverage = fact.get("cost_coverage")
            measured_operations = round(model_calls * float(coverage)) if coverage is not None else 0
            selected.append(
                {
                    **fact,
                    "status": status,
                    "runtime_outcome": runtime_outcome,
                    "business_outcome": fact.get("application_outcome"),
                    "outcome": fact.get("application_outcome"),
                    "known_cost": fact.get("measured_cost"),
                    "repeated_work": int(fact.get("repeated_pattern_count") or 0),
                    "extra_time_seconds": fact.get("extra_work_seconds"),
                    "provider": fact.get("providers"),
                    "model": fact.get("models"),
                    "workflow": fact.get("workflows"),
                    "measured_cost_operations": measured_operations,
                    "unmeasured_cost_operations": max(0, model_calls - measured_operations),
                    "contract_hash": contract.get("contract_hash"),
                    "contract_name": contract.get("contract_name"),
                }
            )
        result = selected[:limit] if limit is not None else selected
        return self._remember(cache_key, result)

    def _serving_contracts_by_execution(self) -> dict[str, dict[str, Any]]:
        cache_key = ("serving_contracts_by_execution",)
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(dict[str, dict[str, Any]], cached)
        if "semantic_facts" not in self._serving_tables:
            return self._remember(cache_key, {})
        rows = self._query(
            "SELECT execution_id, attributes FROM serving.semantic_facts "
            "WHERE name = 'contract.definition' ORDER BY observed_at DESC NULLS LAST"
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            execution_id = str(row["execution_id"])
            if execution_id not in result:
                result[execution_id] = _json(row.get("attributes"))
        return self._remember(cache_key, result)

    def _serving_goals_by_execution(self) -> dict[str, dict[str, Any]]:
        cache_key = ("serving_goals_by_execution",)
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(dict[str, dict[str, Any]], cached)
        if "semantic_facts" not in self._serving_tables:
            return self._remember(cache_key, {})
        rows = self._query(
            "SELECT execution_id, attributes FROM serving.semantic_facts "
            "WHERE name = 'product_goal' ORDER BY observed_at DESC NULLS LAST"
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            execution_id = str(row["execution_id"])
            if execution_id not in result:
                result[execution_id] = _json(row.get("attributes"))
        return self._remember(cache_key, result)

    def contract_definitions(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Return one metadata-only definition per contract in the selected corpus."""

        without_contract = replace(filters, contract_hash=None)
        allowed = {str(row["execution_id"]) for row in self._serving_execution_rows(without_contract, limit=None)}
        grouped: dict[str, dict[str, Any]] = {}
        for execution_id, definition in self._serving_contracts_by_execution().items():
            if execution_id not in allowed:
                continue
            contract_hash = str(definition.get("contract_hash") or "")
            if not contract_hash or (filters.contract_hash and contract_hash != filters.contract_hash):
                continue
            if contract_hash not in grouped:
                public_keys = (
                    "contract_hash",
                    "contract_name",
                    "contract_version",
                    "protocol_version",
                    "service",
                    "contract",
                    "result",
                    "decision",
                    "product_goal",
                    "evaluations",
                    "metrics",
                    "dimensions",
                )
                grouped[contract_hash] = {
                    **{key: definition[key] for key in public_keys if key in definition},
                    "run_count": 0,
                }
            grouped[contract_hash]["run_count"] += 1
        return sorted(
            grouped.values(),
            key=lambda item: (-int(item["run_count"]), str(item.get("contract_name") or "")),
        )

    def _filtered_operation_rows(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        where, params = self._where(filters)
        rows = self._query(load_query("execution/execution_population", fragments={"where": where}), params)
        if not any((filters.workflow, filters.model, filters.tool, filters.stage, filters.has_repeated_work)):
            return rows
        grouped: dict[str, list[Operation]] = {}
        for row in rows:
            operation = _operation_from_row(row)
            grouped.setdefault(operation.execution_id, []).append(operation)
        allowed = {
            execution_id
            for execution_id, operations in grouped.items()
            if self._matches_identity_filters(operations, filters)
        }
        return [row for row in rows if str(row["execution_id"]) in allowed]

    def _filtered_operations(self, filters: FilterState = FilterState()) -> list[Operation]:
        cache_key = ("filtered_operations", filters.as_key())
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(list[Operation], cached)
        if self._serving_is_complete() and "operation_facts" in self._serving_tables:
            allowed_ids = {
                str(row["execution_id"])
                for row in self._serving_execution_rows(
                    replace(filters, tool=None, stage=None),
                    limit=None,
                )
            }
            operations = [
                _operation_from_serving_fact(row)
                for row in self._query("SELECT * FROM serving.operation_facts ORDER BY sequence_number")
                if str(row["execution_id"]) in allowed_ids
            ]
            if filters.tool:
                matching_ids = {
                    operation.execution_id
                    for operation in operations
                    if operation.kind == "tool"
                    and filters.tool in {display_tool(operation), canonical_tool_key(operation)}
                }
                operations = [operation for operation in operations if operation.execution_id in matching_ids]
            if filters.stage:
                matching_ids = {
                    operation.execution_id
                    for operation in operations
                    if operation.kind in {"component", "operation", "tool"}
                    and filters.stage in {display_stage(operation), canonical_stage_key(operation)}
                }
                operations = [operation for operation in operations if operation.execution_id in matching_ids]
            return self._remember(cache_key, operations)
        operations = [_operation_from_row(row) for row in self._filtered_operation_rows(filters)]
        return self._remember(cache_key, operations)

    def _operations_by_execution(self, filters: FilterState = FilterState()) -> dict[str, list[Operation]]:
        grouped: dict[str, list[Operation]] = {}
        for operation in self._filtered_operations(filters):
            grouped.setdefault(operation.execution_id, []).append(operation)
        return grouped

    def operations_by_execution(self, filters: FilterState = FilterState()) -> dict[str, list[Operation]]:
        """Return normalized operations for disposable projection builders."""

        return self._operations_by_execution(filters)

    @staticmethod
    def _matches_identity_filters(operations: list[Operation], filters: FilterState) -> bool:
        if filters.workflow and not any(
            operation.kind in {"workflow", "pipeline"}
            and filters.workflow in {display_operation(operation), canonical_operation_key(operation)}
            for operation in operations
        ):
            return False
        if filters.tool and not any(
            operation.kind == "tool" and filters.tool in {display_tool(operation), canonical_tool_key(operation)}
            for operation in operations
        ):
            return False
        if filters.model and not any(
            operation.kind == "model" and filters.model in {display_model(operation), canonical_model_key(operation)}
            for operation in operations
        ):
            return False
        if filters.stage and not any(
            operation.kind in {"component", "operation", "tool"}
            and filters.stage in {display_stage(operation), canonical_stage_key(operation)}
            for operation in operations
        ):
            return False
        if filters.has_repeated_work:
            keys = [canonical_operation_key(operation) for operation in operations]
            if len(keys) == len(set(keys)):
                return False
        return True

    @staticmethod
    def _failure_for_operations(operations: list[Operation]) -> dict[str, Any]:
        if not operations:
            return {}
        execution_id = operations[0].execution_id
        graph = NormalizedExecutionGraph(execution=Execution(execution_id=execution_id), operations=operations)
        return derive_failure_stage(graph)

    def capabilities(self) -> Capabilities:
        cached = self._cached(("capabilities",))
        if cached is not _CACHE_MISS:
            return cast(Capabilities, cached)
        operations = "operations" in self._tables
        events = "events" in self._tables
        evaluations = "evaluations" in self._tables
        outcomes = "outcomes" in self._tables
        counts = self._capability_counts() if operations else {}
        event_counts = self._event_capability_counts() if events else {}
        capabilities = Capabilities(
            graph=operations and "links" in self._tables,
            timing=operations and int(counts.get("timed_operations") or 0) > 0,
            errors=operations and int(counts.get("error_operations") or 0) > 0,
            model_calls=operations and int(counts.get("model_operations") or 0) > 0,
            tools=operations and int(counts.get("tool_operations") or 0) > 0,
            provider_identity=operations and int(counts.get("provider_operations") or 0) > 0,
            model_identity=operations and int(counts.get("model_identity_operations") or 0) > 0,
            token_usage=operations and int(counts.get("token_operations") or 0) > 0,
            operation_cost=operations and int(counts.get("cost_operations") or 0) > 0,
            tool_cost=operations and int(counts.get("measured_tool_cost_operations") or 0) > 0,
            roles=operations and int(counts.get("role_operations") or 0) > 0,
            semantic_stages=events and int(event_counts.get("semantic_stage_events") or 0) > 0,
            evaluations=evaluations and self._table_count("evaluations_count") > 0,
            business_outcomes=outcomes and self._table_count("outcomes_count") > 0,
            semantic_events=events and int(event_counts.get("semantic_events") or 0) > 0,
        )
        return self._remember(("capabilities",), capabilities)

    def get_metadata_snapshot(self) -> MetadataSnapshot:
        """Read metadata, filter values, and contracts through one connection."""

        with self._overview_read_session():
            capabilities = self.capabilities()
            capability_fields = {
                "workflow": "graph",
                "provider": "provider_identity",
                "model": "model_identity",
                "tool": "tools",
                "stage": "semantic_stages",
            }
            filters = {
                field: tuple(self.values(field))
                for field, capability in capability_fields.items()
                if getattr(capabilities, capability)
            }
            return MetadataSnapshot(
                capabilities=capabilities,
                filters=filters,
                contracts=tuple(self.contract_definitions()),
            )

    def _capability_counts(self) -> dict[str, Any]:
        if self._serving_is_complete() and "operation_facts" in self._serving_tables:
            operations = self._filtered_operations()
            return {
                "timed_operations": sum(
                    operation.started_at is not None and operation.ended_at is not None for operation in operations
                ),
                "error_operations": sum(operation.status == "error" for operation in operations),
                "model_operations": sum(operation.kind == "model" for operation in operations),
                "tool_operations": sum(operation.kind == "tool" for operation in operations),
                "measured_tool_cost_operations": sum(
                    operation.kind == "tool" and operation.attributes.get("cost_usd") is not None
                    for operation in operations
                ),
                "provider_operations": sum(
                    operation.attributes.get("provider") is not None for operation in operations
                ),
                "model_identity_operations": sum(
                    operation.attributes.get("model") is not None for operation in operations
                ),
                "token_operations": sum(
                    operation.attributes.get("total_tokens") is not None for operation in operations
                ),
                "cost_operations": sum(operation.attributes.get("cost_usd") is not None for operation in operations),
                "role_operations": sum(operation.attributes.get("role") is not None for operation in operations),
            }
        rows = self._query(load_query("shared/capabilities"))
        return rows[0] if rows else {}

    def _event_capability_counts(self) -> dict[str, Any]:
        rows = self._query(load_query("shared/events_capabilities"))
        return rows[0] if rows else {}

    def _table_count(self, query_name: str) -> int:
        rows = self._query(load_query(f"shared/{query_name}"))
        return int(rows[0]["row_count"] or 0) if rows else 0

    def _json_count(self, field: str) -> int:
        count_keys = {
            "provider": "provider_operations",
            "model": "model_identity_operations",
            "total_tokens": "token_operations",
            "cost_usd": "cost_operations",
            "role": "role_operations",
        }
        if field not in count_keys:
            raise ValueError(f"unsupported operation attribute: {field}")
        return int(self._capability_counts().get(count_keys[field]) or 0)

    def values(self, field: str) -> list[str]:
        if field in {"tool", "stage"}:
            entity = "tools" if field == "tool" else "stages"
            return [str(row["label"]) for row in self.entity_summary(entity)]
        if field == "workflow":
            operations = [
                operation for operation in self._filtered_operations() if operation.kind in {"workflow", "pipeline"}
            ]
            return sorted({display_operation(operation) for operation in operations})
        if field == "model":
            return [str(row["label"]) for row in self.entity_summary("models")]
        if field != "provider":
            raise ValueError(f"unsupported filter field: {field}")
        if self._serving_is_complete() and "operation_facts" in self._serving_tables:
            return sorted(
                {
                    str(operation.attributes["provider"])
                    for operation in self._filtered_operations()
                    if operation.attributes.get("provider")
                }
            )
        rows = self._query(load_query("entities/provider_values"))
        return [str(row["value"]) for row in rows]

    def _where(self, filters: FilterState, alias: str = "e") -> tuple[str, list[Any]]:
        clauses = ["TRUE"]
        params: list[Any] = []
        if filters.provider:
            clauses.append(load_query("shared/filter_provider", fragments={"alias": alias}))
            params.append(filters.provider)
        if filters.has_failure:
            clauses.append(load_query("shared/filter_has_failure", fragments={"alias": alias}))
        if filters.status:
            if filters.status == "failed":
                # A still-"running" execution must never satisfy "failed" --
                # otherwise derived purely from observed operation errors,
                # unchanged from before live data existed -- since an
                # operation can legitimately error mid-run while the
                # execution as a whole hasn't reached a final result yet.
                # This added clause is additive; the error-derived condition
                # itself is untouched for terminal executions.
                clauses.append(load_query("shared/filter_failed", fragments={"alias": alias}))
            elif filters.status == "completed":
                # Same reasoning: zero errors so far does not mean finished.
                clauses.append(load_query("shared/filter_completed", fragments={"alias": alias}))
            elif filters.status == "running":
                # Unlike "completed"/"failed" above (derived from observed operation
                # errors -- a pre-existing convention this filter keeps unchanged),
                # "still running" cannot be derived from operation-level data alone:
                # a running execution may legitimately have zero errored operations
                # so far. Execution.status is the only source of truth for this.
                clauses.append(load_query("shared/filter_running", fragments={"alias": alias}))
        if filters.start_date:
            clauses.append(load_query("shared/filter_start_date", fragments={"alias": alias}))
            params.append(filters.start_date)
        if filters.end_date:
            clauses.append(load_query("shared/filter_end_date", fragments={"alias": alias}))
            params.append(filters.end_date)
        return " AND ".join(clauses), params

    def execution_rows(self, filters: FilterState = FilterState(), limit: int | None = 500) -> list[dict[str, Any]]:
        cache_key = ("execution_rows", filters.as_key(), limit)
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(list[dict[str, Any]], cached)
        if self._serving_is_complete():
            return self._remember(cache_key, self._serving_execution_rows(filters, limit))
        where, params = self._where(filters)
        python_filter = any((filters.workflow, filters.model, filters.tool, filters.stage, filters.has_repeated_work))
        limit_sql = "" if limit is None or python_filter else " LIMIT ?"
        if limit is not None and not python_filter:
            params.append(limit)
        rows = self._query(
            load_query("overview/execution_health", fragments={"where": where, "limit_clause": limit_sql}),
            params,
        )
        operations_by_execution = self._operations_by_execution(filters)
        goal_attributes: dict[str, dict[str, Any]] = {}
        if self.capabilities().business_outcomes:
            visible_ids = {str(row["execution_id"]) for row in rows}
            for goal_row in self._query(load_query("overview/product_goals")):
                execution_id = str(goal_row["execution_id"])
                if execution_id in visible_ids and execution_id not in goal_attributes:
                    goal_attributes[execution_id] = _json(goal_row.get("attributes"))
        for row in rows:
            execution_id = str(row["execution_id"])
            operations = operations_by_execution.get(execution_id, [])
            execution = _execution_from_row(row)
            observed_goal = goal_attributes.get(execution_id, {})
            if observed_goal:
                execution = execution.model_copy(
                    update={
                        "runtime_id": observed_goal.get("runtime_id") or execution.runtime_id,
                        "attributes": {**execution.attributes, **observed_goal},
                    }
                )
            row["display_name"] = display_execution(execution, operations)
            row["provider"] = (
                ", ".join(
                    sorted(
                        {
                            str(operation.attributes["provider"])
                            for operation in operations
                            if operation.attributes.get("provider")
                        }
                    )
                )
                or None
            )
            row["model"] = (
                ", ".join(
                    sorted(
                        {
                            display_model(operation)
                            for operation in operations
                            if operation.kind == "model" and model_value(operation)
                        }
                    )
                )
                or None
            )
            row["workflow"] = (
                ", ".join(
                    sorted(
                        {
                            display_operation(operation)
                            for operation in operations
                            if operation.kind in {"workflow", "pipeline"}
                        }
                    )
                )
                or None
            )
            graph = NormalizedExecutionGraph(
                execution=Execution(execution_id=row["execution_id"]), operations=operations
            )
            repeated_patterns = derive_repeated_patterns(graph)
            row["repeated_work"] = len(repeated_patterns)
            extra_cost = 0.0
            extra_time = 0.0
            extra_tokens = 0.0
            extra_tokens_seen = False
            extra_cost_complete = True
            operation_by_id = {operation.operation_id: operation for operation in operations}
            for pattern in repeated_patterns:
                pattern_operations = [
                    operation_by_id[operation_id]
                    for operation_id in pattern.get("operation_ids", [])
                    if operation_id in operation_by_id
                ]
                width = len(pattern.get("pattern_keys", []))
                for operation in pattern_operations[width:]:
                    extra_time += _operation_duration(operation)
                    cost = operation.attributes.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        extra_cost += float(cost)
                    elif operation.kind in {"model", "tool"}:
                        extra_cost_complete = False
                    tokens = operation.attributes.get("total_tokens")
                    if isinstance(tokens, (int, float)):
                        extra_tokens += float(tokens)
                        extra_tokens_seen = True
            row["extra_time_seconds"] = extra_time
            row["extra_work_cost"] = extra_cost if extra_cost_complete and repeated_patterns else None
            row["extra_work_tokens"] = extra_tokens if extra_tokens_seen and repeated_patterns else None
            failure = self._failure_for_operations(operations)
            if failure.get("primary_break_point"):
                row["failure_location"] = failure["primary_break_point"]
                row["failure_key"] = failure.get("primary_break_point_key")
        if python_filter:
            rows = [row for row in rows if str(row["execution_id"]) in operations_by_execution]
            if limit is not None:
                rows = rows[:limit]
        return self._remember(cache_key, rows)

    def overview(self, filters: FilterState = FilterState()) -> dict[str, Any]:
        rows = self.execution_rows(filters, limit=None)
        total = len(rows)
        live = sum(row.get("status") == "running" for row in rows)
        # A still-running execution must never be counted as "completed" (not
        # yet true) nor "broke" (not yet final, even if some operation has
        # already errored mid-run and may still be retried) -- same
        # live-takes-precedence rule as the replay view and outcome_label.
        failed = sum(int(row.get("failure_count") or 0) > 0 and row.get("status") != "running" for row in rows)
        repeated = sum(int(row.get("repeated_work") or 0) > 0 for row in rows)
        known_cost_values = [float(row["known_cost"]) for row in rows if row.get("known_cost") is not None]
        known_cost = sum(known_cost_values) if known_cost_values else None
        durations = [float(row["duration_seconds"]) for row in rows if row.get("duration_seconds") is not None]
        completed = total - failed - live
        metrics = dashboard_metrics(rows, business_available=self.capabilities().business_outcomes)
        return {
            "runs": total,
            "completed": completed,
            "failed": failed,
            "live": live,
            "repeated": repeated,
            "known_cost": known_cost,
            "cost_coverage": sum(row.get("known_cost") is not None for row in rows) / total if total else 0,
            "durations": durations,
            "rows": rows,
            "metrics": metrics,
        }

    def dashboard_metrics(self, filters: FilterState = FilterState()) -> dict[str, Any]:
        """Return one filtered read model shared by all dashboard pages."""

        return dashboard_metrics(
            self.execution_rows(filters, limit=None), business_available=self.capabilities().business_outcomes
        )

    def get_execution_summary(self, filters: FilterState = FilterState()) -> ExecutionSummary:
        """Return the semantic execution summary for a filtered population."""

        metrics = self.dashboard_metrics(filters)
        cost_summary = self.get_cost_summary(filters)
        return ExecutionSummary(
            total_runs=int(metrics["total_runs"]),
            successful_runs=int(metrics["completed_runs"]),
            failed_runs=int(metrics["failed_runs"]),
            running_runs=int(metrics["running_runs"]),
            recovered_runs=int(metrics["recovered_runs"]),
            extra_work_runs=int(metrics["extra_work_runs"]),
            avg_duration_seconds=(float(metrics["time_per_run"]) if metrics["time_per_run"] is not None else None),
            measured_cost=cost_summary.measured_cost,
            cost_coverage=cost_summary.cost.coverage,
            business_successful_runs=int(metrics["business_successful_runs"]),
            business_unsuccessful_runs=int(metrics["business_unsuccessful_runs"]),
            business_reported_runs=int(metrics["business_reported_runs"]),
            terminal_runs=int(metrics["terminal_runs"]),
            unknown_runs=int(metrics["unknown_runs"]),
            attention_runs=int(metrics["attention_runs"]),
            runtime_success_rate=float(metrics["runtime_success_rate"]),
        )

    def get_product_goal_summary(self, filters: FilterState = FilterState()) -> ProductGoalSummary:
        """Aggregate explicitly reported product goals without inferring from runtime health."""

        rows = self.execution_rows(filters, limit=None)
        by_execution = {str(row["execution_id"]): row for row in rows}
        goals: dict[str, dict[str, Any]] = {}
        if self._serving_is_complete():
            semantic_goals = self._serving_goals_by_execution()
            goals = {
                execution_id: {
                    "expected_status": row.get("expected_outcome"),
                    "observed_status": row.get("application_outcome"),
                    "decision_correct": row.get("decision_correct"),
                    "product_goal_achieved": row.get("product_goal_achieved"),
                    "assurance_status": row.get("assurance_status"),
                    "artifact_valid": row.get("artifact_valid"),
                    "decision_evidence_sufficient": row.get("evidence_sufficient"),
                    "closest_blocker": row.get("closest_blocker"),
                    "threshold": row.get("threshold"),
                    "threshold_margin": row.get("threshold_margin"),
                    "targeted_research_required": row.get("targeted_research_required"),
                    "targeted_research_performed": row.get("targeted_research_performed"),
                    "product_goal_reported": row.get("product_goal_reported"),
                    **semantic_goals.get(execution_id, {}),
                }
                for execution_id, row in by_execution.items()
                if row.get("product_goal_reported") is True
            }
        elif self.capabilities().business_outcomes:
            for row in self._query(load_query("overview/product_goals")):
                execution_id = str(row["execution_id"])
                if execution_id in by_execution and execution_id not in goals:
                    goals[execution_id] = _json(row.get("attributes"))

        reported = list(goals.items())
        achieved_ids = {
            execution_id for execution_id, attributes in reported if attributes.get("product_goal_achieved") is True
        }
        decision_correct = sum(attributes.get("decision_correct") is True for _, attributes in reported)
        false_acceptances = sum(
            attributes.get("observed_status") == "accepted" and attributes.get("expected_status") != "accepted"
            for _, attributes in reported
        )
        false_rejections = sum(
            attributes.get("observed_status") == "rejected" and attributes.get("expected_status") != "rejected"
            for _, attributes in reported
        )
        escalation_errors = sum(
            (attributes.get("observed_status") == "escalated") != (attributes.get("expected_status") == "escalated")
            for _, attributes in reported
        )
        recovery_ids = {
            execution_id
            for execution_id, attributes in reported
            if attributes.get("targeted_research_required") is True
        }
        recovery_successes = sum(
            execution_id in achieved_ids
            and (
                attributes.get("targeted_research_performed") is True
                or attributes.get("required_path_observed") is True
            )
            for execution_id, attributes in reported
            if execution_id in recovery_ids
        )
        achieved_rows = [by_execution[execution_id] for execution_id in achieved_ids if execution_id in by_execution]
        operations_by_execution = self._operations_by_execution(filters)
        cost_totals = [
            _complete_operation_total(operations_by_execution.get(str(row["execution_id"]), []), "cost")
            for row in achieved_rows
        ]
        token_totals = [
            _complete_operation_total(operations_by_execution.get(str(row["execution_id"]), []), "tokens")
            for row in achieved_rows
        ]
        costs = [float(value) for applicable, value in cost_totals if applicable and value is not None]
        durations = [float(row["duration_seconds"]) for row in achieved_rows if row.get("duration_seconds") is not None]
        token_values = [float(value) for applicable, value in token_totals if applicable and value is not None]
        achieved_count = len(achieved_ids)
        return ProductGoalSummary(
            total_runs=len(rows),
            reported_runs=len(reported),
            achieved_runs=achieved_count,
            decision_correct_runs=decision_correct,
            false_acceptances=false_acceptances,
            false_rejections=false_rejections,
            escalation_errors=escalation_errors,
            targeted_research_runs=len(recovery_ids),
            targeted_research_successes=recovery_successes,
            cost_per_achieved_goal=sum(costs) / len(costs) if costs else None,
            cost_measured_achieved_runs=len(costs),
            time_per_achieved_goal=sum(durations) / len(durations) if durations else None,
            time_measured_achieved_runs=len(durations),
            tokens_per_achieved_goal=sum(token_values) / len(token_values) if token_values else None,
            token_measured_achieved_runs=len(token_values),
        )

    def product_goal_rows(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Return explicitly reported goal semantics joined to their execution facts.

        The result stays generic: it reads the documented goal-outcome attribute
        convention and does not introduce a Product Factory table or schema.  It
        gives frontends one repository-owned boundary for cost/speed/goal tradeoff
        analysis without exposing DuckDB or outcome-table details.
        """

        cache_key = ("product_goal_rows", filters.as_key())
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(list[dict[str, Any]], cached)
        execution_rows = self.execution_rows(filters, limit=None)
        by_execution = {str(row["execution_id"]): row for row in execution_rows}
        goals: dict[str, dict[str, Any]] = {}
        if self._serving_is_complete():
            goals = {
                execution_id: {
                    "case_id": row.get("case_id"),
                    "runtime_id": row.get("runtime_id"),
                    "model_profile": row.get("model_profile"),
                    "expected_status": row.get("expected_outcome"),
                    "observed_status": row.get("application_outcome"),
                    "decision_correct": row.get("decision_correct"),
                    "product_goal_achieved": row.get("product_goal_achieved"),
                    "assurance_status": row.get("assurance_status"),
                    "artifact_valid": row.get("artifact_valid"),
                    "decision_evidence_sufficient": row.get("evidence_sufficient"),
                    "closest_blocker": row.get("closest_blocker"),
                    "threshold": row.get("threshold"),
                    "threshold_margin": row.get("threshold_margin"),
                }
                for execution_id, row in by_execution.items()
                if row.get("product_goal_reported") is True
            }
        elif self.capabilities().business_outcomes:
            for goal_row in self._query(load_query("overview/product_goals")):
                execution_id = str(goal_row["execution_id"])
                if execution_id in by_execution and execution_id not in goals:
                    goals[execution_id] = _json(goal_row.get("attributes"))

        result: list[dict[str, Any]] = []
        operations_by_execution = self._operations_by_execution(filters)
        for execution_id, attributes in goals.items():
            execution = dict(by_execution[execution_id])
            execution["goal_attributes"] = attributes
            for field in (
                "contract_version",
                "case_id",
                "runtime_id",
                "model_profile",
                "expected_status",
                "observed_status",
                "decision_correct",
                "product_goal_achieved",
                "assurance_status",
                "artifact_valid",
                "decision_evidence_sufficient",
                "required_path_observed",
                "closest_blocker",
                "threshold",
                "threshold_margin",
            ):
                if field in attributes:
                    execution[field] = attributes[field]
            cost_applicable, complete_cost = _complete_operation_total(
                operations_by_execution.get(execution_id, []), "cost"
            )
            token_applicable, complete_tokens = _complete_operation_total(
                operations_by_execution.get(execution_id, []), "tokens"
            )
            execution["cost_applicable"] = cost_applicable
            execution["complete_measured_cost"] = complete_cost
            execution["tokens_applicable"] = token_applicable
            execution["complete_total_tokens"] = complete_tokens
            result.append(execution)
        return self._remember(cache_key, result)

    def goal_miss_summary(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Explain explicitly reported goal misses in application language."""

        grouped: dict[str, dict[str, Any]] = {}
        for row in self.product_goal_rows(filters):
            if row.get("product_goal_achieved") is not False:
                continue
            blocker = str(row.get("closest_blocker") or "Goal was not achieved")
            item = grouped.setdefault(
                blocker,
                {
                    "reason": blocker,
                    "runs": 0,
                    "known_cost": 0.0,
                    "cost_measured_runs": 0,
                    "time_seconds": 0.0,
                    "time_measured_runs": 0,
                },
            )
            item["runs"] += 1
            if row.get("complete_measured_cost") is not None:
                item["known_cost"] += float(row["complete_measured_cost"])
                item["cost_measured_runs"] += 1
            if row.get("duration_seconds") is not None:
                item["time_seconds"] += float(row["duration_seconds"])
                item["time_measured_runs"] += 1
        return sorted(grouped.values(), key=lambda item: (-int(item["runs"]), str(item["reason"])))

    def goal_trend(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Return daily goal success, elapsed time, and cost with honest denominators."""

        grouped: dict[str, dict[str, Any]] = {}
        for row in self.product_goal_rows(filters):
            started_at = row.get("started_at")
            date_method = getattr(started_at, "date", None)
            day = date_method().isoformat() if callable(date_method) else str(started_at or "Unknown")[:10]
            item = grouped.setdefault(
                day,
                {
                    "date": day,
                    "reported_runs": 0,
                    "achieved_runs": 0,
                    "duration_seconds": 0.0,
                    "duration_runs": 0,
                    "measured_cost": 0.0,
                    "cost_runs": 0,
                },
            )
            item["reported_runs"] += 1
            achieved = row.get("product_goal_achieved") is True
            item["achieved_runs"] += int(achieved)
            if achieved and row.get("duration_seconds") is not None:
                item["duration_seconds"] += float(row["duration_seconds"])
                item["duration_runs"] += 1
            if achieved and row.get("complete_measured_cost") is not None:
                item["measured_cost"] += float(row["complete_measured_cost"])
                item["cost_runs"] += 1
        result: list[dict[str, Any]] = []
        for item in grouped.values():
            result.append(
                {
                    **item,
                    "success_rate": item["achieved_runs"] / item["reported_runs"],
                    "time_per_achieved_goal": (
                        item["duration_seconds"] / item["duration_runs"] if item["duration_runs"] else None
                    ),
                    "cost_per_achieved_goal": (
                        item["measured_cost"] / item["cost_runs"] if item["cost_runs"] else None
                    ),
                }
            )
        return sorted(result, key=lambda item: str(item["date"]))

    def _evaluation_facts(self) -> list[dict[str, Any]]:
        cache_key = ("evaluation_facts",)
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            return cast(list[dict[str, Any]], cached)
        if "semantic_facts" not in self._serving_tables:
            return self._remember(cache_key, [])
        return self._remember(
            cache_key,
            self._query(
                "SELECT execution_id, name, value, score, label, observed_at, attributes "
                "FROM serving.semantic_facts WHERE record_type = 'evaluation'"
            ),
        )

    def _latest_evaluation_facts(self, allowed: set[str] | None = None) -> list[dict[str, Any]]:
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for fact in self._evaluation_facts():
            execution_id = str(fact["execution_id"])
            if allowed is not None and execution_id not in allowed:
                continue
            attributes = _json(fact.get("attributes"))
            key = str(attributes.get("evaluation_key") or fact.get("name") or "Evaluation")
            existing = latest.get((execution_id, key))
            if existing is None or str(fact.get("observed_at") or "") >= str(existing.get("observed_at") or ""):
                latest[(execution_id, key)] = fact
        return list(latest.values())

    def evaluation_summary(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Aggregate reported evaluations without interpreting contract-specific names."""

        if "semantic_facts" not in self._serving_tables:
            return []
        allowed = {str(row["execution_id"]) for row in self.execution_rows(filters, limit=None)}
        grouped: dict[str, dict[str, Any]] = {}
        for row in self._latest_evaluation_facts(allowed):
            attributes = _json(row.get("attributes"))
            key = str(attributes.get("evaluation_key") or row.get("name") or "Evaluation")
            item = grouped.setdefault(
                key,
                {
                    "key": key,
                    "name": str(row.get("name") or key),
                    "description": attributes.get("evaluation_description"),
                    "unit": attributes.get("unit"),
                    "target": attributes.get("target"),
                    "direction": attributes.get("direction"),
                    "reported_runs": 0,
                    "score_total": 0.0,
                    "score_runs": 0,
                    "labels": {},
                },
            )
            item["reported_runs"] += 1
            if row.get("score") is not None:
                item["score_total"] += float(row["score"])
                item["score_runs"] += 1
            label = row.get("label")
            if label is not None:
                labels = item["labels"]
                labels[str(label)] = labels.get(str(label), 0) + 1
        result: list[dict[str, Any]] = []
        for item in grouped.values():
            result.append(
                {
                    **item,
                    "average_score": (item["score_total"] / item["score_runs"] if item["score_runs"] else None),
                }
            )
        return sorted(result, key=lambda item: (-int(item["reported_runs"]), str(item["name"])))

    @staticmethod
    def _evaluation_met_target(row: dict[str, Any], attributes: dict[str, Any]) -> bool | None:
        passed = attributes.get("passed")
        if isinstance(passed, bool):
            return passed
        score = row.get("score") if row.get("score") is not None else row.get("value")
        target = attributes.get("target")
        direction = str(attributes.get("direction") or "equal").casefold()
        if isinstance(score, (int, float)) and isinstance(target, (int, float)):
            if direction in {"lower_is_better", "max", "at_most", "<="}:
                return float(score) <= float(target)
            if direction in {"higher_is_better", "min", "at_least", ">="}:
                return float(score) >= float(target)
            return float(score) == float(target)
        observed = row.get("value") if row.get("value") is not None else row.get("label")
        if target is not None and observed is not None:
            return bool(observed == target)
        return None

    def goal_assurance(
        self,
        filters: FilterState = FilterState(),
    ) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
        """Aggregate product goals and the strength of their declared evaluation evidence."""

        rows = self.execution_rows(filters, limit=None)
        allowed = {str(row["execution_id"]) for row in rows}
        evaluations_by_execution: dict[str, list[dict[str, Any]]] = {}
        if "semantic_facts" in self._serving_tables:
            for fact in self._latest_evaluation_facts(allowed):
                execution_id = str(fact["execution_id"])
                if execution_id in allowed:
                    evaluations_by_execution.setdefault(execution_id, []).append(fact)

        definitions = {str(item.get("contract_hash") or ""): item for item in self.contract_definitions(filters)}
        grouped: dict[str, dict[str, Any]] = {}
        summary: dict[str, int | float] = {
            "reported_runs": 0,
            "achieved_runs": 0,
            "assured_runs": 0,
            "attention_runs": 0,
            "not_achieved_runs": 0,
            "unassessed_runs": 0,
        }
        for row in rows:
            if row.get("product_goal_reported") is not True:
                continue
            contract_hash = str(row.get("contract_hash") or "unversioned")
            definition = definitions.get(contract_hash, {})
            goal_definition = definition.get("product_goal") if isinstance(definition.get("product_goal"), dict) else {}
            goal_name = str(
                (goal_definition or {}).get("name")
                or row.get("contract_name")
                or definition.get("contract_name")
                or "Business goal"
            )
            description = (goal_definition or {}).get("description")
            goal_key = f"{goal_name.casefold()}::{str(description or '').casefold()}"
            item = grouped.setdefault(
                goal_key,
                {
                    "goal_id": goal_key,
                    "contract_hashes": [],
                    "contract_name": row.get("contract_name") or definition.get("contract_name"),
                    "goal_name": goal_name,
                    "description": description,
                    "runs": 0,
                    "achieved_runs": 0,
                    "assured_runs": 0,
                    "attention_runs": 0,
                    "not_achieved_runs": 0,
                    "unassessed_runs": 0,
                    "evaluations": {},
                },
            )
            if contract_hash not in item["contract_hashes"]:
                item["contract_hashes"].append(contract_hash)
            item["runs"] += 1
            summary["reported_runs"] = int(summary["reported_runs"]) + 1
            achieved = row.get("product_goal_achieved") is True
            if not achieved:
                item["not_achieved_runs"] += 1
                summary["not_achieved_runs"] = int(summary["not_achieved_runs"]) + 1
                continue
            item["achieved_runs"] += 1
            summary["achieved_runs"] = int(summary["achieved_runs"]) + 1
            facts = evaluations_by_execution.get(str(row["execution_id"]), [])
            for fact in facts:
                attributes = _json(fact.get("attributes"))
                met = self._evaluation_met_target(fact, attributes)
                if met is None:
                    continue
                key = str(attributes.get("evaluation_key") or fact.get("name") or "Evaluation")
                evaluation = item["evaluations"].setdefault(
                    key,
                    {
                        "key": key,
                        "name": str(fact.get("name") or key),
                        "description": attributes.get("evaluation_description"),
                        "unit": attributes.get("unit"),
                        "target": attributes.get("target"),
                        "direction": attributes.get("direction"),
                        "reported_runs": 0,
                        "passed_runs": 0,
                        "attention_runs": 0,
                        "score_total": 0.0,
                        "score_runs": 0,
                    },
                )
                evaluation["reported_runs"] += 1
                evaluation["passed_runs"] += int(met)
                evaluation["attention_runs"] += int(not met)
                if isinstance(fact.get("score"), (int, float)):
                    evaluation["score_total"] += float(fact["score"])
                    evaluation["score_runs"] += 1
            explicit_assurance = _goal_assurance_state(row)
            if explicit_assurance == "needs_attention":
                item["attention_runs"] += 1
                summary["attention_runs"] = int(summary["attention_runs"]) + 1
            elif explicit_assurance == "assured":
                item["assured_runs"] += 1
                summary["assured_runs"] = int(summary["assured_runs"]) + 1
            else:
                item["unassessed_runs"] += 1
                summary["unassessed_runs"] = int(summary["unassessed_runs"]) + 1

        portfolio: list[dict[str, Any]] = []
        for item in grouped.values():
            evaluations = []
            for evaluation in item.pop("evaluations").values():
                score_runs = int(evaluation.pop("score_runs"))
                score_total = float(evaluation.pop("score_total"))
                evaluations.append({**evaluation, "average_score": score_total / score_runs if score_runs else None})
            achieved_runs = int(item["achieved_runs"])
            assessed_runs = int(item["assured_runs"]) + int(item["attention_runs"])
            attention = sorted(evaluations, key=lambda value: (-int(value["attention_runs"]), str(value["name"])))
            portfolio.append(
                {
                    **item,
                    "contract_hash": item["contract_hashes"][0] if len(item["contract_hashes"]) == 1 else None,
                    "contract_count": len(item["contract_hashes"]),
                    "success_rate": achieved_runs / int(item["runs"]) if item["runs"] else 0.0,
                    "assurance_rate": int(item["assured_runs"]) / achieved_runs if achieved_runs else 0.0,
                    "assessment_coverage": assessed_runs / achieved_runs if achieved_runs else 0.0,
                    "top_attention": attention[0] if attention and attention[0]["attention_runs"] else None,
                    "evaluations": evaluations,
                }
            )
        achieved_total = int(summary["achieved_runs"])
        assessed_total = int(summary["assured_runs"]) + int(summary["attention_runs"])
        summary["assurance_rate"] = int(summary["assured_runs"]) / achieved_total if achieved_total else 0.0
        summary["attention_rate"] = int(summary["attention_runs"]) / achieved_total if achieved_total else 0.0
        summary["assessment_coverage"] = assessed_total / achieved_total if achieved_total else 0.0
        return sorted(portfolio, key=lambda item: (-int(item["runs"]), str(item["goal_name"]))), summary

    def _measurement_summary(
        self, filters: FilterState, *, measurement: str
    ) -> tuple[MeasurementCoverage, float | None, list[float], float | None, float | None]:
        rows = self.execution_rows(filters, limit=None)
        operations_by_execution = self._operations_by_execution(filters)
        applicable = complete = partial = missing = eligible_total = measured_total = 0
        measured_subtotal = 0.0
        measured_any = False
        complete_run_totals: list[float] = []
        model_subtotal = tool_subtotal = 0.0
        model_seen = tool_seen = False
        for row in rows:
            operations = operations_by_execution.get(str(row["execution_id"]), [])
            eligible = [
                operation
                for operation in operations
                if (_cost_eligible(operation) if measurement == "cost" else _token_eligible(operation))
            ]
            if not eligible:
                continue
            applicable += 1
            field = "cost_usd" if measurement == "cost" else "total_tokens"
            measured = [
                operation for operation in eligible if isinstance(operation.attributes.get(field), (int, float))
            ]
            eligible_total += len(eligible)
            measured_total += len(measured)
            run_total = sum(float(operation.attributes[field]) for operation in measured)
            if measured:
                measured_subtotal += run_total
                measured_any = True
            if len(measured) == len(eligible):
                complete += 1
                complete_run_totals.append(run_total)
            elif measured:
                partial += 1
            else:
                missing += 1
            if measurement == "cost":
                for operation in measured:
                    value = float(operation.attributes[field])
                    if operation.kind == "model":
                        model_subtotal += value
                        model_seen = True
                    elif operation.kind == "tool":
                        tool_subtotal += value
                        tool_seen = True
        coverage = MeasurementCoverage(
            total_runs=len(rows),
            applicable_runs=applicable,
            complete_runs=complete,
            partial_runs=partial,
            missing_runs=missing,
            not_applicable_runs=len(rows) - applicable,
            eligible_operations=eligible_total,
            measured_operations=measured_total,
        )
        return (
            coverage,
            measured_subtotal if measured_any else None,
            complete_run_totals,
            model_subtotal if model_seen else None,
            tool_subtotal if tool_seen else None,
        )

    def get_cost_summary(self, filters: FilterState = FilterState()) -> CostSummary:
        """Return direct measurements with explicit applicability and completeness."""

        cost, measured_cost, complete_costs, model_cost, tool_cost = self._measurement_summary(
            filters, measurement="cost"
        )
        tokens, total_tokens, _complete_tokens, _unused_model, _unused_tool = self._measurement_summary(
            filters, measurement="tokens"
        )
        operations = self._filtered_operations(filters)
        input_values = [
            float(operation.attributes["input_tokens"])
            for operation in operations
            if operation.kind == "model" and isinstance(operation.attributes.get("input_tokens"), (int, float))
        ]
        output_values = [
            float(operation.attributes["output_tokens"])
            for operation in operations
            if operation.kind == "model" and isinstance(operation.attributes.get("output_tokens"), (int, float))
        ]
        return CostSummary(
            measured_cost=measured_cost,
            model_cost=model_cost,
            tool_cost=tool_cost,
            cost_coverage=cost.coverage,
            measured_cost_per_run=_average(complete_costs),
            input_tokens=sum(input_values) if input_values else None,
            output_tokens=sum(output_values) if output_values else None,
            total_tokens=total_tokens,
            token_runs=tokens.complete_runs,
            cost=cost,
            tokens=tokens,
        )

    def get_overview_snapshot(self, filters: FilterState = FilterState()) -> OverviewSnapshot:
        """Read every overview section from one connection and filtered population.

        Public repository methods remain independently usable. Within this scoped
        read, their shared execution, operation, capability, goal, and contract
        populations are memoized so the endpoint does not rescan them.
        """

        with self._overview_read_session():
            rows = self.execution_rows(filters, limit=None)
            runtime_breakdown: dict[str, int] = {}
            outcome_breakdown: dict[str, int] = {}
            for row in rows:
                runtime = runtime_state(row)
                runtime_breakdown[runtime] = runtime_breakdown.get(runtime, 0) + 1
                outcome = row.get("application_outcome") or row.get("business_outcome")
                if outcome:
                    label = str(outcome)
                    outcome_breakdown[label] = outcome_breakdown.get(label, 0) + 1

            goal_portfolio, assurance_summary = self.goal_assurance(filters)
            return OverviewSnapshot(
                execution=self.get_execution_summary(filters),
                goals=self.get_product_goal_summary(filters),
                costs=self.get_cost_summary(filters),
                cost_unavailable=self.cost_unavailable_reasons(filters),
                models=tuple(self.get_model_breakdown(filters)),
                providers=tuple(self.get_provider_breakdown(filters)),
                workflows=tuple(self.workflow_performance(filters)),
                stages=tuple(self.workflow_stage_summary(filters)),
                runtime_breakdown=runtime_breakdown,
                outcome_breakdown=outcome_breakdown,
                failures=tuple(self.get_failure_summary(filters)[:8]),
                evaluations=tuple(self.evaluation_summary(filters)),
                goal_misses=tuple(self.goal_miss_summary(filters)),
                goal_trend=tuple(self.goal_trend(filters)),
                goal_portfolio=tuple(goal_portfolio),
                assurance_summary=assurance_summary,
                contracts=tuple(self.contract_definitions(filters)),
                metadata=self.get_metadata_snapshot(),
            )

    def cost_unavailable_reasons(self, filters: FilterState = FilterState()) -> dict[str, int]:
        """Count explicit model-cost diagnostic reasons in the filtered population."""

        reasons: dict[str, int] = {}
        for operation in self._filtered_operations(filters):
            reason = operation.attributes.get("cost_unavailable_reason")
            if operation.kind == "model" and isinstance(reason, str):
                reasons[reason] = reasons.get(reason, 0) + 1
        return dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0])))

    def get_provider_breakdown(self, filters: FilterState = FilterState()) -> list[ProviderSummary]:
        """Return performance grouped by provider."""

        return [
            ProviderSummary(**_performance_contract(row).to_dict()) for row in self.performance("provider", filters)
        ]

    def get_model_breakdown(self, filters: FilterState = FilterState()) -> list[ModelSummary]:
        """Return performance grouped by model."""

        return [ModelSummary(**_performance_contract(row).to_dict()) for row in self.performance("model", filters)]

    def get_failure_summary(self, filters: FilterState = FilterState()) -> list[FailureSummary]:
        """Return deterministic failure-stage groups."""

        return [
            FailureSummary(
                failure_location=str(row["failure_location"]),
                failure_key=str(row["failure_key"]),
                kind=str(row["kind"]),
                failures=int(row["failures"]),
                executions=int(row["executions"]),
                terminal_runs=int(row["terminal_runs"]),
                recovered_runs=int(row["recovered_runs"]),
                unknown_outcome_runs=int(row["unknown_outcome_runs"]),
                providers=str(row["providers"]) if row.get("providers") is not None else None,
                models=str(row["models"]) if row.get("models") is not None else None,
                time_seconds=float(row["time_seconds"]),
                known_cost=float(row["known_cost"]) if row.get("known_cost") is not None else None,
                total_tokens=float(row["total_tokens"]) if row.get("total_tokens") is not None else None,
                affected_run_time_seconds=float(row["affected_run_time_seconds"]),
                affected_run_cost=(
                    float(row["affected_run_cost"]) if row.get("affected_run_cost") is not None else None
                ),
                affected_run_tokens=(
                    float(row["affected_run_tokens"]) if row.get("affected_run_tokens") is not None else None
                ),
            )
            for row in self.failures(filters)
        ]

    def get_performance_summary(
        self, dimension: str = "provider", filters: FilterState = FilterState()
    ) -> list[PerformanceSummary]:
        """Return performance grouped by a supported semantic dimension."""

        return [_performance_contract(row) for row in self.performance(dimension, filters)]

    def get_path_summary(self, filters: FilterState = FilterState()) -> list[PathSummary]:
        """Return canonical path groups."""

        return [
            PathSummary(
                path=str(row["path"]),
                steps=tuple(str(step) for step in row["steps"]),
                path_signature=str(row["path_signature"]),
                executions=int(row["executions"]),
                completed=int(row["completed"]),
                failures=int(row["failures"]),
                failure_reports=int(row["failure_reports"]),
                time_seconds=float(row["time_seconds"]),
                usual_seconds=float(row["usual_seconds"]) if row.get("usual_seconds") is not None else None,
                known_cost=float(row["known_cost"]) if row.get("known_cost") is not None else None,
                total_tokens=float(row["total_tokens"]) if row.get("total_tokens") is not None else None,
            )
            for row in self.paths(filters)
        ]

    @staticmethod
    def _insight_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
        durations = [float(row["duration_seconds"]) for row in samples if row.get("duration_seconds") is not None]
        costs = [float(row["known_cost"]) for row in samples if row.get("known_cost") is not None]
        tokens = [float(row["total_tokens"]) for row in samples if row.get("total_tokens") is not None]
        calls = [float(row["calls"]) for row in samples if row.get("calls") is not None]
        reported_goals = [row for row in samples if row.get("product_goal_achieved") is not None]
        reported_decisions = [row for row in samples if row.get("decision_correct") is not None]
        return {
            "runs": len(samples),
            "avg_duration_seconds": _average(durations),
            "p50_duration_seconds": _percentile(durations, 0.50),
            "p95_duration_seconds": _percentile(durations, 0.95),
            "avg_cost_per_run": _average(costs),
            "avg_tokens_per_run": _average(tokens),
            "avg_calls_per_run": _average(calls),
            "cost_coverage": len(costs) / len(samples) if samples else 0.0,
            "goal_rate": (
                sum(row.get("product_goal_achieved") is True for row in reported_goals) / len(reported_goals)
                if reported_goals
                else None
            ),
            "decision_correctness_rate": (
                sum(row.get("decision_correct") is True for row in reported_decisions) / len(reported_decisions)
                if reported_decisions
                else None
            ),
            "recovered_runs": sum(row.get("runtime_outcome") == "recovered" for row in samples),
        }

    def get_comparison_insights(self, dimension: str, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Return operation-attributable model/provider comparisons.

        A mixed-model run contributes only the calls, tokens, cost and model time
        attributable to each participant. This avoids charging the full run to
        every model or provider that appeared in it.
        """

        if dimension not in {"model", "provider"}:
            raise ValueError(f"unsupported comparison dimension: {dimension}")
        with self._overview_read_session():
            rows = self.execution_rows(filters, limit=None)
            facts = self.participant_facts(filters, dimension)
            if facts:
                return self._comparison_from_participant_facts(rows, facts, dimension)
            operations_by_execution = self._operations_by_execution(filters)
            groups: dict[str, dict[str, Any]] = {}
            for row in rows:
                per_participant: dict[str, dict[str, Any]] = {}
                for operation in operations_by_execution.get(str(row["execution_id"]), []):
                    if operation.kind != "model":
                        continue
                    identity = _participant_identity(operation, dimension)
                    if identity is None:
                        continue
                    participant_id, label, provider_id, model_id, model_family, vendor_id = identity
                    sample = per_participant.setdefault(
                        participant_id,
                        {
                            **row,
                            "participant_id": participant_id,
                            "label": label,
                            "dimension": dimension,
                            "provider_id": provider_id,
                            "model_id": model_id,
                            "model_family": model_family,
                            "vendor_id": vendor_id,
                            "_operations": [],
                            "known_cost": 0.0,
                            "total_tokens": 0.0,
                            "calls": 0,
                            "_cost_seen": False,
                            "_cost_eligible": 0,
                            "_cost_measured": 0,
                            "_tokens_seen": False,
                            "_token_eligible": 0,
                            "_token_measured": 0,
                        },
                    )
                    if sample["vendor_id"] is None and vendor_id is not None:
                        sample["vendor_id"] = vendor_id
                    sample["_operations"].append(operation)
                    sample["calls"] += 1
                    cost = operation.attributes.get("cost_usd")
                    sample["_cost_eligible"] += int(_cost_eligible(operation))
                    if isinstance(cost, (int, float)):
                        sample["known_cost"] += float(cost)
                        sample["_cost_seen"] = True
                        sample["_cost_measured"] += 1
                    tokens = operation.attributes.get("total_tokens")
                    sample["_token_eligible"] += int(_token_eligible(operation))
                    if isinstance(tokens, (int, float)):
                        sample["total_tokens"] += float(tokens)
                        sample["_tokens_seen"] = True
                        sample["_token_measured"] += 1
                for participant_id, sample in per_participant.items():
                    participant_operations = sample.pop("_operations")
                    sample["duration_seconds"] = _active_seconds(participant_operations)
                    sample["call_durations"] = [_operation_duration(operation) for operation in participant_operations]
                    cost_seen = sample.pop("_cost_seen")
                    if not cost_seen or sample["_cost_measured"] < sample["_cost_eligible"]:
                        sample["known_cost"] = None
                    tokens_seen = sample.pop("_tokens_seen")
                    if not tokens_seen or sample["_token_measured"] < sample["_token_eligible"]:
                        sample["total_tokens"] = None
                    bucket = groups.setdefault(
                        participant_id,
                        {
                            "label": sample["label"],
                            "participant_id": participant_id,
                            "dimension": dimension,
                            "provider_id": sample["provider_id"],
                            "model_id": sample["model_id"],
                            "model_family": sample["model_family"],
                            "vendor_id": sample["vendor_id"],
                            "samples": [],
                        },
                    )
                    bucket["samples"].append(sample)

            evaluations_by_execution: dict[str, list[dict[str, Any]]] = {}
            if "semantic_facts" in self._serving_tables:
                for fact in self._latest_evaluation_facts({str(row["execution_id"]) for row in rows}):
                    if fact.get("score") is not None:
                        evaluations_by_execution.setdefault(str(fact["execution_id"]), []).append(fact)
            results = []
            for bucket in groups.values():
                samples = bucket["samples"]
                evaluation_groups: dict[str, dict[str, Any]] = {}
                for sample in samples:
                    for fact in evaluations_by_execution.get(str(sample["execution_id"]), []):
                        attributes = _json(fact.get("attributes"))
                        key = str(attributes.get("evaluation_key") or fact.get("name") or "Evaluation")
                        name = str(fact.get("name") or key)
                        evaluation = evaluation_groups.setdefault(
                            key,
                            {
                                "key": key,
                                "name": name,
                                "scores": [],
                                "target": attributes.get("target"),
                                "direction": attributes.get("direction") or "higher_is_better",
                            },
                        )
                        evaluation["scores"].append(float(fact["score"]))
                evaluations = []
                for evaluation in evaluation_groups.values():
                    scores = evaluation.pop("scores")
                    evaluations.append({**evaluation, "reported_runs": len(scores), "average_score": _average(scores)})
                insight = self._insight_summary(samples)
                call_durations = [duration for sample in samples for duration in sample["call_durations"]]
                cost_eligible = sum(int(sample["_cost_eligible"]) for sample in samples)
                cost_measured = sum(int(sample["_cost_measured"]) for sample in samples)
                token_eligible = sum(int(sample["_token_eligible"]) for sample in samples)
                token_measured = sum(int(sample["_token_measured"]) for sample in samples)
                results.append(
                    {
                        **{key: value for key, value in bucket.items() if key != "samples"},
                        **insight,
                        "p50_duration_seconds": _percentile(call_durations, 0.50),
                        "p95_duration_seconds": _percentile(call_durations, 0.95),
                        "scope": "cohort+direct-attribution",
                        "cohort_key": _cohort_key(samples),
                        "cost_eligible_operations": cost_eligible,
                        "cost_measured_operations": cost_measured,
                        "token_eligible_operations": token_eligible,
                        "token_measured_operations": token_measured,
                        "cost_coverage": cost_measured / cost_eligible if cost_eligible else 0.0,
                        "evaluations": evaluations,
                    }
                )
            return sorted(
                results, key=lambda item: (-int(item["runs"]), str(item["label"]), str(item["participant_id"]))
            )

    def _comparison_from_participant_facts(
        self, rows: list[dict[str, Any]], facts: list[dict[str, Any]], dimension: str
    ) -> list[dict[str, Any]]:
        by_execution = {str(row["execution_id"]): row for row in rows}
        groups: dict[str, list[dict[str, Any]]] = {}
        for fact in facts:
            execution = by_execution.get(str(fact["execution_id"]))
            if execution is None:
                continue
            cost_complete = int(fact.get("cost_eligible_operations") or 0) == int(
                fact.get("cost_measured_operations") or 0
            )
            token_complete = int(fact.get("token_eligible_operations") or 0) == int(
                fact.get("token_measured_operations") or 0
            )
            groups.setdefault(str(fact["participant_id"]), []).append(
                {
                    **execution,
                    "duration_seconds": fact.get("active_seconds"),
                    "known_cost": fact.get("measured_cost") if cost_complete else None,
                    "total_tokens": fact.get("total_tokens") if token_complete else None,
                    "calls": fact.get("calls"),
                    "call_durations": fact.get("call_durations") or [],
                    "fact": fact,
                }
            )
        allowed = set(by_execution)
        evaluations_by_execution: dict[str, list[dict[str, Any]]] = {}
        for fact in self._latest_evaluation_facts(allowed):
            if fact.get("score") is not None:
                evaluations_by_execution.setdefault(str(fact["execution_id"]), []).append(fact)
        result = []
        for participant_id, samples in groups.items():
            first = samples[0]["fact"]
            vendor_id = next(
                (sample["fact"].get("vendor_id") for sample in samples if sample["fact"].get("vendor_id") is not None),
                None,
            )
            evaluation_groups: dict[str, dict[str, Any]] = {}
            for sample in samples:
                for evaluation_fact in evaluations_by_execution.get(str(sample["execution_id"]), []):
                    attributes = _json(evaluation_fact.get("attributes"))
                    key = str(attributes.get("evaluation_key") or evaluation_fact.get("name") or "Evaluation")
                    name = str(evaluation_fact.get("name") or key)
                    evaluation = evaluation_groups.setdefault(
                        key,
                        {
                            "key": key,
                            "name": name,
                            "scores": [],
                            "target": attributes.get("target"),
                            "direction": attributes.get("direction") or "higher_is_better",
                        },
                    )
                    evaluation["scores"].append(float(evaluation_fact["score"]))
            evaluations = []
            for evaluation in evaluation_groups.values():
                scores = evaluation.pop("scores")
                evaluations.append({**evaluation, "reported_runs": len(scores), "average_score": _average(scores)})
            call_durations = [float(value) for sample in samples for value in sample["call_durations"]]
            cost_eligible = sum(int(sample["fact"].get("cost_eligible_operations") or 0) for sample in samples)
            cost_measured = sum(int(sample["fact"].get("cost_measured_operations") or 0) for sample in samples)
            token_eligible = sum(int(sample["fact"].get("token_eligible_operations") or 0) for sample in samples)
            token_measured = sum(int(sample["fact"].get("token_measured_operations") or 0) for sample in samples)
            result.append(
                {
                    "participant_id": participant_id,
                    "dimension": dimension,
                    "label": first["label"],
                    "provider_id": first.get("provider_id"),
                    "model_id": first.get("model_id"),
                    "model_family": first.get("model_family"),
                    "vendor_id": vendor_id,
                    **self._insight_summary(samples),
                    "p50_duration_seconds": _percentile(call_durations, 0.50),
                    "p95_duration_seconds": _percentile(call_durations, 0.95),
                    "cost_coverage": cost_measured / cost_eligible if cost_eligible else 0.0,
                    "cost_eligible_operations": cost_eligible,
                    "cost_measured_operations": cost_measured,
                    "token_eligible_operations": token_eligible,
                    "token_measured_operations": token_measured,
                    "scope": "cohort+direct-attribution",
                    "cohort_key": _cohort_key(samples),
                    "evaluations": evaluations,
                }
            )
        return sorted(result, key=lambda item: (-int(item["runs"]), str(item["label"]), str(item["participant_id"])))

    def get_workflow_insights(self, filters: FilterState = FilterState(), limit: int = 10) -> dict[str, Any]:
        """Return canonical runtime portfolio, stage contribution and compact path variants."""

        with self._overview_read_session():
            rows = self.execution_rows(filters, limit=None)
            operations_by_execution = self._operations_by_execution(filters)
            workflow_samples: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                runtime_id = str(row.get("runtime_id") or row.get("workflow") or "unknown")
                workflow_samples.setdefault(runtime_id, []).append({**row, "calls": row.get("operation_count")})
            portfolio = []
            for runtime_id, samples in workflow_samples.items():
                label = runtime_id.replace("_", " ").strip().title() or "Unknown"
                portfolio.append({"runtime_id": runtime_id, "label": label, **self._insight_summary(samples)})
            portfolio.sort(key=lambda item: (-int(item["runs"]), str(item["label"])))
            portfolio = portfolio[: max(1, min(limit, 10))]
            visible_runtimes = {str(item["runtime_id"]) for item in portfolio}

            stage_groups: dict[tuple[str, str], dict[str, Any]] = {}
            path_groups: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
            row_by_id = {str(row["execution_id"]): row for row in rows}
            for execution_id, execution in row_by_id.items():
                runtime_id = str(execution.get("runtime_id") or execution.get("workflow") or "unknown")
                if runtime_id not in visible_runtimes:
                    continue
                operations = operations_by_execution.get(execution_id, [])
                semantic = [operation for operation in operations if operation.kind == "workflow_stage"]
                if not semantic:
                    semantic = [
                        operation for operation in operations if operation.kind in {"graph_node", "component", "tool"}
                    ]
                ordered = sorted(
                    semantic,
                    key=lambda operation: operation.started_at.timestamp() if operation.started_at else 0.0,
                )
                steps: list[str] = []
                for operation in ordered:
                    stage = display_operation(operation)
                    if not steps or steps[-1] != stage:
                        steps.append(stage)
                    key = (runtime_id, stage)
                    item = stage_groups.setdefault(
                        key,
                        {
                            "runtime_id": runtime_id,
                            "workflow": runtime_id.replace("_", " ").title(),
                            "label": stage,
                            "calls": 0,
                            "execution_ids": set(),
                            "time_seconds": 0.0,
                            "known_cost": 0.0,
                            "cost_seen": False,
                            "total_tokens": 0.0,
                            "tokens_seen": False,
                            "retries": 0,
                        },
                    )
                    item["calls"] += 1
                    item["execution_ids"].add(execution_id)
                    item["time_seconds"] += _operation_duration(operation)
                    item["retries"] += int((operation.attempt or 1) > 1)
                    cost = operation.attributes.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        item["known_cost"] += float(cost)
                        item["cost_seen"] = True
                    tokens = operation.attributes.get("total_tokens")
                    if isinstance(tokens, (int, float)):
                        item["total_tokens"] += float(tokens)
                        item["tokens_seen"] = True
                path_key = (runtime_id, tuple(steps or ["No semantic stages reported"]))
                path = path_groups.setdefault(
                    path_key,
                    {
                        "runtime_id": runtime_id,
                        "workflow": runtime_id.replace("_", " ").title(),
                        "steps": list(path_key[1]),
                        "runs": 0,
                        "durations": [],
                        "costs": [],
                        "tokens": [],
                        "retries": 0,
                        "recovered_runs": 0,
                    },
                )
                path["runs"] += 1
                if execution.get("duration_seconds") is not None:
                    path["durations"].append(float(execution["duration_seconds"]))
                if execution.get("known_cost") is not None:
                    path["costs"].append(float(execution["known_cost"]))
                if execution.get("total_tokens") is not None:
                    path["tokens"].append(float(execution["total_tokens"]))
                path["retries"] += sum(int((operation.attempt or 1) > 1) for operation in operations)
                path["recovered_runs"] += int(execution.get("runtime_outcome") == "recovered")

            stages: list[dict[str, Any]] = []
            for item in stage_groups.values():
                item["executions"] = len(item.pop("execution_ids"))
                item["known_cost"] = item["known_cost"] if item.pop("cost_seen") else None
                item["total_tokens"] = item["total_tokens"] if item.pop("tokens_seen") else None
                stages.append(item)
            stages.sort(key=lambda item: (-float(item["time_seconds"]), str(item["label"])))

            paths: list[dict[str, Any]] = []
            for item in path_groups.values():
                durations = item.pop("durations")
                costs = item.pop("costs")
                tokens = item.pop("tokens")
                item.update(
                    {
                        "p50_duration_seconds": _percentile(durations, 0.50),
                        "p95_duration_seconds": _percentile(durations, 0.95),
                        "avg_cost_per_run": _average(costs),
                        "avg_tokens_per_run": _average(tokens),
                    }
                )
                paths.append(item)
            paths.sort(key=lambda item: (-int(item["runs"]), str(item["workflow"]), item["steps"]))
            return {"items": portfolio, "stages": stages, "paths": paths[:10]}

    def get_issue_insights(self, filters: FilterState = FilterState()) -> dict[str, Any]:
        """Return actionable, run-linked exceptions and measurement coverage."""

        with self._overview_read_session():
            rows = self.execution_rows(filters, limit=None)
            operations_by_execution = self._operations_by_execution(filters)
            run_by_id = {str(row["execution_id"]): row for row in rows}
            failures: list[dict[str, Any]] = []
            retry_groups: dict[str, dict[str, Any]] = {}
            for execution_id, row in run_by_id.items():
                operations = operations_by_execution.get(execution_id, [])
                failure = self._failure_for_operations(operations)
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
            if "semantic_facts" in self._serving_tables:
                for fact in self._query(
                    "SELECT execution_id, name, score, attributes FROM serving.semantic_facts "
                    "WHERE record_type = 'evaluation'"
                ):
                    execution_id = str(fact["execution_id"])
                    if execution_id not in run_by_id or fact.get("score") is None:
                        continue
                    attributes = _json(fact.get("attributes"))
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
                    "cost_unavailable": self.cost_unavailable_reasons(filters),
                },
            }

    def performance(self, dimension: str, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Compare run performance across one user-facing dimension."""

        allowed = {"provider", "model", "workflow", "stage"}
        if dimension not in allowed:
            raise ValueError(f"unsupported performance dimension: {dimension}")
        rows = self.execution_rows(filters, limit=None)
        if dimension in {"provider", "model"}:
            facts = self.participant_facts(filters, dimension)
            if facts:
                by_execution = {str(row["execution_id"]): row for row in rows}
                fact_groups: dict[str, list[dict[str, Any]]] = {}
                for fact in facts:
                    execution = by_execution.get(str(fact["execution_id"]))
                    if execution is not None:
                        fact_groups.setdefault(str(fact["participant_id"]), []).append({**fact, "execution": execution})
                materialized_result = []
                for samples in fact_groups.values():
                    first = samples[0]
                    vendor_id = next(
                        (sample.get("vendor_id") for sample in samples if sample.get("vendor_id") is not None),
                        None,
                    )
                    states = [runtime_state(sample["execution"]) for sample in samples]
                    positive = [
                        sample
                        for sample, state in zip(samples, states, strict=True)
                        if state in {"completed", "recovered"}
                    ]
                    negative = [sample for sample, state in zip(samples, states, strict=True) if state == "failed"]
                    complete_cost_positive = [
                        sample
                        for sample in positive
                        if int(sample.get("cost_eligible_operations") or 0) > 0
                        and int(sample.get("cost_eligible_operations") or 0)
                        == int(sample.get("cost_measured_operations") or 0)
                    ]
                    complete_token_positive = [
                        sample
                        for sample in positive
                        if int(sample.get("token_eligible_operations") or 0) > 0
                        and int(sample.get("token_eligible_operations") or 0)
                        == int(sample.get("token_measured_operations") or 0)
                    ]
                    call_durations = [float(value) for sample in samples for value in sample.get("call_durations", [])]
                    cost_eligible = sum(int(sample.get("cost_eligible_operations") or 0) for sample in samples)
                    cost_measured = sum(int(sample.get("cost_measured_operations") or 0) for sample in samples)
                    token_eligible = sum(int(sample.get("token_eligible_operations") or 0) for sample in samples)
                    token_measured = sum(int(sample.get("token_measured_operations") or 0) for sample in samples)
                    measured_costs = [
                        float(sample["measured_cost"]) for sample in samples if sample.get("measured_cost") is not None
                    ]
                    token_values = [
                        float(sample["total_tokens"]) for sample in samples if sample.get("total_tokens") is not None
                    ]
                    materialized_result.append(
                        {
                            "participant_id": first["participant_id"],
                            "dimension": dimension,
                            "label": first["label"],
                            "provider_id": first.get("provider_id"),
                            "model_id": first.get("model_id"),
                            "model_family": first.get("model_family"),
                            "vendor_id": vendor_id,
                            "runs": len(samples),
                            "calls": sum(int(sample.get("calls") or 0) for sample in samples),
                            "completed": states.count("completed"),
                            "successful": sum(
                                sample["execution"].get("product_goal_achieved") is True for sample in samples
                            ),
                            "failed": states.count("failed"),
                            "recovered": states.count("recovered"),
                            "extra_work": sum(
                                int(sample["execution"].get("repeated_work") or 0) > 0 for sample in samples
                            ),
                            "measured_cost": sum(measured_costs) if measured_costs else None,
                            "cost_per_positive_run": _average(
                                [
                                    float(sample["measured_cost"])
                                    for sample in complete_cost_positive
                                    if sample.get("measured_cost") is not None
                                ]
                            ),
                            "time_per_positive_run": _average([float(sample["active_seconds"]) for sample in positive]),
                            "failed_run_cost": (
                                sum(
                                    float(sample["measured_cost"])
                                    for sample in negative
                                    if sample.get("measured_cost") is not None
                                )
                                if any(sample.get("measured_cost") is not None for sample in negative)
                                else None
                            ),
                            "total_tokens": sum(token_values) if token_values else None,
                            "tokens_per_positive_run": _average(
                                [
                                    float(sample["total_tokens"])
                                    for sample in complete_token_positive
                                    if sample.get("total_tokens") is not None
                                ]
                            ),
                            "failed_run_tokens": (
                                sum(
                                    float(sample["total_tokens"])
                                    for sample in negative
                                    if sample.get("total_tokens") is not None
                                )
                                if any(sample.get("total_tokens") is not None for sample in negative)
                                else None
                            ),
                            "failure_rate": states.count("failed") / len(samples),
                            "extra_work_rate": sum(
                                int(sample["execution"].get("repeated_work") or 0) > 0 for sample in samples
                            )
                            / len(samples),
                            "cost_coverage": cost_measured / cost_eligible if cost_eligible else 0.0,
                            "semantics": "cohort+direct-attribution",
                            "active_seconds": sum(float(sample.get("active_seconds") or 0.0) for sample in samples),
                            "p50_call_seconds": _percentile(call_durations, 0.50),
                            "p95_call_seconds": _percentile(call_durations, 0.95),
                            "cost_eligible_operations": cost_eligible,
                            "cost_measured_operations": cost_measured,
                            "token_eligible_operations": token_eligible,
                            "token_measured_operations": token_measured,
                        }
                    )
                return sorted(
                    materialized_result,
                    key=lambda item: (-int(item["runs"]), str(item["label"]), str(item["participant_id"])),
                )
            operations_by_execution = self._operations_by_execution(filters)
            groups: dict[str, dict[str, Any]] = {}
            for row in rows:
                per_participant: dict[str, dict[str, Any]] = {}
                for operation in operations_by_execution.get(str(row["execution_id"]), []):
                    identity = _participant_identity(operation, dimension)
                    if identity is None:
                        continue
                    participant_id, label, provider_id, model_id, model_family, vendor_id = identity
                    sample = per_participant.setdefault(
                        participant_id,
                        {
                            "operations": [],
                            "label": label,
                            "provider_id": provider_id,
                            "model_id": model_id,
                            "model_family": model_family,
                            "vendor_id": vendor_id,
                        },
                    )
                    if sample["vendor_id"] is None and vendor_id is not None:
                        sample["vendor_id"] = vendor_id
                    sample["operations"].append(operation)
                for participant_id, sample in per_participant.items():
                    group = groups.setdefault(
                        participant_id,
                        {
                            "participant_id": participant_id,
                            "dimension": dimension,
                            "label": sample["label"],
                            "provider_id": sample["provider_id"],
                            "model_id": sample["model_id"],
                            "model_family": sample["model_family"],
                            "vendor_id": sample["vendor_id"],
                            "samples": [],
                        },
                    )
                    if group["vendor_id"] is None and sample["vendor_id"] is not None:
                        group["vendor_id"] = sample["vendor_id"]
                    participant_operations = sample["operations"]
                    eligible_cost_operations = [
                        operation for operation in participant_operations if _cost_eligible(operation)
                    ]
                    measured_cost_operations = [
                        operation
                        for operation in eligible_cost_operations
                        if isinstance(operation.attributes.get("cost_usd"), (int, float))
                    ]
                    eligible_token_operations = [
                        operation for operation in participant_operations if _token_eligible(operation)
                    ]
                    measured_token_operations = [
                        operation
                        for operation in eligible_token_operations
                        if isinstance(operation.attributes.get("total_tokens"), (int, float))
                    ]
                    group["samples"].append(
                        {
                            "execution": row,
                            "operations": participant_operations,
                            "active_seconds": _active_seconds(participant_operations),
                            "cost": sum(
                                float(operation.attributes["cost_usd"]) for operation in measured_cost_operations
                            ),
                            "cost_eligible": len(eligible_cost_operations),
                            "cost_measured": len(measured_cost_operations),
                            "tokens": sum(
                                float(operation.attributes["total_tokens"]) for operation in measured_token_operations
                            ),
                            "token_eligible": len(eligible_token_operations),
                            "token_measured": len(measured_token_operations),
                        }
                    )
            result = []
            for group in groups.values():
                samples = group.pop("samples")
                run_states = [runtime_state(sample["execution"]) for sample in samples]
                positive = [
                    sample
                    for sample, state in zip(samples, run_states, strict=True)
                    if state in {"completed", "recovered"}
                ]
                negative = [sample for sample, state in zip(samples, run_states, strict=True) if state == "failed"]
                complete_cost_positive = [
                    sample
                    for sample in positive
                    if sample["cost_eligible"] > 0 and sample["cost_measured"] == sample["cost_eligible"]
                ]
                complete_token_positive = [
                    sample
                    for sample in positive
                    if sample["token_eligible"] > 0 and sample["token_measured"] == sample["token_eligible"]
                ]
                all_operations = [operation for sample in samples for operation in sample["operations"]]
                call_durations = [_operation_duration(operation) for operation in all_operations]
                cost_eligible = sum(sample["cost_eligible"] for sample in samples)
                cost_measured = sum(sample["cost_measured"] for sample in samples)
                token_eligible = sum(sample["token_eligible"] for sample in samples)
                token_measured = sum(sample["token_measured"] for sample in samples)
                measured_cost = sum(sample["cost"] for sample in samples) if cost_measured else None
                total_tokens = sum(sample["tokens"] for sample in samples) if token_measured else None
                result.append(
                    {
                        **group,
                        "runs": len(samples),
                        "calls": len(all_operations),
                        "completed": run_states.count("completed"),
                        "successful": sum(
                            sample["execution"].get("product_goal_achieved") is True for sample in samples
                        ),
                        "failed": run_states.count("failed"),
                        "recovered": run_states.count("recovered"),
                        "extra_work": sum(int(sample["execution"].get("repeated_work") or 0) > 0 for sample in samples),
                        "measured_cost": measured_cost,
                        "cost_per_positive_run": (
                            sum(sample["cost"] for sample in complete_cost_positive) / len(complete_cost_positive)
                            if complete_cost_positive
                            else None
                        ),
                        "time_per_positive_run": (
                            sum(sample["active_seconds"] for sample in positive) / len(positive) if positive else None
                        ),
                        "failed_run_cost": sum(sample["cost"] for sample in negative) if negative else None,
                        "total_tokens": total_tokens,
                        "tokens_per_positive_run": (
                            sum(sample["tokens"] for sample in complete_token_positive) / len(complete_token_positive)
                            if complete_token_positive
                            else None
                        ),
                        "failed_run_tokens": sum(sample["tokens"] for sample in negative) if negative else None,
                        "failure_rate": run_states.count("failed") / len(samples) if samples else 0.0,
                        "extra_work_rate": sum(
                            int(sample["execution"].get("repeated_work") or 0) > 0 for sample in samples
                        )
                        / len(samples)
                        if samples
                        else 0.0,
                        "cost_coverage": cost_measured / cost_eligible if cost_eligible else 0.0,
                        "semantics": "cohort+direct-attribution",
                        "active_seconds": sum(sample["active_seconds"] for sample in samples),
                        "p50_call_seconds": _percentile(call_durations, 0.50),
                        "p95_call_seconds": _percentile(call_durations, 0.95),
                        "cost_eligible_operations": cost_eligible,
                        "cost_measured_operations": cost_measured,
                        "token_eligible_operations": token_eligible,
                        "token_measured_operations": token_measured,
                    }
                )
            return sorted(
                result, key=lambda item: (-int(item["runs"]), str(item["label"]), str(item["participant_id"]))
            )
        operations_by_execution = self._operations_by_execution(filters)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            operations = operations_by_execution.get(str(row["execution_id"]), [])
            if dimension == "workflow":
                labels = {
                    display_operation(operation)
                    for operation in operations
                    if operation.kind in {"workflow", "pipeline"}
                }
            else:
                labels = {
                    display_stage(operation)
                    for operation in operations
                    if operation.kind in {"component", "operation", "tool"}
                }
            for label in labels or {"Unknown"}:
                grouped.setdefault(label, []).append(row)
        expanded: list[dict[str, Any]] = []
        for label, grouped_rows in grouped.items():
            expanded.extend(
                aggregate_performance(
                    [dict(row, **{dimension: label}) for row in grouped_rows],
                    dimension=dimension,
                    business_available=self.capabilities().business_outcomes,
                )
            )
        return sorted(expanded, key=lambda item: (-int(item["runs"]), str(item["label"])))

    def build_participant_facts(self, execution_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """Build disposable direct-attribution facts for ELT/rebuild."""

        rows = self.execution_rows(limit=None)
        if execution_ids is not None:
            rows = [row for row in rows if str(row["execution_id"]) in execution_ids]
        operations_by_execution = self._operations_by_execution()
        facts: list[dict[str, Any]] = []
        for row in rows:
            execution_id = str(row["execution_id"])
            operations = operations_by_execution.get(execution_id, [])
            for dimension in ("provider", "model"):
                grouped: dict[str, dict[str, Any]] = {}
                for operation in operations:
                    identity = _participant_identity(operation, dimension)
                    if identity is None:
                        continue
                    participant_id, label, provider_id, model_id, model_family, vendor_id = identity
                    item = grouped.setdefault(
                        participant_id,
                        {
                            "execution_id": execution_id,
                            "dimension": dimension,
                            "participant_id": participant_id,
                            "label": label,
                            "provider_id": provider_id,
                            "model_id": model_id,
                            "model_family": model_family,
                            "vendor_id": vendor_id,
                            "operations": [],
                        },
                    )
                    if item["vendor_id"] is None and vendor_id is not None:
                        item["vendor_id"] = vendor_id
                    item["operations"].append(operation)
                for item in grouped.values():
                    participant_operations = item.pop("operations")
                    cost_eligible = [operation for operation in participant_operations if _cost_eligible(operation)]
                    cost_measured = [
                        operation
                        for operation in cost_eligible
                        if isinstance(operation.attributes.get("cost_usd"), (int, float))
                    ]
                    token_eligible = [operation for operation in participant_operations if _token_eligible(operation)]
                    token_measured = [
                        operation
                        for operation in token_eligible
                        if isinstance(operation.attributes.get("total_tokens"), (int, float))
                    ]
                    facts.append(
                        {
                            **item,
                            "calls": len(participant_operations),
                            "active_seconds": _active_seconds(participant_operations),
                            "call_durations": [_operation_duration(operation) for operation in participant_operations],
                            "measured_cost": (
                                sum(float(operation.attributes["cost_usd"]) for operation in cost_measured)
                                if cost_measured
                                else None
                            ),
                            "total_tokens": (
                                sum(float(operation.attributes["total_tokens"]) for operation in token_measured)
                                if token_measured
                                else None
                            ),
                            "cost_eligible_operations": len(cost_eligible),
                            "cost_measured_operations": len(cost_measured),
                            "token_eligible_operations": len(token_eligible),
                            "token_measured_operations": len(token_measured),
                        }
                    )
        return facts

    def participant_facts(
        self, filters: FilterState = FilterState(), dimension: str | None = None
    ) -> list[dict[str, Any]]:
        if dimension not in {None, "provider", "model"}:
            raise ValueError(f"unsupported participant dimension: {dimension}")
        cache_key = ("participant_facts", filters.as_key())
        cached = self._cached(cache_key)
        if cached is not _CACHE_MISS:
            cached_rows = cast(list[dict[str, Any]], cached)
            return [row for row in cached_rows if dimension is None or row["dimension"] == dimension]
        allowed = {str(row["execution_id"]) for row in self.execution_rows(filters, limit=None)}
        rows: list[dict[str, Any]] = []
        if "participant_execution_facts" in self._tables:
            rows = [
                row
                for row in self._query("SELECT * FROM participant_execution_facts")
                if str(row["execution_id"]) in allowed
            ]
            for row in rows:
                value = _json_value(row.get("call_durations"))
                row["call_durations"] = value if isinstance(value, list) else []
        if not rows and allowed:
            rows = self.build_participant_facts(allowed)
        self._remember(cache_key, rows)
        return [row for row in rows if dimension is None or row["dimension"] == dimension]

    def entity_summary(self, entity: str, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        if entity not in {"providers", "models", "tools", "stages", "operations"}:
            raise ValueError(f"unsupported entity: {entity}")
        groups: dict[str, dict[str, Any]] = {}
        for operation in self._filtered_operations(filters):
            attributes = operation.attributes
            if entity == "providers":
                key = str(attributes.get("provider") or "")
                label = key
                if not key:
                    continue
            elif entity == "models":
                if model_value(operation) is None:
                    continue
                key = canonical_model_key(operation)
                label = display_model(operation)
            elif entity == "tools":
                if operation.kind != "tool":
                    continue
                key = canonical_tool_key(operation)
                label = display_tool(operation)
            elif entity == "stages":
                if operation.kind not in {"component", "operation", "tool", "model"}:
                    continue
                key = canonical_stage_key(operation)
                label = display_stage(operation)
            else:
                key = canonical_operation_key(operation)
                label = display_operation(operation)
            group = groups.setdefault(
                key,
                {
                    "canonical_key": key,
                    "label": label,
                    "calls": 0,
                    "execution_ids": set(),
                    "time_seconds": 0.0,
                    "failures": 0,
                    "extra_attempts": 0,
                    "known_cost": 0.0,
                    "known_cost_seen": False,
                    "total_tokens": 0.0,
                    "input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "tokens_seen": False,
                },
            )
            if entity == "models":
                group.setdefault("observed_versions", set()).add(str(model_value(operation)))
            group["calls"] += 1
            group["execution_ids"].add(operation.execution_id)
            group["time_seconds"] += _operation_duration(operation)
            group["failures"] += int(operation.status == "error")
            group["extra_attempts"] += int(operation.attempt is not None and operation.attempt > 1)
            cost = attributes.get("cost_usd")
            if isinstance(cost, (int, float)):
                group["known_cost"] += float(cost)
                group["known_cost_seen"] = True
            tokens = attributes.get("total_tokens")
            if isinstance(tokens, (int, float)):
                group["total_tokens"] += float(tokens)
                group["tokens_seen"] = True
            for token_field in ("input_tokens", "output_tokens"):
                token_value = attributes.get(token_field)
                if isinstance(token_value, (int, float)):
                    group[token_field] += float(token_value)
        result = []
        for group in groups.values():
            calls = int(group["calls"])
            group["executions"] = len(group.pop("execution_ids"))
            group["usual_seconds"] = group["time_seconds"] / calls if calls else None
            group["known_cost"] = group["known_cost"] if group.pop("known_cost_seen") else None
            if not group.pop("tokens_seen"):
                group["total_tokens"] = None
                group["input_tokens"] = None
                group["output_tokens"] = None
            if "observed_versions" in group:
                group["observed_versions"] = ", ".join(sorted(group["observed_versions"]))
            result.append(group)
        return sorted(result, key=lambda row: (-int(row["executions"]), -int(row["calls"]), str(row["label"])))[:50]

    def workflow_stage_summary(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Aggregate declared YAML nodes from materialized projections."""

        allowed = {str(row["execution_id"]) for row in self.execution_rows(filters, limit=None)}
        groups: dict[str, dict[str, Any]] = {}
        if "workflow_execution_projections" in self._tables:
            for row in self._query("SELECT execution_id, workflow_id, projection FROM workflow_execution_projections"):
                execution_id = str(row["execution_id"])
                if execution_id not in allowed:
                    continue
                projection = _json_value(row.get("projection"))
                if not isinstance(projection, Mapping):
                    continue
                workflow_value = projection.get("workflow")
                workflow: Mapping[str, Any] = dict(workflow_value) if isinstance(workflow_value, Mapping) else {}
                workflow_name = str(workflow.get("name") or row.get("workflow_id") or "Declared workflow")
                for raw_node in projection.get("nodes", []):
                    if not isinstance(raw_node, Mapping) or raw_node.get("state") == "inactive":
                        continue
                    node = dict(raw_node)
                    node_id = str(node.get("id") or node.get("name") or "unknown")
                    key = f"{row.get('workflow_id')}::{node_id}"
                    group = groups.setdefault(
                        key,
                        {
                            "canonical_key": key,
                            "workflow": workflow_name,
                            "label": str(node.get("name") or node_id),
                            "calls": 0,
                            "execution_ids": set(),
                            "time_seconds": 0.0,
                            "known_cost": 0.0,
                            "cost_seen": False,
                            "total_tokens": 0.0,
                            "tokens_seen": False,
                            "failures": 0,
                            "extra_attempts": 0,
                            "cost_eligible_operations": 0,
                            "cost_measured_operations": 0,
                            "token_eligible_operations": 0,
                            "token_measured_operations": 0,
                            "source": "declared_workflow",
                        },
                    )
                    group["calls"] += int(node.get("attempts") or 0)
                    group["execution_ids"].add(execution_id)
                    group["time_seconds"] += float(node.get("duration_seconds") or 0.0)
                    if isinstance(node.get("known_cost"), (int, float)):
                        group["known_cost"] += float(node["known_cost"])
                        group["cost_seen"] = True
                    if isinstance(node.get("total_tokens"), (int, float)):
                        group["total_tokens"] += float(node["total_tokens"])
                        group["tokens_seen"] = True
                    group["failures"] += int(node.get("state") == "failed")
                    group["extra_attempts"] += max(0, int(node.get("attempts") or 0) - 1)
                    for field in (
                        "cost_eligible_operations",
                        "cost_measured_operations",
                        "token_eligible_operations",
                        "token_measured_operations",
                    ):
                        group[field] += int(node.get(field) or 0)
        if not groups:
            for item in self.entity_summary("stages", filters):
                groups[str(item["canonical_key"])] = {
                    **item,
                    "workflow": "Observed operations",
                    "execution_ids": set(range(int(item["executions"]))),
                    "cost_seen": item.get("known_cost") is not None,
                    "tokens_seen": item.get("total_tokens") is not None,
                    "cost_eligible_operations": int(item["calls"]) if item.get("known_cost") is not None else 0,
                    "cost_measured_operations": int(item["calls"]) if item.get("known_cost") is not None else 0,
                    "token_eligible_operations": int(item["calls"]) if item.get("total_tokens") is not None else 0,
                    "token_measured_operations": int(item["calls"]) if item.get("total_tokens") is not None else 0,
                    "source": "observed_operations",
                }
        result = []
        for group in groups.values():
            executions = len(group.pop("execution_ids"))
            group["executions"] = executions
            group["usual_seconds"] = group["time_seconds"] / executions if executions else None
            group["known_cost"] = group["known_cost"] if group.pop("cost_seen") else None
            group["total_tokens"] = group["total_tokens"] if group.pop("tokens_seen") else None
            result.append(group)
        return sorted(result, key=lambda item: (-float(item["time_seconds"]), str(item["label"])))[:50]

    def workflow_performance(self, filters: FilterState = FilterState()) -> list[PerformanceSummary]:
        rows = self.execution_rows(filters, limit=None)
        by_execution = {str(row["execution_id"]): row for row in rows}
        grouped: dict[str, list[dict[str, Any]]] = {}
        names: dict[str, str] = {}
        if "workflow_execution_projections" in self._tables:
            for row in self._query("SELECT execution_id, workflow_id, projection FROM workflow_execution_projections"):
                execution_id = str(row["execution_id"])
                if execution_id not in by_execution:
                    continue
                projection = _json_value(row.get("projection"))
                workflow_value = projection.get("workflow") if isinstance(projection, Mapping) else None
                workflow: Mapping[str, Any] = dict(workflow_value) if isinstance(workflow_value, Mapping) else {}
                workflow_id = str(row.get("workflow_id") or "unmatched")
                names[workflow_id] = str(workflow.get("name") or workflow_id)
                grouped.setdefault(workflow_id, []).append(
                    {**by_execution[execution_id], "workflow": names[workflow_id]}
                )
        matched = {str(item["execution_id"]) for samples in grouped.values() for item in samples}
        unmatched = [row for row in rows if str(row["execution_id"]) not in matched]
        if unmatched:
            grouped["observed-unmatched"] = [
                {**row, "workflow": "Observed operations (unmatched)"} for row in unmatched
            ]
        result: list[PerformanceSummary] = []
        for samples in grouped.values():
            result.extend(
                _performance_contract(item)
                for item in aggregate_performance(
                    samples, dimension="workflow", business_available=self.capabilities().business_outcomes
                )
            )
        return sorted(result, key=lambda item: (-item.runs, item.label))

    def entity_detail(self, entity: str, label: str, filters: FilterState = FilterState()) -> dict[str, Any]:
        summaries = [row for row in self.entity_summary(entity, filters) if str(row.get("label")) == label]
        rows = []
        for operation in self._filtered_operations(filters):
            if entity == "providers":
                matches = operation.attributes.get("provider") == label
            elif entity == "models":
                matches = operation.kind == "model" and display_model(operation) == label
            elif entity == "tools":
                matches = operation.kind == "tool" and display_tool(operation) == label
            elif entity == "stages":
                matches = (
                    operation.kind in {"component", "operation", "tool", "model"} and display_stage(operation) == label
                )
            else:
                matches = display_operation(operation) == label
            if not matches:
                continue
            cost = operation.attributes.get("cost_usd")
            rows.append(
                {
                    "execution_id": operation.execution_id,
                    "execution_label": self._execution_label(operation.execution_id),
                    "kind": operation.kind,
                    "name": display_operation(operation),
                    "canonical_key": canonical_operation_key(operation),
                    "status": operation.status,
                    "started_at": operation.started_at,
                    "ended_at": operation.ended_at,
                    "attempt": operation.attempt,
                    "provider": operation.attributes.get("provider"),
                    "model": operation.attributes.get("model"),
                    "role": operation.attributes.get("role"),
                    "known_cost": cost if isinstance(cost, (int, float)) else None,
                    "total_tokens": operation.attributes.get("total_tokens"),
                }
            )
        rows.sort(key=lambda row: row["started_at"] or "", reverse=True)
        return {"label": label, "summary": summaries[0] if summaries else {}, "rows": rows}

    def _execution_label(self, execution_id: str) -> str:
        execution_row = self._query(load_query("execution/execution_detail"), [execution_id])
        if not execution_row:
            return f"Run {execution_id[:8]}"
        execution = _execution_from_row(execution_row[0])
        goal_rows = self._query(load_query("execution/product_goal"), [execution_id])
        if goal_rows:
            observed_goal = _json(goal_rows[0].get("attributes"))
            execution = execution.model_copy(
                update={
                    "runtime_id": observed_goal.get("runtime_id") or execution.runtime_id,
                    "attributes": {**execution.attributes, **observed_goal},
                }
            )
        return display_execution(execution, self._operations(execution_id))

    def execution_label(self, execution_id: str) -> str:
        """Return the stable UI label for one run."""

        return self._execution_label(execution_id)

    def paths(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        rows = []
        operations_by_execution = self._operations_by_execution(filters)
        for execution in self.execution_rows(filters, limit=None):
            operations = [
                operation
                for operation in operations_by_execution.get(str(execution["execution_id"]), [])
                if operation.kind not in {"workflow", "pipeline", "agent"}
            ]
            rows.append(
                {
                    "execution_id": execution["execution_id"],
                    "steps": [display_operation(operation) for operation in operations],
                    "path": display_path(operations),
                    "path_signature": canonical_path_signature(operations),
                    "duration_seconds": execution.get("duration_seconds"),
                    "known_cost": execution.get("known_cost"),
                    "total_tokens": execution.get("total_tokens"),
                    "failure_reports": sum(operation.status == "error" for operation in operations),
                    "failed": any(operation.status == "error" for operation in operations),
                }
            )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            signature = str(row.get("path_signature") or "path:empty")
            path = str(row.get("path") or "Unknown path")
            item = grouped.setdefault(
                signature,
                {
                    "path": path,
                    "steps": list(row.get("steps") or []),
                    "path_signature": signature,
                    "executions": 0,
                    "completed": 0,
                    "failures": 0,
                    "failure_reports": 0,
                    "time_seconds": 0.0,
                    "known_cost": 0.0,
                    "known_cost_seen": False,
                    "total_tokens": 0.0,
                    "tokens_seen": False,
                },
            )
            item["executions"] += 1
            item["failures"] += int(bool(row.get("failed")))
            item["failure_reports"] += int(row.get("failure_reports") or 0)
            item["completed"] += int(not row.get("failed"))
            item["time_seconds"] += float(row.get("duration_seconds") or 0)
            if row.get("known_cost") is not None:
                item["known_cost"] += float(row["known_cost"])
                item["known_cost_seen"] = True
            if row.get("total_tokens") is not None:
                item["total_tokens"] += float(row["total_tokens"])
                item["tokens_seen"] = True
        result = list(grouped.values())
        for item in result:
            item["usual_seconds"] = item["time_seconds"] / item["executions"] if item["executions"] else None
            item["known_cost"] = item["known_cost"] if item.pop("known_cost_seen") else None
            item["total_tokens"] = item["total_tokens"] if item.pop("tokens_seen") else None
        return sorted(result, key=lambda item: (-item["executions"], item["path"]))[:50]

    def loops(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        rows = self.execution_rows(filters, limit=None)
        operations_by_execution = self._operations_by_execution(filters)
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            operations = operations_by_execution.get(str(row["execution_id"]), [])
            if not operations:
                continue
            from witdem.analytics.core import Execution
            from witdem.analytics.runtime import NormalizedExecutionGraph

            graph = NormalizedExecutionGraph(
                execution=Execution(execution_id=row["execution_id"]), operations=operations
            )
            for pattern in derive_repeated_patterns(graph):
                key = str(pattern["loop_signature"])
                label = " → ".join(pattern["pattern"]) or " → ".join(
                    display_canonical_key(value) for value in pattern.get("pattern_keys", [])
                )
                operation_by_id = {operation.operation_id: operation for operation in operations}
                pattern_operations = [
                    operation_by_id[operation_id]
                    for operation_id in pattern.get("operation_ids", [])
                    if operation_id in operation_by_id
                ]
                width = len(pattern.get("pattern_keys", []))
                extra_operations = pattern_operations[width:]
                extra_time = sum(_operation_duration(operation) for operation in extra_operations)
                known_costs = [
                    float(operation.attributes["cost_usd"])
                    for operation in extra_operations
                    if isinstance(operation.attributes.get("cost_usd"), (int, float))
                ]
                billable_unknown = any(
                    operation.kind in {"model", "tool"}
                    and not isinstance(operation.attributes.get("cost_usd"), (int, float))
                    for operation in extra_operations
                )
                eventual_recovery = (
                    "Completed after loop"
                    if row.get("runtime_outcome") == "recovered" or not int(row.get("failure_count") or 0)
                    else "Ended with failure"
                )
                item = result.setdefault(
                    key,
                    {
                        "pattern": label,
                        "steps": list(pattern.get("pattern") or []),
                        "loop_signature": key,
                        "executions": 0,
                        "iterations": 0,
                        "extra_time_seconds": 0.0,
                        "extra_known_cost": 0.0,
                        "extra_cost_complete": True,
                        "extra_tokens": 0.0,
                        "extra_tokens_seen": False,
                        "completed_after_loop": 0,
                        "recovered_runs": 0,
                    },
                )
                item["executions"] += 1
                item["iterations"] += int(pattern["iterations"])
                item["extra_time_seconds"] += extra_time
                item["extra_known_cost"] += sum(known_costs)
                extra_tokens = [
                    float(operation.attributes["total_tokens"])
                    for operation in extra_operations
                    if isinstance(operation.attributes.get("total_tokens"), (int, float))
                ]
                if extra_tokens:
                    item["extra_tokens"] += sum(extra_tokens)
                    item["extra_tokens_seen"] = True
                item["extra_cost_complete"] = item["extra_cost_complete"] and not billable_unknown
                item["completed_after_loop"] += int(eventual_recovery == "Completed after loop")
                item["recovered_runs"] += int(row.get("runtime_outcome") == "recovered")
        for item in result.values():
            item["extra_known_cost"] = item["extra_known_cost"] if item["extra_cost_complete"] else None
            item["extra_tokens"] = item["extra_tokens"] if item.pop("extra_tokens_seen") else None
            item["eventual_recovery"] = (
                f"{item['completed_after_loop']:,}/{item['executions']:,} completed after loop"
                if item["executions"]
                else "Unknown"
            )
        return sorted(result.values(), key=lambda item: (-item["executions"], item["pattern"]))[:30]

    def failures(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        operations_by_execution = self._operations_by_execution(filters)
        for execution in self.execution_rows(filters, limit=None):
            operations = operations_by_execution.get(str(execution["execution_id"]), [])
            failure = self._failure_for_operations(operations)
            operation_id = failure.get("operation_id")
            chosen = next((operation for operation in operations if operation.operation_id == operation_id), None)
            if chosen is None:
                continue
            key = str(failure.get("primary_break_point_key") or canonical_operation_key(chosen))
            item = groups.setdefault(
                key,
                {
                    "failure_location": str(failure.get("primary_break_point") or display_operation(chosen)),
                    "failure_key": key,
                    "kind": chosen.kind,
                    "failures": 0,
                    "execution_ids": set(),
                    "terminal_runs": 0,
                    "recovered_runs": 0,
                    "unknown_outcome_runs": 0,
                    "providers": set(),
                    "models": set(),
                    "time_seconds": 0.0,
                    "known_cost": 0.0,
                    "known_cost_seen": False,
                    "total_tokens": 0.0,
                    "tokens_seen": False,
                    "affected_run_time_seconds": 0.0,
                    "affected_run_cost": 0.0,
                    "affected_run_cost_seen": False,
                    "affected_run_tokens": 0.0,
                    "affected_run_tokens_seen": False,
                },
            )
            item["failures"] += 1
            item["execution_ids"].add(chosen.execution_id)
            if execution.get("runtime_outcome") == "recovered":
                item["recovered_runs"] += 1
            elif execution.get("status") != "running" and int(execution.get("failure_count") or 0) > 0:
                item["terminal_runs"] += 1
            else:
                item["unknown_outcome_runs"] += 1
            if chosen.attributes.get("provider"):
                item["providers"].add(str(chosen.attributes["provider"]))
            if chosen.attributes.get("model"):
                item["models"].add(str(chosen.attributes["model"]))
            item["time_seconds"] += _operation_duration(chosen)
            operation_cost = chosen.attributes.get("cost_usd")
            if isinstance(operation_cost, (int, float)):
                item["known_cost"] += float(operation_cost)
                item["known_cost_seen"] = True
            operation_tokens = chosen.attributes.get("total_tokens")
            if isinstance(operation_tokens, (int, float)):
                item["total_tokens"] += float(operation_tokens)
                item["tokens_seen"] = True
            item["affected_run_time_seconds"] += float(execution.get("duration_seconds") or 0.0)
            if isinstance(execution.get("known_cost"), (int, float)):
                item["affected_run_cost"] += float(execution["known_cost"])
                item["affected_run_cost_seen"] = True
            if isinstance(execution.get("total_tokens"), (int, float)):
                item["affected_run_tokens"] += float(execution["total_tokens"])
                item["affected_run_tokens_seen"] = True
        result = []
        for item in groups.values():
            item["executions"] = len(item.pop("execution_ids"))
            item["providers"] = ", ".join(sorted(item.pop("providers"))) or None
            item["models"] = ", ".join(sorted(item.pop("models"))) or None
            item["known_cost"] = item["known_cost"] if item.pop("known_cost_seen") else None
            item["total_tokens"] = item["total_tokens"] if item.pop("tokens_seen") else None
            item["affected_run_cost"] = item["affected_run_cost"] if item.pop("affected_run_cost_seen") else None
            item["affected_run_tokens"] = item["affected_run_tokens"] if item.pop("affected_run_tokens_seen") else None
            result.append(item)
        return sorted(result, key=lambda item: (-int(item["failures"]), str(item["failure_location"])))

    def replay(self, execution_id: str) -> ReplayGraph:
        graph, events, semantic_map = self._graph_inputs(execution_id)
        return derive_replay_graph(graph, events=events, semantic_stage_map=semantic_map)

    def workflow_templates(self) -> list[dict[str, Any]]:
        """Return the latest persisted version of every declared workflow."""

        if "workflow_templates" not in self._tables:
            return []
        rows = self._query(
            """
            SELECT workflow_id, template_hash, name, definition, source, registered_at
            FROM workflow_templates
            QUALIFY row_number() OVER (
                PARTITION BY workflow_id ORDER BY registered_at DESC, template_hash DESC
            ) = 1
            ORDER BY name, workflow_id
            """
        )
        for row in rows:
            row["definition"] = _json(row.get("definition"))
        return rows

    def execution_workflow(self, execution_id: str) -> dict[str, Any] | None:
        if "execution_workflows" not in self._tables:
            return None
        rows = self._query(
            "SELECT execution_id, workflow_id, template_hash, match_source, matched_at "
            "FROM execution_workflows WHERE execution_id = ? LIMIT 1",
            [execution_id],
        )
        return rows[0] if rows else None

    def workflow_execution_ids(self, workflow_id: str) -> set[str]:
        """Return executions associated with one authored workflow identity."""

        if "execution_workflows" not in self._tables:
            return set()
        return {
            str(row["execution_id"])
            for row in self._query(
                "SELECT execution_id FROM execution_workflows WHERE workflow_id = ?",
                [workflow_id],
            )
        }

    def workflow_projection(self, execution_id: str) -> dict[str, Any] | None:
        if "workflow_execution_projections" not in self._tables:
            return None
        rows = self._query(
            "SELECT projection FROM workflow_execution_projections WHERE execution_id = ? LIMIT 1",
            [execution_id],
        )
        if not rows:
            return None
        value = rows[0].get("projection")
        if isinstance(value, Mapping):
            return dict(value)
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError):
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None

    def workflow_projection_rows(
        self,
        workflow_id: str,
        *,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        if "workflow_execution_projections" not in self._tables:
            return []
        limit_clause = "" if limit is None else " LIMIT ?"
        params: list[Any] = [workflow_id]
        if limit is not None:
            params.append(limit)
        rows = self._query(
            "SELECT execution_id, template_hash, projector_version, projection, projected_at "
            "FROM workflow_execution_projections WHERE workflow_id = ? ORDER BY projected_at DESC" + limit_clause,
            params,
        )
        for row in rows:
            value = row.get("projection")
            try:
                row["projection"] = value if isinstance(value, Mapping) else json.loads(str(value))
            except (TypeError, ValueError):
                row["projection"] = None
        return rows

    def workflow_projection_catalog(self) -> list[dict[str, Any]]:
        if "workflow_execution_projections" not in self._tables:
            return []
        rows = self._query(
            "SELECT workflow_id, COUNT(*) AS execution_count, "
            "arg_max(projection, COALESCE(json_extract_string(projection, '$.execution.started_at'), "
            "CAST(projected_at AS VARCHAR))) AS latest_projection "
            "FROM workflow_execution_projections GROUP BY workflow_id"
        )
        for row in rows:
            value = row.get("latest_projection")
            try:
                row["latest_projection"] = value if isinstance(value, Mapping) else json.loads(str(value))
            except (TypeError, ValueError):
                row["latest_projection"] = None
        return rows

    def workflow_operation_facts(self, workflow_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return versioned operation identities and long-form measurements."""

        if "operation_classification_facts" not in self._tables:
            return [], []
        operations = self._query(
            "SELECT * FROM operation_classification_facts WHERE workflow_id = ? ORDER BY execution_id, operation_id",
            [workflow_id],
        )
        measurements = self._query(
            "SELECT * FROM operation_measurement_facts WHERE workflow_id = ? "
            "ORDER BY execution_id, operation_id, measurement_key",
            [workflow_id],
        )
        for operation in operations:
            operation["input_modalities"] = _json_value(operation.get("input_modalities")) or []
            operation["output_modalities"] = _json_value(operation.get("output_modalities")) or []
            operation["attributes"] = _json(operation.get("attributes"))
        identity_by_operation = {str(operation.get("operation_id") or ""): operation for operation in operations}
        for measurement in measurements:
            identity = identity_by_operation.get(str(measurement.get("operation_id") or ""), {})
            for key in ("family", "operation_type", "interface", "role", "provider_id", "model_id"):
                measurement[key] = identity.get(key)
        return operations, measurements

    def operation_health_facts(
        self, filters: FilterState = FilterState()
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return materialized operation facts for the selected executions."""

        if "operation_classification_facts" not in self._tables:
            return [], []
        allowed_execution_ids = {
            str(row["execution_id"]) for row in self.execution_rows(filters, limit=None)
        }
        if not allowed_execution_ids:
            return [], []
        rows = self._query(
            "SELECT o.*, m.registry_version AS measurement_registry_version, m.measurement_key, m.value, "
            "m.unit, m.aggregation, m.scope, m.measurement_status, m.provenance, m.applicability_source, "
            "m.attempt AS measurement_attempt FROM operation_classification_facts o "
            "LEFT JOIN operation_measurement_facts m ON o.operation_id = m.operation_id "
            "ORDER BY o.execution_id, o.operation_id, m.measurement_key"
        )
        operations: dict[str, dict[str, Any]] = {}
        measurements: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("execution_id") or "") not in allowed_execution_ids:
                continue
            operation_id = str(row.get("operation_id") or "")
            operations.setdefault(
                operation_id,
                {key: value for key, value in row.items() if not key.startswith("measurement_")},
            )
            if row.get("measurement_key") is not None:
                measurements.append(
                    {
                        "operation_id": operation_id,
                        "execution_id": row.get("execution_id"),
                        "workflow_id": row.get("workflow_id"),
                        "template_hash": row.get("template_hash"),
                        "node_id": row.get("node_id"),
                        "registry_version": row.get("measurement_registry_version"),
                        "measurement_key": row.get("measurement_key"),
                        "value": row.get("value"),
                        "unit": row.get("unit"),
                        "aggregation": row.get("aggregation"),
                        "scope": row.get("scope"),
                        "measurement_status": row.get("measurement_status"),
                        "provenance": row.get("provenance"),
                        "applicability_source": row.get("applicability_source"),
                        "attempt": row.get("measurement_attempt"),
                    }
                )
        return list(operations.values()), measurements

    def execution_operation_facts(self, execution_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if "operation_classification_facts" not in self._tables:
            return [], []
        operations = self._query(
            "SELECT * FROM operation_classification_facts WHERE execution_id = ? ORDER BY operation_id",
            [execution_id],
        )
        measurements = self._query(
            "SELECT * FROM operation_measurement_facts WHERE execution_id = ? ORDER BY operation_id, measurement_key",
            [execution_id],
        )
        for operation in operations:
            operation["input_modalities"] = _json_value(operation.get("input_modalities")) or []
            operation["output_modalities"] = _json_value(operation.get("output_modalities")) or []
            operation["attributes"] = _json(operation.get("attributes"))
        identity_by_operation = {str(operation.get("operation_id") or ""): operation for operation in operations}
        for measurement in measurements:
            identity = identity_by_operation.get(str(measurement.get("operation_id") or ""), {})
            for key in ("family", "operation_type", "interface", "role", "provider_id", "model_id"):
                measurement[key] = identity.get(key)
        return operations, measurements

    def workflow_evaluations(self, workflow_id: str) -> list[dict[str, Any]]:
        if "evaluations" not in self._tables or "execution_workflows" not in self._tables:
            return []
        rows = self._query(
            "SELECT e.*, x.started_at AS execution_started_at "
            "FROM evaluations e JOIN execution_workflows w USING (execution_id) "
            "LEFT JOIN executions x USING (execution_id) "
            "WHERE w.workflow_id = ? ORDER BY e.execution_id, e.name, e.evaluation_id",
            [workflow_id],
        )
        for row in rows:
            row["value"] = _json_value(row.get("value"))
            row["attributes"] = _json(row.get("attributes"))
        return rows

    def workflow_evaluation_campaigns(self, workflow_id: str) -> list[dict[str, Any]]:
        if "evaluation_campaigns" not in self._tables:
            return []
        rows = self._query(
            "SELECT * FROM evaluation_campaigns WHERE workflow_id = ? ORDER BY started_at DESC",
            [workflow_id],
        )
        for row in rows:
            row["attributes"] = _json(row.get("attributes"))
        return rows

    def evaluation_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        if "evaluation_campaigns" not in self._tables:
            return None
        campaigns = self._query("SELECT * FROM evaluation_campaigns WHERE campaign_id = ?", [campaign_id])
        if not campaigns:
            return None
        campaign = campaigns[0]
        campaign["attributes"] = _json(campaign.get("attributes"))
        results = self._query(
            "SELECT * FROM evaluation_case_results WHERE campaign_id = ? ORDER BY case_id, evaluation_key",
            [campaign_id],
        )
        for result in results:
            result["value"] = _json_value(result.get("value"))
            result["target"] = _json_value(result.get("target"))
            result["evidence"] = _json_value(result.get("evidence"))
            result["attributes"] = _json(result.get("attributes"))
        return {"campaign": campaign, "results": results}

    def execution_outcomes(self, execution_id: str) -> dict[str, Any]:
        """Return explicit runtime/application outcomes for one replay."""

        runtime = self._query(load_query("execution/runtime_outcome"), [execution_id])
        business = self._query(load_query("execution/business_outcome"), [execution_id])
        product_goal = self._query(load_query("execution/product_goal"), [execution_id])
        business_row = business[0] if business else None
        goal_row = product_goal[0] if product_goal else None
        attributes = _json(goal_row.get("attributes")) if goal_row else {}
        return {
            "runtime": runtime[0]["outcome"] if runtime else None,
            "business": business_row["outcome"] if business_row else None,
            "business_name": business_row["name"] if business_row else None,
            "product_goal": attributes if goal_row else None,
        }

    def semantic_replay_records(self, execution_id: str) -> list[SemanticReplayRecord]:
        """Return generic SDK semantics when no physical operation graph exists."""

        timed: list[SemanticReplayRecord] = []
        for row in self._query(load_query("execution/events_for_execution"), [execution_id]):
            payload = _json(row.get("payload"))
            timed.append(
                SemanticReplayRecord(
                    record_id=str(row["event_id"]),
                    kind=str(row.get("type") or "event"),
                    name=str(row["name"]),
                    timestamp=_datetime_value(row.get("timestamp")),
                    status=str(payload["status"]) if payload.get("status") is not None else None,
                    value=payload.get("value"),
                    attributes={key: value for key, value in payload.items() if key != "value"},
                )
            )
        for row in self._query(load_query("execution/outcomes_for_execution"), [execution_id]):
            timed.append(
                SemanticReplayRecord(
                    record_id=str(row["outcome_id"]),
                    kind="outcome",
                    name=str(row["name"]),
                    timestamp=_datetime_value(row.get("timestamp")),
                    status=str(row["status"]) if row.get("status") is not None else None,
                    value=_json_value(row.get("value")),
                    attributes=_json(row.get("attributes")),
                )
            )
        timed.sort(key=lambda record: record.timestamp.isoformat() if record.timestamp else "")

        evaluations = [
            SemanticReplayRecord(
                record_id=str(row["evaluation_id"]),
                kind="evaluation",
                name=str(row["name"]),
                timestamp=None,
                status=str(row["label"]) if row.get("label") is not None else None,
                value=_json_value(row.get("value")) if row.get("value") is not None else row.get("score"),
                attributes=_json(row.get("attributes")),
            )
            for row in self._query(load_query("execution/evaluations_for_execution"), [execution_id])
        ]
        first_outcome = next((index for index, record in enumerate(timed) if record.kind == "outcome"), len(timed))
        return [*timed[:first_outcome], *evaluations, *timed[first_outcome:]]

    def failure_explanation(self, execution_id: str) -> dict[str, Any]:
        """Return the existing deterministic break-point explanation."""

        graph, events, semantic_map = self._graph_inputs(execution_id)
        return derive_failure_stage(graph, events=events, semantic_stage_map=semantic_map)

    def export_evidence_bundle(self, execution_id: str) -> EvidenceBundle:
        """Export one coherent, deterministic bundle of canonical OSS evidence."""

        with self._overview_read_session():
            execution_rows = self._query(load_query("execution/execution_detail"), [execution_id])
            if not execution_rows:
                raise KeyError(execution_id)
            execution = _execution_from_row(execution_rows[0])
            operations = sorted(
                self._operations(execution_id),
                key=lambda item: (
                    item.started_at is None,
                    item.started_at.isoformat() if item.started_at is not None else "",
                    item.operation_id,
                ),
            )
            links = sorted(
                (
                    Link.model_validate({**row, "attributes": _json(row.get("attributes"))})
                    for row in self._query(load_query("execution/links_for_execution"), [execution_id])
                ),
                key=lambda item: item.link_id,
            )
            events = sorted(
                (
                    Event.model_validate({**row, "payload": _json(row.get("payload"))})
                    for row in self._query(load_query("execution/events_for_execution"), [execution_id])
                ),
                key=lambda item: (item.timestamp.isoformat(), item.event_id),
            )
            evaluations = sorted(
                (
                    Evaluation.model_validate(
                        {
                            **row,
                            "value": _json_value(row.get("value")),
                            "attributes": _json(row.get("attributes")),
                        }
                    )
                    for row in self._query(load_query("execution/evaluations_for_execution"), [execution_id])
                ),
                key=lambda item: (item.name, item.evaluation_id),
            )
            outcomes = sorted(
                (
                    Outcome.model_validate(
                        {
                            **row,
                            "value": _json_value(row.get("value")),
                            "attributes": _json(row.get("attributes")),
                        }
                    )
                    for row in self._query(load_query("execution/outcomes_for_execution"), [execution_id])
                ),
                key=lambda item: (item.timestamp.isoformat(), item.outcome_id),
            )
            operation_facts, raw_measurements = self.execution_operation_facts(execution_id)
            _profile_operations, measurements = operation_profile_inputs(operation_facts, raw_measurements)
            projection = self.workflow_projection(execution_id)
            raw_discrepancies = projection.get("discrepancies") if projection is not None else None
            discrepancies = dict(raw_discrepancies) if isinstance(raw_discrepancies, Mapping) else None
            diagnostics = EvidenceBundleDiagnostics(
                failure_explanation=self.failure_explanation(execution_id),
                operation_summary=operation_summary(operation_facts, raw_measurements),
                operation_measurements=measurements,
                measurement_coverage=measurement_coverage(measurements),
                evaluation_assessments=[
                    EvaluationAssessment(
                        evaluation_id=evaluation.evaluation_id,
                        passed=explicit_evaluation_pass(evaluation.model_dump()),
                    )
                    for evaluation in evaluations
                ],
                workflow_discrepancies=discrepancies,
            )
            return EvidenceBundle(
                execution=execution,
                operations=operations,
                links=links,
                events=events,
                evaluations=evaluations,
                outcomes=outcomes,
                diagnostics=diagnostics,
            )

    def _graph_inputs(self, execution_id: str) -> tuple[Any, list[Event], dict[str, str]]:
        operations = self._operations(execution_id)
        events = [
            Event.model_validate({**row, "payload": _json(row.get("payload"))})
            for row in self._query(load_query("execution/events_for_execution"), [execution_id])
        ]
        links = [
            Link.model_validate({**row, "attributes": _json(row.get("attributes"))})
            for row in self._query(load_query("execution/links_for_execution"), [execution_id])
        ]
        execution_rows = self._query(load_query("execution/execution_detail"), [execution_id])
        if not execution_rows:
            raise KeyError(execution_id)
        from witdem.analytics.core import Execution

        execution = Execution.model_validate(
            {**execution_rows[0], "attributes": _json(execution_rows[0].get("attributes"))}
        )
        from witdem.analytics.runtime import NormalizedExecutionGraph

        graph = NormalizedExecutionGraph(execution=execution, operations=operations, links=links)
        semantic_map = {"research_pass": "research", "extract_profile": "extraction", "validation": "validation"}
        return graph, events, semantic_map

    def _operations(self, execution_id: str) -> list[Operation]:
        rows = self._query(load_query("execution/execution_timeline"), [execution_id])
        return [Operation.model_validate({**row, "attributes": _json(row.get("attributes"))}) for row in rows]


def _serving_runtime_outcome(status: str, failures: int) -> str:
    """Separate recovered child failures from terminal execution failures."""

    if not failures:
        return "completed"
    if status.casefold() in {"completed", "success", "succeeded", "ok"}:
        return "recovered"
    return "failed"
