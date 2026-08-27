# Define a Witdem YAML contract

The canonical file is `.witdem/witdem.yaml` in the root of your application.
This tutorial builds one from an actual returned value, validates it, and
explains what appears in the dashboard. Use `.yaml` in new projects; a custom
`.yml` filename also works when passed explicitly with `--config`.

A Witdem contract answers four different questions:

1. **Result:** What did the application return?
2. **Decision:** Which business choice did it make, and was that choice correct?
3. **Product goal:** Did the workflow accomplish what the user needed?
4. **Assurance:** How strongly do the declared checks support that achievement?

Runtime completion is deliberately not one of those answers. A workflow may complete successfully while returning the wrong classification, an ungrounded answer, or a report that the critic rejected.

## The shortest useful tutorial

Assume the application returns this value:

```python
{
    "answer": "London is cloudy at 15°C.",
    "safe": True,
    "case_id": "weather-001",
}
```

Create `.witdem/witdem.yaml`:

```yaml
version: 1

service:
  name: weather-agent
  runtime: langgraph

telemetry:
  capture_content: false

default_contract: london_weather

contracts:
  london_weather:
    mode: expression
    description: Return a safe and factually complete London weather answer.

    application_outcome:
      status: answered

    artifact:
      name: Weather answer
      description: The final answer returned to the user.
      valid:
        non_empty: $.witdem.result

    decision:
      name: Answer returned
      expected: true
      observed: $.witdem.artifact_valid

    product_goal:
      name: Correct London weather answer
      description: Return a safe answer containing the required London weather facts.
      hard_requirements:
        all:
          - $.witdem.artifact_valid
          - $.safe
      semantic_outcome:
        name: Weather meaning confidence
        description: Share of required weather facts communicated by the answer.
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

    attributes:
      case_id: $.case_id
```

Validate it from the application directory:

```bash
witdem-sdk validate
```

This contract produces three meaningful states:

| Returned value | Goal | Assurance | Why |
| --- | --- | --- | --- |
| Safe answer with all three facts | Achieved | Assured | Hard rules pass and confidence is `1.0` |
| Safe answer with two facts | Achieved | Needs attention | Confidence clears `0.6` but misses `1.0` |
| Unsafe or empty answer | Not achieved | Not achieved | A hard requirement failed |

The wording may change without changing the business meaning. A hard rule,
however, can never be offset by a high semantic score.

### Choose where semantic confidence comes from

The example above computes confidence deterministically from declared evidence.
This is inexpensive, explainable, and does not make a hidden model call. It is
appropriate when the important facts, citations, or policy checks are known.

If the application already runs an evaluator or LLM judge, return its normalized
score with the application result and read that score from YAML:

```yaml
semantic_outcome:
  name: Answer quality confidence
  description: Score produced by the application's configured answer evaluator.
  evaluator: expression
  score: $.quality.semantic_score
  threshold: 0.7
  assurance_threshold: 0.9
```

Witdem deliberately does not choose or invoke a judge silently. The contract
remains provider-neutral: it consumes a score between `0` and `1`, regardless of
whether that score came from deterministic checks, a model evaluator, human
review, or another application-owned process.

### How the final answer path stays generic

`$.witdem.result` is the preferred path for chat and agent outputs. The SDK
normalizes common return shapes—`result`, `final_answer`, `answer`, `output`,
`report`, `response`, or the final returned message—into that path. It also
exposes:

- `$.witdem.messages`
- `$.witdem.tool_calls`
- `$.witdem.path`
- `$.witdem.runtime`

These values are evaluated locally. With `capture_content: false`, the raw
answer and conversation are not sent as telemetry; only the declared business
facts and scores are reported.

## Choose one contract mode

Every contract has one explicit mode. The rest of its grammar is fixed by that choice.

| Mode | Use it when | Integration |
| --- | --- | --- |
| `expression` | The SDK should read business facts from the framework's final returned value | Instrument the framework; no result-reporting callback |
| `reported` | Your application already computes the authoritative business facts | Call `Witdem.report(...)` with those facts |

