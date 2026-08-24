"""Run the product example catalog through one Witdem service and database."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast

EXAMPLES_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Example:
    name: str
    project: Path
    required_key: str | None = None
    sdk_extra: str | None = None


CATALOG = (
    Example("openai/basic_agent", EXAMPLES_ROOT / "openai/basic_agent", "OPENAI_API_KEY", "openai"),
    Example("openai/multi_agent", EXAMPLES_ROOT / "openai/multi_agent", "OPENAI_API_KEY", "openai"),
    Example("anthropic/basic_agent", EXAMPLES_ROOT / "anthropic/basic_agent", "ANTHROPIC_API_KEY", "anthropic"),
    Example("anthropic/tool_loop", EXAMPLES_ROOT / "anthropic/tool_loop", "ANTHROPIC_API_KEY", "anthropic"),
    Example(
        "langchain/runnable_pipeline",
        EXAMPLES_ROOT / "langchain/runnable_pipeline",
        "OPENAI_API_KEY",
        "langchain",
    ),
    Example("langgraph/state_graph", EXAMPLES_ROOT / "langgraph/state_graph", sdk_extra="langgraph"),
    Example("haystack/pipeline", EXAMPLES_ROOT / "haystack/pipeline", sdk_extra="haystack"),
)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _dotenv_values(project: Path) -> dict[str, str]:
    """Load shared credentials, then allow a tutorial-local file to override them."""

    return {**_read_dotenv(EXAMPLES_ROOT / ".env"), **_read_dotenv(project / ".env")}


def _has_key(name: str | None, dotenv: dict[str, str]) -> bool:
    return name is None or bool(os.getenv(name) or dotenv.get(name))


def _sdk_requirement(package: str, extra: str | None) -> str:
    if not extra:
        return package
    if package.endswith(".whl") or "/" in package:
        return f"{package}[{extra}]"
    return f"{package}[{extra}]"


def _wait_until_queryable(execution_id: str, environment: dict[str, str], timeout: float = 45.0) -> dict[str, object]:
    endpoint = environment.get("WITDEM_ENDPOINT", "http://localhost:4318").rstrip("/")
    url = f"{endpoint}/ingestion/v1/executions/{urllib.parse.quote(execution_id, safe='')}"
    headers: dict[str, str] = {}
    if api_key := environment.get("WITDEM_API_KEY"):
        headers["Authorization"] = f"Bearer {api_key}"
    deadline = time.monotonic() + timeout
    last_status = "not observed"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=3) as response:
                raw_payload = json.loads(response.read())
            if not isinstance(raw_payload, dict):
                raise RuntimeError("execution status response was not an object")
            payload = cast(dict[str, object], raw_payload)
            last_status = str(payload.get("status") or "unknown")
            if last_status == "ready" and payload.get("serving_fact"):
                return payload
            if last_status == "failed":
                raise RuntimeError(f"ELT reported failure for {execution_id}")
        except urllib.error.HTTPError as exc:
            if exc.code not in {404, 409, 425, 503}:
                raise
            last_status = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_status = str(exc.reason)
        time.sleep(0.25)
    raise TimeoutError(f"execution {execution_id} was not queryable after {timeout:.0f}s (last status: {last_status})")


def _dashboard_runs(environment: dict[str, str]) -> list[dict[str, object]]:
    dashboard = environment.get("WITDEM_DASHBOARD_URL", "http://localhost:8501").rstrip("/")
    runs: list[dict[str, object]] = []
    page = 1
    while True:
        url = f"{dashboard}/api/v1/runs?page={page}&page_size=100"
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read())
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("dashboard runs response did not contain an items list")
        runs.extend(item for item in items if isinstance(item, dict))
        if page >= int(payload.get("pages") or 1):
            return runs
        page += 1


def _wait_for_new_execution(
    previous_ids: set[str], environment: dict[str, str], timeout: float = 45.0
) -> str:
    """Discover the execution created by an SDK integration without changing tutorial code."""

    deadline = time.monotonic() + timeout
    last_error = "not observed"
    while time.monotonic() < deadline:
        try:
            for run_row in _dashboard_runs(environment):
                execution_id = run_row.get("execution_id")
                if isinstance(execution_id, str) and execution_id not in previous_ids:
                    return execution_id
        except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"new execution was not visible after {timeout:.0f}s (last error: {last_error})")


def run(
    selected: set[str] | None = None,
    mode: str = "both",
    *,
    sdk_package: str = "witdem-sdk",
) -> int:
    failures = 0
    for example in CATALOG:
        if selected and example.name not in selected:
            continue
        dotenv = _dotenv_values(example.project)
        environment = {**dotenv, **os.environ}
        if not _has_key(example.required_key, dotenv):
            print(f"SKIP {example.name}: {example.required_key} is not configured")
            continue
        print(f"RUN  {example.name}", flush=True)
        modes = ("otel_only", "sdk_enriched") if mode == "both" else (mode,)
        for selected_mode in modes:
            entrypoint = example.project / f"{selected_mode}.py"
            if not entrypoint.exists():
                print(f"SKIP {example.name} [{selected_mode}]: entrypoint not present")
                continue
            sync = subprocess.run(
                ["uv", "sync", "--project", str(example.project)],
                cwd=example.project,
                env=environment,
                check=False,
            )
            if sync.returncode:
                failures += 1
                print(f"FAIL {example.name} [{selected_mode}]: dependency sync failed")
                continue
            if selected_mode == "sdk_enriched":
                install = subprocess.run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(example.project / ".venv/bin/python"),
                        _sdk_requirement(sdk_package, example.sdk_extra),
                    ],
                    cwd=example.project,
                    env=environment,
                    check=False,
                )
                if install.returncode:
                    failures += 1
                    print(f"FAIL {example.name} [{selected_mode}]: witdem-sdk install failed")
                    continue
            try:
                previous_ids = {
                    str(row["execution_id"])
                    for row in _dashboard_runs(environment)
                    if isinstance(row.get("execution_id"), str)
                }
            except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                failures += 1
                print(f"FAIL {example.name} [{selected_mode}]: could not snapshot dashboard runs: {exc}")
                continue
            completed = subprocess.run(
                ["uv", "run", "--no-sync", "--project", str(example.project), "python", str(entrypoint)],
                cwd=example.project,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.stderr:
                print(completed.stderr, end="")
            if completed.returncode:
                failures += 1
                print(f"FAIL {example.name} [{selected_mode}]: exit {completed.returncode}")
                continue
            match = re.search(r"^WITDEM_EXECUTION_ID=(\S+)$", completed.stdout, flags=re.MULTILINE)
            try:
                execution_id = (
                    match.group(1) if match is not None else _wait_for_new_execution(previous_ids, environment)
                )
            except (RuntimeError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                failures += 1
                print(f"FAIL {example.name} [{selected_mode}]: could not identify the new execution: {exc}")
                continue
            try:
                ingestion = _wait_until_queryable(execution_id, environment)
            except (RuntimeError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                failures += 1
                print(f"FAIL {example.name} [{selected_mode}]: {exc}")
                continue
            dashboard = environment.get("WITDEM_DASHBOARD_URL", "http://localhost:8501")
            fact = ingestion["serving_fact"]
            operation_count = int(fact.get("operation_count") or 0) if isinstance(fact, dict) else 0
            print(f"PASS {example.name} [{selected_mode}] execution={execution_id} operations={operation_count}")
            print(f"     {dashboard}/?execution_id={execution_id}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("examples", nargs="*", choices=[example.name for example in CATALOG])
    parser.add_argument("--mode", choices=("otel", "sdk", "both"), default="both")
    parser.add_argument(
        "--sdk-package",
        default="witdem-sdk",
        help="SDK requirement or wheel path used for enriched runs (default: witdem-sdk)",
    )
    args = parser.parse_args()
    mode = {"otel": "otel_only", "sdk": "sdk_enriched", "both": "both"}[args.mode]
    raise SystemExit(1 if run(set(args.examples) or None, mode=mode, sdk_package=args.sdk_package) else 0)


if __name__ == "__main__":
    main()
