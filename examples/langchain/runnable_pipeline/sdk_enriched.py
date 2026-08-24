"""SDK-enriched LangChain entrypoint."""

from pathlib import Path

from app import build_chain
from dotenv import load_dotenv
from witdem_sdk.integrations.langchain import instrument

load_dotenv(Path(__file__).with_name(".env"))

chain = instrument(build_chain())
print(chain.invoke("What is observability?"))
