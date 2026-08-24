"""Shared pytest fixtures.

Configures tracing with an in-memory exporter *at collection time* (module
level, not inside a fixture function), so this runs before pytest imports any
``test_*.py`` module. That ordering matters: ``agent_demo.api`` also calls
``configure_tracing()`` at its own module-import time (to wire up a real OTLP
exporter by default), but ``otel_setup.configure_tracing`` is idempotent --
first call wins. Configuring here first means every test in this suite runs
fully offline, with spans captured in memory instead of exported over the
network to a live Witdem server.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_demo import otel_setup

SPAN_EXPORTER = InMemorySpanExporter()
otel_setup.configure_tracing(exporter=SPAN_EXPORTER)


@pytest.fixture(autouse=True)
def _clear_spans():
    SPAN_EXPORTER.clear()
    yield


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    return SPAN_EXPORTER
