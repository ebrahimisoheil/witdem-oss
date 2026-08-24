"""Ollama workload only; no Witdem or telemetry imports."""

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
    from ollama import Client

    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    response = Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434")).chat(
        model=model,
        messages=[{"role": "user", "content": "Explain observability in one sentence."}],
    )
    return ProviderResult(
        answer=str(response["message"]["content"]),
        model=str(response.get("model") or model),
        input_tokens=int(response["prompt_eval_count"]) if response.get("prompt_eval_count") is not None else None,
        output_tokens=int(response["eval_count"]) if response.get("eval_count") is not None else None,
    )
