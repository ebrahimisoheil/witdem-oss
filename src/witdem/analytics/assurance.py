"""Storage-independent OSS goal portfolio and declared-assurance calculations.

Inputs retain the canonical reader's ordering and latest-evaluation selection.
Assurance describes reported evidence, never regulatory certification.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from witdem.analytics.contract_catalog import contract_definition_metadata
from witdem.analytics.core import Evaluation, Event, Outcome
from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.serving import build_serving_rows


def _attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


AssuranceState = Literal["assured", "needs_attention", "not_achieved", "unassessed"]


def goal_assurance_state(row: Mapping[str, Any]) -> AssuranceState:
    if row.get("product_goal_achieved") is not True:
        return "not_achieved"
    explicit = str(row.get("assurance_status") or "").strip().casefold()
    if explicit == "assured":
        return "assured"
    if explicit == "needs_attention":
        return "needs_attention"
    evidence_sufficient = row.get("evidence_sufficient")
    if evidence_sufficient is True:
        return "assured"
    if evidence_sufficient is False:
        return "needs_attention"
    return "unassessed"


def evaluation_met_target(row: dict[str, Any], attributes: dict[str, Any]) -> bool | None:
    passed = attributes.get("passed")
    if isinstance(passed, bool):
        return passed
    score = row.get("score") if row.get("score") is not None else row.get("value")
    target = attributes.get("target")
    direction = str(attributes.get("direction") or "equal").casefold()
    if isinstance(score, (int, float)) and isinstance(target, (int, float)):
        if direction in {"lower_is_better", "max", "at_most", "<="}:
            return float(score) <= float(target)
        if direction in {"higher_is_better", "min", "at_least", ">="}:
            return float(score) >= float(target)
        return float(score) == float(target)
    observed = row.get("value") if row.get("value") is not None else row.get("label")
    if target is not None and observed is not None:
        return bool(observed == target)
    return None


def latest_goal_evaluations(
    facts: Sequence[dict[str, Any]], allowed: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select the repository's latest observed evaluation per execution/key."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in facts:
        execution_id = str(fact["execution_id"])
        if allowed is not None and execution_id not in allowed:
            continue
        attributes = _attributes(fact.get("attributes"))
        key = str(attributes.get("evaluation_key") or fact.get("name") or "Evaluation")
        existing = latest.get((execution_id, key))
        if existing is None or str(fact.get("observed_at") or "") >= str(existing.get("observed_at") or ""):
            latest[(execution_id, key)] = fact
    return list(latest.values())


@dataclass(frozen=True)
class GoalContractContext:
    """Selected contract identity, independent of whether a goal was reported."""

    contract_hash: str | None
    contract_name: str | None
    observed_at: datetime | None


@dataclass(frozen=True)
class GoalPortfolioProjection:
    """In-process inputs for a portfolio adapter, not a persisted wire contract."""

    contract: GoalContractContext | None
    groups: list[dict[str, Any]]
    counts: dict[str, int | float]
    assurance_state: AssuranceState
    definition: dict[str, Any] | None = None


def project_goal_assurance(bundle: EvidenceBundle) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Return the goal-only accumulator; preserve the existing pair interface."""
    projection = project_goal_portfolio(bundle)
    return projection.groups, projection.counts


