# SDK and instrumentation

Witdem works without its SDK whenever an application emits standard OTLP/HTTP traces. Install `witdem-sdk` only when the application needs to report explicit business meaning or use a framework helper.

## Declarative contract

The application owns `.witdem/witdem.yaml`. Write it explicitly with the human names, descriptions, and final-result field mappings for outcomes, decisions, product goals, evaluations, metrics, and dimensions. Mappings are declarative data expressions; the SDK never generates or guesses them.

`witdem.report(...)` sends values already known by the application. It does not infer runtime telemetry.

Result and decision labels are owned by the contract; Witdem does not attach meaning to names such as `approved`, `rejected`, or `escalated`. A descriptive contract may optionally classify a value with a semantic dashboard tone:

```yaml
contracts:
  - name: research_report
    result:
      name: Editorial result
      values:
        approved:
          description: The report passed editorial review.
          tone: success
        revision_limit_reached:
          description: The report was not approved before the revision limit.
          tone: warning
    product_goal:
      name: Approved research report
      description: Deliver a report approved by the critic.
```

Supported tones are `success`, `warning`, `failure`, and `neutral`. They select design-system colors rather than arbitrary hex values. Existing `value: Description` entries remain valid; values without a tone receive distinct categorical colors without implying success or failure.

## LangGraph with one integration point

Install the optional dependency with the SDK:

```bash
python -m pip install "witdem-sdk[langgraph]"
```

The `langgraph` extra supports LangGraph `>=0.2,<2`, including the current 1.x line.

Wrap the compiled graph once:

```python
from witdem_sdk.integrations.langgraph import instrument

graph = instrument(
    builder.compile(),
    provider="openai",
    model="gpt-4o",
)
```

No invocation plumbing is required. The returned graph delegates its normal attributes and supports `invoke`, `ainvoke`, `stream`, and `astream`. For every top-level call, the wrapper:

- loads the explicit project YAML and environment settings;
- opens one correlated Witdem execution;
- injects the LangGraph callback without replacing existing callbacks;
- records graph nodes, model calls, tools, errors, token usage, and provider/model identity;
- flushes and closes the SDK after the call or after a stream finishes;
- avoids duplicate instrumentation when a Witdem callback or execution already exists.

Existing call sites stay unchanged:

```python
result = graph.invoke({"topic": "battery recycling"})
```

If the final state contains application outcomes, declare the mapping in YAML. No Python mapper is required:

```yaml
contracts:
  - name: research_report
    application_outcome: {status: $.editorial_decision}
    artifact:
      name: Research report
      valid: {non_empty: $.report}
    decision:
      name: Editorial decision
      expected: approved
      observed: $.editorial_decision
    product_goal:
      name: Approved report
      achieved: $.approved
```

The wrapper evaluates the default YAML contract after `invoke` and `ainvoke`, when a final state exists. Streaming remains runtime-only because a stream may expose partial states rather than one authoritative final result. `report_result` remains an optional override for unusual results that cannot be represented declaratively.

The provider and model may be passed to `instrument` or observed from framework metadata. Cost is measured only when provider, model, and usage are present and the server pricing catalog recognizes the model. Witdem reports an unavailable reason for unknown models instead of estimating a price.

## One-point integrations

Every high-level integration owns SDK configuration, one execution, correlation, error recording, cleanup, and automatic evaluation of the explicit YAML contract. `report_result` is only an optional override.

| Runtime | Integration point | Existing invocation |
| --- | --- | --- |
| LangGraph | `langgraph.instrument(compiled_graph, ...)` | `graph.invoke(...)` |
| LangChain | `langchain.instrument(runnable, ...)` | `chain.invoke(...)` |
| Haystack | `haystack.instrument(pipeline, ...)` | `pipeline.run(...)` |
| OpenAI Agents | `openai_agents.instrument(run_agent, ...)` | `observed_run(...)` |
| Anthropic Messages | `anthropic.instrument(run_agent, client=client, ...)` | `observed_run(...)` |
| Claude Agent SDK | `claude_agent.instrument(message_stream, model=...)` | `async for message in stream` |
| Hugging Face smolagents | `smolagents.instrument(agent, ...)` | `agent.run(...)` |
| LiteLLM | `litellm.instrument(run_agent, ...)` | `observed_run(...)` |
| OpenRouter | `openrouter.instrument(run_agent, client=client, ...)` | `observed_run(...)` |
| Other providers | `generic.instrument(provider_call, ...)` | `observed_call(...)` |

LangChain runnables support synchronous, asynchronous, and streaming invocation. Haystack 3 pipelines support `run`, `run_async`, and `run_async_generator`; installing or instrumenting with Haystack 2 raises an explicit compatibility error. Supported framework ranges are also published in the repository's machine-readable `compatibility.json`. Callable integrations preserve synchronous or asynchronous functions automatically.

For Haystack pipelines and agents, the wrapper observes provider response metadata at each native model boundary and enriches that same Haystack span with provider, model, and token usage. This supports different generator components, parallel branches, loops, nested pipelines, and multiple providers without application-specific result parsing or fabricated calls. When a runtime exposes only one aggregate usage total and exactly one configured model identity, Witdem uses that aggregate as a clearly marked fallback; it never invents per-call splits.

Anthropic instrumentation scopes all message calls made by one workload to one execution, including multi-turn tool loops:

```python
from witdem_sdk.integrations.anthropic import instrument

observed_run = instrument(
    run_agent,
    client=anthropic_client,
    report_result=report_result,
)
answer = observed_run()
```

OpenAI Agents registration and cleanup are automatic:

```python
from witdem_sdk.integrations.openai_agents import instrument

observed_run = instrument(run_agent, report_result=report_result)
answer = observed_run()
```

For a provider without a native adapter, the result observer reports runtime facts while the result mapper reports business facts:

```python
from witdem_sdk.integrations.generic import instrument

observed_call = instrument(
    call_provider,
    operation_name="bedrock.converse",
    provider="aws.bedrock",
    model=model,
    observe_result=lambda response: {
        "response_model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    },
    report_result=report_result,
)
```

The existing low-level callbacks, client proxies, trace processors, and registration handles remain public for applications that already own their execution lifecycle.

## Integrations

```bash
python -m pip install "witdem-sdk[anthropic]"
python -m pip install "witdem-sdk[openai]"
python -m pip install "witdem-sdk[langchain]"
python -m pip install "witdem-sdk[langgraph]"
python -m pip install "witdem-sdk[haystack]"
python -m pip install "witdem-sdk[smolagents]"
python -m pip install "witdem-sdk[litellm]"
python -m pip install "witdem-sdk[openrouter]"
```

Supported input evidence includes generic OpenTelemetry, OTel GenAI attributes, OpenInference, LangChain, LangGraph, OpenAI Agents, Anthropic Messages/Claude Agent telemetry, Haystack, and explicit SDK integration callbacks.

One physical runtime boundary maps to an execution. Physically observed agents, model calls, tools, graph nodes, stages, handoffs, and links map to canonical operations and links. Configured-but-unused nodes are never materialized.

## Privacy and identity

Content capture is disabled by default. Prompts, completions, documents, tool arguments/results, and graph state are not required for structural analytics. Correlation uses explicit execution/trace/span identity; names and timestamps are not used to invent missing relationships.

Provider-reported monetary cost is authoritative when present. Otherwise Witdem may calculate cost from observed provider, model, and usage using its versioned catalog. Unknown prices remain unavailable with a diagnostic reason.
