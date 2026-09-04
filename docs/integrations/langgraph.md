# Using Witdem with LangGraph

**Status: beta.** The compiled-graph wrapper is covered for sync, async, and streaming methods and has been exercised against real agent workloads. Haystack remains the more extensively validated integration.

LangGraph defines the application's possible graph. Witdem analyzes what actually happened in each execution and connects the executed branch to latency, models, tools, cost, errors, and application outcomes.

## Requirements and installation

- Python 3.10–3.13
- `langgraph>=0.2,<2`
- A `witdem-sdk` release compatible with the running analytics release

```bash
python -m pip install "witdem-sdk[langgraph]"
export WITDEM_ENDPOINT=http://localhost:4318
```

## Minimal conditional graph

```python
from typing import Literal, TypedDict
from langgraph.graph import END, START, StateGraph
from witdem_sdk.integrations.langgraph import instrument

class State(TypedDict):
    question: str
    route: str
    answer: str

def classify(state: State):
    route = "support" if "invoice" in state["question"].lower() else "general"
    return {"route": route}

def support(state: State):
    return {"answer": "I found the invoice."}

def general(state: State):
    return {"answer": "Here is a general answer."}

def choose(state: State) -> Literal["support", "general"]:
    return state["route"]  # type: ignore[return-value]

builder = StateGraph(State)
builder.add_node("classify", classify)
builder.add_node("support", support)
builder.add_node("general", general)
builder.add_edge(START, "classify")
builder.add_conditional_edges("classify", choose)
builder.add_edge("support", END)
builder.add_edge("general", END)

# The one Witdem-specific integration point.
graph = instrument(builder.compile())

result = graph.invoke({"question": "Where is my invoice?", "route": "", "answer": ""})
```

Declare `$.route` and `$.answer` in `.witdem/witdem.yaml`; see the [contract configuration](../configuration.md#contract-file).

## Existing application integration

Wrap the graph where it is compiled or returned:

```diff
+ from witdem_sdk.integrations.langgraph import instrument

- return builder.compile()
+ return instrument(builder.compile())
```

Existing calls stay unchanged:

```python
graph.invoke(inputs, config=existing_config)
await graph.ainvoke(inputs)
graph.stream(inputs)
graph.astream(inputs)
```

The wrapper appends its callback rather than replacing existing callback handlers. It avoids duplicate instrumentation when a Witdem callback or execution is already active.

## Runtime behavior

- Only nodes and branches that execute appear in the run replay.
- LangChain callback events nested beneath graph nodes remain model, tool, retriever, or component operations rather than being flattened into generic graph steps.
- Errors close the active execution with error status and are re-raised to the application.
- `invoke` and `ainvoke` evaluate the authoritative final state against the YAML contract.
- `stream` and `astream` record runtime telemetry but do not evaluate partial chunks as a final business result.

## Subgraphs, fan-out, and native tracing

The backend adapter preserves observed LangGraph namespaces, checkpoint/task identifiers, `Send` fan-out links, retries, interrupts, resumes, and explicit executed-edge evidence when those fields are emitted. Coverage depends on what the installed LangGraph/LangChain callback surface exposes; a static subgraph definition alone is not enough.

Witdem can coexist with other LangGraph/LangChain callbacks. You do not need to disable LangSmith unless your own exporter setup creates duplicate telemetry.

## Real-world proof

The repository includes a minimal runnable at [`examples/langgraph/state_graph`](https://github.com/ebrahimisoheil/witdem-oss/tree/main/examples/langgraph/state_graph). The external Chinook support benchmark also exercises a native `StateGraph` with conditional tool routing, OpenAI model calls, a physical tool call, token usage, measured cost, and a YAML-defined support outcome.

An `open_deep_research` example is not currently checked into this repository, so the documentation does not claim it as validated.

## Limitations

- Streaming does not report the business contract unless the application separately supplies one authoritative final result.
- Static nodes that did not execute are intentionally absent.
- Some framework versions expose less subgraph/edge metadata than others; use the run's technical records to inspect the evidence received.
- Model identity and token usage depend on the nested model integration or callback metadata.
