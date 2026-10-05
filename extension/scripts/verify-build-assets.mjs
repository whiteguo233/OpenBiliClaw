import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { isAbsolute, relative, resolve, sep } from "node:path";

const TARGETS = {
  chrome: {
    manifestPath: "manifest.json",
    assetRoot: ".",
  },
  firefox: {
    manifestPath: "dist-firefox/manifest.json",
    assetRoot: "dist-firefox",
  },
  safari: {
    manifestPath: "dist-safari/manifest.json",
    assetRoot: "dist-safari",
  },
};

function addAsset(assets, value, source) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${source} contains an invalid asset path`);
  }
  assets.add(value);
}

/**
 * Return every executable or web-accessible asset referenced by a manifest.
 * Static HTML and icons are intentionally outside this preflight: the build
 * regression this guard prevents is a missing generated bundle.
 */
export function collectManifestBuildAssets(manifest) {
  const assets = new Set();

  if (manifest.background?.service_worker !== undefined) {
    addAsset(assets, manifest.background.service_worker, "background.service_worker");
  }
  for (const [index, script] of (manifest.background?.scripts ?? []).entries()) {
    addAsset(assets, script, `background.scripts[${index}]`);
  }
  for (const [entryIndex, contentScript] of (manifest.content_scripts ?? []).entries()) {
    for (const [scriptIndex, script] of (contentScript.js ?? []).entries()) {
      addAsset(
        assets,
        script,
        `content_scripts[${entryIndex}].js[${scriptIndex}]`,
      );
    }
  }

  for (const [entryIndex, resourceEntry] of (
    manifest.web_accessible_resources ?? []
  ).entries()) {
    for (const [resourceIndex, resource] of (resourceEntry.resources ?? []).entries()) {
      addAsset(
        assets,
        resource,
        `web_accessible_resources[${entryIndex}].resources[${resourceIndex}]`,
      );
    }
  }

  if (assets.size === 0) {
    throw new Error("manifest does not reference any generated scripts or WAR assets");
  }
  return [...assets].sort();
}

function resolveContainedAsset(assetRoot, assetPath) {
  if (isAbsolute(assetPath) || assetPath.includes("*")) {
    throw new Error(`unsupported manifest asset path: ${assetPath}`);
  }
  const resolved = resolve(assetRoot, assetPath);
  const relativePath = relative(assetRoot, resolved);
  if (relativePath === ".." || relativePath.startsWith(`..${sep}`)) {
    throw new Error(`manifest asset escapes the extension root: ${assetPath}`);
  }
  return resolved;
}

/** Verify generated manifest assets for one browser target. */
export async function verifyBuildAssets({
  root = resolve(import.meta.dirname, ".."),
  target = "chrome",
  log = true,
} = {}) {
  const targetConfig = TARGETS[target];
  if (!targetConfig) {
    throw new Error(`unknown build target: ${target}`);
  }

  const manifestPath = resolve(root, targetConfig.manifestPath);
  const assetRoot = resolve(root, targetConfig.assetRoot);
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const assets = collectManifestBuildAssets(manifest);
  const missing = [];

  for (const assetPath of assets) {
    const resolvedAsset = resolveContainedAsset(assetRoot, assetPath);
    try {
      const assetStat = await stat(resolvedAsset);
      if (!assetStat.isFile()) missing.push(assetPath);
    } catch (error) {
      if (error?.code === "ENOENT") {
        missing.push(assetPath);
        continue;
      }
      throw error;
    }
  }

  if (missing.length > 0) {
    throw new Error(
      `${target} build is missing manifest assets:\n${missing
        .map((asset) => `- ${asset}`)
        .join("\n")}`,
    );
  }

  if (log) {
    console.log(
      `\n✅ ${target} asset preflight: ${assets.length} manifest scripts/WAR assets present`,
    );
  }
  return assets;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const targetFlag = process.argv.indexOf("--target");
  const target = targetFlag === -1 ? "chrome" : process.argv[targetFlag + 1];
  if (!target) throw new Error("--target requires chrome, firefox, or safari");
  await verifyBuildAssets({ target });
}
