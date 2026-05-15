import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");

const isFirefox = process.env.TARGET === "firefox";
const buildTarget = isFirefox ? "firefox140" : "chrome120";
const outDir = isFirefox ? "dist-firefox" : "dist";

console.log(`\n🔨 Building for ${isFirefox ? "Firefox" : "Chrome/Edge"} (target: ${buildTarget})\n`);

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

// For Firefox builds, write the Firefox manifest with version injected from
// the Chrome manifest (single source of truth), and stage popup/icons.
if (isFirefox) {
  const chromeManifest = JSON.parse(
    await readFile(resolve(root, "manifest.json"), "utf-8"),
  );
  const firefoxManifest = JSON.parse(
    await readFile(resolve(root, "manifest.firefox.json"), "utf-8"),
  );
  // Preserve Firefox manifest field order: insert version right after `name`.
  const merged = {};
  for (const [key, value] of Object.entries(firefoxManifest)) {
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

  // Firefox loads the extension from dist-firefox/, so popup/ and icons/ must be present there
  await cp(resolve(root, "popup"), resolve(root, `${outDir}/popup`), { recursive: true });
  await cp(resolve(root, "icons"), resolve(root, `${outDir}/icons`), { recursive: true });
  console.log(`📁 Copied popup/ → ${outDir}/popup/`);
  console.log(`📁 Copied icons/ → ${outDir}/icons/`);
}

console.log(`\n✅ Build complete: ${outDir}/\n`);
