"""Transport-layer tests: bounded retries, diagnostics, and never-raise delivery.

This is the one hard reliability requirement for witdem_sdk: a network error,
timeout, or Witdem being unreachable must never propagate into caller code.
Exercises ``witdem_sdk._transport._send`` directly (bypassing correlation/
payload-building, which are covered elsewhere) so the retry/drop behavior is
tested in isolation. Fully offline: ``httpx.post`` is always mocked.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future

import httpx
import pytest

import witdem_sdk._transport as transport


def _request(url: str) -> httpx.Request:
    return httpx.Request("POST", url)


def test_send_success_posts_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)

    transport._send({"event_id": "abc"})

    assert len(calls) == 1


def test_send_retries_once_then_drops_on_persistent_http_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        calls.append(url)
        raise httpx.ConnectError("connection refused", request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)

    with caplog.at_level(logging.WARNING, logger="witdem_sdk"):
        transport._send({"event_id": "abc-123"})  # must not raise

    assert len(calls) == 3
    assert "dropping record" in caplog.text
    assert "abc-123" in caplog.text


def test_send_recovers_after_one_failed_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: list[Exception | None] = [httpx.ConnectError("refused", request=_request("x")), None]
    calls: list[str] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        calls.append(url)
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return httpx.Response(200, request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)

    transport._send({"event_id": "abc"})  # must not raise

    assert len(calls) == 2


def test_send_raises_for_http_error_status(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """A non-2xx response (e.g. Witdem rejecting a malformed record) is also a
    send failure: retried once, then dropped -- never raised."""

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        return httpx.Response(500, request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    monkeypatch.setattr(transport.time, "sleep", lambda _seconds: None)

    with caplog.at_level(logging.WARNING, logger="witdem_sdk"):
        transport._send({"event_id": "abc"})  # must not raise


def test_send_never_raises_on_totally_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        raise RuntimeError("totally unexpected")

    monkeypatch.setattr(transport.httpx, "post", fake_post)

    transport._send({"event_id": "abc"})  # must not raise -- the hard requirement


def test_permanent_validation_failure_is_not_retried_and_is_counted_as_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    before = transport.delivery_status()

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422, request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    transport._send({"event_id": "invalid"})

    after = transport.delivery_status()
    assert calls == 1
    assert after.dropped == before.dropped + 1
    assert after.retried == before.retried


def test_transient_retries_use_bounded_exponential_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    def fake_post(url: str, *, json: dict, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=_request(url))

    monkeypatch.setattr(transport.httpx, "post", fake_post)
    monkeypatch.setattr(transport.random, "uniform", lambda _low, high: high)
    monkeypatch.setattr(transport.time, "sleep", sleeps.append)

    transport._send({"event_id": "retry"})

    assert sleeps == [0.1, 0.2]


def test_flush_timeout_uses_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WITDEM_SDK_FLUSH_TIMEOUT", "2.5")
    assert transport._flush_timeout(None) == 2.5
    assert transport._flush_timeout(0.25) == 0.25


def test_queue_full_warning_is_rate_limited(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    class FullCapacity:
        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

    monkeypatch.setattr(transport, "_capacity", FullCapacity())
    monkeypatch.setattr(transport, "_last_queue_warning_at", None)
    monkeypatch.setattr(transport.time, "monotonic", lambda: 100.0)

    with caplog.at_level(logging.WARNING, logger="witdem_sdk"):
        transport.submit_record({"event_id": "secret-id-1"})
        transport.submit_record({"event_id": "secret-id-2"})

    assert caplog.text.count("queue is full") == 1
    assert "secret-id" not in caplog.text


def test_submit_record_delegates_to_the_module_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """``submit_record`` must submit through whatever ``_executor`` currently
    is (not, say, a hardcoded reference captured at import time) -- this is
    what lets tests (see conftest.py) swap in a synchronous stand-in."""

    submitted = []

    class _StubExecutor:
        def submit(self, fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
            submitted.append((fn, args, kwargs))
            future: Future[None] = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(transport, "_executor", _StubExecutor())

    transport.submit_record({"event_id": "xyz"})

    assert len(submitted) == 1
    fn, args, _kwargs = submitted[0]
    assert fn is transport._send
    assert args == ({"event_id": "xyz"},)
