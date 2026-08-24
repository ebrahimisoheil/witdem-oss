import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import {
  api,
  type ContractDefinition,
  type DashboardFilters,
  type Meta,
  type Overview,
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
  QualityComparisonChart,
  seconds,
  GoalTrendChart,
  StageAccumulation,
  StatusBadge,
  useQuery,
  WorkflowBarChart,
  WorkflowGraph,
  WorkflowStageContribution,
} from "./components";

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

export function OverviewPage() {
  const [mode, setMode] = useState<"health" | "goals">("health");
  const [breakdown, setBreakdown] = useState<"model" | "provider">("model");
  const [contractHash, setContractHash] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [range, setRange] = useState("all");
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
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
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
  const outcomeColors = {
    accepted: "#2f6fed",
    rejected: "#536174",
    escalated: "#df7a00",
    not_reached: "#b8bec8",
    completed: "#16864b",
  };
  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title="How are your agents doing?"
        description="Start with system health, then switch to whether each agent achieved the business goal it was given."
      />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#ddd8ef] bg-white p-3">
        <div className="flex rounded-lg bg-[#ecebe7] p-1">
          <ModeButton active={mode === "health"} onClick={() => setMode("health")}>
            System health
          </ModeButton>
          <ModeButton active={mode === "goals"} onClick={() => setMode("goals")}>
            Goal performance
          </ModeButton>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
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
              label="Decision correctness"
              value={percent(d.goals.decision_correctness_rate)}
              note={`${formatNumber(d.goals.decision_correct_runs)} correct reported decisions`}
              tone="good"
            />
            <Kpi label="Time / achieved goal" value={seconds(d.goals.time_per_achieved_goal)} />
            <Kpi label="Cost / achieved goal" value={money(d.goals.cost_per_achieved_goal)} />
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-2">
            {selectedContract ? (
              <Panel title="Business results" note="These labels belong to the selected business contract; they are not runtime states.">
                <BreakdownBar data={d.outcome_breakdown} colors={outcomeColors} />
              </Panel>
            ) : (
              <Panel title="Business goals" note="Choose one goal above before comparing its contract-specific result labels.">
                <div className="space-y-3">
                  {metadata.contracts.map((item) => <ContractCard key={item.contract_hash} contract={item} />)}
                </div>
              </Panel>
            )}
            <Panel title="Result quality" note="What the selected contract evaluated and how the reported runs scored.">
              {selectedContract && d.evaluations.length ? (
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
              ) : <Empty>Select a business goal to see its result checks.</Empty>}
            </Panel>
          </div>
          <div className="mt-4 grid gap-4 xl:grid-cols-[.8fr_1.2fr]">
            <Panel title="Why goals were missed" note="Concrete blockers reported by the application; runtime success is never treated as goal success.">
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
            <Panel title="Change over time" note="Switch between goal success, time, and cost using the same filters.">
              <GoalTrendChart items={d.goal_trend} />
            </Panel>
          </div>
        </>
      )}
      <div className="mt-6 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">{mode === "health" ? "Cost and speed" : "Best goal tradeoffs"}</div>
          <div className="text-xs text-[#777]">
            Apply one grouping to every comparison below.
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
      <div className="mt-3 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,.8fr)]">
        <Panel
          title={`${breakdownLabel} cost versus speed`}
          note="Bubble size shows run volume. Hover for exact values."
        >
          <CostSpeedChart items={breakdownItems} breakdown={breakdown} />
        </Panel>
        <Panel
          title={`${breakdownLabel} share of measured spend`}
          note="Share of measured spend. Hover for the exact amount."
        >
          <ProviderSpendChart items={breakdownItems} breakdown={breakdown} />
        </Panel>
      </div>
      <Panel
        className="mt-4"
        title={`${breakdownLabel} ranking`}
        note="Switch between the slowest and most expensive configurations."
      >
        <EconomicsBarChart items={breakdownItems} />
      </Panel>
    </>
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
function SharedFilterBar({ metadata, values, onChange }: { metadata: Meta; values: SharedFilterValues; onChange: (values: SharedFilterValues) => void }) {
  const set = (key: keyof SharedFilterValues, value: string) => onChange({ ...values, [key]: value });
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-[#e4e2da] bg-white p-3">
      <span className="mr-1 text-xs font-semibold text-[#555]">Filter this view</span>
      <FilterSelect value={values.contractHash} onChange={(value) => set("contractHash", value)} label="All business goals">
        {metadata.contracts.map((item) => <option key={item.contract_hash} value={item.contract_hash}>{item.product_goal?.name || item.contract_name}</option>)}
      </FilterSelect>
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

const col = createColumnHelper<Run>();
const columns = [
  col.accessor((r) => r.display_name || String(r.workflow || "Agent run"), {
    id: "name",
    header: "Run",
    cell: (i) => (
      <Link
        to="/runs/$executionId"
        params={{ executionId: i.row.original.execution_id }}
        className="font-medium text-[#5a35c8] hover:underline"
      >
        {i.getValue()}
      </Link>
    ),
  }),
  col.accessor((r) => r.runtime_outcome || r.status || "unknown", {
    id: "status",
    header: "Runtime",
    cell: (i) => <StatusBadge value={i.getValue()} />,
  }),
  col.accessor("application_outcome", {
    header: "Business result",
    cell: (i) => (
      <span className="capitalize">
        {String(i.getValue() || "Not reported")}
      </span>
    ),
  }),
  col.accessor("product_goal_achieved", {
    header: "Product goal",
    cell: (i) => (
      <StatusBadge
        value={
          i.getValue() === true
            ? "Achieved"
            : i.getValue() === false
              ? "Not achieved"
              : "Not reported"
        }
      />
    ),
  }),
  col.accessor("model", {
    header: "Model",
    cell: (i) => (
      <span className="block max-w-[240px] truncate">
        {String(i.getValue() || "—")}
      </span>
    ),
  }),
  col.accessor("duration_seconds", {
    header: "Time",
    cell: (i) => seconds(i.getValue()),
  }),
  col.accessor("known_cost", {
    header: "Cost",
    cell: (i) => money(i.getValue()),
  }),
];
export function RunsPage() {
  const [filterValues, setFilterValues] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const filters = resolvedFilters(filterValues);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const q = useQuery({ queryKey: ["runs", filterValues, page], queryFn: () => api.runs(filters, page, 10) });
  if (q.isLoading || meta.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
  if (meta.error) return <ErrorPage error={meta.error} />;
  return (
    <>
      <PageHeader
        title="Runs"
        description="Find a run by what it did, then open its complete telemetry and business story."
      />
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
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <Panel title={`${formatNumber(count)} runs`} note="Newest first · 10 per page">
      <div className="overflow-x-auto rounded-lg border border-[#e8e8e3]">
        <table className="min-w-[920px] w-full text-left text-sm">
          <thead className="bg-[#f7f7f3] text-xs text-[#6f6f69]">
            {table.getHeaderGroups().map((g) => (
              <tr key={g.id}>
                {g.headers.map((h) => (
                  <th className="px-4 py-3 font-medium" key={h.id}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-[#eeeeea]">
            {table.getRowModel().rows.map((r) => (
              <tr className="hover:bg-[#fbfbf8]" key={r.id}>
                {r.getVisibleCells().map((c) => (
                  <td className="px-4 py-3" key={c.id}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
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
  if (q.isLoading) return <LoadingPage />;
  if (q.error) return <ErrorPage error={q.error} />;
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
        <Kpi label="Runs checked" value={formatNumber(q.data!.summary.runs)} />
        <Kpi label="Terminal failures" value={formatNumber(q.data!.summary.terminal_failures)} tone={q.data!.summary.terminal_failures ? "warn" : "good"} />
        <Kpi label="Recovered runs" value={formatNumber(q.data!.summary.recovered_runs)} />
        <Kpi label="Extra attempts" value={formatNumber(q.data!.summary.extra_attempts)} tone={q.data!.summary.extra_attempts ? "warn" : "good"} />
        <Kpi label="Quality gaps" value={formatNumber(q.data!.summary.quality_gaps)} tone={q.data!.summary.quality_gaps ? "warn" : "good"} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Recovered and failed runs" note="Every issue links to the exact replay.">
          {q.data!.failures.length ? <div className="max-h-[420px] space-y-2 overflow-y-auto pr-2">{q.data!.failures.map((failure) => <Link key={failure.execution_id} to="/runs/$executionId" params={{ executionId: failure.execution_id }} className="flex items-center justify-between rounded-lg bg-red-50 p-3 text-sm hover:bg-red-100"><div><div className="font-medium text-[#5a35c8]">{failure.display_name || failure.execution_id}</div><div className="mt-1 text-xs text-[#777]">{failure.failure_location} · {seconds(failure.duration_seconds)} · {money(failure.known_cost)}</div></div><Badge color={failure.runtime_outcome === "recovered" ? "green" : "red"}>{failure.runtime_outcome || "failed"}</Badge></Link>)}</div> : <Empty>No runtime failures.</Empty>}
        </Panel>
        <Panel title="Retry hotspots" note="Stages with attempts after the first, linked to affected runs.">
          {q.data!.retries.length ? <div className="max-h-[420px] space-y-3 overflow-y-auto pr-2">{q.data!.retries.map((retry) => <div key={retry.label} className="rounded-lg bg-amber-50 p-3 text-sm"><div className="flex justify-between"><span className="font-medium">{retry.label}</span><Badge color="yellow">{formatNumber(retry.extra_attempts)} extra</Badge></div><div className="mt-2 flex flex-wrap gap-2">{retry.runs.map((run) => <Link key={run.execution_id} to="/runs/$executionId" params={{ executionId: run.execution_id }} className="text-xs font-medium text-[#5a35c8] hover:underline">{run.display_name || run.execution_id.slice(0, 8)}</Link>)}</div></div>)}</div> : <Empty>No retries.</Empty>}
        </Panel>
        <Panel title="Quality below target" note="Contract evaluations that missed their declared target.">
          {q.data!.quality_gaps.length ? <div className="max-h-[420px] space-y-2 overflow-y-auto pr-2">{q.data!.quality_gaps.map((gap) => <Link key={`${gap.execution_id}-${gap.name}`} to="/runs/$executionId" params={{ executionId: gap.execution_id }} className="flex justify-between rounded-lg bg-orange-50 p-3 text-sm hover:bg-orange-100"><div><div className="font-medium text-[#5a35c8]">{gap.display_name || gap.execution_id}</div><div className="text-xs text-[#777]">{gap.name}</div></div><span>{formatNumber(gap.score)} / {formatNumber(gap.target)}</span></Link>)}</div> : <Empty>No reported evaluations are below target.</Empty>}
        </Panel>
        <Panel title="Slow, expensive, or token-heavy runs" note="Runs at or above the selected population's p95 for one or more metrics.">
          {q.data!.outliers.length ? <div className="max-h-[420px] space-y-2 overflow-y-auto pr-2">{q.data!.outliers.map((run) => <Link key={run.execution_id} to="/runs/$executionId" params={{ executionId: run.execution_id }} className="block rounded-lg bg-violet-50 p-3 text-sm hover:bg-violet-100"><div className="font-medium text-[#5a35c8]">{run.display_name || run.execution_id}</div><div className="mt-1 text-xs text-[#777]">{run.reasons.map((reason) => reason.replaceAll("_", " ")).join(" · ")} · {seconds(run.duration_seconds)} · {money(run.known_cost)} · {formatNumber(run.total_tokens)} tokens</div></Link>)}</div> : <Empty>No p95 outliers.</Empty>}
        </Panel>
      </div>
      <Panel className="mt-4" title="Measurement coverage" note="Coverage is context, not an empty-state centerpiece.">
        <div className="grid gap-3 sm:grid-cols-3"><Kpi label="Cost measured" value={`${formatNumber(q.data!.measurement.cost)} / ${formatNumber(q.data!.measurement.total)}`} /><Kpi label="Tokens measured" value={`${formatNumber(q.data!.measurement.tokens)} / ${formatNumber(q.data!.measurement.total)}`} /><Kpi label="Business goal reported" value={`${formatNumber(q.data!.measurement.business_goal)} / ${formatNumber(q.data!.measurement.total)}`} /></div>
      </Panel>
    </>
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
