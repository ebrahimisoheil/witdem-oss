"""Deterministic CI and live provider gateways for shared business roles."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import Any, Protocol

import httpx
from opentelemetry import trace
from pydantic import BaseModel

from product_factory_app.reference.contracts import CompanyIdentity, EvidenceCritique, EvidenceItem, ProfileArtifact
from product_factory_app.reference.profiles import MODEL_PROFILES
from product_factory_app.reference.prompts import CRITIQUE_PROMPT, PROFILE_PROMPT, QUALIFICATION_PROMPT, RESEARCH_PROMPT

REQUIRED_DIMENSIONS = ("catalog_complexity", "market_scale", "data_fragmentation", "operational_pain")
MAX_OUTPUT_TOKENS = 2400


class _ResearchResponse(BaseModel):
    relevant_evidence_ids: list[str]


class _QualificationResponse(BaseModel):
    dimensions: dict[str, float]

    @classmethod
    def from_provider(cls, body: Mapping[str, Any]) -> _QualificationResponse:
        dimensions = ProfileArtifact.normalize_dimension_scores(body.get("dimensions", {}))
        return cls(dimensions=dimensions)


class ModelGateway(Protocol):
    actual_models: dict[str, str]
    usage: dict[str, int]

    async def research(self, evidence: Sequence[EvidenceItem], *, profile: str) -> list[str]: ...
    async def critique(self, evidence: Sequence[EvidenceItem], *, profile: str) -> EvidenceCritique: ...
    async def extract(
        self, company: CompanyIdentity, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> ProfileArtifact: ...
    async def qualify(
        self, profile_artifact: ProfileArtifact, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> dict[str, float]: ...


class DeterministicGateway:
    """Provider fake driven only by agent-visible evidence, never ground truth."""

    def __init__(self) -> None:
        self.actual_models: dict[str, str] = {}
        self.usage: dict[str, int] = {}

    def _model(self, profile: str, role: str) -> None:
        self.actual_models[role] = MODEL_PROFILES[profile][role]
        self.usage[role] = self.usage.get(role, 0) + 100

    async def research(self, evidence: Sequence[EvidenceItem], *, profile: str) -> list[str]:
        self._model(profile, "research")
        return [item.id for item in evidence]

    async def critique(self, evidence: Sequence[EvidenceItem], *, profile: str) -> EvidenceCritique:
        self._model(profile, "evidence_critic")
        by_dimension = {
            dimension: [item for item in evidence if item.dimension == dimension] for dimension in REQUIRED_DIMENSIONS
        }
        missing = [dimension for dimension, items in by_dimension.items() if not items]
        weak = [
            dimension
            for dimension, items in by_dimension.items()
            if items and max(item.reliability for item in items) < 0.8
        ]
        conflicts = sorted({item.dimension for item in evidence if item.conflicting})
        queries = list(dict.fromkeys([*missing, *weak]))
        return EvidenceCritique(missing_dimensions=missing, conflicts=conflicts, research_queries=queries)

    async def extract(
        self, company: CompanyIdentity, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> ProfileArtifact:
        self._model(profile, "profile_extractor")
        dimensions: dict[str, float] = {}
        for dimension in REQUIRED_DIMENSIONS:
            candidates = [item for item in evidence if item.dimension == dimension]
            if candidates:
                best = max(candidates, key=lambda item: item.reliability)
                dimensions[dimension] = best.score_signal
        return ProfileArtifact(
            company_name=company.name,
            summary=f"Controlled evidence profile for {company.name}",
            dimensions=dimensions,
            evidence_ids=[item.id for item in evidence],
            completeness=len(dimensions) / len(REQUIRED_DIMENSIONS),
        )

    async def qualify(
        self, profile_artifact: ProfileArtifact, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> dict[str, float]:
        self._model(profile, "qualification_analyst")
        return dict(profile_artifact.dimensions)


class OpenAIAgentsGateway:
    """Role gateway implemented with native OpenAI Agents, tools, and a handoff."""

    def __init__(self, witdem: Any | None = None) -> None:
        self.actual_models: dict[str, str] = {}
        self.usage: dict[str, int] = {}
        self._witdem = witdem

    def _usage(self, role: str, result: Any, model: str) -> tuple[int, int]:
        input_tokens = 0
        output_tokens = 0
        for response in getattr(result, "raw_responses", ()):
            usage = getattr(response, "usage", None)
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.usage[role] = input_tokens + output_tokens
        self.actual_models[role] = model
        return input_tokens, output_tokens

    def _model_span(self, role: str, model: str) -> Any:
        if self._witdem is None:
            return nullcontext(None)
        return self._witdem.model(
            f"product_factory.{role}.model",
            provider="openai",
            model=model,
            attributes={"product_factory.role": role, "integration": "openai_agents"},
        )

    def _finish_model_span(self, operation: Any, result: Any, *, role: str, model: str) -> None:
        input_tokens, output_tokens = self._usage(role, result, model)
        if operation is not None:
            operation.usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ).response_model(model)

    async def research(self, evidence: Sequence[EvidenceItem], *, profile: str) -> list[str]:
        from agents import (  # type: ignore[import-not-found]
            Agent,
            AgentOutputSchema,
            ModelSettings,
            Runner,
            function_tool,
        )

        model = MODEL_PROFILES[profile]["research"]

        @function_tool
        def get_controlled_evidence(query: str) -> str:
            """Return controlled company evidence for a research query."""

            del query
            return json.dumps([item.model_dump() for item in evidence])

        researcher = Agent(
            name="Product Factory researcher",
            instructions=RESEARCH_PROMPT + " You must call get_controlled_evidence before answering.",
            model=model,
            tools=[get_controlled_evidence],
            output_type=AgentOutputSchema(_ResearchResponse, strict_json_schema=False),
            model_settings=ModelSettings(max_tokens=1200),
        )
        router = Agent(
            name="Product Factory research router",
            instructions="Hand off this controlled-evidence task to the Product Factory researcher.",
            model=model,
            handoffs=[researcher],
            model_settings=ModelSettings(max_tokens=1200),
        )
        with self._model_span("research", model) as operation:
            result = await Runner.run(router, "Research the supplied company evidence.", max_turns=6)
            self._finish_model_span(operation, result, role="research", model=model)
        output = result.final_output
        if not isinstance(output, _ResearchResponse):
            output = _ResearchResponse.model_validate(output)
        return output.relevant_evidence_ids

    async def critique(self, evidence: Sequence[EvidenceItem], *, profile: str) -> EvidenceCritique:
        from agents import Agent, AgentOutputSchema, ModelSettings, Runner  # type: ignore[import-not-found]

        model = MODEL_PROFILES[profile]["evidence_critic"]
        agent = Agent(
            name="Evidence critic",
            instructions=CRITIQUE_PROMPT,
            model=model,
            output_type=AgentOutputSchema(EvidenceCritique, strict_json_schema=False),
            model_settings=ModelSettings(max_tokens=1200),
        )
        with self._model_span("evidence_critic", model) as operation:
            result = await Runner.run(agent, json.dumps([item.model_dump() for item in evidence]), max_turns=3)
            self._finish_model_span(operation, result, role="evidence_critic", model=model)
        return (
            result.final_output
            if isinstance(result.final_output, EvidenceCritique)
            else EvidenceCritique.model_validate(result.final_output)
        )

    async def extract(
        self, company: CompanyIdentity, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> ProfileArtifact:
        from agents import Agent, AgentOutputSchema, ModelSettings, Runner  # type: ignore[import-not-found]

        model = MODEL_PROFILES[profile]["profile_extractor"]
        agent = Agent(
            name="Profile extractor",
            instructions=PROFILE_PROMPT,
            model=model,
            output_type=AgentOutputSchema(ProfileArtifact, strict_json_schema=False),
            model_settings=ModelSettings(max_tokens=1200),
        )
        with self._model_span("profile_extractor", model) as operation:
            result = await Runner.run(
                agent,
                json.dumps({"company": company.model_dump(), "evidence": [item.model_dump() for item in evidence]}),
                max_turns=3,
            )
            self._finish_model_span(operation, result, role="profile_extractor", model=model)
        return (
            result.final_output
            if isinstance(result.final_output, ProfileArtifact)
            else ProfileArtifact.model_validate(result.final_output)
        )

    async def qualify(
        self, profile_artifact: ProfileArtifact, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> dict[str, float]:
        from agents import Agent, AgentOutputSchema, ModelSettings, Runner  # type: ignore[import-not-found]

        model = MODEL_PROFILES[profile]["qualification_analyst"]
        agent = Agent(
            name="Qualification analyst",
            instructions=QUALIFICATION_PROMPT,
            model=model,
            output_type=AgentOutputSchema(_QualificationResponse, strict_json_schema=False),
            model_settings=ModelSettings(max_tokens=1200),
        )
        with self._model_span("qualification_analyst", model) as operation:
            result = await Runner.run(
                agent,
                json.dumps(
                    {"profile": profile_artifact.model_dump(), "evidence": [item.model_dump() for item in evidence]}
                ),
                max_turns=3,
            )
            self._finish_model_span(operation, result, role="qualification_analyst", model=model)
        output = result.final_output
        if not isinstance(output, _QualificationResponse):
            output = _QualificationResponse.model_validate(output)
        return output.dimensions


def _json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Provider response did not contain a JSON object")


class LiveGateway:
    """Small provider-neutral HTTP gateway; orchestration remains framework-owned."""

    def __init__(self, *, timeout: float = 60.0) -> None:
        self.actual_models: dict[str, str] = {}
        self.usage: dict[str, int] = {}
        self._timeout = timeout
        self._cached_research_ids: list[str] | None = None
        self.tool_use_ids: list[str] = []

    async def aclose(self) -> None:
        """Retained for compatibility; each request owns and closes its transport."""

    async def _post_once(self, url: str, **kwargs: Any) -> httpx.Response:
        # Haystack executes synchronous components in worker threads, each with
        # its own short-lived event loop. An AsyncClient created on the caller's
        # loop cannot be safely reused or closed from those component loops.
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(url, **kwargs)

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Retry only transient request failures; a complete cell is never retried."""

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._post_once(url, **kwargs)
                if response.status_code not in {408, 429, 500, 502, 503, 504}:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"transient provider status {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            if attempt < 2:
                await asyncio.sleep((0.25 * (2**attempt)) + random.uniform(0, 0.1))
        assert last_error is not None
        raise last_error

    async def _call(self, role: str, profile: str, prompt: str, data: Mapping[str, Any]) -> dict[str, Any]:
        model = MODEL_PROFILES[profile][role]
        content = f"{prompt}\nINPUT:\n{json.dumps(data, ensure_ascii=False)}"
        if model.startswith("claude-"):
            provider = "anthropic"
        elif model.startswith("deepseek-"):
            provider = "deepseek"
        elif model.startswith("mistral-"):
            provider = "mistral"
        else:
            provider = "openai"
        tracer = trace.get_tracer("product_factory.live_models")
        with tracer.start_as_current_span(f"product_factory.{role}.model") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", provider)
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("product_factory.role", role)
            cache_read_tokens = 0
            cache_creation_tokens = 0
            if provider == "anthropic":
                response = await self._post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": MAX_OUTPUT_TOKENS,
                        "messages": [{"role": "user", "content": content}],
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = "".join(
                    block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"
                )
                usage = body.get("usage", {})
                input_tokens = int(usage.get("input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", 0))
                cache_read_tokens = int(usage.get("cache_read_input_tokens", 0))
                cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0))
                self.usage[role] = input_tokens + output_tokens
            else:
                if provider == "deepseek":
                    base, key = "https://api.deepseek.com/chat/completions", os.environ["DEEPSEEK_API_KEY"]
                elif provider == "mistral":
                    base, key = "https://api.mistral.ai/v1/chat/completions", os.environ["MISTRAL_API_KEY"]
                else:
                    base, key = "https://api.openai.com/v1/chat/completions", os.environ["OPENAI_API_KEY"]
                request_body = {
                    "model": model,
                    "messages": [{"role": "user", "content": content}],
                    ("max_completion_tokens" if provider == "openai" else "max_tokens"): MAX_OUTPUT_TOKENS,
                    "response_format": {"type": "json_object"},
                }
                response = await self._post(
                    base,
                    headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                    json=request_body,
                )
                response.raise_for_status()
                body = response.json()
                message = body["choices"][0]["message"]
                # Some reasoning models put the final object in either field,
                # or emit prose in ``content`` while retaining JSON in
                # ``reasoning_content``. Parse both without logging either.
                text = "\n".join(
                    str(value)
                    for value in (message.get("content"), message.get("reasoning_content"))
                    if value
                )
                usage = body.get("usage", {})
                input_tokens = int(usage.get("prompt_tokens", 0))
                output_tokens = int(usage.get("completion_tokens", 0))
                self.usage[role] = int(usage.get("total_tokens", input_tokens + output_tokens))
            actual_model = str(body.get("model", model))
            self.actual_models[role] = actual_model
            span.set_attribute("gen_ai.response.model", actual_model)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            span.set_attribute("gen_ai.usage.total_tokens", input_tokens + output_tokens)
            if provider == "anthropic":
                span.set_attribute("gen_ai.usage.cache_read.input_tokens", cache_read_tokens)
                span.set_attribute("gen_ai.usage.cache_creation.input_tokens", cache_creation_tokens)
        return _json_object(text)

    async def research(self, evidence: Sequence[EvidenceItem], *, profile: str) -> list[str]:
        if self._cached_research_ids is not None:
            return list(self._cached_research_ids)
        body = await self._call(
            "research", profile, RESEARCH_PROMPT, {"evidence": [item.model_dump() for item in evidence]}
        )
        return [str(item) for item in body.get("relevant_evidence_ids", [])]

    async def anthropic_tool_research(self, evidence: Sequence[EvidenceItem], *, profile: str) -> None:
        """Run the native Messages tool loop and preserve every provider-issued ID."""

        model = MODEL_PROFILES[profile]["research"]
        if not model.startswith("claude-"):
            return
        prompt = (
            "Call get_controlled_evidence exactly once, then return JSON with relevant_evidence_ids. "
            "Use only the tool result."
        )
        first_message = {"role": "user", "content": prompt}
        request = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "tools": [
                {
                    "name": "get_controlled_evidence",
                    "description": "Return the controlled evidence pack for this case.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "get_controlled_evidence"},
            "messages": [first_message],
        }
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        tracer = trace.get_tracer("product_factory.anthropic_messages")
        with tracer.start_as_current_span("anthropic.messages.tool_loop") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "anthropic")
            span.set_attribute("gen_ai.request.model", model)
            response = await self._post("https://api.anthropic.com/v1/messages", headers=headers, json=request)
            response.raise_for_status()
            assistant = response.json()
            tool_uses = [block for block in assistant.get("content", []) if block.get("type") == "tool_use"]
            if not tool_uses:
                raise ValueError("Anthropic Messages response did not issue the required evidence tool call")
            results = []
            for tool_use in tool_uses:
                tool_id = str(tool_use.get("id", ""))
                if not tool_id or tool_id.startswith("demo-"):
                    raise ValueError("Anthropic returned an invalid tool_use.id")
                self.tool_use_ids.append(tool_id)
                with tracer.start_as_current_span("tool.get_controlled_evidence") as tool_span:
                    tool_span.set_attribute("gen_ai.operation.name", "execute_tool")
                    tool_span.set_attribute("gen_ai.tool.name", "get_controlled_evidence")
                    tool_span.set_attribute("gen_ai.tool.call.id", tool_id)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps([item.model_dump() for item in evidence]),
                        }
                    )
            follow_up = {
                "model": model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "tools": request["tools"],
                "messages": [
                    first_message,
                    {"role": "assistant", "content": assistant["content"]},
                    {"role": "user", "content": results},
                ],
            }
            final_response = await self._post("https://api.anthropic.com/v1/messages", headers=headers, json=follow_up)
            final_response.raise_for_status()
            final = final_response.json()
            text = "".join(block.get("text", "") for block in final.get("content", []) if block.get("type") == "text")
            body = _json_object(text)
            self._cached_research_ids = [str(item) for item in body.get("relevant_evidence_ids", [])]
            first_usage, final_usage = assistant.get("usage", {}), final.get("usage", {})
            input_tokens = int(first_usage.get("input_tokens", 0)) + int(final_usage.get("input_tokens", 0))
            output_tokens = int(first_usage.get("output_tokens", 0)) + int(final_usage.get("output_tokens", 0))
            cache_read_tokens = int(first_usage.get("cache_read_input_tokens", 0)) + int(
                final_usage.get("cache_read_input_tokens", 0)
            )
            cache_creation_tokens = int(first_usage.get("cache_creation_input_tokens", 0)) + int(
                final_usage.get("cache_creation_input_tokens", 0)
            )
            self.usage["research"] = input_tokens + output_tokens
            actual_model = str(final.get("model", model))
            self.actual_models["research"] = actual_model
            span.set_attribute("gen_ai.response.model", actual_model)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            span.set_attribute("gen_ai.usage.cache_read.input_tokens", cache_read_tokens)
            span.set_attribute("gen_ai.usage.cache_creation.input_tokens", cache_creation_tokens)
            span.set_attribute("gen_ai.tool.call.ids", self.tool_use_ids)

    async def critique(self, evidence: Sequence[EvidenceItem], *, profile: str) -> EvidenceCritique:
        body = await self._call(
            "evidence_critic", profile, CRITIQUE_PROMPT, {"evidence": [item.model_dump() for item in evidence]}
        )
        return EvidenceCritique.model_validate(body)

    async def extract(
        self, company: CompanyIdentity, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> ProfileArtifact:
        body = await self._call(
            "profile_extractor",
            profile,
            PROFILE_PROMPT,
            {"company": company.model_dump(), "evidence": [item.model_dump() for item in evidence]},
        )
        return ProfileArtifact.model_validate(body)

    async def qualify(
        self, profile_artifact: ProfileArtifact, evidence: Sequence[EvidenceItem], *, profile: str
    ) -> dict[str, float]:
        body = await self._call(
            "qualification_analyst",
            profile,
            QUALIFICATION_PROMPT,
            {"profile": profile_artifact.model_dump(), "evidence": [item.model_dump() for item in evidence]},
        )
        return _QualificationResponse.from_provider(body).dimensions
