import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

import { verifyBuildAssets } from "./verify-build-assets.mjs";

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
    entry: resolve(root, "src/main/bili-interact-tap.ts"),
    outfile: resolve(root, `${outDir}/main/bili-interact-tap.js`),
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
    entry: resolve(root, "src/main/xhs-action-tap.ts"),
    outfile: resolve(root, `${outDir}/main/xhs-action-tap.js`),
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
  {
    entry: resolve(root, "src/content/zhihu.ts"),
    outfile: resolve(root, `${outDir}/content/zhihu.js`),
  },
  {
    entry: resolve(root, "src/content/weibo.ts"),
    outfile: resolve(root, `${outDir}/content/weibo.js`),
  },
  {
    entry: resolve(root, "src/content/reddit.ts"),
    outfile: resolve(root, `${outDir}/content/reddit.js`),
  },
  {
    entry: resolve(root, "src/content/linuxdo.ts"),
    outfile: resolve(root, `${outDir}/content/linuxdo.js`),
  },
  {
    entry: resolve(root, "src/content/v2ex.ts"),
    outfile: resolve(root, `${outDir}/content/v2ex.js`),
  },
  {
    entry: resolve(root, "src/content/instagram.ts"),
    outfile: resolve(root, `${outDir}/content/instagram.js`),
  },
  {
    entry: resolve(root, "src/main/instagram-response-tap.ts"),
    outfile: resolve(root, `${outDir}/main/instagram-response-tap.js`),
  },
  {
    entry: resolve(root, "src/content/x.ts"),
    outfile: resolve(root, `${outDir}/content/x.js`),
  },
  {
    entry: resolve(root, "src/main/x-graphql-tap.ts"),
    outfile: resolve(root, `${outDir}/main/x-graphql-tap.js`),
  },
  {
    entry: resolve(root, "src/content/bangumi.ts"),
    outfile: resolve(root, `${outDir}/content/bangumi.js`),
  },
  {
    entry: resolve(root, "src/main/bgm-identity-bridge.ts"),
    outfile: resolve(root, `${outDir}/main/bgm-identity-bridge.js`),
  },
];

// Frontend logic shared with the desktop page and the setup wizard, which load
// it over HTTP from the backend's /shared mount. MV3's default CSP
// (`script-src 'self'`) forbids the side panel doing the same, so the file has
// to be physically present in the extension package — hence a copy, generated
// on every build and gitignored. Copying (rather than committing a second
// checked-in file) is what keeps it a *shared module* instead of a fourth
// hand-maintained duplicate of the same table.
// Runs before the Firefox staging step below, which recursively copies popup/.
const sharedWebDir = resolve(root, "../src/openbiliclaw/web/shared");
await cp(sharedWebDir, resolve(root, "popup/shared"), { recursive: true });
console.log(`📁 Copied web/shared/ → popup/shared/`);

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
    // Runtime asset paths differ by layout: Chrome loads from the repo root so
    // bundles live under dist/; Firefox packaged builds zip dist-firefox/ as the
    // root, placing bundles at main/…, content/… with no dist/ prefix. Inject the
    // right prefix so dynamic executeScript/getURL paths resolve in both.
    define: { __OBC_ASSET_PREFIX__: JSON.stringify(isFirefox ? "" : "dist/") },
    // Firefox structured-clones the completion value of MAIN-world file
    // injections and rejects non-clonable results (the script still executes);
    // a trailing `null;` guarantees every bundle ends with a clonable value.
    // Safe globally here because all bundles are classic IIFE scripts.
    footer: { js: "null;" },
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

await verifyBuildAssets({ root, target: isFirefox ? "firefox" : "chrome" });

console.log(`\n✅ Build complete: ${outDir}/\n`);
