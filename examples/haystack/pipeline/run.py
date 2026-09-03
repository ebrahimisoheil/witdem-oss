"""One-command haystack/pipeline telemetry example."""

from __future__ import annotations

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "haystack/pipeline"


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
        from haystack import Pipeline, component
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    local_cost = {"cost_usd": 0.0, "cost_source": "local_runtime"}
    with configure() as witdem:

        @component
        class Retriever:
            @component.output_types(documents=list)
            def run(self, query: str):
                with witdem.operation("retriever", kind="component", attributes=local_cost):
                    return {"documents": [f"Reference document for: {query}"]}

        @component
        class Generator:
            @component.output_types(answer=str)
            def run(self, documents: list):
                with witdem.operation("generator", kind="component", attributes=local_cost):
                    return {"answer": " ".join(documents) + " — Witdem observes this pipeline."}

        @component
        class Answer:
            @component.output_types(answer=str)
            def run(self, answer: str):
                with witdem.operation("answer", kind="component", attributes=local_cost):
                    return {"answer": answer}

        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            pipeline = Pipeline()
            pipeline.add_component("retriever", Retriever())
            pipeline.add_component("generator", Generator())
            pipeline.add_component("answer", Answer())
            pipeline.connect("retriever.documents", "generator.documents")
            pipeline.connect("generator.answer", "answer.answer")
            result = pipeline.run({"retriever": {"query": "What is observability?"}})
            answer = result["answer"]["answer"]
            witdem.report(
                result="completed" if answer else "unresolved",
                result_valid=bool(answer),
                requirements={"non_empty_answer": bool(answer)},
                metrics={"answer_characters": len(str(answer))},
            )
            print(result["answer"]["answer"])


if __name__ == "__main__":
    main()
