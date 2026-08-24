import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  NodeToolbar,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { RunDetail } from "./api";
import { formatNumber, money, seconds } from "./api";

type Tone = "violet" | "blue" | "green" | "amber" | "red" | "slate";
type Metric = { label: string; value: string };
type AgentNodeData = Record<string, unknown> & {
  lane: "runtime" | "business";
  graphRole: "spine" | "branch" | "parallel";
  eyebrow: string;
  title: string;
  subtitle?: string;
  badge?: string;
  tone: Tone;
  metrics: Metric[];
  details: Metric[];
};
type AgentNode = Node<AgentNodeData, "agentNode">;

const NODE_WIDTH = 184;
const NODE_HEIGHT = 96;

const toneClasses: Record<Tone, { shell: string; accent: string; badge: string }> = {
  violet: {
    shell: "border-violet-300 bg-white shadow-[0_8px_28px_rgba(93,64,190,0.10)]",
    accent: "bg-violet-500",
    badge: "bg-violet-50 text-violet-700 ring-violet-200",
  },
  blue: {
    shell: "border-blue-300 bg-white shadow-[0_8px_28px_rgba(36,119,230,0.10)]",
    accent: "bg-blue-500",
    badge: "bg-blue-50 text-blue-700 ring-blue-200",
  },
  green: {
    shell: "border-emerald-300 bg-emerald-50/40 shadow-[0_8px_28px_rgba(16,130,84,0.11)]",
    accent: "bg-emerald-500",
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  amber: {
    shell: "border-amber-300 bg-amber-50/40 shadow-[0_8px_28px_rgba(211,143,22,0.11)]",
    accent: "bg-amber-500",
    badge: "bg-amber-50 text-amber-800 ring-amber-200",
  },
  red: {
    shell: "border-red-300 bg-red-50/50 shadow-[0_8px_28px_rgba(205,61,61,0.12)]",
    accent: "bg-red-500",
    badge: "bg-red-50 text-red-700 ring-red-200",
  },
  slate: {
    shell: "border-slate-300 bg-white shadow-[0_8px_28px_rgba(45,55,72,0.08)]",
    accent: "bg-slate-500",
    badge: "bg-slate-100 text-slate-700 ring-slate-200",
  },
};

const human = (value: unknown, fallback = "Not reported") => {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  const normalized = text.replace(/[._-]+/g, " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
};

function AgentCard({ data, selected }: NodeProps<AgentNode>) {
  const [hovered, setHovered] = useState(false);
  const colors = toneClasses[data.tone];
  return (
    <div
      className={`witdem-agent-card relative h-24 w-[184px] overflow-visible rounded-xl border ${colors.shell} ${selected ? "ring-2 ring-violet-400 ring-offset-2" : ""}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <Handle type="target" position={Position.Left} className="!size-2 !border-2 !border-white !bg-slate-400" />
      <div className={`absolute inset-y-3 left-0 w-1 rounded-r-full ${colors.accent}`} />
      <div className="flex h-full flex-col px-3 py-2.5 pl-4">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="truncate text-[9px] font-semibold tracking-[0.04em] text-slate-500">
            {data.eyebrow}
          </span>
          {data.badge ? (
            <span className={`max-w-[76px] truncate rounded-full px-1.5 py-0.5 text-[9px] font-semibold ring-1 ring-inset ${colors.badge}`}>
              {data.badge}
            </span>
          ) : null}
        </div>
        <div className="mt-1 min-h-0 flex-1">
          <div className="line-clamp-2 break-words text-[12px] font-semibold leading-4 text-slate-900">
            {data.title}
          </div>
          {data.subtitle ? (
            <div className="truncate text-[9px] leading-3 text-slate-500">{data.subtitle}</div>
          ) : null}
        </div>
        {data.metrics.length ? (
          <div className="mt-1 flex min-w-0 gap-1 border-t border-slate-200/70 pt-1.5">
            {data.metrics.slice(0, 2).map((metric) => (
              <span
                key={`${metric.label}-${metric.value}`}
                title={`${metric.label}: ${metric.value}`}
                className="min-w-0 truncate rounded-md bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold text-slate-600"
              >
                {metric.value}{metric.label === "Tokens" ? " tok" : ""}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} className="!size-2 !border-2 !border-white !bg-slate-400" />
      <NodeToolbar
        isVisible={hovered}
        position={data.lane === "runtime" ? Position.Bottom : Position.Top}
        offset={12}
        className="witdem-node-toolbar !z-[1001]"
      >
        <div className="w-72 rounded-xl border border-slate-200 bg-white p-3 text-left shadow-2xl">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">{data.eyebrow}</div>
          <div className="mt-1 text-sm font-semibold text-slate-900">{data.title}</div>
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
            {data.details.map((item) => (
              <div key={`${item.label}-${item.value}`} className="min-w-0">
                <div className="text-[10px] text-slate-400">{item.label}</div>
                <div className="break-words text-xs font-medium text-slate-700">{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      </NodeToolbar>
    </div>
  );
}

const nodeTypes = { agentNode: AgentCard };

const runtimeTone = (node: Record<string, unknown>, isRoot: boolean): Tone => {
  const status = String(node.status || "").toLowerCase();
  if (["error", "failed", "broke"].includes(status)) return "red";
  if (status === "recovered") return "amber";
  if (isRoot && ["completed", "success", "ok"].includes(status)) return "green";
  if (node.kind === "model") return "blue";
  if (node.kind === "tool") return "violet";
  return "slate";
};

const businessTone = (record: Record<string, unknown>): Tone => {
  const attributes = (record.attributes || {}) as Record<string, unknown>;
  const value = String(record.status ?? record.value ?? "").toLowerCase();
  if (record.name === "product_goal") return value === "achieved" ? "green" : "red";
  if (attributes.decision_correct === false || ["invalid", "failed", "error"].includes(value)) return "red";
  if (["escalated", "warning", "partial"].includes(value)) return "amber";
  if (["rejected", "false"].includes(value)) return "slate";
  if (["valid", "accepted", "achieved", "completed", "success", "true"].includes(value)) return "green";
  if (typeof record.score === "number" && typeof attributes.target === "number") {
    const direction = String(attributes.direction || "higher_is_better");
    const missed = direction === "lower_is_better"
      ? record.score > attributes.target
      : record.score < attributes.target;
    if (missed) return "amber";
  }
  return "amber";
};

const semanticRank = (record: Record<string, unknown>) => {
  if (record.kind === "decision") return 10;
  if (record.kind === "evaluation" && /valid/i.test(String(record.name || ""))) return 20;
  if (record.name === "application_outcome") return 30;
  if (record.name === "product_goal") return 40;
  if (record.kind === "evaluation") return 50;
  if (record.kind === "metric") return 60;
  return 70;
};

const semanticTitle = (record: Record<string, unknown>, definition: Record<string, unknown>) => {
  const attributes = (record.attributes || {}) as Record<string, unknown>;
  if (record.name === "application_outcome") return "Business result";
  if (record.name === "product_goal") {
    const goal = (definition.product_goal || {}) as Record<string, unknown>;
    return String(goal.name || attributes.product_goal_name || "Product goal");
  }
  return human(record.name);
};

const layoutImpactGraph = (
  runtime: { nodes: AgentNode[]; edges: Edge[] },
  business: { nodes: AgentNode[]; edges: Edge[] },
) => {
  const runtimeSpineCandidates = runtime.nodes.filter((node) => node.data.graphRole === "spine");
  const spineIds = new Set(runtimeSpineCandidates.map((node) => node.id));
  const spineNext = new Map<string, string>();
  const spineTargets = new Set<string>();
  for (const edge of runtime.edges) {
    if (edge.source !== edge.target && spineIds.has(edge.source) && spineIds.has(edge.target)) {
      spineNext.set(edge.source, edge.target);
      spineTargets.add(edge.target);
    }
  }
  const spineRoot = runtimeSpineCandidates.find((node) => !spineTargets.has(node.id)) || runtimeSpineCandidates[0];
  const runtimeSpine: AgentNode[] = [];
  const visited = new Set<string>();
  let current: AgentNode | undefined = spineRoot;
  while (current && !visited.has(current.id)) {
    runtimeSpine.push(current);
    visited.add(current.id);
    const nextId = spineNext.get(current.id);
    current = runtimeSpineCandidates.find((node) => node.id === nextId);
  }
  runtimeSpine.push(...runtimeSpineCandidates.filter((node) => !visited.has(node.id)));
  const branches = runtime.nodes.filter((node) => !spineIds.has(node.id));
  const spine = runtimeSpine;
  const incoming = new Map<string, string>();
  for (const edge of runtime.edges) {
    if (edge.source !== edge.target) incoming.set(edge.target, edge.source);
  }
  const branchesByParent = new Map<string, AgentNode[]>();
  for (const branch of branches) {
    const parent = incoming.get(branch.id) || runtimeSpine[0]?.id || "";
    branchesByParent.set(parent, [...(branchesByParent.get(parent) || []), branch]);
  }
  const deepestBranch = Math.max(0, ...[...branchesByParent.values()].map((items) => items.length));
  const spineY = 40 + deepestBranch * (NODE_HEIGHT + 26);
  const horizontalGap = 76;
  const positions = new Map<string, { x: number; y: number }>();
  spine.forEach((node, index) => {
    positions.set(node.id, { x: 24 + index * (NODE_WIDTH + horizontalGap), y: spineY });
  });
  const businessStartIndex = Math.max(2, runtimeSpine.length - 4);
  business.nodes.forEach((node, index) => {
    positions.set(node.id, {
      x: 24 + (businessStartIndex + index) * (NODE_WIDTH + horizontalGap),
      y: spineY + NODE_HEIGHT + 96,
    });
  });
  for (const [parent, nodes] of branchesByParent) {
    const parentPosition = positions.get(parent) || { x: 24, y: spineY };
    nodes.forEach((node, index) => {
      const parallel = node.data.graphRole === "parallel";
      positions.set(node.id, {
        x: parallel ? parentPosition.x + NODE_WIDTH + horizontalGap : parentPosition.x,
        y: spineY - (index + 1) * (NODE_HEIGHT + 26),
      });
    });
  }
  const bridge: Edge[] = runtimeSpine.length && business.nodes.length
    ? [{
        id: "sdk-business-bridge",
        source: runtimeSpine[runtimeSpine.length - 1].id,
        target: business.nodes[0].id,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#a78bfa", width: 16, height: 16 },
        style: { stroke: "#a78bfa", strokeWidth: 1.8, strokeDasharray: "6 4" },
      }]
    : [];
  return {
    nodes: [...runtime.nodes, ...business.nodes].map((node) => ({
      ...node,
      position: positions.get(node.id) || { x: 24, y: spineY },
    })),
    edges: [...runtime.edges, ...business.edges, ...bridge],
  };
};

const makeRuntimeGraph = (detail: RunDetail) => {
  const hiddenWrappers = new Set(
    detail.graph.nodes
      .filter(
        (node) =>
          node.parent_operation_id &&
          node.kind === "workflow" &&
          /^langgraph(?: graph| workflow)?$/i.test(String(node.display_name || node.name || "")),
      )
      .map((node) => node.id),
  );
  const parentOf = new Map(detail.graph.nodes.map((node) => [node.id, String(node.parent_operation_id || "")]));
  const visibleAncestor = (identifier: string) => {
    let current = identifier;
    while (hiddenWrappers.has(current)) current = parentOf.get(current) || "";
    return current;
  };
  const candidates = detail.graph.nodes
    .filter((node) => !hiddenWrappers.has(node.id))
    .sort((left, right) => {
      if (!left.parent_operation_id) return -1;
      if (!right.parent_operation_id) return 1;
      return String(left.start || "").localeCompare(String(right.start || ""));
    });
  const root = candidates.find((node) => !node.parent_operation_id) || candidates[0];
  const graphSteps = candidates.filter((node) => node.kind === "graph_node");
  const nestedStepIds = new Set(
    graphSteps
      .filter((node) => graphSteps.some((candidate) => candidate.id === node.parent_operation_id))
      .map((node) => node.id),
  );
  const visibleGraphSteps = graphSteps.filter((node) => !nestedStepIds.has(node.id));
  const modelCalls = candidates.filter((node) => node.kind === "model");
  const toolCalls = candidates.filter((node) => node.kind === "tool");
  const calls = [...modelCalls, ...toolCalls];
  const meaningful = visibleGraphSteps.length
    ? [root, ...visibleGraphSteps, ...calls].filter(Boolean) as Array<Record<string, unknown> & { id: string }>
    : candidates.filter(
        (node) =>
          node.id === root?.id ||
          ["agent_step", "workflow_stage", "model", "tool", "component"].includes(String(node.kind)),
      );
  const genericStages = meaningful
    .filter((node) => node.id !== root?.id && !["model", "tool"].includes(String(node.kind)))
    .sort((left, right) => String(left.start || "").localeCompare(String(right.start || "")));
  const genericLayers: Array<Array<Record<string, unknown> & { id: string }>> = [];
  for (const stage of genericStages) {
    const start = Date.parse(String(stage.start || ""));
    const activeLayer = genericLayers[genericLayers.length - 1];
    const activeEnd = activeLayer
      ? Math.max(...activeLayer.map((node) => Date.parse(String(node.end || node.start || ""))))
      : Number.NEGATIVE_INFINITY;
    if (activeLayer && Number.isFinite(start) && start < activeEnd) activeLayer.push(stage);
    else genericLayers.push([stage]);
  }
  const parallelStageIds = new Set(genericLayers.flatMap((layer) => layer.slice(1).map((node) => node.id)));
  const runtimeNodes: AgentNode[] = meaningful.map((node) => {
    const isRoot = node.id === root?.id;
    const displayNode = node;
    const rawTitle = isRoot && detail.summary.display_name
      ? String(detail.summary.display_name)
      : String(node.display_name || node.name || human(node.kind, "Workflow step"));
    const duration = Number(node.duration_seconds || 0);
    const skipped = !isRoot && duration < 0.01 && /targeted research/i.test(rawTitle);
    const cost = typeof displayNode.known_cost === "number" ? displayNode.known_cost : null;
    const tokens = typeof displayNode.total_tokens === "number" ? displayNode.total_tokens : null;
    const provider = String(displayNode.provider || "");
    const model = String(displayNode.model || "");
    const kind = String(node.kind || "operation");
    const title = skipped
      ? "Targeted research skipped"
      : kind === "model" && model
        ? human(model)
        : rawTitle;
    const rawStatus = isRoot ? detail.summary.runtime_outcome || detail.summary.status : node.status;
    const normalizedStatus = String(rawStatus || "").toLowerCase();
    const badge = skipped
      ? "Skipped"
      : isRoot && normalizedStatus && normalizedStatus !== "unset"
        ? human(rawStatus)
        : ["error", "failed", "broke", "recovered"].includes(normalizedStatus)
          ? human(rawStatus)
          : undefined;
    const eyebrow = isRoot
      ? "Execution"
      : kind === "model"
        ? provider ? `${human(provider)} model` : "Model call"
        : kind === "tool"
          ? "Tool call"
          : ["graph_node", "workflow_stage", "agent_step"].includes(kind)
            ? "Workflow stage"
            : human(kind);
    return {
      id: `runtime-${node.id}`,
      type: "agentNode",
      position: { x: 0, y: 0 },
      style: { width: NODE_WIDTH, height: NODE_HEIGHT },
      data: {
        lane: "runtime",
        graphRole: ["model", "tool"].includes(kind)
          ? "branch"
          : parallelStageIds.has(node.id)
            ? "parallel"
            : "spine",
        eyebrow,
        title,
        subtitle: undefined,
        badge,
        tone: skipped ? "slate" : runtimeTone({ ...node, status: rawStatus }, isRoot),
        metrics: [
          duration >= 0.01 ? { label: "Time", value: seconds(duration) } : null,
          cost != null ? { label: "Cost", value: money(cost) } : null,
          tokens != null ? { label: "Tokens", value: formatNumber(tokens) } : null,
        ].filter(Boolean) as Metric[],
        details: [
          badge ? { label: "Runtime health", value: badge } : null,
          { label: "Type", value: human(kind) },
          provider ? { label: "Provider", value: human(provider) } : null,
          model ? { label: "Model", value: model } : null,
          duration >= 0.01 ? { label: "Observed time", value: seconds(duration) } : { label: "Observed time", value: "Not reliably measured" },
          cost != null ? { label: "Measured cost", value: money(cost) } : null,
          tokens != null ? { label: "Tokens", value: formatNumber(tokens) } : null,
          { label: "Operation ID", value: String(node.id) },
        ].filter(Boolean) as Metric[],
      },
    };
  });
  const nodeIds = new Set(meaningful.map((node) => node.id));
  const nestedLoops = graphSteps.filter((node) => nestedStepIds.has(node.id));
  const normalizedEdges = detail.graph.edges
    .map((edge) => ({
      source: visibleAncestor(edge.source),
      target: visibleAncestor(edge.target),
      relation: edge.relation,
    }))
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target) && edge.source !== edge.target);
  const productFactoryStageRank = (node: Record<string, unknown>) => {
    const name = String(node.display_name || node.name || "").toLowerCase();
    if (/^research$|product factory research$/.test(name)) return 10;
    if (/evidence critique after/.test(name)) return 40;
    if (/evidence critique/.test(name)) return 20;
    if (/targeted research/.test(name)) return 30;
    if (/profile extraction/.test(name)) return 50;
    if (/profile validation/.test(name)) return 60;
    if (/qualification analysis/.test(name)) return 70;
    if (/deterministic decision/.test(name)) return 80;
    if (/goal assessment/.test(name)) return 90;
    return 1_000;
  };
  const orderedSteps = [...visibleGraphSteps].sort((left, right) => {
    const rankDelta = productFactoryStageRank(left) - productFactoryStageRank(right);
    if (rankDelta) return rankDelta;
    return String(left.start || "").localeCompare(String(right.start || ""));
  });
  const stepForCall = (call: Record<string, unknown>) => {
    const callName = String(call.display_name || call.name || "").toLowerCase();
    const roleMatch = orderedSteps.find((step) => {
      const stepName = String(step.display_name || step.name || "").toLowerCase();
      return (
        (/research model/.test(callName) && /(^| )research$/.test(stepName)) ||
        (/evidence critic/.test(callName) && /evidence critique/.test(stepName) && !/after/.test(stepName)) ||
        (/profile extractor/.test(callName) && /profile extraction/.test(stepName)) ||
        (/qualification analyst/.test(callName) && /qualification analysis/.test(stepName))
      );
    });
    if (roleMatch) return roleMatch;
    const callStart = Date.parse(String(call.start || ""));
    return [...orderedSteps]
      .filter((step) => Date.parse(String(step.start || "")) <= callStart)
      .sort((left, right) => Date.parse(String(right.start || "")) - Date.parse(String(left.start || "")))[0];
  };
  const genericCallIds = new Set(calls.map((call) => call.id));
  const genericSequence = [
    ...(genericLayers[0] || []).map((node) => ({ source: root.id, target: node.id, relation: "starts" })),
    ...genericLayers.slice(1).flatMap((layer, index) =>
      genericLayers[index].flatMap((source) =>
        layer.map((target) => ({
          source: source.id,
          target: target.id,
          relation: "next observed",
        })),
      ),
    ),
  ];
  const genericBranches = normalizedEdges.filter((edge) => genericCallIds.has(edge.target));
  const genericLoops = normalizedEdges.filter((edge) =>
    ["repeat", "retry", "handoff"].includes(String(edge.relation).toLowerCase()),
  );
  const structuralEdges = visibleGraphSteps.length
    ? [
        ...(orderedSteps.length ? [{ source: root.id, target: orderedSteps[0].id, relation: "starts" }] : []),
        ...orderedSteps.slice(1).map((node, index) => ({
          source: orderedSteps[index].id,
          target: node.id,
          relation: "next observed",
        })),
        ...calls.map((call) => ({
          source: (stepForCall(call) || root).id,
          target: call.id,
          relation: call.kind === "tool" ? "uses tool" : "calls model",
        })),
        ...nestedLoops.map((node) => ({
          source: String(node.parent_operation_id),
          target: String(node.parent_operation_id),
          relation: "loop observed",
        })),
      ]
    : [...genericSequence, ...genericBranches, ...genericLoops];
  const runtimeEdges: Edge[] = structuralEdges.map((edge, index) => ({
    id: `runtime-edge-${index}-${edge.source}-${edge.target}`,
    source: `runtime-${edge.source}`,
    target: `runtime-${edge.target}`,
    type: "smoothstep",
    label: ["repeat", "retry", "handoff", "loop observed"].includes(String(edge.relation).toLowerCase())
      ? human(edge.relation)
      : undefined,
    animated: ["repeat", "retry", "handoff"].includes(String(edge.relation).toLowerCase()),
    markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8", width: 16, height: 16 },
    style: { stroke: "#94a3b8", strokeWidth: 1.5 },
    labelStyle: { fill: "#64748b", fontSize: 10, fontWeight: 600 },
  }));
  return { nodes: runtimeNodes, edges: runtimeEdges };
};

const makeBusinessGraph = (detail: RunDetail) => {
  const definitionRecord = detail.semantic_records.find((record) => record.name === "contract.definition");
  const definition = (definitionRecord?.attributes || {}) as Record<string, unknown>;
  const records = detail.semantic_records
    .filter(
      (record) =>
        ["decision", "evaluation", "metric", "outcome"].includes(String(record.kind)) &&
        record.name !== "execution.completed",
    )
    .sort((left, right) => semanticRank(left) - semanticRank(right));
  const nodes: AgentNode[] = records.map((record, index) => {
    const attributes = (record.attributes || {}) as Record<string, unknown>;
    const rawValue = record.status ?? record.value ?? record.label ?? record.score;
    const value = typeof rawValue === "number" ? formatNumber(rawValue) : human(rawValue);
    const target = typeof attributes.target === "number" ? formatNumber(attributes.target) : null;
    return {
      id: `business-${String(record.record_id || index)}`,
      type: "agentNode",
      position: { x: 0, y: 0 },
      style: { width: NODE_WIDTH, height: NODE_HEIGHT },
      data: {
        lane: "business",
        graphRole: "spine",
        eyebrow:
          record.name === "product_goal"
            ? "Product goal"
            : record.name === "application_outcome"
              ? "Application outcome"
              : human(record.kind),
        title: semanticTitle(record, definition),
        subtitle: String(
          record.name === "product_goal"
            ? attributes.product_goal_description || ""
            : record.name === "application_outcome"
              ? attributes.outcome_description || ""
              : record.kind === "decision"
                ? attributes.decision_description || ""
                : record.kind === "evaluation"
                  ? attributes.evaluation_description || ""
                  : attributes.metric_description || "",
        ) || undefined,
        badge: value,
        tone: businessTone(record),
        metrics: [
          typeof record.score === "number" ? { label: "Score", value: formatNumber(record.score) } : null,
          target ? { label: "Target", value: target } : null,
          typeof attributes.threshold_margin === "number" ? { label: "Margin", value: formatNumber(attributes.threshold_margin) } : null,
        ].filter(Boolean) as Metric[],
        details: [
          { label: "Reported value", value },
          typeof record.score === "number" ? { label: "Score", value: formatNumber(record.score) } : null,
          target ? { label: "Target", value: target } : null,
          attributes.expected_status != null ? { label: "Expected", value: human(attributes.expected_status) } : null,
          attributes.observed_status != null ? { label: "Observed", value: human(attributes.observed_status) } : null,
          attributes.decision_correct != null ? { label: "Decision correct", value: human(attributes.decision_correct) } : null,
          attributes.closest_blocker ? { label: "Closest blocker", value: String(attributes.closest_blocker) } : null,
          { label: "SDK record", value: String(record.record_id || "Not available") },
        ].filter(Boolean) as Metric[],
      },
    };
  });
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({
    id: `business-edge-${index}`,
    source: nodes[index].id,
    target: node.id,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#a78bfa", width: 16, height: 16 },
    style: { stroke: "#a78bfa", strokeWidth: 1.5, strokeDasharray: "6 4" },
  }));
  return { nodes, edges };
};

export function AdvancedWorkflowGraph({ detail }: { detail: RunDetail }) {
  const [expanded, setExpanded] = useState(false);
  const [unifiedGraph, setUnifiedGraph] = useState<{ nodes: AgentNode[]; edges: Edge[] }>({ nodes: [], edges: [] });
  const input = useMemo(() => ({ runtime: makeRuntimeGraph(detail), business: makeBusinessGraph(detail) }), [detail]);

  useEffect(() => {
    setUnifiedGraph(layoutImpactGraph(input.runtime, input.business));
  }, [input]);

  useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [expanded]);

  return (
    <>
      <div className="relative">
        <div className="mb-2 flex items-center justify-between gap-3 px-2">
          <GraphLegend />
          <button
            type="button"
            aria-label="Open workflow full screen"
            onClick={() => setExpanded(true)}
            className="shrink-0 rounded-lg border bg-white px-3 py-1.5 text-xs font-medium shadow-sm"
          >
            <span aria-hidden="true" className="mr-1.5">↗</span>
            Full screen
          </button>
        </div>
        <div className="h-[600px] overflow-hidden rounded-xl border border-slate-200 bg-[#fbfbf8]">
          <GraphCanvas graph={unifiedGraph} backgroundId="execution-grid" />
        </div>
      </div>
      {expanded
        ? createPortal(
            <FullscreenGraph
              title={String(detail.summary.display_name || "Execution workflow")}
              graph={unifiedGraph}
              onClose={() => setExpanded(false)}
            />,
            document.body,
          )
        : null}
    </>
  );
}

function GraphLegend() {
  return (
    <div className="flex flex-wrap gap-2 text-[11px] font-semibold">
      <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-slate-600">Solid edges · observed agent structure</span>
      <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-violet-700">Dashed edges · SDK business meaning</span>
      <span className="rounded-full border border-red-200 bg-red-50 px-2.5 py-1 text-red-700">Red · needs attention</span>
    </div>
  );
}

function FullscreenGraph({
  title,
  graph,
  onClose,
}: {
  title: string;
  graph: { nodes: AgentNode[]; edges: Edge[] };
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[9999] bg-slate-950/35 p-3 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={`${title} workflow`}>
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <header className="relative z-20 flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-5 py-3">
          <div className="min-w-0">
            <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-violet-600">Execution map</div>
            <h2 className="truncate text-lg font-semibold text-slate-900">{title}</h2>
          </div>
          <div className="flex items-center gap-3">
            <GraphLegend />
            <button
              type="button"
              aria-label="Close workflow full screen"
              onClick={onClose}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
            >
              Close <span aria-hidden="true">×</span>
            </button>
          </div>
        </header>
        <div className="relative z-0 min-h-0 flex-1 overflow-hidden bg-[#fbfbf8]">
          {graph.nodes.length ? (
            <ReactFlow
              nodes={graph.nodes}
              edges={graph.edges}
              nodeTypes={nodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              fitView
              fitViewOptions={{ padding: 0.12, minZoom: 0.2, maxZoom: 1.05 }}
              minZoom={0.18}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background id="fullscreen-grid" variant={BackgroundVariant.Dots} gap={22} size={1} color="#d9dce2" />
              <Controls showInteractive={false} position="bottom-right" />
            </ReactFlow>
          ) : (
            <div className="grid h-full place-items-center text-sm text-slate-500">Laying out the complete execution map…</div>
          )}
        </div>
      </div>
    </div>
  );
}

function GraphCanvas({
  graph,
  backgroundId,
}: {
  graph: { nodes: AgentNode[]; edges: Edge[] };
  backgroundId: string;
}) {
  return (
    <section className="h-full min-h-0 overflow-hidden bg-[#fbfbf8]">
      {graph.nodes.length ? (
        <div className="h-full min-h-0">
          <ReactFlow
            nodes={graph.nodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            fitView
            fitViewOptions={{ padding: 0.16, minZoom: 0.2, maxZoom: 1.1 }}
            minZoom={0.18}
            maxZoom={1.8}
            proOptions={{ hideAttribution: true }}
          >
            <Background id={backgroundId} variant={BackgroundVariant.Dots} gap={20} size={1} color="#d9dce2" />
            <Controls showInteractive={false} position="bottom-right" />
          </ReactFlow>
        </div>
      ) : (
        <div className="grid h-full place-items-center text-sm text-slate-500">Laying out this lane…</div>
      )}
    </section>
  );
}
