"""Canonical Witdem configuration and local path resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "witdem"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4318
DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 8501


def data_dir() -> Path:
    """Return the configured persistent data directory."""

    configured = os.getenv("WITDEM_DATA_DIR")
    return Path(configured).expanduser() if configured else Path(user_data_dir(APP_NAME))


def db_path(explicit: str | Path | None = None) -> Path:
    """Resolve the canonical analytics database path."""

    if explicit is not None:
        return Path(explicit).expanduser()
    configured = os.getenv("WITDEM_DB_PATH")
    return Path(configured).expanduser() if configured else data_dir() / "live.duckdb"


def storage_root(explicit: str | Path | None = None) -> Path:
    return Path(explicit).expanduser() if explicit is not None else data_dir()


def endpoint() -> str:
    """Resolve the SDK/server base endpoint."""

    value = os.getenv("WITDEM_ENDPOINT")
    if value:
        return value.rstrip("/")
    return "http://localhost:4318"


@dataclass(frozen=True)
class ResolvedConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    dashboard_host: str = DEFAULT_DASHBOARD_HOST
    dashboard_port: int = DEFAULT_DASHBOARD_PORT
    database: Path = Path()
    data_directory: Path = Path()
    log_level: str = "info"

    @classmethod
    def from_args(cls, args: object | None = None) -> ResolvedConfig:
        values: Mapping[str, object] = vars(args) if args is not None and hasattr(args, "__dict__") else {}
        database_arg = values.get("db")
        data_directory_arg = values.get("data_dir")
        database = (
            db_path(str(database_arg))
            if database_arg
            else Path(str(data_directory_arg)).expanduser() / "live.duckdb"
            if data_directory_arg
            else db_path()
        )
        return cls(
            host=str(values.get("host") or os.getenv("WITDEM_HOST") or DEFAULT_HOST),
            port=int(str(values.get("port") or os.getenv("WITDEM_PORT") or DEFAULT_PORT)),
            dashboard_host=str(
                values.get("dashboard_host") or os.getenv("WITDEM_DASHBOARD_HOST") or DEFAULT_DASHBOARD_HOST
            ),
            dashboard_port=int(
                str(values.get("dashboard_port") or os.getenv("WITDEM_DASHBOARD_PORT") or DEFAULT_DASHBOARD_PORT)
            ),
            database=database,
            data_directory=database.parent,
            log_level=str(values.get("log_level") or os.getenv("WITDEM_LOG_LEVEL") or "info"),
        )

    def child_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["WITDEM_DB_PATH"] = str(self.database)
        env["WITDEM_DATA_DIR"] = str(self.data_directory)
        return env
