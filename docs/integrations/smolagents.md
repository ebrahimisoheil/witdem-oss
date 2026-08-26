# Using Witdem with Hugging Face smolagents

**Status: beta.** Witdem delegates physical agent, step, model, and tool telemetry to smolagents' official OpenInference instrumentor. The Witdem wrapper adds one correlated execution and evaluates the returned value against the project YAML.

## Install

```bash
python -m pip install "witdem-sdk[smolagents]==0.2.0"
export WITDEM_ENDPOINT=http://localhost:4318
```

## Integrate

```python
from witdem_sdk.integrations.smolagents import instrument

agent = instrument(agent)
result = agent.run(task)
```

No smolagents callback or telemetry setup is required. `instrument(agent)` enables `SmolagentsInstrumentor` once, preserves the native `run(...)` API, and does not capture prompts, responses, tool arguments, or tool results through Witdem.

Streaming keeps the execution open until the native iterator finishes:

```python
for item in agent.run(task, stream=True):
    render(item)
```

The final streamed output, rather than an intermediate step, is handed to the default YAML contract.

## Ownership boundary

- smolagents/OpenInference owns observed agent, chain, model, and tool spans.
- Witdem owns execution correlation, ingestion, analytics, and YAML-defined business meaning.
- The underlying model integration owns provider identity, token usage, and cost. A smolagents agent span may contain aggregate tokens, but Witdem does not invent a per-call split.

## Limitations

- Only classes covered by the installed `openinference-instrumentation-smolagents` release can emit native child spans.
- Provider-reported money is preferred. Hugging Face or custom inference endpoints that do not report money remain **Not measured**.
- Instrumentation is process-global and idempotent, matching the official OpenTelemetry instrumentor model.
