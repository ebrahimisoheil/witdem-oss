from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from witdem import cli, retention
from witdem.ingest import corpus


def _age_commit(commit: corpus.CorpusCommit, received_at: str) -> None:
    manifest = corpus.corpus_root() / "committed" / f"{commit.ingest_id}.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["received_at"] = received_at
    manifest.write_text(json.dumps(payload), encoding="utf-8")


def test_retention_plan_uses_batch_receipt_time_and_preserves_extended_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    old = corpus.commit_batch("sdk_records", [{"value": "old"}], execution_ids=["expired", "extended"])
    corpus.commit_batch("sdk_records", [{"value": "new"}], execution_ids=["extended", "retained"])
    _age_commit(old, "2026-01-01T00:00:00+00:00")

    plan = retention.plan_retention(
        older_than_days=30,
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert plan.batches_to_delete == 1
    assert plan.retained_batches == 1
    assert plan.execution_ids_to_delete == ("expired",)
    assert plan.executions_to_rebuild == 1
    assert plan.bytes_to_delete > 0


def test_apply_retention_removes_expired_corpus_and_rebuilds_retained_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(tmp_path / "live.duckdb"))
    old = corpus.commit_batch("otel_traces", [{"value": "old"}], execution_ids=["expired"])
    retained = corpus.commit_batch("otel_traces", [{"value": "new"}], execution_ids=["retained"])
    _age_commit(old, "2026-01-01T00:00:00+00:00")
    run_artifact = tmp_path / "elt" / "runs" / "old-transform" / "input.jsonl"
    run_artifact.parent.mkdir(parents=True)
    run_artifact.write_text("old derived data", encoding="utf-8")
    deleted: list[tuple[str, ...]] = []
    cleared: list[bool] = []
    rebuild_calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(retention.live_db, "delete_execution_projections", lambda values: deleted.append(tuple(values)))
    monkeypatch.setattr(retention.live_db, "clear_transform_runs", lambda: cleared.append(True))

    def _rebuild(*, rebuild: bool, maintenance_lock_held: bool) -> dict[str, object]:
        rebuild_calls.append((rebuild, maintenance_lock_held))
        return {"status": "ready", "batches": 1, "executions": 1}

    monkeypatch.setattr(retention, "run_pending", _rebuild)
    plan = retention.plan_retention(
        older_than_days=30,
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    result = retention.apply_retention(plan)

    assert result.status == "pruned"
    assert corpus.read_commit(old.ingest_id) is None
    assert corpus.read_commit(retained.ingest_id) is not None
    assert deleted == [("expired",)]
    assert cleared == [True]
    assert rebuild_calls == [(True, True)]
    assert not run_artifact.exists()
    assert not list((tmp_path / ".retention-staging").glob("*"))


def test_apply_retention_restores_corpus_when_rebuild_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("WITDEM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WITDEM_DB_PATH", str(tmp_path / "live.duckdb"))
    old = corpus.commit_batch("sdk_records", [{"value": "old"}], execution_ids=["expired"])
    _age_commit(old, "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(retention.live_db, "delete_execution_projections", lambda _values: None)
    monkeypatch.setattr(retention.live_db, "clear_transform_runs", lambda: None)
    calls = 0

    def _rebuild(*, rebuild: bool, maintenance_lock_held: bool) -> dict[str, object]:
        nonlocal calls
        assert rebuild is True
        assert maintenance_lock_held is True
        calls += 1
        if calls == 1:
            raise RuntimeError("injected transform failure")
        return {"status": "ready", "batches": 1}

    monkeypatch.setattr(retention, "run_pending", _rebuild)
    plan = retention.plan_retention(
        older_than_days=30,
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(RuntimeError, match="injected transform failure"):
        retention.apply_retention(plan)

    assert calls == 2
    assert corpus.read_commit(old.ingest_id) is not None


@pytest.mark.parametrize("value, expected", [("30d", 30), ("7", 7), ("365D", 365)])
def test_older_than_parser(value: str, expected: int) -> None:
    assert cli._older_than_days(value) == expected


@pytest.mark.parametrize("value", ["0", "0d", "-1d", "30h", "forever"])
def test_older_than_parser_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli._older_than_days(value)


def test_prune_defaults_to_preview_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.live_db, "initialize_analytics_store", lambda _path: None)
    monkeypatch.setattr(
        retention,
        "plan_retention",
        lambda **_kwargs: retention.RetentionPlan(
            cutoff="2026-01-01T00:00:00+00:00",
            older_than_days=30,
            batches_to_delete=2,
            executions_to_delete=1,
            executions_to_rebuild=0,
            retained_batches=3,
            bytes_to_delete=42,
            corpus_bytes_to_delete=42,
            elt_bytes_to_delete=0,
            ingest_ids=("one", "two"),
            execution_ids_to_delete=("expired",),
        ),
    )
    monkeypatch.setattr(retention, "apply_retention", lambda _plan: pytest.fail("preview applied deletion"))

    cli._prune(SimpleNamespace(data_dir=str(tmp_path), db=None, older_than=30, yes=False))

    output = capsys.readouterr().out
    assert '"status": "preview"' in output
    assert "Re-run with --yes" in output
