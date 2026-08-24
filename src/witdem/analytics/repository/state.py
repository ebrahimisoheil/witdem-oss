"""Serializable filter and capability contracts shared by analytics consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Capabilities:
    """What the loaded corpus can explain without inventing data."""

    graph: bool = False
    timing: bool = False
    errors: bool = False
    model_calls: bool = False
    tools: bool = False
    provider_identity: bool = False
    model_identity: bool = False
    token_usage: bool = False
    operation_cost: bool = False
    tool_cost: bool = False
    roles: bool = False
    semantic_stages: bool = False
    evaluations: bool = False
    business_outcomes: bool = False
    semantic_events: bool = False

    @property
    def enriched(self) -> bool:
        return self.provider_identity or self.model_identity or self.token_usage or self.operation_cost

    @property
    def domain_enriched(self) -> bool:
        return self.evaluations or self.business_outcomes or self.semantic_events


@dataclass
class FilterState:
    """Global analytics filters. Empty values mean no restriction."""

    workflow: str | None = None
    status: str | None = None
    provider: str | None = None
    model: str | None = None
    tool: str | None = None
    stage: str | None = None
    contract_hash: str | None = None
    has_repeated_work: bool = False
    has_failure: bool = False
    start_date: date | None = None
    end_date: date | None = None

    def as_key(self) -> tuple[object, ...]:
        return tuple(self.__dict__.values())
