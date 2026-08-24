"""Physical runtime registry."""

from product_factory_app.reference.runtimes.anthropic_messages import AnthropicMessagesRuntime
from product_factory_app.reference.runtimes.base import ProductFactoryRuntime
from product_factory_app.reference.runtimes.haystack import HaystackRuntime
from product_factory_app.reference.runtimes.langchain import LangChainRuntime
from product_factory_app.reference.runtimes.langgraph import LangGraphRuntime
from product_factory_app.reference.runtimes.openai_agents import OpenAIAgentsRuntime

RUNTIMES: dict[str, type[ProductFactoryRuntime]] = {
    "langchain": LangChainRuntime,
    "langgraph": LangGraphRuntime,
    "haystack": HaystackRuntime,
    "openai_agents": OpenAIAgentsRuntime,
    "anthropic_messages": AnthropicMessagesRuntime,
}

__all__ = ["RUNTIMES"]
