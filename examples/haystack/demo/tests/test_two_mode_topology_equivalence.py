"""The same scenario, run once in
``telemetry_only`` mode and once in ``sdk_enriched`` mode, must produce the
SAME canonical execution graph. Mode is only ever allowed to add witdem_sdk
calls strictly *after* the shared workflow has already returned
(agent_demo.api's ``/run`` handler) -- it must never change which
Haystack/OTel spans get created, how many, or in what shape.

This test proves that at the level that actually matters end to end: real
spans from a real run of agent_demo's real (Haystack-backed) workflow, sent
through Witdem's real OTLP/HTTP receiver (``ingest.otlp_http.router``, the
same protobuf decode path a live ``witdem-server`` process runs), correlated
and normalized by the real adapter pipeline
(``adapters.registry.detect_adapter`` -> ``analytics.runtime.
normalize_haystack_spans``), and upserted into a real (test-isolated) live
DuckDB -- then compares the two resulting sets of canonical
``Operation`` rows using ``analytics.identity``'s existing
``canonical_operation_key`` / ``canonical_path_signature`` /
``canonical_loop_signature`` (via ``analytics.runtime.
derive_repeated_patterns``, which already calls it) -- never inventing new
comparison rules.

Why this test lives in examples/haystack/demo/tests/ (not
the product test suite): it needs BOTH agent_demo's real workflow
AND witdem's real ingest/analytics modules importable
together in one process, so it can call the real OTLP receiver in-process
via TestClient (no real sockets, subprocess, or Docker) rather than merely re-asserting
"the spans are structurally the same" the way
tests/test_mode_equivalence.py already does one level down (OTel span
attributes, not the derived canonical graph). This project's dev
dependency-group therefore carries a TEST-ONLY path dependency on
the Witdem product tree (see pyproject.toml's ``[tool.uv.sources]`` and
``[dependency-groups].dev``) --
agent_demo's own ``src/`` still imports nothing from witdem
(verified by tests/test_no_witdem_sdk_import.py's sibling invariant for
witdem_sdk, and this file's own module-level imports below are entirely
confined to tests/, never touching src/agent_demo).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fastapi
import pytest
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace import ReadableSpan
from witdem.analytics.core import Execution, Operation
from witdem.analytics.identity import canonical_operation_key, canonical_path_signature
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_repeated_patterns
from witdem.ingest import live_db
from witdem.ingest.otlp_http import router as otlp_router
from witdem.ingest.sdk_ingest import router as sdk_router

from agent_demo.api import app as agent_demo_app
from agent_demo.enrichment import WITDEM_SDK_AVAILABLE

pytestmark = pytest.mark.skipif(
    not WITDEM_SDK_AVAILABLE,
    reason=(
        "witdem_sdk not installed (concurrent build in ../../../witdem-sdk); "
        "this test needs the real sdk_enriched path"
    ),
)

agent_demo_client = TestClient(agent_demo_app)

_OTLP_MEDIA_TYPE = "application/x-protobuf"

SCENARIOS = [
    "simple_success",
    "tool_calling",
    "correction_loop",
    "failure_recovery",
    "terminal_failure",
    "nested",
]


@pytest.fixture(scope="module", autouse=True)
def _isolated_witdem_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """One isolated, test-only Witdem storage root for this whole module.

    A module-scoped fixture (not the usual function-scoped monkeypatch) is
    fine here: every execution_id used below is a fresh
    ``agent-demo-{uuid4().hex}`` (agent_demo.api's own ``/run`` handler), so
    nothing collides across scenarios/modes within one shared store.
    Both the database and corpus use canonical Witdem configuration variables.
    """

    root = tmp_path_factory.mktemp("witdem-topology-equivalence")
    previous_cwd = Path.cwd()
    previous_db_path = os.environ.get("WITDEM_DB_PATH")
    previous_data_dir = os.environ.get("WITDEM_DATA_DIR")
    os.chdir(root)
    os.environ["WITDEM_DATA_DIR"] = str(root)
    os.environ["WITDEM_DB_PATH"] = str(root / "live.duckdb")
    live_db._connection = None
    try:
        yield
    finally:
        if live_db._connection is not None:
            live_db._connection.close()
        live_db._connection = None
        os.chdir(previous_cwd)
        if previous_db_path is None:
            os.environ.pop("WITDEM_DB_PATH", None)
        else:
            os.environ["WITDEM_DB_PATH"] = previous_db_path
        if previous_data_dir is None:
            os.environ.pop("WITDEM_DATA_DIR", None)
        else:
            os.environ["WITDEM_DATA_DIR"] = previous_data_dir


@pytest.fixture(scope="module")
def witdem_otlp_client() -> Iterator[TestClient]:
    """A real, in-process mount of Witdem's actual OTLP/HTTP receiver."""

    otlp_app = fastapi.FastAPI()
    otlp_app.include_router(otlp_router)
    with TestClient(otlp_app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def witdem_sdk_client() -> Iterator[TestClient]:
    """A real, in-process mount of Witdem's actual SDK ingest endpoint."""

    sdk_app = fastapi.FastAPI()
    sdk_app.include_router(sdk_router)
    with TestClient(sdk_app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _route_witdem_sdk_through_the_real_local_receiver(
    witdem_sdk_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make witdem_sdk's fire-and-forget background sends land on
    ``witdem_sdk_client`` (the real, in-process sdk_ingest router) instead of
    a real network socket, and complete synchronously so assertions never
    race a background thread -- the same two substitutions
    ../../../witdem-sdk/tests/conftest.py's own fixtures make (a synchronous stand-in
    executor; a patched send), reused here because they are exactly what
    this test needs too.
    """

    import witdem_sdk._transport as transport

    def _send_via_real_router(payload: dict[str, Any]) -> None:
        response = witdem_sdk_client.post("/sdk/v1/records", json=payload)
        # Unlike production's silent fire-and-forget drop, fail loudly here:
        # if agent_demo's enrichment.py ever drifts from witdem_sdk's real
        # public signatures again, this test must catch it, not swallow it.
        assert response.status_code == 200, (response.status_code, response.text)

    class _ImmediateExecutor:
        def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
            fn(*args, **kwargs)

    monkeypatch.setattr(transport, "_send", _send_via_real_router)
    monkeypatch.setattr(transport, "_executor", _ImmediateExecutor())


def _run_and_capture_spans(
    scenario: str, mode: str, span_exporter: Any
) -> tuple[str, list[ReadableSpan]]:
    span_exporter.clear()
    response = agent_demo_client.post("/run", json={"scenario": scenario, "mode": mode})
    assert response.status_code == 200, (mode, scenario, response.text)
    execution_id = response.json()["execution_id"]
    spans = list(span_exporter.get_finished_spans())
    assert spans, f"{scenario}/{mode}: expected at least one captured span"
    span_exporter.clear()
    return execution_id, spans


def _ingest_via_real_otlp_receiver(witdem_otlp_client: TestClient, spans: list[ReadableSpan]) -> None:
    """Encode real ``ReadableSpan``s with the SAME encoder a real
    ``OTLPSpanExporter`` uses, then POST the bytes through Witdem's real
    protobuf decode endpoint -- the actual wire format, not a shortcut."""

    export_request = encode_spans(spans)
    response = witdem_otlp_client.post(
        "/v1/traces", content=export_request.SerializeToString(), headers={"content-type": _OTLP_MEDIA_TYPE}
    )
    assert response.status_code == 200, response.text


def _load_operations(execution_id: str) -> list[Operation]:
    connection = live_db.get_connection()
    rows = connection.execute(
        "SELECT operation_id, execution_id, trace_id, span_id, parent_span_id, kind, name, status, "
        "started_at, ended_at, attempt, attributes FROM operations WHERE execution_id = ?",
        [execution_id],
    ).fetchall()
    columns = (
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
    operations = []
    for row in rows:
        values = dict(zip(columns, row, strict=True))
        import json

        values["attributes"] = json.loads(values["attributes"]) if values["attributes"] else {}
        operations.append(Operation(**values))
    assert operations, f"expected at least one normalized operation for execution {execution_id}"
    return operations


def _ordered(operations: list[Operation]) -> list[Operation]:
    return sorted(operations, key=lambda operation: operation.started_at or datetime.min.replace(tzinfo=UTC))


def _parent_key_shape(operations: list[Operation]) -> list[tuple[str, str | None]]:
    """For each operation (in canonical order), its own canonical key paired
    with its PARENT's canonical key (or None for a root) -- instance-id-free,
    so this is directly comparable across two separate runs with entirely
    different random span_ids."""

    by_span_id = {operation.span_id: operation for operation in operations if operation.span_id}
    shape = []
    for operation in operations:
        parent = by_span_id.get(operation.parent_span_id) if operation.parent_span_id else None
        shape.append((canonical_operation_key(operation), canonical_operation_key(parent) if parent else None))
    return shape


def _loop_signatures(execution_id: str, operations: list[Operation]) -> list[tuple[str, int, tuple[str, ...]]]:
    """Repeated-pattern loop structure, via the existing
    ``analytics.runtime.derive_repeated_patterns`` (which itself calls
    ``analytics.identity.canonical_loop_signature`` -- reused, not
    reimplemented). ``operation_ids``/``first_occurrence``/``last_occurrence``
    are deliberately excluded from the comparison below: they are
    positional/instance-specific and not expected to match between two
    independently-run executions."""

    graph = NormalizedExecutionGraph(execution=Execution(execution_id=execution_id), operations=operations, links=[])
    patterns = derive_repeated_patterns(graph)
    return sorted(
        (pattern["loop_signature"], pattern["iterations"], tuple(pattern["pattern_keys"])) for pattern in patterns
    )


@pytest.mark.enriched
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_two_mode_canonical_graph_equivalence(
    scenario: str,
    span_exporter: Any,
    witdem_otlp_client: TestClient,
) -> None:
    telemetry_execution_id, telemetry_spans = _run_and_capture_spans(scenario, "telemetry_only", span_exporter)
    _ingest_via_real_otlp_receiver(witdem_otlp_client, telemetry_spans)

    enriched_execution_id, enriched_spans = _run_and_capture_spans(scenario, "sdk_enriched", span_exporter)
    _ingest_via_real_otlp_receiver(witdem_otlp_client, enriched_spans)

    telemetry_ops = _ordered(_load_operations(telemetry_execution_id))
    enriched_ops = _ordered(_load_operations(enriched_execution_id))

    # 1. Operation kinds, in order.
    assert [operation.kind for operation in telemetry_ops] == [operation.kind for operation in enriched_ops], (
        f"{scenario}: operation kind sequence differs between modes"
    )

    # 2. Canonical identities, in order (also the input canonical_path_signature is built from).
    assert [canonical_operation_key(operation) for operation in telemetry_ops] == [
        canonical_operation_key(operation) for operation in enriched_ops
    ], f"{scenario}: canonical operation keys differ between modes"

    # 3. Path signature -- the existing analytics.identity function, used directly.
    assert canonical_path_signature(telemetry_ops) == canonical_path_signature(enriched_ops), (
        f"{scenario}: canonical_path_signature differs between modes"
    )

    # 4. Parent-child structure, instance-id-free.
    assert _parent_key_shape(telemetry_ops) == _parent_key_shape(enriched_ops), (
        f"{scenario}: parent-child structure differs between modes"
    )

    # 5. Loop / repeated-pattern structure, via the existing derive_repeated_patterns
    #    (canonical_loop_signature under the hood).
    assert _loop_signatures(telemetry_execution_id, telemetry_ops) == _loop_signatures(
        enriched_execution_id, enriched_ops
    ), f"{scenario}: loop structure differs between modes"
