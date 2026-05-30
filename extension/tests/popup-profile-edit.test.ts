import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

test("popup-api exposes profile edit client hitting the right endpoints", () => {
  const api = readFileSync(resolve("popup", "popup-api.js"), "utf8");
  assert.match(api, /export async function fetchEditState\(\)/);
  assert.match(api, /requestJson\("\/profile\/edit-state", \{ method: "GET" \}\)/);
  assert.match(api, /export async function submitProfileEdit\(/);
  assert.match(api, /requestJson\("\/profile\/edit"/);
  assert.match(api, /method: "POST"/);
});

test("profile view exposes an edit toggle + panel and edit-mode wiring", () => {
  const html = readFileSync(resolve("popup", "popup.html"), "utf8");
  assert.match(html, /id="profileEditToggle"/);
  assert.match(html, /id="profileEditPanel"/);

  const js = readFileSync(resolve("popup", "popup.js"), "utf8");
  assert.match(js, /function renderEditPanel\(/);
  assert.match(js, /function enterProfileEditMode\(/);
  assert.match(js, /function applyProfileEdit\(/);
  assert.match(js, /bindProfileEditToggle\(\)/);
  // deterministic edit ops are posted
  assert.match(js, /op: "remove", value:/);
  assert.match(js, /op: "set", value/);
  assert.match(js, /op: "reset"/);
  // re-render keeps edit mode so a background refresh can't unhide the card
  assert.match(js, /function syncProfileEditChrome\(/);
});

test("profile edit mode is a page-level replacement state", () => {
  const html = readFileSync(resolve("popup", "popup.html"), "utf8");
  const js = readFileSync(resolve("popup", "popup.js"), "utf8");

  assert.match(html, /\.view\.is-profile-editing\s+#profileCard\s*\{[\s\S]*display:\s*none\s*!important/);
  assert.match(html, /\.view\.is-profile-editing\s+#profileEditPanel\s*\{[\s\S]*display:\s*flex\s*!important/);
  assert.match(js, /function setProfileEditingLayout\(/);
  assert.match(js, /elements\.viewProfile\.classList\.toggle\("is-profile-editing", editing\)/);
  assert.match(js, /setProfileEditingLayout\(profileEditing\)/);
});

test("edit panel covers the un-truncated editable fields", () => {
  const js = readFileSync(resolve("popup", "popup.js"), "utf8");
  for (const path of [
    "personality_portrait",
    "core.core_traits",
    "interest.favorite_up_users",
    "likes",
    "dislikes",
  ]) {
    assert.ok(js.includes(`"${path}"`), `EDIT_FIELD_ORDER should include ${path}`);
  }
});

test("edit panel renders scalar (slider) fields committing a 0..1 float", () => {
  const js = readFileSync(resolve("popup", "popup.js"), "utf8");
  for (const path of [
    "surface.exploration_openness",
    "surface.style.quality_sensitivity",
    "surface.style.humor_preference",
    "surface.style.depth_preference",
  ]) {
    assert.ok(js.includes(`"${path}"`), `EDIT_FIELD_ORDER should include ${path}`);
  }
  assert.match(js, /function renderScalarEditField\(/);
  assert.match(js, /field\.type === "scalar"/);
  // slider commits as a 0..1 float on explicit save (not per-drag)
  assert.match(js, /op: "set", value: Number\(slider\.value\) \/ 100/);
});
