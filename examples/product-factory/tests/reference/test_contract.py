from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from product_factory_app.reference.cases import case_ids, load_case, runtime_case
from product_factory_app.reference.contracts import EvidenceCritique, OutcomeStatus, ProfileArtifact
from product_factory_app.reference.gateways import DeterministicGateway, LiveGateway, _json_object
from product_factory_app.reference.matrix import all_cells, cross_runtime_cells
from product_factory_app.reference.policy import decide
from product_factory_app.reference.runner import run_case
from product_factory_app.reference.runtimes.anthropic_messages import tool_result_block
from product_factory_app.reference.runtimes.base import execute_shared_workflow


def test_four_controlled_cases_validate_and_hide_ground_truth() -> None:
    assert set(case_ids()) == {
        "clear-qualification",
        "recoverable-evidence-gap",
        "borderline-escalation",
        "clear-non-qualification",
    }
    for case_id in case_ids():
        case = load_case(case_id)
        visible = runtime_case(case).model_dump()
        assert "ground_truth" not in visible
        assert "expected_status" not in visible


def test_runtime_implementations_cannot_import_evaluator_truth() -> None:
    runtime_root = Path(__file__).resolve().parents[2] / "src" / "product_factory_app" / "reference" / "runtimes"
    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_root.glob("*.py"))
    assert "GroundTruth" not in source
    assert "load_case" not in source
    assert "expected_status" not in source


def test_framework_runtimes_physically_orchestrate_business_stages() -> None:
    runtime_root = Path(__file__).resolve().parents[2] / "src" / "product_factory_app" / "reference" / "runtimes"
    langchain = (runtime_root / "langchain.py").read_text(encoding="utf-8")
    langgraph = (runtime_root / "langgraph.py").read_text(encoding="utf-8")
    haystack = (runtime_root / "haystack.py").read_text(encoding="utf-8")

    assert "RunnableBranch" in langchain and "profile_validation_stage" in langchain
    assert "add_conditional_edges" in langgraph and 'add_node("targeted_research"' in langgraph
    assert 'pipeline.add_component' in haystack and '"qualification_analysis"' in haystack


def test_matrix_has_20_baseline_and_44_unique_total_cells() -> None:
    baseline = cross_runtime_cells()
    full = all_cells()
    assert len(baseline) == 20
    assert len(full) == 44
    assert len({cell.cell_id for cell in full}) == 44


@pytest.mark.parametrize("runtime", ["langchain", "langgraph", "haystack", "openai_agents", "anthropic_messages"])
@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("clear-qualification", OutcomeStatus.ACCEPTED),
        ("recoverable-evidence-gap", OutcomeStatus.ACCEPTED),
        ("borderline-escalation", OutcomeStatus.ESCALATED),
        ("clear-non-qualification", OutcomeStatus.REJECTED),
    ],
)
def test_deterministic_cross_runtime_contract(runtime: str, case_id: str, expected: OutcomeStatus) -> None:
    result = asyncio.run(run_case(case_id, runtime, telemetry=False))
    assert result.decision.observed_status is expected
    assert result.goal.product_goal_achieved is True
    assert result.runtime.topology[-2:] == ["deterministic_decision", "deterministic_goal_assessment"]


def test_recoverable_case_requires_targeted_research() -> None:
    result = asyncio.run(run_case("recoverable-evidence-gap", "langgraph", telemetry=False))
    assert result.runtime.targeted_research_performed is True
    assert result.goal.required_path_observed is True


def test_live_critique_normalizes_structured_provider_items() -> None:
    critique = EvidenceCritique.model_validate(
        {
            "missing_dimensions": [{"dimension": "market_scale", "severity": "high"}],
            "conflicts": [{"dimension": "data_fragmentation", "severity": "medium"}],
            "research_queries": [{"query": "Find audited revenue evidence"}],
        }
    )

    assert critique.missing_dimensions == ["market_scale"]
    assert critique.conflicts == ["data_fragmentation"]
    assert critique.research_queries == ["Find audited revenue evidence"]


def test_live_profile_normalizes_structured_scores_and_ignores_model_completeness() -> None:
    artifact = ProfileArtifact.model_validate(
        {
            "company_name": "Example",
            "summary": "Controlled profile",
            "dimensions": {
                "catalog_complexity": {"proposed_strength": 0.91, "reason": "evidence"},
                "market_scale": {"strength": 0.88},
                "unsupported": None,
            },
            "evidence_ids": [{"id": "claim-1"}],
            "completeness": {"supported_dimensions": 2},
        }
    )

    assert artifact.dimensions == {"catalog_complexity": 0.91, "market_scale": 0.88}
    assert artifact.evidence_ids == ["claim-1"]
    assert artifact.completeness == 0


