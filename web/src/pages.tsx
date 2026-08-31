import { Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  api,
  type ContractDefinition,
  type ComparisonInsight,
  type DashboardFilters,
  type GoalPortfolioItem,
  type Issues,
  type Meta,
  type Overview,
  type Performance,
  type Run,
} from "./api";
import {
  Badge,
  BreakdownBar,
  Button,
  CostLatencyScatter,
  CostSpeedChart,
  EconomicsBarChart,
  Empty,
  ErrorPage,
  ExecutionListCard,
  browserDateDaysAgo,
  formatNumber,
  Kpi,
  LoadingPage,
  LatencyVariabilityChart,
  money,
  NormalizedComparisonChart,
  OperationHealthChart,
  PageHeader,
  Panel,
  percent,
  PerformanceList,
  ProviderSpendChart,
  RuntimeDonutChart,
  QualityComparisonChart,
  seconds,
  GoalTrendChart,
  GoalTradeoffChart,
  GoalRateColumns,
  StageAccumulation,
  useQuery,
  WorkflowBarChart,
  WorkflowStageContribution,
} from "./components";
import { contractOutcomeColors } from "./outcome-colors";

const providerDisplayName = (value: string) => {
  return value.replace(/(^|[\s_-])\p{L}/gu, (match) => match.toUpperCase());
};

const routeParam = (name: string) =>
  typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get(name) || "";

