# Configuring `.witdem/witdem.yaml`

Technical instrumentation tells Witdem what ran. The YAML contract tells Witdem what the returned value means to your application.

Start with the task-oriented [YAML contract tutorial](contract-tutorial.md). It includes complete, validator-tested contracts for boolean goals, classification, quality thresholds, assurance checks, routing, research loops, RAG, flexible chat outcomes, and diagnostic metrics. This page is the field reference.

An LLM request that completed is not automatically a qualified lead, an approved report, or a resolved support case. Put those definitions in `.witdem/witdem.yaml`, next to the application that owns them.

## Discovery and validation

The SDK searches the current directory and its parents for `.witdem/witdem.yaml`. Set `WITDEM_CONFIG=/absolute/path/to/witdem.yaml` or pass `config_path=` when discovery is not appropriate.

```bash
witdem validate
witdem validate --config path/to/witdem.yaml
```

The file is strict: unknown fields and invalid types fail validation rather than being ignored.

## Top-level fields

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `version` | No | integer | Configuration format; currently `1` |
| `service` | Yes | mapping | Application identity |
| `service.name` | Yes | string | Stable application/service name |
| `service.description` | No | string | Human explanation of the application |
| `service.runtime` | No | string | Runtime identity such as `haystack` or `langgraph` |
| `telemetry` | No | mapping | Endpoint, mode, and content-capture policy |
| `telemetry.endpoint` | No | string | Receiver base URL; `WITDEM_ENDPOINT` takes precedence |
| `telemetry.mode` | No | string | `auto`, `existing`, or `disabled` |
| `telemetry.capture_content` | No | boolean | Defaults to `false` |
| `contracts` | No | list or mapping | Named business contracts |
| `default_contract` | No | string | Contract used when more than one exists; the first list entry becomes the default |

`auto` configures an exporter when the process does not already own a concrete global tracer provider. `existing` uses the current provider without adding a Witdem exporter. `disabled` emits semantic records only and requires an execution ID or active trace.

## Contract modes

Set `mode` on every contract:

- `expression` reads business facts from the framework's final returned value. Use it with the two-line `instrument(...)` integration.
- `reported` declares a business glossary for facts supplied explicitly through `Witdem.report(...)`.

The SDK still infers the mode for older files, but new contracts should always declare it. A declared mode whose fields use the other mode's grammar is a validation error.

## Expression contract

Framework wrappers can evaluate a returned object directly. Paths start with `$.` and traverse mappings, dataclasses, Pydantic models, object fields, and list indexes after the result is converted into a JSON-shaped value.

| Field | Required | Meaning |
| --- | --- | --- |
| `application_outcome.status` | No | Business result label; defaults to `completed` |
| `artifact.name` | Yes | Human name of the returned artifact |
| `artifact.description` | No | What the artifact represents |
| `artifact.valid` | Yes | Boolean expression for result validity |
| `decision.name` | Yes | Human name of the decision |
| `decision.expected` | No | Expected value or expression |
| `decision.observed` | Yes | Observed value or expression |
| `decision.correct` | No | Explicit correctness expression; otherwise expected equals observed |
| `decision.reason` | No | Explanation extracted from the result |
| `product_goal.name` | Yes | Human product-goal name |
| `product_goal.achieved` | Conditional | Legacy/direct boolean expression for goal success |
| `product_goal.hard_requirements` | Conditional | Non-negotiable boolean expression; cannot be offset by semantic confidence |
| `product_goal.semantic_outcome` | Conditional | Scored natural-language outcome with achievement and assurance thresholds |
| `product_goal.evidence_sufficient` | No | Defaults to `true` |
| `product_goal.required_path_observed` | No | Defaults to `true` |
| `product_goal.closest_blocker` | No | Defaults to `none` |
| `evaluations` | No | Named scores, labels, or values with optional targets |
| `metrics` | No | Named quantitative values |
| `attributes` | No | Dimensions extracted from the result |

Define either `product_goal.achieved`, or one or both of `hard_requirements` and
`semantic_outcome`. Do not combine the legacy/direct `achieved` expression with
the new assurance fields.

Supported expressions are deliberately small:

```yaml
path: $.report
non_empty:
  non_empty: $.report
all:
  all:
    - $.approved
    - $.evidence_sufficient
any:
  any:
    - $.approved
    - $.needs_review
not:
  not: $.failed
exists:
  exists: $.decision
length:
  length: $.sources
sum:
  sum: $.step_costs
less_or_equal:
  less_than_or_equal:
    - $.retry_count
    - 2
equals:
  equals:
    - $.decision
    - approved
fallback:
  coalesce:
    - $.blocker
    - none
conditional:
  choose:
    when: $.approved
    then: completed
    else: review
confidence:
  fraction_true:
    - $.city_correct
    - $.weather_correct
    - $.temperature_correct
```

The labels on the left are explanatory; only the values are expressions. There is no arbitrary code execution, wildcard JSONPath, filter syntax, or implicit business inference.

## Hard requirements and loose chat outcomes

Framework integrations expose a canonical, content-local envelope while a
contract is evaluated:

