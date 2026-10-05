import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

// 异常报警（LLM / Embedding 请求失败）在 popup 设置页「日志」tab 的结构契约：
// HTML 提供挂载点，popup.js 负责拉取 /diagnostics/alerts、渲染与轮询，
// popup-api.js 导出带 limit 收敛的 fetchDiagnosticsAlerts。

test("settings logging panel mounts the diagnostics alerts section", () => {
  const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

  for (const id of [
    "cfgDiagAlertSummary",
    "cfgRefreshDiagAlerts",
    "cfgDiagAlertsEmpty",
    "cfgDiagAlertList",
  ]) {
    assert.ok(
      popupHtml.includes(`id="${id}"`),
      `popup.html must expose #${id} for the diagnostics alerts feed`,
    );
  }

  // 空态文案与实时区域语义。
  assert.match(popupHtml, /暂无异常报警/);
  assert.match(
    popupHtml,
    /id="cfgDiagAlertSummary"[^>]*aria-live="polite"/,
    "summary must be a polite live region",
  );
  // 列表初始隐藏，等有数据再展开。
  assert.match(popupHtml, /id="cfgDiagAlertList"[^>]*hidden/);

  for (const className of [
    "diag-alerts-section",
    "diag-alerts-head",
    "diag-alerts-refresh",
    "diag-alerts-empty",
    "diag-alert-list",
  ]) {
    assert.ok(
      popupHtml.includes(className),
      `popup.html must style the diagnostics feed with .${className}`,
    );
  }
});

test("popup.js wires polling, rendering and Chinese code labels", () => {
  const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

  // 挂载点全部通过 getElementById 接线。
  for (const id of [
    "cfgDiagAlertList",
    "cfgDiagAlertsEmpty",
    "cfgDiagAlertSummary",
    "cfgRefreshDiagAlerts",
  ]) {
    assert.ok(
      popupJs.includes(`"${id}"`),
      `popup.js must reference #${id}`,
    );
  }

  // 仅当日志面板可见时启动轮询，切走即停止。
  assert.ok(popupJs.includes('startDiagAlertFeed();'));
  assert.match(popupJs, /activePanel === "logging"\) startDiagAlertFeed\(\);/);
  assert.match(popupJs, /else stopDiagAlertFeed\(\);/);
  assert.match(popupJs, /DIAG_ALERT_POLL_MS = 15000/);
  assert.match(popupJs, /if \(document\.hidden\) return;/);

  // 数据面：调用共享的 API 封装并渲染。
  assert.ok(popupJs.includes("fetchDiagnosticsAlerts({ limit: 50 })"));
  assert.ok(popupJs.includes("renderDiagAlerts(payload)"));

  // 错误码中文说明覆盖后端全部 LLM / embedding 错误码。
  for (const code of [
    "rate_limited",
    "auth_failed",
    "timeout",
    "bad_response",
    "provider_error",
    "all_providers_failed",
    "breaker_open",
  ]) {
    assert.ok(
      popupJs.includes(`${code}:`),
      `describeDiagAlertCode must label ${code}`,
    );
  }

  // 渲染走 createElement/replaceChildren（不拼 innerHTML，消息内容不可注入）。
  const renderFn = popupJs.match(/function renderDiagAlerts\(payload\) \{[\s\S]*?\n  \}/);
  assert.notEqual(renderFn, null, "renderDiagAlerts function not found");
  assert.ok(renderFn[0].includes("replaceChildren"));
  assert.ok(!renderFn[0].includes("innerHTML"));

  // 手动刷新按钮存在。
  assert.match(popupJs, /refreshDiagAlertsBtn\.addEventListener\("click"/);
});

test("popup-api clamps the page size before requesting alerts", () => {
  const popupApi = readFileSync(resolve("popup", "popup-api.js"), "utf8");

  assert.match(popupApi, /export async function fetchDiagnosticsAlerts/);
  // 服务端上限 500；非法输入回退默认 50。
  assert.match(popupApi, /Math\.min\(Math\.trunc\(limit\), 500\)/);
  assert.match(popupApi, /\/diagnostics\/alerts\?limit=/);
});
