#!/usr/bin/env python3
"""Exercise an installed SDK against an installed analytics receiver."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Protocol


def _get(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class _Process(Protocol):
    """The process state used while proving receiver ownership."""

    returncode: int | None

    def poll(self) -> int | None: ...


def _available_port() -> int:
    """Ask the OS for an unused loopback port instead of touching a fixed port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_receiver(server: _Process, endpoint: str, *, timeout: float = 30) -> None:
    """Wait for this smoke test's child, never an unrelated receiver."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"analytics receiver exited with {server.returncode}")
        try:
            status, payload = _get(f"{endpoint}/readiness")
            if status == 200 and payload.get("status") == "ok":
                # A bind failure can briefly race an existing listener. Give the
                # child time to report that failure before accepting readiness.
                time.sleep(0.2)
                if server.poll() is not None:
                    raise RuntimeError(f"analytics receiver exited with {server.returncode}")
                return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("analytics receiver did not become ready")


def _wait_for_serving_fact(
    endpoint: str, execution_id: str, *, timeout: float = 30
) -> dict[str, object]:
    """Require transformed, queryable evidence for the exact smoke execution."""

    deadline = time.monotonic() + timeout
    url = f"{endpoint}/ingestion/v1/executions/{execution_id}"
    latest: tuple[int, dict[str, object]] | None = None
    while time.monotonic() < deadline:
        try:
            latest = _get(url)
        except OSError:
            time.sleep(0.2)
            continue
        status, payload = latest
        if status == 200 and payload.get("status") == "ready":
            serving_fact = payload.get("serving_fact")
            if (
                isinstance(serving_fact, dict)
                and serving_fact.get("execution_id") == execution_id
            ):
                return payload
        if payload.get("status") == "failed":
            raise RuntimeError(f"compatibility ELT failed: {payload}")
        time.sleep(0.2)
    raise RuntimeError(f"cross-version record did not become queryable: {latest}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="witdem-compatibility-") as temporary:
        root = Path(temporary)
        port = _available_port()
        endpoint = f"http://127.0.0.1:{port}"
        execution_id = f"compatibility-release-matrix-{uuid.uuid4().hex}"
        environment = {
            **os.environ,
            "WITDEM_DATA_DIR": str(root),
            "WITDEM_DB_PATH": str(root / "live.duckdb"),
            "WITDEM_ENDPOINT": endpoint,
            "WITDEM_COMPAT_EXECUTION_ID": execution_id,
        }
        with (root / "receiver.log").open("w", encoding="utf-8") as receiver_log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "witdem.cli",
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--db",
                    str(root / "live.duckdb"),
                ],
                env=environment,
                stdout=receiver_log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_for_receiver(server, endpoint)

                client = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os; from witdem_sdk import event, flush; "
                            "event('compatibility-smoke', {'source': 'release-matrix'}, "
                            "execution_id=os.environ['WITDEM_COMPAT_EXECUTION_ID']); "
                            "assert flush(10)"
                        ),
                    ],
                    env=environment,
                    check=False,
                )
                if client.returncode != 0:
                    return client.returncode

                transform = subprocess.run(
                    [sys.executable, "-m", "witdem.cli", "elt", "run"],
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if transform.returncode != 0:
                    raise RuntimeError(
                        "compatibility ELT command failed: "
                        f"{transform.stdout}\n{transform.stderr}"
                    )

                payload = _wait_for_serving_fact(endpoint, execution_id)
                if server.poll() is not None:
                    raise RuntimeError(f"analytics receiver exited with {server.returncode}")
                print(json.dumps(payload, indent=2))
                return 0
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