- `$.witdem.result`: the final answer/result
- `$.witdem.messages`: normalized conversation messages when returned
- `$.witdem.tool_calls`: returned tool calls
- `$.witdem.path`: returned runtime or business trajectory
- `$.witdem.runtime`: returned runtime identifier

This does not enable content capture in telemetry. The values are used locally
to evaluate the YAML, and only the resulting scores and business meaning are
reported.

Use hard requirements for behavior that must always hold. Use
`semantic_outcome.score` for wording-tolerant meaning. `threshold` determines
achievement; `assurance_threshold` determines whether an achieved result is
assured or needs attention.

```yaml
product_goal:
  name: Correct weather response
  hard_requirements:
    all:
      - non_empty: $.witdem.result
      - $.safe
  semantic_outcome:
    name: Weather meaning confidence
    evaluator: expression
    score:
      fraction_true:
        - contains:
            - $.witdem.result
            - London
        - contains:
            - $.witdem.result
            - cloudy
        - any:
            - matches:
                - $.witdem.result
                - '15\s*°?\s*C'
            - matches:
                - $.witdem.result
                - '15\s+degrees?\s+Celsius'
    threshold: 0.6
    assurance_threshold: 1.0
```

The result has one of three states:

- `assured`: hard requirements pass and confidence meets the assurance target
- `needs_attention`: the goal is achieved, but confidence is below the assurance target
- `not_achieved`: a hard requirement fails or confidence is below the achievement threshold

`evaluator: expression` is deterministic and requires no external judge. A
future semantic judge can supply its score as part of the returned result and
the same field can read it with a path such as `$.quality.semantic_score`.

## Example 1: simple RAG answer

```yaml
version: 1
service:
  name: rag-answer
  runtime: haystack
telemetry:
  capture_content: false
contracts:
  - name: answer
    mode: expression
    application_outcome:
      status:
        choose:
          when: {non_empty: $.answer}
          then: completed
          else: unresolved
    artifact:
      name: Grounded answer
      valid: {non_empty: $.answer}
    decision:
      name: Result validity
      expected: true
      observed: $.witdem.artifact_valid
    product_goal:
      name: Useful answer returned
      achieved: $.witdem.artifact_valid
    metrics:
      - name: Retrieved documents
        value: {length: $.documents}
        unit: documents
```

## Example 2: decision and branch

```yaml
version: 1
service:
  name: qualification-agent
  runtime: langgraph
contracts:
  - name: qualification
    mode: expression
    application_outcome:
      status: $.decision
    artifact:
      name: Company profile
      valid: {non_empty: $.profile}
    decision:
      name: Qualification decision
      expected: $.expected_decision
      observed: $.decision
      reason: $.reason
    product_goal:
      name: Correct qualification
      achieved:
        all: [$.witdem.artifact_valid, $.witdem.decision_correct]
      closest_blocker:
        choose:
          when: $.witdem.decision_correct
          then: none
          else: decision mismatch
    evaluations:
      - name: Evidence coverage
        score: $.evidence_coverage
        target: 0.8
        direction: higher_is_better
```

## Example 3: multi-step workflow

```yaml
version: 1
service:
  name: research-pipeline
  runtime: native
contracts:
  - name: approved_report
    mode: expression
    application_outcome:
      status: $.editorial_decision
    artifact:
      name: Research report
      valid: {non_empty: $.report}
    decision:
      name: Editorial approval
      expected: approved
      observed: $.editorial_decision
    product_goal:
      name: Approved research report
      achieved:
        all:
          - $.witdem.artifact_valid
          - $.witdem.decision_correct
          - {less_than_or_equal: [$.iterations, 3]}
      closest_blocker: {coalesce: [$.blocker, none]}
    metrics:
      - name: Iterations
        value: $.iterations
        unit: iterations
      - name: Sources found
        value: {length: $.sources}
        unit: sources
```

## Metadata-only contracts

Applications that already compute their business facts can declare names and allowed values in YAML, then call `Witdem.report(...)`. In this mode YAML contains no extraction expressions and Python supplies only known values.

```yaml
contracts:
  - name: support_case
    mode: reported
    result:
      name: Customer support result
      values:
        completed:
          description: A useful answer was returned.
          tone: success
        unresolved:
          description: The request could not be resolved.
          tone: warning
    product_goal:
      name: Correct support resolution
    evaluations:
      reference_coverage:
        name: Reference answer coverage
        target: 1.0
        direction: higher_is_better
```

```python
witdem.report(
    contract="support_case",
    result="completed",
    product_goal_achieved=True,
    evaluations={"reference_coverage": 1.0},
)
```

Supported value tones are `success`, `warning`, `failure`, and `neutral`. Labels not assigned a tone receive categorical colors without Witdem inventing their meaning.

## Common mistakes

- Mapping a path that does not exist; it evaluates to `null`, often producing false or `unknown`.
- Treating runtime completion as product-goal success.
- Writing JSON objects inside quoted YAML strings. Use normal nested YAML mappings.
- Using `Witdem.report(...)` with an expression contract or `Witdem.complete(...)` with a metadata-only contract.
- Expecting streaming wrappers to evaluate partial LangGraph/LangChain chunks as one authoritative final result. Runtime telemetry is recorded, but final contract evaluation occurs only when a final result is available.
