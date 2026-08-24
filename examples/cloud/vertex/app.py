"""Google Vertex AI workload only; no Witdem or telemetry imports."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderResult:
    answer: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


def run() -> ProviderResult:
    from google import genai

    model = os.getenv("VERTEX_MODEL", "gemini-2.0-flash-001")
    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
    )
    response = client.models.generate_content(model=model, contents="Explain observability in one sentence.")
    usage = response.usage_metadata
    return ProviderResult(
        answer=str(response.text or ""),
        model=model,
        input_tokens=int(usage.prompt_token_count) if usage and usage.prompt_token_count is not None else None,
        output_tokens=int(usage.candidates_token_count) if usage and usage.candidates_token_count is not None else None,
    )
