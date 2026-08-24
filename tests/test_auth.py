from __future__ import annotations

import hmac

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from witdem.api import app
from witdem.auth import _unauthorized_limiter, require_api_key


def _request(authorization: str | None = None, *, client: str = "127.0.0.1") -> Request:
    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    return Request({"type": "http", "method": "POST", "path": "/v1/traces", "headers": headers, "client": (client, 1)})


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    _unauthorized_limiter.reset()


def test_trusted_mode_does_not_require_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WITDEM_API_KEY", raising=False)
    require_api_key(_request())


@pytest.mark.parametrize("authorization", [None, "", "Basic secret", "Bearer", "Bearer wrong"])
def test_missing_malformed_and_wrong_credentials_are_generic_401(
    monkeypatch: pytest.MonkeyPatch, authorization: str | None
) -> None:
    monkeypatch.setenv("WITDEM_API_KEY", "correct-secret")
    with pytest.raises(HTTPException) as captured:
        require_api_key(_request(authorization))
    assert captured.value.status_code == 401
    assert captured.value.detail == "unauthorized"
    assert "correct-secret" not in str(captured.value)


def test_valid_bearer_key_uses_constant_time_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WITDEM_API_KEY", "correct-secret")
    calls: list[tuple[str, str]] = []
    original_compare = hmac.compare_digest

    def compare(candidate: str, expected: str) -> bool:
        calls.append((candidate, expected))
        return original_compare(candidate, expected)

    monkeypatch.setattr("witdem.auth.hmac.compare_digest", compare)
    require_api_key(_request("Bearer correct-secret"))
    assert calls == [("correct-secret", "correct-secret")]


def test_repeated_unauthorized_requests_are_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WITDEM_API_KEY", "correct-secret")
    for _ in range(10):
        with pytest.raises(HTTPException) as captured:
            require_api_key(_request("Bearer wrong", client="203.0.113.10"))
        assert captured.value.status_code == 401
    with pytest.raises(HTTPException) as limited:
        require_api_key(_request("Bearer wrong", client="203.0.113.10"))
    assert limited.value.status_code == 429
    assert limited.value.headers == {"Retry-After": "60"}


def test_ingestion_routes_are_protected_but_liveness_remains_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WITDEM_API_KEY", "correct-secret")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.post("/v1/traces", content=b"").status_code == 401
    assert client.post("/sdk/v1/records", content=b"{}").status_code == 401
