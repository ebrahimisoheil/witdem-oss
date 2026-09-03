"""Small stdio MCP server used by the live CUAD runtime matrix."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def _paragraphs(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"\n\s*\n", text) if len(value.strip()) >= 40]


def _retrieve(text: str, query: str, *, top_k: int) -> list[str]:
    terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
    ranked = sorted(
        _paragraphs(text),
        key=lambda paragraph: len(terms & set(re.findall(r"[a-z0-9]+", paragraph.casefold()))),
        reverse=True,
    )
    return ranked[:top_k]


async def _list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="search_contract",
                description="Return the most relevant paragraphs from a CUAD contract.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "contract": {"type": "string"},
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["contract", "query"],
                },
            )
        ]
    )


async def _call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    if params.name != "search_contract":
        return types.CallToolResult(
            content=[types.TextContent(text=f"Unknown tool: {params.name}")],
            is_error=True,
        )
    arguments = params.arguments or {}
    contract = str(arguments.get("contract") or "")
    query = str(arguments.get("query") or "")
    top_k = int(arguments.get("top_k") or 3)
    evidence = _retrieve(contract, query, top_k=top_k)
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(evidence))],
        structured_content={"documents": evidence},
    )


async def main() -> None:
    server = Server(
        "witdem-cuad-contract-server",
        version="1",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
