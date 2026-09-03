import { Link, useParams, useRouterState } from "@tanstack/react-router";
import { Graph as DagreGraph, layout as runDagreLayout, type Point } from "@dagrejs/dagre";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DeclaredWorkflow, type EvaluationResult, type OperationFact, type OperationMeasurement, type OperationSummary, type OperationTypeSummary, type ProjectedWorkflowNode, type Run, type WorkflowDefinitionSummary, type WorkflowEvaluations, type WorkflowOperations, type WorkflowReplay } from "./api";
import { AnalyticsChart, AttributionHealthChart, Badge, Button, Empty, ErrorPage, ExecutionListCard, ExecutionStepDiagnostics, ExecutionTrendChart, LoadingPage, PageHeader, Panel, RatioDonutChart, RetryPressureChart, RuntimeDonutChart, StageDiagnosticsChart, StatusBadge, chartColors, formatBrowserDate, formatDateTime, formatNumber, money, seconds, stableColor, useQuery } from "./components";
import { contractOutcomeColors } from "./outcome-colors";

export const workflowRunsHref = (
  workflowId: string,
  filter: { model?: string; provider?: string } = {},
) => `/runs?${new URLSearchParams({ workflow_id: workflowId, ...filter }).toString()}`;

export const statePresentation = (state: string) => {
  if (state === "failed") {
    return { label: "Failed", border: "#dc5a5a", badge: "bg-red-50 text-red-700 ring-red-200" };
  }
  if (state === "recovered") {
    return { label: "Recovered", border: "#d58b24", badge: "bg-amber-50 text-amber-800 ring-amber-200" };
  }
  if (state === "active" || state === "running") {
    return { label: "Running", border: "#4386c6", badge: "bg-blue-50 text-blue-700 ring-blue-200" };
  }
  if (state === "inactive") {
    return { label: "Not used", border: "#cfcdc6", badge: "bg-stone-100 text-stone-600 ring-stone-200" };
  }
  if (state === "declared") {
    return { label: "Declared", border: "#cfcdc6", badge: "bg-stone-100 text-stone-600 ring-stone-200" };
  }
  return { label: "Completed", border: "#16864b", badge: "bg-green-50 text-green-800 ring-green-200" };
};

export const goalStageDotColor = (state: string, investigating: boolean) =>
  investigating ? "#a8a29e" : statePresentation(state).border;

type CardMetric = { label: string; value: string };
type EvidenceGraphData = Record<string, unknown> & {
  eyebrow: string;
  title: string;
  detail?: string;
  tone?: "success" | "recovered" | "running" | "operation" | "model" | "failure";
};
type EvidenceGraphNode = { id: string; position: { x: number; y: number }; data: EvidenceGraphData };
type EvidenceGraphEdge = { id: string; source: string; target: string };

