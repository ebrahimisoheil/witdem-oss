"""Live read-write DuckDB store for the canonical analytics tables.

The schema matches the canonical analytics model used by the repository read
layer. This module owns the write connection while dashboard repositories open
separate read-only connections.

Every ``upsert_*`` function closes and clears the
shared connection immediately after its own transaction commits/rolls back
(see ``_release_connection()``), instead of leaving it cached open for the
rest of the process's life. This does not change DuckDB's actual rule (still
exactly one process with the file open read-write, XOR any number of
processes read-only) -- it shrinks the window during which *this* process
holds the file open from "the service's entire lifetime" down to "the
duration of one upsert call", so a concurrent read-only reader (the
dashboard) now usually finds the file free between requests instead of
never. ``readiness()``/``ping()`` deliberately never uses the shared cache
at all, for the same reason -- a readiness probe must not itself become a
permanent source of the exact lock contention this change exists to avoid.

Path is configured via the ``WITDEM_DB_PATH`` environment variable
(default ``data/live/analytics.duckdb``); the parent directory is created if
missing.

Upsert strategy:
  * ``executions`` / ``operations`` / ``links`` are recomputed as a batch
    from "all raw spans persisted so far for this execution", so they are
    upserted via ``DELETE ... WHERE execution_id = ?`` followed by
    re-``INSERT``, inside one transaction -- cheap and trivially idempotent
    at local-demo scale; no row-level dedupe is needed there.
  * ``events`` / ``evaluations`` / ``outcomes`` arrive individually from the
    SDK ingest path and dedupe per-row via ``INSERT OR REPLACE`` keyed on
    their own id column. DuckDB's ``INSERT OR REPLACE`` only actually
    replaces (rather than blindly appending duplicates) when the target
    column has a PRIMARY KEY/UNIQUE constraint (verified empirically against
    the pinned ``duckdb`` version: without one, ``INSERT OR REPLACE`` raises
    ``BinderException: There are no UNIQUE/PRIMARY KEY constraints...``), so
    ``ensure_schema`` adds a PRIMARY KEY on each of those three tables' own
    id column. ``executions``/``operations``/``links`` intentionally do NOT
    get a PRIMARY KEY -- the task calls for plain delete-then-insert there,
    and operation/link ids are not guaranteed globally unique across
    unrelated executions (only per-execution), so no constraint is added.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
from filelock import FileLock

from witdem import __version__
from witdem.analytics.core import Evaluation, Event, Execution, Link, Operation, Outcome, utc_now
from witdem.analytics.schema import ANALYTICS_COLUMN_TYPES, ANALYTICS_COLUMNS
from witdem.analytics.serving import SERVING_DDL, build_serving_rows
from witdem.config import db_path

logger = logging.getLogger(__name__)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


#: The six live analytics tables this module owns (docs/architecture.md). This is
#: a subset of ``experiments.synthetic_corpus.ANALYTICS_TABLES`` -- the live
#: service never writes ``expected_derived_insights``, which is a synthetic
#: corpus fixture-only table.
LIVE_TABLES: tuple[str, ...] = ("executions", "operations", "links", "events", "evaluations", "outcomes")

#: Columns whose values are stored JSON-encoded (VARCHAR), matching the exact
#: convention ``experiments/synthetic_corpus.py`` already uses when writing
#: this same schema (see its ``_parquet_rows``/``_write_parquet`` call sites).
_JSON_COLUMNS: dict[str, frozenset[str]] = {
    "executions": frozenset({"attributes"}),
    "operations": frozenset({"attributes"}),
    "links": frozenset({"attributes"}),
    "events": frozenset({"payload"}),
    "evaluations": frozenset({"value", "attributes"}),
    "outcomes": frozenset({"value", "attributes"}),
}

#: Tables that dedupe via ``INSERT OR REPLACE`` keyed on their own id column.
#: These are the only tables ``ensure_schema`` adds a PRIMARY KEY to -- see
#: the module docstring for why.
_SEMANTIC_ID_COLUMN: dict[str, str] = {
    "events": "event_id",
    "evaluations": "evaluation_id",
    "outcomes": "outcome_id",
}

_lock = threading.RLock()
_connection: duckdb.DuckDBPyConnection | None = None


def _default_db_path() -> Path:
    return db_path()


def _file_lock(database_path: Path) -> FileLock:
    """Coordinate short-lived DuckDB sessions across the server/dashboard."""

    return FileLock(str(database_path.with_suffix(database_path.suffix + ".lock")), timeout=5)


def initialize_analytics_store(path: str | Path | None = None) -> Path:
    """Create the canonical analytics tables without inserting any rows."""

    database_path = Path(path) if path is not None else _default_db_path()
    database_path = database_path.expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            ensure_schema(connection)
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
    return database_path


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a read-write DuckDB connection, opening (or re-opening) it lazily.

    Concurrent access note (verified against the pinned ``duckdb`` version,
    see ``docs/architecture.md`` §7, corrected there from an earlier, disproven
    assumption): DuckDB's native single-file format does NOT support one
    read-write connection plus concurrent read-only connections from *other
    processes* to the same file at the same time -- its documented model is
    either (a) exactly one process with read-write access, or (b) any number
    of processes all in ``read_only=True`` mode, never a mix (confirmed
    empirically here: a second process opening ``read_only=True`` while this
    connection is open raises ``IOException: Could not set lock on file``).
    Multiple *read-only* connections, or multiple connections *within this
    same process* (also verified empirically), are unaffected.

    Despite the name, this is no longer necessarily "the same object every
    call": every ``upsert_*`` function below closes and clears the cache
    right after its own transaction finishes (``_release_connection()``), so
    that a concurrent dashboard read-only connection usually finds the file
    free between requests rather than never (see the module docstring's
    "UPDATE" note). A caller in the middle of a longer read session (e.g. a
    test asserting on several queries in a row) still gets back the *same*
    object across calls made before the next write closes it -- only a write
    elsewhere invalidates a previously-returned reference.
    """

    global _connection
    with _lock:
        if _connection is None:
            path = _default_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(path))
            ensure_schema(connection)
            _connection = connection
        return _connection


