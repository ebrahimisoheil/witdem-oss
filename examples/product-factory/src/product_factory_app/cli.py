"""Command-line entry points for research, inspection, and experiments."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def _base_parser(description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=description)


def _result_summary(result: object) -> str:
    from product_factory_app.reference.contracts import ProductFactoryResult

    assert isinstance(result, ProductFactoryResult)
    usage = sum(result.runtime.usage.values())
    cost = (
        f"${result.measured_cost_usd:.6f}"
        if result.measured_cost_usd is not None
        else (f"unavailable ({result.cost_unavailable_reason})")
    )
    return "\n".join(
        [
            f"Execution: {result.execution_id}",
            f"Dashboard: {result.dashboard_url}",
            f"Runtime health: {'terminal' if result.runtime.terminal else 'failed'}",
            f"Artifact: {'valid' if result.decision.artifact_valid else 'invalid'}",
            f"Outcome: expected {result.expected_status.value}, observed {result.decision.observed_status.value}",
            f"Product goal: {'achieved' if result.goal.product_goal_achieved else 'failed'}",
            f"Closest blocker: {result.goal.closest_blocker}",
            f"Threshold margin: {result.decision.threshold_margin}",
            f"Usage: {usage} tokens",
            f"Measured cost: {cost}",
        ]
    )


def _reference_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from product_factory_app.reference.matrix import all_cells, cross_runtime_cells, run_matrix
    from product_factory_app.reference.runner import run_case

    if args.command == "run":
        if args.live and not args.confirm_live:
            parser.error("--live requires --confirm-live")
        result = asyncio.run(
            run_case(args.case_id, args.runtime, args.profile, live=args.live, telemetry=not args.no_telemetry)
        )
        print(result.model_dump_json(indent=2) if args.json else _result_summary(result))
        return
    if args.command == "matrix":
        cells = cross_runtime_cells() if args.suite == "cross-runtime" else all_cells()
        print(f"Planned matrix: {len(cells)} unique cells × {args.repetitions} repetition(s)")
        for cell in cells:
            print(f"  {cell.cell_id}")
        report = asyncio.run(
            run_matrix(
                suite=args.suite,
                live=args.live,
                confirm_live=args.confirm_live,
                repetitions=args.repetitions,
                max_cost_usd=args.max_cost_usd,
                reports_root=Path(args.reports_dir),
                resume=Path(args.resume) if args.resume else None,
            )
        )
        print(report)
        return
    if args.command == "inspect":
        reports_dir = Path(args.reports_dir)
        for cells_path in sorted(reports_dir.glob("matrix-*/cells.jsonl"), reverse=True):
            for line in cells_path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record["result"]["execution_id"] == args.execution:
                    print(json.dumps(record, indent=2, ensure_ascii=False))
                    return
        parser.error(f"execution {args.execution!r} was not found under {reports_dir}")


def research_command() -> None:
    """Authoritative Product Factory command surface."""

    from dotenv import load_dotenv

    # The shared credentials file intentionally lives at examples/.env so all
    # tutorial applications and Product Factory consume one local secret source.
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    load_dotenv()
    parser = _base_parser("Witdem Product Factory reference implementation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run one controlled case")
    run_parser.add_argument("--case", dest="case_id", required=True)
    run_parser.add_argument(
        "--runtime",
        required=True,
        choices=["langchain", "langgraph", "haystack", "openai_agents", "anthropic_messages"],
    )
    run_parser.add_argument("--profile")
    run_parser.add_argument("--live", action="store_true")
    run_parser.add_argument("--confirm-live", action="store_true")
    run_parser.add_argument("--no-telemetry", action="store_true", help="deterministic local debugging only")
    run_parser.add_argument("--json", action="store_true")

    matrix_parser = subparsers.add_parser("matrix", help="run the cross-runtime or full matrix")
    matrix_parser.add_argument("--suite", choices=["cross-runtime", "all"], default="cross-runtime")
    matrix_parser.add_argument("--live", action="store_true")
    matrix_parser.add_argument("--confirm-live", action="store_true")
    matrix_parser.add_argument("--repetitions", type=int, default=1)
    matrix_parser.add_argument("--max-cost-usd", type=float)
    matrix_parser.add_argument("--resume")
    matrix_parser.add_argument("--reports-dir", default="reports")

    inspect_parser = subparsers.add_parser("inspect", help="inspect a matrix execution")
    inspect_parser.add_argument("--execution", required=True)
    inspect_parser.add_argument("--reports-dir", default="reports")

    args = parser.parse_args()
    _reference_command(args, parser)
