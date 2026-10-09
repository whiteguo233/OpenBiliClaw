import assert from "node:assert/strict";
import test from "node:test";
import { parseInstagramLikedBloks } from "../src/main/instagram-liked-bloks.ts";

const payload = (tree: unknown) => ({ payload: { layout: { bloks_payload: { tree } } } });

test("real nonempty grid normalizes composite IDs and ignores padding and UI references", () => {
  // Shape observed 2026-09-30; all values below are synthetic.
  const keys = '(bk.action.array.Make, "media_id", "media_code", "media_product_type", "media_type", "media_image_url")';
  const media = `(bk.action.map.Make, ${keys}, (bk.action.array.Make, "9007199254740993123_456", "EXAMPLE", "clips", (bk.action.i32.Const, 2), "https://scontent.example/image.jpg"))`;
  const padding = `(bk.action.map.Make, ${keys}, (bk.action.array.Make, "", "", "", null, ""))`;
  const result = parseInstagramLikedBloks(payload({
    grid: { on_bind: `(bk.action.array.Map, (bk.action.array.Make, ${media}, ${padding}), (bk.action.core.FuncConst, null))` },
    visibility: { on_bind: '(bk.action.map.Get, (bk.action.core.GetTemplateArg, "item", (bk.action.i32.Const, 3)), "media_id")' },
  }));
  assert.equal(result?.items.length, 1);
  assert.equal(result?.items[0]?.id, "9007199254740993123");
  assert.equal(result?.items[0]?.url, "https://www.instagram.com/reel/EXAMPLE/");
  assert.equal(result?.rejected_count, 0);
  assert.equal(result?.shape_valid, true);
  assert.equal(result?.affirmative_terminal, undefined);
});
test("native likes empty evidence is exact and does not infer empty from unknown trees", () => {
  const empty = parseInstagramLikedBloks(payload({ "bk.components.Text": { text: "你没有赞过任何内容" } }));
  assert.equal(empty?.affirmative_terminal, true);
  assert.deepEqual(empty?.items, []);
  assert.equal(parseInstagramLikedBloks(payload({ "bk.components.Text": { text: "Loading" } }))?.affirmative_terminal, undefined);
  assert.equal(parseInstagramLikedBloks({}), null);
  const unresolved = parseInstagramLikedBloks(payload({
    "bk.components.Text": { text: "你没有赞过任何内容" },
    binding: { on_bind: '(bk.action.map.Get, null, "media_id")' },
  }));
  assert.equal(unresolved?.affirmative_terminal, undefined);
  assert.equal(unresolved?.shape_valid, false);
});
test("data-only Bloks maps preserve large media IDs without executing expressions", () => {
  const bind = '(bk.action.map.Make, (bk.action.array.Make, "media_id", "media_code", "media_type"), (bk.action.array.Make, "9007199254740993123", "EXAMPLE", (bk.action.i32.Const, 2)))';
  const result = parseInstagramLikedBloks(payload({ "bk.components.Flexbox": { on_bind: bind } }));
  assert.equal(result?.items[0]?.id, "9007199254740993123");
  assert.equal(result?.items[0]?.url, "https://www.instagram.com/reel/EXAMPLE/");
  assert.equal(result?.affirmative_terminal, undefined);
  assert.equal(parseInstagramLikedBloks(payload({ on_bind: '(evil.Run, "media_id")' }))?.shape_valid, false);
});
