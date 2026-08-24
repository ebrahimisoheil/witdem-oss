# Operations and deployment

## Local lifecycle

```bash
pip install witdem-analytics
witdem dev
witdem doctor
witdem inspect
witdem elt status
```

CLI arguments override environment variables, which override defaults. Primary variables are `WITDEM_ENDPOINT`, `WITDEM_HOST`, `WITDEM_PORT`, `WITDEM_DASHBOARD_HOST`, `WITDEM_DASHBOARD_PORT`, `WITDEM_DATA_DIR`, `WITDEM_DB_PATH`, `WITDEM_PRICING_FILE`, and `WITDEM_API_KEY`.

## Docker

```bash
docker compose up -d
```

The stack runs receiver, Duckle worker, and dashboard services over the `witdem-live-data` volume. The dashboard image compiles the frontend and bundles it in the Python package.

## ELT and retention

```bash
witdem elt run
witdem elt worker
witdem elt status
witdem elt rebuild
witdem prune --older-than 30d
witdem prune --older-than 30d --yes
```

The first prune command is a preview. The `--yes` form permanently removes expired corpus batches and rebuilds retained projections. Older JSONL/raw layouts can be imported explicitly through the migration/backfill commands; sources are never moved automatically.

## Security

Default deployments are for localhost or a trusted private network. Set `WITDEM_API_KEY` to require bearer authentication on OTLP and SDK ingestion. Never expose the Uvicorn services directly to the public internet.

For remote use, configure the Compose `remote` profile with separate ingestion/dashboard hostnames, Caddy TLS, ingestion bearer authentication, and dashboard Basic Auth:

```bash
docker compose --profile remote up -d
```

Only Caddy exposes public ports in that profile.
