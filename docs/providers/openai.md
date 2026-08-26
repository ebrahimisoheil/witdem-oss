# OpenAI

**Status: beta native support for OpenAI Agents; framework and generic paths are also available.**

## Choose the path

- OpenAI Agents SDK: use [`openai_agents.instrument`](../integrations/openai-agents.md).
- LangChain or LangGraph with an OpenAI model: use that framework's Witdem wrapper and pass `provider="openai"`/`model=` only when framework metadata is incomplete.
- Haystack 3: use the [Haystack wrapper](../integrations/haystack.md); it observes OpenAI generator response metadata at the native component boundary.
- Direct `openai` client calls: use a native `witdem.model(...)` block or the generic wrapper.

## OpenAI Agents example

```bash
cd examples/openai/basic_agent
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

Required variables:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
WITDEM_ENDPOINT=http://localhost:4318
```

See the [full integration guide](../integrations/openai-agents.md) and [multi-agent handoff example](../../examples/openai/multi_agent).

## Cost

The bundled catalog includes the exact OpenAI models listed in [`catalog.yaml`](../../src/witdem/pricing/catalog.yaml), including GPT-4o, GPT-4.1, o3/o4-mini, GPT-5.3 Codex, and GPT-5.4–5.6 families. Other model names remain unmeasured unless the provider reports money or you supply a custom pricing catalog. See [Pricing catalog](../pricing.md) for tier, long-context, regional, search, and media pricing behavior.

## Limitations

- OpenAI Agents tracing is a dedicated integration; the base OpenAI client is not automatically monkey-patched.
- Azure OpenAI has separate endpoint/deployment semantics; use the [Azure guide](azure-openai.md).
- Cost is not proof that content capture is enabled. Prompts and outputs remain disabled by default.
