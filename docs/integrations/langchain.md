# Using Witdem with LangChain

**Status: beta.**

The LangChain integration wraps a runnable and appends a callback that records chains, chat/LLM calls, tools, and retrievers. The wrapper owns execution correlation and evaluates the returned result against `.witdem/witdem.yaml`.

## Requirements and installation

- Python 3.10–3.13
- `langchain-core>=0.3,<2`
- A `witdem-sdk` release compatible with the running analytics release
- The model-provider package used by your chain

```bash
python -m pip install "witdem-sdk[langchain]"
export WITDEM_ENDPOINT=http://localhost:4318
```

## Minimal runnable

```python
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from witdem_sdk.integrations.langchain import instrument

prompt = RunnableLambda(lambda question: f"Answer briefly: {question}")
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
chain = prompt | model | RunnableLambda(lambda message: str(message.content).strip())

# The one Witdem-specific integration point.
chain = instrument(chain, provider="openai", model="gpt-4o-mini")

answer = chain.invoke("What is observability?")
```

The project references a vendor-neutral contract instead of describing the
LangChain return shape:

```yaml
version: 2
service: {name: langchain-answer}
contracts: [contracts/answer.yml]
```

Use `report_result` to translate the final application result into the named
contract facts.

The complete checked-in example is [`examples/langchain/runnable_pipeline`](https://github.com/ebrahimisoheil/witdem-oss/tree/main/examples/langchain/runnable_pipeline).

## Existing application integration

```diff
+ from witdem_sdk.integrations.langchain import instrument

  chain = build_chain()
+ chain = instrument(chain)
  result = chain.invoke(input)
```

The proxy supports `invoke`, `ainvoke`, `stream`, and `astream` and preserves existing callbacks. Callable wrappers are not used for LangChain; instrument the runnable itself.

## What is captured

- parent/child runnable and chain boundaries;
- chat-model and LLM calls;
- tool calls and tool errors;
- retriever calls and errors;
- model response identity and usage when exposed by LangChain messages;
- sync/async failures and timing;
- final-result business semantics for `invoke` and `ainvoke`.

## Limitations

- Streaming records runtime telemetry but does not treat a partial chunk as the final contract result.
- Provider/model arguments are useful when serialization metadata is incomplete; they must describe the real call and are not inferred prices.
- Not every third-party runnable emits every callback type. An opaque runnable can appear as a generic component.
- Nested LangGraph applications should use the [LangGraph wrapper](langgraph.md) so graph-specific metadata is preserved.
