# Cohere

**Status: experimental provider path; structurally tested, not live-validated in this release.**

Witdem has no dedicated Cohere client adapter or standalone Cohere tutorial. A Cohere generator used through [Haystack](../integrations/haystack.md) is recognized from its component identity and provider metadata. A direct client can instead use the generic callable wrapper:

```python
from witdem_sdk.integrations.generic import instrument

call = instrument(
    existing_cohere_call,
    operation_name="cohere.chat",
    provider="cohere",
    model="command-model-name",
)
```

If that client does not expose conventional result metadata, add an `observe_result` mapping for the exact response type used by your installed client. See [Provider support](../providers.md#generic-provider-integration); Witdem does not prescribe or guess a Cohere SDK response shape.

## Cost coverage

The bundled catalog includes standard text-token rates for selected Command A, Command R, Command R+, and Command R7B IDs. It does not model Model Vault capacity pricing, rerank searches, embedding-only input rates, or private terms. Those remain **Not measured** unless the operation reports money or the deployment supplies a matching custom catalog through `WITDEM_PRICING_FILE`.

Do not treat this integration as live-verified until a real Cohere workload has been added to the compatibility matrix.
