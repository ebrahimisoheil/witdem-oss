# Upgrade and compatibility

## Check first

```bash
npx -y witdem@stable-0-1 update --check
witdem update --check
```

The command verifies Witdem's signed release manifest, reports current and
latest component versions, and warns separately about protocol incompatibility.
It is detection and guidance only.

## Apply an upgrade

Use the exact versions printed by the checker. The command forms are:

```bash
# NPX / Docker backend
npx -y "witdem@<version>" up

# pipx / native backend
pipx install --force "witdem-analytics==<version>"

# each instrumented application; preserve its integration extra
python -m pip install --upgrade "witdem-sdk[haystack]==<sdk-version>"
```

Upgrade the backend first, verify `status`, then roll out the compatible SDK to
applications. A newer package is not automatically compatible merely because
it is latest; protocol warnings take precedence.

## Data safety

Normal `up`, `down`, and package upgrades preserve the data directory or Docker
volume. Before a major upgrade:

1. Run `down`.
2. Back up the complete data directory/volume.
3. Upgrade and run `workflow compile --check`.
4. Run `workflow rebuild` when the checker reports a projector/schema change.
5. Start and verify the dashboard and a known execution.

Immutable corpus records are never modified by workflow compilation or
projection rebuilds. Historical executions retain their original template
hash.

## Release manifest

The manifest is published only after the wheel, npm launcher, SDK, and
container are publicly available. It identifies protocol, workflow schema,
compiler, projector, minimum compatible versions, artifacts, publication time,
and release notes. Ed25519 verification uses an embedded public key. A network
failure or invalid signature never blocks startup and never replaces the last
verified cache.
