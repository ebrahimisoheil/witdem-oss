# API reference

Witdem publishes three public integration surfaces. Choose the one that matches what you are building.

| Surface | Use it to | Contract source |
| --- | --- | --- |
| [Python SDK](python-sdk.md) | Instrument application code and report events, decisions, evaluations, outcomes, and metrics | Public `witdem_sdk` exports |
| [Ingestion API](ingestion.md) | Send OTLP traces or SDK semantic records and inspect ingestion status | Receiver FastAPI application |
| [Analytics API](analytics.md) | Read dashboard-ready runs, workflows, issues, comparisons, and evidence | Dashboard FastAPI application |

The HTTP references are generated from Witdem's versioned OpenAPI schemas during the documentation build. They describe the installed OSS version and are not separate handwritten contracts.

!!! note "Deployment addresses"

    A default local installation serves ingestion on `http://localhost:4318` and analytics on `http://localhost:8501`. A self-hosted deployment may expose different origins. The paths and schemas remain the same.
