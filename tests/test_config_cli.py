from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pytest

from witdem import cli


@pytest.mark.parametrize("unsafe", [Path.home(), Path.cwd(), Path(Path.cwd().anchor)])
def test_reset_rejects_broad_unsafe_roots(unsafe: Path) -> None:
    with pytest.raises(SystemExit, match="unsafe data directory"):
        cli._validated_data_root(unsafe)


def test_reset_backs_up_only_witdem_targets(tmp_path: Path) -> None:
    root = tmp_path / "witdem-data"
    root.mkdir()
    unrelated = root / "keep-me.txt"
    unrelated.write_text("user data", encoding="utf-8")
    database = root / "live.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE sample(value INTEGER)")
    connection.close()

    cli._reset(argparse.Namespace(live=True, data_dir=str(root), yes=True, no_backup=False))

    backups = list(tmp_path.glob("witdem-data.backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "live.duckdb").exists()
    assert not (backups[0] / "keep-me.txt").exists()
    assert unrelated.read_text(encoding="utf-8") == "user data"
    assert database.exists()


def test_port_probe_reports_bound_socket() -> None:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        port = int(sock.getsockname()[1])
        assert not cli._port_available("127.0.0.1", port)
    finally:
        sock.close()
    assert cli._port_available("127.0.0.1", port)
