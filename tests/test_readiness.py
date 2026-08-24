from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from witdem.api import app


def test_invalid_pricing_override_fails_readiness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(tmp_path / "live.duckdb"))
    override = tmp_path / "pricing.yaml"
    override.write_text("models: [{provider: openai, model: broken, input_per_million: -1}]", encoding="utf-8")
    monkeypatch.setenv("WITDEM_PRICING_FILE", str(override))

    response = TestClient(app).get("/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["pricing"].startswith("error:")


def test_valid_empty_pricing_override_is_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(tmp_path / "live.duckdb"))
    override = tmp_path / "pricing.yaml"
    override.write_text('schema_version: "1"\nmodels: []\n', encoding="utf-8")
    monkeypatch.setenv("WITDEM_PRICING_FILE", str(override))

    response = TestClient(app).get("/readiness")

    assert response.status_code == 200
    assert response.json()["checks"]["pricing"] == "ok"
