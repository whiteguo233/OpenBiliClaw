import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Convert the Safari extension build (dist-safari/) into an Xcode project using
 * Apple's `safari-web-extension-converter`.
 *
 * Safari does not load a plain manifest.json the way Chrome/Firefox do — it
 * requires an Xcode project that wraps the extension resources. This helper is
 * macOS-only (it shells out to `xcrun`), builds dist-safari/ first, and emits
 * the Xcode project. After conversion the user builds/signs in Xcode (or via
 * `xcodebuild`) and enables the extension in Safari.
 *
 * Usage:
 *   node scripts/convert-safari.mjs                          # build + convert
 *   node scripts/convert-safari.mjs --no-build               # convert only
 *   node scripts/convert-safari.mjs --project-location <path>
 *   node scripts/convert-safari.mjs --bundle-identifier <id>
 */

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist-safari");

const flag = (name) => process.argv.indexOf(`--${name}`);
const projectLocationFlag = flag("project-location");
const bundleIdFlag = flag("bundle-identifier");
const skipBuild = process.argv.includes("--no-build");

const projectLocation =
  projectLocationFlag === -1
    ? resolve(root, "safari-project")
    : resolve(process.argv[projectLocationFlag + 1]);
const bundleIdentifier =
  bundleIdFlag === -1
    ? "com.whiteguo.openbiliclaw.safari"
    : process.argv[bundleIdFlag + 1];

if ((projectLocationFlag !== -1 && !process.argv[projectLocationFlag + 1]) ||
    (bundleIdFlag !== -1 && !process.argv[bundleIdFlag + 1])) {
  throw new Error("--project-location and --bundle-identifier require a value");
}

// --- 1. Build ---------------------------------------------------------
if (!skipBuild) {
  console.log("Building Safari extension...");
  execSync("npm run build:safari", { cwd: root, stdio: "inherit" });
}

if (!existsSync(resolve(distDir, "manifest.json"))) {
  throw new Error(
    `dist-safari/manifest.json not found — run \`npm run build:safari\` first`,
  );
}

// --- 2. Locate the converter ------------------------------------------
let converter;
try {
  converter = execSync("xcrun --find safari-web-extension-converter", {
    encoding: "utf8",
  }).trim();
} catch {
  throw new Error(
    "safari-web-extension-converter not found — run on macOS with Xcode installed",
  );
}

// --- 3. Convert -------------------------------------------------------
console.log(`\nConverting ${distDir} → ${projectLocation}`);
console.log(`Bundle identifier: ${bundleIdentifier}\n`);
execSync(
  [
    converter,
    "--no-open",
    "--force",
    `--project-location ${projectLocation}`,
    "--app-name OpenBiliClaw",
    `--bundle-identifier ${bundleIdentifier}`,
    distDir,
  ].join(" "),
  { stdio: "inherit" },
);

// --- 4. Report --------------------------------------------------------
console.log(`
✅ Safari Xcode project generated at ${projectLocation}

Next steps:
  1. Open ${projectLocation}/OpenBiliClaw.xcodeproj in Xcode.
  2. Select the "OpenBiliClaw" (macOS) target, set your signing team.
  3. Build and run, then enable the extension in Safari (Develop → Allow
     Unsigned Extensions for local dev, or distribute a signed/notarized app).
  4. Safari uses the action popup (no side panel); unsupported Chrome APIs
     (notifications, sidePanel, MAIN-world content scripts) degrade gracefully.
     See docs/safari-extension-build.md for the full matrix.
`);
