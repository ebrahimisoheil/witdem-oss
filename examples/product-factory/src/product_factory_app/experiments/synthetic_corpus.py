"""Deterministic synthetic execution corpus built from retained runtime evidence.

The generator deliberately emits the same raw-ish artifacts that the local
inspection path already reads: Haystack/OpenTelemetry spans, optional semantic
events, and Product Factory-style run records.  The canonical analytical
tables are Parquet snapshots of the existing core records and are derived only
after the normalizer has run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from product_factory_app.experiments.cost import PRICE_SNAPSHOT_VERSION
from product_factory_app.experiments.inspection import runtime_analysis
from witdem.analytics import (
    AGGREGATE_COLUMNS,
    ANALYTICS_COLUMN_TYPES,
    ANALYTICS_COLUMNS,
    ANALYTICS_TABLES,
    V2_ANALYTICS_TABLES,
    Evaluation,
    Event,
    Execution,
    Link,
    NormalizedExecutionGraph,
    Operation,
    Outcome,
    canonical_operation_key,
    canonical_path_signature,
    canonical_stage_key,
    canonical_tool_key,
    derive_replay_graph,
    derive_runtime_insights,
    display_operation,
    display_path,
    display_stage,
    display_tool,
    normalize_haystack_spans,
)

GENERATOR_VERSION = "synthetic-ui-v1.0.0"
TEMPLATE_VERSION = "validated-fixture-adapter-v1"
CORPUS_VARIANTS = {
    "v1": {
        "generator_version": GENERATOR_VERSION,
        "template_version": TEMPLATE_VERSION,
        "scenario_prefix": "synthetic-ui-v1",
    },
    "v2": {
        "generator_version": "synthetic-ui-v2.0.0",
        "template_version": "retained-real-template-resampler-v2",
        "scenario_prefix": "synthetic-ui-v2",
    },
}
CORE_SCHEMA_VERSION = "0.1.0"
SEMANTIC_SCHEMA_VERSION = "0.2.0"
DEFAULT_SEED = 20260821
DEFAULT_EXECUTION_COUNT = 1_000
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

ROOT = Path(__file__).resolve().parents[3]
COMPAT_ROOT = ROOT / "data" / "haystack-compatibility-20260821"
PRODUCT_ROOTS = (
    ROOT / "data" / "live-role-isolation-v1",
    ROOT / "data" / "live-three-model-full-20260821",
    ROOT / "data" / "live-heterogeneous-v2",
    ROOT / "data" / "deterministic-experiment-v4",
    ROOT / "data" / "real-company-corpus-v4",
)

PROVIDER_MODELS: dict[str, tuple[str, str]] = {
    "openai": ("gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"),
    "mistral": ("mistral-small-2603", "mistral-small-2603"),
    "deepseek": ("deepseek-v4-flash", "deepseek-v4-flash"),
}
PROVIDER_POOL = tuple(PROVIDER_MODELS)

# The compatibility share intentionally remains the majority of the corpus.
FAMILY_COUNTS = {
    "linear": 120,
    "conditional_short": 80,
    "conditional_long": 80,
    "loop": 130,
    "agent": 150,
    "fallback": 70,
    "nested": 70,
    "clean_accepted": 40,
    "targeted_research_loop": 35,
    "repair_success": 35,
    "repair_terminal_validation_failure": 30,
    "source_tool_failure_recovery": 30,
    "bounded_uncertainty": 30,
    "valid_but_not_accepted": 20,
    "mixed_provider_roles": 80,
}

_IDENTITY_LEAK_RE = re.compile(r"(?i)(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24,})")


@dataclass(frozen=True)
class SourceTemplate:
    family: str
    source_reference: str
    spans: tuple[dict[str, Any], ...]
    run: dict[str, Any] | None
    events: tuple[dict[str, Any], ...]
    source_execution_id: str | None = None
    source_trace_id: str | None = None


@dataclass
class GeneratedExecution:
    execution: Execution
    operations: list[Operation]
    links: list[Link]
    events: list[Event]
    evaluations: list[Evaluation]
    outcomes: list[Outcome]
    spans: list[dict[str, Any]]
    run: dict[str, Any]
    family: str
    domain_enriched: bool
    insights: dict[str, Any]


def _stable_hex(seed: int, *parts: object, length: int = 32) -> str:
    payload = json.dumps([seed, *parts], sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:length]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1_000_000_000, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _known_times(values: Iterable[Any]) -> list[datetime]:
    result: list[datetime] = []
    for value in values:
        parsed = _parse_time(value)
        if parsed is not None:
            result.append(parsed)
    return result


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _hash_int(seed: int, *parts: object) -> int:
    return int(_stable_hex(seed, *parts, length=16), 16)


def _scale(seed: int, index: int, family: str) -> float:
    return 0.78 + (_hash_int(seed, "timing", index, family) % 51) / 100


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _replace_recursive(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for old, new in replacements.items():
            if old:
                result = result.replace(old, new)
        return result
    if isinstance(value, list):
        return [_replace_recursive(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_recursive(item, replacements) for key, item in value.items()}
    return value


def _compatibility_templates() -> dict[str, SourceTemplate]:
    result: dict[str, SourceTemplate] = {}
    for path in sorted((COMPAT_ROOT / "scenarios").glob("*/telemetry/spans.jsonl")):
        family = path.parents[1].name
        spans = _read_jsonl(path)
        result[family] = SourceTemplate(
            family=family,
            source_reference=str(path.relative_to(ROOT)),
            spans=tuple(spans),
            run=None,
            events=(),
            source_trace_id=str(spans[0].get("trace_id")) if spans else None,
        )
    return result


def _classify_product_run(run: Mapping[str, Any]) -> str:
    manifest = run.get("manifest") or {}
    state = run.get("state") or {}
    acceptance = run.get("acceptance") or {}
    providers = manifest.get("providers") or []
    if len({str(item.get("provider")) for item in providers if item.get("provider")}) > 1:
        return "mixed_provider_roles"
    if int(state.get("profile_repair_count") or 0) > 0:
        return "repair_success" if acceptance.get("status") == "accepted" else "repair_terminal_validation_failure"
    if int(state.get("source_failures") or 0) > 0 and manifest.get("status") == "recovered":
        return "source_tool_failure_recovery"
    if not run.get("execution_completed") and "research incomplete" in str(manifest.get("error") or ""):
        return "bounded_uncertainty"
    if run.get("execution_completed") and run.get("result_valid") and acceptance.get("status") != "accepted":
        return "valid_but_not_accepted"
    if int(state.get("research_pass") or 0) > 1 and state.get("targeted_research_routes"):
        return "targeted_research_loop"
    if acceptance.get("status") == "accepted":
        return "clean_accepted"
    return "clean_accepted"


def _product_templates() -> dict[str, list[SourceTemplate]]:
    candidates: defaultdict[str, list[SourceTemplate]] = defaultdict(list)
    for root in PRODUCT_ROOTS:
        span_rows = _read_jsonl(root / "telemetry" / "spans.jsonl")
        by_trace: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for span in span_rows:
            if span.get("trace_id"):
                by_trace[str(span["trace_id"])].append(span)
        events = _read_jsonl(root / "analytics" / "events.jsonl")
        for run_path in sorted((root / "runs").glob("*/run.json")):
            run = json.loads(run_path.read_text(encoding="utf-8"))
            manifest = run.get("manifest") or {}
            execution_id = str(manifest.get("execution_id") or run_path.parent.name)
            trace_id = str(manifest.get("trace_id") or "")
            spans = by_trace.get(trace_id, [])
            if not spans:
                continue
            family = _classify_product_run(run)
            source_events = [event for event in events if event.get("execution_id") == execution_id]
            candidates[family].append(
                SourceTemplate(
                    family=family,
                    source_reference=str(run_path.relative_to(ROOT)),
                    spans=tuple(spans),
                    run=run,
                    events=tuple(source_events),
                    source_execution_id=execution_id,
                    source_trace_id=trace_id,
                )
            )
    return {family: sorted(items, key=lambda item: item.source_reference) for family, items in candidates.items()}


def load_source_templates() -> dict[str, SourceTemplate | list[SourceTemplate]]:
    """Load retained compatibility fixtures and empirical Product Factory runs."""

    result: dict[str, SourceTemplate | list[SourceTemplate]] = dict(_compatibility_templates())
    result.update(_product_templates())
    missing = [family for family in FAMILY_COUNTS if family not in result]
    if missing:
        raise ValueError(f"retained source fixtures do not cover families: {missing}")
    return result


def _observed_template_evidence(
    sources: Mapping[str, SourceTemplate | list[SourceTemplate]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[int | float]]]:
    """Summarize retained template inventory and numeric anchors for v2."""

    inventory: dict[str, dict[str, Any]] = {}
    anchors: dict[str, list[int | float]] = defaultdict(list)
    for family, value in sorted(sources.items()):
        templates = value if isinstance(value, list) else [value]
        span_count = 0
        source_references: list[str] = []
        for template in templates:
            source_references.append(template.source_reference)
            span_count += len(template.spans)
            for span in template.spans:
                attributes = span.get("attributes") or {}
                for target, key in (
                    ("input_tokens", "gen_ai.usage.input_tokens"),
                    ("output_tokens", "gen_ai.usage.output_tokens"),
                ):
                    anchor_value = attributes.get(key)
                    if isinstance(anchor_value, (int, float)) and not isinstance(anchor_value, bool):
                        anchors[target].append(anchor_value)
                start = span.get("start_time_unix_nano")
                end = span.get("end_time_unix_nano")
                if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
                    anchors["span_duration_seconds"].append((end - start) / 1_000_000_000)
        inventory[family] = {
            "template_count": len(templates),
            "span_count": span_count,
            "source_references": sorted(source_references),
            "domain_enriched_template_count": sum(template.run is not None for template in templates),
        }
    return inventory, dict(anchors)


def _numeric_anchor_summary(anchors: Mapping[str, Sequence[int | float]]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "count": len(values),
            "minimum": min(values),
            "maximum": max(values),
        }
        for name, values in sorted(anchors.items())
        if values
    }


def _family_sequence(execution_count: int) -> list[str]:
    sequence: list[str] = []
    for family, count in FAMILY_COUNTS.items():
        sequence.extend([family] * count)
    if execution_count <= len(sequence):
        return sequence[:execution_count]
    return [sequence[index % len(sequence)] for index in range(execution_count)]


def _provider_for(seed: int, index: int, ordinal: int, family: str, role: str) -> tuple[str, str, str]:
    if family == "mixed_provider_roles":
        provider = {"research": "deepseek", "extraction": "openai"}.get(role, "mistral")
    else:
        provider = PROVIDER_POOL[(index + ordinal + _hash_int(seed, family, role) % 3) % len(PROVIDER_POOL)]
    request_model, response_model = PROVIDER_MODELS[provider]
    return provider, request_model, response_model


def _model_role(row: Mapping[str, Any], ordinal: int) -> str:
    attributes = row.get("attributes") or {}
    if attributes.get("pf.role") or attributes.get("role"):
        return str(attributes.get("pf.role") or attributes.get("role"))
    name = str(row.get("name") or "").casefold()
    if "extract" in name or "profile" in name:
        return "extraction"
    return "research" if ordinal % 2 == 0 else "extraction"


def _adapt_spans(
    template: SourceTemplate,
    *,
    seed: int,
    index: int,
    family: str,
    execution_id: str,
    trace_id: str,
    usage_anchors: Mapping[str, Sequence[int | float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], datetime, datetime]:
    source_spans = list(template.spans)
    old_ids = [str(row.get("span_id")) for row in source_spans if row.get("span_id")]
    span_map = {
        old: _stable_hex(seed, "span", index, family, position, length=16) for position, old in enumerate(old_ids)
    }
    source_times = _known_times(row.get("start_time_unix_nano") for row in source_spans)
    source_origin = min(source_times) if source_times else BASE_TIME
    scale = _scale(seed, index, family)
    anchor = BASE_TIME + timedelta(minutes=index * 20)
    model_ordinal = 0
    adapted: list[dict[str, Any]] = []
    for position, original in enumerate(source_spans):
        row = copy.deepcopy(original)
        old_span_id = str(row.get("span_id") or f"missing-{position}")
        old_trace_id = str(row.get("trace_id") or template.source_trace_id or "")
        row["trace_id"] = trace_id
        row["span_id"] = span_map.get(old_span_id, _stable_hex(seed, "span", index, family, position, length=16))
        old_parent = row.get("parent_span_id")
        row["parent_span_id"] = span_map.get(str(old_parent)) if old_parent else None
        start_value = _parse_time(row.get("start_time_unix_nano"))
        start = start_value if start_value is not None else source_origin
        end_value = _parse_time(row.get("end_time_unix_nano"))
        end = end_value if end_value is not None else start + timedelta(milliseconds=1)
        new_start = anchor + (start - source_origin) * scale
        new_end = anchor + (end - source_origin) * scale
        if new_end <= new_start:
            new_end = new_start + timedelta(microseconds=1)
        row["start_time_unix_nano"] = int(new_start.timestamp() * 1_000_000_000)
        row["end_time_unix_nano"] = int(new_end.timestamp() * 1_000_000_000)
        row["captured_at"] = _iso(new_end)
        row["resource"] = dict(row.get("resource") or {})
        row["resource"]["service.instance.id"] = _stable_hex(seed, "service", index, length=32)
        attributes = dict(row.get("attributes") or {})
        replacements = {
            str(template.source_execution_id or ""): execution_id,
            str(template.source_trace_id or old_trace_id): trace_id,
        }
        attributes = _replace_recursive(attributes, replacements)
        attributes["witdem.execution_id"] = execution_id
        if family.startswith("conditional"):
            attributes["pf.route"] = family.removeprefix("conditional_")
        if row.get("name", "").endswith(".llm") or row.get("name") == "haystack.llm.chat":
            role = _model_role(row, model_ordinal)
            provider, request_model, response_model = _provider_for(seed, index, model_ordinal, family, role)
            model_ordinal += 1
            source_input = attributes.get("gen_ai.usage.input_tokens")
            source_output = attributes.get("gen_ai.usage.output_tokens")
            input_pool = (usage_anchors or {}).get("input_tokens", ())
            output_pool = (usage_anchors or {}).get("output_tokens", ())
            input_tokens = (
                int(source_input)
                if isinstance(source_input, (int, float))
                else (
                    int(input_pool[_hash_int(seed, index, model_ordinal) % len(input_pool)])
                    if input_pool
                    else 180 + (_hash_int(seed, index, model_ordinal) % 2_400)
                )
            )
            output_tokens = (
                int(source_output)
                if isinstance(source_output, (int, float))
                else (
                    int(output_pool[_hash_int(seed, index, model_ordinal, "out") % len(output_pool)])
                    if output_pool
                    else 32 + (_hash_int(seed, index, model_ordinal, "out") % 1_200)
                )
            )
            token_factor = 0.88 + (_hash_int(seed, index, model_ordinal, "tokens") % 25) / 100
            input_tokens = max(1, int(input_tokens * token_factor))
            output_tokens = max(1, int(output_tokens * token_factor))
            attributes.update(
                {
                    "gen_ai.system": provider,
                    "gen_ai.provider.name": provider,
                    "gen_ai.request.model": request_model,
                    "gen_ai.response.model": response_model,
                    "pf.provider_provenance": "observed_invocation_configuration",
                    "pf.role": role,
                    "pf.model_provenance": "observed_provider_response",
                    "gen_ai.usage.input_tokens": input_tokens,
                    "gen_ai.usage.output_tokens": output_tokens,
                    "gen_ai.usage.total_tokens": input_tokens + output_tokens,
                    "pf.usage_provenance": "observed_provider_response",
                }
            )
        row["attributes"] = attributes
        adapted.append(row)
    end_times = [_parse_time(row.get("end_time_unix_nano")) for row in adapted]
    start_times = [_parse_time(row.get("start_time_unix_nano")) for row in adapted]
    return adapted, span_map, min(start for start in start_times if start), max(end for end in end_times if end)


def _adapt_events(
    template: SourceTemplate,
    *,
    seed: int,
    index: int,
    family: str,
    execution_id: str,
    trace_id: str,
    span_map: Mapping[str, str],
    source_origin: datetime,
    anchor: datetime,
    scale: float,
) -> list[Event]:
    result: list[Event] = []
    for position, original in enumerate(template.events):
        row = copy.deepcopy(original)
        timestamp = _parse_time(row.get("timestamp")) or source_origin
        timestamp = anchor + (timestamp - source_origin) * scale
        old_span = row.get("span_id")
        payload_value = row.get("payload")
        payload: dict[str, Any] = dict(payload_value) if isinstance(payload_value, dict) else {}
        name = str(row.get("name") or payload.get("name") or "event")
        event_type = str(row.get("type") or "step")
        result.append(
            Event(
                event_id=_stable_hex(seed, "event", index, position),
                schema_version=SEMANTIC_SCHEMA_VERSION,
                execution_id=execution_id,
                trace_id=trace_id,
                span_id=span_map.get(str(old_span)) if old_span else None,
                timestamp=timestamp,
                type=event_type,
                name=name,
                payload=payload,
            )
        )
    return result


def _minimal_run(
    *,
    execution_id: str,
    trace_id: str,
    family: str,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    providers: list[dict[str, str]],
    tool_present: bool,
    generator_version: str = GENERATOR_VERSION,
    scenario_prefix: str = "synthetic-ui-v1",
) -> dict[str, Any]:
    completed = status not in {"error", "failed"}
    return {
        "manifest": {
            "execution_id": execution_id,
            "artifact_schema_version": SEMANTIC_SCHEMA_VERSION,
            "trace_id": trace_id,
            "scenario": f"{scenario_prefix}:{family}",
            "workflow_version": generator_version,
            "status": "succeeded" if completed else "failed",
            "started_at": _iso(started_at),
            "finished_at": _iso(ended_at),
            "providers": providers,
            "tools": ["lookup"] if tool_present else [],
            "configuration": {"max_research_passes": 2, "max_profile_repairs": 1, "max_model_calls": 12},
            "error": None if completed else "synthetic terminal operation failure",
        },
        "execution_completed": completed,
        "result_valid": None,
        "acceptance": {"status": "unknown"},
        "state": {"source_failures": 0, "targeted_research_routes": [], "dimension_statuses": []},
        "metrics": {"fixture_instrumentation_available": 0.0, "fixture_cost_usd": 0.0},
    }


def _adapt_run(
    template: SourceTemplate,
    *,
    execution_id: str,
    trace_id: str,
    family: str,
    started_at: datetime,
    ended_at: datetime,
    source_origin: datetime,
    anchor: datetime,
    scale: float,
    span_map: Mapping[str, str],
    spans: Sequence[Mapping[str, Any]],
    generator_version: str = GENERATOR_VERSION,
    scenario_prefix: str = "synthetic-ui-v1",
) -> dict[str, Any]:
    if template.run is None:
        providers = []
        for row in spans:
            attributes = row.get("attributes") or {}
            if row.get("name", "").endswith(".llm") or row.get("name") == "haystack.llm.chat":
                providers.append(
                    {
                        "role": str(attributes.get("pf.role") or "research"),
                        "provider": str(attributes.get("gen_ai.provider.name") or "unknown"),
                        "model": str(attributes.get("gen_ai.request.model") or "unknown"),
                    }
                )
        unique = {(item["role"], item["provider"], item["model"]): item for item in providers}
        status = (
            "error"
            if any(str((row.get("status") or {}).get("status_code")) == "StatusCode.ERROR" for row in spans)
            else "ok"
        )
        return _minimal_run(
            execution_id=execution_id,
            trace_id=trace_id,
            family=family,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            providers=list(unique.values()),
            tool_present=any("tool" in str(row.get("name")) for row in spans),
            generator_version=generator_version,
            scenario_prefix=scenario_prefix,
        )
    replacements = {
        str(template.source_execution_id or ""): execution_id,
        str(template.source_trace_id or ""): trace_id,
    }
    run = _replace_recursive(copy.deepcopy(template.run), replacements)
    manifest = run.setdefault("manifest", {})
    manifest.update(
        {
            "execution_id": execution_id,
            "trace_id": trace_id,
            "scenario": f"{scenario_prefix}:{family}",
            "workflow_version": generator_version,
            "started_at": _iso(started_at),
            "finished_at": _iso(ended_at),
        }
    )
    request = manifest.get("request")
    if isinstance(request, dict):
        request["execution_id"] = execution_id
        request["scenario"] = f"{scenario_prefix}:{family}"
    state = run.get("state")
    if isinstance(state, dict):
        request = state.get("request")
        if isinstance(request, dict):
            request["execution_id"] = execution_id
            request["scenario"] = f"{scenario_prefix}:{family}"
    providers = manifest.get("providers")
    if not isinstance(providers, list):
        manifest["providers"] = []
    return cast(dict[str, Any], run)


def _core_event_rows(events: Sequence[Event], generator_version: str = GENERATOR_VERSION) -> list[dict[str, Any]]:
    return [
        {
            **event.model_dump(mode="json"),
            "type": event.type,
            "workflow_version": generator_version,
        }
        for event in events
    ]


def _semantic_records(
    execution_id: str,
    graph: NormalizedExecutionGraph,
    run: Mapping[str, Any],
    seed: int,
    index: int,
    family: str,
) -> tuple[list[Evaluation], list[Outcome]]:
    evaluations: list[Evaluation] = []
    for position, operation in enumerate(graph.operations):
        valid = operation.attributes.get("pf.validation_valid")
        if valid is not None:
            evaluations.append(
                Evaluation(
                    evaluation_id=_stable_hex(seed, "evaluation", index, position),
                    execution_id=execution_id,
                    subject_id=operation.operation_id,
                    name="profile_validation",
                    value={"valid": bool(valid)},
                    label="valid" if valid else "invalid",
                    score=1.0 if valid else 0.0,
                    source="synthetic_fixture_projection",
                    confidence=0.95,
                    definition_version="product-factory-v1",
                    attributes={"template": family},
                )
            )
    acceptance = run.get("acceptance") if isinstance(run, Mapping) else None
    acceptance_status = acceptance.get("status") if isinstance(acceptance, Mapping) else None
    outcome = Outcome(
        outcome_id=_stable_hex(seed, "outcome", index),
        execution_id=execution_id,
        name="execution_outcome",
        status=str(acceptance_status or (run.get("manifest") or {}).get("status") or "unknown"),
        value={"template": family, "result_valid": run.get("result_valid")},
        timestamp=graph.execution.ended_at or graph.execution.started_at or BASE_TIME,
        attributes={"source": "synthetic_fixture_projection"},
    )
    return evaluations, [outcome]


def _events_for_reader(events: Sequence[Event]) -> list[Event]:
    return list(events)


def generate_execution(
    template: SourceTemplate,
    *,
    seed: int,
    index: int,
    family: str,
    generator_version: str = GENERATOR_VERSION,
    scenario_prefix: str = "synthetic-ui-v1",
    usage_anchors: Mapping[str, Sequence[int | float]] | None = None,
) -> GeneratedExecution:
    execution_id = _stable_hex(seed, "execution", index, family)
    trace_id = _stable_hex(seed, "trace", index, family)
    spans, span_map, started_at, ended_at = _adapt_spans(
        template,
        seed=seed,
        index=index,
        family=family,
        execution_id=execution_id,
        trace_id=trace_id,
        usage_anchors=usage_anchors,
    )
    source_times = _known_times(row.get("start_time_unix_nano") for row in template.spans)
    source_origin = min(source_times) if source_times else BASE_TIME
    anchor = BASE_TIME + timedelta(minutes=index * 20)
    scale = _scale(seed, index, family)
    events = _adapt_events(
        template,
        seed=seed,
        index=index,
        family=family,
        execution_id=execution_id,
        trace_id=trace_id,
        span_map=span_map,
        source_origin=source_origin,
        anchor=anchor,
        scale=scale,
    )
    run = _adapt_run(
        template,
        execution_id=execution_id,
        trace_id=trace_id,
        family=family,
        started_at=started_at,
        ended_at=ended_at,
        source_origin=source_origin,
        anchor=anchor,
        scale=scale,
        span_map=span_map,
        spans=spans,
        generator_version=generator_version,
        scenario_prefix=scenario_prefix,
    )
    providers = (run.get("manifest") or {}).get("providers") if isinstance(run, Mapping) else []
    graph = normalize_haystack_spans(
        spans,
        execution_id=execution_id,
        runtime_id=(run.get("manifest") or {}).get("workflow_version"),
        providers=providers if isinstance(providers, list) else [],
    )
    graph.links = [
        link.model_copy(update={"link_id": _stable_hex(seed, "link", index, position)})
        for position, link in enumerate(graph.links)
    ]
    # Runtime-only families intentionally have no semantic events or domain records.
    domain_enriched = template.run is not None
    if not domain_enriched:
        events = []
    evaluations, outcomes = (
        _semantic_records(execution_id, graph, run, seed, index, family) if domain_enriched else ([], [])
    )
    insights = derive_runtime_insights(graph, run=run, events=_events_for_reader(events))
    return GeneratedExecution(
        execution=graph.execution,
        operations=graph.operations,
        links=graph.links,
        events=events,
        evaluations=evaluations,
        outcomes=outcomes,
        spans=spans,
        run=run,
        family=family,
        domain_enriched=domain_enriched,
        insights=insights,
    )


def _parquet_rows(
    records: Iterable[Mapping[str, Any]], columns: Sequence[str], json_columns: set[str]
) -> list[dict[str, Any]]:
    result = []
    for record in records:
        row: dict[str, Any] = {}
        for column in columns:
            value = record.get(column)
            row[column] = _json(value) if column in json_columns and value is not None else value
        result.append(row)
    return result


def _write_parquet(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    normalized_rows = [{column: row.get(column) for column in columns} for row in rows]
    table = pa.Table.from_pylist(normalized_rows)
    pq.write_table(table, path)


def _write_duckdb(output_dir: Path, table_names: Sequence[str] = ANALYTICS_TABLES) -> Path:
    """Materialize the canonical Parquet snapshots as one DuckDB database."""

    import duckdb

    database_path = output_dir / "analytics.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        for table_name in table_names:
            parquet_path = output_dir / f"{table_name}.parquet"
            columns = ANALYTICS_COLUMNS.get(table_name, AGGREGATE_COLUMNS)
            definitions = ", ".join(f'"{column}" {ANALYTICS_COLUMN_TYPES.get(column, "VARCHAR")}' for column in columns)
            connection.execute(f'CREATE OR REPLACE TABLE "{table_name}" ({definitions})')
            try:
                connection.execute(
                    f'INSERT INTO "{table_name}" SELECT '
                    + ", ".join(
                        f'CAST("{column}" AS {ANALYTICS_COLUMN_TYPES.get(column, "VARCHAR")}) AS "{column}"'
                        for column in columns
                    )
                    + " FROM read_parquet(?)",
                    [str(parquet_path)],
                )
            except duckdb.InvalidInputException as error:
                if "at least one non-root column" not in str(error):
                    raise
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    return database_path


def _read_duckdb_rows(database_path: Path, table_name: str) -> list[dict[str, Any]]:
    import duckdb

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        result = connection.execute(f'SELECT * FROM "{table_name}"')
        columns = [str(column[0]) for column in result.description]
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        connection.close()


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_json(row) + "\n")


def _write_run(path: Path, run: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _clear_previous_generated_runs(output_dir: Path, scenario_prefix: str = "synthetic-ui-v1") -> None:
    """Remove only prior runs explicitly marked as synthetic corpus output."""

    runs_dir = output_dir / "runs"
    if not runs_dir.exists():
        return
    for run_path in sorted(runs_dir.glob("*/run.json")):
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scenario = str((run.get("manifest") or {}).get("scenario") or "")
        if scenario.startswith(f"{scenario_prefix}:"):
            shutil.rmtree(run_path.parent)


def _corpus_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    # DuckDB's physical database metadata is session-specific even when every
    # table, schema, and row is identical.  Semantic determinism is asserted
    # separately for this file; the corpus fingerprint covers stable source
    # artifacts and Parquet truth snapshots.
    excluded = {"generation_manifest.json", "validation_report.json", "analytics.duckdb"}
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name not in excluded):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _coverage(generated: Sequence[GeneratedExecution]) -> dict[str, Any]:
    family_counts = Counter(item.family for item in generated)
    kind_counts = Counter(operation.kind for item in generated for operation in item.operations)
    provider_counts = Counter(
        str(operation.attributes.get("provider"))
        for item in generated
        for operation in item.operations
        if operation.kind == "model" and operation.attributes.get("provider")
    )
    model_counts = Counter(
        str(operation.attributes.get("model"))
        for item in generated
        for operation in item.operations
        if operation.kind == "model" and operation.attributes.get("model")
    )
    mixed = sum(
        1
        for item in generated
        if len(
            {
                (operation.attributes.get("role"), operation.attributes.get("provider"))
                for operation in item.operations
                if operation.kind == "model"
            }
        )
        > 1
    )
    known_cost = sum(
        1 for item in generated for operation in item.operations if operation.attributes.get("cost_known") is True
    )
    unknown_cost = sum(
        1
        for item in generated
        for operation in item.operations
        if operation.attributes.get("cost_known") is False and operation.kind in {"model", "tool"}
    )
    return {
        "executions_by_structural_family": dict(sorted(family_counts.items())),
        "runtime_only_executions": sum(not item.domain_enriched for item in generated),
        "domain_enriched_executions": sum(item.domain_enriched for item in generated),
        "successful_or_completed_executions": sum(bool(item.run.get("execution_completed")) for item in generated),
        "terminal_or_failed_executions": sum(not bool(item.run.get("execution_completed")) for item in generated),
        "operations_by_kind": dict(sorted(kind_counts.items())),
        "model_operations": kind_counts.get("model", 0),
        "tool_operations": kind_counts.get("tool", 0),
        "provider_distribution": dict(sorted(provider_counts.items())),
        "model_distribution": dict(sorted(model_counts.items())),
        "mixed_provider_executions": mixed,
        "runs_with_repeated_work": sum(bool(item.insights["repeated_patterns"]) for item in generated),
        "runs_with_repair": sum(item.insights["repair"]["repair_count"] > 0 for item in generated),
        "runs_with_fallback_or_recovery": sum(
            item.family in {"fallback", "source_tool_failure_recovery"} for item in generated
        ),
        "nested_executions": family_counts.get("nested", 0),
        "known_cost_operations": known_cost,
        "unknown_cost_operations": unknown_cost,
        "termination_families": dict(
            sorted(Counter(item.insights["termination_category"] for item in generated).items())
        ),
        "failure_families": dict(
            sorted(
                Counter(
                    item.insights["failure"]["failure_type"]
                    for item in generated
                    if item.insights["failure"].get("failure_type")
                ).items()
            )
        ),
        "path_diversity": len({tuple(item.insights["execution_path"]) for item in generated}),
    }


def _aggregate_rows_from_records(
    records: Iterable[tuple[str, Sequence[Operation], Mapping[str, Any], Mapping[str, Any], Sequence[Event]]],
    *,
    include_template: bool,
) -> list[dict[str, Any]]:
    """Aggregate production-derived insights, optionally adding generator truth."""

    groups: dict[tuple[str, str], dict[str, Any]] = {}

    def add(
        entity_type: str,
        canonical_key: str,
        display_label: str,
        execution_id: str,
        *,
        calls: int = 1,
        failures: int = 0,
        iterations: int = 0,
        metric_value: float | None = None,
        metric_text: str | None = None,
    ) -> None:
        group = groups.setdefault(
            (entity_type, canonical_key),
            {
                "entity_type": entity_type,
                "canonical_key": canonical_key,
                "display_label": display_label,
                "execution_ids": set(),
                "calls": 0,
                "failures": 0,
                "iterations": 0,
                "metric_value": 0.0,
                "metric_seen": False,
                "metric_text": metric_text,
            },
        )
        group["execution_ids"].add(execution_id)
        group["calls"] += calls
        group["failures"] += failures
        group["iterations"] += iterations
        if metric_value is not None:
            group["metric_value"] += float(metric_value)
            group["metric_seen"] = True
        if group["metric_text"] is None and metric_text is not None:
            group["metric_text"] = metric_text

    for execution_id, operations, insights, run, events in records:
        ordered = sorted(operations, key=lambda value: value.started_at or BASE_TIME)
        sequence = [operation for operation in ordered if operation.kind not in {"workflow", "pipeline", "agent"}]
        failure = insights.get("failure") or {}
        repeated_patterns = insights.get("repeated_patterns") or []
        repair = insights.get("repair") or {}
        cost = insights.get("cost_summary") or {}

        if include_template:
            family = str(insights.get("expected_template") or run.get("_synthetic_family") or "")
            if family:
                add("template", f"template:{family}", family.replace("_", " ").title(), execution_id)

        for operation in operations:
            add(
                "operation",
                canonical_operation_key(operation),
                display_operation(operation),
                execution_id,
                failures=int(operation.status == "error"),
            )
            if operation.kind == "tool":
                add(
                    "tool",
                    canonical_tool_key(operation),
                    display_tool(operation),
                    execution_id,
                    failures=int(operation.status == "error"),
                )
            if operation.kind in {"component", "operation", "tool"}:
                add(
                    "stage",
                    canonical_stage_key(operation),
                    display_stage(operation),
                    execution_id,
                    failures=int(operation.status == "error"),
                )

        add(
            "path",
            canonical_path_signature(sequence),
            display_path(sequence),
            execution_id,
            calls=len(sequence),
            failures=sum(operation.status == "error" for operation in sequence),
        )
        for pattern in repeated_patterns:
            add(
                "loop",
                str(pattern["loop_signature"]),
                " → ".join(pattern["pattern"]),
                execution_id,
                iterations=int(pattern["iterations"]),
            )
        if failure.get("primary_break_point_key"):
            add(
                "failure",
                str(failure["primary_break_point_key"]),
                str(failure.get("primary_break_point") or "Unknown failure"),
                execution_id,
                failures=1,
            )

        # These rows validate user-facing run-level claims, not only graph entities.
        execution_completed = bool(run.get("execution_completed"))
        # A failed operation is a failure-group fact; a failed execution is a
        # terminal run-state fact.  Recovery keeps those dimensions separate.
        failed = not execution_completed
        recovered = any(event.type == "recovery" for event in events)
        if execution_completed:
            add("execution_status", "status:completed", "Completed", execution_id)
        if failed:
            add("execution_status", "status:failed", "Failed", execution_id)
        if recovered:
            add("execution_status", "status:recovered", "Recovered", execution_id)
        termination = str(insights.get("termination_category") or "other")
        add("termination", f"termination:{termination}", termination.replace("_", " ").title(), execution_id)

        repeated = bool(repeated_patterns)
        repeat_iterations = sum(int(pattern.get("iterations") or 0) for pattern in repeated_patterns)
        add(
            "repeat",
            "repeat:needed_another_try" if repeated else "repeat:no_repeat",
            "Needed another try" if repeated else "No repeat",
            execution_id,
            iterations=repeat_iterations,
        )
        repair_count = int(repair.get("repair_count") or 0)
        repair_key = "repair:performed" if repair_count else "repair:none"
        repair_label = "Repair performed" if repair_count else "No repair"
        add(
            "repair",
            repair_key,
            repair_label,
            execution_id,
            calls=repair_count,
            iterations=int(repair.get("repair_cycles") or 0),
        )
        if repair_count:
            result = str(repair.get("success")).casefold()
            result_key = "repair:succeeded" if result == "true" else "repair:terminal_failure"
            result_label = "Repair succeeded" if result == "true" else "Repair terminal failure"
            add("repair_result", result_key, result_label, execution_id, calls=repair_count)

        role_counts: Counter[tuple[str, str, str]] = Counter()
        for operation in operations:
            if operation.kind != "model":
                continue
            role = str(operation.attributes.get("role") or operation.attributes.get("pf.role") or "unknown")
            provider = str(operation.attributes.get("provider") or "unknown")
            model = str(operation.attributes.get("model") or "unknown")
            role_counts[(role, provider, model)] += 1
        for (role, provider, model), calls in sorted(role_counts.items()):
            key = f"role:{role}|provider:{provider}|model:{model}"
            add("provider_model_role", key, f"{role} · {provider} · {model}", execution_id, calls=calls)

        model_calls = sum(operation.kind == "model" for operation in operations)
        tool_calls = sum(operation.kind == "tool" for operation in operations)
        known_model_cost = float(cost.get("known_model_cost_usd") or 0.0)
        known_total_cost = float(cost.get("known_total_cost_usd") or 0.0)
        unknown_cost_calls = int(cost.get("unmeasured_operation_count") or 0)
        add("cost", "cost:known_model_total", "Known model cost", execution_id, metric_value=known_model_cost)
        add("cost", "cost:known_total", "Known total cost", execution_id, metric_value=known_total_cost)
        add("cost", "cost:model_calls", "Model calls", execution_id, calls=model_calls, metric_value=model_calls)
        add("cost", "cost:tool_calls", "Tool calls", execution_id, calls=tool_calls, metric_value=tool_calls)
        add(
            "cost",
            "cost:known_operations",
            "Known-cost operations",
            execution_id,
            calls=model_calls + tool_calls - unknown_cost_calls,
        )
        add("cost", "cost:unknown_operations", "Unknown-cost operations", execution_id, calls=unknown_cost_calls)
        coverage_key = "cost:complete" if bool(cost.get("total_cost_complete")) else "cost:incomplete"
        coverage_label = (
            "Complete cost coverage" if bool(cost.get("total_cost_complete")) else "Incomplete cost coverage"
        )
        add("cost_coverage", coverage_key, coverage_label, execution_id)

    rows = []
    for group in groups.values():
        rows.append(
            {
                "entity_type": group["entity_type"],
                "canonical_key": group["canonical_key"],
                "display_label": group["display_label"],
                "executions": len(group["execution_ids"]),
                "calls": group["calls"],
                "failures": group["failures"],
                "iterations": group["iterations"],
                "metric_value": group["metric_value"] if group["metric_seen"] else None,
                "metric_text": group["metric_text"],
            }
        )
    return sorted(rows, key=lambda row: (row["entity_type"], row["canonical_key"]))


def _aggregate_insight_rows(generated: Sequence[GeneratedExecution]) -> list[dict[str, Any]]:
    """Build testing-only aggregate truth from generated execution facts."""

    records: list[tuple[str, Sequence[Operation], Mapping[str, Any], Mapping[str, Any], Sequence[Event]]] = []
    for item in generated:
        insights = dict(item.insights)
        insights["expected_template"] = item.family
        run = dict(item.run)
        records.append((item.execution.execution_id, item.operations, insights, run, item.events))
    return _aggregate_rows_from_records(records, include_template=True)


def _decoded_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _duckdb_analytics_records(
    database_path: Path,
    output_dir: Path,
) -> list[tuple[str, Sequence[Operation], Mapping[str, Any], Mapping[str, Any], Sequence[Event]]]:
    """Read canonical DuckDB records and re-run the production derivations."""

    execution_rows = _read_duckdb_rows(database_path, "executions")
    operation_rows = _read_duckdb_rows(database_path, "operations")
    link_rows = _read_duckdb_rows(database_path, "links")
    event_rows = _read_duckdb_rows(database_path, "events")
    operations_by_execution: defaultdict[str, list[Operation]] = defaultdict(list)
    links_by_execution: defaultdict[str, list[Link]] = defaultdict(list)
    events_by_execution: defaultdict[str, list[Event]] = defaultdict(list)
    for row in operation_rows:
        values = dict(row)
        values["attributes"] = _decoded_mapping(values.get("attributes"))
        operation = Operation.model_validate(values)
        operations_by_execution[operation.execution_id].append(operation)
    for row in link_rows:
        values = dict(row)
        values["attributes"] = _decoded_mapping(values.get("attributes"))
        link = Link.model_validate(values)
        links_by_execution[link.execution_id].append(link)
    for row in event_rows:
        values = dict(row)
        values["payload"] = _decoded_mapping(values.get("payload"))
        events_by_execution[str(values["execution_id"])].append(Event.model_validate(values))

    records: list[tuple[str, Sequence[Operation], Mapping[str, Any], Mapping[str, Any], Sequence[Event]]] = []
    for row in execution_rows:
        values = dict(row)
        values["attributes"] = _decoded_mapping(values.get("attributes"))
        execution = Execution.model_validate(values)
        execution_id = execution.execution_id
        run_path = output_dir / "runs" / execution_id / "run.json"
        run_value = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
        run: Mapping[str, Any] = run_value if isinstance(run_value, Mapping) else {}
        operations = sorted(operations_by_execution[execution_id], key=lambda value: value.started_at or BASE_TIME)
        graph = NormalizedExecutionGraph(
            execution=execution,
            operations=operations,
            links=links_by_execution[execution_id],
        )
        events = sorted(events_by_execution[execution_id], key=lambda value: value.timestamp)
        insights = derive_runtime_insights(graph, run=run, events=events)
        records.append((execution_id, operations, insights, run, events))
    return records


def _identity_leak_rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    leaks = []
    for row in rows:
        for field in ("canonical_key", "display_label"):
            value = str(row.get(field) or "")
            if _IDENTITY_LEAK_RE.search(value):
                leaks.append(f"{row.get('entity_type')}:{row.get('canonical_key')}:{field}")
    return sorted(set(leaks))


def _dashboard_aggregate_mismatches(
    database_path: Path,
    expected_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Compare the DuckDB-backed dashboard repository with testing truth."""

    from witdem.analytics.repository import AnalyticsRepository

    expected = {(str(row["entity_type"]), str(row["canonical_key"])): row for row in expected_rows}
    repository = AnalyticsRepository(database_path)
    try:
        sources: list[tuple[str, list[dict[str, Any]], str]] = [
            ("operation", repository.entity_summary("operations"), "canonical_key"),
            ("tool", repository.entity_summary("tools"), "canonical_key"),
            ("stage", repository.entity_summary("stages"), "canonical_key"),
            ("path", repository.paths(), "path_signature"),
            ("loop", repository.loops(), "loop_signature"),
            ("failure", repository.failures(), "failure_key"),
        ]
        mismatches: list[str] = []
        for entity_type, rows, key_field in sources:
            actual = {(entity_type, str(row.get(key_field))): row for row in rows}
            expected_keys = {key for key in expected if key[0] == entity_type}
            actual_keys = {key for key in actual if key[0] == entity_type}
            mismatches.extend(f"{entity_type}:missing:{key[1]}" for key in sorted(expected_keys - actual_keys))
            mismatches.extend(f"{entity_type}:unexpected:{key[1]}" for key in sorted(actual_keys - expected_keys))
            for key in sorted(expected_keys & actual_keys):
                expected_row = expected[key]
                actual_row = actual[key]
                for field in ("executions", "failures", "iterations"):
                    if field not in actual_row:
                        continue
                    expected_value = expected_row.get(field)
                    actual_value = actual_row.get(field)
                    if actual_value is None and expected_value == 0:
                        actual_value = 0
                    if actual_value != expected_value:
                        mismatches.append(f"{entity_type}:{key[1]}:{field}")
        return sorted(set(mismatches))
    finally:
        repository.close()


