# Examples

Examples are external applications: they do not open or write Witdem's database. Primary tutorials use a shared shape:

```text
app.py          provider/framework workload
otel_only.py    standard OpenTelemetry or native framework telemetry
sdk_enriched.py Witdem SDK plus an explicit integration helper
.env.example    required local configuration
pyproject.toml  isolated dependencies
```

## Run one tutorial

```bash
witdem dev
cp examples/anthropic/basic_agent/.env.example examples/anthropic/basic_agent/.env
cd examples/anthropic/basic_agent
uv sync
uv run python otel_only.py
uv pip install "../../../witdem-sdk[anthropic]"
uv run --no-sync python sdk_enriched.py
```

`otel_only.py` never imports `witdem_sdk`. Enriched mode uses one framework-specific `instrument(...)` call, reads `WITDEM_ENDPOINT`, keeps content capture disabled, and reports business facts through one explicit result mapper and declarative contract. The command above installs the SDK from this checkout; after an SDK release, `uv pip install "witdem-sdk[anthropic]"` is equivalent. Missing credentials, missing telemetry, authentication failures (`401`), and unavailable cost remain explicit diagnostics.

## Catalog

| Path | Demonstrates | SDK extra |
| --- | --- | --- |
| `examples/openai/basic_agent` | OpenAI agent, model, and tool | `openai` |
| `examples/openai/multi_agent` | Agent handoff | `openai` |
| `examples/anthropic/basic_agent` | Anthropic Messages tool use | `anthropic` |
| `examples/anthropic/tool_loop` | Multiple real tool-use IDs | `anthropic` |
| `examples/langchain/runnable_pipeline` | Runnable/LLM/tool stages | `langchain` |
| `examples/langgraph/state_graph` | Graph state and nodes | `langgraph` |
| `examples/haystack/pipeline` | Two concurrent retrievers converging on one answer | `haystack` |
| `examples/cloud/azure` | Azure OpenAI model spans | none |
| `examples/cloud/bedrock` | Amazon Bedrock model spans | none |
| `examples/cloud/vertex` | Vertex AI model spans | none |
| `examples/ollama/basic` | Local Ollama model spans | none |

Run all credential-eligible tutorials with `python examples/run_live.py --mode both`.

Product Factory under `examples/product-factory` is the controlled multi-runtime reference workload. It separates runtime health, application outcome, decision correctness, and product-goal success across its case matrix.
