# Using Witdem with the direct OpenAI SDK

**Status: beta.** This integration wraps the ordinary `OpenAI` or `AsyncOpenAI` client. It is separate from the [OpenAI Agents SDK integration](openai-agents.md).

## Installation

```bash
python -m pip install "witdem-sdk[openai]"
export WITDEM_ENDPOINT=http://localhost:4318
export OPENAI_API_KEY=...
```

## One workload, one execution

```python
from openai import OpenAI
from witdem_sdk.integrations.openai import instrument

client = OpenAI()

def review(observed_client, contract):
    return observed_client.responses.create(
        model="gpt-5.4",
        input=f"Review this contract:\n{contract}",
    )

observed_review = instrument(review, client=client)
response = observed_review(contract)
```

For an application that already owns a Witdem execution, wrap only the client:

```python
from witdem_sdk.integrations.openai import instrument_openai

observed_client = instrument_openai(client, witdem=witdem)
response = observed_client.responses.create(model="gpt-5.4", input="...")
```

The proxy preserves the native client API and supports synchronous and asynchronous Responses, Chat Completions, and embeddings calls. Streaming operations remain open until the stream finishes so final usage is attached to the correct operation. Prompts and responses are not recorded.

Witdem records provider and response model identity, input/output/total tokens, cached and reasoning tokens, embedding vector counts and dimensions, failures, and requested tool-call IDs when returned by the SDK. Cost is resolved by the server pricing catalog when the returned model and usage match a catalog entry.

## With LangGraph

Use the LangGraph callback and direct client proxy together. Framework nodes remain orchestration operations, while the direct OpenAI request is a nested provider operation with `execution_source=openai_sdk`:

```python
from witdem_sdk.integrations.langgraph import WitdemLangGraphCallback
from witdem_sdk.integrations.openai import instrument_openai

client = instrument_openai(OpenAI(), witdem=witdem)
callback = WitdemLangGraphCallback(witdem, provider="openai", model="gpt-5.4")
result = graph.invoke(state, config={"callbacks": [callback]})
```

See [`examples/integrations/cuad_sdk_matrix.py`](../../examples/integrations/cuad_sdk_matrix.py) for direct Anthropic/OpenAI and LangGraph combinations over the same CUAD contract.

## Limitations

- The client is explicitly wrapped; Witdem does not globally monkey-patch OpenAI.
- OpenAI Agents tracing remains a separate adapter because agent handoffs, guardrails, and tool execution are framework operations rather than ordinary client requests.
- Streaming usage depends on the final SDK event or chunk exposing usage.
