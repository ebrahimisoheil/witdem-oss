"""Controlled case loading and evidence access."""

from __future__ import annotations

from pathlib import Path

import yaml

from product_factory_app.reference.contracts import CaseDefinition, EvidenceItem, RuntimeCase


def cases_directory() -> Path:
    packaged = Path(__file__).with_name("case_data")
    return packaged if packaged.is_dir() else Path(__file__).resolve().parents[3] / "cases"


def case_ids() -> tuple[str, ...]:
    return tuple(path.stem for path in sorted(cases_directory().glob("*.yaml")))


def load_case(case_id: str) -> CaseDefinition:
    path = cases_directory() / f"{case_id}.yaml"
    if not path.is_file():
        raise ValueError(f"Unknown Product Factory case {case_id!r}; choose from {', '.join(case_ids())}")
    return CaseDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def runtime_case(case: CaseDefinition) -> RuntimeCase:
    """Return only agent-visible fields, proving truth labels cannot leak."""

    return RuntimeCase(
        case_id=case.case_id,
        company=case.company,
        policy=case.policy,
        pass_one=case.pass_one,
        targeted_research=case.targeted_research,
    )


class ControlledEvidenceTool:
    """The only evidence source used by the authoritative matrix."""

    def __init__(self, case: RuntimeCase) -> None:
        self._case = case
        self.queries: list[str] = []

    def initial(self) -> list[EvidenceItem]:
        return list(self._case.pass_one)

    def targeted(self, query: str) -> list[EvidenceItem]:
        self.queries.append(query)
        return list(self._case.targeted_research.get(query, []))
