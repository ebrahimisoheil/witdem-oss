# YAML configuration

Witdem configuration version 2 has three files with three separate jobs:

- `witdem.yml` identifies the service and references definition files.
- A contract defines what a successful business result means.
- A workflow defines where operations appear in a replay.

Business facts are reported by application code. YAML never extracts values from framework-specific return objects and Witdem never generates a failure explanation.

## Project file

Store `witdem.yml`, `witdem.yaml`, or `.witdem/witdem.yaml` in the application repository:

```yaml
version: 2

service:
  name: contract-review
  description: Reviews commercial agreements.

telemetry:
  endpoint: http://localhost:4318
  mode: auto
  capture_content: false

contracts: [contracts/review.yml]
workflows: [workflows/review.yml]
```

`service` describes the application, not its framework or model provider. Runtime, provider, and model identity come from observed telemetry. Contract and workflow paths are relative to the project file.

When exactly one contract or workflow is referenced, it becomes the default. Use `default_contract` or `default_workflow` only when multiple definitions exist.

Validate all referenced files with:

```bash
witdem-sdk validate
```

Version 1 and inline definitions are intentionally unsupported.

## Contract file

A contract contains stable business vocabulary. It does not contain JSON paths, expressions, provider names, framework names, or executable rules.

```yaml
version: 2
id: review
name: Contract review

result:
  name: Review result
  values:
    approved: {description: The contract was approved, tone: success}
    manual_review: {description: A person must review it, tone: warning}
    rejected: {description: The contract was rejected, tone: failure}

goal:
  name: Produce an evidence-backed decision
  requirements:
    valid_analysis:
      name: The analysis is valid
      failure:
        label: The analysis was invalid
        investigate: {stage: validation}
    sufficient_evidence:
      name: Decision evidence is sufficient
      failure:
        label: Decision evidence was insufficient
        description: Review the evidence used for the final decision.
        investigate: {stage: decision}
```

Every goal has named requirements. Application code reports each requirement as `true`, `false`, or `null`:

- `true`: passed;
- `false`: failed;
- `null`: not known, so evidence is incomplete.

Witdem derives goal achievement and the closest blocker from these facts. The failure label, description, and investigation location are authored in the contract and included in the versioned contract definition. `investigate` is a starting point, not a claim that an operation failed.

Optional contract sections are:

- `decision`: allowed business decisions;
- `evaluations`: named measurements with optional target, direction, and unit;
- `metrics`: named quantities and units;
- `dimensions`: allowed analysis dimensions.

Allowed tones are `success`, `warning`, `failure`, and `neutral`. A tone affects presentation only.

## Reporting facts

```python
witdem.report(
    contract="review",
    result="manual_review",
    result_valid=True,
    requirements={
        "valid_analysis": True,
        "sufficient_evidence": False,
    },
    evaluations={"evidence_coverage": 0.65},
)
```

The reported requirement keys must exactly match the contract. This prevents competing client-side and server-side definitions of success.

## Workflow file

A workflow is an optional, vendor-neutral projection of observed operations:

```yaml
version: 2
id: contract-review
name: Contract review

match:
  service_names: [contract-review]

stages:
  - id: evidence
    name: Evidence
    nodes:
      - id: retrieve
        name: Retrieve clauses
        match:
          names: [contract.keyword_retrieval]

  - id: decision
    name: Decision
    depends_on: [evidence]
    nodes:
      - id: decide
        name: Apply policy
        match:
          names: [contract.policy_decision]
        depends_on: [retrieve]
```

Workflow YAML describes stages, nodes, dependencies, retries, fallbacks, matching, and possible outcomes. It does not determine business success. Provider- and framework-specific details remain telemetry attributes rather than workflow schema fields.

## Discovery and overrides

The SDK searches the current directory and its parents for `witdem.yml`, `witdem.yaml`, then `.witdem/witdem.yaml`. `WITDEM_CONFIG` or `config_path=` selects an explicit file. `WITDEM_ENDPOINT` overrides `telemetry.endpoint`.

Telemetry modes are `auto`, `existing`, and `disabled`. Content capture defaults to `false`.
