import { describe, expect, it } from "vitest";
import { buildStepGraph, groupEvaluations, participantOperationRows, resolveGoalOutcome, statePresentation, summarizeWorkflowRuns, trackpadZoomTarget, uniqueIdentities, validateWorkflowGeometry, workflowFitZoom, workflowLayout, workflowRunsHref } from "./workflow-pages";
import type { EvaluationResult, OperationFact, OperationMeasurement } from "./api";

describe("workflow presentation", () => {
  it("links global execution drilldowns by authored workflow identity", () => {
    expect(workflowRunsHref("contract-review")).toBe("/runs?workflow_id=contract-review");
    expect(workflowRunsHref("contract-review", { provider: "open router" })).toBe(
      "/runs?workflow_id=contract-review&provider=open+router",
    );
  });

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

  it("attributes participant measurements without mixing providers", () => {
    const operation = (id: string, provider: string): OperationFact => ({ operation_id: id, execution_id: "run-1", workflow_id: "flow", family: "inference", operation_type: "text_generation", interface: "model_api", role: "application", input_modalities: ["text"], output_modalities: ["text"], provider_id: provider, model_id: `${provider}-model`, duration_seconds: provider === "one" ? 2 : 3, status: "ok", attributes: {} });
    const measurement = (operation_id: string, measurement_key: string, value: number): OperationMeasurement => ({ operation_id, execution_id: "run-1", workflow_id: "flow", measurement_key, value, unit: measurement_key === "cost.usd" ? "USD" : "token", measurement_status: "measured", provenance: "provider" });
    const rows = participantOperationRows([operation("a", "one"), operation("b", "two")], [measurement("a", "cost.usd", 1), measurement("b", "cost.usd", 4), measurement("b", "tokens.total", 20)], "provider");
    expect(rows).toEqual([{ id: "one", calls: 1, time: 2, cost: 1, tokens: null }, { id: "two", calls: 1, time: 3, cost: 4, tokens: 20 }]);
  });

  it("deduplicates participant identities case-insensitively", () => {
    expect(uniqueIdentities(["lancedb", "LanceDB", null, "voyage"])).toEqual(["lancedb", "voyage"]);
  });

  it("groups evaluation definitions and preserves unassessed results", () => {
    const result = (id: string, passed: boolean | null, score: number): EvaluationResult => ({ evaluation_id: id, execution_id: `run-${id}`, subject_id: `case-${id}`, name: "Evidence quality", score, passed, attributes: { target: 0.8, direction: "higher_is_better" } });
    const [group] = groupEvaluations([result("a", true, 1), result("b", null, 0.9), result("c", false, 0.5)]);
    expect(group).toMatchObject({ name: "Evidence quality", passed: 1, attention: 1, unassessed: 1, target: 0.8, direction: "higher_is_better" });
    expect(group.averageScore).toBeCloseTo(0.8);
  });
});
