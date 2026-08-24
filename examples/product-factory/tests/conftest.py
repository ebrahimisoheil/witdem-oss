"""Shared deterministic test fixtures."""

from pathlib import Path

import pytest
from product_factory_app.config import Settings
from product_factory_app.persistence.store import RunStore
from product_factory_app.service import ResearchService


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep directory-based unit tests aligned with the documented marker commands."""

    unit_marker = pytest.mark.unit
    for item in items:
        if "tests/unit" in str(item.path):
            item.add_marker(unit_marker)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, max_research_passes=2, max_profile_repairs=1)


@pytest.fixture
def service(settings: Settings) -> ResearchService:
    return ResearchService(settings, RunStore(settings.data_dir), enable_telemetry=False)
