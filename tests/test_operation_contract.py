from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from witdem.analytics.core import Execution, Operation
from witdem.analytics.operations import operation_identity, operation_measurements
from witdem.analytics.repository.analytics_repository import _participant_identity, _token_eligible
from witdem.analytics.serving import build_serving_rows
from witdem.cli import build_parser
from witdem.dashboard.service import _operation_summary
from witdem.elt.adapter_stage import transform_bundle
from witdem.integrations.normalizers.otel import sanitize_otel_span


def _operation(operation_type: str, attributes: dict[str, object] | None = None) -> Operation:
    started = datetime(2026, 8, 30, tzinfo=timezone.utc)
    return Operation(
        operation_id=f"op-{operation_type}",
        execution_id="run-neutral",
        kind="operation",
        name=operation_type,
        status="ok",
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        attributes={"witdem.operation.type": operation_type, **(attributes or {})},
    )


def test_ocr_pages_are_measured_while_tokens_are_not_applicable() -> None:
    operation = _operation(
        "ocr",
        {
            "gen_ai.provider.name": "gateway-a",
            "gen_ai.response.model": "document-reader",
            "gen_ai.usage.ocr_pages": 3,
            "gen_ai.cost.usd": 0.004,
        },
    )

    facts = {fact["key"]: fact for fact in operation_measurements(operation)}

    assert facts["pages.processed"]["value"] == 3
    assert facts["pages.processed"]["status"] == "measured"
    assert facts["cost.usd"]["value"] == 0.004
    assert facts["tokens.input"]["status"] == "not_applicable"
    assert facts["tokens.output"]["status"] == "not_applicable"
    assert facts["tokens.total"]["status"] == "not_applicable"
    assert _token_eligible(operation) is False


def test_extension_operation_survives_without_provider_inference() -> None:
    operation = _operation("x.example.future_transform", {"gen_ai.response.model": "gpt-shaped-name"})

    identity = operation_identity(operation)

    assert identity["type"] == "x.example.future_transform"
    assert identity["family"] == "custom"
    assert operation.attributes.get("gen_ai.provider.name") is None


def test_untyped_model_operation_is_unknown_not_generation() -> None:
    started = datetime(2026, 8, 30, tzinfo=timezone.utc)
    operation = Operation(
        operation_id="op-unknown-model",
        execution_id="run-neutral",
        kind="model",
        name="model.call",
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        attributes={"gen_ai.provider.name": "provider-a", "gen_ai.request.model": "opaque-model"},
    )

    assert operation_identity(operation)["type"] == "unknown"


def test_classification_precedence_and_orthogonal_dimensions() -> None:
    operation = _operation(
        "contract_conflict_analysis",
        {
            "witdem.operation.family": "custom",
            "gen_ai.operation.name": "embeddings",
            "call_type": "rerank",
            "witdem.operation.interface": "mcp",
            "witdem.operation.role": "tool",
        },
    )

    identity = operation_identity(operation)
    assert identity["type"] == "contract_conflict_analysis"
    assert identity["family"] == "custom"
    assert identity["interface"] == "mcp"
    assert identity["role"] == "tool"


def test_root_execution_container_is_not_a_work_operation() -> None:
    started = datetime(2026, 8, 30, tzinfo=timezone.utc)
    operation = Operation(
        operation_id="root-contract-review",
        execution_id="run-contract-review",
        kind="operation",
        name="contract-review",
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        attributes={
            "witdem.execution.name": "contract-review",
            "witdem.runtime.kind": "workflow",
        },
    )

    identity = operation_identity(operation)

    assert identity["entity_kind"] == "execution"
    assert identity["family"] == "orchestration"
    assert identity["type"] == "workflow"
    assert identity["plane"] is None
    assert identity["model_applicability"] == "not_applicable"


def test_control_and_work_planes_do_not_depend_on_framework_or_interface() -> None:
    workflow = _operation("workflow")
    retrieval = _operation("retrieval", {"witdem.operation.interface": "mcp"})

    workflow_identity = operation_identity(workflow)
    retrieval_identity = operation_identity(retrieval)

    assert workflow_identity["plane"] == "control"
    assert workflow_identity["role"] == "control"
    assert retrieval_identity["plane"] == "work"
    assert retrieval_identity["interface"] == "mcp"
    assert retrieval_identity["model_applicability"] == "not_applicable"


