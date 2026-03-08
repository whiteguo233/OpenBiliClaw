import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");

const entrypoints = [
  {
    entry: resolve(root, "src/background/service-worker.ts"),
    outfile: resolve(root, "dist/background/service-worker.js"),
  },
  {
    entry: resolve(root, "src/content/collector.ts"),
    outfile: resolve(root, "dist/content/collector.js"),
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
    target: "chrome120",
    sourcemap: true,
    logLevel: "info",
  });
}
