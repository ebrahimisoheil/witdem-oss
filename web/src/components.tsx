import { Badge, Button, ProgressBar } from "@lemonsqueezy/wedges";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { lazy, Suspense, useState } from "react";
import type { ComparisonInsight, Overview, Performance, RunDetail, WorkflowStage } from "./api";
import { formatNumber, money, percent, seconds } from "./api";

const AdvancedWorkflowGraph = lazy(() =>
  import("./advanced-workflow-graph").then((module) => ({
    default: module.AdvancedWorkflowGraph,
  })),
);

echarts.use([
  ScatterChart,
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  LabelLayout,
  CanvasRenderer,
]);

const chartColors = [
  "#6d4aff",
  "#2477e6",
  "#16a085",
  "#e38317",
  "#d34f6f",
  "#637083",
  "#9b59b6",
];
const providerColors: Record<string, string> = {
  Anthropic: "#6d4aff",
  OpenAI: "#2477e6",
  DeepSeek: "#16a085",
  Mistral: "#e38317",
  Ollama: "#637083",
  Other: "#9b59b6",
};
const familyColor = (family: string, fallbackIndex: number) =>
  providerColors[family] || chartColors[fallbackIndex % chartColors.length];
const modelFamily = (name: string) =>
  name.includes("claude")
    ? "Anthropic"
    : name.startsWith("gpt") || name.startsWith("o")
      ? "OpenAI"
      : name.includes("deepseek")
        ? "DeepSeek"
        : name.includes("mistral")
          ? "Mistral"
          : "Other";

const nav = [
  ["/", "Overview"],
  ["/system-health", "System health"],
  ["/goal-performance", "Goal performance"],
  ["/runs", "Runs"],
  ["/compare", "Compare"],
  ["/workflows", "Workflows"],
  ["/issues", "Issues"],
] as const;
export function Shell() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="min-h-screen bg-[#f8f8f5] text-[#242424]">
      <aside className="fixed inset-y-0 left-0 z-20 w-56 border-r border-[#e7e7e1] bg-white px-4 py-5">
        <Link to="/" className="mb-8 flex items-center gap-3 px-2">
          <span className="grid size-8 place-items-center rounded-xl bg-[#7047eb] text-sm font-black text-white">
            W
          </span>
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
          <Outlet />
        </div>
      </main>
    </div>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const isFetching = useIsFetching() > 0;
  return (
    <header className="mb-7 flex items-start justify-between gap-6">
      <div>
        {eyebrow && (
          <div className="mb-2 text-xs font-semibold uppercase tracking-[.12em] text-[#8062df]">
            {eyebrow}
          </div>
        )}
        <h1 className="text-[30px] font-semibold leading-tight tracking-[-.03em]">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-[#6d6d68]">
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
  const familyFor = (label: string) =>
    breakdown === "provider" ? label : modelFamily(label);
  const families = [...new Set(data.map((x) => familyFor(x.label)))];
  return (
    <div className="min-w-0">
      <div className="mb-1 flex flex-wrap justify-end gap-3">
        {families.map((family, index) => (
          <span
            key={family}
            className="flex items-center gap-1.5 text-xs text-[#666]"
          >
            <span
              className="size-2.5 rounded-full"
              style={{ background: familyColor(family, index) }}
            />
            {family}
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
              `<b>${p.data.name}</b><br/>${seconds(p.data.value[0])} per successful run<br/>${money(p.data.value[1])} measured spend<br/>${formatNumber(p.data.value[2])} runs`,
          },
          xAxis: {
            name: "Seconds per successful run",
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
          series: families.map((family, index) => ({
            name: family,
            type: "scatter",
            itemStyle: { color: familyColor(family, index) },
            symbolSize: (v: number[]) =>
              Math.max(13, Math.min(30, 10 + v[2] * 1.2)),
            data: data
              .filter((x) => familyFor(x.label) === family)
              .map((x) => ({
                name: x.label,
                value: [x.time_per_positive_run, x.measured_cost, x.runs],
              })),
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
  if (!total) return <Empty>No runtime states were reported.</Empty>;
  return (
    <ReactEChartsCore
      echarts={echarts}
      style={{ height, width: "100%" }}
      option={{
        tooltip: { trigger: "item", formatter: "{b}<br/>{c} runs · {d}%" },
        legend: { type: "scroll", orient: "vertical", right: 8, top: "center", itemWidth: 10, itemHeight: 10 },
        graphic: [
          { type: "text", left: "31%", top: "42%", style: { text: formatNumber(total), textAlign: "center", fill: "#292925", fontSize: 24, fontWeight: 700 } },
          { type: "text", left: "31%", top: "55%", style: { text: "runs", textAlign: "center", fill: "#7a7a74", fontSize: 11 } },
        ],
        series: [{
          type: "pie",
          radius: ["50%", "72%"],
          center: ["34%", "50%"],
          minShowLabelAngle: 5,
          label: { show: false },
          data: entries.map(([name, value]) => ({
            name: name.replaceAll("_", " "),
            value,
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
            Time / successful run
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
                (point) => point.seriesName === "Time / successful run",
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
              name: "Time / successful run",
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
                      ? familyColor(x.label, index)
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
            return `<b>${item.label}</b><br/>Goal achievement: ${percent(item.goal_rate)}<br/>Cost / run: ${money(item.avg_cost_per_run)}<br/>Time / run: ${seconds(item.avg_duration_seconds)}<br/>${formatNumber(item.runs)} runs`;
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
  if (!shown.length) return <Empty>No goal outcomes are attributable in this view.</Empty>;
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
        ? item.total_tokens || 0
        : item.known_cost || 0;
  const shown = [...items].sort((a, b) => value(b) - value(a)).slice(0, 10);
  const maximum = Math.max(1e-9, ...shown.map(value));
  return (
    <div>
      <div className="mb-4 flex flex-wrap justify-end gap-1">
        {(["time", "tokens", "cost"] as const).map((metric) => (
          <button
            key={metric}
            onClick={() => setSortBy(metric)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ${sortBy === metric ? "bg-[#6d4aff] text-white" : "bg-[#f2f1ed] text-[#666]"}`}
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
                <div className="h-full rounded-full bg-[#6d4aff]" style={{ width: `${Math.max(2, (value(item) / maximum) * 100)}%` }} />
              </div>
              <div className="text-xs text-[#666]">{seconds(item.time_seconds)}</div>
              <div className="text-xs text-[#666]">{formatNumber(item.total_tokens)} tokens</div>
              <div className="text-xs text-[#666]">{money(item.known_cost)}</div>
            </div>
          ))}
        </div>
      ) : <Empty>No workflow-stage measurements in this view.</Empty>}
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
              formatter: (points: Array<{ axisValue: string; value: number }>) =>
                `<b>${points[0]?.axisValue || ""}</b><br/>${label}: ${formatted(points[0]?.value)}`,
            },
            grid: { left: 64, right: 24, top: 20, bottom: 44 },
            xAxis: { type: "category", data: items.map((item) => item.date), axisLabel: { hideOverlap: true } },
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

export function WorkflowGraph({ detail }: { detail: RunDetail }) {
  return (
    <Suspense fallback={<LoadingPage />}>
      <AdvancedWorkflowGraph detail={detail} />
    </Suspense>
  );
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
