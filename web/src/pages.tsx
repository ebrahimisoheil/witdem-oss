import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";
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
  formatNumber,
  Kpi,
  LoadingPage,
  LatencyVariabilityChart,
  money,
  NormalizedComparisonChart,
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
  WorkflowGraph,
  WorkflowStageContribution,
} from "./components";
import { contractOutcomeColors } from "./outcome-colors";

const providerDisplayName = (value: string) => {
  const known: Record<string, string> = {
    anthropic: "Anthropic",
    deepseek: "DeepSeek",
    mistral: "Mistral",
    ollama: "Ollama",
    openai: "OpenAI",
  };
  return (
    known[value.toLowerCase()] ||
    value.replace(/(^|[\s_-])\p{L}/gu, (match) => match.toUpperCase())
  );
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
  const runtimeAttention =
    d.execution.failed_runs + d.execution.recovered_runs + d.execution.running_runs;
  const completionRate = d.execution.total_runs
    ? (d.execution.successful_runs + d.execution.recovered_runs) / d.execution.total_runs
    : 0;
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
              <div><div className="text-2xl font-semibold text-[#ffc267]">{formatNumber(assurance.attention_runs)}</div><div className="text-[#bdb5cf]">achieved · attention</div></div>
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
            <div><div className="font-semibold text-[#333]">{seconds(d.execution.avg_duration_seconds)}</div><div className="mt-1 text-[#777]">average elapsed</div></div>
            <div><div className="font-semibold text-[#333]">{money(d.costs.measured_cost_per_run)}</div><div className="mt-1 text-[#777]">cost / run</div></div>
            <div><div className="font-semibold text-[#333]">{percent(d.execution.cost_coverage)}</div><div className="mt-1 text-[#777]">cost coverage</div></div>
          </div>
          <a href="/system-health" className="mt-5 inline-flex text-xs font-semibold text-[#603bd1]">Explore system health →</a>
        </section>
        <section className="flex min-w-0 flex-col rounded-2xl border border-[#ded7f3] bg-[#f8f5ff] p-6 shadow-[0_8px_30px_rgba(62,42,112,.06)]">
          <div className="text-xs font-semibold uppercase tracking-[.14em] text-[#7151cc]">Total spent</div>
          <div className="mt-3 break-words text-4xl font-semibold tracking-[-.04em] text-[#2f2450]">
            {money(d.costs.measured_cost)}
          </div>
          <div className="mt-2 text-sm leading-5 text-[#716a7f]">
            measured spend in the selected population
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3 border-t border-[#e5def6] pt-4 text-xs">
            <div>
              <div className="font-semibold text-[#3e3458]">{percent(d.execution.cost_coverage)}</div>
              <div className="mt-1 text-[#817a8d]">cost coverage</div>
            </div>
            <div>
              <div className="font-semibold text-[#3e3458]">{money(d.costs.measured_cost_per_run)}</div>
              <div className="mt-1 text-[#817a8d]">measured / run</div>
            </div>
          </div>
          <a href="/system-health" className="mt-auto pt-5 text-xs font-semibold text-[#603bd1]">Inspect spend →</a>
        </section>
      </div>
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
        <Panel className="h-full" title="Model goal trade-offs" note="Business-goal achievement versus measured cost. Bubble size is run volume; select a model to drill down.">
          <GoalTradeoffChart items={models.data!.items} onSelect={(item) => window.location.assign(drilldownHref("/goal-performance", { model: item.label }))} />
        </Panel>
        <Panel className="h-full" title="Model latency distribution" note="Typical latency and tail risk are operational signals. Select a model to inspect System Health.">
          <LatencyVariabilityChart height={360} items={models.data!.items} onSelect={(item) => window.location.assign(drilldownHref("/system-health", { model: item.label }))} />
        </Panel>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3 xl:items-stretch">
        <Panel className="h-full" title="Runtime state mix" note="Completed, recovered, failed, and still-running executions.">
          <RuntimeDonutChart height={310} data={d.runtime_breakdown} colors={{ completed: "#24a267", recovered: "#168e89", failed: "#d95858", running: "#2477e6", unknown: "#9aa1ad" }} />
        </Panel>
        <Panel className="h-full" title="Provider goal outcomes" note="Achievement and decision correctness by provider. Select a column to drill down.">
          <GoalRateColumns height={310} items={providers.data!.items.map((item) => ({ ...item, label: providerDisplayName(item.label) }))} onSelect={(item) => window.location.assign(drilldownHref("/goal-performance", { provider: item.label.toLowerCase() }))} />
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
      : new Date(Date.now() - Number(range) * 86_400_000)
          .toISOString()
          .slice(0, 10);
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
      : d.providers.map((item) => ({
          ...item,
          label: providerDisplayName(item.label),
        }));
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
              value={percent((d.execution.successful_runs + d.execution.recovered_runs) / d.execution.total_runs)}
              note={`${formatNumber(d.execution.recovered_runs)} recovered`}
              tone="good"
            />
            <Kpi
              label="Needs attention"
              value={formatNumber(d.execution.failed_runs + d.execution.recovered_runs + d.execution.running_runs)}
              note={`${formatNumber(d.execution.failed_runs)} failed · ${formatNumber(d.execution.recovered_runs)} recovered`}
              tone={d.execution.failed_runs ? "warn" : "neutral"}
            />
            <Kpi label="Average elapsed" value={seconds(d.execution.avg_duration_seconds)} />
            <Kpi
              label="Measured cost / run"
              value={money(d.costs.measured_cost_per_run)}
              note={`${formatNumber(Math.round(d.execution.cost_coverage * d.execution.total_runs))} of ${formatNumber(d.execution.total_runs)} measured`}
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
          <Panel className="mt-4" title="Where work accumulates" note="Workflow stages ranked by observed time, tokens, or measured cost.">
            <StageAccumulation items={d.stages} />
          </Panel>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 rounded-xl border border-[#e4e2da] bg-white px-4 py-3 text-xs text-[#666]">
            <span className="font-semibold text-[#333]">Telemetry coverage</span>
            <span>Cost: {formatNumber(Math.round(d.execution.cost_coverage * d.execution.total_runs))} of {formatNumber(d.execution.total_runs)} runs</span>
            <span>Tokens: {formatNumber(d.costs.token_runs)} of {formatNumber(d.execution.total_runs)} runs</span>
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
              label="Achieved · needs attention"
              value={formatNumber(d.assurance_summary.attention_runs)}
              note={`${formatNumber(d.assurance_summary.unassessed_runs)} achieved but unassessed`}
              tone={d.assurance_summary.attention_runs ? "warn" : "good"}
            />
            <Kpi label="Cost / achieved goal" value={money(d.goals.cost_per_achieved_goal)} />
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
          <Panel className="mt-3" title={`${breakdownLabel} runtime reliability`} note="Completion, recovery, and failure by operational participant.">
            <SystemBreakdownList items={breakdownItems} dimension={breakdown} destination="/system-health" />
          </Panel>
          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,.8fr)]">
            <Panel title={`${breakdownLabel} cost versus speed`} note="Bubble size shows run volume. Hover for exact values.">
              <CostSpeedChart items={breakdownItems} breakdown={breakdown} />
            </Panel>
            <Panel title={`${breakdownLabel} share of measured spend`} note="Share of measured spend. Hover for the exact amount.">
              <ProviderSpendChart items={breakdownItems} breakdown={breakdown} />
            </Panel>
          </div>
          <Panel className="mt-4" title={`${breakdownLabel} operational ranking`} note="Slowest and most expensive configurations in the selected system population.">
            <EconomicsBarChart items={breakdownItems} />
          </Panel>
        </>
      ) : (
        <>
          <div className="mt-3 grid gap-4 xl:grid-cols-2">
            <Panel title={`Goal outcomes by ${breakdown}`} note="Achievement and decision correctness; runtime completion is intentionally excluded.">
              <GoalRateColumns
                items={(goalComparison.data?.items || []).map((item) => breakdown === "provider" ? { ...item, label: providerDisplayName(item.label) } : item)}
                onSelect={(item) => breakdown === "model" ? setModel(item.label) : setProvider(item.label.toLowerCase())}
              />
            </Panel>
            <Panel title="Goal achievement versus measured cost" note="Higher is better vertically; lower measured cost is better horizontally. Bubble size is run volume.">
              <GoalTradeoffChart items={goalComparison.data?.items || []} />
            </Panel>
          </div>
          {selectedContract && (
            <Panel className="mt-4" title="Declared evaluation quality by participant" note="Average scores compared with targets declared by this selected business contract.">
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
      : new Date(Date.now() - Number(values.range) * 86_400_000)
          .toISOString()
          .slice(0, 10),
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
  return (
    <Panel title="What needs attention" note="Concrete breakpoints and missing measurements.">
      {data.failures.length ? (
        <div className="space-y-3">
          {data.failures.slice(0, 6).map((failure) => (
            <div key={failure.failure_location} className="flex items-center justify-between rounded-lg bg-[#fff5f5] p-3">
              <div>
                <div className="text-sm font-medium">{failure.failure_location}</div>
                <div className="text-xs text-[#777]">{formatNumber(failure.recovered_runs)} recovered · {money(failure.known_cost)}</div>
              </div>
              <Badge color={failure.terminal_runs ? "red" : "green"}>
                {failure.terminal_runs
                  ? `${formatNumber(failure.terminal_runs)} failed`
                  : `${formatNumber(failure.recovered_runs)} recovered`}
              </Badge>
            </div>
          ))}
        </div>
      ) : Object.keys(data.cost_unavailable).length ? (
        <div className="space-y-2">{Object.entries(data.cost_unavailable).map(([reason, count]) => <div key={reason} className="rounded-lg bg-amber-50 p-3 text-sm">{reason.replaceAll("_", " ")} · {formatNumber(count)} runs</div>)}</div>
      ) : <Empty>No failures or missing measurements in this view.</Empty>}
    </Panel>
  );
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
  const filters = { ...resolvedFilters(filterValues), workflow: workflow || undefined };
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({ queryKey: ["runs", workflow, filterValues, page], queryFn: () => api.runs(filters, page, 10) });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  return (
    <>
      <PageHeader
        title="Runs"
        description="Find a run by what it did, then open its complete telemetry and business story."
      />
      {workflow ? <div className="mb-4 flex items-center justify-between rounded-xl border border-[#dcd5ef] bg-[#f7f4ff] px-4 py-3 text-sm"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold text-[#4e348c]">Workflow filter</span><span className="text-[#6f6877]">{workflow}</span>{filterValues.model ? <Badge color="purple">Model · {filterValues.model}</Badge> : null}{filterValues.provider ? <Badge color="purple">Provider · {filterValues.provider}</Badge> : null}</div><a href="/runs" className="text-xs font-semibold text-[#5c35c8] hover:underline">Clear filters</a></div> : null}
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
      note="Newest first · select a run to inspect its complete path"
    >
      <div className="space-y-2">
        {rows.map((run) => <ExecutionListCard key={run.execution_id} run={run} href={`/runs/${encodeURIComponent(run.execution_id)}`} />)}
      </div>
    </Panel>
  );
}

export function RunPage() {
  const { executionId } = useParams({ from: "/runs/$executionId" });
  const q = useQuery({
    queryKey: ["run", executionId],
    queryFn: () => api.run(executionId),
  });
  useEffect(() => {
    if (q.data?.canonical_url) window.location.replace(q.data.canonical_url);
  }, [q.data?.canonical_url]);
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (q.data?.canonical_url) return <LoadingPage />;
  const d = q.data!,
    s = d.summary;
  const definitionRecord = d.semantic_records.find(
    (record) => record.name === "contract.definition",
  );
  const definition = (definitionRecord?.attributes || {}) as Record<
    string,
    Record<string, unknown>
  >;
  const goalRecord = d.semantic_records.find(
    (record) => record.name === "product_goal",
  );
  const goalAttributes = (goalRecord?.attributes || {}) as Record<
    string,
    unknown
  >;
  const decisionRecord = d.semantic_records.find(
    (record) => record.kind === "decision",
  );
  const resultName = String(
    definition.result?.name || goalAttributes.result_name || "Result",
  );
  const goalName = String(
    definition.product_goal?.name ||
      goalAttributes.product_goal_name ||
      "Product goal",
  );
  const resultState =
    s.artifact_valid === true
      ? "Valid"
      : s.artifact_valid === false
        ? "Needs attention"
        : "Not reported";
  const decisionValue = String(
    decisionRecord?.value || s.application_outcome || "Not reported",
  );
  const goalState =
    s.product_goal_achieved === true
      ? "Achieved"
      : s.product_goal_achieved === false
        ? "Not achieved"
        : "Not reported";
  const story = `${resultName} was ${resultState.toLowerCase()}. The run decided ${decisionValue}, and ${goalName} was ${goalState.toLowerCase()} in ${seconds(s.duration_seconds)} for ${money(s.known_cost)}.`;
  return (
    <>
      <PageHeader
        eyebrow="Run replay"
        title={s.display_name || String(s.workflow || "Agent run")}
        description={story}
        action={
          <Link to="/runs">
            <Button variant="outline">All runs</Button>
          </Link>
        }
      />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <Kpi
          label="Runtime"
          value={String(s.runtime_outcome || s.status || "Unknown")}
        />
        <Kpi label={resultName} value={resultState} tone={resultState === "Valid" ? "good" : "neutral"} />
        <Kpi label={String(definition.decision?.name || "Decision")} value={decisionValue} />
        <Kpi label={goalName} value={goalState} tone={goalState === "Achieved" ? "good" : "warn"} />
        <Kpi label="Elapsed" value={seconds(s.duration_seconds)} />
        <Kpi label="Measured cost" value={money(s.known_cost)} />
      </div>
      <Panel
        className="mt-4"
        title="How this run reached its result"
        note="Workflow telemetry and application meaning in one compact view. Open full screen to inspect the complete path."
      >
        <WorkflowGraph detail={d} />
      </Panel>
      <details className="mt-4 rounded-xl border bg-white p-5">
        <summary className="cursor-pointer text-sm font-semibold">
          Technical records
        </summary>
        <pre className="mt-4 max-h-96 overflow-auto rounded-lg bg-[#f6f6f2] p-4 text-xs">
          {JSON.stringify(
            { outcomes: d.outcomes, semantic_records: d.semantic_records },
            null,
            2,
          )}
        </pre>
      </details>
    </>
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
  const score = typeof record.score === "number" ? record.score : typeof attributes.score === "number" ? attributes.score : typeof record.value === "number" ? record.value : null;
  const target = attributes.target;
  const direction = String(attributes.direction || "higher_is_better");
  if (score != null && typeof target === "number") {
    return direction === "lower_is_better" ? score <= target : score >= target;
  }
  const label = String(record.label || attributes.label || "").trim().toLowerCase();
  if (["valid", "passed", "pass", "yes", "true", "achieved", "correct"].includes(label)) return true;
  if (["invalid", "failed", "fail", "no", "false", "not achieved", "incorrect"].includes(label)) return false;
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
  return (
    <>
      <PageHeader
        title="Issues"
        description="Failures, retries, and missing measurements—translated into concrete places to investigate."
      />
      <SharedFilterBar metadata={meta.data!} values={filterValues} onChange={setFilterValues} />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi label="Runs analyzed" value={formatNumber(q.data!.summary.runs)} />
        <Kpi label="Terminal failures" value={formatNumber(q.data!.summary.terminal_failures)} tone={q.data!.summary.terminal_failures ? "warn" : "good"} />
        <Kpi label="Recovered runs" value={formatNumber(q.data!.summary.recovered_runs)} />
        <Kpi label="Retry attempts" value={formatNumber(q.data!.summary.extra_attempts)} tone={q.data!.summary.extra_attempts ? "warn" : "good"} />
        <Kpi label="Below target" value={formatNumber(q.data!.summary.quality_gaps)} tone={q.data!.summary.quality_gaps ? "warn" : "good"} />
      </div>
      <div className="grid gap-4 xl:grid-cols-12 xl:items-start">
        <Panel className="xl:col-span-7" title="Investigation queue" note="Runtime, quality, and resource signals ranked together. Select any row to open its exact replay.">
          <IssueRunQueue data={q.data!} />
        </Panel>
        <Panel className="xl:col-span-5" title="Retry hotspots" note="Repeated stages ranked by extra attempts. Expand a stage only when you need its affected runs.">
          <RetryHotspotList data={q.data!} />
        </Panel>
      </div>
      <Panel className="mt-4" title="Measurement coverage" note="Coverage explains which issue signals can be evaluated reliably for this population.">
        <div className="grid gap-6 sm:grid-cols-3">
          <CoverageMeter label="Cost measured" value={q.data!.measurement.cost} total={q.data!.measurement.total} />
          <CoverageMeter label="Tokens measured" value={q.data!.measurement.tokens} total={q.data!.measurement.total} />
          <CoverageMeter label="Business goal reported" value={q.data!.measurement.business_goal} total={q.data!.measurement.total} />
        </div>
      </Panel>
    </>
  );
}

type IssueQueueItem = {
  key: string;
  executionId: string;
  title: string;
  detail: string;
  signal: string;
  tone: "red" | "amber" | "violet" | "green";
  priority: number;
};

function IssueRunQueue({ data }: { data: Issues }) {
  const items: IssueQueueItem[] = [
    ...data.failures.map((failure) => ({
      key: `failure-${failure.execution_id}`,
      executionId: failure.execution_id,
      title: failure.display_name || failure.execution_id,
      detail: `${failure.failure_location} · ${seconds(failure.duration_seconds)} · ${money(failure.known_cost)}`,
      signal: failure.runtime_outcome === "recovered" ? "Recovered" : "Terminal",
      tone: failure.runtime_outcome === "recovered" ? "green" as const : "red" as const,
      priority: failure.runtime_outcome === "recovered" ? 1 : 0,
    })),
    ...data.quality_gaps.map((gap) => ({
      key: `quality-${gap.execution_id}-${gap.name}`,
      executionId: gap.execution_id,
      title: gap.display_name || gap.execution_id,
      detail: `${gap.name} · observed ${formatNumber(gap.score)} · target ${formatNumber(gap.target)}`,
      signal: "Below target",
      tone: "amber" as const,
      priority: 2,
    })),
    ...data.outliers.map((run) => ({
      key: `outlier-${run.execution_id}`,
      executionId: run.execution_id,
      title: run.display_name || run.execution_id,
      detail: `${run.reasons.map((reason) => reason.replaceAll("_", " ")).join(" · ")} · ${seconds(run.duration_seconds)} · ${money(run.known_cost)} · ${formatNumber(run.total_tokens)} tokens`,
      signal: "p95 outlier",
      tone: "violet" as const,
      priority: 3,
    })),
  ].sort((left, right) => left.priority - right.priority || left.title.localeCompare(right.title));
  if (!items.length) return <Empty>No run-level issues in this population.</Empty>;
  const toneClasses = {
    red: "border-red-200 bg-red-50 text-red-700",
    amber: "border-amber-200 bg-amber-50 text-amber-800",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  };
  return (
    <div>
      <div className="mb-3 flex items-center justify-between text-xs text-[#777]">
        <span>{formatNumber(items.length)} signals</span>
        <span>Highest priority first</span>
      </div>
      <div className="max-h-[430px] divide-y divide-[#ecebe6] overflow-y-auto rounded-xl border border-[#ecebe6]">
        {items.map((item) => (
          <Link
            key={item.key}
            to="/runs/$executionId"
            params={{ executionId: item.executionId }}
            className="group grid min-w-0 gap-2 bg-white px-4 py-3 hover:bg-[#faf9ff] sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[#4f32aa] group-hover:underline" title={item.title}>{item.title}</div>
              <div className="mt-1 truncate text-xs text-[#777]" title={item.detail}>{item.detail}</div>
            </div>
            <span className={`w-fit shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-semibold ${toneClasses[item.tone]}`}>{item.signal}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function RetryHotspotList({ data }: { data: Issues }) {
  if (!data.retries.length) return <Empty>No retries in this population.</Empty>;
  return (
    <div className="max-h-[490px] divide-y divide-[#ece8da] overflow-y-auto rounded-xl border border-[#ece8da] bg-[#fffdf7]">
      {data.retries.map((retry, index) => (
        <details key={retry.label} className="group px-4 py-3 open:bg-[#fffaf0]">
          <summary className="cursor-pointer list-none">
            <div className="flex items-start gap-3">
              <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-[#f6ead0] text-xs font-bold text-[#8a5a08]">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-3">
                  <span className="line-clamp-2 text-sm font-semibold text-[#3d382f]">{retry.label}</span>
                  <span className="shrink-0 text-sm font-semibold text-[#9a6208]">+{formatNumber(retry.extra_attempts)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-xs text-[#81786b]">
                  <span>{formatNumber(retry.affected_runs)} affected runs</span>
                  <span className="font-medium text-[#6b4bbd] group-open:hidden">Show runs ↓</span>
                  <span className="hidden font-medium text-[#6b4bbd] group-open:inline">Hide runs ↑</span>
                </div>
              </div>
            </div>
          </summary>
          <div className="ml-10 mt-3 max-h-44 space-y-1 overflow-y-auto border-l border-[#eadab8] pl-3">
            {retry.runs.map((run) => (
              <Link
                key={run.execution_id}
                to="/runs/$executionId"
                params={{ executionId: run.execution_id }}
                className="block truncate rounded-md px-2 py-1.5 text-xs font-medium text-[#5a35c8] hover:bg-white hover:underline"
                title={run.display_name || run.execution_id}
              >
                {run.display_name || run.execution_id}
              </Link>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
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
