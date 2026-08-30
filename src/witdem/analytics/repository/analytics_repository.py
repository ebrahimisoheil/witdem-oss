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
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar, cast

import duckdb
from filelock import FileLock

from witdem.analytics.contracts import (
    CostSummary,
    ExecutionSummary,
    FailureSummary,
    MetadataSnapshot,
    ModelSummary,
    OverviewSnapshot,
    PathSummary,
    PerformanceSummary,
    ProductGoalSummary,
    ProviderSummary,
    SemanticReplayRecord,
)
from witdem.analytics.core import Event, Execution, Link, Operation
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
from witdem.analytics.read_model import aggregate_performance, dashboard_metrics
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
        return ExecutionSummary(
            total_runs=int(metrics["total_runs"]),
            successful_runs=int(metrics["completed_runs"]),
            failed_runs=int(metrics["failed_runs"]),
            running_runs=int(metrics["running_runs"]),
            recovered_runs=int(metrics["recovered_runs"]),
            extra_work_runs=int(metrics["extra_work_runs"]),
            avg_duration_seconds=(float(metrics["time_per_run"]) if metrics["time_per_run"] is not None else None),
            measured_cost=(float(metrics["measured_cost"]) if metrics["measured_cost"] is not None else None),
            cost_coverage=float(metrics["cost_coverage"]),
            business_successful_runs=int(metrics["business_successful_runs"]),
            business_unsuccessful_runs=int(metrics["business_unsuccessful_runs"]),
            business_reported_runs=int(metrics["business_reported_runs"]),
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
        costs = [float(row["known_cost"]) for row in achieved_rows if row.get("known_cost") is not None]
        durations = [float(row["duration_seconds"]) for row in achieved_rows if row.get("duration_seconds") is not None]
        token_values = [float(row["total_tokens"]) for row in achieved_rows if row.get("total_tokens") is not None]
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
                "artifact_valid",
                "decision_evidence_sufficient",
                "required_path_observed",
                "closest_blocker",
                "threshold",
                "threshold_margin",
            ):
                if field in attributes:
                    execution[field] = attributes[field]
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
            if row.get("known_cost") is not None:
                item["known_cost"] += float(row["known_cost"])
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
            if achieved and row.get("known_cost") is not None:
                item["measured_cost"] += float(row["known_cost"])
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
                "SELECT execution_id, name, value, score, label, attributes "
                "FROM serving.semantic_facts WHERE record_type = 'evaluation'"
            ),
        )

    def evaluation_summary(self, filters: FilterState = FilterState()) -> list[dict[str, Any]]:
        """Aggregate reported evaluations without interpreting contract-specific names."""

        if "semantic_facts" not in self._serving_tables:
            return []
        allowed = {str(row["execution_id"]) for row in self.execution_rows(filters, limit=None)}
        grouped: dict[str, dict[str, Any]] = {}
        for row in self._evaluation_facts():
            if str(row["execution_id"]) not in allowed:
                continue
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
        score = row.get("score")
        target = attributes.get("target")
        direction = str(attributes.get("direction") or "higher_is_better")
        if isinstance(score, (int, float)) and isinstance(target, (int, float)):
            return float(score) <= float(target) if direction == "lower_is_better" else float(score) >= float(target)
        label = str(row.get("label") or "").strip().casefold()
        if label in {"valid", "passed", "pass", "yes", "true", "achieved", "correct"}:
            return True
        if label in {"invalid", "failed", "fail", "no", "false", "not achieved", "incorrect"}:
            return False
        if isinstance(score, (int, float)) and target is None:
            return float(score) >= 1.0
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
            for fact in self._evaluation_facts():
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
            assessed: list[bool] = []
            for fact in facts:
                attributes = _json(fact.get("attributes"))
                met = self._evaluation_met_target(fact, attributes)
                if met is None:
                    continue
                assessed.append(met)
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
            if assessed and not all(assessed):
                item["attention_runs"] += 1
                summary["attention_runs"] = int(summary["attention_runs"]) + 1
            elif assessed:
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
                evaluations.append(
                    {**evaluation, "average_score": score_total / score_runs if score_runs else None}
                )
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

    def get_cost_summary(self, filters: FilterState = FilterState()) -> CostSummary:
        """Return cost and token metrics without exposing SQL or table names."""

        metrics = self.dashboard_metrics(filters)
        return CostSummary(
            measured_cost=float(metrics["measured_cost"]) if metrics["measured_cost"] is not None else None,
            model_cost=float(metrics["model_cost"]) if metrics["model_cost"] is not None else None,
            tool_cost=float(metrics["tool_cost"]) if metrics["tool_cost"] is not None else None,
            cost_coverage=float(metrics["cost_coverage"]),
            measured_cost_per_run=(
                float(metrics["measured_cost_per_run"]) if metrics["measured_cost_per_run"] is not None else None
            ),
            input_tokens=float(metrics["input_tokens"]) if metrics["input_tokens"] is not None else None,
            output_tokens=float(metrics["output_tokens"]) if metrics["output_tokens"] is not None else None,
            total_tokens=float(metrics["total_tokens"]) if metrics["total_tokens"] is not None else None,
            token_runs=int(metrics["token_runs"]),
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
                runtime = str(row.get("runtime_outcome") or row.get("runtime_status") or row.get("status") or "unknown")
                runtime_breakdown[runtime] = runtime_breakdown.get(runtime, 0) + 1
                outcome = row.get("application_outcome") or row.get("business_outcome") or row.get("outcome")
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
                workflows=tuple(self.get_performance_summary("workflow", filters)),
                stages=tuple(self.entity_summary("stages", filters)),
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

    def get_comparison_insights(
        self, dimension: str, filters: FilterState = FilterState()
    ) -> list[dict[str, Any]]:
        """Return operation-attributable model/provider comparisons.

        A mixed-model run contributes only the calls, tokens, cost and model time
        attributable to each participant. This avoids charging the full run to
        every model or provider that appeared in it.
        """

        if dimension not in {"model", "provider"}:
            raise ValueError(f"unsupported comparison dimension: {dimension}")
        with self._overview_read_session():
            rows = self.execution_rows(filters, limit=None)
            operations_by_execution = self._operations_by_execution(filters)
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                per_label: dict[str, dict[str, Any]] = {}
                for operation in operations_by_execution.get(str(row["execution_id"]), []):
                    if operation.kind != "model":
                        continue
                    label = (
                        display_model(operation)
                        if dimension == "model"
                        else str(operation.attributes.get("provider") or "")
                    )
                    if not label:
                        continue
                    sample = per_label.setdefault(
                        label,
                        {
                            **row,
                            "duration_seconds": 0.0,
                            "known_cost": 0.0,
                            "total_tokens": 0.0,
                            "calls": 0,
                            "_cost_seen": False,
                            "_tokens_seen": False,
                        },
                    )
                    sample["duration_seconds"] += _operation_duration(operation)
                    sample["calls"] += 1
                    cost = operation.attributes.get("cost_usd")
                    if isinstance(cost, (int, float)):
                        sample["known_cost"] += float(cost)
                        sample["_cost_seen"] = True
                    tokens = operation.attributes.get("total_tokens")
                    if isinstance(tokens, (int, float)):
                        sample["total_tokens"] += float(tokens)
                        sample["_tokens_seen"] = True
                for label, sample in per_label.items():
                    if not sample.pop("_cost_seen"):
                        sample["known_cost"] = None
                    if not sample.pop("_tokens_seen"):
                        sample["total_tokens"] = None
                    groups.setdefault(label, []).append(sample)

            evaluations_by_execution: dict[str, list[dict[str, Any]]] = {}
            if "semantic_facts" in self._serving_tables:
                for fact in self._query(
                    "SELECT execution_id, name, score, attributes FROM serving.semantic_facts "
                    "WHERE record_type = 'evaluation'"
                ):
                    if fact.get("score") is not None:
                        evaluations_by_execution.setdefault(str(fact["execution_id"]), []).append(fact)
            results = []
            for label, samples in groups.items():
                evaluation_groups: dict[str, dict[str, Any]] = {}
                for sample in samples:
                    for fact in evaluations_by_execution.get(str(sample["execution_id"]), []):
                        attributes = _json(fact.get("attributes"))
                        name = str(fact.get("name") or attributes.get("evaluation_key") or "Evaluation")
                        evaluation = evaluation_groups.setdefault(
                            name,
                            {
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
                    evaluations.append(
                        {**evaluation, "reported_runs": len(scores), "average_score": _average(scores)}
                    )
                results.append(
                    {"label": label, **self._insight_summary(samples), "evaluations": evaluations}
                )
            return sorted(results, key=lambda item: (-int(item["runs"]), str(item["label"])))

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
                        operation
                        for operation in operations
                        if operation.kind in {"graph_node", "component", "tool"}
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
            outliers.sort(
                key=lambda item: (-len(item["reasons"]), -float(item.get("duration_seconds") or 0))
            )
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
        operations_by_execution = self._operations_by_execution(filters)
        if dimension in {"provider", "model"}:
            entity_groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                operations = operations_by_execution.get(str(row["execution_id"]), [])
                if dimension == "provider":
                    labels = {
                        str(operation.attributes["provider"])
                        for operation in operations
                        if operation.attributes.get("provider")
                    }
                else:
                    labels = {
                        display_model(operation)
                        for operation in operations
                        if operation.kind == "model" and model_value(operation)
                    }
                for label in labels:
                    entity_groups.setdefault(label, []).append(dict(row, **{dimension: label}))
            result: list[dict[str, Any]] = []
            for _label, grouped_rows in entity_groups.items():
                result.extend(
                    aggregate_performance(
                        grouped_rows,
                        dimension=dimension,
                        business_available=self.capabilities().business_outcomes,
                    )
                )
            return sorted(result, key=lambda item: (-int(item["runs"]), str(item["label"])))
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
            item["time_seconds"] += float(execution.get("duration_seconds") or 0.0)
            if isinstance(execution.get("known_cost"), (int, float)):
                item["known_cost"] += float(execution["known_cost"])
                item["known_cost_seen"] = True
            if isinstance(execution.get("total_tokens"), (int, float)):
                item["total_tokens"] += float(execution["total_tokens"])
                item["tokens_seen"] = True
        result = []
        for item in groups.values():
            item["executions"] = len(item.pop("execution_ids"))
            item["providers"] = ", ".join(sorted(item.pop("providers"))) or None
            item["models"] = ", ".join(sorted(item.pop("models"))) or None
            item["known_cost"] = item["known_cost"] if item.pop("known_cost_seen") else None
            item["total_tokens"] = item["total_tokens"] if item.pop("tokens_seen") else None
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
