#!/usr/bin/env python3
"""Generate deterministic public OpenAPI snapshots from the application code."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIRECTORY = ROOT / "docs" / "openapi"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        return str(tomllib.load(project_file)["project"]["version"])


def _schemas() -> dict[str, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="witdem-docs-") as data_directory:
        os.environ["WITDEM_DATA_DIR"] = data_directory
        os.environ["WITDEM_DB_PATH"] = str(Path(data_directory) / "docs.duckdb")

        from witdem.api import app as ingestion_app
        from witdem.dashboard import create_dashboard_app

        analytics_app = create_dashboard_app(database=Path(data_directory) / "docs.duckdb")
        schemas = {
            "analytics.json": analytics_app.openapi(),
            "ingestion.json": ingestion_app.openapi(),
        }
        # Editable environments can retain metadata from an older installed
        # wheel. Documentation always describes the source tree being built.
        schemas["ingestion.json"]["info"]["version"] = _project_version()
        return schemas


def _render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed schemas differ from the code")
    args = parser.parse_args()

    stale: list[Path] = []
    for filename, schema in _schemas().items():
        path = OUTPUT_DIRECTORY / filename
        rendered = _render(schema)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")

    if stale:
        for path in stale:
            print(f"OpenAPI snapshot is stale: {path.relative_to(ROOT)}")
        print("Run: python scripts/docs/generate_openapi.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
