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
    # Additive serving migrations keep existing local databases rebuildable.
    for column, sql_type in (
        ("providers", "VARCHAR"),
        ("provider_adapters", "VARCHAR"),
        ("models", "VARCHAR"),
        ("workflows", "VARCHAR"),
        ("operation_count", "BIGINT"),
        ("source_ingest_ids", "VARCHAR"),
    ):
        connection.execute(f'ALTER TABLE serving.execution_facts ADD COLUMN IF NOT EXISTS "{column}" {sql_type}')
    connection.execute(
        'ALTER TABLE serving.operation_facts ADD COLUMN IF NOT EXISTS "provider_adapter" VARCHAR'
    )


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
        f'INSERT INTO {table} ({column_list}) VALUES ({placeholders})',
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
                    for table in (
                        "execution_facts",
                        "operation_facts",
                        "semantic_facts",
                        "execution_edges",
                        "path_facts",
                    ):
                        connection.execute(f'DELETE FROM serving."{table}" WHERE execution_id = ?', [execution_id])
                        _insert_mappings(connection, f'serving."{table}"', serving_rows[table])
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
        finally:
            _release_connection()
