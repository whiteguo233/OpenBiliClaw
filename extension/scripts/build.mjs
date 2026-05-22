import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");

const targetEnv = process.env.TARGET ?? "chrome";
const isFirefox = targetEnv === "firefox";
const isSafari = targetEnv === "safari";

// esbuild --target flags. Safari uses MV3 only on 17.4+, so we target Safari 17.
const buildTarget = isFirefox
  ? "firefox140"
  : isSafari
    ? "safari17"
    : "chrome120";

// Output directory per target. Safari mirrors the Firefox layout
// (no `dist/` prefix in manifest paths) so the bundle drops directly into
// the extension's resource root.
const outDir = isFirefox
  ? "dist-firefox"
  : isSafari
    ? "dist-safari"
    : "dist";

const labelMap = {
  chrome: "Chrome/Edge",
  firefox: "Firefox",
  safari: "Safari",
};
console.log(`\n🔨 Building for ${labelMap[targetEnv] ?? targetEnv} (target: ${buildTarget})\n`);

const entrypoints = [
  {
    entry: resolve(root, "src/background/service-worker.ts"),
    outfile: resolve(root, `${outDir}/background/service-worker.js`),
  },
  {
    entry: resolve(root, "src/content/bilibili.ts"),
    outfile: resolve(root, `${outDir}/content/bilibili.js`),
  },
  {
    entry: resolve(root, "src/content/xiaohongshu.ts"),
    outfile: resolve(root, `${outDir}/content/xiaohongshu.js`),
  },
  {
    entry: resolve(root, "src/content/douyin.ts"),
    outfile: resolve(root, `${outDir}/content/douyin.js`),
  },
  {
    entry: resolve(root, "src/main/xhs-token-sniffer.ts"),
    outfile: resolve(root, `${outDir}/main/xhs-token-sniffer.js`),
  },
  {
    entry: resolve(root, "src/main/xhs-state-bridge.ts"),
    outfile: resolve(root, `${outDir}/main/xhs-state-bridge.js`),
  },
  {
    entry: resolve(root, "src/main/dy-fetch-tap.ts"),
    outfile: resolve(root, `${outDir}/main/dy-fetch-tap.js`),
  },
  {
    entry: resolve(root, "src/content/youtube.ts"),
    outfile: resolve(root, `${outDir}/content/youtube.js`),
  },
];

for (const target of entrypoints) {
  await mkdir(dirname(target.outfile), { recursive: true });
  await build({
    entryPoints: [target.entry],
    outfile: target.outfile,
    bundle: true,
    format: "iife",
    platform: "browser",
    target: buildTarget,
    sourcemap: true,
    logLevel: "info",
  });
}

// For non-Chrome builds (Firefox / Safari) we lay out a self-contained
// resource directory: write the appropriate manifest with the version
// injected from manifest.json (single source of truth), and stage popup/
// and icons/ alongside the bundled scripts.
if (isFirefox || isSafari) {
  const chromeManifest = JSON.parse(
    await readFile(resolve(root, "manifest.json"), "utf-8"),
  );
  const sourceManifestName = isFirefox ? "manifest.firefox.json" : "manifest.safari.json";
  const sourceManifest = JSON.parse(
    await readFile(resolve(root, sourceManifestName), "utf-8"),
  );
  // Preserve source manifest field order: insert version right after `name`.
  const merged = {};
  for (const [key, value] of Object.entries(sourceManifest)) {
    merged[key] = value;
    if (key === "name") merged.version = chromeManifest.version;
  }
  await writeFile(
    resolve(root, `${outDir}/manifest.json`),
    `${JSON.stringify(merged, null, 4)}\n`,
  );
  console.log(
    `\n📄 Wrote ${outDir}/manifest.json (version ${chromeManifest.version} from manifest.json)`,
  );

  // popup/ and icons/ must be present alongside the manifest at the
  // resource root for the extension to load.
  await cp(resolve(root, "popup"), resolve(root, `${outDir}/popup`), { recursive: true });
  await cp(resolve(root, "icons"), resolve(root, `${outDir}/icons`), { recursive: true });
  console.log(`📁 Copied popup/ → ${outDir}/popup/`);
  console.log(`📁 Copied icons/ → ${outDir}/icons/`);
}

console.log(`\n✅ Build complete: ${outDir}/\n`);
