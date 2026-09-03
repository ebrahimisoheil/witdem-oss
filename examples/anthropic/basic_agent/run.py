"""One-command anthropic/basic_agent telemetry example."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "anthropic/basic_agent"


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
        from anthropic import Anthropic
    except ImportError as exc:
        raise SystemExit("Install dependencies with `uv sync` before running this example.") from exc

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    tool = {
        "name": "lookup_weather",
        "description": "Look up weather",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    }
    with configure() as witdem:  # noqa: SIM117
        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            with witdem.model("claude.messages", provider="anthropic", model=model_name) as model_call:
                first = client.messages.create(
                    model=model_name,
                    max_tokens=128,
                    tools=[tool],
                    messages=[{"role": "user", "content": "What is the weather in Berlin?"}],
                )
                model_call.response_model(first.model).usage(
                    input_tokens=first.usage.input_tokens,
                    output_tokens=first.usage.output_tokens,
                )
            with witdem.tool("lookup_weather"):
                city = (
                    first.content[0].input.get("city", "Berlin")
                    if first.content and hasattr(first.content[0], "input")
                    else "Berlin"
                )
                answer = f"Weather in {city} is sunny."
            with witdem.model("claude.messages.final", provider="anthropic", model=model_name) as model_call:
                final = client.messages.create(
                    model=model_name,
                    max_tokens=128,
                    messages=[{"role": "user", "content": answer}],
                )
                model_call.response_model(final.model).usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                )
            final_answer = final.content[0].text if final.content else answer
            witdem.report(
                result="completed" if final_answer else "unresolved",
                result_valid=bool(final_answer),
                requirements={"non_empty_answer": bool(final_answer)},
                metrics={"answer_characters": len(final_answer)},
            )
            print(final_answer)


if __name__ == "__main__":
    main()
