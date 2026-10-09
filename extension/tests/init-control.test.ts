import assert from "node:assert/strict";
import test from "node:test";

// Publish the shared roster on globalThis BEFORE popup-init-control evaluates,
// so INIT_SOURCE_OPTIONS derives from SourceStatus.SOURCE_KEYS exactly as it
// does in the side panel (this import must stay first — ESM evaluates imports
// in source order). Without it the module falls back to its local key list.
import "../../src/openbiliclaw/web/shared/source-status.js";

import {
  buildInitChecklist,
  describeInitFailure,
  describeInitReason,
  describeInitStatusReason,
  describeInitStartError,
  embeddingPullProgressView,
  embeddingRepairAction,
  embeddingRepairStartAccepted,
  getEnabledPlatforms,
  hardPrereqsSatisfied,
  initProgressView,
  initSelectedSourcesNeedingEnable,
  initSourceLabels,
  INIT_SOURCE_LOGIN_HINT,
  INIT_SOURCE_OPTIONS,
  initStartButtonState,
  isInitTerminal,
  shouldAttachEmbeddingPullProgress,
  shouldAttachRunningInitProgress,
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
      { n: 3, label: "生成并保存完整画像", status: "pending", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null },
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

test("embeddingPullProgressView reports live bge-m3 pull percent + label", () => {
  const idle = embeddingPullProgressView({ embedding_ready: true });
  assert.equal(idle.active, false);
  assert.equal(idle.pct, 0);

  const pulling = embeddingPullProgressView({
    embedding_repair_running: true,
    embedding_repair_completed: 50,
    embedding_repair_total: 200,
    embedding_pull_status: "downloading",
  });
  assert.equal(pulling.active, true);
  assert.equal(pulling.pct, 25);
  assert.ok(pulling.label.includes("downloading"));

  const starting = embeddingPullProgressView({
    embedding_repair_running: true,
    ollama_phase: "starting",
  });
  assert.equal(starting.pct, 1); // no totals yet → 1% floor while active
  assert.ok(starting.label.includes("Ollama"));
});

test("embeddingRepairAction picks the right button per embedding_check", () => {
  assert.deepEqual(embeddingRepairAction({ embedding_ready: true }), {
    repairable: false,
    label: "",
  });
  assert.equal(
    embeddingRepairAction({ embedding_check: "model_missing" }).label,
    "自动下载向量模型",
  );
  assert.equal(
    embeddingRepairAction({ embedding_check: "model_path_encoding" }).label,
    "迁移模型目录并修复",
  );
  assert.equal(embeddingRepairAction({ embedding_check: "disk_full" }).label, "重新检测");
  assert.equal(embeddingRepairAction({ embedding_check: "not_running" }).repairable, false);
  assert.equal(embeddingRepairStartAccepted({ status: 202 }), true);
  assert.equal(
    embeddingRepairStartAccepted({ status: 409, error: "already_running" }),
    true,
  );
  assert.equal(embeddingRepairStartAccepted({ status: 0 }), false);
  assert.equal(embeddingRepairStartAccepted({ status: 404 }), false);
  assert.equal(embeddingRepairStartAccepted({ status: 500 }), false);
});

test("embedding checklist row carries pull progress + repair action", () => {
  const rows = buildInitChecklist(
    statusWith({
      prerequisites: {
        embedding_ready: false,
        embedding_required: true,
        embedding_check: "model_missing",
        embedding_repair_running: true,
        embedding_repair_completed: 10,
        embedding_repair_total: 100,
      },
    }),
  );
  const emb = rows.find((r) => r.key === "embedding");
  assert.equal(emb?.pull.active, true);
  assert.equal(emb?.pull.pct, 10);
  assert.equal(emb?.repair.repairable, true);
});

test("boot re-attach recognizes an embedding pull before guided init starts", () => {
  const pulling = statusWith({
    prerequisites: {
      embedding_required: true,
      embedding_ready: false,
      embedding_check: "repairing",
      embedding_repair_running: true,
      embedding_repair_completed: 12,
      embedding_repair_total: 100,
    },
  });
  assert.equal(shouldAttachEmbeddingPullProgress(pulling), true);
  assert.equal(
    shouldAttachEmbeddingPullProgress(
      statusWith({
        prerequisites: {
          embedding_required: true,
          embedding_ready: false,
          embedding_check: "model_missing",
        },
      }),
    ),
    false,
  );
  assert.equal(shouldAttachEmbeddingPullProgress(statusWith()), false);
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
  assert.ok(platformRow?.label.includes("YouTube"));
  assert.equal(platformRow?.ok, true);
});

test("start button disabled with reason when prereqs missing", () => {
  const btn = initStartButtonState(statusWith());
  assert.equal(btn.enabled, false);
  assert.ok(btn.reason.includes("B 站"));
});

test("start button enabled exactly when can_start is true and idle", () => {
  const btn = initStartButtonState(
    statusWith({
      can_start: true,
      reason: "none",
      prerequisites: { bilibili_logged_in: true, llm_ready: true, embedding_ready: false },
    }),
  );
  assert.equal(btn.enabled, true);
  assert.equal(btn.label, "开始初始化");
});

test("start button gates on selection: empty selection and bilibili-without-login block", () => {
  const noBiliLogin = statusWith({
    can_start: true,
    reason: "none",
    prerequisites: { bilibili_logged_in: false, llm_ready: true, embedding_ready: true },
  });
  // Nothing checked → blocked regardless of can_start.
  const empty = initStartButtonState(noBiliLogin, []);
  assert.equal(empty.enabled, false);
  assert.ok(empty.reason.includes("至少"));
  // Bilibili checked but not logged in → blocked with the B 站 reason.
  const withBili = initStartButtonState(noBiliLogin, ["bilibili", "xiaohongshu"]);
  assert.equal(withBili.enabled, false);
  assert.ok(withBili.reason.includes("B 站"));
  // Bilibili deselected → B 站 login no longer blocks.
  const withoutBili = initStartButtonState(noBiliLogin, ["xiaohongshu"]);
  assert.equal(withoutBili.enabled, true);
  // Legacy callers (no selection passed) keep treating bilibili as required.
  assert.equal(initStartButtonState(noBiliLogin).enabled, false);
});

test("checklist B 站 row is hard only while bilibili is selected", () => {
  const status = statusWith();
  const withBili = buildInitChecklist(status, ["bilibili", "xiaohongshu"]).find(
    (r) => r.key === "bilibili",
  );
  assert.equal(withBili?.hard, true);
  const withoutBili = buildInitChecklist(status, ["xiaohongshu"]).find(
    (r) => r.key === "bilibili",
  );
  assert.equal(withoutBili?.hard, false);
  assert.ok(withoutBili?.label.includes("可跳过"));
  // hardPrereqsSatisfied honours the same selection.
  const llmReady = statusWith({
    prerequisites: { bilibili_logged_in: false, llm_ready: true, embedding_ready: false },
  });
  assert.equal(hardPrereqsSatisfied(llmReady, ["xiaohongshu"]), true);
  assert.equal(hardPrereqsSatisfied(llmReady, ["bilibili", "xiaohongshu"]), false);
});

test("start button reflects running and already-initialized states", () => {
  assert.equal(initStartButtonState(statusWith({ running: true })).enabled, false);
  const done = initStartButtonState(statusWith({ initialized: true, can_start: false }));
  assert.equal(done.enabled, false);
  assert.equal(done.label, "已初始化");
});

test("progress view advances through the sequential full-profile stage", () => {
  const status = statusWith({
    running: true,
    current_stage: 3,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成并保存完整画像", status: "running", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null },
    ],
  });
  const view = initProgressView(status);
  assert.equal(view.active, true);
  assert.equal(view.doneCount, 2);
  assert.ok(view.stageLabel.includes("生成并保存完整画像"));
  // 2 done + nothing invented for the running stage → exactly 50%. The stage
  // has no per-unit progress signal (one LLM call), so the bar reports what is
  // actually finished and goes indeterminate rather than faking a half-step.
  assert.equal(view.pct, 50);
  assert.equal(view.indeterminate, true);
  assert.equal(view.failed, false);
});

