from __future__ import annotations

from importlib.resources import files

import yaml

from witdem.analytics.cost import BUILT_IN_CATALOG
from witdem.pricing.update import build_catalog


def test_build_catalog_converts_registry_prices_and_preserves_unchanged_date() -> None:
    policy = {
        "schema_version": "1",
        "providers": [
            {
                "provider": "example",
                "registry_providers": ["example-registry"],
                "source": "https://example.com/pricing",
                "models": [{"model": "model-a", "registry_key": "upstream-a", "aliases": ["model-latest"]}],
            }
        ],
    }
    registry = {
        "upstream-a": {
            "litellm_provider": "example-registry",
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
            "cache_read_input_token_cost": 0.0000001,
        }
    }
    current = {
        "catalog_version": "2026-01-01",
        "models": [
            {
                "provider": "example",
                "model": "model-a",
                "aliases": ["model-latest"],
                "input_per_million": 1,
                "output_per_million": 2,
                "cache_read_per_million": 0.1,
                "effective_date": "2025-12-01",
                "source": "https://example.com/pricing",
            }
        ],
    }

    generated = build_catalog(policy, registry, current, today="2026-08-25")

    generated_model = generated["models"][0]
    assert generated_model["pricing"] == {
        "cache_read_input_token_cost": 0.0000001,
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000002,
    }
    assert generated_model["effective_date"] == "2026-08-25"
    assert generated["catalog_version"] == "2026-01-01"


def test_build_catalog_marks_only_changed_rate_with_refresh_date() -> None:
    policy = {
        "schema_version": "1",
        "providers": [
            {
                "provider": "example",
                "registry_providers": ["example-registry"],
                "source": "https://example.com/pricing",
                "models": [{"model": "model-a"}],
            }
        ],
    }
    registry = {
        "model-a": {
            "litellm_provider": "example-registry",
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000004,
        }
    }
    current = {
        "catalog_version": "2026-01-01",
        "models": [
            {
                "provider": "example",
                "model": "model-a",
                "input_per_million": 1,
                "output_per_million": 2,
                "effective_date": "2025-12-01",
                "source": "https://example.com/pricing",
            }
        ],
    }

    generated = build_catalog(policy, registry, current, today="2026-08-25")

    assert generated["models"][0]["input_per_million"] == 3
    assert generated["models"][0]["output_per_million"] == 4
    assert generated["models"][0]["effective_date"] == "2026-08-25"


def test_source_policy_and_packaged_catalog_cover_the_same_models() -> None:
    raw = yaml.safe_load(files("witdem.pricing").joinpath("sources.yaml").read_text(encoding="utf-8"))
    configured = {
        (provider["provider"], model["model"])
        for provider in raw["providers"]
        for model in provider["models"]
    }

    assert configured == set(BUILT_IN_CATALOG.entries)
