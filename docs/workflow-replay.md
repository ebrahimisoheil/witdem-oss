# Workflow-centric replay

Witdem treats a workflow as a persistent declared template and an execution as observed evidence projected onto that template:

```text
workflow definition + execution telemetry = workflow replay
```

## Why the model changed

The earlier run page built a graph from one execution and compressed long paths into phases based on node count. Those phases were screen-layout artifacts, not application meaning. Different executions of the same workflow could therefore appear to have different structures, model calls became detached graph objects, and inactive branches were absent rather than visibly inactive.

The new boundary is explicit:

| Owner | Responsibility |
| --- | --- |
| Developer YAML | Workflow identity, meaningful stages, step identity, branches, convergence, loops, fallbacks, and outcomes |
| Framework adapter | Native component/node identity and physical topology, without inventing business stages |
| Runtime telemetry | Which operations ran, parentage, attempts, route evidence, timing, provider/model, tokens, and measured cost |
| Witdem projection | Match observations to declared steps, embed owned model evidence, aggregate stage state, validate topology, and expose discrepancies |

The canonical analytics tables and their definitions are unchanged. Workflow templates and execution associations are additive read context, so existing cost, latency, failure, path, and business-outcome analytics retain their meaning.

## Configuration

Place one `witdem.yml` in the project root. It owns service/telemetry configuration and registers workflow files:

```yaml
version: 1
service:
  name: support-service
  runtime: langgraph/support-routing
telemetry:
  capture_content: false
workflows:
  - id: support-routing
    definition: workflows/support-routing.yml
default_workflow: support-routing
```

Each workflow is ordinary YAML. Nested objects are YAML mappings, never JSON strings:

```yaml
version: 1
id: support-routing
name: Support routing
framework: langgraph
match:
  runtime_names: [langgraph/support-routing]
stages:
  - id: understand
    name: Understand
    nodes:
      - id: classify
        name: Classify request
        kind: graph_node
        match: {names: [classify]}

  - id: resolve
    name: Resolve
    depends_on: [understand]
    nodes:
      - id: retrieve
        name: Retrieve context
        kind: graph_node
        match: {names: [retrieve]}
        depends_on:
          - {node: classify, type: branch, route: self_service}

      - id: answer
        name: Answer customer
        kind: graph_node
        match: {names: [answer]}
        depends_on: [retrieve]

      - id: escalate
        name: Escalate to human
        kind: graph_node
        match: {names: [escalate]}
        depends_on:
          - {node: classify, type: branch, route: human}
outcomes:
  - {id: resolved, name: Resolved, from: [answer]}
  - {id: escalated, name: Escalated, from: [escalate]}
```

Steps live inside their presentation stage and own their incoming `depends_on` edges, so the file reads top-to-bottom as a DAG. A string dependency is an ordinary edge. A dependency mapping adds branch, convergence, route, or fallback meaning. Multiple dependencies imply convergence. Retry behavior belongs on the step it repeats (`retry: {via: repair, max_attempts: 2}`) and is excluded from DAG cycle validation.

The schema deliberately does not repeat provider/model configuration, token fields, duration, cost, or observed attempts. Those facts belong to telemetry. `kind` is a user-facing business role such as `Research`, `Validation`, or `Decision`; it is not required to mirror a framework's generic `component` or `graph_node` kind.

Frameworks may emit useful implementation-detail spans in addition to the shared business steps. Keep those spans in raw replay while excluding them from topology warnings with YAML:

```yaml
ignore_observed:
  - kinds: [component, graph_node]
  - names: [workflow.definition]
```

The full Haystack and LangGraph examples are in `examples/workflow-replay/`.

## Declare semantic operations, not framework wrappers

Workflow topology and operation analytics answer different questions:

- A YAML **workflow node** says where work belongs in the business DAG.
- An observed **operation** says what kind of work actually ran: generation,
  retrieval, embedding, reranking, OCR, a tool call, an evaluation, or another
  vendor-neutral type.
- Framework workflow, pipeline, agent, chain, and component spans describe
  coordination. Witdem retains them for attribution and replay, but excludes
  them from the default semantic Operation Health view.

The generic Python SDK operation API intentionally starts without guessing the
semantic operation type:

| `witdem.operation(...)` argument | Default |
| --- | --- |
| `kind` | `component` |
| `type` | not set |
| `interface` | `unknown` |
| `role` | `application` |

An untyped generic operation is therefore classified as orchestration and may
appear as **Workflow step** in coordination or replay evidence. A top-level
framework pipeline span may appear as **Workflow**. These are not model,
retrieval, OCR, or tool calls, and the dashboard does not count them as
semantic operation health by default.

Declare the operation on the matched YAML node when the framework span is
ambiguous:

```yaml
stages:
  - id: knowledge
    name: Knowledge
    nodes:
      - id: embed_query
        name: Embed query
        kind: embedding
        match:
          names: [embed_query]
        operation:
          type: embedding
          expects: [items.input, vectors.output]
          optional: [tokens.input, cost.usd]

      - id: retrieve_context
        name: Retrieve context
        kind: retrieval
        match:
          names: [retrieve_context]
        operation:
          type: retrieval
          expects: [queries, documents.output]
```