test("progress view reports completion and failure terminals", () => {
  const ok = statusWith({
    initialized: true,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成并保存完整画像", status: "ok", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "ok", reason: null },
    ],
  });
  assert.equal(initProgressView(ok).pct, 100);
  assert.equal(isInitTerminal(ok), true);

  const failed = statusWith({
    reason: "llm_not_ready",
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "failed", reason: "llm_not_ready" },
      { n: 3, label: "生成并保存完整画像", status: "pending", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null },
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
  assert.equal(
    describeInitReason("no_profile_signal_sources"),
    "所选来源暂时无法提供画像信号，请检查对应账号、凭据及扩展连接后重试。",
  );
  assert.equal(describeInitReason("totally_unknown"), "");
  const err = Object.assign(new Error("boom"), {
    status: 409,
    details: { error: "already_running" },
  });
  assert.ok(describeInitStartError(err).includes("进行中"));
});

test("an Instagram-only 409 keeps the backend's source-specific guidance", () => {
  const detail =
    "Instagram 公开 topic / creator 发现无需登录，但初始化点赞、收藏和关注记录需要当前浏览器已登录 Instagram 并连接扩展。";
  const rejected = Object.assign(new Error("/api/init request failed: 409"), {
    status: 409,
    details: { error: "no_profile_signal_sources", detail },
  });
  assert.equal(describeInitStartError(rejected), detail);
});

