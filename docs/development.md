# Development

## Install and verify

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest -q
cd web && npm ci && npm run build
```

The SDK is independently testable from `witdem-sdk`. Examples have isolated environments and should not be installed into the product runtime.

## Repository rules

- Product Python code lives under `src/witdem`; public SDK code under `witdem-sdk/src/witdem_sdk`; dashboard source under `web/src`.
- Analytics consumers use repository contracts, not direct SQL/database connections.
- SQL owned by analytics lives under `src/witdem/analytics/queries`; query names are loaded through `sql_loader.py`.
- Generated environments, dependency installs, caches, build artifacts, reports, and local databases are not source-controlled.
- Markdown documentation lives only under `docs`.
- Compatibility code must have an active migration/protocol purpose and tests; retired product implementations are removed rather than kept as alternate paths.
