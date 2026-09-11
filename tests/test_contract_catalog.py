from copy import deepcopy
from dataclasses import replace

from witdem.analytics.contract_catalog import contract_definition_metadata, summarize_contract_definitions
from witdem.analytics.repository.analytics_repository import AnalyticsRepository
from witdem.analytics.repository.state import FilterState


def test_catalog_metadata_counts_ordering_and_copy_boundary():
    latest = {
        "contract_hash": "a", "contract_name": "A", "contract_version": "v1", "protocol_version": "1",
        "service": "review", "contract": {"name": "contract"}, "result": {"name": "result"},
        "decision": {"name": "decision"}, "product_goal": {"name": "goal"},
        "evaluations": [{"name": "quality"}], "metrics": ["tokens"], "dimensions": ["model"],
        "prompt": "PRIVATE", "response": "PRIVATE", "extra": {"secret": "PRIVATE"}, "run_count": 99,
    }
    inputs = [latest, {"contract_hash": "b", "contract_name": "B"},
              {"contract_hash": "a", "contract_name": "Older"}, {"contract_name": "Unversioned"}]
    before = deepcopy(inputs)
    expected = {key: value for key, value in latest.items() if key not in {"prompt", "response", "extra", "run_count"}}
    result = summarize_contract_definitions(inputs)
    assert result == [{**expected, "run_count": 2}, {"contract_hash": "b", "contract_name": "B", "run_count": 1}]
    assert summarize_contract_definitions(inputs, contract_hash="b") == [result[1]]
    assert summarize_contract_definitions(inputs, contract_hash="absent") == []
    assert summarize_contract_definitions([]) == []
    assert "PRIVATE" not in repr(result)
    result[0]["product_goal"]["name"] = "changed"
    copied = contract_definition_metadata(latest)
    copied["evaluations"].clear()
    assert inputs == before


def test_repository_preserves_population_before_contract_filter(monkeypatch):
    reader = object.__new__(AnalyticsRepository)
    filters = FilterState(provider="chosen", contract_hash="a", goal_status="unreported")
    definitions = {"excluded": {"contract_hash": "a", "contract_name": "Excluded"},
                   "first": {"contract_hash": "a", "contract_name": "Selected"},
                   "second": {"contract_hash": "a", "contract_name": "Older"},
                   "other": {"contract_hash": "b", "contract_name": "Other"}}

    def rows(selected, limit):
        assert selected == replace(filters, contract_hash=None)
        assert limit is None
        return [{"execution_id": key} for key in ("first", "second", "other")]

    monkeypatch.setattr(reader, "_serving_execution_rows", rows)
    monkeypatch.setattr(reader, "_serving_contracts_by_execution", lambda: definitions)
    assert reader.contract_definitions(filters) == [{"contract_hash": "a", "contract_name": "Selected", "run_count": 2}]
