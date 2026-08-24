from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_witdem_product_does_not_import_product_factory_application() -> None:
    offenders = []
    for path in _python_files(ROOT / "src" / "witdem"):
        text = path.read_text(encoding="utf-8")
        if "product_factory_agent" in text or "product_factory_app" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_external_demo_does_not_import_product_factory_application() -> None:
    offenders = []
    for path in _python_files(ROOT / "examples" / "haystack" / "demo" / "src"):
        text = path.read_text(encoding="utf-8")
        if "product_factory_agent" in text or "product_factory_app" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_product_factory_uses_only_public_witdem_boundaries() -> None:
    forbidden = (
        "witdem.ingest",
        "witdem.api",
        "witdem.analytics.core",
        "witdem.analytics.runtime",
        "witdem.analytics.identity",
        "witdem.analytics.schema",
        "witdem.telemetry.otel",
    )
    offenders = []
    for path in _python_files(ROOT / "examples" / "product-factory" / "src"):
        source = path.read_text(encoding="utf-8")
        if any(item in source for item in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_authoritative_product_factory_imports_only_the_public_sdk() -> None:
    offenders = []
    source_root = ROOT / "examples" / "product-factory" / "src" / "product_factory_app"
    for path in _python_files(source_root):
        if "experiments" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("from witdem") or stripped.startswith("import witdem")
            ) and "witdem_sdk" not in stripped:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
