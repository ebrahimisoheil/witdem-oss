"""Haystack fan-out/fan-in workload; telemetry setup is entrypoint-owned."""

from __future__ import annotations

import asyncio
import time


def build_pipeline(
    *,
    branch_delay_seconds: float = 0.15,
    use_openai: bool = False,
    openai_model: str = "gpt-4o-mini",
):
    """Build two independent starting branches that converge on one answer."""
    from haystack import Pipeline, component

    generator = None
    if use_openai:
        from haystack.components.generators.chat import OpenAIChatGenerator

        generator = OpenAIChatGenerator(model=openai_model)

    @component
    class KeywordRetriever:
        @component.output_types(keyword_documents=list)
        def run(self, query: str):
            time.sleep(branch_delay_seconds)
            return {"keyword_documents": [f"Keyword evidence for: {query}"]}

    @component
    class SemanticRetriever:
        @component.output_types(semantic_documents=list)
        def run(self, query: str):
            time.sleep(branch_delay_seconds)
            return {"semantic_documents": [f"Semantic evidence for: {query}"]}

    @component
    class Answer:
        @component.output_types(answer=str)
        def run(self, keyword_documents: list, semantic_documents: list):
            evidence = [*keyword_documents, *semantic_documents]
            if generator is not None:
                from haystack.dataclasses import ChatMessage

                prompt = "Answer in one concise sentence using both evidence branches.\n" + "\n".join(
                    f"- {item}" for item in evidence
                )
                reply = generator.run(messages=[ChatMessage.from_user(prompt)])["replies"][0]
                return {"answer": reply.text}
            return {"answer": " | ".join(evidence) + " — both branches completed."}

    pipeline = Pipeline()
    pipeline.add_component("keyword_retriever", KeywordRetriever())
    pipeline.add_component("semantic_retriever", SemanticRetriever())
    pipeline.add_component("answer", Answer())
    pipeline.connect("keyword_retriever.keyword_documents", "answer.keyword_documents")
    pipeline.connect("semantic_retriever.semantic_documents", "answer.semantic_documents")
    return pipeline


async def run_async() -> str:
    result = await build_pipeline().run_async(
        {
            "keyword_retriever": {"query": "What is observability?"},
            "semantic_retriever": {"query": "What is observability?"},
        },
        concurrency_limit=2,
    )
    return str(result["answer"]["answer"])


def run() -> str:
    return asyncio.run(run_async())
