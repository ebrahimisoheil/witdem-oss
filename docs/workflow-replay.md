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
| Runtime telemetry | Which operations ran, parentage, attempts, route evidence, timing, provider/model, tokens, and vadmeasured cost |
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

The workflow page shows the complete declared DAG in one vertical canvas. Stage labels remain visible beside their steps; no stage drill-down is required to discover the rest of the workflow. Step cards carry their business role and description plus runtime state, time, attempts, provider/model, tokens, and measured cost. Completed cards are visually neutral. Failed cards are red, recovered cards amber, running cards blue, and inactive branches gray. Clickability is a separate purple `Inspect evidence →` affordance. Detailed raw observations live in the step evidence drawer.

`/api/v1/runs/{execution_id}` remains compatible and returns `canonical_url`. The legacy UI route redirects associated runs to the workflow execution route. Unmatched historical runs retain the legacy replay instead of breaking.

## Topology discrepancies

Inactive declared nodes are expected and shown as inactive; they are not errors. Unexpected observed operations and unexpected mapped transitions are returned in `workflow_replay.discrepancies`. Witdem shows these differences rather than mutating the template or forcing runtime evidence onto unrelated nodes.

## Deliberate limits

- Matching is exact and deterministic. Regex and executable match expressions are intentionally excluded from v1.
- The full graph uses declared `depends_on` topology. Framework topology enriches and validates it but never rewrites it.
- Definitions are versioned by a content hash. Editing YAML creates a new stored template version; automatic visual diffing between versions is not yet included.
- Executions with ambiguous legacy matching require an explicit workflow id or a more specific configured match.
