#!/usr/bin/env python3
"""Exercise an installed SDK against an installed analytics receiver."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _get(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="witdem-compatibility-") as temporary:
        root = Path(temporary)
        port = 14318
        endpoint = f"http://127.0.0.1:{port}"
        environment = {
            **os.environ,
            "WITDEM_DATA_DIR": str(root),
            "WITDEM_DB_PATH": str(root / "live.duckdb"),
            "WITDEM_ENDPOINT": endpoint,
        }
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
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    status, _payload = _get(f"{endpoint}/readiness")
                    if status == 200:
                        break
                except OSError:
                    pass
                if server.poll() is not None:
                    raise RuntimeError(f"analytics receiver exited with {server.returncode}")
                time.sleep(0.2)
            else:
                raise RuntimeError("analytics receiver did not become ready")

            client = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from witdem_sdk import event, flush; "
                        "event('compatibility-smoke', {'source': 'release-matrix'}, "
                        "execution_id='compatibility-release-matrix'); "
                        "assert flush(10)"
                    ),
                ],
                env=environment,
                check=False,
            )
            if client.returncode != 0:
                return client.returncode
            status, payload = _get(
                f"{endpoint}/ingestion/v1/executions/compatibility-release-matrix"
            )
            if status != 200:
                raise RuntimeError(f"cross-version record was not accepted: {status} {payload}")
            print(json.dumps(payload, indent=2))
            return 0
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    raise SystemExit(main())