The declaration does not fabricate measurements. It tells Witdem what the
matched operation is and which meters are required or optional. Runtime
telemetry still supplies provider, model, duration, attempts, measurements,
and cost.

When instrumenting a custom operation directly, supply the same semantic type
in code:

```python
with witdem.operation(
    "embed_query",
    type="embedding",
    interface="model_api",
    role="application",
    provider="reported-provider",
    model="reported-model",
) as operation:
    operation.measure("items.input", 1, unit="item")
    operation.measure("vectors.output", 1, unit="vector")
```

Prefer automatic framework/provider instrumentation first. Use explicit SDK
metadata or a YAML `operation` declaration only where the observed span is
ambiguous. Provider and model identities remain telemetry facts; do not encode
them into the YAML operation type.

## Why an execution says “No YAML replay”

The execution list can display runtime telemetry without a workflow graph. The
**No YAML replay** badge means the execution was observed but was not associated
with an authored workflow definition and template hash.

To make the execution open a workflow replay:

1. Register the workflow definition in the project-root `witdem.yml`.
2. Set `default_workflow` when the application has one workflow, or pass the
   workflow ID when opening the execution.
3. Make sure the workflow `match` rules identify the runtime/component names
   actually emitted by the integration.
4. Validate and compile the configuration before running the application.

```bash
witdem workflow compile --check
```

After changing matching or operation declarations, new executions use the new
template automatically. Rebuild disposable historical projections when needed:

```bash
witdem workflow rebuild
```

Do not add a placeholder workflow merely to remove the badge. A missing replay
is more accurate than attaching an execution to the wrong DAG.

## Compilation and rebuilds

YAML is the only authored workflow source. Witdem automatically compiles a
configured definition when its manifest is missing, corrupt, or stale:

```bash
witdem workflow compile
witdem workflow compile --check
witdem workflow compile --force
```

Manifests live at
`${WITDEM_DATA_DIR}/compiled/workflows/<workflow-id>/<template-hash>.json`.
They contain normalized nodes, transitions, outcomes, match indexes,
dependency order, and stable logic/goal geometry. The browser only fits that
geometry to the viewport. Validity is keyed by YAML content hash plus compiler
version. `--check` never writes and exits nonzero for invalid, missing, or
stale output.

Execution projections are materialized during ELT. Historical executions keep
the template hash observed when they ran, even after YAML changes. Rebuild
serving data and projections from the immutable corpus after an upgrade with:

```bash
witdem workflow rebuild
```

Compiled manifests and projections are disposable derivatives; do not edit or
back them up as authoritative workflow definitions.

## Matching and ingestion

When exactly one workflow is registered, the SDK uses it automatically; otherwise pass `workflow="workflow-id"` to `Witdem.execution`. The SDK writes the workflow id and template hash onto execution spans and sends a `workflow.definition` semantic record whose `definition` is a nested object. The immutable SDK/OTel corpus remains the source of truth.

During normalization, Witdem persists templates in `workflow_templates` and execution associations in `execution_workflows`. Matching precedence is:

1. explicit `witdem.workflow.id` emitted by the SDK;
2. a persisted SDK definition record;
3. an unambiguous configured runtime/service/execution-name match.

The third rule is the migration path for executions recorded before workflow declarations existed. Ambiguous or unmatched executions are left unassociated; Witdem does not silently guess.

## API and navigation

Workflow-first routes are:

- `GET /api/v1/workflow-definitions`
- `GET /api/v1/workflow-definitions/{workflow_id}`
- `GET /api/v1/workflow-definitions/{workflow_id}/executions/{execution_id}`
- `/workflows`
- `/workflows/{workflow_id}`
- `/workflows/{workflow_id}/executions/{execution_id}`

The workflow page shows the complete declared DAG horizontally and fits the
first view to the available page. Logic and goal flow use a toggle; evidence
inspection opens vertically. Step cards carry their business role and
description plus runtime state, time, attempts, provider/model, tokens, and
measured cost. Failures use failure color, recovery uses attention color, and
clickability remains a separate visible affordance.

`/api/v1/runs/{execution_id}` remains compatible and returns `canonical_url`. The legacy UI route redirects associated runs to the workflow execution route. Unmatched historical runs retain the legacy replay instead of breaking.

## Topology discrepancies

Inactive declared nodes are expected and shown as inactive; they are not errors. Unexpected observed operations and unexpected mapped transitions are returned in `workflow_replay.discrepancies`. Witdem shows these differences rather than mutating the template or forcing runtime evidence onto unrelated nodes.

## Deliberate limits

- Matching is exact and deterministic. Regex and executable match expressions are intentionally excluded from v1.
- The full graph uses declared `depends_on` topology. Framework topology enriches and validates it but never rewrites it.
- Definitions are versioned by a content hash. Editing YAML creates a new stored template version; automatic visual diffing between versions is not yet included.
- Executions with ambiguous legacy matching require an explicit workflow id or a more specific configured match.
