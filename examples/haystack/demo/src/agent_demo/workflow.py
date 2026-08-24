"""The one Haystack pipeline/agent builder shared by every scenario and both modes.

This module is deliberately mode-blind: nothing in here has ever heard of
"telemetry_only" or "sdk_enriched". ``build_workflow`` builds one set of
``@component`` classes and Haystack ``Pipeline`` objects; ``run_workflow``
orchestrates them with a bounded, scenario-driven loop. The only thing that
can make one run's physical shape (spans emitted) differ from another's is
which scenario's deterministic script (see ``fake_provider.py`` /
``scenarios/*.py``) is being replayed -- never the mode. api.py is the only
place mode is known, and it only acts on it *after* this module returns a
result, by optionally calling out to ``enrichment.py`` (see there).

The demo domain: a tiny tool-using QA agent with two tools, ``lookup``
(a fixed in-memory knowledge base) and ``calculator`` (a small safe
arithmetic evaluator), plus a compound ``multi_step_lookup`` tool used only
by the "nested" scenario to demonstrate genuinely nested child spans.
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Any

from haystack import Pipeline, component
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agent_demo.fake_provider import FakeChatProvider, ScriptedTurn
from agent_demo.otel_setup import bind_execution_id, detach_execution_id, get_tracer

# ---------------------------------------------------------------------------
# Tool implementations: deterministic, dependency-free, safe.
# ---------------------------------------------------------------------------


class ToolExecutionError(RuntimeError):
    """Raised by a tool implementation; ``ExecuteTool`` turns this into a failed ToolResult."""


_KNOWLEDGE_BASE: dict[str, str] = {
    "capital_of_france": "Paris",
    "largest_planet": "Jupiter",
    "speed_of_light_m_s": "299792458",
    "haystack_framework_creator": "deepset",
}


def _lookup(args: dict[str, Any]) -> str:
    topic = args.get("topic")
    if not isinstance(topic, str) or topic not in _KNOWLEDGE_BASE:
        raise ToolExecutionError(f"lookup: unknown topic {topic!r}")
    return _KNOWLEDGE_BASE[topic]


_ARITHMETIC_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_arithmetic_node(node: ast.AST) -> float:
    """Whitelist-only arithmetic evaluator: literals, +-*/** and unary +/- only.

    No names, calls, attributes, subscripts, or comprehensions are ever
    accepted, so this is safe to run on arbitrary strings without a real
    ``eval()``.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC_OPS:
        left, right = _eval_arithmetic_node(node.left), _eval_arithmetic_node(node.right)
        return float(_ARITHMETIC_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ARITHMETIC_OPS:
        return float(_ARITHMETIC_OPS[type(node.op)](_eval_arithmetic_node(node.operand)))
    raise ValueError(f"unsupported expression node: {ast.dump(node)}")


def _calculate(args: dict[str, Any]) -> float:
    expression = args.get("expression")
    if not isinstance(expression, str):
        raise ToolExecutionError("calculator: 'expression' must be a string")
    try:
        node = ast.parse(expression, mode="eval").body
        return _eval_arithmetic_node(node)
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise ToolExecutionError(f"calculator: invalid expression {expression!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Shared state/result types
# ---------------------------------------------------------------------------


@dataclass
class ConversationState:
    question: str
    scenario: str
    execution_id: str
    turn_count: int = 0
    tool_call_count: int = 0
    correction_count: int = 0
    used_fallback: bool = False
    final_answer: str | None = None
    quality_score: float | None = None
    last_tool_error: str | None = None


@dataclass
class ToolResult:
    tool_name: str
    ok: bool
    output: str | None = None
    error: str | None = None


@dataclass
class AnswerEvaluation:
    quality_score: float
    accepted: bool
    reason: str


@dataclass
class WorkflowResult:
    """Everything api.py needs to decide its (mode-gated) SDK enrichment calls."""

    execution_id: str
    scenario: str
    status: str  # "success" | "failed"
    route: str  # "direct_answer" | "tool_assisted" | "fallback_recovery" | "terminal_failure"
    final_answer: str | None
    quality_score: float | None
    tool_call_count: int
    correction_count: int
    used_fallback: bool
    turn_count: int


# ---------------------------------------------------------------------------
# Haystack components -- identical across every scenario and both modes.
# ---------------------------------------------------------------------------


@component
class InterpretQuery:
    """Create the first conversation state from a scenario's question."""

    @component.output_types(state=ConversationState)
    def run(self, question: str, scenario: str, execution_id: str) -> dict[str, Any]:
        return {"state": ConversationState(question=question, scenario=scenario, execution_id=execution_id)}


@component
class GenerateModelTurn:
    """Ask the (fake, deterministic) chat model for the next turn of the conversation.

    Returns a ``turn`` output only when the model wants to call a tool;
    when it produces a final answer, ``turn`` is omitted entirely. Haystack
    only runs a downstream-connected component once all of its mandatory
    inputs have actually arrived, so omitting ``turn`` is what makes
    ``execute_tool`` simply not run for a plain final-answer turn -- this is
    the one thing that makes the shared pipeline's physical shape vary by
    scenario at all, and it is a standard Haystack conditional-branching
    idiom (the same mechanism ``ConditionalRouter`` relies on), not a hack.
    """

    def __init__(self, provider: FakeChatProvider) -> None:
        self.provider = provider

    @component.output_types(state=ConversationState, turn=ScriptedTurn)
    def run(self, state: ConversationState) -> dict[str, Any]:
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.execution_id)
        turn = self.provider.next_turn(state.scenario, state.turn_count)
        state.turn_count += 1
        span.set_attribute("agent_demo.turn_kind", turn.kind)
        if turn.kind == "final_answer":
            state.final_answer = turn.answer_text
            state.quality_score = turn.quality_score
            return {"state": state}
        return {"state": state, "turn": turn}


@component
class ExecuteTool:
    """Execute a requested tool deterministically, recording success/error on the active span.

    Never raises out of ``run()``: a failing tool is a normal, expected
    outcome for the failure_recovery/terminal_failure scenarios, so it is
    represented as an ERROR-status span plus a ``ToolResult(ok=False, ...)``
    the caller can act on, exactly like ``ResearchCompany.run()`` does in
    the sibling project for source failures.
    """

    @component.output_types(state=ConversationState, tool_result=ToolResult)
    def run(self, state: ConversationState, turn: ScriptedTurn) -> dict[str, Any]:
        span = trace.get_current_span()
        tool_name = turn.tool_name or "unknown"
        span.set_attribute("witdem.execution_id", state.execution_id)
        span.set_attribute("agent_demo.tool_name", tool_name)
        state.tool_call_count += 1
        try:
            output = self._invoke(turn)
        except ToolExecutionError as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            state.last_tool_error = str(exc)
            return {"state": state, "tool_result": ToolResult(tool_name=tool_name, ok=False, error=str(exc))}
        span.set_status(Status(StatusCode.OK))
        state.last_tool_error = None
        return {"state": state, "tool_result": ToolResult(tool_name=tool_name, ok=True, output=output)}

    def _invoke(self, turn: ScriptedTurn) -> str:
        if turn.force_tool_error:
            raise ToolExecutionError(f"{turn.tool_name}: simulated tool/provider failure")
        if turn.tool_name == "lookup":
            return _lookup(turn.tool_args or {})
        if turn.tool_name == "calculator":
            return str(_calculate(turn.tool_args or {}))
        if turn.tool_name == "multi_step_lookup":
            return self._invoke_nested(turn.tool_args or {})
        raise ToolExecutionError(f"unknown tool: {turn.tool_name!r}")

    @staticmethod
    def _invoke_nested(args: dict[str, Any]) -> str:
        """The "nested" scenario's compound tool: two real nested child spans.

        Distinct from every other scenario's flat one-span-per-tool-call
        shape -- these two spans are children of this very execute_tool
        span, which is itself a "haystack.component.run" span created by
        Haystack's OpenTelemetry integration.
        """

        tracer = trace.get_tracer("agent_demo.tools")
        with tracer.start_as_current_span("tool.multi_step_lookup.lookup_step") as lookup_span:
            topic = args.get("topic", "")
            fact = _lookup({"topic": topic})
            lookup_span.set_attribute("agent_demo.topic", topic)
        with tracer.start_as_current_span("tool.multi_step_lookup.calculate_step") as calc_span:
            expression = args.get("expression", "0")
            value = _calculate({"expression": expression})
            calc_span.set_attribute("agent_demo.expression", expression)
        return f"{fact} (ref #{int(value)})"


ACCEPT_THRESHOLD = 0.6


@component
class EvaluateAnswer:
    """Score the current final answer and decide whether it's acceptable."""

    @component.output_types(state=ConversationState, evaluation=AnswerEvaluation)
    def run(self, state: ConversationState) -> dict[str, Any]:
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.execution_id)
        score = state.quality_score if state.quality_score is not None else 0.0
        accepted = score >= ACCEPT_THRESHOLD
        span.set_attribute("agent_demo.quality_score", score)
        span.set_attribute("agent_demo.accepted", accepted)
        if not accepted:
            state.correction_count += 1
        evaluation = AnswerEvaluation(
            quality_score=score,
            accepted=accepted,
            reason="quality_at_or_above_threshold" if accepted else "quality_below_threshold",
        )
        return {"state": state, "evaluation": evaluation}


# ---------------------------------------------------------------------------
# ONE builder, reused for every scenario and both modes.
# ---------------------------------------------------------------------------


@dataclass
class WorkflowComponents:
    interpreter: InterpretQuery
    generator: GenerateModelTurn
    executor: ExecuteTool
    evaluator: EvaluateAnswer


@dataclass
class Workflow:
    provider: FakeChatProvider
    components: WorkflowComponents
    turn_pipeline: Pipeline
    evaluation_pipeline: Pipeline


def build_workflow(provider: FakeChatProvider) -> Workflow:
    """THE single Haystack pipeline/agent builder. Mode is never a parameter here.

    Two small Pipelines are built from one shared component set (the same
    style the sibling project's ``workflow/pipeline.py`` uses for its
    research-assessment vs profile phases): a ``turn_pipeline``
    (generate_turn -> optionally execute_tool) run once per conversation
    turn, and a single-component ``evaluation_pipeline`` run once per
    answer attempt. Splitting into two rather than one gives ``ExecuteTool``
    a real Haystack/OTel component span only exactly when it actually runs.
    """

    components = WorkflowComponents(
        interpreter=InterpretQuery(),
        generator=GenerateModelTurn(provider),
        executor=ExecuteTool(),
        evaluator=EvaluateAnswer(),
    )

    turn_pipeline = Pipeline(max_runs_per_component=2)
    turn_pipeline.add_component("generate_turn", components.generator)
    turn_pipeline.add_component("execute_tool", components.executor)
    turn_pipeline.connect("generate_turn.turn", "execute_tool.turn")
    turn_pipeline.connect("generate_turn.state", "execute_tool.state")

    evaluation_pipeline = Pipeline(max_runs_per_component=2)
    evaluation_pipeline.add_component("evaluate_answer", components.evaluator)

    return Workflow(
        provider=provider,
        components=components,
        turn_pipeline=turn_pipeline,
        evaluation_pipeline=evaluation_pipeline,
    )


# ---------------------------------------------------------------------------
# Orchestration: one bounded, scenario-driven loop shared by every mode.
# ---------------------------------------------------------------------------

MAX_TURNS = 6
MAX_CONSECUTIVE_TOOL_FAILURES = 2
MAX_CORRECTIONS = 3


def run_workflow(workflow: Workflow, *, scenario: str, execution_id: str) -> WorkflowResult:
    """Run one execution of the shared workflow end to end.

    Mode-blind by construction: this function's signature has no ``mode``
    parameter, so it is structurally impossible for it to make the pipeline
    behave differently by mode. Only ``scenario`` (via the provider's
    deterministic script) can change the physical shape.
    """

    scripted = workflow.provider.scenario(scenario)
    tracer = get_tracer()
    # Attach witdem.execution_id to OTel baggage for the *entire* execution before
    # the root span even starts, so ExecutionIdSpanProcessor.on_start copies it
    # onto the root span and every descendant span created anywhere below
    # (generate_turn / execute_tool / evaluate_answer, and the nested tool
    # child spans for the "nested" scenario) -- not just the outermost one.
    baggage_token = bind_execution_id(execution_id)
    try:
        with tracer.start_as_current_span("agent_demo.execution") as root_span:
            root_span.set_attribute("witdem.execution_id", execution_id)
            root_span.set_attribute("agent_demo.scenario", scenario)

            state: ConversationState = workflow.components.interpreter.run(
                question=scripted.question, scenario=scenario, execution_id=execution_id
            )["state"]

            tool_failure_streak = 0
            for _ in range(MAX_TURNS):
                outputs = workflow.turn_pipeline.run(
                    {"generate_turn": {"state": state}},
                    include_outputs_from={"generate_turn", "execute_tool"},
                )
                state = outputs["generate_turn"]["state"]
                if "execute_tool" in outputs:
                    state = outputs["execute_tool"]["state"]
                    tool_result: ToolResult = outputs["execute_tool"]["tool_result"]
                    if tool_result.ok:
                        if tool_failure_streak > 0:
                            state.used_fallback = True
                        tool_failure_streak = 0
                    else:
                        tool_failure_streak += 1
                        if tool_failure_streak >= MAX_CONSECUTIVE_TOOL_FAILURES:
                            result = _terminal_result(state)
                            _annotate_root_span(root_span, result)
                            return result
                if state.final_answer is not None:
                    break
            else:
                result = _terminal_result(state)  # exhausted MAX_TURNS without ever reaching an answer
                _annotate_root_span(root_span, result)
                return result

            evaluation: AnswerEvaluation | None = None
            for attempt in range(MAX_CORRECTIONS + 1):
                eval_outputs = workflow.evaluation_pipeline.run(
                    {"evaluate_answer": {"state": state}},
                    include_outputs_from={"evaluate_answer"},
                )
                state = eval_outputs["evaluate_answer"]["state"]
                evaluation = eval_outputs["evaluate_answer"]["evaluation"]
                if evaluation.accepted:
                    break
                if attempt == MAX_CORRECTIONS:
                    result = _terminal_result(state)  # exhausted corrections without an accepted answer
                    _annotate_root_span(root_span, result)
                    return result
                state.final_answer = None
                outputs = workflow.turn_pipeline.run(
                    {"generate_turn": {"state": state}},
                    include_outputs_from={"generate_turn"},
                )
                state = outputs["generate_turn"]["state"]

            if evaluation is None or not evaluation.accepted:
                # pragma: no cover - defensive, unreachable given the loop above
                result = _terminal_result(state)
                _annotate_root_span(root_span, result)
                return result

            result = WorkflowResult(
                execution_id=execution_id,
                scenario=scenario,
                status="success",
                route=_route(state),
                final_answer=state.final_answer,
                quality_score=state.quality_score,
                tool_call_count=state.tool_call_count,
                correction_count=state.correction_count,
                used_fallback=state.used_fallback,
                turn_count=state.turn_count,
            )
            _annotate_root_span(root_span, result)
            return result
    finally:
        detach_execution_id(baggage_token)


def _terminal_result(state: ConversationState) -> WorkflowResult:
    """A terminal failure always reports no accepted answer, whatever draft existed."""

    return WorkflowResult(
        execution_id=state.execution_id,
        scenario=state.scenario,
        status="failed",
        route="terminal_failure",
        final_answer=None,
        quality_score=state.quality_score,
        tool_call_count=state.tool_call_count,
        correction_count=state.correction_count,
        used_fallback=state.used_fallback,
        turn_count=state.turn_count,
    )


def _route(state: ConversationState) -> str:
    if state.used_fallback:
        return "fallback_recovery"
    if state.tool_call_count > 0:
        return "tool_assisted"
    return "direct_answer"


def _annotate_root_span(root_span: trace.Span, result: WorkflowResult) -> None:
    root_span.set_attribute("agent_demo.status", result.status)
    root_span.set_attribute("agent_demo.route", result.route)
    root_span.set_attribute("agent_demo.tool_call_count", result.tool_call_count)
    root_span.set_attribute("agent_demo.correction_count", result.correction_count)
