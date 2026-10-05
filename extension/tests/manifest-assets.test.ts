import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runtimeAssetCandidates } from "../src/shared/asset-prefix.ts";

test("dynamic asset paths support both unpacked Chrome layouts", () => {
  assert.deepEqual(runtimeAssetCandidates("main/dy-fetch-tap.js", "dist/"), [
    "dist/main/dy-fetch-tap.js",
    "main/dy-fetch-tap.js",
  ]);
  assert.deepEqual(runtimeAssetCandidates("main/dy-fetch-tap.js", ""), [
    "main/dy-fetch-tap.js",
  ]);
});

test("manifest icon assets exist", () => {
  const root = process.cwd();
  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    icons?: Record<string, string>;
    action?: {
      default_icon?: Record<string, string>;
      default_popup?: string;
    };
    permissions?: string[];
    side_panel?: { default_path?: string };
  };

  const iconPaths = new Set<string>([
    ...Object.values(manifest.icons ?? {}),
    ...Object.values(manifest.action?.default_icon ?? {}),
  ]);

  assert.ok(iconPaths.size > 0);
  for (const relativePath of iconPaths) {
    assert.equal(
      existsSync(join(root, relativePath)),
      true,
      `missing icon asset: ${relativePath}`,
    );
  }
});

test("manifest uses side panel instead of popup", () => {
  const root = process.cwd();
  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    action?: { default_popup?: string };
    permissions?: string[];
    side_panel?: { default_path?: string };
  };

  assert.equal(manifest.permissions?.includes("sidePanel"), true);
  assert.equal(manifest.side_panel?.default_path, "popup/popup.html");
  assert.equal("default_popup" in (manifest.action ?? {}), false);
});

test("extension package version files stay aligned", () => {
  const root = process.cwd();
  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    version?: string;
  };
  const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8")) as {
    version?: string;
  };
  const packageLock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8")) as {
    version?: string;
    packages?: Record<string, { version?: string }>;
  };

  assert.equal(packageJson.version, manifest.version);
  assert.equal(packageLock.version, manifest.version);
  assert.equal(packageLock.packages?.[""]?.version, manifest.version);
});

test("Firefox manifest declares required data collection categories", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.firefox.json"), "utf8"),
  ) as {
    browser_specific_settings?: {
      gecko?: {
        strict_min_version?: string;
        data_collection_permissions?: {
          required?: string[];
        };
      };
      gecko_android?: {
        strict_min_version?: string;
      };
    };
  };

  assert.equal(
    manifest.browser_specific_settings?.gecko?.strict_min_version,
    "140.0",
  );
  assert.equal(
    manifest.browser_specific_settings?.gecko_android?.strict_min_version,
    "142.0",
  );
  assert.deepEqual(
    manifest.browser_specific_settings?.gecko?.data_collection_permissions?.required,
    [
      "authenticationInfo",
      "browsingActivity",
      "personalCommunications",
      "searchTerms",
      "websiteActivity",
      "websiteContent",
    ],
  );
});

test("Firefox manifest uses the project-owned AMO Gecko ID", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.firefox.json"), "utf8"),
  ) as {
    browser_specific_settings?: { gecko?: { id?: string } };
  };

  assert.equal(
    manifest.browser_specific_settings?.gecko?.id,
    "openbiliclaw-firefox@whiteguo233.github.io",
  );
});

test("Safari manifest uses action popup instead of side panel", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    action?: { default_popup?: string };
    permissions?: string[];
    side_panel?: { default_path?: string };
  };

  assert.equal(manifest.permissions?.includes("sidePanel"), false);
  assert.equal(manifest.permissions?.includes("notifications"), false);
  assert.equal(manifest.side_panel, undefined);
  assert.equal(manifest.action?.default_popup, "popup/popup.html");
});

test("Safari manifest keeps alarms/scripting and uses a service worker", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    permissions?: string[];
    background?: { service_worker?: string; scripts?: string[] };
    browser_specific_settings?: unknown;
    content_scripts?: Array<{ world?: string }>;
  };

  assert.equal(manifest.permissions?.includes("alarms"), true);
  assert.equal(manifest.permissions?.includes("scripting"), true);
  assert.equal(manifest.permissions?.includes("cookies"), true);
  assert.equal(manifest.background?.service_worker, "background/service-worker.js");
  assert.equal(manifest.background?.scripts, undefined);
  assert.equal(manifest.browser_specific_settings, undefined);
  assert.equal(
    manifest.content_scripts?.some((entry) => entry.world !== undefined),
    false,
  );
});

test("Safari manifest shares host-permission boundaries with Chrome/Firefox", () => {
  const root = process.cwd();
  const safari = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    host_permissions?: string[];
    optional_host_permissions?: string[];
  };

  assert.equal(safari.host_permissions?.includes("<all_urls>"), false);
  assert.equal(safari.host_permissions?.includes("http://*/*"), false);
  assert.equal(safari.host_permissions?.includes("https://*/*"), false);
  assert.equal(safari.host_permissions?.includes("http://127.0.0.1/*"), true);
  assert.equal(safari.host_permissions?.includes("http://localhost/*"), true);
  assert.deepEqual(safari.optional_host_permissions, ["http://*/*", "https://*/*"]);
  assert.equal(safari.host_permissions?.includes("*://*.bilibili.com/*"), true);
});

