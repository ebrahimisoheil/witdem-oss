"""Local, inspectable persistence for research runs and event streams."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from product_factory_app.domain.models import ResearchRun


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


class RunStore:
    """Persist complete runs and preserve a small JSONL event corpus."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def run_dir(self, execution_id: str) -> Path:
        return self.root / "runs" / execution_id

    def save_run(self, run: ResearchRun) -> Path:
        """Write a complete run artifact and return its directory."""

        directory = self.run_dir(run.manifest.execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / "manifest.json", run.manifest.model_dump(mode="json"))
        _write_json(directory / "run.json", run.model_dump(mode="json"))
        if run.profile:
            _write_json(directory / "result.json", run.profile.model_dump(mode="json"))
        if run.state:
            evidence_path = directory / "evidence.jsonl"
            evidence_path.write_text(
                "".join(
                    json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n" for item in run.state.evidence
                )
            )
        return directory

    def append_jsonl(self, relative_path: str, value: dict[str, Any]) -> Path:
        """Append an event without imposing a universal event schema."""

        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        return path

    def read_run(self, execution_id: str) -> dict[str, Any]:
        """Read a persisted run for inspection."""

        path = self.run_dir(execution_id) / "run.json"
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