def test_mcp_protocol_methods_preserve_semantics_and_interface() -> None:
    started = datetime(2026, 8, 30, tzinfo=timezone.utc)

    def identity(method: str) -> dict[str, object]:
        return operation_identity(
            Operation(
                operation_id=f"mcp-{method}",
                execution_id="run-mcp",
                kind="operation",
                name=f"mcp send {method}",
                started_at=started,
                ended_at=started + timedelta(seconds=1),
                attributes={
                    "mcp.method.name": method,
                    "otel.instrumentation_scope": {"name": "mcp-python-sdk"},
                },
            )
        )

    connection = identity("initialize")
    assert connection["family"] == "mcp"
    assert connection["type"] == "mcp_connection"
    assert connection["subtype"] == "initialize"
    assert connection["interface"] == "mcp"
    assert connection["role"] == "control"
    assert connection["plane"] == "control"
    assert identity("tools/list")["type"] == "capability_discovery"
    assert identity("tools/list")["plane"] == "control"
    assert identity("resources/read")["type"] == "resource_read"
    assert identity("prompts/get")["type"] == "prompt_retrieval"
    assert identity("tools/call")["family"] == "tools"
    assert identity("tools/call")["type"] == "tool"
    assert identity("tools/call")["interface"] == "mcp"
    assert identity("tools/call")["role"] == "tool"


def test_operation_summary_excludes_containers_and_preserves_linked_child_activity() -> None:
    operations = [
        {
            "operation_id": "root",
            "entity_kind": "execution",
            "family": "orchestration",
            "operation_type": "workflow",
            "status": "ok",
        },
        {
            "operation_id": "retrieval",
            "entity_kind": "operation",
            "family": "knowledge",
            "operation_type": "retrieval",
            "implementation_id": "lancedb",
            "model_applicability": "not_applicable",
            "status": "ok",
        },
        {
            "operation_id": "embedding",
            "parent_operation_id": "retrieval",
            "entity_kind": "operation",
            "family": "inference",
            "operation_type": "embedding",
            "provider_id": "voyage",
            "model_id": "voyage-4-large",
            "model_applicability": "applicable",
            "status": "ok",
        },
    ]

    summary = _operation_summary(operations, [])
    retrieval = next(item for item in summary["types"] if item["type"] == "retrieval")

    assert summary["total_operations"] == 2
    assert summary["execution_containers"] == 1
    assert all(item["type"] != "workflow" for item in summary["types"])
    assert retrieval["models"] == []
    assert retrieval["model_applicability"] == "not_applicable"
    assert retrieval["linked_children"] == [
        {
            "type": "embedding",
            "family": "inference",
            "operations": 1,
            "providers": ["voyage"],
            "models": ["voyage-4-large"],
            "implementations": [],
        }
    ]


def test_taxonomy_reprocess_cli_is_available() -> None:
    args = build_parser().parse_args(["taxonomy", "reprocess"])

    assert args.command == "taxonomy"
    assert args.taxonomy_command == "reprocess"


def test_adapter_and_bounded_function_metadata_classify_without_provider_rules() -> None:
    adapter = _operation("", {"witdem.operation.type": "", "call_type": "embedding"})
    function = _operation(
        "",
        {"witdem.operation.type": "", "code.function.name": "run_hybrid_search"},
    )

    assert operation_identity(adapter)["type"] == "embedding"
    assert operation_identity(function)["type"] == "hybrid_search"


def test_materialized_facts_preserve_nested_execution_and_implementation_identity() -> None:
    spans = [
        {
            "trace_id": "a" * 32,
            "span_id": "1" * 16,
            "name": "retrieve",
            "attributes": {
                "witdem.execution_id": "run-nested",
                "witdem.operation.family": "knowledge",
                "witdem.operation.type": "retrieval",
                "witdem.operation.interface": "mcp",
                "witdem.implementation.id": "lancedb",
                "witdem.execution.source": "custom-pipeline",
            },
        },
        {
            "trace_id": "a" * 32,
            "span_id": "2" * 16,
            "parent_span_id": "1" * 16,
            "name": "embed query",
            "attributes": {
                "witdem.execution_id": "run-nested",
                "gen_ai.operation.name": "embeddings",
                "gen_ai.provider.name": "provider-a",
            },
        },
    ]

    result = transform_bundle({"execution_id": "run-nested", "spans_json": json.dumps(spans)})
    facts = json.loads(result["operation_classifications_json"])
    retrieval = next(item for item in facts if item["operation_type"] == "retrieval")
    embedding = next(item for item in facts if item["operation_type"] == "embedding")

    assert retrieval["interface"] == "mcp"
    assert retrieval["implementation_id"] == "lancedb"
    assert retrieval["execution_source"] == "custom-pipeline"
    assert embedding["parent_operation_id"] == retrieval["operation_id"]


def test_default_interfaces_describe_framework_and_local_work() -> None:
    assert operation_identity(_operation("component"))["interface"] == "framework"
    assert operation_identity(_operation("x.example.future_transform"))["interface"] == "local"


def test_observed_tool_span_implies_one_calculated_tool_call() -> None:
    facts = {fact["key"]: fact for fact in operation_measurements(_operation("tool"))}

    assert facts["tool.calls"]["value"] == 1
    assert facts["tool.calls"]["status"] == "measured"
    assert facts["tool.calls"]["provenance"] == "calculated"


