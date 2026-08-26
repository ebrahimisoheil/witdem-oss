"""Canonical analytical keys and human-facing names for runtime analytics.

Instance identifiers stay on the physical records and replay edges.  This
module is the boundary used by derived analytics and presentation code when it
needs an identity that should aggregate across executions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from witdem.analytics.core import Execution, Operation

_UUID_OR_HASH = re.compile(r"(?i)(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24,})")
_MODEL_VERSION_SUFFIX = re.compile(r"(?i)(?:[-_.]20\d{2}[-_.]\d{2}[-_.]\d{2})$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-zA-Z0-9]+")


def _attributes(operation: Operation) -> Mapping[str, Any]:
    return operation.attributes


def _meaningful(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or _UUID_OR_HASH.fullmatch(text):
        return None
    return text


def _slug(value: Any) -> str | None:
    text = _meaningful(value)
    if text is None:
        return None
    text = _UUID_OR_HASH.sub("", text)
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = _NON_WORD.sub("_", text).strip("_").casefold()
    return text or None


def _humanize(value: Any, fallback: str) -> str:
    text = _meaningful(value)
    if text is None:
        return fallback
    text = _UUID_OR_HASH.sub("", text)
    text = _CAMEL_BOUNDARY.sub(" ", text)
    text = re.sub(r"[_./:-]+", " ", text)
    words = text.split()
    if not words:
        return fallback
    return " ".join([words[0].capitalize(), *(word.casefold() for word in words[1:])])


def _value(operation: Operation, *names: str) -> Any:
    attributes = _attributes(operation)
    for name in names:
        value = attributes.get(name)
        if value is not None:
            return value
    return None


def _operation_service_name(operation: Operation) -> str | None:
    """Read service identity from normalized or preserved OTel resource data."""

    if value := _meaningful(operation.attributes.get("service.name")):
        return value
    resource = operation.attributes.get("otel.resource")
    if isinstance(resource, Mapping):
        return _meaningful(resource.get("service.name"))
    return None


def canonical_tool_key(operation: Operation) -> str:
    """Return a stable tool key, never an operation/span/execution ID."""

    value = _value(operation, "haystack.tool.name", "tool_name", "tool")
    if value is None and operation.kind == "tool":
        value = operation.name
    return f"tool:{_slug(value) or 'unknown'}"


def model_value(operation: Operation) -> str | None:
    value = _value(operation, "model", "gen_ai.response.model", "gen_ai.request.model")
    meaningful = _meaningful(value)
    return meaningful


def model_family(value: Any) -> str | None:
    """Collapse date-stamped response versions into one visible model family."""

    meaningful = _meaningful(value)
    if meaningful is None:
        return None
    family = _MODEL_VERSION_SUFFIX.sub("", meaningful).strip("-_. ")
    return family or meaningful


def canonical_model_key(operation: Operation) -> str:
    return f"model:{_slug(model_family(model_value(operation))) or 'unknown'}"


def display_model(operation: Operation) -> str:
    return model_family(model_value(operation)) or "Unknown model"


def canonical_stage_key(operation: Operation, semantic_stage: str | None = None) -> str:
    """Return the best stable semantic or physical stage key."""

    value = semantic_stage or _value(operation, "semantic_stage", "pf.stage", "stage")
    if value is None and operation.kind == "component":
        value = _value(operation, "haystack.component.name", "haystack.component.type")
    if value is None:
        value = _value(operation, "runtime.name") or operation.name
    return f"stage:{_slug(value) or _slug(operation.kind) or 'unknown'}"


def canonical_operation_key(operation: Operation, semantic_stage: str | None = None) -> str:
    """Return a stable grouping key for one logical runtime operation."""

    if semantic_stage:
        return canonical_stage_key(operation, semantic_stage)
    if operation.kind == "tool":
        return canonical_tool_key(operation)
    if operation.kind == "component":
        value = _value(operation, "haystack.component.name", "haystack.component.type")
        return f"component:{_slug(value) or 'unknown'}"
    if operation.kind == "model":
        role = _slug(_value(operation, "role", "pf.role"))
        if role:
            return f"model_role:{role}"
        provider = _slug(_value(operation, "provider", "gen_ai.provider.name"))
        model = _slug(_value(operation, "model", "gen_ai.response.model", "gen_ai.request.model"))
        if provider or model:
            return f"model:{provider or 'unknown'}:{model or 'unknown'}"
    value = _value(operation, "runtime.name") or operation.name
    return f"{operation.kind}:{_slug(value) or 'unknown'}"


def canonical_path_signature(operations: Sequence[Operation], semantic_stages: Mapping[str, str] | None = None) -> str:
    keys = [
        canonical_operation_key(operation, (semantic_stages or {}).get(operation.operation_id))
        for operation in operations
    ]
    return ">".join(keys) or "path:empty"


def canonical_loop_signature(pattern_keys: Sequence[str]) -> str:
    return "loop:" + ">".join(pattern_keys) if pattern_keys else "loop:empty"


def display_tool(operation: Operation) -> str:
    value = _value(operation, "haystack.tool.name", "tool_name", "tool")
    if value is None and operation.kind == "tool":
        value = operation.name
    return _humanize(value, "Tool")


def display_stage(operation: Operation, semantic_stage: str | None = None) -> str:
    value = semantic_stage or _value(operation, "semantic_stage", "pf.stage", "stage")
    if value is None and operation.kind == "component":
        value = _value(operation, "haystack.component.name", "haystack.component.type")
    if value is None:
        value = _value(operation, "runtime.name") or operation.name
    return _humanize(value, _humanize(operation.kind, "Stage"))


def display_operation(operation: Operation, semantic_stage: str | None = None) -> str:
    """Choose one human-facing operation label without exposing instance IDs."""

    if semantic_stage:
        return display_stage(operation, semantic_stage)
    if operation.kind == "tool":
        return display_tool(operation)
    if operation.kind == "component":
        return _humanize(
            _value(operation, "haystack.component.name", "haystack.component.type"),
            "Component",
        )
    if operation.kind == "model":
        role = _value(operation, "role", "pf.role")
        provider = _value(operation, "provider", "gen_ai.provider.name")
        model = model_value(operation)
        if role and provider:
            return f"{_humanize(role, 'Model')} model · {_humanize(provider, 'Provider')}"
        if role:
            return f"{_humanize(role, 'Model')} model"
        if provider or model:
            return " / ".join(item for item in (_humanize(provider, ""), _humanize(model, "")) if item) or "Model call"
        return "Model call"
    if operation.kind == "agent_step":
        action = _value(operation, "witdem.agent.step.name") or operation.name
        label = _humanize(action, "Agent step")
        index = _value(operation, "haystack.agent.step")
        if isinstance(index, (int, float)) and not isinstance(index, bool):
            return f"Step {int(index) + 1} · {label}"
        return label
    if operation.kind in {"workflow", "pipeline", "agent"}:
        workflow_name = _value(operation, "workflow.name", "workflow_name", "agent.name", "gen_ai.agent.name")
        operation_name = _meaningful(operation.name)
        if workflow_name:
            return _humanize(workflow_name, _humanize(operation.kind, "Workflow"))
        if operation_name and _slug(operation_name) not in {"workflow", "pipeline", "agent", "execution"}:
            return _humanize(operation_name, _humanize(operation.kind, "Workflow"))
    value = _value(operation, "runtime.name") or operation.name
    return _humanize(value, _humanize(operation.kind, "Operation"))


def display_path(operations: Sequence[Operation], semantic_stages: Mapping[str, str] | None = None) -> str:
    return (
        " → ".join(
            display_operation(operation, (semantic_stages or {}).get(operation.operation_id))
            for operation in operations
        )
        or "Unknown path"
    )


def display_loop(operations: Sequence[Operation], semantic_stages: Mapping[str, str] | None = None) -> str:
    return (
        " → ".join(
            display_operation(operation, (semantic_stages or {}).get(operation.operation_id))
            for operation in operations
        )
        or "Unknown loop"
    )


def display_execution(execution: Execution, operations: Sequence[Operation] = ()) -> str:
    """Return an observed, runtime-neutral label; timestamps stay separate."""

    generic_names = {"execution", "witdem execution", "witdem.execution", "workflow", "run"}
    authoritative_names = ("witdem.execution.display_name", "witdem.execution.name")
    for name in authoritative_names:
        if value := _meaningful(execution.attributes.get(name)):
            cleaned = _UUID_OR_HASH.sub("", value).strip()
            if cleaned.casefold() not in generic_names and " · " in cleaned:
                return cleaned or "Execution"

    explicit_names = (
        "execution.name",
        "display_name",
        "workflow.name",
        "workflow_name",
        "agent.name",
        "gen_ai.agent.name",
    )
    for name in explicit_names:
        if (value := _meaningful(execution.attributes.get(name))) and " · " in value:
            return _UUID_OR_HASH.sub("", value).strip() or "Execution"

    runtime = _meaningful(execution.attributes.get("runtime_id")) or _meaningful(execution.runtime_id)
    case = _meaningful(execution.attributes.get("case_id"))
    profile = _meaningful(execution.attributes.get("model_profile"))
    if case and runtime and runtime.casefold() not in {"sdk", "otel", "opentelemetry"}:
        runtime_label = {
            "langchain": "LangChain",
            "langgraph": "LangGraph",
            "openai_agents": "OpenAI Agents",
            "anthropic_messages": "Anthropic Messages",
            "haystack": "Haystack",
        }.get(runtime.casefold(), _humanize(runtime, "Run"))
        parts = [
            runtime_label,
            _humanize(case, "Run"),
            _humanize(profile, "Profile") if profile else None,
        ]
        return " · ".join(part for part in parts if part)

    for name in authoritative_names:
        if value := _meaningful(execution.attributes.get(name)):
            cleaned = _UUID_OR_HASH.sub("", value).strip()
            if cleaned.casefold() not in generic_names:
                return cleaned or "Execution"

    if value := _meaningful(execution.attributes.get("service.name")):
        return _humanize(value, "Execution")
    roots = [operation for operation in operations if operation.parent_span_id is None]
    for operation in roots:
        if operation.name.casefold() not in generic_names:
            return display_operation(operation)
    for operation in operations:
        if value := _operation_service_name(operation):
            return _humanize(value, "Execution")
    for name in explicit_names:
        if (value := _meaningful(execution.attributes.get(name))) and value.casefold() not in generic_names:
            return _humanize(value, "Execution")
    for operation in operations:
        for name in (*authoritative_names, *explicit_names, "service.name"):
            if (value := _meaningful(operation.attributes.get(name))) and value.casefold() not in generic_names:
                return _humanize(value, "Execution")
    candidates = [operation for operation in roots if operation.name.casefold() not in generic_names] or [
        operation
        for operation in operations
        if operation.kind in {"workflow", "agent", "pipeline"} and operation.name.casefold() not in generic_names
    ]
    candidates = candidates or roots
    if candidates:
        return display_operation(candidates[0])
    if runtime and runtime.lower() != "sdk":
        return _humanize(runtime, "Run")
    return f"Run {execution.execution_id[:8]}"


def display_canonical_key(key: str) -> str:
    """Humanize a canonical key when a derived row has no physical operation."""

    prefix, _, value = key.partition(":")
    if prefix in {"model_role", "model"}:
        return "Model call"
    if prefix == "tool":
        return _humanize(value, "Tool")
    if prefix in {"component", "stage", "agent_step", "pipeline", "workflow", "agent", "operation"}:
        return _humanize(value, prefix.replace("_", " ").capitalize())
    return _humanize(value or key, "Operation")
