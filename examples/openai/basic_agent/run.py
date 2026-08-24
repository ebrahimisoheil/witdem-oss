"""One-command openai/basic_agent telemetry example."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "openai/basic_agent"


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
        from agents import Agent, Runner, function_tool
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    with configure() as witdem:

        @function_tool
        def lookup_weather(city: str) -> str:
            with witdem.tool("lookup_weather"):
                return f"The weather in {city} is sunny."

        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            agent = Agent(
                name="weather-agent",
                model=model_name,
                instructions="Answer with the weather. Use the tool.",
                tools=[lookup_weather],
            )
            with witdem.operation(  # noqa: SIM117
                "agent.run", kind="agent", attributes={"agent.name": "weather-agent"}
            ):
                with witdem.model("openai.responses", provider="openai", model=model_name) as model_call:
                    result = Runner.run_sync(agent, "What is the weather in Berlin?")
                    input_tokens = sum(response.usage.input_tokens for response in result.raw_responses)
                    output_tokens = sum(response.usage.output_tokens for response in result.raw_responses)
                    model_call.response_model(model_name).usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
            answer = str(result.final_output)
            witdem.report(
                result="completed" if answer else "unresolved",
                result_valid=bool(answer),
                product_goal_achieved=bool(answer),
                metrics={"answer_characters": len(answer)},
            )
            print(result.final_output)


if __name__ == "__main__":
    main()
