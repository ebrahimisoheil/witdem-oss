import json
from datetime import datetime

import pytest
from product_factory_app.domain.models import CompanyResearchRequest
from product_factory_app.persistence.store import RunStore
from product_factory_app.research.sources import FixtureResearchSource
from product_factory_app.service import ResearchService


@pytest.mark.integration
def test_runtime_spans_and_semantic_events_correlate(tmp_path) -> None:
    from product_factory_app.config import Settings

    settings = Settings(data_dir=tmp_path)
    service = ResearchService(settings, RunStore(tmp_path), enable_telemetry=True)
    results = [
        service.run(
            CompanyResearchRequest(company_name=f"Telemetry {scenario}", scenario=scenario),
            source=FixtureResearchSource(scenario),
        )
        for scenario in ("success", "recovery", "terminal")
    ]
    spans = [json.loads(line) for line in (tmp_path / "telemetry" / "spans.jsonl").read_text().splitlines()]
    events = [json.loads(line) for line in (tmp_path / "analytics" / "events.jsonl").read_text().splitlines()]
    for result in results:
        assert result.manifest.trace_id
        if result.acceptance and result.acceptance.status.value == "accepted":
            assert result.manifest.accepted_at is not None
        matching_spans = [span for span in spans if span["trace_id"] == result.manifest.trace_id]
        assert matching_spans
        assert all(span["artifact_type"] == "otel.span" for span in matching_spans)
        assert all(span["schema_version"] == "0.1.0" for span in matching_spans)
        assert all("attributes" in span and "events" in span for span in matching_spans)
        assert any(event["execution_id"] == result.manifest.execution_id for event in events)
        assert any(event["trace_id"] == result.manifest.trace_id for event in events)
        matching_events = [event for event in events if event["execution_id"] == result.manifest.execution_id]
        assert all(event["event_id"] for event in matching_events)
        assert all(event["schema_version"] == "0.2.0" for event in matching_events)
        assert all(
            span["attributes"].get("witdem.execution_id") == result.manifest.execution_id
            for span in matching_spans
        )

        if result.acceptance and result.acceptance.status.value == "accepted":
            acceptance_events = [event for event in matching_events if event["type"] == "acceptance"]
            assert len(acceptance_events) == 1
            assert acceptance_events[0]["payload"]["accepted_at"] == result.manifest.accepted_at.isoformat()
            accepted_at = datetime.fromisoformat(result.manifest.accepted_at.isoformat())
            started_at = datetime.fromisoformat(result.manifest.started_at.isoformat())
            assert (
                abs(result.metrics["time_to_acceptance_seconds"] - (accepted_at - started_at).total_seconds()) < 0.001
            )

        if result.manifest.status.value == "recovered":
            recovery_events = [event for event in matching_events if event["type"] == "recovery"]
            assert len(recovery_events) == 1
            assert recovery_events[0]["payload"]["status"] == "recovered"
