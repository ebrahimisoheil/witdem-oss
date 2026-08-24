"""LangChain workload only; callback configuration is supplied by entrypoints."""

from __future__ import annotations

import os


def build_chain():
    from langchain_core.runnables import RunnableLambda
    from langchain_openai import ChatOpenAI

    prompt = RunnableLambda(lambda question: f"Answer briefly: {question}")
    model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)
    return prompt | model | RunnableLambda(lambda message: str(message.content).strip())


def run(callbacks=None) -> str:
    return str(build_chain().invoke("What is observability?", config={"callbacks": callbacks or []}))
