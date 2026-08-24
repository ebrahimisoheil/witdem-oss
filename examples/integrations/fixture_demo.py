"""Run one compact cross-runtime adapter fixture."""

from __future__ import annotations

from witdem.integrations.adapters.langgraph import LangGraphAdapter


def main() -> None:
    graph = LangGraphAdapter().normalize(
        [
            {
                "run_id": "root",
                "trace_id": "fixture-trace",
                "event": "on_chain_end",
                "name": "route",
                "metadata": {"langgraph_node": "route", "langgraph_step": 1},
            },
            {
                "run_id": "tool",
                "trace_id": "fixture-trace",
                "parent_ids": ["root"],
                "event": "on_tool_end",
                "name": "search",
                "metadata": {"langgraph_node": "search", "langgraph_step": 2},
            },
        ]
    )
    print(
        {
            "execution": graph.execution.execution_id,
            "operations": [(operation.kind, operation.name) for operation in graph.operations],
            "links": [(link.relation, link.source_id, link.target_id) for link in graph.links],
        }
    )


if __name__ == "__main__":
    main()
