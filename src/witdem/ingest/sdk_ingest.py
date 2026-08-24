"""SDK ingest endpoint.

``POST /sdk/v1/records`` accepts one versioned SDK wire record at a time --
the ``witdem_sdk`` client package's wire contract exactly (``event``/
``decision``/``evaluation``/``outcome``/``metric``). Strict pydantic
validation rejects any malformed request with **HTTP 400** (never a 500):
an unknown ``version``, a ``kind`` outside the five literal values, a
missing ``event_id``/``execution_id``/``name``, or any unexpected extra
field all fail validation the same way. On success, the record is
committed to the immutable corpus for correlation with its OTel execution.

Domain vocabulary validation is optional and off by default. Unlike
``analytics.semantic``/``config.Settings``'s existing
research-pipeline default, this router never calls
``load_product_factory_config()`` itself, and works with zero domain
configuration out of the box. An operator may opt in with
``WITDEM_DOMAIN_CONFIG_PATH`` pointing at a domain YAML file (loaded once, at
import time); when configured, ``name`` is checked against that domain's
vocabulary for the record kinds ``DomainConfig`` actually models a
name-vocabulary for (``evaluation``, ``outcome``), plus ``decision`` when
its ``value`` is itself a string (``DomainConfig.validate_decision``'s own
signature takes a ``str``). ``event``/``metric`` records have no vocabulary
concept in ``DomainConfig`` at all and are therefore never restricted by
it, regardless of configuration.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import fastapi
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from witdem.auth import require_api_key
from witdem.ingest import sdk_store

logger = logging.getLogger(__name__)

router = fastapi.APIRouter()

_ENV_DOMAIN_CONFIG_PATH = "WITDEM_DOMAIN_CONFIG_PATH"

RecordKind = Literal["event", "decision", "evaluation", "outcome", "metric"]


class SDKRecordIn(BaseModel):
    """Strict validation of the ``witdem_sdk`` wire envelope (docs/architecture.md).

    ``extra="forbid"`` plus required ``version``/``kind``/``event_id``/
    ``execution_id``/``name`` are what let ``create_record`` return HTTP 400
    (never 500) on any malformed request -- pydantic raises
    ``ValidationError`` uniformly for all of these.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    kind: RecordKind
    event_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    trace_id: str | None = None
    span_id: str | None = None
    name: str = Field(min_length=1)
    value: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class DomainVocabulary:
    """Optional generic name vocabulary loaded only when explicitly configured."""

    decisions: dict[str, tuple[str, ...]]
    evaluations: frozenset[str]
    outcomes: frozenset[str]

    def validate_decision(self, name: str, value: str) -> None:
        values = self.decisions.get(name)
        if values is None or value not in values:
            raise ValueError(f"invalid value {value!r} for domain decision {name!r}")

    def validate_evaluation(self, name: str) -> None:
        if name not in self.evaluations:
            raise ValueError(f"unknown domain evaluation: {name}")

    def validate_outcome(self, name: str) -> None:
        if name not in self.outcomes:
            raise ValueError(f"unknown domain outcome: {name}")


def _load_domain_config_from_env() -> DomainVocabulary | None:
    path = os.environ.get(_ENV_DOMAIN_CONFIG_PATH)
    if not path:
        return None
    try:
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unable to load configured domain vocabulary: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configured domain vocabulary must be a mapping")
    decisions_raw = raw.get("decisions", {})
    evaluations_raw = raw.get("evaluations", {})
    outcomes_raw = raw.get("outcomes", {})
    decisions = {
        str(name): tuple(str(value) for value in definition.get("values", []))
        for name, definition in decisions_raw.items()
        if isinstance(definition, dict)
    }
    evaluations = frozenset(str(name) for name in evaluations_raw)
    outcomes = frozenset(str(name) for name in outcomes_raw)
    return DomainVocabulary(decisions=decisions, evaluations=evaluations, outcomes=outcomes)


_domain_config: DomainVocabulary | None = _load_domain_config_from_env()


def get_domain_config() -> DomainVocabulary | None:
    return _domain_config


def _validate_domain_vocabulary(record: SDKRecordIn, domain_config: DomainVocabulary) -> None:
    if record.kind == "decision" and isinstance(record.value, str):
        domain_config.validate_decision(record.name, record.value)
    elif record.kind == "evaluation":
        domain_config.validate_evaluation(record.name)
    elif record.kind == "outcome":
        domain_config.validate_outcome(record.name)


@router.post("/sdk/v1/records")
async def create_record(
    request: fastapi.Request,
    background_tasks: fastapi.BackgroundTasks,
) -> dict[str, Any]:
    """Validate and durably commit one SDK wire record.

    Returns HTTP 400 with a clear message for any invalid input (bad JSON,
    wrong shape, unknown version/kind, missing required field, or -- when a
    domain config is configured -- an out-of-vocabulary name/value).
    """

    require_api_key(request)
    raw_payload = await request.body()
    try:
        payload = json.loads(raw_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise fastapi.HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise fastapi.HTTPException(status_code=400, detail="request body must be a JSON object")

    try:
        record = SDKRecordIn.model_validate(payload)
    except ValidationError as exc:
        raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

    domain_config = get_domain_config()
    if domain_config is not None:
        try:
            _validate_domain_vocabulary(record, domain_config)
        except ValueError as exc:
            raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

    record_dict = record.model_dump(mode="json")
    commit = sdk_store.commit_record(record_dict, raw_payload=raw_payload)
    logger.debug(
        "create_record: accepted %s record %s for execution %s",
        record.kind,
        record.event_id,
        record.execution_id,
    )
    return {
        "status": "accepted",
        "ingest_id": commit.ingest_id,
        "analytics_status": "pending",
        "event_id": record.event_id,
        "kind": record.kind,
    }
