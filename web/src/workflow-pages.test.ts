import { describe, expect, it } from "vitest";
import { buildStepGraph, resolveGoalOutcome, statePresentation, summarizeWorkflowRuns, trackpadZoomTarget, validateWorkflowGeometry, workflowFitZoom, workflowLayout } from "./workflow-pages";

describe("workflow presentation", () => {
  it("fits the complete workflow inside both viewport axes", () => {
    const zoom = workflowFitZoom(10_000, 2_200, 1_000, 700);

    expect(10_000 * zoom).toBeLessThanOrEqual(1_000 - 32);
    expect(2_200 * zoom).toBeLessThanOrEqual(700 - 32);
  });

  it("makes trackpad zoom gradual and caps unusually large wheel events", () => {
    expect(trackpadZoomTarget(1, -1)).toBeCloseTo(1.006, 3);
    expect(trackpadZoomTarget(1, 1)).toBeCloseTo(0.994, 3);
    expect(trackpadZoomTarget(1, -1_000)).toBeLessThan(1.17);
    expect(trackpadZoomTarget(1, 1_000)).toBeGreaterThan(0.86);
  });

  it("resolves the observed goal outcome from declared YAML outcomes", () => {
    expect(resolveGoalOutcome([
      {id: "approved", name: "Approved", from: ["result"]},
      {id: "manual_review", name: "Human review required", from: ["result"]},
    ], {execution_id: "run-1", application_outcome: "manual_review", product_goal_achieved: true})).toEqual({
      id: "manual_review",
      name: "Human review required",
      achieved: true,
    });
  });

  it("places dependencies in later columns without overlapping siblings", () => {
    const nodes = [
      { id: "start" },
      { id: "native", depends_on: [{ node: "start" }] },
      { id: "ocr", depends_on: [{ node: "start" }] },
      { id: "join", depends_on: [{ node: "native" }, { node: "ocr" }] },
    ];
    const transitions = [
      { from: "start", to: "native" },
      { from: "start", to: "ocr" },
      { from: "native", to: "join" },
      { from: "ocr", to: "join" },
    ];
    const layout = workflowLayout(nodes, transitions);

    expect(layout.positions.get("start")!.x).toBeLessThan(layout.positions.get("native")!.x);
    expect(layout.positions.get("native")!.x).toBe(layout.positions.get("ocr")!.x);
    expect(layout.positions.get("native")!.y).not.toBe(layout.positions.get("ocr")!.y);
    expect(layout.positions.get("join")!.x).toBeGreaterThan(layout.positions.get("native")!.x);
    expect(validateWorkflowGeometry(layout, nodes, transitions)).toEqual([]);
  });

  it("uses color for runtime attention, not clickability", () => {
    expect(statePresentation("failed").border).toBe("#dc5a5a");
    expect(statePresentation("recovered").border).toBe("#d58b24");
    expect(statePresentation("running").border).toBe("#4386c6");
    expect(statePresentation("completed").border).toBe("#16864b");
  });

  it("summarizes workflow-level results without treating missing measurements as zero", () => {
    const stats = summarizeWorkflowRuns([
      { runtime_id: "langgraph", duration_seconds: 2, product_goal_achieved: true, decision_correct: true, workflow_retry_attempts: 0 },
      { runtime_id: "haystack", duration_seconds: 4, product_goal_achieved: false, decision_correct: true, workflow_retry_attempts: 2 },
      { runtime_id: "langgraph", duration_seconds: 9 },
    ]);

    expect(stats.runs).toBe(3);
    expect(stats.goalRate).toBe(0.5);
    expect(stats.decisionRate).toBe(1);
    expect(stats.medianDuration).toBe(4);
    expect(stats.retryRuns).toBe(1);
    expect(stats.retryAttempts).toBe(2);
    expect(stats.runtimeCounts).toEqual([["langgraph", 2], ["haystack", 1]]);
  });

  it("builds a separate connected graph for inspected step evidence", () => {
    const graph = buildStepGraph({
      id: "research",
      name: "Research company",
      kind: "Research",
      state: "completed",
      attempts: 1,
      duration_seconds: 1.2,
      known_cost: 0.01,
      total_tokens: 42,
      providers: ["anthropic"],
      models: ["claude"],
      observations: [
        { id: "operation", kind: "workflow_stage", name: "Research" },
      ],
      model_calls: [
        { id: "model-call", parent_operation_id: "operation", kind: "model", name: "Claude call" },
      ],
    });

    expect(graph.nodes.map((node) => node.id)).toEqual(["step:research", "operation", "model-call"]);
    expect(graph.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["step:research", "operation"],
      ["operation", "model-call"],
    ]);
  });
});
