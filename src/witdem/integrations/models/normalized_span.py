"""Transport-neutral representation of one observed span."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedLink(BaseModel):
    """An explicit source link, kept separate from parentage."""

    model_config = ConfigDict(extra="allow")

    trace_id: str | None = None
    span_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class NormalizedSpan(BaseModel):
    """Common OTel envelope facts used by all semantic dialects.

    ``attributes`` deliberately remains a source-evidence bag.  Semantic
    normalizers add interpreted fields to :class:`NormalizedOperation` while
    retaining this bag for auditability and forward compatibility.
    """

    model_config = ConfigDict(extra="allow")

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    name: str = "operation"
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str | None = None
    status_description: str | None = None
    exception: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    links: list[NormalizedLink] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    resource: dict[str, Any] = Field(default_factory=dict)
    instrumentation_scope: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_seconds(self) -> float | None:
        if self.start_time is None or self.end_time is None:
            return None
        return max(0.0, (self.end_time - self.start_time).total_seconds())

    def with_utc_timestamps(self) -> NormalizedSpan:
        """Return a copy with naive timestamps made explicitly timezone.utc-aware."""

        updates: dict[str, Any] = {}
        for field in ("start_time", "end_time"):
            value = getattr(self, field)
            if isinstance(value, datetime) and value.tzinfo is None:
                updates[field] = value.replace(tzinfo=timezone.utc)
        return self.model_copy(update=updates) if updates else self
