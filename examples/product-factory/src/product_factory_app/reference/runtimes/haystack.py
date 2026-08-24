"""Haystack runtime boundary."""

from __future__ import annotations

import asyncio
from typing import Any

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


class HaystackRuntime:
    runtime_id = "haystack"

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
            from haystack import Pipeline, component
        except ImportError:
            return await execute_shared_workflow(
                self.runtime_id,
                case,
                profile=profile,
                gateway=gateway,
                observe=observe,
            )

        handle = None
        if witdem is not None:
            from witdem_sdk.integrations.haystack import enable_haystack

            handle = enable_haystack(witdem, capture_content=False)

        @component
        class Research:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                return {"state": asyncio.run(research_stage(state))}

        @component
        class EvidenceCritique:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                return {"state": asyncio.run(critique_stage(state))}

        @component
        class OptionalResearchRecovery:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                async def recover() -> WorkflowState:
                    if needs_targeted_research(state):
                        await targeted_research_stage(state)
                        await critique_stage(state, after_research=True)
                    return state

                return {"state": asyncio.run(recover())}

        @component
        class ProfileExtraction:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                return {"state": asyncio.run(profile_extraction_stage(state))}

        @component
        class ProfileValidation:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                return {"state": asyncio.run(profile_validation_stage(state))}

        @component
        class QualificationAnalysis:
            @component.output_types(state=WorkflowState)
            def run(self, state: WorkflowState) -> dict[str, WorkflowState]:
                return {"state": asyncio.run(qualification_stage(state))}

        @component
        class GoalAssessment:
            @component.output_types(result=RuntimeOutput)
            def run(self, state: WorkflowState) -> dict[str, RuntimeOutput]:
                return {"result": finish_workflow(state)}

        pipeline = Pipeline()
        stages = (
            ("research", Research()),
            ("evidence_critique", EvidenceCritique()),
            ("optional_research_recovery", OptionalResearchRecovery()),
            ("profile_extraction", ProfileExtraction()),
            ("profile_validation", ProfileValidation()),
            ("qualification_analysis", QualificationAnalysis()),
            ("goal_assessment", GoalAssessment()),
        )
        for name, stage in stages:
            pipeline.add_component(name, stage)
        for index in range(len(stages) - 1):
            source, target = stages[index][0], stages[index + 1][0]
            pipeline.connect(f"{source}.state", f"{target}.state")
        initial = workflow_state(self.runtime_id, case, profile=profile, gateway=gateway, observe=observe)
        try:
            pipeline_result = await asyncio.to_thread(pipeline.run, {"research": {"state": initial}})
            return pipeline_result["goal_assessment"]["result"]
        finally:
            if handle is not None:
                handle.disable()
