# Contributing to Witdem

Thank you for helping improve Witdem. Contributions are welcome across the
analytics backend, SDK integrations, dashboard, documentation, and examples.

## Before you start

- Search existing issues and pull requests before starting overlapping work.
- Open an issue first for large features, protocol changes, new persisted data,
  or changes to analytics meaning.
- Keep pull requests focused. Unrelated refactors make behavior harder to verify.
- Never commit provider credentials, `.env` files, local DuckDB data, generated
  environments, or user telemetry.

## Development setup

You need Python 3.10–3.13, [uv](https://docs.astral.sh/uv/), Node.js 20 or newer,
and Docker with Compose.

```bash
git clone https://github.com/ebrahimisoheil/witdem-oss.git
cd witdem-oss
uv sync --all-groups
cd web && npm ci && cd ..
```

To run the complete local stack from the checkout:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The receiver is available at `http://localhost:4318` and the dashboard at
`http://localhost:8501`.

## Make and verify a change

Run the checks that cover the area you changed. Before requesting review, the
full baseline is:

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src

cd witdem-sdk
uv sync --all-groups --all-extras
uv run pytest -q
uv run ruff check src tests
uv run mypy src

cd ../web
npm test
npm run build
```

Add tests for behavior changes. Analytics changes must preserve the distinction
between operational health, reported business outcomes, and declared assurance
checks. SDK integration changes should cover synchronous and asynchronous paths
when the target framework supports both.

## Documentation and examples

- Update the relevant guide when behavior, configuration, or compatibility changes.
- Use proper YAML syntax in contracts; do not embed JSON objects inside YAML strings.
- Keep examples runnable and avoid hidden application-specific reporting logic when
  the same meaning can be declared in `.witdem/witdem.yaml`.
- Never make up provider cost. Unknown or unsupported pricing remains unmeasured.

## Pull requests

A good pull request includes:

- A clear problem statement and the intended outcome.
- A concise description of the implementation.
- Test evidence and, for visual changes, screenshots.
- Compatibility or migration notes when wire formats, stored data, or public APIs change.
- Documentation updates for user-visible behavior.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE).