test("a Bangumi-only 409 names all three account tiers", () => {
  // The popup used to refuse this run client-side, which hid the third tier
  // (extension-reported bgm.tv identity) from zero-config users. The guard is
  // gone, so the backend's 409 body is now the user's only feedback and it has
  // to arrive as readable text rather than a silent no-op.
  const rejected = Object.assign(new Error("/api/init request failed: 409"), {
    status: 409,
    details: {
      error: "no_profile_signal_sources",
      detail:
        "只选择 Bangumi 初始化时，需提供个人令牌（推荐，自动识别当前用户）、公开用户名，或先在浏览器登录 bgm.tv 让扩展自动识别。",
    },
  });
  const text = describeInitStartError(rejected);
  assert.equal(text, rejected.details.detail);
  assert.ok(text.includes("个人令牌"));
  assert.ok(text.includes("公开用户名"));
  // The tier that needs no typing at all must be named, otherwise the copy
  // still tells a logged-in bgm.tv user to go fetch a token.
  assert.ok(text.includes("bgm.tv"));
});

test("start-error backend guidance accepts only bounded non-empty text", () => {
  for (const detail of [undefined, null, "  ", {}, [], 42, false]) {
    assert.equal(
      describeInitStartError({ details: { error: "already_running", detail } }),
      "初始化正在进行中。",
    );
  }
  assert.equal(
    describeInitStartError({ details: { error: "already_running", detail: "  请连接扩展。  " } }),
    "请连接扩展。",
  );
  assert.equal(
    describeInitStartError({ details: { detail: "x".repeat(5000) } }),
    "x".repeat(2000),
  );
});

test("guided-init start failures prefer source-specific backend detail", () => {
  const detail =
    "只选择 GitHub 初始化时，请填写公开用户名或可用 PAT；公开 repository 发现仍可匿名使用。";
  const rejected = Object.assign(new Error("/api/init request failed: 409"), {
    status: 409,
    details: { error: "no_profile_signal_sources", detail },
  });

  assert.equal(describeInitStartError(rejected), detail);
  assert.ok(!describeInitStartError(rejected).includes("Bangumi"));
});

test("failure text appends backend detail so internal_error is diagnosable", () => {
  // Mapped reason + stored crash detail → generic copy with specifics appended.
  const crashed = statusWith({
    reason: "internal_error",
    detail: "RuntimeError: provider exploded mid-run",
  });
  const text = describeInitFailure(crashed);
  assert.ok(text.includes("初始化过程中出错了"));
  assert.ok(text.includes("RuntimeError: provider exploded mid-run"));
  // Mapped reason without detail (pre-v0.3.156 backend) → generic copy only.
  assert.equal(
    describeInitFailure(statusWith({ reason: "internal_error", detail: "" })),
    "初始化过程中出错了，请稍后重试。",
  );
  // Unmapped typed reason (empty_signals …) → its human message stands alone.
  assert.equal(
    describeInitFailure(statusWith({ reason: "empty_signals", detail: "没有拉到任何行为信号。" })),
    "没有拉到任何行为信号。",
  );
  // Nothing at all → stage reason, then the generic retry hint.
  assert.equal(
    describeInitFailure(statusWith({ reason: "none" }), { failedReason: "stage-2-broke" }),
    "stage-2-broke",
  );
  assert.equal(describeInitFailure(statusWith({ reason: "none" })), "请稍后重试");
  // interrupted / cancelled now map to human copy instead of raw codes.
  assert.ok(describeInitReason("interrupted").includes("打断"));
  assert.ok(describeInitReason("cancelled").includes("取消"));
});

test("timeout and account-sync details explain cause and recovery without machine codes", () => {
  const timeoutDetail =
    "偏好分析等待 AI 服务超过 6 分钟仍未返回结果。请到模型设置测试 AI 服务后重试初始化。";
  const hardFailure = statusWith({ reason: "analyze_failed", detail: timeoutDetail });
  assert.equal(describeInitFailure(hardFailure), timeoutDetail);
  assert.equal(describeInitStatusReason(hardFailure), timeoutDetail);
  assert.ok(!describeInitFailure(hardFailure).includes("analyze_failed"));

  const accountDetail =
    "画像分析失败：AI 偏好分析等待模型服务超过 6 分钟仍未返回结果，请检查 Base URL。";
  const accountFailure = statusWith({ reason: "llm_not_ready", detail: accountDetail });
  assert.equal(describeInitStatusReason(accountFailure), accountDetail);
  assert.equal(initStartButtonState(accountFailure).reason, accountDetail);

  const partialDetail =
    "画像已生成，但首轮内容池等待超过 10 分钟；系统会在后台继续补池。";
  const partial = statusWith({
    initialized: true,
    partial_success: true,
    reason: "discovery_timeout",
    detail: partialDetail,
  });
  const partialButton = initStartButtonState(partial);
  assert.equal(describeInitStatusReason(partial), partialDetail);
  assert.equal(partialButton.label, "初始化部分完成");
  assert.equal(partialButton.reason, partialDetail);

  const douyinDetail =
    "抖音采集状态 dy_status=degraded：已保留并用于画像建模 57 条已采事件，但至少一个范围未能证明分页完整。";
  const douyinPartial = statusWith({
    initialized: true,
    partial_success: true,
    reason: "douyin_degraded",
    detail: douyinDetail,
  });
  assert.ok(describeInitReason("douyin_degraded").includes("抖音"));
  assert.equal(describeInitStatusReason(douyinPartial), douyinDetail);
  assert.equal(initStartButtonState(douyinPartial).label, "初始化部分完成");
  assert.equal(initStartButtonState(douyinPartial).reason, douyinDetail);
});

