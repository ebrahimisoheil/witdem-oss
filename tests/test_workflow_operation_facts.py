from copy import deepcopy
from datetime import datetime, timedelta, timezone

from witdem.analytics.core import Operation
from witdem.analytics.evidence import measurement_coverage, operation_summary
from witdem.analytics.operation_facts import workflow_operation_facts
from witdem.dashboard.service import _operation_facts


def test_workflow_rebuild_does_not_reintroduce_wrapper_token_measurements():
    operations = [
        Operation(operation_id="wrapper", execution_id="run", span_id="parent", kind="agent", name="agent",
                  attributes={"witdem.operation.type": "agent", "gen_ai.usage.total_tokens": 12}),
        Operation(operation_id="model", execution_id="run", span_id="child", parent_span_id="parent",
                  kind="llm", name="model", attributes={"witdem.operation.type": "text_generation",
                                                        "gen_ai.usage.total_tokens": 12}),
    ]
    projection = {"execution": {"execution_id": "run"}, "workflow": {"id": "review", "template_hash": "test"},
                  "nodes": []}
    _, measurements = _operation_facts(projection, operations)
    totals = [item for item in measurements
              if item["measurement_key"] == "tokens.total" and item["measurement_status"] == "measured"]
    assert [(item["operation_id"], item["value"]) for item in totals] == [("model", 12)]


def test_projector_preserves_declarations_missing_zero_and_private_content_defaults():
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    operations = [Operation(
        operation_id="ocr", execution_id="run", kind="operation", name="read", span_id="ocr-span", status="ok",
        started_at=started, ended_at=started + timedelta(seconds=2), attempt=2,
        attributes={"witdem.operation.type": "ocr", "gen_ai.usage.ocr_pages": 0, "gen_ai.cost.usd": 0.,
                    "provider": "provider-a", "model": "ocr-model", "prompt": "PRIVATE_PROMPT",
                    "response": "PRIVATE_RESPONSE"},
    )]
    projection = {"execution": {"execution_id": "run"}, "workflow": {"id": "review", "template_hash": "test"},
                  "nodes": [{"id": "read", "operation": {"type": "ocr", "expects": ["custom.required"],
                                                         "optional": ["custom.optional"]},
                             "observations": [{"operation_id": "ocr"}]}]}
    before = deepcopy(projection), [operation.model_dump() for operation in operations]
    facts, measurements = workflow_operation_facts(projection, operations)
    assert _operation_facts is workflow_operation_facts
    assert facts[0]["node_id"] == "read" and facts[0]["duration_seconds"] == 2
    assert facts[0]["attributes"] == {"trace_id": None, "span_id": "ocr-span", "attempt": 2}
    assert facts[0]["provider_id"] == "provider-a" and facts[0]["model_id"] == "ocr-model"
    values = {item["measurement_key"]: item for item in measurements}
    assert values["pages.processed"]["value"] == values["cost.usd"]["value"] == 0
    assert values["pages.processed"]["measurement_status"] == "measured"
    assert values["custom.required"]["measurement_status"] == "missing"
    assert values["custom.required"]["applicability_source"] == "declared"
    assert values["custom.optional"]["measurement_status"] == "not_applicable"
    assert "PRIVATE" not in str((facts, measurements))
    assert before == (projection, [operation.model_dump() for operation in operations])
    assert operation_summary(facts, measurements)["types"][0]["measurements"]["cost.usd"] == 0
    assert measurement_coverage(measurements)["missing"] >= 1


def test_nonduplicated_wrapper_usage_and_real_parent_child_work_remain_visible():
    operations = [
        Operation(operation_id="wrapper", execution_id="run", span_id="parent", kind="agent", name="agent",
                  attributes={"witdem.operation.type": "agent", "gen_ai.usage.total_tokens": 12}),
        Operation(operation_id="tool", execution_id="run", span_id="child", parent_span_id="parent", kind="tool",
                  name="tool", attributes={"witdem.operation.type": "tool"}),
    ]
    projection = {"execution": {"execution_id": "run"}, "workflow": {"id": "review"}, "nodes": []}
    facts, measurements = workflow_operation_facts(projection, operations)
    assert facts[1]["parent_operation_id"] == "wrapper"
    assert any(item["operation_id"] == "wrapper" and item["measurement_key"] == "tokens.total"
               and item["value"] == 12 for item in measurements)
    summary = operation_summary(facts, measurements)
    parent = next(item for item in summary["types"] if item["type"] == "agent")
    assert parent["linked_children"][0]["operations"] == 1
