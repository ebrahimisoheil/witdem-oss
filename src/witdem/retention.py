"""Safe time-based retention for the authoritative ingest corpus."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from witdem.config import storage_root
from witdem.elt.worker import run_pending
from witdem.ingest import corpus, live_db


@dataclass(frozen=True)
class RetentionPlan:
    cutoff: str
    older_than_days: int
    batches_to_delete: int
    executions_to_delete: int
    executions_to_rebuild: int
    retained_batches: int
    bytes_to_delete: int
    corpus_bytes_to_delete: int
    elt_bytes_to_delete: int
    ingest_ids: tuple[str, ...]
    execution_ids_to_delete: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RetentionResult:
    status: str
    plan: RetentionPlan
    rebuild: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "plan": self.plan.to_dict(), "rebuild": self.rebuild}


def _observed_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _commit_paths(commit: corpus.CorpusCommit) -> tuple[Path, ...]:
    root = corpus.corpus_root()
    paths = [
        root / commit.records_path,
        root / "state" / f"{commit.ingest_id}.json",
        root / "committed" / f"{commit.ingest_id}.json",
    ]
    if commit.raw_path:
        paths.insert(1, root / commit.raw_path)
    return tuple(paths)


def plan_retention(*, older_than_days: int, now: datetime | None = None) -> RetentionPlan:
    """Describe corpus batches received strictly before the retention cutoff."""

    if older_than_days <= 0:
        raise ValueError("older-than must be at least 1 day")
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=timezone.utc)
    cutoff = observed_now.astimezone(timezone.utc) - timedelta(days=older_than_days)
    commits = corpus.list_commits()
    expired = [commit for commit in commits if _observed_at(commit.received_at) < cutoff]
    retained = [commit for commit in commits if commit not in expired]
    expired_execution_ids = {value for commit in expired for value in commit.execution_ids}
    retained_execution_ids = {value for commit in retained for value in commit.execution_ids}
    execution_ids_to_delete = expired_execution_ids - retained_execution_ids
    executions_to_rebuild = expired_execution_ids & retained_execution_ids
    corpus_size = sum(path.stat().st_size for commit in expired for path in _commit_paths(commit) if path.exists())
    elt_runs = storage_root() / "elt" / "runs"
    elt_size = (
        sum(path.stat().st_size for path in elt_runs.rglob("*") if path.is_file())
        if expired and elt_runs.exists()
        else 0
    )
    return RetentionPlan(
        cutoff=cutoff.isoformat(),
        older_than_days=older_than_days,
        batches_to_delete=len(expired),
        executions_to_delete=len(execution_ids_to_delete),
        executions_to_rebuild=len(executions_to_rebuild),
        retained_batches=len(retained),
        bytes_to_delete=corpus_size + elt_size,
        corpus_bytes_to_delete=corpus_size,
        elt_bytes_to_delete=elt_size,
        ingest_ids=tuple(commit.ingest_id for commit in expired),
        execution_ids_to_delete=tuple(sorted(execution_ids_to_delete)),
    )


def _move(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def _restore_tree(staging: Path, root: Path) -> None:
    if not staging.exists():
        return
    for source in sorted((path for path in staging.rglob("*") if path.is_file()), reverse=True):
        destination = root / source.relative_to(staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def apply_retention(plan: RetentionPlan) -> RetentionResult:
    """Delete the planned batches and rebuild every retained serving projection."""

    if not plan.ingest_ids:
        return RetentionResult(status="unchanged", plan=plan, rebuild={"status": "idle", "batches": 0})
    root = storage_root()
    staging = root / ".retention-staging" / uuid.uuid4().hex
    commits = [corpus.read_commit(ingest_id) for ingest_id in plan.ingest_ids]
    selected = [commit for commit in commits if commit is not None]
    if len(selected) != len(plan.ingest_ids):
        raise RuntimeError("corpus changed after the retention preview; run the command again")
    mutated = False
    try:
        with corpus.maintenance_lock(timeout=60.0), corpus.ingest_lock(timeout=60.0):
            cutoff = _observed_at(plan.cutoff)
            current_ingest_ids = tuple(
                commit.ingest_id for commit in corpus.list_commits() if _observed_at(commit.received_at) < cutoff
            )
            if current_ingest_ids != plan.ingest_ids:
                raise RuntimeError("corpus changed after the retention preview; run the command again")
            corpus_root = corpus.corpus_root()
            for commit in selected:
                for path in _commit_paths(commit):
                    if path.exists():
                        mutated = True
                        _move(path, staging / "corpus" / path.relative_to(corpus_root))
            elt_runs = root / "elt" / "runs"
            if elt_runs.exists():
                _move(elt_runs, staging / "elt" / "runs")
            live_db.delete_execution_projections(plan.execution_ids_to_delete)
            live_db.clear_transform_runs()
            rebuild = run_pending(rebuild=True, maintenance_lock_held=True)
        shutil.rmtree(staging, ignore_errors=True)
        return RetentionResult(status="pruned", plan=plan, rebuild=dict(rebuild))
    except Exception as exc:
        recovery_error: Exception | None = None
        if mutated:
            try:
                with corpus.maintenance_lock(timeout=60.0), corpus.ingest_lock(timeout=60.0):
                    _restore_tree(staging, root)
                    run_pending(rebuild=True, maintenance_lock_held=True)
            except Exception as caught:  # noqa: BLE001 - report both maintenance failures
                recovery_error = caught
        shutil.rmtree(staging, ignore_errors=True)
        if recovery_error is not None:
            raise RuntimeError(f"retention failed and recovery also failed: {recovery_error}") from exc
        raise
