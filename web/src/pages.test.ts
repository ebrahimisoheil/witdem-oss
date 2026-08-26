import { describe, expect, it } from "vitest";

import { drilldownHref, evaluationMetTarget } from "./pages";

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

  it("respects lower-is-better targets and labels", () => {
    expect(evaluationMetTarget({ score: 0.2, attributes: { target: 0.5, direction: "lower_is_better" } })).toBe(true);
    expect(evaluationMetTarget({ attributes: { label: "valid" } })).toBe(true);
  });
});

describe("drilldownHref", () => {
  it("carries model and goal filters into detail pages", () => {
    expect(drilldownHref("/goal-performance", { model: "gpt-5.4", contract_hash: "goal-1" }))
      .toBe("/goal-performance?model=gpt-5.4&contract_hash=goal-1");
  });
});