export function OverviewPage() {
  const [filterValues, setFilterValues] = useState(EMPTY_FILTERS);
  const filters = resolvedFilters(filterValues);
  const q = useQuery({
    queryKey: ["overview", "portfolio", filterValues],
    queryFn: () => api.overview(filters),
  });
  const models = useQuery({
    queryKey: ["compare", "overview", "model", filterValues],
    queryFn: () => api.compare("model", filters),
  });
  const providers = useQuery({
    queryKey: ["compare", "overview", "provider", filterValues],
    queryFn: () => api.compare("provider", filters),
  });
  if (q.isLoading || models.isLoading || providers.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (models.error) return <ErrorPage error={models.error} />;
  if (providers.error) return <ErrorPage error={providers.error} />;
  const d = q.data!;
  const assurance = d.assurance_summary;
  const runtimeAttention = d.execution.attention_runs;
  const completionRate = d.execution.runtime_success_rate;
  const costIncomplete = d.costs.cost.partial_runs + d.costs.cost.missing_runs > 0;
  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title="What is working and what needs attention?"
        description="Business outcomes and operational health in one view. Drill into any goal, model, or provider with the same filters preserved."
      />
      <SharedFilterBar
        metadata={d.metadata}
        values={filterValues}
        onChange={setFilterValues}
        includeGoal={false}
      />
      <div className="grid gap-4 xl:grid-cols-[1.05fr_.72fr_.48fr]">
        <section className="overflow-hidden rounded-2xl bg-[#231b3d] p-6 text-white shadow-[0_12px_35px_rgba(43,29,83,.14)]">
          <div className="text-xs font-semibold uppercase tracking-[.14em] text-[#bdaaff]">Business outcomes</div>
          <div className="mt-3 flex flex-wrap items-end justify-between gap-6">
            <div>
              <div className="text-4xl font-semibold tracking-[-.04em]">{percent(d.goals.success_rate)}</div>
              <div className="mt-2 text-sm text-[#d5cdea]">{formatNumber(d.goals.achieved_runs)} of {formatNumber(d.goals.reported_runs)} reported goals achieved</div>
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
              <div><div className="text-2xl font-semibold text-[#7ee0aa]">{formatNumber(assurance.assured_runs)}</div><div className="text-[#bdb5cf]">assured</div></div>
              <div><div className="text-2xl font-semibold text-[#ffc267]">{formatNumber(assurance.attention_runs + assurance.unassessed_runs)}</div><div className="text-[#bdb5cf]">assurance attention</div></div>
            </div>
          </div>
          <a href="/goal-performance" className="mt-6 inline-flex rounded-lg bg-white/10 px-3 py-2 text-xs font-semibold text-white hover:bg-white/15">Explore goal performance →</a>
        </section>
        <section className="rounded-2xl border border-[#e2e1db] bg-white p-6 shadow-[0_8px_30px_rgba(40,40,30,.05)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[.14em] text-[#6f6f69]">System health</div>
              <div className="mt-3 text-4xl font-semibold tracking-[-.04em] text-[#252522]">{percent(completionRate)}</div>
              <div className="mt-2 text-sm text-[#72726c]">runtime completion across {formatNumber(d.execution.total_runs)} runs</div>
            </div>
            <div className={`rounded-xl px-4 py-3 text-right ${runtimeAttention ? "bg-[#fff4e7]" : "bg-[#edf9f2]"}`}>
              <div className={`text-2xl font-semibold ${runtimeAttention ? "text-[#a96108]" : "text-[#167a47]"}`}>{formatNumber(runtimeAttention)}</div>
              <div className="text-xs text-[#777]">need attention</div>
            </div>
          </div>
          <div className="mt-5 grid grid-cols-3 gap-2 border-t border-[#ecebe6] pt-4 text-xs">
            <div><div className="font-semibold text-[#333]">{seconds(d.execution.avg_duration_seconds)}</div><div className="mt-1 text-[#777]">avg / terminal run</div></div>
            <div><div className="font-semibold text-[#333]">{money(d.costs.measured_cost_per_run)}</div><div className="mt-1 text-[#777]">complete measured / applicable run</div></div>
            <div><div className="font-semibold text-[#333]">{percent(d.costs.cost.coverage)}</div><div className="mt-1 text-[#777]">applicable cost coverage</div></div>
          </div>
          <a href="/system-health" className="mt-5 inline-flex text-xs font-semibold text-[#603bd1]">Explore system health →</a>
        </section>
        <section className="flex min-w-0 flex-col rounded-2xl border border-[#ded7f3] bg-[#f8f5ff] p-6 shadow-[0_8px_30px_rgba(62,42,112,.06)]">
          <div className="text-xs font-semibold uppercase tracking-[.14em] text-[#7151cc]">{costIncomplete ? "Known subtotal" : "Measured spend"}</div>
          <div className="mt-3 break-words text-4xl font-semibold tracking-[-.04em] text-[#2f2450]">
            {money(d.costs.measured_cost)}
          </div>
          <div className="mt-2 text-sm leading-5 text-[#716a7f]">
            {formatNumber(d.costs.cost.applicable_runs)} of {formatNumber(d.execution.total_runs)} runs contained billable activity
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3 border-t border-[#e5def6] pt-4 text-xs">
            <div>
              <div className="font-semibold text-[#3e3458]">{percent(d.costs.cost.coverage)}</div>
              <div className="mt-1 text-[#817a8d]">of applicable runs</div>
            </div>
            <div>
              <div className="font-semibold text-[#3e3458]">{money(d.costs.measured_cost_per_run)}</div>
              <div className="mt-1 text-[#817a8d]">measured / run</div>
            </div>
          </div>
          <a href="/system-health" className="mt-auto pt-5 text-xs font-semibold text-[#603bd1]">Inspect spend →</a>
        </section>
      </div>
      {(d.operation_health.failed_operations > 0 || d.operation_measurement_alerts.length > 0) ? <a href="/issues" className="mt-3 flex items-center justify-between rounded-xl border border-[#ead9c8] bg-[#fff9f1] px-4 py-2.5 text-xs text-[#805527]"><span><strong>{formatNumber(d.operation_health.failed_operations)} operation failures</strong>{d.operation_measurement_alerts.length ? ` · ${formatNumber(d.operation_measurement_alerts.length)} required measurement gaps` : ""}</span><span className="font-semibold">Inspect issues →</span></a> : null}
      <div className="mt-4 grid gap-4 xl:grid-cols-12 xl:items-stretch">
        <Panel
          className="xl:col-span-7"
          title="Business goals"
          note="Achievement and assurance are separate. Select a goal for its complete breakdown."
        >
          <GoalPortfolio items={d.goal_portfolio.slice(0, 5)} destination="/goal-performance" compact />
        </Panel>
        <div className="grid min-w-0 gap-4 xl:col-span-5 xl:grid-rows-[minmax(0,1fr)_auto]">
          <Panel
            className="h-full"
            title="Goal performance over time"
            note="Achievement, time, and cost for the complete selected population."
          >
            <GoalTrendChart items={d.goal_trend} />
          </Panel>
          <Panel title="Where systems break" note="Highest-volume operational breakpoints. Select one to investigate the affected runs.">
            <OverviewFailures data={d} />
          </Panel>
        </div>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel className="h-full" title="Model goal trade-offs" note="Goal outcomes for runs involving each model versus that model’s directly attributed cost.">
          <GoalTradeoffChart items={models.data!.items} onSelect={(item) => window.location.assign(drilldownHref("/goal-performance", { model: item.label }))} />
        </Panel>
        <Panel className="h-full" title="Model call latency distribution" note="Direct model-call p50 and p95 latency. Select a model to inspect System Health.">
          <LatencyVariabilityChart height={360} items={models.data!.items} onSelect={(item) => window.location.assign(drilldownHref("/system-health", { model: item.label }))} />
        </Panel>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3 xl:items-stretch">
        <Panel className="h-full" title="Runtime state mix" note="Completed, recovered, failed, and still-running executions.">
          <RuntimeDonutChart height={310} data={d.runtime_breakdown} colors={{ completed: "#24a267", recovered: "#168e89", failed: "#d95858", running: "#2477e6", unknown: "#9aa1ad" }} />
        </Panel>
        <Panel className="h-full" title="Provider goal outcomes" note="Achievement and decision correctness for runs involving each provider.">
          <GoalRateColumns height={310} items={providers.data!.items} onSelect={(item) => window.location.assign(drilldownHref("/goal-performance", { provider: item.provider_id || item.label }))} />
        </Panel>
        <Panel className="h-full" title="Provider share of measured spend" note="Operational spend composition for the same population. Select a segment to inspect System Health.">
          <ProviderSpendChart height={310} items={d.providers} breakdown="provider" onSelect={(item) => window.location.assign(drilldownHref("/system-health", { provider: item.label }))} />
        </Panel>
      </div>
    </>
  );
}

function DashboardSectionPage({ mode }: { mode: "health" | "goals" }) {
  const [breakdown, setBreakdown] = useState<"model" | "provider">("model");
  const [contractHash, setContractHash] = useState(() => routeParam("contract_hash"));
  const [provider, setProvider] = useState(() => routeParam("provider"));
  const [model, setModel] = useState(() => routeParam("model"));
  const [status, setStatus] = useState(() => routeParam("status"));
  const [range, setRange] = useState(() => routeParam("range") || "all");
  const startDate =
    range === "all"
      ? undefined
      : browserDateDaysAgo(Number(range));
  const filters: DashboardFilters = {
    contract_hash: contractHash || undefined,
    provider: provider || undefined,
    model: model || undefined,
    status: status || undefined,
    start_date: startDate,
  };
  const q = useQuery({
    queryKey: ["overview", contractHash, provider, model, status, range],
    queryFn: () => api.overview(filters),
  });
  const goalComparison = useQuery({
    queryKey: ["compare", "goal-performance", breakdown, contractHash, provider, model, status, range],
    queryFn: () => api.compare(breakdown, filters),
    enabled: mode === "goals",
  });
  if (q.isLoading || (mode === "goals" && goalComparison.isLoading)) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (mode === "goals" && goalComparison.error) return <ErrorPage error={goalComparison.error} />;
  const d = q.data!;
  const metadata = d.metadata;
  const selectedContract = contractHash
    ? metadata.contracts.find((item) => item.contract_hash === contractHash)
    : undefined;
  const breakdownItems =
    breakdown === "model"
      ? d.models
      : d.providers;
  const breakdownLabel = breakdown === "model" ? "Model" : "Provider";
  const goalNote = `${formatNumber(d.goals.achieved_runs)} of ${formatNumber(d.goals.reported_runs)} reported goals achieved`;
  const runtimeColors = {
    completed: "#16864b",
    recovered: "#168e89",
    failed: "#d64545",
    running: "#2477e6",
    unknown: "#7a8290",
  };
  const outcomeColors = contractOutcomeColors(d.outcome_breakdown, d.contracts);
  const semanticOperationTypes = d.operation_health.types.filter((item) => item.family !== "orchestration" && item.type !== "x.witdem.unclassified");
  const semanticOperationCount = semanticOperationTypes.reduce((total, item) => total + item.operations, 0);
  const semanticOperationFailures = semanticOperationTypes.reduce((total, item) => total + item.failed, 0);
  const excludedCoordinationCount = d.operation_health.total_operations - semanticOperationCount;
  return (
    <>
      <PageHeader
        eyebrow={mode === "health" ? "Operations" : "Business outcomes"}
        title={mode === "health" ? "System health" : "Goal performance"}
        description={
          mode === "health"
            ? "Inspect completion, failures, recoveries, latency, and measurement coverage across every workflow."
            : "Understand which goals were achieved, how strongly their declared checks support that result, and what needs attention."
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-[#ddd8ef] bg-white p-3">
          <span className="mr-1 text-xs font-semibold text-[#555]">Filter this view</span>
          <FilterSelect value={contractHash} onChange={setContractHash} label="All business goals">
            {metadata.contracts.map((item) => (
              <option key={item.contract_hash} value={item.contract_hash}>
                {item.product_goal?.name || item.contract_name || "Business goal"}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect value={provider} onChange={setProvider} label="All providers">
            {(metadata.filters.provider || []).map((value) => (
              <option key={value} value={value}>{providerDisplayName(value)}</option>
            ))}
          </FilterSelect>
          <FilterSelect value={model} onChange={setModel} label="All models">
            {(metadata.filters.model || []).map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </FilterSelect>
          <FilterSelect value={status} onChange={setStatus} label="All runtime states">
            <option value="completed">Completed or recovered</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
          </FilterSelect>
          <FilterSelect value={range} onChange={setRange} label="All time" includeEmpty={false}>
            <option value="1">Last 24 hours</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
          </FilterSelect>
      </div>
      {selectedContract && <ContractBanner contract={selectedContract} />}
      {mode === "health" ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Kpi label="Runs" value={formatNumber(d.execution.total_runs)} />
            <Kpi
              label="Runtime completion"
              value={percent(d.execution.runtime_success_rate)}
              note={`${formatNumber(d.execution.successful_runs + d.execution.recovered_runs)} of ${formatNumber(d.execution.terminal_runs)} terminal runs`}
              tone="good"
            />
            <Kpi
              label="Needs attention"
              value={formatNumber(d.execution.attention_runs)}
              note={`${formatNumber(d.execution.failed_runs)} failed · ${formatNumber(d.execution.recovered_runs)} recovered`}
              tone={d.execution.failed_runs ? "warn" : "neutral"}
            />
            <Kpi label="Average elapsed" value={seconds(d.execution.avg_duration_seconds)} />
            <Kpi
              label="Measured cost / run"
              value={money(d.costs.measured_cost_per_run)}
              note={`${formatNumber(d.costs.cost.complete_runs)} complete of ${formatNumber(d.costs.cost.applicable_runs)} applicable`}
            />
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-[1.15fr_.85fr]">
            <Panel title="Runtime health" note="Did each agent finish, recover, fail, or remain running?">
              <BreakdownBar data={d.runtime_breakdown} colors={runtimeColors} />
            </Panel>
            <AttentionPanel data={d} />
          </div>
          <Panel className="mt-4" title="Workflow volume and reliability" note="Where work is completing, recovering, or breaking.">
            <WorkflowBarChart items={d.workflows} />
          </Panel>
          <Panel className="mt-4" title="Where work accumulates" note="Declared YAML steps ranked by deduplicated active wall time, directly attributed tokens, or measured cost.">
            <StageAccumulation items={d.stages} />
          </Panel>
          <Panel className="mt-4" title="Operation health" note={`Semantic AI, knowledge, media, action, and quality work. ${formatNumber(excludedCoordinationCount)} coordination or unclassified spans are excluded.`}>
            <div className="grid gap-4 xl:grid-cols-[1.45fr_.55fr]">
              <OperationHealthChart items={semanticOperationTypes} height={230} />
              <div className="grid content-start gap-2 sm:grid-cols-2 xl:grid-cols-1">
                <div className="rounded-lg border border-[#e8e5e9] bg-[#fbfbf9] p-3"><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Semantic activity</div><div className="mt-1 text-xl font-semibold">{formatNumber(semanticOperationCount)}</div><div className="mt-1 text-[10px] text-[#777178]">across {formatNumber(semanticOperationTypes.length)} operation types</div></div>
                <div className={`rounded-lg border p-3 ${semanticOperationFailures ? "border-red-200 bg-red-50" : "border-[#e8e5e9] bg-[#fbfbf9]"}`}><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Direct failures</div><div className={`mt-1 text-xl font-semibold ${semanticOperationFailures ? "text-red-700" : ""}`}>{formatNumber(semanticOperationFailures)}</div><div className="mt-1 text-[10px] text-[#777178]">failed semantic operations, not affected-run exposure</div></div>
                <div className={`rounded-lg border p-3 sm:col-span-2 xl:col-span-1 ${d.operation_measurement_alerts.length ? "border-amber-200 bg-amber-50" : "border-[#e8e5e9] bg-[#fbfbf9]"}`}><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#8b858d]">Required measurement gaps</div><div className="mt-1 text-xl font-semibold">{formatNumber(d.operation_measurement_alerts.length)}</div>{d.operation_measurement_alerts.length ? <div className="mt-2 space-y-1 text-[10px] text-[#86531d]">{d.operation_measurement_alerts.slice(0, 3).map((item) => <div key={`${item.operation_type}-${item.measurement_key}`}>{humanizeOperation(item.operation_type)} · {item.measurement_key}: {formatNumber(item.operations)}</div>)}</div> : <div className="mt-1 text-[10px] text-[#777178]">No required meters are missing.</div>}</div>
              </div>
            </div>
          </Panel>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 rounded-xl border border-[#e4e2da] bg-white px-4 py-3 text-xs text-[#666]">
            <span className="font-semibold text-[#333]">Telemetry coverage</span>
            <span>Cost: {formatNumber(d.costs.cost.complete_runs)} complete · {formatNumber(d.costs.cost.partial_runs)} partial · {formatNumber(d.costs.cost.applicable_runs)} applicable of {formatNumber(d.execution.total_runs)}</span>
            <span>Tokens: {formatNumber(d.costs.tokens.complete_runs)} complete · {formatNumber(d.costs.tokens.partial_runs)} partial · {formatNumber(d.costs.tokens.applicable_runs)} applicable</span>
            <span>Business goals: {formatNumber(d.goals.reported_runs)} of {formatNumber(d.execution.total_runs)} runs</span>
          </div>
        </>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Kpi
              label="Goal reporting"
              value={percent(d.goals.coverage)}
              note={`${formatNumber(d.goals.reported_runs)} of ${formatNumber(d.execution.total_runs)} runs`}
            />
            <Kpi label="Goal success" value={percent(d.goals.success_rate)} note={goalNote} tone="good" />
            <Kpi
              label="Assured achievements"
              value={formatNumber(d.assurance_summary.assured_runs)}
              note={`${percent(d.assurance_summary.assurance_rate)} of achieved goals`}
              tone="good"
            />
            <Kpi
              label="Assurance attention"
              value={formatNumber(d.assurance_summary.attention_runs + d.assurance_summary.unassessed_runs)}
              note={`${formatNumber(d.assurance_summary.attention_runs)} needs attention · ${formatNumber(d.assurance_summary.unassessed_runs)} unassessed`}
              tone={d.assurance_summary.attention_runs + d.assurance_summary.unassessed_runs ? "warn" : "good"}
            />
            <Kpi label="Cost / achieved goal" value={money(d.goals.cost_per_achieved_goal)} note={`${formatNumber(d.goals.cost_measured_achieved_runs)} of ${formatNumber(d.goals.achieved_runs)} achieved runs measured`} />
          </div>
          {selectedContract ? (
            <>
              <Panel className="mt-4" title="Goal assurance" note="Achievement and the strength of its declared checks for this goal.">
                <GoalPortfolio items={d.goal_portfolio} />
              </Panel>
              <div className="mt-4 grid gap-4 xl:grid-cols-2">
              <Panel title="Business results" note="These labels belong to the selected business contract; they are not runtime states.">
                <BreakdownBar data={d.outcome_breakdown} colors={outcomeColors} />
              </Panel>
                <Panel title="Declared checks" note="What this contract evaluated and how its reported runs scored.">
              {d.evaluations.length ? (
                <div className="space-y-3">
                  {d.evaluations.slice(0, 6).map((evaluation) => (
                    <div key={evaluation.key} className="rounded-lg bg-[#f7f7f3] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="text-sm font-semibold">{evaluation.name}</div>
                        <Badge color="gray">{formatNumber(evaluation.reported_runs)} runs</Badge>
                      </div>
                      {evaluation.description && <div className="mt-1 text-xs leading-5 text-[#6f6f69]">{evaluation.description}</div>}
                      <div className="mt-2 text-xs font-medium text-[#5a35c8]">
                        {evaluation.average_score != null ? `Average: ${formatNumber(evaluation.average_score)} ${evaluation.unit || ""}` : Object.entries(evaluation.labels).map(([label, count]) => `${label}: ${count}`).join(" · ")}
                        {evaluation.target != null ? ` · Target: ${formatNumber(Number(evaluation.target))} ${evaluation.unit || ""}` : ""}
                      </div>
                    </div>
                  ))}
                </div>
              ) : <Empty>No declared checks were reported for this goal.</Empty>}
                </Panel>
              </div>
              <div className="mt-4 grid gap-4 xl:grid-cols-[.8fr_1.2fr]">
                <Panel title="Why this goal was missed" note="Concrete blockers reported by the application; runtime success is never treated as goal success.">
              {d.goal_misses.length ? (
                <div className="space-y-3">
                  {d.goal_misses.map((item) => (
                    <div key={item.reason} className="rounded-lg bg-[#fff5f5] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="text-sm font-medium">{item.reason}</div>
                        <Badge color="red">{formatNumber(item.runs)} runs</Badge>
                      </div>
                      <div className="mt-2 text-xs text-[#777]">{seconds(item.time_seconds)} observed · {money(item.known_cost)}</div>
                    </div>
                  ))}
                </div>
              ) : <Empty>No reported goal misses in this view.</Empty>}
              {d.goals.targeted_research_runs > 0 && (
                <div className="mt-3 rounded-lg bg-[#f3fbf6] p-3 text-xs text-[#286b45]">
                  Targeted research recovered {formatNumber(d.goals.targeted_research_successes)} of {formatNumber(d.goals.targeted_research_runs)} required cases.
                </div>
              )}
                </Panel>
                <Panel title="Change over time" note="Success, time, and cost for this goal under the selected filters.">
                  <GoalTrendChart items={d.goal_trend} />
                </Panel>
              </div>
            </>
          ) : (
            <>
              <Panel className="mt-4" title="Goal portfolio" note="A compact view of achievement, assurance, and attention across every logical goal.">
                <GoalPortfolioGrid items={d.goal_portfolio} />
              </Panel>
              <div className="mt-4 grid gap-4 xl:grid-cols-[.85fr_1.15fr]">
                <Panel title="What needs attention" note="Below-target checks and reported goal blockers, ranked for investigation.">
                  <GoalAttentionQueue data={d} />
                </Panel>
                <Panel title="Portfolio change over time" note="Goal achievement, time, and cost across the complete selected portfolio.">
                  <GoalTrendChart items={d.goal_trend} />
                </Panel>
              </div>
            </>
          )}
        </>
      )}
      <div className="mt-6 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">{mode === "health" ? "Operational breakdown" : "Goal breakdown"}</div>
          <div className="text-xs text-[#777]">
            {mode === "health"
              ? "Reliability, latency, cost, and spend for the selected population."
              : "Achievement, assurance, evaluation quality, and goal economics for the selected population."}
          </div>
        </div>
        <div className="flex rounded-lg bg-[#ecebe7] p-1">
          {(["model", "provider"] as const).map((value) => (
            <button
              key={value}
              onClick={() => setBreakdown(value)}
              className={`rounded-md px-4 py-2 text-xs font-semibold ${
                breakdown === value
                  ? "bg-white text-[#5a35c8] shadow-sm"
                  : "text-[#666]"
              }`}
            >
              By {value}
            </button>
          ))}
        </div>
      </div>
      {mode === "health" ? (
        <>
          <Panel className="mt-3" title={`${breakdownLabel} runtime reliability`} note="Execution outcomes for runs involving each operational participant; cost, tokens, calls, and active time remain directly attributed.">
            <SystemBreakdownList items={breakdownItems} dimension={breakdown} destination="/system-health" />
          </Panel>
          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,.8fr)]">
            <Panel title={`${breakdownLabel} cost versus active time`} note="Directly attributed active time and measured spend; bubble size shows involved-run volume.">
              <CostSpeedChart items={breakdownItems} breakdown={breakdown} />
            </Panel>
            <Panel title={`${breakdownLabel} share of measured spend`} note="Share of measured spend. Hover for the exact amount.">
              <ProviderSpendChart items={breakdownItems} breakdown={breakdown} />
            </Panel>
          </div>
          <Panel className="mt-4" title={`${breakdownLabel} operational ranking`} note="Direct active time and measured cost for each participant.">
            <EconomicsBarChart items={breakdownItems} />
          </Panel>
        </>
      ) : (
        <>
          <div className="mt-3 grid gap-4 xl:grid-cols-2">
            <Panel title={`Goal outcomes for runs involving each ${breakdown}`} note="These are cohort outcomes, not causal attribution; runtime completion is excluded.">
              <GoalRateColumns
                items={goalComparison.data?.items || []}
                onSelect={(item) => breakdown === "model" ? setModel(item.model_family || item.model_id || item.label) : setProvider(item.provider_id || item.label)}
              />
            </Panel>
            <Panel title="Goal achievement versus attributed cost" note="Vertical values are cohort outcomes for involved runs; horizontal values are directly attributed participant cost.">
              <GoalTradeoffChart items={goalComparison.data?.items || []} />
            </Panel>
          </div>
          {selectedContract && (
            <Panel className="mt-4" title="Evaluation results for runs involving participant" note="Average final evaluation facts for each participant cohort, compared with the selected contract targets.">
              <QualityComparisonChart items={goalComparison.data?.items || []} />
            </Panel>
          )}
        </>
      )}
    </>
  );
}

export function SystemHealthPage() {
  return <DashboardSectionPage mode="health" />;
}

export function GoalPerformancePage() {
  return <DashboardSectionPage mode="goals" />;
}

export const drilldownHref = (
  destination: string,
  filters: { contract_hash?: string | null; model?: string; provider?: string },
) => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => value && params.set(key, value));
  const query = params.toString();
  return query ? `${destination}?${query}` : destination;
};

function OverviewFailures({ data }: { data: Overview }) {
  if (!data.failures.length) {
    return (
      <div className="flex items-center justify-between rounded-lg bg-[#f1faf5] px-4 py-3 text-sm">
        <span className="font-medium text-[#167a47]">No terminal failure locations reported</span>
        <a href="/system-health" className="text-xs font-semibold text-[#603bd1]">Inspect health →</a>
      </div>
    );
  }
  const failure = data.failures[0];
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <a href="/issues" className="min-w-0 flex-1 rounded-lg border border-[#f4ddd7] bg-[#fff5f2] px-4 py-3 hover:bg-[#ffefe9]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[#713a30]">{failure.failure_location}</div>
            <div className="mt-1 text-xs text-[#846b65]">{formatNumber(failure.terminal_runs)} terminal · {formatNumber(failure.recovered_runs)} recovered</div>
          </div>
          <span className="shrink-0 rounded-full bg-white/80 px-2 py-1 text-[10px] font-semibold uppercase tracking-[.08em] text-[#9a5b4d]">Top breakpoint</span>
        </div>
      </a>
      <a href="/issues" className="shrink-0 text-xs font-semibold text-[#603bd1] hover:underline">
        See all issues →
      </a>
    </div>
  );
}

function GoalPortfolioGrid({ items }: { items: GoalPortfolioItem[] }) {
  if (!items.length) return <Empty>No business goals were reported in this view.</Empty>;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {items.map((item) => {
        const total = Math.max(item.runs, 1);
        const assuredEnd = (item.assured_runs / total) * 100;
        const attentionEnd = assuredEnd + (item.attention_runs / total) * 100;
        const missedEnd = attentionEnd + (item.not_achieved_runs / total) * 100;
        const href = drilldownHref("/goal-performance", { contract_hash: item.contract_hash || item.contract_hashes[0] });
        return (
          <a key={item.goal_id} href={href} className="group rounded-xl border border-[#e8e7e1] bg-[#fcfcfa] p-4 hover:border-[#cfc5ef] hover:bg-[#faf8ff]">
            <div className="flex items-start gap-4">
              <div
                className="relative grid size-16 shrink-0 place-items-center rounded-full"
                style={{ background: `conic-gradient(#24a267 0 ${assuredEnd}%, #ed9b2d ${assuredEnd}% ${attentionEnd}%, #d95858 ${attentionEnd}% ${missedEnd}%, #9aa1ad ${missedEnd}% 100%)` }}
              >
                <div className="absolute inset-[7px] rounded-full bg-white" />
                <div className="relative text-center"><div className="text-sm font-bold">{formatNumber(item.runs)}</div><div className="text-[9px] text-[#777]">runs</div></div>
              </div>
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 text-sm font-semibold group-hover:text-[#603bd1]">{item.goal_name} →</div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div><span className="font-semibold text-[#16864b]">{percent(item.success_rate)}</span><div className="text-[10px] text-[#85857f]">achieved</div></div>
                  <div><span className={item.attention_runs ? "font-semibold text-[#a96108]" : "font-semibold text-[#16864b]"}>{percent(item.assurance_rate)}</span><div className="text-[10px] text-[#85857f]">assured</div></div>
                </div>
              </div>
            </div>
            {item.top_attention && <div className="mt-3 truncate rounded-md bg-[#fff5e8] px-2.5 py-2 text-[11px] text-[#8a570e]">Attention: {item.top_attention.name}</div>}
          </a>
        );
      })}
    </div>
  );
}

function GoalAttentionQueue({ data }: { data: Overview }) {
  const checks = data.goal_portfolio
    .filter((item) => item.top_attention)
    .sort((a, b) => (b.top_attention?.attention_runs || 0) - (a.top_attention?.attention_runs || 0));
  if (!checks.length && !data.goal_misses.length) return <Empty>No goal checks or blockers need attention.</Empty>;
  return (
    <div className="space-y-2">
      {checks.slice(0, 4).map((item) => (
        <a key={item.goal_id} href={drilldownHref("/goal-performance", { contract_hash: item.contract_hash || item.contract_hashes[0] })} className="block rounded-lg bg-[#fff7e9] p-3 hover:bg-[#fff1d8]">
          <div className="flex items-start justify-between gap-3"><div className="text-sm font-semibold">{item.goal_name}</div><Badge color="yellow">{formatNumber(item.top_attention?.attention_runs)} runs</Badge></div>
          <div className="mt-1 text-xs text-[#7a5b2c]">{item.top_attention?.name}: average {formatNumber(item.top_attention?.average_score)} · target {String(item.top_attention?.target ?? "not declared")}</div>
        </a>
      ))}
      {data.goal_misses.slice(0, Math.max(2, 5 - checks.length)).map((item) => (
        <div key={item.reason} className="rounded-lg bg-[#fff2f2] p-3">
          <div className="flex items-start justify-between gap-3"><div className="text-sm font-semibold">{item.reason}</div><Badge color="red">{formatNumber(item.runs)} runs</Badge></div>
          <div className="mt-1 text-xs text-[#846767]">{seconds(item.time_seconds)} observed · {money(item.known_cost)}</div>
        </div>
      ))}
    </div>
  );
}

function GoalBreakdownList({
  items,
  dimension,
  destination,
}: {
  items: ComparisonInsight[];
  dimension: "model" | "provider";
  destination: string;
}) {
  const shown = [...items]
    .filter((item) => item.goal_rate != null)
    .sort((a, b) => (b.goal_rate || 0) - (a.goal_rate || 0) || b.runs - a.runs)
    .slice(0, 8);
  if (!shown.length) return <Empty>No goal outcomes are attributable in this view.</Empty>;
  return (
    <div className="space-y-3">
      {shown.map((item) => {
        const label = dimension === "provider" ? providerDisplayName(item.label) : item.label;
        return (
          <a
            key={item.label}
            href={drilldownHref(destination, { [dimension]: item.label })}
            className="group grid gap-2 rounded-lg border border-transparent px-2 py-2 hover:border-[#e3ddf7] hover:bg-[#faf8ff] sm:grid-cols-[minmax(150px,1fr)_minmax(180px,2fr)_74px_74px] sm:items-center"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold group-hover:text-[#603bd1]">{label}</div>
              <div className="text-[11px] text-[#777]">{formatNumber(item.runs)} runs</div>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-[#ecebe7]">
              <div className="h-full rounded-full bg-[#6d4aff]" style={{ width: `${Math.max(2, (item.goal_rate || 0) * 100)}%` }} />
            </div>
            <div className="text-xs"><span className="font-semibold">{percent(item.goal_rate)}</span><div className="text-[10px] text-[#888]">achieved</div></div>
            <div className="text-xs"><span className="font-semibold">{percent(item.decision_correctness_rate)}</span><div className="text-[10px] text-[#888]">decision</div></div>
          </a>
        );
      })}
    </div>
  );
}

function SystemBreakdownList({
  items,
  dimension,
  destination,
}: {
  items: Performance[];
  dimension: "model" | "provider";
  destination: string;
}) {
  const shown = [...items].sort((a, b) => b.runs - a.runs).slice(0, 8);
  if (!shown.length) return <Empty>No operational participants in this view.</Empty>;
  return (
    <div className="space-y-3">
      {shown.map((item) => {
        const reliability = item.runs ? Math.max(0, 1 - item.failure_rate) : 0;
        const label = dimension === "provider" ? providerDisplayName(item.label) : item.label;
        return (
          <a
            key={item.label}
            href={drilldownHref(destination, { [dimension]: item.label })}
            className="group grid gap-2 rounded-lg border border-transparent px-2 py-2 hover:border-[#e3ddf7] hover:bg-[#faf8ff] sm:grid-cols-[minmax(140px,1fr)_minmax(150px,1.5fr)_72px_82px] sm:items-center"
          >
            <div className="min-w-0"><div className="truncate text-sm font-semibold group-hover:text-[#603bd1]">{label}</div><div className="text-[11px] text-[#777]">{formatNumber(item.runs)} runs</div></div>
            <div className="h-2.5 overflow-hidden rounded-full bg-[#ecebe7]"><div className={`h-full rounded-full ${item.failure_rate ? "bg-[#e38317]" : "bg-[#24a267]"}`} style={{ width: `${Math.max(2, reliability * 100)}%` }} /></div>
            <div className="text-xs"><span className="font-semibold">{percent(reliability)}</span><div className="text-[10px] text-[#888]">reliable</div></div>
            <div className="text-xs"><span className="font-semibold">{money(item.measured_cost)}</span><div className="text-[10px] text-[#888]">measured</div></div>
          </a>
        );
      })}
    </div>
  );
}

function GoalPortfolio({ items, destination, compact = false }: { items: GoalPortfolioItem[]; destination?: string; compact?: boolean }) {
  if (!items.length) return <Empty>No business goals were reported in this view.</Empty>;
  return (
    <div className="space-y-3">
      {items.map((item) => {
        const total = Math.max(item.runs, 1);
        const segments = [
          { label: "Assured", count: item.assured_runs, color: "bg-[#24a267]" },
          { label: "Achieved · attention", count: item.attention_runs, color: "bg-[#ed9b2d]" },
          { label: "Not achieved", count: item.not_achieved_runs, color: "bg-[#d95858]" },
          { label: "Unassessed", count: item.unassessed_runs, color: "bg-[#9aa1ad]" },
        ];
        return (
          <div key={item.goal_id} className="rounded-xl border border-[#e9e8e2] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {destination ? (
                  <a href={drilldownHref(destination, { contract_hash: item.contract_hash || item.contract_hashes[0] })} className="text-sm font-semibold text-[#3d2b76] hover:text-[#6d4aff]">{item.goal_name} →</a>
                ) : <div className="text-sm font-semibold">{item.goal_name}</div>}
                {!compact && item.description && <div className="mt-1 max-w-3xl text-xs leading-5 text-[#71716b]">{item.description}</div>}
              </div>
              <div className="flex gap-4 text-right text-xs">
                <div><div className="font-semibold text-[#333]">{percent(item.success_rate)}</div><div className="text-[#7a7a74]">achieved</div></div>
                <div><div className="font-semibold text-[#333]">{percent(item.assurance_rate)}</div><div className="text-[#7a7a74]">assured</div></div>
                <div><div className="font-semibold text-[#333]">{formatNumber(item.runs)}</div><div className="text-[#7a7a74]">runs · {formatNumber(item.contract_count)} contract{item.contract_count === 1 ? "" : "s"}</div></div>
              </div>
            </div>
            <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-[#efefeb]" aria-label={`${item.goal_name} assurance breakdown`}>
              {segments.map((segment) => segment.count > 0 && (
                <div
                  key={segment.label}
                  title={`${segment.label}: ${segment.count}`}
                  className={segment.color}
                  style={{ width: `${(segment.count / total) * 100}%` }}
                />
              ))}
            </div>
            {!compact && <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#686862]">
              {segments.map((segment) => (
                <span key={segment.label} className="inline-flex items-center gap-1.5">
                  <span className={`size-2 rounded-sm ${segment.color}`} />
                  {segment.label} {formatNumber(segment.count)}
                </span>
              ))}
              <span>Assessment coverage {percent(item.assessment_coverage)}</span>
            </div>}
            {!compact && item.top_attention && (
              <div className="mt-3 rounded-lg bg-[#fff8ec] px-3 py-2 text-xs text-[#8a570e]">
                <span className="font-semibold">Needs attention:</span> {item.top_attention.name} averaged {item.top_attention.average_score == null ? "an unscored result" : formatNumber(item.top_attention.average_score)}{item.top_attention.target == null ? "" : ` against a target of ${String(item.top_attention.target)}`} and missed in {formatNumber(item.top_attention.attention_runs)} run{item.top_attention.attention_runs === 1 ? "" : "s"}.
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ModeButton({ active, onClick, children }: React.PropsWithChildren<{ active: boolean; onClick: () => void }>) {
  return <button onClick={onClick} className={`rounded-md px-5 py-2 text-sm font-semibold ${active ? "bg-white text-[#5a35c8] shadow-sm" : "text-[#666]"}`}>{children}</button>;
}

function FilterSelect({ value, onChange, label, includeEmpty = true, children }: React.PropsWithChildren<{ value: string; onChange: (value: string) => void; label: string; includeEmpty?: boolean }>) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} className="max-w-[220px] rounded-lg border border-[#dddcd6] bg-white px-3 py-2 text-xs text-[#555]">
      {includeEmpty && <option value="">{label}</option>}
      {!includeEmpty && <option value="all">{label}</option>}
      {children}
    </select>
  );
}

type SharedFilterValues = {
  contractHash: string;
  provider: string;
  model: string;
  status: string;
  range: string;
};
const EMPTY_FILTERS: SharedFilterValues = {
  contractHash: "",
  provider: "",
  model: "",
  status: "",
  range: "all",
};
const resolvedFilters = (values: SharedFilterValues): DashboardFilters => ({
  contract_hash: values.contractHash || undefined,
  provider: values.provider || undefined,
  model: values.model || undefined,
  status: values.status || undefined,
  start_date:
    values.range === "all"
      ? undefined
      : browserDateDaysAgo(Number(values.range)),
});
function SharedFilterBar({ metadata, values, onChange, includeGoal = true }: { metadata: Meta; values: SharedFilterValues; onChange: (values: SharedFilterValues) => void; includeGoal?: boolean }) {
  const set = (key: keyof SharedFilterValues, value: string) => onChange({ ...values, [key]: value });
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-[#e4e2da] bg-white p-3">
      <span className="mr-1 text-xs font-semibold text-[#555]">Filter this view</span>
      {includeGoal && (
        <FilterSelect value={values.contractHash} onChange={(value) => set("contractHash", value)} label="All business goals">
          {metadata.contracts.map((item) => <option key={item.contract_hash} value={item.contract_hash}>{item.product_goal?.name || item.contract_name}</option>)}
        </FilterSelect>
      )}
      <FilterSelect value={values.provider} onChange={(value) => set("provider", value)} label="All providers">
        {(metadata.filters.provider || []).map((value) => <option key={value} value={value}>{providerDisplayName(value)}</option>)}
      </FilterSelect>
      <FilterSelect value={values.model} onChange={(value) => set("model", value)} label="All models">
        {(metadata.filters.model || []).map((value) => <option key={value} value={value}>{value}</option>)}
      </FilterSelect>
      <FilterSelect value={values.status} onChange={(value) => set("status", value)} label="All runtime states">
        <option value="completed">Completed or recovered</option>
        <option value="failed">Failed</option>
        <option value="running">Running</option>
      </FilterSelect>
      <FilterSelect value={values.range} onChange={(value) => set("range", value)} label="All time" includeEmpty={false}>
        <option value="1">Last 24 hours</option>
        <option value="7">Last 7 days</option>
        <option value="30">Last 30 days</option>
      </FilterSelect>
    </div>
  );
}

function ContractBanner({ contract }: { contract: ContractDefinition }) {
  return (
    <div className="mb-4 rounded-xl border border-[#ded7f8] bg-[#f5f2ff] px-5 py-4">
      <div className="text-xs font-semibold uppercase tracking-[.1em] text-[#7151cc]">Active business goal</div>
      <div className="mt-1 text-lg font-semibold">{contract.product_goal?.name || contract.contract_name}</div>
      <div className="mt-1 max-w-4xl text-sm leading-6 text-[#65616e]">{contract.product_goal?.description || contract.contract?.description}</div>
    </div>
  );
}

function ContractCard({ contract }: { contract: ContractDefinition }) {
  return (
    <div className="rounded-lg border border-[#eceae4] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-semibold">{contract.product_goal?.name || contract.contract_name}</div>
        <Badge color="gray">{formatNumber(contract.run_count)} runs</Badge>
      </div>
      <div className="mt-1 text-xs leading-5 text-[#70706a]">{contract.product_goal?.description || contract.contract?.description}</div>
      <div className="mt-2 text-xs text-[#5a35c8]">Result: {contract.result?.name || "Reported result"}</div>
    </div>
  );
}

function AttentionPanel({ data }: { data: Overview }) {
  const measurementMessages = measurementAttentionMessages(data);
  return (
    <Panel title="What needs attention" note="Concrete breakpoints and missing measurements.">
      {data.failures.length ? (
        <div className="space-y-3">
          {data.failures.slice(0, 6).map((failure) => (
            <div key={failure.failure_location} className="flex items-center justify-between rounded-lg bg-[#fff5f5] p-3">
              <div>
                <div className="text-sm font-medium">{failure.failure_location}</div>
                <div className="text-xs text-[#777]">{formatNumber(failure.recovered_runs)} recovered · failed operation {seconds(failure.time_seconds)} · {money(failure.known_cost)}</div>
                <div className="mt-0.5 text-[10px] text-[#999]">Affected-run exposure: {seconds(failure.affected_run_time_seconds)} · {money(failure.affected_run_cost)}</div>
              </div>
              <Badge color={failure.terminal_runs ? "red" : "green"}>
                {failure.terminal_runs
                  ? `${formatNumber(failure.terminal_runs)} failed`
                  : `${formatNumber(failure.recovered_runs)} recovered`}
              </Badge>
            </div>
          ))}
        </div>
      ) : measurementMessages.length ? (
        <div className="space-y-2">
          {measurementMessages.map((message) => <div key={message} className="rounded-lg bg-amber-50 p-3 text-sm text-[#80520e]">{message}</div>)}
        </div>
      ) : Object.keys(data.cost_unavailable).length ? (
        <div className="space-y-2">{Object.entries(data.cost_unavailable).map(([reason, count]) => <div key={reason} className="rounded-lg bg-amber-50 p-3 text-sm">{reason.replaceAll("_", " ")} · {formatNumber(count)} runs</div>)}</div>
      ) : <Empty>No failures or incomplete applicable measurements in this view.</Empty>}
    </Panel>
  );
}

export function measurementAttentionMessages(data: Overview): string[] {
  return ([
    ["Cost", data.costs.cost],
    ["Token", data.costs.tokens],
  ] as const).flatMap(([label, coverage]) => {
    if (coverage.partial_runs + coverage.missing_runs === 0) return [];
    return [`${label} measurement is incomplete for ${formatNumber(coverage.partial_runs)} partial and ${formatNumber(coverage.missing_runs)} unmeasured applicable runs.`];
  });
}

export function RunsPage() {
  const [filterValues, setFilterValues] = useState<SharedFilterValues>(() => ({
    ...EMPTY_FILTERS,
    contractHash: routeParam("contract_hash"),
    provider: routeParam("provider"),
    model: routeParam("model"),
    status: routeParam("status"),
  }));
  const [page, setPage] = useState(1);
  const workflow = routeParam("workflow");
  const workflowId = routeParam("workflow_id");
  const unavailableReplay = routeParam("unavailable_replay");
  const filters = { ...resolvedFilters(filterValues), workflow: workflow || undefined, workflow_id: workflowId || undefined };
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({ queryKey: ["runs", workflow, workflowId, filterValues, page], queryFn: () => api.runs(filters, page, 10) });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  return (
    <>
      <PageHeader
        title="All executions"
        description="Open executions that are associated with an authored YAML workflow contract."
      />
      {unavailableReplay ? (
        <div className="mb-4 flex items-center justify-between rounded-xl border border-[#e8dfc6] bg-[#fffaf0] px-4 py-3 text-sm">
          <div>
            <span className="font-semibold text-[#80520e]">No workflow replay</span>
            <span className="ml-2 text-[#756d60]">This execution has no associated YAML workflow contract.</span>
          </div>
          <a href="/runs" className="text-xs font-semibold text-[#5c35c8] hover:underline">Dismiss</a>
        </div>
      ) : null}
      {workflow || workflowId ? <div className="mb-4 flex items-center justify-between rounded-xl border border-[#dcd5ef] bg-[#f7f4ff] px-4 py-3 text-sm"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold text-[#4e348c]">Workflow filter</span><span className="text-[#6f6877]">{workflow || workflowId}</span>{filterValues.model ? <Badge color="purple">Model · {filterValues.model}</Badge> : null}{filterValues.provider ? <Badge color="purple">Provider · {filterValues.provider}</Badge> : null}</div><a href="/runs" className="text-xs font-semibold text-[#5c35c8] hover:underline">Clear filters</a></div> : null}
      <SharedFilterBar metadata={meta.data!} values={filterValues} onChange={(values) => { setFilterValues(values); setPage(1); }} />
      <RunsTable rows={q.data!.items} count={q.data!.count} />
      <div className="mt-4 flex items-center justify-between text-sm">
        <span className="text-[#74746e]">Page {q.data!.page} of {q.data!.pages} · {formatNumber(q.data!.count)} runs</span>
        <div className="flex gap-2">
          <button disabled={q.data!.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} className="rounded-lg border bg-white px-4 py-2 font-medium disabled:cursor-not-allowed disabled:opacity-40">Previous</button>
          <button disabled={q.data!.page >= q.data!.pages} onClick={() => setPage((value) => value + 1)} className="rounded-lg border bg-white px-4 py-2 font-medium disabled:cursor-not-allowed disabled:opacity-40">Next</button>
        </div>
      </div>
    </>
  );
}
function RunsTable({ rows, count }: { rows: Run[]; count: number }) {
  return (
    <Panel
      title={`${formatNumber(count)} runs`}
      note="Newest first · YAML-backed executions open their canonical workflow replay"
    >
      <div className="space-y-2">
        {rows.map((run) => (
          <ExecutionListCard
            key={run.execution_id}
            run={run}
            href={run.canonical_url || undefined}
          />
        ))}
      </div>
    </Panel>
  );
}

type SemanticRecord = Record<string, unknown> & {
  kind?: string;
  name?: string;
  score?: number;
  label?: string;
  value?: unknown;
  attributes?: Record<string, unknown>;
};

export const evaluationMetTarget = (record: SemanticRecord): boolean | null => {
  const attributes = record.attributes || {};
  if (typeof attributes.passed === "boolean") return attributes.passed;
  const score = typeof record.score === "number" ? record.score : typeof attributes.score === "number" ? attributes.score : typeof record.value === "number" ? record.value : null;
  const target = attributes.target;
  const direction = String(attributes.direction || "equal");
  if (score != null && typeof target === "number") {
    if (["lower_is_better", "max", "at_most", "<="].includes(direction)) return score <= target;
    if (["higher_is_better", "min", "at_least", ">="].includes(direction)) return score >= target;
    return score === target;
  }
  const observed = record.value ?? record.label ?? attributes.label;
  if (target != null && observed != null) return observed === target;
  return null;
};

export function ComparePage() {
  const [dimension, setDimension] = useState<"model" | "provider">("model");
  const [filterValues, setFilterValues] = useState(EMPTY_FILTERS);
  const filters = resolvedFilters(filterValues);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({
    queryKey: ["compare", dimension, filterValues],
    queryFn: () => api.compare(dimension, filters),
  });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  const compareItems =
    dimension === "provider"
      ? q.data!.items.map((item) => ({
          ...item,
          label: providerDisplayName(item.label),
        }))
      : q.data!.items;
  return (
    <>
      <PageHeader
        title="Compare"
        description="See which providers and models deliver the result you need at the time and cost you can accept."
        action={
          <div className="flex rounded-lg bg-[#ecebe7] p-1">
            <ModeButton active={dimension === "model"} onClick={() => setDimension("model")}>By model</ModeButton>
            <ModeButton active={dimension === "provider"} onClick={() => setDimension("provider")}>By provider</ModeButton>
          </div>
        }
      />
      <SharedFilterBar metadata={meta.data!} values={filterValues} onChange={setFilterValues} />
      <Panel title={`${dimension === "model" ? "Models" : "Providers"}: attributable speed and spend`} note="Each point uses only the model calls, tokens, cost, and elapsed model time attributable to that participant.">
        <CostLatencyScatter items={compareItems} />
      </Panel>
      <Panel className="mt-4" title="Trade-offs relative to the selected median" note="1× is the median. Lower time, cost, tokens, and calls indicate greater efficiency; use sample size and goal rate from the scatter tooltip before choosing.">
        <NormalizedComparisonChart items={compareItems} />
      </Panel>
      <Panel className="mt-4" title="Quality against the declared target" note="Only contract-defined evaluations are shown; use the business-goal filter when evaluation definitions differ.">
        <QualityComparisonChart items={compareItems} />
      </Panel>
      <Panel className="mt-4" title="Typical latency and tail risk" note="The darker segment is p50; the lighter extension shows the distance from p50 to p95.">
        <LatencyVariabilityChart items={compareItems} />
      </Panel>
    </>
  );
}
export function WorkflowsPage() {
  const [filterValues, setFilterValues] = useState(EMPTY_FILTERS);
  const filters = resolvedFilters(filterValues);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({ queryKey: ["workflows", filterValues], queryFn: () => api.workflows(filters) });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  return (
    <>
      <PageHeader
        title="Workflows"
        description="Understand which workflows carry the work and which paths are slow, expensive, or fragile."
      />
      <SharedFilterBar metadata={meta.data!} values={filterValues} onChange={setFilterValues} />
      <Panel title="Workflow portfolio" note="Canonical runtimes only; wrapper spans are not counted as separate workflows. At most 10 workflows are shown.">
        <CostLatencyScatter items={q.data!.items} xLabel="End-to-end time / run (seconds)" />
      </Panel>
      <Panel className="mt-4" title="Where workflow resources accumulate" note="Semantic stage contribution across the selected workflows; switch between elapsed stage time, measured cost, and tokens.">
        <WorkflowStageContribution items={q.data!.stages} />
      </Panel>
      <Panel className="mt-4" title="Observed path variants" note="Compact semantic paths replace the low-level loop graph. Repeated adjacent stages and wrapper/model-call noise are removed.">
        {q.data!.paths.length ? <div className="overflow-x-auto"><table className="min-w-[980px] w-full text-left text-sm"><thead className="border-b text-xs text-[#777]"><tr><th className="pb-3">Workflow and path</th><th className="pb-3">Runs</th><th className="pb-3">p50 / p95</th><th className="pb-3">Cost / run</th><th className="pb-3">Retries</th><th className="pb-3">Recovered</th></tr></thead><tbody className="divide-y">{q.data!.paths.map((path, index) => <tr key={`${path.runtime_id}-${index}`}><td className="max-w-[560px] py-4 pr-5"><div className="font-medium">{path.workflow}</div><div className="mt-1 text-xs leading-5 text-[#74746e]">{path.steps.join(" → ")}</div></td><td>{formatNumber(path.runs)}</td><td>{seconds(path.p50_duration_seconds)} / {seconds(path.p95_duration_seconds)}</td><td>{money(path.avg_cost_per_run)}</td><td>{formatNumber(path.retries)}</td><td>{formatNumber(path.recovered_runs)}</td></tr>)}</tbody></table></div> : <Empty>No semantic paths were reported.</Empty>}
      </Panel>
    </>
  );
}
export function IssuesPage() {
  const [filterValues, setFilterValues] = useState(EMPTY_FILTERS);
  const filters = resolvedFilters(filterValues);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({ queryKey: ["issues", filterValues], queryFn: () => api.issues(filters) });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  const data = q.data!;
  const affectedRetryRuns = new Set(data.retries.flatMap((retry) => retry.runs.map((run) => run.execution_id))).size;
  const runSignals = data.failures.length + data.quality_gaps.length + data.outliers.length;
  const telemetrySignals = data.operation_failures.length + data.missing_required_measurements.length;
  const operationFailures = data.operation_failures.reduce((total, item) => total + item.failed, 0);
  const hasFailure = data.summary.terminal_failures > 0 || operationFailures > 0;
  const hasAttention = runSignals + telemetrySignals > 0 || affectedRetryRuns > 0;
  const headline = hasFailure
    ? `${formatNumber(data.summary.terminal_failures + operationFailures)} failures need attention`
    : hasAttention
      ? "No failures. Efficiency and telemetry still need review."
      : "No active issues in this population.";
  const summary = [
    data.outliers.length ? `${formatNumber(data.outliers.length)} p95 outlier run${data.outliers.length === 1 ? "" : "s"}` : null,
    affectedRetryRuns ? `retries affected ${formatNumber(affectedRetryRuns)} run${affectedRetryRuns === 1 ? "" : "s"}` : null,
    data.missing_required_measurements.length ? `${formatNumber(data.missing_required_measurements.length)} required measurement gap${data.missing_required_measurements.length === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(" · ") || "No failure, quality, retry, outlier, or required-measurement signals were found.";
  return (
    <>
      <PageHeader
        title="Issues"
        description="A prioritized investigation queue for failures, quality regressions, retry pressure, resource outliers, and missing evidence."
      />
      <SharedFilterBar metadata={meta.data!} values={filterValues} onChange={setFilterValues} />
      <section className={`mb-4 overflow-hidden rounded-xl border ${hasFailure ? "border-red-200 bg-red-50/50" : hasAttention ? "border-amber-200 bg-[#fffdf7]" : "border-emerald-200 bg-emerald-50/40"}`}>
        <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(440px,.75fr)] lg:items-center">
          <div className="min-w-0">
            <div className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[.1em] ${hasFailure ? "bg-red-100 text-red-700" : hasAttention ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-700"}`}><span className={`size-1.5 rounded-full ${hasFailure ? "bg-red-500" : hasAttention ? "bg-amber-500" : "bg-emerald-500"}`} />Current assessment</div>
            <h2 className="mt-3 text-xl font-semibold tracking-[-.02em] text-[#302d33]">{headline}</h2>
            <p className="mt-1.5 max-w-3xl text-xs leading-5 text-[#77716f]">{summary}</p>
          </div>
          <dl className="grid grid-cols-2 overflow-hidden rounded-lg border border-black/5 bg-white/80 sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4">
            <IssueFact label="Runs" value={formatNumber(data.summary.runs)} />
            <IssueFact label="Run signals" value={formatNumber(runSignals)} />
            <IssueFact label="Retry runs" value={formatNumber(affectedRetryRuns)} />
            <IssueFact label="Telemetry gaps" value={formatNumber(telemetrySignals)} />
          </dl>
        </div>
      </section>
      <Panel title="Prioritized investigations" note="The most consequential signals first. Open a run to inspect its YAML-backed execution replay.">
        <IssueRunQueue data={data} />
      </Panel>
      <div className="mt-4 grid gap-4 xl:grid-cols-12 xl:items-start">
        <Panel className="xl:col-span-7" title="Retry concentration" note="Extra attempts are grouped by the operation that retried; expand a row for its affected executions.">
          <RetryHotspotList data={data} />
        </Panel>
        <Panel className="xl:col-span-5" title="Measurement visibility" note="Run-level reporting availability. Required operation-level gaps are surfaced in the investigation queue.">
          <div className="space-y-5">
            <CoverageMeter label="Measured cost" value={data.measurement.cost} total={data.measurement.total} />
            <CoverageMeter label="Token usage" value={data.measurement.tokens} total={data.measurement.total} />
            <CoverageMeter label="Business goal" value={data.measurement.business_goal} total={data.measurement.total} />
          </div>
          <div className="mt-5 border-t border-[#ece9e4] pt-4 text-[10px] leading-4 text-[#85807e]">Availability is not the same as applicability. Optional meters and operations without billable activity are not treated as failures.</div>
        </Panel>
      </div>
    </>
  );
}

const humanizeOperation = (value: string) => value.replace(/^x\.[^.]+\./, "").replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());

function IssueFact({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 border-l border-[#ece9e4] px-3 py-3 first:border-l-0 lg:[&:nth-child(3)]:border-l-0 xl:[&:nth-child(3)]:border-l">
    <dt className="text-[8px] font-semibold uppercase tracking-[.12em] text-[#938e8b]">{label}</dt>
    <dd className="mt-1 text-lg font-semibold text-[#38333a]">{value}</dd>
  </div>;
}

type IssueQueueItem = {
  key: string;
  category: "failures" | "quality" | "efficiency" | "telemetry";
  executionId?: string;
  title: string;
  detail: string;
  signal: string;
  metrics: string[];
  tone: "red" | "amber" | "violet" | "green";
  priority: number;
};

function IssueRunQueue({ data }: { data: Issues }) {
  const [category, setCategory] = useState<"all" | IssueQueueItem["category"]>("all");
  const outlierReason = (reason: string) => reason === "duration_seconds" ? "Latency above p95" : reason === "known_cost" ? "Cost above p95" : reason === "total_tokens" ? "Token usage above p95" : reason.replaceAll("_", " ");
  const meterLabel = (value: string) => value.split(".").map((part) => part.replaceAll("_", " ")).join(" · ");
  const items: IssueQueueItem[] = [
    ...data.failures.map((failure) => ({
      key: `failure-${failure.execution_id}`,
      category: "failures" as const,
      executionId: failure.execution_id,
      title: `${failure.display_name || "Execution"} ${failure.runtime_outcome === "recovered" ? "recovered after a failure" : "failed"}`,
      detail: `${failure.failure_location} · execution ${failure.execution_id.slice(0, 12)}`,
      signal: failure.runtime_outcome === "recovered" ? "Recovered" : "Terminal failure",
      metrics: [seconds(failure.duration_seconds), money(failure.known_cost)],
      tone: failure.runtime_outcome === "recovered" ? "green" as const : "red" as const,
      priority: failure.runtime_outcome === "recovered" ? 1 : 0,
    })),
    ...data.quality_gaps.map((gap) => ({
      key: `quality-${gap.execution_id}-${gap.name}`,
      category: "quality" as const,
      executionId: gap.execution_id,
      title: `${gap.name} missed its declared target`,
      detail: `${gap.display_name || "Execution"} · execution ${gap.execution_id.slice(0, 12)}`,
      signal: "Below target",
      metrics: [`Observed ${formatNumber(gap.score)}`, `Target ${formatNumber(gap.target)}`, gap.direction.replaceAll("_", " ")],
      tone: "amber" as const,
      priority: 1,
    })),
    ...data.outliers.map((run) => ({
      key: `outlier-${run.execution_id}`,
      category: "efficiency" as const,
      executionId: run.execution_id,
      title: run.reasons.length > 1 ? `${run.display_name || "Execution"} is an outlier across ${run.reasons.length} resource signals` : `${run.display_name || "Execution"} has ${outlierReason(run.reasons[0] || "a p95 outlier").toLowerCase()}`,
      detail: `${run.reasons.map(outlierReason).join(" · ")} · execution ${run.execution_id.slice(0, 12)}`,
      signal: "Resource outlier",
      metrics: [seconds(run.duration_seconds), money(run.known_cost), run.total_tokens == null ? "Tokens not measured" : `${formatNumber(run.total_tokens)} tokens`],
      tone: "violet" as const,
      priority: 2,
    })),
    ...data.operation_failures.map((item) => ({
      key: `operation-${item.type}`,
      category: "failures" as const,
      title: `${humanizeOperation(item.type)} operations failed`,
      detail: "Direct operation failures, independent of the final execution outcome.",
      signal: "Operation failure",
      metrics: [`${formatNumber(item.failed)} failed`, `${formatNumber(item.operations)} observed`],
      tone: "red" as const,
      priority: 0,
    })),
    ...data.missing_required_measurements.map((item) => ({
      key: `meter-${item.operation_type}-${item.measurement_key}`,
      category: "telemetry" as const,
      title: `${humanizeOperation(item.operation_type)} is missing ${meterLabel(item.measurement_key)}`,
      detail: "A required operation measurement was applicable but not reported.",
      signal: "Missing evidence",
      metrics: [`${formatNumber(item.operations)} operation${item.operations === 1 ? "" : "s"}`, `${formatNumber(item.executions)} execution${item.executions === 1 ? "" : "s"}`],
      tone: "amber" as const,
      priority: 1,
    })),
  ].sort((left, right) => left.priority - right.priority || left.title.localeCompare(right.title));
  const visible = category === "all" ? items : items.filter((item) => item.category === category);
  if (!items.length) return <Empty>No failures, quality gaps, resource outliers, or required-measurement gaps in this population.</Empty>;
  const toneClasses = {
    red: "border-red-200 bg-red-50 text-red-700",
    amber: "border-amber-200 bg-amber-50 text-amber-800",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  };
  const categories: Array<{ id: "all" | IssueQueueItem["category"]; label: string }> = [{ id: "all", label: "All" }, { id: "failures", label: "Failures" }, { id: "quality", label: "Quality" }, { id: "efficiency", label: "Efficiency" }, { id: "telemetry", label: "Telemetry" }];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Issue category">
          {categories.map((item) => {
            const count = item.id === "all" ? items.length : items.filter((candidate) => candidate.category === item.id).length;
            return <button key={item.id} type="button" role="tab" aria-selected={category === item.id} onClick={() => setCategory(item.id)} className={`rounded-md px-2.5 py-1.5 text-[10px] font-semibold transition ${category === item.id ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#69645f] hover:bg-[#ebe8f2]"}`}>{item.label} <span className="ml-1 opacity-75">{count}</span></button>;
          })}
        </div>
        <span className="text-[10px] text-[#8a8581]">Highest priority first</span>
      </div>
      <div className="divide-y divide-[#ecebe6] overflow-hidden rounded-xl border border-[#ecebe6]">
        {visible.map((item) => {
          const content = <><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className={`w-fit shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-semibold ${toneClasses[item.tone]}`}>{item.signal}</span><span className="text-sm font-semibold text-[#373239] group-hover:text-[#5836b0]">{item.title}</span></div><div className="mt-1.5 text-[10px] leading-4 text-[#7b7578]">{item.detail}</div></div><div className="flex min-w-0 flex-wrap items-center gap-1.5 lg:justify-end">{item.metrics.map((metric) => <span key={metric} className="rounded-md bg-[#f4f2f5] px-2 py-1 text-[9px] font-medium text-[#5e5861]">{metric}</span>)}{item.executionId ? <span className="ml-1 text-[10px] font-semibold text-[#5c35c8]">Open replay →</span> : null}</div></>;
          return item.executionId ? <a key={item.key} href={`/runs/${encodeURIComponent(item.executionId)}`} className="group grid min-w-0 gap-3 bg-white px-4 py-3.5 transition hover:bg-[#faf8ff] lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">{content}</a> : <div key={item.key} className="group grid min-w-0 gap-3 bg-white px-4 py-3.5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">{content}</div>;
        })}
      </div>
      {!visible.length ? <div className="rounded-xl border border-dashed border-[#dedbe0] px-4 py-8 text-center text-xs text-[#817b80]">No {categories.find((item) => item.id === category)?.label.toLowerCase()} issues in this population.</div> : null}
    </div>
  );
}

function RetryHotspotList({ data }: { data: Issues }) {
  if (!data.retries.length) return <Empty>No retries in this population.</Empty>;
  const totalAttempts = data.summary.extra_attempts;
  const affectedRuns = new Set(data.retries.flatMap((retry) => retry.runs.map((run) => run.execution_id))).size;
  const topShare = totalAttempts ? data.retries.slice(0, 2).reduce((total, retry) => total + retry.extra_attempts, 0) / totalAttempts : 0;
  return (
    <div>
      <div className="mb-3 grid grid-cols-3 overflow-hidden rounded-lg border border-[#ece8da] bg-[#fffdf7]">
        <RetryFact label="Extra attempts" value={formatNumber(totalAttempts)} />
        <RetryFact label="Affected runs" value={formatNumber(affectedRuns)} />
        <RetryFact label="Top two share" value={percent(topShare)} />
      </div>
      <div className="max-h-[390px] divide-y divide-[#ece8da] overflow-y-auto rounded-xl border border-[#ece8da] bg-white">
      {data.retries.map((retry, index) => (
        <details key={retry.label} className="group px-3 py-2.5 open:bg-[#fffaf0]">
          <summary className="cursor-pointer list-none">
            <div className="grid grid-cols-[24px_minmax(0,1fr)_70px_72px_20px] items-center gap-2 text-xs">
              <span className="text-center text-[10px] font-semibold text-[#9a7a3a]">{index + 1}</span>
              <span className="truncate font-semibold text-[#3d382f]" title={retry.label}>{retry.label}</span>
              <span className="text-right font-semibold text-[#9a6208]">+{formatNumber(retry.extra_attempts)}</span>
              <span className="text-right text-[10px] text-[#81786b]">{formatNumber(retry.affected_runs)} {retry.affected_runs === 1 ? "run" : "runs"}</span>
              <span className="text-[#6b4bbd] transition group-open:rotate-180">⌄</span>
            </div>
          </summary>
          <div className="ml-8 mt-2 max-h-40 space-y-1 overflow-y-auto border-l border-[#eadab8] pl-3">
            {retry.runs.map((run) => (
              <a
                key={run.execution_id}
                href={`/runs/${encodeURIComponent(run.execution_id)}`}
                className="block truncate rounded-md px-2 py-1.5 text-xs font-medium text-[#5a35c8] hover:bg-white hover:underline"
                title={run.display_name || run.execution_id}
              >
                {run.display_name || "Execution"} · {run.execution_id.slice(0, 12)}
              </a>
            ))}
          </div>
        </details>
      ))}
      </div>
    </div>
  );
}

function RetryFact({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0 border-l border-[#ece8da] px-3 py-2.5 first:border-l-0"><div className="text-[8px] font-semibold uppercase tracking-[.1em] text-[#9a8d75]">{label}</div><div className="mt-1 text-sm font-semibold text-[#4a4032]">{value}</div></div>;
}

function CoverageMeter({ label, value, total }: { label: string; value: number; total: number }) {
  const rate = total ? value / total : 0;
  return (
    <div className="min-w-0">
      <div className="flex items-end justify-between gap-3">
        <span className="text-xs font-medium text-[#666]">{label}</span>
        <span className="text-sm font-semibold">{formatNumber(value)} / {formatNumber(total)}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-[#eeede8]">
        <div className="h-full rounded-full bg-[#6d4aff]" style={{ width: `${Math.max(0, Math.min(100, rate * 100))}%` }} />
      </div>
      <div className="mt-1 text-right text-[10px] text-[#85857f]">{percent(rate)} coverage</div>
    </div>
  );
}
export function DeveloperPage() {
  return (
    <>
      <PageHeader
        title="Developer data"
        description="Raw records and API documentation live here so the decision views can stay focused."
      />
      <Panel title="Dashboard API">
        <p className="text-sm text-[#666]">
          Inspect the versioned read API and its exact response contracts.
        </p>
        <a href="/api/docs" target="_blank">
          <Button className="mt-4" variant="outline">
            Open API docs
          </Button>
        </a>
      </Panel>
    </>
  );
}
