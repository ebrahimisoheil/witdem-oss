"""FastAPI read application for the bundled Witdem dashboard."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import Timeout as FileLockTimeout

from witdem.analytics.repository.state import FilterState
from witdem.config import db_path
from witdem.dashboard import service
from witdem.protocol import DASHBOARD_API_VERSION


def _filter_state(
    workflow: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    tool: str | None = None,
    stage: str | None = None,
    contract_hash: str | None = None,
    has_repeated_work: bool = False,
    has_failure: bool = False,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FilterState:
    return service.filters_from_values(
        workflow=workflow,
        status=status,
        provider=provider,
        model=model,
        tool=tool,
        stage=stage,
        contract_hash=contract_hash,
        has_repeated_work=has_repeated_work,
        has_failure=has_failure,
        start_date=start_date,
        end_date=end_date,
    )


def create_dashboard_app(database: str | Path | None = None, static_dir: str | Path | None = None) -> FastAPI:
    database_path = db_path(database)
    assets = Path(static_dir) if static_dir else Path(__file__).with_name("static")
    app = FastAPI(
        title="Witdem Dashboard API",
        version=DASHBOARD_API_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(FileLockTimeout)
    async def data_busy(_request: Request, _exc: FileLockTimeout) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Data is busy while new telemetry is being processed. Retrying shortly is safe."},
            headers={"Retry-After": "1"},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/meta")
    def meta() -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.metadata(repo)

    @app.get("/api/v1/overview")
    def overview(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.overview(repo, filters)

    @app.get("/api/v1/runs")
    def runs(
        filters: Annotated[FilterState, Depends(_filter_state)],
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.runs(repo, filters, page, page_size)

    @app.get("/api/v1/runs/{execution_id}")
    def run(execution_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.run_detail(repo, execution_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get("/api/v1/compare/{dimension}")
    def compare(
        dimension: Literal["provider", "model"],
        filters: Annotated[FilterState, Depends(_filter_state)],
    ) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.compare(repo, dimension, filters)

    @app.get("/api/v1/workflows")
    def workflows(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.workflows(repo, filters)

    @app.get("/api/v1/issues")
    def issues(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.issues(repo, filters)

    if assets.is_dir():
        asset_dir = assets / "assets"
        if asset_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=asset_dir), name="dashboard-assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = assets / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(assets / "index.html")

    return app


app = create_dashboard_app()
