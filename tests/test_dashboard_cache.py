from __future__ import annotations

from threading import Event

from witdem.dashboard.cache import DashboardResponseCache


def test_cache_returns_fresh_value_without_reloading() -> None:
    cache = DashboardResponseCache(fresh_seconds=60)
    calls = 0

    def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"version": calls}

    assert cache.get_or_compute("overview", loader) == {"version": 1}
    assert cache.get_or_compute("overview", loader) == {"version": 1}
    assert calls == 1
    cache.close()


def test_stale_value_is_returned_while_one_refresh_runs() -> None:
    cache = DashboardResponseCache(fresh_seconds=-1, stale_seconds=60)
    refresh_started = Event()
    release_refresh = Event()
    calls = 0

    def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        if calls > 1:
            refresh_started.set()
            release_refresh.wait(timeout=2)
        return {"version": calls}

    assert cache.get_or_compute("overview", loader) == {"version": 1}
    assert cache.get_or_compute("overview", loader) == {"version": 1}
    assert refresh_started.wait(timeout=1)
    assert cache.get_or_compute("overview", loader) == {"version": 1}
    release_refresh.set()
    cache.close()
