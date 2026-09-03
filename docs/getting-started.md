# Getting started

This guide takes one application execution from code to the local Witdem dashboard.

## Prerequisites

- Docker with Compose for the NPX path, or Python 3.10–3.13 with pipx for native operation
- Python 3.10–3.13 for the SDK and examples
- An existing AI application or one of the checked-in examples
- The API key required by the provider you choose

## 1. Start Witdem

Choose one:

```bash
# Docker-managed
npx -y witdem@latest up

# Native Python, without Node or Docker
pipx install witdem-analytics
witdem up
```

Verify both public services:

```bash
npx -y witdem@latest status  # NPX
witdem status                # pipx
curl http://localhost:4318/readiness
curl http://localhost:8501/health
```

The receiver is at `http://localhost:4318`; the dashboard is at `http://localhost:8501`.
NPX uses the container matching its package version and a persistent named
volume. pipx runs the same three services as validated background processes
and stores data under the platform data directory. See [Operations](operations.md).

## 2. Choose an integration

| Application | Guide | SDK extra |
| --- | --- | --- |
| Haystack 3 | [Using Witdem with Haystack](integrations/haystack.md) | `haystack` |
| LangGraph | [Using Witdem with LangGraph](integrations/langgraph.md) | `langgraph` |
| LangChain | [Using Witdem with LangChain](integrations/langchain.md) | `langchain` |
| Direct OpenAI SDK | [Using Witdem with the direct OpenAI SDK](integrations/openai.md) | `openai` |
| OpenAI Agents | [Using Witdem with OpenAI Agents](integrations/openai-agents.md) | `openai` |
| Anthropic Messages or Claude Agent SDK | [Using Witdem with Anthropic](integrations/anthropic.md) | `anthropic` for Messages |
| Hugging Face smolagents | [Using Witdem with smolagents](integrations/smolagents.md) | `smolagents` |
| LiteLLM SDK or Proxy | [Using Witdem with LiteLLM](integrations/litellm.md) | `litellm` for embedded SDK |
| Direct OpenRouter | [Using Witdem with OpenRouter](providers/openrouter.md) | `openrouter` |
| Custom Python workflow | [Instrumenting custom AI workflows](integrations/native-python.md) | none |
| Existing OpenTelemetry | [OTLP-only mode](concepts.md#two-ingestion-modes) | no Witdem dependency |

Install the SDK with the framework extra selected above:

```bash
python -m pip install "witdem-sdk[haystack]"
```

Replace `haystack` with the extra in the table. The provider-specific packages remain dependencies of your application.

## 3. Point the application at Witdem

```bash
export WITDEM_ENDPOINT=http://localhost:4318
```

For a receiver protected by a bearer key, also set `WITDEM_API_KEY`. Do not commit provider keys or the Witdem key.

## 4. Add the business contract

Initialize `.witdem/witdem.yaml` from the application repository:

```bash
witdem-sdk init
```

The command creates a small project index and a separate contract. It does not
detect frameworks or modify application code. It refuses to overwrite an
existing project unless `--force` is passed.

Edit the generated contract to describe what a useful application result
means. For example:

```yaml
version: 2
service:
  name: my-agent
telemetry:
  capture_content: false
contracts: [contracts/answer.yml]
```

The contract declares allowed results and named goal requirements. Application
code reports those facts explicitly; the YAML contains no framework-specific
return paths. Validate all referenced files from the application directory:

```bash
witdem-sdk validate
```

Follow the [Witdem YAML contract tutorial](contract-tutorial.md) to model hard
rules, flexible chat outcomes, confidence thresholds, decisions, assurance,
and diagnostic metrics. Use [YAML configuration](configuration.md) as the field
reference.

## 5. Instrument and run

For Haystack:

```python
from witdem_sdk.integrations.haystack import instrument

pipeline = instrument(build_pipeline())
result = pipeline.run(data)
```

The integration loads the YAML, opens one correlated execution, observes the
framework, flushes telemetry, and closes its resources. Report business facts
with `Witdem.report(...)` or an integration's `report_result` callback.

## 6. Verify the first run

Open `http://localhost:8501/runs` and check:

```text
✓ the run appears
✓ the root execution is visible
✓ executed child steps appear
✓ model and tool calls appear when the framework exposed them
✓ runtime status matches what happened
✓ application result and product goal match the YAML contract
✓ token and cost coverage are explicit rather than silently zero
```

The ELT worker processes ingestion asynchronously. A new run can take a short moment to become queryable. If it does not appear, use [Troubleshooting](troubleshooting.md#no-runs-appear).

## Stop without deleting data

```bash
npx -y witdem@latest down  # NPX
witdem down                # pipx
```

Both paths preserve the corpus. Use `witdem dev` only for foreground platform
development, not as the normal native installation lifecycle.
