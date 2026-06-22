const SUFFIX_MULTIPLIERS: Record<string, number> = {
  k: 1_000,
  K: 1_000,
  m: 1_000_000,
  M: 1_000_000,
  b: 1_000_000_000,
  B: 1_000_000_000,
  w: 10_000,
  W: 10_000,
  万: 10_000,
  亿: 100_000_000,
};

export function normalizeMetricCountText(value: unknown): number {
  const text = String(value ?? "").replace(/,/g, "").trim();
  if (!text || text === "--") return 0;
  const match = text.match(/(\d+(?:\.\d+)?)\s*([kKmMbBwW万亿])?/);
  if (!match) return 0;
  const numeric = Number.parseFloat(match[1] ?? "0");
  if (!Number.isFinite(numeric)) return 0;
  const suffix = match[2] ?? "";
  return Math.floor(numeric * (SUFFIX_MULTIPLIERS[suffix] ?? 1));
}

export function collectMetricTexts(root: ParentNode): string[] {
  const selector = [
    "[aria-label]",
    "[title]",
    "span",
    "div",
    "button",
    "[class*='count']",
    "[class*='stat']",
    "[class*='like']",
    "[class*='collect']",
    "[class*='comment']",
    "[class*='share']",
  ].join(",");
  const nodes = Array.from(root.querySelectorAll<Element>(selector));
  return nodes
    .flatMap((node) => [
      node.textContent ?? "",
      node.getAttribute("aria-label") ?? "",
      node.getAttribute("title") ?? "",
    ])
    .map((text) => text.trim())
    .filter(Boolean);
}

export function pickMetricCount(root: ParentNode, labels: readonly string[]): number {
  const lowerLabels = labels.map((label) => label.toLowerCase());
  for (const text of collectMetricTexts(root)) {
    const lower = text.toLowerCase();
    if (!lowerLabels.some((label) => lower.includes(label))) continue;
    const count = normalizeMetricCountText(text);
    if (count > 0) return count;
  }
  return 0;
}
