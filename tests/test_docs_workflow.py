from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("owner", ["Kaufman-AIS", "ebrahimisoheil"])
def test_documentation_image_normalizes_owner_case(tmp_path: Path, owner: str) -> None:
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/docs.yml").read_text()
    )
    steps = workflow["jobs"]["build"]["steps"]
    resolve = next(step for step in steps if step.get("id") == "docs-image")
    output = tmp_path / "output"
    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", resolve["run"]],
        check=True,
        env={**os.environ, "GITHUB_REPOSITORY_OWNER": owner, "GITHUB_OUTPUT": str(output)},
    )
    assert output.read_text().strip() == f"name=ghcr.io/{owner.lower()}/witdem-docs"
    publish = next(step for step in steps if step.get("name") == "Publish documentation image")
    assert publish["with"]["tags"].splitlines() == [
        "${{ steps.docs-image.outputs.name }}:latest",
        "${{ steps.docs-image.outputs.name }}:sha-${{ github.sha }}",
    ]
