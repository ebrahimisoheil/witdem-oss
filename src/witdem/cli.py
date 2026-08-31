"""The Witdem command-line lifecycle."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from witdem import __version__
from witdem.config import ResolvedConfig, data_dir, db_path
from witdem.ingest import live_db
from witdem.protocol import SEMANTIC_RECORD_PROTOCOL_VERSION


def _serve(args: argparse.Namespace) -> None:
    config = ResolvedConfig.from_args(args)
    os.environ.update(config.child_environment())
    live_db.initialize_analytics_store(config.database)
    from witdem.workflows import compile_registry

    compile_registry(root=config.data_directory)
    import uvicorn

    from witdem.api import app

    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)


def _dashboard(args: argparse.Namespace) -> None:
    config = ResolvedConfig.from_args(args)
    os.environ.update(config.child_environment())
    import uvicorn

    from witdem.dashboard.app import create_dashboard_app

    uvicorn.run(
        create_dashboard_app(config.database),
        host=config.dashboard_host,
        port=config.dashboard_port,
        log_level=config.log_level,
    )


def _wait_for_readiness(config: ResolvedConfig, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://{config.host}:{config.port}/readiness"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def _port_available(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return bool(response.status == 200)
    except (OSError, urllib.error.URLError):
        return False


def _dev(args: argparse.Namespace) -> None:
    config = ResolvedConfig.from_args(args)
    for label, host, port in (
        ("ingestion", config.host, config.port),
        ("dashboard", config.dashboard_host, config.dashboard_port),
    ):
        if not _port_available(host, port):
            raise SystemExit(
                f"{label} port {host}:{port} is already occupied; select another port or stop that process"
            )
    if (not _is_loopback(config.host) or not _is_loopback(config.dashboard_host)) and not os.getenv("WITDEM_API_KEY"):
        print("Warning: Witdem is binding beyond loopback in trusted mode; use only on a trusted private network.")
    config.database.parent.mkdir(parents=True, exist_ok=True)
    live_db.initialize_analytics_store(config.database)
    env = config.child_environment()
    serve_command = [
        sys.executable,
        "-m",
        "witdem.cli",
        "serve",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--db",
        str(config.database),
        "--log-level",
        config.log_level,
    ]
    dashboard_command = [
        sys.executable,
        "-m",
        "witdem.cli",
        "dashboard",
        "--dashboard-host",
        config.dashboard_host,
        "--dashboard-port",
        str(config.dashboard_port),
        "--db",
        str(config.database),
    ]
    worker_command = [
        sys.executable,
        "-m",
        "witdem.cli",
        "elt",
        "worker",
        "--poll-interval",
        "0.25",
    ]
    server = subprocess.Popen(serve_command, env=env)
    dashboard: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    previous_handlers: dict[signal.Signals, Any] = {}

    def request_shutdown(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[handled_signal] = signal.getsignal(handled_signal)
        signal.signal(handled_signal, request_shutdown)
    try:
        if not _wait_for_readiness(config):
            raise SystemExit("Witdem server did not become ready within 20 seconds")
        worker = subprocess.Popen(worker_command, env=env)
        dashboard = subprocess.Popen(dashboard_command, env=env)
        print(f"Witdem server: http://{config.host}:{config.port}")
        print(f"Witdem dashboard: http://{config.dashboard_host}:{config.dashboard_port}")
        print(f"Witdem database: {config.database}")
        from witdem.pricing import CATALOG_VERSION

        print(f"Witdem pricing catalog: {CATALOG_VERSION}")
        print(f"Witdem security: {'bearer-key ingestion' if os.getenv('WITDEM_API_KEY') else 'trusted local mode'}")
        if args.open:
            webbrowser.open(f"http://{config.dashboard_host}:{config.dashboard_port}")
        while True:
            server_code = server.poll()
            dashboard_code = dashboard.poll()
            worker_code = worker.poll()
            if server_code is not None:
                raise SystemExit(f"Witdem server exited unexpectedly with status {server_code}")
            if dashboard_code is not None:
                raise SystemExit(f"Witdem dashboard exited unexpectedly with status {dashboard_code}")
            if worker_code is not None:
                raise SystemExit(f"Witdem ELT worker exited unexpectedly with status {worker_code}")
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for handled_signal, previous in previous_handlers.items():
            signal.signal(handled_signal, previous)
        for process in (dashboard, worker, server):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (dashboard, worker, server):
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _inspect(args: argparse.Namespace) -> None:
    import duckdb

    database = db_path(args.db)
    if not database.exists():
        live_db.initialize_analytics_store(database)
    lock = FileLock(str(database.with_suffix(database.suffix + ".lock")), timeout=10)
    try:
        with lock:
            connection = duckdb.connect(str(database), read_only=True)
            try:
                tables = [str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()]
                counts: dict[str, int] = {}
                for table in tables:
                    row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                    counts[table] = int(row[0]) if row else 0
            finally:
                connection.close()
    except Timeout as exc:
        raise SystemExit(f"database is busy; retry inspect shortly: {database}") from exc
    print(json.dumps({"database": str(database), "tables": tables, "counts": counts}, indent=2))


def _reset(args: argparse.Namespace) -> None:
    if not args.live:
        raise SystemExit("reset requires the explicit --live target")
    root = _validated_data_root(Path(args.data_dir).expanduser() if args.data_dir else data_dir())
    targets = [
        root / "live.duckdb",
        root / "analytics.duckdb",
        root / "live.duckdb.lock",
        root / "analytics.duckdb.lock",
        root / "raw_spans",
        root / "sdk_records",
        root / "corpus",
        root / "elt",
    ]
    if not args.yes:
        answer = input(f"Reset mutable Witdem state at {root}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Reset cancelled.")
            return
    existing_targets = [target for target in targets if target.exists()]
    if not args.no_backup and existing_targets:
        backup = root.with_name(f"{root.name}.backup-{int(time.time())}")
        backup.mkdir(parents=True, exist_ok=False)
        for target in existing_targets:
            destination = backup / target.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, destination)
            else:
                shutil.copy2(target, destination)
        print(f"Created backup at {backup}")
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    live_db.initialize_analytics_store(root / "live.duckdb")
    print(f"Reset live Witdem state at {root}; synthetic and regression corpora were not touched.")


def _validated_data_root(root: Path) -> Path:
    resolved = root.resolve(strict=False)
    forbidden = {Path(resolved.anchor), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in forbidden:
        raise SystemExit(f"refusing to reset unsafe data directory: {resolved}")
    return resolved


def _doctor(args: argparse.Namespace) -> None:
    config = ResolvedConfig.from_args(args)
    checks: dict[str, str] = {}
    try:
        import duckdb

        config.database.parent.mkdir(parents=True, exist_ok=True)
        live_db.initialize_analytics_store(config.database)
        lock = FileLock(str(config.database.with_suffix(config.database.suffix + ".lock")), timeout=1)
        with lock:
            checks["database_lock"] = "available"
            connection = duckdb.connect(str(config.database))
            try:
                connection.execute("BEGIN")
                connection.execute("CREATE TEMP TABLE witdem_doctor(value INTEGER)")
                connection.execute("INSERT INTO witdem_doctor VALUES (1)")
                row = connection.execute("SELECT value FROM witdem_doctor").fetchone()
                if row != (1,):
                    raise RuntimeError("diagnostic read/write cycle returned an unexpected result")
                connection.execute("ROLLBACK")
            finally:
                connection.close()
        checks["database"] = "read/write ok"
    except Timeout:
        checks["database_lock"] = "error: database lock is unavailable"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
    port_checks = (
        ("server_port", config.host, config.port),
        ("dashboard_port", config.dashboard_host, config.dashboard_port),
    )
    for label, host, port in port_checks:
        if _port_available(host, port):
            checks[label] = "available"
        else:
            health_url = f"http://{host}:{port}/readiness" if label == "server_port" else f"http://{host}:{port}/health"
            checks[label] = "running and healthy" if _url_healthy(health_url) else "error: occupied but unhealthy"
    checks["python"] = sys.version.split()[0]
    checks["witdem_analytics"] = __version__
    checks["semantic_record_protocol"] = SEMANTIC_RECORD_PROTOCOL_VERSION
    override = os.getenv("WITDEM_PRICING_FILE")
    if override:
        try:
            from witdem.analytics.cost import validate_pricing_override

            validate_pricing_override(Path(override))
            checks["pricing"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["pricing"] = f"error: {exc}"
    else:
        from witdem.pricing import CATALOG_VERSION

        checks["pricing"] = f"built-in {CATALOG_VERSION}"
    try:
        import witdem_sdk  # type: ignore[import-not-found]

        sdk_protocol = str(witdem_sdk.SEMANTIC_RECORD_PROTOCOL_VERSION)
        checks["witdem_sdk"] = str(witdem_sdk.__version__)
        checks["sdk_protocol"] = (
            "compatible"
            if sdk_protocol == SEMANTIC_RECORD_PROTOCOL_VERSION
            else f"error: SDK {sdk_protocol}, server {SEMANTIC_RECORD_PROTOCOL_VERSION}"
        )
    except ImportError:
        checks["witdem_sdk"] = "not installed"
    try:
        duckle_version = version("duckle")
        checks["duckle"] = duckle_version
        from witdem.elt.worker import duckle_executable, pipeline_path

        executable = duckle_executable()
        if executable is None:
            raise OSError("Duckle executable is not available in the active Python environment")
        validation = subprocess.run(
            [executable, "validate", str(pipeline_path()), "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        checks["duckle_pipeline"] = "ok" if validation.returncode == 0 else f"error: {validation.stderr.strip()}"
    except (PackageNotFoundError, OSError) as exc:
        checks["duckle"] = f"error: {exc}"
    print(json.dumps({"database": str(config.database), "checks": checks}, indent=2))
    if any(value.startswith("error") for value in checks.values()):
        raise SystemExit(1)


def _version(args: argparse.Namespace) -> None:
    print(f"witdem-analytics {__version__}")
    try:
        import witdem_sdk

        print(f"witdem-sdk {witdem_sdk.__version__}")
    except ImportError:
        pass


def _native_up(args: argparse.Namespace) -> None:
    from witdem.lifecycle import native_up

    config = ResolvedConfig.from_args(args)
    try:
        result = native_up(config, open_dashboard=bool(args.open))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    _print_result(result, as_json=bool(args.json))


def _native_open(args: argparse.Namespace) -> None:
    config = ResolvedConfig.from_args(args)
    url = f"http://{config.dashboard_host}:{config.dashboard_port}"
    if not _url_healthy(f"{url}/health"):
        raise SystemExit("Witdem dashboard is not healthy; run 'witdem up' first")
    webbrowser.open(url)
    print(url)


def _native_status(args: argparse.Namespace) -> None:
    from witdem.lifecycle import native_status

    result = native_status(ResolvedConfig.from_args(args))
    _print_result(result, as_json=bool(args.json))
    if not all(item["healthy"] for item in result["services"].values()):
        raise SystemExit(1)


def _native_logs(args: argparse.Namespace) -> None:
    from witdem.lifecycle import native_logs

    raise SystemExit(native_logs(ResolvedConfig.from_args(args), args.service, follow=bool(args.follow)))


def _native_down(args: argparse.Namespace) -> None:
    from witdem.lifecycle import native_down

    result = native_down(ResolvedConfig.from_args(args))
    _print_result(result, as_json=bool(args.json))


def _workflow_compile(args: argparse.Namespace) -> None:
    from witdem.workflows import compile_registry

    root = Path(args.data_dir).expanduser() if args.data_dir else data_dir()
    try:
        result = compile_registry(args.config, check=bool(args.check), force=bool(args.force), root=root)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"workflow compilation failed: {exc}") from exc
    print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        raise SystemExit(1)


def _workflow_rebuild(args: argparse.Namespace) -> None:
    from witdem.dashboard.service import materialize_workflow_projections
    from witdem.elt.worker import run_pending
    from witdem.ingest import corpus

    config = ResolvedConfig.from_args(args)
    os.environ.update(config.child_environment())
    live_db.initialize_analytics_store(config.database)
    with corpus.maintenance_lock(timeout=60.0):
        transform = run_pending(rebuild=True, maintenance_lock_held=True)
        projections = materialize_workflow_projections(config.database)
    print(json.dumps({"status": "ok", "transform": transform, "projections": projections}, indent=2))


def _eval_validate(args: argparse.Namespace) -> None:
    from witdem.evaluation_campaigns import validate_jsonl

    try:
        campaign, results = validate_jsonl(args.path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"evaluation campaign validation failed: {exc}") from exc
    print(
        json.dumps(
            {"status": "ok", "campaign_id": campaign.campaign_id, "results": len(results)},
            indent=2,
        )
    )


def _eval_import(args: argparse.Namespace) -> None:
    from witdem.evaluation_campaigns import validate_jsonl
    from witdem.ingest.live_db import initialize_analytics_store, store_evaluation_campaign

    config = ResolvedConfig.from_args(args)
    try:
        campaign, results = validate_jsonl(args.path)
        initialize_analytics_store(config.database)
        store_evaluation_campaign(config.database, campaign, results)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"evaluation campaign import failed: {exc}") from exc
    print(
        json.dumps(
            {"status": "ok", "campaign_id": campaign.campaign_id, "results": len(results)},
            indent=2,
        )
    )


def _update_check(args: argparse.Namespace) -> None:
    from witdem.update import check_updates

    if not args.check:
        raise SystemExit("update is detection-only; use 'witdem update --check'")
    root = Path(args.data_dir).expanduser() if args.data_dir else data_dir()
    result = check_updates(root=root, refresh=bool(args.refresh), offline=bool(args.offline))
    _print_result(result, as_json=bool(args.json))


def _print_result(value: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2))
        return
    status = value.get("status")
    if status:
        print(f"Witdem {status}")
    if value.get("dashboard"):
        print(f"Dashboard: {value['dashboard']}")
    if value.get("receiver"):
        print(f"Receiver: {value['receiver']}")
    services = value.get("services")
    if isinstance(services, dict):
        for name, service in services.items():
            state = "healthy" if service.get("healthy") else "stopped"
            print(f"{name}: {state}")
    if value.get("data_preserved"):
        print("Data preserved")
    latest = value.get("latest")
    current = value.get("current")
    if isinstance(latest, dict) and isinstance(current, dict):
        print(f"Installed: {current.get('platform')} · Latest: {latest.get('platform')}")
    compatibility = value.get("compatibility")
    if isinstance(compatibility, dict):
        print(f"Compatibility: {'compatible' if compatibility.get('compatible') else 'attention required'}")
    guidance = value.get("guidance")
    if isinstance(guidance, dict):
        for launcher, command in guidance.items():
            print(f"{launcher}: {command}")
    if value.get("reason"):
        print(f"Reason: {value['reason']}")


def _older_than_days(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)d?", value.strip().casefold())
    if match is None:
        raise argparse.ArgumentTypeError("use a positive number of days such as 30d")
    return int(match.group(1))


def _prune(args: argparse.Namespace) -> None:
    from witdem.retention import apply_retention, plan_retention

    root = _validated_data_root(Path(args.data_dir).expanduser() if args.data_dir else data_dir())
    database = Path(args.db).expanduser() if args.db else root / "live.duckdb"
    os.environ["WITDEM_DATA_DIR"] = str(root)
    os.environ["WITDEM_DB_PATH"] = str(database)
    live_db.initialize_analytics_store(database)
    plan = plan_retention(older_than_days=args.older_than)
    if not args.yes:
        print(json.dumps({"status": "preview", "plan": plan.to_dict()}, indent=2))
        if plan.batches_to_delete:
            print("Preview only. Re-run with --yes to permanently delete these corpus batches.")
        return
    print(json.dumps(apply_retention(plan).to_dict(), indent=2))


def _elt_run(args: argparse.Namespace) -> None:
    from witdem.elt.worker import run_pending

    print(json.dumps(run_pending(rebuild=bool(args.rebuild)), indent=2))


def _elt_worker(args: argparse.Namespace) -> None:
    from witdem.elt.worker import run_pending

    interval = max(0.1, float(args.poll_interval))
    print(f"Witdem ELT worker: Duckle polling every {interval:.2f}s")
    try:
        while True:
            try:
                result = run_pending()
                if result.get("status") != "idle":
                    print(json.dumps(result, default=str))
            except Exception as exc:  # noqa: BLE001 - worker retains failed state and continues polling
                print(f"Witdem ELT transform failed: {exc}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def _elt_status(args: argparse.Namespace) -> None:
    from witdem.ingest import corpus

    rows = []
    for commit in corpus.list_commits():
        rows.append(
            {
                "ingest_id": commit.ingest_id,
                "signal": commit.signal,
                "received_at": commit.received_at,
                "record_count": commit.record_count,
                "execution_ids": list(commit.execution_ids),
                "analytics": corpus.read_state(commit.ingest_id),
            }
        )
    print(json.dumps({"batches": rows}, indent=2))


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _add_runtime_options(command: argparse.ArgumentParser, *, include_hosts: bool = True) -> None:
    if include_hosts:
        command.add_argument("--host", default=None)
        command.add_argument("--receiver-port", "--port", dest="port", type=_port, default=None)
        command.add_argument("--dashboard-host", default=None)
        command.add_argument("--dashboard-port", type=_port, default=None)
    command.add_argument("--db")
    command.add_argument("--data-dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="witdem", description="Runtime analytics for agent applications")
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("up", help="start native Witdem services in the background")
    _add_runtime_options(up)
    up.add_argument("--log-level", default=None)
    open_choice = up.add_mutually_exclusive_group()
    open_choice.add_argument("--open", dest="open", action="store_true")
    open_choice.add_argument("--no-open", dest="open", action="store_false")
    up.set_defaults(func=_native_up, open=True)
    up.add_argument("--json", action="store_true")
    open_command = commands.add_parser("open", help="open the running dashboard")
    _add_runtime_options(open_command)
    open_command.set_defaults(func=_native_open)
    status = commands.add_parser("status", help="show native service and endpoint health")
    _add_runtime_options(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_native_status)
    logs = commands.add_parser("logs", help="show native Witdem service logs")
    _add_runtime_options(logs)
    logs.add_argument("service", nargs="?", choices=("receiver", "worker", "dashboard"))
    logs.add_argument("--follow", action="store_true")
    logs.set_defaults(func=_native_logs)
    down = commands.add_parser("down", help="stop validated native Witdem services")
    _add_runtime_options(down)
    down.add_argument("--json", action="store_true")
    down.set_defaults(func=_native_down)
    serve = commands.add_parser("serve", help="start the OTLP and SDK receiver")
    serve.add_argument("--host", default=None)
    serve.add_argument("--receiver-port", "--port", dest="port", type=_port, default=None)
    serve.add_argument("--db")
    serve.add_argument("--data-dir")
    serve.add_argument("--log-level", default=None)
    serve.set_defaults(func=_serve)
    dashboard = commands.add_parser("dashboard", help="open the live dashboard")
    dashboard.add_argument("--db")
    dashboard.add_argument("--data-dir")
    dashboard.add_argument("--dashboard-host", default=None)
    dashboard.add_argument("--dashboard-port", type=_port, default=None)
    dashboard.set_defaults(func=_dashboard)
    dev = commands.add_parser("dev", help="start the receiver and dashboard together")
    _add_runtime_options(dev)
    dev.add_argument("--log-level", default=None)
    dev.add_argument("--open", action="store_true")
    dev.set_defaults(func=_dev)
    inspect = commands.add_parser("inspect", help="inspect database tables and row counts")
    inspect.add_argument("--db")
    inspect.set_defaults(func=_inspect)
    reset = commands.add_parser("reset", help="reset mutable local live state")
    reset.add_argument("--live", action="store_true", help="required explicit target")
    reset.add_argument("--data-dir")
    reset.add_argument("--yes", action="store_true")
    reset.add_argument("--no-backup", action="store_true")
    reset.set_defaults(func=_reset)
    doctor = commands.add_parser("doctor", help="validate local Witdem configuration")
    _add_runtime_options(doctor)
    doctor.set_defaults(func=_doctor)
    version = commands.add_parser("version", help="show installed Witdem versions")
    version.set_defaults(func=_version)
    update = commands.add_parser("update", help="check for compatible Witdem releases")
    update.add_argument("--check", action="store_true", help="detect updates without changing anything")
    update.add_argument("--refresh", action="store_true", help="bypass the 24-hour verified cache")
    update.add_argument("--offline", action="store_true", help="use only the last verified cache")
    update.add_argument("--data-dir")
    update.add_argument("--json", action="store_true")
    update.set_defaults(func=_update_check)
    prune = commands.add_parser("prune", help="delete corpus data older than a retention window")
    prune.add_argument("--older-than", required=True, type=_older_than_days, metavar="DAYS")
    prune.add_argument("--data-dir")
    prune.add_argument("--db")
    prune.add_argument("--yes", action="store_true", help="perform the permanent deletion")
    prune.set_defaults(func=_prune)
    elt = commands.add_parser("elt", help="operate the Duckle raw-to-serving pipeline")
    elt_commands = elt.add_subparsers(dest="elt_command", required=True)
    elt_run = elt_commands.add_parser("run", help="process currently pending corpus batches")
    elt_run.add_argument("--rebuild", action="store_true", help="reprocess every committed corpus batch")
    elt_run.set_defaults(func=_elt_run)
    elt_worker = elt_commands.add_parser("worker", help="continuously process committed corpus batches")
    elt_worker.add_argument("--poll-interval", type=float, default=0.5)
    elt_worker.set_defaults(func=_elt_worker)
    elt_status = elt_commands.add_parser("status", help="show corpus and transform status")
    elt_status.set_defaults(func=_elt_status)
    workflow = commands.add_parser("workflow", help="compile and rebuild workflow projections")
    workflow_commands = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_compile = workflow_commands.add_parser(
        "compile",
        help="validate and compile configured YAML workflows",
    )
    workflow_compile.add_argument("--config")
    workflow_compile.add_argument("--data-dir")
    workflow_compile.add_argument("--check", action="store_true")
    workflow_compile.add_argument("--force", action="store_true")
    workflow_compile.set_defaults(func=_workflow_compile)
    workflow_rebuild = workflow_commands.add_parser(
        "rebuild",
        help="rebuild serving data and materialized workflow projections",
    )
    workflow_rebuild.add_argument("--db")
    workflow_rebuild.add_argument("--data-dir")
    workflow_rebuild.set_defaults(func=_workflow_rebuild)
    evaluations = commands.add_parser("eval", help="validate or import offline evaluation campaigns")
    evaluation_commands = evaluations.add_subparsers(dest="evaluation_command", required=True)
    evaluation_validate = evaluation_commands.add_parser("validate", help="validate campaign JSONL without writes")
    evaluation_validate.add_argument("path")
    evaluation_validate.set_defaults(func=_eval_validate)
    evaluation_import = evaluation_commands.add_parser("import", help="import a validated campaign JSONL")
    evaluation_import.add_argument("path")
    evaluation_import.add_argument("--db")
    evaluation_import.add_argument("--data-dir")
    evaluation_import.set_defaults(func=_eval_import)
    return parser


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {"init", "validate", "run"}:
        try:
            from witdem_sdk.cli import main as sdk_main  # type: ignore[import-not-found]
        except ImportError:
            raise SystemExit(
                f"witdem {sys.argv[1]} requires witdem-sdk; install it with 'pip install witdem-sdk'"
            ) from None
        raise SystemExit(sdk_main(sys.argv[1:]))
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