Use `expression` for the lowest-friction framework integration:

```yaml
contracts:
  answer:
    mode: expression
    artifact:
      name: Agent answer
      valid:
        non_empty: $.answer
    decision:
      name: Answer validity
      expected: true
      observed: $.witdem.artifact_valid
    product_goal:
      name: Useful answer returned
      achieved: $.witdem.artifact_valid
```

```python
graph = instrument(build_graph())
result = graph.invoke(inputs)
```

Here `$.answer` reads `result["answer"]`. Witdem adds computed values under `$.witdem` while evaluating the contract, including `$.witdem.artifact_valid` and `$.witdem.decision_correct`.

For chat-shaped results, prefer the canonical `$.witdem.result` path. It finds
the common final-result fields or the final returned message, so the same
contract can work across LangChain, LangGraph, Haystack, OpenAI Agents,
LiteLLM, Smolagents, and direct SDK integrations.

When success has strict behavior but flexible wording, split the goal into
`hard_requirements` and `semantic_outcome`. The complete grammar and a
wording-tolerant chat example are in [Configuration](configuration.md#hard-requirements-and-loose-chat-outcomes).

Use `reported` when the application—not Witdem—already owns the final facts:

```yaml
contracts:
  support_case:
    mode: reported
    result:
      name: Customer support result
      values:
        answered: A useful answer was returned.
        escalated: Human assistance was requested.
    product_goal:
      name: Correct support resolution
```

```python
witdem.report(
    contract="support_case",
    result="answered",
    product_goal_achieved=True,
)
```

Do not mix the two modes. An expression contract contains extraction paths; a reported contract contains a business glossary.

## Build an expression contract step by step

### 1. Write one product-goal sentence

Start with what the user needed, not what the framework executed.

Good product goals:

- Return a non-empty answer supported by retrieved evidence.
- Select the expected support route.
- Deliver a report approved by the critic within three iterations.

Avoid implementation goals such as “the graph completed” or “the model returned HTTP 200.” Those are system-health signals.

```yaml
product_goal:
  name: Successful grounded answer
  description: Return a non-empty answer supported by retrieved evidence.
  achieved:
    all:
      - $.witdem.artifact_valid
      - $.witdem.decision_correct
```

### 2. Define the returned artifact

The artifact is the useful thing delivered by the application. `artifact.valid` must be a boolean, a result path, or a supported expression.

```yaml
artifact:
  name: Grounded answer
  description: The final answer returned to the user.
  valid:
    non_empty: $.answer
```

### 3. Define the business decision

Expected and observed values are separate. This prevents the SDK from declaring a decision correct merely because the application reported it.

```yaml
decision:
  name: Support route
  expected: $.expected_route
  observed: $.route
  reason: $.routing_reason
  outcomes:
    answer: Answer the customer directly.
    escalate: Route the request to a person.
```

If there is no independent expectation, omit `expected`. Decision correctness will be **Not observed**, not incorrectly reported as success.

### 4. Add assurance checks

Evaluations explain confidence in the result. Each evaluation defines exactly one observed `score`, `label`, or `value`.

```yaml
evaluations:
  - name: Reference answer coverage
    description: Share of required reference facts present in the answer.
    score: $.reference_coverage
    target: 0.8
    direction: higher_is_better
    unit: ratio
```

Allowed directions are:

- `higher_is_better`
- `lower_is_better`

Evaluations are explanatory measurements. They do not silently change goal
success. Put a required quality bar in `hard_requirements`,
`semantic_outcome`, or `product_goal.evidence_sufficient` when it must affect
achievement or assurance.

### 5. Add diagnostic metrics separately

Metrics describe the run but do not change product-goal success unless the goal expression explicitly references the same returned field.

```yaml
metrics:
  - name: Iterations
    value: $.iteration_count
    unit: iterations
  - name: Sources found
    value:
      length: $.sources
    unit: sources
```

### 6. Add dimensions for filtering

```yaml
attributes:
  case_id: $.case_id
  customer_tier: $.customer_tier
```

Dimensions should be stable categorical identifiers. Do not put prompts, reports, or other high-cardinality content into dimensions.

## Expression grammar

The expression language is deliberately small and has no arbitrary code execution.

| Expression | Operator | Meaning |
| --- | --- | --- |
| Path | `$.field` | Read a returned field |
| All | `all` | Every list item is truthy |
| Any | `any` | At least one list item is truthy |
| Not | `not` | Boolean negation |
| Exists | `exists` | Value is not null |
| Non-empty | `non_empty` | Value is present and non-empty |
| Length | `length` | Length of a list or string |
| Sum | `sum` | Add numeric values |
| Fraction true | `fraction_true` | Share of list items that are truthy |
| At most | `less_than_or_equal` | Numeric upper bound |
| Equals | `equals` | Values are equal |
| Contains | `contains` | Case-insensitive text containment or collection membership |
| Matches | `matches` | Case-insensitive regular-expression match |
| Fallback | `coalesce` | First non-null value |
| Conditional | `choose` | Select one branch |

Expressions always use normal nested YAML. For example:

```yaml
achieved:
  all:
    - $.witdem.artifact_valid
    - equals:
        - $.route
        - answer
    - less_than_or_equal:
        - $.iterations
        - 3
```

The validator rejects unknown operators, malformed paths, incorrect list lengths, incomplete `choose` expressions, and expression mappings containing multiple operators.

## Complete examples

Each file is a complete configuration and is compiled by the test suite.

| Scenario | Contract | Expected returned facts |
| --- | --- | --- |
| Minimal boolean goal | [`01-boolean-goal.yaml`](contracts/01-boolean-goal.yaml) | `answer` |
| Multi-result classification | [`02-classification.yaml`](contracts/02-classification.yaml) | `profile`, `decision`, `expected_decision` |
| Numeric quality threshold | [`03-quality-threshold.yaml`](contracts/03-quality-threshold.yaml) | `answer`, `quality_score`, `quality_passed` |
| Multiple assurance checks | [`04-multiple-assurance-checks.yaml`](contracts/04-multiple-assurance-checks.yaml) | Facts passed to `Witdem.report(...)` |
| Routing and escalation | [`05-routing-and-escalation.yaml`](contracts/05-routing-and-escalation.yaml) | `route`, `expected_route`, `confidence` |
| Research approval loop | [`06-research-approval-loop.yaml`](contracts/06-research-approval-loop.yaml) | `report`, `editorial_decision`, `iteration_count` |
| RAG grounding and coverage | [`07-rag-grounding.yaml`](contracts/07-rag-grounding.yaml) | `answer`, `grounded`, evaluator scores, `documents` |
| Diagnostic metrics | [`08-diagnostic-metrics.yaml`](contracts/08-diagnostic-metrics.yaml) | `answer`, `iterations`, `tool_calls` |
| Hard rules with a loose chat outcome | [`09-chat-hard-and-loose.yaml`](contracts/09-chat-hard-and-loose.yaml) | `answer`, `safe`, `case_id` |

## Validate before running

```bash
witdem-sdk validate
witdem-sdk validate --config path/to/witdem.yaml
```

Errors identify the contract and field:

```text
invalid Witdem configuration at .witdem/witdem.yaml:
  - contracts: contracts.answer.artifact.valid: unknown expression operator 'nonempty'; did you mean 'non_empty'?
```

Validation confirms the grammar and internal consistency. It cannot prove that a runtime result will contain every configured path. Test at least one successful and one unsuccessful returned value in your application test suite.

## Review checklist

- The product goal describes user value, not runtime completion.
- `mode` is explicit.
- The artifact is the useful returned object.
- Decision correctness has an independent expectation when correctness matters.
- Every evaluation has one observed value and a declared target when applicable.
- Diagnostic metrics are not accidentally treated as success criteria.
- Result paths match the actual final return object.
- Hard requirements contain only rules that cannot be traded off.
- Semantic achievement and assurance thresholds express two different confidence bars.
- Chat contracts prefer `$.witdem.result` over framework-specific answer paths.
- `witdem-sdk validate` passes before deployment.