def _derived_known_answer(
    operations: Sequence[Operation],
    insights: Mapping[str, Any],
    *,
    template: str,
    domain_enriched: bool,
    run: Mapping[str, Any],
    events: Sequence[Event],
) -> dict[str, Any]:
    failure = insights.get("failure") or {}
    loop_signatures = sorted(str(pattern["loop_signature"]) for pattern in insights.get("repeated_patterns") or [])
    tool_names = sorted(display_tool(operation) for operation in operations if operation.kind == "tool")
    stage_keys = sorted(
        canonical_stage_key(operation)
        for operation in operations
        if operation.kind in {"component", "operation", "tool"}
    )
    provider_roles = sorted(
        {
            f"{operation.attributes.get('role', 'unknown')}:{operation.attributes.get('provider', 'unknown')}:"
            f"{operation.attributes.get('model', 'unknown')}"
            for operation in operations
            if operation.kind == "model"
        }
    )
    cost = insights.get("cost_summary") or {}
    recovered = any(event.type == "recovery" for event in events)
    return {
        "expected_template": template,
        "expected_repeated_pattern_count": len(insights.get("repeated_patterns") or []),
        "expected_repair_count": int((insights.get("repair") or {}).get("repair_count") or 0),
        "expected_terminal_family": insights.get("termination_category"),
        "expected_failure_operation": failure.get("primary_break_point"),
        "expected_failure_key": failure.get("primary_break_point_key"),
        "expected_path_signature": insights.get("path_signature"),
        "expected_loop_signatures": _json(loop_signatures),
        "expected_tool_names": _json(tool_names),
        "expected_stage_keys": _json(stage_keys),
        "expected_provider_roles": _json(provider_roles),
        "expected_domain_enriched": domain_enriched,
        "expected_recovered": recovered,
        "expected_model_calls": sum(operation.kind == "model" for operation in operations),
        "expected_tool_calls": sum(operation.kind == "tool" for operation in operations),
        "expected_known_model_cost": float(cost.get("known_model_cost_usd") or 0.0),
        "expected_cost_complete": bool(cost.get("total_cost_complete")),
    }


