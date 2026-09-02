from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_COMMANDS = {
    "up",
    "open",
    "status",
    "logs",
    "doctor",
    "version",
    "update",
    "down",
    "dev",
    "workflow",
}


def test_cli_reference_matches_python_and_npx_help() -> None:
    python_help = subprocess.run(
        [sys.executable, "-m", "witdem.cli", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    npx_help = subprocess.run(
        ["node", "npm/bin/witdem.mjs", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    reference = (ROOT / "docs/cli-reference.md").read_text(encoding="utf-8")
    for command in LIFECYCLE_COMMANDS:
        assert re.search(rf"\b{command}\b", python_help)
        assert re.search(rf"\b{command}\b", npx_help)
        assert f"witdem {command}" in reference


def test_primary_user_docs_do_not_pin_stale_example_versions() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "docs/getting-started.md",
        ROOT / "docs/operations.md",
        ROOT / "docs/npm-launcher.md",
        ROOT / "docs/upgrade.md",
        ROOT / "docs/cli-reference.md",
        ROOT / "docs/troubleshooting.md",
        ROOT / "docs/workflow-replay.md",
        ROOT / "docs/integrations/haystack.md",
        ROOT / "docs/integrations/langgraph.md",
        ROOT / "docs/integrations/langchain.md",
    ]
    for path in paths:
        assert not re.search(r"\b0\.3\.\d+\b", path.read_text(encoding="utf-8")), path
