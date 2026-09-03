from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from witdem_sdk._contract import DescriptiveContractSpec, load_project_config

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTED_CONTRACTS = tuple(sorted((ROOT / "docs" / "contracts").glob("*.yml")))
INTERNAL_CONTRACTS = tuple(sorted((ROOT / "examples").glob("**/.witdem/witdem.yaml")))


@pytest.mark.parametrize("path", DOCUMENTED_CONTRACTS, ids=lambda path: path.name)
def test_documented_contract_is_complete_and_compiles(path: Path) -> None:
    contract = DescriptiveContractSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert contract.version == 2
    assert contract.goal.requirements


@pytest.mark.parametrize(
    "path",
    INTERNAL_CONTRACTS,
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_internal_example_contract_compiles_as_v2_external_definition(path: Path) -> None:
    config = load_project_config(path, required=True)

    assert config is not None
    assert config.version == 2
    assert config.contracts
    assert all(contract.version == 2 for contract in config.contracts.values())


def test_contract_tutorial_links_every_compiled_example() -> None:
    tutorial = (ROOT / "docs" / "contract-tutorial.md").read_text(encoding="utf-8")

    for path in DOCUMENTED_CONTRACTS:
        assert f"contracts/{path.name}" in tutorial
