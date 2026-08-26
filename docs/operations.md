# Operations and deployment

## Local lifecycle

The package-index release is pending. From a source checkout:

```bash
uv sync
uv run witdem dev --open
uv run witdem doctor
uv run witdem inspect
uv run witdem elt status
```

CLI flags override environment variables, which override defaults. Primary variables are `WITDEM_ENDPOINT`, `WITDEM_HOST`, `WITDEM_PORT`, `WITDEM_DASHBOARD_HOST`, `WITDEM_DASHBOARD_PORT`, `WITDEM_DATA_DIR`, `WITDEM_DB_PATH`, `WITDEM_PRICING_FILE`, and `WITDEM_API_KEY`.

## Docker

```bash
docker compose up -d
docker compose ps
docker compose logs -f witdem elt-worker dashboard
```

The stack runs the receiver, continuous Duckle worker, and dashboard over the named `witdem-analytics-live-data` volume.

Stop services without deleting the corpus:

```bash
docker compose down
```

Removing the named volume deletes the local corpus and is intentionally not part of normal shutdown instructions.

## ELT and retention

```bash
uv run witdem elt run
uv run witdem elt worker
uv run witdem elt status
uv run witdem prune --older-than 30d
uv run witdem prune --older-than 30d --yes
```

The first prune command is a preview. The `--yes` form permanently removes expired corpus data. Inspect the preview before confirmation.

## Pricing catalog

The bundled versioned catalog is [`src/witdem/pricing/catalog.yaml`](../src/witdem/pricing/catalog.yaml). It is refreshed through a scheduled, reviewable pull request; see [Pricing catalog](pricing.md). Point `WITDEM_PRICING_FILE` at another compatible catalog for models or negotiated rates not bundled with this release. An invalid override fails receiver readiness rather than silently disabling pricing.

## Security

Default Compose ports bind to `127.0.0.1`. Set `WITDEM_API_KEY` to require bearer authentication on OTLP and SDK ingestion. Never expose the Uvicorn services directly to the public internet.

For remote use, configure the `remote` Compose profile with separate ingestion/dashboard hostnames, Caddy TLS, ingestion bearer authentication, and dashboard Basic Auth:

```bash
docker compose --profile remote up -d
```

Only Caddy should expose public ports in that profile. See `.env.example` for the required variables.
