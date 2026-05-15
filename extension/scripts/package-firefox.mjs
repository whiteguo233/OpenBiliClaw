import { execSync } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
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
const manifest = JSON.parse(
  await readFile(resolve(root, "manifest.firefox.json"), "utf-8"),
);
const version = normalizeReleaseVersion(archiveVersionInput ?? manifest.version);
const outName = makeExtensionArchiveName(version).replace(
  ".zip",
  "-firefox.zip",
);
const outPath = resolve(root, outName);

// --- 3. Collect files to include --------------------------------------
// Ship: dist-firefox/ (as dist/), icons/, popup/, manifest.firefox.json (as manifest.json)
// The build script already copies manifest.firefox.json → dist-firefox/manifest.json,
// so we just zip the dist-firefox directory contents + static assets.
const includes = ["dist-firefox/manifest.json", "dist-firefox/background", "dist-firefox/content", "dist-firefox/main", "icons", "popup"];

console.log(`\nPackaging ${outName}...`);
execSync(
  `cd "${root}" && zip -r -9 "${outPath}" ${includes.join(" ")}`,
  { stdio: "inherit" },
);

// --- 4. Report --------------------------------------------------------
const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
