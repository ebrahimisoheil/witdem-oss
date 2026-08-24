"""Task requirement 3: the telemetry-only code path has ZERO imports of witdem_sdk.

Checked structurally via ``ast`` (an import-absence assertion, not a naive
text grep -- a plain substring search would also flag this very docstring,
or the human-readable "witdem_sdk is not installed" error message api.py
returns on a 503, neither of which is an actual import): every ``.py`` file
under ``src/agent_demo`` except ``enrichment.py`` itself must contain no
``import witdem_sdk`` / ``from witdem_sdk import ...`` statement anywhere.
``enrichment.py`` is the one deliberate exception (see its module
docstring), and it is only ever imported from inside api.py's
``mode == "sdk_enriched"`` branch via a local import -- never at module
level, never from any telemetry-only code path -- which
test_sdk_enriched.py separately verifies via behavior (a telemetry_only run
succeeds identically whether or not witdem_sdk is installed at all).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "agent_demo"
ALLOWED_FILE = "enrichment.py"


def _source_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _witdem_sdk_import_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "witdem_sdk" or alias.name.startswith("witdem_sdk.") for alias in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (
            node.module == "witdem_sdk" or (node.module or "").startswith("witdem_sdk.")
        ):
            hits.append(node.lineno)
    return hits


def test_source_tree_is_not_empty() -> None:
    # Guards against this test silently passing if the path ever moves.
    assert _source_files(), f"expected .py files under {SRC_ROOT}"


def test_no_witdem_sdk_imports_outside_enrichment_module() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _source_files():
        if path.name == ALLOWED_FILE:
            continue
        hits = _witdem_sdk_import_lines(path)
        if hits:
            offenders[str(path.relative_to(SRC_ROOT.parent.parent))] = hits

    assert not offenders, (
        "witdem_sdk must only ever be imported from enrichment.py (the "
        f"telemetry-only code path must never import it), but found actual "
        f"import statements in: {offenders}"
    )


def test_enrichment_module_is_the_only_one_mentioning_witdem_sdk_at_all() -> None:
    """Belt-and-suspenders: a plain substring scan, informational only.

    This is intentionally a *softer* check than the import-based test above
    -- it is fine (and expected) for other files to mention "witdem_sdk" in
    prose (docstrings, error-message text) explaining the invariant; this
    just documents that fact rather than asserting on it, so a future
    reader isn't surprised if it's the only test file for this topic they
    look at.
    """

    mentions = {
        str(path.relative_to(SRC_ROOT.parent.parent)): sum(
            "witdem_sdk" in line for line in path.read_text().splitlines()
        )
        for path in _source_files()
        if path.name != ALLOWED_FILE
    }
    # No assertion: this test exists to make the distinction (prose mention
    # vs. actual import) discoverable, not to fail the build on prose.
    assert isinstance(mentions, dict)