// ── Per-run platform source selection ──────────────────────────────────────

test("init source options: bilibili is default-checked but deselectable, others opt-in", () => {
  const bili = INIT_SOURCE_OPTIONS.find((o) => o.key === "bilibili");
  assert.ok(bili && bili.defaultChecked === true);
  assert.ok(!("required" in bili), "bilibili must no longer be marked required");
  const optional = INIT_SOURCE_OPTIONS.filter((o) => !o.defaultChecked).map((o) => o.key);
  assert.deepEqual(optional, [
    "xiaohongshu",
    "douyin",
    "weibo",
    "youtube",
    "twitter",
    "github",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "instagram",
  ]);
  // The login reminder copy mentions logging in on this browser.
  assert.ok(INIT_SOURCE_LOGIN_HINT.includes("登录"));
});

test("init source roster derives from shared capability-aware INIT_SOURCE_KEYS (drift lock)", () => {
  const shared = (globalThis as Record<string, any>).OpenBiliClawSourceStatus;
  assert.ok(shared, "shared source-status module must be loaded for this test");
  // Same keys, same order — the picker is the shared capability projection,
  // not a parallel hardcoded list that can drift when a platform is added.
  assert.deepEqual(
    INIT_SOURCE_OPTIONS.map((o) => o.key),
    [...shared.INIT_SOURCE_KEYS],
  );
  assert.ok(shared.SOURCE_KEYS.includes("weibo"));
  assert.ok(shared.INIT_SOURCE_KEYS.includes("weibo"));
  assert.ok(INIT_SOURCE_OPTIONS.some((o) => o.key === "weibo"));
  // Labels come from the shared module too.
  for (const opt of INIT_SOURCE_OPTIONS) {
    assert.equal(opt.label, shared.sourceLabel(opt.key));
  }
});

test("init source options: X (twitter) is present, opt-in, labelled X", () => {
  const x = INIT_SOURCE_OPTIONS.find((o) => o.key === "twitter");
  assert.ok(x, "twitter option must exist");
  assert.ok(!x?.defaultChecked);
  assert.equal(x?.label, "X");
});

test("init source options: Zhihu is present, opt-in, labelled 知乎", () => {
  const zhihu = INIT_SOURCE_OPTIONS.find((o) => o.key === "zhihu");
  assert.ok(zhihu, "zhihu option must exist");
  assert.ok(!zhihu?.defaultChecked);
  assert.equal(zhihu?.label, "知乎");
});

test("init source options: Instagram is present, opt-in, labelled Instagram", () => {
  const instagram = INIT_SOURCE_OPTIONS.find((option) => option.key === "instagram");
  assert.ok(instagram, "instagram option must exist");
  assert.ok(!instagram?.defaultChecked);
  assert.equal(instagram?.label, "Instagram");
});

test("init source options: Reddit is present, opt-in, labelled Reddit", () => {
  const reddit = INIT_SOURCE_OPTIONS.find((o) => o.key === "reddit");
  assert.ok(reddit, "reddit option must exist");
  assert.ok(!reddit?.defaultChecked);
  assert.equal(reddit?.label, "Reddit");
});

test("init source options: Bangumi is anonymous and opt-in", () => {
  const bangumi = INIT_SOURCE_OPTIONS.find((o) => o.key === "bangumi");
  assert.ok(bangumi, "bangumi option must exist");
  assert.ok(!bangumi?.defaultChecked);
  assert.equal(bangumi?.label, "Bangumi");
  assert.ok(INIT_SOURCE_LOGIN_HINT.includes("无需登录"));
});

test("init source options: GitHub public repositories are anonymous and opt-in", () => {
  const github = INIT_SOURCE_OPTIONS.find((o) => o.key === "github");
  assert.ok(github, "github option must exist");
  assert.ok(!github?.defaultChecked);
  assert.equal(github?.label, "GitHub");
  assert.ok(INIT_SOURCE_LOGIN_HINT.includes("GitHub"));
  assert.ok(describeInitReason("github_bootstrap_not_ready").includes("starred repositories"));
  assert.ok(describeInitReason("github_identity_required").includes("用户名"));
  assert.ok(describeInitReason("github_identity_not_found").includes("没有找到"));
  assert.ok(describeInitReason("invalid_github_access_token").includes("PAT"));
  assert.ok(describeInitReason("github_token_rejected").includes("PAT"));
  assert.ok(describeInitReason("github_identity_mismatch").includes("不一致"));
  assert.ok(describeInitReason("github_bootstrap_timeout").includes("超时"));
  assert.ok(describeInitReason("github_bootstrap_failed").includes("失败"));
  assert.ok(describeInitReason("github_partial").includes("部分"));
});

