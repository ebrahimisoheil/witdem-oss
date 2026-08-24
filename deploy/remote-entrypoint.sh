#!/bin/sh
set -eu

required="WITDEM_INGEST_HOST WITDEM_DASHBOARD_HOSTNAME WITDEM_TLS_EMAIL WITDEM_API_KEY WITDEM_DASHBOARD_USER WITDEM_DASHBOARD_PASSWORD_HASH"
for name in $required; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "remote profile requires $name" >&2
    exit 64
  fi
done

if [ "$WITDEM_INGEST_HOST" = "$WITDEM_DASHBOARD_HOSTNAME" ]; then
  echo "remote profile requires separate ingestion and dashboard hostnames" >&2
  exit 64
fi

case "$WITDEM_DASHBOARD_PASSWORD_HASH" in
  \$2a\$*|\$2b\$*|\$2y\$*) ;;
  *)
    echo "WITDEM_DASHBOARD_PASSWORD_HASH must be a Caddy-compatible bcrypt hash" >&2
    exit 64
    ;;
esac

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
