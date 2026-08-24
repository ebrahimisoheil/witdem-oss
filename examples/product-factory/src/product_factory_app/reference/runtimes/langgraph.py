"""LangGraph state graph with the shared workflow as its business node."""

from __future__ import annotations

from typing import Any, TypedDict, cast

from product_factory_app.reference.contracts import RuntimeCase, RuntimeOutput
from product_factory_app.reference.gateways import ModelGateway
from product_factory_app.reference.runtimes.base import (
    StageObserver,
    WorkflowState,
    critique_stage,
    execute_shared_workflow,
    finish_workflow,
    needs_targeted_research,
    profile_extraction_stage,
    profile_validation_stage,
    qualification_stage,
    research_stage,
    targeted_research_stage,
    workflow_state,
)


class _State(TypedDict, total=False):
    workflow: WorkflowState
    result: RuntimeOutput


class LangGraphRuntime:
    runtime_id = "langgraph"

    async def execute(
        self,
        case: RuntimeCase,
        *,
        profile: str,
        gateway: ModelGateway,
        observe: StageObserver,
        witdem: Any | None = None,
    ) -> RuntimeOutput:
        try:
            from langchain_core.runnables import RunnableConfig
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            return await execute_shared_workflow(
                self.runtime_id, case, profile=profile, gateway=gateway, observe=observe
            )

        async def research(state: _State) -> _State:
            await research_stage(state["workflow"])
            return state

        async def critique(state: _State) -> _State:
            await critique_stage(state["workflow"])
            return state

        def route_after_critique(state: _State) -> str:
            return "targeted_research" if needs_targeted_research(state["workflow"]) else "profile_extraction"

        async def targeted_research(state: _State) -> _State:
            await targeted_research_stage(state["workflow"])
            return state

        async def critique_after_research(state: _State) -> _State:
            await critique_stage(state["workflow"], after_research=True)
            return state

        async def profile_extraction(state: _State) -> _State:
            await profile_extraction_stage(state["workflow"])
            return state

        async def profile_validation(state: _State) -> _State:
            await profile_validation_stage(state["workflow"])
            return state

        async def qualification_analysis(state: _State) -> _State:
            await qualification_stage(state["workflow"])
            return state

        async def complete(state: _State) -> _State:
            state["result"] = finish_workflow(state["workflow"])
            return state

        graph = StateGraph(_State)
        graph.add_node("research", cast("Any", research))
        graph.add_node("evidence_critique", cast("Any", critique))
        graph.add_node("targeted_research", cast("Any", targeted_research))
        graph.add_node("evidence_critique_after_research", cast("Any", critique_after_research))
        graph.add_node("profile_extraction", cast("Any", profile_extraction))
        graph.add_node("profile_validation", cast("Any", profile_validation))
        graph.add_node("qualification_analysis", cast("Any", qualification_analysis))
        graph.add_node("goal_assessment", cast("Any", complete))
        graph.add_edge(START, "research")
        graph.add_edge("research", "evidence_critique")
        graph.add_conditional_edges(
            "evidence_critique",
            cast("Any", route_after_critique),
            {
                "targeted_research": "targeted_research",
                "profile_extraction": "profile_extraction",
            },
        )
        graph.add_edge("targeted_research", "evidence_critique_after_research")
        graph.add_edge("evidence_critique_after_research", "profile_extraction")
        graph.add_edge("profile_extraction", "profile_validation")
        graph.add_edge("profile_validation", "qualification_analysis")
        graph.add_edge("qualification_analysis", "goal_assessment")
        graph.add_edge("goal_assessment", END)
        config: dict[str, Any] = {}
        if witdem is not None:
            from witdem_sdk.integrations.langgraph import WitdemLangGraphCallback

            config["callbacks"] = [WitdemLangGraphCallback(witdem, capture_content=False)]
        initial = workflow_state(self.runtime_id, case, profile=profile, gateway=gateway, observe=observe)
        final: dict[str, Any] = await graph.compile().ainvoke(
            {"workflow": initial},
            config=cast("RunnableConfig", config),
        )
        return final["result"]
