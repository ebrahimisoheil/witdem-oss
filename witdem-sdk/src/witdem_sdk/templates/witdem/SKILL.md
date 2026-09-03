---
name: witdem
description: Add or extend basic Witdem OSS instrumentation in an application. Use when a coding agent needs to initialize or edit .witdem/witdem.yaml, define a v2 business contract, instrument an execution, report application results and goal requirements, or validate a Witdem project without inventing business semantics.
---

# Implement with Witdem

Keep Witdem configuration small and application-owned. Describe facts the application can report; do not infer business success from a completed model call.

## Start safely

1. Inspect `.witdem/witdem.yaml` and every referenced contract before editing.
2. Run `witdem-sdk init` only when no Witdem project exists. Never replace an existing project unless the user explicitly requests `--force`.
3. Keep `version: 2`. Do not introduce legacy fields or a second definition of executions, evaluations, outcomes, or workflow topology.
4. Keep `telemetry.capture_content: false` unless the user explicitly accepts content capture.

## Define the YAML

Use `.witdem/witdem.yaml` only as the project index:

- `service` identifies the observed application.
- `telemetry` controls delivery and content capture.
- `contracts` lists business-contract files relative to `.witdem/`.
- `workflows` is optional and lists authored replay topology when the application needs it.

In each contract:

- Give `result.values` only values the application can explicitly report.
- Make `goal.requirements` independently observable business checks.
- Give each requirement a clear `name` and concise `failure.label`.
- Use `failure.investigate` only to link a requirement to an existing authored workflow stage or node.
- Use `evaluations` for measurements such as quality or evidence coverage. Do not let a measurement silently redefine the product goal.
- Use `dimensions` only for stable comparison fields the application will report.

Do not duplicate contract meaning in a workflow file. Contracts define business success; workflows define optional replay structure.

## Instrument once

Prefer the public integration for the application's framework. If no integration owns the execution boundary, use the public SDK directly:

```python
from witdem_sdk import configure

with configure() as witdem:
    with witdem.execution("Application run"):
        result = run_application()
        witdem.report(
            contract="application_run",
            result="completed",
            requirements={"useful_result": True},
            dimensions={"request_type": "example"},
        )
```

Report requirement values as `true`, `false`, or `null`. `null` means unknown; never convert missing evidence into success. Keep client code and server analytics from becoming competing sources of truth.

## Verify

1. Run `witdem-sdk validate` from the application root.
2. Run the application's existing deterministic tests.
3. Confirm reported contract, result, requirement, and dimension identifiers exactly match the YAML.
4. Confirm one application run creates one execution boundary and that existing framework orchestration remains unchanged.
5. State which evidence remains unknown or unavailable; do not claim completeness when instrumentation can drop or omit data.
