"""Amazon Bedrock workload only; no Witdem or telemetry imports."""

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
    import boto3

    model = os.getenv("BEDROCK_MODEL", "amazon.nova-lite-v1:0")
    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    response = client.converse(
        modelId=model,
        messages=[{"role": "user", "content": [{"text": "Explain observability in one sentence."}]}],
        inferenceConfig={"maxTokens": 128},
    )
    usage = response.get("usage") or {}
    return ProviderResult(
        answer=str(response["output"]["message"]["content"][0]["text"]),
        model=model,
        input_tokens=int(usage["inputTokens"]) if usage.get("inputTokens") is not None else None,
        output_tokens=int(usage["outputTokens"]) if usage.get("outputTokens") is not None else None,
    )
