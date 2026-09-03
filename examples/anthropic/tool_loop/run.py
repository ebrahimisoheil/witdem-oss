"""One-command anthropic/tool_loop telemetry example."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from witdem_sdk import configure

load_dotenv()

EXAMPLE_NAME = "anthropic/tool_loop"


def _tool_uses(response: object) -> list[object]:
    """Return the actual tool-use blocks emitted by an Anthropic response."""

    content = getattr(response, "content", ())
    return [
        block
        for block in content
        if getattr(block, "type", None) == "tool_use" and isinstance(getattr(block, "id", None), str)
    ]


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
    tools = [
        {
            "name": "search_orders",
            "description": "Find recent orders",
            "input_schema": {
                "type": "object",
                "properties": {"customer": {"type": "string"}},
                "required": ["customer"],
            },
        },
        {
            "name": "check_refund",
            "description": "Check refund status",
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    ]
    messages = [
        {
            "role": "user",
            "content": (
                "Use search_orders for customer 'Example Customer', then use check_refund with the returned order ID. "
                "Do not ask follow-up questions."
            ),
        }
    ]
    with configure() as witdem:  # noqa: SIM117
        with witdem.execution(attributes={"witdem.example": EXAMPLE_NAME}):
            last_response = None
            for step in range(4):
                with witdem.model(
                    f"claude.messages.{step}", provider="anthropic", model=model_name
                ) as model_call:
                    response = client.messages.create(model=model_name, max_tokens=256, tools=tools, messages=messages)
                    model_call.response_model(response.model).usage(
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                    )
                last_response = response
                messages.append({"role": "assistant", "content": response.content})
                tool_uses = _tool_uses(response)
                if not tool_uses:
                    break
                tool_results = []
                for tool_use in tool_uses:
                    tool_name = str(tool_use.name)
                    tool_use_id = str(tool_use.id)
                    tool_input = getattr(tool_use, "input", {})
                    if not isinstance(tool_input, dict):
                        tool_input = {}
                    with witdem.tool(tool_name, call_id=tool_use_id):
                        if tool_name == "search_orders":
                            tool_result = {
                                "order_id": "order-123",
                                "customer": str(tool_input.get("customer", "customer")),
                            }
                        elif tool_name == "check_refund":
                            tool_result = {
                                "order_id": str(tool_input.get("order_id", "order-123")),
                                "status": "processing",
                            }
                        else:
                            tool_result = {"error": f"Unknown tool: {tool_name}"}
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(tool_result)}
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )
            else:
                raise RuntimeError("Anthropic tool loop exceeded its maximum number of turns")
            text_blocks = [getattr(block, "text", "") for block in getattr(last_response, "content", ())]
            final_answer = str(next((text for text in reversed(text_blocks) if text), messages[-1]))
            witdem.report(
                result="completed" if final_answer else "unresolved",
                result_valid=bool(final_answer),
                requirements={"non_empty_answer": bool(final_answer)},
                metrics={"answer_characters": len(final_answer)},
            )
            print(final_answer)


if __name__ == "__main__":
    main()
