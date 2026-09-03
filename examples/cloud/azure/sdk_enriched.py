"""Azure OpenAI with Witdem traces and application semantics."""

import os
from pathlib import Path

from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.generic import instrument

load_dotenv(Path(__file__).with_name(".env"))
model = os.environ["AZURE_OPENAI_DEPLOYMENT"]

observed_run = instrument(
    run,
    operation_name="azure.openai.chat",
    provider="azure.openai",
    model=model,
    report_result=lambda response: {
        "result": "completed" if response.answer else "unresolved",
        "result_valid": bool(response.answer),
        "requirements": {"non_empty_answer": bool(response.answer)},
        "metrics": {"answer_characters": len(response.answer)},
    },
)
print(observed_run().answer)
