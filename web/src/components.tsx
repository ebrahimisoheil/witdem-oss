import { Badge, Button, ProgressBar } from "@lemonsqueezy/wedges";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import type { ComparisonInsight, OperationTypeSummary, Overview, Performance, ProjectedWorkflowNode, Run, WorkflowStage } from "./api";
import { api, formatNumber, money, percent, seconds } from "./api";
import witdemMark from "./assets/witdem-mark-purple.png";

const EChartsRuntime = lazy(() => import("./echarts-runtime"));
const echarts = undefined;

export function AnalyticsChart(props: React.ComponentProps<typeof EChartsRuntime>) {
  return (
    <Suspense fallback={<div className="animate-pulse rounded-lg bg-[#f4f3f0]" style={props.style as React.CSSProperties} />}>
      <EChartsRuntime {...props} />
    </Suspense>
  );
}

const ReactEChartsCore = AnalyticsChart;

export const chartColors = [
  "#6d4aff",
  "#2477e6",
  "#16a085",
  "#e38317",
  "#d34f6f",
  "#637083",
  "#9b59b6",
];
export const stableColor = (identity: string) => {
  let hash = 2166136261;
  for (const character of identity) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return chartColors[Math.abs(hash) % chartColors.length];
};

const nav = [
  ["/", "Overview"],
  ["/system-health", "System health"],
  ["/goal-performance", "Goal performance"],
  ["/workflows", "Workflows"],
  ["/runs", "All executions"],
  ["/compare", "Compare"],
  ["/issues", "Issues"],
] as const;
export function Shell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="min-h-screen bg-[#f8f8f5] text-[#242424]">
      <aside className="fixed inset-y-0 left-0 z-20 w-56 border-r border-[#e7e7e1] bg-white px-4 py-5">
        <Link to="/" className="mb-8 flex items-center gap-3 px-2">
          <img
            src={witdemMark}
            alt=""
            aria-hidden="true"
            className="h-7 w-11 shrink-0 object-contain"
          />
          <span className="font-semibold">Witdem AI</span>
        </Link>
        <nav className="space-y-1">
          {nav.map(([to, label]) => (
            <Link
              key={to}
              to={to}
              className={`block rounded-lg px-3 py-2 text-sm font-medium ${path === to || (to !== "/" && path.startsWith(to)) ? "bg-[#f0edff] text-[#5a35c8]" : "text-[#666] hover:bg-[#f5f5f1]"}`}
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="absolute bottom-5 left-4 right-4 border-t pt-4">
          <Link
            to="/developer"
            className="px-3 text-xs font-medium text-[#777]"
          >
            Developer data
          </Link>
        </div>
      </aside>
      <main className="ml-56 min-h-screen">
        <div className="mx-auto max-w-[1480px] px-8 py-7">
          <UpdateNotice />
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function UpdateNotice() {
  const { data } = useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: 60_000 });
  const update = data?.update;
  const latest = update?.latest?.platform;
  const incompatible = update?.compatibility?.compatible === false;
  const visible = update?.status === "update-available" || incompatible;
  const dismissalKey = `witdem-update-dismissed:${latest || "compatibility"}`;
  const [dismissed, setDismissed] = useState(
    () => typeof window !== "undefined" && window.localStorage.getItem(dismissalKey) === "1",
  );
  if (!visible || dismissed) return null;
  return (
    <div className="mb-4 flex items-center justify-between gap-4 rounded-lg border border-[#d9d0ef] bg-[#f5f1ff] px-4 py-2 text-xs text-[#4d3b75]">
      <span>
        {incompatible
          ? "Witdem components need a compatibility update."
          : `Witdem ${latest} is available.`}{" "}
        {update?.release_notes_url && (
          <a className="font-semibold underline" href={update.release_notes_url} target="_blank" rel="noreferrer">
            Release notes
          </a>
        )}
      </span>
      <button
        type="button"
        className="shrink-0 font-semibold text-[#684bb0]"
        onClick={() => {
          window.localStorage.setItem(dismissalKey, "1");
          setDismissed(true);
        }}
      >
        Dismiss
      </button>
    </div>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  action,
  compact = false,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: React.ReactNode;
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const isFetching = useIsFetching() > 0;
  return (
    <header className={`${compact ? "mb-4" : "mb-7"} flex items-start justify-between gap-6`}>
      <div>
        {eyebrow && (
          <div className={`${compact ? "mb-1" : "mb-2"} text-xs font-semibold uppercase tracking-[.12em] text-[#8062df]`}>
            {eyebrow}
          </div>
        )}
        <h1 className={`${compact ? "text-[26px]" : "text-[30px]"} font-semibold leading-tight tracking-[-.03em]`}>
          {title}
        </h1>
        <p className={`${compact ? "mt-1 leading-5" : "mt-2 leading-6"} max-w-2xl text-sm text-[#6d6d68]`}>
          {description}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          variant="outline"
          disabled={isFetching}
          onClick={() => void queryClient.invalidateQueries()}
        >
          {isFetching ? "Refreshing…" : "Refresh"}
        </Button>
        {action}
      </div>
    </header>
  );
}
export function Panel({
  title,
  note,
  children,
  className = "",
}: React.PropsWithChildren<{
  title: string;
  note?: string;
  className?: string;
}>) {
  return (
    <section
      className={`min-w-0 overflow-hidden rounded-xl border border-[#e4e4df] bg-white p-5 shadow-[0_1px_2px_rgba(0,0,0,.02)] ${className}`}
    >
      <div className="mb-4">
        <h2 className="text-sm font-semibold">{title}</h2>
        {note && <p className="mt-1 text-xs text-[#7a7a74]">{note}</p>}
      </div>
      {children}
    </section>
  );
}

export function formatDateTime(value?: string | null) {
  if (!value) return "Time not reported";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Time not reported";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

export function formatBrowserDate(value?: string | null) {
  if (!value) return "Date not reported";
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Date not reported";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "2-digit" }).format(parsed);
}

export function browserDateDaysAgo(days: number) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function ExecutionListCard({ run, href }: { run: Run; href?: string }) {
  const runtime = String(run.runtime_outcome || run.status || "unknown");
  const outcome = String(run.application_outcome || "Not reported").replaceAll("_", " ");
  const goal = run.product_goal_achieved === true ? run.evidence_sufficient === false ? "Achieved · attention" : "Achieved" : run.product_goal_achieved === false ? "Not achieved" : "Not reported";
  const provider = run.workflow_providers?.join(", ") || String(run.provider || "Provider not observed");
  const measuredTokens = typeof run.total_tokens === "number";
  const healthy = runtime.toLowerCase() === "completed" && run.product_goal_achieved === true;
  const content = <>
    <span className={`absolute inset-y-0 left-0 w-1 ${healthy ? "bg-[#25a86b]" : run.product_goal_achieved === false ? "bg-[#df5a5a]" : "bg-[#f0a128]"}`} />
    <div className="min-w-0 pl-1"><div className="flex min-w-0 items-center gap-2"><div className="truncate text-base font-semibold text-[#3f277f] group-hover:text-[#5c35c8]">{run.display_name || String(run.workflow || "Agent run")}</div>{!href ? <span className="shrink-0 rounded-full bg-[#f3f1ed] px-2 py-1 text-[9px] font-semibold text-[#77716a]">No YAML replay</span> : null}</div><div className="mt-2 flex min-w-0 flex-nowrap items-center gap-x-2 text-xs text-[#74746e]"><StatusBadge value={runtime} /><span className="shrink-0 whitespace-nowrap">{formatDateTime(run.started_at)}</span><span className="shrink-0 text-[#c2c1bb]">·</span><span className="min-w-0 truncate text-[11px]" title={provider}>{provider}</span></div></div>
    <div className="min-w-0 border-t border-[#efeee9] pt-3 xl:border-l xl:border-t-0 xl:pl-5 xl:pr-6 xl:pt-0"><div className="whitespace-nowrap text-[9px] font-semibold uppercase tracking-[.1em] text-[#92918a]">Business result</div><div className="mt-1 whitespace-nowrap text-xs font-semibold capitalize text-[#33332f]" title={outcome}>{outcome}</div></div>
    <div className="min-w-0"><div className="whitespace-nowrap text-[9px] font-semibold uppercase tracking-[.1em] text-[#92918a]">Product goal</div><div className="mt-1"><Badge size="sm" className="whitespace-nowrap text-xs" color={goal === "Achieved" ? "green" : goal === "Not achieved" ? "red" : goal === "Achieved · attention" ? "yellow" : "gray"}>{goal}</Badge></div></div>
    <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#92918a]">Elapsed</div><div className="mt-1 whitespace-nowrap text-xs font-semibold text-[#34342f]">{seconds(run.duration_seconds)}</div></div>
    <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#92918a]">Cost</div><div className="mt-1 whitespace-nowrap text-xs font-semibold text-[#34342f]">{money(run.known_cost)}</div></div>
    <div className="min-w-0"><div className="text-[9px] font-semibold uppercase tracking-[.1em] text-[#92918a]">Tokens</div><div className="mt-1 whitespace-nowrap text-xs font-semibold text-[#34342f]">{measuredTokens ? formatNumber(run.total_tokens) : "Not measured"}</div></div>
  </>;
  const className = "group relative grid min-w-0 gap-5 overflow-hidden rounded-xl border border-[#e8e7e2] bg-white px-5 py-4 transition xl:grid-cols-[minmax(260px,1fr)_205px_130px_65px_95px_95px] xl:items-center";
  if (href) return <a href={href} className={`${className} hover:-translate-y-px hover:border-[#cfc6ef] hover:shadow-[0_8px_24px_rgba(45,35,78,.07)]`}>{content}</a>;
  return <div className={`${className} opacity-75`} aria-label="No YAML workflow replay available">
    {content}
  </div>;
}
export function Kpi({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "neutral" | "good" | "warn";
}) {
  return (
    <div className="min-w-0 overflow-hidden rounded-xl border border-[#e4e4df] bg-white p-4">
      <div className="break-words text-xs font-medium text-[#73736d]">{label}</div>
      <div
        className={`mt-3 min-w-0 break-words text-2xl font-semibold leading-tight tracking-[-.03em] [overflow-wrap:anywhere] ${tone === "good" ? "text-[#14794c]" : tone === "warn" ? "text-[#a15c00]" : ""}`}
        title={value}
      >
        {value}
      </div>
      {note && <div className="mt-2 break-words text-xs text-[#888880]">{note}</div>}
    </div>
  );
}
export function Empty({ children }: React.PropsWithChildren) {
  return (
    <div className="rounded-lg bg-[#f6f6f2] px-4 py-8 text-center text-sm text-[#777]">
      {children}
    </div>
  );
}
export function LoadingPage() {
  return (
    <div className="grid min-h-[50vh] place-items-center text-sm text-[#777]">
      Loading dashboard…
    </div>
  );
}
export function ErrorPage({ error }: { error: Error }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800">
      Could not load this view: {error.message}
    </div>
  );
}

export function CostSpeedChart({
  items,
  breakdown = "model",
}: {
  items: Performance[];
  breakdown?: "model" | "provider";
}) {
  const data = items.filter(
    (x) => x.measured_cost != null && x.time_per_positive_run != null,
  );
  if (!data.length)
    return <Empty>No models have both measured time and cost yet.</Empty>;
  const participants = data.map((item) => ({
    ...item,
    color: stableColor(item.participant_id || `${breakdown}:${item.label}`),
  }));
  return (
    <div className="min-w-0">
      <div className="mb-1 flex flex-wrap justify-end gap-3">
        {participants.map((participant) => (
          <span
            key={participant.participant_id}
            className="flex items-center gap-1.5 text-xs text-[#666]"
          >
            <span
              className="size-2.5 rounded-full"
              style={{ background: participant.color }}
            />
            {participant.label}
          </span>
        ))}
      </div>
      <ReactEChartsCore
        echarts={echarts}
        style={{ height: 300, width: "100%" }}
        option={{
          color: chartColors,
          grid: {
            left: 64,
            right: 24,
            top: 18,
            bottom: 54,
            containLabel: false,
          },
          tooltip: {
            trigger: "item",
            formatter: (p: { data: { name: string; value: number[] } }) =>
              `<b>${p.data.name}</b><br/>${seconds(p.data.value[0])} attributed active time / successful involved run<br/>${money(p.data.value[1])} directly measured spend<br/>${formatNumber(p.data.value[2])} involved runs`,
          },
          xAxis: {
            name: "Attributed active seconds / successful involved run",
            nameLocation: "middle",
            nameGap: 34,
            splitLine: { lineStyle: { color: "#eee" } },
          },
          yAxis: {
            name: "Measured spend",
            nameGap: 48,
            nameLocation: "middle",
            axisLabel: { formatter: (v: number) => money(v) },
            splitLine: { lineStyle: { color: "#eee" } },
          },
          series: participants.map((participant) => ({
            name: participant.label,
            type: "scatter",
            itemStyle: { color: participant.color },
            symbolSize: (v: number[]) =>
              Math.max(13, Math.min(30, 10 + v[2] * 1.2)),
            data: [{
              name: participant.label,
              value: [participant.time_per_positive_run, participant.measured_cost, participant.runs],
            }],
            emphasis: {
              label: { show: true, position: "top", formatter: "{b}" },
            },
          })),
        }}
      />
    </div>
  );
}

export function BreakdownBar({
  data,
  colors,
}: {
  data: Record<string, number>;
  colors: Record<string, string>;
}) {
  const entries = Object.entries(data);
  const total = entries.reduce((n, [, v]) => n + v, 0);
  if (!total) return <Empty>No reported data.</Empty>;
  return (
    <div>
      <div className="flex h-10 overflow-hidden rounded-lg">
        {entries.map(([name, value]) => (
          <div
            key={name}
            title={`${name}: ${formatNumber(value)}`}
            className="grid place-items-center text-xs font-semibold text-white"
            style={{
              width: `${(value / total) * 100}%`,
              background: colors[name.toLowerCase()] || "#7a8290",
            }}
          >
            {value / total > 0.09 ? percent(value / total) : ""}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        {entries.map(([name, value]) => (
          <div
            key={name}
            className="flex items-center gap-2 text-xs capitalize"
          >
            <span
              className="size-2.5 rounded-sm"
              style={{ background: colors[name.toLowerCase()] || "#7a8290" }}
            />
            <span>
              {name.replaceAll("_", " ")} <b>{formatNumber(value)}</b>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RuntimeDonutChart({
  data,
  colors,
  height = 245,
}: {
  data: Record<string, number>;
  colors: Record<string, string>;
  height?: number;
}) {
  const entries = Object.entries(data).filter(([, value]) => value > 0);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  const compact = height < 200;
  if (!total) return <Empty>No runtime states were reported.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      style={{ height, width: "100%" }}
      option={{
        tooltip: { trigger: "item", formatter: "{b}<br/>{c} runs · {d}%" },
        legend: { type: "scroll", orient: "vertical", right: 4, top: "center", itemWidth: compact ? 7 : 10, itemHeight: compact ? 7 : 10, textStyle: { fontSize: compact ? 9 : 11 }, width: compact ? "42%" : undefined },
        graphic: [
          { type: "text", left: compact ? "27%" : "31%", top: "42%", style: { text: formatNumber(total), textAlign: "center", fill: "#292925", fontSize: compact ? 18 : 24, fontWeight: 700 } },
          { type: "text", left: compact ? "27%" : "31%", top: "57%", style: { text: "runs", textAlign: "center", fill: "#7a7a74", fontSize: compact ? 8 : 11 } },
        ],
        series: [{
          type: "pie",
          radius: ["50%", "72%"],
          center: [compact ? "28%" : "34%", "50%"],
          minShowLabelAngle: 5,
          label: { show: false },
          data: entries.map(([name, value]) => ({
            name: name.replaceAll("_", " "),
            value,
            label: name === entries[0]?.[0] ? { show: true, position: "center", formatter: `{value|${formatNumber(total)}}\n{caption|runs}`, rich: { value: { color: "#292925", fontSize: compact ? 17 : 22, fontWeight: 700, lineHeight: compact ? 19 : 25 }, caption: { color: "#7a7a74", fontSize: compact ? 8 : 10, lineHeight: 12 } } } : { show: false },
            itemStyle: { color: colors[name.toLowerCase()] || "#7a8290", borderColor: "#fff", borderWidth: 2 },
          })),
        }],
      }}
    />
  );
}

export function WorkflowBarChart({ items }: { items: Performance[] }) {
  const shown = [...items]
    .sort((a, b) => b.runs - a.runs)
    .slice(0, 10)
    .reverse();
  const wrapLabel = (value: string) => {
    const words = value.split(" ");
    const lines: string[] = [];
    for (const word of words) {
      const current = lines.at(-1);
      if (!current || current.length + word.length + 1 > 24) {
        lines.push(word);
      } else {
        lines[lines.length - 1] = `${current} ${word}`;
      }
    }
    return lines.join("\n");
  };
  return (
    <ReactEChartsCore
      echarts={echarts}
      style={{ height: Math.max(280, shown.length * 38) }}
      option={{
        color: ["#6d4aff", "#d34f6f"],
        legend: { top: 0 },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          valueFormatter: (value: number) => `${formatNumber(value)} runs`,
        },
        grid: { left: 205, right: 25, top: 42, bottom: 28 },
        xAxis: {
          type: "value",
          name: "Runs",
          axisLabel: { formatter: (value: number) => formatNumber(value) },
        },
        yAxis: {
          type: "category",
          data: shown.map((x) => x.label),
          axisLabel: {
            formatter: (value: string) => wrapLabel(value),
            lineHeight: 15,
          },
        },
        series: [
          {
            name: "Completed",
            type: "bar",
            stack: "runs",
            data: shown.map((x) => Math.max(0, x.completed - x.recovered)),
            barWidth: 18,
          },
          {
            name: "Failed / recovered",
            type: "bar",
            stack: "runs",
            data: shown.map((x) => x.failed + x.recovered),
            barWidth: 18,
          },
        ],
      }}
    />
  );
}

export function EconomicsBarChart({ items }: { items: Performance[] }) {
  const [sortBy, setSortBy] = useState<"time" | "cost">("cost");
  const shown = [...items]
    .filter((x) => x.measured_cost != null && x.time_per_positive_run != null)
    .sort((a, b) =>
      sortBy === "cost"
        ? (b.measured_cost || 0) - (a.measured_cost || 0)
        : (b.time_per_positive_run || 0) - (a.time_per_positive_run || 0),
    )
    .slice(0, 12);
  const maxTime = Math.max(
    1,
    ...shown.map((item) => item.time_per_positive_run || 0),
  );
  const maxCost = Math.max(
    0.000001,
    ...shown.map((item) => item.measured_cost || 0),
  );
  const scaledCost = (cost: number | null) => ((cost || 0) / maxCost) * maxTime;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-4 text-xs text-[#666]">
          <span className="flex items-center gap-2">
            <span className="h-2.5 w-6 rounded-sm bg-[#2477e6]" />
            Attributed time / successful involved run
          </span>
          <span className="flex items-center gap-2">
            <span className="h-2.5 w-6 rounded-sm bg-[#e38317]" />
            Measured cost
          </span>
        </div>
        <div className="flex gap-1 rounded-lg">
          <button
            onClick={() => setSortBy("time")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ${sortBy === "time" ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}
          >
            Sort by time
          </button>
          <button
            onClick={() => setSortBy("cost")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ${sortBy === "cost" ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}
          >
            Sort by cost
          </button>
        </div>
      </div>
      <ReactEChartsCore
        echarts={echarts}
        style={{ height: 500, width: "100%" }}
        option={{
          color: ["#2477e6", "#e38317"],
          tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            formatter: (
              p: Array<{
                name: string;
                seriesName: string;
                value: number;
                data: number | { value: number; raw: number };
              }>,
            ) => {
              const timePoint = p.find(
                (point) => point.seriesName === "Attributed time / successful involved run",
              );
              const costPoint = p.find(
                (point) => point.seriesName === "Measured cost",
              );
              const timeValue =
                typeof timePoint?.value === "number" ? timePoint.value : null;
              const costValue =
                typeof costPoint?.data === "object" ? costPoint.data.raw : null;
              return `<b>${p[0]?.name || ""}</b><br/>Time: ${seconds(timeValue)}<br/>Cost: ${money(costValue)}`;
            },
          },
          grid: { left: 74, right: 78, top: 34, bottom: 145 },
          xAxis: {
            type: "category",
            data: shown.map((x) => x.label),
            axisLabel: {
              rotate: 38,
              interval: 0,
              fontSize: 10,
              margin: 14,
            },
          },
          yAxis: [
            {
              type: "value",
              name: "Time (seconds)",
              position: "left",
              nameLocation: "middle",
              nameGap: 48,
              axisLabel: { formatter: (v: number) => formatNumber(v) },
            },
            {
              type: "value",
              name: "Cost (USD)",
              position: "right",
              nameLocation: "middle",
              nameGap: 54,
              min: 0,
              max: maxTime,
              axisLabel: {
                formatter: (v: number) => money((v / maxTime) * maxCost),
              },
              splitLine: { show: false },
            },
          ],
          series: [
            {
              name: "Attributed time / successful involved run",
              type: "bar",
              data: shown.map((x) => x.time_per_positive_run),
              barMaxWidth: 28,
              itemStyle: { borderRadius: [5, 5, 0, 0] },
              label: {
                show: true,
                position: "top",
                formatter: (p: { value: number }) => seconds(p.value),
                fontSize: 9,
              },
            },
            {
              name: "Measured cost",
              type: "bar",
              data: shown.map((x) => ({
                value: scaledCost(x.measured_cost),
                raw: x.measured_cost || 0,
              })),
              barMaxWidth: 28,
              itemStyle: { borderRadius: [5, 5, 0, 0] },
              label: {
                show: true,
                position: "top",
                formatter: (p: { data: { raw: number } }) => money(p.data.raw),
                fontSize: 9,
              },
            },
          ],
        }}
      />
    </div>
  );
}

export function ProviderSpendChart({
  items,
  breakdown = "model",
  onSelect,
  height = 320,
}: {
  items: Performance[];
  breakdown?: "model" | "provider";
  onSelect?: (item: Performance) => void;
  height?: number;
}) {
  const data = items
    .filter((x) => x.measured_cost != null)
    .sort((a, b) => (b.measured_cost || 0) - (a.measured_cost || 0));
  return (
    <div>
      <ReactEChartsCore
        echarts={echarts}
        onEvents={onSelect ? { click: (point: { data?: { item?: Performance } }) => point.data?.item && onSelect(point.data.item) } : undefined}
        style={{ height, width: "100%" }}
        option={{
          color: chartColors,
          tooltip: {
            trigger: "item",
            formatter: (p: { name: string; value: number; percent: number }) =>
              `<b>${p.name}</b><br/>${formatNumber(p.percent)}% · ${money(p.value)}`,
          },
          series: [
            {
              type: "pie",
              radius: ["38%", "62%"],
              center: ["50%", "50%"],
              avoidLabelOverlap: true,
              minShowLabelAngle: 1,
              data: data.map((x, index) => ({
                name: breakdown === "provider" ? x.label.replace(/(^|[\s_-])\p{L}/gu, (match) => match.toUpperCase()) : x.label,
                value: x.measured_cost,
                item: x,
                itemStyle: {
                  color:
                    breakdown === "provider"
                      ? stableColor(x.participant_id || `provider:${x.label}`)
                      : chartColors[index % chartColors.length],
                },
              })),
              label: {
                show: true,
                position: "outside",
                formatter: "{b} · {d}%",
                color: "#3f3d45",
                fontSize: 11,
                fontWeight: 600,
                alignTo: "edge",
                edgeDistance: 10,
                bleedMargin: 4,
              },
              labelLine: {
                show: true,
                length: 12,
                length2: 8,
                lineStyle: { color: "#aaa6b2", width: 1 },
              },
              labelLayout: { moveOverlap: "shiftY" },
            },
          ],
        }}
      />
    </div>
  );
}

export function PerformanceList({ items }: { items: Performance[] }) {
  return (
    <div className="divide-y divide-[#eeeeea]">
      {items.slice(0, 8).map((item) => (
        <div
          key={item.label}
          className="grid grid-cols-[minmax(180px,1fr)_90px_100px_100px] items-center gap-3 py-3 text-sm"
        >
          <div className="min-w-0">
            <div className="truncate font-medium">{item.label}</div>
            <div className="mt-1">
              <ProgressBar value={Math.max(0, 100 - item.failure_rate * 100)} />
            </div>
          </div>
          <span>{formatNumber(item.runs)} runs</span>
          <span>{seconds(item.time_per_positive_run)}</span>
          <span>{money(item.measured_cost)}</span>
        </div>
      ))}
    </div>
  );
}

export function CostLatencyScatter({ items, xLabel = "Attributable model time / run (seconds)" }: { items: ComparisonInsight[]; xLabel?: string }) {
  const shown = items.filter((item) => item.avg_duration_seconds != null && item.avg_cost_per_run != null);
  if (!shown.length) return <Empty>No comparable model activity in this view.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      style={{ height: 410, width: "100%" }}
      option={{
        tooltip: {
          trigger: "item",
          formatter: (point: { data: { name: string; value: number[]; item: ComparisonInsight } }) => {
            const item = point.data.item;
            return `<b>${item.label}</b><br/>${formatNumber(item.runs)} runs<br/>Time / run: ${seconds(item.avg_duration_seconds)}<br/>Cost / run: ${money(item.avg_cost_per_run)}<br/>p95: ${seconds(item.p95_duration_seconds)}<br/>Tokens / run: ${formatNumber(item.avg_tokens_per_run)}<br/>Goal rate: ${percent(item.goal_rate)}`;
          },
        },
        legend: {
          type: "scroll",
          bottom: 0,
          left: 72,
          right: 24,
          itemWidth: 10,
          itemHeight: 10,
          icon: "circle",
          textStyle: { color: "#5f5f5a", fontSize: 11 },
        },
        grid: { left: 76, right: 34, top: 20, bottom: 86 },
        xAxis: {
          type: "value",
          name: xLabel,
          nameLocation: "middle",
          nameGap: 40,
          min: 0,
          splitNumber: 5,
          axisLine: { lineStyle: { color: "#8b8b86" } },
          splitLine: { lineStyle: { color: "#ecece7" } },
        },
        yAxis: {
          type: "value",
          name: "Cost / run (USD)",
          nameLocation: "middle",
          nameGap: 56,
          min: 0,
          splitNumber: 4,
          axisLine: { show: true, lineStyle: { color: "#8b8b86" } },
          splitLine: { lineStyle: { color: "#ecece7" } },
          axisLabel: { formatter: (value: number) => money(value) },
        },
        series: shown.map((item, index) => ({
          name: item.label,
          type: "scatter",
          clip: false,
          itemStyle: { color: chartColors[index % chartColors.length], opacity: 0.88 },
          data: [{
            name: item.label,
            value: [item.avg_duration_seconds, item.avg_cost_per_run, item.runs],
            item,
            symbolSize: Math.max(14, Math.min(42, 10 + Math.sqrt(item.runs) * 5)),
          }],
          emphasis: {
            focus: "series",
            label: {
              show: true,
              position: "top",
              formatter: "{b}",
              fontSize: 11,
              fontWeight: 600,
              color: "#353532",
              backgroundColor: "rgba(255,255,255,0.94)",
              borderRadius: 4,
              padding: [3, 5],
            },
          },
        })),
      }}
    />
  );
}

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return 1;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

export function NormalizedComparisonChart({ items }: { items: ComparisonInsight[] }) {
  const shown = items.slice(0, 10);
  const metrics = [
    ["Time", "avg_duration_seconds"],
    ["Cost", "avg_cost_per_run"],
    ["Tokens", "avg_tokens_per_run"],
    ["Calls", "avg_calls_per_run"],
  ] as const;
  const baselines = Object.fromEntries(metrics.map(([, key]) => [key, median(shown.map((item) => item[key] || 0).filter((value) => value > 0))]));
  if (!shown.length) return <Empty>No comparable activity.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      style={{ height: Math.max(300, shown.length * 48), width: "100%" }}
      option={{
        color: chartColors,
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value: number) => `${formatNumber(value)}× median` },
        legend: { top: 0 },
        grid: { left: 205, right: 36, top: 42, bottom: 42 },
        xAxis: { type: "value", name: "Relative to selected median", nameLocation: "middle", nameGap: 28, axisLabel: { formatter: (value: number) => `${value}×` } },
        yAxis: { type: "category", data: shown.map((item) => item.label).reverse(), axisLabel: { width: 188, overflow: "break", lineHeight: 16 } },
        series: metrics.map(([name, key]) => ({
          name,
          type: "bar",
          barMaxWidth: 12,
          data: shown.map((item) => (item[key] == null ? null : Number(((item[key] || 0) / (baselines[key] || 1)).toFixed(3)))).reverse(),
        })),
      }}
    />
  );
}

export function QualityComparisonChart({ items }: { items: ComparisonInsight[] }) {
  const rows = items.flatMap((item) => (item.evaluations || []).map((evaluation) => ({
    participant: item.label,
    evaluation: evaluation.name,
    label: `${item.label}\n${evaluation.name}`,
    score: evaluation.average_score,
    target: typeof evaluation.target === "number" ? evaluation.target : null,
    runs: evaluation.reported_runs,
  }))).filter((row) => row.score != null).slice(0, 12).reverse();
  if (!rows.length) return <Empty>No evaluation scores are reported for this selection.</Empty>;
  const maximum = Math.max(1, ...rows.flatMap((row) => [row.score || 0, row.target || 0]));
  return (
    <ReactEChartsCore echarts={echarts} style={{ height: Math.max(290, rows.length * 56), width: "100%" }} option={{
      color: ["#6d4aff", "#282824"],
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ dataIndex: number }>) => { const row = rows[points[0]?.dataIndex || 0]; return `<b>${row.participant}</b><br/>${row.evaluation}<br/>Average: ${formatNumber(row.score)}<br/>Target: ${formatNumber(row.target)}<br/>${formatNumber(row.runs)} evaluated runs`; } },
      legend: { top: 0, itemWidth: 12, itemHeight: 8 },
      grid: { left: 285, right: 48, top: 44, bottom: 34 },
      xAxis: { type: "value", min: 0, max: maximum, splitNumber: 5, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: rows.map((row) => row.label), axisLabel: { width: 260, overflow: "break", lineHeight: 17 } },
      series: [
        {
          name: "Average score",
          type: "bar",
          data: rows.map((row) => row.score),
          barMaxWidth: 18,
          showBackground: true,
          backgroundStyle: { color: "#f0efeb", borderRadius: 4 },
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: "insideRight", formatter: ({ value }: { value: number }) => formatNumber(value), color: "#fff", fontWeight: 600 },
        },
        {
          name: "Declared target",
          type: "scatter",
          symbol: "rect",
          symbolSize: [4, 24],
          data: rows.map((row) => row.target == null ? null : [row.target, row.label]),
          z: 4,
        },
      ],
    }} />
  );
}

export function GoalTradeoffChart({ items, onSelect }: { items: ComparisonInsight[]; onSelect?: (item: ComparisonInsight) => void }) {
  const shown = items.filter((item) => item.goal_rate != null && item.avg_cost_per_run != null);
  if (!shown.length) return <Empty>No measured goal-and-cost combinations in this view.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      onEvents={onSelect ? { click: (point: { data?: { item?: ComparisonInsight } }) => point.data?.item && onSelect(point.data.item) } : undefined}
      style={{ height: 360, width: "100%" }}
      option={{
        color: chartColors,
        tooltip: {
          trigger: "item",
          formatter: (point: { data: { item: ComparisonInsight } }) => {
            const item = point.data.item;
            return `<b>${item.label}</b><br/>Goal achievement for involved runs: ${percent(item.goal_rate)}<br/>Direct cost / involved run: ${money(item.avg_cost_per_run)}<br/>Attributed active time / involved run: ${seconds(item.avg_duration_seconds)}<br/>${formatNumber(item.runs)} involved runs`;
          },
        },
        legend: { type: "scroll", bottom: 0, left: 72, right: 24, itemWidth: 10, itemHeight: 10, icon: "circle" },
        grid: { left: 76, right: 34, top: 18, bottom: 78 },
        xAxis: {
          type: "value",
          name: "Measured cost / run",
          nameLocation: "middle",
          nameGap: 38,
          min: 0,
          axisLabel: { formatter: (value: number) => money(value) },
          splitLine: { lineStyle: { color: "#ecece7" } },
        },
        yAxis: {
          type: "value",
          name: "Goal achievement",
          min: 0,
          max: 1,
          axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` },
          splitLine: { lineStyle: { color: "#ecece7" } },
        },
        series: shown.map((item, index) => ({
          name: item.label,
          type: "scatter",
          itemStyle: { color: chartColors[index % chartColors.length], opacity: 0.88 },
          data: [{
            value: [item.avg_cost_per_run, item.goal_rate, item.runs],
            item,
            symbolSize: Math.max(14, Math.min(40, 10 + Math.sqrt(item.runs) * 5)),
          }],
          emphasis: { focus: "series" },
        })),
      }}
    />
  );
}

export function GoalRateColumns({ items, onSelect, height = 330 }: { items: ComparisonInsight[]; onSelect?: (item: ComparisonInsight) => void; height?: number }) {
  const shown = [...items].filter((item) => item.goal_rate != null).sort((a, b) => b.runs - a.runs).slice(0, 8);
  if (!shown.length) return <Empty>No participant cohorts have reported goal outcomes in this view.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      onEvents={onSelect ? { click: (point: { data?: { item?: ComparisonInsight } }) => point.data?.item && onSelect(point.data.item) } : undefined}
      style={{ height, width: "100%" }}
      option={{
        color: ["#6d4aff", "#27a46b"],
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value: number) => percent(value) },
        legend: { top: 0 },
        grid: { left: 54, right: 20, top: 42, bottom: 90 },
        xAxis: { type: "category", data: shown.map((item) => item.label), axisLabel: { rotate: 30, interval: 0, width: 100, overflow: "truncate" } },
        yAxis: { type: "value", min: 0, max: 1, axisLabel: { formatter: (value: number) => `${Math.round(value * 100)}%` }, splitLine: { lineStyle: { color: "#ecece7" } } },
        series: [
          { name: "Goal achievement", type: "bar", barMaxWidth: 24, data: shown.map((item) => ({ value: item.goal_rate, item })), itemStyle: { borderRadius: [5, 5, 0, 0] } },
          { name: "Decision correctness", type: "bar", barMaxWidth: 24, data: shown.map((item) => ({ value: item.decision_correctness_rate, item })), itemStyle: { borderRadius: [5, 5, 0, 0] } },
        ],
      }}
    />
  );
}

export function LatencyVariabilityChart({ items, onSelect, height }: { items: ComparisonInsight[]; onSelect?: (item: ComparisonInsight) => void; height?: number }) {
  const rows = items.filter((item) => item.p50_duration_seconds != null && item.p95_duration_seconds != null).slice(0, 10).reverse();
  if (!rows.length) return <Empty>No duration distribution is available.</Empty>;
  return (
    <ReactEChartsCore echarts={echarts} onEvents={onSelect ? { click: (point: { data?: { item?: ComparisonInsight } }) => point.data?.item && onSelect(point.data.item) } : undefined} style={{ height: height ?? Math.max(280, rows.length * 46), width: "100%" }} option={{
      color: ["#6d4aff", "#d8d2ff"],
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ dataIndex: number }>) => { const row = rows[points[0]?.dataIndex || 0]; return `<b>${row.label}</b><br/>p50: ${seconds(row.p50_duration_seconds)}<br/>p95: ${seconds(row.p95_duration_seconds)}<br/>Tail spread: ${seconds((row.p95_duration_seconds || 0) - (row.p50_duration_seconds || 0))}`; } },
      legend: { top: 0 },
      grid: { left: 225, right: 70, top: 42, bottom: 38 },
      xAxis: { type: "value", name: "Seconds", nameLocation: "middle", nameGap: 28, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: rows.map((row) => row.label), axisLabel: { width: 208, overflow: "break", lineHeight: 16 } },
      series: [
        { name: "p50", type: "bar", stack: "latency", data: rows.map((row) => ({ value: row.p50_duration_seconds, item: row })), barMaxWidth: 18, itemStyle: { borderRadius: [4, 0, 0, 4] } },
        {
          name: "p50 → p95",
          type: "bar",
          stack: "latency",
          data: rows.map((row) => ({ value: (row.p95_duration_seconds || 0) - (row.p50_duration_seconds || 0), item: row })),
          barMaxWidth: 18,
          itemStyle: { borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: "right", formatter: ({ dataIndex }: { dataIndex: number }) => seconds(rows[dataIndex].p95_duration_seconds), color: "#64645f", fontSize: 11 },
        },
      ],
    }} />
  );
}

export function WorkflowStageContribution({ items }: { items: WorkflowStage[] }) {
  const [metric, setMetric] = useState<"time" | "cost" | "tokens">("time");
  const value = (item: WorkflowStage) => metric === "time" ? item.time_seconds : metric === "cost" ? item.known_cost || 0 : item.total_tokens || 0;
  const shown = [...items].sort((a, b) => value(b) - value(a)).slice(0, 10).reverse();
  return (
    <div>
      <div className="mb-3 flex justify-end gap-1">
        {(["time", "cost", "tokens"] as const).map((choice) => <button key={choice} onClick={() => setMetric(choice)} className={`rounded-md px-3 py-1.5 text-xs font-medium ${metric === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{choice}</button>)}
      </div>
      {shown.length ? <ReactEChartsCore echarts={echarts} style={{ height: 390, width: "100%" }} option={{
        color: ["#6d4aff"],
        tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (raw: number) => metric === "time" ? seconds(raw) : metric === "cost" ? money(raw) : formatNumber(raw) },
        grid: { left: 205, right: 32, top: 12, bottom: 32 },
        xAxis: { type: "value" },
        yAxis: { type: "category", data: shown.map((item) => `${item.workflow} · ${item.label}`), axisLabel: { width: 190, overflow: "truncate" } },
        series: [{ type: "bar", data: shown.map(value), barMaxWidth: 18, itemStyle: { borderRadius: [0, 5, 5, 0] } }],
      }} /> : <Empty>No semantic stages were reported.</Empty>}
    </div>
  );
}

export function StageAccumulation({ items }: { items: Overview["stages"] }) {
  const [sortBy, setSortBy] = useState<"time" | "tokens" | "cost">("time");
  const value = (item: Overview["stages"][number]) =>
    sortBy === "time"
      ? item.time_seconds
      : sortBy === "tokens"
        ? item.total_tokens
        : item.known_cost;
  const available = (metric: "time" | "tokens" | "cost") => metric === "time" || items.some((item) =>
    metric === "cost" ? item.cost_eligible_operations > 0 : item.token_eligible_operations > 0,
  );
  const shown = [...items].filter((item) => value(item) != null).sort((a, b) => Number(value(b)) - Number(value(a))).slice(0, 10);
  const maximum = Math.max(1e-9, ...shown.map((item) => Number(value(item))));
  return (
    <div>
      <div className="mb-4 flex flex-wrap justify-end gap-1">
        {(["time", "tokens", "cost"] as const).map((metric) => (
          <button
            key={metric}
            disabled={!available(metric)}
            onClick={() => setSortBy(metric)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40 ${sortBy === metric ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}
          >
            Sort by {metric}
          </button>
        ))}
      </div>
      {shown.length ? (
        <div className="space-y-3">
          {shown.map((item) => (
            <div key={item.label} className="grid gap-2 sm:grid-cols-[minmax(160px,1.2fr)_minmax(160px,2fr)_80px_90px_90px] sm:items-center">
              <div className="min-w-0 text-sm font-medium" title={item.label}>{item.label}</div>
              <div className="h-2.5 overflow-hidden rounded-full bg-[#ecebe7]">
                <div className="h-full rounded-full bg-[#6d4aff]" style={{ width: `${Math.max(2, (Number(value(item)) / maximum) * 100)}%` }} />
              </div>
              <div className="text-xs text-[#666]">{seconds(item.time_seconds)}</div>
              <div className="text-xs text-[#666]">{item.token_eligible_operations === 0 ? "Not applicable" : item.total_tokens == null ? "Not measured" : `${formatNumber(item.total_tokens)} tokens`}</div>
              <div className="text-xs text-[#666]">{item.cost_eligible_operations === 0 ? "Not applicable" : item.known_cost == null ? "Not measured" : money(item.known_cost)}</div>
            </div>
          ))}
        </div>
      ) : <Empty>{available(sortBy) ? "No measured values for this metric." : "This metric is not applicable in this view."}</Empty>}
    </div>
  );
}

export function GoalTrendChart({ items }: { items: Overview["goal_trend"] }) {
  const [metric, setMetric] = useState<"success" | "time" | "cost">("success");
  const values = items.map((item) =>
    metric === "success"
      ? item.success_rate * 100
      : metric === "time"
        ? item.time_per_achieved_goal
        : item.cost_per_achieved_goal,
  );
  const label = metric === "success" ? "Goal success" : metric === "time" ? "Time / achieved goal" : "Cost / achieved goal";
  const formatted = (value: number | null | undefined) =>
    metric === "success" ? percent((value || 0) / 100) : metric === "time" ? seconds(value) : money(value);
  return (
    <div>
      <div className="mb-2 flex justify-end gap-1">
        {(["success", "time", "cost"] as const).map((value) => (
          <button
            key={value}
            onClick={() => setMetric(value)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ${metric === value ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}
          >
            {value === "success" ? "Goal success" : value === "time" ? "Time" : "Cost"}
          </button>
        ))}
      </div>
      {items.length ? (
        <ReactEChartsCore
          echarts={echarts}
          style={{ height: 260, width: "100%" }}
          option={{
            color: ["#6d4aff"],
            tooltip: {
              trigger: "axis",
              formatter: (points: Array<{ axisValue: string; value: number; dataIndex: number }>) => {
                const point = points[0];
                const item = items[point?.dataIndex || 0];
                const coverage = metric === "cost"
                  ? `${formatNumber(item.cost_runs)} measured of ${formatNumber(item.achieved_runs)} achieved`
                  : metric === "time"
                    ? `${formatNumber(item.duration_runs)} timed of ${formatNumber(item.achieved_runs)} achieved`
                    : `${formatNumber(item.achieved_runs)} achieved of ${formatNumber(item.reported_runs)} reported`;
                return `<b>${formatBrowserDate(item?.date)}</b><br/>${label}: ${formatted(point?.value)}<br/>${coverage}`;
              },
            },
            grid: { left: 64, right: 24, top: 20, bottom: 44 },
            xAxis: { type: "category", data: items.map((item) => formatBrowserDate(item.date)), axisLabel: { hideOverlap: true } },
            yAxis: {
              type: "value",
              min: 0,
              max: metric === "success" ? 100 : undefined,
              axisLabel: { formatter: (value: number) => formatted(value) },
            },
            series: [{ name: label, type: "line", smooth: true, symbolSize: 8, data: values, connectNulls: false }],
          }}
        />
      ) : <Empty>No reported goal history in this view.</Empty>}
    </div>
  );
}

export function RatioDonutChart({
  value,
  achievedLabel,
  remainderLabel,
  detail,
  height = 190,
}: {
  value: number;
  achievedLabel: string;
  remainderLabel: string;
  detail: string;
  height?: number;
}) {
  const clamped = Math.max(0, Math.min(1, value));
  return <ReactEChartsCore echarts={echarts} style={{ height, width: "100%" }} option={{
    tooltip: { trigger: "item", formatter: (point: { name: string; percent: number }) => `<b>${point.name}</b><br/>${formatNumber(point.percent)}%<br/>${detail}` },
    legend: { orient: "vertical", right: 2, top: "center", itemWidth: height < 160 ? 7 : 10, itemHeight: height < 160 ? 7 : 10, textStyle: { fontSize: height < 160 ? 9 : 11 }, selectedMode: true },
    title: { text: clamped ? percent(clamped) : "—", subtext: achievedLabel, left: height < 160 ? "27%" : "31%", top: "35%", textAlign: "center", textStyle: { color: "#342f39", fontSize: height < 160 ? 17 : 22, fontWeight: 700 }, subtextStyle: { color: "#817b83", fontSize: height < 160 ? 8 : 10 } },
    series: [{ type: "pie", radius: ["49%", "70%"], center: [height < 160 ? "28%" : "31%", "50%"], label: { show: false }, data: [
      { name: achievedLabel, value: clamped, label: { show: true, position: "center", formatter: clamped ? percent(clamped) : "—", color: "#342f39", fontSize: height < 160 ? 17 : 22, fontWeight: 700 }, itemStyle: { color: "#7153b5", borderColor: "#fff", borderWidth: 2 } },
      { name: remainderLabel, value: 1 - clamped, itemStyle: { color: "#e8e5eb", borderColor: "#fff", borderWidth: 2 } },
    ] }],
  }} />;
}

export function ExecutionTrendChart({ runs, height = 210 }: { runs: Run[]; height?: number }) {
  const ordered = [...runs].filter((run) => typeof run.duration_seconds === "number").sort((left, right) => String(left.started_at || "").localeCompare(String(right.started_at || "")));
  if (!ordered.length) return <Empty>No latency measurements were reported.</Empty>;
  return <ReactEChartsCore echarts={echarts} style={{ height, width: "100%" }} option={{
    color: ["#7153b5"],
    tooltip: { trigger: "axis", formatter: (points: Array<{ data: { run: Run; value: number } }>) => { const run = points[0]?.data.run; return run ? `<b>${formatDateTime(run.started_at)}</b><br/>Elapsed: ${seconds(run.duration_seconds)}<br/>Retries: ${formatNumber(Number(run.workflow_retry_attempts || 0))}<br/>${String(run.application_outcome || run.runtime_outcome || "Not reported").replaceAll("_", " ")}` : ""; } },
    legend: { top: 0, data: ["Elapsed"], itemWidth: 10, itemHeight: 7, textStyle: { fontSize: 9 } },
    grid: { left: 52, right: 12, top: 28, bottom: 34 },
    xAxis: { type: "category", name: "Execution order", nameLocation: "middle", nameGap: 24, data: ordered.map((_run, index) => `Run ${index + 1}`), nameTextStyle: { fontSize: 9 }, axisLabel: { hideOverlap: true, fontSize: 9 } },
    yAxis: { type: "value", axisLabel: { formatter: (value: number) => seconds(value), fontSize: 9 }, splitLine: { lineStyle: { color: "#ecece7" } } },
    series: [{ name: "Elapsed", type: "line", smooth: true, symbolSize: 8, emphasis: { focus: "series" }, data: ordered.map((run) => ({ value: run.duration_seconds, run })) }],
  }} />;
}

export function RetryPressureChart({ runs, height = 210 }: { runs: Run[]; height?: number }) {
  const shown = runs.filter((run) => typeof run.duration_seconds === "number");
  if (!shown.length) return <Empty>No execution measurements were reported.</Empty>;
  const groups = ["No retries", "Retried"];
  return <ReactEChartsCore echarts={echarts} style={{ height, width: "100%" }} option={{
    color: ["#8068b7", "#d58b24"],
    tooltip: { trigger: "item", formatter: (point: { data: { run: Run } }) => { const run = point.data.run; return `<b>${formatDateTime(run.started_at)}</b><br/>Elapsed: ${seconds(run.duration_seconds)}<br/>Retries: ${formatNumber(Number(run.workflow_retry_attempts || 0))}<br/>Goal: ${run.product_goal_achieved === true ? "achieved" : run.product_goal_achieved === false ? "not achieved" : "not reported"}`; } },
    legend: { top: 0, data: groups, selectedMode: true, itemWidth: 9, itemHeight: 7, textStyle: { fontSize: 9 } },
    grid: { left: 36, right: 10, top: 28, bottom: 34 },
    xAxis: { type: "value", name: "Elapsed", nameLocation: "middle", nameGap: 24, nameTextStyle: { fontSize: 9 }, axisLabel: { formatter: (value: number) => seconds(value), fontSize: 9 }, splitLine: { lineStyle: { color: "#ecece7" } } },
    yAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 9 }, splitLine: { lineStyle: { color: "#ecece7" } } },
    series: groups.map((name, index) => ({ name, type: "scatter", symbolSize: (value: number[]) => 11 + Math.min(8, value[1] * 2), emphasis: { focus: "series", scale: 1.4 }, data: shown.filter((run) => (Number(run.workflow_retry_attempts || 0) > 0) === Boolean(index)).map((run) => ({ value: [run.duration_seconds, Number(run.workflow_retry_attempts || 0)], run })) })),
  }} />;
}

export function AttributionHealthChart({ items, dimension, onSelect, height = 260, completedIsSupporting = false }: { items: Performance[]; dimension: "model" | "provider"; onSelect?: (item: Performance) => void; height?: number; completedIsSupporting?: boolean }) {
  const [metric, setMetric] = useState<"reliability" | "cost" | "tokens">("reliability");
  const shown = [...items].sort((a, b) => b.runs - a.runs).slice(0, 10).reverse();
  const hasMetric = metric === "reliability" ? shown.length > 0 : metric === "cost" ? shown.some((item) => item.measured_cost != null) : shown.some((item) => item.total_tokens != null);
  return <div>
    <div className="mb-2 flex justify-end gap-1">{(["reliability", "cost", "tokens"] as const).map((choice) => <button key={choice} type="button" onClick={() => setMetric(choice)} className={`rounded-md px-2.5 py-1 text-[11px] font-medium ${metric === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{choice === "reliability" ? "Outcomes" : choice[0].toUpperCase() + choice.slice(1)}</button>)}</div>
    {!shown.length ? <Empty>No {dimension} calls were attributed to this workflow.</Empty> : !hasMetric ? <Empty>{metric === "cost" ? "Cost" : "Token"} telemetry is not measured for these {dimension}s.</Empty> : <ReactEChartsCore echarts={echarts} onEvents={onSelect ? { click: (point: { data?: { item?: Performance } }) => point.data?.item && onSelect(point.data.item) } : undefined} style={{ height, width: "100%" }} option={{
      color: metric === "reliability" ? [completedIsSupporting ? "#8fcfab" : "#16864b", "#d58b24", "#dc5a5a"] : ["#7153b5"],
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ data: { item: Performance }; seriesName: string; value: number }>) => { const item = points[0]?.data.item; if (!item) return ""; return `<b>${item.label}</b><br/>${formatNumber(item.runs)} runs<br/>Completed: ${formatNumber(item.completed)}<br/>Recovered: ${formatNumber(item.recovered)}<br/>Failed: ${formatNumber(item.failed)}<br/>Cost: ${money(item.measured_cost)}<br/>Tokens: ${formatNumber(item.total_tokens)}`; } },
      legend: { top: 0, selectedMode: true, itemWidth: 9, itemHeight: 7, textStyle: { fontSize: 9 } },
      grid: { left: 150, right: 14, top: 34, bottom: 22 },
      xAxis: { type: "value", splitNumber: 3, minInterval: metric === "reliability" ? 1 : undefined, axisLabel: { formatter: (value: number) => metric === "cost" ? money(value) : formatNumber(value), fontSize: 8, hideOverlap: true, margin: 6 }, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: shown.map((item) => item.label), axisLabel: { width: 136, overflow: "truncate", fontSize: 9, margin: 8 } },
      series: metric === "reliability" ? [
        { name: "Completed", type: "bar", stack: "outcome", data: shown.map((item) => ({ value: Math.max(0, item.completed - item.recovered), item })) },
        { name: "Recovered", type: "bar", stack: "outcome", data: shown.map((item) => ({ value: item.recovered, item })) },
        { name: "Failed", type: "bar", stack: "outcome", data: shown.map((item) => ({ value: item.failed, item })) },
      ] : [{ name: metric === "cost" ? "Measured cost" : "Tokens", type: "bar", data: shown.map((item) => ({ value: metric === "cost" ? item.measured_cost : item.total_tokens, item })), itemStyle: { borderRadius: [0, 4, 4, 0] } }],
    }} />}
  </div>;
}

export function StageDiagnosticsChart({ items, height = 310 }: { items: Overview["stages"]; height?: number }) {
  const [metric, setMetric] = useState<"time" | "failures" | "retries" | "cost" | "tokens">("time");
  const value = (item: Overview["stages"][number]) => metric === "time" ? item.time_seconds : metric === "failures" ? item.failures : metric === "retries" ? Number(item.extra_attempts || 0) : metric === "cost" ? item.known_cost : item.total_tokens;
  const measured = items.filter((item) => value(item) != null);
  const shown = [...measured].sort((a, b) => Number(value(b) || 0) - Number(value(a) || 0)).slice(0, 10).reverse();
  return <div>
    <div className="mb-2 flex flex-wrap justify-end gap-1">{(["time", "failures", "retries", "cost", "tokens"] as const).map((choice) => <button key={choice} type="button" onClick={() => setMetric(choice)} className={`rounded-md px-2.5 py-1 text-[11px] font-medium ${metric === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{choice === "time" ? "Elapsed" : choice[0].toUpperCase() + choice.slice(1)}</button>)}</div>
    {!shown.length ? <Empty>{metric === "cost" ? "Cost" : metric === "tokens" ? "Token" : metric} telemetry is not reported by step.</Empty> : <ReactEChartsCore echarts={echarts} style={{ height: Math.max(height, shown.length * 20), width: "100%" }} option={{
      color: [metric === "failures" ? "#dc5a5a" : metric === "retries" ? "#d58b24" : "#7153b5"],
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ data: { item: Overview["stages"][number] } }>) => { const item = points[0]?.data.item; return item ? `<b>${item.label}</b><br/>Elapsed: ${seconds(item.time_seconds)}<br/>Failures: ${formatNumber(item.failures)}<br/>Extra attempts: ${formatNumber(item.extra_attempts)}<br/>Cost: ${money(item.known_cost)}<br/>Tokens: ${formatNumber(item.total_tokens)}` : ""; } },
      legend: { top: 0, data: [metric === "time" ? "Elapsed" : metric[0].toUpperCase() + metric.slice(1)], itemWidth: 9, itemHeight: 7, textStyle: { fontSize: 9 } },
      grid: { left: 128, right: 18, top: 28, bottom: 22 },
      xAxis: { type: "value", splitNumber: 4, minInterval: metric === "failures" || metric === "retries" ? 1 : undefined, axisLabel: { fontSize: 8, hideOverlap: true, margin: 5, formatter: (raw: number) => metric === "time" ? seconds(raw) : metric === "cost" ? money(raw) : formatNumber(raw) }, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: shown.map((item) => item.label), axisLabel: { width: 116, overflow: "truncate", fontSize: 8 } },
      series: [{ name: metric === "time" ? "Elapsed" : metric[0].toUpperCase() + metric.slice(1), type: "bar", data: shown.map((item) => ({ value: value(item), item })), barMaxWidth: 16, itemStyle: { borderRadius: [0, 4, 4, 0] }, emphasis: { focus: "series" } }],
    }} />}
  </div>;
}

