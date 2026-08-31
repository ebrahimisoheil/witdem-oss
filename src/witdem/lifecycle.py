"""Safe native background lifecycle used by pipx-installed Witdem."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from witdem import __version__
from witdem.config import ResolvedConfig
from witdem.ingest.live_db import initialize_analytics_store
from witdem.workflows import compile_registry


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state_path(config: ResolvedConfig) -> Path:
    return config.data_directory / "run" / "services.json"


def _logs_dir(config: ResolvedConfig) -> Path:
    return config.data_directory / "logs"


def _healthy(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return bool(response.status == 200)
    except (OSError, urllib.error.URLError):
        return False


def _wait(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(url):
            return
        time.sleep(0.2)
    raise RuntimeError(f"service did not become healthy at {url}")


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as handle:
        # Native services enable address reuse. Mirror that bind behavior so a
        # clean stop followed by an immediate restart is not rejected solely
        # because the prior listener still has connections in TIME_WAIT.
        if os.name != "nt":
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            handle.bind((host, port))
        except OSError:
            return False
    return True


def _identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    if os.name == "nt":
        return str(pid)
    result = subprocess.run(
        ["ps", "-o", "lstart=", "-o", "command=", "-p", str(pid)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() or None


def _load_state(config: ResolvedConfig) -> dict[str, Any] | None:
    path = _state_path(config)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(value) if isinstance(value, dict) else None


def _start_token(identity: str) -> str:
    return " ".join(identity.split()[:5])


def _valid_process(service: dict[str, Any]) -> bool:
    identity = _identity(int(service.get("pid") or 0))
    command_parts = [str(item) for item in service.get("command") or ()]
    command = " ".join(command_parts)
    expected_suffix = " ".join(command_parts[1:])
    expected_token = str(service.get("start_token") or "")
    if not expected_token and service.get("identity"):
        expected_token = _start_token(str(service["identity"]))
    recognized = bool(identity and "witdem.cli" in identity) if os.name != "nt" else "witdem.cli" in command
    command_matches = bool(identity and expected_suffix and expected_suffix in identity)
    return bool(
        identity and expected_token and _start_token(identity) == expected_token and recognized and command_matches
    )


def native_status(config: ResolvedConfig) -> dict[str, Any]:
    state = _load_state(config) or {}
    services = dict(state.get("services") or {})
    result: dict[str, Any] = {}
    for name in ("receiver", "worker", "dashboard"):
        service = dict(services.get(name) or {})
        running = _valid_process(service)
        url = service.get("health_url")
        result[name] = {
            "pid": service.get("pid"),
            "running": running,
            "healthy": bool(running and (not url or _healthy(str(url)))),
            "log": service.get("log"),
        }
    return {"version": state.get("version"), "data_dir": str(config.data_directory), "services": result}


def _spawn(
    name: str,
    command: list[str],
    config: ResolvedConfig,
    health_url: str | None,
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    log_path = _logs_dir(config) / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab", buffering=0)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            env=config.child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    finally:
        log.close()
    identity = None
    for _ in range(20):
        identity = _identity(process.pid)
        if identity:
            break
        time.sleep(0.05)
    if not identity:
        process.terminate()
        raise RuntimeError(f"could not identify started {name} process")
    return process, {
        "pid": process.pid,
        "identity": identity,
        "start_token": _start_token(identity),
        "command": command,
        "health_url": health_url,
        "log": str(log_path),
    }


def native_up(config: ResolvedConfig, *, open_dashboard: bool = True) -> dict[str, Any]:
    current = native_status(config)
    if any(item["running"] for item in current["services"].values()):
        raise RuntimeError("Witdem is already running for this data directory")
    for label, host, port in (
        ("receiver", config.host, config.port),
        ("dashboard", config.dashboard_host, config.dashboard_port),
    ):
        if not _port_available(host, port):
            raise RuntimeError(f"{label} port {host}:{port} is occupied")
    initialize_analytics_store(config.database)
    compile_registry(root=config.data_directory)
    base = [sys.executable, "-m", "witdem.cli"]
    commands = {
        "receiver": base + ["serve", "--host", config.host, "--port", str(config.port), "--db", str(config.database)],
        "worker": base + ["elt", "worker", "--poll-interval", "0.25"],
        "dashboard": base
        + [
            "dashboard",
            "--dashboard-host",
            config.dashboard_host,
            "--dashboard-port",
            str(config.dashboard_port),
            "--db",
            str(config.database),
        ],
    }
    processes: list[subprocess.Popen[bytes]] = []
    services: dict[str, Any] = {}
    try:
        receiver, services["receiver"] = _spawn(
            "receiver",
            commands["receiver"],
            config,
            f"http://{config.host}:{config.port}/readiness",
        )
        services["receiver"]["port"] = config.port
        processes.append(receiver)
        _wait(str(services["receiver"]["health_url"]))
        worker, services["worker"] = _spawn("worker", commands["worker"], config, None)
        processes.append(worker)
        dashboard, services["dashboard"] = _spawn(
            "dashboard",
            commands["dashboard"],
            config,
            f"http://{config.dashboard_host}:{config.dashboard_port}/health",
        )
        services["dashboard"]["port"] = config.dashboard_port
        processes.append(dashboard)
        _wait(str(services["dashboard"]["health_url"]))
        state = {
            "version": __version__,
            "data_dir": str(config.data_directory),
            "database": str(config.database),
            "ports": {"receiver": config.port, "dashboard": config.dashboard_port},
            "services": services,
        }
        _atomic_json(_state_path(config), state)
    except Exception:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise
    url = f"http://{config.dashboard_host}:{config.dashboard_port}"
    if open_dashboard:
        webbrowser.open(url)
    if os.getenv("WITDEM_UPDATE_CHECK", "1").casefold() not in {"0", "false", "no", "off"}:
        subprocess.Popen(
            base + ["update", "--check", "--data-dir", str(config.data_directory), "--json"],
            env=config.child_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
    return {
        "status": "ready",
        "dashboard": url,
        "receiver": f"http://{config.host}:{config.port}",
        **native_status(config),
    }


def native_down(config: ResolvedConfig) -> dict[str, Any]:
    state = _load_state(config) or {}
    services = dict(state.get("services") or {})
    stopped: list[str] = []
    for name in ("dashboard", "worker", "receiver"):
        service = dict(services.get(name) or {})
        if not _valid_process(service):
            continue
        pid = int(service["pid"])
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _identity(pid):
            time.sleep(0.1)
        if _identity(pid):
            os.kill(pid, signal.SIGKILL)
        stopped.append(name)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _port_available(config.host, config.port) and _port_available(
            config.dashboard_host,
            config.dashboard_port,
        ):
            break
        time.sleep(0.1)
    _state_path(config).unlink(missing_ok=True)
    return {"status": "stopped", "stopped": stopped, "data_preserved": True}


def native_logs(config: ResolvedConfig, service: str | None = None, *, follow: bool = False) -> int:
    names = [service] if service else ["receiver", "worker", "dashboard"]
    paths = [_logs_dir(config) / f"{name}.log" for name in names]
    offsets: dict[Path, int] = {}
    try:
        while True:
            for path in paths:
                if not path.exists():
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = offsets.get(path, max(0, len(lines) - 200))
                if len(paths) > 1 and start < len(lines):
                    print(f"==> {path.name} <==")
                for line in lines[start:]:
                    print(line)
                offsets[path] = len(lines)
            if not follow:
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