def test_duckle_adapter_stage_emits_operation_and_measurement_facts() -> None:
    span = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": None,
        "name": "document OCR",
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {
            "witdem.execution_id": "run-duckle-operations",
            "witdem.operation.type": "ocr",
            "witdem.operation.interface": "model_api",
            "gen_ai.provider.name": "provider-a",
            "gen_ai.usage.ocr_pages": 2,
        },
        "events": [],
        "resource": {"service.name": "neutral-test"},
        "instrumentation_scope": {"name": "neutral-test", "version": "1"},
    }

    result = transform_bundle({"execution_id": "run-duckle-operations", "spans_json": json.dumps([span])})
    classifications = json.loads(result["operation_classifications_json"])
    measurements = json.loads(result["operation_measurements_json"])

    assert classifications[0]["operation_type"] == "ocr"
    assert classifications[0]["provider_id"] == "provider-a"
    assert any(item["measurement_key"] == "pages.processed" and item["value"] == 2 for item in measurements)


def test_duckle_adapter_does_not_double_count_child_usage_on_framework_wrappers() -> None:
    base = {
        "trace_id": "a" * 32,
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "events": [],
        "resource": {"service.name": "neutral-test"},
        "instrumentation_scope": {"name": "neutral-test", "version": "1"},
    }
    spans = [
        {
            **base,
            "span_id": "b" * 16,
            "parent_span_id": None,
            "name": "agent",
            "attributes": {
                "witdem.execution_id": "run-wrapper-usage",
                "openinference.span.kind": "AGENT",
                "gen_ai.usage.total_tokens": 12,
            },
        },
        {
            **base,
            "span_id": "c" * 16,
            "parent_span_id": "b" * 16,
            "name": "model",
            "attributes": {
                "witdem.execution_id": "run-wrapper-usage",
                "openinference.span.kind": "LLM",
                "gen_ai.provider.name": "provider-a",
                "gen_ai.request.model": "model-a",
                "gen_ai.usage.total_tokens": 12,
            },
        },
    ]

    result = transform_bundle({"execution_id": "run-wrapper-usage", "spans_json": json.dumps(spans)})
    classifications = json.loads(result["operation_classifications_json"])
    measurements = json.loads(result["operation_measurements_json"])
    type_by_operation = {item["operation_id"]: item["operation_type"] for item in classifications}
    measured_totals = [
        item
        for item in measurements
        if item["measurement_key"] == "tokens.total" and item["measurement_status"] == "measured"
    ]

    assert len(measured_totals) == 1
    assert type_by_operation[measured_totals[0]["operation_id"]] == "text_generation"


def test_execution_summary_keeps_model_identity_for_non_generation_model_apis() -> None:
    operation = _operation(
        "embedding",
        {
            "gen_ai.provider.name": "provider-a",
            "gen_ai.request.model": "embedder-a",
            "input_tokens": 12,
            "total_tokens": 12,
        },
    )

    rows = build_serving_rows(
        Execution(execution_id=operation.execution_id, runtime_id="custom", status="completed"),
        [operation],
        [],
        [],
        transformed_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        transform_version="test",
    )

    assert rows["execution_facts"][0]["models"] == "embedder-a"
    assert rows["execution_facts"][0]["model_calls"] == 1
    assert rows["execution_facts"][0]["total_tokens"] == 12

    provider = _participant_identity(operation, "provider")
    model = _participant_identity(operation, "model")
    assert provider is not None and provider[0] == "provider-a"
    assert model is not None and model[2:4] == ("provider-a", "embedder-a")
    assert _token_eligible(operation) is True


def test_execution_summary_counts_canonical_mcp_tool_calls() -> None:
    started = datetime(2026, 8, 30, tzinfo=timezone.utc)
    operation = Operation(
        operation_id="mcp-tool-call",
        execution_id="run-mcp",
        kind="operation",
        name="mcp send tools call",
        started_at=started,
        ended_at=started + timedelta(seconds=1),
        attributes={"mcp.method.name": "tools/call"},
    )

    rows = build_serving_rows(
        Execution(execution_id="run-mcp", runtime_id="mcp", status="completed"),
        [operation],
        [],
        [],
        transformed_at=started,
        transform_version="test",
    )

    assert rows["execution_facts"][0]["tool_calls"] == 1


def test_metadata_only_sanitization_keeps_counts_and_removes_payloads() -> None:
    span = {
        "trace_id": "a",
        "span_id": "b",
        "attributes": {
            "retrieval.documents": [{"content": "secret one"}, {"content": "secret two"}],
            "embedding.embeddings": [[0.1, 0.2]],
            "gen_ai.input.messages.0.content": "secret prompt",
            "provider": "explicit-provider",
        },
        "events": [{"name": "result", "attributes": {"output.value": "secret output", "items": 2}}],
    }

    safe = sanitize_otel_span(span)

    assert safe["attributes"]["provider"] == "explicit-provider"
    assert safe["attributes"]["witdem.observed.documents_output"] == 2
    assert safe["attributes"]["witdem.observed.vectors_output"] == 1
    assert "retrieval.documents" not in safe["attributes"]
    assert "gen_ai.input.messages.0.content" not in safe["attributes"]
    assert "output.value" not in safe["events"][0]["attributes"]
