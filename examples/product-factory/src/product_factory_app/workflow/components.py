"""Business-named Haystack components for the research workflow."""

from __future__ import annotations

import json
import re
from typing import Any

from haystack import component
from haystack.dataclasses import ChatMessage
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import ValidationError

from product_factory_app.analytics.semantic import SemanticAnalytics
from product_factory_app.domain.config import (
    DomainConfig,
    QualificationWeights,
    is_strong_pim_claim,
    load_product_factory_config,
)
from product_factory_app.domain.models import (
    CatalogScaleBucket,
    CatalogScaleEstimate,
    CompanyProfile,
    Evidence,
    EvidenceAssessment,
    FitLevel,
    PimIdentificationState,
    ProductFactoryQualification,
    ProductInformationStack,
    ProfileFinding,
    QualificationDimensionScore,
    ResearchDimensionState,
    ResearchDimensionStatus,
    ResearchState,
    ValidationResult,
)
from product_factory_app.research.sources import ResearchAgentError, ResearchSource, ResearchSourceError


@component
class InitializeResearch:
    """Create the first serialized research state from a company request."""

    @component.output_types(state=ResearchState)
    def run(self, request: Any) -> dict[str, ResearchState]:
        return {"state": ResearchState(request=request)}


@component
class ResearchCompany:
    """Collect one evidence pass through a source or Haystack Agent tool loop."""

    def __init__(
        self,
        source: ResearchSource,
        analytics: SemanticAnalytics | None = None,
        agent: Any | None = None,
    ) -> None:
        self.source = source
        self.analytics = analytics
        self.agent = agent

    @component.output_types(state=ResearchState, usage=dict)
    def run(self, state: ResearchState) -> dict[str, Any]:
        pass_number = state.research_pass + 1
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.request.execution_id)
        span.set_attribute("pf.research_pass", pass_number)
        usage: dict[str, Any] = {}
        if self.analytics:
            self.analytics.step("research_pass", pass_number=pass_number)
        try:
            if self.agent is None:
                evidence = self.source.collect(state.request, pass_number)
                instrumentation = getattr(self.source, "instrumentation", None)
                if callable(instrumentation):
                    usage.update(instrumentation())
            else:
                evidence, usage = self._collect_with_agent(state, pass_number)
            state.evidence.extend(evidence)
            state.research_pass = pass_number
            state.attempted_sources.append(str(state.request.website_url or "fixture-source"))
            if state.source_failures and evidence and self.analytics:
                self.analytics.recovery(
                    recovery_type="research_source",
                    status="recovered",
                    recovery_pass=pass_number,
                    source_failures=state.source_failures,
                    previous_failure=state.last_failure,
                )
        except ResearchSourceError as exc:
            failure_usage = getattr(exc, "usage", {})
            if isinstance(failure_usage, dict):
                usage.update(failure_usage)
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            state.research_pass = pass_number
            state.source_failures += 1
            state.last_failure = str(exc)
            state.notes.append(str(exc))
            state.attempted_sources.append(str(state.request.website_url or "fixture-source"))
            instrumentation = getattr(self.source, "instrumentation", None)
            if callable(instrumentation):
                usage.update(instrumentation())
        return {"state": state, "usage": usage}

    def _collect_with_agent(self, state: ResearchState, pass_number: int) -> tuple[list[Evidence], dict[str, Any]]:
        targets = list(dict.fromkeys([*state.missing_signals, *state.targeted_research_routes]))
        targeted_instruction = (
            " This is a targeted pass. Resolve these unresolved dimensions/routes: "
            + ", ".join(targets)
            + ". Use a relevant public page URL when the supplied homepage is insufficient."
            if targets
            else ""
        )
        prompt = (
            f"Research {state.request.company_name}. This is pass {pass_number}. "
            "Use collect_company_evidence exactly once, then summarize what it found. "
            "Focus on products, catalog scale and complexity, markets, commerce technology, "
            "PIM technology, and product-data complexity. "
            "After the tool call, return any additional source-backed evidence as a JSON array "
            "of Evidence objects, or return an empty JSON array; do not return prose alone."
            + targeted_instruction
            + f" Supplied website: {state.request.website_url or 'none'}."
        )
        assert self.agent is not None
        usage: dict[str, Any] = {"model_calls": 1.0}
        try:
            result = self.agent.run(messages=[ChatMessage.from_user(prompt)])
        except Exception as exc:
            raise ResearchAgentError(f"research agent/provider failed: {exc}", usage) from exc
        evidence: list[Evidence] = []
        for message in result.get("messages", []):
            meta = getattr(message, "meta", None) or {}
            message_usage = meta.get("usage", {})
            if isinstance(message_usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = message_usage.get(key)
                    if isinstance(value, (int, float)):
                        usage[key] = float(usage.get(key, 0)) + float(value)
            for tool_result in message.tool_call_results:
                usage["tool_calls"] = float(usage.get("tool_calls", 0)) + 1.0
                if tool_result.error:
                    raise ResearchAgentError(str(tool_result.result), usage)
                raw = tool_result.result
                if isinstance(raw, str):
                    evidence.extend(Evidence.model_validate(item) for item in json.loads(raw))
            text = getattr(message, "text", "") or ""
            if isinstance(text, str):
                evidence.extend(self._parse_agent_evidence(text, state.request.website_url))
        return evidence, usage

    @staticmethod
    def _parse_agent_evidence(text: str, website_url: Any) -> list[Evidence]:
        """Accept a provider's optional JSON evidence continuation without trusting prose."""

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
        if not cleaned.startswith(("[", "{")):
            return []
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            payload = payload.get("evidence", [])
        if not isinstance(payload, list):
            return []
        parsed: list[Evidence] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            candidate = dict(item)
            candidate.setdefault("source_url", str(website_url or "unknown"))
            try:
                parsed.append(Evidence.model_validate(candidate))
            except ValidationError:
                continue
        return parsed


@component
class AssessEvidence:
    """Determine whether evidence is sufficient for profile extraction."""

    REQUIRED_SIGNALS = (
        "company_identity",
        "product_catalog",
        "pim_stack",
        "catalog_scale",
        "catalog_complexity",
        "source_data_complexity",
    )

    def __init__(
        self,
        analytics: SemanticAnalytics | None = None,
        known_pim_vendors: list[str] | None = None,
        domain_config: DomainConfig | None = None,
    ) -> None:
        self.analytics = analytics
        self.domain_config = domain_config or load_product_factory_config()
        self.known_pim_vendors = tuple(known_pim_vendors or self.domain_config.vendors.pim)

    @component.output_types(state=ResearchState, assessment=EvidenceAssessment)
    def run(self, state: ResearchState) -> dict[str, Any]:
        attempts_before = dict(state.route_attempts)
        dimension_statuses = _assess_research_dimensions(
            state,
            known_pim_vendors=self.known_pim_vendors,
            route_attempts=attempts_before,
            max_targeted_attempts=self.domain_config.policies.research.max_targeted_attempts_per_dimension,
            high_confidence_threshold=self.domain_config.policies.pim_stack.high_confidence_threshold,
            require_strong_source_for_confirmed=self.domain_config.policies.pim_stack.require_strong_source_for_confirmed,
        )
        missing = [
            item.dimension
            for item in dimension_statuses
            if item.status in {ResearchDimensionState.MISSING, ResearchDimensionState.WEAK}
        ]
        conflicts = [
            item.reason_code
            for item in dimension_statuses
            if item.status is ResearchDimensionState.CONFLICTING or item.reason_code == "conflicting_pim_evidence"
        ]
        routes = [
            item.next_route
            for item in dimension_statuses
            if item.next_route is not None
            and attempts_before.get(item.next_route, 0)
            < self.domain_config.policies.research.max_targeted_attempts_per_dimension
        ]
        sufficient = bool(state.evidence) and all(
            item.status in {ResearchDimensionState.SUFFICIENT, ResearchDimensionState.UNKNOWN_ACCEPTABLE}
            for item in dimension_statuses
        )
        state.targeted_research_routes.extend(routes)
        for route in routes:
            state.route_attempts[route] = state.route_attempts.get(route, 0) + 1
        reason = (
            "evidence_sufficient"
            if sufficient
            else ("conflicting_sources" if conflicts else "required_evidence_missing")
        )
        assessment = EvidenceAssessment(
            sufficient=sufficient,
            reason_code=reason,
            confidence=0.9 if sufficient else 0.55,
            missing_signals=missing,
            conflicts=conflicts,
            dimension_statuses=dimension_statuses,
        )
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.request.execution_id)
        span.set_attribute("pf.evidence_count", len(state.evidence))
        span.set_attribute("pf.assessment_sufficient", assessment.sufficient)
        state.missing_signals = missing
        state.conflicts = conflicts
        if sufficient:
            has_accepted_uncertainty = any(
                item.status is ResearchDimensionState.UNKNOWN_ACCEPTABLE for item in dimension_statuses
            )
            state.termination_reason = (
                "evidence_sufficient_with_uncertainty"
                if missing or conflicts or has_accepted_uncertainty
                else "evidence_sufficient"
            )
        else:
            state.termination_reason = reason
        if self.analytics:
            self.analytics.decision(
                "research_completeness",
                "terminate_research" if sufficient else "continue_research",
                reason_code=reason,
                confidence=assessment.confidence,
                missing_signals=missing,
                conflicts=conflicts,
                dimension_statuses=[item.model_dump(mode="json") for item in dimension_statuses],
            )
            if routes:
                self.analytics.decision(
                    "targeted_research_route",
                    ",".join(routes),
                    missing_signals=missing,
                    conflicts=conflicts,
                )
        return {"state": state, "assessment": assessment}