def _release_connection() -> None:
    """Close the shared connection (if open) and clear the cache.

    Called at the end of every ``upsert_*`` function, success or failure, so
    this process never holds the live DuckDB file open between requests --
    see the module docstring's "UPDATE" note for why.
    """

    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
            _connection = None


def ping() -> None:
    """Prove the live DuckDB file is reachable and queryable, then let it go.

    Used by ``api.py``'s ``/readiness`` check. Deliberately opens its own
    throwaway connection and closes it immediately, rather than going
    through :func:`get_connection`'s shared cache -- a readiness probe
    (polled repeatedly, e.g. by a container healthcheck) must never itself
    become a standing source of the same lock contention
    ``_release_connection()`` exists to avoid. Raises whatever
    ``duckdb`` raises on failure; callers decide how to report that.
    """

    path = _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        connection = duckdb.connect(str(path))
        try:
            ensure_schema(connection)
            connection.execute("SELECT 1")
        finally:
            connection.close()


def ensure_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the six live analytics tables if they do not already exist.

    Column names/types are reused verbatim from
    ``experiments.synthetic_corpus.ANALYTICS_COLUMNS`` /
    ``ANALYTICS_COLUMN_TYPES`` -- the exact schema the dashboard's
    ``AnalyticsRepository`` already queries against. See the module
    docstring for why ``events``/``evaluations``/``outcomes`` additionally
    get a PRIMARY KEY here.
    """

    for table in LIVE_TABLES:
        columns = ANALYTICS_COLUMNS[table]
        id_column = _SEMANTIC_ID_COLUMN.get(table)

        def _definition(column: str, id_column: str | None = id_column) -> str:
            sql_type = ANALYTICS_COLUMN_TYPES.get(column, "VARCHAR")
            suffix = " PRIMARY KEY" if column == id_column else ""
            return f'"{column}" {sql_type}{suffix}'

        definitions = ", ".join(_definition(column) for column in columns)
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({definitions})')
    connection.execute(SERVING_DDL)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_templates (
            workflow_id VARCHAR,
            template_hash VARCHAR,
            name VARCHAR,
            definition VARCHAR,
            source VARCHAR,
            registered_at TIMESTAMP,
            PRIMARY KEY (workflow_id, template_hash)
        );
        CREATE TABLE IF NOT EXISTS execution_workflows (
            execution_id VARCHAR PRIMARY KEY,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            match_source VARCHAR,
            matched_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS workflow_execution_projections (
            execution_id VARCHAR PRIMARY KEY,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            projector_version VARCHAR,
            projection VARCHAR,
            projected_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS workflow_execution_nodes (
            execution_id VARCHAR,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            node_id VARCHAR,
            state VARCHAR,
            attempts BIGINT,
            duration_seconds DOUBLE,
            known_cost DOUBLE,
            total_tokens DOUBLE,
            providers VARCHAR,
            models VARCHAR,
            evidence VARCHAR,
            cost_eligible_operations BIGINT,
            cost_measured_operations BIGINT,
            token_eligible_operations BIGINT,
            token_measured_operations BIGINT,
            PRIMARY KEY (execution_id, node_id)
        );
        CREATE TABLE IF NOT EXISTS participant_execution_facts (
            execution_id VARCHAR,
            dimension VARCHAR,
            participant_id VARCHAR,
            label VARCHAR,
            provider_id VARCHAR,
            model_id VARCHAR,
            model_family VARCHAR,
            vendor_id VARCHAR,
            calls BIGINT,
            active_seconds DOUBLE,
            call_durations VARCHAR,
            measured_cost DOUBLE,
            total_tokens DOUBLE,
            cost_eligible_operations BIGINT,
            cost_measured_operations BIGINT,
            token_eligible_operations BIGINT,
            token_measured_operations BIGINT,
            PRIMARY KEY (execution_id, dimension, participant_id)
        );
        CREATE TABLE IF NOT EXISTS operation_classification_facts (
            operation_id VARCHAR PRIMARY KEY,
            execution_id VARCHAR,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            node_id VARCHAR,
            taxonomy_version VARCHAR,
            family VARCHAR,
            operation_type VARCHAR,
            subtype VARCHAR,
            interface VARCHAR,
            role VARCHAR,
            input_modalities VARCHAR,
            output_modalities VARCHAR,
            provider_id VARCHAR,
            model_id VARCHAR,
            gateway_id VARCHAR,
            vendor_id VARCHAR,
            runtime_id VARCHAR,
            framework_id VARCHAR,
            duration_seconds DOUBLE,
            status VARCHAR,
            attributes VARCHAR
        );
        CREATE TABLE IF NOT EXISTS operation_measurement_facts (
            operation_id VARCHAR,
            execution_id VARCHAR,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            node_id VARCHAR,
            registry_version VARCHAR,
            measurement_key VARCHAR,
            value DOUBLE,
            unit VARCHAR,
            aggregation VARCHAR,
            scope VARCHAR,
            measurement_status VARCHAR,
            provenance VARCHAR,
            applicability_source VARCHAR,
            attempt BIGINT,
            PRIMARY KEY (operation_id, measurement_key)
        );
        CREATE TABLE IF NOT EXISTS evaluation_campaigns (
            campaign_id VARCHAR PRIMARY KEY,
            suite_id VARCHAR,
            workflow_id VARCHAR,
            template_hash VARCHAR,
            dataset_id VARCHAR,
            dataset_version VARCHAR,
            candidate_version VARCHAR,
            baseline_version VARCHAR,
            status VARCHAR,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            attributes VARCHAR
        );
        CREATE TABLE IF NOT EXISTS evaluation_case_results (
            result_id VARCHAR PRIMARY KEY,
            campaign_id VARCHAR,
            case_id VARCHAR,
            execution_id VARCHAR,
            subject_type VARCHAR,
            subject_id VARCHAR,
            evaluation_key VARCHAR,
            definition_version VARCHAR,
            value VARCHAR,
            label VARCHAR,
            score DOUBLE,
            passed BOOLEAN,
            target VARCHAR,
            direction VARCHAR,
            evaluator_type VARCHAR,
            evaluator_id VARCHAR,
            evidence VARCHAR,
            observed_at TIMESTAMP,
            attributes VARCHAR
        );
        """
    )
    # Additive serving migrations keep existing local databases rebuildable.
    for column, sql_type in (
        ("providers", "VARCHAR"),
        ("provider_adapters", "VARCHAR"),
        ("models", "VARCHAR"),
        ("workflows", "VARCHAR"),
        ("operation_count", "BIGINT"),
        ("source_ingest_ids", "VARCHAR"),
        ("assurance_status", "VARCHAR"),
    ):
        connection.execute(f'ALTER TABLE serving.execution_facts ADD COLUMN IF NOT EXISTS "{column}" {sql_type}')
    connection.execute('ALTER TABLE serving.operation_facts ADD COLUMN IF NOT EXISTS "provider_adapter" VARCHAR')
    for column in (
        "cost_eligible_operations",
        "cost_measured_operations",
        "token_eligible_operations",
        "token_measured_operations",
    ):
        connection.execute(f'ALTER TABLE workflow_execution_nodes ADD COLUMN IF NOT EXISTS "{column}" BIGINT')
    connection.execute('ALTER TABLE operation_measurement_facts ADD COLUMN IF NOT EXISTS "attempt" BIGINT')
    connection.execute('ALTER TABLE participant_execution_facts ADD COLUMN IF NOT EXISTS "vendor_id" VARCHAR')


