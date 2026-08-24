"""Azure OpenAI workload only; no Witdem or telemetry imports."""

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
    from openai import AzureOpenAI

    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    client = AzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.environ["AZURE_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )
    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": "Explain observability in one sentence."}],
        max_tokens=128,
    )
    usage = response.usage
    return ProviderResult(
        answer=str(response.choices[0].message.content or ""),
        model=str(response.model or deployment),
        input_tokens=int(usage.prompt_tokens) if usage else None,
        output_tokens=int(usage.completion_tokens) if usage else None,
    )
