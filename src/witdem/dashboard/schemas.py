"""OpenAPI response contracts for the dashboard read API.

The dashboard payloads intentionally allow additive fields so telemetry and
workflow metadata can evolve without dropping data from API responses.  The
stable fields below keep Swagger useful while preserving that extensibility.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JsonObject = dict[str, Any]
Plane = Literal["control", "work", "business"]
EntityKind = Literal["execution", "operation", "business_event"]
MeasurementStatus = Literal["measured", "missing", "not_applicable"]
ModelApplicability = Literal["applicable", "not_applicable"]


class ExtensibleModel(BaseModel):
    """Document stable fields while retaining additive response properties."""

    model_config = ConfigDict(extra="allow")


class HealthResponse(BaseModel):
    status: Literal["ok"]


class MetadataResponse(ExtensibleModel):
    product: str
    capabilities: JsonObject = Field(default_factory=dict)
    mode: str
    filters: dict[str, list[str]] = Field(default_factory=dict)
    contracts: list[JsonObject] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    update: JsonObject = Field(default_factory=dict)


class RunSummary(ExtensibleModel):
    execution_id: str
    trace_id: str | None = None
    display_name: str | None = None
    runtime_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    runtime_status: str | None = None
    runtime_outcome: str | None = None
    application_outcome: str | None = None
    failure_count: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    operation_count: int = 0
    input_tokens: float | None = None
    output_tokens: float | None = None
    total_tokens: float | None = None
    measured_cost: float | None = None
    known_cost: float | None = None
    canonical_url: str | None = None


class RunsResponse(BaseModel):
    items: list[RunSummary]
    count: int
    page: int
    page_size: int
    pages: int


class GraphNode(ExtensibleModel):
    id: str
    name: str | None = None
    display_name: str | None = None
    runtime_name: str | None = None
    kind: str | None = None
    status: str | None = None
    role: str | None = None
    provider: str | None = None
    model: str | None = None
    parent: str | None = None
    parent_operation_id: str | None = None
    attributes: JsonObject = Field(default_factory=dict)


class GraphEdge(ExtensibleModel):
    source: str
    target: str
    relation: str


class ExecutionGraph(ExtensibleModel):
    execution: JsonObject = Field(default_factory=dict)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class LinkedOperationSummary(BaseModel):
    type: str
    family: str
    operations: int
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    implementations: list[str] = Field(default_factory=list)


class OperationTypeSummary(BaseModel):
    type: str
    family: str
    plane: Plane
    operations: int
    failed: int
    active_seconds: float
    roles: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    implementations: list[str] = Field(default_factory=list)
    model_applicability: ModelApplicability
    linked_children: list[LinkedOperationSummary] = Field(default_factory=list)
    measurements: dict[str, float] = Field(default_factory=dict)


class OperationSummary(BaseModel):
    total_operations: int
    execution_containers: int
    failed_operations: int
    types: list[OperationTypeSummary] = Field(default_factory=list)


class OperationFact(ExtensibleModel):
    operation_id: str
    execution_id: str
    workflow_id: str | None = None
    template_hash: str | None = None
    node_id: str | None = None
    taxonomy_version: str | None = None
    entity_kind: EntityKind = "operation"
    plane: Plane | None = None
    family: str
    operation_type: str
    subtype: str | None = None
    interface: str
    role: str
    model_applicability: ModelApplicability | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    provider_id: str | None = None
    model_id: str | None = None
    gateway_id: str | None = None
    vendor_id: str | None = None
    runtime_id: str | None = None
    framework_id: str | None = None
    implementation_id: str | None = None
    execution_source: str | None = None
    parent_operation_id: str | None = None
    duration_seconds: float | None = None
    status: str
    attributes: JsonObject = Field(default_factory=dict)


class OperationMeasurement(ExtensibleModel):
    operation_id: str
    execution_id: str
    workflow_id: str | None = None
    template_hash: str | None = None
    node_id: str | None = None
    registry_version: str | None = None
    measurement_key: str
    value: float | None = None
    unit: str
    aggregation: str | None = None
    scope: str | None = None
    measurement_status: MeasurementStatus
    provenance: str
    applicability_source: str | None = None
    attempt: int | None = None
    family: str | None = None
    operation_type: str | None = None
    interface: str | None = None
    role: str | None = None
    provider_id: str | None = None
    model_id: str | None = None


class MeasurementCoverage(BaseModel):
    measured: int
    missing: int
    not_applicable: int
    applicable: int
    coverage: float | None = None


class RunDetailResponse(BaseModel):
    summary: RunSummary
    outcomes: JsonObject
    graph: ExecutionGraph
    semantic_records: list[JsonObject] = Field(default_factory=list)
    workflow_replay: JsonObject | None = None
    operation_summary: OperationSummary
    measurements: list[OperationMeasurement] = Field(default_factory=list)
    measurement_coverage: MeasurementCoverage
    evaluation_results: list[JsonObject] = Field(default_factory=list)
    canonical_url: str | None = None


class OverviewResponse(ExtensibleModel):
    execution: JsonObject
    goals: JsonObject
    costs: JsonObject
    cost_unavailable: dict[str, int | float]
    models: list[JsonObject]
    providers: list[JsonObject]
    workflows: list[JsonObject]
    stages: list[JsonObject]
    runtime_breakdown: dict[str, int]
    outcome_breakdown: dict[str, int]
    failures: list[JsonObject]
    evaluations: list[JsonObject]
    goal_misses: list[JsonObject]
    goal_trend: list[JsonObject]
    goal_portfolio: list[JsonObject]
    assurance_summary: JsonObject
    operation_health: OperationSummary
    operation_measurement_coverage: MeasurementCoverage
    operation_measurement_alerts: list[JsonObject]
    paths: list[JsonObject]
    contracts: list[JsonObject]
    metadata: MetadataResponse


class ComparisonResponse(BaseModel):
    dimension: Literal["provider", "model"]
    items: list[JsonObject] = Field(default_factory=list)


class WorkflowsResponse(BaseModel):
    items: list[JsonObject] = Field(default_factory=list)
    stages: list[JsonObject] = Field(default_factory=list)
    paths: list[JsonObject] = Field(default_factory=list)


class WorkflowCatalogResponse(BaseModel):
    items: list[JsonObject] = Field(default_factory=list)


class WorkflowDetailResponse(ExtensibleModel):
    workflow: JsonObject
    executions: list[RunSummary] = Field(default_factory=list)
    analytics: JsonObject
    execution_count: int


class WorkflowOperationsResponse(BaseModel):
    workflow_id: str
    summary: OperationSummary
    measurement_coverage: MeasurementCoverage
    operations: list[OperationFact] = Field(default_factory=list)
    measurements: list[OperationMeasurement] = Field(default_factory=list)


class EvaluationSummary(BaseModel):
    reported: int
    passed: int
    needs_attention: int
    unassessed: int
    executions: int


class WorkflowEvaluationsResponse(BaseModel):
    workflow_id: str
    summary: EvaluationSummary
    results: list[JsonObject] = Field(default_factory=list)
    campaigns: list[JsonObject] = Field(default_factory=list)


class WorkflowEvaluationCampaignsResponse(BaseModel):
    workflow_id: str
    campaigns: list[JsonObject] = Field(default_factory=list)


class EvaluationCampaignResponse(BaseModel):
    campaign: JsonObject
    results: list[JsonObject] = Field(default_factory=list)


class IssuesResponse(ExtensibleModel):
    summary: JsonObject
    failures: list[JsonObject] = Field(default_factory=list)
    retries: list[JsonObject] = Field(default_factory=list)
    quality_gaps: list[JsonObject] = Field(default_factory=list)
    outliers: list[JsonObject] = Field(default_factory=list)
    measurement: JsonObject
    operation_failures: list[OperationTypeSummary] = Field(default_factory=list)
    missing_required_measurements: list[JsonObject] = Field(default_factory=list)
