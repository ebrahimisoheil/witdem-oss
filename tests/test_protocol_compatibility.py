from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from witdem.api import app
from witdem.protocol import SEMANTIC_RECORD_PROTOCOL_VERSION

FIXTURE = Path(__file__).with_name("fixtures") / "sdk-wire-v1-oldest.json"


def test_oldest_supported_sdk_v1_fixture_is_accepted_by_current_server(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(tmp_path / "live.duckdb"))
    monkeypatch.delenv("WITDEM_API_KEY", raising=False)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    response = TestClient(app).post("/sdk/v1/records", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["kind"] == "outcome"


def test_published_compatibility_metadata_matches_server_protocol() -> None:
    root = Path(__file__).parents[1]
    metadata = json.loads((root / "compatibility.json").read_text(encoding="utf-8"))
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    assert metadata["semantic_record_protocol"] == SEMANTIC_RECORD_PROTOCOL_VERSION
    assert metadata["semantic_record_protocol"] == release["semantic_record_protocol"]
    assert metadata["python"] == ">=3.10,<3.14"
    assert metadata["tested_pairs"]
    assert all(
        pair["semantic_record_protocol"] == SEMANTIC_RECORD_PROTOCOL_VERSION
        for pair in metadata["tested_pairs"]
    )
