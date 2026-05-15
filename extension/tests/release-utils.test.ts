import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  makeExtensionArchiveName,
  normalizeReleaseVersion,
} from "../scripts/release-utils.mjs";

test("normalizeReleaseVersion strips extension channel prefix", () => {
  assert.equal(normalizeReleaseVersion("extension-v0.1.3"), "v0.1.3");
});

test("normalizeReleaseVersion preserves plain manifest versions", () => {
  assert.equal(normalizeReleaseVersion("0.1.3"), "v0.1.3");
});

test("makeExtensionArchiveName keeps only the user-facing version", () => {
  assert.equal(
    makeExtensionArchiveName("extension-v0.1.3"),
    "openbiliclaw-extension-v0.1.3.zip",
  );
});

test("package scripts remove stale archive before zipping", () => {
  const chromeScript = readFileSync(resolve("scripts", "package.mjs"), "utf8");
  const firefoxScript = readFileSync(resolve("scripts", "package-firefox.mjs"), "utf8");

  for (const script of [chromeScript, firefoxScript]) {
    assert.match(script, /rm\(outPath,\s*\{\s*force:\s*true\s*\}\)/);
    assert.match(script, /zip -r -9/);
  }
});

test("Firefox build target matches manifest minimum version", () => {
  const script = readFileSync(resolve("scripts", "build.mjs"), "utf8");

  assert.match(script, /firefox140/);
});
