import { execSync } from "node:child_process";
import { readFile, rm, stat } from "node:fs/promises";
import { resolve } from "node:path";

import {
  makeExtensionArchiveName,
  normalizeReleaseVersion,
} from "./release-utils.mjs";

/**
 * Package the Firefox extension into a .zip for AMO submission or sideloading.
 *
 * Usage:
 *   node scripts/package-firefox.mjs              # build + zip
 *   node scripts/package-firefox.mjs --no-build    # zip only (skip build)
 */

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist-firefox");
const skipBuild = process.argv.includes("--no-build");
const archiveVersionFlag = process.argv.indexOf("--archive-version");
const archiveVersionInput =
  archiveVersionFlag === -1 ? null : process.argv[archiveVersionFlag + 1];

if (archiveVersionFlag !== -1 && !archiveVersionInput) {
  throw new Error("--archive-version requires a value");
}

// --- 1. Build ---------------------------------------------------------
if (!skipBuild) {
  console.log("Building Firefox extension...");
  execSync("npm run build:firefox", { cwd: root, stdio: "inherit" });
}

// --- 2. Read version from manifest ------------------------------------
// manifest.json is the single source of truth; build:firefox injects the
// same version into dist-firefox/manifest.json at build time.
const manifest = JSON.parse(
  await readFile(resolve(root, "manifest.json"), "utf-8"),
);
const version = normalizeReleaseVersion(archiveVersionInput ?? manifest.version);
const outName = makeExtensionArchiveName(version).replace(
  ".zip",
  "-firefox.zip",
);
const outPath = resolve(root, outName);

// --- 3. Zip dist-firefox/ contents ------------------------------------
// Firefox / AMO require manifest.json at the archive root, so we zip the
// contents of dist-firefox/ (not the directory itself). build:firefox has
// already laid out manifest.json, popup/, icons/, and the bundled scripts
// inside dist-firefox/.
console.log(`\nPackaging ${outName}...`);
await rm(outPath, { force: true });
execSync(`zip -r -9 "${outPath}" .`, { cwd: distDir, stdio: "inherit" });

// --- 4. Report --------------------------------------------------------
const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
