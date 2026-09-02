# Release notes

## 0.2.4

- Added concrete OpenAPI response schemas for every documented dashboard endpoint.
- Documented run summaries, execution graphs, operation identity, measurements, workflows, evaluations, and issue responses while preserving additive fields.
- Grouped Swagger operations by system, metadata, analytics, runs, workflows, and evaluations for easier API discovery.

## 0.2.3

- Added native MCP protocol normalization for connection initialization, capability discovery, resource reads, prompt retrieval, and tool execution.
- Preserved MCP as the invocation interface while retaining the semantic operation family, type, role, provider, and nested downstream work.
- Counted canonical MCP tool executions in run summaries even when telemetry arrives as generic OpenTelemetry operations.

## 0.2.2

- Separated execution containers, control-plane orchestration, and work-plane operations so workflow roots no longer appear as unknown AI operations.
- Added explicit model applicability, participant deduplication, and linked child activity to operation profiles.
- Added safe taxonomy reprocessing from immutable raw telemetry with `witdem taxonomy reprocess`.
- Reclassified the preserved CUAD stress dataset with taxonomy v2 and verified direct Anthropic and OpenAI SDK paths alongside framework-emitted operations.

## 0.2.1

- Added an extensible, versioned AI operation taxonomy that keeps operation type separate from interface, provider, implementation, framework, and role.
- Added first-class direct OpenAI SDK instrumentation for Responses, Chat Completions, embeddings, synchronous and asynchronous calls, streaming usage, and tool-call identifiers.
- Preserved nested provider operations inside LangGraph nodes and expanded canonical measurements across retrieval, tools, quality, media, memory, human work, orchestration, and custom operations.
- Verified direct Anthropic and OpenAI SDK calls, with and without LangGraph, against the CUAD contract-review workload using live provider APIs.
- Updated the dashboard to display unfamiliar operation families and types without requiring provider-specific UI changes.

## 0.1.4

- Fixed the worker container's false-negative health status under DuckDB lock contention.
- Added an explicit lock-free process-liveness probe for workers in local and NPX Compose deployments.
- Replaced a machine-speed timing assertion with a deterministic event-loop concurrency check.

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
