"""SDK-enriched Anthropic entrypoint."""

import os
from pathlib import Path

from anthropic import Anthropic
from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.anthropic import instrument

load_dotenv(Path(__file__).with_name(".env"))
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

observed_run = instrument(
    run,
    client=client,
    report_result=lambda answer: {
        "result": "completed" if answer else "unresolved",
        "result_valid": bool(answer),
        "requirements": {"non_empty_answer": bool(answer)},
        "metrics": {"answer_characters": len(answer)},
    },
)
print(observed_run())
