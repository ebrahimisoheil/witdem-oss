"""Automatic correlation to the caller's active OpenTelemetry execution.

See ``docs/sdk.md`` §5 in the Witdem AI repository. Every public
``witdem_sdk`` function reads the caller's *current* OpenTelemetry context --
no manual id-copying by the caller -- to fill in ``execution_id``,
``trace_id``, and ``span_id`` on the wire record it sends.
"""

from __future__ import annotations

from opentelemetry import baggage as otel_baggage
from opentelemetry import trace as otel_trace

from witdem_sdk._errors import WitdemSDKError

EXECUTION_ID_BAGGAGE_KEY = "witdem.execution_id"


def resolve_correlation(explicit_execution_id: str | None) -> tuple[str, str | None, str | None]:
    """Resolve ``(execution_id, trace_id, span_id)`` for the current call.

    ``trace_id``/``span_id`` are read from ``trace.get_current_span()``
    whenever it carries a valid span context, and are ``None`` otherwise --
    that is never an error on its own, since they are correlation hints, not
    required fields (docs/architecture.md marks them optional).

    ``execution_id`` resolves in this order:

    1. ``explicit_execution_id`` (the caller's ``execution_id=`` keyword
       argument), if given -- an explicit argument always wins, so callers
       can deliberately attribute a call to a different execution than
       whatever the ambient context suggests.
    2. OpenTelemetry baggage under :data:`EXECUTION_ID_BAGGAGE_KEY`, read from
       the current context -- the automatic path this package exists for.

    If neither yields a non-empty string, :class:`WitdemSDKError` is raised
    rather than sending an uncorrelated record. Per docs/architecture.md, this is
    "no active span AND no explicit execution_id" in the common case, but the
    same error also covers the (rarer) case of an active span whose context
    simply never had ``witdem.execution_id`` baggage set on it.
    """

    span_context = otel_trace.get_current_span().get_span_context()
    trace_id: str | None = None
    span_id: str | None = None
    if span_context.is_valid:
        trace_id = format(span_context.trace_id, "032x")
        span_id = format(span_context.span_id, "016x")

    execution_id = explicit_execution_id or None
    if not execution_id:
        baggage_value = otel_baggage.get_baggage(EXECUTION_ID_BAGGAGE_KEY)
        if isinstance(baggage_value, str) and baggage_value:
            execution_id = baggage_value
    if not execution_id:
        raise WitdemSDKError(
            "witdem_sdk: could not determine execution_id -- no active OTel span "
            f"carried {EXECUTION_ID_BAGGAGE_KEY!r} baggage and no explicit "
            "execution_id=... keyword argument was passed. Either pass "
            "execution_id explicitly, or make this call inside an active span "
            f"whose context carries {EXECUTION_ID_BAGGAGE_KEY!r} baggage."
        )

    return execution_id, trace_id, span_id
