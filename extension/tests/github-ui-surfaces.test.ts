import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  buildContentUrl as buildPopupContentUrl,
  getRecommendationCardKind as getPopupCardKind,
  normalizeRecommendation as normalizePopupRecommendation,
  platformDisplayName,
} from "../popup/popup-helpers.js";
import { normalizeCanonicalSavedItem } from "../popup/popup-saved-sync.js";
import {
  buildContentUrl as buildMobileContentUrl,
  getRecommendationCardKind as getMobileCardKind,
  getSourceLabel,
  normalizeRecommendation as normalizeMobileRecommendation,
  normalizeSourcePlatform,
} from "../../src/openbiliclaw/web/js/view-models.js";
import { buildAppDeepLink } from "../../src/openbiliclaw/web/js/app-launch.js";

const REPOSITORY = {
  source_platform: "gh",
  content_id: "repository:1296269",
  content_url: "https://github.com/octocat/Hello-World",
  content_type: "repository",
  title: "octocat/Hello-World",
  body_text: "A public repository used for GitHub UI parity.",
  cover_url: "https://example.com/must-not-render.jpg",
};

test("popup and mobile normalize GitHub aliases, hosts, labels, and repository text cards", () => {
  const popup = normalizePopupRecommendation(REPOSITORY);
  const mobile = normalizeMobileRecommendation(REPOSITORY);

  assert.equal(popup.source_platform, "github");
  assert.equal(mobile.source_platform, "github");
  assert.equal(
    normalizeSourcePlatform({ content_url: "https://github.com/octocat/Hello-World" }),
    "github",
  );
  assert.equal(platformDisplayName("gh"), "GitHub");
  assert.equal(getSourceLabel("github"), "GitHub");
  assert.deepEqual(getPopupCardKind(popup), {
    kind: "text",
    coverUrl: "",
    text: REPOSITORY.body_text,
  });
  assert.deepEqual(getMobileCardKind(mobile), {
    kind: "text",
    coverUrl: "",
    text: REPOSITORY.body_text,
  });
});

test("GitHub navigation keeps the canonical HTTPS repository URL and claims no native scheme", () => {
  assert.equal(buildPopupContentUrl(REPOSITORY), REPOSITORY.content_url);
  assert.equal(buildMobileContentUrl(REPOSITORY), REPOSITORY.content_url);
  assert.equal(buildAppDeepLink(REPOSITORY.content_url), "");

  const withoutCanonicalUrl = { ...REPOSITORY, content_url: "" };
  assert.equal(buildPopupContentUrl(withoutCanonicalUrl), "");
  assert.equal(buildMobileContentUrl(withoutCanonicalUrl), "");
});

test("popup saved identity recognizes both gh and github.com without inventing a content type", () => {
  assert.deepEqual(normalizeCanonicalSavedItem(REPOSITORY), {
    item_key: "github:repository:1296269",
    source_platform: "github",
    content_id: "repository:1296269",
    content_url: REPOSITORY.content_url,
    content_type: "repository",
  });
  assert.equal(
    normalizeCanonicalSavedItem({
      content_url: REPOSITORY.content_url,
      content_id: "repository:1296269",
      content_type: "repository",
    }).source_platform,
    "github",
  );
});

test("GitHub stays backend-only: popup parity adds no GitHub host permission or content script", () => {
  for (const manifestName of ["manifest.json", "manifest.firefox.json"]) {
    const manifest = JSON.parse(readFileSync(resolve(manifestName), "utf8"));
    const hostPermissions = [
      ...(manifest.host_permissions || []),
      ...(manifest.optional_host_permissions || []),
    ];
    assert.equal(hostPermissions.some((value) => String(value).includes("github.com")), false);
    const contentScriptMatches = (manifest.content_scripts || [])
      .flatMap((entry) => entry.matches || []);
    assert.equal(contentScriptMatches.some((value) => String(value).includes("github.com")), false);
  }

  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");
  const mobileCss = readFileSync(
    resolve("..", "src", "openbiliclaw", "web", "css", "app.css"),
    "utf8",
  );
  assert.match(popupHtml, /data-source-card="github"/);
  assert.match(popupHtml, /data-source-status="github"/);
  assert.match(popupHtml, /source-platform-github/);
  assert.match(mobileCss, /\.card-source\[data-source="github"\]/);
});
