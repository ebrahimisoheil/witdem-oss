from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest


def _smoke_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "compatibility" / "smoke.py"
    spec = importlib.util.spec_from_file_location("compatibility_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ExitedProcess:
    returncode = 1

    def poll(self) -> int:
        return self.returncode


def test_readiness_from_an_unrelated_receiver_cannot_hide_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _smoke_module()
    monkeypatch.setattr(smoke, "_get", lambda _url: (200, {"status": "ok"}))

    with pytest.raises(RuntimeError, match="analytics receiver exited with 1"):
        smoke._wait_for_receiver(_ExitedProcess(), "http://127.0.0.1:14318", timeout=0.1)


def test_serving_check_rejects_pending_record_without_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _smoke_module()
    monkeypatch.setattr(
        smoke,
        "_get",
        lambda _url: (200, {"status": "pending", "serving_fact": None}),
    )
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="did not become queryable"):
        smoke._wait_for_serving_fact("http://127.0.0.1:1", "unique-run", timeout=0.001)


def test_serving_check_requires_the_exact_execution_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _smoke_module()
    responses = iter(
        [
            (200, {"status": "ready", "serving_fact": {"execution_id": "other-run"}}),
            (200, {"status": "ready", "serving_fact": {"execution_id": "unique-run"}}),
        ]
    )
    monkeypatch.setattr(smoke, "_get", lambda _url: next(responses))
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    payload = smoke._wait_for_serving_fact(
        "http://127.0.0.1:1", "unique-run", timeout=1
    )

    serving_fact = cast(dict[str, object], payload["serving_fact"])
    assert serving_fact["execution_id"] == "unique-run"
