"""LangChain runnable composition."""

from __future__ import annotations

from typing import Any, cast

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


class LangChainRuntime:
    runtime_id = "langchain"

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
            from langchain_core.runnables import RunnableBranch, RunnableConfig, RunnableLambda
        except ImportError:
            return await execute_shared_workflow(
                self.runtime_id, case, profile=profile, gateway=gateway, observe=observe
            )

        async def critique(state: WorkflowState) -> WorkflowState:
            return await critique_stage(state)

        async def critique_after_research(state: WorkflowState) -> WorkflowState:
            return await critique_stage(state, after_research=True)

        async def unchanged(state: WorkflowState) -> WorkflowState:
            return state

        config: dict[str, Any] = {"run_name": "product_factory"}
        if witdem is not None:
            from witdem_sdk.integrations.langchain import WitdemCallbackHandler

            config["callbacks"] = [WitdemCallbackHandler(witdem, capture_content=False)]
        targeted_path = (
            RunnableLambda(targeted_research_stage).with_config(run_name="Targeted research")
            | RunnableLambda(critique_after_research).with_config(run_name="Evidence critique after research")
        )
        research_branch = RunnableBranch(
            (needs_targeted_research, targeted_path),
            RunnableLambda(unchanged).with_config(run_name="Targeted research skipped"),
        )
        runnable = (
            RunnableLambda(research_stage).with_config(run_name="Research")
            | RunnableLambda(critique).with_config(run_name="Evidence critique")
            | research_branch
            | RunnableLambda(profile_extraction_stage).with_config(run_name="Profile extraction")
            | RunnableLambda(profile_validation_stage).with_config(run_name="Profile validation")
            | RunnableLambda(qualification_stage).with_config(run_name="Qualification analysis")
            | RunnableLambda(finish_workflow).with_config(run_name="Goal assessment")
        ).with_config(cast("RunnableConfig", config))
        initial = workflow_state(self.runtime_id, case, profile=profile, gateway=gateway, observe=observe)
        return await runnable.ainvoke(initial)
