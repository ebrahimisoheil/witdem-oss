# Anthropic

**Status: beta native support for Messages and Claude Agent SDK telemetry.**

## Choose the path

- Anthropic Messages: wrap the workload with `witdem_sdk.integrations.anthropic.instrument`.
- Claude Agent SDK: observe its async message stream with `witdem_sdk.integrations.claude_agent.instrument`.
- Haystack/LangGraph/LangChain: use the framework wrapper; provider identity can be observed from component or response metadata.

## Messages example

```bash
cd examples/anthropic/basic_agent
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

Required variables:

```text
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
WITDEM_ENDPOINT=http://localhost:4318
```

The [tool-loop example](../../examples/anthropic/tool_loop) proves that multiple provider tool-use IDs remain associated with one execution. See [Using Witdem with Anthropic](../integrations/anthropic.md) for code.

## Cost

The bundled catalog contains selected Haiku, Sonnet, and Opus models listed in [`catalog.yaml`](../../src/witdem/pricing/catalog.yaml). Input, output, cache-read, and cache-creation token fields are preserved where Anthropic returns them. Unknown snapshots remain unmeasured unless aliased or provider-priced. See [Pricing catalog](../pricing.md) for automated refresh and limitations.

## Limitations

- The native client proxy covers `messages.create`, not every Anthropic API surface.
- Claude Agent usage may be an execution total rather than a physical per-call split.
- A tool-use request and a tool execution are distinct; Witdem does not invent the latter.
