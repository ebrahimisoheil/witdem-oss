"""One-command langgraph/state_graph telemetry example."""

from __future__ import annotations

from typing import TypedDict

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "langgraph/state_graph"


def telemetry_smoke() -> dict[str, int]:
    """Create a deterministic execution with one child operation without a network call."""
    provider = TracerProvider(resource=Resource.create({"service.name": f"witdem-example-{EXAMPLE_NAME}"}))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(EXAMPLE_NAME)
    try:
        with tracer.start_as_current_span("witdem.execution") as execution:
            execution.set_attribute("witdem.example", EXAMPLE_NAME)
            with tracer.start_as_current_span("example.operation") as operation:
                operation.set_attribute("witdem.operation.kind", "tool")
    finally:
        provider.shutdown()
    spans = exporter.get_finished_spans()
    return {"executions": 1, "operations": len(spans) - 1}


class State(TypedDict):
    question: str
    route: str
    answer: str


def main() -> None:
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    local_cost = {"cost_usd": 0.0, "cost_source": "local_runtime"}
    with configure() as witdem:  # noqa: SIM117
        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):

            def node_a(state: State) -> State:
                with witdem.operation("node.a", attributes=local_cost):
                    route = "b" if "billing" in state["question"].lower() else "c"
                    witdem.event("route.selected", {"route": route})
                    return {**state, "route": route}

            def node_b(state: State) -> State:
                with witdem.operation("node.b", attributes=local_cost):
                    return {**state, "answer": "Billing specialist path selected."}

            def node_c(state: State) -> State:
                with witdem.operation("node.c", attributes=local_cost):
                    return {**state, "answer": "General support path selected."}

            def final_node(state: State) -> State:
                with witdem.operation("node.final", attributes=local_cost):
                    return state

            graph = StateGraph(State)
            graph.add_node("a", node_a)
            graph.add_node("b", node_b)
            graph.add_node("c", node_c)
            graph.add_node("final", final_node)
            graph.set_entry_point("a")
            graph.add_conditional_edges("a", lambda state: state["route"], {"b": "b", "c": "c"})
            graph.add_edge("b", "final")
            graph.add_edge("c", "final")
            graph.add_edge("final", END)
            result = graph.compile().invoke({"question": "billing question", "route": "", "answer": ""})
            answer = result["answer"]
            witdem.report(
                result="completed" if answer else "unresolved",
                result_valid=bool(answer),
                product_goal_achieved=bool(answer),
                metrics={"answer_characters": len(str(answer))},
            )
            print(result["answer"])


if __name__ == "__main__":
    main()
