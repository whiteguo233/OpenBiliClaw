/** Extract the web viewer, never a public profile or cookie-derived identity. */
export function parseInstagramViewer(html: string, username: string): string {
  if (!username || html.length > 4 * 1024 * 1024) return "";
  const identities = new Set<string>();
  let invalid = false;
  let visited = 0;
  function walk(value: unknown, depth = 0): void {
    if (!value || typeof value !== "object") return;
    if (depth > 32 || ++visited > 100_000) { invalid = true; return; }
    if (Array.isArray(value)) {
      for (const entry of value) walk(entry, depth + 1);
      return;
    }
    const object = value as Record<string, unknown>;
    if (Array.isArray(object.define)) {
      for (const entry of object.define) {
        if (!Array.isArray(entry) || entry[0] !== "PolarisViewer") continue;
        const viewer = entry[2] as { id?: unknown; data?: { id?: unknown; username?: unknown } } | null;
        const id = viewer?.id;
        if (typeof id !== "string" || !/^[1-9][0-9]*$/.test(id)
          || id !== viewer?.data?.id || viewer?.data?.username !== username) invalid = true;
        else identities.add(id);
      }
    }
    for (const nested of Object.values(object)) walk(nested, depth + 1);
  }
  for (const script of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    if (!/\bdata-sjs(?:\s|=|$)/i.test(script[1] || "")) continue;
    try { walk(JSON.parse(script[2] || "")); } catch { /* Non-JSON script. */ }
  }
  return !invalid && identities.size === 1 ? [...identities][0]! : "";
}
