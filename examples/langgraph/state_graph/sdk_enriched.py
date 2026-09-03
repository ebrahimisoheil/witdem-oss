"""SDK-enriched LangGraph entrypoint."""

from pathlib import Path

from app import build_graph
from dotenv import load_dotenv
from witdem_sdk.integrations.langgraph import instrument

load_dotenv(Path(__file__).with_name(".env"))

graph = instrument(
    build_graph(),
    report_result=lambda state: {
        "result": "completed" if state.get("answer") else "unresolved",
        "result_valid": bool(state.get("answer")),
        "requirements": {"non_empty_answer": bool(state.get("answer"))},
        "metrics": {"answer_characters": len(str(state.get("answer", "")))},
    },
)
result = graph.invoke({"question": "What is observability?", "answer": ""})
print(result["answer"])
