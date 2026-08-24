import json

import pytest

pytest.importorskip("witdem", reason="archived contributor experiment requires the analytics package")

from product_factory_app.experiments.live_heterogeneous import (
    candidates_for_provider_names,
    derived_termination_category,
    write_live_heterogeneous_report,
    write_live_role_isolation_report,
)


def test_candidates_can_be_filtered_by_provider() -> None:
    candidates = candidates_for_provider_names(("openai", "mistral"))

    assert {settings.provider for _, settings in candidates} == {"openai", "mistral"}


def test_live_heterogeneous_report_answers_empirical_questions(tmp_path) -> None:
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    records = [
        {
            "experiment_id": "live-heterogeneous-v1",
            "provider": "openai",
            "company_name": "Example Co",
            "status": "succeeded",
            "execution_completed": True,
            "result_valid": True,
            "accepted_result": True,
            "profile_repairs": 0,
            "research_passes": 2,
            "targeted_routes": ["target_pim_stack"],
            "unknown_acceptable_dimensions": ["catalog_scale"],
            "pim_vendor": "Akeneo",
            "pim_state": "confirmed",
            "catalog_scale_bucket": "very_large",
            "catalog_estimated_range": "500k+ SKUs",
            "source_data_status": "sufficient",
            "source_data_signals": ["supplier spreadsheets"],
            "strongest_pim_source_type": "vendor",
            "fit_band": "medium",
            "fit_score": 71.0,
            "model_cost_usd": 0.01,
            "time_to_acceptance_seconds": 12.0,
        },
        {
            "experiment_id": "live-heterogeneous-v1",
            "provider": "mistral",
            "company_name": "Example Co",
            "status": "failed",
            "execution_completed": False,
            "result_valid": False,
            "accepted_result": False,
            "profile_repairs": 1,
            "research_passes": 1,
            "targeted_routes": [],
            "unknown_acceptable_dimensions": ["pim_stack"],
            "pim_vendor": None,
            "pim_state": "unknown",
            "catalog_scale_bucket": "unknown",
            "catalog_estimated_range": None,
            "source_data_status": "missing",
            "source_data_signals": [],
            "strongest_pim_source_type": None,
            "fit_band": None,
            "fit_score": None,
            "model_cost_usd": 0.002,
            "time_to_acceptance_seconds": None,
        },
    ]
    (experiments / "runs.jsonl").write_text("\n".join(json.dumps(item) for item in records))

    report_path = write_live_heterogeneous_report(tmp_path, tmp_path / "report.md")
    report = report_path.read_text()

    assert "Targeted PIM research frequency: 1/2" in report
    assert "Unknown acceptable PIM frequency: 1/2" in report
    assert "Unknown acceptable catalog-scale frequency: 1/2" in report
    assert "| vendor | 1 |" in report


def test_role_isolation_report_separates_research_and_extraction_roles(tmp_path) -> None:
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    records = [
        {
            "experiment_id": "live-role-isolation-v1",
            "research_provider": "openai",
            "extraction_provider": "deepseek",
            "status": "failed",
            "execution_completed": False,
            "result_valid": False,
            "accepted_result": False,
            "profile_repairs": 1,
            "research_passes": 2,
            "source_failures": 0,
            "targeted_routes": ["target_catalog_scale"],
            "dimension_statuses": [{"dimension": "catalog_scale", "status": "missing"}],
            "pim_vendor": "Pimcore",
            "pim_state": "confirmed",
            "catalog_scale_bucket": "unknown",
            "catalog_estimated_range": None,
            "terminal_error": "research incomplete after 2 passes: ['catalog_scale']",
            "model_cost_usd": 0.002,
        },
        {
            "experiment_id": "live-role-isolation-v1",
            "research_provider": "deepseek",
            "extraction_provider": "openai",
            "status": "recovered",
            "execution_completed": True,
            "result_valid": True,
            "accepted_result": True,
            "profile_repairs": 0,
            "pim_vendor": "Pimcore",
            "pim_state": "confirmed",
            "catalog_scale_bucket": "very_large",
            "catalog_estimated_range": "2M+ objects",
            "terminal_error": None,
            "model_cost_usd": 0.02,
        },
    ]
    (experiments / "runs.jsonl").write_text("\n".join(json.dumps(item) for item in records))

    report_path = write_live_role_isolation_report(tmp_path, tmp_path / "role-report.md")
    report = report_path.read_text()

    assert "Live Role-Isolation Evaluation" in report
    assert "Future insight: termination decomposition is derived here" in report
    assert "Research Role Summary" in report
    assert "Extraction Role Summary" in report
    assert "| bounded_uncertainty | 1 |" in report
    assert "| openai | deepseek | failed | no | no | bounded_uncertainty | 1 |" in report


def test_termination_category_is_derived_from_corpus_facts() -> None:
    record = {
        "execution_completed": False,
        "result_valid": False,
        "accepted_result": False,
        "research_passes": 2,
        "source_failures": 0,
        "targeted_routes": ["target_pim_stack"],
        "dimension_statuses": [{"dimension": "pim_stack", "status": "missing"}],
        "terminal_error": "research incomplete after 2 passes: ['pim_stack']",
    }

    assert derived_termination_category(record) == "bounded_uncertainty"
