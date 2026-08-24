"""sdk_enriched mode ONLY. This is the one and only module in agent_demo that
references ``witdem_sdk`` anywhere in its source.

api.py imports this module lazily -- with a local ``import`` statement
placed *inside* the ``if request.mode == "sdk_enriched":`` branch of the
``/run`` handler, never at module level -- so a telemetry_only request never
executes an ``import`` of this module, and therefore never executes an
``import witdem_sdk`` either. That structural guarantee (rather than merely
"unused if present") is what tests/test_no_witdem_sdk_import.py checks via an
AST-based scan of every other file in this package: zero actual
``import witdem_sdk`` / ``from witdem_sdk import ...`` statements outside this
one file (a plain text grep would also flag this docstring and the
human-readable 503 error message in api.py, which are prose, not imports).

The SDK is optional for this example. The guarded import keeps telemetry-only
mode available when the ``enriched`` extra is not installed.
"""

from __future__ import annotations

from agent_demo.workflow import WorkflowResult

try:
    import witdem_sdk

    WITDEM_SDK_AVAILABLE = True
    _IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - exercised only if witdem-sdk isn't installed
    witdem_sdk = None  # type: ignore[assignment]
    WITDEM_SDK_AVAILABLE = False
    _IMPORT_ERROR = exc

# Generic demo domain vocabulary:
# decision="answer_route", evaluation="answer_quality",
# outcome="completed_answer", metric="records_processed". Nothing
# domain-specific to Product Factory appears anywhere in this module.
DECISION_NAME = "answer_route"
EVALUATION_NAME = "answer_quality"
OUTCOME_NAME = "completed_answer"
METRIC_NAME = "records_processed"


def require_witdem_sdk() -> None:
    if not WITDEM_SDK_AVAILABLE:
        raise RuntimeError(
            "witdem_sdk is not installed. sdk_enriched mode requires the witdem-sdk path "
            "dependency (pyproject.toml [project.optional-dependencies].enriched -> "
            "../../../witdem-sdk) to be installed, e.g. via `pip install -e '.[enriched]'` or "
            "`uv sync --extra enriched`."
        ) from _IMPORT_ERROR


def enrich_execution(result: WorkflowResult) -> None:
    """Report four business facts after the shared workflow completes.

    The workflow has already closed its root span, so every report supplies
    the execution id explicitly. Runtime context remains on the correlated
    OpenTelemetry spans and is not duplicated into these SDK records.
    """

    require_witdem_sdk()
    execution_id = result.execution_id

    # --- decision point 1: which route this execution took ---
    witdem_sdk.decision(DECISION_NAME, result.route, execution_id=execution_id)

    # --- decision point 2: quality evaluation of the final answer ---
    witdem_sdk.evaluation(
        EVALUATION_NAME,
        score=result.quality_score,
        label="accepted" if result.status == "success" else "rejected",
        value=result.final_answer,
        execution_id=execution_id,
    )

    # --- decision point 3: terminal outcome of the execution ---
    witdem_sdk.outcome(OUTCOME_NAME, status=result.status, value=result.final_answer, execution_id=execution_id)

    # --- decision point 4: a generic countable metric ---
    # Standing in for "records_processed" with this demo's nearest analog: how
    # many tool invocations this execution performed.
    witdem_sdk.metric(METRIC_NAME, value=float(result.tool_call_count), execution_id=execution_id)
