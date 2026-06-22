import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const kernelSource = readFileSync(
  new URL("../src/content/kernel.ts", import.meta.url),
  "utf8",
);

test("collector observes clicks in capture phase so stopped platform events are still captured", () => {
  assert.match(
    kernelSource,
    /document\.addEventListener\("click",\s*\(event\) => \{[\s\S]*?\},\s*\{\s*capture:\s*true\s*\}\s*\);/,
  );
});
