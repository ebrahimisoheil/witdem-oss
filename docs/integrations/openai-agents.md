# Using Witdem with OpenAI Agents

**Status: beta.**

The integration uses the OpenAI Agents SDK's supported trace-processor registration. It records native agents, generations, tools, handoffs, errors, model identity, and usage without changing the agent definition.

## Installation

```bash
python -m pip install "witdem-sdk[openai]"
export WITDEM_ENDPOINT=http://localhost:4318
export OPENAI_API_KEY=...
```

The SDK extra currently supports `openai-agents>=0.0.10,<0.21`. This narrower
range keeps the direct OpenAI SDK, OpenAI Agents, and LiteLLM extras jointly
solvable.

## Minimal integration

Keep the existing agent workload in a function, then wrap that function:

```python
from agents import Agent, Runner
from witdem_sdk.integrations.openai_agents import instrument

agent = Agent(name="assistant", model="gpt-4o-mini", instructions="Answer briefly.")

def run_agent():
    return Runner.run_sync(agent, "What is observability?").final_output

run_agent = instrument(run_agent)
print(run_agent())
```

The wrapper supports synchronous and async functions, owns processor registration and removal, and evaluates the returned result against `.witdem/witdem.yaml`.

Runnable examples:

- [`examples/openai/basic_agent`](https://github.com/ebrahimisoheil/witdem-oss/tree/main/examples/openai/basic_agent): one agent and tool
- [`examples/openai/multi_agent`](https://github.com/ebrahimisoheil/witdem-oss/tree/main/examples/openai/multi_agent): native handoff

## Limitations

- This adapter targets OpenAI Agents SDK tracing. Use the separate [direct OpenAI SDK integration](openai.md) for Responses, Chat Completions, or embeddings calls.
- Content capture is disabled by default.
- Cost requires provider-reported money or recognized model and usage evidence.
- Native SDK trace fields can change before a 1.0 release; use the dependency
  range declared by the installed `witdem-sdk` release rather than forcing a
  newer Agents SDK version.
