"""Bounded, best-effort semantic record delivery."""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import BoundedSemaphore, Lock
from typing import Any

import httpx

from witdem_sdk._config import api_key, records_endpoint

logger = logging.getLogger("witdem_sdk")

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.1
_BACKOFF_MAX_SECONDS = 1.0
_QUEUE_WARNING_INTERVAL_SECONDS = 30.0
_QUEUE_SIZE = max(1, int(os.getenv("WITDEM_SDK_QUEUE_SIZE", "1000")))
_executor: Executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="witdem-sdk-send")
_capacity = BoundedSemaphore(_QUEUE_SIZE)
_pending: set[Future[None]] = set()
_lock = Lock()
_api_key_override: str | None = None
_submitted = 0
_sent = 0
_retried = 0
_dropped = 0
_last_error: str | None = None
_last_success_at: str | None = None
_last_queue_warning_at: float | None = None


@dataclass(frozen=True)
class DeliveryStatus:
    submitted: int
    sent: int
    retried: int
    dropped: int
    pending: int
    last_error: str | None
    last_success_at: str | None


def configure_api_key(value: str | None) -> str | None:
    global _api_key_override
    previous = _api_key_override
    _api_key_override = value
    return previous


def delivery_status() -> DeliveryStatus:
    with _lock:
        return DeliveryStatus(
            submitted=_submitted,
            sent=_sent,
            retried=_retried,
            dropped=_dropped,
            pending=len(_pending),
            last_error=_last_error,
            last_success_at=_last_success_at,
        )


def _nonnegative_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _request_timeout() -> float:
    return _nonnegative_env("WITDEM_SDK_REQUEST_TIMEOUT", 10.0)


def _queue_wait() -> float:
    return _nonnegative_env("WITDEM_SDK_QUEUE_WAIT", 0.1)


def submit_record(payload: Mapping[str, Any]) -> Future[None] | None:
    global _submitted, _dropped, _last_queue_warning_at
    with _lock:
        _submitted += 1
    queue_wait = _queue_wait()
    acquired = _capacity.acquire(timeout=queue_wait) if queue_wait > 0 else _capacity.acquire(blocking=False)
    if not acquired:
        now = time.monotonic()
        should_warn = False
        with _lock:
            _dropped += 1
            if _last_queue_warning_at is None or now - _last_queue_warning_at >= _QUEUE_WARNING_INTERVAL_SECONDS:
                _last_queue_warning_at = now
                should_warn = True
        if should_warn:
            logger.warning("witdem_sdk: delivery queue is full; dropping new telemetry records")
        return None
    try:
        future = _executor.submit(_send, dict(payload))
    except Exception:
        _capacity.release()
        with _lock:
            _dropped += 1
        logger.exception("witdem_sdk: could not submit record %s", payload.get("event_id"))
        return None
    with _lock:
        _pending.add(future)
    future.add_done_callback(_discard_pending)
    return future


def _discard_pending(future: Future[None]) -> None:
    with _lock:
        _pending.discard(future)
    _capacity.release()


def _flush_timeout(timeout: float | None) -> float:
    if timeout is not None:
        return max(0.0, float(timeout))
    try:
        return max(0.0, float(os.getenv("WITDEM_SDK_FLUSH_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def flush(timeout: float | None = None) -> bool:
    with _lock:
        futures = set(_pending)
    if not futures:
        return True
    _done, not_done = wait(futures, timeout=_flush_timeout(timeout))
    return not not_done


def _send(payload: dict[str, Any]) -> None:
    global _sent, _retried, _dropped, _last_error, _last_success_at
    url = f"{records_endpoint()}/sdk/v1/records"
    token = _api_key_override if _api_key_override is not None else api_key()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            request_kwargs: dict[str, Any] = {"json": payload, "timeout": _request_timeout()}
            if headers:
                request_kwargs["headers"] = headers
            response = httpx.post(url, **request_kwargs)
            response.raise_for_status()
            with _lock:
                _sent += 1
                _last_success_at = datetime.now(timezone.utc).isoformat()
            return
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if 400 <= exc.response.status_code < 500:
                break
        except httpx.HTTPError as exc:
            last_error = exc
        except Exception as exc:  # defensive: delivery never reaches application code
            last_error = exc
            break
        if attempt < _MAX_ATTEMPTS:
            with _lock:
                _retried += 1
            ceiling = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            time.sleep(random.uniform(0.0, ceiling))
    with _lock:
        _dropped += 1
        _last_error = str(last_error) if last_error else "unknown delivery error"
    logger.warning(
        "witdem_sdk: dropping record %s after delivery failure: %s",
        payload.get("event_id"),
        last_error,
    )
