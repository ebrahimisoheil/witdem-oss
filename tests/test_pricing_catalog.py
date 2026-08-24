from __future__ import annotations

from pathlib import Path

import pytest

from witdem.analytics.cost import (
    BUILT_IN_CATALOG,
    PRICING_CATALOG_VERSION,
    cost_unavailable_reason,
    estimate_chat_cost,
    resolve_pricing_model,
    validate_pricing_override,
)


def test_packaged_catalog_has_versioned_explainable_entries() -> None:
    assert BUILT_IN_CATALOG.schema_version == "1"
    assert BUILT_IN_CATALOG.catalog_version == PRICING_CATALOG_VERSION
    assert BUILT_IN_CATALOG.entries
    assert all(entry.currency == "USD" for entry in BUILT_IN_CATALOG.entries.values())
    assert all(entry.effective_date and entry.reference for entry in BUILT_IN_CATALOG.entries.values())


def test_catalog_alias_and_cache_prices_are_deterministic() -> None:
    resolution = resolve_pricing_model("anthropic", "claude-haiku-4-5-20251001")
    assert resolution.pricing_model == "claude-haiku-4-5"
    assert resolution.match == "explicit_alias"
    cost = estimate_chat_cost(
        "anthropic",
        "claude-haiku-4-5-20251001",
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        },
    )
    assert cost == pytest.approx((100 * 1 + 20 * 5 + 50 * 0.1 + 10 * 1.25) / 1_000_000)


@pytest.mark.parametrize(
    ("provider", "model", "input_price", "output_price"),
    [
        ("openai", "gpt-5.6-sol", 4.0, 20.0),
        ("openai", "gpt-5.6-terra", 2.0, 12.0),
        ("openai", "gpt-5.6-luna", 0.2, 1.2),
        ("anthropic", "claude-sonnet-4-6", 3.0, 15.0),
    ],
)
def test_chinook_models_have_current_exact_pricing(
    provider: str,
    model: str,
    input_price: float,
    output_price: float,
) -> None:
    resolution = resolve_pricing_model(provider, model)
    assert resolution.pricing_model == model
    assert resolution.match == "exact"
    assert estimate_chat_cost(
        provider,
        model,
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    ) == pytest.approx(input_price + output_price)


def test_override_exact_precedes_built_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "pricing.yaml"
    override.write_text(
        """
models:
  - provider: openai
    model: gpt-4o-mini
    input_per_million: 10
    output_per_million: 20
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WITDEM_PRICING_FILE", str(override))
    resolution = resolve_pricing_model("openai", "gpt-4o-mini")
    assert resolution.source == "override"
    assert estimate_chat_cost("openai", "gpt-4o-mini", {"input_tokens": 1, "output_tokens": 1}) == 30 / 1_000_000


def test_override_alias_can_target_built_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "pricing.yaml"
    override.write_text(
        """
aliases:
  - provider: openai
    alias: company-mini
    model: gpt-4o-mini
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WITDEM_PRICING_FILE", str(override))
    resolution = resolve_pricing_model("openai", "company-mini")
    assert resolution.match == "override_alias"
    assert resolution.pricing_model == "gpt-4o-mini"
    assert estimate_chat_cost("openai", "company-mini", {"input_tokens": 100, "output_tokens": 10}) is not None


@pytest.mark.parametrize(
    ("provider", "model", "usage", "reason"),
    [
        (None, "gpt-4o-mini", {}, "missing_provider"),
        ("openai", None, {}, "missing_model"),
        ("openai", "not-listed", {}, "unknown_model"),
        ("openai", "gpt-4o-mini", {}, "missing_usage"),
        ("openai", "gpt-4o-mini", {"input_tokens": -1, "output_tokens": 2}, "invalid_usage"),
    ],
)
def test_unavailable_reasons_are_specific(
    provider: str | None,
    model: str | None,
    usage: dict[str, int],
    reason: str,
) -> None:
    assert cost_unavailable_reason(provider, model, usage) == reason


def test_invalid_override_is_rejected(tmp_path: Path) -> None:
    override = tmp_path / "pricing.yaml"
    override.write_text("models: [{provider: openai, model: bad, input_per_million: -1}]", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_pricing_override(override)
