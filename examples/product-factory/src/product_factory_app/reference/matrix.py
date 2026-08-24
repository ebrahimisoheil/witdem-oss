"""The 20-cell portability proof and 44-cell sensitivity experiment."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from product_factory_app.reference.cases import case_ids
from product_factory_app.reference.profiles import RUNTIME_DEFAULT_PROFILE
from product_factory_app.reference.runner import run_case

REQUIRED_CREDENTIALS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY")


@dataclass(frozen=True)
class MatrixCell:
    runtime_id: str
    case_id: str
    model_profile: str
    baseline: bool

    @property
    def cell_id(self) -> str:
        return f"{self.runtime_id}__{self.case_id}__{self.model_profile}"


def cross_runtime_cells() -> list[MatrixCell]:
    return [
        MatrixCell(runtime, case_id, RUNTIME_DEFAULT_PROFILE[runtime], True)
        for runtime in RUNTIME_DEFAULT_PROFILE
        for case_id in case_ids()
    ]


def all_cells() -> list[MatrixCell]:
    cells = cross_runtime_cells()
    extras = [
        *(MatrixCell("openai_agents", case_id, "openai-full", False) for case_id in case_ids()),
        *(MatrixCell("anthropic_messages", case_id, "anthropic-sonnet", False) for case_id in case_ids()),
    ]
    for profile in ("openai-mini", "anthropic-haiku", "deepseek-v4-flash", "mistral-small"):
        extras.extend(MatrixCell("langgraph", case_id, profile, False) for case_id in case_ids())
    cells.extend(extras)
    assert len(cells) == 44 and len({cell.cell_id for cell in cells}) == 44
    return cells


def preflight_live() -> None:
    missing = [name for name in REQUIRED_CREDENTIALS if not os.getenv(name)]
    if missing:
        raise RuntimeError("Authoritative matrix requires all provider credentials; missing: " + ", ".join(missing))


def _write_reports(
    report_dir: Path, manifest: dict[str, Any], records: list[dict[str, Any]], *, resume: bool = False
) -> None:
    report_dir.mkdir(parents=True, exist_ok=resume)
    (report_dir / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (report_dir / "cells.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8"
    )
    active_records = [record for record in records if "superseded_by" not in record]
    achieved = sum(bool(record["result"]["goal"]["product_goal_achieved"]) for record in active_records)
    correct = sum(bool(record["result"]["goal"]["decision_correct"]) for record in active_records)
    false_acceptances = sum(
        record["result"]["decision"]["observed_status"] == "accepted"
        and record["result"]["expected_status"] != "accepted"
        for record in active_records
    )
    false_rejections = sum(
        record["result"]["decision"]["observed_status"] == "rejected"
        and record["result"]["expected_status"] != "rejected"
        for record in active_records
    )
    correct_records = [record for record in active_records if record["result"]["goal"]["decision_correct"]]

    def observed_tokens(record: dict[str, Any]) -> float:
        fact = record["result"].get("analytics_fact") or {}
        if fact.get("total_tokens") is not None:
            return float(fact["total_tokens"])
        return float(sum(record["result"]["runtime"]["usage"].values()))

    correct_count = len(correct_records)
    known_costs = [
        float(record["result"]["measured_cost_usd"])
        for record in active_records
        if record["result"]["measured_cost_usd"] is not None
    ]
    correct_costs = [
        float(record["result"]["measured_cost_usd"])
        for record in correct_records
        if record["result"]["measured_cost_usd"] is not None
    ]
    summary = {
        "total": len(active_records),
        "attempts": len(records),
        "valid_cells": sum(bool(record.get("cell_valid")) for record in active_records),
        "invalid_cells": sum(not bool(record.get("cell_valid")) for record in active_records),
        "terminal": sum(bool(record["result"]["runtime"]["terminal"]) for record in active_records),
        "product_goal_achieved": achieved,
        "decision_correct": correct,
        "product_goal_success_rate": achieved / len(active_records) if active_records else 0,
        "decision_correctness_rate": correct / len(active_records) if active_records else 0,
        "false_acceptances": false_acceptances,
        "false_rejections": false_rejections,
        "total_tokens": sum(observed_tokens(record) for record in active_records),
        "measured_cost_usd": sum(known_costs) if known_costs else None,
        "cost_coverage": len(known_costs) / len(active_records) if active_records else 0,
        "cost_per_correct_decision": (
            sum(correct_costs) / len(correct_costs)
            if correct_costs
            else None
        ),
        "time_per_correct_decision": (
            sum(record["result"]["latency_seconds"] or 0 for record in correct_records) / correct_count
            if correct_count
            else None
        ),
        "tokens_per_correct_decision": (
            sum(observed_tokens(record) for record in correct_records) / correct_count
            if correct_count
            else None
        ),
    }
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (report_dir / "summary.md").write_text(
        "# Product Factory matrix\n\n"
        f"- Runs: {summary['total']}\n"
        f"- Terminal: {summary['terminal']}\n"
        f"- Valid analytics cells: {summary['valid_cells']}\n"
        f"- Invalid analytics cells: {summary['invalid_cells']}\n"
        f"- Product goals achieved: {achieved}\n"
        f"- Correct decisions: {correct}\n"
        f"- Total tokens: {summary['total_tokens']}\n",
        encoding="utf-8",
    )


async def run_matrix(
    *,
    suite: str,
    live: bool,
    confirm_live: bool,
    repetitions: int = 1,
    max_cost_usd: float | None = None,
    reports_root: Path = Path("reports"),
    telemetry: bool = True,
    resume: Path | None = None,
) -> Path:
    if live and not confirm_live:
        raise RuntimeError("Live matrix execution requires --confirm-live")
    if live:
        preflight_live()
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    cells = cross_runtime_cells() if suite == "cross-runtime" else all_cells()
    report_dir = resume or reports_root / datetime.now(timezone.utc).strftime("matrix-%Y%m%dT%H%M%SZ")
    manifest = {
        "contract_version": "1.0",
        "suite": suite,
        "live": live,
        "repetitions": repetitions,
        "unique_cells": len(cells),
        "cells": [asdict(cell) for cell in cells],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if resume is None:
        report_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    completed_keys: set[tuple[str, int]] = set()
    if resume is not None:
        cells_file = resume / "cells.jsonl"
        if not cells_file.is_file():
            raise ValueError(f"Resume directory has no cells.jsonl: {resume}")
        records = [json.loads(line) for line in cells_file.read_text(encoding="utf-8").splitlines() if line]
        for record in records:
            result = record.get("result", {})
            valid = (
                bool(record.get("cell_valid"))
                and bool(result.get("runtime", {}).get("terminal"))
                and bool(result.get("trace_id") or not telemetry)
                and bool(result.get("goal"))
                and (not live or not telemetry or result.get("analytics_status") == "ready")
            )
            if valid:
                completed_keys.add(
                    (
                        str(
                            record["cell"]["runtime_id"]
                            + "__"
                            + record["cell"]["case_id"]
                            + "__"
                            + record["cell"]["model_profile"]
                        ),
                        int(record["repetition"]),
                    )
                )
    spent = 0.0
    for repetition in range(1, repetitions + 1):
        for cell in cells:
            if (cell.cell_id, repetition) in completed_keys:
                continue
            result = await run_case(cell.case_id, cell.runtime_id, cell.model_profile, live=live, telemetry=telemetry)
            for prior in records:
                prior_cell = prior.get("cell", {})
                if (
                    prior_cell.get("runtime_id") == cell.runtime_id
                    and prior_cell.get("case_id") == cell.case_id
                    and prior_cell.get("model_profile") == cell.model_profile
                    and prior.get("repetition") == repetition
                    and "superseded_by" not in prior
                ):
                    prior["superseded_by"] = result.execution_id
            required_topology = {
                "research",
                "evidence_critique",
                "profile_extraction",
                "profile_validation",
                "qualification_analysis",
                "deterministic_decision",
                "deterministic_goal_assessment",
            }
            record = {
                "cell": asdict(cell),
                "repetition": repetition,
                "topology_valid": required_topology.issubset(result.runtime.topology),
                "analytics_valid": not live or not telemetry or result.analytics_status == "ready",
                "delivery_valid": not telemetry or int(result.delivery_status.get("pending") or 0) == 0,
                "result": result.model_dump(mode="json"),
            }
            record["cell_valid"] = bool(
                record["topology_valid"] and record["analytics_valid"] and record["delivery_valid"]
            )
            records.append(record)
            # A paid matrix is checkpointed after every cell so an infrastructure
            # interruption can resume without repeating completed provider calls.
            _write_reports(report_dir, manifest, records, resume=True)
            spent += result.measured_cost_usd or 0
            if max_cost_usd is not None and spent > max_cost_usd:
                raise RuntimeError(f"Observed measured cost ${spent:.4f} exceeded --max-cost-usd ${max_cost_usd:.4f}")
    _write_reports(report_dir, manifest, records, resume=True)
    return report_dir
