import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

// The engagement-stats row (▶ views · 👍 likes · 💬 comments · ⭐ favorites ·
// 弹幕 danmaku) must render on BOTH the recommendation card and the delight
// (surprise) card, mirroring the desktop + mobile web surfaces. These are
// static source contracts that lock the wiring in place.

const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
const popupHelpers = readFileSync(resolve("popup", "popup-helpers.js"), "utf8");

test("popup exposes the shared count formatter + stats builder", () => {
  assert.match(popupJs, /function formatCountCn\(/);
  assert.match(popupJs, /function recommendationStats\(/);
  assert.match(popupJs, /function appendRecommendationStats\(/);

  // Chinese unit condensation.
  assert.ok(popupJs.includes("亿"), "missing 亿 unit");
  assert.ok(popupJs.includes("万"), "missing 万 unit");

  // Every engagement segment, gated on > 0, joined with " · ".
  for (const marker of ["▶ ", "👍 ", "💬 ", "🔁 ", "⭐ ", "弹幕 "]) {
    assert.ok(popupJs.includes(marker), `missing stats segment ${marker}`);
  }
  assert.ok(popupJs.includes('segments.join(" · ")'), "segments must join with ' · '");
  assert.ok(!popupJs.includes("formatCountCn(item.source_rank)"), "rank must stay an ordinal");
  assert.ok(popupJs.includes("segments.push(`排名 #${sourceRank}`)"));
});

test("recommendation card appends the stats element", () => {
  const renderRecommendations = popupJs.slice(
    popupJs.indexOf("function renderRecommendations"),
    popupJs.indexOf("\nfunction ", popupJs.indexOf("function renderRecommendations") + 1),
  );
  assert.match(
    renderRecommendations,
    /appendRecommendationStats\(content, item\)/,
    "recommendation card must append stats for the item",
  );
});

test("delight card appends the stats element", () => {
  const buildDelightCard = popupJs.slice(
    popupJs.indexOf("function buildDelightCard"),
    popupJs.indexOf("function buildDelightCard") + 4000,
  );
  assert.match(
    buildDelightCard,
    /appendRecommendationStats\(item, delight\)/,
    "delight card must append stats for the delight",
  );
});

test("stats element only renders when non-empty", () => {
  const appendFn = popupJs.slice(
    popupJs.indexOf("function appendRecommendationStats"),
    popupJs.indexOf("function appendRecommendationStats") + 400,
  );
  // Empty stats string ⇒ no element created.
  assert.match(appendFn, /if \(!text\) return;/);
  assert.match(appendFn, /className = "recommendation-stats"/);
});

test("delight normalizer threads the raw count fields through", () => {
  for (const field of [
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
    "favorite_count",
    "danmaku_count",
    "rating_score",
    "rating_count",
    "source_rank",
  ]) {
    assert.ok(
      popupHelpers.includes(`${field}: Number(item?.${field}`),
      `normalizeDelightCandidate drops ${field}`,
    );
  }
});

test("normalizeRecommendation keeps the engagement counts (else the card row is empty)", async () => {
  const { normalizeRecommendation } = await import("../popup/popup-helpers.js");
  const rec = normalizeRecommendation({
    id: 1,
    bvid: "BV1x",
    title: "t",
    view_count: 69000,
    like_count: 3200,
    comment_count: 880,
    share_count: 77,
    favorite_count: 455, // XHS 收藏 folded into favorite_count backend-side
    danmaku_count: 150,
  });
  assert.equal(rec.view_count, 69000);
  assert.equal(rec.like_count, 3200);
  assert.equal(rec.comment_count, 880);
  assert.equal(rec.share_count, 77);
  assert.equal(rec.favorite_count, 455);
  assert.equal(rec.danmaku_count, 150);
});

test("popup styles the stats row as muted meta text", () => {
  const block = popupHtml.match(/\.recommendation-stats\s*\{[\s\S]*?\}/);
  assert.ok(block, "missing .recommendation-stats CSS rule");
  assert.ok(block![0].includes("var(--text-muted)"), "stats row must use muted token");
});
