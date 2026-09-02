from __future__ import annotations

import json

import duckdb
import pytest

from witdem.evaluation_campaigns import validate_jsonl
from witdem.ingest.live_db import initialize_analytics_store, store_evaluation_campaign


def test_validate_and_import_framework_neutral_campaign(tmp_path) -> None:
    source = tmp_path / "campaign.jsonl"
    rows = [
        {
            "record_type": "campaign",
            "campaign_id": "campaign-1",
            "suite_id": "quality",
            "workflow_id": "workflow-1",
            "dataset_id": "contracts",
            "dataset_version": "2026-08",
            "candidate_version": "candidate-a",
        },
        {
            "record_type": "result",
            "campaign_id": "campaign-1",
            "case_id": "case-1",
            "evaluation_key": "accuracy",
            "score": 0.9,
            "passed": True,
            "target": 0.8,
            "direction": "at_least",
            "evaluator_type": "code",
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    campaign, results = validate_jsonl(source)
    database = tmp_path / "analytics.duckdb"
    initialize_analytics_store(database)

    store_evaluation_campaign(database, campaign, results)

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM evaluation_campaigns").fetchone() == (1,)
        assert connection.execute("SELECT passed FROM evaluation_case_results").fetchone() == (True,)
    finally:
        connection.close()


def test_campaign_requires_explicit_observation(tmp_path) -> None:
    source = tmp_path / "invalid.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record_type": "campaign",
                        "campaign_id": "campaign-1",
                        "suite_id": "quality",
                        "workflow_id": "workflow-1",
                        "dataset_id": "dataset",
                        "dataset_version": "1",
                        "candidate_version": "candidate",
                    }
                ),
                json.dumps(
                    {
                        "record_type": "result",
                        "campaign_id": "campaign-1",
                        "case_id": "case-1",
                        "evaluation_key": "accuracy",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires a value"):
        validate_jsonl(source)