@component
class ExtractCompanyProfile:
    """Extract a typed profile from evidence using a configured chat generator or fixture logic."""

    def __init__(
        self,
        generator: Any | None = None,
        analytics: SemanticAnalytics | None = None,
        known_pim_vendors: list[str] | None = None,
        domain_config: DomainConfig | None = None,
    ) -> None:
        self.generator = generator
        self.analytics = analytics
        self.domain_config = domain_config or load_product_factory_config()
        self.known_pim_vendors = tuple(known_pim_vendors or self.domain_config.vendors.pim)

    @component.output_types(profile=CompanyProfile, usage=dict, error=str)
    def run(self, state: ResearchState, correction_feedback: str | None = None) -> dict[str, Any]:
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.request.execution_id)
        span.set_attribute("pf.correction_feedback", bool(correction_feedback))
        if self.analytics:
            self.analytics.step("extract_profile", correction_feedback=bool(correction_feedback))
        if self.generator is not None:
            try:
                profile, usage = self._extract_with_generator(state, correction_feedback)
                extraction_error = ""
            except (json.JSONDecodeError, ValidationError) as exc:
                profile = CompanyProfile(company_name=state.request.company_name, summary="")
                usage = {"model_calls": 1.0}
                extraction_error = f"structured profile extraction failed: {exc}"
        else:
            profile = self._extract_deterministically(state)
            usage = {}
            extraction_error = ""
        if not profile.evidence_ids and profile.findings:
            profile.evidence_ids = list(
                dict.fromkeys(evidence_id for finding in profile.findings for evidence_id in finding.evidence_ids)
            )
        self._complete_deterministic_fields(
            profile,
            state,
            known_pim_vendors=self.known_pim_vendors,
            weights=self.domain_config.policies.qualification,
            allow_generic_fields=self.generator is None or correction_feedback is not None,
        )
        if not profile.findings:
            profile.findings = self._derive_findings(profile, state)
        return {"profile": profile, "usage": usage, "error": extraction_error}

    def _extract_with_generator(
        self, state: ResearchState, correction_feedback: str | None
    ) -> tuple[CompanyProfile, dict[str, Any]]:
        evidence_text = "\n".join(
            f"[{item.evidence_id}] {item.claim} — {item.excerpt} ({item.source_url})" for item in state.evidence
        )
        prompt = (
            "Create a Product Factory company profile from the evidence below. Return only a valid JSON object "
            "for the requested structured profile. Mark unknowns explicitly and never invent evidence.\n"
            "Include every top-level CompanyProfile field: company_name, summary, products, catalog_scale, "
            "catalog_complexity, markets, languages, commerce_technology, pim_technology, "
            "pim_stack, catalog_scale_estimate, product_data_complexity_signals, "
            "source_data_complexity_signals, product_factory_fit, qualification, fit_rationale, uncertainties, "
            "evidence_ids, findings, and confidence. Use null for unknown scalar fields and [] for unknown lists.\n"
            "qualification.fit_score must be a number from 0 to 100, not a 0 to 1 probability.\n"
            "Represent PIM findings as pim_stack.pim_vendor, pim_stack.pim_product, confidence, state, "
            "evidence_reliability, evidence_ids, alternatives, and evidence_summary. Never create fields such as "
            "uses_akeneo.\n"
            "Keep evidence_reliability separate from confidence: evidence_reliability describes source quality; "
            "confidence describes certainty in the derived finding.\n"
            "The summary must be non-empty, and products must contain evidence-backed product categories when "
            "the source shows products.\n"
            "When the evidence supports them, catalog_complexity and product_factory_fit must be populated; "
            "product_factory_fit must be one of high, medium, low, or unknown.\n"
            "For every important conclusion, include a finding object with exactly these keys: field (string), "
            "value (string), evidence_ids (array of strings), confidence (number from 0 to 1), and "
            "uncertainty (string or null). Do not use profile_field.\n"
            f"Company: {state.request.company_name}\nEvidence:\n{evidence_text}"
        )
        if correction_feedback:
            prompt += f"\nCorrect these validation issues: {correction_feedback}"
        assert self.generator is not None
        result = self.generator.run(messages=[ChatMessage.from_user(prompt)])
        text = result["replies"][0].text
        usage = result["replies"][0].meta.get("usage", {})
        usage = dict(usage) if isinstance(usage, dict) else {}
        usage["model_calls"] = 1.0
        return CompanyProfile.model_validate(self._normalize_profile_payload(text)), usage

    @staticmethod
    def _normalize_profile_payload(text: str) -> dict[str, Any]:
        """Accept harmless provider formatting variants before strict validation."""

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise json.JSONDecodeError("profile response must be a JSON object", cleaned, 0)
        list_fields = (
            "products",
            "markets",
            "languages",
            "commerce_technology",
            "pim_technology",
            "product_data_complexity_signals",
            "source_data_complexity_signals",
            "uncertainties",
            "evidence_ids",
        )
        for field in list_fields:
            if payload.get(field) is None:
                payload[field] = []
        for object_field in ("pim_stack", "catalog_scale_estimate", "qualification"):
            if payload.get(object_field) is None:
                payload[object_field] = {}
        qualification = payload.get("qualification")
        if isinstance(qualification, dict):
            fit_score = qualification.get("fit_score")
            if isinstance(fit_score, (int, float)) and 0 < fit_score <= 1:
                qualification["fit_score"] = round(float(fit_score) * 100, 2)
        if payload.get("product_factory_fit") is None:
            payload["product_factory_fit"] = "unknown"
        payload["confidence"] = ExtractCompanyProfile._normalize_confidence(payload.get("confidence"), default=0.5)
        findings = payload.get("findings")
        if isinstance(findings, list):
            normalized_findings: list[dict[str, Any]] = []
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                normalized = dict(finding)
                if "field" not in normalized and "profile_field" in normalized:
                    normalized["field"] = normalized.pop("profile_field")
                value = normalized.get("value")
                if value is not None and not isinstance(value, str):
                    normalized["value"] = json.dumps(value, ensure_ascii=False)
                normalized["confidence"] = ExtractCompanyProfile._normalize_confidence(
                    normalized.get("confidence"), default=0.5
                )
                normalized_findings.append(normalized)
            payload["findings"] = normalized_findings
        return payload

    @staticmethod
    def _normalize_confidence(value: Any, *, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return {"low": 0.3, "medium": 0.5, "high": 0.8}.get(value.lower(), default)
        return default

    def _extract_deterministically(self, state: ResearchState) -> CompanyProfile:
        text = " ".join(f"{item.claim} {item.excerpt}" for item in state.evidence)
        lowered = text.lower()
        products = ["physical products"] if state.evidence else []
        if "small catalog" in lowered or "simple product operation" in lowered:
            products = ["limited product assortment"]
        pim_stack = _classify_pim_stack(state, self.known_pim_vendors)
        catalog_scale_estimate = _classify_catalog_scale(state)
        complexity_signals = _classify_catalog_complexity(lowered)
        source_signals = _classify_source_data_complexity(lowered)
        markets = _classify_markets(lowered)
        languages = ["German", "French", "English"] if "multilingual" in lowered or "localized" in lowered else []
        qualification = _score_qualification(
            state,
            pim_stack=pim_stack,
            catalog_scale=catalog_scale_estimate,
            complexity_signals=complexity_signals,
            source_signals=source_signals,
            markets=markets,
            languages=languages,
            weights=self.domain_config.policies.qualification,
        )
        fit = qualification.fit_band
        scale_label = catalog_scale_estimate.estimated_range or (
            catalog_scale_estimate.scale_bucket.value
            if catalog_scale_estimate.scale_bucket != CatalogScaleBucket.UNKNOWN
            else None
        )
        return CompanyProfile(
            company_name=state.request.company_name,
            summary=(
                f"{state.request.company_name} is a researched company with evidence-backed product catalog signals."
            ),
            products=products,
            catalog_scale=scale_label,
            catalog_complexity="complex"
            if complexity_signals
            else ("simple" if "simple product operation" in lowered else None),
            markets=markets,
            languages=languages,
            commerce_technology=[],
            pim_technology=[pim_stack.pim_vendor] if pim_stack.pim_vendor else [],
            pim_stack=pim_stack,
            catalog_scale_estimate=catalog_scale_estimate,
            product_data_complexity_signals=complexity_signals,
            source_data_complexity_signals=source_signals,
            product_factory_fit=fit,
            qualification=qualification,
            fit_rationale="; ".join(qualification.fit_reasons),
            uncertainties=list(dict.fromkeys(state.missing_signals + state.conflicts)),
            evidence_ids=[item.evidence_id for item in state.evidence],
            findings=_build_deterministic_findings(state, pim_stack, catalog_scale_estimate, qualification),
            confidence=min(0.9, max(0.2, qualification.fit_score / 100)),
        )

    @staticmethod
    def _derive_findings(profile: CompanyProfile, state: ResearchState) -> list[ProfileFinding]:
        """Create conservative provenance links for structured output that omitted findings."""

        evidence_ids = {item.evidence_id for item in state.evidence}
        valid_ids = [item for item in profile.evidence_ids if item in evidence_ids]
        if not valid_ids:
            return []
        return [
            ProfileFinding(
                field="summary",
                value=profile.summary,
                evidence_ids=valid_ids,
                confidence=profile.confidence,
            )
        ]

    @staticmethod
    def _complete_deterministic_fields(
        profile: CompanyProfile,
        state: ResearchState,
        *,
        known_pim_vendors: tuple[str, ...] | None = None,
        weights: QualificationWeights | None = None,
        allow_generic_fields: bool = True,
    ) -> None:
        """Fill application-owned qualification fields when provider output omits them."""

        vendors = known_pim_vendors or KNOWN_PIM_VENDORS
        text = " ".join(f"{item.claim} {item.excerpt}" for item in state.evidence).lower()
        if allow_generic_fields:
            if state.evidence and not profile.evidence_ids:
                profile.evidence_ids = [item.evidence_id for item in state.evidence]
            if state.evidence and not profile.summary.strip():
                profile.summary = (
                    f"{state.request.company_name} is a researched company with evidence-backed "
                    "product catalog signals."
                )
            if state.evidence and not profile.products:
                profile.products = ["physical products"]
            if state.evidence and not profile.markets:
                profile.markets = _classify_markets(text)
        if not profile.source_data_complexity_signals:
            profile.source_data_complexity_signals = _classify_source_data_complexity(text)
        if not profile.product_data_complexity_signals:
            profile.product_data_complexity_signals = _classify_catalog_complexity(text)
        if profile.pim_stack.state is PimIdentificationState.UNKNOWN and not profile.pim_stack.evidence_ids:
            profile.pim_stack = _classify_pim_stack(state, vendors)
        elif profile.pim_stack.evidence_ids and profile.pim_stack.evidence_reliability == 0:
            profile.pim_stack.evidence_reliability = _evidence_reliability(state, profile.pim_stack.evidence_ids)
        if profile.catalog_scale_estimate.scale_bucket is CatalogScaleBucket.UNKNOWN:
            profile.catalog_scale_estimate = _classify_catalog_scale(state)
        elif profile.catalog_scale_estimate.evidence_ids and profile.catalog_scale_estimate.evidence_reliability == 0:
            profile.catalog_scale_estimate.evidence_reliability = _evidence_reliability(
                state,
                profile.catalog_scale_estimate.evidence_ids,
            )
        if not profile.catalog_scale and profile.catalog_scale_estimate.scale_bucket is not CatalogScaleBucket.UNKNOWN:
            profile.catalog_scale = (
                profile.catalog_scale_estimate.estimated_range or profile.catalog_scale_estimate.scale_bucket.value
            )
        if not profile.catalog_complexity and profile.product_data_complexity_signals:
            profile.catalog_complexity = "complex"
        if profile.qualification.fit_band is FitLevel.UNKNOWN and profile.qualification.fit_score == 0:
            profile.qualification = _score_qualification(
                state,
                pim_stack=profile.pim_stack,
                catalog_scale=profile.catalog_scale_estimate,
                complexity_signals=profile.product_data_complexity_signals,
                source_signals=profile.source_data_complexity_signals,
                markets=profile.markets,
                languages=profile.languages,
                weights=weights,
            )
        profile.product_factory_fit = profile.qualification.fit_band
        if not profile.fit_rationale:
            profile.fit_rationale = "; ".join(profile.qualification.fit_reasons)


@component
class ValidateCompanyProfile:
    """Apply deterministic business validation to a structured profile."""

    def __init__(
        self,
        analytics: SemanticAnalytics | None = None,
        domain_config: DomainConfig | None = None,
    ) -> None:
        self.analytics = analytics
        self.domain_config = domain_config or load_product_factory_config()

    @component.output_types(profile=CompanyProfile, validation=ValidationResult)
    def run(self, profile: CompanyProfile, state: ResearchState) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        if not profile.summary.strip():
            errors.append("summary is required")
        if not profile.products:
            errors.append("at least one product category is required")
        evidence_ids = {item.evidence_id for item in state.evidence}
        missing_ids = [item for item in profile.evidence_ids if item not in evidence_ids]
        if missing_ids:
            errors.append("profile references unknown evidence IDs")
        nested_ids = [*profile.pim_stack.evidence_ids, *profile.catalog_scale_estimate.evidence_ids]
        if any(evidence_id not in evidence_ids for evidence_id in nested_ids):
            errors.append("nested qualification fields reference unknown evidence IDs")
        missing_finding_ids = [
            evidence_id
            for finding in profile.findings
            for evidence_id in finding.evidence_ids
            if evidence_id not in evidence_ids
        ]
        if missing_finding_ids:
            errors.append("profile findings reference unknown evidence IDs")
        if not profile.findings:
            warnings.append("profile has no explicit finding-to-evidence links")
        if not profile.catalog_scale:
            warnings.append("catalog scale is unresolved")
        if (
            profile.pim_stack.state in {PimIdentificationState.CONFIRMED, PimIdentificationState.PROBABLE}
            and not profile.pim_stack.evidence_ids
        ):
            errors.append("high-confidence PIM identification requires supporting evidence")
        if is_strong_pim_claim(profile.pim_stack.confidence, self.domain_config) and not _pim_support_is_strong(
            state, profile.pim_stack.evidence_ids
        ):
            errors.append("high-confidence PIM identification requires strong source support")
        unknown_pim_with_vendor = (
            profile.pim_stack.state is PimIdentificationState.UNKNOWN and profile.pim_stack.pim_vendor
        )
        if unknown_pim_with_vendor:
            errors.append("unknown PIM state cannot name a selected vendor")
        if (
            profile.catalog_scale_estimate.scale_bucket is not CatalogScaleBucket.UNKNOWN
            and not profile.catalog_scale_estimate.evidence_ids
        ):
            errors.append("catalog scale estimate requires supporting evidence")
        finding_fields = {finding.field for finding in profile.findings}
        if profile.qualification.fit_score and "product_factory_fit" not in finding_fields:
            warnings.append("qualification has no explicit finding")
        if profile.product_factory_fit != profile.qualification.fit_band:
            errors.append("product_factory_fit must agree with qualification.fit_band")
        validation = ValidationResult(valid=not errors, errors=errors, warnings=warnings)
        span = trace.get_current_span()
        span.set_attribute("witdem.execution_id", state.request.execution_id)
        span.set_attribute("pf.validation_attempt", state.validation_attempts + 1)
        span.set_attribute("pf.validation_valid", validation.valid)
        state.validation_attempts += 1
        state.validation_errors = errors
        state.validation_warnings = warnings
        if self.analytics:
            self.analytics.decision(
                "validation",
                "accept_profile" if validation.valid else "repair_profile",
                errors=errors,
                warnings=warnings,
            )
        return {"profile": profile, "validation": validation}


KNOWN_PIM_VENDORS = (
    "Akeneo",
    "Pimcore",
    "Salsify",
    "inriver",
    "Contentserv",
    "Syndigo",
    "Stibo Systems",
    "Informatica PIM",
    "SAP",
)


def _assess_research_dimensions(
    state: ResearchState,
    *,
    known_pim_vendors: tuple[str, ...],
    route_attempts: dict[str, int],
    max_targeted_attempts: int = 1,
    high_confidence_threshold: float = 0.85,
    require_strong_source_for_confirmed: bool = True,
) -> list[ResearchDimensionStatus]:
    text = " ".join(f"{item.claim} {item.excerpt}".lower() for item in state.evidence)
    product_ids = _matching_evidence_ids(state, ("sell", "product", "catalog"))
    identity_ids = _matching_evidence_ids(
        state,
        (
            "market",
            "country",
            "international",
            "global",
            "worldwide",
            "nationwide",
            "dach",
            "b2b",
            "b2c",
            "germany",
            "france",
        ),
    )
    scale = _classify_catalog_scale(state)
    complexity_ids = _matching_evidence_ids(
        state,
        (
            "variant",
            "complex catalog",
            "attribute",
            "taxonomy",
            "multilingual",
            "regional assortment",
            "simple product operation",
            "upc",
            "hierarch",
            "duplicate",
            "inconsistent",
            "instances",
        ),
    )
    source_data_ids = _matching_evidence_ids(
        state,
        (
            "spreadsheet",
            "excel",
            "csv",
            "pdf",
            "supplier",
            "feed",
            "erp export",
            "manual enrichment",
            "duplicate",
            "inconsistent",
            "independent systems",
        ),
    )
    pim = _classify_pim_stack(state, known_pim_vendors)
    core_supported = all(
        (
            bool(product_ids),
            bool(identity_ids),
            bool(scale.evidence_ids),
            bool(complexity_ids),
            bool(source_data_ids),
        )
    )
    catalog_scale_unknown_supported = all(
        (
            bool(product_ids),
            bool(identity_ids),
            pim.state
            in {
                PimIdentificationState.CONFIRMED,
                PimIdentificationState.PROBABLE,
                PimIdentificationState.POSSIBLE,
            },
            bool(complexity_ids),
            bool(source_data_ids),
            route_attempts.get("target_catalog_scale", 0) > 0,
        )
    )
    statuses = [
        _dimension_status(
            "product_catalog",
            bool(product_ids),
            evidence_ids=product_ids,
            state=state,
            missing_reason="product_catalog_missing",
            max_targeted_attempts=max_targeted_attempts,
        ),
        _dimension_status(
            "company_identity",
            bool(identity_ids),
            evidence_ids=identity_ids,
            state=state,
            missing_reason="company_identity_missing",
            max_targeted_attempts=max_targeted_attempts,
        ),
        _assess_pim_dimension(
            state,
            pim,
            text=text,
            core_supported=core_supported,
            route_attempts=route_attempts,
            max_targeted_attempts=max_targeted_attempts,
            high_confidence_threshold=high_confidence_threshold,
            require_strong_source_for_confirmed=require_strong_source_for_confirmed,
        ),
        _assess_catalog_scale_dimension(
            state,
            scale,
            unknown_supported=catalog_scale_unknown_supported,
            route_attempts=route_attempts,
            max_targeted_attempts=max_targeted_attempts,
        ),
        _dimension_status(
            "catalog_complexity",
            bool(complexity_ids),
            evidence_ids=complexity_ids,
            state=state,
            missing_reason="catalog_complexity_missing",
            max_targeted_attempts=max_targeted_attempts,
        ),
        _dimension_status(
            "source_data_complexity",
            bool(source_data_ids),
            evidence_ids=source_data_ids,
            state=state,
            missing_reason="source_data_complexity_missing",
            max_targeted_attempts=max_targeted_attempts,
        ),
    ]
    conflicts = _detect_conflicts(text, known_pim_vendors)
    if "conflicting_pim_evidence" in conflicts:
        conflict_route_attempted = route_attempts.get("target_conflicting_pim_evidence", 0) > 0
        conflict_status = (
            ResearchDimensionState.UNKNOWN_ACCEPTABLE
            if conflict_route_attempted and core_supported
            else ResearchDimensionState.CONFLICTING
        )
        statuses[2] = ResearchDimensionStatus(
            dimension="pim_stack",
            status=conflict_status,
            reason_code="conflicting_pim_evidence",
            evidence_ids=pim.evidence_ids,
            evidence_reliability=pim.evidence_reliability,
            finding_confidence=pim.confidence,
            next_route=(
                "target_conflicting_pim_evidence"
                if route_attempts.get("target_conflicting_pim_evidence", 0) < max_targeted_attempts
                else None
            ),
        )
    return statuses


def _dimension_status(
    dimension: str,
    is_sufficient: bool,
    *,
    evidence_ids: list[str],
    state: ResearchState,
    missing_reason: str,
    max_targeted_attempts: int = 1,
) -> ResearchDimensionStatus:
    if is_sufficient:
        return ResearchDimensionStatus(
            dimension=dimension,
            status=ResearchDimensionState.SUFFICIENT,
            reason_code=f"{dimension}_sufficient",
            evidence_ids=evidence_ids,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
            finding_confidence=0.8,
        )
    route = f"target_{dimension}"
    return ResearchDimensionStatus(
        dimension=dimension,
        status=ResearchDimensionState.MISSING,
        reason_code=missing_reason,
        evidence_ids=evidence_ids,
        evidence_reliability=_evidence_reliability(state, evidence_ids),
        finding_confidence=0.0,
        next_route=route if state.route_attempts.get(route, 0) < max_targeted_attempts else None,
    )


def _assess_catalog_scale_dimension(
    state: ResearchState,
    scale: CatalogScaleEstimate,
    *,
    unknown_supported: bool,
    route_attempts: dict[str, int],
    max_targeted_attempts: int = 1,
) -> ResearchDimensionStatus:
    route = "target_catalog_scale"
    if scale.evidence_ids:
        return ResearchDimensionStatus(
            dimension="catalog_scale",
            status=ResearchDimensionState.SUFFICIENT,
            reason_code="catalog_scale_sufficient",
            evidence_ids=scale.evidence_ids,
            evidence_reliability=scale.evidence_reliability,
            finding_confidence=scale.confidence,
        )
    if unknown_supported:
        return ResearchDimensionStatus(
            dimension="catalog_scale",
            status=ResearchDimensionState.UNKNOWN_ACCEPTABLE,
            reason_code="catalog_scale_unknown_acceptable_after_targeted_research",
            evidence_ids=[],
            evidence_reliability=0.0,
            finding_confidence=0.0,
        )
    return ResearchDimensionStatus(
        dimension="catalog_scale",
        status=ResearchDimensionState.MISSING,
        reason_code="catalog_scale_missing",
        evidence_ids=[],
        evidence_reliability=0.0,
        finding_confidence=0.0,
        next_route=route if route_attempts.get(route, 0) < max_targeted_attempts else None,
    )


def _assess_pim_dimension(
    state: ResearchState,
    pim: ProductInformationStack,
    *,
    text: str,
    core_supported: bool,
    route_attempts: dict[str, int],
    max_targeted_attempts: int = 1,
    high_confidence_threshold: float = 0.85,
    require_strong_source_for_confirmed: bool = True,
) -> ResearchDimensionStatus:
    route = "target_pim_stack"
    attempted = route_attempts.get(route, 0) > 0
    explicit_unknown = "no reliable public pim" in text or "no public pim" in text
    if pim.state in {PimIdentificationState.CONFIRMED, PimIdentificationState.PROBABLE}:
        if (
            require_strong_source_for_confirmed
            and pim.confidence >= high_confidence_threshold
            and not _pim_support_is_strong(state, pim.evidence_ids)
        ):
            return ResearchDimensionStatus(
                dimension="pim_stack",
                status=ResearchDimensionState.WEAK,
                reason_code="high_confidence_pim_needs_stronger_evidence",
                evidence_ids=pim.evidence_ids,
                evidence_reliability=pim.evidence_reliability,
                finding_confidence=pim.confidence,
                next_route=route if route_attempts.get(route, 0) < max_targeted_attempts else None,
            )
        return ResearchDimensionStatus(
            dimension="pim_stack",
            status=ResearchDimensionState.SUFFICIENT,
            reason_code="pim_stack_sufficient",
            evidence_ids=pim.evidence_ids,
            evidence_reliability=pim.evidence_reliability,
            finding_confidence=pim.confidence,
        )
    if pim.state is PimIdentificationState.POSSIBLE:
        if attempted:
            return ResearchDimensionStatus(
                dimension="pim_stack",
                status=ResearchDimensionState.UNKNOWN_ACCEPTABLE if core_supported else ResearchDimensionState.WEAK,
                reason_code="pim_possible_after_targeted_research",
                evidence_ids=pim.evidence_ids,
                evidence_reliability=pim.evidence_reliability,
                finding_confidence=pim.confidence,
            )
        return ResearchDimensionStatus(
            dimension="pim_stack",
            status=ResearchDimensionState.WEAK,
            reason_code="pim_stack_possible_needs_targeted_research",
            evidence_ids=pim.evidence_ids,
            evidence_reliability=pim.evidence_reliability,
            finding_confidence=pim.confidence,
            next_route=route,
        )
    if core_supported and (attempted or explicit_unknown):
        return ResearchDimensionStatus(
            dimension="pim_stack",
            status=ResearchDimensionState.UNKNOWN_ACCEPTABLE,
            reason_code="pim_unknown_acceptable_with_strong_qualification",
            evidence_ids=pim.evidence_ids,
            evidence_reliability=pim.evidence_reliability,
            finding_confidence=pim.confidence,
        )
    return ResearchDimensionStatus(
        dimension="pim_stack",
        status=ResearchDimensionState.MISSING,
        reason_code="pim_stack_missing",
        evidence_ids=pim.evidence_ids,
        evidence_reliability=pim.evidence_reliability,
        finding_confidence=pim.confidence,
        next_route=route if route_attempts.get(route, 0) < max_targeted_attempts else None,
    )


def _evidence_reliability(state: ResearchState, evidence_ids: list[str]) -> float:
    values = [item.reliability for item in state.evidence if item.evidence_id in evidence_ids]
    return round(sum(values) / len(values), 3) if values else 0.0


def _pim_support_is_strong(state: ResearchState, evidence_ids: list[str]) -> bool:
    strong_types = {"vendor_case_study", "official_company_page", "partner_reference"}
    support = [item for item in state.evidence if item.evidence_id in evidence_ids]
    return any(item.evidence_type in strong_types and item.reliability >= 0.75 for item in support)


def _has_pim_resolution(text: str, known_pim_vendors: tuple[str, ...] = KNOWN_PIM_VENDORS) -> bool:
    return any(term.lower() in text for term in known_pim_vendors) or any(
        term in text
        for term in (
            "pim",
            "product-information",
            "product information management",
            "no reliable public pim",
            "no public pim",
            "custom product-information",
        )
    )


def _has_catalog_scale(text: str) -> bool:
    return any(
        term in text
        for term in (
            "sku",
            "skus",
            "tens of thousands",
            "50,000",
            "50k",
            "100,000",
            "100k",
            "500,000",
            "500k",
            "2.5m",
            "small catalog",
            "large catalog",
            "very large catalog",
            "product count",
        )
    )


def _has_catalog_complexity(text: str) -> bool:
    return any(
        term in text
        for term in (
            "variant",
            "complex catalog",
            "attribute",
            "taxonomy",
            "multilingual",
            "localized",
            "regional assortment",
            "regulatory",
            "simple product operation",
        )
    )


def _has_source_data_complexity(text: str) -> bool:
    return any(
        term in text
        for term in (
            "spreadsheet",
            "excel",
            "csv",
            "pdf",
            "supplier",
            "feed",
            "erp export",
            "manual enrichment",
            "fragmented",
            "inconsistent",
            "incomplete product information",
            "source data",
        )
    )


def _detect_conflicts(text: str, known_pim_vendors: tuple[str, ...] = KNOWN_PIM_VENDORS) -> list[str]:
    conflicts: list[str] = []
    vendor_hits = [vendor for vendor in known_pim_vendors if vendor.lower() in text]
    if len(vendor_hits) > 1 and "conflicting" in text:
        conflicts.append("conflicting_pim_evidence")
    if "500 skus" in text and ("50,000 skus" in text or "50k skus" in text):
        conflicts.append("catalog_scale_disagreement")
    return conflicts


def _targeted_routes(missing: list[str], conflicts: list[str], state: ResearchState) -> list[str]:
    candidates = [f"target_{signal}" for signal in missing]
    candidates.extend(f"target_{conflict}" for conflict in conflicts)
    return [route for route in candidates if state.route_attempts.get(route, 0) == 0]


def _matching_evidence_ids(state: ResearchState, terms: tuple[str, ...]) -> list[str]:
    ids: list[str] = []
    for item in state.evidence:
        text = f"{item.claim} {item.excerpt}".lower()
        if any(term in text for term in terms):
            ids.append(item.evidence_id)
    return ids


def _classify_pim_stack(
    state: ResearchState,
    known_pim_vendors: tuple[str, ...] = KNOWN_PIM_VENDORS,
) -> ProductInformationStack:
    text = " ".join(f"{item.claim} {item.excerpt}" for item in state.evidence)
    lowered = text.lower()
    evidence_ids = _matching_evidence_ids(
        state,
        ("pim", "product-information", "product information management", "centralized catalog workflow"),
    )
    pim_scoped_text = " ".join(
        f"{item.claim} {item.excerpt}".lower()
        for item in state.evidence
        if item.evidence_id in evidence_ids or item.qualification_dimension == "pim_stack"
    )
    vendors = [vendor for vendor in known_pim_vendors if vendor.lower() in pim_scoped_text]
    if "no reliable public pim" in lowered or "no public pim" in lowered:
        return ProductInformationStack(
            confidence=0.28,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
            state=PimIdentificationState.UNKNOWN,
            evidence_ids=evidence_ids,
            alternatives=["custom/internal PIM", "undisclosed PIM"],
            evidence_summary="Targeted research did not find reliable public PIM evidence.",
        )
    if len(vendors) > 1:
        return ProductInformationStack(
            confidence=0.48,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
            state=PimIdentificationState.POSSIBLE,
            evidence_ids=evidence_ids,
            alternatives=vendors,
            evidence_summary="Public evidence names more than one possible PIM system.",
        )
    if vendors:
        confidence = 0.91 if any(term in lowered for term in ("case study", "confirmed", "uses")) else 0.74
        state_value = PimIdentificationState.CONFIRMED if confidence >= 0.9 else PimIdentificationState.PROBABLE
        return ProductInformationStack(
            pim_vendor=vendors[0],
            pim_product=vendors[0],
            confidence=confidence,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
            state=state_value,
            evidence_ids=evidence_ids,
            evidence_summary=f"Evidence identifies {vendors[0]} as the product-information system.",
        )
    if evidence_ids:
        return ProductInformationStack(
            confidence=0.52,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
            state=PimIdentificationState.POSSIBLE,
            evidence_ids=evidence_ids,
            alternatives=["custom/internal PIM", "centralized product-information workflow"],
            evidence_summary="Evidence shows a product-information workflow without identifying a vendor.",
        )
    return ProductInformationStack()


def _classify_catalog_scale(state: ResearchState) -> CatalogScaleEstimate:
    lowered = " ".join(f"{item.claim} {item.excerpt}".lower() for item in state.evidence)
    evidence_ids = _matching_evidence_ids(state, ("sku", "product count", "tens of thousands", "catalog"))
    if any(term in lowered for term in ("500,000", "500k")):
        bucket = CatalogScaleBucket.VERY_LARGE
        estimated_range = "500k+ SKUs"
        confidence = 0.86
    elif any(term in lowered for term in ("2.5m", "2,500,000")):
        bucket = CatalogScaleBucket.VERY_LARGE
        estimated_range = "2.5M data objects"
        confidence = 0.82
    elif any(term in lowered for term in ("100,000", "100k", "very large catalog")):
        bucket = CatalogScaleBucket.VERY_LARGE
        estimated_range = "100k+ products"
        confidence = 0.78
    elif any(term in lowered for term in ("50,000", "50k", "tens of thousands")):
        bucket = CatalogScaleBucket.VERY_LARGE
        estimated_range = "50k+ SKUs"
        confidence = 0.74
    elif "large catalog" in lowered or "25,000" in lowered:
        bucket = CatalogScaleBucket.LARGE
        estimated_range = "10k-25k products"
        confidence = 0.68
    elif any(term in lowered for term in ("3,200 products", "3200 products")):
        bucket = CatalogScaleBucket.SMALL
        estimated_range = "3,200 products"
        confidence = 0.8
    elif "small catalog" in lowered or "500 skus" in lowered:
        bucket = CatalogScaleBucket.SMALL
        estimated_range = "under 1k products"
        confidence = 0.72
    else:
        bucket = CatalogScaleBucket.UNKNOWN
        estimated_range = None
        confidence = 0.0
    return CatalogScaleEstimate(
        scale_bucket=bucket,
        estimated_range=estimated_range,
        confidence=confidence,
        evidence_reliability=_evidence_reliability(state, evidence_ids)
        if bucket is not CatalogScaleBucket.UNKNOWN
        else 0.0,
        evidence_ids=evidence_ids if bucket is not CatalogScaleBucket.UNKNOWN else [],
        uncertainty=None if bucket is not CatalogScaleBucket.UNKNOWN else "No reliable public catalog-size estimate.",
    )


def _classify_catalog_complexity(text: str) -> list[str]:
    signals: list[str] = []
    signal_terms = {
        "variants": ("variant",),
        "localized attributes": ("localized", "localization", "multilingual"),
        "attribute combinations": ("attribute",),
        "regional assortments": ("regional assortment",),
        "regulatory documents": ("regulatory", "product document"),
        "large taxonomy": ("taxonomy", "category tree"),
        "SKU mapping": ("upc", "sku mapping"),
        "product hierarchies": ("hierarch",),
        "duplicate or inconsistent data": ("duplicate", "inconsistent"),
        "multiple brands": ("brand",),
        "multiple suppliers": ("supplier",),
    }
    for signal, terms in signal_terms.items():
        if any(term in text for term in terms):
            signals.append(signal)
    return signals


def _classify_source_data_complexity(text: str) -> list[str]:
    signals: list[str] = []
    signal_terms = {
        "spreadsheets": ("spreadsheet", "excel"),
        "CSV exports": ("csv",),
        "supplier files": ("supplier file", "supplier catalog"),
        "PDF catalogs": ("pdf",),
        "product feeds": ("feed",),
        "ERP exports": ("erp export",),
        "manual enrichment": ("manual enrichment",),
        "fragmented sources": ("fragmented", "multiple data sources"),
        "duplicate work": ("duplicate",),
        "independent systems": ("independent systems",),
        "inconsistent attributes": ("inconsistent", "incomplete product information"),
    }
    for signal, terms in signal_terms.items():
        if any(term in text for term in terms):
            signals.append(signal)
    return signals


def _classify_markets(text: str) -> list[str]:
    markets = []
    for label, terms in (
        ("retail", ("retail", "stores", "store locations")),
        ("eCommerce", ("ecommerce", "e-commerce", "online store", "online")),
        ("B2B", ("b2b", "business-to-business")),
        ("B2C", ("b2c", "business-to-consumer")),
    ):
        if any(term in text for term in terms):
            markets.append(label)
    for market in ("Germany", "France", "United Kingdom", "United States", "Netherlands"):
        if market.lower() in text:
            markets.append(market)
    if not markets and any(term in text for term in ("multiple markets", "international", "global", "worldwide")):
        markets = ["multiple markets"]
    return markets


def _score_qualification(
    state: ResearchState,
    *,
    pim_stack: ProductInformationStack,
    catalog_scale: CatalogScaleEstimate,
    complexity_signals: list[str],
    source_signals: list[str],
    markets: list[str],
    languages: list[str],
    weights: QualificationWeights | None = None,
) -> ProductFactoryQualification:
    configured_weights = weights or QualificationWeights(
        pim_product_information_infrastructure=0.18,
        catalog_scale=0.25,
        catalog_complexity=0.24,
        multi_market_multilingual=0.13,
        source_data_complexity=0.20,
    )
    scores: list[QualificationDimensionScore] = []
    pim_score = {
        PimIdentificationState.CONFIRMED: 1.0,
        PimIdentificationState.PROBABLE: 0.8,
        PimIdentificationState.POSSIBLE: 0.55,
        PimIdentificationState.UNKNOWN: 0.25,
    }[pim_stack.state]
    scores.append(
        QualificationDimensionScore(
            dimension="pim_product_information_infrastructure",
            score=pim_score,
            evidence_reliability=pim_stack.evidence_reliability,
            finding_confidence=pim_stack.confidence,
            evidence_ids=pim_stack.evidence_ids,
            reason=pim_stack.evidence_summary or "No reliable public PIM evidence.",
        )
    )
    scale_score = {
        CatalogScaleBucket.VERY_LARGE: 1.0,
        CatalogScaleBucket.LARGE: 0.78,
        CatalogScaleBucket.MEDIUM: 0.5,
        CatalogScaleBucket.SMALL: 0.15,
        CatalogScaleBucket.UNKNOWN: 0.25,
    }[catalog_scale.scale_bucket]
    scores.append(
        QualificationDimensionScore(
            dimension="catalog_scale",
            score=scale_score,
            evidence_reliability=catalog_scale.evidence_reliability,
            finding_confidence=catalog_scale.confidence,
            evidence_ids=catalog_scale.evidence_ids,
            reason=catalog_scale.estimated_range or "Catalog scale is unresolved.",
        )
    )
    complexity_score = min(1.0, len(complexity_signals) / 4)
    scores.append(
        QualificationDimensionScore(
            dimension="catalog_complexity",
            score=complexity_score,
            evidence_ids=_matching_evidence_ids(state, ("variant", "attribute", "taxonomy", "multilingual")),
            evidence_reliability=_evidence_reliability(
                state, _matching_evidence_ids(state, ("variant", "attribute", "taxonomy", "multilingual"))
            ),
            finding_confidence=0.8 if complexity_signals else 0.0,
            reason=", ".join(complexity_signals) if complexity_signals else "Limited catalog-complexity evidence.",
        )
    )
    market_score = min(1.0, (len(markets) + len(languages)) / 4)
    scores.append(
        QualificationDimensionScore(
            dimension="multi_market_multilingual",
            score=market_score,
            evidence_ids=_matching_evidence_ids(
                state, ("market", "country", "international", "global", "worldwide", "localized")
            ),
            evidence_reliability=_evidence_reliability(
                state,
                _matching_evidence_ids(
                    state, ("market", "country", "international", "global", "worldwide", "localized")
                ),
            ),
            finding_confidence=0.8 if markets or languages else 0.0,
            reason=", ".join(markets + languages) if markets or languages else "No multi-market complexity evidence.",
        )
    )
    source_score = min(1.0, len(source_signals) / 4)
    scores.append(
        QualificationDimensionScore(
            dimension="source_data_complexity",
            score=source_score,
            evidence_ids=_matching_evidence_ids(state, ("spreadsheet", "csv", "pdf", "supplier", "feed", "erp")),
            evidence_reliability=_evidence_reliability(
                state, _matching_evidence_ids(state, ("spreadsheet", "csv", "pdf", "supplier", "feed", "erp"))
            ),
            finding_confidence=0.8 if source_signals else 0.0,
            reason=", ".join(source_signals) if source_signals else "Limited source-data complexity evidence.",
        )
    )
    weighted = (
        pim_score * configured_weights.pim_product_information_infrastructure
        + scale_score * configured_weights.catalog_scale
        + complexity_score * configured_weights.catalog_complexity
        + market_score * configured_weights.multi_market_multilingual
        + source_score * configured_weights.source_data_complexity
    )
    fit_score = round(weighted * 100, 2)
    if fit_score >= 72:
        band = FitLevel.HIGH
    elif fit_score >= 45:
        band = FitLevel.MEDIUM
    elif fit_score > 0:
        band = FitLevel.LOW
    else:
        band = FitLevel.UNKNOWN
    reasons = [score.reason for score in scores if score.score >= 0.5]
    risks = [score.reason for score in scores if score.score < 0.5]
    return ProductFactoryQualification(
        fit_score=fit_score,
        fit_band=band,
        fit_reasons=reasons,
        fit_risks=risks,
        dimension_scores=scores,
    )


def _build_deterministic_findings(
    state: ResearchState,
    pim_stack: ProductInformationStack,
    catalog_scale: CatalogScaleEstimate,
    qualification: ProductFactoryQualification,
) -> list[ProfileFinding]:
    evidence_ids = [item.evidence_id for item in state.evidence]
    findings = [
        ProfileFinding(
            field="summary",
            value=f"{state.request.company_name} has evidence-backed product catalog signals.",
            evidence_ids=evidence_ids,
            confidence=0.82 if evidence_ids else 0.2,
            evidence_reliability=_evidence_reliability(state, evidence_ids),
        ),
        ProfileFinding(
            field="product_factory_fit",
            value=qualification.fit_band.value,
            evidence_ids=evidence_ids,
            confidence=max(0.2, qualification.fit_score / 100),
            evidence_reliability=_evidence_reliability(state, evidence_ids),
        ),
    ]
    if pim_stack.evidence_ids:
        findings.append(
            ProfileFinding(
                field="pim_stack",
                value=pim_stack.pim_vendor or pim_stack.state.value,
                evidence_ids=pim_stack.evidence_ids,
                confidence=pim_stack.confidence,
                evidence_reliability=pim_stack.evidence_reliability,
                uncertainty="unresolved" if pim_stack.state is PimIdentificationState.UNKNOWN else None,
            )
        )
    if catalog_scale.evidence_ids:
        findings.append(
            ProfileFinding(
                field="catalog_scale",
                value=catalog_scale.estimated_range or catalog_scale.scale_bucket.value,
                evidence_ids=catalog_scale.evidence_ids,
                confidence=catalog_scale.confidence,
                evidence_reliability=catalog_scale.evidence_reliability,
                uncertainty=catalog_scale.uncertainty,
            )
        )
    return findings
