export type Performance = {
  participant_id: string;
  dimension: string;
  provider_id: string | null;
  model_id: string | null;
  model_family: string | null;
  vendor_id: string | null;
  label: string;
  runs: number;
  completed: number;
  failed: number;
  recovered: number;
  measured_cost: number | null;
  time_per_positive_run: number | null;
  total_tokens: number | null;
  failure_rate: number;
  cost_coverage: number;
  active_seconds: number;
  p50_call_seconds: number | null;
  p95_call_seconds: number | null;
  cost_eligible_operations: number;
  cost_measured_operations: number;
  token_eligible_operations: number;
  token_measured_operations: number;
};
export type ComparisonInsight = {
  participant_id: string;
  dimension: string;
  provider_id: string | null;
  model_id: string | null;
  model_family: string | null;
  vendor_id: string | null;
  scope: "cohort+direct-attribution";
  label: string;
  runs: number;
  avg_duration_seconds: number | null;
  p50_duration_seconds: number | null;
  p95_duration_seconds: number | null;
  avg_cost_per_run: number | null;
  avg_tokens_per_run: number | null;
  avg_calls_per_run: number | null;
  cost_coverage: number;
  goal_rate: number | null;
  decision_correctness_rate: number | null;
  recovered_runs: number;
  cost_eligible_operations: number;
  cost_measured_operations: number;
  token_eligible_operations: number;
  token_measured_operations: number;
  evaluations?: Array<{ name: string; reported_runs: number; average_score: number | null; target?: number | string | boolean; direction: string }>;
};
export type WorkflowInsight = ComparisonInsight & { runtime_id: string };
export type WorkflowStage = {
  runtime_id: string;
  workflow: string;
  label: string;
  calls: number;
  executions: number;
  time_seconds: number;
  known_cost: number | null;
  total_tokens: number | null;
  retries: number;
};
export type WorkflowPath = {
  runtime_id: string;
  workflow: string;
  steps: string[];
  runs: number;
  p50_duration_seconds: number | null;
  p95_duration_seconds: number | null;
  avg_cost_per_run: number | null;
  avg_tokens_per_run: number | null;
  retries: number;
  recovered_runs: number;
};
export type Issues = {
  summary: { runs: number; terminal_failures: number; recovered_runs: number; extra_attempts: number; quality_gaps: number };
  failures: Array<{ execution_id: string; display_name?: string; failure_location: string; runtime_outcome?: string; duration_seconds?: number; known_cost?: number }>;
  retries: Array<{ label: string; extra_attempts: number; affected_runs: number; runs: Array<{ execution_id: string; display_name?: string }> }>;
  quality_gaps: Array<{ execution_id: string; display_name?: string; name: string; score: number; target: number; direction: string }>;
  outliers: Array<{ execution_id: string; display_name?: string; reasons: string[]; duration_seconds?: number; known_cost?: number; total_tokens?: number }>;
  measurement: { cost: number; tokens: number; business_goal: number; total: number; cost_unavailable: Record<string, number> };
  operation_failures: OperationTypeSummary[];
  missing_required_measurements: Overview["operation_measurement_alerts"];
};
export type Overview = {
  metadata: Meta;
  execution: {
    total_runs: number;
    successful_runs: number;
    failed_runs: number;
    running_runs: number;
    recovered_runs: number;
    avg_duration_seconds: number | null;
    measured_cost: number | null;
    cost_coverage: number;
    business_reported_runs: number;
    terminal_runs: number;
    unknown_runs: number;
    attention_runs: number;
    runtime_success_rate: number;
  };
  goals: {
    reported_runs: number;
    achieved_runs: number;
    decision_correct_runs: number;
    coverage: number;
    success_rate: number;
    decision_correctness_rate: number;
    targeted_research_runs: number;
    targeted_research_successes: number;
    cost_per_achieved_goal: number | null;
    time_per_achieved_goal: number | null;
    tokens_per_achieved_goal: number | null;
    cost_measured_achieved_runs: number;
    time_measured_achieved_runs: number;
    token_measured_achieved_runs: number;
  };
  costs: {
    measured_cost: number | null;
    measured_cost_per_run: number | null;
    total_tokens: number | null;
    token_runs: number;
    cost: MeasurementCoverage;
    tokens: MeasurementCoverage;
  };
  cost_unavailable: Record<string, number>;
  models: Performance[];
  providers: Performance[];
  workflows: Performance[];
  stages: Array<{
    label: string;
    calls: number;
    executions: number;
    usual_seconds: number | null;
    time_seconds: number;
    known_cost: number | null;
    total_tokens: number | null;
    failures: number;
    extra_attempts?: number;
    workflow?: string;
    source?: "declared_workflow" | "observed_operations";
    cost_eligible_operations: number;
    cost_measured_operations: number;
    token_eligible_operations: number;
    token_measured_operations: number;
  }>;
  runtime_breakdown: Record<string, number>;
  outcome_breakdown: Record<string, number>;
  failures: Array<{
    failure_location: string;
    failures: number;
    terminal_runs: number;
    recovered_runs: number;
    known_cost: number | null;
    time_seconds: number;
    total_tokens?: number | null;
    affected_run_time_seconds: number;
    affected_run_cost: number | null;
    affected_run_tokens: number | null;
  }>;
  evaluations: Array<{
    key: string;
    name: string;
    description?: string;
    unit?: string;
    target?: number | boolean | string;
    direction?: string;
    reported_runs: number;
    average_score: number | null;
    labels: Record<string, number>;
  }>;
  goal_misses: Array<{
    reason: string;
    runs: number;
    known_cost: number;
    cost_measured_runs: number;
    time_seconds: number;
    time_measured_runs: number;
  }>;
  goal_trend: Array<{
    date: string;
    reported_runs: number;
    achieved_runs: number;
    success_rate: number;
    time_per_achieved_goal: number | null;
    cost_per_achieved_goal: number | null;
    duration_runs: number;
    cost_runs: number;
  }>;
  goal_portfolio: GoalPortfolioItem[];
  assurance_summary: {
    reported_runs: number;
    achieved_runs: number;
    assured_runs: number;
    attention_runs: number;
    not_achieved_runs: number;
    unassessed_runs: number;
    assurance_rate: number;
    attention_rate: number;
    assessment_coverage: number;
  };
  operation_health: OperationSummary;
  operation_measurement_coverage: OperationMeasurementCoverage;
  operation_measurement_alerts: Array<{
    operation_type: string;
    measurement_key: string;
    operations: number;
    executions: number;
    workflow_ids: string[];
  }>;
  paths: Array<{
    path: string;
    steps: string[];
    executions: number;
    failures: number;
    usual_seconds: number | null;
    known_cost: number | null;
  }>;
  contracts: ContractDefinition[];
};
export type MeasurementCoverage = {
  total_runs: number;
  applicable_runs: number;
  complete_runs: number;
  partial_runs: number;
  missing_runs: number;
  not_applicable_runs: number;
  eligible_operations: number;
  measured_operations: number;
  coverage: number;
  operation_coverage: number;
};
export type GoalEvaluationSummary = {
  key: string;
  name: string;
  description?: string;
  unit?: string;
  target?: number | boolean | string;
  direction?: string;
  reported_runs: number;
  passed_runs: number;
  attention_runs: number;
  average_score: number | null;
};
export type GoalPortfolioItem = {
  goal_id: string;
  contract_hash: string | null;
  contract_hashes: string[];
  contract_count: number;
  contract_name?: string;
  goal_name: string;
  description?: string;
  runs: number;
  achieved_runs: number;
  assured_runs: number;
  attention_runs: number;
  not_achieved_runs: number;
  unassessed_runs: number;
  success_rate: number;
  assurance_rate: number;
  assessment_coverage: number;
  top_attention?: GoalEvaluationSummary | null;
  evaluations: GoalEvaluationSummary[];
};
export type ContractDefinition = {
  contract_hash: string;
  contract_name?: string;
  contract_version?: string;
  run_count: number;
  service?: { name?: string; description?: string; runtime?: string };
  contract?: { name?: string; description?: string };
  result?: {
    name?: string;
    description?: string;
    values?: Record<
      string,
      string | { description?: string; tone?: "success" | "warning" | "failure" | "neutral" }
    >;
  };
  decision?: {
    name?: string;
    description?: string;
    outcomes?: Record<string, string>;
    values?: Record<
      string,
      string | { description?: string; tone?: "success" | "warning" | "failure" | "neutral" }
    >;
  };
  product_goal?: { name?: string; description?: string; subject?: string };
  evaluations?: Array<{
    name?: string;
    description?: string;
    unit?: string;
    target?: number;
    direction?: string;
  }>;
  metrics?: Array<{ name?: string; description?: string; unit?: string }>;
  dimensions?: Array<{ key?: string; name?: string; description?: string }> | string[];
};
export type Meta = {
  product: string;
  mode: string;
  contracts: ContractDefinition[];
  filters: Record<string, string[]>;
  versions?: Record<string, string>;
  update?: {
    status: string;
    latest?: Record<string, string>;
    compatibility?: { compatible: boolean; protocol: string; platform: string };
    guidance?: Record<string, string>;
    release_notes_url?: string;
  };
};
export type DashboardFilters = {
  workflow?: string;
  workflow_id?: string;
  contract_hash?: string;
  provider?: string;
  model?: string;
  status?: string;
  start_date?: string;
  end_date?: string;
};
export type Run = Record<string, unknown> & {
  execution_id: string;
  started_at?: string;
  ended_at?: string;
  workflow?: string;
  display_name?: string;
  runtime_outcome?: string;
  status?: string;
  duration_seconds?: number;
  known_cost?: number;
  total_tokens?: number;
  provider?: string;
  model?: string;
  application_outcome?: string;
  product_goal_achieved?: boolean;
  workflow_active_steps?: number;
  workflow_total_steps?: number;
  workflow_attempts?: number;
  workflow_retry_attempts?: number;
  workflow_recovered_steps?: number;
  workflow_failed_steps?: number;
  workflow_models?: string[];
  workflow_providers?: string[];
  contract_hash?: string;
  contract_name?: string;
  canonical_url?: string | null;
};
export type RunDetail = {
  summary: Run;
  outcomes: Record<string, unknown>;
  graph: {
    nodes: Array<
      Record<string, unknown> & {
        id: string;
        display_name?: string;
        name?: string;
        kind?: string;
        status?: string;
      }
    >;
    edges: Array<{ source: string; target: string; relation: string }>;
  };
  semantic_records: Array<Record<string, unknown>>;
  workflow_replay?: WorkflowReplay | null;
  operation_summary?: OperationSummary;
  measurements?: OperationMeasurement[];
  measurement_coverage?: OperationMeasurementCoverage;
  evaluation_results?: Array<Record<string, unknown>>;
  canonical_url?: string | null;
};

