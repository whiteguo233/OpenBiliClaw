import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInitChecklist,
  describeInitReason,
  describeInitStartError,
  getEnabledPlatforms,
  hardPrereqsSatisfied,
  initProgressView,
  initSelectedSourcesNeedingEnable,
  initSourceLabels,
  INIT_SOURCE_LOGIN_HINT,
  INIT_SOURCE_OPTIONS,
  initStartButtonState,
  isInitTerminal,
} from "../popup/popup-init-control.js";

function statusWith(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    initialized: false,
    running: false,
    current_stage: 0,
    total_stages: 4,
    stages: [
      { n: 1, label: "拉取数据", status: "pending", reason: null },
      { n: 2, label: "分析偏好", status: "pending", reason: null },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
    partial_success: false,
    can_start: false,
    can_manage: true,
    prerequisites: {
      bilibili_logged_in: false,
      bilibili_check: "failed",
      llm_ready: false,
      embedding_ready: false,
      enabled_platforms: [],
    },
    reason: "bilibili_not_logged_in",
    detail: "",
    ...overrides,
  };
}

test("checklist marks hard prereqs and surfaces hints when missing", () => {
  const rows = buildInitChecklist(statusWith());
  const bili = rows.find((r) => r.key === "bilibili");
  const llm = rows.find((r) => r.key === "llm");
  assert.equal(bili?.hard, true);
  assert.equal(bili?.ok, false);
  assert.ok(bili?.hint.length > 0);
  assert.equal(llm?.hard, true);
  assert.equal(llm?.ok, false);
});

test("hardPrereqsSatisfied is false until both bilibili and llm are ready", () => {
  assert.equal(hardPrereqsSatisfied(statusWith()), false);
  assert.equal(
    hardPrereqsSatisfied(
      statusWith({ prerequisites: { bilibili_logged_in: true, llm_ready: false } }),
    ),
    false,
  );
  assert.equal(
    hardPrereqsSatisfied(
      statusWith({
        prerequisites: { bilibili_logged_in: true, llm_ready: true, embedding_ready: false },
      }),
    ),
    true,
  );
});

test("enabled platforms surface in the checklist label", () => {
  const status = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      llm_ready: true,
      embedding_ready: true,
      enabled_platforms: ["bilibili", "youtube"],
    },
  });
  assert.deepEqual(getEnabledPlatforms(status), ["bilibili", "youtube"]);
  const platformRow = buildInitChecklist(status).find((r) => r.key === "platforms");
  assert.ok(platformRow?.label.includes("youtube"));
  assert.equal(platformRow?.ok, true);
});

test("start button disabled with reason when prereqs missing", () => {
  const btn = initStartButtonState(statusWith());
  assert.equal(btn.enabled, false);
  assert.ok(btn.reason.includes("B 站"));
});

test("start button enabled exactly when can_start is true and idle", () => {
  const btn = initStartButtonState(statusWith({ can_start: true, reason: "none" }));
  assert.equal(btn.enabled, true);
  assert.equal(btn.label, "开始初始化");
});

test("start button reflects running and already-initialized states", () => {
  assert.equal(initStartButtonState(statusWith({ running: true })).enabled, false);
  const done = initStartButtonState(statusWith({ initialized: true, can_start: false }));
  assert.equal(done.enabled, false);
  assert.equal(done.label, "已初始化");
});

test("progress view advances mid-stage and reports parallel stage 3/4", () => {
  const status = statusWith({
    running: true,
    current_stage: 3,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成画像", status: "running", reason: null },
      { n: 4, label: "发现内容池", status: "running", reason: null },
    ],
  });
  const view = initProgressView(status);
  assert.equal(view.active, true);
  assert.equal(view.doneCount, 2);
  assert.ok(view.stageLabel.includes("生成画像"));
  // 2 done + 0.5 in-flight over 4 → ~63%.
  assert.ok(view.pct > 50 && view.pct < 75);
  assert.equal(view.failed, false);
});

test("progress view reports completion and failure terminals", () => {
  const ok = statusWith({
    initialized: true,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成画像", status: "ok", reason: null },
      { n: 4, label: "发现内容池", status: "ok", reason: null },
    ],
  });
  assert.equal(initProgressView(ok).pct, 100);
  assert.equal(isInitTerminal(ok), true);

  const failed = statusWith({
    reason: "llm_not_ready",
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "failed", reason: "llm_not_ready" },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
  });
  assert.equal(initProgressView(failed).failed, true);
  assert.equal(isInitTerminal(failed), true);
});

test("idle status is not terminal", () => {
  assert.equal(isInitTerminal(statusWith()), false);
  assert.equal(isInitTerminal(null), false);
});

test("reason + start-error text mapping", () => {
  assert.ok(describeInitReason("bilibili_not_logged_in").includes("B 站"));
  assert.equal(describeInitReason("none"), "");
  assert.equal(describeInitReason("totally_unknown"), "");
  const err = Object.assign(new Error("boom"), {
    status: 409,
    details: { error: "already_running" },
  });
  assert.ok(describeInitStartError(err).includes("进行中"));
});

// ── Per-run platform source selection ──────────────────────────────────────

test("init source options: bilibili is the required base, others opt-in", () => {
  const bili = INIT_SOURCE_OPTIONS.find((o) => o.key === "bilibili");
  assert.ok(bili && bili.required === true);
  const optional = INIT_SOURCE_OPTIONS.filter((o) => !o.required).map((o) => o.key);
  assert.deepEqual(optional, ["xiaohongshu", "douyin", "youtube", "twitter"]);
  // The login reminder copy mentions logging in on this browser.
  assert.ok(INIT_SOURCE_LOGIN_HINT.includes("登录"));
});

test("init source options: X (twitter) is present, opt-in, labelled X", () => {
  const x = INIT_SOURCE_OPTIONS.find((o) => o.key === "twitter");
  assert.ok(x, "twitter option must exist");
  assert.equal(x?.required, false);
  assert.equal(x?.label, "X");
});

test("initSourceLabels maps known keys and passes unknowns through", () => {
  assert.deepEqual(initSourceLabels(["bilibili", "xiaohongshu", "weibo"]), [
    "B 站",
    "小红书",
    "weibo",
  ]);
  assert.deepEqual(initSourceLabels(undefined as unknown as string[]), []);
});

test("needs-enable: flags checked optional sources missing from config", () => {
  const status = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: true,
      enabled_platforms: ["bilibili", "xiaohongshu"],
    },
  });
  // User checked xhs (enabled) + douyin (NOT enabled) → only douyin flagged.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu", "douyin"], status),
    ["douyin"],
  );
  // Everything checked is enabled → nothing to flag.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu"], status),
    [],
  );
  // Bilibili is never flagged even if absent from enabled_platforms (it's base).
  const biliOnly = statusWith({
    prerequisites: { enabled_platforms: [] },
  });
  assert.deepEqual(initSelectedSourcesNeedingEnable(["bilibili"], biliOnly), []);
});
