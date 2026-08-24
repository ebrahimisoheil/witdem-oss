"""Google Vertex AI with Witdem traces and application semantics."""

import os
from pathlib import Path

from app import run
from dotenv import load_dotenv
from witdem_sdk.integrations.generic import instrument

load_dotenv(Path(__file__).with_name(".env"))
model = os.getenv("VERTEX_MODEL", "gemini-2.0-flash-001")

observed_run = instrument(
    run,
    operation_name="vertex.generate_content",
    provider="google.vertex",
    model=model,
)
print(observed_run().answer)
