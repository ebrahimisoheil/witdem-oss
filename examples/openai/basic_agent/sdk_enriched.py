"""SDK-enriched entrypoint using the native OpenAI Agents hook."""

from __future__ import annotations

from pathlib import Path

from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.openai_agents import instrument

load_dotenv(Path(__file__).with_name(".env"))

observed_run = instrument(run)
print(observed_run())
