"""Validate the reference from built wheels in a fresh Python 3.12 environment."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
SDK = ROOT / "witdem-sdk"
PRODUCT_FACTORY = ROOT / "examples" / "product-factory"


def _run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, check=True, capture_output=True)


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/readiness", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Fresh Witdem receiver did not become ready")


def _wait_dashboard(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Fresh Witdem dashboard did not become ready")


def _wait_ports_released(*ports: int) -> None:
    deadline = time.monotonic() + 10
    pending = set(ports)
    while pending and time.monotonic() < deadline:
        for port in tuple(pending):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    continue
            pending.remove(port)
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError(f"Validation ports were not released after shutdown: {sorted(pending)}")


def _inspect(python: Path, database: Path, env: dict[str, str]) -> dict[str, object]:
    failure: subprocess.CalledProcessError | None = None
    for _ in range(5):
        try:
            output = _run(str(python), "-m", "witdem.cli", "inspect", "--db", str(database), env=env).stdout
            parsed = json.loads(output)
            if not isinstance(parsed, dict):
                raise RuntimeError("witdem inspect returned a non-object JSON response")
            return cast(dict[str, object], parsed)
        except subprocess.CalledProcessError as exc:
            failure = exc
            time.sleep(1)
    assert failure is not None
    raise RuntimeError(f"witdem inspect failed: {failure.stderr or failure.stdout}") from failure


def _counts(inspected: dict[str, object]) -> dict[str, int]:
    raw_counts = inspected.get("counts")
    if not isinstance(raw_counts, dict):
        raise RuntimeError("witdem inspect response has no counts object")
    return {str(name): int(value) for name, value in raw_counts.items()}


def _wait_for_count(
    python: Path,
    database: Path,
    env: dict[str, str],
    table: str,
    minimum: int,
) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        inspected = _inspect(python, database, env)
        if _counts(inspected)[table] >= minimum:
            return inspected
        time.sleep(0.5)
    raise RuntimeError(f"Fresh validation never observed {minimum} row(s) in {table}")


def _start_stack(
    python: Path,
    database: Path,
    port: int,
    dashboard_port: int,
    environment: dict[str, str],
) -> subprocess.Popen[str]:
    _wait_ports_released(port, dashboard_port)
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "witdem.cli",
            "dev",
            "--port",
            str(port),
            "--dashboard-port",
            str(dashboard_port),
            "--db",
            str(database),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_ready(port)
        _wait_dashboard(dashboard_port)
    except Exception as exc:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        detail = output.strip() or f"process exit status: {process.returncode}"
        raise RuntimeError(f"Fresh Witdem stack failed to start:\n{detail}") from exc
    return process


def _stop(process: subprocess.Popen[Any]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _free_port(start: int) -> int:
    """Find a free loopback port outside the platform's ephemeral range."""

    for port in range(start, start + 1_000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No free validation port found from {start} to {start + 999}")


def _set_endpoints(environment: dict[str, str], port: int, dashboard_port: int) -> None:
    environment.update(
        {
            "WITDEM_ENDPOINT": f"http://127.0.0.1:{port}",
            "WITDEM_DASHBOARD_URL": f"http://127.0.0.1:{dashboard_port}",
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        }
    )


def _reset(python: Path, data_directory: Path, environment: dict[str, str]) -> None:
    _run(
        str(python),
        "-m",
        "witdem.cli",
        "reset",
        "--live",
        "--data-dir",
        str(data_directory),
        "--yes",
        "--no-backup",
        env=environment,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-matrix", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if args.live_matrix and not args.confirm_live:
        parser.error("--live-matrix requires --confirm-live")

    with tempfile.TemporaryDirectory(prefix="witdem-fresh-") as raw_temp:
        temp = Path(raw_temp)
        dist = temp / "dist"
        analytics_dist, sdk_dist, product_dist = dist / "analytics", dist / "sdk", dist / "product"
        for project, target in ((ROOT, analytics_dist), (SDK, sdk_dist), (PRODUCT_FACTORY, product_dist)):
            target.mkdir(parents=True)
            _run("uv", "build", "--wheel", "--out-dir", str(target), cwd=project)

        environment = os.environ.copy()
        venv = temp / "venv"
        _run("uv", "venv", "--python", "3.12", str(venv), env=environment)
        python = venv / "bin" / "python"
        wheels = [
            next(analytics_dist.glob("*.whl")),
            next(sdk_dist.glob("*.whl")),
            next(product_dist.glob("*.whl")),
        ]
        _run("uv", "pip", "install", "--python", str(python), *(str(path) for path in wheels), env=environment)
        _run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "langchain-core>=0.3,<2",
            "langgraph>=0.2,<1",
            "haystack-ai>=3.0,<4",
            "opentelemetry-haystack>=1,<2",
            "openai-agents>=0.0.10,<1",
            "anthropic>=0.50,<1",
            env=environment,
        )

        port, dashboard_port = _free_port(24_318), _free_port(28_501)
        database = temp / "data" / "live.duckdb"
        _set_endpoints(environment, port, dashboard_port)
        process = _start_stack(python, database, port, dashboard_port, environment)
        try:
            assert _counts(_inspect(python, database, environment))["executions"] == 0

            otel_script = """
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
p=TracerProvider(); p.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter())); trace.set_tracer_provider(p)
with trace.get_tracer('fresh').start_as_current_span('fresh.otel.golden'): pass
p.force_flush()
            """
            _run(str(python), "-c", otel_script, env=environment)
            otel_counts = _counts(_wait_for_count(python, database, environment, "executions", 1))
            assert otel_counts["events"] == 0 and otel_counts["outcomes"] == 0
        finally:
            _stop(process)
            environment.pop("WITDEM_CONFIG", None)

        _reset(python, database.parent, environment)
        port, dashboard_port = _free_port(port + 1), _free_port(dashboard_port + 1)
        _set_endpoints(environment, port, dashboard_port)
        process = _start_stack(python, database, port, dashboard_port, environment)
        try:
            assert _counts(_inspect(python, database, environment))["executions"] == 0
            golden_project = temp / "sdk-golden"
            _run(
                str(python),
                "-m",
                "witdem_sdk",
                "init",
                "--directory",
                str(golden_project),
                "--service-name",
                "fresh-sdk-golden",
                env=environment,
            )
            environment["WITDEM_CONFIG"] = str(golden_project / ".witdem" / "witdem.yaml")
            sdk_script = """
from witdem_sdk import configure
with configure() as witdem:
    with witdem.execution('Fresh SDK golden'):
        with witdem.operation('Validate fresh SDK enrichment'):
            witdem.report(
                result='completed',
                result_valid=True,
                requirements={'useful_result': True},
            )
    assert witdem.flush()
"""
            _run(str(python), "-c", sdk_script, env=environment)
            sdk_counts = _counts(_wait_for_count(python, database, environment, "outcomes", 1))
            assert sdk_counts["executions"] >= 1
            assert sdk_counts["events"] >= 1 and sdk_counts["outcomes"] >= 1
        finally:
            _stop(process)
            environment.pop("WITDEM_CONFIG", None)

        _reset(python, database.parent, environment)
        port, dashboard_port = _free_port(port + 1), _free_port(dashboard_port + 1)
        _set_endpoints(environment, port, dashboard_port)
        process = _start_stack(python, database, port, dashboard_port, environment)
        try:
            matrix_command = [
                str(venv / "bin" / "product-factory"),
                "matrix",
                "--suite",
                "all" if args.live_matrix else "cross-runtime",
                "--reports-dir",
                str(temp / "reports"),
            ]
            if args.live_matrix:
                matrix_command.extend(("--live", "--confirm-live"))
            _run(
                *matrix_command,
                env=environment,
            )
            expected_runs = 44 if args.live_matrix else 20
            counts_before_restart = _counts(
                _wait_for_count(python, database, environment, "executions", expected_runs)
            )
        finally:
            _stop(process)

        port = _free_port(port + 1)
        _set_endpoints(environment, port, dashboard_port)
        restarted = subprocess.Popen(
            [str(python), "-m", "witdem.cli", "serve", "--port", str(port), "--db", str(database)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_ready(port)
            assert _counts(_inspect(python, database, environment)) == counts_before_restart
        finally:
            _stop(restarted)

        print("Fresh Python 3.12 wheel validation passed")


if __name__ == "__main__":
    main()
