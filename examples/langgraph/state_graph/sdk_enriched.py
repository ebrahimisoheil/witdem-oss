"""SDK-enriched LangGraph entrypoint."""

from pathlib import Path

from app import build_graph
from dotenv import load_dotenv
from witdem_sdk.integrations.langgraph import instrument

load_dotenv(Path(__file__).with_name(".env"))

graph = instrument(build_graph())
result = graph.invoke({"question": "What is observability?", "answer": ""})
print(result["answer"])
