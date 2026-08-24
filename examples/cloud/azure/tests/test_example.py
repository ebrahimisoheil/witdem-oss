import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_two_modes_share_an_sdk_free_workload() -> None:
    assert {"app.py", "otel_only.py", "sdk_enriched.py"} <= {path.name for path in ROOT.iterdir()}
    app = (ROOT / "app.py").read_text()
    otel = (ROOT / "otel_only.py").read_text()
    sdk = (ROOT / "sdk_enriched.py").read_text()
    assert "import witdem" not in app and "from witdem" not in app
    assert "witdem_sdk" not in otel
    assert "from witdem_sdk.integrations.generic import instrument" in sdk


def test_entrypoints_compile() -> None:
    for name in ("app.py", "otel_only.py", "sdk_enriched.py"):
        ast.parse((ROOT / name).read_text(), filename=name)
