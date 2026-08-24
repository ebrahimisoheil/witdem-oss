"""Claude Agent SDK message-stream instrumentation."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Mapping
from typing import Any

from witdem_sdk.integrations._common import ResultReporter, settings


def _integer(source: Any, *names: str) -> int | None:
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if isinstance(value, int):
            return value
    return None


class ClaudeAgentObserver:
    """Turn native Claude Agent messages into correlated OTel operations."""

    def __init__(self, witdem: Any, *, model: str, capture_content: bool = False) -> None:
        self._witdem = witdem
        self._model = model
        self._capture_content = capture_content

    def observe(self, message: Any) -> None:
        """Observe one yielded SDK message without retaining message content."""

        message_type = type(message).__name__
        if message_type == "AssistantMessage":
            self._observe_tool_use(message)
        elif message_type == "ResultMessage":
            self._observe_result(message)

    def _observe_result(self, message: Any) -> None:
        model_usage = getattr(message, "model_usage", None)
        observed = (
            model_usage
            if isinstance(model_usage, dict) and model_usage
            else {self._model: getattr(message, "usage", None) or {}}
        )
        for model, usage in observed.items():
            self._record_model(str(model), usage)

    def _record_model(self, response_model: str, usage: Any) -> None:
        attributes: dict[str, Any] = {
            "integration": "claude_agent_sdk",
            "witdem.usage.scope": "execution_total",
        }
        if self._capture_content:
            attributes["witdem.capture_content"] = True
        with self._witdem.model(
            "claude_agent.model",
            provider="anthropic",
            model=self._model,
            attributes=attributes,
        ) as operation:
            operation.response_model(response_model)
            input_tokens = _integer(usage, "input_tokens", "inputTokens")
            output_tokens = _integer(usage, "output_tokens", "outputTokens")
            cache_read_tokens = _integer(usage, "cache_read_input_tokens", "cacheReadInputTokens")
            cache_creation_tokens = _integer(
                usage,
                "cache_creation_input_tokens",
                "cacheCreationInputTokens",
            )
            if input_tokens is not None or output_tokens is not None:
                operation.usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=(input_tokens or 0) + (output_tokens or 0),
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                )
            cost = usage.get("costUSD") if isinstance(usage, dict) else getattr(usage, "costUSD", None)
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                operation.cost(float(cost))

    def _observe_tool_use(self, message: Any) -> None:
        for block in getattr(message, "content", ()):
            if type(block).__name__ != "ToolUseBlock":
                continue
            self._witdem.event(
                "claude_agent.tool_use",
                {
                    "tool_name": str(getattr(block, "name", "unknown")),
                    "tool_use_id": str(getattr(block, "id", "")),
                    "integration": "claude_agent_sdk",
                },
            )


def instrument_claude_agent(
    witdem: Any,
    *,
    model: str,
    capture_content: bool = False,
) -> ClaudeAgentObserver:
    """Create an observer for messages yielded by ``claude_agent_sdk.query``."""

    return ClaudeAgentObserver(witdem, model=model, capture_content=capture_content)


async def instrument(
    messages: AsyncIterable[Any],
    *,
    model: str,
    service_name: str | None = None,
    execution_name: str | None = None,
    endpoint: str | None = None,
    config_path: str | None = None,
    capture_content: bool = False,
    attributes: Mapping[str, Any] | None = None,
    report_result: ResultReporter | None = None,
) -> AsyncIterator[Any]:
    """Observe a Claude Agent message stream inside one Witdem execution."""

    integration_settings = settings(
        service_name=service_name,
        execution_name=execution_name,
        endpoint=endpoint,
        config_path=config_path,
        attributes=attributes,
        report_result=report_result,
    )
    with integration_settings.invocation() as witdem:
        observer = instrument_claude_agent(witdem, model=model, capture_content=capture_content)
        final_message: Any = None
        async for message in messages:
            observer.observe(message)
            final_message = message
            yield message
        if final_message is not None:
            integration_settings.report(final_message, witdem)
