import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const desktop = readFileSync(new URL("../../src/openbiliclaw/web/desktop/assets/js/app.js", import.meta.url), "utf8");
function fn(name) {
  const start = desktop.indexOf(`    function ${name}(`);
  assert.ok(start >= 0);
  return desktop.slice(start, desktop.indexOf("\n    }\n", start) + 6);
}

test("existing diagnostics feed renders recovery separately and links the precise source", () => {
  let handler;
  const button = { dataset: { diagnosticSource: "custom/a" }, addEventListener(_, callback) { handler = callback; } };
  const list = { innerHTML: "", hidden: true, querySelectorAll: () => [button] };
  const summary = {}, empty = {}, opened = [];
  const context = vm.createContext({ Date, Number, String,
    $: (selector) => selector === "#diagnosticsAlertList" ? list : selector === "#diagnosticsAlertsSummary" ? summary : empty,
    escapeHtml: (value) => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    openSourceSettings: (id) => opened.push(id),
  });
  for (const name of ["describeDiagnosticsAlertCode", "formatDiagnosticsAlertTime", "renderDiagnosticsAlerts"]) vm.runInContext(fn(name), context);
  context.renderDiagnosticsAlerts({ alerts: [{ category: "discovery", code: "source_recovered", severity: "info", source_id: "custom/a", source_name: "<Feed>", message: "已恢复", last_seen: 100 }], summary: { recovered: 1 } });
  assert.match(list.innerHTML, /data-severity="info"/);
  assert.match(list.innerHTML, /已恢复/);
  assert.match(list.innerHTML, /&lt;Feed>/);
  assert.match(summary.textContent, /1 恢复/);
  handler();
  assert.deepEqual(opened, ["custom/a"]);
  const previous = list.innerHTML;
  context.renderDiagnosticsAlerts({ alerts: [], source_history_available: false });
  assert.equal(list.innerHTML, previous);
  assert.match(summary.textContent, /暂时无法刷新/);
});
