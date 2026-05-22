import { execSync } from "node:child_process";
import { cp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Stage the Safari extension build into the Xcode project's Resources folder.
 *
 * Unlike Chrome/Firefox where we zip the build for a web store, Safari
 * extensions ship as native macOS apps. The Xcode project at
 * ../safari/OpenBiliClawSafari.xcodeproj expects the WebExtension resources
 * to live at ../safari/Extension/Resources/. This script (re)builds the
 * extension and refreshes that directory.
 *
 * Usage:
 *   node scripts/package-safari.mjs              # build + stage
 *   node scripts/package-safari.mjs --no-build   # stage only (skip build)
 *
 * After running this, open ../safari/OpenBiliClawSafari.xcodeproj in Xcode
 * and Product > Run to launch the host app, then enable the extension in
 * Safari > Settings > Extensions.
 */

const root = resolve(import.meta.dirname, "..");
const repoRoot = resolve(root, "..");
const distDir = resolve(root, "dist-safari");
const xcodeResources = resolve(repoRoot, "safari", "Extension", "Resources");
const skipBuild = process.argv.includes("--no-build");

// --- 1. Build ---------------------------------------------------------
if (!skipBuild) {
  console.log("Building Safari extension...");
  execSync("npm run build:safari", { cwd: root, stdio: "inherit" });
}

if (!existsSync(distDir)) {
  throw new Error(
    `dist-safari/ does not exist at ${distDir}. Run without --no-build, ` +
      "or run `npm run build:safari` first.",
  );
}

// --- 2. Verify Xcode project exists ----------------------------------
if (!existsSync(resolve(repoRoot, "safari"))) {
  throw new Error(
    `Xcode project directory not found at ${resolve(repoRoot, "safari")}. ` +
      "Generate it first (see safari/README.md — typically `xcodegen generate` " +
      "from inside safari/).",
  );
}

// --- 2a. Verify AppIcon.appiconset is populated ----------------------
// Xcode builds happily with an asset catalog whose Contents.json
// declares 10 image slots but provides zero PNG files — the resulting
// .app just has no icon, with no error or warning in the build log.
// We've been bitten by this once already (the slots existed but every
// filename field was blank, so macOS showed the generic app icon and
// it took a bogus "missing manifest version" commit to diagnose).
// Catch the regression at package time instead of in the user's Finder.
await verifyAppIconSet();

async function verifyAppIconSet() {
  const appIconSet = resolve(
    repoRoot,
    "safari",
    "App",
    "Assets.xcassets",
    "AppIcon.appiconset",
  );
  const contentsPath = resolve(appIconSet, "Contents.json");
  if (!existsSync(contentsPath)) {
    throw new Error(
      `AppIcon set is missing: ${appIconSet}/Contents.json not found.`,
    );
  }
  const contents = JSON.parse(await readFile(contentsPath, "utf-8"));
  const images = Array.isArray(contents.images) ? contents.images : [];
  if (images.length === 0) {
    throw new Error(`AppIcon set declares zero image slots in ${contentsPath}.`);
  }

  const missing = [];
  const noFilename = [];
  for (const slot of images) {
    if (!slot.filename) {
      noFilename.push(`${slot.size}@${slot.scale} (${slot.idiom})`);
      continue;
    }
    const png = resolve(appIconSet, slot.filename);
    if (!existsSync(png)) {
      missing.push(slot.filename);
    }
  }

  if (noFilename.length > 0 || missing.length > 0) {
    const parts = [];
    parts.push(`AppIcon.appiconset is incomplete:`);
    if (noFilename.length > 0) {
      parts.push(
        `  ${noFilename.length} slot(s) have no \`filename\` in Contents.json:`,
      );
      for (const s of noFilename) parts.push(`    - ${s}`);
    }
    if (missing.length > 0) {
      parts.push(`  ${missing.length} referenced PNG file(s) are missing:`);
      for (const m of missing) parts.push(`    - ${m}`);
    }
    parts.push(
      "",
      "  Regenerate with:  python3 scripts/regenerate_icons.py  (from repo root)",
    );
    throw new Error(parts.join("\n"));
  }
}

// --- 3. Read version (for log output) --------------------------------
const manifest = JSON.parse(
  await readFile(resolve(root, "manifest.json"), "utf-8"),
);
const version = manifest.version;

// --- 4. Refresh Resources/ -------------------------------------------
// We wipe and recopy rather than rsync so stale files (renamed bundles,
// removed icons) don't linger in the next Xcode build. We then restore
// a .gitkeep so the directory stays tracked in version control even
// before someone runs this script for the first time.
console.log(`\nStaging dist-safari/ → ${xcodeResources}`);
await rm(xcodeResources, { recursive: true, force: true });
await mkdir(xcodeResources, { recursive: true });
await cp(distDir, xcodeResources, { recursive: true });
await writeFile(resolve(xcodeResources, ".gitkeep"), "");

// --- 5. Report --------------------------------------------------------
const manifestStat = await stat(resolve(xcodeResources, "manifest.json"));
console.log(
  `\nDone: staged OpenBiliClaw v${version} ` +
    `(manifest.json: ${(manifestStat.size / 1024).toFixed(1)} KB)`,
);
console.log("\nNext steps:");
console.log("  1. open safari/OpenBiliClawSafari.xcodeproj");
console.log("  2. Product > Run (⌘R)");
console.log("  3. In Safari: Settings > Extensions > enable OpenBiliClaw");
console.log("     (you may need: Develop > Allow Unsigned Extensions)");
