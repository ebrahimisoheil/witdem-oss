import json
import re
from dataclasses import dataclass

import pytest
from product_factory_app.domain.models import CompanyResearchRequest
from product_factory_app.research.sources import FixtureResearchSource


@dataclass
class FakeReply:
    text: str
    meta: dict[str, object]


class MalformedThenValidGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, messages: list[object]) -> dict[str, list[FakeReply]]:
        self.calls += 1
        if self.calls == 1:
            return {"replies": [FakeReply("not-json", {"usage": {"total_tokens": 5}})]}
        prompt = str(messages[0])
        evidence_id = re.search(r"\[([a-f0-9]{32})\]", prompt).group(1)
        profile = {
            "company_name": "Acme",
            "summary": "Acme sells goods.",
            "products": ["goods"],
            "catalog_complexity": "complex",
            "markets": ["Germany"],
            "product_factory_fit": "high",
            "evidence_ids": [evidence_id],
            "findings": [
                {
                    "field": "summary",
                    "value": "Acme sells goods.",
                    "evidence_ids": [evidence_id],
                }
            ],
        }
        return {"replies": [FakeReply(json.dumps(profile), {"usage": {"total_tokens": 10}})]}


@pytest.mark.e2e
def test_successful_company_research_is_persisted(service, settings) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="success"),
        source=FixtureResearchSource("success"),
    )
    assert result.manifest.status.value == "succeeded"
    assert result.execution_completed is True
    assert result.result_valid is True
    assert result.acceptance is not None and result.acceptance.status.value == "accepted"
    assert result.profile is not None
    assert result.profile.findings
    assert (settings.data_dir / "runs" / result.manifest.execution_id / "result.json").exists()


@pytest.mark.e2e
def test_incomplete_evidence_causes_another_pass(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="incomplete"),
        source=FixtureResearchSource("incomplete"),
    )
    assert result.manifest.status.value == "recovered"
    assert result.state is not None and result.state.research_pass == 2


@pytest.mark.e2e
def test_source_failure_recovers(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="recovery"),
        source=FixtureResearchSource("recovery"),
    )
    assert result.manifest.status.value == "recovered"
    assert result.state is not None and result.state.notes
    assert result.state.source_failures == 1


@pytest.mark.e2e
def test_terminal_failure_is_persisted(service, settings) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="terminal"),
        source=FixtureResearchSource("terminal"),
    )
    assert result.manifest.status.value == "failed"
    assert result.execution_completed is False
    assert result.acceptance is not None and result.acceptance.status.value == "rejected"
    assert result.manifest.error
    assert result.state is not None and result.state.termination_reason == "research_pass_limit_exhausted"
    assert result.metrics["duration_seconds"] >= 0
    assert "max_research_passes" in result.manifest.configuration
    run_file = settings.data_dir / "runs" / result.manifest.execution_id / "run.json"
    persisted = json.loads(run_file.read_text())
    assert persisted["manifest"]["status"] == "failed"
    assert persisted["manifest"]["configuration"]["max_profile_repairs"] == settings.max_profile_repairs
    assert "duration_seconds" in persisted["metrics"]


@pytest.mark.e2e
def test_malformed_profile_is_repaired_with_a_bounded_second_attempt(service) -> None:
    generator = MalformedThenValidGenerator()
    result = service.run(
        CompanyResearchRequest(company_name="Acme"),
        source=FixtureResearchSource("success"),
        extraction_generator=generator,
    )

    assert generator.calls == 2
    assert result.manifest.status.value == "succeeded"
    assert result.result_valid is True
    assert result.acceptance is not None and result.acceptance.status.value == "accepted"
    assert result.state is not None and result.state.profile_repair_count == 1


@pytest.mark.e2e
def test_confirmed_pim_profile_is_vendor_neutral(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="success"),
        source=FixtureResearchSource("success"),
    )

    assert result.result_valid is True
    assert result.profile is not None
    assert result.profile.pim_stack.pim_vendor == "Akeneo"
    assert result.profile.pim_stack.confidence >= 0.7
    assert result.profile.pim_stack.evidence_reliability >= 0.75
    assert "uses_akeneo" not in result.profile.model_dump(mode="json")


@pytest.mark.e2e
def test_unknown_pim_preserves_uncertainty_with_catalog_evidence(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="unknown_pim"),
        source=FixtureResearchSource("unknown_pim"),
    )

    assert result.result_valid is True
    assert result.profile is not None
    assert result.profile.pim_stack.state.value == "unknown"
    assert result.profile.pim_stack.pim_vendor is None
    assert result.profile.catalog_scale_estimate.scale_bucket.value == "very_large"
    assert result.profile.pim_stack.alternatives
    assert result.acceptance is not None and result.acceptance.status.value == "accepted"


@pytest.mark.e2e
def test_targeted_pim_loop_resolves_vendor_on_second_pass(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="targeted_pim_loop"),
        source=FixtureResearchSource("targeted_pim_loop"),
    )

    assert result.manifest.status.value == "recovered"
    assert result.state is not None and result.state.research_pass == 2
    assert "target_pim_stack" in result.state.targeted_research_routes
    assert result.profile is not None
    assert result.profile.pim_stack.pim_vendor == "Salsify"


@pytest.mark.e2e
def test_catalog_size_loop_resolves_scale_on_second_pass(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="catalog_size_loop"),
        source=FixtureResearchSource("catalog_size_loop"),
    )

    assert result.manifest.status.value == "recovered"
    assert result.state is not None and result.state.research_pass == 2
    assert "target_catalog_scale" in result.state.targeted_research_routes
    assert result.profile is not None
    assert result.profile.catalog_scale_estimate.estimated_range == "100k+ products"


@pytest.mark.e2e
def test_conflicting_pim_evidence_preserves_alternatives(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Acme", scenario="conflicting_pim"),
        source=FixtureResearchSource("conflicting_pim"),
    )

    assert result.result_valid is True
    assert result.profile is not None
    assert result.profile.pim_stack.state.value == "possible"
    assert set(result.profile.pim_stack.alternatives) == {"Akeneo", "Salsify"}
    assert "conflicting_pim_evidence" in result.state.conflicts


@pytest.mark.e2e
def test_weak_fit_is_valid_but_not_accepted(service) -> None:
    result = service.run(
        CompanyResearchRequest(company_name="Tiny Shop", scenario="weak_fit"),
        source=FixtureResearchSource("weak_fit"),
    )

    assert result.result_valid is True
    assert result.profile is not None
    assert result.profile.qualification.fit_band.value == "low"
    assert result.acceptance is not None and result.acceptance.status.value == "rejected"
