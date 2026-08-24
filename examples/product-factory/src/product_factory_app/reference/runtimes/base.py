"""Runtime protocol and framework-neutral business-stage implementation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from product_factory_app.reference.cases import ControlledEvidenceTool
from product_factory_app.reference.contracts import (
    EvidenceCritique,
    EvidenceItem,
    ProfileArtifact,
    RuntimeCase,
    RuntimeOutput,
)
from product_factory_app.reference.gateways import REQUIRED_DIMENSIONS, ModelGateway

StageObserver = Callable[[str], None]


def _application_completeness(dimensions: dict[str, float]) -> float:
    """Compute completeness from the contract; model self-assessments are advisory only."""

    return len(set(dimensions).intersection(REQUIRED_DIMENSIONS)) / len(REQUIRED_DIMENSIONS)


def _application_critique(evidence: list[Any]) -> EvidenceCritique:
    by_dimension = {
        dimension: [item for item in evidence if item.dimension == dimension]
        for dimension in REQUIRED_DIMENSIONS
    }
    missing = [dimension for dimension, items in by_dimension.items() if not items]
    weak = [
        dimension
        for dimension, items in by_dimension.items()
        if items and max(item.reliability for item in items) < 0.8
    ]
    return EvidenceCritique(
        missing_dimensions=missing,
        conflicts=sorted({item.dimension for item in evidence if item.conflicting}),
        research_queries=list(dict.fromkeys([*missing, *weak])),
    )


def _application_profile(case: RuntimeCase, evidence: list[Any]) -> ProfileArtifact:
    dimensions: dict[str, float] = {}
    for dimension in REQUIRED_DIMENSIONS:
        candidates = [item for item in evidence if item.dimension == dimension]
        if candidates:
            dimensions[dimension] = max(candidates, key=lambda item: item.reliability).score_signal
    return ProfileArtifact(
        company_name=case.company.name,
        summary=f"Validated controlled-evidence profile for {case.company.name}",
        dimensions=dimensions,
        evidence_ids=[item.id for item in evidence],
        completeness=_application_completeness(dimensions),
    )


class ProductFactoryRuntime(Protocol):
    runtime_id: str

    async def execute(
        self,
        case: RuntimeCase,
        *,
        profile: str,
        gateway: ModelGateway,
        observe: StageObserver,
        witdem: Any | None = None,
    ) -> RuntimeOutput: ...


@dataclass
class WorkflowState:
    """Agent-visible mutable state shared by every physical orchestrator."""

    runtime_id: str
    case: RuntimeCase
    profile: str
    gateway: ModelGateway
    observe: StageObserver
    evidence: list[EvidenceItem] = field(default_factory=list)
    critique: EvidenceCritique = field(default_factory=EvidenceCritique)
    artifact: ProfileArtifact | None = None
    targeted_research_performed: bool = False
    topology: list[str] = field(default_factory=list)
    tool: ControlledEvidenceTool = field(init=False)

    def __post_init__(self) -> None:
        self.tool = ControlledEvidenceTool(self.case)

    def stage(self, name: str) -> None:
        self.topology.append(name)
        self.observe(name)


def workflow_state(
    runtime_id: str,
    case: RuntimeCase,
    *,
    profile: str,
    gateway: ModelGateway,
    observe: StageObserver,
) -> WorkflowState:
    return WorkflowState(runtime_id, case, profile, gateway, observe)


async def research_stage(state: WorkflowState) -> WorkflowState:
    state.stage("research")
    state.evidence = state.tool.initial()
    # The checked-in evidence pack is already the controlled, case-specific
    # corpus. The research model may rank or cite it, but it must not be able to
    # silently delete evidence before the deterministic policy sees the case.
    try:
        await state.gateway.research(state.evidence, profile=state.profile)
    except (ValueError, TypeError):
        state.stage("research_output_repair")
    return state


async def critique_stage(state: WorkflowState, *, after_research: bool = False) -> WorkflowState:
    state.stage("evidence_critique_after_research" if after_research else "evidence_critique")
    try:
        critique = await state.gateway.critique(state.evidence, profile=state.profile)
    except (ValueError, TypeError):
        state.stage("evidence_critique_after_research_repair" if after_research else "evidence_critique_repair")
        critique = _application_critique(state.evidence)
    by_dimension = {
        dimension: [item for item in state.evidence if item.dimension == dimension] for dimension in REQUIRED_DIMENSIONS
    }
    required_queries = [
        dimension
        for dimension, items in by_dimension.items()
        if not items or max(item.reliability for item in items) < 0.8
    ]
    critique.research_queries = list(dict.fromkeys([*critique.research_queries, *required_queries]))
    state.critique = critique
    return state


def needs_targeted_research(state: WorkflowState) -> bool:
    return bool(state.critique.research_queries)


async def targeted_research_stage(state: WorkflowState) -> WorkflowState:
    if not needs_targeted_research(state):
        return state
    state.stage("targeted_research")
    for query in state.critique.research_queries:
        additions = state.tool.targeted(query)
        state.targeted_research_performed = state.targeted_research_performed or bool(additions)
        state.evidence.extend(additions)
    return state


async def profile_extraction_stage(state: WorkflowState) -> WorkflowState:
    state.stage("profile_extraction")
    try:
        artifact = await state.gateway.extract(state.case.company, state.evidence, profile=state.profile)
    except (ValueError, TypeError):
        state.stage("profile_repair")
        artifact = _application_profile(state.case, state.evidence)
    artifact.completeness = _application_completeness(artifact.dimensions)
    state.artifact = artifact
    return state


async def profile_validation_stage(state: WorkflowState) -> WorkflowState:
    state.stage("profile_validation")
    if state.artifact is None:
        raise RuntimeError("profile extraction must run before validation")
    artifact = state.artifact
    if (
        set(artifact.dimensions) != set(REQUIRED_DIMENSIONS)
        or artifact.completeness < state.case.policy.minimum_profile_completeness
    ):
        state.stage("profile_repair")
        try:
            artifact = await state.gateway.extract(state.case.company, state.evidence, profile=state.profile)
        except (ValueError, TypeError):
            artifact = _application_profile(state.case, state.evidence)
        artifact.completeness = _application_completeness(artifact.dimensions)

    # Application-owned repair fills only contract dimensions from controlled evidence.
    for dimension in REQUIRED_DIMENSIONS:
        if dimension not in artifact.dimensions:
            candidates = [item for item in state.evidence if item.dimension == dimension]
            if candidates:
                artifact.dimensions[dimension] = max(candidates, key=lambda item: item.reliability).score_signal
    artifact.dimensions = {
        name: artifact.dimensions[name] for name in REQUIRED_DIMENSIONS if name in artifact.dimensions
    }
    artifact.completeness = _application_completeness(artifact.dimensions)
    state.artifact = artifact
    return state


async def qualification_stage(state: WorkflowState) -> WorkflowState:
    state.stage("qualification_analysis")
    if state.artifact is None:
        raise RuntimeError("profile validation must run before qualification")
    try:
        proposed_dimensions = await state.gateway.qualify(
            state.artifact,
            state.evidence,
            profile=state.profile,
        )
    except (ValueError, TypeError):
        state.stage("qualification_analysis_repair")
        proposed_dimensions = dict(state.artifact.dimensions)
    state.artifact.dimensions = {
        name: proposed_dimensions.get(name, state.artifact.dimensions[name])
        for name in REQUIRED_DIMENSIONS
        if name in proposed_dimensions or name in state.artifact.dimensions
    }
    state.artifact.completeness = _application_completeness(state.artifact.dimensions)
    return state


def finish_workflow(state: WorkflowState) -> RuntimeOutput:
    if state.artifact is None:
        raise RuntimeError("workflow reached completion without a profile artifact")
    state.stage("deterministic_decision")
    state.stage("deterministic_goal_assessment")
    return RuntimeOutput(
        runtime_id=state.runtime_id,
        model_profile=state.profile,
        profile=state.artifact,
        critique=state.critique,
        targeted_research_performed=state.targeted_research_performed,
        evidence_used=state.evidence,
        actual_models=dict(state.gateway.actual_models),
        usage=dict(state.gateway.usage),
        topology=state.topology,
    )


async def execute_shared_workflow(
    runtime_id: str,
    case: RuntimeCase,
    *,
    profile: str,
    gateway: ModelGateway,
    observe: StageObserver,
) -> RuntimeOutput:
    """Execute the stage contract directly for native-provider runtimes."""

    state = workflow_state(runtime_id, case, profile=profile, gateway=gateway, observe=observe)
    await research_stage(state)
    await critique_stage(state)
    if needs_targeted_research(state):
        await targeted_research_stage(state)
        await critique_stage(state, after_research=True)
    await profile_extraction_stage(state)
    await profile_validation_stage(state)
    await qualification_stage(state)
    return finish_workflow(state)


class DirectRuntime:
    runtime_id = "direct"

    async def execute(
        self,
        case: RuntimeCase,
        *,
        profile: str,
        gateway: ModelGateway,
        observe: StageObserver,
        witdem: Any | None = None,
    ) -> RuntimeOutput:
        del witdem
        return await execute_shared_workflow(self.runtime_id, case, profile=profile, gateway=gateway, observe=observe)
