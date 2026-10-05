import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import {
  collectManifestBuildAssets,
  verifyBuildAssets,
} from "../scripts/verify-build-assets.mjs";

async function writeFixture(root: string, relativePath: string, body = "// fixture") {
  const path = join(root, relativePath);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, body);
}

test("build scripts clean and typecheck only their own browser target", async () => {
  const packageJson = JSON.parse(await readFile("package.json", "utf8")) as {
    scripts: Record<string, string>;
  };

  assert.match(packageJson.scripts.build, /clean:chrome/);
  assert.doesNotMatch(packageJson.scripts.build, /clean:firefox/);
  assert.doesNotMatch(packageJson.scripts.build, /clean:safari/);
  assert.match(packageJson.scripts["build:firefox"], /clean:firefox/);
  assert.doesNotMatch(packageJson.scripts["build:firefox"], /clean:chrome/);
  assert.doesNotMatch(packageJson.scripts["build:firefox"], /clean:safari/);
  assert.match(packageJson.scripts["build:safari"], /clean:safari/);
  assert.doesNotMatch(packageJson.scripts["build:safari"], /clean:chrome/);
  assert.doesNotMatch(packageJson.scripts["build:safari"], /clean:firefox/);
  assert.match(packageJson.scripts["build:firefox"], /npm run typecheck/);
  assert.doesNotMatch(packageJson.scripts["build:firefox"], /build:types/);
  assert.match(packageJson.scripts["build:safari"], /npm run typecheck/);
  assert.doesNotMatch(packageJson.scripts["build:safari"], /build:types/);
});

test("manifest preflight includes background, content, and WAR bundles", () => {
  const assets = collectManifestBuildAssets({
    background: { service_worker: "dist/background/service-worker.js" },
    content_scripts: [
      { js: ["dist/content/linuxdo.js"] },
      { js: ["dist/content/douyin.js"] },
    ],
    web_accessible_resources: [{ resources: ["dist/main/dy-fetch-tap.js"] }],
  });
  assert.deepEqual(assets, [
    "dist/background/service-worker.js",
    "dist/content/douyin.js",
    "dist/content/linuxdo.js",
    "dist/main/dy-fetch-tap.js",
  ]);
});

test("Firefox preflight resolves assets from dist-firefox root", async () => {
  const root = await mkdtemp(join(tmpdir(), "obc-firefox-assets-"));
  try {
    const manifest = {
      background: { scripts: ["background/service-worker.js"] },
      content_scripts: [
        { js: ["content/linuxdo.js"] },
        { js: ["content/douyin.js"] },
      ],
      web_accessible_resources: [{ resources: ["main/dy-fetch-tap.js"] }],
    };
    await writeFixture(root, "dist-firefox/manifest.json", JSON.stringify(manifest));
    await writeFixture(root, "dist-firefox/background/service-worker.js");
    await writeFixture(root, "dist-firefox/content/linuxdo.js");
    await writeFixture(root, "dist-firefox/content/douyin.js");
    await writeFixture(root, "dist-firefox/main/dy-fetch-tap.js");

    await assert.doesNotReject(
      verifyBuildAssets({ root, target: "firefox", log: false }),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Safari preflight resolves assets from dist-safari root", async () => {
  const root = await mkdtemp(join(tmpdir(), "obc-safari-assets-"));
  try {
    const manifest = {
      background: { service_worker: "background/service-worker.js" },
      content_scripts: [
        { js: ["content/linuxdo.js"] },
        { js: ["content/douyin.js"] },
        { js: ["content/safari-page-injector.js"] },
      ],
      web_accessible_resources: [
        { resources: ["main/dy-fetch-tap.js"] },
        { resources: ["main/bili-interact-tap.js"] },
      ],
    };
    await writeFixture(root, "dist-safari/manifest.json", JSON.stringify(manifest));
    await writeFixture(root, "dist-safari/background/service-worker.js");
    await writeFixture(root, "dist-safari/content/linuxdo.js");
    await writeFixture(root, "dist-safari/content/douyin.js");
    await writeFixture(root, "dist-safari/content/safari-page-injector.js");
    await writeFixture(root, "dist-safari/main/dy-fetch-tap.js");
    await writeFixture(root, "dist-safari/main/bili-interact-tap.js");

    await assert.doesNotReject(
      verifyBuildAssets({ root, target: "safari", log: false }),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("preflight reports a missing Linux.do bundle by manifest path", async () => {
  const root = await mkdtemp(join(tmpdir(), "obc-chrome-assets-"));
  try {
    const manifest = {
      background: { service_worker: "dist/background/service-worker.js" },
      content_scripts: [{ js: ["dist/content/linuxdo.js"] }],
    };
    await writeFixture(root, "manifest.json", JSON.stringify(manifest));
    await writeFixture(root, "dist/background/service-worker.js");
    await assert.rejects(
      verifyBuildAssets({ root, target: "chrome", log: false }),
      /chrome build is missing manifest assets:[\s\S]*dist\/content\/linuxdo\.js/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("preflight reports a missing WAR bundle by manifest path", async () => {
  const root = await mkdtemp(join(tmpdir(), "obc-chrome-assets-"));
  try {
    const manifest = {
      background: { service_worker: "dist/background/service-worker.js" },
      web_accessible_resources: [
        { resources: ["dist/main/dy-fetch-tap.js"] },
      ],
    };
    await writeFixture(root, "manifest.json", JSON.stringify(manifest));
    await writeFixture(root, "dist/background/service-worker.js");

    await assert.rejects(
      verifyBuildAssets({ root, target: "chrome", log: false }),
      /chrome build is missing manifest assets:[\s\S]*dist\/main\/dy-fetch-tap\.js/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
