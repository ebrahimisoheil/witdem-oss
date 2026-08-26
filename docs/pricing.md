# Pricing catalog

Witdem calculates cost only from observed usage and an exact provider/model price. Provider-reported money always wins. The bundled catalog is a versioned, reviewable snapshot—not a live billing API—and unknown dimensions remain **Not measured** instead of being guessed.

## Coverage

The generated catalog covers selected models from OpenAI, Azure OpenAI, Anthropic, DeepSeek, Mistral, Google Gemini/Vertex AI, Amazon Bedrock, and Cohere. It preserves every machine-readable price dimension published for those entries by the update registry, including when available:

- standard, batch, flex, and priority inference;
- long-context thresholds and cache-duration thresholds;
- cache reads and cache creation;
- reasoning, audio, image, and video tokens;
- regional processing uplifts;
- search and grounding queries;
- custom usage meters such as provisioned-unit seconds.

Ollama has no public per-request API tariff. Witdem therefore does not label local inference as free: record provider-reported infrastructure cost or configure compute meters in an override.

Azure OpenAI is a separate provider (`azure_openai`) because Azure and direct OpenAI prices are not interchangeable. Arbitrary Azure deployment aliases still need an explicit alias in an override.

## Catalog format

The original flat fields stay in place for compatibility. The `pricing` mapping contains the complete dimensional rates used by the richer estimator:

```yaml
schema_version: "1"
catalog_version: "2026-08-26"
currency: USD
models:
  - provider: openai
    model: gpt-4o-mini
    aliases: [gpt-4o-mini-2024-07-18]
    input_per_million: 0.15
    output_per_million: 0.60
    cache_read_per_million: 0.075
    pricing:
      input_cost_per_token: 0.00000015
      input_cost_per_token_batches: 0.000000075
      input_cost_per_token_priority: 0.00000025
      output_cost_per_token: 0.00000060
      output_cost_per_token_batches: 0.00000030
      output_cost_per_token_priority: 0.00000100
    effective_date: 2026-08-26
    source: https://platform.openai.com/pricing
```

Witdem selects a dimensional rate only when the corresponding telemetry is present. Examples include `gen_ai.request.service_tier`, `cloud.region`, `gen_ai.request.cache_duration_seconds`, `gen_ai.usage.audio.input_tokens`, and `gen_ai.usage.search_queries`. An explicitly requested tier without a matching rate is not silently priced at the standard rate.

## Custom and negotiated pricing

Set `WITDEM_PRICING_FILE` to represent negotiated rates, deployment aliases, or infrastructure allocation. Custom meters are first-class:

```yaml
models:
  - provider: ollama
    model: llama-local
    input_per_million: 0
    output_per_million: 0
    pricing:
      meters:
        gpu_seconds: 0.0008
```

Then report the observed meter through the SDK:

```python
operation.usage(
    input_tokens=420,
    output_tokens=80,
    meters={"gpu_seconds": 2.4},
)
```

For prepaid or provisioned capacity, use an allocation meter such as `provisioned_unit_seconds`; for negotiated invoices that cannot be allocated safely, emit `gen_ai.cost.usd` as provider-reported cost.

## Automated refresh

[`sources.yaml`](../src/witdem/pricing/sources.yaml) maps Witdem's canonical provider/model IDs to LiteLLM's public pricing registry and records the official provider pricing page reviewers must check. The updater copies every cost and multiplier dimension, not only text-token rates.

Refresh locally:

```bash
uv run python -m witdem.pricing.update
uv run pytest -q tests/test_pricing_catalog.py tests/test_pricing_update.py
```

Check without writing:

```bash
uv run python -m witdem.pricing.update --check
```

The scheduled GitHub workflow runs every Monday, updates the YAML snapshot, and opens or refreshes a pull request. It never mutates a running deployment and never merges pricing changes automatically. Review every diff against the official `source` URL before merging.
