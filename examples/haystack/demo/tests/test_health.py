from __future__ import annotations

from fastapi.testclient import TestClient

from agent_demo.api import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
