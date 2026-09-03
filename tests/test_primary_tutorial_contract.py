from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TUTORIALS = (
    Path("examples/openai/basic_agent"),
    Path("examples/openai/multi_agent"),
    Path("examples/anthropic/basic_agent"),
    Path("examples/anthropic/tool_loop"),
    Path("examples/langchain/runnable_pipeline"),
    Path("examples/langgraph/state_graph"),
    Path("examples/haystack/pipeline"),
    Path("examples/cloud/azure"),
    Path("examples/cloud/bedrock"),
    Path("examples/cloud/vertex"),
    Path("examples/ollama/basic"),
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize("relative", TUTORIALS, ids=str)
def test_primary_tutorial_is_a_symmetric_external_consumer(relative: Path) -> None:
    tutorial = ROOT / relative
    required = {"pyproject.toml", ".env.example", "app.py", "otel_only.py", "sdk_enriched.py"}
    assert required.issubset({path.name for path in tutorial.iterdir()})

    workload_imports = _imports(tutorial / "app.py")
    assert not any(name == "witdem" or name.startswith("witdem.") for name in workload_imports)
    assert not any(name == "witdem_sdk" or name.startswith("witdem_sdk.") for name in workload_imports)

    otel_imports = _imports(tutorial / "otel_only.py")
    assert not any(name == "witdem_sdk" or name.startswith("witdem_sdk.") for name in otel_imports)

    enriched = (tutorial / "sdk_enriched.py").read_text(encoding="utf-8")
    assert "witdem_sdk" in enriched
    if relative in {
        Path("examples/cloud/azure"),
        Path("examples/cloud/bedrock"),
        Path("examples/cloud/vertex"),
        Path("examples/ollama/basic"),
    }:
        assert "from witdem_sdk.integrations.generic import instrument" in enriched
    assert "instrument(" in enriched
    assert "observe_result=" not in enriched
    assert "report_result=" in enriched
    assert "capture_content=" not in enriched
    assert "WITDEM_EXECUTION_ID=" not in enriched
    assert "with configure()" not in enriched


def test_primary_tutorials_are_documented_in_the_central_catalog() -> None:
    documentation = (ROOT / "docs" / "examples.md").read_text(encoding="utf-8").casefold()
    for relative in TUTORIALS:
        assert str(relative) in documentation
    for required_text in ("witdem dev", "otel_only.py", "sdk_enriched.py", "witdem_endpoint", "missing", "401"):
        assert required_text in documentation
