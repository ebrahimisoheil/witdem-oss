# Provider support

Provider support is not one binary capability. Witdem can observe a provider through a dedicated SDK adapter, through a framework such as Haystack, or through standard OpenTelemetry/the generic callable wrapper.

## Support matrix

| Provider | Current path | Evidence | Cost behavior | Status |
| --- | --- | --- | --- | --- |
| [OpenAI](providers/openai.md) | OpenAI Agents native processor; LangChain/LangGraph/Haystack callbacks; generic wrapper | Unit/integration tests, tutorials, live Product Factory and external Chinook runs | Catalog entries for listed OpenAI models; provider money accepted | Beta native support |
| [Anthropic](providers/anthropic.md) | Messages client proxy; Claude Agent observer; framework callbacks | Unit/integration tests, tutorials, live Product Factory and external Chinook runs | Catalog entries for listed Claude models; cache tokens supported | Beta native support |
| [DeepSeek](providers/deepseek.md) | Haystack OpenAI-compatible generator or generic wrapper | Live Product Factory matrix | Catalog entries for `deepseek-v4-flash` and `deepseek-v4-pro` | Experimental provider path |
| [Mistral](providers/mistral.md) | Haystack Mistral generator or generic wrapper | Live Product Factory matrix | Catalog entries for `mistral-small-2603` and `mistral-medium-3-5` | Experimental provider path |
| [Azure OpenAI](providers/azure-openai.md) | Generic wrapper or GenAI OTLP; recognized by Haystack | Standalone tutorial and tests | Separate `azure_openai` catalog; deployment aliases supported by override | Experimental provider path |
| [Amazon Bedrock](providers/bedrock.md) | Generic wrapper or GenAI OTLP; recognized by Haystack | Standalone tutorial and tests | Bundled standard rates for selected Nova, Cohere, and Mistral model IDs | Experimental provider path |
| [Google Vertex AI](providers/vertex-ai.md) | Generic wrapper or GenAI OTLP; Google/Gemini recognized by Haystack | Standalone tutorial and tests | Bundled standard rates for selected Gemini IDs | Experimental provider path |
| [Ollama](providers/ollama.md) | Generic wrapper or GenAI OTLP | Standalone local tutorial and tests | Normally not measured as API spend; usage can still be recorded | Experimental provider path |
| [Cohere](providers/cohere.md) | Recognized from Haystack component identity/metadata; generic wrapper | Structural Haystack adapter tests only; no standalone tutorial | Bundled standard rates for selected Command models | Experimental, not live-validated |
| [OpenRouter](providers/openrouter.md) | Native OpenAI-compatible proxy or LiteLLM callback | Unit tests plus direct response/stream normalization | Provider-reported charged cost is authoritative; upstream cost retained separately | Beta native enrichment |
| Hugging Face inference | smolagents OpenInference, LiteLLM, or generic OTLP | Official instrumentor conformance and normalization tests | Provider money when exposed; custom endpoints otherwise remain unmeasured | Beta framework path |
| Any OTel GenAI provider | Standard OTLP/HTTP attributes | Generic protocol and normalization tests | Provider-reported money or custom catalog | Protocol support, not a native adapter |

“Recognized by Haystack” means the integration can associate provider/model/usage metadata with the actual native generator span. It does not mean Witdem bundles that provider's client library or credentials.

Provider aliases are normalized before analytics. For example, `azure.openai` groups with `azure_openai`, `aws.bedrock` groups with `amazon_bedrock`, `google.vertex` groups with `google`, and `mistralai` groups with `mistral`. The originally observed value is retained as provenance when it differs from the canonical name.

## Evidence required for a measured model call

For provider/model comparisons, emit or expose:

```text
gen_ai.provider.name
gen_ai.request.model or gen_ai.response.model
gen_ai.usage.input_tokens
gen_ai.usage.output_tokens
```

Provider-reported cost can be supplied as `gen_ai.cost.usd` or through a native/generic SDK operation. Without reported money, the model must match [`src/witdem/pricing/catalog.yaml`](https://github.com/ebrahimisoheil/witdem-oss/blob/main/src/witdem/pricing/catalog.yaml). Override that catalog with `WITDEM_PRICING_FILE` when your deployment uses other models or negotiated rates. See [Pricing catalog](pricing.md) for dimensional rates, custom meters, and automated refresh.

## Generic provider integration

Use this for an existing provider function whose result exposes conventional metadata:

```python
from witdem_sdk.integrations.generic import instrument

call = instrument(
    call_provider,
    operation_name="provider.generate",
    provider="provider-name",
    model="model-name",
)
response = call(prompt)
```

If its response fields differ, supply `observe_result=`:

```python
call = instrument(
    call_provider,
    operation_name="provider.generate",
    provider="provider-name",
    model="model-name",
    observe_result=lambda response: {
        "response_model": response.model_id,
        "input_tokens": response.usage.input,
        "output_tokens": response.usage.output,
        "cost_usd": response.billed_usd,
        "cost_source": "provider_reported",
    },
)
```

This maps fields; it does not fabricate calls, token counts, or prices.
