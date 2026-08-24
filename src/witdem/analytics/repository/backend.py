"""Backend boundary for dashboard reads.

DuckDB is the only implementation in the current release. Keeping this small
factory behind an explicit boundary lets a future ELT-backed repository replace the
storage implementation without changing dashboard pages or contracts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from witdem.analytics.repository.analytics_repository import AnalyticsRepository
from witdem.config import db_path


@dataclass(frozen=True)
class BackendHealth:
    backend: str
    database: Path
    status: str


class AnalyticsBackend(Protocol):
    def create_repository(self) -> AnalyticsRepository: ...

    def health(self) -> BackendHealth: ...


class DuckDBAnalyticsBackend:
    name = "duckdb"

    def __init__(self, database: str | Path | None = None) -> None:
        self.database = db_path(database)

    def create_repository(self) -> AnalyticsRepository:
        return AnalyticsRepository(self.database)

    def health(self) -> BackendHealth:
        return BackendHealth(backend=self.name, database=self.database, status="ok")


def create_backend(database: str | Path | None = None) -> AnalyticsBackend:
    backend = os.getenv("WITDEM_ANALYTICS_BACKEND", "duckdb").casefold()
    if backend != "duckdb":
        raise ValueError(
            f"unsupported WITDEM_ANALYTICS_BACKEND={backend!r}; only 'duckdb' is available in this release"
        )
    return DuckDBAnalyticsBackend(database)
