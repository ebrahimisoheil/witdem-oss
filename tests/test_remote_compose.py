from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_remote_profile_keeps_ingestion_and_dashboard_behind_tls_proxy() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    proxy = services["remote-proxy"]

    assert proxy["profiles"] == ["remote"]
    assert proxy["depends_on"]["witdem"]["condition"] == "service_healthy"
    assert proxy["depends_on"]["dashboard"]["condition"] == "service_healthy"
    assert services["witdem"]["ports"] == ["127.0.0.1:4318:4318"]
    assert services["dashboard"]["ports"] == ["127.0.0.1:8501:8501"]
    assert services["witdem"]["environment"]["WITDEM_API_KEY"] == "${WITDEM_API_KEY:-}"


def test_remote_profile_preflight_rejects_missing_configuration() -> None:
    script = ROOT / "deploy" / "remote-entrypoint.sh"
    completed = subprocess.run(
        ["sh", str(script)],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 64
    assert "requires WITDEM_INGEST_HOST" in completed.stderr


def test_caddy_routes_use_separate_hosts_and_dashboard_authentication() -> None:
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "{$WITDEM_INGEST_HOST}" in caddyfile
    assert "reverse_proxy witdem:4318" in caddyfile
    assert "{$WITDEM_DASHBOARD_HOSTNAME}" in caddyfile
    assert "basic_auth" in caddyfile
    assert "reverse_proxy dashboard:8501" in caddyfile
