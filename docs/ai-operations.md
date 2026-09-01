# AI operations and evaluations

Witdem normalizes AI work into a vendor-neutral, versioned analytics contract. OpenTelemetry, OpenInference, native SDKs, and framework callbacks are input dialects; they are not the public dashboard schema.

## Operation identity

Every observed operation keeps independent dimensions. Operation type says
what happened; interface says how it was invoked; provider/implementation say
who performed it; execution source/framework say where it ran:

- `family`: an extensible grouping such as inference, knowledge, tools, MCP,
  agent control, orchestration, media, quality, memory, human work, external
  action, data movement, or custom;
- `type`: a well-known type or `x.<namespace>.<name>` extension;
- `subtype`: the source operation name, retained without interpretation;
- `interface`: model API, MCP, tool, framework, library, datastore, search
  service, local code, external API, browser, human, or unknown;
- `role`: application, model, tool, agent, evaluator, guardrail, system, or human;
- input and output modalities: text, structured data, document, vector, image, audio, or video.

Provider, gateway, model, model vendor, runtime, and framework are separate explicit identities. Witdem never derives a provider from a model-name prefix. A Mistral model reached through OpenRouter is therefore represented as an OpenRouter provider and a Mistral model only when those facts were reported.

The registry is deliberately open. Its initial vocabulary covers inference,
knowledge/search, tools, MCP boundaries, agent control, orchestration, media,
quality, memory, human work, external actions, and data movement. Dashboard
cards are generated from the observed canonical facts, so adding a custom type
does not require provider-specific UI code.

MCP is normally an interface, not the semantic type. For example, an MCP tool
call may contain a retrieval operation, whose child operations are an embedding
and vector search. Likewise, a generation span requesting a tool remains the
parent of the tool execution rather than absorbing the downstream work.

Unknown future operations remain queryable as extensions:

```python
with witdem.operation(
    "special transform",
    family="custom",
    operation_type="contract_conflict_analysis",
    interface="local",
) as operation:
    operation.measure("items.output", 12, unit="item")
```

The same boundary is usable as a decorator:

```python
@witdem.operation(
    family="knowledge",
    operation_type="retrieval",
    subtype="hybrid_search",
    interface="mcp",
)
def retrieve_contracts(query):
    ...
```

## Typed measurements

Measurements are long-form facts with a key, value, unit, aggregation, scope, status, provenance, and applicability source. Missing values are never converted to zero.

```python
with witdem.operation(
    "contract OCR",
    type="ocr",
    interface="model_api",
    input_modalities=["document"],
    output_modalities=["text"],
    provider="reported-provider",
    model="reported-model",
) as operation:
    operation.measure("pages.processed", 4, unit="page", provenance="provider_reported")
    operation.cost(0.008)
```

OCR requires processed pages. Tokens are optional and display as **Not applicable** when the provider does not report token billing. Retrieval expects query and returned-document counts; reranking expects input and output candidate counts; embeddings expect input-item and output-vector counts. Audio/video operations use duration, frame, item, or token meters according to the reported API capability. Cost is applicable only when billing evidence, catalog eligibility, or YAML explicitly requires it.

The compatibility fields `total_tokens` and `known_cost` remain derived for existing consumers.

## Optional workflow declarations

Automatic observation works without YAML declarations. A declaration makes expected-versus-observed coverage explicit and resolves ambiguous framework spans:

The generic SDK `operation()` call defaults to `kind="component"`, no explicit
semantic `type`, `interface="unknown"`, and `role="application"`. Until an
integration or YAML node supplies a semantic type, Witdem treats that span as
workflow coordination and displays it as **Workflow step** rather than as an
embedding, retrieval, generation, OCR, or tool operation. Top-level framework
pipeline spans similarly remain **Workflow** coordination. Both stay available
for replay and attribution but are excluded from semantic Operation Health by
default.

```yaml
stages:
  - id: ingestion
    name: Ingestion
    nodes:
      - id: contract_ocr
        name: Contract OCR
        operation:
          type: ocr
          expects: [pages.processed]
          optional: [cost.usd]
```

Reusable workflow evaluation suites may also be declared:

```yaml
evaluation_suites:
  contract_quality:
    workflow: contract-review
    evaluations: [extraction_quality, evidence_sufficiency]
```

Dataset and candidate versions belong to the campaign, not application code.

## Online and offline evaluations

An evaluator operation and an evaluation result are distinct. The operation records evaluator latency, provider/model, tokens, and cost with `role=evaluator`; the result records the subject, observed value, explicit pass state or target, definition version, and provenance. Evaluator cost does not silently inflate application-generation cost.

Python-native campaigns can attach campaign and case identity:

```python
with client.execution("offline evaluation"):
    with client.evaluation_campaign(
        "campaign-2026-08",
        suite_id="contract_quality",
        dataset_id="cuad",
        dataset_version="1",
        candidate_version="candidate-a",
        baseline_version="baseline-a",
    ):
        with client.evaluation_case("case-001"):
            client.evaluation(
                "extraction_quality",
                score=0.94,
                attributes={"passed": True, "target": 0.9, "direction": "at_least"},
            )
```

Framework-neutral campaigns use JSONL. The first record describes the campaign; later records describe case results:

```jsonl
{"record_type":"campaign","campaign_id":"campaign-1","suite_id":"contract_quality","workflow_id":"contract-review","dataset_id":"cuad","dataset_version":"1","candidate_version":"candidate-a","baseline_version":"baseline-a"}
{"record_type":"result","campaign_id":"campaign-1","case_id":"case-001","evaluation_key":"extraction_quality","score":0.94,"passed":true,"target":0.9,"direction":"at_least","evaluator_type":"code"}
```

Validate without writing, then import:

```bash
witdem eval validate campaign.jsonl
witdem eval import campaign.jsonl
```

Witdem records and analyzes evaluator outputs. It does not execute arbitrary evaluation code.

## Privacy and rebuilding

Content capture is off by default. Prompts, responses, documents, retrieved passages, reranking inputs, embeddings/vectors, and media payloads are removed from normalized analytics. Safe counts and explicit metadata survive. Content must be enabled deliberately in application configuration, and raw OTLP payload retention requires the separate backend gate `WITDEM_CAPTURE_CONTENT=1`.

Raw corpus batches remain immutable. Duckle produces canonical operation classifications and measurement facts, and the workflow projector adds the matched template/node context. These serving projections are disposable:

```bash
witdem workflow rebuild
```

The rebuild retains historical workflow hashes and unknown extension measurements.

## Dashboard placement

Workflow detail links to dedicated Overview, Operations, Evaluations, and Executions routes. Operations and Evaluations combine compact summaries, interactive analytics, and filtered evidence rather than presenting raw lists alone. Execution replay includes compact operation/evaluation summaries. System Health and Issues surface cross-workflow operation failures and missing applicable meters; Goal Performance links evaluations without treating every evaluation as a product goal. Charts never mix pages, tokens, documents, vectors, seconds, and media units on one axis.
