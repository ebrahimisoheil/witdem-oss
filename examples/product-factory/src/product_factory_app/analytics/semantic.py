"""Product Factory's thin semantic adapter over generic analytics events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from opentelemetry import trace
from pydantic import BaseModel, Field

from product_factory_app.domain.config import DomainConfig, load_product_factory_config
from product_factory_app.persistence.store import RunStore


class _Event(BaseModel):
    schema_version: str
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    trace_id: str | None = None
    span_id: str | None = None
    timestamp: datetime
    type: str
    name: str
    payload: dict[str, Any]


class _Evaluation(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    name: str
    value: Any = None
    source: str
    score: float | None = None
    confidence: float | None = None
    definition_version: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class _Outcome(BaseModel):
    outcome_id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    name: str
    status: str | None = None
    value: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict)


PRODUCT_FACTORY_STAGE_MAP = {
    "research_pass": "research",
    "research_completeness": "evidence_assessment",
    "targeted_research_route": "targeted_research",
    "extract_profile": "extraction",
    "validation": "validation",
    "acceptance": "acceptance",
    "execution_outcome": "outcome",
}


def product_factory_stage_map() -> dict[str, str]:
    """Return optional Product Factory labels for replay enrichment.

    This mapping lives beside the semantic adapter rather than in the Haystack
    normalizer.  A runtime-only caller simply omits it.
    """

    return dict(PRODUCT_FACTORY_STAGE_MAP)


def _hex_id(value: int) -> str | None:
    return f"{value:032x}" if value else None


class SemanticAnalytics:
    """Write explicit semantic events without influencing workflow behavior."""

    schema_version = "0.2.0"

    def __init__(
        self,
        store: RunStore,
        execution_id: str,
        workflow_version: str,
        domain_config: DomainConfig | None = None,
        sdk_enabled: bool = False,
    ) -> None:
        self.store = store
        self.execution_id = execution_id
        self.workflow_version = workflow_version
        self.domain_config = domain_config or load_product_factory_config()
        self._bound_context: trace.SpanContext | None = None
        self._sdk = None
        if sdk_enabled:
            from product_factory_app.analytics.witdem_sdk_semantics import WitdemSemanticSink

            self._sdk = WitdemSemanticSink()

    def bind_trace_context(self, context: trace.SpanContext) -> None:
        """Remember the execution span for events emitted after the span closes."""

        self._bound_context = context

    def emit(
        self,
        event_type: str,
        *,
        name: str | None = None,
        payload: dict[str, Any] | None = None,
        **attributes: Any,
    ) -> None:
        """Append one canonical semantic event."""

        span = trace.get_current_span()
        context = span.get_span_context()
        if not context.is_valid and self._bound_context is not None:
            context = self._bound_context
        event_payload = dict(payload or {})
        event_payload.update(attributes)
        event = _Event(
            schema_version=self.schema_version,
            execution_id=self.execution_id,
            trace_id=_hex_id(context.trace_id),
            span_id=_hex_id(context.span_id),
            timestamp=datetime.now(timezone.utc),
            type=event_type,
            name=name or event_type,
            payload=event_payload,
        )
        record = event.model_dump(mode="json")
        record["workflow_version"] = self.workflow_version
        self.store.append_jsonl("analytics/events.jsonl", record)
        if self._sdk is not None:
            self._sdk.emit(event_type, event.name, event_payload)

    def execution(self, **payload: Any) -> None:
        self.emit("execution", name="execution", **payload)

    def step(self, name: str, **payload: Any) -> None:
        self.emit("step", name=name, **payload)

    def decision(self, decision_type: str, selected: str, **payload: Any) -> None:
        self.domain_config.validate_decision(decision_type, selected)
        self.emit(
            "decision",
            name=decision_type,
            payload={"value": selected, "decision_type": decision_type, "selected": selected},
            **payload,
        )

    def outcome(self, status: str, **payload: Any) -> None:
        self.emit("outcome", name="execution_outcome", status=status, **payload)

    def acceptance(self, **payload: Any) -> None:
        """Record the application moment at which a result became accepted."""

        self.emit("acceptance", name="acceptance", **payload)

    def recovery(self, **payload: Any) -> None:
        """Record a source/tool failure followed by a successful recovery pass."""

        self.emit("recovery", name="recovery", **payload)

    def metric(self, name: str, value: float, **payload: Any) -> None:
        self.emit("metric", name=name, value=value, **payload)

    def evaluate(
        self,
        name: str,
        *,
        value: Any = None,
        source: str,
        score: float | None = None,
        confidence: float | None = None,
        definition_version: str | None = None,
        **attributes: Any,
    ) -> None:
        """Persist a typed evaluation after validating domain vocabulary."""

        self.domain_config.validate_evaluation(name)
        evaluation = _Evaluation(
            execution_id=self.execution_id,
            name=name,
            value=value,
            source=source,
            score=score,
            confidence=confidence,
            definition_version=definition_version,
            attributes=attributes,
        )
        self.store.append_jsonl("analytics/evaluations.jsonl", evaluation.model_dump(mode="json"))
        if self._sdk is not None:
            self._sdk.evaluation(name, value=value, score=score, attributes=attributes)

    def record_outcome(
        self,
        name: str,
        *,
        status: str | None = None,
        value: Any = None,
        **attributes: Any,
    ) -> None:
        """Persist a typed outcome after validating domain vocabulary."""

        self.domain_config.validate_outcome(name)
        outcome = _Outcome(
            execution_id=self.execution_id,
            name=name,
            status=status,
            value=value,
            attributes=attributes,
        )
        self.store.append_jsonl("analytics/outcomes.jsonl", outcome.model_dump(mode="json"))
        if self._sdk is not None:
            self._sdk.outcome(name, status=status, value=value, attributes=attributes)