def test_json_parser_finds_object_after_provider_prose() -> None:
    assert _json_object('analysis first\n```json\n{"value": 1}\n```') == {"value": 1}


def test_research_ranking_cannot_delete_controlled_evidence() -> None:
    class OmittingGateway(DeterministicGateway):
        async def research(self, evidence, *, profile):  # type: ignore[no-untyped-def]
            await super().research(evidence, profile=profile)
            return [evidence[0].id]

    case = runtime_case(load_case("borderline-escalation"))
    output = asyncio.run(
        execute_shared_workflow(
            "test",
            case,
            profile="mixed-v1",
            gateway=OmittingGateway(),
            observe=lambda _: None,
        )
    )

    assert {item.id for item in output.evidence_used} == {"be-1", "be-2", "be-3", "be-4"}


def test_application_repairs_malformed_advisory_model_outputs() -> None:
    class MalformedGateway(DeterministicGateway):
        async def research(self, evidence, *, profile):  # type: ignore[no-untyped-def]
            self._model(profile, "research")
            raise ValueError("malformed research")

        async def critique(self, evidence, *, profile):  # type: ignore[no-untyped-def]
            self._model(profile, "evidence_critic")
            raise ValueError("malformed critique")

        async def extract(self, company, evidence, *, profile):  # type: ignore[no-untyped-def]
            self._model(profile, "profile_extractor")
            raise ValueError("malformed profile")

        async def qualify(self, profile_artifact, evidence, *, profile):  # type: ignore[no-untyped-def]
            self._model(profile, "qualification_analyst")
            raise ValueError("malformed scores")

    case_definition = load_case("clear-non-qualification")
    output = asyncio.run(
        execute_shared_workflow(
            "test",
            runtime_case(case_definition),
            profile="mixed-v1",
            gateway=MalformedGateway(),
            observe=lambda _: None,
        )
    )

    assert output.terminal is True
    assert output.profile is not None
    assert decide(case_definition, output).observed_status is OutcomeStatus.REJECTED
    assert {"research_output_repair", "evidence_critique_repair", "qualification_analysis_repair"} <= set(
        output.topology
    )


def test_goal_attribute_contract_is_complete() -> None:
    result = asyncio.run(run_case("clear-non-qualification", "haystack", telemetry=False))
    assert set(result.goal_attributes(0.8)) >= {
        "contract_version",
        "case_id",
        "runtime_id",
        "model_profile",
        "expected_status",
        "observed_status",
        "decision_correct",
        "product_goal_achieved",
        "artifact_valid",
        "decision_evidence_sufficient",
        "required_path_observed",
        "closest_blocker",
        "threshold",
        "threshold_margin",
    }


def test_anthropic_tool_result_rejects_fabricated_ids() -> None:
    assert tool_result_block("toolu_real", "ok")["tool_use_id"] == "toolu_real"
    with pytest.raises(ValueError, match="provider-issued"):
        tool_result_block("demo-0", "bad")


def test_live_gateway_retries_transient_but_not_permanent_failures(monkeypatch) -> None:
    gateway = LiveGateway()
    calls = 0

    async def no_sleep(_: float) -> None:
        return None

    async def transient_then_success(url: str, **kwargs) -> httpx.Response:
        nonlocal calls
        del kwargs
        calls += 1
        status = 503 if calls == 1 else 200
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr("product_factory_app.reference.gateways.asyncio.sleep", no_sleep)
    monkeypatch.setattr(gateway, "_post_once", transient_then_success)
    response = asyncio.run(gateway._post("https://provider.invalid"))
    assert response.status_code == 200
    assert calls == 2

    calls = 0

    async def permanent(url: str, **kwargs) -> httpx.Response:
        nonlocal calls
        del kwargs
        calls += 1
        return httpx.Response(400, request=httpx.Request("POST", url))

    monkeypatch.setattr(gateway, "_post_once", permanent)
    response = asyncio.run(gateway._post("https://provider.invalid"))
    assert response.status_code == 400
    assert calls == 1
    asyncio.run(gateway.aclose())
