import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

const popup = readFileSync(resolve("popup", "popup.js"), "utf8");
const start = popup.indexOf("function renderMBTI(");
const end = popup.indexOf("\nfunction ", start + 1);
assert.ok(start >= 0 && end > start);
const render = popup.slice(start, end);

class Element {
  textContent = "";
  className = "";
  children: Element[] = [];
  replaceChildren() { this.children = []; }
  append(child: Element) { this.children.push(child); }
}

for (const mbti of [null, undefined, {}, { type: "", dimensions: {}, confidence: 0 }]) {
  test(`unknown MBTI explains missing evidence (${JSON.stringify(mbti)})`, () => {
    const container = new Element();
    runInNewContext(`${render}\nrenderMBTI(container, mbti);`, {
      HTMLElement: Element,
      document: { createElement: () => new Element() },
      container,
      mbti,
    });
    assert.equal(container.children.length, 1);
    assert.match(container.children[0].textContent, /仅凭观看和收藏无法可靠判断 MBTI/);
    assert.match(container.children[0].textContent, /补充自述或测试结果/);
  });
}