def _row_from_model(model: Execution | Operation | Link | Event | Evaluation | Outcome, table: str) -> dict[str, Any]:
    """Convert a canonical model instance into a DB row dict for ``table``.

    Uses the same ``model_dump(mode="json")`` and selective ``json.dumps``
    convention as the analytics repository read layer.
    """

    dumped = model.model_dump(mode="json")
    json_columns = _JSON_COLUMNS.get(table, frozenset())
    row: dict[str, Any] = {}
    for column in ANALYTICS_COLUMNS[table]:
        value = dumped.get(column)
        if column in json_columns and value is not None:
            row[column] = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        else:
            row[column] = value
    return row


def _insert_one(connection: duckdb.DuckDBPyConnection, table: str, row: Mapping[str, Any]) -> None:
    columns = ANALYTICS_COLUMNS[table]
    column_list = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders})',
        [row.get(column) for column in columns],
    )


def _insert_many(connection: duckdb.DuckDBPyConnection, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    columns = ANALYTICS_COLUMNS[table]
    column_list = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders})',
        [[row.get(column) for column in columns] for row in rows],
    )


def _insert_mappings(connection: duckdb.DuckDBPyConnection, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    column_list = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
        [[row.get(column) for column in columns] for row in rows],
    )


def upsert_execution(execution: Execution) -> None:
    """Upsert one execution row, keyed by ``execution_id``.

    Implemented as ``DELETE ... WHERE execution_id = ?`` + re-``INSERT`` in
    one transaction (docs/architecture.md) -- consistent with, and cheap at the
    same scale as, ``upsert_operations_and_links``. Releases the shared
    connection when done (``_release_connection()``), success or failure --
    see the module docstring's "UPDATE" note.
    """

    row = _row_from_model(execution, "executions")
    with _file_lock(_default_db_path()):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    connection.execute('DELETE FROM "executions" WHERE execution_id = ?', [execution.execution_id])
                    _insert_one(connection, "executions", row)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def ensure_semantic_execution(
    execution_id: str,
    *,
    runtime_id: str | None = None,
    terminal_status: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    """Create an SDK-only execution placeholder without overwriting trace facts."""

    with _file_lock(_default_db_path()):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    existing = connection.execute(
                        'SELECT runtime_id, attributes FROM "executions" WHERE execution_id = ? LIMIT 1',
                        [execution_id],
                    ).fetchone()
                    context_keys = {
                        "case_id",
                        "display_name",
                        "execution.name",
                        "gen_ai.agent.name",
                        "model_profile",
                        "runtime_id",
                        "service.name",
                        "workflow.name",
                        "workflow_name",
                    }
                    observed_context = {
                        key: value for key, value in dict(attributes or {}).items() if key in context_keys
                    }
                    if existing is None:
                        _insert_one(
                            connection,
                            "executions",
                            _row_from_model(
                                Execution(
                                    execution_id=execution_id,
                                    runtime_id=runtime_id or "sdk",
                                    started_at=utc_now(),
                                    status=terminal_status or "running",
                                    attributes={"witdem.source": "sdk", **observed_context},
                                ),
                                "executions",
                            ),
                        )
                    else:
                        existing_runtime, existing_attributes = existing
                        stored_attributes = _json_dict(existing_attributes)
                        is_sdk_placeholder = stored_attributes.get("witdem.source") == "sdk"
                        if is_sdk_placeholder:
                            stored_attributes.update(observed_context)
                            resolved_runtime = (
                                runtime_id if runtime_id and existing_runtime == "sdk" else existing_runtime
                            )
                            connection.execute(
                                'UPDATE "executions" SET runtime_id = ?, attributes = ? WHERE execution_id = ?',
                                [resolved_runtime, json.dumps(stored_attributes, separators=(",", ":")), execution_id],
                            )
                        if terminal_status is not None:
                            connection.execute(
                                'UPDATE "executions" SET status = ?, ended_at = COALESCE(ended_at, ?) '
                                "WHERE execution_id = ?",
                                [terminal_status, utc_now().isoformat(), execution_id],
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def upsert_operations_and_links(execution_id: str, operations: list[Operation], links: list[Link]) -> None:
    """Recompute ``operations``/``links`` for one execution.

    Per ``docs/architecture.md`` §2: ``DELETE ... WHERE execution_id = ?`` on
    both tables, followed by re-``INSERT``, inside one transaction. This is
    cheap and trivially idempotent at local-demo scale -- no row-level
    dedupe is built here on purpose. Releases the shared connection when
    done (``_release_connection()``), success or failure -- see the module
    docstring's "UPDATE" note.
    """

    operation_rows = [_row_from_model(operation, "operations") for operation in operations]
    link_rows = [_row_from_model(link, "links") for link in links]
    with _file_lock(_default_db_path()):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    connection.execute('DELETE FROM "operations" WHERE execution_id = ?', [execution_id])
                    connection.execute('DELETE FROM "links" WHERE execution_id = ?', [execution_id])
                    _insert_many(connection, "operations", operation_rows)
                    _insert_many(connection, "links", link_rows)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def upsert_graph(execution: Execution, operations: list[Operation], links: list[Link]) -> None:
    """Persist one complete canonical graph in one short DuckDB session.

    OTLP correlation always derives these three collections together. Keeping
    them under one interprocess lock prevents the dashboard from repeatedly
    winning the lock between two related writes and keeps one export below the
    standard OTLP HTTP timeout during concurrent dashboard refreshes.
    """

    execution_row = _row_from_model(execution, "executions")
    operation_rows = [_row_from_model(operation, "operations") for operation in operations]
    link_rows = [_row_from_model(link, "links") for link in links]
    path = _default_db_path()
    with _file_lock(path):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    connection.execute(
                        'DELETE FROM "executions" WHERE execution_id = ?',
                        [execution.execution_id],
                    )
                    connection.execute(
                        'DELETE FROM "operations" WHERE execution_id = ?',
                        [execution.execution_id],
                    )
                    connection.execute(
                        'DELETE FROM "links" WHERE execution_id = ?',
                        [execution.execution_id],
                    )
                    _insert_one(connection, "executions", execution_row)
                    _insert_many(connection, "operations", operation_rows)
                    _insert_many(connection, "links", link_rows)
                    _match_configured_workflow(connection, execution)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def publish_transformed_bundle(
    execution: Execution,
    operations: list[Operation],
    links: list[Link],
    semantics: list[Event | Evaluation | Outcome],
    *,
    operation_classifications: Sequence[Mapping[str, Any]] = (),
    operation_measurements: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Atomically replace one execution's canonical and serving projection."""

    execution_id = execution.execution_id
    canonical_rows: dict[str, list[Mapping[str, Any]]] = {
        "executions": [_row_from_model(execution, "executions")],
        "operations": [_row_from_model(item, "operations") for item in operations],
        "links": [_row_from_model(item, "links") for item in links],
        "events": [_row_from_model(item, "events") for item in semantics if isinstance(item, Event)],
        "evaluations": [_row_from_model(item, "evaluations") for item in semantics if isinstance(item, Evaluation)],
        "outcomes": [_row_from_model(item, "outcomes") for item in semantics if isinstance(item, Outcome)],
    }
    serving_rows = build_serving_rows(
        execution,
        operations,
        links,
        semantics,
        transformed_at=utc_now(),
        transform_version=__version__,
    )
    path = _default_db_path()
    with _file_lock(path):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    for table in LIVE_TABLES:
                        connection.execute(f'DELETE FROM "{table}" WHERE execution_id = ?', [execution_id])
                    for table in LIVE_TABLES:
                        _insert_many(connection, table, canonical_rows[table])
                    _match_configured_workflow(connection, execution)
                    association = connection.execute(
                        "SELECT workflow_id, template_hash FROM execution_workflows WHERE execution_id = ?",
                        [execution_id],
                    ).fetchone()
                    for semantic in semantics:
                        if isinstance(semantic, Event) and semantic.name == "workflow.definition":
                            _register_definition_event(connection, execution_id, semantic.payload)
                    for table in (
                        "execution_facts",
                        "operation_facts",
                        "semantic_facts",
                        "execution_edges",
                        "path_facts",
                    ):
                        connection.execute(f'DELETE FROM serving."{table}" WHERE execution_id = ?', [execution_id])
                        _insert_mappings(connection, f'serving."{table}"', serving_rows[table])
                    connection.execute("DELETE FROM operation_measurement_facts WHERE execution_id = ?", [execution_id])
                    connection.execute(
                        "DELETE FROM operation_classification_facts WHERE execution_id = ?", [execution_id]
                    )
                    workflow_id = str(association[0]) if association else None
                    template_hash = str(association[1]) if association else None
                    for fact in operation_classifications:
                        connection.execute(
                            "INSERT INTO operation_classification_facts VALUES "
                            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [
                                fact.get("operation_id"),
                                execution_id,
                                workflow_id,
                                template_hash,
                                None,
                                fact.get("taxonomy_version"),
                                fact.get("family"),
                                fact.get("operation_type"),
                                fact.get("subtype"),
                                fact.get("interface"),
                                fact.get("role"),
                                json.dumps(fact.get("input_modalities") or []),
                                json.dumps(fact.get("output_modalities") or []),
                                fact.get("provider_id"),
                                fact.get("model_id"),
                                fact.get("gateway_id"),
                                fact.get("vendor_id"),
                                fact.get("runtime_id"),
                                fact.get("framework_id"),
                                fact.get("duration_seconds"),
                                fact.get("status"),
                                json.dumps(fact.get("attributes") or {}, sort_keys=True),
                            ],
                        )
                    for fact in operation_measurements:
                        connection.execute(
                            "INSERT INTO operation_measurement_facts VALUES "
                            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [
                                fact.get("operation_id"),
                                execution_id,
                                workflow_id,
                                template_hash,
                                None,
                                fact.get("registry_version"),
                                fact.get("measurement_key"),
                                fact.get("value"),
                                fact.get("unit"),
                                fact.get("aggregation"),
                                fact.get("scope"),
                                fact.get("measurement_status"),
                                fact.get("provenance"),
                                fact.get("applicability_source"),
                                fact.get("attempt"),
                            ],
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def delete_execution_projections(execution_ids: Sequence[str]) -> None:
    """Delete canonical and serving rows for executions absent from the corpus."""

    identifiers = sorted({str(value) for value in execution_ids if value})
    if not identifiers:
        return
    path = _default_db_path()
    with _file_lock(path):
        connection = get_connection()
        try:
            with _lock:
                connection.begin()
                try:
                    for execution_id in identifiers:
                        for table in LIVE_TABLES:
                            connection.execute(f'DELETE FROM "{table}" WHERE execution_id = ?', [execution_id])
                        for table in (
                            "execution_facts",
                            "operation_facts",
                            "semantic_facts",
                            "execution_edges",
                            "path_facts",
                        ):
                            connection.execute(
                                f'DELETE FROM serving."{table}" WHERE execution_id = ?',
                                [execution_id],
                            )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        finally:
            _release_connection()


def clear_transform_runs() -> None:
    """Clear rebuildable ELT operational history before a retention rebuild."""

    path = _default_db_path()
    with _file_lock(path):
        connection = get_connection()
        try:
            with _lock:
                connection.execute("DELETE FROM witdem_control.transform_runs")
        finally:
            _release_connection()


def record_transform_run(row: Mapping[str, Any]) -> None:
    """Upsert one operational Duckle run without exposing it as analytics data."""

    columns = (
        "transform_run_id",
        "started_at",
        "ended_at",
        "status",
        "engine",
        "engine_version",
        "input_batches",
        "affected_executions",
        "error",
    )
    with _file_lock(_default_db_path()):
        connection = get_connection()
        try:
            with _lock:
                connection.execute(
                    "INSERT OR REPLACE INTO witdem_control.transform_runs "
                    f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    [row.get(column) for column in columns],
                )
        finally:
            _release_connection()


def upsert_semantic(record: Event | Evaluation | Outcome) -> None:
    """``INSERT OR REPLACE`` one SDK-sourced semantic row, keyed by its own id.

    Idempotency here relies on ``ensure_schema`` having created the target
    table with a PRIMARY KEY on that id column (see module docstring).
    Releases the shared connection when done (``_release_connection()``),
    success or failure -- see the module docstring's "UPDATE" note.
    """

    if isinstance(record, Event):
        table = "events"
    elif isinstance(record, Evaluation):
        table = "evaluations"
    elif isinstance(record, Outcome):
        table = "outcomes"
    else:  # pragma: no cover - guarded by the Event/Evaluation/Outcome union upstream
        raise TypeError(f"unsupported semantic record type: {type(record)!r}")

    row = _row_from_model(record, table)
    columns = ANALYTICS_COLUMNS[table]
    column_list = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    with _file_lock(_default_db_path()):
        connection = get_connection()
        try:
            with _lock:
                connection.execute(
                    f'INSERT OR REPLACE INTO "{table}" ({column_list}) VALUES ({placeholders})',
                    [row.get(column) for column in columns],
                )
                if isinstance(record, Event) and record.name == "workflow.definition":
                    _register_definition_event(connection, record.execution_id, record.payload)
        finally:
            _release_connection()


def _store_workflow_definition(
    connection: duckdb.DuckDBPyConnection,
    definition: Any,
    *,
    source: str,
) -> None:
    from witdem.workflows import WorkflowDefinition, compile_definition

    validated = (
        definition if isinstance(definition, WorkflowDefinition) else WorkflowDefinition.model_validate(definition)
    )
    connection.execute(
        "INSERT OR REPLACE INTO workflow_templates "
        "(workflow_id, template_hash, name, definition, source, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            validated.id,
            validated.template_hash,
            validated.name,
            json.dumps(validated.model_dump(mode="json", by_alias=True), sort_keys=True),
            source,
            utc_now(),
        ],
    )
    compile_definition(validated)


def _associate_workflow(
    connection: duckdb.DuckDBPyConnection,
    execution_id: str,
    workflow_id: str,
    template_hash: str,
    source: str,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO execution_workflows "
        "(execution_id, workflow_id, template_hash, match_source, matched_at) VALUES (?, ?, ?, ?, ?)",
        [execution_id, workflow_id, template_hash, source, utc_now()],
    )


def store_workflow_projection(path: str | Path, projection: Mapping[str, Any]) -> None:
    """Atomically replace one rebuildable workflow execution projection."""

    from witdem.workflows import WORKFLOW_PROJECTOR_VERSION

    database_path = Path(path).expanduser()
    workflow = dict(projection.get("workflow") or {})
    execution = dict(projection.get("execution") or {})
    execution_id = str(execution.get("execution_id") or "")
    workflow_id = str(workflow.get("id") or "")
    template_hash = str(workflow.get("template_hash") or "")
    if not execution_id or not workflow_id or not template_hash:
        raise ValueError("workflow projection requires execution, workflow, and template identifiers")
    with _file_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            ensure_schema(connection)
            connection.execute("BEGIN")
            connection.execute("DELETE FROM workflow_execution_nodes WHERE execution_id = ?", [execution_id])
            connection.execute("DELETE FROM workflow_execution_projections WHERE execution_id = ?", [execution_id])
            connection.execute(
                "INSERT INTO workflow_execution_projections VALUES (?, ?, ?, ?, ?, ?)",
                [
                    execution_id,
                    workflow_id,
                    template_hash,
                    WORKFLOW_PROJECTOR_VERSION,
                    json.dumps(dict(projection), sort_keys=True, default=str),
                    utc_now(),
                ],
            )
            for node in projection.get("nodes", []):
                if not isinstance(node, Mapping):
                    continue
                connection.execute(
                    "INSERT INTO workflow_execution_nodes "
                    "(execution_id, workflow_id, template_hash, node_id, state, attempts, duration_seconds, "
                    "known_cost, total_tokens, providers, models, evidence, cost_eligible_operations, "
                    "cost_measured_operations, token_eligible_operations, token_measured_operations) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        execution_id,
                        workflow_id,
                        template_hash,
                        str(node.get("id") or ""),
                        str(node.get("state") or "inactive"),
                        int(node.get("attempts") or 0),
                        node.get("duration_seconds"),
                        node.get("known_cost"),
                        node.get("total_tokens"),
                        json.dumps(node.get("providers") or []),
                        json.dumps(node.get("models") or []),
                        json.dumps(
                            {
                                "observations": node.get("observations") or [],
                                "model_calls": node.get("model_calls") or [],
                            },
                            sort_keys=True,
                            default=str,
                        ),
                        int(node.get("cost_eligible_operations") or 0),
                        int(node.get("cost_measured_operations") or 0),
                        int(node.get("token_eligible_operations") or 0),
                        int(node.get("token_measured_operations") or 0),
                    ],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def store_participant_facts(path: str | Path, execution_ids: Sequence[str], facts: Sequence[Mapping[str, Any]]) -> None:
    """Atomically replace rebuildable direct-attribution facts."""

    database_path = Path(path).expanduser()
    selected = sorted(set(execution_ids))
    with _file_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            ensure_schema(connection)
            connection.execute("BEGIN")
            if selected:
                placeholders = ", ".join("?" for _ in selected)
                connection.execute(
                    f"DELETE FROM participant_execution_facts WHERE execution_id IN ({placeholders})",
                    selected,
                )
            for fact in facts:
                connection.execute(
                    """INSERT INTO participant_execution_facts (
                        execution_id, dimension, participant_id, label, provider_id,
                        model_id, model_family, vendor_id, calls, active_seconds,
                        call_durations, measured_cost, total_tokens,
                        cost_eligible_operations, cost_measured_operations,
                        token_eligible_operations, token_measured_operations
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        fact.get("execution_id"),
                        fact.get("dimension"),
                        fact.get("participant_id"),
                        fact.get("label"),
                        fact.get("provider_id"),
                        fact.get("model_id"),
                        fact.get("model_family"),
                        fact.get("vendor_id"),
                        fact.get("calls"),
                        fact.get("active_seconds"),
                        json.dumps(fact.get("call_durations") or []),
                        fact.get("measured_cost"),
                        fact.get("total_tokens"),
                        fact.get("cost_eligible_operations"),
                        fact.get("cost_measured_operations"),
                        fact.get("token_eligible_operations"),
                        fact.get("token_measured_operations"),
                    ],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def store_operation_facts(
    path: str | Path,
    execution_ids: Sequence[str],
    classifications: Sequence[Mapping[str, Any]],
    measurements: Sequence[Mapping[str, Any]],
) -> None:
    """Atomically replace rebuildable operation classifications and meters."""

    database_path = Path(path).expanduser()
    selected = sorted(set(execution_ids))
    with _file_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            ensure_schema(connection)
            connection.execute("BEGIN")
            if selected:
                placeholders = ", ".join("?" for _ in selected)
                connection.execute(
                    f"DELETE FROM operation_measurement_facts WHERE execution_id IN ({placeholders})",
                    selected,
                )
                connection.execute(
                    f"DELETE FROM operation_classification_facts WHERE execution_id IN ({placeholders})",
                    selected,
                )
            for fact in classifications:
                connection.execute(
                    """INSERT INTO operation_classification_facts VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    [
                        fact.get("operation_id"),
                        fact.get("execution_id"),
                        fact.get("workflow_id"),
                        fact.get("template_hash"),
                        fact.get("node_id"),
                        fact.get("taxonomy_version"),
                        fact.get("family"),
                        fact.get("operation_type"),
                        fact.get("subtype"),
                        fact.get("interface"),
                        fact.get("role"),
                        json.dumps(fact.get("input_modalities") or []),
                        json.dumps(fact.get("output_modalities") or []),
                        fact.get("provider_id"),
                        fact.get("model_id"),
                        fact.get("gateway_id"),
                        fact.get("vendor_id"),
                        fact.get("runtime_id"),
                        fact.get("framework_id"),
                        fact.get("duration_seconds"),
                        fact.get("status"),
                        json.dumps(fact.get("attributes") or {}, sort_keys=True, default=str),
                    ],
                )
            for fact in measurements:
                connection.execute(
                    """INSERT INTO operation_measurement_facts VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )""",
                    [
                        fact.get("operation_id"),
                        fact.get("execution_id"),
                        fact.get("workflow_id"),
                        fact.get("template_hash"),
                        fact.get("node_id"),
                        fact.get("registry_version"),
                        fact.get("measurement_key"),
                        fact.get("value"),
                        fact.get("unit"),
                        fact.get("aggregation"),
                        fact.get("scope"),
                        fact.get("measurement_status"),
                        fact.get("provenance"),
                        fact.get("applicability_source"),
                        fact.get("attempt"),
                    ],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def store_evaluation_campaign(path: str | Path, campaign: Any, results: Sequence[Any]) -> None:
    """Atomically import one validated, rebuildable offline campaign."""

    database_path = Path(path).expanduser()
    with _file_lock(database_path):
        connection = duckdb.connect(str(database_path))
        try:
            ensure_schema(connection)
            connection.execute("BEGIN")
            connection.execute("DELETE FROM evaluation_case_results WHERE campaign_id = ?", [campaign.campaign_id])
            connection.execute("DELETE FROM evaluation_campaigns WHERE campaign_id = ?", [campaign.campaign_id])
            connection.execute(
                "INSERT INTO evaluation_campaigns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    campaign.campaign_id,
                    campaign.suite_id,
                    campaign.workflow_id,
                    campaign.template_hash,
                    campaign.dataset_id,
                    campaign.dataset_version,
                    campaign.candidate_version,
                    campaign.baseline_version,
                    campaign.status,
                    campaign.started_at,
                    campaign.ended_at,
                    json.dumps(campaign.attributes, sort_keys=True, default=str),
                ],
            )
            for result in results:
                connection.execute(
                    "INSERT INTO evaluation_case_results VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        result.stable_id(),
                        result.campaign_id,
                        result.case_id,
                        result.execution_id,
                        result.subject_type,
                        result.subject_id or result.case_id,
                        result.evaluation_key,
                        result.definition_version,
                        json.dumps(result.value, sort_keys=True, default=str),
                        result.label,
                        result.score,
                        result.passed,
                        json.dumps(result.target, sort_keys=True, default=str),
                        result.direction,
                        result.evaluator_type,
                        result.evaluator_id,
                        json.dumps(result.evidence, sort_keys=True),
                        result.observed_at,
                        json.dumps(result.attributes, sort_keys=True, default=str),
                    ],
                )
            connection.execute("COMMIT")
            connection.execute("CHECKPOINT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def _match_configured_workflow(connection: duckdb.DuckDBPyConnection, execution: Execution) -> None:
    from witdem.workflows import load_registry

    registry = load_registry()
    definition = registry.match(execution.model_dump(mode="json"))
    if definition is None:
        return
    _store_workflow_definition(connection, definition, source="project_config")
    explicit = bool(execution.attributes.get("witdem.workflow.id"))
    _associate_workflow(
        connection,
        execution.execution_id,
        definition.id,
        definition.template_hash,
        "sdk_explicit" if explicit else "configured_match",
    )


def _register_definition_event(
    connection: duckdb.DuckDBPyConnection,
    execution_id: str,
    payload: Mapping[str, Any],
) -> None:
    definition = payload.get("definition")
    if not isinstance(definition, Mapping):
        return
    from witdem.workflows import WorkflowDefinition

    validated = WorkflowDefinition.model_validate(definition)
    _store_workflow_definition(connection, validated, source="sdk")
    _associate_workflow(connection, execution_id, validated.id, validated.template_hash, "sdk_definition")