export type OperationMeasurement = {
  operation_id: string;
  execution_id: string;
  workflow_id: string;
  node_id?: string | null;
  measurement_key: string;
  value: number | null;
  unit: string;
  measurement_status: "measured" | "missing" | "not_applicable";
  provenance: string;
};
export type OperationMeasurementCoverage = {
  measured: number;
  missing: number;
  not_applicable: number;
  applicable: number;
  coverage: number | null;
};
export type OperationFact = {
  operation_id: string;
  execution_id: string;
  workflow_id: string;
  node_id?: string | null;
  family: string;
  operation_type: string;
  subtype?: string | null;
  interface: string;
  role: string;
  input_modalities: string[];
  output_modalities: string[];
  provider_id?: string | null;
  model_id?: string | null;
  gateway_id?: string | null;
  vendor_id?: string | null;
  runtime_id?: string | null;
  framework_id?: string | null;
  duration_seconds: number;
  status: string;
  attributes: Record<string, unknown>;
};
export type OperationTypeSummary = {
  type: string;
  family: string;
  operations: number;
  failed: number;
  active_seconds: number;
  roles: string[];
  interfaces: string[];
  providers: string[];
  models: string[];
  measurements: Record<string, number>;
};
export type OperationSummary = {
  total_operations: number;
  failed_operations: number;
  types: OperationTypeSummary[];
};
export type WorkflowOperations = {
  workflow_id: string;
  summary: OperationSummary;
  measurement_coverage: OperationMeasurementCoverage;
  operations: OperationFact[];
  measurements: OperationMeasurement[];
};
export type EvaluationResult = {
  evaluation_id: string;
  execution_id: string;
  execution_started_at?: string | null;
  subject_id: string;
  name: string;
  value?: number | string | boolean | null;
  label?: string | null;
  score?: number | null;
  source?: string | null;
  confidence?: number | null;
  definition_version?: string | null;
  passed?: boolean | null;
  attributes: Record<string, unknown>;
};
export type WorkflowEvaluations = {
  workflow_id: string;
  summary: { reported: number; passed: number; needs_attention: number; unassessed: number; executions: number };
  results: EvaluationResult[];
  campaigns: Array<Record<string, unknown>>;
};

