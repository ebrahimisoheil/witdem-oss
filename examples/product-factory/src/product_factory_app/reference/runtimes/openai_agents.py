"""OpenAI Agents native runtime boundary."""

from __future__ import annotations

from typing import Any

from product_factory_app.reference.contracts import RuntimeCase, RuntimeOutput
from product_factory_app.reference.gateways import LiveGateway, ModelGateway, OpenAIAgentsGateway
from product_factory_app.reference.runtimes.base import StageObserver, execute_shared_workflow


class OpenAIAgentsRuntime:
    runtime_id = "openai_agents"

    async def execute(
        self,
        case: RuntimeCase,
        *,
        profile: str,
        gateway: ModelGateway,
        observe: StageObserver,
        witdem: Any | None = None,
    ) -> RuntimeOutput:
        handle = None
        if witdem is not None:
            from witdem_sdk.integrations.openai_agents import install_openai_agents

            handle = install_openai_agents(witdem, capture_content=False)
        if isinstance(gateway, LiveGateway):
            gateway = OpenAIAgentsGateway(witdem)
        try:
            return await execute_shared_workflow(
                self.runtime_id,
                case,
                profile=profile,
                gateway=gateway,
                observe=observe,
            )
        finally:
            if handle is not None:
                handle.uninstall()
