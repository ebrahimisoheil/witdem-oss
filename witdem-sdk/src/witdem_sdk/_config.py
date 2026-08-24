"""Environment-based configuration for :mod:`witdem_sdk`."""

from __future__ import annotations

import os

_ENDPOINT_ENV_VAR = "WITDEM_ENDPOINT"
_DEFAULT_ENDPOINT = "http://localhost:4318"
_endpoint_override: str | None = None


def configure_records_endpoint(endpoint: str | None) -> str | None:
    """Set the process-local endpoint override and return its previous value."""

    global _endpoint_override
    previous = _endpoint_override
    _endpoint_override = endpoint.rstrip("/") if endpoint else None
    return previous


def records_endpoint() -> str:
    """Base URL of the Witdem service.

    Records are POSTed to ``f"{records_endpoint()}/sdk/v1/records"``. Read
    fresh on every call (not cached at import time) so tests can repoint it
    per-test via ``monkeypatch.setenv`` without reloading this module.
    """

    if _endpoint_override is not None:
        return _endpoint_override
    value = os.environ.get(_ENDPOINT_ENV_VAR)
    if value:
        return value.rstrip("/")
    return _DEFAULT_ENDPOINT


def api_key(explicit: str | None = None) -> str | None:
    return explicit or os.getenv("WITDEM_API_KEY")
