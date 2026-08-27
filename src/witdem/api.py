"""Witdem ingestion service and process health endpoints.

The app mounts the OTLP/HTTP trace receiver at ``POST /v1/traces`` and the
SDK record receiver at ``POST /sdk/v1/records``. It also exposes ingestion
status plus liveness and readiness endpoints.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response

from witdem import __version__
from witdem.analytics.repository import AnalyticsRepository
from witdem.config import db_path
from witdem.ingest import corpus, live_db, raw_store
from witdem.ingest.otlp_http import router as otlp_router
from witdem.ingest.sdk_ingest import router as sdk_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Witdem AI", version=__version__)

app.include_router(otlp_router)
app.include_router(sdk_router)


@app.get("/ingestion/v1/batches/{ingest_id}")
def ingestion_batch(ingest_id: str) -> dict[str, object]:
    """Return durable acceptance and asynchronous analytics status."""

    commit = corpus.read_commit(ingest_id)
    if commit is None:
        raise HTTPException(status_code=404, detail="ingest batch not found")
    state = corpus.read_state(ingest_id) or {"status": "accepted"}
    return {
        "ingest_id": commit.ingest_id,
        "signal": commit.signal,
        "received_at": commit.received_at,
        "record_count": commit.record_count,
        "execution_ids": list(commit.execution_ids),
        "analytics": state,
    }


@app.get("/ingestion/v1/executions/{execution_id}")
def ingestion_execution(execution_id: str) -> dict[str, object]:
    """Report whether all committed signals for an execution are queryable."""

    commits = [commit for commit in corpus.list_commits() if execution_id in commit.execution_ids]
    if not commits:
        raise HTTPException(status_code=404, detail="execution has no committed ingest batches")
    batches: list[dict[str, object]] = []
    statuses: set[str] = set()
    for commit in commits:
        state = corpus.read_state(commit.ingest_id) or {"status": "accepted"}
        statuses.add(str(state.get("status")))
        batches.append({"ingest_id": commit.ingest_id, "signal": commit.signal, "analytics": state})
    status = "failed" if "failed" in statuses else "ready" if statuses == {"ready"} else "pending"
    serving_fact: dict[str, object] | None = None
    if status == "ready":
        repository = AnalyticsRepository(db_path())
        try:
            serving_fact = repository.execution_fact(execution_id)
        finally:
            repository.close()
    return {"execution_id": execution_id, "status": status, "batches": batches, "serving_fact": serving_fact}


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness only — never touches storage. See ``/readiness`` for that."""

    return {"status": "ok"}


@app.get("/readiness")
def readiness(response: Response) -> dict[str, object]:
    """Minimal reachability check for the ingest + storage + read path.

    This does not validate every ingest code path; it confirms
    the two things a load balancer/orchestrator actually needs to know before
    routing traffic here: (1) the live DuckDB file this service upserts
    canonical analytics into is reachable and queryable (``ingest.live_db``,
    the same file the dashboard also reads), and (2) the raw span storage
    directory (``ingest.raw_store``) this service persists to before every
    OTLP export is decoded is reachable. A failure in either returns HTTP 503
    with the specific error, rather than a bare "ok"/500 that hides which
    dependency is down.

    Uses ``live_db.ping()`` rather than ``live_db.get_connection()`` on
    purpose: ``ping()`` opens its own connection and closes it immediately,
    so a readiness probe — polled repeatedly by a container healthcheck, as
    docker-compose.yml's does every 10s — never itself becomes a standing
    open connection that would defeat ``ingest.live_db``'s close-after-every-
    write behavior (see that module's docstring) and permanently block the
    dashboard's read-only access.
    """

    checks: dict[str, str] = {}
    ok = True

    try:
        live_db.ping()
        checks["live_db"] = "ok"
    except Exception as exc:  # noqa: BLE001 - readiness must report any failure, not just expected ones
        ok = False
        checks["live_db"] = f"error: {exc}"
        logger.warning("readiness: live_db check failed: %s", exc)

    try:
        raw_store.list_execution_ids()
        checks["raw_store"] = "ok"
    except Exception as exc:  # noqa: BLE001 - same rationale as above
        ok = False
        checks["raw_store"] = f"error: {exc}"
        logger.warning("readiness: raw_store check failed: %s", exc)

    pricing_override = os.getenv("WITDEM_PRICING_FILE")
    if pricing_override:
        try:
            from witdem.analytics.cost import validate_pricing_override

            validate_pricing_override(Path(pricing_override).expanduser())
            checks["pricing"] = "ok"
        except Exception as exc:  # noqa: BLE001 - invalid pricing must fail readiness
            ok = False
            checks["pricing"] = f"error: {exc}"
            logger.warning("readiness: pricing override validation failed: %s", exc)

    response.status_code = 200 if ok else 503
    return {"status": "ok" if ok else "not_ready", "checks": checks}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=4318)


if __name__ == "__main__":
    main()
