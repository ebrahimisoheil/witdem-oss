"""Public telemetry setup for external applications."""

from witdem.telemetry.otel import configure_tracing, force_flush_tracing, record_cost

__all__ = ["configure_tracing", "force_flush_tracing", "record_cost"]
