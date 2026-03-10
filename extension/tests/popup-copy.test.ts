import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("popup copy uses a more native bilibili-style voice in key entry points", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  assert.match(popupHtml, /首页先放一边，这里是你最近更可能点开的。/);
  assert.match(popupHtml, /这几条，你大概会点开/);
  assert.match(popupHtml, /阿B 最近新记住了什么/);
  assert.match(popupHtml, /最近你到底在看啥/);
  assert.match(popupHtml, /写点你最近爱看的/);
  assert.doesNotMatch(popupHtml, /对个暗号|来，唠一句/);
});
