# OpenRouter

**Status: beta native enrichment.** OpenRouter uses an OpenAI-compatible client, while Witdem adds routing and billing fields that generic OpenAI telemetry does not expose consistently.

## Direct OpenRouter client

```bash
python -m pip install "witdem-sdk[openrouter]==0.3.0"
export WITDEM_ENDPOINT=http://localhost:4318
export OPENROUTER_API_KEY=...
```

Wrap the application function and let Witdem inject a proxied client:

```python
import os

from openai import OpenAI
from witdem_sdk.integrations.openrouter import instrument

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

def run_agent(client):
    response = client.chat.completions.create(
        model="openai/gpt-5-mini",
        messages=[{"role": "user", "content": "Give one telemetry tip."}],
    )
    return {"answer": response.choices[0].message.content}

run_agent = instrument(run_agent, client=client)
result = run_agent()
```

The proxy automatically sends `X-OpenRouter-Metadata: enabled`. It supports sync and async Chat Completions and Responses calls, including streaming, and preserves the underlying client API.

## Identity and cost

Witdem keeps routing dimensions separate:

```text
gateway: OpenRouter
requested model: the alias or model sent by the application
provider: the endpoint that successfully served the request
response model: the model returned by OpenRouter
```

`usage.cost` is recorded as authoritative `openrouter_reported` cost. `cost_details.upstream_inference_cost` is retained separately and is not substituted for the amount charged to the application. Route attempts are structural diagnostics; Witdem does not fabricate failed provider calls when OpenRouter reports only attempt metadata.

## Through LiteLLM

LiteLLM can call OpenRouter directly. In that case use the [LiteLLM integration](../integrations/litellm.md); the same OpenRouter response enrichment is applied and a second OpenRouter wrapper is unnecessary.