export type WorkflowDefinitionSummary = {
  version: number;
  id: string;
  name: string;
  description?: string | null;
  framework?: string | null;
  template_hash: string;
  stage_count: number;
  node_count: number;
  execution_count: number;
  latest_execution?: Run | null;
};

export type WorkflowProjectionAnalytics = Pick<Overview, "models" | "providers" | "stages">;

export type DeclaredWorkflow = {
  version: 1;
  id: string;
  name: string;
  description?: string | null;
  framework?: string | null;
  template_hash: string;
  stages: Array<{ id: string; name: string; description?: string | null; depends_on: string[]; nodes: string[] }>;
  nodes: Array<{ id: string; name: string; description?: string | null; kind?: string | null; depends_on?: Array<{ node: string; type?: string | null; route?: string | null; label?: string | null }>; retry?: { via?: string | null; max_attempts?: number | null } | null }>;
  transitions: Array<{ from: string; to: string; type: "next" | "branch" | "convergence" | "loop" | "fallback"; label?: string | null; route?: string | null }>;
  outcomes: Array<{ id: string; name: string; from: string[] }>;
};

export type ProjectedWorkflowNode = DeclaredWorkflow["nodes"][number] & {
  state: "inactive" | "completed" | "recovered" | "failed";
  attempts: number;
  duration_seconds: number | null;
  known_cost: number | null;
  total_tokens: number | null;
  providers: string[];
  models: string[];
  emitted_route?: unknown;
  observations: Array<Record<string, unknown>>;
  model_calls: Array<Record<string, unknown>>;
};

