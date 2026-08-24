"""Optional trusted-mode/shared-key authentication."""

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException, Request

_UNAUTHORIZED_LIMIT = 10
_UNAUTHORIZED_WINDOW_SECONDS = 60.0


class _UnauthorizedRateLimiter:
    """Small process-local limiter for repeated failed ingestion authentication."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def record_failure(self, client: str) -> bool:
        now = self._clock()
        cutoff = now - _UNAUTHORIZED_WINDOW_SECONDS
        with self._lock:
            failures = self._failures[client]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            failures.append(now)
            return len(failures) <= _UNAUTHORIZED_LIMIT

    def clear(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


_unauthorized_limiter = _UnauthorizedRateLimiter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def require_api_key(request: Request) -> None:
    expected = os.getenv("WITDEM_API_KEY")
    if not expected:
        return
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    client = _client_key(request)
    if scheme.casefold() == "bearer" and token and hmac.compare_digest(token, expected):
        _unauthorized_limiter.clear(client)
        return
    if not _unauthorized_limiter.record_failure(client):
        raise HTTPException(status_code=429, detail="too many unauthorized requests", headers={"Retry-After": "60"})
    raise HTTPException(status_code=401, detail="unauthorized")
