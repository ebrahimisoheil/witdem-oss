"""Public, domain-neutral Witdem analytics API."""

from witdem.analytics.contracts import (
    CostSummary,
    ExecutionSummary,
    FailureSummary,
    ModelSummary,
    PathSummary,
    PerformanceSummary,
    ProviderSummary,
)
from witdem.analytics.core import Evaluation, Event, Execution, Link, Operation, Outcome
from witdem.analytics.derived import derived_termination_category
from witdem.analytics.evidence import EvaluationAssessment, EvidenceBundle, EvidenceBundleDiagnostics
from witdem.analytics.identity import (
    canonical_operation_key,
    canonical_path_signature,
    canonical_stage_key,
    canonical_tool_key,
    display_operation,
    display_path,
    display_stage,
    display_tool,
)
from witdem.analytics.runtime import (
    NormalizedExecutionGraph,
    ReplayGraph,
    derive_replay_graph,
    derive_runtime_insights,
    find_similar_executions,
    normalize_haystack_spans,
)
from witdem.analytics.schema import (
    AGGREGATE_COLUMNS,
    ANALYTICS_COLUMN_TYPES,
    ANALYTICS_COLUMNS,
    ANALYTICS_TABLES,
    V2_ANALYTICS_TABLES,
)

__all__ = [
    "Event",
    "Execution",
    "Evaluation",
    "Link",
    "Operation",
    "Outcome",
    "EvaluationAssessment",
    "EvidenceBundle",
    "EvidenceBundleDiagnostics",
    "ExecutionSummary",
    "CostSummary",
    "ProviderSummary",
    "ModelSummary",
    "FailureSummary",
    "PerformanceSummary",
    "PathSummary",
    "NormalizedExecutionGraph",
    "ReplayGraph",
    "normalize_haystack_spans",
    "derive_replay_graph",
    "derive_runtime_insights",
    "find_similar_executions",
    "derived_termination_category",
    "canonical_operation_key",
    "canonical_path_signature",
    "canonical_stage_key",
    "canonical_tool_key",
    "display_operation",
    "display_path",
    "display_stage",
    "display_tool",
    "ANALYTICS_TABLES",
    "ANALYTICS_COLUMNS",
    "ANALYTICS_COLUMN_TYPES",
    "AGGREGATE_COLUMNS",
    "V2_ANALYTICS_TABLES",
]
