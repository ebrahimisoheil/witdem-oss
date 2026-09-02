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

    assert adapters == ("anthropic", "mistral", "openai")
    assert [row["attributes"].get("witdem.provider_adapter.name") for row in normalized] == [
        "openai",
        "anthropic",
        None,
        "mistral",
        None,
    ]
    assert normalized[-1]["attributes"]["custom"] == "preserved"
    assert normalized[2]["attributes"]["model"] == "deepseek-v4-flash"
    assert "provider" not in normalized[2]["attributes"]
    assert normalized[0]["attributes"]["witdem.operation.kind"] == "model"


def test_cloud_provider_aliases_match_framework_provider_names() -> None:
    spans = [
        {
            "span_id": "bedrock-generic",
            "attributes": {"gen_ai.provider.name": "aws.bedrock"},
        },
        {
            "span_id": "bedrock-haystack",
            "attributes": {"gen_ai.provider.name": "amazon_bedrock"},
        },
        {
            "span_id": "vertex-generic",
            "attributes": {"gen_ai.provider.name": "google.vertex"},
        },
        {
            "span_id": "vertex-haystack",
            "attributes": {"gen_ai.provider.name": "google"},
        },
    ]

    normalized, adapters = normalize_provider_spans(spans)

    assert adapters == ("amazon_bedrock", "google")
    assert [row["attributes"]["gen_ai.provider.name"] for row in normalized] == [
        "amazon_bedrock",
        "amazon_bedrock",
        "google",
        "google",
    ]


def test_azure_openai_remains_a_distinct_billable_provider() -> None:
    normalized, adapters = normalize_provider_spans(
        [{"attributes": {"gen_ai.provider.name": "azure.openai", "gen_ai.request.model": "gpt-4o-mini"}}]
    )

    assert adapters == ("azure_openai",)
    assert normalized[0]["attributes"]["provider"] == "azure_openai"