test("init source options: Linux.do is public, optional-login and opt-in", () => {
  const linuxdo = INIT_SOURCE_OPTIONS.find((o) => o.key === "linuxdo");
  assert.ok(linuxdo, "linuxdo option must exist");
  assert.ok(!linuxdo?.defaultChecked);
  assert.equal(linuxdo?.label, "Linux.do");
});

test("start button allows Reddit as the only profile signal source", () => {
  const state = initStartButtonState(
    statusWith({
      can_start: true,
      reason: "none",
      prerequisites: {
        bilibili_logged_in: true,
        bilibili_check: "ok",
        llm_ready: true,
        embedding_ready: true,
        enabled_platforms: ["reddit"],
      },
    }),
    ["reddit"],
  );

  assert.equal(state.enabled, true);
  assert.equal(state.reason, "");
});

test("initSourceLabels maps known keys and passes unknowns through", () => {
  assert.deepEqual(initSourceLabels(["bilibili", "xiaohongshu", "github", "zhihu", "reddit", "bangumi", "linuxdo", "weibo"]), [
    "B 站",
    "小红书",
    "GitHub",
    "知乎",
    "Reddit",
    "Bangumi",
    "Linux.do",
    "微博",
  ]);
  assert.deepEqual(initSourceLabels(undefined as unknown as string[]), []);
});

test("needs-enable: selected optional sources are guided-init opt-ins", () => {
  const status = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: true,
      enabled_platforms: ["bilibili", "xiaohongshu"],
    },
  });
  // User checked xhs (enabled) + douyin (NOT enabled). The checkbox is now an
  // explicit opt-in, so the UI must not block before POST /api/init.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu", "douyin"], status),
    [],
  );
  // Everything checked is enabled → nothing to flag.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu"], status),
    [],
  );
  // Bilibili follows the same rule: selected means effective for this run.
  const biliDisabled = statusWith({
    prerequisites: { enabled_platforms: [] },
  });
  assert.deepEqual(initSelectedSourcesNeedingEnable(["bilibili"], biliDisabled), []);
});

test("embedding hint prefers backend detail, falls back to check code, then generic", () => {
  const rows = (prereqOverrides: Record<string, unknown>) =>
    buildInitChecklist(
      statusWith({
        prerequisites: {
          bilibili_logged_in: true,
          bilibili_check: "ok",
          llm_ready: true,
          embedding_ready: false,
          enabled_platforms: ["bilibili"],
          ...prereqOverrides,
        },
      }),
    ).find((r) => r.key === "embedding");

  // Backend-provided detail wins verbatim (v0.3.155+ embedding_detail).
  const withDetail = rows({
    embedding_check: "model_broken",
    embedding_detail: "bge-m3 已安装但调用返回 HTTP 500",
  });
  assert.equal(withDetail?.hint, "bge-m3 已安装但调用返回 HTTP 500");

  // No detail → per-code fallback copy.
  const byCode = rows({ embedding_check: "model_missing", embedding_detail: "" });
  assert.ok(byCode?.hint.includes("ollama pull bge-m3"));
  const notRunning = rows({ embedding_check: "not_running", embedding_detail: "" });
  assert.ok(notRunning?.hint.includes("ollama serve"));

  // Older backend (no embedding_check at all) → legacy generic copy.
  const legacy = rows({});
  assert.ok(legacy?.hint.includes("语义检索会弱一些"));

  // Ready → no hint.
  const ready = rows({ embedding_ready: true, embedding_check: "ok" });
  assert.equal(ready?.hint, "");
});

test("embedding row becomes a hard prereq when the backend requires it", () => {
  // v0.3.137+ a configured embedding provider hard-gates can_start server-side;
  // the popup used to hardcode the row soft + "非必须", contradicting the
  // blocked start button (field report 2026-07-05).
  const status = statusWith({
    can_start: false,
    reason: "embedding_not_ready",
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: false,
      embedding_required: true,
      enabled_platforms: ["bilibili"],
    },
  });
  const row = buildInitChecklist(status, ["bilibili"]).find((r) => r.key === "embedding");
  assert.equal(row?.hard, true);
  assert.equal(row?.label, "向量模型可用");
  assert.ok(!row?.hint.includes("也能初始化"));
  assert.equal(hardPrereqsSatisfied(status, ["bilibili"]), false);

  // embedding_not_ready now maps to a real message instead of the generic
  // "以下条件未满足" fallback.
  assert.ok(describeInitReason("embedding_not_ready").includes("向量模型"));

  // Optional (not required) keeps the soft row and legacy label.
  const optional = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: false,
      embedding_required: false,
      enabled_platforms: ["bilibili"],
    },
  });
  const softRow = buildInitChecklist(optional, ["bilibili"]).find((r) => r.key === "embedding");
  assert.equal(softRow?.hard, false);
  assert.equal(softRow?.label, "向量模型可用（推荐，非必须）");
});

