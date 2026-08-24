import pytest
from product_factory_app.config import ModelSettings, Settings
from pydantic import ValidationError


def test_provider_configuration_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        ModelSettings(provider="unsupported", model="example")


def test_acceptance_threshold_is_centralized_and_bounded() -> None:
    settings = Settings(acceptance_min_quality_score=0.8)

    assert settings.acceptance_min_quality_score == 0.8
    with pytest.raises(ValidationError):
        Settings(acceptance_min_quality_score=1.1)


def test_known_pim_vendors_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PF_KNOWN_PIM_VENDORS", "Akeneo,Custom PIM, Salsify ")

    settings = Settings.from_env(env_file=None)

    assert settings.known_pim_vendors == ["Akeneo", "Custom PIM", "Salsify"]
