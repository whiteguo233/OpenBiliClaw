import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  makeExtensionArchiveName,
  makeFirefoxSignedXpiName,
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

test("makeFirefoxSignedXpiName names the installable Firefox package", () => {
  assert.equal(
    makeFirefoxSignedXpiName("extension-v0.1.3"),
    "openbiliclaw-extension-v0.1.3-firefox.xpi",
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

test("Firefox signing script uses AMO unlisted signing and emits XPI", () => {
  const script = readFileSync(resolve("scripts", "sign-firefox.mjs"), "utf8");

  assert.match(script, /AMO_JWT_ISSUER/);
  assert.match(script, /AMO_JWT_SECRET/);
  assert.match(script, /web-ext/);
  assert.match(script, /sign/);
  assert.match(script, /--channel=unlisted/);
  assert.match(script, /makeFirefoxSignedXpiName/);
  assert.doesNotMatch(script, /zip -r -9/);
});

test("extension release workflow publishes signed Firefox XPI when enabled", () => {
  const workflow = readFileSync(
    resolve("..", ".github", "workflows", "release-extension.yml"),
    "utf8",
  );

  assert.match(workflow, /FIREFOX_SIGNING_ENABLED/);
  assert.match(workflow, /AMO_JWT_ISSUER/);
  assert.match(workflow, /AMO_JWT_SECRET/);
  assert.match(workflow, /npm run sign:firefox/);
  assert.match(workflow, /openbiliclaw-extension-v\$\{expected\}-firefox\.xpi/);
  assert.match(workflow, /release-artifacts\/openbiliclaw-extension-v\*\.(zip|xpi)/);
});

test("aggregate release sync treats signed Firefox XPI as a package asset", () => {
  const script = readFileSync(
    resolve("..", ".github", "scripts", "sync-aggregate-release.sh"),
    "utf8",
  );

  assert.match(script, /firefox_signed_asset_line/);
  assert.match(script, /openbiliclaw-extension-v\$\{extension_version\}-firefox\.xpi/);
  assert.match(script, /download_release_assets "\$extension_tag" "openbiliclaw-extension-v\*\.zip" "openbiliclaw-extension-v\*\.xpi"/);
  assert.match(script, /openbiliclaw-extension-v\*\.xpi/);
});
