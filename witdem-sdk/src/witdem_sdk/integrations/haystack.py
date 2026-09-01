"""Haystack native OpenTelemetry integration."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from witdem_sdk.integrations._common import IntegrationSettings, ResultReporter, settings

_HANDLES: dict[int, _HaystackHandle] = {}

_PROVIDERS = (
    ("azureopenai", "openai"),
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("mistral", "mistral"),
    ("cohere", "cohere"),
    ("google", "google"),
    ("gemini", "google"),
    ("bedrock", "amazon_bedrock"),
)

_MODEL_COMPONENT_MARKERS = ("generator", "embedder", "ranker")


def _semantic_tags(operation_name: str, tags: Mapping[str, Any]) -> dict[str, Any]:
    """Describe native Haystack work from callback semantics, not identity."""

    evidence = " ".join(
        (
            operation_name,
            str(tags.get("haystack.component.fully_qualified_type") or ""),
            str(tags.get("haystack.component.name") or ""),
        )
    ).casefold()
    operation_type: str | None = None
    interface = "framework"
    if "hybridsearch" in evidence or "hybrid_search" in evidence:
        operation_type, interface = "hybrid_search", "library"
    elif "vectorsearch" in evidence or "vector_search" in evidence:
        operation_type, interface = "vector_search", "library"
    elif "embedder" in evidence or "embedding" in evidence:
        operation_type, interface = "embedding", "model_api"
    elif "retriever" in evidence or "retrieval" in evidence:
        operation_type, interface = "retrieval", "library"
    elif "reranker" in evidence or "ranker" in evidence:
        operation_type, interface = "reranking", "model_api"
    elif "generator" in evidence or operation_name.casefold().endswith(".llm"):
        operation_type, interface = "text_generation", "model_api"
    elif operation_name.casefold().endswith(".tool"):
        operation_type, interface = "tool_execution", "tool"
    semantic = {
        "witdem.framework.id": "haystack",
        "witdem.execution.source": "haystack",
    }
    if operation_type:
        semantic.update(
            {
                "witdem.operation.type": operation_type,
                "witdem.operation.interface": interface,
            }
        )
    return semantic


def _require_supported_haystack() -> None:
    try:
        installed = package_version("haystack-ai")
    except PackageNotFoundError as exc:
        raise ImportError("Witdem's Haystack integration requires haystack-ai>=3.0,<4") from exc
    try:
        major = int(installed.split(".", 1)[0])
    except ValueError as exc:
        raise RuntimeError(f"Cannot determine whether haystack-ai {installed!r} is supported") from exc
    if major != 3:
        raise RuntimeError(f"Witdem's Haystack integration requires haystack-ai>=3.0,<4; found {installed}")


def _contract_result(result: Any) -> Any:
    """Expose Haystack's final message text through a stable YAML path."""

    if not isinstance(result, Mapping):
        return result
    message = result.get("last_message")
    if message is None:
        return result
    try:
        text = message.text
    except (AttributeError, TypeError, ValueError):
        return result
    if text is None:
        return result

    serialized: dict[str, Any] = {}
    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            serialized.update(value)
    serialized["text"] = text
    normalized = dict(result)
    normalized["last_message"] = serialized
    return normalized


