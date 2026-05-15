import { execSync } from "node:child_process";
import { readFile, rm, stat } from "node:fs/promises";
import { resolve } from "node:path";

import {
  makeExtensionArchiveName,
  normalizeReleaseVersion,
} from "./release-utils.mjs";

/**
 * Package the extension into a .zip for Chrome Web Store or sideloading.
 *
 * Usage:
 *   node scripts/package.mjs          # build + zip
 *   node scripts/package.mjs --no-build   # zip only (skip build)
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
  console.log("Building extension...");
  execSync("npm run build", { cwd: root, stdio: "inherit" });
}

// --- 2. Read version from manifest ------------------------------------
const manifest = JSON.parse(
  await readFile(resolve(root, "manifest.json"), "utf-8"),
);
const version = normalizeReleaseVersion(archiveVersionInput ?? manifest.version);
const outName = makeExtensionArchiveName(version);
const outPath = resolve(root, outName);

// --- 3. Collect files to include --------------------------------------
// Only ship what the browser needs: manifest, dist/, icons/, popup/
const includes = ["manifest.json", "dist", "icons", "popup"];

console.log(`\nPackaging ${outName}...`);
await rm(outPath, { force: true });
execSync(`zip -r -9 "${outPath}" ${includes.join(" ")}`, {
  cwd: root,
  stdio: "inherit",
});

// --- 4. Report --------------------------------------------------------
const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
