"""Validated atomic publisher for Duckle canonical staging bundles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from witdem.analytics.core import Execution, Link, Operation
from witdem.ingest import live_db
from witdem.ingest.correlate import build_semantic_record


def _json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("canonical staging collection must be a JSON list")
    return [dict(item) for item in value if isinstance(item, Mapping)]


def publish_staging_row(row: Mapping[str, Any]) -> str:
    execution = Execution.model_validate_json(str(row["execution_json"]))
    operations = [Operation.model_validate(item) for item in _json_list(row.get("operations_json"))]
    links = [Link.model_validate(item) for item in _json_list(row.get("links_json"))]
    semantic_by_id: dict[str, Any] = {}
    for item in _json_list(row.get("sdk_records_json")):
        semantic = build_semantic_record(item)
        identifier = (
            getattr(semantic, "event_id", None)
            or getattr(semantic, "evaluation_id", None)
            or getattr(semantic, "outcome_id", None)
        )
        semantic_by_id[str(identifier)] = semantic
    live_db.publish_transformed_bundle(
        execution,
        operations,
        links,
        list(semantic_by_id.values()),
        operation_classifications=_json_list(row.get("operation_classifications_json") or []),
        operation_measurements=_json_list(row.get("operation_measurements_json") or []),
    )
    return execution.execution_id
