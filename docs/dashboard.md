# Dashboard and analytics semantics

The dashboard is a React single-page application served by FastAPI. Its versioned read API lives under `/api/v1`; OpenAPI documentation is available at `/api/docs`.

## Views

- **Overview:** runtime health, product-goal performance, economics, trends, and breakdowns.
- **Runs:** ten newest filtered runs per server-paginated page and a replay for each execution.
- **Compare:** attributable provider/model time, cost, tokens, calls, quality, and latency distribution.
- **Workflows:** canonical runtime portfolio, semantic-stage contribution, and compact path variants.
- **Issues:** run-linked failures, retries, evaluation gaps, outliers, and measurement coverage.
- **Developer data:** API and raw technical access without crowding decision views.

## Metric rules

- Runtime completion, terminal failure, running state, and recovery are distinct.
- Application outcomes such as accepted, rejected, or escalated are neutral labels, not success/failure proxies.
- Product-goal success and decision correctness are calculated only when explicitly reported.
- Model/provider comparison attributes only their observed model calls, elapsed model time, tokens, and cost; it does not charge a mixed run in full to every participant.
- Cost totals include measured/calculated values only. Coverage is always shown separately; unavailable values are never silently zero-filled.
- p50 and p95 are computed over observed per-run values in the selected population.
- Failures, retries, quality gaps, and outliers link to their exact execution replay.

## Read architecture

Dashboard service functions depend on `AnalyticsRepository`, not DuckDB or SQL. The Overview endpoint uses one coherent snapshot read and memoizes shared filtered populations inside a single repository session. Other insight pages use dedicated coherent repository operations.
