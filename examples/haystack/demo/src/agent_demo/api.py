"""FastAPI app for the examples/haystack/demo.

``POST /run {"scenario": str, "mode": "telemetry_only" | "sdk_enriched"}``
runs the one shared Haystack workflow (see ``workflow.py``) for the named
scenario, then -- only when ``mode == "sdk_enriched"`` -- calls out to
``enrichment.py`` at exactly four business-decision points. ``GET /health``
needs no Witdem/witdem_sdk imports at all.

Telemetry-only invariant (task requirement 3): the ``import`` of
``enrichment`` (the only module that touches ``witdem_sdk``) is a *local*
import placed inside the ``mode == "sdk_enriched"`` branch below, not a
module-level import. A telemetry_only request therefore never executes that
import statement, so it never imports witdem_sdk either -- see
tests/test_no_witdem_sdk_import.py for the grep-based proof.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent_demo.fake_provider import FakeChatProvider, UnknownScenarioError
from agent_demo.otel_setup import configure_tracing
from agent_demo.scenarios import REGISTRY
from agent_demo.workflow import WorkflowResult, build_workflow, run_workflow

logger = logging.getLogger(__name__)

# Module-level, one-time startup wiring -- configure_tracing() is idempotent
# (see otel_setup._CONFIGURED), so this is safe even if something else in the
# process already configured tracing first (e.g. a test's in-memory exporter,
# which deliberately wins that race -- see tests/conftest.py).
configure_tracing()

# _PROVIDER is a safe, stateless, read-only singleton (it just looks up
# ScriptedScenario entries from an immutable dict; see fake_provider.py).
#
# build_workflow(), however, is deliberately called FRESH inside the /run
# handler below rather than once here at module level. This was a real bug
# found via an actual concurrent Docker run, not a hypothetical: uvicorn
# runs each sync endpoint in a threadpool, so two overlapping /run requests
# can execute run_workflow() on separate threads at the same time. A single
# shared Workflow (i.e. shared haystack.Pipeline / component instances)
# is not safe under concurrent .run() calls -- Haystack's own internal
# per-run scheduling state got confused across the two concurrent calls and
# logged "Cannot run pipeline - the pipeline appears to be blocked" for a
# component (generate_turn) that was, in its own call's data, perfectly
# runnable. Building a fresh Workflow per request gives every request its
# own isolated Pipeline/component graph, which is cheap (no I/O, no heavy
# resources) and eliminates the shared-mutable-state race entirely.
_PROVIDER = FakeChatProvider(REGISTRY)

app = FastAPI(title="agent-demo", version="0.1.0")


class RunRequest(BaseModel):
    scenario: str
    mode: Literal["telemetry_only", "sdk_enriched"] = "telemetry_only"


class RunResponse(BaseModel):
    execution_id: str
    scenario: str
    mode: str
    status: str
    route: str
    final_answer: str | None
    quality_score: float | None
    tool_call_count: int
    correction_count: int
    turn_count: int
    sdk_enriched: bool


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest) -> RunResponse:
    if request.scenario not in _PROVIDER.available_scenarios():
        raise HTTPException(
            status_code=400,
            detail=f"unknown scenario {request.scenario!r}; available: {_PROVIDER.available_scenarios()}",
        )

    execution_id = f"agent-demo-{uuid4().hex}"
    workflow = build_workflow(_PROVIDER)  # fresh per request -- see the module-level note above
    try:
        result: WorkflowResult = run_workflow(workflow, scenario=request.scenario, execution_id=execution_id)
    except UnknownScenarioError as exc:  # pragma: no cover - already guarded above
        raise HTTPException(status_code=400, detail=f"unknown scenario: {exc}") from exc

    sdk_enriched = False
    if request.mode == "sdk_enriched":
        # ---- the ONLY branch, in the ONLY handler, that ever imports enrichment.py ----
        from agent_demo.enrichment import WITDEM_SDK_AVAILABLE, enrich_execution

        if not WITDEM_SDK_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail=(
                    "sdk_enriched mode requested but witdem_sdk is not installed. "
                    "Install the 'enriched' extra once ../../../witdem-sdk exists "
                    "(pip install -e '.[enriched]' or uv sync --extra enriched)."
                ),
            )
        enrich_execution(result)
        sdk_enriched = True
    # mode == "telemetry_only": nothing above this comment on this branch ever
    # references agent_demo.enrichment or witdem_sdk.

    return RunResponse(
        execution_id=execution_id,
        scenario=request.scenario,
        mode=request.mode,
        status=result.status,
        route=result.route,
        final_answer=result.final_answer,
        quality_score=result.quality_score,
        tool_call_count=result.tool_call_count,
        correction_count=result.correction_count,
        turn_count=result.turn_count,
        sdk_enriched=sdk_enriched,
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
