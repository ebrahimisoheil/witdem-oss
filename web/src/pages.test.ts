import { describe, expect, it } from "vitest";

import { drilldownHref, evaluationMetTarget, goalPortfolioRunsHref, issueSignalCount, measurementAttentionMessages } from "./pages";
import { sharedCohortSize } from "./components";
import type { ComparisonInsight, GoalPortfolioItem } from "./api";
import type { Overview } from "./api";
import type { Issues } from "./api";

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

  it("creates bookmarkable semantic and failure drilldowns", () => {
    expect(drilldownHref("/runs", {
      contract_hash: "goal-1",
      goal_status: "not_achieved",
      evaluation_status: "failed",
      has_failure: true,
      ignored: false,
    })).toBe("/runs?contract_hash=goal-1&goal_status=not_achieved&evaluation_status=failed&has_failure=true");
  });
});

describe("goalPortfolioRunsHref", () => {
  it("opens a single goal run directly and filters multi-run goals", () => {
    const item = {
      runs: 1,
      single_execution_id: "run-123",
      contract_hash: "goal-1",
      contract_hashes: ["goal-1"],
    } as GoalPortfolioItem;

    expect(goalPortfolioRunsHref(item, { provider: "acme" })).toBe("/runs?id=run-123");
    expect(goalPortfolioRunsHref({ ...item, runs: 2, single_execution_id: null }, { provider: "acme" }))
      .toBe("/runs?provider=acme&contract_hash=goal-1");
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

describe("issueSignalCount", () => {
  it("counts visible issue signals and deduplicates retry runs", () => {
    const issues = {
      failures: [{ execution_id: "failed" }],
      quality_gaps: [{ execution_id: "quality" }],
      outliers: [],
      operation_failures: [{ type: "retrieval" }],
      missing_required_measurements: [{ operation_type: "generation", measurement_key: "cost.usd" }],
      retries: [
        { runs: [{ execution_id: "retried" }, { execution_id: "retried" }] },
        { runs: [{ execution_id: "retried" }, { execution_id: "retried-again" }] },
      ],
    } as unknown as Issues;

    expect(issueSignalCount(issues)).toBe(6);
  });
});

describe("sharedCohortSize", () => {
  const participant = (cohort_key: string, runs = 11) => ({
    cohort_key,
    runs,
  }) as ComparisonInsight;

  it("detects when participant percentages describe the same runs", () => {
    expect(sharedCohortSize([participant("same"), participant("same")])).toBe(11);
    expect(sharedCohortSize([participant("one"), participant("two")])).toBeNull();
  });
});
