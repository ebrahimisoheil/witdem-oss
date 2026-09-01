"""Vendor-neutral AI operation taxonomy and measurement registry."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from witdem.analytics.core import Operation

OPERATION_TAXONOMY_VERSION = "2"
MEASUREMENT_REGISTRY_VERSION = "1"

OperationFamily = str

OPERATION_FAMILIES: dict[str, OperationFamily] = {
    "workflow": "orchestration",
    "agent": "orchestration",
    "chain": "orchestration",
    "component": "orchestration",
    "prompt": "orchestration",
    "text_generation": "inference",
    "multimodal_generation": "inference",
    "embedding": "inference",
    "reranking": "inference",
    "classification": "inference",
    "moderation": "inference",
    "structured_extraction": "inference",
    "retrieval": "knowledge",
    "vector_search": "knowledge",
    "keyword_search": "knowledge",
    "hybrid_search": "knowledge",
    "search": "knowledge",
    "indexing": "knowledge",
    "document_loading": "knowledge",
    "graph_query": "knowledge",
    "database_query": "tools",
    "function_execution": "tools",
    "api_request": "tools",
    "ocr": "media",
    "document_processing": "media",
    "image_generation": "media",
    "image_edit": "media",
    "image_understanding": "media",
    "audio_transcription": "media",
    "speech_recognition": "media",
    "speech_synthesis": "media",
    "audio_generation": "media",
    "audio_understanding": "media",
    "video_generation": "media",
    "video_edit": "media",
    "video_understanding": "media",
    "tool": "tools",
    "tool_execution": "tools",
    "api_call": "tools",
    "code_execution": "tools",
    "browser_action": "external_action",
    "database_write": "external_action",
    "mcp_connection": "mcp",
    "mcp_server": "mcp",
    "mcp_tool_call": "mcp",
    "mcp_resource_read": "mcp",
    "mcp_prompt_retrieval": "mcp",
    "mcp_capability_discovery": "mcp",
    "planning": "agent_control",
    "delegation": "agent_control",
    "handoff": "agent_control",
    "routing": "agent_control",
    "reflection": "agent_control",
    "retry": "agent_control",
    "loop": "agent_control",
    "branch": "orchestration",
    "join": "orchestration",
    "fan_out": "orchestration",
    "checkpoint": "orchestration",
    "state_transition": "orchestration",
    "human_interrupt": "orchestration",
    "guardrail": "quality",
    "evaluation": "quality",
    "judging": "quality",
    "validation": "quality",
    "policy_check": "quality",
    "grounding_check": "quality",
    "memory_read": "memory",
    "memory_write": "memory",
    "memory_consolidation": "memory",
    "memory_summarization": "memory",
    "memory_eviction": "memory",
    "approval": "human_work",
    "rejection": "human_work",
    "correction": "human_work",
    "escalation": "human_work",
    "feedback": "human_work",
    "email": "external_action",
    "ticket_creation": "external_action",
    "deployment": "external_action",
    "payment": "external_action",
    "parsing": "data_movement",
    "transformation": "data_movement",
    "batch_ingestion": "data_movement",
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
    "classification": "classification",
    "classify": "classification",
    "moderation": "moderation",
    "search": "search",
    "vector_search": "vector_search",
    "keyword_search": "keyword_search",
    "hybrid_search": "hybrid_search",
    "indexing": "indexing",
    "document_loading": "document_loading",
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
    "tool_execution": "tool_execution",
    "api_call": "api_call",
    "code_execution": "code_execution",
    "browser_action": "browser_action",
    "database_write": "database_write",
    "evaluation": "evaluation",
    "judging": "judging",
    "validation": "validation",
    "guardrail": "guardrail",
    "policy_check": "policy_check",
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

# Adapter metadata uses operation vocabulary, never participant names.  These
# aliases are deliberately shared across integrations so a callback does not
# need provider-specific classification rules.
ADAPTER_OPERATION_TYPES = {
    **OTEL_OPERATION_TYPES,
    "embed": "embedding",
    "aembedding": "embedding",
    "reranking": "reranking",
    "vectorsearch": "vector_search",
    "keywordsearch": "keyword_search",
    "hybridsearch": "hybrid_search",
    "load_documents": "document_loading",
    "tool": "tool_execution",
}

_METADATA_TYPE_KEYS = (
    "witdem.adapter.operation.type",
    "call_type",
    "litellm.call_type",
    "rpc.method",
)

_BOUNDED_NAME_MARKERS = (
    ("hybrid_search", "hybrid_search"),
    ("hybridsearch", "hybrid_search"),
    ("vector_search", "vector_search"),
    ("vectorsearch", "vector_search"),
    ("keyword_search", "keyword_search"),
    ("keywordsearch", "keyword_search"),
    ("embedder", "embedding"),
    ("embedding", "embedding"),
    ("retriever", "retrieval"),
    ("retrieval", "retrieval"),
    ("reranker", "reranking"),
    ("reranking", "reranking"),
    ("moderation", "moderation"),
    ("classifier", "classification"),
    ("document_loader", "document_loading"),
)

VALID_FAMILIES = frozenset(
    {
        "orchestration", "inference", "knowledge", "tools", "mcp", "agent_control",
        "media", "quality", "memory", "human_work", "external_action", "data_movement",
        "action", "custom",
    }
)
VALID_INTERFACES = frozenset(
    {
        "model_api", "tool", "framework", "datastore", "vector_database", "search_service",
        "mcp", "library", "local", "external_api", "browser", "human", "unknown",
    }
)
VALID_ROLES = frozenset(
    {"application", "model", "tool", "agent", "evaluator", "guardrail", "system", "human", "control"}
)
VALID_MODALITIES = frozenset({"text", "structured", "document", "vector", "image", "audio", "video"})
VALID_ENTITY_KINDS = frozenset({"execution", "operation", "business_event"})
VALID_PLANES = frozenset({"control", "work", "business"})

_CONTROL_FAMILIES = frozenset({"orchestration", "agent_control"})
_CONTROL_MCP_TYPES = frozenset({"mcp_connection", "mcp_server", "mcp_capability_discovery"})
_MODEL_APPLICABLE_FAMILIES = frozenset({"inference", "media"})


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
        "candidates": MeasurementDefinition("candidates", "document"),
        "documents.output": MeasurementDefinition("documents.output", "document", requirement="required"),
        "results": MeasurementDefinition("results", "document"),
        "top_k": MeasurementDefinition("top_k", "document", "latest"),
    },
    "vector_search": {
        "queries": MeasurementDefinition("queries", "query", requirement="required"),
        "candidates": MeasurementDefinition("candidates", "document"),
        "results": MeasurementDefinition("results", "document", requirement="required"),
        "top_k": MeasurementDefinition("top_k", "document", "latest"),
    },
    "keyword_search": {
        "queries": MeasurementDefinition("queries", "query", requirement="required"),
        "results": MeasurementDefinition("results", "document", requirement="required"),
        "top_k": MeasurementDefinition("top_k", "document", "latest"),
    },
    "hybrid_search": {
        "queries": MeasurementDefinition("queries", "query", requirement="required"),
        "candidates": MeasurementDefinition("candidates", "document"),
        "results": MeasurementDefinition("results", "document", requirement="required"),
        "top_k": MeasurementDefinition("top_k", "document", "latest"),
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
        "documents.input": MeasurementDefinition("documents.input", "document"),
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
    "tool": {
        "tool.calls": MeasurementDefinition("tool.calls", "call", requirement="required"),
        "retries": MeasurementDefinition("retries", "retry"),
        "successes": MeasurementDefinition("successes", "call"),
        "failures": MeasurementDefinition("failures", "call"),
    },
    "tool_execution": {
        "tool.calls": MeasurementDefinition("tool.calls", "call", requirement="required"),
        "retries": MeasurementDefinition("retries", "retry"),
        "successes": MeasurementDefinition("successes", "call"),
        "failures": MeasurementDefinition("failures", "call"),
    },
    "code_execution": {
        "code.executions": MeasurementDefinition("code.executions", "execution", requirement="required")
    },
    "evaluation": {
        "evaluations": MeasurementDefinition("evaluations", "evaluation", requirement="required"),
        "score": MeasurementDefinition("score", "score", "average"),
        "target": MeasurementDefinition("target", "score", "latest"),
        "passes": MeasurementDefinition("passes", "evaluation"),
        "failures": MeasurementDefinition("failures", "evaluation"),
    },
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
    "candidates": ("candidates", "document"),
    "results": ("results", "document"),
    "top_k": ("top_k", "document"),
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


def _canonical_adapter_type(value: Any) -> str | None:
    candidate = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return ADAPTER_OPERATION_TYPES.get(candidate)


def _bounded_metadata_type(operation: Operation) -> str | None:
    """Infer semantics only from class/function vocabulary, never identity."""

    attributes = operation.attributes
    evidence = " ".join(
        str(attributes.get(key) or "")
        for key in (
            "code.function.name",
            "code.function",
            "haystack.component.fully_qualified_type",
            "witdem.component.class",
            "witdem.function.name",
        )
    )
    # Span names are allowed only when they contain an explicit operation word.
    evidence = f"{evidence} {operation.name}".casefold().replace("-", "_").replace(" ", "_")
    return next((operation_type for marker, operation_type in _BOUNDED_NAME_MARKERS if marker in evidence), None)


def operation_identity(operation: Operation) -> dict[str, Any]:
    """Return a versioned identity using explicit semantic attributes only."""

    attributes = operation.attributes
    explicit_type = str(attributes.get("witdem.operation.type") or "").strip().casefold()
    otel_name = str(attributes.get("gen_ai.operation.name") or "").strip().casefold()
    oi_kind = str(attributes.get("openinference.span.kind") or attributes.get("openinference.kind") or "").upper()
    adapter_type = next(
        (
            canonical
            for key in _METADATA_TYPE_KEYS
            if attributes.get(key)
            if (canonical := _canonical_adapter_type(attributes.get(key))) is not None
        ),
        None,
    )
    explicit_entity_kind = str(attributes.get("witdem.entity.kind") or "").strip().casefold()
    execution_name = str(attributes.get("witdem.execution.name") or "").strip()
    entity_kind = (
        explicit_entity_kind
        if explicit_entity_kind in VALID_ENTITY_KINDS
        else "execution"
        if operation.parent_span_id is None and execution_name
        else "operation"
    )
    runtime_kind = str(attributes.get("witdem.runtime.kind") or attributes.get("runtime.kind") or "").strip().casefold()
    operation_type = (
        explicit_type
        or OTEL_OPERATION_TYPES.get(otel_name)
        or OPENINFERENCE_OPERATION_TYPES.get(oi_kind)
        or adapter_type
        or _bounded_metadata_type(operation)
    )
    if not operation_type:
        operation_type = ({
            "workflow": "workflow",
            "pipeline": "workflow",
            "agent": "agent",
        }.get(runtime_kind) if entity_kind == "execution" else None) or {
            "workflow": "workflow",
            "pipeline": "workflow",
            "agent": "agent",
            "component": "component",
            "tool": "tool",
        }.get(operation.kind, "unknown")
    explicit_family = str(attributes.get("witdem.operation.family") or "").strip().casefold()
    family: OperationFamily = (
        explicit_family if explicit_family in VALID_FAMILIES else OPERATION_FAMILIES.get(operation_type, "custom")
    )
    interface = str(attributes.get("witdem.operation.interface") or "").strip().casefold()
    if interface not in VALID_INTERFACES:
        interface = (
            "tool"
            if operation.kind == "tool"
            else "model_api"
            if family in {"inference", "media"}
            else "framework"
            if family == "orchestration"
            else "local"
        )
    explicit_plane = str(attributes.get("witdem.operation.plane") or "").strip().casefold()
    plane = (
        None
        if entity_kind == "execution"
        else explicit_plane
        if explicit_plane in VALID_PLANES
        else "control"
        if family in _CONTROL_FAMILIES or operation_type in _CONTROL_MCP_TYPES
        else "work"
    )
    role = str(
        attributes.get("witdem.operation.role")
        or ("control" if plane == "control" else "application")
    ).strip().casefold()
    if role not in VALID_ROLES:
        role = "application"
    model_reported = any(
        attributes.get(key)
        for key in ("gen_ai.response.model", "gen_ai.request.model", "model")
    )
    model_applicability = (
        "applicable" if model_reported or family in _MODEL_APPLICABLE_FAMILIES else "not_applicable"
    )
    return {
        "taxonomy_version": OPERATION_TAXONOMY_VERSION,
        "entity_kind": entity_kind,
        "plane": plane,
        "family": family,
        "type": operation_type,
        "subtype": str(attributes.get("witdem.operation.subtype") or otel_name or oi_kind or operation.name),
        "interface": interface,
        "role": role,
        "model_applicability": model_applicability,
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
        "tool_execution": ("tool.calls", "call"),
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
