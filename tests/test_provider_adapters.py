from __future__ import annotations

from witdem.adapters.providers import normalize_provider_spans


def test_provider_adapters_run_per_operation_in_mixed_execution() -> None:
    spans = [
        {
            "span_id": "openai",
            "attributes": {
                "gen_ai.provider.name": "openai",
                "gen_ai.operation.name": "chat",
                "model": "gpt-5.4-mini",
            },
        },
        {"span_id": "anthropic", "attributes": {"gen_ai.system": "anthropic", "model": "claude-sonnet-5"}},
        {"span_id": "deepseek", "attributes": {"model": "deepseek-v4-flash"}},
        {"span_id": "mistral", "attributes": {"provider": "mistralai", "model": "mistral-small-2603"}},
        {"span_id": "unknown", "attributes": {"custom": "preserved"}},
    ]

    normalized, adapters = normalize_provider_spans(spans)

    assert adapters == ("anthropic", "deepseek", "mistral", "openai")
    assert [row["attributes"].get("witdem.provider_adapter.name") for row in normalized] == [
        "openai",
        "anthropic",
        "deepseek",
        "mistral",
        None,
    ]
    assert normalized[-1]["attributes"]["custom"] == "preserved"
    assert normalized[0]["attributes"]["witdem.operation.kind"] == "model"
