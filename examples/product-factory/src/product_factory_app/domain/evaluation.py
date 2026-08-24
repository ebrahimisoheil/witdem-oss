"""Application-level evaluation of whether a valid profile is acceptable."""

from __future__ import annotations

from product_factory_app.domain.models import (
    AcceptanceResult,
    AcceptanceStatus,
    CompanyProfile,
    ResearchState,
    ValidationResult,
)


def evaluate_profile_acceptance(
    profile: CompanyProfile | None,
    validation: ValidationResult | None,
    state: ResearchState | None,
    *,
    minimum_quality_score: float,
) -> AcceptanceResult:
    """Evaluate application acceptance separately from execution and validation status."""

    if profile is None or validation is None or state is None:
        return AcceptanceResult(
            status=AcceptanceStatus.REJECTED,
            reason_code="no_structured_result",
            details=["The workflow did not produce enough data for acceptance evaluation."],
        )

    evidence_ids = {item.evidence_id for item in state.evidence}
    profile_evidence_ids = set(profile.evidence_ids)
    required_checks = {
        "summary": bool(profile.summary.strip()),
        "products": bool(profile.products),
        "markets": bool(profile.markets),
        "catalog_scale": profile.catalog_scale_estimate.scale_bucket.value != "unknown",
        "catalog_complexity": bool(profile.catalog_complexity),
        "source_data_complexity": bool(profile.source_data_complexity_signals),
        "product_factory_fit": profile.product_factory_fit.value != "unknown",
        "evidence_references": bool(profile_evidence_ids),
    }
    failed_checks = [name for name, passed in required_checks.items() if not passed]
    required_signal_coverage = sum(required_checks.values()) / len(required_checks)
    evidence_coverage = (
        len(profile_evidence_ids & evidence_ids) / len(profile_evidence_ids) if profile_evidence_ids else 0.0
    )
    deterministic_fit_score = profile.qualification.fit_score / 100
    quality_score = (required_signal_coverage + evidence_coverage + profile.confidence + deterministic_fit_score) / 4
    accepted = (
        validation.valid and not failed_checks and evidence_coverage == 1.0 and quality_score >= minimum_quality_score
    )
    if not validation.valid:
        reason_code = "validation_failed"
    elif failed_checks:
        reason_code = "required_acceptance_signal_missing"
    elif accepted:
        reason_code = "accepted"
    else:
        reason_code = "quality_threshold_not_met"
    return AcceptanceResult(
        status=AcceptanceStatus.ACCEPTED if accepted else AcceptanceStatus.REJECTED,
        reason_code=reason_code,
        quality_score=quality_score,
        required_signal_coverage=required_signal_coverage,
        evidence_coverage=evidence_coverage,
        details=failed_checks + validation.errors,
    )
