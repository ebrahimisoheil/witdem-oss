"""SDK-enriched OpenAI multi-agent entrypoint."""

from pathlib import Path

from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.openai_agents import instrument

load_dotenv(Path(__file__).with_name(".env"))

observed_run = instrument(run)
print(observed_run())
