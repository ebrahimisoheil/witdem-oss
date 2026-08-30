import type { ContractDefinition } from "./api";

const toneColors = {
  success: "#16864b",
  warning: "#df7a00",
  failure: "#d64545",
  neutral: "#536174",
} as const;

const categoricalColors = [
  "#2f6fed",
  "#7357d9",
  "#168e89",
  "#b84f8c",
  "#8a6d1d",
  "#39738f",
  "#715b9c",
  "#4e7c50",
];

export function contractOutcomeColors(
  outcomes: Record<string, number>,
  contracts: ContractDefinition[],
) {
  const declaredTones = new Map<string, keyof typeof toneColors>();
  const conflictingTones = new Set<string>();
  for (const contract of contracts) {
    for (const [name, definition] of Object.entries(contract.result?.values || {})) {
      if (typeof definition === "string" || !definition.tone) continue;
      const key = name.toLowerCase();
      const existing = declaredTones.get(key);
      if (existing && existing !== definition.tone) conflictingTones.add(key);
      else declaredTones.set(key, definition.tone);
    }
  }

  const colors: Record<string, string> = {};
  const unclassified = Object.keys(outcomes)
    .map((name) => name.toLowerCase())
    .filter((name) => !declaredTones.has(name) || conflictingTones.has(name))
    .sort();
  unclassified.forEach((name, index) => {
    colors[name] = categoricalColors[index % categoricalColors.length];
  });
  for (const [name, tone] of declaredTones) {
    if (!conflictingTones.has(name)) colors[name] = toneColors[tone];
  }

  return colors;
}
