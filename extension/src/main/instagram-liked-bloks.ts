/** Data-only decoder for native Likes Bloks. Never evaluates embedded actions. */
import type { InstagramObservedEnvelope } from "../content/instagram/response-buffer.ts";
import type { InstagramWireItem } from "../content/instagram/task-executor.ts";
import { instagramCanonicalUrl, instagramMediaIdentity } from "../shared/platforms/instagram.ts";

type Atom = string | boolean | null | Atom[] | { [key: string]: Atom };
const EMPTY_LABELS = new Set(["你没有赞过任何内容", "You haven't liked anything yet"]);

function mediaMaps(expression: string): Record<string, Atom>[] {
  if (expression.length > 256_000) throw new Error("bounded");
  let pos = 0;
  let nodes = 0;
  const maps: Record<string, Atom>[] = [];
  const ws = () => { while (/\s/.test(expression[pos] || "~")) pos++; };
  function parse(depth = 0): Atom {
    if (++nodes > 20_000 || depth > 64) throw new Error("bounded");
    ws();
    if (expression[pos] === '"') {
      const start = pos++;
      while (pos < expression.length) {
        if (expression[pos] === "\\") { pos += 2; continue; }
        if (expression[pos++] === '"') return JSON.parse(expression.slice(start, pos)) as string;
      }
      throw new Error("string");
    }
    if (expression[pos] !== "(") {
      const token = /^[^\s,()]+/.exec(expression.slice(pos))?.[0];
      if (!token) throw new Error("token");
      pos += token.length;
      // Numeric lexemes stay strings; large IDs must not lose precision.
      return token === "true" ? true : token === "false" ? false : token === "null" ? null : token;
    }
    pos++;
    const operator = parse(depth + 1);
    const args: Atom[] = [];
    ws();
    while (expression[pos] === ",") {
      pos++;
      args.push(parse(depth + 1));
      ws();
    }
    if (expression[pos++] !== ")") throw new Error("expression");
    if (operator === "bk.action.array.Make") return args;
    if (["bk.action.i32.Const", "bk.action.bool.Const", "bk.action.string.Const"].includes(String(operator))) {
      return args.length === 1 ? args[0]! : null;
    }
    if (operator === "bk.action.map.Make" && Array.isArray(args[0]) && Array.isArray(args[1])) {
      if (args[0].length !== args[1].length) throw new Error("map");
      const value: Record<string, Atom> = Object.create(null) as Record<string, Atom>;
      for (const [index, key] of args[0].entries()) {
        if (typeof key !== "string" || Object.hasOwn(value, key)) throw new Error("key");
        value[key] = args[1][index]!;
      }
      maps.push(value);
      return value;
    }
    return null; // Unknown actions are not executed or treated as data values.
  }
  parse();
  ws();
  if (pos !== expression.length) throw new Error("trailing");
  return maps;
}

/** Decode only whitelisted fields and explicit native empty-state evidence. */
export function parseInstagramLikedBloks(payload: unknown): InstagramObservedEnvelope | null {
  const tree = (payload as { payload?: { layout?: { bloks_payload?: { tree?: unknown } } } })
    ?.payload?.layout?.bloks_payload?.tree;
  if (!tree || typeof tree !== "object") return null;
  const items = new Map<string, InstagramWireItem>();
  let empty = false;
  let rejected = 0;
  let unresolvedReference = false;
  let nodes = 0;
  function walk(value: unknown, depth = 0): void {
    if (!value || typeof value !== "object") return;
    if (++nodes > 20_000 || depth > 64) { rejected++; return; }
    for (const [key, nested] of Object.entries(value)) {
      if ((key === "bk.components.Text" || key === "bk.components.TextSpan") && nested && typeof nested === "object") {
        if (EMPTY_LABELS.has(String((nested as { text?: unknown }).text || ""))) empty = true;
      }
      if (key === "on_bind" && typeof nested === "string" && nested.includes("media_id")) {
        try {
          let matched = false;
          for (const map of mediaMaps(nested)) {
            if (!Object.hasOwn(map, "media_id")) continue;
            matched = true;
            // The native grid includes an empty cell to pad its last row.
            if (map.media_id === "" && map.media_code === "" && map.media_type === null
              && map.media_product_type === "" && map.media_image_url === "") continue;
            const id = instagramMediaIdentity(map.media_id);
            const code = map.media_code;
            const type = String(map.media_type);
            if (typeof id !== "string" || !/^[1-9][0-9]*$/.test(id)
              || typeof code !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(code)
              || !["1", "2", "8"].includes(type)) { rejected++; continue; }
            if (items.size >= 80) break;
            const contentType = type === "2" ? "reel" : type === "8" ? "carousel" : "post";
            items.set(id, { id, code, content_type: contentType, url: instagramCanonicalUrl(contentType, code),
              ...(typeof map.media_image_url === "string" ? { cover_url: map.media_image_url.slice(0, 4096) } : {}) });
          }
          // Visibility/template bindings refer to media_id without defining a
          // membership. Do not count them as rejected media, or infer empty.
          if (!matched) unresolvedReference = true;
        } catch { rejected++; }
      }
      walk(nested, depth + 1);
    }
  }
  walk(tree);
  const terminalEmpty = empty && !items.size && !rejected && !unresolvedReference;
  return { route: "liked", collection_id: "liked:bloks", items: [...items.values()],
    observed_count: items.size + rejected, rejected_count: rejected,
    shape_valid: rejected === 0 && (terminalEmpty || items.size > 0),
    ...(terminalEmpty ? { affirmative_terminal: true, has_next_page: false } : {}) };
}