def _integer(mapping: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _provider(identity: str, model: str | None = None) -> str | None:
    provider = next((canonical for marker, canonical in _PROVIDERS if marker in identity.casefold()), None)
    if provider is not None or model is None:
        return provider
    lowered = model.casefold()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if lowered.startswith("claude-"):
        return "anthropic"
    if lowered.startswith(("mistral-", "ministral-", "codestral-")):
        return "mistral"
    if lowered.startswith("gemini-"):
        return "google"
    return None


def _component_identity(component: Any) -> tuple[str, str] | None:
    configured = getattr(component, "chat_generator", None)
    if configured is not None:
        component = configured
    serialized: Mapping[str, Any] = {}
    to_dict = getattr(component, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            serialized = value
    parameters = serialized.get("init_parameters")
    parameters = parameters if isinstance(parameters, Mapping) else {}
    model = parameters.get("model") or getattr(component, "model", None)
    if not isinstance(model, str) or not model:
        return None
    identity = " ".join(
        (
            type(component).__module__,
            type(component).__name__,
            str(serialized.get("type") or ""),
        )
    )
    provider = _provider(identity, model)
    return (provider, model) if provider is not None else None


def _configured_identities(pipeline: Any) -> tuple[dict[str, tuple[str, str]], tuple[tuple[str, str], ...]]:
    by_component: dict[str, tuple[str, str]] = {}
    identities: list[tuple[str, str]] = []
    direct = _component_identity(pipeline)
    if direct is not None:
        identities.append(direct)
    walk = getattr(pipeline, "walk", None)
    if callable(walk):
        for name, component in walk():
            identity = _component_identity(component)
            if identity is not None:
                by_component[str(name)] = identity
                identities.append(identity)
    return by_component, tuple(dict.fromkeys(identities))


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    values: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "completion_tokens", "outputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    for target, names in aliases.items():
        observed = _integer(value, *names)
        if observed is not None:
            values[target] = observed
    if "total_tokens" not in values and {"input_tokens", "output_tokens"} <= values.keys():
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    return values


def _response_evidence(value: Any) -> tuple[str | None, str | None, dict[str, int]]:
    queue: list[Any] = [value]
    seen: set[int] = set()
    provider: str | None = None
    model: str | None = None
    observed_usage: dict[str, int] = {}
    while queue and len(seen) < 40:
        item = queue.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, Mapping):
            provider_value = item.get("provider") or item.get("provider_name")
            model_value = item.get("model") or item.get("model_name") or item.get("response_model")
            if provider is None and isinstance(provider_value, str):
                provider = _provider(provider_value) or provider_value.casefold()
            if model is None and isinstance(model_value, str):
                model = model_value
            usage_value = item.get("usage") or item.get("token_usage")
            candidate_usage = _usage(usage_value) if isinstance(usage_value, Mapping) else _usage(item)
            if candidate_usage and not observed_usage:
                observed_usage = candidate_usage
            for key in ("replies", "messages", "message", "last_message", "meta"):
                child = item.get(key)
                if child is not None:
                    queue.extend(child if isinstance(child, (list, tuple)) else [child])
            continue
        for name in ("meta", "usage"):
            child = getattr(item, name, None)
            if child is not None:
                queue.append(child)
        model_value = getattr(item, "model", None)
        if model is None and isinstance(model_value, str):
            model = model_value
    return provider, model, observed_usage


class _ObservedSpan:
    def __init__(
        self,
        span: Any,
        *,
        operation_name: str,
        tags: Mapping[str, Any],
        tracer: _ObservedTracer,
    ) -> None:
        self._span = span
        self._operation_name = operation_name
        self._tags = tags
        self._tracer = tracer
        self._tool_names: list[str] = []
        self._saw_model_child = False

    def set_tag(self, key: str, value: Any) -> None:
        self._span.set_tag(key, value)

    def set_tags(self, tags: dict[str, Any]) -> None:
        self._span.set_tags(tags)

    def set_content_tag(self, key: str, value: Any) -> None:
        with suppress(Exception):
            self._observe_output(key, value)
        self._span.set_content_tag(key, value)

    def raw_span(self) -> Any:
        return self._span.raw_span()

    def get_correlation_data_for_logs(self) -> dict[str, Any]:
        return dict(self._span.get_correlation_data_for_logs())

    def observe_child(self, operation_name: str, tags: Mapping[str, Any]) -> None:
        """Attach an observed child action to its native Agent step."""

        if self._operation_name.casefold() != "haystack.agent.step":
            return
        lowered = operation_name.casefold()
        if lowered.endswith(".llm"):
            self._saw_model_child = True
        if not lowered.endswith(".tool"):
            return
        tool_name = tags.get("haystack.tool.name")
        if isinstance(tool_name, str) and tool_name:
            self._tool_names.append(tool_name)
        self._set_step_identity()

    def finalize(self) -> None:
        """Name a tool-free completed Agent step from its observed structure."""

        if self._operation_name.casefold() == "haystack.agent.step" and not self._tool_names and self._saw_model_child:
            self._set_step_identity(final_answer=True)

    def _set_step_identity(self, *, final_answer: bool = False) -> None:
        if self._tool_names:
            name = self._tool_names[0] if len(self._tool_names) == 1 else f"{len(self._tool_names)} tool calls"
            attributes: dict[str, Any] = {
                "witdem.agent.step.name": name,
                "witdem.agent.step.action": "tool_call",
                "witdem.agent.step.tools": tuple(dict.fromkeys(self._tool_names)),
                "witdem.agent.step.name_provenance": "observed_child_tool",
            }
        elif final_answer:
            attributes = {
                "witdem.agent.step.name": "final_answer",
                "witdem.agent.step.action": "final_answer",
                "witdem.agent.step.name_provenance": "observed_tool_free_model_step",
            }
        else:
            return
        self.set_tags(attributes)

    def _observe_output(self, key: str, value: Any) -> None:
        if not key.endswith(".output"):
            return
        component_type = str(self._tags.get("haystack.component.fully_qualified_type") or "")
        is_model_boundary = "llm" in self._operation_name.casefold() or any(
            marker in component_type.casefold() for marker in _MODEL_COMPONENT_MARKERS
        )
        if not is_model_boundary:
            return
        provider, model, usage = _response_evidence(value)
        if not usage:
            return
        component_name = str(self._tags.get("haystack.component.name") or "")
        configured = self._tracer.by_component.get(component_name)
        if configured is None and model is not None:
            configured = next((item for item in self._tracer.identities if item[1] == model), None)
        if configured is None and len(self._tracer.identities) == 1:
            configured = self._tracer.identities[0]
        if configured is not None:
            provider = provider or configured[0]
            model = model or configured[1]
        provider = provider or _provider(component_type, model)
        if provider is None or model is None:
            return
        attributes = {
            "gen_ai.operation.name": "embeddings" if "embedder" in component_type.casefold() else "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.response.model": model,
            "gen_ai.usage.input_tokens": usage.get("input_tokens"),
            "gen_ai.usage.output_tokens": usage.get("output_tokens"),
            "gen_ai.usage.total_tokens": usage.get("total_tokens"),
            "pf.provider_provenance": "observed_provider_response",
            "pf.model_provenance": "observed_provider_response",
            "pf.usage_provenance": "observed_provider_response",
        }
        self.set_tags({key: value for key, value in attributes.items() if value is not None})
        self._tracer.usage_observations += 1


class _ObservedTracer:
    def __init__(
        self,
        tracer: Any,
        *,
        by_component: dict[str, tuple[str, str]],
        identities: tuple[tuple[str, str], ...],
    ) -> None:
        self._tracer = tracer
        self.by_component = by_component
        self.identities = identities
        self.usage_observations = 0
        self._active_span: ContextVar[_ObservedSpan | None] = ContextVar(
            f"witdem_haystack_active_span_{id(self)}",
            default=None,
        )
        self._spans_by_raw_id: dict[int, _ObservedSpan] = {}

    @contextmanager
    def trace(
        self,
        operation_name: str,
        tags: dict[str, Any] | None = None,
        parent_span: Any = None,
    ) -> Any:
        resolved_tags = {**dict(tags or {}), **_semantic_tags(operation_name, tags or {})}
        observed_parent = parent_span if isinstance(parent_span, _ObservedSpan) else self._active_span.get()
        if observed_parent is not None:
            observed_parent.observe_child(operation_name, resolved_tags)
        with self._tracer.trace(operation_name, tags=resolved_tags, parent_span=parent_span) as span:
            observed = _ObservedSpan(
                span,
                operation_name=operation_name,
                tags=resolved_tags,
                tracer=self,
            )
            raw_id = id(span.raw_span())
            self._spans_by_raw_id[raw_id] = observed
            token = self._active_span.set(observed)
            try:
                yield observed
            finally:
                self._active_span.reset(token)
                observed.finalize()
                if self._spans_by_raw_id.get(raw_id) is observed:
                    self._spans_by_raw_id.pop(raw_id, None)

    def current_span(self) -> Any:
        active = self._active_span.get()
        if active is not None:
            return active
        span = self._tracer.current_span()
        if span is None:
            return None
        observed = self._spans_by_raw_id.get(id(span.raw_span()))
        return observed or _ObservedSpan(span, operation_name="current", tags={}, tracer=self)


def _record_usage_summary(witdem: Any, pipeline: Any, result: Any) -> None:
    if not isinstance(result, Mapping):
        return
    usage = result.get("token_usage")
    if not isinstance(usage, Mapping):
        return
    _, identities = _configured_identities(pipeline)
    if len(identities) != 1:
        return
    input_tokens = _integer(usage, "input_tokens", "prompt_tokens")
    output_tokens = _integer(usage, "output_tokens", "completion_tokens")
    total_tokens = _integer(usage, "total_tokens")
    if input_tokens is None or output_tokens is None:
        return
    provider, model = identities[0]
    attributes = {
        "witdem.haystack.usage_summary": True,
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.total_tokens": total_tokens or input_tokens + output_tokens,
        "pf.provider_provenance": "observed_invocation_configuration",
        "pf.model_provenance": "observed_invocation_configuration",
        "pf.usage_provenance": "observed_provider_response",
    }
    with witdem.operation("witdem.haystack.usage_summary", attributes=attributes):
        pass


class _HaystackHandle:
    def __init__(self, disable: Any, key: int, tracer: _ObservedTracer) -> None:
        self._disable = disable
        self._key = key
        self._tracer = tracer

    @property
    def usage_observations(self) -> int:
        return self._tracer.usage_observations

    def disable(self) -> None:
        if self._disable is not None:
            self._disable()
            self._disable = None
            _HANDLES.pop(self._key, None)


def enable_haystack(
    witdem: Any,
    *,
    capture_content: bool = False,
    pipeline: Any = None,
) -> _HaystackHandle:
    """Enable Haystack's native tracer, using Witdem's active OTel provider."""

    _require_supported_haystack()
    key = id(witdem)
    existing = _HANDLES.get(key)
    if existing is not None:
        if existing._disable is None:
            _HANDLES.pop(key, None)
        else:
            return existing

    # Haystack's import-time auto-enabler can race its own partially
    # initialized tracing module. Witdem installs the native tracer explicitly,
    # so the automatic bootstrap is both redundant and harmful here.
    os.environ.setdefault("HAYSTACK_AUTO_TRACE_ENABLED", "false")
    try:
        from haystack.tracing import enable_tracing  # type: ignore[attr-defined,import-not-found,unused-ignore]
        from haystack_integrations.tracing.opentelemetry import (  # type: ignore[import-not-found,unused-ignore]
            OpenTelemetryTracer,
        )
        from opentelemetry import trace
    except ImportError as exc:
        raise ImportError("enable_haystack requires witdem-sdk[haystack] and haystack-ai") from exc
    by_component, identities = _configured_identities(pipeline)
    base_tracer = OpenTelemetryTracer(trace.get_tracer("witdem.haystack"))
    tracer = _ObservedTracer(base_tracer, by_component=by_component, identities=identities)
    # The wrapper implements Haystack's tracer protocol by delegation, but
    # Haystack types this hook as its concrete Tracer class.
    enable_tracing(tracer)  # type: ignore[arg-type,unused-ignore]
    handle = _HaystackHandle(lambda: getattr(base_tracer, "disable", lambda: None)(), key, tracer)
    _HANDLES[key] = handle
    return handle


class InstrumentedPipeline:
    """Transparent Haystack pipeline with an SDK-owned run lifecycle."""

    def __init__(
        self,
        pipeline: Any,
        *,
        integration_settings: IntegrationSettings,
        capture_content: bool,
    ) -> None:
        self._pipeline = pipeline
        self._settings = integration_settings
        self._capture_content = capture_content

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pipeline, name)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        with self._settings.invocation() as witdem:
            handle = enable_haystack(
                witdem,
                capture_content=self._capture_content,
                pipeline=self._pipeline,
            )
            try:
                result = self._pipeline.run(*args, **kwargs)
                if handle.usage_observations == 0:
                    _record_usage_summary(witdem, self._pipeline, result)
                reportable = result if self._settings.report_result is not None else _contract_result(result)
                self._settings.report(reportable, witdem)
                return result
            finally:
                handle.disable()

    async def run_async(self, *args: Any, **kwargs: Any) -> Any:
        with self._settings.invocation() as witdem:
            handle = enable_haystack(
                witdem,
                capture_content=self._capture_content,
                pipeline=self._pipeline,
            )
            try:
                result = await self._pipeline.run_async(*args, **kwargs)
                if handle.usage_observations == 0:
                    _record_usage_summary(witdem, self._pipeline, result)
                reportable = result if self._settings.report_result is not None else _contract_result(result)
                self._settings.report(reportable, witdem)
                return result
            finally:
                handle.disable()

    async def run_async_generator(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        """Stream native Haystack outputs while retaining one SDK lifecycle."""

        with self._settings.invocation() as witdem:
            handle = enable_haystack(
                witdem,
                capture_content=self._capture_content,
                pipeline=self._pipeline,
            )
            final_result: Any = None
            try:
                async for result in self._pipeline.run_async_generator(*args, **kwargs):
                    final_result = result
                    yield result
                if handle.usage_observations == 0:
                    _record_usage_summary(witdem, self._pipeline, final_result)
                reportable = (
                    final_result if self._settings.report_result is not None else _contract_result(final_result)
                )
                self._settings.report(reportable, witdem)
            finally:
                # An abandoned generator has no authoritative final result.
                # Its runtime spans still close through the invocation context.
                handle.disable()


def instrument(
    pipeline: Any,
    *,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> InstrumentedPipeline:
    """Wrap a Haystack pipeline with automatic Witdem instrumentation."""

    if isinstance(pipeline, InstrumentedPipeline):
        return pipeline
    _require_supported_haystack()
    return InstrumentedPipeline(
        pipeline,
        integration_settings=settings(
            service_name=service_name,
            execution_name=execution_name,
            endpoint=endpoint,
            config_path=config_path,
            attributes=attributes,
            report_result=report_result,
        ),
        capture_content=capture_content,
    )
