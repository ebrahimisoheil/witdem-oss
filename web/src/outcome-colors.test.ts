import { describe, expect, it } from "vitest";

import type { ContractDefinition } from "./api";
import { contractOutcomeColors } from "./outcome-colors";

describe("contractOutcomeColors", () => {
  it("gives arbitrary unclassified outcomes distinct stable colors", () => {
    const first = contractOutcomeColors(
      { revision_limit_reached: 1, approved: 1 },
      [],
    );
    const second = contractOutcomeColors(
      { approved: 4, revision_limit_reached: 2 },
      [],
    );

    expect(first.approved).not.toBe(first.revision_limit_reached);
    expect(second).toEqual(first);
  });

  it("uses contract-declared semantic tones without inspecting label names", () => {
    const contracts: ContractDefinition[] = [
      {
        contract_hash: "research",
        run_count: 2,
        result: {
          values: {
            shipped: { tone: "success" },
            needs_editor: { tone: "warning" },
          },
        },
      },
    ];

    expect(contractOutcomeColors({ shipped: 1, needs_editor: 1 }, contracts)).toEqual({
      shipped: "#16864b",
      needs_editor: "#df7a00",
    });
  });

  it("falls back to a categorical color when contracts disagree", () => {
    const contracts: ContractDefinition[] = [
      {
        contract_hash: "one",
        run_count: 1,
        result: { values: { done: { tone: "success" } } },
      },
      {
        contract_hash: "two",
        run_count: 1,
        result: { values: { done: { tone: "failure" } } },
      },
    ];

    expect(contractOutcomeColors({ done: 2 }, contracts).done).toBe("#2f6fed");
  });
});