export function OperationHealthChart({ items, height = 260 }: { items: OperationTypeSummary[]; height?: number }) {
  const [metric, setMetric] = useState<"volume" | "time" | "failures">("volume");
  const value = (item: OperationTypeSummary) => metric === "volume" ? item.operations : metric === "time" ? item.active_seconds : item.failed;
  const shown = [...items].sort((left, right) => value(right) - value(left)).slice(0, 10).reverse();
  const name = metric === "volume" ? "Operations" : metric === "time" ? "Active time" : "Failures";
  return <div>
    <div className="mb-2 flex justify-end gap-1">{(["volume", "time", "failures"] as const).map((choice) => <button key={choice} type="button" onClick={() => setMetric(choice)} className={`rounded-md px-2.5 py-1 text-[11px] font-medium ${metric === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{choice === "volume" ? "Volume" : choice === "time" ? "Active time" : "Failures"}</button>)}</div>
    {!shown.length ? <Empty>No operation classifications have been materialized.</Empty> : metric === "failures" && !shown.some((item) => item.failed) ? <Empty>No operation failures were observed in this selection.</Empty> : <AnalyticsChart echarts={echarts} style={{ height: Math.max(height, shown.length * 38), width: "100%" }} option={{
      color: [metric === "failures" ? "#dc5a5a" : metric === "time" ? "#2477e6" : "#6d4aff"],
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (points: Array<{ data: { item: OperationTypeSummary } }>) => { const item = points[0]?.data.item; return item ? `<b>${humanizeOperationType(item.type)}</b><br/>${formatNumber(item.operations)} operations<br/>Active time: ${seconds(item.active_seconds)}<br/>Failures: ${formatNumber(item.failed)}<br/>Interface: ${item.interfaces.join(", ") || "unknown"}<br/>Providers: ${item.providers.join(", ") || "not reported"}` : ""; } },
      grid: { left: 142, right: 28, top: 12, bottom: 30 },
      xAxis: { type: "value", minInterval: metric === "time" ? undefined : 1, name, nameLocation: "middle", nameGap: 24, nameTextStyle: { fontSize: 9 }, axisLabel: { fontSize: 8, formatter: (raw: number) => metric === "time" ? seconds(raw) : formatNumber(raw) }, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: shown.map((item) => humanizeOperationType(item.type)), axisLabel: { width: 132, overflow: "truncate", fontSize: 9 } },
      series: [{ name, type: "bar", barMaxWidth: 18, data: shown.map((item) => ({ value: value(item), item })), itemStyle: { borderRadius: [0, 4, 4, 0] }, emphasis: { focus: "series" } }],
    }} />}
  </div>;
}

const humanizeOperationType = (value: string) => {
  if (value === "component") return "Workflow step";
  if (value === "unknown" || value === "x.witdem.unclassified") return "Other / Unknown";
  return value.replace(/^x\.[^.]+\./, "").replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
};

export function ExecutionStepDiagnostics({ nodes, onSelect, height = 360, completedIsSupporting = false }: { nodes: ProjectedWorkflowNode[]; onSelect?: (node: ProjectedWorkflowNode) => void; height?: number; completedIsSupporting?: boolean }) {
  const [metric, setMetric] = useState<"time" | "attempts" | "cost" | "tokens">("time");
  const value = (node: ProjectedWorkflowNode) => metric === "time" ? node.duration_seconds : metric === "attempts" ? node.attempts : metric === "cost" ? node.known_cost : node.total_tokens;
  const executed = nodes.filter((node) => node.state !== "inactive" && value(node) != null);
  const shown = [...executed].sort((a, b) => Number(value(b) || 0) - Number(value(a) || 0)).slice(0, 12).reverse();
  const states = ["Completed", "Recovered", "Failed"];
  return <div>
    <div className="mb-2 flex justify-end gap-1">{(["time", "attempts", "cost", "tokens"] as const).map((choice) => <button key={choice} type="button" onClick={() => setMetric(choice)} className={`rounded-md px-2.5 py-1 text-[11px] font-medium ${metric === choice ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}>{choice === "time" ? "Elapsed" : choice[0].toUpperCase() + choice.slice(1)}</button>)}</div>
    {!shown.length ? <Empty>{metric === "cost" ? "Cost" : metric === "tokens" ? "Token" : metric} telemetry is not reported for executed steps.</Empty> : <ReactEChartsCore echarts={echarts} onEvents={onSelect ? { click: (point: { data?: { node?: ProjectedWorkflowNode } }) => point.data?.node && onSelect(point.data.node) } : undefined} style={{ height: Math.max(height, shown.length * 18), width: "100%" }} option={{
      color: [completedIsSupporting ? "#8fcfab" : "#16864b", "#d58b24", "#dc5a5a"],
      tooltip: { trigger: "item", formatter: (point: { data: { node: ProjectedWorkflowNode } }) => { const node = point.data.node; return `<b>${node.name}</b><br/>State: ${node.state}<br/>Elapsed: ${seconds(node.duration_seconds)}<br/>Attempts: ${formatNumber(node.attempts)}<br/>Provider: ${node.providers.join(", ") || "Not observed"}<br/>Model: ${node.models.join(", ") || "No model call"}<br/>Cost: ${money(node.known_cost)}<br/>Tokens: ${formatNumber(node.total_tokens)}<br/><span style="color:#7153b5">Select to inspect evidence</span>`; } },
      legend: { top: 0, data: states, selectedMode: true, itemWidth: 9, itemHeight: 7, textStyle: { fontSize: 9 } },
      grid: { left: 156, right: 20, top: 28, bottom: 24 },
      xAxis: { type: "value", splitNumber: 4, minInterval: metric === "attempts" ? 1 : undefined, axisLabel: { fontSize: 8, hideOverlap: true, margin: 5, formatter: (raw: number) => metric === "time" ? seconds(raw) : metric === "cost" ? money(raw) : formatNumber(raw) }, splitLine: { lineStyle: { color: "#ecece7" } } },
      yAxis: { type: "category", data: shown.map((node) => node.name), axisLabel: { width: 144, overflow: "truncate", fontSize: 8 } },
      series: states.map((state) => ({ name: state, type: "bar", stack: "step", barMaxWidth: 16, emphasis: { focus: "series" }, data: shown.map((node) => ({ value: node.state === state.toLowerCase() ? value(node) : 0, node })) })),
    }} />}
  </div>;
}

export function StatusBadge({ value }: { value?: string }) {
  const good = [
    "completed",
    "recovered",
    "achieved",
    "accepted",
    "success",
  ].includes(String(value).toLowerCase());
  const bad = ["failed", "broke", "error"].includes(
    String(value).toLowerCase(),
  );
  return (
    <Badge color={good ? "green" : bad ? "red" : "gray"}>
      {value || "Not reported"}
    </Badge>
  );
}
export { Badge, Button, useQuery, formatNumber, percent, money, seconds };
