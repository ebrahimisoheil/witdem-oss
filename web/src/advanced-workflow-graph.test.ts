import { describe, expect, it } from "vitest";

import {
  businessTone,
  compactRuntimeGraph,
  layoutImpactGraph,
  makeRuntimeGraph,
  selectBusinessGraphRecords,
  sequentialSiblingCallGroups,
} from "./advanced-workflow-graph";
import type { RunDetail } from "./api";

describe("business replay records", () => {
  it("keeps core outcomes and groups evaluations and measurements into compact summaries", () => {
    const records = [
      { kind: "decision", name: "Agent completion reason" },
      { kind: "outcome", name: "application_outcome" },
      { kind: "outcome", name: "product_goal" },
      { kind: "metric", name: "Agent steps", value: 3 },
      { kind: "evaluation", name: "Answer validity", value: 1, attributes: { target: true } },
      { kind: "evaluation", name: "Retrieval evidence", value: 0, attributes: { target: true } },
    ];

    const selected = selectBusinessGraphRecords(records);
    expect(selected.map((record) => record.name)).toEqual([
      "Agent completion reason",
      "application_outcome",
      "product_goal",
      "Contract checks",
      "Run measurements",
    ]);
    expect(selected[3].value).toBe("1/2");
    expect(selected[3].attributes).toMatchObject({ failed_count: 1 });
    expect(selected[4].value).toBe(1);
  });

  it("colors boolean evaluation misses red and passes green", () => {
    expect(businessTone({ kind: "evaluation", value: 0, attributes: { target: true } })).toBe("red");
    expect(businessTone({ kind: "evaluation", value: 1, attributes: { target: true } })).toBe("green");
    expect(businessTone({ kind: "evaluation_summary", attributes: { failed_count: 1 } })).toBe("red");
  });
});

