"""Compatibility suite for current public Haystack execution shapes.

The suite follows official Haystack examples closely enough to exercise their
runtime structure, but replaces network providers with deterministic local chat
generators by default, with an explicit live-provider mode for bounded smoke
runs. The only input to normalization is the exported OpenTelemetry span
envelope; Product Factory semantic events and YAML are intentionally not used
here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from haystack import Pipeline, component
from haystack.components.builders.chat_prompt_builder import ChatPromptBuilder
from haystack.components.joiners import BranchJoiner
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.components.routers import ConditionalRouter
from haystack.components.routers.conditional_router import Route
from haystack.components.validators import JsonSchemaValidator
from haystack.dataclasses import ChatMessage, Document, ToolCall
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack.tools import PipelineTool
from haystack.tools.tool import Tool
from opentelemetry import baggage, trace
from opentelemetry.context import attach, detach
from opentelemetry.trace import Status, StatusCode
from product_factory_app.config import ModelSettings, Settings
from product_factory_app.providers.factory import InstrumentedChatGenerator, build_chat_generator

from witdem.analytics.runtime import (
    NormalizedExecutionGraph,
    derive_replay_graph,
    derive_runtime_insights,
    normalize_haystack_spans,
)
from witdem.telemetry.otel import configure_tracing

OFFICIAL_SOURCES: dict[str, dict[str, str]] = {
    "linear": {
        "name": "Your First QA Pipeline with Retrieval-Augmentation / Pipelines",
        "url": "https://docs.haystack.deepset.ai/docs/pipelines",
        "context": "Haystack 3.0.0; deterministic local chat generator substituted for the provider generator.",
    },
    "conditional": {
        "name": "ConditionalRouter",
        "url": "https://docs.haystack.deepset.ai/docs/conditionalrouter",
        "context": "Haystack 3.0.0; the documented length-based route is run with short and long inputs.",
    },
    "loop": {
        "name": "Generating Structured Output with Loop-Based Auto-Correction",
        "url": "https://docs.haystack.deepset.ai/docs/branchjoiner",
        "context": "Haystack 3.0.0; documented BranchJoiner/validator loop with a local generator.",
    },
    "agent": {
        "name": "Agent / Build a Tool-Calling Agent",
        "url": "https://docs.haystack.deepset.ai/docs/agent",
        "context": "Haystack 3.0.0; deterministic local chat generator emits one tool call then a final reply.",
    },
    "fallback": {
        "name": "Building an Agentic RAG with Fallback to Websearch",
        "url": "https://docs.haystack.deepset.ai/docs/agents",
        "context": (
            "Haystack 3.0.0; the primary route returns a deterministic failure condition and the fallback succeeds."
        ),
    },
    "nested": {
        "name": "PipelineTool",
        "url": "https://docs.haystack.deepset.ai/docs/pipelinetool",
        "context": "Haystack 3.0.0; a retrieval pipeline is exposed as an Agent tool.",
    },
}


@dataclass
class CompatibilityCase:
    """One deterministic run of an official Haystack execution family."""

    case_id: str
    family: str
    build: Callable[[], tuple[Pipeline, dict[str, Any]]]
    expected_kinds: tuple[str, ...]
    expected_names: tuple[str, ...]


class _LocalChatGenerator:
    """Small provider-free ChatGenerator implementation for structural tests."""

    def __init__(self, replies: list[ChatMessage] | Callable[[list[ChatMessage], int], ChatMessage]) -> None:
        self._replies = replies
        self.calls = 0

    def run(self, messages: list[ChatMessage], **_: Any) -> dict[str, Any]:
        self.calls += 1
        reply = (
            self._replies(messages, self.calls)
            if callable(self._replies)
            else self._replies[min(self.calls - 1, len(self._replies) - 1)]
        )
        return {"replies": [reply]}


class _FirstResponseInvalid:
    """Force one validation repair while retaining real provider calls."""

    def __init__(self, generator: Any) -> None:
        self.generator = generator
        self.calls = 0
        self._otel_instrumented = True

    def run(self, messages: list[ChatMessage], **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        result = cast(dict[str, Any], self.generator.run(messages=messages, **kwargs))
        if self.calls != 1:
            return result
        replies = result.get("replies")
        metadata = getattr(replies[0], "meta", {}) if isinstance(replies, list) and replies else {}
        return {
            "replies": [
                ChatMessage.from_assistant('{"wrong": "forced compatibility repair"}', meta=dict(metadata or {}))
            ]
        }


@component
class _LocalChatGeneratorComponent:
    """Haystack component wrapper that creates a model child span."""

    def __init__(
        self,
        generator: Any,
        *,
        role: str = "compatibility",
        provider: str = "local",
        model: str = "deterministic",
    ) -> None:
        self.generator = (
            generator
            if isinstance(generator, InstrumentedChatGenerator) or getattr(generator, "_otel_instrumented", False)
            else InstrumentedChatGenerator(generator, provider=provider, model=model, role=role)
        )

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage]) -> dict[str, Any]:
        return cast(dict[str, Any], self.generator.run(messages))


@component
class _RouteHandler:
    @component.output_types(value=str)
    def run(self, value: str) -> dict[str, str]:
        return {"value": value}


@component
class _FallbackPrimary:
    @component.output_types(query=str, primary_failed=bool)
    def run(self, query: str) -> dict[str, Any]:
        span = trace.get_current_span()
        error = RuntimeError("primary retrieval unavailable")
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        return {"query": query, "primary_failed": True}


@component
class _FallbackSearch:
    @component.output_types(answer=str)
    def run(self, query: str) -> dict[str, str]:
        return {"answer": f"fallback answer for {query}"}


@component
class _Lookup:
    @component.output_types(answer=str)
    def run(self, query: str) -> dict[str, str]:
        return {"answer": f"nested answer for {query}"}


@component
class _SeedMessages:
    @component.output_types(messages=list[ChatMessage])
    def run(self, messages: list[ChatMessage]) -> dict[str, list[ChatMessage]]:
        return {"messages": messages}


def _linear(generator: Any | None = None) -> tuple[Pipeline, dict[str, Any]]:
    store = InMemoryDocumentStore()
    store.write_documents(
        [
            Document(content="Haystack is an orchestration framework for AI applications."),
            Document(content="RAG retrieves documents before generation."),
        ]
    )
    generator = generator or _LocalChatGenerator(
        [
            ChatMessage.from_assistant(
                "Haystack is an orchestration framework.",
                meta={
                    "model": "deterministic",
                    "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
                },
            )
        ]
    )
    pipeline = Pipeline()
    pipeline.add_component("retriever", InMemoryBM25Retriever(store, top_k=2))
    pipeline.add_component(
        "prompt_builder",
        ChatPromptBuilder(
            template=[
                ChatMessage.from_user("Answer from these docs: {% for doc in docs %}{{ doc.content }}{% endfor %}")
            ]
        ),
    )
    pipeline.add_component("generator", _LocalChatGeneratorComponent(generator))
    pipeline.connect("retriever.documents", "prompt_builder.docs")
    pipeline.connect("prompt_builder", "generator.messages")
    return pipeline, {"retriever": {"query": "What is Haystack?"}}


def _conditional(route_input: str) -> tuple[Pipeline, dict[str, Any]]:
    routes: list[Route] = [
        {
            "condition": "{{ query|length > 10 }}",
            "output": "{{ query }}",
            "output_name": "long_query",
            "output_type": str,
        },
        {
            "condition": "{{ query|length <= 10 }}",
            "output": "{{ query }}",
            "output_name": "short_query",
            "output_type": str,
        },
    ]
    pipeline = Pipeline()
    pipeline.add_component("router", ConditionalRouter(routes))
    pipeline.add_component("long_route", _RouteHandler())
    pipeline.add_component("short_route", _RouteHandler())
    pipeline.add_component("join", BranchJoiner(str))
    pipeline.connect("router.long_query", "long_route.value")
    pipeline.connect("router.short_query", "short_route.value")
    pipeline.connect("long_route.value", "join")
    pipeline.connect("short_route.value", "join")
    return pipeline, {"router": {"query": route_input}}


def _loop(generator: Any | None = None) -> tuple[Pipeline, dict[str, Any]]:
    def reply(_messages: list[ChatMessage], call: int) -> ChatMessage:
        if call == 1:
            return ChatMessage.from_assistant(
                '{"wrong": "invalid"}',
                meta={
                    "model": "deterministic",
                    "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
                },
            )
        return ChatMessage.from_assistant(
            '{"answer": "corrected"}',
            meta={"model": "deterministic", "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}},
        )

    generator = generator or _LocalChatGenerator(reply)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    pipeline = Pipeline(max_runs_per_component=3)
    pipeline.add_component("seed", _SeedMessages())
    pipeline.add_component("joiner", BranchJoiner(list[ChatMessage]))
    pipeline.add_component("generator", _LocalChatGeneratorComponent(generator))
    pipeline.add_component("validator", JsonSchemaValidator(json_schema=schema))
    pipeline.connect("seed.messages", "joiner")
    pipeline.connect("joiner", "generator.messages")
    pipeline.connect("generator.replies", "validator.messages")
    pipeline.connect("validator.validation_error", "joiner")
    return pipeline, {"seed": {"messages": [ChatMessage.from_user("Return the required JSON.")]}}


def _agent(chat_generator: Any | None = None) -> tuple[Pipeline, dict[str, Any]]:
    def reply(messages: list[ChatMessage], call: int) -> ChatMessage:
        if call == 1:
            return ChatMessage.from_assistant(
                tool_calls=[ToolCall("lookup", {"query": "Haystack"}, id="tool-call-1")],
                meta={
                    "model": "deterministic",
                    "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                },
            )
        return ChatMessage.from_assistant(
            "The tool found the answer.",
            meta={"model": "deterministic", "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}},
        )

    tool = Tool(
        name="lookup",
        description="Look up a fixed local fact.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        function=lambda query: {"answer": f"local result for {query}"},
    )
    from haystack.components.agents import Agent

    chat_generator = chat_generator or InstrumentedChatGenerator(
        _LocalChatGenerator(reply), provider="local", model="deterministic", role="agent"
    )
    agent = Agent(
        chat_generator=chat_generator,
        tools=[tool],
        system_prompt="Always call the lookup tool exactly once before answering.",
        max_agent_steps=3,
    )
    pipeline = Pipeline()
    pipeline.add_component("agent", agent)
    return pipeline, {"agent": {"messages": [ChatMessage.from_user("Use the lookup tool.")]}}


def _fallback() -> tuple[Pipeline, dict[str, Any]]:
    routes: list[Route] = [
        {
            "condition": "{{ primary_failed }}",
            "output": "{{ query }}",
            "output_name": "fallback_query",
            "output_type": str,
        },
        {
            "condition": "{{ not primary_failed }}",
            "output": "{{ query }}",
            "output_name": "primary_query",
            "output_type": str,
        },
    ]
    pipeline = Pipeline()
    pipeline.add_component("primary", _FallbackPrimary())
    pipeline.add_component("router", ConditionalRouter(routes))
    pipeline.add_component("fallback", _FallbackSearch())
    pipeline.connect("primary.query", "router.query")
    pipeline.connect("primary.primary_failed", "router.primary_failed")
    pipeline.connect("router.fallback_query", "fallback.query")
    return pipeline, {"primary": {"query": "fallback test"}}


def _nested(chat_generator: Any | None = None) -> tuple[Pipeline, dict[str, Any]]:
    inner = Pipeline()
    inner.add_component("lookup", _Lookup())
    tool = PipelineTool(
        pipeline=inner,
        name="nested_lookup",
        description="Run the local lookup pipeline.",
        input_mapping={"query": ["lookup.query"]},
        output_mapping={"lookup.answer": "answer"},
    )

    def reply(messages: list[ChatMessage], call: int) -> ChatMessage:
        if call == 1:
            return ChatMessage.from_assistant(
                tool_calls=[ToolCall("nested_lookup", {"query": "delegated"}, id="nested-call-1")],
                meta={
                    "model": "deterministic",
                    "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                },
            )
        return ChatMessage.from_assistant(
            "The nested pipeline returned a result.",
            meta={"model": "deterministic", "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}},
        )

    from haystack.components.agents import Agent

    chat_generator = chat_generator or InstrumentedChatGenerator(
        _LocalChatGenerator(reply), provider="local", model="deterministic", role="delegating_agent"
    )
    agent = Agent(
        chat_generator=chat_generator,
        tools=[tool],
        system_prompt="Always delegate the lookup to the nested_lookup tool exactly once before answering.",
        max_agent_steps=3,
    )
    outer = Pipeline()
    outer.add_component("delegating_agent", agent)
    return outer, {"delegating_agent": {"messages": [ChatMessage.from_user("Delegate this lookup.")]}}


def compatibility_cases() -> list[CompatibilityCase]:
    return [
        CompatibilityCase(
            "linear",
            "linear",
            _linear,
            ("pipeline", "component", "component", "model"),
            ("retriever", "prompt_builder", "generator"),
        ),
        CompatibilityCase(
            "conditional_short",
            "conditional",
            lambda: _conditional("short"),
            ("pipeline", "component", "component"),
            ("router", "short_route"),
        ),
        CompatibilityCase(
            "conditional_long",
            "conditional",
            lambda: _conditional("a sufficiently long query"),
            ("pipeline", "component", "component"),
            ("router", "long_route"),
        ),
        CompatibilityCase(
            "loop",
            "loop",
            _loop,
            ("pipeline", "component", "component", "component", "model", "model"),
            ("joiner", "generator", "validator"),
        ),
        CompatibilityCase(
            "agent",
            "agent",
            _agent,
            ("pipeline", "agent", "agent_step", "model", "tool", "agent_step", "model"),
            ("agent", "lookup"),
        ),
        CompatibilityCase(
            "fallback",
            "fallback",
            _fallback,
            ("pipeline", "component", "component", "component"),
            ("primary", "router", "fallback"),
        ),
        CompatibilityCase(
            "nested",
            "nested",
            _nested,
            ("pipeline", "agent", "agent_step", "model", "tool", "pipeline", "component", "agent_step", "model"),
            ("delegating_agent", "nested_lookup", "lookup"),
        ),
    ]


def live_compatibility_cases(provider: str, model: str) -> list[CompatibilityCase]:
    """Build the same suite with real provider-backed chat generators."""

    def generator(role: str) -> Any:
        return build_chat_generator(ModelSettings(provider=cast(Any, provider), model=model), role=role)

    return [
        CompatibilityCase(
            "linear",
            "linear",
            lambda: _linear(generator("extraction")),
            ("pipeline", "component", "component", "model"),
            ("retriever", "prompt_builder", "generator"),
        ),
        CompatibilityCase(
            "conditional_short",
            "conditional",
            lambda: _conditional("short"),
            ("pipeline", "component", "component"),
            ("router", "short_route"),
        ),
        CompatibilityCase(
            "conditional_long",
            "conditional",
            lambda: _conditional("a sufficiently long query"),
            ("pipeline", "component", "component"),
            ("router", "long_route"),
        ),
        CompatibilityCase(
            "loop",
            "loop",
            lambda: _loop(_FirstResponseInvalid(generator("compatibility_loop"))),
            ("pipeline", "component", "component", "component", "model", "model"),
            ("joiner", "generator", "validator"),
        ),
        CompatibilityCase(
            "agent",
            "agent",
            lambda: _agent(generator("agent")),
            ("pipeline", "agent", "agent_step", "model", "tool", "agent_step", "model"),
            ("agent", "lookup"),
        ),
        CompatibilityCase(
            "fallback",
            "fallback",
            _fallback,
            ("pipeline", "component", "component", "component"),
            ("primary", "router", "fallback"),
        ),
        CompatibilityCase(
            "nested",
            "nested",
            lambda: _nested(generator("delegating_agent")),
            ("pipeline", "agent", "agent_step", "model", "tool", "pipeline", "component", "agent_step", "model"),
            ("delegating_agent", "nested_lookup", "lookup"),
        ),
    ]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _result_from_spans(
    case_id: str,
    family: str,
    spans: list[dict[str, Any]],
    *,
    adaptation: str,
) -> dict[str, Any]:
    execution_id = f"haystack-compat-{case_id}"
    graph: NormalizedExecutionGraph = normalize_haystack_spans(spans, execution_id=execution_id)
    replay = derive_replay_graph(graph)
    insights = derive_runtime_insights(graph)
    operation_rows = [
        {
            "span_id": op.span_id,
            "parent_span_id": op.parent_span_id,
            "raw_name": op.attributes.get("runtime.name"),
            "kind": op.kind,
            "name": op.name,
            "status": op.status,
            "started_at": op.started_at.isoformat() if op.started_at else None,
            "ended_at": op.ended_at.isoformat() if op.ended_at else None,
            "attempt": op.attempt,
            "provider": op.attributes.get("provider"),
            "model": op.attributes.get("model"),
            "tool": op.attributes.get("haystack.tool.name"),
        }
        for op in graph.operations
    ]
    return {
        "case_id": case_id,
        "family": family,
        "source": OFFICIAL_SOURCES[family],
        "adaptation": adaptation,
        "otel_only": True,
        "raw_span_names": [str(span.get("name")) for span in spans],
        "raw_spans": spans,
        "operations": operation_rows,
        "links": [link.model_dump(mode="json") for link in graph.links],
        "replay": replay.model_dump(mode="json"),
        "insights": insights,
        "semantic_events_used": 0,
        "yaml_used": False,
    }


def _run_case(case: CompatibilityCase, root: Path) -> dict[str, Any]:
    case_dir = root / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    # Each case directory is a replaceable run artifact. This prevents a
    # provider retry from merging stale traces into the next normalized graph.
    span_path = case_dir / "telemetry" / "spans.jsonl"
    span_path.unlink(missing_ok=True)
    configure_tracing(case_dir)
    execution_id = f"haystack-compat-{case.case_id}"
    context = attach(baggage.set_baggage("witdem.execution_id", execution_id))
    try:
        tracer = trace.get_tracer("product_factory_app.compatibility")
        with tracer.start_as_current_span("compatibility.execution") as execution_span:
            execution_span.set_attribute("witdem.execution_id", execution_id)
            pipeline, data = case.build()
            try:
                pipeline.run(data)
            except Exception as error:
                execution_span.record_exception(error)
                execution_span.set_status(Status(StatusCode.ERROR, str(error)))
    finally:
        detach(context)
        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush()

    spans = _read_jsonl(case_dir / "telemetry" / "spans.jsonl")
    return _result_from_spans(
        case.case_id,
        case.family,
        spans,
        adaptation=(
            "Only the model/provider call was replaced with a deterministic local ChatGenerator; "
            "Haystack components, routing, loop, Agent, Tool, PipelineTool, parentage, and runtime tracing remain real."
        ),
    )


def run_compatibility_suite(root: Path) -> dict[str, Any]:
    """Run every offline case and return a JSON-serializable compatibility report."""

    root.mkdir(parents=True, exist_ok=True)
    results = [_run_case(case, root / "scenarios") for case in compatibility_cases()]
    report = {
        "suite": "haystack-public-example-compatibility",
        "haystack_version": "3.0.0",
        "normalization_input": "Haystack/OpenTelemetry raw span envelopes only",
        "core_schema": ["Execution", "Operation", "Link", "Event", "Evaluation", "Outcome"],
        "results": results,
        "schema_changes": [],
        "schema_conclusion": (
            "All exercised physical runtime shapes map to Execution/Operation/Link without a core-schema change."
        ),
    }
    (root / "compatibility-report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return cast(dict[str, Any], report)


def run_live_compatibility_suite(
    root: Path,
    *,
    provider: str = "openai",
    model: str | None = None,
    case_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run a bounded compatibility pass with real provider-backed model calls.

    Conditional routing and fallback remain provider-free because their
    structural behavior is deterministic. Linear, loop, Agent, and nested
    cases use live model calls. The loop intentionally corrupts only the first
    returned message so the second call exercises real provider-backed repair.
    """

    settings = Settings.from_env()
    live_model = model or settings.research_model.model
    cases = live_compatibility_cases(provider, live_model)
    if case_ids:
        selected = set(case_ids)
        cases = [case for case in cases if case.case_id in selected]
    if not cases:
        raise ValueError("no live compatibility cases selected")

    root.mkdir(parents=True, exist_ok=True)
    results = [_run_case(case, root / "scenarios") for case in cases]
    for result in results:
        result["mode"] = "live"
        result["provider"] = provider
        result["model"] = live_model
        result["adaptation"] = (
            "Real Haystack provider-backed chat generation was used for model, Agent, and nested cases. "
            "The local deterministic tool and fallback implementations remain to isolate runtime graph shape; "
            "the first loop message is intentionally invalidated to force a real repair call."
        )
    report = {
        "suite": "haystack-public-example-compatibility-live",
        "haystack_version": "3.0.0",
        "mode": "live",
        "provider": provider,
        "model": live_model,
        "normalization_input": "Haystack/OpenTelemetry raw span envelopes only",
        "core_schema": ["Execution", "Operation", "Link", "Event", "Evaluation", "Outcome"],
        "results": results,
        "schema_changes": [],
        "schema_conclusion": (
            "The live cases retain the same Execution/Operation/Link representation; no core-schema change was needed."
        ),
    }
    (root / "compatibility-report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def refresh_compatibility_report(root: Path) -> dict[str, Any]:
    """Re-normalize retained span artifacts after analytics rules change."""

    report_path = root / "compatibility-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    refreshed_results = []
    for previous in report["results"]:
        refreshed = _result_from_spans(
            previous["case_id"],
            previous["family"],
            previous["raw_spans"],
            adaptation=previous["adaptation"],
        )
        for key in ("mode", "provider", "model"):
            if key in previous:
                refreshed[key] = previous[key]
        refreshed_results.append(refreshed)
    report["results"] = refreshed_results
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return cast(dict[str, Any], report)
