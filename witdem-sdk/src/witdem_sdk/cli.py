"""Small application-side CLI for initializing and validating Witdem projects."""

from __future__ import annotations

import argparse
import importlib.resources
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from witdem_sdk._contract import load_project_config

_TEMPLATE = """version: 2
service:
  name: {service_name}
  description: The agent or workflow being observed.
telemetry:
  endpoint: http://localhost:4318
  mode: auto
  capture_content: false
contracts: [contracts/application-run.yml]
"""

_CONTRACT_TEMPLATE = """version: 2
id: application_run
name: Application run
description: One completed application request.

result:
  name: Application result
  description: The useful result returned to the user.
  values:
    completed: A useful result was returned.
    unresolved: The request could not be completed.

goal:
  name: Useful result returned
  description: The application returned the result the user needed.
  requirements:
    useful_result:
      name: A useful result was returned
      failure:
        label: No useful result was returned

dimensions:
  request_type:
    name: Request type
"""

_SKILL_NAME = "witdem"
_SKILL_FILES = (Path("SKILL.md"), Path("agents/openai.yaml"))


def _skill_template(relative_path: Path) -> str:
    resource = importlib.resources.files("witdem_sdk").joinpath("templates").joinpath(_SKILL_NAME)
    for part in relative_path.parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def _agent_skill_link(directory: Path) -> Path:
    return directory / ".agents" / "skills" / _SKILL_NAME


def _prepare_agent_skill_link(directory: Path, *, force: bool) -> Path:
    link = _agent_skill_link(directory)
    expected = Path("../../.witdem/skills") / _SKILL_NAME
    if link.is_symlink() and link.readlink() == expected:
        return link
    if link.exists() or link.is_symlink():
        if not force:
            raise SystemExit(f"{link} already exists; use --force to replace it")
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(expected, target_is_directory=True)
    except OSError as exc:
        raise SystemExit(f"could not expose {link} as an agent skill: {exc}") from exc
    return link


def init_project(
    directory: Path,
    *,
    service_name: str,
    force: bool = False,
    expose_agent_skill: bool = False,
) -> Path:
    target_dir = directory / ".witdem"
    target = target_dir / "witdem.yaml"
    contract = target_dir / "contracts" / "application-run.yml"
    skill_dir = target_dir / "skills" / _SKILL_NAME
    if skill_dir.is_symlink():
        raise SystemExit(f"{skill_dir} must be a directory, not a symbolic link")
    generated_files = [target, contract, *(skill_dir / path for path in _SKILL_FILES)]
    if expose_agent_skill:
        generated_files.append(_agent_skill_link(directory))
    if not force:
        existing = next((path for path in generated_files if path.exists() or path.is_symlink()), None)
        if existing is not None:
            raise SystemExit(f"{existing} already exists; use --force to replace it")
    target_dir.mkdir(parents=True, exist_ok=True)
    contract.parent.mkdir(parents=True, exist_ok=True)
    skill_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(_TEMPLATE.format(service_name=service_name), encoding="utf-8")
    contract.write_text(_CONTRACT_TEMPLATE, encoding="utf-8")
    for relative_path in _SKILL_FILES:
        destination = skill_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_skill_template(relative_path), encoding="utf-8")
    if expose_agent_skill:
        _prepare_agent_skill_link(directory, force=force)
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
    if arguments and arguments[0] not in {"init", "validate", "run", "-h", "--help"}:
        return _delegate_to_analytics(arguments)
    parser = argparse.ArgumentParser(prog="witdem-sdk")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a declarative Witdem application contract")
    init.add_argument("--service-name", help="defaults to the project directory name")
    init.add_argument("--directory", type=Path, default=Path.cwd())
    init.add_argument("--force", action="store_true")
    init.add_argument(
        "--expose-agent-skill",
        action="store_true",
        help="link .agents/skills/witdem to the canonical skill under .witdem",
    )
    validate = commands.add_parser("validate", help="validate .witdem/witdem.yaml")
    validate.add_argument("--config", type=Path)
    run = commands.add_parser("run", help="run a command with validated Witdem configuration")
    run.add_argument("command_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    if args.command == "init":
        service_name = args.service_name or args.directory.resolve().name
        target = init_project(
            args.directory,
            service_name=service_name,
            force=args.force,
            expose_agent_skill=args.expose_agent_skill,
        )
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
        raise SystemExit("witdem-sdk run requires a command")
    return subprocess.call(args.command_args)


if __name__ == "__main__":
    raise SystemExit(main())
