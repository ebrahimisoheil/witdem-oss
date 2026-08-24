"""Semantic operation facts shared by runtime adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from witdem.integrations.models.normalized_span import NormalizedSpan


class NormalizedOperation(BaseModel):
    """An observed unit of work before it is mapped to Witdem ``Operation``."""

    model_config = ConfigDict(extra="allow")

    source_id: str
    trace_id: str | None = None
    parent_source_id: str | None = None
    name: str = "operation"
    kind: str = "operation"
    status: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempt: int | None = None
    provider: str | None = None
    request_model: str | None = None
    response_model: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    agent_name: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    span: NormalizedSpan | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at).total_seconds())
