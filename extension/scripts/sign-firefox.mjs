import { execFileSync, execSync } from "node:child_process";
import { cp, readFile, readdir, rm, stat } from "node:fs/promises";
import { resolve } from "node:path";

import {
  makeFirefoxSignedXpiName,
  normalizeReleaseVersion,
} from "./release-utils.mjs";

/**
 * Submit the Firefox build to Mozilla AMO for unlisted signing.
 *
 * Usage:
 *   AMO_JWT_ISSUER=... AMO_JWT_SECRET=... node scripts/sign-firefox.mjs
 *   AMO_JWT_ISSUER=... AMO_JWT_SECRET=... node scripts/sign-firefox.mjs --no-build
 *
 * The output .xpi is signed by Mozilla and can be installed directly in
 * regular Firefox Release/Beta builds. The unsigned -firefox.zip remains only
 * for about:debugging temporary loading or AMO submission input.
 */

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist-firefox");
const artifactsDir = resolve(root, "web-ext-artifacts", "firefox-signed");
const skipBuild = process.argv.includes("--no-build");
const archiveVersionFlag = process.argv.indexOf("--archive-version");
const archiveVersionInput =
  archiveVersionFlag === -1 ? null : process.argv[archiveVersionFlag + 1];

if (archiveVersionFlag !== -1 && !archiveVersionInput) {
  throw new Error("--archive-version requires a value");
}

const apiKey = process.env.AMO_JWT_ISSUER;
const apiSecret = process.env.AMO_JWT_SECRET;

if (!apiKey || !apiSecret) {
  throw new Error(
    "Firefox signing requires AMO_JWT_ISSUER and AMO_JWT_SECRET environment variables",
  );
}

if (!skipBuild) {
  console.log("Building Firefox extension before signing...");
  execSync("npm run build:firefox", { cwd: root, stdio: "inherit" });
}

const manifest = JSON.parse(await readFile(resolve(root, "manifest.json"), "utf-8"));
const version = normalizeReleaseVersion(archiveVersionInput ?? manifest.version);
const outName = makeFirefoxSignedXpiName(version);
const outPath = resolve(root, outName);

await rm(artifactsDir, { recursive: true, force: true });
await rm(outPath, { force: true });

console.log(`\nSigning Firefox extension as unlisted AMO package...`);
execFileSync(
  process.platform === "win32" ? "npx.cmd" : "npx",
  [
    "web-ext",
    "sign",
    "--channel=unlisted",
    `--source-dir=${distDir}`,
    `--artifacts-dir=${artifactsDir}`,
    `--api-key=${apiKey}`,
    `--api-secret=${apiSecret}`,
  ],
  { cwd: root, stdio: "inherit" },
);

const signedFiles = (await readdir(artifactsDir))
  .filter((entry) => entry.endsWith(".xpi"))
  .sort();

if (signedFiles.length !== 1) {
  throw new Error(
    `Expected exactly one signed Firefox .xpi in ${artifactsDir}, found ${signedFiles.length}`,
  );
}

await cp(resolve(artifactsDir, signedFiles[0]), outPath);

const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
