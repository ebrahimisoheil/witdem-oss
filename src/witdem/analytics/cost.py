"""Deterministic model-cost calculation from versioned pricing catalogs."""

from __future__ import annotations

import os
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
        return PricingModelResolution(normalized_provider, model, built_in_target, "explicit_alias")
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


def estimate_chat_cost(provider: str, model: str, usage: dict[str, Any]) -> float | None:
    """Measure one model call, including cache usage when the catalog supports it."""

    resolution = resolve_pricing_model(provider, model)
    entry = _resolved_entry(resolution)
    if entry is None:
        return None
    input_tokens = _usage_number(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_number(usage, "completion_tokens", "output_tokens")
    if input_tokens is None or output_tokens is None or input_tokens < 0 or output_tokens < 0:
        return None
    total = input_tokens * entry.input_per_million + output_tokens * entry.output_per_million
    cache_read = _usage_number(usage, "cache_read_tokens", "cache_read_input_tokens") or 0.0
    cache_write = _usage_number(usage, "cache_write_tokens", "cache_creation_input_tokens") or 0.0
    if cache_read < 0 or cache_write < 0:
        return None
    if cache_read and entry.cache_read_per_million is not None:
        total += cache_read * entry.cache_read_per_million
    if cache_write and entry.cache_write_per_million is not None:
        total += cache_write * entry.cache_write_per_million
    return total / 1_000_000


def cost_unavailable_reason(provider: str | None, model: str | None, usage: dict[str, Any] | None) -> str | None:
    """Explain why an observed model operation cannot be priced."""

    if not provider:
        return "missing_provider"
    if not model:
        return "missing_model"
    resolution = resolve_pricing_model(provider, model)
    if resolution.pricing_model is None:
        return "unknown_model"
    values = usage or {}
    input_tokens = _usage_number(values, "prompt_tokens", "input_tokens")
    output_tokens = _usage_number(values, "completion_tokens", "output_tokens")
    if input_tokens is None or output_tokens is None:
        return "missing_usage"
    cache_read = _usage_number(values, "cache_read_tokens", "cache_read_input_tokens") or 0.0
    cache_write = _usage_number(values, "cache_write_tokens", "cache_creation_input_tokens") or 0.0
    if min(input_tokens, output_tokens, cache_read, cache_write) < 0:
        return "invalid_usage"
    return None
