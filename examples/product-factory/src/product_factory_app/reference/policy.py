"""Deterministic decision and product-goal assessment."""

from __future__ import annotations

from product_factory_app.reference.contracts import (
    CaseDefinition,
    DecisionResult,
    GoalAssessment,
    OutcomeStatus,
    RuntimeOutput,
)


def _application_dimension_scores(output: RuntimeOutput, required_dimensions: set[str]) -> dict[str, float]:
    """Calculate evidence-weighted scores; model proposals remain advisory metadata."""

    scores: dict[str, float] = {}
    for dimension in required_dimensions:
        evidence = [item for item in output.evidence_used if item.dimension == dimension]
        reliability_total = sum(item.reliability for item in evidence)
        if reliability_total:
            scores[dimension] = sum(item.score_signal * item.reliability for item in evidence) / reliability_total
        elif output.profile is not None and dimension in output.profile.dimensions:
            scores[dimension] = output.profile.dimensions[dimension]
    return scores


def decide(case: CaseDefinition, output: RuntimeOutput) -> DecisionResult:
    if not output.terminal or output.profile is None:
        return DecisionResult(observed_status=OutcomeStatus.NOT_REACHED, closest_blocker="runtime_not_terminal")

    profile = output.profile
    required_dimensions = set(case.ground_truth.expected_dimensions)
    found_dimensions = required_dimensions.intersection(profile.dimensions)
    application_completeness = len(found_dimensions) / max(1, len(required_dimensions))
    profile.completeness = application_completeness
    evidence_ids = {item.id for item in output.evidence_used}
    supported_ids = evidence_ids.intersection(case.ground_truth.required_claim_ids)
    evidence_coverage = len(supported_ids) / max(1, len(case.ground_truth.required_claim_ids))
    artifact_valid = (
        profile.company_name == case.company.name
        and application_completeness >= case.policy.minimum_profile_completeness
        and found_dimensions == required_dimensions
    )
    evidence_sufficient = evidence_coverage >= case.policy.minimum_evidence_coverage
    application_scores = _application_dimension_scores(output, required_dimensions)
    score = sum(application_scores.get(name, 0.0) for name in required_dimensions) / max(1, len(required_dimensions))
    margin = score - case.policy.qualification_threshold
    material_conflict = any(item.conflicting for item in output.evidence_used)

    if not artifact_valid:
        status = OutcomeStatus.NOT_REACHED
        blocker = "invalid_profile"
    elif not evidence_sufficient:
        status = OutcomeStatus.ESCALATED
        blocker = "insufficient_decision_evidence"
    elif material_conflict or abs(margin) <= case.policy.escalation_margin:
        status = OutcomeStatus.ESCALATED
        blocker = "material_conflict" if material_conflict else "inside_escalation_band"
    elif margin > case.policy.escalation_margin:
        status = OutcomeStatus.ACCEPTED
        blocker = "threshold_cleared"
    else:
        status = OutcomeStatus.REJECTED
        blocker = "below_threshold"

    return DecisionResult(
        observed_status=status,
        qualification_score=score,
        threshold_margin=margin,
        evidence_coverage=evidence_coverage,
        artifact_valid=artifact_valid,
        decision_evidence_sufficient=evidence_sufficient,
        closest_blocker=blocker,
    )


def assess_goal(case: CaseDefinition, output: RuntimeOutput, decision: DecisionResult) -> GoalAssessment:
    path_observed = output.targeted_research_performed == case.expected_targeted_research
    correct = decision.observed_status == case.expected_status
    achieved = (
        output.terminal
        and decision.artifact_valid
        and decision.decision_evidence_sufficient
        and correct
        and path_observed
        and bool(decision.closest_blocker)
    )
    if achieved:
        blocker = "none"
    elif not output.terminal:
        blocker = "runtime_not_terminal"
    elif not decision.artifact_valid:
        blocker = "invalid_profile"
    elif not decision.decision_evidence_sufficient:
        blocker = "insufficient_decision_evidence"
    elif not path_observed:
        blocker = "required_path_not_observed"
    elif not correct:
        blocker = "wrong_business_decision"
    else:
        blocker = decision.closest_blocker
    return GoalAssessment(
        decision_correct=correct,
        product_goal_achieved=achieved,
        required_path_observed=path_observed,
        closest_blocker=blocker,
    )
