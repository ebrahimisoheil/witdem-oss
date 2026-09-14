# AI Rules

## Tech stack

- The repository contains a Python 3.10–3.13 backend/package named `witdem-analytics`, built with Hatch and managed with `uv`.
- The backend exposes HTTP APIs with FastAPI and runs with Uvicorn.
- Backend data storage and analytics use DuckDB through `duckle`; YAML is used for configuration, contracts, and pricing data.
- Backend data models and request/response validation use Pydantic v2.
- Observability is built around OpenTelemetry APIs, OTLP HTTP export, and the project’s runtime analytics modules.
- The dashboard is a React 18 + TypeScript application in `web/`, bundled with Vite and run on Node.js 22.
- The dashboard uses TanStack Router for client-side routing and TanStack Query for server-state fetching, caching, retries, and refetching.
- Dashboard visualizations use Apache ECharts via `echarts-for-react`; graph layouts and network diagrams use Cytoscape and Dagre.
- Dashboard styling uses Tailwind CSS with PostCSS; UI primitives and shared visual components are maintained in `web/src/components.tsx`.
- Testing uses Pytest for Python and Vitest for the web application; Ruff and mypy enforce Python quality and type checking.

## Library and architecture rules

- Keep backend production code under `src/witdem/`; put backend tests under `tests/` and runnable examples under `examples/`.
- Keep dashboard code under `web/src/`; use TypeScript and preserve the existing Vite entry point in `web/src/main.tsx`.
- Define and update dashboard routes in `web/src/main.tsx` using TanStack Router. Use lazy route components for page-level code splitting.
- Use TanStack Query for all dashboard API/server state. Do not add ad hoc `fetch` calls inside page components when the API client and query hooks can be used.
- Keep HTTP API definitions and request calls centralized in `web/src/api.ts`; keep rendering and interaction logic in page/component modules.
- Use TanStack Table for tabular data and ECharts for charts. Use Cytoscape or Dagre only for graph/network visualization and layout problems.
- Use Tailwind CSS and the existing shared components for dashboard styling. Avoid introducing another CSS framework or one-off styling system.
- Use React state and hooks for local UI state; do not introduce a global state library unless the requirement cannot be handled with React state and TanStack Query.
- Use FastAPI for HTTP boundaries, Pydantic for validation/schema models, and `httpx` for outbound HTTP calls. Keep domain and analytics logic out of route handlers.
- Use DuckDB/`duckle` for analytical persistence and SQL query work. Keep reusable SQL in the existing analytics query directories rather than embedding large queries in Python.
- Use OpenTelemetry for tracing and telemetry interoperability; do not create a parallel custom telemetry protocol for supported signals.
- Use Pytest for Python tests, Vitest for web tests, Ruff for Python linting/import sorting, and mypy for Python static typing. Keep tests deterministic and label integration/e2e tests with the project’s configured markers.
- Prefer existing dependencies and shared utilities. Add a new library only when the existing stack cannot reasonably solve the problem, and update the appropriate lockfile/package manifest when doing so.
- Never commit secrets, credentials, generated build assets, or local environment files. Validate external input at API and file boundaries, and preserve existing security and error-handling conventions.