// ── init-progress-visibility Phase 2: intra-stage fraction / clamp / staleness ──

import {
  INIT_EXPECTATION_HINT,
  INIT_PROGRESS_STALL_FLOOR_SECONDS,
  INIT_STALL_THRESHOLD_SECONDS,
  resetInitProgressViewState,
  stageDetailText,
  INIT_RUNNING_HINT,
  stalenessView,
} from "../popup/popup-init-control.js";

function runningStage2Status(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return statusWith({
    running: true,
    run_id: (overrides.run_id as string) || "run-frac",
    current_stage: 2,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "running", reason: null },
      { n: 3, label: "生成并保存完整画像", status: "pending", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null },
    ],
    ...overrides,
  });
}

function stage2With(progress: unknown, eta: number | null, runId: string) {
  return runningStage2Status({
    run_id: runId,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null, eta_seconds: 90 },
      { n: 2, label: "分析偏好", status: "running", reason: null, progress, eta_seconds: eta },
      { n: 3, label: "生成并保存完整画像", status: "pending", reason: null, eta_seconds: 70 },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null, eta_seconds: 300 },
    ],
  });
}

test("pct advances per completed chunk when stage progress is present", () => {
  resetInitProgressViewState();
  const t = 1_000_000;
  const pcts = [0, 2, 4, 8].map(
    (done) =>
      initProgressView(stage2With({ done, total: 8, note: `第 ${done}/8 批` }, 180, "run-chunks"), t)
        .pct,
  );
  // done/total fraction: 0/8→25, 2/8→31, 4/8→38, 8/8 capped 0.95→49.
  assert.deepEqual(pcts, [25, 31, 38, 49]);
});

test("running stage label appends the sub-progress note", () => {
  resetInitProgressViewState();
  const view = initProgressView(
    stage2With({ done: 3, total: 8, note: "第 3/8 批" }, 180, "run-note"),
    5_000,
  );
  assert.equal(view.stageLabel, "2/4 分析偏好 · 第 3/8 批");
});

test("legacy status without new fields no longer fakes a half-step", () => {
  resetInitProgressViewState();
  // No run_id, no progress → the stage contributes nothing (25%) instead of
  // the old flat 0.5 half-step, which implied progress it had not made.
  const legacy = statusWith({
    running: true,
    current_stage: 2,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "running", reason: null },
      { n: 3, label: "生成并保存完整画像", status: "pending", reason: null },
      { n: 4, label: "生成首轮可用推荐", status: "pending", reason: null },
    ],
  });
  assert.equal(initProgressView(legacy).pct, 25);
});

test("pct is monotonic per run_id even when statuses regress out of order", () => {
  resetInitProgressViewState();
  const t = 3_000_000;
  const high = initProgressView(
    stage2With({ done: 6, total: 8, note: null }, 180, "run-clamp"),
    t,
  ).pct;
  assert.ok(high >= 43);
  // A stale poll result arrives late with less progress → clamp holds.
  const regressed = initProgressView(
    stage2With({ done: 1, total: 8, note: null }, 180, "run-clamp"),
    t + 1000,
  ).pct;
  assert.equal(regressed, high);
  // A different run starts fresh (no clamp bleed across runs).
  resetInitProgressViewState();
  const fresh = initProgressView(
    stage2With({ done: 1, total: 8, note: null }, 180, "run-clamp-2"),
    t + 2000,
  ).pct;
  assert.ok(fresh < high);
});

