import { execFileSync, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, rm, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { normalizeReleaseVersion } from "./release-utils.mjs";

/**
 * Package the Safari Web Extension into a notarized macOS app archive.
 *
 * Safari does not load a raw manifest.json package. The extension must be
 * wrapped in the converter-generated Xcode project, built into an .app,
 * optionally signed + notarized, and then shipped as a .dmg or .zip.
 *
 * Usage:
 *   node scripts/package-safari.mjs                          # build + convert + sign (ad-hoc) + dmg
 *   node scripts/package-safari.mjs --no-build               # reuse existing dist-safari/
 *   node scripts/package-safari.mjs --no-convert             # reuse existing safari-project/
 *   node scripts/package-safari.mjs --no-package             # stop after xcodebuild
 *   node scripts/package-safari.mjs --format zip             # .zip instead of .dmg
 *   node scripts/package-safari.mjs --configuration Debug   # build Debug config
 *   node scripts/package-safari.mjs --notarize               # Developer ID sign + notarize + staple
 *
 * Signing / notarization inputs come from the same environment variables the
 * release workflow uses:
 *   APPLE_SIGNING_IDENTITY  "Developer ID Application: ..." (defaults to ad-hoc "-")
 *   APPLE_TEAM_ID           Apple Developer team id
 *   APPLE_NOTARY_USER       Apple ID used for notarytool
 *   APPLE_NOTARY_PASSWORD   App-specific password for notarytool
 */

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist-safari");

function flagValue(name) {
  const index = process.argv.indexOf(`--${name}`);
  if (index === -1) return null;
  const value = process.argv[index + 1];
  if (!value) throw new Error(`--${name} requires a value`);
  return value;
}

const skipBuild = process.argv.includes("--no-build");
const skipConvert = process.argv.includes("--no-convert");
const skipPackage = process.argv.includes("--no-package");
const notarize = process.argv.includes("--notarize");
const archiveVersionInput = flagValue("archive-version");
const format = (flagValue("format") ?? "dmg").toLowerCase();
const configuration = flagValue("configuration") ?? "Release";
const projectLocation = resolve(
  root,
  flagValue("project-location") ?? "safari-project",
);
const signIdentity =
  process.env.APPLE_SIGNING_IDENTITY || flagValue("sign-identity") || "-";
const teamId = process.env.APPLE_TEAM_ID || flagValue("team-id") || "";

/** Versioned artifact name, exported for unit tests. */
export function makeSafariArtifactName(version, artifactFormat) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(version)}-safari.${artifactFormat}`;
}

async function packageSafari() {
  if (!["dmg", "zip"].includes(format)) {
    throw new Error("--format must be dmg or zip");
  }
  if (notarize && !teamId) {
    throw new Error("--notarize requires APPLE_TEAM_ID (or --team-id)");
  }
  if (
    notarize &&
    (!process.env.APPLE_NOTARY_USER || !process.env.APPLE_NOTARY_PASSWORD)
  ) {
    throw new Error(
      "--notarize requires APPLE_NOTARY_USER and APPLE_NOTARY_PASSWORD",
    );
  }

  // --- 1. Build dist-safari/ --------------------------------------------
  if (!skipBuild) {
    console.log("Building Safari extension...");
    execSync("npm run build:safari", { cwd: root, stdio: "inherit" });
  }

  if (!existsSync(resolve(distDir, "manifest.json"))) {
    throw new Error(
      "dist-safari/manifest.json not found — run `npm run build:safari` first",
    );
  }

  // --- 2. Convert to Xcode project --------------------------------------
  if (!skipConvert) {
    console.log("\nConverting Safari extension to Xcode project...");
    execSync(
      [
        "npm",
        "run",
        "convert:safari",
        "--",
        "--no-build",
        "--project-location",
        projectLocation,
      ].join(" "),
      { cwd: root, stdio: "inherit" },
    );
  }

  const xcodeProject = resolve(
    projectLocation,
    "OpenBiliClaw",
    "OpenBiliClaw.xcodeproj",
  );
  if (!existsSync(xcodeProject)) {
    throw new Error(
      `Xcode project not found at ${xcodeProject} — run \`npm run convert:safari\` first`,
    );
  }

  // --- 3. xcodebuild ------------------------------------------------------
  // Keep DerivedData inside the ignored safari-project/ directory so a local
  // package run never pollutes ~/Library/Developer/Xcode/DerivedData.
  const derivedData = resolve(projectLocation, "derived-data");
  await rm(derivedData, { recursive: true, force: true });

  console.log(
    `\nBuilding macOS app (${configuration}, identity: ${signIdentity})...`,
  );
  const xcodeArgs = [
    "-project",
    xcodeProject,
    "-scheme",
    "OpenBiliClaw (macOS)",
    "-configuration",
    configuration,
    "-destination",
    "platform=macOS",
    "-derivedDataPath",
    derivedData,
    "CODE_SIGN_STYLE=Manual",
    `CODE_SIGN_IDENTITY=${signIdentity}`,
    "CODE_SIGNING_REQUIRED=YES",
    "CODE_SIGNING_ALLOWED=YES",
  ];
  if (teamId) {
    xcodeArgs.push(`DEVELOPMENT_TEAM=${teamId}`);
  }
  if (notarize) {
    xcodeArgs.push("OTHER_CODE_SIGN_FLAGS=--timestamp");
  }
  execFileSync("xcodebuild", xcodeArgs, { stdio: "inherit" });

  const appPath = resolve(
    derivedData,
    "Build",
    "Products",
    configuration,
    "OpenBiliClaw.app",
  );
  if (!existsSync(appPath)) {
    throw new Error(
      `xcodebuild finished but the app was not found at ${appPath}`,
    );
  }

  execFileSync("codesign", ["--verify", "--deep", "--strict", appPath], {
    stdio: "inherit",
  });

  // --- 4. Notarize + staple ----------------------------------------------
  if (notarize) {
    const notaryZip = resolve(projectLocation, "notarize.zip");
    await rm(notaryZip, { force: true });
    console.log("\nSubmitting app for notarization...");
    execFileSync(
      "ditto",
      ["-c", "-k", "--sequesterRsrc", "--keepParent", appPath, notaryZip],
      { stdio: "inherit" },
    );
    execFileSync(
      "xcrun",
      [
        "notarytool",
        "submit",
        notaryZip,
        "--apple-id",
        process.env.APPLE_NOTARY_USER,
        "--password",
        process.env.APPLE_NOTARY_PASSWORD,
        "--team-id",
        teamId,
        "--wait",
      ],
      { stdio: "inherit" },
    );
    console.log("\nStapling notarization ticket...");
    execFileSync("xcrun", ["stapler", "staple", appPath], {
      stdio: "inherit",
    });
    execFileSync("xcrun", ["stapler", "validate", appPath], {
      stdio: "inherit",
    });
    await rm(notaryZip, { force: true });
  }

  // --- 5. Package ----------------------------------------------------------
  if (skipPackage) {
    console.log(`\nSkipping archive packaging (app at ${appPath})`);
    await rm(derivedData, { recursive: true, force: true });
    return;
  }

  const manifest = JSON.parse(
    await readFile(resolve(root, "manifest.json"), "utf-8"),
  );
  const version = normalizeReleaseVersion(
    archiveVersionInput ?? manifest.version,
  );
  const outName = makeSafariArtifactName(version, format);
  const outPath = resolve(root, outName);
  await mkdir(root, { recursive: true });
  await rm(outPath, { force: true });

  console.log(`\nPackaging ${outName}...`);
  if (format === "dmg") {
    execFileSync(
      "hdiutil",
      [
        "create",
        "-volname",
        "OpenBiliClaw Safari",
        "-srcfolder",
        appPath,
        "-ov",
        "-format",
        "UDZO",
        outPath,
      ],
      { stdio: "inherit" },
    );
  } else {
    execFileSync(
      "ditto",
      ["-c", "-k", "--sequesterRsrc", "--keepParent", appPath, outPath],
      { stdio: "inherit" },
    );
  }

  const stats = await stat(outPath);
  const sizeKB = (stats.size / 1024).toFixed(1);
  console.log(`\nDone: ${outName} (${sizeKB} KB)`);

  // --- 6. Clean up DerivedData ---------------------------------------------
  await rm(derivedData, { recursive: true, force: true });
}

const isMain =
  import.meta.url === pathToFileURL(process.argv[1] ?? "").href;
if (isMain) {
  await packageSafari();
}
