"""Provider price snapshots used only to measure experiment executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Snapshot consulted 2026-08-20 from the providers' public pricing documentation.
# This identifier is persisted with calculated operation cost so a later price
# revision never looks like a historical measurement change.
PRICE_SNAPSHOT_VERSION = "2026-08-20"
# DeepSeek values use cache-miss input pricing because the current usage contract does not
# distinguish cache hits. Unknown model IDs remain unknown rather than fabricated.
PRICE_PER_MILLION: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-5.4-mini"): (0.75, 4.50),
    ("openai", "gpt-5.4-nano"): (0.20, 1.25),
    ("mistral", "mistral-small-2603"): (0.15, 0.60),
    ("mistral", "mistral-medium-3-5"): (1.50, 7.50),
    ("deepseek", "deepseek-v4-flash"): (0.14, 0.28),
    ("deepseek", "deepseek-v4-pro"): (0.435, 0.87),
}

# Provider response model IDs can carry a dated snapshot suffix even when the
# configured/priced model is the stable family name. Keep this mapping explicit
# and provider-scoped: an unlisted model remains unknown rather than being
# guessed by stripping arbitrary suffixes.
MODEL_PRICING_ALIASES: dict[tuple[str, str], tuple[str, str | None]] = {
    ("openai", "gpt-5.4-mini-2026-03-17"): ("gpt-5.4-mini", "2026-03-17"),
}


@dataclass(frozen=True)
class PricingModelResolution:
    """How an observed provider model maps to the local price snapshot."""

    provider: str
    observed_model: str
    pricing_model: str | None
    match: str
    version: str | None = None


def resolve_pricing_model(provider: str, model: str) -> PricingModelResolution:
    """Resolve an observed model to an exact or explicitly listed price key."""

    exact_key = (provider, model)
    if exact_key in PRICE_PER_MILLION:
        return PricingModelResolution(
            provider=provider,
            observed_model=model,
            pricing_model=model,
            match="exact",
        )
    alias = MODEL_PRICING_ALIASES.get(exact_key)
    if alias is not None and (provider, alias[0]) in PRICE_PER_MILLION:
        return PricingModelResolution(
            provider=provider,
            observed_model=model,
            pricing_model=alias[0],
            match="explicit_alias",
            version=alias[1],
        )
    return PricingModelResolution(
        provider=provider,
        observed_model=model,
        pricing_model=None,
        match="unknown",
    )


def estimate_chat_cost(provider: str, model: str, usage: dict[str, Any]) -> float | None:
    """Estimate one chat call cost from provider usage metadata."""

    resolution = resolve_pricing_model(provider, model)
    prices = PRICE_PER_MILLION.get((provider, resolution.pricing_model)) if resolution.pricing_model else None
    if prices is None:
        return None
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    if not isinstance(input_tokens, (int, float)) or not isinstance(output_tokens, (int, float)):
        return None
    input_price, output_price = prices
    return (float(input_tokens) * input_price + float(output_tokens) * output_price) / 1_000_000
