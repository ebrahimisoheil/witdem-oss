import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkflowEvaluationsView } from "./workflow-pages";
import type { WorkflowEvaluations } from "./api";

describe("evaluation page rendering", () => {
  const data: WorkflowEvaluations = {
    workflow_id: "review", summary: { reported: 510, passed: 509, needs_attention: 1, unassessed: 0, executions: 510 },
    results: Array.from({ length: 25 }, (_, index) => ({ evaluation_id: `e${index}`, execution_id: `run${index}`, subject_id: "run", name: "quality", passed: true, score: .9, attributes: {} })),
    campaigns: [], campaigns_status: "unavailable",
    definition_groups: [{ name: "quality", reported: 510, passed: 509, needs_attention: 1, unassessed: 0, average_score: .9, target: .8, direction: ">=" }],
    pagination: { schema_version: "v1alpha1", summary_scope: "all_projected_executions", revision: 2, results_next_cursor: "next", definitions_next_cursor: null, results_total: 510, definitions_total: 1, page_size: 25, definition_page_size: 25, selected_name: null },
  };
  const render = (value: WorkflowEvaluations) => renderToStaticMarkup(<WorkflowEvaluationsView workflowId="review" data={value} loading={false} selectedName={null} onSelect={() => {}} />);
  it("labels full-population totals and renders every result in the supporting page", () => {
    const html = render(data);
    expect(html).toContain("Totals cover all projected executions.");
    expect(html).toContain("25 of 510 supporting results");
    expect(html).toContain("/workflows/review/executions/run24");
    expect(html).toContain("509 passed");
    expect(html).toContain("Campaign data is not available from this source");
    expect(html).not.toContain("No campaign results imported");
  });
  it("keeps legacy campaign reporting and its existing bounded supporting list", () => {
    const html = render({ ...data, pagination: null, definition_groups: null, campaigns_status: null });
    expect(html).toContain("No campaign results imported");
    expect(html).not.toContain("Totals cover all projected executions.");
    expect(html).not.toContain("/workflows/review/executions/run24");
  });
});
