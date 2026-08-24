"""Anthropic Messages runtime; tool results always retain provider tool-use IDs."""

from __future__ import annotations

from typing import Any

from product_factory_app.reference.contracts import RuntimeCase, RuntimeOutput
from product_factory_app.reference.gateways import LiveGateway, ModelGateway
from product_factory_app.reference.runtimes.base import StageObserver, execute_shared_workflow


def tool_result_block(tool_use_id: str, content: str) -> dict[str, str]:
    """Construct a valid Anthropic result block from a real response ID."""

    if not tool_use_id or tool_use_id.startswith("demo-"):
        raise ValueError("Anthropic tool results require the real provider-issued tool_use.id")
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


class AnthropicMessagesRuntime:
    runtime_id = "anthropic_messages"

    async def execute(
        self,
        case: RuntimeCase,
        *,
        profile: str,
        gateway: ModelGateway,
        observe: StageObserver,
        witdem: Any | None = None,
    ) -> RuntimeOutput:
        del witdem
        if isinstance(gateway, LiveGateway):
            await gateway.anthropic_tool_research(case.pass_one, profile=profile)
        return await execute_shared_workflow(self.runtime_id, case, profile=profile, gateway=gateway, observe=observe)
