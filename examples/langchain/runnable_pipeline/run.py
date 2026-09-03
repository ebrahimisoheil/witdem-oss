"""One-command langchain/runnable_pipeline telemetry example."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "langchain/runnable_pipeline"


def telemetry_smoke() -> dict[str, int]:
    """Create a deterministic execution with one child operation without a network call."""
    provider = TracerProvider(resource=Resource.create({"service.name": f"witdem-example-{EXAMPLE_NAME}"}))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(EXAMPLE_NAME)
    try:
        with tracer.start_as_current_span("witdem.execution") as execution:
            execution.set_attribute("witdem.example", EXAMPLE_NAME)
            with tracer.start_as_current_span("example.operation") as operation:
                operation.set_attribute("witdem.operation.kind", "tool")
    finally:
        provider.shutdown()
    spans = exporter.get_finished_spans()
    return {"executions": 1, "operations": len(spans) - 1}


def main() -> None:
    try:
        from langchain_core.runnables import RunnableLambda
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    with configure() as witdem:  # noqa: SIM117
        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            with witdem.operation("runnable.input", kind="component"):
                prompt = RunnableLambda(lambda question: f"Answer this briefly: {question}")
                rendered = prompt.invoke("What is observability?")
            with witdem.model("langchain.chat", provider="openai", model=model_name) as model_call:
                response = ChatOpenAI(model=model_name, temperature=0).invoke(rendered)
                usage = response.usage_metadata or {}
                model_call.response_model(str(response.response_metadata.get("model_name") or model_name)).usage(
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                )
            with witdem.tool("summarize"):
                tool = RunnableLambda(lambda text: str(text).strip())
                summary = tool.invoke(response.content)
            with witdem.operation("runnable.output", kind="component"):
                output = RunnableLambda(lambda text: {"answer": text}).invoke(summary)
            answer = output["answer"]
            witdem.report(
                result="completed" if answer else "unresolved",
                result_valid=bool(answer),
                requirements={"non_empty_answer": bool(answer)},
                metrics={"answer_characters": len(str(answer))},
            )
            print(output)


if __name__ == "__main__":
    main()
