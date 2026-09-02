"""Provider normalization executed after landing and before runtime adaptation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from witdem import __version__

_PROVIDER_ALIASES = {
    "openai": "openai",
    "azure_openai": "azure_openai",
    "azure.openai": "azure_openai",
    "azure": "azure_openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "mistralai": "mistral",
    "amazon_bedrock": "amazon_bedrock",
    "aws.bedrock": "amazon_bedrock",
    "bedrock": "amazon_bedrock",
    "google": "google",
    "google.vertex": "google",
    "vertex": "google",
    "vertex_ai": "google",
    "gemini": "google",
    "cohere": "cohere",
    "ollama": "ollama",
}


def _first(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attributes.get(key)
        if value is not None and str(value):
            return value
    return None


def _provider(attributes: Mapping[str, Any]) -> tuple[str | None, str | None]:
    observed = _first(attributes, "gen_ai.provider.name", "gen_ai.system", "provider", "llm.provider")
    if observed is not None:
        canonical = _PROVIDER_ALIASES.get(str(observed).strip().casefold().replace("-", "_"))
        return canonical or str(observed).strip().casefold(), "observed_attribute"
    return None, None


def normalize_provider_spans(
    spans: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Normalize provider evidence per span without choosing one run-wide provider."""

    normalized: list[dict[str, Any]] = []
    adapters: set[str] = set()
    for raw in spans:
        row = dict(raw)
        attributes_value = row.get("attributes")
        attributes = dict(attributes_value) if isinstance(attributes_value, Mapping) else {}
        provider, source = _provider(attributes)
        if provider is not None:
            adapters.add(provider)
            observed_provider = _first(
                attributes,
                "gen_ai.provider.name",
                "gen_ai.system",
                "provider",
                "llm.provider",
            )
            if observed_provider is not None and str(observed_provider).strip().casefold() != provider:
                attributes["witdem.provider_adapter.observed"] = str(observed_provider)
            attributes["provider"] = provider
            attributes["gen_ai.provider.name"] = provider
            attributes["witdem.provider_adapter.name"] = provider
            attributes["witdem.provider_adapter.version"] = __version__
            attributes["witdem.provider_adapter.source"] = source
            model = _first(
                attributes,
                "gen_ai.response.model",
                "gen_ai.request.model",
                "model",
                "llm.model_name",
            )
            gen_ai_operation = str(attributes.get("gen_ai.operation.name") or "").casefold()
            has_usage = any(str(key).startswith("gen_ai.usage.") for key in attributes)
            if model is not None and (gen_ai_operation or has_usage):
                # Framework spans often use a generic name such as "Component".
                # The provider adapter has stronger evidence than that name and
                # records the canonical kind for the runtime adapter to consume.
                attributes["witdem.operation.kind"] = "model"
        row["attributes"] = attributes
        normalized.append(row)
    return normalized, tuple(sorted(adapters))
