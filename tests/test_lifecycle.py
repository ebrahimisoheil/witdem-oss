from __future__ import annotations

import json
from pathlib import Path

import pytest

from witdem import lifecycle
from witdem.config import ResolvedConfig


def _config(tmp_path: Path) -> ResolvedConfig:
    return ResolvedConfig(
        host="127.0.0.1",
        port=14318,
        dashboard_host="127.0.0.1",
        dashboard_port=18501,
        database=tmp_path / "live.duckdb",
        data_directory=tmp_path,
    )


def test_native_up_refuses_occupied_ports_before_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(lifecycle, "_port_available", lambda host, port: port != config.port)
    with pytest.raises(RuntimeError, match="receiver port"):
        lifecycle.native_up(config, open_dashboard=False)


def test_partial_native_startup_rolls_back_started_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    class Process:
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float) -> int:
            return 0

    receiver = Process()
    calls = 0

    def spawn(name, command, config, health_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            return receiver, {"pid": 1, "identity": "receiver", "command": command, "health_url": health_url}
        raise RuntimeError("injected child failure")

    monkeypatch.setattr(lifecycle, "_port_available", lambda host, port: True)
    monkeypatch.setattr(lifecycle, "initialize_analytics_store", lambda path: None)
    monkeypatch.setattr(lifecycle, "compile_registry", lambda **kwargs: {})
    monkeypatch.setattr(lifecycle, "_spawn", spawn)
    monkeypatch.setattr(lifecycle, "_wait", lambda url: None)

    with pytest.raises(RuntimeError, match="injected child failure"):
        lifecycle.native_up(config, open_dashboard=False)
    assert receiver.terminated is True
    assert not (tmp_path / "run/services.json").exists()


def test_stale_pid_metadata_never_stops_an_unverified_process(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = tmp_path / "run/services.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "services": {
                    "receiver": {
                        "pid": 1,
                        "identity": "definitely-not-the-current-process",
                        "start_token": "old",
                        "command": ["python", "-m", "witdem.cli", "serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = lifecycle.native_down(config)

    assert result["stopped"] == []
    assert result["data_preserved"] is True
    assert not state.exists()
