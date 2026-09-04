# Define business success

Witdem v2 separates configuration from business meaning. The project file points to a contract; the contract names the facts that the application reports.

## 1. Create the project

```bash
witdem-sdk init
```

This creates:

```text
.witdem/
├── witdem.yaml
├── contracts/
│   └── application-run.yml
└── skills/
    └── witdem/
        ├── SKILL.md
        └── agents/
            └── openai.yaml
```

The generated skill gives coding agents the same minimal v2 modeling rules.
Pass `--expose-agent-skill` during initialization to link it from
`.agents/skills/witdem` without creating a second source of truth.

## 2. Name the result

Declare only values your application can actually report:

```yaml
result:
  name: Support result
  values:
    answered: A useful answer was returned.
    escalated: Human support was requested.
    unresolved: The request was not resolved.
```

## 3. Name the goal requirements

Each requirement is independently observable. Its failure text is the exact explanation shown during investigation.

```yaml
goal:
  name: Correct support resolution
  requirements:
    useful_answer:
      name: The answer is useful
      failure:
        label: The answer did not resolve the request
        investigate: {stage: answer}
    correct_route:
      name: The expected route was selected
      failure:
        label: The selected support route was incorrect
        investigate: {stage: routing}
```

Do not describe a technical crash here. Runtime failures already come from telemetry. A requirement describes why the business goal was not achieved.

## 4. Report the facts

```python
witdem.report(
    result="escalated",
    requirements={
        "useful_answer": True,
        "correct_route": False,
    },
)
```

Witdem computes `product_goal_achieved=false`, selects `correct_route` as the closest blocker, and uses only its authored diagnostic metadata. A `null` requirement means the fact is unknown and marks the evidence as insufficient.

## 5. Add assurance measurements when needed

```yaml
evaluations:
  evidence_coverage:
    name: Evidence coverage
    unit: ratio
    target: 0.8
    direction: higher_is_better
```

```python
witdem.report(
    result="answered",
    requirements={"useful_answer": True, "correct_route": True},
    evaluations={"evidence_coverage": 0.91},
)
```

Evaluations measure quality. They do not silently redefine the goal. If an evaluation is mandatory, represent that business rule as a named goal requirement too.

## Complete examples

- [Minimal answer](contracts/minimal-answer.yml)
- [Classification decision](contracts/classification.yml)
- [Assured document review](contracts/assured-review.yml)

The [CUAD integration](https://github.com/ebrahimisoheil/witdem-oss/tree/main/examples/integrations/cuad) combines a v2 contract and workflow with deterministic and live provider execution.
