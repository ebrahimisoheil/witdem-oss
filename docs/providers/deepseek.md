# DeepSeek

**Status: experimental provider path, live-validated through Haystack Product Factory.**

Witdem has no dedicated DeepSeek client adapter. The verified path uses Haystack's OpenAI-compatible generator with `DEEPSEEK_API_KEY` and the normal [Haystack integration](../integrations/haystack.md). Direct clients can use the generic provider wrapper.

```python
from witdem_sdk.integrations.generic import instrument

call = instrument(
    existing_deepseek_call,
    operation_name="deepseek.chat",
    provider="deepseek",
    model="deepseek-v4-flash",
    observe_result=lambda response: {
        "response_model": response.model,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    },
)
```

This assumes those fields match your existing client response; adjust `observe_result` explicitly if they do not.

## Evidence and cost

Provider aliases and `deepseek-` model prefixes normalize to `deepseek`. The bundled catalog contains `deepseek-v4-flash` and `deepseek-v4-pro`; the legacy `deepseek-chat` and `deepseek-reasoner` IDs explicitly alias to V4 Flash. A different model or snapshot is unmeasured until the catalog is extended or the provider reports cost. Rates participate in the [automated, reviewable refresh](../pricing.md).

## Proof and limitations

Product Factory's `mixed-v1` and DeepSeek profiles exercise real DeepSeek calls through Haystack. There is no standalone DeepSeek tutorial or native SDK proxy in this release, so support remains experimental.
