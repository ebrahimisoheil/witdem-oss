"""Anthropic tool loop; provider-issued tool-use IDs are always reused."""

from __future__ import annotations

import os


def run(client) -> str:
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    tools = [
        {
            "name": "lookup_order",
            "description": "Look up an order",
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        }
    ]
    messages = [{"role": "user", "content": "Look up order order-123 and tell me its status."}]
    for _ in range(4):
        response = client.messages.create(model=model, max_tokens=256, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
        if not tool_uses:
            return next((str(block.text) for block in response.content if getattr(block, "type", None) == "text"), "")
        results = []
        for tool_use in tool_uses:
            tool_use_id = str(tool_use.id)
            results.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": "status: processing"})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("Anthropic tool loop exceeded its maximum number of turns")
