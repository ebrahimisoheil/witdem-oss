# Running Witdem with NPX

The npm package is a small, dependency-free launcher for the version-matched
Witdem container. It does not use `postinstall`, install a host daemon, or edit
the current project.

```bash
npx -y witdem@latest up
```

It starts the receiver, ELT worker, and dashboard, waits for health, and opens
`http://localhost:8501`. The receiver is at `http://localhost:4318`.

## Lifecycle

```bash
npx -y witdem@latest status
npx -y witdem@latest logs receiver
npx -y witdem@latest logs --follow worker
npx -y witdem@latest open
npx -y witdem@latest update --check
npx -y witdem@latest down
```

`down` preserves the named volume or explicit data directory. Use `--json` on
status and update checks for automation.

## Ports, data, and isolation

```bash
npx -y witdem@latest up \
  --dashboard-port 18501 \
  --receiver-port 14318 \
  --data-dir /srv/witdem/team-a \
  --project-name witdem-team-a \
  --no-open
```

Without `--data-dir`, Compose uses a project-scoped persistent named volume.
With it, all data is bind-mounted at the explicit path. Separate project names,
ports, and paths allow multiple installations and clean release tests without
touching shared data.

## Versions and images

For reproducible automation, quote an exact release:

```bash
npx -y "witdem@<version>" up
```

Every launcher defaults to its matching container. `--image` is available for
a locally built release candidate. Run `doctor` to validate Node, Docker,
Compose, and daemon access.
