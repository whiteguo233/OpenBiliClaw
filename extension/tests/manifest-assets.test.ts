import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

test("manifest icon assets exist", () => {
  const root = process.cwd();
  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8")) as {
    icons?: Record<string, string>;
    action?: { default_icon?: Record<string, string> };
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
