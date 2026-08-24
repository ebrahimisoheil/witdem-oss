"""Ollama with Witdem traces and application semantics."""

import os
from pathlib import Path

from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.generic import instrument

load_dotenv(Path(__file__).with_name(".env"))
model = os.getenv("OLLAMA_MODEL", "llama3.2")

observed_run = instrument(
    run,
    operation_name="ollama.chat",
    provider="ollama",
    model=model,
)
print(observed_run().answer)
