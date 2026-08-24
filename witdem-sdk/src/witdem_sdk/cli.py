"""Small application-side CLI for initializing and validating Witdem projects."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from witdem_sdk._contract import load_project_config

_TEMPLATE = """version: 1
service:
  name: {service_name}
  description: The agent or workflow being observed.
  runtime: {runtime}
telemetry:
  endpoint: http://localhost:4318
  mode: auto
  capture_content: false
contracts:
  - name: application_run
    description: One completed application request.
    result:
      name: Application result
      description: The useful result returned to the user.
      values:
        completed: A useful result was returned.
        unresolved: The request could not be completed.
    product_goal:
      name: Useful result returned
      description: The application returned the result the user needed.
    dimensions:
      request_type:
        name: Request type
"""


def init_project(directory: Path, *, service_name: str, runtime: str, force: bool = False) -> Path:
    target_dir = directory / ".witdem"
    target = target_dir / "witdem.yaml"
    if target.exists() and not force:
        raise SystemExit(f"{target} already exists; use --force to replace it")
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_TEMPLATE.format(service_name=service_name, runtime=runtime), encoding="utf-8")
    return target


def _delegate_to_analytics(argv: list[str]) -> int:
    try:
        from witdem.cli import main as analytics_main  # type: ignore[import-not-found]
    except ImportError:
        raise SystemExit(f"unknown SDK command: {argv[0] if argv else ''}") from None
    original = sys.argv
    try:
        sys.argv = [original[0], *argv]
        analytics_main_any = cast(Callable[[], Any], analytics_main)
        result = analytics_main_any()
        return int(result or 0)
    finally:
        sys.argv = original


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] not in {"init", "validate", "run"}:
        return _delegate_to_analytics(arguments)
    parser = argparse.ArgumentParser(prog="witdem")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a declarative Witdem application contract")
    init.add_argument("--service-name", help="defaults to the project directory name")
    init.add_argument("--runtime", default="application")
    init.add_argument("--directory", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true")
    validate = commands.add_parser("validate", help="validate .witdem/witdem.yaml")
    validate.add_argument("--config", type=Path)
    run = commands.add_parser("run", help="run a command with validated Witdem configuration")
    run.add_argument("command_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    if args.command == "init":
        service_name = args.service_name or args.directory.resolve().name
        target = init_project(args.directory, service_name=service_name, runtime=args.runtime, force=args.force)
        print(f"Created {target}")
        return 0
    if args.command == "validate":
        config = load_project_config(args.config, required=True)
        assert config is not None
        print(f"Valid Witdem configuration for {config.service.name} ({len(config.contracts)} contracts)")
        return 0
    config = load_project_config(required=True)
    assert config is not None
    if not args.command_args:
        raise SystemExit("witdem run requires a command")
    return subprocess.call(args.command_args)


if __name__ == "__main__":
    raise SystemExit(main())
