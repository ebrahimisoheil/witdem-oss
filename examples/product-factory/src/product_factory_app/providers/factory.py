"""Haystack-native provider construction kept at the integration boundary."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from haystack.utils import Secret
from opentelemetry import trace

from product_factory_app.config import ModelSettings
from product_factory_app.domain.models import CompanyProfile, ProviderMetadata


def _response_metadata(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract one response's safe identity and usage metadata.

    Haystack keeps provider response metadata on the returned ChatMessage.  The
    wrapper below reads only that metadata; prompt, completion, and tool payloads
    never enter the stable operational attributes.
    """

    replies = result.get("replies", []) if isinstance(result, dict) else []
    if not isinstance(replies, list) or not replies:
        return {}, {}
    message = replies[0]
    meta = getattr(message, "meta", None)
    if not isinstance(meta, dict):
        return {}, {}
    usage = meta.get("usage")
    return meta, dict(usage) if isinstance(usage, dict) else {}


class InstrumentedChatGenerator:
    """Attach response identity and usage to the exact active model call.

    Haystack's Agent already creates ``haystack.agent.step.llm``.  In that
    case the response attributes are written to that span while it is active.
    A direct generator call from a Product Factory component otherwise has no
    model span, so a small child ``haystack.llm.chat`` span supplies the same
    physical operation boundary.
    """

    def __init__(self, generator: Any, *, provider: str, model: str, role: str | None = None) -> None:
        self.generator = generator
        self.provider = provider
        self.model = model
        self.role = role

    def __getattr__(self, name: str) -> Any:
        return getattr(self.generator, name)

    def run(
        self,
        messages: Any,
        streaming_callback: Any | None = None,
        generation_kwargs: dict[str, Any] | None = None,
        *,
        tools: Any | None = None,
        tools_strict: bool | None = None,
    ) -> Any:
        current = trace.get_current_span()
        current_name = getattr(current, "name", "")
        owns_span = current_name != "haystack.agent.step.llm"
        span_context = (
            trace.get_tracer("product_factory_app.provider").start_as_current_span("haystack.llm.chat")
            if owns_span
            else nullcontext(current)
        )
        with span_context as model_span:
            span = model_span or trace.get_current_span()
            if not isinstance(span, trace.NonRecordingSpan):
                span.set_attribute("gen_ai.system", self.provider)
                span.set_attribute("gen_ai.provider.name", self.provider)
                span.set_attribute("gen_ai.request.model", self.model)
                span.set_attribute("pf.provider_provenance", "observed_invocation_configuration")
                span.set_attribute("pf.model_provenance", "observed_invocation_configuration")
                if self.role:
                    span.set_attribute("pf.role", self.role)
            kwargs: dict[str, Any] = {"messages": messages}
            if streaming_callback is not None:
                kwargs["streaming_callback"] = streaming_callback
            if generation_kwargs is not None:
                kwargs["generation_kwargs"] = generation_kwargs
            if tools is not None:
                kwargs["tools"] = tools
            if tools_strict is not None:
                kwargs["tools_strict"] = tools_strict
            result = self.generator.run(**kwargs)
            meta, usage = _response_metadata(result)
            response_model = meta.get("model")
            if not isinstance(response_model, str) or not response_model:
                response_model = None
            if not isinstance(span, trace.NonRecordingSpan):
                if response_model:
                    span.set_attribute("gen_ai.response.model", response_model)
                    span.set_attribute("pf.model_provenance", "observed_provider_response")
                for source_key, target_key in (
                    ("prompt_tokens", "gen_ai.usage.input_tokens"),
                    ("input_tokens", "gen_ai.usage.input_tokens"),
                    ("completion_tokens", "gen_ai.usage.output_tokens"),
                    ("output_tokens", "gen_ai.usage.output_tokens"),
                    ("total_tokens", "gen_ai.usage.total_tokens"),
                ):
                    value = usage.get(source_key)
                    if isinstance(value, (int, float)):
                        span.set_attribute(target_key, float(value))
                if usage:
                    span.set_attribute("pf.usage_provenance", "observed_provider_response")
            return result


def build_chat_generator(
    settings: ModelSettings,
    *,
    structured: bool = False,
    role: str | None = None,
) -> Any:
    """Map typed settings to one Haystack-native generator.

    This is intentionally a thin integration adapter: it does not define a
    provider-neutral model interface, pricing policy, or orchestration logic.
    """

    generation_kwargs: dict[str, Any] = {"temperature": 0.0}

    generator: Any
    if settings.provider == "openai":
        from haystack.components.generators.chat import OpenAIChatGenerator

        if structured:
            generation_kwargs["response_format"] = CompanyProfile
        generator = OpenAIChatGenerator(model=settings.model, generation_kwargs=generation_kwargs)
    elif settings.provider == "deepseek":
        from haystack.components.generators.chat import OpenAIChatGenerator

        if structured:
            # Haystack routes response_format through the OpenAI parse endpoint,
            # whose SDK signature rejects DeepSeek's thinking parameter. Use the
            # normal completion endpoint and enforce JSON in the existing prompt
            # plus application-side json.loads/Pydantic validation instead.
            generation_kwargs["max_tokens"] = 4096
            generation_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        generator = OpenAIChatGenerator(
            api_key=Secret.from_env_var("DEEPSEEK_API_KEY"),
            api_base_url="https://api.deepseek.com",
            model=settings.model,
            generation_kwargs=generation_kwargs,
        )
    elif settings.provider == "mistral":
        from haystack_integrations.components.generators.mistral import MistralChatGenerator

        generator = MistralChatGenerator(model=settings.model, generation_kwargs=generation_kwargs)
    else:
        raise ValueError(f"Unsupported chat provider {settings.provider!r}; use openai, deepseek, or mistral")
    return InstrumentedChatGenerator(generator, provider=settings.provider, model=settings.model, role=role)


def build_embedding_component(model: str) -> Any:
    """Build Voyage's native text embedder for optional evidence embeddings."""

    from haystack_integrations.components.embedders.voyage_embedders import VoyageTextEmbedder

    return VoyageTextEmbedder(model=model, input_type="document")


def build_research_agent(settings: ModelSettings, research_tool: Any) -> Any:
    """Build a Haystack Agent for live tool-based research."""

    from haystack.components.agents import Agent

    generator = build_chat_generator(settings, structured=False, role="research")
    return Agent(
        chat_generator=generator,
        tools=[research_tool],
        system_prompt=(
            "You are a careful company research agent. Use the supplied research tool to gather "
            "source-backed evidence. Do not invent facts. Call the tool once for the current pass, "
            "then stop and return a concise summary."
        ),
        max_agent_steps=3,
        raise_on_tool_invocation_failure=False,
    )


def metadata_for(settings: ModelSettings, role: str) -> ProviderMetadata:
    """Return safe, non-secret provider metadata for the run manifest."""

    return ProviderMetadata(provider=settings.provider, model=settings.model, role=role)
