import { describe, expect, it } from "vitest";

import { drilldownHref, evaluationMetTarget, measurementAttentionMessages } from "./pages";
import type { Overview } from "./api";

describe("evaluationMetTarget", () => {
  it("reads SDK scores from semantic attributes", () => {
    expect(
      evaluationMetTarget({
        kind: "evaluation",
        value: 0.8333,
        attributes: { score: 0.8333, target: 1, direction: "higher_is_better" },
      }),
    ).toBe(false);
  });

  it("respects lower-is-better targets and explicit pass states", () => {
    expect(evaluationMetTarget({ score: 0.2, attributes: { target: 0.5, direction: "lower_is_better" } })).toBe(true);
    expect(evaluationMetTarget({ attributes: { passed: true } })).toBe(true);
    expect(evaluationMetTarget({ attributes: { label: "valid" } })).toBe(null);
  });
});

describe("drilldownHref", () => {
  it("carries model and goal filters into detail pages", () => {
    expect(drilldownHref("/goal-performance", { model: "gpt-5.4", contract_hash: "goal-1" }))
      .toBe("/goal-performance?model=gpt-5.4&contract_hash=goal-1");
  });
});

describe("measurementAttentionMessages", () => {
  it("distinguishes incomplete applicable tokens from absent billable activity", () => {
    const coverage = (partial_runs: number, missing_runs: number) => ({
      total_runs: 32,
      applicable_runs: 2,
      complete_runs: 2 - partial_runs - missing_runs,
      partial_runs,
      missing_runs,
      not_applicable_runs: 30,
      eligible_operations: 2,
      measured_operations: 2 - missing_runs,
      coverage: partial_runs || missing_runs ? 0 : 1,
      operation_coverage: missing_runs ? 0.5 : 1,
    });
    const data = {
      costs: { cost: coverage(0, 0), tokens: coverage(2, 0) },
    } as unknown as Overview;

    expect(measurementAttentionMessages(data)).toEqual([
      "Token measurement is incomplete for 2 partial and 0 unmeasured applicable runs.",
    ]);
  });

  it("does not treat not-applicable runs as missing", () => {
    const complete = {
      total_runs: 32,
      applicable_runs: 0,
      complete_runs: 0,
      partial_runs: 0,
      missing_runs: 0,
      not_applicable_runs: 32,
      eligible_operations: 0,
      measured_operations: 0,
      coverage: 0,
      operation_coverage: 0,
    };
    const data = { costs: { cost: complete, tokens: complete } } as unknown as Overview;
    expect(measurementAttentionMessages(data)).toEqual([]);
  });
});
