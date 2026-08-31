# Release notes

## 0.1.2

- Kept SDK and OTLP ingestion responsive during parallel durable writes and ordinary ELT processing.
- Added bounded grouped persistence, explicit retryable backpressure, and configurable SDK delivery deadlines.
- Added parallel durability and maintenance-contention regression coverage with no immutable-corpus changes.

## 0.1.1

- Rotated the production release-manifest signing key after the original private key was unavailable.
- Preserved the `0.1` protocol, workflow, corpus, and SDK compatibility contracts.

## 0.1.0

- Hardened package and container reproducibility with locked runtime constraints and pinned build tools.
- Made platform and SDK release identities tag-verified and immutable across PyPI, npm, and GHCR.
- Added cross-version SDK/server compatibility tests and dependency security automation.
- Assigned the shared `witdem` command exclusively to `witdem-analytics`; SDK-only commands use `witdem-sdk`.

## 0.2.0

- Added the raw-first immutable corpus and Duckle raw-to-serving worker.
- Added canonical serving projections and repository-backed dashboard reads.
- Bundled the React dashboard and versioned FastAPI dashboard API.
- Added OpenTelemetry-only and SDK-enriched integration paths.
- Added one-point SDK wrappers across LangGraph, LangChain, OpenAI Agents, Anthropic, Haystack, Claude Agent streams, and generic provider calls, with explicit result mapping and LangGraph 1.x support.
- Added explicit runtime/application/product-goal outcome semantics.
- Added managed pricing with unavailable-cost diagnostics.
- Added authenticated ingestion and an opt-in TLS remote deployment profile.
- Added runtime examples for OpenAI, Anthropic, LangChain, LangGraph, Haystack, Azure, Bedrock, Vertex, and Ollama.
- Consolidated overview reads and redesigned Compare, Workflows, Issues, and Runs pagination.
- Removed the retired Streamlit dashboard and reconciled repository documentation.
