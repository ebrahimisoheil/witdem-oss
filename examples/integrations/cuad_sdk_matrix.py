"""Live CUAD stress harness for direct SDK and LangGraph execution paths."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from witdem_sdk import Witdem, configure
from witdem_sdk.integrations.anthropic import instrument_anthropic
from witdem_sdk.integrations.openai import instrument_openai

Provider = Literal["anthropic", "openai", "deterministic"]
Framework = Literal["direct", "langgraph", "openai_agents", "mcp"]


class ReviewState(TypedDict):
    contract: str
    evidence: list[str]
    answer: str
    valid: bool


def _paragraphs(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"\n\s*\n", text) if len(value.strip()) >= 40]


def _keyword_retrieve(text: str, query: str, *, top_k: int = 3) -> list[str]:
    terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
    ranked = sorted(
        _paragraphs(text),
        key=lambda paragraph: len(terms & set(re.findall(r"[a-z0-9]+", paragraph.casefold()))),
        reverse=True,
    )
    return ranked[:top_k]


def _prompt(evidence: list[str]) -> str:
    joined = "\n\n---\n\n".join(evidence)
    return (
        "Review this evidence from a CUAD contract. Return only a JSON object with "
        'keys "risk_summary", "renewal", "termination", and "governing_law". '
        "Use null when the evidence is insufficient. Do not invent clauses.\n\n"
        f"EVIDENCE:\n{joined}"
    )


def _anthropic_text(response: Any) -> str:
    return "".join(
        str(block.text)
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", None) == "text"
    )


def _openai_text(response: Any) -> str:
    value = getattr(response, "output_text", None)
    return str(value) if value is not None else ""


def _valid_json(text: str) -> bool:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "risk_summary" in parsed


def _provider_call(provider: Provider, client: Any, evidence: list[str]) -> str:
    if provider == "deterministic":
        return json.dumps(
            {
                "risk_summary": "Deterministic CUAD fixture review.",
                "renewal": None,
                "termination": None,
                "governing_law": None,
            }
        )
    prompt = _prompt(evidence)
    if provider == "anthropic":
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=500,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return _anthropic_text(response)
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
        instructions="You are a precise contract-review analyst. Output JSON only.",
        input=prompt,
        max_output_tokens=500,
    )
    return _openai_text(response)


def _instrumented_client(provider: Provider, witdem: Witdem) -> Any:
    if provider == "deterministic":
        return None
    if provider == "anthropic":
        from anthropic import Anthropic

        return instrument_anthropic(Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]), witdem=witdem)
    from openai import OpenAI

    return instrument_openai(OpenAI(api_key=os.environ["OPENAI_API_KEY"]), witdem=witdem)


def _analyze(provider: Provider, client: Any, evidence: list[str], witdem: Witdem) -> str:
    if provider != "deterministic":
        return _provider_call(provider, client, evidence)
    with witdem.operation(
        "cuad.deterministic_analysis",
        kind="model",
        family="model",
        operation_type="inference",
        subtype="fixture",
        interface="local",
        implementation_id="deterministic_cuad_fixture",
        provider_id="deterministic",
        model_id="cuad-fixture-v1",
        input_modalities=("text",),
        output_modalities=("json",),
        execution_source="python",
    ):
        return _provider_call(provider, client, evidence)


def _retrieve(witdem: Witdem, contract: str) -> list[str]:
    with witdem.operation(
        "cuad.keyword_retrieval",
        family="knowledge",
        operation_type="retrieval",
        subtype="keyword_search",
        interface="local",
        implementation_id="python_keyword_index",
        input_modalities=("text",),
        output_modalities=("documents",),
        execution_source="python",
    ) as operation:
        evidence = _keyword_retrieve(contract, "renewal termination governing law notice period")
        operation.measure("queries", 1, unit="query", provenance="application_reported")
        operation.measure("documents.output", len(evidence), unit="document", provenance="application_reported")
        operation.measure("top_k", 3, unit="document", provenance="application_reported")
        return evidence


def _validate(witdem: Witdem, answer: str) -> bool:
    with witdem.operation(
        "cuad.structured_validation",
        family="quality",
        operation_type="validation",
        subtype="json_schema",
        interface="local",
        implementation_id="python_json",
        input_modalities=("text",),
        output_modalities=("boolean",),
        execution_source="python",
    ) as operation:
        valid = _valid_json(answer)
        operation.measure("items.input", 1, unit="item", provenance="application_reported")
        operation.measure("items.output", 1 if valid else 0, unit="item", provenance="application_reported")
        return valid


def _direct(provider: Provider, witdem: Witdem, contract: str) -> ReviewState:
    client = _instrumented_client(provider, witdem)
    evidence = _retrieve(witdem, contract)
    answer = _analyze(provider, client, evidence, witdem)
    return {"contract": contract, "evidence": evidence, "answer": answer, "valid": _validate(witdem, answer)}


def _langgraph(provider: Provider, witdem: Witdem, contract: str) -> ReviewState:
    from langgraph.graph import END, StateGraph
    from witdem_sdk.integrations.langgraph import WitdemLangGraphCallback

    client = _instrumented_client(provider, witdem)

    def retrieve_node(state: ReviewState) -> dict[str, Any]:
        return {"evidence": _retrieve(witdem, state["contract"])}

    def analyze_node(state: ReviewState) -> dict[str, Any]:
        return {"answer": _analyze(provider, client, state["evidence"], witdem)}

    def validate_node(state: ReviewState) -> dict[str, Any]:
        return {"valid": _validate(witdem, state["answer"])}

    builder = StateGraph(ReviewState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("validate", validate_node)
    builder.set_entry_point("retrieve")
    builder.add_edge("retrieve", "analyze")
    builder.add_edge("analyze", "validate")
    builder.add_edge("validate", END)
    callbacks = []
    if provider != "deterministic":
        callbacks.append(
            WitdemLangGraphCallback(
                witdem,
                provider=provider,
                model=(
                    os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
                    if provider == "anthropic"
                    else os.getenv("OPENAI_MODEL", "gpt-5.4")
                ),
            )
        )
    return cast(
        ReviewState,
        builder.compile().invoke(
            {"contract": contract, "evidence": [], "answer": "", "valid": False},
            config={"callbacks": callbacks},
        ),
    )


def _openai_agents(witdem: Witdem, contract: str) -> ReviewState:
    from agents import Agent, Runner, function_tool
    from witdem_sdk.integrations.openai_agents import install_openai_agents

    @function_tool
    def search_contract(query: str) -> str:
        """Return relevant evidence from the current CUAD contract."""

        return json.dumps(_retrieve(witdem, contract))

    agent = Agent(
        name="CUAD contract reviewer",
        model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
        instructions=(
            "Always call search_contract once. Then return only a JSON object with keys "
            '"risk_summary", "renewal", "termination", and "governing_law". '
            "Use null when evidence is insufficient."
        ),
        tools=[search_contract],
    )
    registration = install_openai_agents(witdem)
    try:
        result = Runner.run_sync(agent, "Review the contract for renewal, termination, and governing law.")
    finally:
        registration.uninstall()
    answer = str(result.final_output)
    return {
        "contract": contract,
        "evidence": _keyword_retrieve(contract, "renewal termination governing law notice period"),
        "answer": answer,
        "valid": _validate(witdem, answer),
    }


async def _mcp_retrieve(witdem: Witdem, contract: str) -> list[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_path = Path(__file__).with_name("cuad_mcp_server.py")
    parameters = StdioServerParameters(command=sys.executable, args=[str(server_path)])
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        with witdem.operation(
            "mcp.tools.list",
            family="mcp",
            operation_type="capability_discovery",
            interface="mcp",
            provider_id="internal-contract-server",
            implementation_id="python-mcp",
            role="control",
            output_modalities=("structured",),
            execution_source="mcp_client",
        ) as discovery:
            tools = await session.list_tools()
            discovery.measure("items.output", len(tools.tools), unit="tool", provenance="runtime_reported")
        with witdem.operation(
            "mcp.tools.call.search_contract",
            family="knowledge",
            operation_type="retrieval",
            subtype="keyword_search",
            interface="mcp",
            provider_id="internal-contract-server",
            implementation_id="python_keyword_index",
            role="tool",
            input_modalities=("text",),
            output_modalities=("documents",),
            execution_source="mcp_client",
        ) as operation:
            result = await session.call_tool(
                "search_contract",
                {
                    "contract": contract,
                    "query": "renewal termination governing law notice period",
                    "top_k": 3,
                },
            )
            structured = getattr(result, "structured_content", None)
            evidence = structured.get("documents") if isinstance(structured, dict) else None
            if not isinstance(evidence, list):
                text = next(
                    (
                        str(getattr(item, "text", ""))
                        for item in result.content
                        if getattr(item, "type", None) == "text"
                    ),
                    "[]",
                )
                evidence = json.loads(text)
            documents = [str(item) for item in evidence]
            operation.measure("queries", 1, unit="query", provenance="application_reported")
            operation.measure("documents.output", len(documents), unit="document", provenance="runtime_reported")
            operation.measure("top_k", 3, unit="document", provenance="application_reported")
            return documents


def _mcp(provider: Provider, witdem: Witdem, contract: str) -> ReviewState:
    client = _instrumented_client(provider, witdem)
    evidence = asyncio.run(_mcp_retrieve(witdem, contract))
    answer = _provider_call(provider, client, evidence)
    return {"contract": contract, "evidence": evidence, "answer": answer, "valid": _validate(witdem, answer)}


def run(
    provider: Provider,
    framework: Framework,
    contract_path: Path,
    endpoint: str,
    config_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    contract = contract_path.read_text(encoding="utf-8")
    variant = f"{framework}-{provider}"
    with (
        configure(runtime=framework, endpoint=endpoint, config_path=str(config_path)) as witdem,
        witdem.execution(
            f"CUAD {framework} {provider}",
            attributes={
                "stress.variant": variant,
                "stress.dataset": "CUAD",
                "stress.contract": contract_path.name,
                "witdem.operation.family": "orchestration",
                "witdem.operation.type": "workflow",
                "witdem.operation.subtype": "cuad_contract_review",
                "witdem.operation.interface": (
                    "mcp" if framework == "mcp" else "framework" if framework != "direct" else "local"
                ),
                "witdem.framework.id": framework if framework in {"langgraph", "openai_agents"} else None,
                "witdem.execution.source": framework,
            },
        ) as execution_id,
    ):
        with witdem.operation(
            "cuad.document_loading",
            family="data_movement",
            operation_type="document_loading",
            subtype="text_file",
            interface="local",
            implementation_id="pathlib",
            input_modalities=("document",),
            output_modalities=("text",),
            execution_source="python",
        ) as operation:
            operation.measure("documents.input", 1, unit="document", provenance="application_reported")
            operation.measure("bytes.input", contract_path.stat().st_size, unit="byte", provenance="runtime_reported")
        if framework == "direct":
            result = _direct(provider, witdem, contract)
        elif framework == "langgraph":
            result = _langgraph(provider, witdem, contract)
        elif framework == "openai_agents":
            if provider != "openai":
                raise ValueError("openai_agents supports only the OpenAI provider")
            result = _openai_agents(witdem, contract)
        else:
            result = _mcp(provider, witdem, contract)
        witdem.outcome(
            "cuad.review.completed",
            status="success" if result["valid"] else "invalid",
            value={"valid": result["valid"], "variant": variant},
        )
        witdem.report(
            contract="review",
            result="completed" if result["valid"] else "invalid",
            result_valid=result["valid"],
            requirements={
                "evidence_retrieved": bool(result["evidence"]),
                "structured_review": result["valid"],
            },
            dimensions={
                "variant": variant,
                "provider": provider,
                "execution_style": framework,
            },
        )
    return {
        "variant": variant,
        "execution_id": execution_id,
        "valid": result["valid"],
        "evidence_documents": len(result["evidence"]),
        "answer_characters": len(result["answer"]),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("anthropic", "openai", "deterministic"), required=True)
    parser.add_argument("--framework", choices=("direct", "langgraph", "openai_agents", "mcp"), required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--endpoint", default=os.getenv("WITDEM_ENDPOINT", "http://127.0.0.1:4318"))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("cuad") / "witdem.yml",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(
        args.provider,
        args.framework,
        args.contract.expanduser().resolve(),
        args.endpoint,
        args.config.expanduser().resolve(),
    )
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{encoded}\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
