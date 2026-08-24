"""Typed errors raised by :mod:`witdem_sdk`."""

from __future__ import annotations


class WitdemSDKError(Exception):
    """Raised when a call cannot be correlated to an execution.

    This is raised instead of silently sending an uncorrelated (and
    therefore useless) record whenever there is no active OpenTelemetry span
    carrying ``"witdem.execution_id"`` baggage *and* the caller did not pass an
    explicit ``execution_id=`` keyword argument. It is never raised for
    transport/network failures -- those are logged and dropped (see
    :mod:`witdem_sdk._transport`), never surfaced to caller code.
    """
