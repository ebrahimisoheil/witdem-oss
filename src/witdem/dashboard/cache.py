"""Small stale-while-refresh cache for dashboard response contracts."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from typing import TypeVar

_Value = TypeVar("_Value")


class DashboardResponseCache:
    """Serve a recent coherent snapshot while its replacement is computed.

    DuckDB permits either one writer or multiple readers across processes. A
    previously computed dashboard response is therefore more useful than a
    503 while ELT owns the file. Refreshes are bounded to one task per key.
    """

    def __init__(self, *, fresh_seconds: float = 5.0, stale_seconds: float = 300.0, max_entries: int = 128):
        self.fresh_seconds = fresh_seconds
        self.stale_seconds = stale_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
        self._refreshing: set[Hashable] = set()
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="witdem-dashboard-refresh")

    def get_or_compute(self, key: Hashable, loader: Callable[[], _Value]) -> _Value:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                age = now - entry[0]
                self._entries.move_to_end(key)
                if age <= self.fresh_seconds:
                    return entry[1]  # type: ignore[return-value]
                if age <= self.stale_seconds:
                    if key not in self._refreshing:
                        self._refreshing.add(key)
                        self._executor.submit(self._refresh, key, loader)
                    return entry[1]  # type: ignore[return-value]
        value = loader()
        self._store(key, value)
        return value

    def _refresh(self, key: Hashable, loader: Callable[[], _Value]) -> None:
        try:
            self._store(key, loader())
        except Exception:
            # Keep serving the last coherent value. The next request after the
            # fresh window schedules another bounded refresh attempt.
            pass
        finally:
            with self._lock:
                self._refreshing.discard(key)

    def _store(self, key: Hashable, value: object) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
