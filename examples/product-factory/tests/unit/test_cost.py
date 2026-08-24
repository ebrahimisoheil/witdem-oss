from product_factory_app.experiments.cost import estimate_chat_cost, resolve_pricing_model


def test_known_model_cost_uses_input_and_output_tokens() -> None:
    cost = estimate_chat_cost(
        "openai",
        "gpt-5.4-mini",
        {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    )

    assert cost == 5.25


def test_unknown_model_cost_is_explicitly_unknown() -> None:
    assert estimate_chat_cost("unknown", "model", {"prompt_tokens": 1, "completion_tokens": 1}) is None


def test_versioned_response_model_uses_explicit_provider_alias() -> None:
    resolution = resolve_pricing_model("openai", "gpt-5.4-mini-2026-03-17")

    assert resolution.pricing_model == "gpt-5.4-mini"
    assert resolution.match == "explicit_alias"
    assert resolution.version == "2026-03-17"
    assert (
        estimate_chat_cost(
            "openai",
            "gpt-5.4-mini-2026-03-17",
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        )
        == 5.25
    )


def test_unlisted_model_suffix_is_not_guessed() -> None:
    resolution = resolve_pricing_model("openai", "gpt-5.4-mini-unlisted")

    assert resolution.pricing_model is None
    assert resolution.match == "unknown"
    assert estimate_chat_cost("openai", "gpt-5.4-mini-unlisted", {"prompt_tokens": 1, "completion_tokens": 1}) is None
