from __future__ import annotations

from pathlib import Path

import pytest

from witdem_sdk._contract import load_project_config

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTED_CONTRACTS = tuple(sorted((ROOT / "docs" / "contracts").glob("*.yaml")))
INTERNAL_CONTRACTS = tuple(sorted((ROOT / "examples").glob("**/.witdem/witdem.yaml")))


@pytest.mark.parametrize("path", DOCUMENTED_CONTRACTS, ids=lambda path: path.name)
def test_documented_contract_is_complete_and_compiles(path: Path) -> None:
    config = load_project_config(path, required=True)

    assert config is not None
    assert config.contracts
    assert all(contract.mode in {"expression", "reported"} for contract in config.contracts.values())


@pytest.mark.parametrize(
    "path",
    INTERNAL_CONTRACTS,
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_internal_example_contract_compiles_with_explicit_mode(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    config = load_project_config(path, required=True)

    assert config is not None
    assert config.contracts
    assert "mode:" in source
    assert all(contract.mode in {"expression", "reported"} for contract in config.contracts.values())


def test_contract_tutorial_links_every_compiled_example() -> None:
    tutorial = (ROOT / "docs" / "contract-tutorial.md").read_text(encoding="utf-8")

    for path in DOCUMENTED_CONTRACTS:
        assert f"contracts/{path.name}" in tutorial
