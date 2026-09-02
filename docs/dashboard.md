# Dashboard

The dashboard is a React application served by FastAPI at `http://localhost:8501`. It reads transformed analytics from DuckDB; it does not query a provider or agent application directly.

## Overview — are my agents achieving the right goals?

Overview is the all-goals command center. It does not require choosing a business contract. It balances business-goal achievement and assurance with runtime completion, breakpoints, cost coverage, and separate model/provider breakdowns for goal outcomes and operational reliability. Selecting a goal, model, or provider opens the appropriate detail page with that filter applied.

The goal portfolio separates four states: assured achievement, achievement with a below-target check, not achieved, and achieved but unassessed. Contract versions with the same logical goal are aggregated while their contract count remains visible.

An application can have 100% runtime completion and 0% product-goal success. That is expected when the code ran but the YAML-defined goal was not achieved.

## System Health — is the workflow operating reliably?

System Health contains execution volume, completion, failures, recovery, latency, measured cost and coverage, workflow reliability, and stage accumulation. These operational signals are deliberately separate from business success.

## Goal Performance — how strong is the evidence for each goal?

Goal Performance supports aggregate and contract-specific analysis. “Assurance” is not a calibrated probability: it means the product goal was achieved and every reported check declared by that contract met its target. A goal may therefore be achieved while still needing attention.

## Runs — what happened in this execution?

Runs shows ten newest filtered executions per server-paginated page. Open a run to see:

- the execution path that actually ran;
- branches, concurrent siblings, loops, models, tools, and timings;
- runtime status and failure evidence;
- application result, decision, product goal, grouped contract checks, and measurements;
- a selectable evidence inspector showing each check's observed value, target, direction, and reported diagnostics;
- measured cost and token evidence.

The replay is not a static framework diagram. Configured nodes that never ran are absent.

## Compare — which provider/model/configuration fits the goal?

Compare uses only model activity attributable to the selected participant. It shows speed/spend, relative resource trade-offs, contract-defined quality, and p50/p95 latency.

Compare a consistent business contract and inspect sample size before choosing a model. A run containing multiple providers is not charged in full to each participant.

## Workflows — which paths carry the work?

Workflows summarizes canonical runtimes, semantic-stage contribution, and compact observed path variants. It removes adjacent repetition and low-level wrapper/model noise from the portfolio view while the individual run replay retains technical evidence.

## Issues — where should I investigate?

Issues links terminal/recovered failures, retry hotspots, below-target evaluations, and slow/expensive/token-heavy outliers to exact runs. Measurement coverage remains visible so missing tokens or cost are not mistaken for zero.

## Developer data

Developer data links to the versioned read API. OpenAPI documentation is at `http://localhost:8501/api/docs`; dashboard endpoints live under `/api/v1`.

The dashboard and read API do not use `WITDEM_API_KEY`; that key protects
ingestion only. Keep the dashboard on loopback or behind an authenticated
reverse proxy. See [Operations](operations.md#security).

## Metric rules

- Runtime completion, failure, running state, and recovery are separate.
- Application result labels are contract-owned categories, not built-in success/failure meanings.
- Product-goal success and decision correctness require explicit application semantics.
- Missing cost/tokens are unavailable, never silently zero-filled.
- Provider-reported cost wins; catalog calculation requires provider, model, usage, and an exact catalog match.
- p50 and p95 are computed from observed per-run values in the selected population.
