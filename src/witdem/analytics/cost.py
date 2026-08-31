"""Deterministic model-cost calculation from versioned pricing catalogs."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PriceEntry:
    provider: str
    model: str
    aliases: tuple[str, ...]
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float | None
    cache_write_per_million: float | None
    currency: str
    effective_date: str | None
    reference: str | None
    pricing: dict[str, Any]


@dataclass(frozen=True)
class CostEstimate:
    """A cost plus the exact dimensions used to calculate it."""

    amount_usd: float | None
    reason: str | None
    components: dict[str, float]
    tier: str
    region: str | None


@dataclass(frozen=True)
class PricingCatalog:
    schema_version: str
    catalog_version: str
    currency: str
    entries: dict[tuple[str, str], PriceEntry]
    aliases: dict[tuple[str, str], str]


@dataclass(frozen=True)
class PricingModelResolution:
    """How an observed provider model maps to the local price snapshot."""

    provider: str
    observed_model: str
    pricing_model: str | None
    match: str
    version: str | None = None
    source: str = "built-in"


def _number(entry: dict[str, Any], key: str, *, required: bool) -> float | None:
    value = entry.get(key)
    if value is None:
        if not required:
            return None
        raise ValueError(f"pricing model requires numeric {key}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"pricing model requires numeric {key}") from exc
    if parsed < 0:
        raise ValueError(f"pricing model cannot contain negative {key}")
    return parsed


def _catalog_from_mapping(raw: Any, *, require_metadata: bool) -> PricingCatalog:
    if not isinstance(raw, dict):
        raise ValueError("pricing catalog must be a YAML mapping")
    schema_version = str(raw.get("schema_version") or "")
    catalog_version = str(raw.get("catalog_version") or "")
    currency = str(raw.get("currency") or "USD").upper()
    if require_metadata and (not schema_version or not catalog_version):
        raise ValueError("pricing catalog requires schema_version and catalog_version")
    if schema_version and schema_version != "1":
        raise ValueError(f"unsupported pricing catalog schema_version: {schema_version}")
    models = raw.get("models", raw.get("prices", []))
    if not isinstance(models, list):
        raise ValueError("pricing catalog 'models' must be a list")
    entries: dict[tuple[str, str], PriceEntry] = {}
    aliases: dict[tuple[str, str], str] = {}
    for item in models:
        if not isinstance(item, dict):
            raise ValueError("each pricing model must be a mapping")
        provider = str(item.get("provider") or "").strip().casefold()
        model = str(item.get("model") or "").strip()
        if not provider or not model:
            raise ValueError("pricing models require provider and model")
        key = (provider, model)
        if key in entries:
            raise ValueError(f"duplicate pricing model: {provider}/{model}")
        model_aliases = tuple(str(alias).strip() for alias in (item.get("aliases") or ()) if str(alias).strip())
        input_price = _number(item, "input_per_million", required=True)
        output_price = _number(item, "output_per_million", required=True)
        assert input_price is not None and output_price is not None
        entry = PriceEntry(
            provider=provider,
            model=model,
            aliases=model_aliases,
            input_per_million=input_price,
            output_per_million=output_price,
            cache_read_per_million=_number(item, "cache_read_per_million", required=False),
            cache_write_per_million=_number(item, "cache_write_per_million", required=False),
            currency=str(item.get("currency") or currency).upper(),
            effective_date=str(item["effective_date"]) if item.get("effective_date") is not None else None,
            reference=str(item["source"]) if item.get("source") else None,
            pricing=dict(item.get("pricing") or {}),
        )
        if require_metadata and (entry.effective_date is None or entry.reference is None):
            raise ValueError(f"built-in pricing model requires effective_date and source: {provider}/{model}")
        if entry.currency != "USD":
            raise ValueError(f"unsupported pricing currency for {provider}/{model}: {entry.currency}")
        entries[key] = entry
        for alias in model_aliases:
            alias_key = (provider, alias)
            if alias_key in aliases or alias_key in entries:
                raise ValueError(f"duplicate pricing alias: {provider}/{alias}")
            aliases[alias_key] = model
    top_level_aliases = raw.get("aliases", [])
    if not isinstance(top_level_aliases, list):
        raise ValueError("pricing catalog 'aliases' must be a list")
    for item in top_level_aliases:
        if not isinstance(item, dict):
            raise ValueError("each pricing alias must be a mapping")
        provider = str(item.get("provider") or "").strip().casefold()
        alias = str(item.get("alias") or "").strip()
        model = str(item.get("model") or "").strip()
        if not provider or not alias or not model:
            raise ValueError("pricing aliases require provider, alias, and model")
        alias_key = (provider, alias)
        if alias_key in aliases or alias_key in entries:
            raise ValueError(f"duplicate pricing alias: {provider}/{alias}")
        aliases[alias_key] = model
    return PricingCatalog(schema_version or "1", catalog_version or "override", currency, entries, aliases)


def _read_catalog(path: Path, *, require_metadata: bool) -> PricingCatalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read pricing catalog {path}: {exc}") from exc
    return _catalog_from_mapping(raw, require_metadata=require_metadata)


_BUILT_IN_PATH = Path(str(files("witdem.pricing").joinpath("catalog.yaml")))
BUILT_IN_CATALOG = _read_catalog(_BUILT_IN_PATH, require_metadata=True)
PRICE_SNAPSHOT_VERSION = BUILT_IN_CATALOG.catalog_version
PRICING_CATALOG_VERSION = BUILT_IN_CATALOG.catalog_version

# Compatibility projections for callers that used the 0.2 preview constants.
PRICE_PER_MILLION = {
    key: (entry.input_per_million, entry.output_per_million) for key, entry in BUILT_IN_CATALOG.entries.items()
}
MODEL_PRICING_ALIASES = {key: (model, None) for key, model in BUILT_IN_CATALOG.aliases.items()}

_MODEL_DATE_VERSION = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|\d{8})(?!\d)")


def _model_version(model: str) -> str | None:
    match = _MODEL_DATE_VERSION.search(model)
    return match.group(1) if match else None


def _override_catalog() -> PricingCatalog | None:
    value = os.getenv("WITDEM_PRICING_FILE")
    return _read_catalog(Path(value).expanduser(), require_metadata=False) if value else None


def validate_pricing_override(path: Path) -> None:
    _read_catalog(path, require_metadata=False)


def resolve_pricing_model(provider: str, model: str) -> PricingModelResolution:
    """Resolve exact and aliased models in the documented precedence order."""

    normalized_provider = provider.casefold().strip()
    exact_key = (normalized_provider, model)
    override = _override_catalog()
    if override is not None:
        if exact_key in override.entries:
            return PricingModelResolution(normalized_provider, model, model, "override_exact", source="override")
        override_target = override.aliases.get(exact_key)
        if override_target is not None:
            target_key = (normalized_provider, override_target)
            if target_key in override.entries or target_key in BUILT_IN_CATALOG.entries:
                return PricingModelResolution(
                    normalized_provider,
                    model,
                    override_target,
                    "override_alias",
                    source="override",
                )
    if exact_key in BUILT_IN_CATALOG.entries:
        return PricingModelResolution(normalized_provider, model, model, "exact")
    built_in_target = BUILT_IN_CATALOG.aliases.get(exact_key)
    if built_in_target is not None:
        return PricingModelResolution(
            normalized_provider,
            model,
            built_in_target,
            "explicit_alias",
            version=_model_version(model),
        )
    provider_prefix = f"{normalized_provider}/"
    if model.casefold().startswith(provider_prefix):
        unprefixed = model[len(provider_prefix) :]
        nested = resolve_pricing_model(normalized_provider, unprefixed)
        if nested.pricing_model is not None:
            return PricingModelResolution(
                normalized_provider,
                model,
                nested.pricing_model,
                f"provider_prefixed_{nested.match}",
                version=nested.version,
                source=nested.source,
            )
    return PricingModelResolution(normalized_provider, model, None, "unknown")


def _resolved_entry(resolution: PricingModelResolution) -> PriceEntry | None:
    if resolution.pricing_model is None:
        return None
    key = (resolution.provider, resolution.pricing_model)
    override = _override_catalog()
    if override is not None and key in override.entries:
        return override.entries[key]
    return BUILT_IN_CATALOG.entries.get(key)


def _usage_number(usage: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _context_value(context: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = context.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().casefold().replace("-", "_")
    return None


def _rate(
    entry: PriceEntry,
    base: str,
    *,
    tier: str,
    input_tokens: float,
    cache_duration_seconds: float = 0,
) -> float | None:
    pricing = entry.pricing
    threshold = ""
    for token_limit in (512_000, 272_000, 256_000, 200_000, 128_000):
        if input_tokens > token_limit:
            threshold = f"_above_{token_limit // 1_000}k_tokens"
            break
    tier_suffix = {"batch": "_batches", "flex": "_flex", "priority": "_priority"}.get(tier, "")
    candidates = []
    if tier == "standard" and base == "cache_creation_input_token_cost" and cache_duration_seconds > 3_600:
        if threshold:
            candidates.append(f"{base}_above_1hr{threshold}")
        candidates.append(f"{base}_above_1hr")
    if threshold and tier_suffix:
        candidates.append(f"{base}{threshold}{tier_suffix}")
    if threshold and tier == "standard":
        candidates.append(f"{base}{threshold}")
    if tier_suffix:
        candidates.append(f"{base}{tier_suffix}")
    if tier == "standard":
        candidates.append(base)
    for key in candidates:
        value = pricing.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    if tier == "standard" and base == "cache_read_input_token_cost":
        cache_hit = pricing.get("input_cost_per_token_cache_hit")
        if isinstance(cache_hit, (int, float)) and not isinstance(cache_hit, bool) and cache_hit >= 0:
            return float(cache_hit)
    if tier != "standard":
        return None
    fallback = {
        "input_cost_per_token": entry.input_per_million / 1_000_000,
        "output_cost_per_token": entry.output_per_million / 1_000_000,
        "cache_read_input_token_cost": (
            None if entry.cache_read_per_million is None else entry.cache_read_per_million / 1_000_000
        ),
        "cache_creation_input_token_cost": (
            None if entry.cache_write_per_million is None else entry.cache_write_per_million / 1_000_000
        ),
    }.get(base)
    return fallback


def _meter(usage: dict[str, Any], *keys: str) -> float:
    return _usage_number(usage, *keys) or 0.0


_ADDITIONAL_METERS: dict[str, tuple[str, float]] = {
    "input_characters": ("input_cost_per_character", 1.0),
    "output_characters": ("output_cost_per_character", 1.0),
    "input_images": ("input_cost_per_image", 1.0),
    "output_images": ("output_cost_per_image", 1.0),
    "input_pixels": ("input_cost_per_pixel", 1.0),
    "output_pixels": ("output_cost_per_pixel", 1.0),
    "input_seconds": ("input_cost_per_second", 1.0),
    "output_seconds": ("output_cost_per_second", 1.0),
    "audio_input_seconds": ("input_cost_per_audio_per_second", 1.0),
    "video_input_seconds": ("input_cost_per_video_per_second", 1.0),
    "video_output_seconds": ("output_cost_per_video_per_second", 1.0),
    "input_requests": ("input_cost_per_request", 1.0),
    "input_queries": ("input_cost_per_query", 1.0),
    "annotation_pages": ("annotation_cost_per_page", 1.0),
    "ocr_pages": ("ocr_cost_per_page", 1.0),
    "ocr_credits": ("ocr_cost_per_credit", 1.0),
    "citation_tokens": ("citation_cost_per_token", 1.0),
    "code_interpreter_sessions": ("code_interpreter_cost_per_session", 1.0),
    "file_search_calls": ("file_search_cost_per_1k_calls", 1_000.0),
    "file_search_gb_days": ("file_search_cost_per_gb_per_day", 1.0),
    "vector_store_gb_days": ("vector_store_cost_per_gb_per_day", 1.0),
    "guardrail_units": ("guardrail_cost_per_unit", 1.0),
    "computer_use_input_tokens": ("computer_use_input_cost_per_1k_tokens", 1_000.0),
    "computer_use_output_tokens": ("computer_use_output_cost_per_1k_tokens", 1_000.0),
    "input_dbu_tokens": ("input_dbu_cost_per_token", 1.0),
    "output_dbu_tokens": ("output_dbu_cost_per_token", 1.0),
}


def estimate_chat_cost_details(
    provider: str,
    model: str,
    usage: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> CostEstimate:
    """Price every observable billable dimension without inventing missing data."""

    resolution = resolve_pricing_model(provider, model)
    entry = _resolved_entry(resolution)
    if entry is None:
        return CostEstimate(None, "unknown_model", {}, "standard", None)
    input_tokens = _usage_number(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_number(usage, "completion_tokens", "output_tokens")
    has_non_text_meter = any(_meter(usage, meter) for meter in _ADDITIONAL_METERS) or bool(
        _meter(usage, "search_queries", "web_search_queries", "grounding_queries")
    )
    if has_non_text_meter:
        input_tokens = input_tokens or 0.0
        output_tokens = output_tokens or 0.0
    if input_tokens is not None and input_tokens < 0 or output_tokens is not None and output_tokens < 0:
        return CostEstimate(None, "invalid_usage", {}, "standard", None)
    if input_tokens is None or output_tokens is None:
        return CostEstimate(None, "missing_usage", {}, "standard", None)
    dimensions = context or {}
    tier = (
        _context_value(
            dimensions,
            "gen_ai.request.service_tier",
            "service_tier",
            "inference_tier",
            "tier",
        )
        or "standard"
    )
    tier = {"default": "standard", "auto": "standard"}.get(tier, tier)
    tier = {"batches": "batch"}.get(tier, tier)
    if tier not in {"standard", "batch", "flex", "priority"}:
        return CostEstimate(None, "unsupported_pricing_tier", {}, tier, None)
    region = _context_value(dimensions, "cloud.region", "gen_ai.request.region", "region")
    cache_duration_seconds = (
        _usage_number(
            dimensions,
            "gen_ai.request.cache_duration_seconds",
            "cache_duration_seconds",
        )
        or 0.0
    )
    rates = {
        "input_tokens": _rate(entry, "input_cost_per_token", tier=tier, input_tokens=input_tokens),
        "output_tokens": _rate(entry, "output_cost_per_token", tier=tier, input_tokens=input_tokens),
        "cache_read_tokens": _rate(entry, "cache_read_input_token_cost", tier=tier, input_tokens=input_tokens),
        "cache_write_tokens": _rate(
            entry,
            "cache_creation_input_token_cost",
            tier=tier,
            input_tokens=input_tokens,
            cache_duration_seconds=cache_duration_seconds,
        ),
        "reasoning_tokens": _rate(entry, "output_cost_per_reasoning_token", tier=tier, input_tokens=input_tokens),
        "audio_input_tokens": _rate(entry, "input_cost_per_audio_token", tier=tier, input_tokens=input_tokens),
        "audio_output_tokens": _rate(entry, "output_cost_per_audio_token", tier=tier, input_tokens=input_tokens),
        "image_input_tokens": _rate(entry, "input_cost_per_image_token", tier=tier, input_tokens=input_tokens),
        "image_output_tokens": _rate(entry, "output_cost_per_image_token", tier=tier, input_tokens=input_tokens),
        "video_input_tokens": _rate(entry, "input_cost_per_video_token", tier=tier, input_tokens=input_tokens),
        "video_output_tokens": _rate(entry, "output_cost_per_video_token", tier=tier, input_tokens=input_tokens),
    }
    quantities = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": _meter(usage, "cache_read_tokens", "cache_read_input_tokens"),
        "cache_write_tokens": _meter(usage, "cache_write_tokens", "cache_creation_input_tokens"),
        "reasoning_tokens": _meter(
            usage,
            "reasoning_tokens",
            "output_reasoning_tokens",
            "reasoning_output_tokens",
        ),
        "audio_input_tokens": _meter(usage, "audio_input_tokens", "input_audio_tokens"),
        "audio_output_tokens": _meter(usage, "audio_output_tokens", "output_audio_tokens"),
        "image_input_tokens": _meter(usage, "image_input_tokens", "input_image_tokens"),
        "image_output_tokens": _meter(usage, "image_output_tokens", "output_image_tokens"),
        "video_input_tokens": _meter(usage, "video_input_tokens", "input_video_tokens"),
        "video_output_tokens": _meter(usage, "video_output_tokens", "output_video_tokens"),
    }
    for meter, (base, divisor) in _ADDITIONAL_METERS.items():
        quantity = _meter(usage, meter)
        if not quantity:
            continue
        quantities[meter] = quantity
        rate = _rate(entry, base, tier=tier, input_tokens=input_tokens)
        rates[meter] = None if rate is None else rate / divisor
    reasoning_tokens = quantities["reasoning_tokens"]
    if reasoning_tokens and rates["reasoning_tokens"] is not None:
        includes_reasoning = dimensions.get("gen_ai.usage.output_tokens_include_reasoning", True)
        if includes_reasoning:
            quantities["output_tokens"] = max(0.0, quantities["output_tokens"] - reasoning_tokens)
    cache_tokens = quantities["cache_read_tokens"]
    includes_cache = dimensions.get("gen_ai.usage.input_tokens_include_cache")
    if includes_cache is None:
        includes_cache = entry.provider in {"openai", "deepseek", "google"}
    if cache_tokens and includes_cache:
        quantities["input_tokens"] = max(0.0, quantities["input_tokens"] - cache_tokens)
    if any(value < 0 for value in quantities.values()):
        return CostEstimate(None, "invalid_usage", {}, tier, region)
    missing = [name for name, quantity in quantities.items() if quantity and rates[name] is None]
    if missing:
        return CostEstimate(None, f"unsupported_pricing_dimension:{missing[0]}", {}, tier, region)
    components: dict[str, float] = {}
    for name, quantity in quantities.items():
        rate = rates[name]
        if quantity and rate is not None:
            components[name] = quantity * rate
    search_queries = _meter(usage, "search_queries", "web_search_queries", "grounding_queries")
    if search_queries:
        search_rate = entry.pricing.get("search_context_cost_per_query")
        if isinstance(search_rate, dict):
            size = _context_value(dimensions, "search_context_size", "gen_ai.request.search_context_size") or "medium"
            search_rate = search_rate.get(size, search_rate.get(f"search_context_size_{size}"))
        if not isinstance(search_rate, (int, float)) or isinstance(search_rate, bool):
            return CostEstimate(None, "unsupported_pricing_dimension:search_queries", {}, tier, region)
        components["search_queries"] = search_queries * float(search_rate)
    known_usage = set(quantities) | {
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_reasoning_tokens",
        "reasoning_output_tokens",
        "input_audio_tokens",
        "output_audio_tokens",
        "input_image_tokens",
        "output_image_tokens",
        "input_video_tokens",
        "output_video_tokens",
        "web_search_queries",
        "grounding_queries",
        "total_tokens",
    }
    custom_rates = entry.pricing.get("meters")
    if custom_rates is not None and not isinstance(custom_rates, dict):
        return CostEstimate(None, "invalid_pricing_dimension:meters", {}, tier, region)
    for meter, raw_quantity in usage.items():
        if meter in known_usage or meter in {"search_queries"}:
            continue
        if not isinstance(raw_quantity, (int, float)) or isinstance(raw_quantity, bool) or not raw_quantity:
            continue
        rate = custom_rates.get(meter) if isinstance(custom_rates, dict) else None
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            return CostEstimate(None, f"unsupported_pricing_dimension:{meter}", {}, tier, region)
        components[meter] = float(raw_quantity) * float(rate)
    if region:
        region_code = region.split("_")[0]
        multiplier = entry.pricing.get(f"regional_processing_uplift_multiplier_{region_code}")
        if isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool):
            components["regional_uplift"] = sum(components.values()) * (float(multiplier) - 1.0)
        endpoint_multiplier = entry.pricing.get("regional_endpoint_uplift_multiplier")
        if isinstance(endpoint_multiplier, (int, float)) and not isinstance(endpoint_multiplier, bool):
            components["regional_endpoint_uplift"] = sum(components.values()) * (float(endpoint_multiplier) - 1.0)
    return CostEstimate(round(sum(components.values()), 15), None, components, tier, region)


def estimate_chat_cost(
    provider: str,
    model: str,
    usage: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> float | None:
    """Compatibility API returning the total USD cost when every used dimension is priced."""

    return estimate_chat_cost_details(provider, model, usage, context=context).amount_usd


def cost_unavailable_reason(
    provider: str | None,
    model: str | None,
    usage: dict[str, Any] | None,
    *,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Explain why an observed model operation cannot be priced."""

    if not provider:
        return "missing_provider"
    if not model:
        return "missing_model"
    resolution = resolve_pricing_model(provider, model)
    if resolution.pricing_model is None:
        return "unknown_model"
    values = usage or {}
    return estimate_chat_cost_details(provider, model, values, context=context).reason
