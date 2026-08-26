# Mistral

**Status: experimental provider path, live-validated through Haystack Product Factory.**

Witdem has no dedicated Mistral client adapter. The verified path uses `MistralChatGenerator` inside Haystack and the normal [Haystack integration](../integrations/haystack.md). Direct clients can use the generic provider wrapper.

```python
from witdem_sdk.integrations.generic import instrument

call = instrument(
    existing_mistral_call,
    operation_name="mistral.chat",
    provider="mistral",
    model="mistral-small-2603",
    observe_result=lambda response: {
        "response_model": response.model,
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    },
)
```

Adjust the observer to the actual client response shape.

## Evidence and cost

`mistral`, `mistralai`, and common Mistral model prefixes normalize to `mistral`. The bundled catalog includes selected Mistral Small, Medium, Large, Ministral, and Codestral model IDs. See [Pricing catalog](../pricing.md) for the exact allowlist and refresh process.

Product Factory exercises Mistral through Haystack in live matrices. There is no standalone Mistral tutorial or native client proxy in this release.
