from __future__ import annotations

from copy import deepcopy

from product_factory_app.reference.cases import load_case
from product_factory_app.reference.contracts import EvidenceCritique, OutcomeStatus, ProfileArtifact, RuntimeOutput
from product_factory_app.reference.matrix import preflight_live
from product_factory_app.reference.policy import assess_goal, decide


def _output(score: float, *, conflict: bool = False) -> RuntimeOutput:
    case = load_case("clear-qualification")
    evidence = deepcopy(case.pass_one)
    for item in evidence:
        item.score_signal = score
    if conflict:
        evidence[0].conflicting = True
    return RuntimeOutput(
        runtime_id="fake",
        model_profile="mixed-v1",
        profile=ProfileArtifact(
            company_name=case.company.name,
            summary="valid",
            dimensions={name: score for name in case.ground_truth.expected_dimensions},
            evidence_ids=list(case.ground_truth.required_claim_ids),
            completeness=1,
        ),
        critique=EvidenceCritique(conflicts=["data_fragmentation"] if conflict else []),
        evidence_used=evidence,
    )


def test_threshold_and_escalation_boundaries() -> None:
    case = load_case("clear-qualification")
    assert decide(case, _output(0.86)).observed_status is OutcomeStatus.ACCEPTED
    assert decide(case, _output(0.80)).observed_status is OutcomeStatus.ESCALATED
    assert decide(case, _output(0.74)).observed_status is OutcomeStatus.REJECTED
    assert decide(case, _output(0.90, conflict=True)).observed_status is OutcomeStatus.ESCALATED


def test_healthy_runtime_can_still_fail_product_goal() -> None:
    case = load_case("clear-qualification")
    output = _output(0.74)
    decision = decide(case, output)
    goal = assess_goal(case, output, decision)
    assert output.terminal is True
    assert decision.artifact_valid is True
    assert goal.product_goal_achieved is False
    assert goal.closest_blocker == "wrong_business_decision"


def test_application_computes_completeness_instead_of_trusting_model() -> None:
    case = load_case("borderline-escalation")
    output = _output(0.80, conflict=True)
    output.profile.company_name = case.company.name
    output.profile.dimensions = {name: 0.80 for name in case.ground_truth.expected_dimensions}
    output.profile.completeness = 0.25
    output.evidence_used = case.pass_one

    decision = decide(case, output)

    assert output.profile.completeness == 1.0
    assert decision.artifact_valid is True
    assert decision.observed_status is OutcomeStatus.ESCALATED


def test_application_owns_weighted_score_instead_of_trusting_model_proposal() -> None:
    case = load_case("clear-qualification")
    output = _output(0.92)
    output.profile.dimensions = {name: 0.0 for name in case.ground_truth.expected_dimensions}

    decision = decide(case, output)

    assert decision.qualification_score is not None
    assert decision.qualification_score > 0.90
    assert decision.observed_status is OutcomeStatus.ACCEPTED


def test_authoritative_preflight_requires_every_provider(monkeypatch) -> None:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    try:
        preflight_live()
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("preflight unexpectedly passed")
    assert "OPENAI_API_KEY" not in message
    assert "ANTHROPIC_API_KEY" in message
    assert "DEEPSEEK_API_KEY" in message
    assert "MISTRAL_API_KEY" in message
