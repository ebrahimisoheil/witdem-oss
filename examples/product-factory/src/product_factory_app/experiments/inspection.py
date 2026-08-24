"""Small read-only helpers for following one execution across local artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory_app.analytics.semantic import product_factory_stage_map
from witdem.analytics import (
    Event,
    derive_replay_graph,
    derive_runtime_insights,
    find_similar_executions,
    normalize_haystack_spans,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _core_events(records: list[dict[str, Any]]) -> list[Event]:
    """Read canonical semantic events."""

    allowed = {
        "event_id",
        "execution_id",
        "trace_id",
        "span_id",
        "timestamp",
        "type",
        "name",
        "payload",
        "schema_version",
    }
    normalized: list[Event] = []
    for record in records:
        values = {key: value for key, value in record.items() if key in allowed}
        normalized.append(Event.model_validate(values))
    return normalized


def inspect_execution(data_dir: Path, execution_id: str) -> dict[str, Any]:
    """Return the persisted run plus matching spans, semantic events, and experiments."""

    run_path = data_dir / "runs" / execution_id / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else None
    trace_id = run.get("manifest", {}).get("trace_id") if run else None
    spans = [item for item in _read_jsonl(data_dir / "telemetry" / "spans.jsonl") if item.get("trace_id") == trace_id]
    events = [
        item
        for item in _read_jsonl(data_dir / "analytics" / "events.jsonl")
        if item.get("execution_id") == execution_id
    ]
    experiments = [
        item
        for item in _read_jsonl(data_dir / "experiments" / "runs.jsonl")
        if item.get("execution_id") == execution_id
    ]
    return {
        "execution_id": execution_id,
        "run": run,
        "spans": spans,
        "semantic_events": events,
        "experiment_records": experiments,
    }


def validate_correlation(data_dir: Path, execution_id: str) -> dict[str, Any]:
    """Check the stable execution-to-artifact correlation contract."""

    inspected = inspect_execution(data_dir, execution_id)
    errors: list[str] = []
    run = inspected["run"]
    if run is None:
        errors.append("persisted run is missing")
        return {"execution_id": execution_id, "valid": False, "errors": errors}
    trace_id = run.get("manifest", {}).get("trace_id")
    if not trace_id:
        errors.append("run manifest has no trace_id")
    if not inspected["spans"]:
        errors.append("no runtime spans match manifest.trace_id")
    events = inspected["semantic_events"]
    if not events:
        errors.append("no semantic events match execution_id")
    for event in events:
        if event.get("trace_id") != trace_id:
            errors.append(f"semantic event {event.get('event_id')} has a mismatched trace_id")
        if event.get("workflow_version") != run.get("manifest", {}).get("workflow_version"):
            errors.append(f"semantic event {event.get('event_id')} has a mismatched workflow_version")
    if not inspected["experiment_records"]:
        errors.append("no experiment record matches execution_id")
    return {
        "execution_id": execution_id,
        "trace_id": trace_id,
        "valid": not errors,
        "errors": errors,
        "span_count": len(inspected["spans"]),
        "semantic_event_count": len(events),
        "experiment_record_count": len(inspected["experiment_records"]),
    }


def runtime_analysis(data_dir: Path, execution_id: str, *, domain_enrichment: bool = True) -> dict[str, Any]:
    """Return normalized graph, replay, and deterministic insights for one run."""

    inspected = inspect_execution(data_dir, execution_id)
    if not inspected["spans"]:
        raise ValueError(f"no runtime spans found for execution {execution_id}")
    run = inspected["run"] or {}
    manifest = run.get("manifest", {})
    graph = normalize_haystack_spans(
        inspected["spans"],
        execution_id=execution_id,
        runtime_id=manifest.get("workflow_version"),
        providers=manifest.get("providers", []),
    )
    events = _core_events(inspected["semantic_events"])
    replay = derive_replay_graph(
        graph,
        events=events,
        semantic_stage_map=product_factory_stage_map() if domain_enrichment else None,
    )
    stage_map = product_factory_stage_map() if domain_enrichment else None
    insights = derive_runtime_insights(graph, run=run, events=events, semantic_stage_map=stage_map)
    return {
        "execution_id": execution_id,
        "normalized": graph.model_dump(mode="json"),
        "replay": replay.model_dump(mode="json"),
        "insights": insights,
        "domain_enriched": domain_enrichment,
    }


def similar_executions(data_dir: Path, execution_id: str) -> dict[str, Any]:
    """Compare one execution with available local corpus executions."""

    target = runtime_analysis(data_dir, execution_id, domain_enrichment=False)
    candidates: list[dict[str, Any]] = []
    for run_path in sorted((data_dir / "runs").glob("*/run.json")):
        candidate_id = run_path.parent.name
        if candidate_id == execution_id:
            continue
        try:
            analysis = runtime_analysis(data_dir, candidate_id, domain_enrichment=False)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        candidates.append({"execution_id": candidate_id, **analysis["insights"]})
    target_record = {"execution_id": execution_id, **target["insights"]}
    return {
        "execution_id": execution_id,
        "matches": find_similar_executions(target_record, candidates),
        "candidate_count": len(candidates),
    }
