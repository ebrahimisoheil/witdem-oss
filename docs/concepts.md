# Concepts

## Tracing and business meaning

An execution trace can show that an LLM call completed, a tool ran, and a branch was taken. It cannot know whether the user received the right answer or whether the application achieved its product goal.

For example:

```text
Technical fact: the final model call returned successfully.
Business fact: the support case was resolved using the required customer-data route.
```

Witdem keeps these facts separate:

- **Runtime telemetry** records executions, parent/child structure, timing, models, tools, retries, errors, tokens, and provider evidence.
- **Application semantics** define result validity, decisions, evaluations, metrics, dimensions, and product-goal success.

This separation prevents a healthy runtime from being reported as a successful product outcome.

## Two ingestion modes

### Standard OpenTelemetry

If an application already emits OTLP/HTTP traces, it needs no Witdem package:

```bash
export OTEL_SERVICE_NAME=my-agent
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

Witdem accepts generic OpenTelemetry plus common GenAI and OpenInference attributes. This mode provides runtime analytics only unless the application separately sends semantic SDK records.

### SDK-enriched

The SDK can configure OpenTelemetry, own one execution lifecycle, install a framework-native callback or processor, and evaluate `.witdem/witdem.yaml` against the returned result.

Use SDK-enriched mode when you want product-goal, decision, evaluation, or application-outcome analytics. Do not add a second manual exporter to the same SDK-owned entrypoint; duplicate exporters can send the same spans twice.

## Executions and operations

One top-level application request maps to one execution. Physically observed framework nodes, components, agents, model calls, tools, retrievers, and other steps map to operations beneath it.

Witdem uses trace/span identity and explicit framework links. It does not connect operations merely because their names or timestamps look similar. Configured graph nodes that did not execute are not materialized.

## Paths, concurrency, and loops

The run replay represents observed execution, not the framework's static definition. Conditional branches therefore show only the path that ran. Overlapping sibling spans are preserved as concurrent branches. Repeated operations can remain visible as iterations while workflow analytics derives compact semantic path variants.

## Cost and measurement coverage

Cost has two valid sources:

1. money reported by the provider or framework;
2. calculation from observed provider, model, and token usage using Witdem's versioned pricing catalog.

If any required evidence is absent or the model is not in the catalog, cost remains unavailable with a reason. Witdem never silently converts missing cost to zero.

## Privacy

Content capture is disabled by default. Structural analytics does not require prompts, completions, documents, tool arguments, tool results, or graph state. Each integration guide states what metadata it observes and its current limitations.