def validate_generated(
    generated: Sequence[GeneratedExecution],
    output_dir: Path,
    aggregate_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    derived_mismatches: list[str] = []
    known_answer_mismatches: list[str] = []
    rederived_by_id: dict[str, tuple[Sequence[Operation], Mapping[str, Any]]] = {}
    replay_count = 0
    for item in generated:
        operation_ids = {operation.operation_id for operation in item.operations}
        if any(operation.execution_id != item.execution.execution_id for operation in item.operations):
            errors.append(f"{item.execution.execution_id}: operation execution correlation")
        if any(link.source_id not in operation_ids or link.target_id not in operation_ids for link in item.links):
            errors.append(f"{item.execution.execution_id}: dangling link")
        for operation in item.operations:
            if operation.parent_span_id and operation.parent_span_id not in operation_ids:
                errors.append(f"{item.execution.execution_id}: dangling parent {operation.operation_id}")
            if operation.started_at and operation.ended_at and operation.ended_at < operation.started_at:
                errors.append(f"{item.execution.execution_id}: inverted operation timing")
        try:
            graph = normalize_haystack_spans(
                item.spans,
                execution_id=item.execution.execution_id,
                runtime_id=(item.run.get("manifest") or {}).get("workflow_version"),
                providers=(item.run.get("manifest") or {}).get("providers", []),
            )
            replay_count += 1
            replay = derive_replay_graph(graph, events=item.events)
            if len(replay.nodes) != len(item.operations):
                errors.append(f"{item.execution.execution_id}: replay node mismatch")
            insights = derive_runtime_insights(graph, run=item.run, events=item.events)
            rederived_by_id[item.execution.execution_id] = (graph.operations, insights)
            if insights["execution_path"] != item.insights["execution_path"]:
                derived_mismatches.append(item.execution.execution_id)
        except (ValueError, TypeError) as error:
            errors.append(f"{item.execution.execution_id}: normalization {error}")
    expected_path = output_dir / "expected_derived_insights.parquet"
    known_answer_comparison_count = 0
    if expected_path.exists():
        import pyarrow.parquet as pq

        expected_rows = pq.read_table(expected_path).to_pylist()
        expected_by_id = {str(row["execution_id"]): row for row in expected_rows}
        for item in generated:
            expected = expected_by_id.get(item.execution.execution_id)
            if expected is None:
                known_answer_mismatches.append(item.execution.execution_id)
                continue
            known_answer_comparison_count += 1
            rederived = rederived_by_id.get(item.execution.execution_id)
            if rederived is None:
                known_answer_mismatches.append(item.execution.execution_id)
                continue
            actual = _derived_known_answer(
                rederived[0],
                rederived[1],
                template=item.family,
                domain_enriched=item.domain_enriched,
                run=item.run,
                events=item.events,
            )
            for key, value in actual.items():
                expected_value = expected.get(key)
                if expected_value is not None and expected_value != value:
                    known_answer_mismatches.append(item.execution.execution_id)
                    break
    duckdb_known_answer_mismatches: list[str] = []
    duckdb_known_answer_comparison_count = 0
    duckdb_rederived_by_id: dict[str, tuple[Sequence[Operation], Mapping[str, Any]]] = {}
    duckdb_analytics_records: list[
        tuple[str, Sequence[Operation], Mapping[str, Any], Mapping[str, Any], Sequence[Event]]
    ] = []
    database_path = output_dir / "analytics.duckdb"
    if database_path.exists():
        try:
            duckdb_analytics_records = _duckdb_analytics_records(database_path, output_dir)
            duckdb_rederived_by_id = {
                execution_id: (operations, insights)
                for execution_id, operations, insights, _run, _events in duckdb_analytics_records
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"DuckDB execution derivation: {error}")
        expected_rows = _read_duckdb_rows(database_path, "expected_derived_insights")
        expected_by_id = {str(row["execution_id"]): row for row in expected_rows}
        for item in generated:
            expected = expected_by_id.get(item.execution.execution_id)
            if expected is None:
                duckdb_known_answer_mismatches.append(item.execution.execution_id)
                continue
            duckdb_known_answer_comparison_count += 1
            rederived = duckdb_rederived_by_id.get(item.execution.execution_id)
            if rederived is None:
                duckdb_known_answer_mismatches.append(item.execution.execution_id)
                continue
            actual = _derived_known_answer(
                rederived[0],
                rederived[1],
                template=item.family,
                domain_enriched=item.domain_enriched,
                run=item.run,
                events=item.events,
            )
            for key, value in actual.items():
                expected_value = expected.get(key)
                if expected_value is not None and expected_value != value:
                    duckdb_known_answer_mismatches.append(item.execution.execution_id)
                    break
    else:
        errors.append("analytics.duckdb: missing")
    aggregate_insight_mismatches: list[str] = []
    aggregate_insight_comparison_count = 0
    aggregate_truth_readback_mismatches: list[str] = []
    dashboard_aggregate_mismatches: list[str] = []
    aggregate_identity_leaks: list[str] = []
    aggregate_sanity_errors: list[str] = []
    if aggregate_rows is not None:
        aggregate_path = output_dir / "expected_aggregate_insights.parquet"
        if not aggregate_path.exists():
            errors.append("expected_aggregate_insights.parquet: missing")
        elif database_path.exists():
            import pyarrow.parquet as pq

            truth_rows = pq.read_table(aggregate_path).to_pylist()
            expected_by_key = {
                (str(row["entity_type"]), str(row["canonical_key"])): dict(row) for row in aggregate_rows
            }
            truth_by_key = {(str(row["entity_type"]), str(row["canonical_key"])): row for row in truth_rows}
            if set(expected_by_key) != set(truth_by_key):
                aggregate_truth_readback_mismatches.extend(
                    [f"parquet_missing:{key}" for key in sorted(set(expected_by_key) - set(truth_by_key))]
                )
                aggregate_truth_readback_mismatches.extend(
                    [f"parquet_unexpected:{key}" for key in sorted(set(truth_by_key) - set(expected_by_key))]
                )
            for aggregate_key in sorted(set(expected_by_key) & set(truth_by_key)):
                if any(
                    truth_by_key[aggregate_key].get(column) != expected_by_key[aggregate_key].get(column)
                    for column in AGGREGATE_COLUMNS
                ):
                    aggregate_truth_readback_mismatches.append(f"parquet:{aggregate_key}")

            actual_truth_rows = _read_duckdb_rows(database_path, "expected_aggregate_insights")
            actual_truth_by_key = {
                (str(row["entity_type"]), str(row["canonical_key"])): row for row in actual_truth_rows
            }
            if set(expected_by_key) != set(actual_truth_by_key):
                aggregate_truth_readback_mismatches.extend(
                    [f"duckdb_missing:{key}" for key in sorted(set(expected_by_key) - set(actual_truth_by_key))]
                )
                aggregate_truth_readback_mismatches.extend(
                    [f"duckdb_unexpected:{key}" for key in sorted(set(actual_truth_by_key) - set(expected_by_key))]
                )
            for aggregate_key in sorted(set(expected_by_key) & set(actual_truth_by_key)):
                if any(
                    actual_truth_by_key[aggregate_key].get(column) != expected_by_key[aggregate_key].get(column)
                    for column in AGGREGATE_COLUMNS
                ):
                    aggregate_truth_readback_mismatches.append(f"duckdb:{aggregate_key}")

            # Re-read the canonical DuckDB records and use the same derivation
            # engine as the dashboard/runtime path.  The expected table is not
            # used as an input to this calculation.
            try:
                actual_rows = _aggregate_rows_from_records(duckdb_analytics_records, include_template=False)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"aggregate analytics readback: {error}")
                actual_rows = []
            analytics_expected = {key: row for key, row in expected_by_key.items() if key[0] != "template"}
            expected_by_key = {key: row for key, row in analytics_expected.items()}
            actual_by_key = {(str(row["entity_type"]), str(row["canonical_key"])): row for row in actual_rows}
            aggregate_insight_comparison_count = len(expected_by_key)
            if set(expected_by_key) != set(actual_by_key):
                aggregate_insight_mismatches.extend(
                    [f"missing:{key}" for key in sorted(set(expected_by_key) - set(actual_by_key))]
                )
                aggregate_insight_mismatches.extend(
                    [f"unexpected:{key}" for key in sorted(set(actual_by_key) - set(expected_by_key))]
                )
            for aggregate_key in sorted(set(expected_by_key) & set(actual_by_key)):
                expected = expected_by_key[aggregate_key]
                actual = actual_by_key[aggregate_key]
                if any(actual.get(column) != expected.get(column) for column in AGGREGATE_COLUMNS):
                    aggregate_insight_mismatches.append(str(aggregate_key))
            try:
                dashboard_aggregate_mismatches = _dashboard_aggregate_mismatches(database_path, aggregate_rows)
            except (OSError, TypeError, ValueError) as error:
                errors.append(f"dashboard aggregate readback: {error}")

            aggregate_identity_leaks = _identity_leak_rows(aggregate_rows) + _identity_leak_rows(actual_rows)
            template_rows = [row for row in aggregate_rows if row["entity_type"] == "template"]
            if template_rows and len(generated) >= 100:
                path_rows = [row for row in aggregate_rows if row["entity_type"] == "path"]
                singleton_paths = sum(int(row["executions"]) == 1 for row in path_rows)
                if path_rows and singleton_paths * 2 > len(path_rows):
                    aggregate_sanity_errors.append("singleton paths are a majority")
                if any(int(row["executions"]) < 2 for row in template_rows):
                    aggregate_sanity_errors.append("a generated template has fewer than two executions")
            if any(item.family == "linear" and item.insights.get("repeated_patterns") for item in generated):
                aggregate_sanity_errors.append("clean linear execution marked as repeated")
            if any(
                any(event.type == "recovery" for event in item.events) and not bool(item.run.get("execution_completed"))
                for item in generated
            ):
                aggregate_sanity_errors.append("recovered execution counted as terminal")
            if aggregate_identity_leaks:
                aggregate_sanity_errors.append("identity leakage in aggregate labels")
    sample_ids = [item.execution.execution_id for item in generated[:8]]
    shared_reader_samples = 0
    for execution_id in sample_ids:
        try:
            analysis = runtime_analysis(output_dir, execution_id, domain_enrichment=True)
            if analysis["execution_id"] == execution_id:
                shared_reader_samples += 1
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{execution_id}: shared reader {error}")
    return {
        "valid": (
            not errors
            and not derived_mismatches
            and not known_answer_mismatches
            and not duckdb_known_answer_mismatches
            and not aggregate_insight_mismatches
            and not aggregate_truth_readback_mismatches
            and not dashboard_aggregate_mismatches
            and not aggregate_sanity_errors
        ),
        "errors": errors,
        "derived_mismatches": derived_mismatches,
        "known_answer_mismatches": sorted(set(known_answer_mismatches)),
        "known_answer_comparison_count": known_answer_comparison_count,
        "duckdb_known_answer_mismatches": sorted(set(duckdb_known_answer_mismatches)),
        "duckdb_known_answer_comparison_count": duckdb_known_answer_comparison_count,
        "aggregate_insight_mismatches": sorted(set(aggregate_insight_mismatches)),
        "aggregate_insight_comparison_count": aggregate_insight_comparison_count,
        "aggregate_truth_readback_mismatches": sorted(set(aggregate_truth_readback_mismatches)),
        "dashboard_aggregate_mismatches": sorted(set(dashboard_aggregate_mismatches)),
        "aggregate_identity_leaks": sorted(set(aggregate_identity_leaks)),
        "aggregate_sanity_errors": sorted(set(aggregate_sanity_errors)),
        "normalized_execution_count": replay_count,
        "replay_execution_count": replay_count,
        "shared_reader_sample_count": shared_reader_samples,
        "coverage": _coverage(generated),
    }


