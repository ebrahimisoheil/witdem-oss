from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = (
    "openai/basic_agent",
    "openai/multi_agent",
    "anthropic/basic_agent",
    "anthropic/tool_loop",
    "langchain/runnable_pipeline",
    "langgraph/state_graph",
    "haystack/pipeline",
    "cloud/azure",
    "cloud/bedrock",
    "cloud/vertex",
    "ollama/basic",
)


def test_tutorials_have_symmetric_external_consumer_shape() -> None:
    for name in TUTORIALS:
        directory = ROOT / "examples" / name
        assert {"pyproject.toml", ".env.example", "app.py", "otel_only.py", "sdk_enriched.py"} <= {
            path.name for path in directory.iterdir()
        }


def test_workloads_and_otel_only_entrypoints_do_not_import_the_sdk() -> None:
    for name in TUTORIALS:
        directory = ROOT / "examples" / name
        workload = (directory / "app.py").read_text(encoding="utf-8")
        assert "from witdem" not in workload and "import witdem" not in workload
        assert "witdem_sdk" not in (directory / "otel_only.py").read_text(encoding="utf-8")


def test_enriched_entrypoints_use_the_native_minimal_integration() -> None:
    for name in TUTORIALS:
        directory = ROOT / "examples" / name
        source = (directory / "sdk_enriched.py").read_text(encoding="utf-8")
        project = (directory / "pyproject.toml").read_text(encoding="utf-8").casefold()
        assert "witdem-sdk" in project
        assert "[tool.uv.sources]" in project
        assert "from witdem_sdk.integrations." in source
        assert " import instrument" in source
        if name in {"cloud/azure", "cloud/bedrock", "cloud/vertex", "ollama/basic"}:
            assert "from witdem_sdk.integrations.generic import instrument" in source
        assert "instrument(" in source
        assert "report_result=" not in source
        assert "observe_result=" not in source
        assert "capture_content=" not in source
        assert 'with_name(".env")' in source
        assert "WITDEM_EXECUTION_ID=" not in source
        assert "with configure()" not in source
        assert "WITDEM_EXECUTION_ID=" in (ROOT / "examples" / name / "otel_only.py").read_text(encoding="utf-8")


def test_sdk_tutorials_use_one_declarative_application_contract() -> None:
    for name in TUTORIALS:
        directory = ROOT / "examples" / name
        source = (directory / "sdk_enriched.py").read_text(encoding="utf-8")
        assert "report_result" not in source
        assert "observe_result" not in source
        assert not any(
            semantic_method in source
            for semantic_method in ("witdem.decision(", "witdem.evaluation(", "witdem.metric(", "witdem.outcome(")
        )
        contract = (directory / ".witdem" / "witdem.yaml").read_text(encoding="utf-8")
        parsed = yaml.safe_load(contract)
        assert isinstance(parsed["contracts"], list)
        configured = parsed["contracts"][0]
        assert parsed["telemetry"]["capture_content"] is False
        assert configured["application_outcome"]["status"]
        assert configured["artifact"]["valid"]
        assert configured["decision"]["observed"]
        assert configured["product_goal"]["description"]
        assert configured["metrics"]
        assert "{" not in contract and "}" not in contract


def test_anthropic_tool_loop_never_contains_a_fabricated_tool_id() -> None:
    directory = ROOT / "examples" / "anthropic" / "tool_loop"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in directory.glob("*.py"))
    assert "demo-0" not in sources
    assert "tool_use_id" in sources
