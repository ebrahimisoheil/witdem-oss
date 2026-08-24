"""One-command openai/multi_agent telemetry example."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "openai/multi_agent"


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
        from agents import Agent, Runner
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    with configure() as witdem:  # noqa: SIM117
        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            specialist = Agent(
                name="billing-specialist",
                model=model_name,
                instructions="Answer billing questions briefly.",
            )
            triage = Agent(
                name="triage",
                model=model_name,
                instructions="Route billing questions to the specialist.",
                handoffs=[specialist],
            )
            with witdem.operation("agent.triage", kind="agent", attributes={"agent.name": "triage"}):
                with witdem.operation("agent.handoff", kind="handoff"):  # noqa: SIM117
                    with witdem.model("openai.responses", provider="openai", model=model_name) as model_call:
                        result = Runner.run_sync(triage, "Why was I charged twice?")
                        model_call.response_model(model_name).usage(
                            input_tokens=sum(response.usage.input_tokens for response in result.raw_responses),
                            output_tokens=sum(response.usage.output_tokens for response in result.raw_responses),
                        )
                print(result.final_output)
            answer = str(result.final_output)
            witdem.report(
                result="completed" if answer else "unresolved",
                result_valid=bool(answer),
                product_goal_achieved=bool(answer),
                metrics={"answer_characters": len(answer)},
            )


if __name__ == "__main__":
    main()
