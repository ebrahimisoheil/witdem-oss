"""Small domain-neutral analytics records.

This module deliberately knows nothing about any business domain, framework, or
provider. Domain vocabulary is carried by strings and validated by a domain
layer before records are persisted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware timezone.utc timestamp."""

    return datetime.now(timezone.utc)


class AnalyticsRecord(BaseModel):
    """Strict base for persisted analytics records."""

    model_config = ConfigDict(extra="forbid")


class Execution(AnalyticsRecord):
    """One unit of workflow or runtime work."""

    execution_id: str
    runtime_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: str | None = None
    schema_version: str = "0.1.0"
    attributes: dict[str, Any] = Field(default_factory=dict)


class Operation(AnalyticsRecord):
    """Work performed within an execution."""

    operation_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    kind: str
    name: str
    status: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attempt: int | None = Field(default=None, ge=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Event(AnalyticsRecord):
    """A meaningful instantaneous occurrence."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    trace_id: str | None = None
    span_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "0.1.0"


class Link(AnalyticsRecord):
    """A relationship not represented by operation parentage alone."""

    link_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    source_id: str
    target_id: str
    relation: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Evaluation(AnalyticsRecord):
    """A structured assessment of an execution, operation, result, or object."""

    evaluation_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    subject_id: str | None = None
    name: str = Field(min_length=1)
    value: Any = None
    label: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    source: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    definition_version: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Outcome(AnalyticsRecord):
    """An externally meaningful result of an execution."""

    outcome_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    name: str = Field(min_length=1)
    status: str | None = None
    value: Any = None
    timestamp: datetime = Field(default_factory=utc_now)
    attributes: dict[str, Any] = Field(default_factory=dict)
