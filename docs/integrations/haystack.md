# Using Witdem with Haystack

**Status: stable and recommended for first-time users.**

Witdem uses Haystack's native OpenTelemetry tracer to observe the components that actually run. The SDK wrapper adds one correlated execution lifecycle and evaluates the pipeline's returned data against `.witdem/witdem.yaml`.

## Requirements

- Python 3.10 or newer
- `haystack-ai>=3.0,<4`
- `opentelemetry-haystack>=1,<2`
- `witdem-sdk[haystack]` 0.3.x
- A running Witdem receiver at `WITDEM_ENDPOINT`
- Provider credentials only when the chosen Haystack components require them

Instrumenting Haystack 2 raises an explicit compatibility error. The exact range is also published in [`compatibility.json`](../../compatibility.json).

## Installation

Until the SDK is published, install it from the Witdem checkout:

```bash
python -m pip install "witdem-sdk[haystack]==0.3.0"
export WITDEM_ENDPOINT=http://localhost:4318
```

After package publication, the equivalent command will be:

```bash
python -m pip install "witdem-sdk[haystack]>=0.3,<0.4"
```

## Minimal pipeline

This complete provider-free example makes the integration boundary visible:

```python
from haystack import Pipeline, component
from witdem_sdk.integrations.haystack import instrument

@component
class Answer:
    @component.output_types(answer=str)
    def run(self, question: str):
        return {"answer": f"Answered: {question}"}

pipeline = Pipeline()
pipeline.add_component("answer", Answer())

# The one Witdem-specific code integration.
pipeline = instrument(pipeline)

result = pipeline.run({"answer": {"question": "What is observability?"}})
print(result["answer"]["answer"])
```

Use this matching `.witdem/witdem.yaml`:

```yaml
version: 1
service:
  name: haystack-answer
  runtime: haystack
telemetry:
  capture_content: false
contracts:
  - name: answer
    artifact:
      name: Agent answer
      valid: {non_empty: $.answer.answer}
    decision:
      name: Result validity
      expected: true
      observed: $.witdem.artifact_valid
    product_goal:
      name: Useful answer returned
      achieved: $.witdem.artifact_valid
```

The wrapper exposes the underlying pipeline's normal attributes and supports:

```python
pipeline.run(data)
await pipeline.run_async(data, concurrency_limit=4)
async for partial in pipeline.run_async_generator(data):
    ...
```

## Add Witdem to an existing pipeline

The application architecture does not change:

```diff
+ from witdem_sdk.integrations.haystack import instrument

  pipeline = build_pipeline()
+ pipeline = instrument(pipeline)
  result = pipeline.run(data)
```

If your pipeline returns extra wrapper keys that are not part of the business result, the integration removes known Haystack include-output wrappers before evaluating the contract. For unusual return shapes, pass `report_result=` as an explicit override.

## Real-world execution shapes

The deterministic compatibility suite in [`examples/haystack/compatibility/runner.py`](../../examples/haystack/compatibility/runner.py) exercises Haystack 3 forms based on official documentation:

- retrieval-augmented linear pipelines;
- `ConditionalRouter` branches;
- `BranchJoiner` validation/correction loops;
- tool-calling `Agent` executions;
- fallback search after a failed primary path;
- nested pipelines exposed through `PipelineTool`.

The suite records source URLs and substitutes local deterministic generators where provider calls are not necessary. Product Factory then validates live OpenAI, DeepSeek, Mistral, and Anthropic-backed workloads without claiming ownership of Haystack's examples.

## Parallel and asynchronous proof

[`examples/haystack/pipeline`](../../examples/haystack/pipeline) starts keyword and semantic retrievers concurrently, joins them, and optionally calls OpenAI:

```bash
cd examples/haystack/pipeline
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

The integration combines Haystack's declared component/socket graph with the
socket names actually activated during a run. The dashboard therefore renders
active fan-out and fan-in from explicit framework relationships. Static routes
that did not emit and components that did not execute are omitted. Historical
runs without this metadata retain the earlier parentage/timing fallback.

## What Witdem captures

Verified behavior includes:

- the pipeline and executed component boundaries;
- agents, tools, retrievers, generators, nested pipelines, branches, and loops when Haystack emits them;
- synchronous, asynchronous, and async-generator lifecycles;
- overlapping sibling execution;
- stable component identifiers and types;
- source/output and destination/input socket relationships for executed paths;
- router outputs that emitted, joiner inputs that activated, and retry/feedback edges;
- errors and component timings;
- provider, model, and usage observed from generator response metadata;
- multiple configured providers without assigning one run-wide provider to every call;
- YAML-defined application result, decision, evaluations, metrics, dimensions, and product goal.

Provider response metadata is attached to the same native Haystack span. When Haystack exposes only one aggregate usage total and exactly one configured model identity, Witdem records a marked aggregate fallback; it does not fabricate per-call splits.

## Limitations

- Haystack 2 is unsupported by the high-level wrapper.
- A custom component that neither creates a native model span nor exposes recognizable generator response metadata may appear only as a component. Add standard GenAI attributes or a native `witdem.model(...)` operation around that call.
- An abandoned `run_async_generator` has no authoritative final result, so runtime spans close but the business contract is not evaluated.
- Content capture remains disabled by default; pipeline inputs and outputs are not retained merely because tracing is enabled.
- Topology capture stores component/socket names only. It never stores socket values, prompts, documents, model responses, contract content, credentials, or API keys when `capture_content=False`.
- Cost still requires provider-reported money or a recognized provider/model plus token usage.

See [Troubleshooting: Haystack component not captured](../troubleshooting.md#haystack-component-or-model-not-captured).
