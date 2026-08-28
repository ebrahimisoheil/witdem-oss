#!/usr/bin/env python3
"""Validate release identity before any artifact is published."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI job
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip() if result.returncode == 0 else ""


def _expect(label: str, actual: object, expected: object, errors: list[str]) -> None:
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, found {actual!r}")


def _locked_project_version(lock: dict[str, Any], name: str) -> str | None:
    for package in lock.get("package", []):
        if isinstance(package, dict) and package.get("name") == name:
            return str(package.get("version"))
    return None


def validate(component: str, tag: str | None, *, require_clean: bool) -> list[str]:
    release = _json(ROOT / "release.json")
    compatibility = _json(ROOT / "compatibility.json")
    analytics_config = _toml(ROOT / "pyproject.toml")
    sdk_config = _toml(ROOT / "witdem-sdk" / "pyproject.toml")
    analytics = analytics_config["project"]
    sdk = sdk_config["project"]
    npm = _json(ROOT / "npm" / "package.json")
    npm_lock = _json(ROOT / "npm" / "package-lock.json")
    dashboard = _json(ROOT / "web" / "package.json")
    dashboard_lock = _json(ROOT / "web" / "package-lock.json")
    analytics_lock = _toml(ROOT / "uv.lock")
    sdk_lock = _toml(ROOT / "witdem-sdk" / "uv.lock")
    server_protocol = runpy.run_path(str(ROOT / "src" / "witdem" / "protocol.py"))
    sdk_protocol = runpy.run_path(str(ROOT / "witdem-sdk" / "src" / "witdem_sdk" / "_protocol.py"))
    errors: list[str] = []

    platform_version = str(release["platform_version"])
    sdk_version = str(release["sdk_version"])
    _expect("analytics package version", analytics["version"], platform_version, errors)
    _expect("npm launcher version", npm["version"], platform_version, errors)
    _expect("dashboard version", dashboard["version"], platform_version, errors)
    _expect("SDK package version", sdk["version"], sdk_version, errors)
    _expect(
        "analytics lock version",
        _locked_project_version(analytics_lock, "witdem-analytics"),
        platform_version,
        errors,
    )
    _expect("SDK lock version", _locked_project_version(sdk_lock, "witdem-sdk"), sdk_version, errors)
    for example_lock_path in sorted((ROOT / "examples").rglob("uv.lock")):
        example_lock = _toml(example_lock_path)
        relative = example_lock_path.relative_to(ROOT)
        locked_analytics = _locked_project_version(example_lock, "witdem-analytics")
        locked_sdk = _locked_project_version(example_lock, "witdem-sdk")
        if locked_analytics is not None:
            _expect(f"{relative} analytics version", locked_analytics, platform_version, errors)
        if locked_sdk is not None:
            _expect(f"{relative} SDK version", locked_sdk, sdk_version, errors)
    _expect("npm lock version", npm_lock["version"], platform_version, errors)
    _expect("dashboard lock version", dashboard_lock["version"], platform_version, errors)
    _expect("analytics CLI ownership", analytics.get("scripts", {}).get("witdem"), "witdem.cli:main", errors)
    if "witdem" in sdk.get("scripts", {}):
        errors.append("witdem-sdk must not publish the shared 'witdem' console script")
    _expect("SDK CLI ownership", sdk.get("scripts", {}).get("witdem-sdk"), "witdem_sdk.cli:main", errors)
    _expect(
        "semantic protocol",
        compatibility["semantic_record_protocol"],
        release["semantic_record_protocol"],
        errors,
    )
    _expect(
        "server semantic protocol",
        server_protocol["SEMANTIC_RECORD_PROTOCOL_VERSION"],
        release["semantic_record_protocol"],
        errors,
    )
    _expect(
        "SDK semantic protocol",
        sdk_protocol["SEMANTIC_RECORD_PROTOCOL_VERSION"],
        release["semantic_record_protocol"],
        errors,
    )
    _expect(
        "dashboard API contract",
        server_protocol["DASHBOARD_API_VERSION"],
        release["dashboard_api_version"],
        errors,
    )
    _expect(
        "corpus schema contract",
        server_protocol["CORPUS_SCHEMA_VERSION"],
        release["corpus_schema_version"],
        errors,
    )
    _expect("supported Python", analytics["requires-python"], ">=3.10,<3.14", errors)
    _expect("SDK supported Python", sdk["requires-python"], ">=3.10,<3.14", errors)
    _expect(
        "analytics build backend",
        analytics_config["build-system"]["requires"],
        ["hatchling==1.32.0"],
        errors,
    )
    _expect(
        "SDK build backend",
        sdk_config["build-system"]["requires"],
        ["hatchling==1.32.0"],
        errors,
    )
    for project_path in sorted((ROOT / "examples").rglob("pyproject.toml")):
        project_config = _toml(project_path)
        if "witdem-sdk" not in project_config.get("tool", {}).get("uv", {}).get("sources", {}):
            continue
        relative = project_path.relative_to(ROOT)
        _expect(
            f"{relative} uv version",
            project_config.get("tool", {}).get("uv", {}).get("required-version"),
            "==0.12.5",
            errors,
        )

    image_reference = f"ghcr.io/ebrahimisoheil/witdem-analytics:{platform_version}"
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    if image_reference not in compose_text:
        errors.append(f"docker-compose.yml does not default to {image_reference}")

    expected_tag = f"analytics-v{platform_version}" if component == "platform" else f"sdk-v{sdk_version}"
    observed_tag = tag
    if not observed_tag and os.getenv("GITHUB_REF_TYPE") == "tag":
        observed_tag = os.getenv("GITHUB_REF_NAME")
    if observed_tag:
        _expect("release tag", observed_tag, expected_tag, errors)

    existing_tag_commit = _git("rev-parse", f"refs/tags/{expected_tag}^{{commit}}", check=False)
    head_commit = _git("rev-parse", "HEAD")
    if existing_tag_commit and existing_tag_commit != head_commit:
        errors.append(
            f"version reuse refused: {expected_tag} already identifies {existing_tag_commit}, not {head_commit}"
        )
    if observed_tag and not existing_tag_commit:
        errors.append(f"release tag {expected_tag} is not available in the checkout")
    if require_clean and _git("status", "--porcelain"):
        errors.append("release worktree is not clean")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", choices=("platform", "sdk"), required=True)
    parser.add_argument("--tag")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    errors = validate(args.component, args.tag, require_clean=args.require_clean)
    if errors:
        print("Release verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Release verification passed for {args.component}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