def project_goal_portfolio(bundle: EvidenceBundle) -> GoalPortfolioProjection:
    """Build one execution's accumulator through the canonical serving projection.

    No operations or prompt/response attributes are included in the accumulator.
    Contract/evaluation descriptive metadata follows the public portfolio rules.
    This per-execution calculation must run before a storage write transaction.
    """
    records: list[Event | Evaluation | Outcome] = [
        *bundle.events, *bundle.evaluations, *sorted(bundle.outcomes, key=lambda item: item.timestamp)
    ]
    execution_id = bundle.execution.execution_id
    if any(item.execution_id != execution_id for item in records):
        raise ValueError("assurance record belongs to another execution")
    serving = build_serving_rows(bundle.execution, [], [], records,
                                 transformed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
                                 transform_version="goal-assurance-1")
    facts = serving["semantic_facts"]
    contracts = sorted((fact for fact in facts if fact["name"] == "contract.definition"),
                       key=lambda fact: (fact["observed_at"] is not None, fact["observed_at"]), reverse=True)
    definition = _attributes(contracts[0]["attributes"]) if contracts else {}
    row = {**serving["execution_facts"][0], "contract_hash": definition.get("contract_hash"),
           "contract_name": definition.get("contract_name")}
    evaluations = latest_goal_evaluations([fact for fact in facts if fact["record_type"] == "evaluation"])
    groups, counts = accumulate_goal_assurance([row], {execution_id: evaluations},
                                             {str(definition.get("contract_hash") or ""): definition})
    # Do not expose the definition's arbitrary attributes. In particular, an
    # unreported goal still belongs to its contract's execution population.
    contract = GoalContractContext(
        contract_hash=str(definition["contract_hash"]) if definition.get("contract_hash") else None,
        contract_name=str(definition["contract_name"]) if definition.get("contract_name") else None,
        observed_at=contracts[0]["observed_at"],
    ) if contracts else None
    return GoalPortfolioProjection(contract=contract, groups=groups, counts=counts,
                                   assurance_state=goal_assurance_state(row),
                                   definition=contract_definition_metadata(definition) if contracts else None)


