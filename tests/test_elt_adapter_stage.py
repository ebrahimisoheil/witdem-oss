from __future__ import annotations

import json

import pytest

from witdem.analytics.core import Operation
from witdem.analytics.cost import estimate_chat_cost
from witdem.analytics.identity import display_operation
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


def test_haystack_usage_summary_enriches_native_model_calls_without_adding_a_call() -> None:
    def span(span_id: str, name: str, attributes: dict, start: int) -> dict:
        return {
            "trace_id": "7" * 32,
            "span_id": span_id,
            "parent_span_id": None,
            "name": name,
            "kind": "SpanKind.INTERNAL",
            "start_time_unix_nano": start,
            "end_time_unix_nano": start + 100_000_000,
            "status": {"status_code": "StatusCode.OK"},
            "attributes": {"witdem.execution_id": "run-haystack", **attributes},
            "events": [],
            "resource": {"service.name": "haystack-demo"},
            "instrumentation_scope": {"name": "witdem.haystack", "version": "1"},
        }

    spans = [
        span("1" * 16, "haystack.agent.step.llm", {"haystack.agent.step": 1}, 1_000_000_000),
        span("2" * 16, "haystack.agent.step.llm", {"haystack.agent.step": 2}, 2_000_000_000),
        span(
            "3" * 16,
            "witdem.haystack.usage_summary",
            {
                "witdem.haystack.usage_summary": True,
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-5.4",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
                "gen_ai.usage.total_tokens": 120,
                "pf.provider_provenance": "observed_invocation_configuration",
                "pf.model_provenance": "observed_invocation_configuration",
                "pf.usage_provenance": "observed_provider_response",
            },
            3_000_000_000,
        ),
    ]

    result = transform_bundle(
        {"execution_id": "run-haystack", "spans_json": json.dumps(spans)}
    )
    operations = json.loads(result["operations_json"])
    models = [operation for operation in operations if operation["kind"] == "model"]

    assert result["adapter_name"] == "haystack"
    assert len(models) == 2
    assert {operation["attributes"]["provider"] for operation in models} == {"openai"}
    assert {operation["attributes"]["model"] for operation in models} == {"gpt-5.4"}
    assert sum(operation["attributes"].get("total_tokens", 0) for operation in models) == 120
    measured = [
        operation["attributes"]["cost_usd"]
        for operation in models
        if "cost_usd" in operation["attributes"]
    ]
    expected_cost = estimate_chat_cost(
        "openai", "gpt-5.4", {"input_tokens": 100, "output_tokens": 20}
    )
    assert measured == pytest.approx([expected_cost])
    assert not any(operation["name"] == "witdem.haystack.usage_summary" for operation in operations)


def test_haystack_agent_steps_are_named_from_observed_children() -> None:
    def span(
        span_id: str,
        name: str,
        *,
        parent_span_id: str | None = None,
        attributes: dict | None = None,
        start: int,
    ) -> dict:
        return {
            "trace_id": "8" * 32,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "name": name,
            "kind": "SpanKind.INTERNAL",
            "start_time_unix_nano": start,
            "end_time_unix_nano": start + 100_000_000,
            "status": {"status_code": "StatusCode.OK"},
            "attributes": {"witdem.execution_id": "run-steps", **(attributes or {})},
            "events": [],
            "resource": {"service.name": "haystack-demo"},
            "instrumentation_scope": {"name": "witdem.haystack", "version": "1"},
        }

    first_step = "1" * 16
    final_step = "4" * 16
    spans = [
        span(first_step, "haystack.agent.step", attributes={"haystack.agent.step": 0}, start=1_000_000_000),
        span("2" * 16, "haystack.agent.step.llm", parent_span_id=first_step, start=1_100_000_000),
        span(
            "3" * 16,
            "haystack.agent.step.tool",
            parent_span_id=first_step,
            attributes={"haystack.tool.name": "list_metadata_fields"},
            start=1_200_000_000,
        ),
        span(final_step, "haystack.agent.step", attributes={"haystack.agent.step": 1}, start=2_000_000_000),
        span("5" * 16, "haystack.agent.step.llm", parent_span_id=final_step, start=2_100_000_000),
    ]

    result = transform_bundle({"execution_id": "run-steps", "spans_json": json.dumps(spans)})
    operations = [Operation.model_validate(value) for value in json.loads(result["operations_json"])]
    steps = [operation for operation in operations if operation.kind == "agent_step"]

    assert [operation.name for operation in steps] == ["list_metadata_fields", "final_answer"]
    assert [display_operation(operation) for operation in steps] == [
        "Step 1 · List metadata fields",
        "Step 2 · Final answer",
    ]
    assert steps[0].attributes["witdem.agent.step.name_provenance"] == "observed_child_tool"
