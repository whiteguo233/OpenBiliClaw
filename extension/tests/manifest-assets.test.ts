import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

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

test("Safari manifest targets 17.4+ and uses a popup launcher instead of side panel", () => {
  const root = process.cwd();
  const manifest = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    manifest_version?: number;
    browser_specific_settings?: {
      safari?: { strict_min_version?: string };
    };
    background?: { scripts?: string[]; service_worker?: string };
    action?: { default_popup?: string };
    side_panel?: { default_path?: string };
    permissions?: string[];
  };

  assert.equal(manifest.manifest_version, 3);
  // Safari MV3 + MAIN world content scripts require 17.4.
  assert.equal(
    manifest.browser_specific_settings?.safari?.strict_min_version,
    "17.4",
  );
  // Safari prefers the scripts array form for the background.
  assert.ok(Array.isArray(manifest.background?.scripts));
  assert.equal("service_worker" in (manifest.background ?? {}), false);
  // No side panel / sidebar — Safari doesn't support either.
  assert.equal(manifest.permissions?.includes("sidePanel"), false);
  assert.equal("side_panel" in manifest, false);
  // The action opens the compact launcher.
  assert.equal(manifest.action?.default_popup, "popup/popup-launcher.html");
});

test("Safari popup launcher assets exist", () => {
  const root = process.cwd();
  assert.equal(
    existsSync(join(root, "popup", "popup-launcher.html")),
    true,
    "missing popup/popup-launcher.html",
  );
  assert.equal(
    existsSync(join(root, "popup", "popup-launcher.js")),
    true,
    "missing popup/popup-launcher.js",
  );
});

test("Safari manifest hosts and content-script entries match the Chrome manifest", () => {
  // Safari and Chrome should target the same set of sites — divergence
  // means we forgot to update one of them when adding a new platform.
  const root = process.cwd();
  const chrome = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    host_permissions?: string[];
    content_scripts?: Array<{ matches?: string[]; js?: string[] }>;
  };
  const safari = JSON.parse(
    readFileSync(join(root, "manifest.safari.json"), "utf8"),
  ) as {
    host_permissions?: string[];
    content_scripts?: Array<{ matches?: string[]; js?: string[] }>;
  };

  assert.deepEqual(
    [...(safari.host_permissions ?? [])].sort(),
    [...(chrome.host_permissions ?? [])].sort(),
  );

  const stripDistPrefix = (path: string) =>
    path.startsWith("dist/") ? path.slice("dist/".length) : path;
  const normalize = (scripts: Array<{ matches?: string[]; js?: string[] }> = []) =>
    scripts
      .map((s) => ({
        matches: [...(s.matches ?? [])].sort(),
        js: [...(s.js ?? [])].map(stripDistPrefix).sort(),
      }))
      .sort((a, b) => {
        // Sort by `matches`, with `js` as a tiebreaker — two entries can
        // legitimately share a `matches` set (e.g. xiaohongshu has both
        // an isolated-world content script and a MAIN-world injector),
        // and we don't want the test to depend on stable-sort behavior.
        const ka = `${a.matches.join("|")}::${a.js.join("|")}`;
        const kb = `${b.matches.join("|")}::${b.js.join("|")}`;
        return ka.localeCompare(kb);
      });

  assert.deepEqual(
    normalize(safari.content_scripts),
    normalize(chrome.content_scripts),
  );
});
