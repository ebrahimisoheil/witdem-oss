import asyncio
import time

from app import build_pipeline


def test_independent_retrievers_run_concurrently() -> None:
    pipeline = build_pipeline(branch_delay_seconds=0.2)
    started = time.perf_counter()
    result = asyncio.run(
        pipeline.run_async(
            {
                "keyword_retriever": {"query": "parallel tracing"},
                "semantic_retriever": {"query": "parallel tracing"},
            },
            concurrency_limit=2,
        )
    )
    elapsed = time.perf_counter() - started

    assert "Keyword evidence" in result["answer"]["answer"]
    assert "Semantic evidence" in result["answer"]["answer"]
    assert elapsed < 0.35
