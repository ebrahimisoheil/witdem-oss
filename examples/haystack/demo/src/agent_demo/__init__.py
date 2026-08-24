"""agent_demo: a small, fully separate Haystack demo app proving Witdem's two
ingestion modes (telemetry-only OTLP, and telemetry + optional Witdem SDK
enrichment) against the SAME physical execution.

This package never imports ``witdem``. See
docs/architecture.md and this package's own module docstrings (``workflow.py``,
``api.py``, ``enrichment.py``, ``otel_setup.py``) for the invariants each
enforces.
"""

from __future__ import annotations

__all__: list[str] = []
