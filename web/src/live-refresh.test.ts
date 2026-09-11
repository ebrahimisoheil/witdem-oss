import { expect, it } from "vitest";
import { liveExecutionRefreshInterval } from "./workflow-pages";

it("refreshes live OSS and Enterprise executions only", () => {
  expect(liveExecutionRefreshInterval({status:"running"})).toBe(2000);
  expect(liveExecutionRefreshInterval({runtime_status:"running"})).toBe(2000);
  expect(liveExecutionRefreshInterval({runtime_status:"completed",status:"running"})).toBe(false);
  for (const status of ["completed", "failed", "recovered", "unknown"])
    expect(liveExecutionRefreshInterval({status})).toBe(false);
  expect(liveExecutionRefreshInterval()).toBe(false);
});
