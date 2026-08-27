# Instrumenting custom AI workflows

**Status: supported.** No framework is required.

Use the native SDK when the application already has meaningful step boundaries or when a provider has no dedicated adapter. The SDK emits standard OpenTelemetry spans and correlated semantic records.

## Installation

```bash
python -m pip install "witdem-sdk==0.3.0"
export WITDEM_ENDPOINT=http://localhost:4318
```

## Multi-step workflow

```python
from witdem_sdk import configure

def run_workflow(question: str) -> dict:
    with configure(runtime="native") as witdem:
        with witdem.execution("Research request"):
            with witdem.operation("Research", kind="component"):
                sources = research(question)

            with witdem.operation("Evaluate evidence", kind="component"):
                approved = evaluate(sources)

            if not approved:
                with witdem.operation("Targeted retry", kind="component"):
                    sources.extend(research(question, targeted=True))
                    approved = evaluate(sources)

            result = {
                "report": write_report(sources),
                "editorial_decision": "approved" if approved else "needs_review",
                "approved": approved,
                "sources": sources,
            }
            witdem.complete(result, contract="approved_report")
            return result
```

The matching YAML can define the result, decision, goal, and metrics without Python key-by-key reporting; see [multi-step workflow configuration](../configuration.md#example-3-multi-step-workflow).

## Model and tool operations

```python
with witdem.model("Draft report", provider="openai", model="gpt-4o-mini") as call:
    response = client.responses.create(...)
    call.response_model(response.model)
    call.usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        total_tokens=response.usage.total_tokens,
    )

with witdem.tool("search_catalog", call_id=tool_call_id):
    documents = search_catalog(query)
```

Record provider-reported money with `call.cost(amount_usd)`. Otherwise the server can calculate cost only when provider, model, usage, and a matching catalog entry are present.

## Explicit business reporting

Use a metadata-only YAML contract when the application already computes every business fact:

```python
witdem.report(
    contract="support_case",
    result="completed",
    result_valid=True,
    decision="expected_route",
    expected_decision="expected_route",
    product_goal_achieved=True,
    evaluations={"reference_coverage": 0.92},
    metrics={"retrieved_documents": 8},
    dimensions={"customer_tier": "enterprise"},
)
```

Prefer `complete(result)` when YAML expressions can describe the return shape. Use `report(...)` when the facts already exist but cannot or should not be reconstructed from one result object.

## Existing OpenTelemetry setup

`configure()` defaults to `telemetry_mode="auto"`. If the process already owns a compatible tracer provider and exporter, pass `telemetry_mode="existing"` and ensure that provider exports to Witdem. If you want semantic records without creating spans, use `disabled` with an explicit execution ID or active trace.

## Generic provider wrapper

For a sync or async provider call without a native adapter:

```python
from witdem_sdk.integrations.generic import instrument

observed_call = instrument(
    call_provider,
    operation_name="provider.generate",
    provider="provider-name",
    model="model-name",
)
result = observed_call(prompt)
```

The default observer reads conventional `model`, `input_tokens`, `output_tokens`, `total_tokens`, `cost`, and `cost_source` fields from mapping or object results. Pass `observe_result=` when the provider uses a different response shape. This wrapper is experimental because response schemas vary by provider.