export type WorkflowReplay = {
  workflow: DeclaredWorkflow;
  execution: Run;
  stages: Array<DeclaredWorkflow["stages"][number] & { state: string; active_nodes: number; duration_seconds: number | null; known_cost: number | null; total_tokens: number | null }>;
  nodes: ProjectedWorkflowNode[];
  transitions: DeclaredWorkflow["transitions"];
  outcomes: DeclaredWorkflow["outcomes"];
  discrepancies: {
    unexpected_operations: Array<{ id: string; name: string; kind: string }>;
    unexpected_transitions: Array<{ from: string; to: string }>;
  };
};

async function get<T>(path: string, attempt = 0): Promise<T> {
  const response = await fetch(path);
  if (response.status === 503 && attempt < 2) {
    await new Promise((resolve) => window.setTimeout(resolve, 150 * (attempt + 1)));
    return get<T>(path, attempt + 1);
  }
  if (!response.ok)
    throw new Error(
      (await response.json().catch(() => null))?.detail ??
        `Request failed: ${response.status}`,
    );
  return response.json() as Promise<T>;
}
const withFilters = (path: string, filters: Record<string, string | undefined> = {}) => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return query ? `${path}?${query}` : path;
};
export const api = {
  meta: () => get<Meta>("/api/v1/meta"),
  overview: (filters: DashboardFilters = {}) =>
    get<Overview>(withFilters("/api/v1/overview", filters)),
  runs: (filters: DashboardFilters = {}, page = 1, pageSize = 10) =>
    get<{ items: Run[]; count: number; page: number; page_size: number; pages: number }>(
      withFilters("/api/v1/runs", { ...filters, page: String(page), page_size: String(pageSize) }),
    ),
  run: (id: string) => get<RunDetail>(`/api/v1/runs/${encodeURIComponent(id)}`),
  workflowDefinitions: () =>
    get<{ items: WorkflowDefinitionSummary[] }>("/api/v1/workflow-definitions"),
  workflowDefinition: (id: string) =>
    get<{ workflow: DeclaredWorkflow; executions: Run[]; analytics: WorkflowProjectionAnalytics }>(`/api/v1/workflow-definitions/${encodeURIComponent(id)}`),
  workflowExecution: (workflowId: string, executionId: string) =>
    get<RunDetail>(`/api/v1/workflow-definitions/${encodeURIComponent(workflowId)}/executions/${encodeURIComponent(executionId)}`),
  workflowOperations: (workflowId: string) =>
    get<WorkflowOperations>(`/api/v1/workflow-definitions/${encodeURIComponent(workflowId)}/operations`),
  workflowEvaluations: (workflowId: string) =>
    get<WorkflowEvaluations>(`/api/v1/workflow-definitions/${encodeURIComponent(workflowId)}/evaluations`),
  compare: (dimension: string, filters: DashboardFilters = {}) =>
    get<{ dimension: string; items: ComparisonInsight[] }>(
      withFilters(`/api/v1/compare/${dimension}`, filters),
    ),
  workflows: (filters: DashboardFilters = {}) =>
    get<{ items: WorkflowInsight[]; stages: WorkflowStage[]; paths: WorkflowPath[] }>(
      withFilters("/api/v1/workflows", filters),
    ),
  issues: (filters: DashboardFilters = {}) =>
    get<Issues>(withFilters("/api/v1/issues", filters)),
};

export const formatNumber = (value?: number | null) => {
  if (value == null || !Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude >= 1) {
    return new Intl.NumberFormat("en-US", {
      notation: magnitude >= 1000 ? "compact" : "standard",
      maximumFractionDigits: 1,
    }).format(value);
  }
  if (magnitude >= 0.1) {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  }
  return new Intl.NumberFormat("en-US", {
    maximumSignificantDigits: 4,
  }).format(value);
};

export const money = (value?: number | null) => {
  if (value == null || !Number.isFinite(value)) return "Not measured";
  const magnitude = Math.abs(value);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: magnitude >= 1000 ? "compact" : "standard",
    ...(magnitude >= 1
      ? { maximumFractionDigits: 1 }
      : magnitude >= 0.1
        ? { minimumFractionDigits: 2, maximumFractionDigits: 2 }
        : { maximumSignificantDigits: 4 }),
  }).format(value);
};

export const seconds = (value?: number | null) =>
  value == null || !Number.isFinite(value)
    ? "Not observed"
    : Math.abs(value) < 1
      ? `${formatNumber(value * 1000)} ms`
      : `${formatNumber(value)} s`;

export const percent = (value?: number | null) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : `${formatNumber(value * 100)}%`;