def summarize_goal_assurance(
    rows: Sequence[dict[str, Any]],
    evaluations_by_execution: Mapping[str, list[dict[str, Any]]],
    definitions: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Reduce selected public facts without storage access or input mutation.

    Callers supply one selected execution fact and latest evaluation per key.
    Preserve their input order: first metadata and contract ordering are part
    of the existing dashboard behavior. Explicit assurance is not inferred
    from a passing evaluation or from successful runtime completion.
    """
    groups, summary = accumulate_goal_assurance(rows, evaluations_by_execution, definitions)
    return finalize_goal_assurance(groups, summary)


def accumulate_goal_assurance(
    rows: Sequence[dict[str, Any]],
    evaluations_by_execution: Mapping[str, list[dict[str, Any]]],
    definitions: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Return pre-ratio counts and score sums for the selected canonical facts.

    This is an in-process calculation API, not a persisted wire contract. Storage
    adapters must version their records, preserve metadata/contract ordering and
    deduplicate memberships when combining these accumulators. Counts and score
    sums are additive; identifiers and descriptive metadata are not.
    """
    grouped: dict[str, dict[str, Any]] = {}
    summary: dict[str, int | float] = {
        "reported_runs": 0,
        "achieved_runs": 0,
        "assured_runs": 0,
        "attention_runs": 0,
        "not_achieved_runs": 0,
        "unassessed_runs": 0,
    }
    for row in rows:
        if row.get("product_goal_reported") is not True:
            continue
        contract_hash = str(row.get("contract_hash") or "unversioned")
        definition = definitions.get(contract_hash, {})
        goal_definition = definition.get("product_goal") if isinstance(definition.get("product_goal"), dict) else {}
        goal_name = str(
            (goal_definition or {}).get("name")
            or row.get("contract_name")
            or definition.get("contract_name")
            or "Business goal"
        )
        description = (goal_definition or {}).get("description")
        goal_key = f"{goal_name.casefold()}::{str(description or '').casefold()}"
        item = grouped.setdefault(
            goal_key,
            {
                "goal_id": goal_key,
                "contract_hashes": [],
                "contract_name": row.get("contract_name") or definition.get("contract_name"),
                "goal_name": goal_name,
                "description": description,
                "single_execution_id": str(row["execution_id"]),
                "runs": 0,
                "achieved_runs": 0,
                "assured_runs": 0,
                "attention_runs": 0,
                "not_achieved_runs": 0,
                "unassessed_runs": 0,
                "evaluations": {},
            },
        )
        if contract_hash not in item["contract_hashes"]:
            item["contract_hashes"].append(contract_hash)
        item["runs"] += 1
        summary["reported_runs"] = int(summary["reported_runs"]) + 1
        achieved = row.get("product_goal_achieved") is True
        if not achieved:
            item["not_achieved_runs"] += 1
            summary["not_achieved_runs"] = int(summary["not_achieved_runs"]) + 1
            continue
        item["achieved_runs"] += 1
        summary["achieved_runs"] = int(summary["achieved_runs"]) + 1
        facts = evaluations_by_execution.get(str(row["execution_id"]), [])
        for fact in facts:
            attributes = _attributes(fact.get("attributes"))
            met = evaluation_met_target(fact, attributes)
            if met is None:
                continue
            key = str(attributes.get("evaluation_key") or fact.get("name") or "Evaluation")
            evaluation = item["evaluations"].setdefault(
                key,
                {
                    "key": key,
                    "name": str(fact.get("name") or key),
                    "description": attributes.get("evaluation_description"),
                    "unit": attributes.get("unit"),
                    "target": attributes.get("target"),
                    "direction": attributes.get("direction"),
                    "reported_runs": 0,
                    "passed_runs": 0,
                    "attention_runs": 0,
                    "score_total": 0.0,
                    "score_runs": 0,
                },
            )
            evaluation["reported_runs"] += 1
            evaluation["passed_runs"] += int(met)
            evaluation["attention_runs"] += int(not met)
            if isinstance(fact.get("score"), (int, float)):
                evaluation["score_total"] += float(fact["score"])
                evaluation["score_runs"] += 1
        explicit_assurance = goal_assurance_state(row)
        if explicit_assurance == "needs_attention":
            item["attention_runs"] += 1
            summary["attention_runs"] = int(summary["attention_runs"]) + 1
        elif explicit_assurance == "assured":
            item["assured_runs"] += 1
            summary["assured_runs"] = int(summary["assured_runs"]) + 1
        else:
            item["unassessed_runs"] += 1
            summary["unassessed_runs"] = int(summary["unassessed_runs"]) + 1

    return list(grouped.values()), summary


def finalize_goal_assurance(
    groups: Sequence[dict[str, Any]], summary_counts: Mapping[str, int | float],
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Render complete accumulators without averaging averages or mutating input."""
    summary = dict(summary_counts)
    portfolio: list[dict[str, Any]] = []
    for item in deepcopy(groups):
        evaluations = []
        for evaluation in item.pop("evaluations").values():
            score_runs = int(evaluation.pop("score_runs"))
            score_total = float(evaluation.pop("score_total"))
            evaluations.append({**evaluation, "average_score": score_total / score_runs if score_runs else None})
        achieved_runs = int(item["achieved_runs"])
        assessed_runs = int(item["assured_runs"]) + int(item["attention_runs"])
        attention = sorted(evaluations, key=lambda value: (-int(value["attention_runs"]), str(value["name"])))
        portfolio.append(
            {
                **item,
                "single_execution_id": item["single_execution_id"] if int(item["runs"]) == 1 else None,
                "contract_hash": item["contract_hashes"][0] if len(item["contract_hashes"]) == 1 else None,
                "contract_count": len(item["contract_hashes"]),
                "success_rate": achieved_runs / int(item["runs"]) if item["runs"] else 0.0,
                "assurance_rate": int(item["assured_runs"]) / achieved_runs if achieved_runs else 0.0,
                "assessment_coverage": assessed_runs / achieved_runs if achieved_runs else 0.0,
                "top_attention": attention[0] if attention and attention[0]["attention_runs"] else None,
                "evaluations": evaluations,
            }
        )
    achieved_total = int(summary["achieved_runs"])
    assessed_total = int(summary["assured_runs"]) + int(summary["attention_runs"])
    summary["assurance_rate"] = int(summary["assured_runs"]) / achieved_total if achieved_total else 0.0
    summary["attention_rate"] = int(summary["attention_runs"]) / achieved_total if achieved_total else 0.0
    summary["assessment_coverage"] = assessed_total / achieved_total if achieved_total else 0.0
    return sorted(portfolio, key=lambda item: (-int(item["runs"]), str(item["goal_name"]))), summary
