"""LangGraph workload shared by the telemetry entrypoints."""

from __future__ import annotations

from typing import TypedDict


class State(TypedDict):
    question: str
    answer: str


def build_graph():
    from langgraph.graph import END, StateGraph

    def answer(state: State) -> State:
        return {**state, "answer": f"Answered: {state['question']}"}

    graph = StateGraph(State)
    graph.add_node("answer", answer)
    graph.set_entry_point("answer")
    graph.add_edge("answer", END)
    return graph.compile()


def run(callbacks=None) -> str:
    result = build_graph().invoke(
        {"question": "What is observability?", "answer": ""},
        config={"callbacks": callbacks or []},
    )
    return str(result["answer"])
