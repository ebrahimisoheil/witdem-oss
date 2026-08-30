# Examples

Every primary tutorial is an external application: it sends telemetry to Witdem but never imports analytics internals or opens Witdem's DuckDB database.

Start the Docker stack before running a tutorial. The source-only alternative is `uv run witdem dev` from the repository root. SDK tutorials use `WITDEM_ENDPOINT=http://localhost:4318`; OTLP-only tutorials use the standard exporter variables shown in their `.env.example` files.

Most tutorials use this shape:

```text
app.py          provider/framework workload
otel_only.py    standard OpenTelemetry path
sdk_enriched.py Witdem SDK integration
.witdem/        application-owned business contract
.env.example    required configuration
pyproject.toml  isolated dependencies
```

For business-contract design rather than framework setup, use the [YAML contract tutorial](contract-tutorial.md). Its nine [complete contract files](contracts/) cover boolean goals, classification, numeric thresholds, multiple assurance checks, escalation, research approval loops, RAG grounding, flexible chat outcomes, and diagnostic metrics. Every file is compiled by the SDK test suite.

## Framework tutorials

| Path | Demonstrates | Guide |
| --- | --- | --- |
| [`examples/haystack/pipeline`](../examples/haystack/pipeline) | Haystack 3 async fan-out/fan-in and OpenAI generator | [Haystack](integrations/haystack.md) |
| [`examples/langgraph/state_graph`](../examples/langgraph/state_graph) | Compiled state graph | [LangGraph](integrations/langgraph.md) |
| [`examples/langchain/runnable_pipeline`](../examples/langchain/runnable_pipeline) | Runnable pipeline with OpenAI | [LangChain](integrations/langchain.md) |
| [`examples/openai/basic_agent`](../examples/openai/basic_agent) | OpenAI agent and tool | [OpenAI Agents](integrations/openai-agents.md) |
| [`examples/openai/multi_agent`](../examples/openai/multi_agent) | OpenAI Agents handoff | [OpenAI Agents](integrations/openai-agents.md) |
| [`examples/anthropic/basic_agent`](../examples/anthropic/basic_agent) | Anthropic Messages | [Anthropic](integrations/anthropic.md) |
| [`examples/anthropic/tool_loop`](../examples/anthropic/tool_loop) | Anthropic multi-turn tool-use IDs | [Anthropic](integrations/anthropic.md) |

## Provider tutorials

| Path | Integration path | Guide |
| --- | --- | --- |
| [`examples/cloud/azure`](../examples/cloud/azure) | GenAI OTLP or generic wrapper | [Azure OpenAI](providers/azure-openai.md) |
| [`examples/cloud/bedrock`](../examples/cloud/bedrock) | GenAI OTLP or generic wrapper | [Amazon Bedrock](providers/bedrock.md) |
| [`examples/cloud/vertex`](../examples/cloud/vertex) | GenAI OTLP or generic wrapper | [Vertex AI](providers/vertex-ai.md) |
| [`examples/ollama/basic`](../examples/ollama/basic) | GenAI OTLP or generic wrapper | [Ollama](providers/ollama.md) |

DeepSeek and Mistral are live-validated in Product Factory rather than separate tutorials. See their [provider guides](providers.md).

## Run one tutorial

Start Witdem first, then from the repository root:

```bash
cd examples/anthropic/basic_agent
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

The checked-in `pyproject.toml` files resolve the SDK from this repository. For a copied external project, install the SDK from the source checkout as shown in [Getting started](getting-started.md).

To compare runtime-only and SDK-enriched paths:

```bash
uv run python otel_only.py
uv run python sdk_enriched.py
```

## Run credential-eligible framework tutorials

Put shared keys in `examples/.env`, then:

```bash
python examples/run_live.py --mode both
```

The catalog runner currently covers OpenAI, Anthropic, LangChain, LangGraph, and Haystack tutorials. Cloud-provider and Ollama tutorials are run individually. Live calls may incur provider charges.

If a tutorial is skipped, check its `.env.example` for a missing credential. An HTTP `401` means the provider or Witdem receiver rejected the configured key. See [Troubleshooting](troubleshooting.md) rather than adding credentials to source files.

## Product Factory

[`examples/product-factory`](../examples/product-factory) is the controlled multi-runtime workload. It exercises LangChain, LangGraph, Haystack, OpenAI Agents, and Anthropic Messages across OpenAI, Anthropic, DeepSeek, and Mistral profiles, while keeping runtime health separate from business results and goal success.

All runtimes project onto the same `company-qualification` workflow declared in `.witdem/workflows/company-qualification.yaml`. Runtime switches create comparable executions; they do not change the business DAG.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  -f examples/product-factory/compose.yaml \
  up -d --build
cd examples/product-factory
uv sync --all-extras
uv run product-factory run --case clear-qualification --runtime haystack --live --confirm-live
```

The complete matrix uses paid APIs and requires explicit `--live --confirm-live` flags.
