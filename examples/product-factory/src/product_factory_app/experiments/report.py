"""Generate a short inspectability report from the collected execution corpus."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate_report(data_dir: Path, output: Path) -> Path:
    """Summarize runtime, semantic, and experiment artifacts without imposing a schema."""

    spans = _read_jsonl(data_dir / "telemetry" / "spans.jsonl")
    events = _read_jsonl(data_dir / "analytics" / "events.jsonl")
    experiments = _read_jsonl(data_dir / "experiments" / "runs.jsonl")
    span_names = Counter(item.get("name", "unknown") for item in spans)
    event_types = Counter(item.get("type", "unknown") for item in events)
    trace_ids = {item.get("trace_id") for item in spans if item.get("trace_id")}
    completed = sum(bool(item.get("execution_completed")) for item in experiments)
    valid = sum(bool(item.get("result_valid")) for item in experiments)
    accepted = sum(bool(item.get("accepted_result", item.get("accepted_success"))) for item in experiments)
    report = f"""# Telemetry and execution findings

Generated from the local execution corpus in `{data_dir}`.

## Corpus

- Runtime spans: {len(spans)}
- Distinct traces: {len(trace_ids)}
- Semantic events: {len(events)}
- Experiment measurements: {len(experiments)}
- Completed experiment executions: {completed}
- Valid structured results: {valid}
- Accepted experiment results: {accepted}

## Runtime telemetry exposed

Observed span names:

{chr(10).join(f"- `{name}`: {count}" for name, count in sorted(span_names.items())) or "- No spans collected yet."}

The local span export preserves trace IDs, span IDs, parent span IDs, timestamps, status, attributes, events, resource metadata, and instrumentation scope. Haystack component and pipeline spans provide execution order and nesting; model/tool details depend on the configured provider and whether content tracing is enabled.

## Semantic instrumentation

Observed event types:

{chr(10).join(f"- `{name}`: {count}" for name, count in sorted(event_types.items())) or "- No semantic events collected yet."}

Semantic events add application meaning such as research completeness, continuation decisions, profile validation, and outcomes. They carry an event ID, schema version, workflow version, execution ID, and trace/span identifiers. They do not duplicate the raw runtime span corpus.

## Branches, iteration, and recovery

The workflow records repeated research passes in the persisted state and semantic decision events. Recoverable source failures are represented in the run notes and can produce a `recovered` outcome. Terminal failures retain their manifest, state, error, and trace correlation instead of disappearing.

## Experiment observations

The initial measurements preserve execution completion, structured validity, accepted-result status, quality, model-cost scope, duration, research passes, model/tool calls, source failures, profile repairs, and the captured application configuration. Total execution cost remains explicitly unknown until billable tools and embeddings are measured; no cost is fabricated.

This report is descriptive. It does not finalize a universal analytics schema. Further analytics decisions should be based on additional real executions and model comparisons.
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report)
    return output
