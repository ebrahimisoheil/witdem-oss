# Using Witdem with Anthropic

**Status: beta.** Witdem has separate helpers for Anthropic Messages and Claude Agent SDK streams.

## Anthropic Messages

Install the Anthropic extra:

```bash
python -m pip install "witdem-sdk[anthropic]==0.3.0"
export WITDEM_ENDPOINT=http://localhost:4318
export ANTHROPIC_API_KEY=...
```

Wrap the application function and let Witdem inject a proxied client:

```python
from anthropic import Anthropic
from witdem_sdk.integrations.anthropic import instrument

client = Anthropic()

def run_agent(client):
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=128,
        messages=[{"role": "user", "content": "Give one observability tip."}],
    )
    return response.content[0].text

run_agent = instrument(run_agent, client=client)
print(run_agent())
```

The proxy preserves the client API, supports sync and async `messages.create`, and observes response model, input/output/cache tokens, and physical tool-use IDs. Multi-turn calls made by the wrapped workload stay under one execution.

Runnable examples:

- [`examples/anthropic/basic_agent`](../../examples/anthropic/basic_agent)
- [`examples/anthropic/tool_loop`](../../examples/anthropic/tool_loop)

## Claude Agent SDK

The Claude Agent helper observes the SDK's async message stream:

```python
from claude_agent_sdk import query
from witdem_sdk.integrations.claude_agent import instrument

messages = query(prompt="Inspect this repository")
async for message in instrument(messages, model="claude-sonnet-4-6"):
    handle(message)
```

It records model usage reported by the final result and tool-use events without retaining message content. Native Claude OTel telemetry can additionally provide physical hierarchy, tools, subagents, and blocking events; availability depends on what the installed Claude runtime emits.

## Limitations

- The Messages proxy currently instruments `messages.create`; other Anthropic API surfaces are not native adapters.
- Claude Agent usage can be execution-total rather than a per-call split and is marked accordingly.
- Tool-use observation does not invent a tool execution when only a requested tool-use block exists.
- Content capture is disabled by default.
