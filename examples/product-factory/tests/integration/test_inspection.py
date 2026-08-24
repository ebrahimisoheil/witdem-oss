import json

import pytest

pytest.importorskip("witdem", reason="archived contributor experiment requires the analytics package")

from product_factory_app.domain.models import CompanyResearchRequest
from product_factory_app.experiments.harness import OrchestrationPolicy, measurement
from product_factory_app.experiments.inspection import inspect_execution, validate_correlation
from product_factory_app.persistence.store import RunStore
from product_factory_app.research.sources import FixtureResearchSource
from product_factory_app.service import ResearchService


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["success", "incomplete", "recovery", "terminal"])
def test_correlation_validator_covers_completed_and_failed_runs(tmp_path, scenario) -> None:
    from product_factory_app.config import Settings

    settings = Settings(data_dir=tmp_path)
    store = RunStore(tmp_path)
    run = ResearchService(settings, store, enable_telemetry=True).run(
        CompanyResearchRequest(company_name="Correlation Commerce", scenario=scenario),
        source=FixtureResearchSource(scenario),
    )
    record = measurement(
        run,
        OrchestrationPolicy("test", settings.max_research_passes),
        scenario,
        settings=settings,
        experiment_id="correlation-test-v1",
    )
    store.append_jsonl("experiments/runs.jsonl", record)

    result = validate_correlation(tmp_path, run.manifest.execution_id)
    assert result["valid"] is True, result
    inspected = inspect_execution(tmp_path, run.manifest.execution_id)
    assert inspected["run"]["manifest"]["execution_id"] == run.manifest.execution_id
    assert all(event["execution_id"] == run.manifest.execution_id for event in inspected["semantic_events"])
    assert all(span["trace_id"] == run.manifest.trace_id for span in inspected["spans"])


@pytest.mark.integration
def test_correlation_validator_detects_mismatched_semantic_trace(tmp_path) -> None:
    from product_factory_app.config import Settings

    settings = Settings(data_dir=tmp_path)
    store = RunStore(tmp_path)
    run = ResearchService(settings, store, enable_telemetry=True).run(
        CompanyResearchRequest(company_name="Broken Correlation"),
        source=FixtureResearchSource("success"),
    )
    events_path = tmp_path / "analytics" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events[0]["trace_id"] = "0" * 32
    events_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    store.append_jsonl(
        "experiments/runs.jsonl",
        measurement(
            run,
            OrchestrationPolicy("test", settings.max_research_passes),
            "success",
            settings=settings,
            experiment_id="correlation-test-v1",
        ),
    )

    result = validate_correlation(tmp_path, run.manifest.execution_id)
    assert result["valid"] is False
    assert any("mismatched trace_id" in error for error in result["errors"])
