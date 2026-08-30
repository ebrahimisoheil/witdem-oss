from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from witdem.analytics.core import Execution, Operation
from witdem.analytics.operations import operation_identity, operation_measurements
from witdem.analytics.repository.analytics_repository import _participant_identity, _token_eligible
from witdem.analytics.serving import build_serving_rows
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
