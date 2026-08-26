# Using Witdem with LiteLLM

**Status: beta.** The integration appends a content-safe LiteLLM custom callback. It records the actual provider/model response, usage, reported cost, failures, and OpenRouter routing evidence without replacing callbacks already owned by the application.

## Embedded LiteLLM SDK

Install the optional dependency:

```bash
python -m pip install "witdem-sdk[litellm]==0.2.0"
export WITDEM_ENDPOINT=http://localhost:4318
```

Wrap the application workload, not every `completion(...)` call:

```python
import litellm
from witdem_sdk.integrations.litellm import instrument

def run_agent():
    response = litellm.completion(
        model="openrouter/openai/gpt-5-mini",
        messages=[{"role": "user", "content": "Give one telemetry tip."}],
    )
    return {"answer": response.choices[0].message.content}

run_agent = instrument(run_agent)
result = run_agent()
```

The wrapper creates one execution for the workload, installs the callback for that invocation, preserves existing callbacks, and evaluates `result` using `.witdem/witdem.yaml`.

If the application already owns its execution lifecycle, install only the callback:

```python
from witdem_sdk.integrations.litellm import install_litellm

registration = install_litellm()
```

## LiteLLM Proxy

The proxy can export its native OpenTelemetry directly to Witdem without adding the Witdem SDK to calling applications:

```yaml
litellm_settings:
  callbacks:
    - otel
```

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

Proxy-only telemetry provides operational model health and spend. To attach a final application outcome or product goal, the calling workflow still needs a correlated Witdem execution and YAML contract.

## Observed facts

- requested and returned model;
- actual LiteLLM provider;
- input, output, cached, cache-write, reasoning, and audio tokens when present;
- `response_cost` as authoritative money with `litellm_reported` provenance;
- failure status without making telemetry failures break model calls;
- OpenRouter selected provider, route strategy, attempts, BYOK state, region, and charged cost when present.

Prompt and response content is never copied into Witdem attributes by this callback.
