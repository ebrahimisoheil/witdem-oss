from product_factory_app.config import Settings
from product_factory_app.domain.models import CompanyResearchRequest, ResearchMode
from product_factory_app.experiments.harness import OrchestrationPolicy, measurement
from product_factory_app.persistence.store import RunStore
from product_factory_app.research.sources import FixtureResearchSource
from product_factory_app.service import ResearchService


def test_measurement_persists_reproducible_application_configuration(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, max_research_passes=1)
    service = ResearchService(settings, RunStore(tmp_path), enable_telemetry=False)
    run = service.run(
        CompanyResearchRequest(
            company_name="Experiment Acme",
            research_mode=ResearchMode.FIXTURE,
        ),
        source=FixtureResearchSource("success"),
    )

    record = measurement(
        run,
        OrchestrationPolicy("single-pass", 1),
        "success",
        settings=settings,
        experiment_id="test-experiment-v1",
    )

    assert record["experiment_id"] == "test-experiment-v1"
    assert record["accepted_result"] is True
    assert record["configuration"]["policy"]["max_research_passes"] == 1
    assert record["workflow_version"] == settings.workflow_version
    assert record["total_cost_known"] is True
    assert not (tmp_path / "analytics" / "events.jsonl").exists()
