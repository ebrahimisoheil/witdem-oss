import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { operationPageQuery, type WorkflowOperations } from "./api";
import { WorkflowOperationsView } from "./workflow-pages";

describe("paginated operation scope", () => {
  const data: WorkflowOperations = {
    workflow_id: "review", summary: { total_operations: 510, failed_operations: 2, execution_containers: 50, types: [
      { type: "text_generation", family: "inference", plane: "work", operations: 400, failed: 1, active_seconds: 90,
        roles: [], interfaces: [], providers: ["provider"], models: [], implementations: [], measurements: { "cost.usd": 0 },
        detail_truncated: ["providers"] },
    ] },
    measurement_coverage: { measured: 300, missing: 100, applicable: 400, not_applicable: 5, coverage: .75 },
    operations: Array.from({ length: 25 }, (_, i) => ({ operation_id: `op${i}`, execution_id: `run${i}`,
      workflow_id: "review", family: "inference", operation_type: "text_generation", interface: "model_api", role: "app",
      input_modalities: [], output_modalities: [], duration_seconds: null, status: null, attributes: {} })),
    measurements: [], participants: [{ dimension: "provider", id: "provider", calls: 400, time: 90, cost: 0, tokens: null }],
    pagination: { schema_version: "v1alpha1", summary_scope: "all_projected_executions", measurements_scope: "not_included",
      revision: 2, operations_next_cursor: "next-op", types_next_cursor: "next-type", types_total: 30, page_size: 25,
      type_page_size: 10, detail_limit: 20, operation_type: null, participant_dimension: "provider",
      participant_metric: "calls", participant_limit: 10 },
  };
  it("uses all-history totals and renders the entire supporting page with explicit scope", () => {
    const html = renderToStaticMarkup(<WorkflowOperationsView workflowId="review" data={data} loading={false} onRequest={() => {}} />);
    expect(html).toContain("510");
    expect(html).toContain("75%");
    expect(html).toContain("Showing 1 of 30 type summaries and 25 supporting operations");
    expect(html).toContain("Type charts cover this page only");
    expect(html).toContain("Supporting metadata limited to 20 entries per list: providers");
    expect(html).toContain("/workflows/review/executions/run24");
    expect(html).toContain("Next supporting operations");
    expect(html).toContain("Not reported");
  });
  it("does not mistake an empty filtered page for an empty workflow", () => {
    const html = renderToStaticMarkup(<WorkflowOperationsView workflowId="review" data={{ ...data, operations: [],
      summary: { ...data.summary, types: [] }, pagination: { ...data.pagination!, operation_type: "unmatched" } }} loading={false} />);
    expect(html).toContain("510");
    expect(html).toContain("No supporting operations match this page and filter");
    expect(html).not.toContain("No classified operations have been materialized");
  });
  it("omits cleared cursor/filter parameters and encodes exact values", () => {
    expect(operationPageQuery({ after: undefined, operation_type: undefined })).toBe("");
    expect(operationPageQuery({ after: "a+b=", operation_type: "a/b" })).toBe("?after=a%2Bb%3D&operation_type=a%2Fb");
  });
});