function WorkflowListCard({ workflow }: { workflow: WorkflowDefinitionSummary }) {
  const latest = workflow.latest_execution;
  const runtime = String(latest?.runtime_outcome || latest?.status || "Not observed");
  const result = String(latest?.application_outcome || "Not reported").replaceAll("_", " ");
  const goal = latest?.product_goal_achieved === true
    ? latest.evidence_sufficient === false ? "Achieved · attention" : "Achieved"
    : latest?.product_goal_achieved === false ? "Not achieved" : "Not reported";
  const provider = latest?.workflow_providers?.join(", ") || String(latest?.provider || "Provider not observed");
  const model = latest?.workflow_models?.join(", ") || String(latest?.model || "Model not observed");
  const healthy = runtime.toLowerCase() === "completed" && latest?.product_goal_achieved === true;
  const definitionHash = workflow.template_hash.slice(0, 8);
  const adapterVersion = latest?.adapter_version ? `SDK v${latest.adapter_version}` : "SDK version not observed";
  return <Link to="/workflows/$workflowId" params={{ workflowId: workflow.id }} className="group relative flex min-h-[370px] min-w-0 flex-col overflow-hidden rounded-xl border border-[#e8e7e2] bg-white px-5 py-5 transition hover:-translate-y-px hover:border-[#cfc6ef] hover:shadow-[0_8px_24px_rgba(45,35,78,.07)]">
    <span className={`absolute inset-y-0 left-0 w-1 ${healthy ? "bg-[#25a86b]" : latest?.product_goal_achieved === false ? "bg-[#df5a5a]" : latest ? "bg-[#f0a128]" : "bg-[#d8d5cd]"}`} />
    <div className="flex min-w-0 items-start justify-between gap-4 pl-1">
      <div className="min-w-0">
        <div className="text-[10px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Workflow definition · v{workflow.version}</div>
        <h2 className="mt-1 truncate text-lg font-semibold text-[#3f277f] group-hover:text-[#5c35c8]">{workflow.name}</h2>
      </div>
      {workflow.framework ? <Badge color="purple">{workflow.framework}</Badge> : null}
    </div>
    <p className="mt-2 min-h-[42px] pl-1 text-sm leading-5 text-[#74746e] line-clamp-3">{workflow.description || "No workflow description."}</p>
    <div className="mt-4 border-y border-[#efeee9] py-3 pl-1">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[#74746e]">
        <StatusBadge value={runtime} />
        <span>{latest ? formatDateTime(latest.started_at) : "No execution observed"}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-4 text-xs text-[#74746e]">
        <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Provider</div><div className="mt-1 break-words leading-4" title={provider}>{provider}</div></div>
        <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Model</div><div className="mt-1 break-words font-medium leading-4 text-[#55554f]" title={model}>{model}</div></div>
      </div>
    </div>
    <div className="grid grid-cols-2 gap-x-6 gap-y-3 py-4">
      <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Latest result</div><div className="mt-1 break-words text-xs font-semibold capitalize leading-4 text-[#33332f]" title={result}>{result}</div></div>
      <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Product goal</div><div className="mt-1"><Badge color={goal === "Achieved" ? "green" : goal === "Not achieved" ? "red" : goal === "Achieved · attention" ? "yellow" : "gray"}>{goal}</Badge></div></div>
      <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Topology</div><div className="mt-1 text-xs font-semibold text-[#34342f]">{workflow.stage_count} stages · {workflow.node_count} steps</div></div>
      <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#92918a]">Executions</div><div className="mt-1 text-xs font-semibold text-[#34342f]">{formatNumber(workflow.execution_count)}</div></div>
    </div>
    <div className="mt-auto flex min-w-0 items-center justify-between gap-3 border-t border-[#efeee9] pt-3 pl-1 text-[10px] text-[#85817f]">
      <div className="flex min-w-0 items-center gap-3"><span>Definition {definitionHash}</span><span className="truncate" title={adapterVersion}>{adapterVersion}</span></div>
      <span className="shrink-0 font-semibold text-[#5c35c8]">Open workflow →</span>
    </div>
  </Link>;
}

export function WorkflowDefinitionsPage() {
  const q = useQuery({ queryKey: ["workflow-definitions"], queryFn: api.workflowDefinitions });
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  return <>
    <PageHeader title="Workflows" description="Declared workflow structures and the executions observed against each template." />
    <div className="grid gap-4 lg:grid-cols-2">
      {q.data!.items.map((workflow) => <WorkflowListCard key={workflow.id} workflow={workflow} />)}
    </div>
    {!q.data!.items.length ? <Panel title="No declared workflows"><Empty>No workflow definitions are registered. Add workflows to witdem.yml.</Empty></Panel> : null}
  </>;
}

export function WorkflowDefinitionPage() {
  const { workflowId } = useParams({ from: "/workflows/$workflowId" });
  const q = useQuery({ queryKey: ["workflow-definition", workflowId], queryFn: () => api.workflowDefinition(workflowId) });
  const operationQuery = useQuery({ queryKey: ["workflow-operations", workflowId], queryFn: () => api.workflowOperations(workflowId) });
  const evaluationQuery = useQuery({ queryKey: ["workflow-evaluations", workflowId], queryFn: () => api.workflowEvaluations(workflowId) });
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  const workflow = q.data!.workflow;
  const executions = q.data!.executions;
  const stats = summarizeWorkflowRuns(q.data!.executions);
  return <>
    <PageHeader compact eyebrow="Workflow" title={workflow.name} description={workflow.description || "Declared workflow template"} action={<Link to="/workflows"><Button variant="outline">All workflows</Button></Link>} />
    <WorkflowSubnav workflowId={workflowId} />
    <Panel title="Declared structure" note="The YAML topology stays stable while telemetry activates the path taken by each runtime.">
      <DeclaredOverview replay={{ workflow, execution: { execution_id: "template" }, stages: workflow.stages.map((stage) => ({ ...stage, state: "inactive", active_nodes: 0, duration_seconds: null, known_cost: null, total_tokens: null })), nodes: [], transitions: workflow.transitions, outcomes: workflow.outcomes, discrepancies: { unexpected_operations: [], unexpected_transitions: [] } }} />
    </Panel>
    <WorkflowContextSummary workflowId={workflowId} operations={operationQuery.data} evaluations={evaluationQuery.data} />
    <WorkflowAtAGlance executions={executions} stats={stats} overview={q.data!.analytics} workflowId={workflowId} />
  </>;
}

function WorkflowSubnav({ workflowId }: { workflowId: string }) {
  const path = useRouterState({ select: (state) => state.location.pathname });
  const links = [
    { label: "Overview", to: `/workflows/${encodeURIComponent(workflowId)}` },
    { label: "Operations", to: `/workflows/${encodeURIComponent(workflowId)}/operations` },
    { label: "Evaluations", to: `/workflows/${encodeURIComponent(workflowId)}/evaluations` },
    { label: "Executions", to: `/workflows/${encodeURIComponent(workflowId)}/executions` },
  ];
  return <nav className="mb-4 flex w-fit rounded-lg border border-[#ddd8e5] bg-[#f3f1f5] p-1" aria-label="Workflow views">{links.map((item) => <a key={item.label} href={item.to} aria-current={path === item.to ? "page" : undefined} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${path === item.to ? "bg-white text-[#5b3aa5] shadow-sm" : "text-[#767079] hover:text-[#4c4650]"}`}>{item.label}</a>)}</nav>;
}

function WorkflowContextSummary({ workflowId, operations, evaluations }: { workflowId: string; operations?: WorkflowOperations; evaluations?: WorkflowEvaluations }) {
  return <div className="mt-4 grid gap-3 md:grid-cols-2">
    <a href={`/workflows/${encodeURIComponent(workflowId)}/operations`} className="rounded-lg border border-[#e5e2e8] bg-white p-3 text-left transition hover:border-[#cfc6ef]">
      <div className="flex items-center justify-between"><h3 className="text-xs font-semibold">Operation profile</h3><span className="text-[10px] font-semibold text-[#6544b0]">Open →</span></div>
      <p className="mt-1 text-[10px] text-[#7c767e]">{formatNumber(operations?.summary.total_operations)} observed operations · {formatNumber(operations?.summary.types.length)} types · {formatNumber(operations?.summary.failed_operations)} failed</p>
    </a>
    <a href={`/workflows/${encodeURIComponent(workflowId)}/evaluations`} className="rounded-lg border border-[#e5e2e8] bg-white p-3 text-left transition hover:border-[#cfc6ef]">
      <div className="flex items-center justify-between"><h3 className="text-xs font-semibold">Evaluation status</h3><span className="text-[10px] font-semibold text-[#6544b0]">Open →</span></div>
      <p className="mt-1 text-[10px] text-[#7c767e]">{formatNumber(evaluations?.summary.reported)} reported · {formatNumber(evaluations?.summary.passed)} passed · {formatNumber(evaluations?.summary.needs_attention)} need attention</p>
    </a>
  </div>;
}

export function WorkflowOperationsPage() {
  const { workflowId } = useParams({ from: "/workflows/$workflowId/operations" });
  const workflow = useQuery({ queryKey: ["workflow-definition", workflowId], queryFn: () => api.workflowDefinition(workflowId) });
  const operations = useQuery({ queryKey: ["workflow-operations", workflowId], queryFn: () => api.workflowOperations(workflowId) });
  if (workflow.isLoading || operations.isLoading) return <LoadingPage />;
  if (workflow.error) return <ErrorPage error={workflow.error} />;
  if (operations.error) return <ErrorPage error={operations.error} />;
  return <>
    <PageHeader compact eyebrow="Workflow operations" title={workflow.data!.workflow.name} description="What ran, where time and usage accumulated, and which providers or models performed the work." action={<Link to="/workflows"><Button variant="outline">All workflows</Button></Link>} />
    <WorkflowSubnav workflowId={workflowId} />
    <WorkflowOperationsView workflowId={workflowId} data={operations.data} loading={false} />
  </>;
}

export function WorkflowEvaluationsPage() {
  const { workflowId } = useParams({ from: "/workflows/$workflowId/evaluations" });
  const workflow = useQuery({ queryKey: ["workflow-definition", workflowId], queryFn: () => api.workflowDefinition(workflowId) });
  const evaluations = useQuery({ queryKey: ["workflow-evaluations", workflowId], queryFn: () => api.workflowEvaluations(workflowId) });
  if (workflow.isLoading || evaluations.isLoading) return <LoadingPage />;
  if (workflow.error) return <ErrorPage error={workflow.error} />;
  if (evaluations.error) return <ErrorPage error={evaluations.error} />;
  return <>
    <PageHeader compact eyebrow="Workflow evaluations" title={workflow.data!.workflow.name} description="Assessment coverage, score-versus-target results, regressions, and the exact executions behind them." action={<Link to="/workflows"><Button variant="outline">All workflows</Button></Link>} />
    <WorkflowSubnav workflowId={workflowId} />
    <WorkflowEvaluationsView workflowId={workflowId} data={evaluations.data} loading={false} />
  </>;
}

export function WorkflowExecutionsPage() {
  const { workflowId } = useParams({ from: "/workflows/$workflowId/executions" });
  const q = useQuery({ queryKey: ["workflow-definition", workflowId], queryFn: () => api.workflowDefinition(workflowId) });
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  const workflow = q.data!.workflow;
  const executions = q.data!.executions;
  return <>
    <PageHeader compact eyebrow="Workflow executions" title={workflow.name} description="Runs matched to this workflow and its historical template versions." action={<Link to="/workflows"><Button variant="outline">All workflows</Button></Link>} />
    <WorkflowSubnav workflowId={workflowId} />
    <Panel title="Executions" note="Select a run to inspect its path, operations, measurements, and evaluations.">
      <div className="space-y-2">{executions.map((run) => <ExecutionListCard key={run.execution_id} run={run} href={`/workflows/${encodeURIComponent(workflowId)}/executions/${encodeURIComponent(run.execution_id)}`} />)}</div>
      {!executions.length ? <Empty>No executions have matched this workflow yet.</Empty> : null}
      {executions.length ? <div className="mt-4 flex justify-end border-t border-[#eceae4] pt-4"><a href={workflowRunsHref(workflowId)} className="text-xs font-semibold text-[#5c35c8] hover:underline">See all executions for this workflow →</a></div> : null}
    </Panel>
  </>;
}

function WorkflowOperationsView({ workflowId, data, loading }: { workflowId: string; data?: WorkflowOperations; loading: boolean }) {
  const [selectedType, setSelectedType] = useState<string | null>(null);
  if (loading) return <LoadingPage />;
  if (!data?.summary.types.length) return <Panel title="Operations"><Empty>No classified operations have been materialized yet. Run workflow rebuild after telemetry arrives.</Empty></Panel>;
  const operationPlane = (item: OperationTypeSummary) => item.plane || (["orchestration", "agent_control"].includes(item.family) ? "control" : "work");
  const workTypes = data.summary.types.filter((item) => operationPlane(item) === "work");
  const controlTypes = data.summary.types.filter((item) => operationPlane(item) === "control");
  const workTypeIds = new Set(workTypes.map((item) => item.type));
  const workOperations = data.operations.filter((operation) => (operation.entity_kind || "operation") === "operation" && workTypeIds.has(operation.operation_type));
  const workOperationIds = new Set(workOperations.map((operation) => operation.operation_id));
  const workMeasurements = data.measurements.filter((measurement) => workOperationIds.has(measurement.operation_id));
  const executionCount = new Set(workOperations.map((operation) => operation.execution_id)).size;
  const measuredMeters = workMeasurements.filter((measurement) => measurement.measurement_status === "measured").length;
  const missingMeters = workMeasurements.filter((measurement) => measurement.measurement_status === "missing").length;
  const applicableMeters = measuredMeters + missingMeters;
  const meterCoverage = applicableMeters ? measuredMeters / applicableMeters : null;
  const selectedOperationIds = new Set(
    workOperations
      .filter((operation) => !selectedType || operation.operation_type === selectedType)
      .map((operation) => operation.operation_id),
  );
  if (selectedType) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const operation of workOperations) {
        if (operation.parent_operation_id && selectedOperationIds.has(operation.parent_operation_id) && !selectedOperationIds.has(operation.operation_id)) {
          selectedOperationIds.add(operation.operation_id);
          changed = true;
        }
      }
    }
  }
  const selectedOperations = workOperations.filter((operation) => selectedOperationIds.has(operation.operation_id)).sort((left, right) => right.duration_seconds - left.duration_seconds).slice(0, 12);
  const selectedLabel = selectedType ? operationLabel(selectedType) : "All operation types";
  return <div className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <InsightCard label="Work operations" value={formatNumber(workOperations.length)} note={`${formatNumber(executionCount)} executions · ${formatNumber(workTypes.length)} operation types`} />
      <InsightCard label="Direct failures" value={formatNumber(workOperations.filter((operation) => ["error", "failed"].includes(operation.status)).length)} note="Failures in computational, external, or human work" tone={workOperations.some((operation) => ["error", "failed"].includes(operation.status)) ? "attention" : "good"} />
      <InsightCard label="Usage coverage" value={meterCoverage == null ? "Not applicable" : `${Math.round(meterCoverage * 100)}%`} note={`${formatNumber(measuredMeters)} measured · ${formatNumber(missingMeters)} missing applicable meters`} tone={missingMeters ? "attention" : "good"} />
      <InsightCard label="Participants" value={formatNumber(uniqueIdentities(workOperations.flatMap((operation) => [operation.provider_id, operation.model_id, operation.implementation_id])).length)} note="Distinct reported providers, models, and implementations" />
    </div>
    {controlTypes.length ? <Panel title="Control-flow profile" note="How the system coordinated work. Execution containers remain in run headers and are not counted as operations.">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {controlTypes.map((item) => <CoordinationCard key={item.type} title={operationLabel(item.type)} value={`${formatNumber(item.operations)} operations`} detail={seconds(item.active_seconds)} explanation={`${operationFamilyLabel(item.family)} control activity${item.failed ? ` · ${formatNumber(item.failed)} failed` : ""}.`} />)}
      </div>
    </Panel> : null}
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="Where work happened" note="Work-plane operations only. Switch between volume, active time, cost, tokens, and operation-specific meters."><OperationActivityChart items={workTypes} onSelect={setSelectedType} /></Panel>
      <Panel title="Who performed the work" note="Directly attributed calls, latency, cost, and tokens by distinct participant identity."><ParticipantOperationChart operations={workOperations} measurements={workMeasurements} /></Panel>
    </div>
    <Panel title="Work profile" note="Computational, external, and human work—separate from control flow and business outcomes. Select a card to inspect supporting operations.">
      <div className="grid auto-rows-fr gap-2.5 md:grid-cols-2">{workTypes.map((item) => <OperationTypeCard key={item.type} item={item} active={selectedType === item.type} onClick={() => setSelectedType(selectedType === item.type ? null : item.type)} />)}</div>
    </Panel>
    <Panel title={`Observed operations · ${selectedLabel}`} note={selectedType ? "Selected operations and their nested child work. Open an execution to inspect the full path." : "Longest operations first. Open an execution to inspect the operation in its workflow path."}>
      <div className="overflow-hidden rounded-lg border border-[#e8e5e9]">
        <div className="grid grid-cols-[1.35fr_1fr_.7fr_.55fr] gap-3 bg-[#f5f3f6] px-3 py-2 text-[9px] font-semibold uppercase tracking-[.1em] text-[#847e86]"><span>Operation / node</span><span>Participant</span><span>Elapsed</span><span>Status</span></div>
        {selectedOperations.map((operation) => <a key={operation.operation_id} href={`/workflows/${encodeURIComponent(workflowId)}/executions/${encodeURIComponent(operation.execution_id)}`} className="grid grid-cols-[1.35fr_1fr_.7fr_.55fr] gap-3 border-t border-[#ece9ed] px-3 py-2.5 text-[10px] transition first:border-t-0 hover:bg-[#faf8ff]"><div className="min-w-0"><div className="truncate font-semibold text-[#37323a]">{operationLabel(operation.operation_type)}</div><div className="truncate text-[9px] text-[#89838b]">{operation.node_id || operation.subtype || "Observed operation"}</div></div><div className="min-w-0 truncate text-[#625c65]">{operation.model_id || operation.provider_id || operation.implementation_id || operation.interface || "Not reported"}</div><div>{seconds(operation.duration_seconds)}</div><div className={operation.status === "error" || operation.status === "failed" ? "font-semibold text-red-700" : "text-[#27754c]"}>{operation.status === "unset" ? "Observed" : operation.status}</div></a>)}
      </div>
      {!selectedOperations.length ? <Empty>No operations match this type.</Empty> : null}
    </Panel>
  </div>;
}

function CoordinationCard({ title, value, detail, explanation }: { title: string; value: string; detail: string; explanation: string }) {
  return <div className="grid min-w-0 gap-3 rounded-lg border border-[#e5e2e8] bg-[#fbfafc] p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"><div className="min-w-0"><div className="text-xs font-semibold text-[#39343e]">{title}</div><div className="mt-1 text-[9px] leading-4 text-[#777178]">{explanation}</div></div><div className="min-w-0 sm:text-right"><div className="text-sm font-semibold text-[#3e3650]">{value}</div><div className="mt-0.5 text-[9px] text-[#89838b]">{detail}</div></div></div>;
}

function WorkflowEvaluationsView({ workflowId, data, loading }: { workflowId: string; data?: WorkflowEvaluations; loading: boolean }) {
  const [selectedName, setSelectedName] = useState<string | null>(null);
  if (loading) return <LoadingPage />;
  if (!data) return null;
  const groups = groupEvaluations(data.results);
  const assessed = data.summary.passed + data.summary.needs_attention;
  const passRate = assessed ? data.summary.passed / assessed : null;
  const selected = selectedName ? data.results.filter((result) => result.name === selectedName) : data.results;
  return <div className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <InsightCard label="Assessed pass rate" value={passRate == null ? "Not assessed" : `${Math.round(passRate * 100)}%`} note={`${formatNumber(data.summary.passed)} passed of ${formatNumber(assessed)} assessed`} tone={data.summary.needs_attention ? "attention" : assessed ? "good" : "default"} />
      <InsightCard label="Evaluation coverage" value={`${formatNumber(data.summary.reported)} results`} note={`${formatNumber(data.summary.executions)} executions · ${formatNumber(groups.length)} definitions`} />
      <InsightCard label="Needs attention" value={formatNumber(data.summary.needs_attention)} note={`${formatNumber(data.summary.unassessed)} unassessed results remain neutral`} tone={data.summary.needs_attention ? "attention" : "good"} />
      <InsightCard label="Offline campaigns" value={formatNumber(data.campaigns.length)} note={data.campaigns.length ? "Dataset and regression campaigns" : "No campaign results imported"} />
    </div>
    {!data.results.length && !data.campaigns.length ? <Panel title="Evaluations"><Empty>No online evaluation results or offline campaigns have been reported.</Empty></Panel> : null}
    {data.results.length ? <div className="grid gap-4 xl:grid-cols-[.72fr_1.28fr]">
      <Panel title="Assessment state" note="Only explicit status or declared target semantics produce pass/attention."><EvaluationOutcomeChart summary={data.summary} /></Panel>
      <Panel title="Score versus declared target" note="Average observed score and target by evaluation definition. Select a bar for supporting runs."><EvaluationScoreChart groups={groups} onSelect={setSelectedName} /></Panel>
    </div> : null}
    {groups.length ? <Panel title="Evaluation definitions" note="Compact definition-level analytics replace repeated result rows. Select a card to drill into runs.">
      <div className="grid items-start gap-2.5 md:grid-cols-2 xl:grid-cols-3">{groups.map((group) => <EvaluationDefinitionCard key={group.name} group={group} active={selectedName === group.name} onClick={() => setSelectedName(selectedName === group.name ? null : group.name)} />)}</div>
    </Panel> : null}
    {selected.length ? <Panel title={`Supporting results · ${selectedName || "all evaluations"}`} note="The exact workflow executions behind the aggregate; evaluator economics remain in Operations.">
      <div className="overflow-hidden rounded-lg border border-[#e8e5e9]">
        <div className={`grid gap-3 bg-[#f5f3f6] px-3 py-1.5 text-[8px] font-semibold uppercase tracking-[.1em] text-[#847e86] ${selectedName ? "grid-cols-[1.35fr_.65fr_.75fr_.45fr]" : "grid-cols-[1.35fr_.7fr_.65fr_.45fr]"}`}><span>{selectedName ? "Execution" : "Definition / execution"}</span><span>Observed value</span><span>Assessment</span><span className="text-right">Source</span></div>
        {selected.slice(0, 18).map((result) => <a key={result.evaluation_id} href={`/workflows/${encodeURIComponent(workflowId)}/executions/${encodeURIComponent(result.execution_id)}`} className={`grid items-center gap-3 border-t border-[#ece9ed] px-3 py-2 text-[9px] transition hover:bg-[#faf8ff] ${selectedName ? "grid-cols-[1.35fr_.65fr_.75fr_.45fr]" : "grid-cols-[1.35fr_.7fr_.65fr_.45fr]"}`}><div className="min-w-0">{selectedName ? <><div className="truncate font-medium text-[#39343e]">{result.execution_id.slice(0, 12)}</div><div className="mt-0.5 truncate text-[8px] text-[#89838b]">{formatDateTime(result.execution_started_at)}</div></> : <><div className="truncate font-semibold">{result.name}</div><div className="truncate text-[8px] text-[#89838b]">{result.execution_id.slice(0, 12)} · {formatDateTime(result.execution_started_at)}</div></>}</div><div className="font-medium text-[#39343e]">{evaluationValue(result)}</div><div><EvaluationState result={result} /></div><div className="truncate text-right text-[8px] text-[#777178]" title={result.source || "reported"}>{evaluationSourceLabel(result.source)}</div></a>)}
      </div>
    </Panel> : null}
  </div>;
}

function InsightCard({ label, value, note, tone = "default" }: { label: string; value: string; note: string; tone?: "default" | "good" | "attention" }) {
  return <div className={`rounded-xl border bg-white p-4 ${tone === "attention" ? "border-red-200" : tone === "good" ? "border-emerald-200" : "border-[#e4e4df]"}`}><div className="text-[9px] font-semibold uppercase tracking-[.11em] text-[#88828a]">{label}</div><div className={`mt-2 text-xl font-semibold tracking-[-.02em] ${tone === "attention" ? "text-red-700" : tone === "good" ? "text-emerald-700" : "text-[#332e37]"}`}>{value}</div><div className="mt-1 text-[10px] leading-4 text-[#777178]">{note}</div></div>;
}

function OperationActivityChart({ items, onSelect }: { items: OperationTypeSummary[]; onSelect: (type: string) => void }) {
  const [metric, setMetric] = useState<"operations" | "time" | "cost" | "tokens" | "pages">("operations");
  const value = (item: OperationTypeSummary) => metric === "operations" ? item.operations : metric === "time" ? item.active_seconds : metric === "cost" ? item.measurements["cost.usd"] : metric === "tokens" ? item.measurements["tokens.total"] : item.measurements["pages.processed"];
  const rows = [...items].filter((item) => value(item) != null).sort((left, right) => Number(value(right)) - Number(value(left))).reverse();
  const labels = { operations: "Operations", time: "Active time", cost: "Measured cost", tokens: "Tokens", pages: "Pages" };
  return <div><MetricToggle choices={["operations", "time", "cost", "tokens", "pages"]} active={metric} onChange={setMetric} labels={{ operations: "Volume", time: "Active time", cost: "Cost", tokens: "Tokens", pages: "Pages" }} />{rows.length ? <AnalyticsChart style={{ height: 270, width: "100%" }} onEvents={{ click: (point: { data?: { item?: OperationTypeSummary } }) => point.data?.item && onSelect(point.data.item.type) }} option={{ color: [metric === "cost" ? "#16a085" : metric === "time" ? "#2477e6" : "#6d4aff"], tooltip: { trigger: "axis", confine: true, axisPointer: { type: "shadow" }, formatter: (points: Array<{ data: { item: OperationTypeSummary } }>) => { const item = points[0]?.data.item; return item ? `<b>${operationLabel(item.type)}</b><br/>${formatNumber(item.operations)} operations<br/>Active time: ${seconds(item.active_seconds)}<br/>Failures: ${formatNumber(item.failed)}<br/>Cost: ${money(item.measurements["cost.usd"])}<br/>Tokens: ${item.measurements["tokens.total"] == null ? "Not applicable" : formatNumber(item.measurements["tokens.total"])}<br/><span style="color:#6d4aff">Select to inspect operations</span>` : ""; } }, grid: { left: 132, right: 26, top: 12, bottom: 34 }, xAxis: { type: "value", name: labels[metric], nameLocation: "middle", nameGap: 26, axisLabel: { fontSize: 8, formatter: (raw: number) => metric === "time" ? seconds(raw) : metric === "cost" ? money(raw) : formatNumber(raw) }, splitLine: { lineStyle: { color: "#ecece7" } } }, yAxis: { type: "category", data: rows.map((item) => operationLabel(item.type)), axisLabel: { width: 122, overflow: "truncate", fontSize: 9 } }, series: [{ type: "bar", barMaxWidth: 20, data: rows.map((item) => ({ value: value(item), item })), itemStyle: { borderRadius: [0, 4, 4, 0] }, emphasis: { focus: "series" } }] }} /> : <Empty>This measurement is not applicable to the observed operation types.</Empty>}</div>;
}

function ParticipantOperationChart({ operations, measurements }: { operations: OperationFact[]; measurements: OperationMeasurement[] }) {
  const [dimension, setDimension] = useState<"provider" | "model" | "implementation">("provider");
  const [metric, setMetric] = useState<"calls" | "time" | "cost" | "tokens">("calls");
  const rows = participantOperationRows(operations, measurements, dimension).filter((row) => row[metric] != null).sort((left, right) => Number(right[metric]) - Number(left[metric])).slice(0, 10).reverse();
  return <div><div className="mb-2 flex flex-wrap justify-between gap-2"><MetricToggle choices={["provider", "model", "implementation"]} active={dimension} onChange={setDimension} labels={{ provider: "Provider", model: "Model", implementation: "Implementation" }} /><MetricToggle choices={["calls", "time", "cost", "tokens"]} active={metric} onChange={setMetric} labels={{ calls: "Calls", time: "Call time", cost: "Cost", tokens: "Tokens" }} /></div>{rows.length ? <AnalyticsChart style={{ height: 270, width: "100%" }} option={{ color: rows.map((row) => stableColor(`${dimension}:${row.id}`)), tooltip: { trigger: "axis", confine: true, axisPointer: { type: "shadow" }, formatter: (points: Array<{ data: { item: ParticipantOperationRow } }>) => { const item = points[0]?.data.item; return item ? `<b>${item.id}</b><br/>${formatNumber(item.calls)} calls<br/>Call time: ${seconds(item.time)}<br/>Measured cost: ${money(item.cost)}<br/>Tokens: ${item.tokens == null ? "Not measured" : formatNumber(item.tokens)}` : ""; } }, grid: { left: 150, right: 26, top: 12, bottom: 34 }, xAxis: { type: "value", axisLabel: { fontSize: 8, formatter: (raw: number) => metric === "time" ? seconds(raw) : metric === "cost" ? money(raw) : formatNumber(raw) }, splitLine: { lineStyle: { color: "#ecece7" } } }, yAxis: { type: "category", data: rows.map((row) => row.id), axisLabel: { width: 140, overflow: "truncate", fontSize: 9 } }, series: [{ type: "bar", barMaxWidth: 20, data: rows.map((row) => ({ value: row[metric], item: row, itemStyle: { color: stableColor(`${dimension}:${row.id}`), borderRadius: [0, 4, 4, 0] } })) }] }} /> : <Empty>No explicitly reported {dimension} measurements are available.</Empty>}</div>;
}

type ParticipantOperationRow = { id: string; calls: number; time: number; cost: number | null; tokens: number | null };
export function participantOperationRows(operations: OperationFact[], measurements: OperationMeasurement[], dimension: "provider" | "model" | "implementation") {
  const measurementByOperation = new Map<string, OperationMeasurement[]>();
  measurements.filter((item) => item.measurement_status === "measured").forEach((item) => measurementByOperation.set(item.operation_id, [...(measurementByOperation.get(item.operation_id) || []), item]));
  const grouped = new Map<string, ParticipantOperationRow>();
  operations.forEach((operation) => {
    const id = dimension === "provider" ? operation.provider_id : dimension === "model" ? operation.model_id : operation.implementation_id;
    if (!id) return;
    const row = grouped.get(id) || { id, calls: 0, time: 0, cost: null, tokens: null };
    row.calls += 1;
    row.time += operation.duration_seconds || 0;
    for (const measurement of measurementByOperation.get(operation.operation_id) || []) {
      if (measurement.measurement_key === "cost.usd" && measurement.value != null) row.cost = (row.cost || 0) + measurement.value;
      if (measurement.measurement_key === "tokens.total" && measurement.value != null) row.tokens = (row.tokens || 0) + measurement.value;
    }
    grouped.set(id, row);
  });
  return [...grouped.values()];
}

function OperationTypeCard({ item, active, onClick }: { item: OperationTypeSummary; active: boolean; onClick: () => void }) {
  const preferred = ["cost.usd", "tokens.total", "pages.processed", "documents.output", "results", "vectors.output"];
  const measurementKeys = Object.keys(item.measurements).sort((left, right) => {
    const leftRank = preferred.indexOf(left);
    const rightRank = preferred.indexOf(right);
    return (leftRank < 0 ? preferred.length : leftRank) - (rightRank < 0 ? preferred.length : rightRank) || left.localeCompare(right);
  }).slice(0, 3);
  const usage = measurementKeys.map((key) => [key, operationFamilyLabel(key.replaceAll(".", "_"))] as const);
  const participants = uniqueIdentities([...item.providers, ...item.implementations]);
  const linkedActivity = (item.linked_children || []).map((child) => {
    const identities = uniqueIdentities([...child.providers, ...child.models, ...child.implementations]);
    return `${operationLabel(child.type)}${identities.length ? `: ${identities.join(" · ")}` : ""}`;
  });
  return <button type="button" onClick={onClick} className={`flex h-full min-w-0 flex-col rounded-lg border p-3 text-left transition ${active ? "border-[#7658bd] bg-[#f8f5ff] shadow-sm" : "border-[#e5e2e8] bg-white hover:border-[#cfc6ef] hover:bg-[#fcfbff]"}`}>
    <div className="flex min-w-0 items-start justify-between gap-2.5"><div className="min-w-0"><div className="truncate text-[13px] font-semibold">{operationLabel(item.type)}</div><div className="mt-0.5 truncate text-[9px] text-[#817b83]">{operationFamilyLabel(item.family)} · {operationInterfaceLabel(item.interfaces, item.family)}</div></div><span className={`shrink-0 rounded-full px-2 py-0.5 text-[8px] font-semibold ${item.failed ? "bg-red-50 text-red-700" : "bg-[#eef8f2] text-[#27754c]"}`}>{item.failed ? `${item.failed} failed` : "Healthy"}</span></div>
    <div className="mt-2.5 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-[#ece9ed] bg-[#ece9ed]"><CardCell label="Operations" value={formatNumber(item.operations)} /><CardCell label="Active time" value={seconds(item.active_seconds)} /></div>
    {usage.length ? <div className={`mt-2 grid gap-px overflow-hidden rounded-md border border-[#ece9ed] bg-[#ece9ed] ${usage.length === 1 ? "grid-cols-1" : usage.length === 2 ? "grid-cols-2" : "grid-cols-3"}`}>{usage.map(([key, label]) => <CardCell key={key} label={label} value={key === "cost.usd" ? money(item.measurements[key]) : formatNumber(item.measurements[key])} />)}</div> : <div className="mt-2 text-[9px] text-[#89838b]">Usage meters not applicable</div>}
    <div className="mt-2 grid gap-2 border-t border-[#ece9ed] pt-2 sm:grid-cols-[.7fr_1.3fr]">
      <div className="min-w-0"><div className="text-[7px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Participants</div><div className="mt-0.5 break-words text-[9px] leading-4 text-[#5f5962]">{participants.join(", ") || "Not reported"}</div></div>
      <div className="min-w-0"><div className="text-[7px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Models</div><div className="mt-0.5 break-words text-[9px] leading-4 text-[#5f5962]">{item.models.join(", ") || (item.model_applicability === "not_applicable" ? "Not applicable" : "Not reported")}</div></div>
    </div>
    {linkedActivity.length ? <div className="mt-2 border-t border-[#ece9ed] pt-2"><div className="text-[7px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Linked child activity</div><div className="mt-0.5 break-words text-[9px] leading-4 text-[#5f5962]">{linkedActivity.join("; ")}</div></div> : null}
  </button>;
}

export function uniqueIdentities(values: Array<string | null | undefined>) {
  const seen = new Set<string>();
  return values.filter((value): value is string => {
    if (!value) return false;
    const key = value.trim().toLocaleLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const operationFamilyLabel = (family: string) => family.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
const operationInterfaceLabel = (interfaces: string[], family: string) => {
  const labels: Record<string, string> = { model_api: "Provider API", mcp: "MCP", tool: "Tool interface", framework: "Framework", library: "Library", datastore: "Datastore", vector_database: "Vector database", search_service: "Search service", local: "Application", external_api: "External API", browser: "Browser", human: "Human" };
  const resolved = interfaces.filter((value) => value !== "unknown").map((value) => labels[value] || operationFamilyLabel(value));
  if (resolved.length) return resolved.join(", ");
  return family === "orchestration" ? "Framework" : "Interface not reported";
};

function CardValue({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><div className="text-[8px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">{label}</div><div className="mt-0.5 truncate text-xs font-semibold text-[#39343e]" title={value}>{value}</div></div>; }
function CardCell({ label, value }: { label: string; value: string }) { return <div className="min-w-0 bg-[#fbfafc] px-2.5 py-2"><div className="text-[7px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">{label}</div><div className="mt-0.5 truncate text-[11px] font-semibold text-[#39343e]" title={value}>{value}</div></div>; }

type EvaluationGroup = { name: string; results: EvaluationResult[]; passed: number; attention: number; unassessed: number; averageScore: number | null; target: number | null; direction: string | null };
export function groupEvaluations(results: EvaluationResult[]): EvaluationGroup[] {
  const grouped = new Map<string, EvaluationResult[]>();
  results.forEach((result) => grouped.set(result.name, [...(grouped.get(result.name) || []), result]));
  return [...grouped].map(([name, rows]) => {
    const scores = rows.map((row) => row.score).filter((score): score is number => typeof score === "number");
    const targets = rows.map((row) => row.attributes.target).filter((target): target is number => typeof target === "number");
    const direction = rows.map((row) => row.attributes.direction).find((value) => typeof value === "string");
    return { name, results: rows, passed: rows.filter((row) => row.passed === true).length, attention: rows.filter((row) => row.passed === false).length, unassessed: rows.filter((row) => row.passed == null).length, averageScore: scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null, target: targets.length ? targets[0] : null, direction: typeof direction === "string" ? direction : null };
  }).sort((left, right) => right.results.length - left.results.length || left.name.localeCompare(right.name));
}

function EvaluationOutcomeChart({ summary }: { summary: WorkflowEvaluations["summary"] }) {
  return <AnalyticsChart style={{ height: 260, width: "100%" }} option={{ tooltip: { trigger: "item", formatter: (point: { name: string; value: number; percent: number }) => `<b>${point.name}</b><br/>${formatNumber(point.value)} results · ${formatNumber(point.percent)}%` }, legend: { orient: "vertical", right: 8, top: "center", itemWidth: 10, itemHeight: 10, selectedMode: true }, series: [{ type: "pie", radius: ["48%", "70%"], center: ["34%", "50%"], label: { show: false }, data: [{ name: "Passed", value: summary.passed, itemStyle: { color: "#16864b" } }, { name: "Needs attention", value: summary.needs_attention, itemStyle: { color: "#dc5a5a" } }, { name: "Unassessed", value: summary.unassessed, itemStyle: { color: "#aaa6ad" } }] }] }} />;
}

function EvaluationScoreChart({ groups, onSelect }: { groups: EvaluationGroup[]; onSelect: (name: string) => void }) {
  const rows = groups.filter((group) => group.averageScore != null).slice(0, 10).reverse();
  if (!rows.length) return <Empty>No numeric evaluation scores were reported.</Empty>;
  const maximum = Math.max(1, ...rows.flatMap((row) => [row.averageScore || 0, row.target || 0]));
  return <AnalyticsChart onEvents={{ click: (point: { data?: { item?: EvaluationGroup } }) => point.data?.item && onSelect(point.data.item.name) }} style={{ height: Math.max(260, rows.length * 54), width: "100%" }} option={{ color: ["#6d4aff", "#2d2a2f"], tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ data?: { item?: EvaluationGroup } }>) => { const item = points.find((point) => point.data?.item)?.data?.item; return item ? `<b>${item.name}</b><br/>Average score: ${formatNumber(item.averageScore)}<br/>Target: ${item.target == null ? "Not declared" : formatNumber(item.target)}<br/>${formatNumber(item.passed)} passed · ${formatNumber(item.attention)} attention · ${formatNumber(item.unassessed)} unassessed<br/><span style="color:#6d4aff">Select for supporting runs</span>` : ""; } }, legend: { top: 0, itemWidth: 10, itemHeight: 8 }, grid: { left: 190, right: 32, top: 38, bottom: 28 }, xAxis: { type: "value", min: 0, max: maximum, axisLabel: { fontSize: 8 }, splitLine: { lineStyle: { color: "#ecece7" } } }, yAxis: { type: "category", data: rows.map((row) => row.name), axisLabel: { width: 178, overflow: "truncate", fontSize: 9 } }, series: [{ name: "Average score", type: "bar", barMaxWidth: 18, data: rows.map((row) => ({ value: row.averageScore, item: row })), itemStyle: { borderRadius: [0, 4, 4, 0] } }, { name: "Declared target", type: "scatter", symbol: "rect", symbolSize: [4, 22], data: rows.filter((row) => row.target != null).map((row) => ({ value: [row.target, row.name], item: row })) }] }} />;
}

function EvaluationDefinitionCard({ group, active, onClick }: { group: EvaluationGroup; active: boolean; onClick: () => void }) {
  const assessed = group.passed + group.attention;
  const rate = assessed ? Math.round(group.passed / assessed * 100) : null;
  return <button type="button" onClick={onClick} className={`min-w-0 rounded-lg border p-3 text-left transition ${active ? "border-[#7658bd] bg-[#f8f5ff] shadow-sm" : "border-[#e5e2e8] bg-white hover:border-[#cfc6ef] hover:bg-[#fcfbff]"}`}>
    <div className="flex min-w-0 items-start justify-between gap-2.5"><div className="min-w-0 truncate text-[13px] font-semibold" title={group.name}>{group.name}</div><span className={`shrink-0 rounded-full px-2 py-0.5 text-[8px] font-semibold ${group.attention ? "bg-red-50 text-red-700" : assessed ? "bg-[#eef8f2] text-[#27754c]" : "bg-stone-100 text-stone-600"}`}>{rate == null ? "Unassessed" : `${rate}% pass`}</span></div>
    <div className="mt-2.5 grid grid-cols-3 gap-px overflow-hidden rounded-md border border-[#ece9ed] bg-[#ece9ed]"><CardCell label="Reported" value={formatNumber(group.results.length)} /><CardCell label="Average" value={group.averageScore == null ? "Not scored" : formatNumber(group.averageScore)} /><CardCell label="Target" value={group.target == null ? "None" : formatNumber(group.target)} /></div>
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[8px]"><span className="text-emerald-700">{group.passed} passed</span><span className={group.attention ? "font-semibold text-red-700" : "text-[#89838b]"}>{group.attention} attention</span><span className="text-[#777178]">{group.unassessed} unassessed</span>{group.direction ? <span className="ml-auto text-[#89838b]">{group.direction.replaceAll("_", " ")}</span> : null}</div>
  </button>;
}

function EvaluationState({ result }: { result: EvaluationResult }) { return <span className={`rounded-full px-2 py-1 text-[9px] font-semibold ${result.passed === true ? "bg-[#eef8f2] text-[#27754c]" : result.passed === false ? "bg-red-50 text-red-700" : "bg-stone-100 text-stone-600"}`}>{result.passed === true ? "Passed" : result.passed === false ? "Needs attention" : "Unassessed"}</span>; }
function evaluationValue(result: EvaluationResult) { return result.score != null ? formatNumber(result.score) : result.value != null ? String(result.value) : result.label || "Reported"; }
function evaluationSourceLabel(source?: string | null) { return source === "sdk" ? "SDK" : source ? source.replaceAll("_", " ") : "Reported"; }

function MetricToggle<T extends string>({ choices, active, onChange, labels }: { choices: readonly T[]; active: T; onChange: (choice: T) => void; labels: Record<T, string> }) { return <div className="flex flex-wrap justify-end gap-1">{choices.map((choice) => <button key={choice} type="button" onClick={() => onChange(choice)} className={`rounded-md px-2.5 py-1 text-[10px] font-medium ${active === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{labels[choice]}</button>)}</div>; }

function CompactFact({ label, value, tone = "default", active = false, onClick }: { label: string; value: string; tone?: "default" | "attention"; active?: boolean; onClick?: () => void }) {
  const content = <><div className="text-[8px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">{label}</div><div className={`mt-0.5 text-xs font-semibold ${tone === "attention" ? "text-[#b84040]" : "text-[#39343e]"}`}>{value}</div></>;
  const className = `rounded-md border px-2.5 py-2 text-left transition ${active ? "border-[#7658bd] bg-[#f6f2ff] ring-1 ring-[#7658bd]/20" : "border-[#e7e4e8] bg-[#fbfbf9] hover:border-[#cfc6ef] hover:bg-white"}`;
  return onClick ? <button type="button" onClick={onClick} aria-expanded={active} className={className}>{content}</button> : <div className={className}>{content}</div>;
}

type ExecutionSummaryView = "operations" | "types" | "failures" | "evaluations";

function ExecutionSummaryDetails({ view, replay, operationSummary, evaluationResults }: { view: ExecutionSummaryView; replay: WorkflowReplay; operationSummary?: OperationSummary; evaluationResults: EvaluationResult[] }) {
  const activeNodes = replay.nodes.filter((node) => node.state !== "inactive");
  const stateCounts = activeNodes.reduce((counts, node) => {
    const state = effectiveNodeState(node);
    counts[state] = (counts[state] || 0) + 1;
    return counts;
  }, {} as Record<string, number>);
  if (view === "operations") return <div className="grid gap-2 sm:grid-cols-4">
    {(["completed", "recovered", "failed", "inactive"] as const).map((state) => <div key={state} className="rounded-md border border-[#e8e5ea] bg-white px-3 py-2"><div className="text-[8px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">{state === "inactive" ? "Not used" : state}</div><div className="mt-1 text-sm font-semibold text-[#39343e]">{formatNumber(state === "inactive" ? replay.nodes.length - activeNodes.length : stateCounts[state] || 0)} steps</div></div>)}
  </div>;
  if (view === "types") return <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
    {(operationSummary?.types || []).map((item) => <div key={`${item.plane}-${item.type}`} className={`rounded-md border bg-white px-3 py-2 ${item.failed ? "border-red-200" : "border-[#e8e5ea]"}`}><div className="text-[10px] font-semibold text-[#3d3742]">{operationLabel(item.type)}</div><div className="mt-1 text-[9px] text-[#7b757d]">{formatNumber(item.operations)} operations{item.failed ? ` · ${formatNumber(item.failed)} failed` : ""}</div></div>)}
  </div>;
  if (view === "failures") {
    const failures = replay.nodes.flatMap((node) => nodeFailureRecords(node).map((record) => ({ node, record })));
    return failures.length ? <div className="space-y-2">{failures.map(({ node, record }, index) => <div key={`${node.id}-${String(record.id || index)}`} className="flex items-start justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-3 py-2"><div><div className="text-[10px] font-semibold text-red-800">{node.name}</div><div className="mt-0.5 text-[9px] text-red-700">{String(record.display_name || record.name || "Failed operation")}</div></div><span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-[8px] font-semibold uppercase text-red-700 ring-1 ring-red-200">{String(record.status || "failed")}</span></div>)}</div> : <div className="rounded-md border border-[#e8e5ea] bg-white px-3 py-3 text-xs text-[#6f6971]">No failed operations were observed in this execution.</div>;
  }
  return evaluationResults.length ? <div className="grid gap-2 sm:grid-cols-2">{evaluationResults.map((result) => <div key={result.evaluation_id} className="flex items-center justify-between gap-3 rounded-md border border-[#e8e5ea] bg-white px-3 py-2"><div className="min-w-0"><div className="truncate text-[10px] font-semibold text-[#3d3742]" title={result.name}>{result.name}</div><div className="mt-0.5 text-[9px] text-[#7b757d]">{evaluationValue(result)}</div></div><EvaluationState result={result} /></div>)}</div> : <div className="rounded-md border border-[#e8e5ea] bg-white px-3 py-3 text-xs text-[#6f6971]">No evaluations were reported for this execution.</div>;
}

const operationLabel = (value: string) => {
  if (value === "component") return "Workflow step";
  if (value === "unknown" || value === "x.witdem.unclassified") return "Other / Unknown";
  if (value === "ocr") return "OCR";
  return value.replace(/^x\.[^.]+\./, "").replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
};

export function WorkflowExecutionPage() {
  const { workflowId, executionId } = useParams({ from: "/workflows/$workflowId/executions/$executionId" });
  const [summaryView, setSummaryView] = useState<ExecutionSummaryView | null>(null);
  const q = useQuery({ queryKey: ["workflow-execution", workflowId, executionId], queryFn: () => api.workflowExecution(workflowId, executionId) });
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  const replay = q.data!.workflow_replay!;
  const operationSummary = q.data!.operation_summary;
  const evaluationResults = q.data!.evaluation_results || [];
  const toggleSummary = (view: ExecutionSummaryView) => setSummaryView((current) => current === view ? null : view);
  return <div className="-mb-[21px]">
    <PageHeader compact eyebrow="Workflow replay" title={replay.workflow.name} description={`Execution ${executionId}`} action={<Link to="/workflows/$workflowId" params={{ workflowId }}><Button variant="outline">Workflow executions</Button></Link>} />
    {(operationSummary || evaluationResults.length) ? <><div className="mb-2 grid grid-cols-4 gap-2"><CompactFact label="Operations" value={formatNumber(operationSummary?.total_operations)} active={summaryView === "operations"} onClick={() => toggleSummary("operations")} /><CompactFact label="Operation types" value={formatNumber(operationSummary?.types.length)} active={summaryView === "types"} onClick={() => toggleSummary("types")} /><CompactFact label="Operation failures" value={formatNumber(operationSummary?.failed_operations)} tone={operationSummary?.failed_operations ? "attention" : "default"} active={summaryView === "failures"} onClick={() => toggleSummary("failures")} /><CompactFact label="Evaluations" value={formatNumber(evaluationResults.length)} active={summaryView === "evaluations"} onClick={() => toggleSummary("evaluations")} /></div>{summaryView ? <div className="mb-3 rounded-lg border border-[#ded9e5] bg-[#faf9fc] p-3"><ExecutionSummaryDetails view={summaryView} replay={replay} operationSummary={operationSummary} evaluationResults={evaluationResults} /></div> : null}</> : null}
    <WorkflowReplayView replay={replay} evaluationResults={evaluationResults} />
  </div>;
}

function cardMetrics(node: ProjectedWorkflowNode): CardMetric[] {
  const model = node.models[0];
  const provider = node.providers[0];
  return [
    { label: "Elapsed", value: seconds(node.duration_seconds) },
    { label: "Attempts", value: formatNumber(node.attempts) },
    { label: "Model", value: model ? `${model}${provider ? ` · ${provider}` : ""}` : "No model call" },
    { label: node.total_tokens != null ? "Tokens" : "Cost", value: node.total_tokens != null ? formatNumber(node.total_tokens) : money(node.known_cost) },
  ];
}

export function summarizeWorkflowRuns(runs: Array<Record<string, unknown>>) {
  const assessed = runs.filter((run) => typeof run.product_goal_achieved === "boolean");
  const decisions = runs.filter((run) => typeof run.decision_correct === "boolean");
  const durations = runs.map((run) => run.duration_seconds).filter((value): value is number => typeof value === "number").sort((left, right) => left - right);
  const middle = Math.floor(durations.length / 2);
  const medianDuration = durations.length
    ? durations.length % 2 ? durations[middle] : (durations[middle - 1] + durations[middle]) / 2
    : null;
  const runtimes = new Map<string, number>();
  runs.forEach((run) => {
    const runtime = String(run.runtime_id || run.adapter_name || "Unknown");
    runtimes.set(runtime, (runtimes.get(runtime) || 0) + 1);
  });
  const goalAchieved = assessed.filter((run) => run.product_goal_achieved === true).length;
  const correctDecisions = decisions.filter((run) => run.decision_correct === true).length;
  return {
    runs: runs.length,
    assessedRuns: assessed.length,
    goalAchieved,
    goalRate: assessed.length ? goalAchieved / assessed.length : 0,
    decisionsReported: decisions.length,
    correctDecisions,
    decisionRate: decisions.length ? correctDecisions / decisions.length : 0,
    medianDuration,
    retryRuns: runs.filter((run) => Number(run.workflow_retry_attempts || 0) > 0).length,
    retryAttempts: runs.reduce((total, run) => total + Number(run.workflow_retry_attempts || 0), 0),
    runtimeCounts: [...runtimes.entries()].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0])),
  };
}

function AnalysisCard({ title, note, children, className = "" }: React.PropsWithChildren<{ title: string; note?: string; className?: string }>) {
  return <section className={`min-w-0 rounded-lg border border-[#e5e2e8] bg-[#fbfbf9] p-3 ${className}`}>
    <div className="mb-2"><h3 className="text-[11px] font-semibold leading-4 text-[#39343e]">{title}</h3>{note ? <p className="mt-0.5 text-[9px] leading-3 text-[#817b83]">{note}</p> : null}</div>
    {children}
  </section>;
}

function RingMetric({ value, valueLabel, label, detail }: { value: number; valueLabel: string; label: string; detail: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  const circumference = 2 * Math.PI * 42;
  return <div className="flex items-center gap-4">
    <svg viewBox="0 0 112 112" className="size-28 shrink-0" role="img" aria-label={`${label}: ${valueLabel}`}>
      <circle cx="56" cy="56" r="42" fill="none" stroke="#ebe8ee" strokeWidth="10" />
      <circle cx="56" cy="56" r="42" fill="none" stroke="#7153b5" strokeWidth="10" strokeLinecap="round" strokeDasharray={`${circumference * clamped} ${circumference}`} transform="rotate(-90 56 56)" />
      <text x="56" y="53" textAnchor="middle" className="fill-[#342f39] text-[19px] font-semibold">{valueLabel}</text>
      <text x="56" y="69" textAnchor="middle" className="fill-[#8a848c] text-[8px] uppercase tracking-wider">{label}</text>
    </svg>
    <p className="text-xs leading-5 text-[#706a72]">{detail}</p>
  </div>;
}

function StepStateMatrix({ nodes, onSelect, completedIsSupporting = false }: { nodes: ProjectedWorkflowNode[]; onSelect?: (node: ProjectedWorkflowNode) => void; completedIsSupporting?: boolean }) {
  const [visibleState, setVisibleState] = useState<ProjectedWorkflowNode["state"] | "all">("all");
  const counts = {
    completed: nodes.filter((node) => node.state === "completed").length,
    recovered: nodes.filter((node) => node.state === "recovered").length,
    failed: nodes.filter((node) => node.state === "failed").length,
    inactive: nodes.filter((node) => node.state === "inactive").length,
  };
  const colors: Record<string, string> = { completed: completedIsSupporting ? "bg-[#8fcfab]" : "bg-[#16864b]", recovered: "bg-[#d58b24]", failed: "bg-[#dc5a5a]", inactive: "bg-[#e2e0da]" };
  return <>
    <div className="grid max-w-[292px] grid-cols-[repeat(14,minmax(0,1fr))] gap-1" aria-label={`${counts.completed} completed, ${counts.recovered} recovered, ${counts.failed} failed, ${counts.inactive} not used`}>
      {nodes.map((node) => <button type="button" key={node.id} onClick={() => node.state !== "inactive" && onSelect?.(node)} title={`${node.name}: ${statePresentation(node.state).label}${node.state !== "inactive" ? " · select to inspect" : ""}`} className={`aspect-square rounded-[3px] transition ${colors[node.state] || colors.inactive} ${visibleState !== "all" && visibleState !== node.state ? "opacity-15" : "hover:scale-125 hover:ring-2 hover:ring-[#7153b5]/30"}`} />)}
    </div>
    <div className="mt-2 flex flex-wrap gap-1 text-[8px] text-[#777178]">
      {(["all", "completed", "recovered", "failed", "inactive"] as const).map((state) => <button type="button" key={state} onClick={() => setVisibleState(state)} className={`rounded-full border px-1.5 py-0.5 capitalize ${visibleState === state ? "border-[#7153b5] bg-[#f2edff] text-[#5839a6]" : "border-[#e2dfe5] bg-white"}`}>{state === "all" ? `${nodes.length} all` : `${counts[state]} ${state === "inactive" ? "unused" : state}`}</button>)}
    </div>
  </>;
}

function StageDurationLollipop({ stages }: { stages: WorkflowReplay["stages"] }) {
  const maximum = Math.max(...stages.map((stage) => stage.duration_seconds || 0), 0.000001);
  return <div className="space-y-2.5" role="img" aria-label="Elapsed time by workflow stage">
    {stages.map((stage) => {
      const duration = stage.duration_seconds || 0;
      const width = duration ? Math.max(5, duration / maximum * 100) : 0;
      return <div key={stage.id} className="grid grid-cols-[128px_minmax(0,1fr)_52px] items-center gap-3">
        <span className="truncate text-[10px] font-medium text-[#5d5760]" title={stage.name}>{stage.name}</span>
        <span className="relative h-px bg-[#ddd9df]"><span className="absolute left-0 top-0 h-px bg-[#866bc2]" style={{ width: `${width}%` }} /><span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-[#7153b5] shadow-sm" style={{ left: `${width}%`, display: duration ? "block" : "none" }} /></span>
        <span className="text-right text-[9px] font-semibold text-[#716a73]">{duration ? seconds(duration) : "Not used"}</span>
      </div>;
    })}
  </div>;
}

function LatencyTrend({ runs }: { runs: Run[] }) {
  const ordered = [...runs].filter((run) => typeof run.duration_seconds === "number").sort((left, right) => String(left.started_at || "").localeCompare(String(right.started_at || "")));
  if (!ordered.length) return <Empty>No latency measurements were reported.</Empty>;
  const values = ordered.map((run) => Number(run.duration_seconds));
  const low = Math.min(...values);
  const high = Math.max(...values);
  const range = high - low || 1;
  const points = ordered.map((run, index) => ({ x: 34 + (ordered.length === 1 ? 156 : index / (ordered.length - 1) * 312), y: 130 - (Number(run.duration_seconds) - low) / range * 92, run }));
  return <svg viewBox="0 0 380 165" className="h-44 w-full" role="img" aria-label="Execution latency trend over time">
    <line x1="34" y1="130" x2="356" y2="130" stroke="#d9d6dc" /><line x1="34" y1="30" x2="34" y2="130" stroke="#d9d6dc" />
    <polyline points={points.map((point) => `${point.x},${point.y}`).join(" ")} fill="none" stroke="#7255b5" strokeWidth="2.5" strokeLinejoin="round" />
    {points.map((point) => <g key={point.run.execution_id}><circle cx={point.x} cy={point.y} r="5" fill="#fff" stroke="#7255b5" strokeWidth="2.5"><title>{`${formatDateTime(point.run.started_at)} · ${seconds(point.run.duration_seconds)}`}</title></circle></g>)}
    <text x="34" y="148" fontSize="9" fill="#827c84">{formatBrowserDate(ordered[0].started_at)}</text>
    <text x="356" y="148" fontSize="9" textAnchor="end" fill="#827c84">{formatBrowserDate(ordered.at(-1)?.started_at)}</text>
    <text x="28" y="34" fontSize="9" textAnchor="end" fill="#827c84">{seconds(high)}</text><text x="28" y="132" fontSize="9" textAnchor="end" fill="#827c84">{seconds(low)}</text>
  </svg>;
}

function RetryScatter({ runs }: { runs: Run[] }) {
  const points = runs.filter((run) => typeof run.duration_seconds === "number");
  if (!points.length) return <Empty>No execution measurements were reported.</Empty>;
  const maxDuration = Math.max(...points.map((run) => Number(run.duration_seconds)), 0.001);
  const maxRetries = Math.max(...points.map((run) => Number(run.workflow_retry_attempts || 0)), 1);
  return <svg viewBox="0 0 300 165" className="h-44 w-full" role="img" aria-label="Retries plotted against execution latency">
    <line x1="36" y1="130" x2="278" y2="130" stroke="#d9d6dc" /><line x1="36" y1="24" x2="36" y2="130" stroke="#d9d6dc" />
    {points.map((run) => {
      const retries = Number(run.workflow_retry_attempts || 0);
      const x = 42 + Number(run.duration_seconds) / maxDuration * 228;
      const y = 124 - retries / maxRetries * 92;
      return <circle key={run.execution_id} cx={x} cy={y} r={6 + Math.min(4, retries)} fill={retries ? "#d58b24" : "#8068b7"} opacity="0.82"><title>{`${formatDateTime(run.started_at)} · ${seconds(run.duration_seconds)} · ${retries} retries`}</title></circle>;
    })}
    <text x="157" y="154" textAnchor="middle" fontSize="9" fill="#827c84">Elapsed time →</text><text x="12" y="80" transform="rotate(-90 12 80)" textAnchor="middle" fontSize="9" fill="#827c84">Retries →</text>
  </svg>;
}

function OutcomeConstellation({ runs }: { runs: Run[] }) {
  const counts = new Map<string, number>();
  runs.forEach((run) => { const key = String(run.application_outcome || run.runtime_outcome || run.status || "Not reported"); counts.set(key, (counts.get(key) || 0) + 1); });
  const items = [...counts.entries()].sort((left, right) => right[1] - left[1]);
  const maximum = Math.max(...items.map((item) => item[1]), 1);
  return <div className="flex min-h-36 flex-wrap items-center justify-center gap-4" role="img" aria-label={items.map(([name, count]) => `${name}: ${count}`).join(", ")}>
    {items.map(([name, count], index) => { const size = 52 + count / maximum * 32; return <div key={name} className="grid place-items-center rounded-full border text-center" style={{ width: size, height: size, borderColor: index ? "#d7d2dc" : "#8066bd", background: index ? "#faf9f7" : "#f4f0ff" }}><div><div className="text-lg font-semibold text-[#443650]">{count}</div><div className="max-w-16 text-[8px] leading-3 text-[#726b74]">{name.replaceAll("_", " ")}</div></div></div>; })}
  </div>;
}

function WorkflowGoalAnalysis({ executions, stats }: { executions: Run[]; stats: ReturnType<typeof summarizeWorkflowRuns> }) {
  const outcomes = executions.reduce<Record<string, number>>((result, run) => { const key = String(run.application_outcome || run.runtime_outcome || run.status || "Not reported"); result[key] = (result[key] || 0) + 1; return result; }, {});
  return <Panel className="mt-4" title="Goal analysis" note="Whether this workflow produces the intended product result, and which outcomes it reaches.">
    <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
      <AnalysisCard title="Goal attainment" note={`${stats.assessedRuns} of ${stats.runs} executions reported a goal result.`}><RatioDonutChart value={stats.goalRate} achievedLabel="Achieved" remainderLabel="Not achieved" detail={stats.assessedRuns ? `${stats.goalAchieved} of ${stats.assessedRuns} assessed executions` : "No goal assessment was reported"} /></AnalysisCard>
      <AnalysisCard title="Observed outcomes" note="Hover for counts; use the legend to isolate a final disposition."><RuntimeDonutChart height={190} data={outcomes} colors={{ approved: "#25a86b", rejected: "#dc5a5a", review_required: "#d58b24", completed: "#77727b", failed: "#dc5a5a" }} /></AnalysisCard>
    </div>
  </Panel>;
}

function WorkflowExecutionAnalysis({ executions, stats, overview, workflowId }: { executions: Run[]; stats: ReturnType<typeof summarizeWorkflowRuns>; overview: Awaited<ReturnType<typeof api.overview>>; workflowId: string }) {
  const runHref = (filter: { model?: string; provider?: string }) => workflowRunsHref(workflowId, filter);
  return <Panel className="mt-4" title="Execution analysis" note="Run-to-run latency and the relationship between retries and elapsed time.">
    <div className="grid gap-4 lg:grid-cols-[1.25fr_.75fr]">
      <AnalysisCard title="Latency over time" note={`Median ${seconds(stats.medianDuration)} · ${stats.runs} executions`}><ExecutionTrendChart runs={executions} /></AnalysisCard>
      <AnalysisCard title="Retry pressure" note={`${stats.retryRuns} runs retried · ${stats.retryAttempts} extra attempts`}><RetryPressureChart runs={executions} /></AnalysisCard>
    </div>
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <AnalysisCard title="By model" note="Outcomes, measured cost, and tokens. Select a bar to open matching executions."><AttributionHealthChart dimension="model" items={overview.models} onSelect={(item) => window.location.assign(runHref({ model: item.label }))} /></AnalysisCard>
      <AnalysisCard title="By provider" note="The same telemetry contract grouped by provider."><AttributionHealthChart dimension="provider" items={overview.providers} onSelect={(item) => window.location.assign(runHref({ provider: item.label }))} /></AnalysisCard>
    </div>
    <AnalysisCard className="mt-4" title="By step" note="Switch between elapsed time, failures, retries, measured cost, and tokens; hover for the complete step record."><StageDiagnosticsChart items={overview.stages} /></AnalysisCard>
  </Panel>;
}

function WorkflowAtAGlance({ executions, stats, overview, workflowId }: { executions: Run[]; stats: ReturnType<typeof summarizeWorkflowRuns>; overview: Pick<Awaited<ReturnType<typeof api.overview>>, "models" | "providers" | "stages">; workflowId: string }) {
  const outcomes = executions.reduce<Record<string, number>>((result, run) => { const key = String(run.application_outcome || run.runtime_outcome || run.status || "Not reported"); result[key] = (result[key] || 0) + 1; return result; }, {});
  const outcomeColors = contractOutcomeColors(outcomes, []);
  const hasProductSuccess = executions.some((run) => run.product_goal_achieved === true)
    || Object.entries(outcomeColors).some(([name, color]) => name !== "completed" && color === "#16864b" && Boolean(outcomes[name]));
  const runHref = (filter: { model?: string; provider?: string }) => workflowRunsHref(workflowId, filter);
  return <Panel className="mt-3" title="Workflow at a glance" note={`${stats.runs} matched executions · goal, runtime, and attribution signals specific to this workflow`}>
    <div className="grid gap-2 xl:grid-cols-4">
      <AnalysisCard title="Goal attainment" note={`${stats.assessedRuns}/${stats.runs} assessed`}><RatioDonutChart height={138} value={stats.goalRate} achievedLabel="Achieved" remainderLabel="Not achieved" detail={`${stats.goalAchieved} of ${stats.assessedRuns} assessed executions`} /></AnalysisCard>
      <AnalysisCard title="Outcome mix" note="Legend filters dispositions"><RuntimeDonutChart height={138} data={outcomes} colors={outcomeColors} /></AnalysisCard>
      <AnalysisCard title="Latency" note={`Median ${seconds(stats.medianDuration)}`}><ExecutionTrendChart height={138} runs={executions} /></AnalysisCard>
      <AnalysisCard title="Retry pressure" note={`${stats.retryRuns} runs · ${stats.retryAttempts} extra attempts`}><RetryPressureChart height={138} runs={executions} /></AnalysisCard>
    </div>
    <div className="mt-2 grid gap-2 xl:grid-cols-[.8fr_.8fr_1.4fr]">
      <AnalysisCard title="By model" note="Outcomes, cost, tokens"><AttributionHealthChart height={196} dimension="model" items={overview.models} completedIsSupporting={hasProductSuccess} onSelect={(item) => window.location.assign(runHref({ model: item.label }))} /></AnalysisCard>
      <AnalysisCard title="By provider" note="Same contract by provider"><AttributionHealthChart height={196} dimension="provider" items={overview.providers} completedIsSupporting={hasProductSuccess} onSelect={(item) => window.location.assign(runHref({ provider: item.label }))} /></AnalysisCard>
      <AnalysisCard title="By step" note="Elapsed, failure, retry, cost, or tokens"><StageDiagnosticsChart height={196} items={overview.stages} /></AnalysisCard>
    </div>
  </Panel>;
}

function WorkflowCanvas({ replay, onSelect, firstScreen = false, controlsHost = null }: { replay: WorkflowReplay; onSelect?: (node: ProjectedWorkflowNode) => void; firstScreen?: boolean; controlsHost?: HTMLElement | null }) {
  const viewport = useRef<HTMLDivElement>(null);
  const pan = useRef<{ pointerId: number; clientX: number; clientY: number; scrollLeft: number; scrollTop: number } | null>(null);
  const [zoom, setZoom] = useState(0.9);
  const [isPanning, setIsPanning] = useState(false);
  const declaredOnly = replay.nodes.length === 0;
  const nodes = useMemo<ProjectedWorkflowNode[]>(() => declaredOnly
    ? replay.workflow.nodes.map((node) => ({
      ...node,
      state: "inactive",
      attempts: 0,
      duration_seconds: null,
      known_cost: null,
      total_tokens: null,
      providers: [],
      models: [],
      observations: [],
      model_calls: [],
    }))
    : replay.nodes, [declaredOnly, replay.nodes, replay.workflow.nodes]);
  const layout = useMemo(() => workflowLayout(nodes, replay.transitions), [nodes, replay.transitions]);
  const geometryIssues = useMemo(() => validateWorkflowGeometry(layout, nodes, replay.transitions), [layout, nodes, replay.transitions]);
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const focusNode = (nodeId: string, behavior: ScrollBehavior = "smooth", targetZoom = zoom) => {
    const element = viewport.current;
    const position = layout.positions.get(nodeId);
    if (!element || !position) return;
    element.scrollTo({
      left: PAN_PADDING + position.x * targetZoom - 28,
      top: PAN_PADDING + position.y * targetZoom - element.clientHeight / 2 + FLOW_NODE_HEIGHT * targetZoom / 2,
      behavior,
    });
  };
  const fit = (behavior: ScrollBehavior = "smooth") => {
    const element = viewport.current;
    const bounds = element?.getBoundingClientRect();
    if (!element || !bounds) return;
    const target = workflowFitZoom(layout.width, layout.height, bounds.width, bounds.height);
    setZoom(target);
    window.requestAnimationFrame(() => element.scrollTo({
      left: PAN_PADDING - (element.clientWidth - layout.width * target) / 2,
      top: PAN_PADDING - (element.clientHeight - layout.height * target) / 2,
      behavior,
    }));
  };
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    let frame = 0;
    const refit = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => fit("auto"));
    };
    const observer = new ResizeObserver(refit);
    observer.observe(element);
    refit();
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [replay.workflow.id, declaredOnly, layout.width, layout.height]);
  const zoomTo = (next: number) => {
    const element = viewport.current;
    const target = Math.max(0.05, Math.min(2.5, next));
    if (!element) { setZoom(target); return; }
    const centerX = (element.scrollLeft + element.clientWidth / 2 - PAN_PADDING) / zoom;
    const centerY = (element.scrollTop + element.clientHeight / 2 - PAN_PADDING) / zoom;
    setZoom(target);
    window.requestAnimationFrame(() => element.scrollTo({
      left: PAN_PADDING + centerX * target - element.clientWidth / 2,
      top: PAN_PADDING + centerY * target - element.clientHeight / 2,
      behavior: "auto",
    }));
  };
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const handleTrackpadZoom = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      event.stopPropagation();
      zoomTo(trackpadZoomTarget(zoom, event.deltaY, event.deltaMode));
    };
    element.addEventListener("wheel", handleTrackpadZoom, { passive: false });
    return () => element.removeEventListener("wheel", handleTrackpadZoom);
  }, [zoom]);
  const focusStart = () => {
    setZoom(0.9);
    window.requestAnimationFrame(() => { if (nodes[0]) focusNode(nodes[0].id, "smooth", 0.9); });
  };
  const zoomControls = <div className="flex items-center gap-1 rounded-lg border border-[#ddd9e2] bg-white p-1">
    <span className="hidden px-2 text-[9px] font-medium text-[#8a838d] lg:inline">Drag canvas to pan</span>
    <button type="button" aria-label="Zoom out" onClick={() => zoomTo(zoom - 0.15)} className="rounded px-2 py-1 text-sm font-semibold text-[#5d5662] hover:bg-[#f2eff5]">−</button>
    <input aria-label="Flowchart zoom" type="range" min="5" max="250" step="5" value={Math.round(zoom * 100)} onChange={(event) => zoomTo(Number(event.target.value) / 100)} className="w-24 accent-[#7052b4]" />
    <span className="min-w-12 text-center text-[10px] font-semibold text-[#777079]">{Math.round(zoom * 100)}%</span>
    <button type="button" aria-label="Zoom in" onClick={() => zoomTo(zoom + 0.15)} className="rounded px-2 py-1 text-sm font-semibold text-[#5d5662] hover:bg-[#f2eff5]">+</button>
    <button type="button" onClick={focusStart} className="rounded px-2 py-1 text-[10px] font-semibold text-[#5f43a2] hover:bg-[#f2edff]">Start</button>
    <button type="button" onClick={() => fit()} className="rounded px-2 py-1 text-[10px] font-semibold text-[#5d5662] hover:bg-[#f2eff5]">Overview</button>
  </div>;

  return <>{controlsHost ? createPortal(zoomControls, controlsHost) : null}<div className={`${firstScreen ? "h-[clamp(280px,calc(100vh-387px),560px)]" : "h-[clamp(320px,calc(100vh-360px),620px)]"} flex flex-col overflow-hidden rounded-2xl border border-[#dfdce5] bg-[#f8f7f4]`}>
    <span className="sr-only" role="status">{geometryIssues.length ? `${geometryIssues.length} flowchart geometry issues: ${geometryIssues.join(", ")}` : "Flowchart geometry valid"}</span>
    <div className="flex shrink-0 items-center gap-3 border-b border-[#dedbd6] bg-white px-3 py-2">
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto [scrollbar-width:none]">
        {replay.stages.map((stage, index) => {
          const active = stage.nodes.filter((id) => nodeById.get(id)?.state !== "inactive").length;
          return <button key={stage.id} type="button" onClick={() => {
            const first = stage.nodes.find((id) => layout.positions.has(id));
            if (first) {
              setZoom(0.9);
              window.requestAnimationFrame(() => focusNode(first, "smooth", 0.9));
            }
          }} className="shrink-0 whitespace-nowrap rounded-full border border-[#ddd7e5] bg-[#faf8fd] px-3 py-1.5 text-[10px] font-semibold text-[#5f4b8c] hover:border-[#8e75c8] hover:bg-[#f3edff]">
            {index + 1}. {stage.name} <span className="text-[#99919f]">· {declaredOnly ? stage.nodes.length : `${active}/${stage.nodes.length}`}</span>
          </button>;
        })}
      </div>
    </div>
    <div ref={viewport} role="region" aria-label="Workflow canvas" onPointerDown={(event) => {
      if (event.button !== 0 || (event.target as HTMLElement).closest("button, input, a")) return;
      const element = event.currentTarget;
      pan.current = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        scrollLeft: element.scrollLeft,
        scrollTop: element.scrollTop,
      };
      element.setPointerCapture(event.pointerId);
      setIsPanning(true);
      event.preventDefault();
    }} onPointerMove={(event) => {
      const origin = pan.current;
      if (!origin || origin.pointerId !== event.pointerId) return;
      event.currentTarget.scrollTo({
        left: origin.scrollLeft - (event.clientX - origin.clientX),
        top: origin.scrollTop - (event.clientY - origin.clientY),
        behavior: "auto",
      });
    }} onPointerUp={(event) => {
      if (pan.current?.pointerId !== event.pointerId) return;
      pan.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      setIsPanning(false);
    }} onPointerCancel={(event) => {
      if (pan.current?.pointerId !== event.pointerId) return;
      pan.current = null;
      setIsPanning(false);
    }} onLostPointerCapture={(event) => {
      if (pan.current?.pointerId !== event.pointerId) return;
      pan.current = null;
      setIsPanning(false);
    }} onDoubleClick={() => zoomTo(zoom + 0.2)} className={`min-h-0 flex-1 touch-none select-none overflow-auto bg-[#111b36] bg-[radial-gradient(#53617c_0.8px,transparent_0.8px)] [background-size:22px_22px] [scrollbar-color:#7180a0_transparent] ${isPanning ? "cursor-grabbing" : "cursor-grab"}`}>
      <div className="relative" style={{ width: layout.width * zoom + PAN_PADDING * 2, height: layout.height * zoom + PAN_PADDING * 2 }}>
        <div className="absolute origin-top-left" style={{ left: PAN_PADDING, top: PAN_PADDING, width: layout.width, height: layout.height, transform: `scale(${zoom})` }}>
          <svg className="pointer-events-none absolute inset-0 z-0" width={layout.width} height={layout.height} aria-hidden="true">
            <defs><marker id="workflow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#c1cbe0" /></marker></defs>
            {replay.transitions.map((transition, index) => {
              const edge = layout.edges[index];
              if (!edge?.points.length) return null;
              const attention = transition.type === "fallback" || transition.type === "loop";
              return <path key={`${transition.from}-${transition.to}-${index}`} d={polylinePath(edge.points)} fill="none" stroke={attention ? "#f2b85b" : transition.type === "branch" ? "#b6a2f2" : "#91a0bd"} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" strokeDasharray={transition.type === "loop" ? "7 5" : undefined} markerEnd="url(#workflow-arrow)" />;
            })}
          </svg>
          {replay.transitions.map((transition, index) => {
            const edge = layout.edges[index];
            const label = transition.label || transition.route || (transition.type !== "next" ? transition.type : null);
            if (!edge?.label || !label) return null;
            return <span key={`label-${transition.from}-${transition.to}-${index}`} className="pointer-events-none absolute z-[1] max-w-36 -translate-x-1/2 -translate-y-1/2 rounded-md border border-[#52617f] bg-[#17233f] px-2 py-1 text-center text-[9px] font-semibold text-[#eef2fa] shadow-sm" style={{ left: edge.label.x, top: edge.label.y }}>{label}</span>;
          })}
          {nodes.map((node) => {
            const position = layout.positions.get(node.id)!;
            return <FlowNodeCard key={node.id} node={node} declared={declaredOnly} position={position} onSelect={!declaredOnly && node.state !== "inactive" && onSelect ? () => onSelect(node) : undefined} />;
          })}
        </div>
      </div>
    </div>
  </div></>;
}

const FLOW_NODE_WIDTH = 280;
const FLOW_NODE_HEIGHT = 218;
const PAN_PADDING = 1000;
const FIT_MARGIN = 32;

export function workflowFitZoom(graphWidth: number, graphHeight: number, viewportWidth: number, viewportHeight: number) {
  if (graphWidth <= 0 || graphHeight <= 0 || viewportWidth <= 0 || viewportHeight <= 0) return 1;
  return Math.max(0.05, Math.min(1, (viewportWidth - FIT_MARGIN) / graphWidth, (viewportHeight - FIT_MARGIN) / graphHeight));
}

export function trackpadZoomTarget(currentZoom: number, deltaY: number, deltaMode = 0) {
  const modeScale = deltaMode === 1 ? 16 : deltaMode === 2 ? 200 : 1;
  const normalizedDelta = Math.max(-25, Math.min(25, deltaY * modeScale));
  return currentZoom * Math.exp(-normalizedDelta * 0.006);
}
type FlowTransition = { from: string; to: string; label?: string | null; route?: string | null; type?: string | null };
type FlowLayoutEdge = { points: Point[]; label: { x: number; y: number; width: number; height: number } | null };

export function workflowLayout(nodes: Array<{ id: string }>, transitions: FlowTransition[]) {
  const graph = new DagreGraph({ multigraph: true });
  graph.setGraph({
    rankdir: "LR",
    ranker: "network-simplex",
    acyclicer: "greedy",
    ranksep: 116,
    nodesep: 52,
    edgesep: 28,
    marginx: 44,
    marginy: 44,
  });
  graph.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((node) => graph.setNode(node.id, { width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT }));
  transitions.forEach((transition, index) => {
    const label = transition.label || transition.route || (transition.type !== "next" ? transition.type : "");
    graph.setEdge(transition.from, transition.to, {
      width: label ? Math.min(144, Math.max(48, label.length * 6 + 18)) : 0,
      height: label ? 24 : 0,
      labelpos: "c",
      labeloffset: 12,
      minlen: 1,
      weight: transition.type === "loop" || transition.type === "fallback" ? 1 : 3,
    }, String(index));
  });
  runDagreLayout(graph);
  const positions = new Map(nodes.map((node) => {
    const placed = graph.node(node.id);
    return [node.id, { x: placed.x - FLOW_NODE_WIDTH / 2, y: placed.y - FLOW_NODE_HEIGHT / 2 }];
  }));
  const edges: FlowLayoutEdge[] = transitions.map((transition, index) => {
    const placed = graph.edge({ v: transition.from, w: transition.to, name: String(index) });
    return {
      points: placed?.points || [],
      label: placed && typeof placed.x === "number" && typeof placed.y === "number" && placed.width && placed.height
        ? { x: placed.x, y: placed.y, width: placed.width, height: placed.height }
        : null,
    };
  });
  const bounds = graph.graph();
  const baseHeight = Math.max(560, Number(bounds.height || 560));
  const rectangles = new Map(nodes.map((node) => {
    const position = positions.get(node.id)!;
    return [node.id, { left: position.x, top: position.y, right: position.x + FLOW_NODE_WIDTH, bottom: position.y + FLOW_NODE_HEIGHT }];
  }));
  let returnRails = 0;
  edges.forEach((edge, index) => {
    const transition = transitions[index];
    const crossesNode = nodes.some((node) => {
      if (node.id === transition.from || node.id === transition.to) return false;
      for (let point = 1; point < edge.points.length; point += 1) {
        if (segmentIntersectsRect(edge.points[point - 1], edge.points[point], insetRect(rectangles.get(node.id)!, 2))) return true;
      }
      return false;
    });
    if (!crossesNode) return;
    const source = rectangles.get(transition.from)!;
    const target = rectangles.get(transition.to)!;
    const railY = baseHeight + 54 + returnRails * 42;
    const sourceY = (source.top + source.bottom) / 2;
    const targetY = (target.top + target.bottom) / 2;
    const sourceRailX = source.right + 32;
    const targetRailX = target.left - 32;
    edge.points = [
      { x: source.right, y: sourceY },
      { x: sourceRailX, y: sourceY },
      { x: sourceRailX, y: railY },
      { x: targetRailX, y: railY },
      { x: targetRailX, y: targetY },
      { x: target.left, y: targetY },
    ];
    if (edge.label) edge.label = { ...edge.label, x: (sourceRailX + targetRailX) / 2, y: railY };
    returnRails += 1;
  });
  return {
    positions,
    edges,
    width: Math.max(900, Number(bounds.width || 900)),
    height: baseHeight + (returnRails ? 96 + (returnRails - 1) * 42 : 0),
  };
}

function polylinePath(points: Point[]) {
  return points.length ? `M ${points.map((point) => `${point.x} ${point.y}`).join(" L ")}` : "";
}

type FlowLayout = ReturnType<typeof workflowLayout>;
type Rect = { left: number; top: number; right: number; bottom: number };

export function validateWorkflowGeometry(layout: FlowLayout, nodes: Array<{ id: string }>, transitions: FlowTransition[]) {
  const issues: string[] = [];
  const rectangles = new Map(nodes.map((node) => {
    const position = layout.positions.get(node.id)!;
    return [node.id, {
      left: position.x,
      top: position.y,
      right: position.x + FLOW_NODE_WIDTH,
      bottom: position.y + FLOW_NODE_HEIGHT,
    }];
  }));
  nodes.forEach((node, index) => {
    const rectangle = rectangles.get(node.id)!;
    nodes.slice(index + 1).forEach((other) => {
      if (rectanglesOverlap(rectangle, rectangles.get(other.id)!)) issues.push(`nodes:${node.id}:${other.id}`);
    });
  });
  transitions.forEach((transition, index) => {
    const edge = layout.edges[index];
    if (!edge) return;
    nodes.filter((node) => node.id !== transition.from && node.id !== transition.to).forEach((node) => {
      const rectangle = insetRect(rectangles.get(node.id)!, 2);
      for (let point = 1; point < edge.points.length; point += 1) {
        if (segmentIntersectsRect(edge.points[point - 1], edge.points[point], rectangle)) {
          issues.push(`edge:${transition.from}:${transition.to}:${node.id}`);
          break;
        }
      }
    });
    if (edge.label) {
      const labelRect = {
        left: edge.label.x - edge.label.width / 2,
        top: edge.label.y - edge.label.height / 2,
        right: edge.label.x + edge.label.width / 2,
        bottom: edge.label.y + edge.label.height / 2,
      };
      nodes.forEach((node) => {
        if (rectanglesOverlap(labelRect, rectangles.get(node.id)!)) issues.push(`label:${transition.from}:${transition.to}:${node.id}`);
      });
    }
  });
  return [...new Set(issues)];
}

function rectanglesOverlap(left: Rect, right: Rect) {
  return left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top;
}

function insetRect(rectangle: Rect, amount: number): Rect {
  return { left: rectangle.left + amount, top: rectangle.top + amount, right: rectangle.right - amount, bottom: rectangle.bottom - amount };
}

function segmentIntersectsRect(start: Point, end: Point, rectangle: Rect) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const p = [-dx, dx, -dy, dy];
  const q = [start.x - rectangle.left, rectangle.right - start.x, start.y - rectangle.top, rectangle.bottom - start.y];
  let lower = 0;
  let upper = 1;
  for (let index = 0; index < 4; index += 1) {
    if (p[index] === 0) {
      if (q[index] < 0) return false;
      continue;
    }
    const ratio = q[index] / p[index];
    if (p[index] < 0) lower = Math.max(lower, ratio);
    else upper = Math.min(upper, ratio);
    if (lower > upper) return false;
  }
  return true;
}

function FlowNodeCard({ node, declared, position, onSelect }: {
  node: ProjectedWorkflowNode;
  declared: boolean;
  position: { x: number; y: number };
  onSelect?: () => void;
}) {
  const failures = declared ? [] : nodeFailureRecords(node);
  const visibleState = declared ? "declared" : effectiveNodeState(node);
  const presentation = statePresentation(visibleState);
  return <article className={`absolute z-[2] flex flex-col rounded-xl border-2 bg-white p-4 shadow-[0_7px_20px_rgba(47,39,59,.08)] ${node.state === "inactive" && !declared ? "opacity-50" : ""}`} style={{ left: position.x, top: position.y, width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT, borderColor: presentation.border }}>
    <span className="absolute -left-[6px] top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-[#111b36] bg-[#a89bd0]" />
    <span className="absolute -right-[6px] top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-[#111b36] bg-[#a89bd0]" />
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[9px] font-semibold uppercase tracking-[0.13em] text-[#817b84]">{node.kind || "Workflow step"}</div>
        <h4 className="mt-1 text-sm font-semibold leading-5 text-[#342f39]">{node.name}</h4>
      </div>
      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-semibold ring-1 ring-inset ${presentation.badge}`}>{presentation.label}</span>
    </div>
    {failures.length ? <div className="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-[9px] font-semibold leading-3 text-red-700">{failures.length} failed operation{failures.length === 1 ? "" : "s"} · {String(failures[0].display_name || failures[0].name || "inspect evidence")}</div> : node.description ? <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-[#777178]">{node.description}</p> : null}
    {!declared ? <div className="mt-auto grid grid-cols-2 gap-x-3 gap-y-2 border-t border-[#eeeaf0] pt-3">
      {cardMetrics(node).map((metric) => <div key={metric.label} className="min-w-0"><div className="text-[8px] font-semibold uppercase tracking-[0.1em] text-[#a09aa2]">{metric.label}</div><div className="mt-0.5 truncate text-[10px] font-medium text-[#514b53]" title={metric.value}>{metric.value}</div></div>)}
    </div> : null}
    {onSelect ? <button type="button" onClick={onSelect} aria-label={`Inspect graph for ${node.name}`} className="mt-3 flex w-full items-center justify-between rounded-lg border border-[#c4b5e6] bg-white px-3 py-2 text-[10px] font-semibold text-[#5839a6] transition hover:border-[#7455bd] hover:bg-[#f7f3ff] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7455bd]">
      <span>Inspect evidence</span><span aria-hidden="true">↗</span>
    </button> : null}
  </article>;
}

type EvidenceRecord = Record<string, unknown> & {
  id?: string;
  parent?: string | null;
  parent_operation_id?: string | null;
  kind?: string;
  name?: string;
  display_name?: string;
  status?: string;
  provider?: string;
  model?: string;
  tool_name?: string;
};

export function nodeFailureRecords(node: ProjectedWorkflowNode): EvidenceRecord[] {
  return [...node.observations, ...node.model_calls].filter((record) => ["failed", "error"].includes(String(record.status || "").toLowerCase()));
}

export function effectiveNodeState(node: ProjectedWorkflowNode): ProjectedWorkflowNode["state"] {
  return node.state === "completed" && nodeFailureRecords(node).length ? "recovered" : node.state;
}

export function buildStepGraph(step: ProjectedWorkflowNode): { nodes: EvidenceGraphNode[]; edges: EvidenceGraphEdge[] } {
  const rootId = `step:${step.id}`;
  const modelIds = new Set(step.model_calls.map((record, index) => String(record.id || `model-${index}`)));
  const records = new Map<string, EvidenceRecord>();
  [...step.observations, ...step.model_calls].forEach((record, index) => {
    const typed = record as EvidenceRecord;
    records.set(String(typed.id || `record-${index}`), typed);
  });
  const parentFor = (record: EvidenceRecord) => {
    const candidate = String(record.parent_operation_id || record.parent || "");
    return records.has(candidate) ? candidate : rootId;
  };
  const depthCache = new Map<string, number>();
  const depthFor = (id: string, visiting = new Set<string>()): number => {
    if (depthCache.has(id)) return depthCache.get(id)!;
    if (visiting.has(id)) return 1;
    visiting.add(id);
    const record = records.get(id)!;
    const parent = parentFor(record);
    const depth = parent === rootId ? 1 : depthFor(parent, visiting) + 1;
    visiting.delete(id);
    depthCache.set(id, depth);
    return depth;
  };
  const layers = new Map<number, string[]>();
  records.forEach((_record, id) => {
    const depth = depthFor(id);
    layers.set(depth, [...(layers.get(depth) || []), id]);
  });
  const widest = Math.max(1, ...Array.from(layers.values(), (items) => items.length));
  const graphNodes: EvidenceGraphNode[] = [{
    id: rootId,
    position: { x: ((widest - 1) * 260) / 2, y: 0 },
    data: {
      eyebrow: step.kind || "Workflow step",
      title: step.name,
      detail: `${statePresentation(step.state).label} · ${seconds(step.duration_seconds)}`,
      tone: step.state === "failed" ? "failure" : step.state === "recovered" ? "recovered" : "success",
    },
  }];
  records.forEach((record, id) => {
    const depth = depthFor(id);
    const siblings = layers.get(depth) || [];
    const column = siblings.indexOf(id) + (widest - siblings.length) / 2;
    const status = String(record.status || "").toLowerCase();
    const tone = ["failed", "error"].includes(status)
      ? "failure"
      : modelIds.has(id) || record.kind === "model"
        ? "model"
        : "operation";
    const detail = [record.model, record.provider, record.tool_name, record.status === "unset" ? null : record.status]
      .filter(Boolean)
      .map(String)
      .join(" · ");
    graphNodes.push({
      id,
      position: { x: column * 260, y: depth * 165 },
      data: {
        eyebrow: evidenceKindLabel(record.kind, modelIds.has(id)),
        title: String(record.display_name || record.name || "Observed operation"),
        detail: detail || undefined,
        tone,
      },
    });
  });
  const graphEdges = Array.from(records, ([id, record]) => ({
    id: `evidence:${parentFor(record)}:${id}`,
    source: parentFor(record),
    target: id,
  }));
  return { nodes: graphNodes, edges: graphEdges };
}

function evidenceKindLabel(kind: unknown, modelCall: boolean): string {
  if (modelCall || kind === "model") return "Model call";
  const labels: Record<string, string> = {
    component: "Pipeline step",
    graph_node: "Graph step",
    chain: "Chain step",
    tool: "Tool call",
  };
  const raw = String(kind || "");
  return labels[raw] || raw.replaceAll("_", " ") || "Observed operation";
}

function StepGraphDialog({ step, diagnostic, onClose }: { step: ProjectedWorkflowNode; diagnostic?: GoalDiagnostic; onClose: () => void }) {
  const graph = useMemo(() => buildStepGraph(step), [step]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return createPortal(<div className="fixed inset-0 z-[9999] flex items-center justify-center overflow-hidden bg-[#27232f]/45 p-4 sm:p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-labelledby="step-graph-title" className="flex max-h-[calc(100vh-2rem)] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[#d8d2e5] bg-white shadow-2xl sm:max-h-[calc(100vh-3rem)]">
      <header className="flex items-start justify-between gap-6 border-b border-[#e8e5ee] px-6 py-5">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#7557b8]">Evidence timeline · {statePresentation(step.state).label}</div>
          <h2 id="step-graph-title" className="mt-1 text-xl font-semibold text-[#312b3b]">{step.name}</h2>
          <p className="mt-1 text-sm text-[#74746e]">{step.description || "Attempts, runtime operations, and model calls attributed to this workflow step."}</p>
        </div>
        <button type="button" autoFocus className="rounded-lg border border-[#d8d4df] px-3 py-2 text-sm font-semibold text-[#514b59] hover:bg-[#f4f2f6]" onClick={onClose}>Close</button>
      </header>
      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-h-[420px] overflow-y-auto border-r border-[#e8e5ee] bg-[#fafaf7] p-6 sm:p-8">
          {diagnostic ? <div className="mx-auto mb-5 max-w-xl rounded-xl border border-red-200 bg-red-50 p-4">
            <div className="text-[9px] font-semibold uppercase tracking-[0.13em] text-red-600">Failed business requirement · {diagnostic.requirementId}</div>
            <div className="mt-1 text-sm font-semibold text-red-800">{diagnostic.label}</div>
            {diagnostic.description ? <p className="mt-1 text-[10px] leading-4 text-red-700">{diagnostic.description}</p> : null}
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="rounded-full bg-red-100 px-2.5 py-1 text-[9px] font-semibold text-red-800 ring-1 ring-inset ring-red-200">Business check · Failed</span>
              <span className={`rounded-full px-2.5 py-1 text-[9px] font-semibold ring-1 ring-inset ${statePresentation(step.state).badge}`}>Linked runtime step · {statePresentation(step.state).label}</span>
            </div>
          </div> : null}
          <div className="mx-auto mb-4 flex max-w-xl flex-wrap gap-x-4 gap-y-2 text-[9px] font-medium text-[#746d78]" aria-label="Evidence color legend">
            <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-[#16864b]" />Completed</span>
            <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-[#d58b24]" />Recovered</span>
            <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-[#dc5a5a]" />Failed</span>
            <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-[#79a7d4]" />Model call</span>
            <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-[#c9c5ba]" />Observed operation</span>
          </div>
          <VerticalEvidenceFlow graph={graph} />
        </div>
        <aside className="overflow-y-auto p-5">
          <div className="grid gap-4 text-sm">
            <Evidence label="Runtime" value={statePresentation(step.state).label} />
            <Evidence label="Elapsed" value={seconds(step.duration_seconds)} />
            <Evidence label="Attempts" value={formatNumber(step.attempts)} />
            <Evidence label="Provider" value={step.providers.join(", ") || "Not observed"} />
            <Evidence label="Model" value={step.models.join(", ") || "Not observed"} />
            <Evidence label="Tokens" value={formatNumber(step.total_tokens)} />
            <Evidence label="Measured cost" value={money(step.known_cost)} />
            <Evidence label="Emitted route" value={String(step.emitted_route || "Not emitted")} />
          </div>
          <details className="mt-6 border-t border-[#ece9f0] pt-4">
            <summary className="cursor-pointer text-sm font-semibold text-[#5c35c8]">Raw telemetry</summary>
            <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-[#f6f6f2] p-3 text-[10px]">{JSON.stringify({ observations: step.observations, model_calls: step.model_calls }, null, 2)}</pre>
          </details>
        </aside>
      </div>
    </section>
  </div>, document.body);
}

function VerticalEvidenceFlow({ graph }: { graph: { nodes: EvidenceGraphNode[]; edges: EvidenceGraphEdge[] } }) {
  const incoming = new Map(graph.edges.map((edge) => [edge.target, edge.source]));
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const ordered = [...graph.nodes].sort((left, right) => left.position.y - right.position.y || left.position.x - right.position.x);
  const identity = (node: EvidenceGraphNode) => `${node.data.eyebrow}:${node.data.title}`;
  const totals = new Map<string, number>();
  ordered.slice(1).forEach((node) => totals.set(identity(node), (totals.get(identity(node)) || 0) + 1));
  const occurrences = new Map<string, number>();
  const tones = {
    success: "border-[#16864b] bg-[#f4fbf7]",
    recovered: "border-[#d58b24] bg-[#fff9ed]",
    running: "border-[#4386c6] bg-[#f4f9ff]",
    operation: "border-[#c9c5ba] bg-white",
    model: "border-[#79a7d4] bg-[#f4f9ff]",
    failure: "border-[#dc5a5a] bg-[#fff6f6]",
  };
  return <div className="mx-auto max-w-xl">
    {ordered.map((node, index) => {
      const parent = byId.get(incoming.get(node.id) || "");
      const key = identity(node);
      const occurrence = index ? (occurrences.get(key) || 0) + 1 : 0;
      if (index) occurrences.set(key, occurrence);
      const repeated = (totals.get(key) || 0) > 1;
      return <div key={node.id} className="relative pb-6 pl-11 last:pb-0">
        {index < ordered.length - 1 ? <span className="absolute bottom-0 left-[15px] top-7 w-px bg-[#b3a7ca]" /> : null}
        <span className={`absolute left-0 top-3 grid size-8 place-items-center rounded-full border-2 bg-white text-[10px] font-bold ${index ? "border-[#b8aec8] text-[#675b76]" : "border-[#7455bd] text-[#5b3fa0]"}`}>{index || "S"}</span>
        <div className={`w-full rounded-xl border-2 p-4 shadow-sm ${tones[node.data.tone || "operation"]}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[9px] font-semibold uppercase tracking-[0.13em] text-[#817b84]">{node.data.eyebrow}</span>
            {repeated ? <span className="rounded-full bg-[#f0ecf7] px-2 py-1 text-[9px] font-semibold text-[#655184]">Attempt {occurrence} of {totals.get(key)}</span> : parent ? <span className="text-[9px] text-[#9a939c]">after {parent.data.title}</span> : null}
          </div>
          <div className="mt-1.5 text-sm font-semibold leading-5 text-[#35313d]">{node.data.title}</div>
          {node.data.detail ? <div className="mt-1 text-[10px] text-[#74746e]">{node.data.detail}</div> : null}
          {repeated && parent ? <div className="mt-2 text-[9px] text-[#918995]">Observed under {parent.data.title}</div> : null}
        </div>
      </div>;
    })}
  </div>;
}

function DeclaredOverview({ replay }: { replay: WorkflowReplay }) {
  return <WorkflowViewToggle replay={replay} />;
}

type WorkflowView = "logic" | "goals";
type WorkflowNodeSelection = (node: ProjectedWorkflowNode, diagnostic?: GoalDiagnostic) => void;

function WorkflowViewToggle({ replay, evaluationResults = [], onSelect, firstScreen = false }: { replay: WorkflowReplay; evaluationResults?: EvaluationResult[]; onSelect?: WorkflowNodeSelection; firstScreen?: boolean }) {
  const [view, setView] = useState<WorkflowView>(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("view") === "goals" ? "goals" : "logic");
  const [controlsHost, setControlsHost] = useState<HTMLDivElement | null>(null);
  const selectView = (next: WorkflowView) => {
    setView(next);
    const url = new URL(window.location.href);
    if (next === "logic") url.searchParams.delete("view");
    else url.searchParams.set("view", next);
    window.history.replaceState(window.history.state, "", url);
  };
  return <>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div className="inline-flex rounded-lg border border-[#ddd8e5] bg-[#f6f4f8] p-1" role="tablist" aria-label="Workflow view">
        <button type="button" role="tab" aria-selected={view === "logic"} onClick={() => selectView("logic")} className={`rounded-md px-3 py-1.5 text-[10px] font-semibold transition ${view === "logic" ? "bg-white text-[#5839a6] shadow-sm" : "text-[#777079] hover:text-[#4e4654]"}`}>Logic flow</button>
        <button type="button" role="tab" aria-selected={view === "goals"} onClick={() => selectView("goals")} className={`rounded-md px-3 py-1.5 text-[10px] font-semibold transition ${view === "goals" ? "bg-white text-[#5839a6] shadow-sm" : "text-[#777079] hover:text-[#4e4654]"}`}>Goal flow</button>
      </div>
      {view === "logic" ? <div ref={setControlsHost} className="ml-auto" /> : null}
    </div>
    {view === "logic"
      ? <WorkflowCanvas replay={replay} onSelect={onSelect} firstScreen={firstScreen} controlsHost={controlsHost} />
      : <WorkflowGoalFlow replay={replay} evaluationResults={evaluationResults} onSelect={onSelect} firstScreen={firstScreen} />}
  </>;
}

export function resolveGoalOutcome(outcomes: DeclaredWorkflow["outcomes"], execution: WorkflowReplay["execution"]) {
  const observedId = String(execution.application_outcome || execution.runtime_outcome || execution.status || "");
  const declared = outcomes.find((outcome) => outcome.id === observedId);
  return {
    id: observedId || null,
    name: declared?.name || (observedId ? observedId.replaceAll("_", " ") : "Not observed"),
    achieved: execution.product_goal_achieved,
  };
}

export type GoalDiagnostic = {
  requirementId: string;
  label: string;
  description: string | null;
  stageId: string | null;
  nodeId: string | null;
};

const stringAttribute = (attributes: Record<string, unknown>, key: string) => {
  const value = attributes[key];
  return typeof value === "string" && value.trim() ? value : null;
};

export function resolveGoalDiagnostic(results: EvaluationResult[], closestBlocker?: string | null): GoalDiagnostic | null {
  const requirements = results.filter((result) => stringAttribute(result.attributes, "requirement_id") && result.passed !== true);
  const selected = requirements.find((result) => stringAttribute(result.attributes, "requirement_id") === closestBlocker)
    || requirements.find((result) => result.passed === false)
    || requirements[0];
  if (!selected) return null;
  const requirementId = stringAttribute(selected.attributes, "requirement_id")!;
  return {
    requirementId,
    label: stringAttribute(selected.attributes, "requirement_failure_label") || selected.name,
    description: stringAttribute(selected.attributes, "requirement_failure_description"),
    stageId: stringAttribute(selected.attributes, "investigation_stage"),
    nodeId: stringAttribute(selected.attributes, "investigation_node"),
  };
}

export const observedOutcomeTone = (active: boolean, goalAchieved?: boolean) => {
  if (!active) return "border-[#ddd9df] bg-white text-[#777178]";
  if (goalAchieved === false) return "border-red-300 bg-red-50 text-red-700";
  return "border-[#7557b8] bg-[#f2edff] text-[#5839a6]";
};

function WorkflowGoalFlow({ replay, evaluationResults, onSelect, firstScreen }: { replay: WorkflowReplay; evaluationResults: EvaluationResult[]; onSelect?: WorkflowNodeSelection; firstScreen: boolean }) {
  const declaredOnly = replay.nodes.length === 0;
  const outcome = resolveGoalOutcome(replay.outcomes, replay.execution);
  const diagnostic = outcome.achieved === false ? resolveGoalDiagnostic(evaluationResults, replay.execution.closest_blocker) : null;
  const investigationNode = diagnostic?.nodeId ? replay.nodes.find((node) => node.id === diagnostic.nodeId) : undefined;
  const goalLabel = outcome.achieved === true ? "Achieved" : outcome.achieved === false ? "Needs attention" : declaredOnly ? "Declared" : "Not reported";
  const goalTone = outcome.achieved === true ? "bg-green-700 text-white ring-green-800" : outcome.achieved === false ? "bg-red-50 text-red-700 ring-red-200" : "bg-stone-100 text-stone-600 ring-stone-200";
  return <section aria-label="Goal flow" className={`${firstScreen ? "h-[clamp(280px,calc(100vh-387px),560px)]" : "h-[clamp(320px,calc(100vh-360px),620px)]"} overflow-auto rounded-2xl border border-[#dfdce5] bg-[radial-gradient(#ddd9e2_0.8px,transparent_0.8px)] [background-size:22px_22px] p-4`}>
    <div className="mx-auto flex min-h-full min-w-[760px] max-w-6xl flex-col justify-center rounded-xl border border-[#e2dee7] bg-white/90 p-4 shadow-[0_7px_20px_rgba(47,39,59,.06)]">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[9px] font-semibold uppercase tracking-[0.13em] text-[#7557b8]">Product goal journey</div>
          <h3 className="mt-1 truncate text-sm font-semibold text-[#342f39]">{replay.workflow.name}</h3>
          <p className="mt-1 max-w-3xl truncate text-[10px] text-[#777178]">{replay.workflow.description || "Complete the declared workflow and reach an accepted outcome."}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[9px] font-semibold ring-1 ring-inset ${goalTone}`}>{goalLabel}</span>
      </div>
      <div className="mt-3 flex items-stretch justify-center">
        {replay.stages.map((stage, index) => {
          const state = declaredOnly ? "declared" : stage.state;
          const presentation = statePresentation(state);
          return <div key={stage.id} className="flex items-center">
            <article className={`relative flex h-[76px] w-32 shrink-0 flex-col justify-between rounded-lg border bg-white px-3 py-2.5 ${diagnostic?.stageId === stage.id ? "ring-2 ring-red-200" : ""}`} style={{ borderColor: diagnostic?.stageId === stage.id ? "#dc5a5a" : presentation.border }}>
              {diagnostic?.stageId === stage.id ? <span className="absolute -top-2 right-2 rounded-full bg-red-50 px-1.5 py-0.5 text-[7px] font-semibold uppercase tracking-wide text-red-700 ring-1 ring-red-200">Investigate</span> : null}
              <div className="line-clamp-2 text-[11px] font-semibold leading-4 text-[#3d3742]">{index + 1}. {stage.name}</div>
              <div className="flex items-center justify-between text-[9px] text-[#8a838d]"><span>{declaredOnly ? `${stage.nodes.length} steps` : `${stage.active_nodes}/${stage.nodes.length} reached`}</span><span className="size-2 rounded-full" style={{ background: goalStageDotColor(state, diagnostic?.stageId === stage.id) }} /></div>
            </article>
            {index < replay.stages.length - 1 ? <div className="flex w-6 shrink-0 items-center" aria-hidden="true"><span className="h-px flex-1 bg-[#a99dbb]" /><span className="text-[10px] text-[#786b91]">›</span></div> : null}
          </div>;
        })}
      </div>
      {diagnostic ? <div className="mx-auto mt-3 flex w-full max-w-3xl items-start justify-between gap-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-left">
        <div className="min-w-0">
          <div className="text-[8px] font-semibold uppercase tracking-[.12em] text-red-600">Failed contract requirement · {diagnostic.requirementId}</div>
          <div className="mt-0.5 text-[11px] font-semibold text-red-800">{diagnostic.label}</div>
          {diagnostic.description ? <div className="mt-0.5 text-[9px] leading-4 text-red-700">{diagnostic.description}</div> : null}
        </div>
        {investigationNode && onSelect ? <button type="button" onClick={() => onSelect(investigationNode, diagnostic)} className="shrink-0 rounded-md border border-red-300 bg-white px-2.5 py-1.5 text-[9px] font-semibold text-red-700 hover:bg-red-100">Inspect {investigationNode.name}</button> : null}
      </div> : null}
      <div className="mt-3 flex flex-wrap items-center justify-center gap-2 border-t border-[#ebe8ee] pt-2">
        <span className="mr-1 text-[8px] font-semibold uppercase tracking-[0.12em] text-[#969098]">Possible outcomes</span>
        {replay.outcomes.map((item) => {
          const active = outcome.id === item.id;
          return <span key={item.id} className={`rounded-full border px-2.5 py-1 text-[9px] font-semibold ${observedOutcomeTone(active, outcome.achieved)}`}>{item.name}{active ? " · observed" : ""}</span>;
        })}
      </div>
    </div>
  </section>;
}

function WorkflowReplayView({ replay, evaluationResults = [] }: { replay: WorkflowReplay; evaluationResults?: EvaluationResult[] }) {
  const [selected, setSelected] = useState<{ step: ProjectedWorkflowNode; diagnostic?: GoalDiagnostic } | null>(null);
  const selectNode: WorkflowNodeSelection = (step, diagnostic) => setSelected({ step, diagnostic });
  const discrepancyCount = replay.discrepancies.unexpected_operations.length + replay.discrepancies.unexpected_transitions.length;
  return <>
    <Panel title="Workflow replay" note="Switch between the complete runtime DAG and the product-goal journey. Inspect opens step evidence vertically.">
      <WorkflowViewToggle replay={replay} evaluationResults={evaluationResults} onSelect={selectNode} firstScreen />
    </Panel>
    <ExecutionAnalytics replay={replay} onSelect={(step) => selectNode(step)} />
    {selected ? <StepGraphDialog step={selected.step} diagnostic={selected.diagnostic} onClose={() => setSelected(null)} /> : null}
    {discrepancyCount ? <Panel className="mt-4" title="Declared / observed differences" note="Witdem reports differences instead of forcing telemetry into the template."><div className="space-y-2 text-sm">{replay.discrepancies.unexpected_operations.map((item) => <div key={item.id} className="rounded-lg bg-amber-50 p-3">Unexpected {item.kind}: {item.name}</div>)}{replay.discrepancies.unexpected_transitions.map((item) => <div key={`${item.from}-${item.to}`} className="rounded-lg bg-amber-50 p-3">Unexpected transition: {item.from} → {item.to}</div>)}</div></Panel> : null}
  </>;
}

function ExecutionAnalytics({ replay, onSelect }: { replay: WorkflowReplay; onSelect?: (node: ProjectedWorkflowNode) => void }) {
  const visibleNodes = replay.nodes.map((node) => {
    const state = effectiveNodeState(node);
    return state === node.state ? node : { ...node, state };
  });
  const executedNodes = visibleNodes.filter((node) => node.state !== "inactive");
  const attempts = executedNodes.reduce((total, node) => total + node.attempts, 0);
  const retryAttempts = executedNodes.reduce((total, node) => total + Math.max(0, node.attempts - 1), 0);
  const recoveredSteps = executedNodes.filter((node) => node.state === "recovered").length;
  const pathCoverage = visibleNodes.length ? executedNodes.length / visibleNodes.length : 0;
  const goal = replay.execution.product_goal_achieved === true ? "Goal achieved" : replay.execution.product_goal_achieved === false ? "Goal needs attention" : "Goal not reported";
  const completedIsSupporting = replay.execution.product_goal_achieved === true;
  const outcome = String(replay.execution.application_outcome || replay.execution.runtime_outcome || replay.execution.status || "Not reported").replaceAll("_", " ");
  const runtime = String(replay.execution.runtime_id || replay.execution.adapter_name || "Not reported");
  return <Panel className="mt-3" title="Execution at a glance" note={`${formatDateTime(replay.execution.started_at)} · ${runtime}`}>
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-[#ece9ee] pb-2">
      <div className="flex min-w-0 items-baseline gap-2"><div className="text-[9px] font-semibold uppercase tracking-[0.12em] text-[#8d8790]">Result</div><div className="truncate text-base font-semibold capitalize text-[#352f3b]">{outcome}</div></div>
      <div className={`rounded-full px-2 py-1 text-[10px] font-semibold ${replay.execution.product_goal_achieved === false ? "bg-red-50 text-red-700" : replay.execution.product_goal_achieved === true ? "bg-green-700 text-white" : "bg-stone-100 text-stone-600"}`}>{goal}</div>
    </div>
    <div className="grid gap-4 lg:grid-cols-[.72fr_1.28fr]">
      <AnalysisCard title="Path coverage" note="Executed versus declared"><RatioDonutChart height={138} value={pathCoverage} achievedLabel="Executed" remainderLabel="Not used" detail={`${executedNodes.length} of ${visibleNodes.length} declared steps`} /></AnalysisCard>
      <AnalysisCard title="Step footprint" note="Filter by state; select an executed square to inspect its evidence."><StepStateMatrix nodes={visibleNodes} completedIsSupporting={completedIsSupporting} onSelect={onSelect} /></AnalysisCard>
    </div>
    <AnalysisCard className="mt-2" title="Step attribution" note="Hover for model/provider; select a bar for evidence"><ExecutionStepDiagnostics height={224} nodes={visibleNodes} completedIsSupporting={completedIsSupporting} onSelect={onSelect} /></AnalysisCard>
    <dl className="mt-2 grid gap-x-4 gap-y-1 border-t border-[#ece9ee] pt-2 text-[10px] sm:grid-cols-2 lg:grid-cols-5">
      <div><dt className="text-[#8b858d]">Elapsed</dt><dd className="font-semibold text-[#39343e]">{seconds(replay.execution.duration_seconds)}</dd></div>
      <div><dt className="text-[#8b858d]">Attempts</dt><dd className="font-semibold text-[#39343e]">{attempts} total · {retryAttempts} extra</dd></div>
      <div><dt className="text-[#8b858d]">Recovery</dt><dd className="font-semibold text-[#39343e]">{recoveredSteps ? `${recoveredSteps} recovered step${recoveredSteps === 1 ? "" : "s"}` : "No recovery"}</dd></div>
      <div><dt className="text-[#8b858d]">Tokens</dt><dd className="font-semibold text-[#39343e]">{replay.execution.total_tokens == null ? "Not measured" : formatNumber(replay.execution.total_tokens)}</dd></div>
      <div><dt className="text-[#8b858d]">Measured cost</dt><dd className="font-semibold text-[#39343e]">{money(replay.execution.known_cost)}</dd></div>
    </dl>
  </Panel>;
}

function Evidence({ label, value }: { label: string; value: string }) { return <div><div className="text-[10px] font-semibold uppercase tracking-wider text-[#92918a]">{label}</div><div className="mt-1 font-medium text-[#34342f]">{value}</div></div>; }
