"""Vendor-neutral AI operation taxonomy and measurement registry."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from witdem.analytics.core import Operation

OPERATION_TAXONOMY_VERSION = "1"
MEASUREMENT_REGISTRY_VERSION = "1"

OperationFamily = Literal["orchestration", "inference", "knowledge", "media", "action", "quality", "custom"]

OPERATION_FAMILIES: dict[str, OperationFamily] = {
    "workflow": "orchestration",
    "agent": "orchestration",
    "chain": "orchestration",
    "component": "orchestration",
    "prompt": "orchestration",
    "text_generation": "inference",
    "multimodal_generation": "inference",
    "embedding": "inference",
    "retrieval": "knowledge",
    "reranking": "knowledge",
    "search": "knowledge",
    "ocr": "media",
    "document_processing": "media",
    "image_generation": "media",
    "image_edit": "media",
    "image_understanding": "media",
    "audio_transcription": "media",
    "speech_synthesis": "media",
    "audio_generation": "media",
    "audio_understanding": "media",
    "video_generation": "media",
    "video_edit": "media",
    "video_understanding": "media",
    "tool": "action",
    "code_execution": "action",
    "guardrail": "quality",
    "evaluation": "quality",
}

OTEL_OPERATION_TYPES = {
    "chat": "text_generation",
    "text_completion": "text_generation",
    "completion": "text_generation",
    "generate": "text_generation",
    "generate_content": "multimodal_generation",
    "responses": "multimodal_generation",
    "embeddings": "embedding",
    "embedding": "embedding",
    "retrieval": "retrieval",
    "retrieve": "retrieval",
    "rerank": "reranking",
    "search": "search",
    "ocr": "ocr",
    "image_generation": "image_generation",
    "image_edit": "image_edit",
    "image_understanding": "image_understanding",
    "audio_transcription": "audio_transcription",
    "speech_synthesis": "speech_synthesis",
    "audio_generation": "audio_generation",
    "audio_understanding": "audio_understanding",
    "video_generation": "video_generation",
    "video_edit": "video_edit",
    "video_understanding": "video_understanding",
    "execute_tool": "tool",
    "invoke_agent": "agent",
    "invoke_workflow": "workflow",
}

OPENINFERENCE_OPERATION_TYPES = {
    "LLM": "text_generation",
    "EMBEDDING": "embedding",
    "RETRIEVER": "retrieval",
    "RERANKER": "reranking",
    "TOOL": "tool",
    "AGENT": "agent",
    "CHAIN": "chain",
    "GUARDRAIL": "guardrail",
    "EVALUATOR": "evaluation",
    "PROMPT": "prompt",
}

VALID_INTERFACES = frozenset(
    {"model_api", "tool", "framework", "datastore", "search_service", "local", "external_api", "unknown"}
)
VALID_ROLES = frozenset({"application", "evaluator", "guardrail", "system"})
VALID_MODALITIES = frozenset({"text", "structured", "document", "vector", "image", "audio", "video"})


@dataclass(frozen=True, slots=True)
class MeasurementDefinition:
    key: str
    unit: str
    aggregation: Literal["sum", "average", "maximum", "latest"] = "sum"
    requirement: Literal["required", "conditional", "optional"] = "optional"


COMMON_MEASUREMENTS = {
    "cost.usd": MeasurementDefinition("cost.usd", "USD", requirement="conditional"),
    "requests": MeasurementDefinition("requests", "request", requirement="optional"),
}

MEASUREMENT_REGISTRY: dict[str, dict[str, MeasurementDefinition]] = {
    "text_generation": {
        "tokens.input": MeasurementDefinition("tokens.input", "token", requirement="conditional"),
        "tokens.output": MeasurementDefinition("tokens.output", "token", requirement="conditional"),
        "tokens.total": MeasurementDefinition("tokens.total", "token", requirement="conditional"),
        "tokens.cache_read": MeasurementDefinition("tokens.cache_read", "token"),
        "tokens.cache_creation": MeasurementDefinition("tokens.cache_creation", "token"),
        "tokens.reasoning": MeasurementDefinition("tokens.reasoning", "token"),
    },
    "multimodal_generation": {
        "tokens.input": MeasurementDefinition("tokens.input", "token", requirement="conditional"),
        "tokens.output": MeasurementDefinition("tokens.output", "token", requirement="conditional"),
        "tokens.total": MeasurementDefinition("tokens.total", "token", requirement="conditional"),
        "tokens.image_input": MeasurementDefinition("tokens.image_input", "token"),
        "tokens.audio_input": MeasurementDefinition("tokens.audio_input", "token"),
        "tokens.audio_output": MeasurementDefinition("tokens.audio_output", "token"),
        "tokens.video_input": MeasurementDefinition("tokens.video_input", "token"),
        "tokens.video_output": MeasurementDefinition("tokens.video_output", "token"),
    },
    "embedding": {
        "items.input": MeasurementDefinition("items.input", "item", requirement="required"),
        "vectors.output": MeasurementDefinition("vectors.output", "vector", requirement="required"),
        "vector.dimensions": MeasurementDefinition("vector.dimensions", "dimension", "latest", "optional"),
        "tokens.input": MeasurementDefinition("tokens.input", "token", requirement="conditional"),
    },
    "retrieval": {
        "queries": MeasurementDefinition("queries", "query", requirement="required"),
        "documents.output": MeasurementDefinition("documents.output", "document", requirement="required"),
    },
    "reranking": {
        "candidates.input": MeasurementDefinition("candidates.input", "candidate", requirement="required"),
        "candidates.output": MeasurementDefinition("candidates.output", "candidate", requirement="required"),
    },
    "search": {
        "queries": MeasurementDefinition("queries", "query", requirement="required"),
        "results.output": MeasurementDefinition("results.output", "result", requirement="required"),
    },
    "ocr": {
        "pages.processed": MeasurementDefinition("pages.processed", "page", requirement="required"),
        "bytes.input": MeasurementDefinition("bytes.input", "byte"),
        "characters.output": MeasurementDefinition("characters.output", "character"),
        # OCR providers commonly bill per page and do not expose token usage.
        # Keep token fields visible as explicitly not applicable instead of
        # treating their absence as incomplete model telemetry.
        "tokens.input": MeasurementDefinition("tokens.input", "token"),
        "tokens.output": MeasurementDefinition("tokens.output", "token"),
        "tokens.total": MeasurementDefinition("tokens.total", "token"),
    },
    "document_processing": {
        "pages.processed": MeasurementDefinition("pages.processed", "page", requirement="conditional"),
        "documents.input": MeasurementDefinition("documents.input", "document", requirement="required"),
    },
    "image_generation": {
        "images.output": MeasurementDefinition("images.output", "image", requirement="required"),
        "pixels.output": MeasurementDefinition("pixels.output", "pixel"),
    },
    "image_edit": {
        "images.input": MeasurementDefinition("images.input", "image", requirement="required"),
        "images.output": MeasurementDefinition("images.output", "image", requirement="required"),
    },
    "image_understanding": {
        "images.input": MeasurementDefinition("images.input", "image", requirement="required"),
    },
    "audio_transcription": {
        "audio.seconds_input": MeasurementDefinition("audio.seconds_input", "second", requirement="required"),
        "characters.output": MeasurementDefinition("characters.output", "character"),
    },
    "speech_synthesis": {
        "characters.input": MeasurementDefinition("characters.input", "character", requirement="conditional"),
        "audio.seconds_output": MeasurementDefinition("audio.seconds_output", "second", requirement="required"),
    },
    "audio_generation": {
        "audio.seconds_output": MeasurementDefinition("audio.seconds_output", "second", requirement="required"),
    },
    "audio_understanding": {
        "audio.seconds_input": MeasurementDefinition("audio.seconds_input", "second", requirement="required"),
    },
    "video_generation": {
        "video.seconds_output": MeasurementDefinition("video.seconds_output", "second", requirement="required"),
        "frames.output": MeasurementDefinition("frames.output", "frame"),
    },
    "video_edit": {
        "video.seconds_input": MeasurementDefinition("video.seconds_input", "second", requirement="required"),
        "video.seconds_output": MeasurementDefinition("video.seconds_output", "second", requirement="required"),
    },
    "video_understanding": {
        "video.seconds_input": MeasurementDefinition("video.seconds_input", "second", requirement="required"),
        "frames.input": MeasurementDefinition("frames.input", "frame"),
    },
    "tool": {"tool.calls": MeasurementDefinition("tool.calls", "call", requirement="required")},
    "code_execution": {
        "code.executions": MeasurementDefinition("code.executions", "execution", requirement="required")
    },
    "evaluation": {"evaluations": MeasurementDefinition("evaluations", "evaluation", requirement="required")},
}

ATTRIBUTE_MEASUREMENTS = {
    "gen_ai.usage.input_tokens": ("tokens.input", "token"),
    "input_tokens": ("tokens.input", "token"),
    "gen_ai.usage.output_tokens": ("tokens.output", "token"),
    "output_tokens": ("tokens.output", "token"),
    "gen_ai.usage.total_tokens": ("tokens.total", "token"),
    "total_tokens": ("tokens.total", "token"),
    "gen_ai.usage.cache_read.input_tokens": ("tokens.cache_read", "token"),
    "gen_ai.usage.cache_creation.input_tokens": ("tokens.cache_creation", "token"),
    "gen_ai.usage.reasoning.output_tokens": ("tokens.reasoning", "token"),
    "gen_ai.usage.audio.input_tokens": ("tokens.audio_input", "token"),
    "gen_ai.usage.audio.output_tokens": ("tokens.audio_output", "token"),
    "gen_ai.usage.image.input_tokens": ("tokens.image_input", "token"),
    "gen_ai.usage.image.output_tokens": ("tokens.image_output", "token"),
    "gen_ai.usage.video.input_tokens": ("tokens.video_input", "token"),
    "gen_ai.usage.video.output_tokens": ("tokens.video_output", "token"),
    "gen_ai.usage.search_queries": ("queries", "query"),
    "gen_ai.usage.ocr_pages": ("pages.processed", "page"),
    "gen_ai.usage.input_bytes": ("bytes.input", "byte"),
    "gen_ai.usage.input_items": ("items.input", "item"),
    "gen_ai.usage.output_vectors": ("vectors.output", "vector"),
    "gen_ai.usage.vector_dimensions": ("vector.dimensions", "dimension"),
    "gen_ai.usage.output_documents": ("documents.output", "document"),
    "gen_ai.usage.input_candidates": ("candidates.input", "candidate"),
    "gen_ai.usage.output_candidates": ("candidates.output", "candidate"),
    "gen_ai.usage.output_results": ("results.output", "result"),
    "gen_ai.usage.input_images": ("images.input", "image"),
    "gen_ai.usage.output_images": ("images.output", "image"),
    "gen_ai.usage.input_audio_seconds": ("audio.seconds_input", "second"),
    "gen_ai.usage.output_audio_seconds": ("audio.seconds_output", "second"),
    "gen_ai.usage.input_video_seconds": ("video.seconds_input", "second"),
    "gen_ai.usage.output_video_seconds": ("video.seconds_output", "second"),
    "gen_ai.usage.input_frames": ("frames.input", "frame"),
    "gen_ai.usage.output_frames": ("frames.output", "frame"),
    "queries": ("queries", "query"),
    "documents_output": ("documents.output", "document"),
    "candidates_input": ("candidates.input", "candidate"),
    "candidates_output": ("candidates.output", "candidate"),
    "items_input": ("items.input", "item"),
    "vectors_output": ("vectors.output", "vector"),
    "vector_dimensions": ("vector.dimensions", "dimension"),
    "pages_processed": ("pages.processed", "page"),
    "results_output": ("results.output", "result"),
    "images_input": ("images.input", "image"),
    "images_output": ("images.output", "image"),
    "audio_input_seconds": ("audio.seconds_input", "second"),
    "audio_output_seconds": ("audio.seconds_output", "second"),
    "video_input_seconds": ("video.seconds_input", "second"),
    "video_output_seconds": ("video.seconds_output", "second"),
    "frames_input": ("frames.input", "frame"),
    "frames_output": ("frames.output", "frame"),
    "cost_usd": ("cost.usd", "USD"),
    "gen_ai.cost.usd": ("cost.usd", "USD"),
}


def token_measurement_applicable(operation_type: str, attributes: Mapping[str, Any]) -> bool:
    """Return whether complete token reporting applies to one operation.

    Observed token values always make the operation applicable. Otherwise the
    registry controls applicability: required and conditional token meters are
    eligible, while optional-only token meters (such as OCR) are explicitly
    not applicable when the provider reports none.
    """

    token_attributes = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
    )
    if any(
        isinstance(attributes.get(key), (int, float)) and not isinstance(attributes.get(key), bool)
        for key in token_attributes
    ):
        return True
    return any(
        key.startswith("tokens.") and definition.requirement in {"required", "conditional"}
        for key, definition in MEASUREMENT_REGISTRY.get(operation_type, {}).items()
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip().casefold() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (Mapping, bytes)):
        return [str(item).strip().casefold() for item in value if str(item).strip()]
    return []


def operation_identity(operation: Operation) -> dict[str, Any]:
    """Return a versioned identity using explicit semantic attributes only."""

    attributes = operation.attributes
    explicit_type = str(attributes.get("witdem.operation.type") or "").strip().casefold()
    otel_name = str(attributes.get("gen_ai.operation.name") or "").strip().casefold()
    oi_kind = str(attributes.get("openinference.span.kind") or attributes.get("openinference.kind") or "").upper()
    operation_type = explicit_type or OTEL_OPERATION_TYPES.get(otel_name) or OPENINFERENCE_OPERATION_TYPES.get(oi_kind)
    if not operation_type:
        operation_type = {
            "workflow": "workflow",
            "pipeline": "workflow",
            "agent": "agent",
            "component": "component",
            "model": "text_generation",
            "tool": "tool",
        }.get(operation.kind, "x.witdem.unclassified")
    family: OperationFamily = OPERATION_FAMILIES.get(operation_type, "custom")
    interface = str(attributes.get("witdem.operation.interface") or "").strip().casefold()
    if interface not in VALID_INTERFACES:
        interface = (
            "tool" if operation.kind == "tool" else "model_api" if family in {"inference", "media"} else "unknown"
        )
    role = str(attributes.get("witdem.operation.role") or "application").strip().casefold()
    if role not in VALID_ROLES:
        role = "application"
    return {
        "taxonomy_version": OPERATION_TAXONOMY_VERSION,
        "family": family,
        "type": operation_type,
        "subtype": str(attributes.get("witdem.operation.subtype") or otel_name or oi_kind or operation.name),
        "interface": interface,
        "role": role,
        "input_modalities": [
            item for item in _strings(attributes.get("witdem.operation.input_modalities")) if item in VALID_MODALITIES
        ],
        "output_modalities": [
            item for item in _strings(attributes.get("witdem.operation.output_modalities")) if item in VALID_MODALITIES
        ],
    }


def _structured_measurements(attributes: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = attributes.get("witdem.measurements")
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw)) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [dict(item) for item in parsed if isinstance(item, Mapping)] if isinstance(parsed, list) else []


def operation_measurements(
    operation: Operation,
    *,
    expected: Iterable[str] = (),
    optional: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return measured and declared-missing facts without inventing zero values."""

    identity = operation_identity(operation)
    operation_type = str(identity["type"])
    registry = {**COMMON_MEASUREMENTS, **MEASUREMENT_REGISTRY.get(operation_type, {})}
    observed: dict[str, dict[str, Any]] = {}
    for item in _structured_measurements(operation.attributes):
        key = str(item.get("key") or "").strip().casefold()
        value = item.get("value")
        if key and isinstance(value, (int, float)) and not isinstance(value, bool):
            observed[key] = {
                "key": key,
                "value": float(value),
                "unit": str(item.get("unit") or registry.get(key, MeasurementDefinition(key, "unit")).unit),
                "aggregation": str(
                    item.get("aggregation") or registry.get(key, MeasurementDefinition(key, "unit")).aggregation
                ),
                "scope": str(item.get("scope") or "operation"),
                "provenance": str(item.get("provenance") or "application_reported"),
            }
    for attribute, (key, unit) in ATTRIBUTE_MEASUREMENTS.items():
        value = operation.attributes.get(attribute)
        if key not in observed and isinstance(value, (int, float)) and not isinstance(value, bool):
            observed[key] = {
                "key": key,
                "value": float(value),
                "unit": unit,
                "aggregation": registry.get(key, MeasurementDefinition(key, unit)).aggregation,
                "scope": "operation",
                "provenance": str(operation.attributes.get("usage_provenance") or "observed_span"),
            }
    implied_count = {
        "tool": ("tool.calls", "call"),
        "code_execution": ("code.executions", "execution"),
        "evaluation": ("evaluations", "evaluation"),
    }.get(operation_type)
    if implied_count is not None and implied_count[0] not in observed:
        key, unit = implied_count
        observed[key] = {
            "key": key,
            "value": 1.0,
            "unit": unit,
            "aggregation": "sum",
            "scope": "operation",
            "provenance": "calculated",
        }
    required = {key for key, definition in registry.items() if definition.requirement == "required"} | {
        str(key).strip().casefold() for key in expected
    }
    conditional = {key for key, definition in registry.items() if definition.requirement == "conditional"}
    if any(key.startswith("tokens.") for key in observed):
        required.update(key for key in conditional if key.startswith("tokens."))
    if "cost.usd" in observed:
        required.add("cost.usd")
    optional_keys = {
        key for key, definition in registry.items() if definition.requirement in {"optional", "conditional"}
    } | {str(key).strip().casefold() for key in optional}
    facts: list[dict[str, Any]] = []
    for key in sorted(set(observed) | required | optional_keys):
        observed_item = observed.get(key)
        definition = registry.get(
            key,
            MeasurementDefinition(key, str(observed_item.get("unit") if observed_item else "unit")),
        )
        status = "measured" if observed_item is not None else "missing" if key in required else "not_applicable"
        facts.append(
            {
                "registry_version": MEASUREMENT_REGISTRY_VERSION,
                "key": key,
                "value": observed_item.get("value") if observed_item else None,
                "unit": str(observed_item.get("unit") if observed_item else definition.unit),
                "aggregation": str(observed_item.get("aggregation") if observed_item else definition.aggregation),
                "scope": str(observed_item.get("scope") if observed_item else "operation"),
                "status": status,
                "provenance": str(
                    observed_item.get("provenance") if observed_item else "declared" if key in expected else "registry"
                ),
                "applicability_source": "declared" if key in set(expected) else "registry",
            }
        )
    return facts
