"""SDK-enriched concurrent Haystack entrypoint using its native tracer."""

import asyncio
import os
from pathlib import Path

from app import build_pipeline
from dotenv import load_dotenv
from witdem_sdk.integrations.haystack import instrument

load_dotenv(Path(__file__).with_name(".env"))

pipeline = instrument(
    build_pipeline(
        use_openai=True,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
)
result = asyncio.run(
    pipeline.run_async(
        {
            "keyword_retriever": {"query": "What is observability?"},
            "semantic_retriever": {"query": "What is observability?"},
        },
        concurrency_limit=2,
    )
)
print(result["answer"]["answer"])
