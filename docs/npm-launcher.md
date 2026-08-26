# Running Witdem with npx

The npm package is a small launcher for the official Witdem container. It gives
Node-oriented projects a one-command local environment without duplicating the
Python server or installing Witdem into the application.

## Start

Install Docker Desktop or Docker Engine with the Compose plugin, then run:

```bash
npx -y witdem@0.2.0 up
```

The command resolves the matching `0.2.0` container, starts the receiver, ELT
worker, and dashboard, waits for both public endpoints, and opens the
dashboard. It does not use `postinstall`, start a host daemon, or edit the
current project.

| Service | Local address |
| --- | --- |
| Dashboard | `http://localhost:8501` |
| OTLP/SDK receiver | `http://localhost:4318` |

## Lifecycle

```bash
# Health and container state
npx -y witdem@0.2.0 status

# Follow service output
npx -y witdem@0.2.0 logs

# Run in the foreground during development
npx -y witdem@0.2.0 dev

# Stop all services
npx -y witdem@0.2.0 down
```

`down` preserves executions in the named `witdem-data` Docker volume. The
launcher intentionally has no data-deletion command.

## Ports and authentication

```bash
npx -y witdem@0.2.0 up \
  --dashboard-port 18501 \
  --receiver-port 14318 \
  --no-open
```

When receiver authentication is required, export `WITDEM_API_KEY` before
starting. The same value must be configured in the instrumented application.

## Versions and images

Use an explicit npm version in automation. Every published launcher defaults
to the exact same container version; the release workflow rejects mismatched
Python, npm, and container versions.

For a locally built or private image:

```bash
npx -y ./npm up --image witdem-analytics:dev
```

Docker reuses the exact local tag when it exists and pulls it when it is
missing.

Run `npx -y witdem@0.2.0 doctor` to check Node, Docker, Docker Compose, and the
Docker daemon. If startup fails, the launcher prints container state and recent
logs before exiting.
