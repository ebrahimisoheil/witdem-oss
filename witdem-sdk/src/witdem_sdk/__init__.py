"""witdem_sdk -- the public Witdem client SDK.

``configure(...)`` provides one setup for OpenTelemetry traces and Witdem's
business-semantic records. ``WITDEM_ENDPOINT`` is the common base URL for the
trace and SDK ingest routes and defaults to ``http://localhost:4318``.

The five typed semantic functions (events, decisions, evaluations, outcomes,
metrics) remain available independently for applications that already own
their OpenTelemetry setup.

Correlation is automatic: each call reads the active OTel span
(``trace_id``/``span_id``) and ``"witdem.execution_id"`` baggage
(``execution_id``) from the current OpenTelemetry context -- callers never
copy ids by hand. If no execution id can be resolved (no baggage and no
explicit ``execution_id=`` keyword argument), :class:`WitdemSDKError` is raised
instead of sending an uncorrelated record.

Sending is fire-and-forget on a small bounded background thread pool: a
network error, or Witdem being unreachable, is logged and dropped (after one
quick retry) and never raised into caller code -- the one hard reliability
requirement for this package.

The unified client exposes execution, model, tool, and generic operation
contexts. It records standard GenAI attributes and flushes traces and semantic
records at shutdown; provider pricing remains a server-side concern.

See ``docs/sdk.md`` §5 in the Witdem AI repository for the wire
contract this package implements.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from witdem_sdk._contract import ContractResult, WitdemProjectConfig, load_project_config
from witdem_sdk._correlation import resolve_correlation
from witdem_sdk._errors import WitdemSDKError
from witdem_sdk._payload import Kind as _Kind
from witdem_sdk._payload import build_payload
from witdem_sdk._protocol import SEMANTIC_RECORD_PROTOCOL_VERSION
from witdem_sdk._telemetry import Operation, Witdem, configure
from witdem_sdk._transport import DeliveryStatus, flush, submit_record

__all__ = [
    "WitdemSDKError",
    "Witdem",
    "Operation",
    "configure",
    "decision",
    "event",
    "evaluation",
    "metric",
    "outcome",
    "flush",
    "DeliveryStatus",
    "SEMANTIC_RECORD_PROTOCOL_VERSION",
    "ContractResult",
    "WitdemProjectConfig",
    "load_project_config",
]

__version__ = "0.2.0"


def _emit(
    kind: _Kind,
    name: str,
    value: Any,
    attributes: dict[str, Any],
    *,
    execution_id: str | None,
) -> None:
    """Shared plumbing: resolve correlation, build the wire payload, send it."""

    resolved_execution_id, trace_id, span_id = resolve_correlation(execution_id)
    payload = build_payload(
        kind=kind,
        name=name,
        value=value,
        execution_id=resolved_execution_id,
        trace_id=trace_id,
        span_id=span_id,
        attributes=attributes,
    )
    submit_record(payload)


def event(
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    attributes: Mapping[str, Any] | None = None,
    execution_id: str | None = None,
) -> None:
    """Record a domain event that happened during the active execution.

    ``payload`` is an arbitrary JSON-serializable dict describing what
    happened; it is sent as the wire record's ``attributes``. Raises
    :class:`WitdemSDKError` if ``execution_id`` cannot be resolved (see module
    docs). Never raises when Witdem is unavailable on the network.
    """

    _emit(
        "event",
        name,
        None,
        {**dict(payload or {}), **dict(attributes or {})},
        execution_id=execution_id,
    )


def decision(
    name: str,
    value: Any,
    *,
    attributes: Mapping[str, Any] | None = None,
    execution_id: str | None = None,
) -> None:
    """Record a business decision (e.g. a chosen route/branch) and its value.

    Raises :class:`WitdemSDKError` if ``execution_id`` cannot be resolved (see
    module docs). Never raises when Witdem is unavailable on the network.
    """

    _emit("decision", name, value, dict(attributes or {}), execution_id=execution_id)


def evaluation(
    name: str,
    *,
    score: float | None = None,
    label: str | None = None,
    value: Any = None,
    attributes: Mapping[str, Any] | None = None,
    execution_id: str | None = None,
) -> None:
    """Record a structured assessment: a ``score``, a ``label``, or both.

    Raises :class:`WitdemSDKError` if ``execution_id`` cannot be resolved (see
    module docs). Never raises when Witdem is unavailable on the network.
    """

    resolved_attributes = dict(attributes or {})
    if score is not None:
        resolved_attributes["score"] = score
    if label is not None:
        resolved_attributes["label"] = label
    _emit("evaluation", name, value, resolved_attributes, execution_id=execution_id)


def outcome(
    name: str,
    *,
    status: str | None = None,
    value: Any = None,
    attributes: Mapping[str, Any] | None = None,
    execution_id: str | None = None,
) -> None:
    """Record an externally meaningful result of the active execution.

    Raises :class:`WitdemSDKError` if ``execution_id`` cannot be resolved (see
    module docs). Never raises when Witdem is unavailable on the network.
    """

    resolved_attributes = dict(attributes or {})
    if status is not None:
        resolved_attributes["status"] = status
    _emit("outcome", name, value, resolved_attributes, execution_id=execution_id)


def metric(
    name: str,
    value: Any,
    *,
    attributes: Mapping[str, Any] | None = None,
    execution_id: str | None = None,
) -> None:
    """Record a numeric (or otherwise quantitative) measurement.

    Raises :class:`WitdemSDKError` if ``execution_id`` cannot be resolved (see
    module docs). Never raises when Witdem is unavailable on the network.
    """

    _emit("metric", name, value, dict(attributes or {}), execution_id=execution_id)
