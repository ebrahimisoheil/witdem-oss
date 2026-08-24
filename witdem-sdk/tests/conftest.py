"""Shared fixtures for witdem_sdk tests.

Two autouse fixtures make every test deterministic, isolated, and offline:

- ``_reset_otel_context`` attaches a brand-new, empty OTel ``Context`` before
  each test (and detaches it after), so a span or baggage value set by one
  test never leaks into the next -- OpenTelemetry context lives in a
  ``contextvars.ContextVar`` that pytest does not reset between tests on its
  own.
- ``_synchronous_transport`` swaps witdem_sdk's background
  ``ThreadPoolExecutor`` for a tiny stand-in whose ``submit`` runs the given
  function immediately, in the calling thread. This lets tests assert on a
  mocked HTTP call right after calling a public ``witdem_sdk`` function, with
  no sleeping/polling for a background thread to finish, and needs no real
  network access -- everything here runs fully offline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from typing import Any, TypeVar

import pytest
from opentelemetry import context as otel_context

import witdem_sdk._telemetry as telemetry
import witdem_sdk._transport as transport

_T = TypeVar("_T")


class _ImmediateExecutor(Executor):
    """A stand-in ``Executor`` that runs submitted work synchronously."""

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        future: Future[_T] = Future()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - defensive
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future


@pytest.fixture(autouse=True)
def _reset_otel_context() -> Iterator[None]:
    token = otel_context.attach(otel_context.Context())
    try:
        yield
    finally:
        otel_context.detach(token)


@pytest.fixture(autouse=True)
def _synchronous_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "_executor", _ImmediateExecutor())


@pytest.fixture(autouse=True)
def _reset_sdk_provider_registry() -> Iterator[None]:
    telemetry._CONFIGURED_PROVIDER_IDS.clear()
    telemetry._CONFIGURED_EXPORTER_CONFIGS.clear()
    telemetry._PROVIDER_EXPORTER_CONFIG.clear()
    yield
    telemetry._CONFIGURED_PROVIDER_IDS.clear()
    telemetry._CONFIGURED_EXPORTER_CONFIGS.clear()
    telemetry._PROVIDER_EXPORTER_CONFIG.clear()
