"""Generate Witdem's reviewable YAML pricing snapshot from a public registry."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml

_PRICE_FIELDS = (
    "input_per_million",
    "output_per_million",
    "cache_read_per_million",
    "cache_write_per_million",
)
_REGISTRY_FIELDS = {
    "input_per_million": "input_cost_per_token",
    "output_per_million": "output_cost_per_token",
    "cache_read_per_million": "cache_read_input_token_cost",
    "cache_write_per_million": "cache_creation_input_token_cost",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return value


def _load_registry(url: str, local_path: Path | None) -> dict[str, Any]:
    if local_path is not None:
        raw = local_path.read_text(encoding="utf-8")
    else:
        request = Request(url, headers={"User-Agent": "witdem-pricing-updater/0.2"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured HTTPS registry
            raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("pricing registry must contain a JSON object")
    return value


def _per_million(value: Any, *, key: str, model: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"registry model {model!r} requires non-negative numeric {key}")
    amount = Decimal(str(value)) * Decimal(1_000_000)
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def _comparable(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry.get(key) for key in ("aliases", *_PRICE_FIELDS, "pricing", "source") if key in entry}


def _pricing_dimensions(upstream: dict[str, Any]) -> dict[str, Any]:
    """Preserve every machine-readable price dimension from the upstream snapshot."""

    return {
        str(key): _normalized(value)
        for key, value in sorted(upstream.items())
        if ("cost" in str(key) or "multiplier" in str(key)) and value is not None
    }


def build_catalog(
    policy: dict[str, Any],
    registry: dict[str, Any],
    current: dict[str, Any],
    *,
    today: str,
) -> dict[str, Any]:
    if str(policy.get("schema_version") or "") != "1":
        raise ValueError("pricing source policy requires schema_version 1")
    providers = policy.get("providers")
    if not isinstance(providers, list):
        raise ValueError("pricing source policy requires a providers list")
    old_entries = {
        (str(item.get("provider")), str(item.get("model"))): item
        for item in current.get("models", [])
        if isinstance(item, dict)
    }
    models: list[dict[str, Any]] = []
    aliases_seen: set[tuple[str, str]] = set()
    for provider_policy in providers:
        if not isinstance(provider_policy, dict):
            raise ValueError("each provider policy must be a mapping")
        provider = str(provider_policy.get("provider") or "").strip()
        source = str(provider_policy.get("source") or "").strip()
        allowed = {str(value) for value in provider_policy.get("registry_providers", [])}
        configured_models = provider_policy.get("models")
        if not provider or not source or not isinstance(configured_models, list):
            raise ValueError("provider policies require provider, source, and models")
        for configured in configured_models:
            if not isinstance(configured, dict):
                raise ValueError(f"models for {provider} must be mappings")
            model = str(configured.get("model") or "").strip()
            registry_key = str(configured.get("registry_key") or model).strip()
            upstream = registry.get(registry_key)
            if not model or not isinstance(upstream, dict):
                raise ValueError(f"registry is missing configured model {registry_key!r}")
            upstream_provider = str(upstream.get("litellm_provider") or "")
            if allowed and upstream_provider not in allowed:
                expected = ", ".join(sorted(allowed))
                raise ValueError(
                    f"registry model {registry_key!r} belongs to {upstream_provider!r}; expected one of: {expected}"
                )
            entry: dict[str, Any] = {"provider": provider, "model": model}
            aliases = [str(value).strip() for value in configured.get("aliases", []) if str(value).strip()]
            if aliases:
                entry["aliases"] = aliases
            for alias in aliases:
                alias_key = (provider, alias)
                if alias_key in aliases_seen:
                    raise ValueError(f"duplicate generated alias: {provider}/{alias}")
                aliases_seen.add(alias_key)
            for output_key, registry_field in _REGISTRY_FIELDS.items():
                value = upstream.get(registry_field)
                if value is not None:
                    entry[output_key] = _per_million(value, key=registry_field, model=registry_key)
            if "input_per_million" not in entry or "output_per_million" not in entry:
                raise ValueError(f"registry model {registry_key!r} has no text input/output token prices")
            pricing = _pricing_dimensions(upstream)
            if pricing:
                entry["pricing"] = pricing
            entry["source"] = source
            old = old_entries.get((provider, model))
            entry["effective_date"] = (
                str(old.get("effective_date")) if old and _comparable(old) == _comparable(entry) else today
            )
            models.append(entry)
    return {
        "schema_version": "1",
        "catalog_version": str(current.get("catalog_version") or today),
        "currency": "USD",
        "models": models,
    }


def _normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def main(argv: list[str] | None = None) -> int:
    package = Path(str(files("witdem.pricing")))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=package / "sources.yaml")
    parser.add_argument("--catalog", type=Path, default=package / "catalog.yaml")
    parser.add_argument("--registry-file", type=Path)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    policy = _load_yaml(args.sources)
    current = _load_yaml(args.catalog)
    registry_url = str(policy.get("registry_url") or "")
    if not registry_url.startswith("https://") and args.registry_file is None:
        raise ValueError("pricing registry URL must use HTTPS")
    registry = _load_registry(registry_url, args.registry_file)
    generated = build_catalog(policy, registry, current, today=args.date)
    if _normalized(generated) == _normalized(current):
        print(f"Pricing catalog is current ({current.get('catalog_version')}).")
        return 0
    generated["catalog_version"] = args.date
    rendered = yaml.safe_dump(generated, sort_keys=False, allow_unicode=True)
    if args.check:
        print("Pricing catalog is stale. Run: uv run python -m witdem.pricing.update", file=sys.stderr)
        return 1
    args.catalog.write_text(rendered, encoding="utf-8")
    print(f"Updated {args.catalog} to {args.date} with {len(generated['models'])} models.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
