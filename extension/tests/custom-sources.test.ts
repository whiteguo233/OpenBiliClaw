import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { normalizeRecommendation, normalizeDelightCandidate, recommendationSourceName } from "../popup/popup-helpers.js";

test("custom website labels retain source identity and web content behavior", () => {
  const raw = { source_platform: "web", source_id: "source-1", source_name: "我的研究站", content_id: "article-1", content_url: "https://example.com/article", title: "论文" };
  const item = normalizeRecommendation(raw);
  assert.equal(item.source_platform, "web");
  assert.equal(item.source_id, "source-1");
  assert.equal(item.source_name, "我的研究站");
  assert.equal(recommendationSourceName(item), "我的研究站");
  assert.equal(normalizeDelightCandidate(raw).source_name, "我的研究站");
  assert.equal(recommendationSourceName({ source_platform: "bilibili" }), "B 站");
});

test("popup and desktop load shared manager and put custom shares beside platform shares", () => {
  const popup = readFileSync(new URL("../popup/popup.html", import.meta.url), "utf8");
  const desktop = readFileSync(new URL("../../src/openbiliclaw/web/desktop/index.html", import.meta.url), "utf8");
  for (const html of [popup, desktop]) {
    assert.match(html, /shared\/custom-sources\.js/);
    assert.match(html, /shared\/custom-sources\.css/);
    assert.equal((html.match(/id="customSourceManager"/g) || []).length, 1);
    assert.match(html, /与.*来源平级/);
    const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
    assert.equal(new Set(ids).size, ids.length);
  }
  const code = readFileSync(new URL("../popup/popup.js", import.meta.url), "utf8");
  assert.match(code, /baseUrl: getBackendOrigin/);
  assert.match(code, /openUrl: \(url\) => chrome\.tabs\.create/);
  assert.match(code, /void customSourceManager\?\.load\(\)/);
  const desktopCode = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");
  assert.match(desktopCode, /entries\.push\(\.\.\.\(window\.OpenBiliClawCustomSources\?\.shareEntries\(customSourceItems\)/);
});
