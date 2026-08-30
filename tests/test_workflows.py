from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from witdem.analytics.core import Execution, Operation
from witdem.dashboard.app import create_dashboard_app
from witdem.dashboard.service import _workflow_projection_analytics, materialize_workflow_projections
from witdem.ingest import live_db
from witdem.workflows import (
    WorkflowDefinition,
    compile_registry,
    definition_from_record,
    load_registry,
    project_execution,
)


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "version": 1,
            "id": "answer-flow",
            "name": "Answer flow",
            "match": {"runtime_names": ["langgraph/answer"]},
            "stages": [
                {
                    "id": "prepare",
                    "name": "Prepare",
                    "nodes": [{"id": "classify", "name": "Classify", "match": {"names": ["classify"]}}],
                },
                {
                    "id": "answer",
                    "name": "Answer",
                    "depends_on": ["prepare"],
                    "nodes": [
                        {
                            "id": "retrieve",
                            "name": "Retrieve",
                            "operation": {"type": "retrieval", "expects": ["queries", "documents.output"]},
                            "match": {"names": ["retrieve"]},
                            "depends_on": [{"node": "classify", "type": "branch", "route": "grounded"}],
                        },
                        {
                            "id": "respond",
                            "name": "Respond",
                            "match": {"names": ["respond"]},
                            "depends_on": ["retrieve"],
                            "retry": {"max_attempts": 2},
                        },
                        {
                            "id": "fallback",
                            "name": "Fallback",
                            "match": {"names": ["fallback"]},
                            "depends_on": [{"node": "classify", "type": "fallback", "route": "unsupported"}],
                        },
                    ],
                },
            ],
            "outcomes": [{"id": "answered", "name": "Answered", "from": ["respond", "fallback"]}],
            "evaluation_suites": {"answer_quality": {"workflow": "answer-flow", "evaluations": ["groundedness"]}},
        }
    )


