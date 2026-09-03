"""SDK-enriched LangChain entrypoint."""

from pathlib import Path

from app import build_chain
from dotenv import load_dotenv
from witdem_sdk.integrations.langchain import instrument

load_dotenv(Path(__file__).with_name(".env"))

chain = instrument(
    build_chain(),
    report_result=lambda answer: {
        "result": "completed" if answer else "unresolved",
        "result_valid": bool(answer),
        "requirements": {"non_empty_answer": bool(answer)},
        "metrics": {"answer_characters": len(str(answer))},
    },
)
print(chain.invoke("What is observability?"))