test("20-step simulated status sequence is non-decreasing and ends at 100", () => {
  resetInitProgressViewState();
  const runId = "run-sim";
  const t0 = 9_000_000;
  const mk = (stages: unknown[], extra: Record<string, unknown> = {}) =>
    statusWith({ running: true, run_id: runId, stages, ...extra });
  const S = (n: number, label: string, status: string, progress: unknown = null, eta: number | null = null) => {
    const s: Record<string, unknown> = { n, label, status, reason: null };
    if (progress) s.progress = progress;
    if (eta !== null) s.eta_seconds = eta;
    return s;
  };
  const L = ["拉取数据", "分析偏好", "生成并保存完整画像", "生成首轮可用推荐"];
  const seq: Array<Record<string, unknown>> = [
    // stage 1 running, per-source progress
    mk([S(1, L[0], "running", { done: 0, total: 2, note: "正在采集 B 站" }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    mk([S(1, L[0], "running", { done: 1, total: 2, note: "正在采集 Reddit" }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    // stale out-of-order frame (regressed to done 0)
    mk([S(1, L[0], "running", { done: 0, total: 2 }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    // stage 1 done, stage 2 running with chunk progress 0..8 (one frame missing fields)
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 0, total: 8, note: "第 0/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 1, total: 8, note: "第 1/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }), // legacy-shaped frame, no new fields
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 3, total: 8, note: "第 3/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 2, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }), // out-of-order regress
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 5, total: 8, note: "第 5/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 6, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 7, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 8, total: 8, note: "第 8/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    // stages 3+4 run in parallel (eta pseudo progress, then one gets progress)
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    // profile lands, discovery still running
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    // terminal frame
    statusWith({
      running: false,
      initialized: true,
      run_id: runId,
      current_stage: 4,
      stages: [S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "ok")],
    }),
  ];
  assert.equal(seq.length, 20);
  let prev = -1;
  seq.forEach((status, i) => {
    const pct = initProgressView(status, t0 + i * 10_000).pct;
    assert.ok(pct >= prev, `step ${i}: pct ${pct} regressed below ${prev}`);
    prev = pct;
  });
  assert.equal(prev, 100);
});

test("stalenessView distinguishes a live backend from stalled substantive work", () => {
  resetInitProgressViewState();
  const t0 = 5_000_000;
  const status = (heartbeat: string, sequence: number, progressSequence = 4) =>
    runningStage2Status({
      run_id: "run-stale",
      last_activity: heartbeat,
      last_heartbeat_at: heartbeat,
      last_progress_at: "2026-07-10 08:00:00",
      progress_sequence: progressSequence,
      sequence,
    });
  const fresh = stalenessView(status("2026-07-10 08:00:00", 7), t0);
  assert.equal(fresh.fresh, true);
  assert.ok(fresh.text.includes("后端在线"));
  // A slow-but-healthy work unit (91s — past the heartbeat threshold) must NOT
  // read as stalled: only the connection check uses the 90s beat window.
  const slowButHealthy = stalenessView(status("2026-07-10 08:01:31", 10), t0 + 91_000);
  assert.equal(slowButHealthy.fresh, true);
  assert.ok(slowButHealthy.staleSeconds > INIT_STALL_THRESHOLD_SECONDS);
  // Past the work-unit floor with no milestone: connected yet genuinely stuck.
  const stalled = stalenessView(
    status("2026-07-10 08:05:10", 17),
    t0 + (INIT_PROGRESS_STALL_FLOOR_SECONDS + 10) * 1000,
  );
  assert.equal(stalled.fresh, false);
  assert.ok(stalled.staleSeconds > INIT_PROGRESS_STALL_FLOOR_SECONDS);
  assert.ok(stalled.text.includes("后端在线"));
  assert.ok(stalled.text.includes("取消"));
  // A substantive milestone advances independently and clears the warning.
  const revived = stalenessView(
    status("2026-07-10 08:05:15", 18, 18),
    t0 + (INIT_PROGRESS_STALL_FLOOR_SECONDS + 15) * 1000,
  );
  assert.equal(revived.fresh, true);
});

test("progress-stall threshold adapts to the pace this run demonstrates", () => {
  resetInitProgressViewState();
  const t0 = 9_000_000;
  // The heartbeat keeps advancing throughout (the connection is fine); only
  // the work-unit marker stalls, which is what the adaptive threshold judges.
  let beat = 0;
  const status = (progressSequence: number, progressAt: string) =>
    runningStage2Status({
      run_id: "run-slow-model",
      last_activity: `2026-07-10 09:00:${String((beat += 1) % 60).padStart(2, "0")}`,
      last_heartbeat_at: `2026-07-10 09:00:${String(beat % 60).padStart(2, "0")}`,
      last_progress_at: progressAt,
      progress_sequence: progressSequence,
      sequence: 100 + beat,
    });
  // Two work units, each ~280s: slower than the floor, but a steady pace.
  stalenessView(status(1, "2026-07-10 09:00:00"), t0);
  stalenessView(status(2, "2026-07-10 09:04:40"), t0 + 280_000);
  stalenessView(status(3, "2026-07-10 09:09:20"), t0 + 560_000);
  // 330s into the next unit: past the 300s floor, but well within 1.5× the
  // 280s pace this run has shown → still healthy, no alarm.
  const withinPace = stalenessView(status(3, "2026-07-10 09:09:20"), t0 + 890_000);
  assert.equal(withinPace.fresh, true);
  // 450s: beyond 1.5 × 280s → now genuinely slower than its own rhythm.
  const beyondPace = stalenessView(status(3, "2026-07-10 09:09:20"), t0 + 1_010_000);
  assert.equal(beyondPace.fresh, false);
  assert.ok(beyondPace.text.includes("比本轮此前的节奏慢"));
});

test("stalenessView reports a missing backend heartbeat separately", () => {
  resetInitProgressViewState();
  const status = runningStage2Status({
    run_id: "run-heartbeat-stale",
    last_activity: "2026-07-10 08:00:00",
    last_heartbeat_at: "2026-07-10 08:00:00",
    last_progress_at: "2026-07-10 08:00:00",
    progress_sequence: 3,
    sequence: 3,
  });
  stalenessView(status, 7_000_000);
  const stalled = stalenessView(status, 7_091_000);
  assert.equal(stalled.fresh, false);
  assert.ok(stalled.text.includes("没有心跳"));
  assert.ok(stalled.text.includes("连接可能中断"));
});

test("indeterminate stage progress exposes mode without inventing item percentage", () => {
  resetInitProgressViewState();
  const status = runningStage2Status({
    run_id: "run-indeterminate",
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      {
        n: 2,
        label: "分析偏好",
        status: "running",
        reason: null,
        eta_seconds: 180,
        progress: {
          done: 0,
          total: 0,
          mode: "indeterminate",
          elapsed_seconds: 40,
          max_seconds: 360,
          note: "AI 正在处理",
        },
      },
    ],
  });
  const view = initProgressView(status, 8_000_000);
  assert.equal(view.indeterminate, true);
  assert.ok(view.stageLabel.includes("AI 正在处理"));
});

test("stalenessView stays fresh on old backends without last_activity", () => {
  resetInitProgressViewState();
  const status = runningStage2Status({ run_id: "run-old" });
  delete (status as Record<string, unknown>).last_activity;
  const t0 = 6_000_000;
  stalenessView(status, t0);
  const later = stalenessView(status, t0 + 600_000);
  assert.equal(later.fresh, true); // no heartbeat signal → never claim a stall
});

test("stalenessView is inert for non-running statuses", () => {
  resetInitProgressViewState();
  const idle = stalenessView(statusWith(), 1_000);
  assert.equal(idle.fresh, true);
  assert.equal(idle.text, "");
});

test("stageDetailText reports observed facts and never a forecast", () => {
  // Elapsed alone for a stage with no sub-progress (stage 3, one LLM call).
  assert.equal(stageDetailText({ progress: { elapsed_seconds: 360 } }), "已用时 6 分钟");
  // Elapsed + real counts when the backend publishes them.
  assert.equal(
    stageDetailText({ progress: { elapsed_seconds: 720, done: 3, total: 6 } }),
    "已用时 12 分钟 · 已完成 3/6",
  );
  // Sub-minute waits do not round to a misleading "0 分钟".
  assert.equal(stageDetailText({ progress: { elapsed_seconds: 20 } }), "已用时不到 1 分钟");
  // done is clamped to total; a stage with no progress payload says nothing.
  assert.equal(
    stageDetailText({ progress: { done: 9, total: 4 } }),
    "已完成 4/4",
  );
  assert.equal(stageDetailText({}), "");
  assert.equal(stageDetailText(null), "");
  assert.ok(INIT_EXPECTATION_HINT.includes("严格按顺序生成"));
  assert.ok(INIT_EXPECTATION_HINT.includes("不预估时间"));
  assert.ok(INIT_EXPECTATION_HINT.includes("进度会保留"));
  assert.ok(INIT_RUNNING_HINT.includes("只要还在出结果就不会被打断"));
});

test("progress bar is driven only by real counts, never by a hidden eta", () => {
  resetInitProgressViewState();
  const base = {
    running: true,
    run_id: "run-eta-free",
    total_stages: 4,
    current_stage: 2,
  };
  // Stage 2 running with NO sub-progress: contributes nothing to the bar and
  // renders indeterminate rather than a faked elapsed-based fill.
  const blind = initProgressView({
    ...base,
    stages: [
      { n: 1, label: "拉取数据", status: "ok" },
      { n: 2, label: "分析偏好", status: "running" },
      { n: 3, label: "生成并保存完整画像", status: "pending" },
      { n: 4, label: "生成首轮可用推荐", status: "pending" },
    ],
  });
  assert.equal(blind.indeterminate, true);
  assert.equal(blind.pct, 25);

  // Real counts move it; the monotonic clamp still holds afterwards.
  const real = initProgressView({
    ...base,
    stages: [
      { n: 1, label: "拉取数据", status: "ok" },
      {
        n: 2,
        label: "分析偏好",
        status: "running",
        progress: { done: 3, total: 6, mode: "determinate" },
      },
      { n: 3, label: "生成并保存完整画像", status: "pending" },
      { n: 4, label: "生成首轮可用推荐", status: "pending" },
    ],
  });
  assert.equal(real.indeterminate, false);
  assert.equal(real.pct, 38);
  assert.equal(real.stageDetailText, "已完成 3/6");
  assert.equal(initProgressView({ ...base, stages: blind.stages || [] }).pct >= 38, true);
});

test("shouldAttachRunningInitProgress: boot re-attach only when a run is live", () => {
  // A run in flight (popup opened / refreshed mid-init, started elsewhere so no
  // click or SSE kicked the poll here) → re-attach the progress poll.
  assert.equal(shouldAttachRunningInitProgress(statusWith({ running: true })), true);
  // Idle / not-yet-started → leave the idle panel, no poll.
  assert.equal(shouldAttachRunningInitProgress(statusWith({ running: false })), false);
  // Missing/legacy status must never throw or falsely attach.
  assert.equal(shouldAttachRunningInitProgress(null), false);
  assert.equal(shouldAttachRunningInitProgress(undefined), false);
  assert.equal(shouldAttachRunningInitProgress({}), false);
});