describe("runtime sibling layout", () => {
  it("promotes non-overlapping sibling calls into an observed sequence", () => {
    const groups = sequentialSiblingCallGroups([
      {
        id: "weather",
        parent_operation_id: "step",
        start: "2026-08-26T10:44:12.566006Z",
        end: "2026-08-26T10:44:12.566107Z",
      },
      {
        id: "model",
        parent_operation_id: "step",
        start: "2026-08-26T10:44:10.637862Z",
        end: "2026-08-26T10:44:12.542659Z",
      },
      {
        id: "final",
        parent_operation_id: "step",
        start: "2026-08-26T10:44:12.566267Z",
        end: "2026-08-26T10:44:12.566302Z",
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].calls.map((call) => call.id)).toEqual(["model", "weather", "final"]);
  });

  it("keeps overlapping sibling calls as parallel branches", () => {
    const groups = sequentialSiblingCallGroups([
      {
        id: "retriever-a",
        parent_operation_id: "step",
        start: "2026-08-26T10:44:10.000Z",
        end: "2026-08-26T10:44:11.000Z",
      },
      {
        id: "retriever-b",
        parent_operation_id: "step",
        start: "2026-08-26T10:44:10.200Z",
        end: "2026-08-26T10:44:10.900Z",
      },
    ]);

    expect(groups).toEqual([]);
  });

  it("renders a sequential smolagents step as one left-to-right runtime rail", () => {
    const detail = {
      summary: {
        display_name: "LiteLLM weather",
        runtime_outcome: "completed",
        status: "completed",
      },
      semantic_records: [],
      graph: {
        nodes: [
          { id: "execution", kind: "workflow", start: "2026-08-26T10:44:10.000Z", end: "2026-08-26T10:44:13.000Z" },
          { id: "step", kind: "component", display_name: "Component", parent_operation_id: "execution", start: "2026-08-26T10:44:10.100Z", end: "2026-08-26T10:44:12.900Z" },
          { id: "model", kind: "model", model: "gpt-5.4-mini", parent_operation_id: "step", start: "2026-08-26T10:44:10.200Z", end: "2026-08-26T10:44:12.500Z" },
          { id: "weather", kind: "tool", display_name: "Get weather", parent_operation_id: "step", start: "2026-08-26T10:44:12.600Z", end: "2026-08-26T10:44:12.700Z" },
          { id: "final", kind: "tool", display_name: "Final answer", parent_operation_id: "step", start: "2026-08-26T10:44:12.800Z", end: "2026-08-26T10:44:12.900Z" },
        ],
        edges: [
          { source: "execution", target: "step", relation: "contains" },
          { source: "step", target: "model", relation: "contains" },
          { source: "step", target: "weather", relation: "contains" },
          { source: "step", target: "final", relation: "contains" },
        ],
      },
    } as unknown as RunDetail;

    const runtime = makeRuntimeGraph(detail);
    const laidOut = layoutImpactGraph(runtime, { nodes: [], edges: [] });
    const rail = ["runtime-execution", "runtime-step", "runtime-model", "runtime-weather", "runtime-final"]
      .map((id) => laidOut.nodes.find((node) => node.id === id));

    expect(rail.every((node) => node?.data.graphRole === "spine")).toBe(true);
    expect(rail.map((node) => node?.position.y)).toEqual([40, 40, 40, 40, 40]);
    expect(rail.map((node) => node?.position.x)).toEqual([24, 284, 544, 804, 1064]);
    expect(runtime.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["runtime-execution", "runtime-step"],
      ["runtime-step", "runtime-model"],
      ["runtime-model", "runtime-weather"],
      ["runtime-weather", "runtime-final"],
    ]);
  });

  it("keeps calls attached when the trace exposes a stage topology", () => {
    const detail = {
      summary: {
        display_name: "Advanced RAG",
        runtime_outcome: "completed",
        status: "completed",
      },
      semantic_records: [],
      graph: {
        nodes: [
          { id: "execution", kind: "workflow", start: "2026-08-26T08:15:59.600Z", end: "2026-08-26T08:16:04.100Z" },
          { id: "step-1", kind: "workflow_stage", display_name: "Step 1", parent_operation_id: "execution", start: "2026-08-26T08:15:59.640Z", end: "2026-08-26T08:16:00.760Z" },
          { id: "model-1", kind: "model", model: "gpt-5.4", parent_operation_id: "step-1", start: "2026-08-26T08:15:59.644Z", end: "2026-08-26T08:16:00.759Z" },
          { id: "tool-1", kind: "tool", display_name: "List metadata fields", parent_operation_id: "step-1", start: "2026-08-26T08:16:00.7594Z", end: "2026-08-26T08:16:00.7596Z" },
          { id: "step-2", kind: "workflow_stage", display_name: "Step 2", parent_operation_id: "execution", start: "2026-08-26T08:16:00.760Z", end: "2026-08-26T08:16:02.686Z" },
          { id: "model-2", kind: "model", model: "gpt-5.4", parent_operation_id: "step-2", start: "2026-08-26T08:16:00.761Z", end: "2026-08-26T08:16:02.684Z" },
        ],
        edges: [
          { source: "execution", target: "step-1", relation: "parent" },
          { source: "step-1", target: "model-1", relation: "parent" },
          { source: "step-1", target: "tool-1", relation: "parent" },
          { source: "execution", target: "step-2", relation: "parent" },
          { source: "step-2", target: "model-2", relation: "parent" },
        ],
      },
    } as unknown as RunDetail;

    const runtime = makeRuntimeGraph(detail);
    const byId = new Map(runtime.nodes.map((node) => [node.id, node]));

    expect(byId.get("runtime-step-1")?.data.graphRole).toBe("spine");
    expect(byId.get("runtime-step-2")?.data.graphRole).toBe("spine");
    expect(byId.get("runtime-model-1")?.data.graphRole).toBe("branch");
    expect(byId.get("runtime-tool-1")?.data.graphRole).toBe("branch");
    expect(byId.get("runtime-model-2")?.data.graphRole).toBe("branch");
    expect(runtime.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["runtime-execution", "runtime-step-1"],
      ["runtime-step-1", "runtime-step-2"],
      ["runtime-step-1", "runtime-model-1"],
      ["runtime-step-1", "runtime-tool-1"],
      ["runtime-step-2", "runtime-model-2"],
    ]);
  });

  it("compresses a long execution spine into expandable neutral phases", () => {
    const nodes = Array.from({ length: 10 }, (_, index) => ({
      id: `runtime-step-${index}`,
      type: "agentNode",
      position: { x: 0, y: 0 },
      data: {
        lane: "runtime",
        graphRole: "spine",
        eyebrow: index === 0 ? "Execution" : "Component",
        title: index === 0 ? "Contract review" : `Observed step ${index}`,
        tone: "slate",
        metrics: [],
        details: [],
      },
    }));
    const edges = nodes.slice(1).map((node, index) => ({
      id: `edge-${index}`,
      source: nodes[index].id,
      target: node.id,
    }));

    const compact = compactRuntimeGraph({ nodes, edges } as never);
    const groups = compact.nodes.filter((node) => node.data.graphRole === "group");

    expect(compact.nodes.map((node) => node.data.title)).toEqual([
      "Contract review",
      "Steps 1–3",
      "Steps 4–6",
      "Steps 7–9",
    ]);
    expect(groups.every((node) => node.data.expandGroupId)).toBe(true);

    const firstGroupId = String(groups[0].data.expandGroupId);
    const expanded = compactRuntimeGraph({ nodes, edges } as never, new Set([firstGroupId]));
    expect(expanded.nodes.map((node) => node.data.title)).toEqual([
      "Contract review",
      "Observed step 1",
      "Observed step 2",
      "Observed step 3",
      "Steps 4–6",
      "Steps 7–9",
    ]);
  });

  it("keeps recorded nested iterations as clickable retry nodes below their owner", () => {
    const detail = {
      summary: {
        display_name: "Research workflow",
        runtime_outcome: "completed",
        status: "completed",
      },
      semantic_records: [],
      graph: {
        nodes: [
          { id: "execution", kind: "workflow", start: "2026-08-26T08:00:00Z", end: "2026-08-26T08:00:05Z" },
          { id: "research", kind: "graph_node", display_name: "Research", parent_operation_id: "execution", start: "2026-08-26T08:00:00.1Z", end: "2026-08-26T08:00:05Z" },
          { id: "research-attempt-2", kind: "graph_node", display_name: "Research attempt 2", status: "completed", parent_operation_id: "research", start: "2026-08-26T08:00:02Z", end: "2026-08-26T08:00:04Z" },
        ],
        edges: [
          { source: "execution", target: "research", relation: "starts" },
          { source: "research", target: "research-attempt-2", relation: "repeat" },
        ],
      },
    } as unknown as RunDetail;

    const runtime = makeRuntimeGraph(detail);
    const retry = runtime.nodes.find((node) => node.id === "runtime-research-attempt-2");
    const laidOut = layoutImpactGraph(runtime, { nodes: [], edges: [] });
    const owner = laidOut.nodes.find((node) => node.id === "runtime-research");
    const attempt = laidOut.nodes.find((node) => node.id === "runtime-research-attempt-2");

    expect(retry?.data.graphRole).toBe("retry");
    expect(runtime.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "runtime-research", target: "runtime-research-attempt-2", label: "Retry attempt" }),
    ]));
    expect(attempt?.position.x).toBe(owner?.position.x);
    expect(Number(attempt?.position.y)).toBeGreaterThan(Number(owner?.position.y));
  });

  it("recognizes repeated component instances from a generic pipeline as vertical attempts", () => {
    const detail = {
      summary: { display_name: "Contract review", runtime_outcome: "completed", status: "completed" },
      semantic_records: [],
      graph: {
        nodes: [
          { id: "execution", kind: "operation", display_name: "Contract review", start: "2026-08-27T15:00:00Z", end: "2026-08-27T15:00:10Z" },
          { id: "extract-1", kind: "component", display_name: "Clause extractor", parent_operation_id: "pipeline", start: "2026-08-27T15:00:01Z", end: "2026-08-27T15:00:03Z" },
          { id: "model-1", kind: "model", model: "deepseek-v4-flash", parent_operation_id: "extract-1", start: "2026-08-27T15:00:01.1Z", end: "2026-08-27T15:00:02.9Z" },
          { id: "extract-2", kind: "component", display_name: "Clause extractor", parent_operation_id: "pipeline", start: "2026-08-27T15:00:04Z", end: "2026-08-27T15:00:06Z" },
          { id: "model-2", kind: "model", model: "deepseek-v4-flash", parent_operation_id: "extract-2", start: "2026-08-27T15:00:04.1Z", end: "2026-08-27T15:00:05.9Z" },
        ],
        edges: [
          { source: "execution", target: "extract-1", relation: "next" },
          { source: "extract-1", target: "model-1", relation: "calls model" },
          { source: "extract-1", target: "extract-2", relation: "retry" },
          { source: "extract-2", target: "model-2", relation: "calls model" },
        ],
      },
    } as unknown as RunDetail;

    const runtime = makeRuntimeGraph(detail);
    const laidOut = layoutImpactGraph(runtime, { nodes: [], edges: [] });
    const owner = laidOut.nodes.find((node) => node.id === "runtime-extract-1");
    const attempt = laidOut.nodes.find((node) => node.id === "runtime-extract-2");
    const attemptModel = laidOut.nodes.find((node) => node.id === "runtime-model-2");

    expect(attempt?.data.graphRole).toBe("retry");
    expect(attempt?.data.badge).toBe("Attempt 2");
    expect(attempt?.position.x).toBe(owner?.position.x);
    expect(Number(attempt?.position.y)).toBeGreaterThan(Number(owner?.position.y));
    expect(Number(attemptModel?.position.x)).toBeGreaterThan(Number(attempt?.position.x));
    expect(attemptModel?.position.y).toBe(attempt?.position.y);
  });

  it("uses explicit Haystack fan-out and convergence in expanded and compact views", () => {
    const workflow = (source: string, target: string, relation = "workflow") => ({
      source,
      target,
      relation,
      attributes: { framework: "haystack", explicit: true },
    });
    const detail = {
      summary: { display_name: "Contract review", runtime_outcome: "completed", status: "completed" },
      semantic_records: [],
      graph: {
        nodes: [
          { id: "execution", kind: "workflow", start: "2026-08-29T08:10:40Z", end: "2026-08-29T08:11:10Z" },
          ...["load", "classify", "extract", "normalize", "route"].map((id, index) => ({
            id,
            kind: "component",
            display_name: id,
            parent_operation_id: "execution",
            start: `2026-08-29T08:10:4${index}Z`,
            end: `2026-08-29T08:10:4${index}.5Z`,
          })),
          { id: "dispatcher", kind: "component", display_name: "Domain review dispatcher", parent_operation_id: "execution", start: "2026-08-29T08:10:42Z", end: "2026-08-29T08:10:42.5Z" },
          { id: "legal", kind: "component", display_name: "Legal domain review", parent_operation_id: "execution", start: "2026-08-29T08:10:42.555Z", end: "2026-08-29T08:10:46.740Z" },
          { id: "finance", kind: "component", display_name: "Finance domain review", parent_operation_id: "execution", start: "2026-08-29T08:10:42.554Z", end: "2026-08-29T08:10:44.801Z" },
          { id: "legal-model", kind: "model", model: "gpt-5.4", parent_operation_id: "legal", start: "2026-08-29T08:10:42.6Z", end: "2026-08-29T08:10:46.7Z" },
          { id: "finance-model", kind: "model", model: "gpt-5.4", parent_operation_id: "finance", start: "2026-08-29T08:10:42.6Z", end: "2026-08-29T08:10:44.7Z" },
          { id: "joiner", kind: "component", display_name: "Domain review joiner", parent_operation_id: "execution", start: "2026-08-29T08:10:46.741Z", end: "2026-08-29T08:10:46.747Z" },
          { id: "aggregator", kind: "component", display_name: "Domain review aggregator", parent_operation_id: "execution", start: "2026-08-29T08:10:46.8Z", end: "2026-08-29T08:10:47Z" },
          { id: "fallback-1", kind: "component", display_name: "Fallback generator", parent_operation_id: "execution", start: "2026-08-29T08:10:47Z", end: "2026-08-29T08:10:48Z" },
          { id: "fallback-1-model", kind: "model", model: "gpt-5.4", parent_operation_id: "fallback-1", start: "2026-08-29T08:10:47.1Z", end: "2026-08-29T08:10:47.9Z" },
          { id: "fallback-2", kind: "component", display_name: "Fallback generator", parent_operation_id: "execution", start: "2026-08-29T08:10:48Z", end: "2026-08-29T08:10:49Z" },
          { id: "fallback-2-model", kind: "model", model: "gpt-5.4", parent_operation_id: "fallback-2", start: "2026-08-29T08:10:48.1Z", end: "2026-08-29T08:10:48.9Z" },
        ],
        edges: [
          workflow("load", "classify"),
          workflow("classify", "extract"),
          workflow("extract", "normalize"),
          workflow("normalize", "route"),
          workflow("route", "dispatcher"),
          workflow("dispatcher", "legal"),
          workflow("dispatcher", "finance"),
          workflow("legal", "joiner"),
          workflow("finance", "joiner"),
          workflow("joiner", "aggregator"),
          workflow("aggregator", "fallback-1"),
          workflow("fallback-1", "fallback-2", "workflow_retry"),
          { source: "legal", target: "legal-model", relation: "parent" },
          { source: "finance", target: "finance-model", relation: "parent" },
          { source: "fallback-1", target: "fallback-1-model", relation: "parent" },
          { source: "fallback-2", target: "fallback-2-model", relation: "parent" },
        ],
      },
    } as unknown as RunDetail;

    const runtime = makeRuntimeGraph(detail);
    const edgePairs = runtime.edges.map((edge) => [edge.source, edge.target]);
    expect(edgePairs).toEqual(expect.arrayContaining([
      ["runtime-dispatcher", "runtime-legal"],
      ["runtime-dispatcher", "runtime-finance"],
      ["runtime-legal", "runtime-joiner"],
      ["runtime-finance", "runtime-joiner"],
      ["runtime-joiner", "runtime-aggregator"],
    ]));
    expect(edgePairs).not.toContainEqual(["runtime-legal", "runtime-finance"]);

    const expandedLayout = layoutImpactGraph(runtime, { nodes: [], edges: [] });
    const position = (id: string) => expandedLayout.nodes.find((node) => node.id === id)?.position;
    expect(position("runtime-legal")?.x).toBe(position("runtime-finance")?.x);
    expect(position("runtime-legal")?.y).not.toBe(position("runtime-finance")?.y);
    expect(Number(position("runtime-joiner")?.x)).toBeGreaterThan(Number(position("runtime-legal")?.x));
    expect(position("runtime-legal-model")?.x).toBe(position("runtime-legal")?.x);
    expect(position("runtime-finance-model")?.x).toBe(position("runtime-finance")?.x);
    expect(position("runtime-fallback-2")?.x).toBe(position("runtime-fallback-1")?.x);
    expect(Number(position("runtime-fallback-2")?.y)).toBeGreaterThan(Number(position("runtime-fallback-1")?.y));
    const expectNoOverlaps = (nodes: typeof expandedLayout.nodes) => {
      for (const [index, node] of nodes.entries()) {
        for (const other of nodes.slice(index + 1)) {
        const separated =
          node.position.x + 184 + 16 <= other.position.x ||
          other.position.x + 184 + 16 <= node.position.x ||
          node.position.y + 96 + 16 <= other.position.y ||
          other.position.y + 96 + 16 <= node.position.y;
        expect(separated, `${node.id} overlaps ${other.id}`).toBe(true);
        }
      }
    };
    expectNoOverlaps(expandedLayout.nodes);

    const compact = compactRuntimeGraph(runtime);
    const group = compact.nodes.find((node) => node.data.expandGroupId);
    expect(group).toBeDefined();
    expect(compact.edges.map((edge) => [edge.source, edge.target])).toEqual(expect.arrayContaining([
      ["runtime-dispatcher", "runtime-legal"],
      ["runtime-dispatcher", "runtime-finance"],
      ["runtime-legal", "runtime-joiner"],
      ["runtime-finance", "runtime-joiner"],
    ]));
    expectNoOverlaps(layoutImpactGraph(compact, { nodes: [], edges: [] }).nodes);
    const revealed = compactRuntimeGraph(runtime, new Set([String(group?.data.expandGroupId)]));
    expect(revealed.nodes.find((node) => node.id === "runtime-classify")).toBeDefined();
    expect(revealed.edges.map((edge) => [edge.source, edge.target])).toContainEqual([
      "runtime-classify",
      "runtime-extract",
    ]);
    expectNoOverlaps(layoutImpactGraph(revealed, { nodes: [], edges: [] }).nodes);
  });
});
