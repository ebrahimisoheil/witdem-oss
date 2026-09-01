# Operations

## Installation paths

NPX manages a version-matched Docker Compose stack:

```bash
npx -y witdem@latest up
```

pipx installs the analytics platform into an isolated Python environment and
runs the receiver, ELT worker, and dashboard natively:

```bash
pipx install witdem-analytics
witdem up
```

`witdem-sdk` remains a dependency of each instrumented application. Installing
the backend with pipx does not install or modify application environments.

## Lifecycle

The command vocabulary is the same on both launchers:

```bash
witdem up [--open|--no-open]
witdem open
witdem status [--json]
witdem logs [--follow] [receiver|worker|dashboard]
witdem doctor
witdem version
witdem update --check [--refresh|--offline]
witdem down
witdem workflow compile [--check|--force]
witdem workflow rebuild
```

Prefix commands with `npx -y witdem@latest` instead of `witdem` for the Docker
path. `down` stops services and never deletes data. `dev` is a foreground mode
for contributors.

## Ports and isolated installations

Defaults are receiver `4318` and dashboard `8501`. Both paths support:

```bash
witdem up --receiver-port 14318 --dashboard-port 18501 --data-dir /srv/witdem/team-a
```

For NPX, `--data-dir` creates a bind-mounted installation and derives an
isolated Compose project name; `--project-name` can set it explicitly. For
pipx, the same option controls database, compiled manifests, run metadata,
logs, cache, and corpus paths.

## Data and process metadata

Without `--data-dir`, pipx uses the operating system's standard application
data directory for `witdem`. It stores:

```text
live.duckdb                 serving database
corpus/                     immutable accepted records
compiled/workflows/         disposable workflow manifests
run/services.json           validated PID, command, start token, ports, version
logs/{receiver,worker,dashboard}.log
cache/release-manifest.json last verified update manifest
```

NPX stores the same application data inside its persistent Compose volume.
`witdem status` validates both process identity and endpoint health; stale PID
files are never trusted merely because a PID exists.

## Backup and recovery

Stop services before a filesystem-level backup, then copy the entire data
directory or Docker volume. Preserve `corpus/`; databases, compiled manifests,
and workflow projections can be rebuilt.

```bash
witdem down
witdem workflow compile --force
witdem workflow rebuild
witdem up
```

`workflow rebuild` holds the maintenance lock, reprocesses committed corpus
batches, and replaces rebuildable projections without changing immutable
records. A corrupt compiled manifest is recovered automatically from YAML at
startup.

## ELT and retention

```bash
witdem elt status
witdem elt run
witdem prune --older-than 30d        # preview
witdem prune --older-than 30d --yes  # permanent corpus retention action
```

Retention is the only command above that deletes corpus data, and requires an
explicit target and confirmation.

## Ingestion reliability and backpressure

The receiver acknowledges SDK and OTLP requests only after their immutable
corpus data is durable. Concurrent writes pass through a bounded group-commit
queue so disk work cannot block the async request loop. When that queue remains
full, the receiver returns `503` with `Retry-After: 1`; clients should retry the
same idempotent event or span identity.

Receiver controls:

| Environment variable | Default | Purpose |
| --- | ---: | --- |
| `WITDEM_INGEST_QUEUE_SIZE` | `2048` | Maximum durable commits waiting for the writer |
| `WITDEM_INGEST_GROUP_SIZE` | `64` | Maximum commits published in one writer group |
| `WITDEM_INGEST_GROUP_WINDOW_MS` | `2` | Short collection window for concurrent commits |
| `WITDEM_INGEST_ENQUEUE_TIMEOUT` | `5` | Seconds to apply backpressure before returning `503` |

Increasing timeouts alone does not increase receiver throughput. Check receiver
logs and disk latency before increasing the queue. ELT remains outside the
acknowledgement path and cannot make an accepted batch disappear.

## Updates and offline operation

`update --check` detects and prints exact NPX, pipx, and SDK commands. It never
updates packages, containers, or data. Successful signed checks are cached for
24 hours. Use `--refresh` to bypass the cache, `--offline` to use only a
verified cache, or `WITDEM_UPDATE_CHECK=0` to disable automatic discovery.

See [Upgrade and compatibility](upgrade.md).

## Security

Default ports bind to `127.0.0.1`. Set `WITDEM_API_KEY` to require bearer
authentication on OTLP and SDK ingestion. Do not expose Uvicorn directly to
the public internet. The source-only remote Compose profile and proxy setup
are documented for operators in the repository's deployment configuration.
