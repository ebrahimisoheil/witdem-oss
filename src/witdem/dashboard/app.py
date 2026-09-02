"""FastAPI read application for the bundled Witdem dashboard."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from filelock import Timeout as FileLockTimeout

from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.repository.state import FilterState
from witdem.config import db_path
from witdem.dashboard import service
from witdem.dashboard.schemas import (
    ComparisonResponse,
    EvaluationCampaignResponse,
    HealthResponse,
    IssuesResponse,
    MetadataResponse,
    OverviewResponse,
    RunDetailResponse,
    RunsResponse,
    WorkflowCatalogResponse,
    WorkflowDetailResponse,
    WorkflowEvaluationCampaignsResponse,
    WorkflowEvaluationsResponse,
    WorkflowOperationsResponse,
    WorkflowsResponse,
)
from witdem.protocol import DASHBOARD_API_VERSION


def _filter_state(
    workflow: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    tool: str | None = None,
    stage: str | None = None,
    contract_hash: str | None = None,
    goal_status: str | None = None,
    assurance_status: str | None = None,
    application_outcome: str | None = None,
    blocker: str | None = None,
    evaluation_key: str | None = None,
    evaluation_status: str | None = None,
    cost_status: str | None = None,
    token_status: str | None = None,
    operation_type: str | None = None,
    operation_status: str | None = None,
    failure_location: str | None = None,
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
        goal_status=goal_status,
        assurance_status=assurance_status,
        application_outcome=application_outcome,
        blocker=blocker,
        evaluation_key=evaluation_key,
        evaluation_status=evaluation_status,
        cost_status=cost_status,
        token_status=token_status,
        operation_type=operation_type,
        operation_status=operation_status,
        failure_location=failure_location,
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

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/meta", response_model=MetadataResponse, tags=["metadata"])
    def meta() -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.metadata(repo)

    @app.get("/api/v1/overview", response_model=OverviewResponse, tags=["analytics"])
    def overview(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.overview(repo, filters)

    @app.get("/api/v1/runs", response_model=RunsResponse, tags=["runs"])
    def runs(
        filters: Annotated[FilterState, Depends(_filter_state)],
        page: int = 1,
        page_size: int = 10,
        workflow_id: str | None = None,
    ) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.runs(repo, filters, page, page_size, workflow_id=workflow_id)

    @app.get("/api/v1/runs/{execution_id}", response_model=RunDetailResponse, tags=["runs"])
    def run(execution_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.run_detail(repo, execution_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get(
        "/api/v1/runs/{execution_id}/evidence-bundle",
        response_model=EvidenceBundle,
        tags=["runs"],
    )
    def run_evidence_bundle(execution_id: str) -> EvidenceBundle:
        with service.repository(database_path) as repo:
            result = service.evidence_bundle(repo, execution_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @app.get("/api/v1/compare/{dimension}", response_model=ComparisonResponse, tags=["analytics"])
    def compare(
        dimension: Literal["provider", "model"],
        filters: Annotated[FilterState, Depends(_filter_state)],
    ) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.compare(repo, dimension, filters)

    @app.get("/api/v1/workflows", response_model=WorkflowsResponse, tags=["workflows"])
    def workflows(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.workflows(repo, filters)

    @app.get("/api/v1/workflow-definitions", response_model=WorkflowCatalogResponse, tags=["workflows"])
    def workflow_definitions() -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.workflow_catalog(repo)

    @app.get(
        "/api/v1/workflow-definitions/{workflow_id}",
        response_model=WorkflowDetailResponse,
        tags=["workflows"],
    )
    def workflow_definition(workflow_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.workflow_detail(repo, workflow_id)
        if result is None:
            raise HTTPException(status_code=404, detail="workflow definition not found")
        return result

    @app.get(
        "/api/v1/workflow-definitions/{workflow_id}/executions/{execution_id}",
        response_model=RunDetailResponse,
        tags=["workflows"],
    )
    def workflow_execution(workflow_id: str, execution_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.workflow_execution(repo, workflow_id, execution_id)
        if result is None:
            raise HTTPException(status_code=404, detail="execution is not associated with this workflow")
        return result

    @app.get(
        "/api/v1/workflow-definitions/{workflow_id}/operations",
        response_model=WorkflowOperationsResponse,
        tags=["workflows"],
    )
    def workflow_operations(workflow_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.workflow_operations(repo, workflow_id)
        if result is None:
            raise HTTPException(status_code=404, detail="workflow definition not found")
        return result

    @app.get(
        "/api/v1/workflow-definitions/{workflow_id}/evaluations",
        response_model=WorkflowEvaluationsResponse,
        tags=["workflows"],
    )
    def workflow_evaluations(workflow_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.workflow_evaluations(repo, workflow_id)
        if result is None:
            raise HTTPException(status_code=404, detail="workflow definition not found")
        return result

    @app.get(
        "/api/v1/workflow-definitions/{workflow_id}/evaluation-campaigns",
        response_model=WorkflowEvaluationCampaignsResponse,
        tags=["workflows"],
    )
    def workflow_evaluation_campaigns(workflow_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.workflow_evaluation_campaigns(repo, workflow_id)
        if result is None:
            raise HTTPException(status_code=404, detail="workflow definition not found")
        return result

    @app.get(
        "/api/v1/evaluation-campaigns/{campaign_id}",
        response_model=EvaluationCampaignResponse,
        tags=["evaluations"],
    )
    def evaluation_campaign(campaign_id: str) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            result = service.evaluation_campaign(repo, campaign_id)
        if result is None:
            raise HTTPException(status_code=404, detail="evaluation campaign not found")
        return result

    @app.get("/api/v1/issues", response_model=IssuesResponse, tags=["analytics"])
    def issues(filters: Annotated[FilterState, Depends(_filter_state)]) -> dict[str, Any]:
        with service.repository(database_path) as repo:
            return service.issues(repo, filters)

    if assets.is_dir():
        asset_dir = assets / "assets"

        @app.get("/runs/{execution_id}", include_in_schema=False)
        def canonical_execution(execution_id: str) -> RedirectResponse:
            """Keep one execution UI: the YAML-backed workflow replay."""
            with service.repository(database_path) as repo:
                result = service.run_detail(repo, execution_id)
            canonical_url = result.get("canonical_url") if result else None
            target = str(canonical_url) if canonical_url else f"/runs?unavailable_replay={execution_id}"
            return RedirectResponse(target, status_code=307)

        if asset_dir.is_dir():
            resolved_asset_dir = asset_dir.resolve()

            @app.get("/assets/{asset_name:path}", include_in_schema=False)
            def dashboard_asset(asset_name: str) -> Response:
                candidate = (resolved_asset_dir / asset_name).resolve()
                if candidate.is_relative_to(resolved_asset_dir) and candidate.is_file():
                    return FileResponse(
                        candidate,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"},
                    )
                if asset_name.startswith("advanced-workflow-graph-") and asset_name.endswith(".js"):
                    return Response(
                        "window.location.reload(); export const AdvancedWorkflowGraph = () => null;",
                        media_type="text/javascript",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                    )
                raise HTTPException(status_code=404, detail="asset not found")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = assets / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(
                assets / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    return app


app = create_dashboard_app()
