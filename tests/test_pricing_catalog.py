from __future__ import annotations

from pathlib import Path

import pytest

from witdem.analytics.cost import (
    BUILT_IN_CATALOG,
    PRICING_CATALOG_VERSION,
    cost_unavailable_reason,
    estimate_chat_cost,
    estimate_chat_cost_details,
    resolve_pricing_model,
    validate_pricing_override,
)


def test_packaged_catalog_has_versioned_explainable_entries() -> None:
    assert BUILT_IN_CATALOG.schema_version == "1"
    assert BUILT_IN_CATALOG.catalog_version == PRICING_CATALOG_VERSION
    assert BUILT_IN_CATALOG.entries
    assert all(entry.currency == "USD" for entry in BUILT_IN_CATALOG.entries.values())
    assert all(entry.effective_date and entry.reference for entry in BUILT_IN_CATALOG.entries.values())
    assert len(BUILT_IN_CATALOG.entries) >= 50


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-4.1-mini"),
        ("anthropic", "claude-opus-4-6"),
        ("deepseek", "deepseek-v4-flash"),
        ("mistral", "mistral-large-3"),
        ("google", "gemini-2.5-flash"),
        ("amazon_bedrock", "amazon.nova-lite-v1:0"),
        ("cohere", "command-r"),
        ("azure_openai", "gpt-4o-mini"),
    ],
)
def test_supported_provider_catalog_coverage(provider: str, model: str) -> None:
    assert resolve_pricing_model(provider, model).pricing_model == model
    assert estimate_chat_cost(provider, model, {"input_tokens": 100, "output_tokens": 10}) is not None


def test_catalog_alias_and_cache_prices_are_deterministic() -> None:
    resolution = resolve_pricing_model("anthropic", "claude-haiku-4-5-20251001")
    assert resolution.pricing_model == "claude-haiku-4-5"
    assert resolution.match == "explicit_alias"
    assert resolution.version == "20251001"
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


def test_catalog_resolves_litellm_provider_prefixed_model() -> None:
    resolution = resolve_pricing_model("openai", "openai/gpt-5.4-mini")

    assert resolution.pricing_model == "gpt-5.4-mini"
    assert resolution.match == "provider_prefixed_exact"


def test_gemini_normalized_reasoning_usage_is_priced_once() -> None:
    result = estimate_chat_cost_details(
        "google",
        "gemini-3.6-flash",
        {
            "input_tokens": 280,
            "output_tokens": 97,
            "total_tokens": 377,
            "reasoning_tokens": 86,
            # The normalized operation also retains this exact OTel-derived
            # spelling. It is an alias, not an additional billed meter.
            "reasoning_output_tokens": 86,
        },
    )

    assert result.reason is None
    assert result.amount_usd == pytest.approx(0.00057375)
    assert result.components == {
        "input_tokens": pytest.approx(0.00021),
        "output_tokens": pytest.approx(0.00004125),
        "reasoning_tokens": pytest.approx(0.0003225),
    }


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
        {"input_tokens": 100_000, "output_tokens": 100_000},
    ) == pytest.approx((input_price + output_price) / 10)


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


def test_tier_region_search_and_custom_meter_pricing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    override = tmp_path / "pricing.yaml"
    override.write_text(
        """
models:
  - provider: example
    model: multimodal
    input_per_million: 1
    output_per_million: 2
    pricing:
      input_cost_per_token: 0.000001
      output_cost_per_token: 0.000002
      input_cost_per_token_priority: 0.000003
      output_cost_per_token_priority: 0.000004
      input_cost_per_audio_token_priority: 0.000005
      output_cost_per_image: 0.04
      regional_processing_uplift_multiplier_eu: 1.1
      search_context_cost_per_query:
        search_context_size_high: 0.025
      meters:
        provisioned_unit_seconds: 0.01
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WITDEM_PRICING_FILE", str(override))

    result = estimate_chat_cost_details(
        "example",
        "multimodal",
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "audio_input_tokens": 2,
            "search_queries": 1,
            "provisioned_unit_seconds": 3,
        },
        context={"service_tier": "priority", "cloud.region": "eu-west-1", "search_context_size": "high"},
    )

    assert result.reason is None
    assert result.tier == "priority"
    assert result.region == "eu_west_1"
    assert result.components["search_queries"] == 0.025
    assert result.components["provisioned_unit_seconds"] == 0.03
    assert result.components["regional_uplift"] > 0

    image_result = estimate_chat_cost_details(
        "example",
        "multimodal",
        {"output_images": 2},
    )
    assert image_result.amount_usd == 0.08


def test_explicit_unpriced_tier_is_not_guessed() -> None:
    result = estimate_chat_cost_details(
        "cohere",
        "command-r",
        {"input_tokens": 10, "output_tokens": 5},
        context={"service_tier": "priority"},
    )
    assert result.amount_usd is None
    assert result.reason == "unsupported_pricing_dimension:input_tokens"
