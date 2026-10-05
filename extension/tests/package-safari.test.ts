import test from "node:test";
import assert from "node:assert/strict";

import { makeSafariArtifactName } from "../scripts/package-safari.mjs";

test("Safari package name normalizes plain and tag-shaped versions", () => {
  assert.equal(
    makeSafariArtifactName("0.3.206", "dmg"),
    "openbiliclaw-extension-v0.3.206-safari.dmg",
  );
  assert.equal(
    makeSafariArtifactName("v0.3.206", "zip"),
    "openbiliclaw-extension-v0.3.206-safari.zip",
  );
  assert.equal(
    makeSafariArtifactName("extension-v0.3.206", "dmg"),
    "openbiliclaw-extension-v0.3.206-safari.dmg",
  );
});
