# Architecture and data flow

The repository produces two Python distributions and one bundled web application:

| Component | Responsibility |
| --- | --- |
| `witdem-analytics` | OTLP/SDK ingestion, durable corpus, adapters, canonical analytics, API, dashboard, and CLI |
| `witdem-sdk` | Optional application-side semantic reporting and explicit framework integrations |
| `web` | React dashboard compiled into the analytics wheel and container image |

## Data flow

```text
OTLP or SDK request
  → immutable corpus commit and acknowledgement
  → Duckle batch selection
  → runtime adapter
  → provider adapter
  → canonical executions, operations, links, events, and semantic facts
  → serving.* projections
  → AnalyticsRepository
  → dashboard API, neutral evidence-bundle export, and React application
```

The corpus is authoritative. Canonical and serving projections are rebuildable. Ingestion acknowledges only after the original payload and decoded records are durable. Runtime/provider-specific data is normalized at adapter boundaries; provider-specific tables are not exposed to analytics consumers.

## Execution graph semantics

Canonical `Link` records distinguish span containment from workflow flow. A
framework integration may emit explicit active relationships such as Haystack
component/socket edges. Normalization resolves those relationships to concrete
operation instances and writes `workflow` or `workflow_retry` links with source
and destination socket metadata.

The replay API is the single topology source for both expanded and compact
dashboard views. The frontend lays explicit workflow links out as a DAG:
horizontal rank is workflow progression, fan-out targets occupy separate
lanes, fan-in targets converge at a shared rank, and retry instances remain
vertical beneath their owner. Compact phases contract only linear stretches;
they do not replace or reinterpret branch edges.

When explicit framework relationships are absent, retained historical runs use
the existing parentage and chronological fallback. Timestamp overlap can help
order that fallback, but it never overrides an explicit edge.

## Boundaries

- `src/witdem/ingest`: wire endpoints, correlation, durable corpus, and database publication.
- `src/witdem/elt`: pending-batch processing and rebuild/backfill commands.
- `src/witdem/integrations` and `src/witdem/adapters`: runtime/provider normalization.
- `src/witdem/analytics`: canonical models, SQL query catalog, contracts, and repository reads.
- `src/witdem/dashboard`: versioned read API and bundled static application.
- `witdem-sdk/src/witdem_sdk`: public application SDK; it does not import server internals.

The active dashboard is FastAPI plus React. The retired Streamlit implementation has been removed.

The evidence-bundle export reads canonical records and existing diagnostics
through `AnalyticsRepository`. It is a versioned read contract and does not
expose the immutable corpus layout or serving-table implementation details.

## Compatibility

| Analytics | SDK | Semantic protocol | Python |
| --- | --- | --- | --- |
| `>=0.2,<0.3` | `>=0.1,<0.3` | `1.0` | `>=3.10,<3.14` |

This table describes the currently declared release family, not a future
roadmap. Exact tested package pairs and framework constraints are published in
[`compatibility.json`](https://github.com/ebrahimisoheil/witdem-oss/blob/main/compatibility.json).

Compatibility aliases for old environment variables and raw-data migration remain deliberately isolated in configuration and ELT code. They protect existing installations and are not alternate product architectures.