test("Safari manifest injects MAIN-world taps through a document_start bridge", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    content_scripts?: Array<{
      matches?: string[];
      js?: string[];
      run_at?: string;
      world?: string;
    }>;
    web_accessible_resources?: Array<{ resources?: string[]; matches?: string[] }>;
  };

  const mainContentScripts =
    manifest.content_scripts?.filter((entry) =>
      entry.js?.some((asset) => asset.startsWith("main/")),
    ) ?? [];
  assert.deepEqual(mainContentScripts, []);

  const injector = manifest.content_scripts?.find((entry) =>
    entry.js?.includes("content/safari-page-injector.js"),
  );
  assert.ok(injector, "missing Safari page-context injector content script");
  assert.equal(injector.run_at, "document_start");
  assert.equal(injector.world, undefined);
  for (const host of [
    "*://*.bilibili.com/*",
    "*://*.xiaohongshu.com/*",
    "*://*.douyin.com/*",
    "*://*.x.com/*",
    "*://*.twitter.com/*",
    "*://*.bgm.tv/*",
    "*://*.bangumi.tv/*",
  ]) {
    assert.ok(injector.matches?.includes(host), `injector missing match ${host}`);
  }

  const warResources = new Set(
    (manifest.web_accessible_resources ?? []).flatMap((entry) => entry.resources ?? []),
  );
  for (const script of [
    "main/bili-interact-tap.js",
    "main/xhs-token-sniffer.js",
    "main/xhs-state-bridge.js",
    "main/xhs-action-tap.js",
    "main/dy-fetch-tap.js",
    "main/x-graphql-tap.js",
    "main/bgm-identity-bridge.js",
  ]) {
    assert.ok(warResources.has(script), `missing Safari WAR resource ${script}`);
  }
});

test("Chrome and Firefox manifests avoid all-sites host permission", () => {
  const root = process.cwd();
  const chromeManifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    host_permissions?: string[];
    optional_host_permissions?: string[];
  };
  const firefoxManifest = JSON.parse(
    readFileSync(join(root, "manifest.firefox.json"), "utf8"),
  ) as {
    host_permissions?: string[];
    optional_host_permissions?: string[];
  };

  for (const manifest of [chromeManifest, firefoxManifest]) {
    assert.equal(manifest.host_permissions?.includes("http://*/*"), false);
    assert.equal(manifest.host_permissions?.includes("https://*/*"), false);
    assert.equal(manifest.host_permissions?.includes("<all_urls>"), false);
    assert.equal(manifest.host_permissions?.includes("http://127.0.0.1/*"), true);
    assert.equal(manifest.host_permissions?.includes("http://localhost/*"), true);
    assert.deepEqual([...(manifest.optional_host_permissions ?? [])].sort(), [
      "http://*/*",
      "https://*/*",
      "https://*.instagram.com/*",
    ].sort());
    assert.equal(manifest.host_permissions?.includes("*://*.bilibili.com/*"), true);
    assert.equal(manifest.host_permissions?.includes("*://*.xiaohongshu.com/*"), true);
    assert.equal(manifest.host_permissions?.includes("*://*.douyin.com/*"), true);
    assert.equal(manifest.host_permissions?.includes("*://*.youtube.com/*"), true);
  }
});

test("Chrome and Firefox manifests do not request tabs permission", () => {
  const root = process.cwd();
  const chromeManifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    permissions?: string[];
  };
  const firefoxManifest = JSON.parse(
    readFileSync(join(root, "manifest.firefox.json"), "utf8"),
  ) as {
    permissions?: string[];
  };

  for (const manifest of [chromeManifest, firefoxManifest]) {
    assert.equal(manifest.permissions?.includes("tabs"), false);
  }
});

test("Douyin fetch tap starts in the MAIN world before page requests", () => {
  const root = process.cwd();
  const manifests = [
    {
      manifest: JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
        content_scripts?: Array<{
          matches?: string[];
          js?: string[];
          run_at?: string;
          world?: string;
        }>;
      },
      script: "dist/main/dy-fetch-tap.js",
    },
    {
      manifest: JSON.parse(
        readFileSync(join(root, "manifest.firefox.json"), "utf8"),
      ) as {
        content_scripts?: Array<{
          matches?: string[];
          js?: string[];
          run_at?: string;
          world?: string;
        }>;
      },
      script: "main/dy-fetch-tap.js",
    },
  ];

  for (const { manifest, script } of manifests) {
    const entry = manifest.content_scripts?.find((candidate) =>
      candidate.js?.includes(script),
    );
    assert.ok(entry, `missing Douyin MAIN-world content script: ${script}`);
    assert.deepEqual(entry.matches, ["*://*.douyin.com/*"]);
    assert.equal(entry.run_at, "document_start");
    assert.equal(entry.world, "MAIN");
    const isolatedIndex = manifest.content_scripts?.findIndex((candidate) =>
      candidate.js?.some((asset) => asset.endsWith("content/douyin.js")),
    );
    const mainIndex = manifest.content_scripts?.indexOf(entry);
    assert.ok(
      isolatedIndex !== undefined && mainIndex !== undefined && isolatedIndex < mainIndex,
      "the isolated replay listener must register before the MAIN-world tap",
    );
  }
});
