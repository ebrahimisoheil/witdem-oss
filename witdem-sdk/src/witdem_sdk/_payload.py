"""Builds the exact versioned wire envelope ``witdem_sdk`` sends to Witdem.

See ``docs/sdk.md`` §5 in the Witdem AI repository for the fixed
wire shape:

```json
{
  "version": "1.0",
  "kind": "event | decision | evaluation | outcome | metric",
  "event_id": "uuid, required, idempotency key",
  "execution_id": "required",
  "trace_id": "optional",
  "span_id": "optional",
  "name": "string",
  "value": "any",
  "attributes": {}
}
```
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from witdem_sdk._protocol import SEMANTIC_RECORD_PROTOCOL_VERSION

Kind = Literal["event", "decision", "evaluation", "outcome", "metric"]

_WIRE_VERSION: Literal["1.0"] = SEMANTIC_RECORD_PROTOCOL_VERSION


class _WireRecord(BaseModel):
    """The exact SDK -> Witdem wire envelope. Internal; never exposed publicly."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = _WIRE_VERSION
    kind: Kind
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str = Field(min_length=1)
    trace_id: str | None = None
    span_id: str | None = None
    name: str = Field(min_length=1)
    value: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def build_payload(
    *,
    kind: Kind,
    name: str,
    value: Any,
    execution_id: str,
    trace_id: str | None,
    span_id: str | None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact wire dict POSTed to ``{endpoint}/sdk/v1/records``.

    ``event_id`` is generated here (client-side), per docs/architecture.md, as a
    fresh ``uuid4().hex`` -- it is the idempotency key the server dedupes on,
    so each call to a public ``witdem_sdk`` function produces exactly one new
    ``event_id``, even if the resulting send is later retried by the
    transport layer (the retry resends the *same* built payload, not a new
    one).
    """

    record = _WireRecord(
        kind=kind,
        execution_id=execution_id,
        trace_id=trace_id,
        span_id=span_id,
        name=name,
        value=value,
        attributes=attributes or {},
    )
    return record.model_dump(mode="json")
