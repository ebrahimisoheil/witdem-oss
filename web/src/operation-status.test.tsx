import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { WorkflowOperations } from "./api";
import { operationStatusPresentation, WorkflowOperationsView, workflowParticipantRows, participantOperationRows } from "./workflow-pages";

describe("operation status rendering", () => {
  it("uses server participant totals even when supporting operations are empty", () => {
    const aggregate = { dimension: "provider" as const, id: "p", calls: 510, time: 25, cost: 0, tokens: null };
    expect(workflowParticipantRows([], [], "provider", [aggregate])).toEqual([aggregate]);
    expect(workflowParticipantRows([], [], "model", [aggregate])).toEqual([]);
    expect(workflowParticipantRows([], [], "provider", [])).toEqual([]);
  });
  it("does not mix measurement identities across executions in the legacy fallback", () => {
    const base = { operation_id: "same", workflow_id: "review", family: "inference", operation_type: "text_generation",
      interface: "model_api", role: "application", input_modalities: [], output_modalities: [],
      duration_seconds: 1, status: "ok", attributes: {} };
    const rows = participantOperationRows([{ ...base, execution_id: "a", provider_id: "one" },
      { ...base, execution_id: "b", provider_id: "two" }], [{ operation_id: "same", execution_id: "a",
      workflow_id: "review", measurement_key: "cost.usd", value: 0, unit: "USD", measurement_status: "measured",
      provenance: "provider" }], "provider");
    expect(rows.map((row) => row.cost)).toEqual([0, null]);
  });
  it("keeps absent, unassessed and vendor-specific states neutral", () => {
    for (const status of [null, undefined, "", "unset", "running", "vendor-specific"]) {
      expect(operationStatusPresentation(status).failed).toBe(false);
      expect(operationStatusPresentation(status).className).toBe("text-[#89838b]");
    }
    expect(operationStatusPresentation(null).label).toBe("Not reported");
    expect(operationStatusPresentation("unset").label).toBe("Observed");
    expect(operationStatusPresentation("vendor-specific").label).toBe("vendor-specific");
    expect(operationStatusPresentation("error").failed).toBe(true);
    expect(operationStatusPresentation("ok").className).toBe("text-[#27754c]");
  });
  it("renders an operation with absent status and elapsed time", () => {
    const data: WorkflowOperations = {
      workflow_id: "review", summary: { total_operations: 1, execution_containers: 0, failed_operations: 0,
        types: [{ type: "text_generation", family: "inference", plane: "work", operations: 1, failed: 0,
          active_seconds: 0, roles: [], interfaces: [], providers: [], models: [], implementations: [],
          model_applicability: "applicable", linked_children: [], measurements: {} }] },
      measurement_coverage: { measured: 0, missing: 0, not_applicable: 0, applicable: 0, coverage: null },
      measurements: [], operations: [{ operation_id: "op", execution_id: "run", workflow_id: "review",
        family: "inference", operation_type: "text_generation", interface: "model_api", role: "application",
        input_modalities: [], output_modalities: [], duration_seconds: null, status: null, attributes: {} }],
    };
    const html = renderToStaticMarkup(<WorkflowOperationsView workflowId="review" data={data} loading={false} />);
    expect(html).toContain("/workflows/review/executions/run");
    expect(html).toContain('class="text-[#89838b]">Not reported</div>');
    expect(html).not.toContain(">null<");
    expect(html).not.toContain("NaN");
  });
});
