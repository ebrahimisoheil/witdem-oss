from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import pytest
import yaml

from witdem.dashboard import service
from witdem.ingest import live_db
from witdem.workflows import WorkflowDefinition

SIZES = (10, 100, 1_000, 10_000)


def _definition(workflow_id: str) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "version": 2,
            "id": workflow_id,
            "name": f"Benchmark {workflow_id}",
            "stages": [
                {
                    "id": "work",
                    "name": "Work",
                    "nodes": [{"id": "step", "name": "Step", "match": {"names": ["step"]}}],
                }
            ],
        }
    )


@pytest.mark.integration
def test_materialized_workflow_queries_scale_to_ten_thousand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "live.duckdb"
    live_db.initialize_analytics_store(database)
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    references = []
    rows: list[list[object]] = []
    projected_at = "2026-08-30T00:00:00Z"
    for size in SIZES:
        definition = _definition(f"scale-{size}")
        definition_path = workflow_dir / f"scale-{size}.yaml"
        definition_path.write_text(
            yaml.safe_dump(definition.model_dump(mode="json", by_alias=True), sort_keys=False),
            encoding="utf-8",
        )
        references.append({"id": definition.id, "definition": str(definition_path.relative_to(tmp_path))})
        for index in range(size):
            execution_id = f"{definition.id}-{index:05d}"
            projection = {
                "workflow": {**definition.api_dict(), "template_hash": definition.template_hash},
                "execution": {
                    "execution_id": execution_id,
                    "status": "completed",
                    "runtime_outcome": "completed",
                    "duration_seconds": 0.1,
                    "known_cost": 0.001,
                    "total_tokens": 10,
                },
                "stages": [],
                "nodes": [
                    {
                        "id": "step",
                        "state": "completed",
                        "attempts": 1,
                        "models": ["benchmark-model"],
                        "providers": ["benchmark-provider"],
                    }
                ],
                "transitions": [],
                "outcomes": [],
                "discrepancies": {"unexpected_operations": [], "unexpected_transitions": []},
            }
            rows.append(
                [
                    execution_id,
                    definition.id,
                    definition.template_hash,
                    "1",
                    json.dumps(projection),
                    projected_at,
                ]
            )
    config = tmp_path / "witdem.yaml"
    config.write_text(
        yaml.safe_dump({"version": 2, "workflows": [item["definition"] for item in references]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("WITDEM_CONFIG", str(config))
    connection = duckdb.connect(str(database))
    try:
        connection.executemany(
            "INSERT INTO workflow_execution_projections VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    finally:
        connection.close()

    for size in SIZES:
        with service.repository(database) as repo:
            started = time.perf_counter()
            detail = service.workflow_detail(repo, f"scale-{size}")
            cold_seconds = time.perf_counter() - started
            started = time.perf_counter()
            warm_detail = service.workflow_detail(repo, f"scale-{size}")
            warm_seconds = time.perf_counter() - started
        assert detail is not None and warm_detail is not None
        assert detail["execution_count"] == size
        assert len(detail["executions"]) == min(size, 100)
        assert cold_seconds < 1.0, (size, cold_seconds)
        assert warm_seconds < 0.5, (size, warm_seconds)