def test_compile_registry_writes_hashed_manifest_and_check_is_read_only(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    config = tmp_path / "witdem.yml"
    definition = _definition()
    config.write_text(
        "version: 1\nworkflows:\n  - id: answer-flow\n    definition: workflows/answer.yml\n",
        encoding="utf-8",
    )
    (workflow_dir / "answer.yml").write_text(
        yaml.safe_dump(definition.model_dump(mode="json", by_alias=True), sort_keys=False),
        encoding="utf-8",
    )

    checked = compile_registry(config, check=True, root=tmp_path / "data")
    assert checked["status"] == "stale"
    assert not Path(checked["workflows"][0]["path"]).exists()

    compiled = compile_registry(config, root=tmp_path / "data")
    manifest_path = Path(compiled["workflows"][0]["path"])
    assert manifest_path.name == f"{definition.template_hash}.json"
    assert manifest_path.is_file()
    assert compile_registry(config, check=True, root=tmp_path / "data")["status"] == "ok"

    manifest_path.write_text("{broken", encoding="utf-8")
    assert compile_registry(config, check=True, root=tmp_path / "data")["status"] == "stale"
    assert compile_registry(config, root=tmp_path / "data")["status"] == "ok"
    assert compile_registry(config, check=True, root=tmp_path / "data")["status"] == "ok"


def test_project_execution_keeps_template_stable_and_embeds_model_calls() -> None:
    definition = _definition()
    graph = {
        "nodes": [
            {"id": "c", "name": "classify", "kind": "graph_node", "status": "ok", "attributes": {"route": "grounded"}},
            {"id": "r1", "name": "retrieve", "kind": "tool", "status": "error", "attempt": 1, "duration_seconds": 0.2},
            {"id": "r2", "name": "retrieve", "kind": "tool", "status": "ok", "attempt": 2, "duration_seconds": 0.3},
            {
                "id": "a",
                "name": "respond",
                "kind": "graph_node",
                "status": "ok",
                "duration_seconds": 0.4,
                "attributes": {"technical.span_id": "a-span"},
            },
            {
                "id": "m",
                "name": "chat",
                "kind": "model",
                "status": "ok",
                "parent_operation_id": "a-span",
                "provider": "openai",
                "model": "gpt-5",
                "total_tokens": 42,
                "known_cost": 0.01,
            },
            {"id": "x", "name": "framework.checkpoint", "kind": "operation", "status": "ok"},
        ],
        "edges": [
            {"source": "c", "target": "r1", "relation": "child"},
            {"source": "r1", "target": "r2", "relation": "retry"},
            {"source": "r2", "target": "a", "relation": "next"},
        ],
    }

    replay = project_execution(definition, execution={"execution_id": "run-1"}, graph=graph)

    assert replay["workflow"]["template_hash"] == definition.template_hash
    assert replay["workflow"]["stages"][1]["nodes"] == ["retrieve", "respond", "fallback"]
    assert ("classify", "retrieve") in {(edge["from"], edge["to"]) for edge in replay["transitions"]}
    projected = {node["id"]: node for node in replay["nodes"]}
    assert projected["fallback"]["state"] == "inactive"
    assert projected["retrieve"]["attempts"] == 2
    assert projected["retrieve"]["state"] == "recovered"
    assert projected["respond"]["models"] == ["gpt-5"]
    assert projected["respond"]["total_tokens"] == 42
    assert all(node["kind"] != "model" for node in replay["nodes"])
    assert replay["discrepancies"]["unexpected_operations"][0]["name"] == "framework.checkpoint"

    analytics = _workflow_projection_analytics([replay])
    model = analytics["models"][0]
    assert model["participant_id"] == "openai::gpt-5"
    assert model["dimension"] == "model"
    assert model["provider_id"] == "openai"
    assert model["model_id"] == "gpt-5"
    assert model["runs"] == 1
    assert model["completed"] == 0
    assert model["failed"] == 0
    assert model["recovered"] == 1
    assert model["measured_cost"] == 0.01
    assert model["total_tokens"] == 42.0
    assert model["cost_coverage"] == 1.0
    assert model["semantics"] == "cohort+direct-attribution"
    assert analytics["providers"][0]["label"] == "openai"
    assert next(row for row in analytics["stages"] if row["label"] == "Respond")["total_tokens"] == 42.0


def test_project_execution_matches_framework_native_component_identity() -> None:
    definition = _definition()
    replay = project_execution(
        definition,
        execution={"execution_id": "haystack-run"},
        graph={
            "nodes": [
                {
                    "id": "component-span",
                    "name": "Classify component",
                    "display_name": "Classify component",
                    "runtime_name": "haystack.component.run",
                    "operation_key": "component:classify",
                    "kind": "component",
                    "status": "ok",
                    "attributes": {"haystack.component.name": "classify"},
                }
            ],
            "edges": [],
        },
    )

    projected = {node["id"]: node for node in replay["nodes"]}
    assert projected["classify"]["state"] == "completed"
    assert replay["discrepancies"]["unexpected_operations"] == []


def test_projected_node_uses_deduplicated_active_wall_time_and_measurement_coverage() -> None:
    replay = project_execution(
        _definition(),
        execution={"execution_id": "overlap-run"},
        graph={
            "nodes": [
                {
                    "id": "response",
                    "name": "respond",
                    "kind": "graph_node",
                    "status": "ok",
                    "start": "2026-08-23T12:00:00+00:00",
                    "end": "2026-08-23T12:00:10+00:00",
                    "duration_seconds": 10.0,
                    "attributes": {"technical.span_id": "response-span"},
                },
                {
                    "id": "model",
                    "name": "call",
                    "kind": "model",
                    "status": "ok",
                    "parent_operation_id": "response-span",
                    "start": "2026-08-23T12:00:02+00:00",
                    "end": "2026-08-23T12:00:04+00:00",
                    "duration_seconds": 2.0,
                    "provider": "gateway-any",
                    "model": "model-any",
                    "known_cost": 0.0,
                },
            ],
            "edges": [],
        },
    )

    node = next(item for item in replay["nodes"] if item["id"] == "respond")
    assert node["duration_seconds"] == 10.0
    assert node["known_cost"] == 0.0
    assert node["cost_eligible_operations"] == 1
    assert node["cost_measured_operations"] == 1
    assert node["token_eligible_operations"] == 1
    assert node["token_measured_operations"] == 0


def test_projected_ocr_node_marks_unreported_tokens_not_applicable() -> None:
    replay = project_execution(
        _definition(),
        execution={"execution_id": "ocr-run"},
        graph={
            "nodes": [
                {
                    "id": "response",
                    "name": "respond",
                    "kind": "graph_node",
                    "status": "ok",
                    "attributes": {"technical.span_id": "response-span"},
                },
                {
                    "id": "model",
                    "name": "ocr",
                    "kind": "model",
                    "status": "ok",
                    "parent_operation_id": "response-span",
                    "provider": "gateway-any",
                    "model": "document-reader",
                    "known_cost": 0.004,
                    "attributes": {
                        "witdem.operation.type": "ocr",
                        "gen_ai.usage.ocr_pages": 1,
                    },
                },
            ],
            "edges": [],
        },
    )

    node = next(item for item in replay["nodes"] if item["id"] == "respond")
    assert node["cost_eligible_operations"] == 1
    assert node["cost_measured_operations"] == 1
    assert node["token_eligible_operations"] == 0
    assert node["token_measured_operations"] == 0
    assert node["total_tokens"] is None


def test_parallel_declared_nodes_keep_independent_active_wall_time() -> None:
    replay = project_execution(
        _definition(),
        execution={"execution_id": "parallel-run"},
        graph={
            "nodes": [
                {
                    "id": "retrieve",
                    "name": "retrieve",
                    "kind": "tool",
                    "status": "ok",
                    "start": "2026-08-23T12:00:00+00:00",
                    "end": "2026-08-23T12:00:05+00:00",
                },
                {
                    "id": "respond",
                    "name": "respond",
                    "kind": "graph_node",
                    "status": "ok",
                    "start": "2026-08-23T12:00:00+00:00",
                    "end": "2026-08-23T12:00:05+00:00",
                },
            ],
            "edges": [],
        },
    )

    nodes = {node["id"]: node for node in replay["nodes"]}
    assert nodes["retrieve"]["duration_seconds"] == 5.0
    assert nodes["respond"]["duration_seconds"] == 5.0


def test_declared_business_retry_is_recovered_without_a_failed_span() -> None:
    definition = _definition()
    graph = {
        "nodes": [
            {"id": "r", "name": "retrieve", "kind": "tool", "status": "ok"},
            {"id": "a1", "name": "respond", "kind": "graph_node", "status": "ok"},
            {"id": "a2", "name": "respond", "kind": "graph_node", "status": "ok"},
        ],
        "edges": [],
    }
    replay = project_execution(definition, execution={"execution_id": "retry-run"}, graph=graph)
    projected = {node["id"]: node for node in replay["nodes"]}

    assert projected["respond"]["attempts"] == 2
    assert projected["respond"]["state"] == "recovered"


def test_project_config_registers_separate_yaml_workflows(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (tmp_path / "witdem.yml").write_text(
        "version: 1\nservice:\n  name: demo\nworkflows:\n  - id: answer-flow\n    definition: workflows/answer.yml\n",
        encoding="utf-8",
    )
    (workflow_dir / "answer.yml").write_text(
        yaml.safe_dump(_definition().model_dump(mode="json", by_alias=True), sort_keys=False),
        encoding="utf-8",
    )

    registry = load_registry(tmp_path / "witdem.yml")

    assert registry.get("answer-flow") is not None
    assert registry.match({"runtime_id": "langgraph/answer"}).id == "answer-flow"


def test_product_factory_yaml_is_the_shared_cross_runtime_workflow() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = load_registry(root / "examples/product-factory/.witdem/witdem.yaml")
    workflow = registry.get("company-qualification")

    assert workflow is not None
    assert registry.match({"runtime_id": "anthropic_messages"}) is workflow
    assert registry.match({"runtime_id": "openai_agents"}) is workflow
    assert registry.match({"runtime_id": "haystack"}) is workflow
    assert registry.match({"runtime_id": "langchain"}) is workflow
    assert registry.match({"runtime_id": "langgraph"}) is workflow
    assert {node.kind for node in workflow.nodes} >= {
        "Research",
        "Validation",
        "Decision",
        "Goal assessment",
    }
    replay = project_execution(
        workflow,
        execution={"execution_id": "haystack-run"},
        graph={
            "nodes": [
                {"id": "native", "name": "Research", "kind": "component", "status": "ok"},
                {
                    "id": "semantic",
                    "name": "product_factory.research",
                    "kind": "operation",
                    "status": "ok",
                },
            ],
            "edges": [],
        },
    )

    assert replay["nodes"][0]["attempts"] == 1
    assert replay["discrepancies"]["unexpected_operations"] == []
    runtime_states = []
    for runtime_id in ("anthropic_messages", "openai_agents", "haystack", "langchain", "langgraph"):
        runtime_replay = project_execution(
            registry.match({"runtime_id": runtime_id}),
            execution={"execution_id": f"{runtime_id}-run", "runtime_id": runtime_id},
            graph={
                "nodes": [
                    {
                        "id": f"{runtime_id}-research",
                        "name": "product_factory.research",
                        "kind": "operation",
                        "status": "ok",
                    }
                ],
                "edges": [],
            },
        )
        runtime_states.append([(node["id"], node["state"]) for node in runtime_replay["nodes"]])
    assert all(states == runtime_states[0] for states in runtime_states[1:])


def test_dependency_cycles_are_rejected() -> None:
    raw = _definition().model_dump(mode="json", by_alias=True)
    raw["stages"][0]["nodes"][0]["depends_on"] = ["respond"]

    try:
        WorkflowDefinition.model_validate(raw)
    except ValueError as exc:
        assert "must form a DAG" in str(exc)
    else:
        raise AssertionError("cyclic depends_on relationships must fail validation")


def test_invalid_historical_definition_record_does_not_break_replay() -> None:
    definition = definition_from_record(
        [
            {
                "name": "workflow.definition",
                "attributes": {"definition": {"id": "obsolete-flat-schema"}},
            }
        ]
    )

    assert definition is None


def test_historical_and_new_executions_share_one_persisted_template(tmp_path: Path, monkeypatch) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    config_path = tmp_path / "witdem.yml"
    config_path.write_text(
        "version: 1\nservice:\n  name: demo\nworkflows:\n  - id: answer-flow\n    definition: workflows/answer.yml\n",
        encoding="utf-8",
    )
    (workflow_dir / "answer.yml").write_text(
        yaml.safe_dump(_definition().model_dump(mode="json", by_alias=True), sort_keys=False),
        encoding="utf-8",
    )
    database = tmp_path / "analytics.duckdb"
    monkeypatch.setenv("WITDEM_CONFIG", str(config_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)

    # The first execution predates SDK workflow identity; runtime matching is
    # the compatibility path. The second explicitly carries workflow identity.
    for execution_id, explicit, fallback in (
        ("historical-run", False, False),
        ("new-run", True, True),
    ):
        attributes = {"witdem.workflow.id": "answer-flow"} if explicit else {}
        operations = [
            Operation(
                operation_id=f"{execution_id}-classify",
                execution_id=execution_id,
                span_id=f"{execution_id}-c",
                kind="graph_node",
                name="classify",
                status="ok",
            ),
            Operation(
                operation_id=f"{execution_id}-path",
                execution_id=execution_id,
                span_id=f"{execution_id}-p",
                kind="graph_node",
                name="fallback" if fallback else "retrieve",
                status="ok",
            ),
        ]
        live_db.upsert_graph(
            Execution(
                execution_id=execution_id,
                runtime_id="langgraph/answer",
                status="completed",
                attributes=attributes,
            ),
            operations,
            [],
        )

    materialize_workflow_projections(database, ["historical-run", "new-run"])

    client = TestClient(create_dashboard_app(database, static_dir=tmp_path / "missing"))
    catalog = client.get("/api/v1/workflow-definitions").json()
    workflow = client.get("/api/v1/workflow-definitions/answer-flow").json()
    old = client.get("/api/v1/runs/historical-run").json()
    new = client.get("/api/v1/workflow-definitions/answer-flow/executions/new-run").json()
    operations = client.get("/api/v1/workflow-definitions/answer-flow/operations").json()
    evaluations = client.get("/api/v1/workflow-definitions/answer-flow/evaluations").json()
    campaigns = client.get("/api/v1/workflow-definitions/answer-flow/evaluation-campaigns").json()

    assert catalog["items"][0]["execution_count"] == 2
    assert catalog["items"][0]["version"] == 1
    assert all(run["workflow_total_steps"] == 4 for run in workflow["executions"])
    assert all(run["workflow_active_steps"] == 2 for run in workflow["executions"])
    assert all(run["workflow_attempts"] == 2 for run in workflow["executions"])
    assert all(run["workflow_retry_attempts"] == 0 for run in workflow["executions"])
    assert all(run["workflow_models"] == [] for run in workflow["executions"])
    assert old["canonical_url"] == "/workflows/answer-flow/executions/historical-run"
    assert old["workflow_replay"]["workflow"]["template_hash"] == new["workflow_replay"]["workflow"]["template_hash"]
    assert next(node for node in old["workflow_replay"]["nodes"] if node["id"] == "fallback")["state"] == "inactive"
    assert next(node for node in new["workflow_replay"]["nodes"] if node["id"] == "fallback")["state"] == "completed"
    assert operations["summary"]["total_operations"] == 4
    assert operations["measurement_coverage"]["missing"] == 2
    assert evaluations["summary"]["reported"] == 0
    assert campaigns["campaigns"] == []


def test_projection_materialization_preserves_operations_without_a_declared_workflow(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "analytics.duckdb"
    monkeypatch.delenv("WITDEM_CONFIG", raising=False)
    monkeypatch.setenv("WITDEM_DB_PATH", str(database))
    live_db.initialize_analytics_store(database)
    execution_id = "unmatched-operation-run"
    operation_id = "embedding-call"
    operation = Operation(
        operation_id=operation_id,
        execution_id=execution_id,
        span_id="embedding-span",
        kind="model",
        name="embed",
        status="ok",
        attributes={"witdem.operation.type": "embedding", "gen_ai.provider.name": "provider-a"},
    )
    live_db.publish_transformed_bundle(
        Execution(execution_id=execution_id, runtime_id="custom/runtime", status="completed"),
        [operation],
        [],
        [],
        operation_classifications=[
            {
                "operation_id": operation_id,
                "taxonomy_version": "1",
                "family": "inference",
                "operation_type": "embedding",
                "subtype": "embed",
                "interface": "model_api",
                "role": "application",
                "input_modalities": ["text"],
                "output_modalities": ["vector"],
                "provider_id": "provider-a",
                "status": "ok",
            }
        ],
        operation_measurements=[
            {
                "operation_id": operation_id,
                "registry_version": "1",
                "measurement_key": "vectors.output",
                "value": 2,
                "unit": "vector",
                "aggregation": "sum",
                "scope": "operation",
                "measurement_status": "measured",
                "provenance": "provider_reported",
                "applicability_source": "core_registry",
            }
        ],
    )

    materialize_workflow_projections(database, [execution_id])

    client = TestClient(create_dashboard_app(database, static_dir=tmp_path / "missing"))
    detail = client.get(f"/api/v1/runs/{execution_id}").json()
    assert detail["operation_summary"]["total_operations"] == 1
    assert detail["measurements"][0]["measurement_key"] == "vectors.output"