def generate_corpus(
    output_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    execution_count: int = DEFAULT_EXECUTION_COUNT,
    corpus_version: str = "v1",
) -> dict[str, Any]:
    """Generate, persist, and validate a deterministic synthetic corpus."""

    if execution_count < 1:
        raise ValueError("execution_count must be positive")
    if corpus_version not in CORPUS_VARIANTS:
        raise ValueError(f"unsupported corpus_version: {corpus_version}")
    variant = CORPUS_VARIANTS[corpus_version]
    generator_version = str(variant["generator_version"])
    template_version = str(variant["template_version"])
    scenario_prefix = str(variant["scenario_prefix"])
    sources = load_source_templates()
    source_inventory, observed_anchors = _observed_template_evidence(sources)
    generated: list[GeneratedExecution] = []
    for index, family in enumerate(_family_sequence(execution_count)):
        source = sources[family]
        template = source[index % len(source)] if isinstance(source, list) else source
        generated.append(
            generate_execution(
                template,
                seed=seed,
                index=index,
                family=family,
                generator_version=generator_version,
                scenario_prefix=scenario_prefix,
                usage_anchors=observed_anchors if corpus_version == "v2" else None,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_generated_runs(output_dir, scenario_prefix=scenario_prefix)
    (output_dir / "runs").mkdir(exist_ok=True)
    (output_dir / "telemetry").mkdir(exist_ok=True)
    (output_dir / "analytics").mkdir(exist_ok=True)
    all_spans = [span for item in generated for span in item.spans]
    all_events = [event for item in generated for event in item.events]
    _write_jsonl(output_dir / "telemetry" / "spans.jsonl", all_spans)
    _write_jsonl(output_dir / "analytics" / "events.jsonl", _core_event_rows(all_events, generator_version))
    for item in generated:
        _write_run(output_dir / "runs" / item.execution.execution_id / "run.json", item.run)
    run_artifact_count = len(list((output_dir / "runs").glob("*/run.json")))
    if run_artifact_count != execution_count:
        raise ValueError(f"expected {execution_count} synthetic run artifacts, found {run_artifact_count}")

    execution_columns = [
        "execution_id",
        "runtime_id",
        "started_at",
        "ended_at",
        "status",
        "schema_version",
        "attributes",
    ]
    operation_columns = [
        "operation_id",
        "execution_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "kind",
        "name",
        "status",
        "started_at",
        "ended_at",
        "attempt",
        "attributes",
    ]
    link_columns = ["link_id", "execution_id", "source_id", "target_id", "relation", "attributes"]
    event_columns = [
        "event_id",
        "execution_id",
        "trace_id",
        "span_id",
        "timestamp",
        "type",
        "name",
        "payload",
        "schema_version",
    ]
    evaluation_columns = [
        "evaluation_id",
        "execution_id",
        "subject_id",
        "name",
        "value",
        "label",
        "score",
        "source",
        "confidence",
        "definition_version",
        "attributes",
    ]
    outcome_columns = ["outcome_id", "execution_id", "name", "status", "value", "timestamp", "attributes"]
    _write_parquet(
        output_dir / "executions.parquet",
        _parquet_rows(
            (item.execution.model_dump(mode="json") for item in generated), execution_columns, {"attributes"}
        ),
        execution_columns,
    )
    _write_parquet(
        output_dir / "operations.parquet",
        _parquet_rows(
            (operation.model_dump(mode="json") for item in generated for operation in item.operations),
            operation_columns,
            {"attributes"},
        ),
        operation_columns,
    )
    _write_parquet(
        output_dir / "links.parquet",
        _parquet_rows(
            (link.model_dump(mode="json") for item in generated for link in item.links), link_columns, {"attributes"}
        ),
        link_columns,
    )
    _write_parquet(
        output_dir / "events.parquet",
        _parquet_rows(
            (event.model_dump(mode="json") for item in generated for event in item.events), event_columns, {"payload"}
        ),
        event_columns,
    )
    _write_parquet(
        output_dir / "evaluations.parquet",
        _parquet_rows(
            (record.model_dump(mode="json") for item in generated for record in item.evaluations),
            evaluation_columns,
            {"value", "attributes"},
        ),
        evaluation_columns,
    )
    _write_parquet(
        output_dir / "outcomes.parquet",
        _parquet_rows(
            (record.model_dump(mode="json") for item in generated for record in item.outcomes),
            outcome_columns,
            {"value", "attributes"},
        ),
        outcome_columns,
    )
    expected_rows = []
    for item in generated:
        expected = _derived_known_answer(
            item.operations,
            item.insights,
            template=item.family,
            domain_enriched=item.domain_enriched,
            run=item.run,
            events=item.events,
        )
        expected_rows.append({"execution_id": item.execution.execution_id, **expected})
    expected_columns = list(ANALYTICS_COLUMNS["expected_derived_insights"])
    _write_parquet(output_dir / "expected_derived_insights.parquet", expected_rows, expected_columns)
    aggregate_rows = _aggregate_insight_rows(generated) if corpus_version == "v2" else None
    if aggregate_rows is not None:
        _write_parquet(output_dir / "expected_aggregate_insights.parquet", aggregate_rows, AGGREGATE_COLUMNS)
    _write_duckdb(output_dir, table_names=V2_ANALYTICS_TABLES if corpus_version == "v2" else ANALYTICS_TABLES)

    fingerprint = _corpus_fingerprint(output_dir)
    manifest = {
        "corpus_version": corpus_version,
        "generator_version": generator_version,
        "template_version": template_version,
        "schema_version": CORE_SCHEMA_VERSION,
        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        "seed": seed,
        "execution_count": execution_count,
        "template_families": sorted(FAMILY_COUNTS),
        "composition": dict(sorted(Counter(item.family for item in generated).items())),
        "source_fixture_references": sorted(
            {str(source.source_reference) for source in sources.values() if isinstance(source, SourceTemplate)}
            | {
                str(candidate.source_reference)
                for value in sources.values()
                if isinstance(value, list)
                for candidate in value
            }
        ),
        "provider_model_pool": PROVIDER_MODELS,
        "pricing_snapshot_version": PRICE_SNAPSHOT_VERSION,
        "generated_at_utc": None,
        "corpus_fingerprint": fingerprint,
        "fingerprint_excludes": ["generation_manifest.json", "validation_report.json", "analytics.duckdb"],
        "output_files": sorted(str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file()),
    }
    if corpus_version == "v2":
        manifest["source_template_inventory"] = source_inventory
        manifest["sampling_policy"] = {
            "basis": "retained real and compatibility-tested execution templates",
            "family_target_counts": dict(sorted(FAMILY_COUNTS.items())),
            "target_policy": (
                "coverage-balanced resampling of every retained family; compatibility shapes remain the majority"
            ),
            "numeric_anchor_ranges": _numeric_anchor_summary(observed_anchors),
            "duration_parameterization": "retained span durations scaled deterministically by 0.78-1.28",
            "usage_parameterization": "retained input/output token anchors scaled deterministically by 0.88-1.12",
            "structure_policy": (
                "copy retained span topology, operation kinds, names, parentage, events, and run semantics; "
                "vary only identifiers, timestamps, bounded durations, and observed usage anchors"
            ),
        }
    (output_dir / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    validation = validate_generated(generated, output_dir, aggregate_rows=aggregate_rows)
    validation["run_artifact_count"] = run_artifact_count
    validation["corpus_fingerprint"] = fingerprint
    (output_dir / "validation_report.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    if not validation["valid"]:
        raise ValueError(f"synthetic corpus validation failed: {validation['errors'][:3]}")
    return {"manifest": manifest, "validation": validation}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the deterministic synthetic UI execution corpus")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "synthetic-ui-v1")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--execution-count", type=int, default=DEFAULT_EXECUTION_COUNT)
    parser.add_argument("--corpus-version", choices=sorted(CORPUS_VARIANTS), default="v1")
    args = parser.parse_args()
    result = generate_corpus(
        args.output,
        seed=args.seed,
        execution_count=args.execution_count,
        corpus_version=args.corpus_version,
    )
    print(json.dumps({"output": str(args.output), **result["validation"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
