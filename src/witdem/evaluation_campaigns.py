"""Framework-neutral validation and import for offline evaluation campaigns."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVALUATION_SCHEMA_VERSION = "1"


class CampaignRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    record_type: Literal["campaign"] = "campaign"
    campaign_id: str = Field(min_length=1)
    suite_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    template_hash: str | None = None
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    baseline_version: str | None = None
    status: str = "completed"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvaluationResultRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    record_type: Literal["result"] = "result"
    result_id: str | None = None
    campaign_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    execution_id: str | None = None
    subject_type: str = "dataset_case"
    subject_id: str | None = None
    evaluation_key: str = Field(min_length=1)
    definition_version: str = "1"
    value: Any = None
    label: str | None = None
    score: float | None = None
    passed: bool | None = None
    target: Any = None
    direction: str | None = None
    evaluator_type: str | None = None
    evaluator_id: str | None = None
    evidence: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_observation(self) -> EvaluationResultRecord:
        if self.value is None and self.label is None and self.score is None and self.passed is None:
            raise ValueError("evaluation result requires a value, label, score, or explicit passed state")
        return self

    def stable_id(self) -> str:
        if self.result_id:
            return self.result_id
        identity = "\0".join(
            [
                self.campaign_id,
                self.case_id,
                self.subject_id or self.case_id,
                self.evaluation_key,
                self.definition_version,
            ]
        )
        return hashlib.sha256(identity.encode()).hexdigest()


def validate_jsonl(path: str | Path) -> tuple[CampaignRecord, list[EvaluationResultRecord]]:
    campaign: CampaignRecord | None = None
    results: list[EvaluationResultRecord] = []
    source = Path(path).expanduser()
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{source}:{line_number}: each line must be a JSON object")
        try:
            if payload.get("record_type") == "campaign":
                if campaign is not None:
                    raise ValueError("only one campaign record is allowed")
                campaign = CampaignRecord.model_validate(payload)
            elif payload.get("record_type") == "result":
                results.append(EvaluationResultRecord.model_validate(payload))
            else:
                raise ValueError("record_type must be 'campaign' or 'result'")
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
    if campaign is None:
        raise ValueError(f"{source}: a campaign record is required")
    foreign = sorted({item.campaign_id for item in results if item.campaign_id != campaign.campaign_id})
    if foreign:
        raise ValueError(f"{source}: results reference different campaigns: {', '.join(foreign)}")
    return campaign, results
