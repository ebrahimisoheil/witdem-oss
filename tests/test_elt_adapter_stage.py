from __future__ import annotations

import json

from witdem.elt.adapter_stage import transform_bundle


def test_duckle_adapter_stage_preserves_generic_unknown_otel_data() -> None:
    span = {
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "parent_span_id": None,
        "name": "custom.workflow",
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {"witdem.execution_id": "run-1", "custom.value": "preserved"},
        "events": [],
        "resource": {"service.name": "custom-service"},
        "instrumentation_scope": {"name": "custom.instrumentation", "version": "1"},
    }

    result = transform_bundle(
        {
            "execution_id": "run-1",
            "source_ingest_ids_json": json.dumps(["ingest-1", "ingest-2"]),
            "spans_json": json.dumps([span]),
        }
    )
    execution = json.loads(result["execution_json"])
    operations = json.loads(result["operations_json"])

    assert result["adapter_name"] == "otel"
    assert operations[0]["attributes"]["custom.value"] == "preserved"
    assert operations[0]["attributes"]["witdem.transform.engine"] == "duckle"
    assert json.loads(execution["attributes"]["witdem.source_ingest_ids"]) == ["ingest-1", "ingest-2"]


def test_duckle_adapter_stage_builds_sdk_only_placeholder() -> None:
    record = {
        "version": "1.0",
        "kind": "outcome",
        "event_id": "event-1",
        "execution_id": "run-sdk",
        "name": "execution.completed",
        "value": "success",
        "attributes": {"runtime_id": "langgraph", "case_id": "clear-qualification"},
    }

    result = transform_bundle({"execution_id": "run-sdk", "sdk_records_json": json.dumps([record])})
    execution = json.loads(result["execution_json"])

    assert execution["status"] == "completed"
    assert execution["runtime_id"] == "langgraph"
    assert json.loads(result["sdk_records_json"])[0]["event_id"] == "event-1"


def test_duckle_adapter_stage_prefers_langgraph_for_sdk_callback_spans() -> None:
    span = {
        "trace_id": "3" * 32,
        "span_id": "4" * 16,
        "parent_span_id": None,
        "name": "langgraph.graph",
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {
            "witdem.execution_id": "run-langgraph",
            "witdem.runtime.kind": "workflow",
            "product_factory.runtime": "langgraph",
            "langchain.run_id": "run-id",
        },
        "events": [],
        "resource": {"service.name": "product-factory"},
        "instrumentation_scope": {"name": "witdem_sdk.integrations.langchain", "version": "1"},
    }

    result = transform_bundle({"execution_id": "run-langgraph", "spans_json": json.dumps([span])})

    assert result["adapter_name"] == "langgraph"


def test_provider_adapter_promotes_generic_genai_component_to_model() -> None:
    span = {
        "trace_id": "5" * 32,
        "span_id": "6" * 16,
        "parent_span_id": None,
        "name": "Component",
        "kind": "SpanKind.INTERNAL",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "status": {"status_code": "StatusCode.OK"},
        "attributes": {
            "witdem.execution_id": "run-model",
            "product_factory.runtime": "langgraph",
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai",
            "gen_ai.response.model": "gpt-5.4-mini",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 5,
        },
        "events": [],
        "resource": {"service.name": "product-factory"},
        "instrumentation_scope": {"name": "product_factory.live_models", "version": "1"},
    }

    result = transform_bundle({"execution_id": "run-model", "spans_json": json.dumps([span])})
    operation = json.loads(result["operations_json"])[0]

    assert result["adapter_name"] == "langgraph"
    assert operation["kind"] == "model"
    assert operation["attributes"]["witdem.provider_adapter.name"] == "openai"
