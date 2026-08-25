import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// The shared module is a classic script (it has to be: the side panel loads it
// through a <script> tag because MV3's CSP forbids importing it from the
// backend). Importing it for its side effect is enough — it publishes itself on
// globalThis, the same way the side panel, the desktop page and the setup
// wizard all consume it.
await import("../../src/openbiliclaw/web/shared/source-status.js");

const SourceStatus = (globalThis as Record<string, any>).OpenBiliClawSourceStatus;

test("the shared module publishes itself for classic-script consumers", () => {
  assert.ok(SourceStatus, "source-status.js did not define OpenBiliClawSourceStatus");
  assert.deepEqual([...SourceStatus.SOURCE_KEYS], [
    "bilibili", "xiaohongshu", "douyin", "weibo", "youtube", "twitter", "zhihu", "reddit",
    "bangumi", "linuxdo", "v2ex", "instagram",
  ]);
});

// Bangumi is the one source the backend sends `auth: null` for. Pinning the
// fallback here because the honest-looking alternative is the dangerous one: a
// default-constructed contract reads auth_required=true + credential="none",
// which renders as「需要登录」for a source that works anonymously off the public
// API. Under-reporting is fine; inventing a verdict is not (invariant I3).
test("a source with no auth contract falls back to its legacy state", () => {
  const view = SourceStatus.describeAccess({
    state: "ready", detail: "Bangumi 使用官方公开 API，无需登录。", enabled: true, auth: null,
  });

  assert.equal(view.present, true);
  assert.equal(view.known, true);
  assert.equal(view.contract, false, "must not claim a contract it was not sent");
  assert.equal(view.label, "凭据已就绪");
  // No contract means no basis for rating the evidence, so no badge is shown.
  assert.equal(view.evidence.text, "");
});

// A switched-off source still reports its state through the legacy field, and
// the shared table has to name it — before this it fell through to「状态未知」.
test("a disabled source says so rather than reading as unknown", () => {
  const view = SourceStatus.describeAccess({ state: "disabled", enabled: false, auth: null });

  assert.equal(view.label, "来源未启用");
  assert.equal(view.tone, "muted");
});

// The desktop page and the side panel each carried their own copy of this rule
// before, which is the duplication that let the two status tables drift (D6).
test("a rejected personal token outranks the run-derived verdict", () => {
  const view = SourceStatus.describeAccess({
    state: "ready", enabled: true, auth: null, token_state: "rejected",
  });

  assert.equal(view.label, "令牌已失效");
  assert.equal(view.tone, "danger");
});

// spec D6, reachable drift #1. Both states are really emitted — YouTube reports
// no_auth, 小红书/抖音/Reddit report unverified — and the side panel painted
// both of them #9aa0a6, so "needs no login" and "no idea" were the same pixel.
test("no_auth and unverified are visually distinguishable", () => {
  const noAuth = SourceStatus.describeAccess({ state: "no_auth", enabled: true });
  const unverified = SourceStatus.describeAccess({ state: "unverified", enabled: true });

  assert.notEqual(noAuth.tone, unverified.tone);
  assert.notEqual(noAuth.color, unverified.color);
  assert.equal(noAuth.label, "无需登录");
  assert.equal(unverified.label, "状态待验证");
});

// spec D6, reachable drift #2. The desktop page said "状态未知"; the side panel
// said nothing at all, leaving a coloured dot with no explanation next to it.
test("an unrecognised state always says so", () => {
  const view = SourceStatus.describeAccess({ state: "not_a_real_state", enabled: true });

  assert.equal(view.label, "状态未知");
  assert.equal(view.known, false);
  assert.ok(view.line.includes("状态未知"));
  assert.equal(view.color, SourceStatus.TONE_COLORS.muted);
});

// ── the orthogonal contract ────────────────────────────────────────────────
//
// The review finding this suite exists to pin: describeAccess used to read
// `item.state` and nothing else, so the entire contract was invisible. The two
// rows below are the exact pair from the spec's Goal — B站 and Reddit both
// legitimately verified, on evidence of completely different strength — which
// used to render as one identical green「凭据已就绪」.
const BILI_LIVE = {
  state: "ready",
  enabled: true,
  detail: "B站 Cookie 已通过 nav 接口验证。",
  auth: {
    auth_required: true,
    credential: "present",
    credential_origin: "data_file",
    verification: "verified",
    verify_method: "live_probe",
    verified_at: "2026-07-18T09:57:00+00:00",
    verify_ttl_seconds: 60,
  },
};
const REDDIT_FILE = {
  state: "ready",
  enabled: true,
  detail: "Reddit 凭据文件存在且未超过 7 天。",
  auth: {
    auth_required: true,
    credential: "present",
    credential_origin: "external_cli",
    verification: "verified",
    verify_method: "local_file",
    verified_at: "2026-07-16T10:00:00+00:00",
    verify_ttl_seconds: 604800,
  },
};
// 2026-07-18T10:00:00Z — three minutes after the B站 probe, two days after the
// Reddit file was written.
const NOW = Date.parse("2026-07-18T10:00:00Z");

test("the same verdict on different evidence does not render the same", () => {
  const bili = SourceStatus.describeAccess(BILI_LIVE, { now: NOW });
  const reddit = SourceStatus.describeAccess(REDDIT_FILE, { now: NOW });

  // Both are honestly verified — the contract does not demote Reddit.
  assert.equal(bili.label, reddit.label);
  assert.equal(bili.tone, reddit.tone);

  // ...but the evidence behind them must be tellable apart, and not by colour:
  // a different glyph, a different noun, a different rank attribute.
  assert.notEqual(bili.evidence.symbol, reddit.evidence.symbol);
  assert.notEqual(bili.evidence.label, reddit.evidence.label);
  assert.notEqual(bili.evidence.rank, reddit.evidence.rank);
  assert.equal(bili.evidence.rank, "direct");
  assert.equal(reddit.evidence.rank, "indirect");
  assert.equal(bili.evidence.text, "◆ 联网验证 · 3 分钟前");
  assert.equal(reddit.evidence.text, "◇ 本地文件 · 2 天前");
  // The single-line surface folds it in, so the side panel shows it too.
  assert.notEqual(bili.line, reddit.line);
  assert.ok(bili.line.includes("◆ 联网验证"));
  assert.ok(reddit.line.includes("◇ 本地文件"));
});

test("evidence strength survives greyscale and a screen reader", () => {
  for (const item of [BILI_LIVE, REDDIT_FILE]) {
    const view = SourceStatus.describeAccess(item, { now: NOW });
    // Nothing about the distinction is carried by colour alone: the rank is an
    // attribute, the glyph is a shape, and the method is spelled out in words.
    assert.ok(view.evidence.label.length > 0);
    assert.ok(view.evidence.symbol.length > 0);
    assert.ok(view.evidence.hint.length > 0);
  }
  const bili = SourceStatus.describeAccess(BILI_LIVE, { now: NOW });
  const reddit = SourceStatus.describeAccess(REDDIT_FILE, { now: NOW });
  assert.notEqual(bili.evidence.hint, reddit.evidence.hint);
  // Same tone, so a colour-only encoding would have said nothing at all.
  assert.equal(bili.color, reddit.color);
});

test("a passive verdict counts as network evidence, a heartbeat does not", () => {
  const x = SourceStatus.describeAccess({
    state: "ok",
    enabled: true,
    auth: { credential: "present", verification: "verified", verify_method: "passive_health" },
  }, { now: NOW });
  const xhs = SourceStatus.describeAccess({
    state: "ready",
    enabled: true,
    auth: { credential: "present", verification: "verified", verify_method: "browser_heartbeat" },
  }, { now: NOW });

  assert.equal(x.evidence.rank, "direct");
  assert.equal(xhs.evidence.rank, "indirect");
  assert.notEqual(x.evidence.label, xhs.evidence.label);
});

test("auth_required=false is its own tier, not a shade of verified", () => {
  const youtube = SourceStatus.describeAccess({
    state: "no_auth",
    enabled: true,
    detail: "YouTube 无需登录。",
    auth: {
      auth_required: false,
      credential: "none",
      verification: "unverified",
      verify_method: "none",
    },
  }, { now: NOW });
  const verified = SourceStatus.describeAccess(BILI_LIVE, { now: NOW });
  const pending = SourceStatus.describeAccess({
    state: "unverified",
    enabled: true,
    auth: { credential: "present", verification: "unverified", verify_method: "live_probe" },
  }, { now: NOW });

  assert.equal(youtube.label, "无需登录");
  assert.equal(youtube.tone, "public");
  // Distinct from both neighbours it used to be confused with.
  assert.notEqual(youtube.tone, verified.tone);
  assert.notEqual(youtube.tone, pending.tone);
  // A source that needs no credential has no evidence to rate — rendering an
  // empty strength badge would read as a missing value rather than as "n/a".
  assert.equal(youtube.evidence.text, "");
  assert.equal(youtube.evidence.rank, "none");
  // `credential: "none"` must not drag it into 需要登录: auth_required wins.
  assert.notEqual(youtube.label, "需要登录");
});

test("optional browser login never hides Linux.do public discovery", () => {
  const detail =
    "Linux.do 公开发现可用；浏览器当前未登录，个人信号同步暂不可用。";
  const loggedOut = {
    state: "ready",
    enabled: true,
    detail,
    auth: {
      auth_required: false,
      credential: "none",
      verification: "failed",
      verify_method: "browser_heartbeat",
      verified_at: "2026-07-18T09:57:00+00:00",
    },
  };
  const access = SourceStatus.describeAccess(loggedOut, { now: NOW });

  assert.equal(SourceStatus.sourceLabel("linuxdo"), "Linux.do");
  assert.equal(access.label, "公开发现可用");
  assert.equal(access.tone, "public");
  assert.equal(access.detail, detail);
  assert.ok(access.evidence.text.includes("插件心跳"));
  assert.equal(SourceStatus.describeSourceIssue(loggedOut), null);

  const credential = SourceStatus.describeCredential({
    available: false,
    detail: "连接已登录插件后可同步个人收藏、点赞和阅读记录。",
    form: { kind: "extension_only", label: "Linux.do 登录态（可选）" },
  });
  assert.equal(credential.form.writable, false);
});

test("a capability is not a result: unverified says so, whatever the method", () => {
  const view = SourceStatus.describeAccess({
    state: "unverified",
    enabled: true,
    auth: {
      credential: "present",
      verification: "unverified",
      verify_method: "live_probe",
      verified_at: "",
    },
  }, { now: NOW });

  assert.equal(view.label, "待验证");
  assert.equal(view.tone, "pending");
  // The noun names what *can* be done; the suffix admits it has not been.
  assert.equal(view.evidence.text, "◆ 联网验证 · 尚未验证");
});

test("no verification capability is stated, not hidden", () => {
  const view = SourceStatus.describeAccess({
    state: "unverified",
    enabled: true,
    auth: { credential: "present", verification: "unverified", verify_method: "none" },
  }, { now: NOW });

  assert.equal(view.evidence.rank, "unable");
  assert.equal(view.evidence.text, "— 无验证能力");
  // No age to report, so none is invented.
  assert.equal(view.evidence.freshness, "");
});

test("an unrecognised verify_method is rendered weak, never strong", () => {
  const view = SourceStatus.describeAccess({
    state: "ok",
    enabled: true,
    auth: { credential: "present", verification: "verified", verify_method: "quantum_vibes" },
  }, { now: NOW });

  assert.equal(view.evidence.rank, "unknown");
  // Guessing "strong" for a method this build has never heard of is exactly the
  // overclaim the contract removes.
  assert.notEqual(view.evidence.rank, "direct");
  assert.equal(view.evidence.symbol, SourceStatus.UNKNOWN_EVIDENCE.symbol);
});

test("credential trouble outranks the verification verdict", () => {
  const none = SourceStatus.describeAccess({
    state: "missing",
    enabled: true,
    auth: { credential: "none", verification: "unverified", verify_method: "live_probe" },
  }, { now: NOW });
  const invalid = SourceStatus.describeAccess({
    state: "missing_cookie",
    enabled: true,
    auth: { credential: "invalid", verification: "unverified", verify_method: "live_probe" },
  }, { now: NOW });
  // The dimensions are orthogonal, so nothing stops a backend from pairing a
  // stale `verified` with a credential that has since been cleared. When they
  // disagree we under-claim rather than paint a green light we cannot back.
  const contradictory = SourceStatus.describeAccess({
    state: "ready",
    enabled: true,
    auth: { credential: "none", verification: "verified", verify_method: "live_probe" },
  }, { now: NOW });

  assert.equal(none.label, "需要登录");
  assert.equal(invalid.label, "凭据不完整");
  assert.equal(contradictory.label, "需要登录");
  assert.notEqual(contradictory.tone, "ready");
});

test("每个 verification 都有 tone 与文案，rate_limited 不诬告凭据", () => {
  for (const verification of Object.keys(SourceStatus.SOURCE_ACCESS_VERIFICATION)) {
    const view = SourceStatus.describeAccess({
      state: "ok",
      enabled: true,
      auth: { credential: "present", verification, verify_method: "passive_health" },
    }, { now: NOW });
    assert.equal(view.known, true, verification);
    assert.ok(view.label.length > 0, verification);
    assert.ok(/^#[0-9a-f]{6}$/i.test(view.color), `${verification} -> ${view.color}`);
  }
  // The platform throttling us says nothing about the credential, so it must
  // not be painted as a credential failure and send the user off to re-paste a
  // cookie that works.
  const limited = SourceStatus.describeAccess({
    state: "rate_limited",
    enabled: true,
    auth: { credential: "present", verification: "rate_limited", verify_method: "passive_health" },
  }, { now: NOW });
  assert.notEqual(limited.tone, "danger");
});

test("dashboard issue classification covers every enabled source without flagging normal pending states", () => {
  const actionable = [
    { state: "missing", auth: { credential: "none", verification: "unverified" } },
    { state: "stale", auth: { credential: "present", verification: "stale" } },
    { state: "error", auth: { credential: "present", verification: "failed" } },
    { state: "blocked", auth: { credential: "present", verification: "blocked" } },
    { state: "rate_limited", auth: { credential: "present", verification: "rate_limited" } },
  ];
  for (const item of actionable) {
    const issue = SourceStatus.describeSourceIssue({
      ...item,
      enabled: true,
      detail: `backend detail for ${item.state}`,
    });
    assert.ok(issue, item.state);
    assert.equal(issue.detail, `backend detail for ${item.state}`);
  }

  for (const item of [
    { state: "ready", auth: { credential: "present", verification: "verified" } },
    { state: "unverified", auth: { credential: "present", verification: "unverified" } },
    { state: "syncing", auth: { credential: "present", verification: "unverified" } },
    { state: "missing", enabled: false, auth: { credential: "none", verification: "unverified" } },
  ]) {
    assert.equal(SourceStatus.describeSourceIssue({ enabled: true, ...item }), null, item.state);
  }
});

test("anonymous Weibo discovery health remains actionable independently of auth", () => {
  const base = {
    state: "no_auth",
    enabled: true,
    auth: {
      auth_required: false,
      credential: "none",
      verification: "unverified",
      verify_method: "none",
    },
  };

  const failed = SourceStatus.describeSourceIssue({
    ...base,
    discovery_state: "error",
    detail: "微博公开发现最近失败，将按节流策略自动重试。",
  });
  assert.deepEqual(failed, {
    tone: "danger",
    label: "发现失败",
    detail: "微博公开发现最近失败，将按节流策略自动重试。",
  });

  const limited = SourceStatus.describeSourceIssue({
    ...base,
    discovery_state: "rate_limited",
    feed_paused: true,
    detail: "微博公开接口正在退避冷却，到期后自动重试。",
  });
  assert.equal(limited?.tone, "warning");
  assert.equal(limited?.label, "发现已暂停");

  assert.equal(SourceStatus.describeSourceIssue({
    ...base,
    discovery_state: "ready",
    detail: "微博公开发现正常。",
  }), null);
});

test("feed_paused remains an actionable fallback for older health payloads", () => {
  const issue = SourceStatus.describeSourceIssue({
    state: "no_auth",
    enabled: true,
    feed_paused: true,
    detail: "后台发现暂时暂停。",
    auth: { auth_required: false, credential: "none", verification: "unverified" },
  });

  assert.equal(issue?.tone, "warning");
  assert.equal(issue?.label, "发现已暂停");
  assert.equal(issue?.detail, "后台发现暂时暂停。");
});

// A side panel installed from the store can be pointed at a self-hosted backend
// older than this contract, and a cached payload can predate it too. Neither
// may blank the row out.
test("a backend older than the contract still renders the legacy state", () => {
  const legacy = SourceStatus.describeAccess({
    state: "ready",
    enabled: true,
    detail: "B站 Cookie 含三个登录字段。",
  }, { now: NOW });

  assert.equal(legacy.contract, false);
  assert.equal(legacy.label, "凭据已就绪");
  assert.equal(legacy.tone, "ready");
  assert.equal(legacy.known, true);
  // Nothing is claimed about evidence we were never sent.
  assert.equal(legacy.evidence.text, "");
  assert.equal(legacy.evidence.rank, "none");
  assert.equal(legacy.line, "凭据已就绪：B站 Cookie 含三个登录字段。");
  assert.equal(legacy.verifyMethod, "");
});

test("a malformed auth object falls back rather than rendering a blank chip", () => {
  for (const auth of [null, {}, "nope", 42, { verification: "made_up" }]) {
    const view = SourceStatus.describeAccess({ state: "ready", enabled: true, auth }, { now: NOW });
    assert.equal(view.contract, false, JSON.stringify(auth));
    assert.equal(view.label, "凭据已就绪", JSON.stringify(auth));
    assert.equal(view.evidence.text, "", JSON.stringify(auth));
  }
});

test("verified_at ages the verdict, and a naked SQLite stamp is read as UTC", () => {
  const ago = (ms: number) => new Date(NOW - ms).toISOString();

  assert.equal(SourceStatus.describeVerifiedAt(ago(5_000), NOW), "刚刚");
  assert.equal(SourceStatus.describeVerifiedAt(ago(3 * 60_000), NOW), "3 分钟前");
  assert.equal(SourceStatus.describeVerifiedAt(ago(5 * 3_600_000), NOW), "5 小时前");
  assert.equal(SourceStatus.describeVerifiedAt(ago(2 * 86_400_000), NOW), "2 天前");
  // Older than a week falls back to an absolute local stamp.
  assert.match(SourceStatus.describeVerifiedAt(ago(30 * 86_400_000), NOW), /^\d{2}-\d{2} \d{2}:\d{2}$/);
  // Never verified, and unparseable input, both mean "nothing to say".
  assert.equal(SourceStatus.describeVerifiedAt("", NOW), "");
  assert.equal(SourceStatus.describeVerifiedAt("not a date", NOW), "");
  // A clock skew that puts the verdict in the future is not a negative age.
  assert.equal(SourceStatus.describeVerifiedAt(new Date(NOW + 60_000).toISOString(), NOW), "刚刚");

  // x_source_health and the task tables hand back SQLite's CURRENT_TIMESTAMP:
  // UTC, but with no marker on it. Date.parse would read that as local time and
  // age a fresh verdict by the reader's UTC offset — making real evidence look
  // stale. Both spellings of the same instant must agree.
  assert.equal(
    SourceStatus.describeVerifiedAt("2026-07-18 09:57:00", NOW),
    SourceStatus.describeVerifiedAt("2026-07-18T09:57:00+00:00", NOW),
  );
  assert.equal(SourceStatus.describeVerifiedAt("2026-07-18 09:57:00", NOW), "3 分钟前");
});

test("a missing payload reads as offline rather than as a verdict", () => {
  for (const missing of [null, undefined, "", 0]) {
    const view = SourceStatus.describeAccess(missing);
    assert.equal(view.present, false, String(missing));
    assert.ok(view.detail.length > 0, String(missing));
  }
});

test("every emitted state resolves to a tone with a colour", () => {
  // The eleven the backend can actually produce (spec D6's证伪 pass), plus the
  // four legacy keys the merged table still carries.
  for (const state of Object.keys(SourceStatus.SOURCE_ACCESS_STATE)) {
    const view = SourceStatus.describeAccess({ state, enabled: true });
    assert.equal(view.known, true, state);
    assert.ok(view.label.length > 0, state);
    assert.ok(/^#[0-9a-f]{6}$/i.test(view.color), `${state} -> ${view.color}`);
  }
});

test("the backend's detail is rendered verbatim, never rewritten", () => {
  const view = SourceStatus.describeAccess({
    state: "ready",
    enabled: true,
    detail: "B站 Cookie 含三个登录字段。",
  });

  assert.equal(view.detail, "B站 Cookie 含三个登录字段。");
  assert.equal(view.text, "凭据已就绪：B站 Cookie 含三个登录字段。");
});

test("a disabled source is marked as such on single-line surfaces", () => {
  const off = SourceStatus.describeAccess({ state: "ready", enabled: false });
  const on = SourceStatus.describeAccess({ state: "ready", enabled: true });

  assert.ok(off.line.startsWith("(未启用) "));
  assert.ok(!on.line.startsWith("(未启用) "));
  // `text` stays clean for the desktop page, which shows scheduling in its own
  // badge and would otherwise print the note twice.
  assert.equal(off.text, on.text);
});

test("verify outcomes map to three tones, indeterminate included", () => {
  assert.equal(SourceStatus.describeVerifyResult({ outcome: "verified" }).tone, "success");
  assert.equal(SourceStatus.describeVerifyResult({ outcome: "failed" }).tone, "error");
  assert.equal(
    SourceStatus.describeVerifyResult({ outcome: "indeterminate" }).tone,
    "neutral",
  );
  // An unknown outcome must not fall through to red: telling a user their
  // credential failed when we simply could not tell is the error this avoids.
  assert.equal(SourceStatus.describeVerifyResult({ outcome: "wat" }).tone, "neutral");
  assert.equal(SourceStatus.describeVerifyResult(null).tone, "neutral");
});

test("a replayed verification is visibly distinct from a fresh one", () => {
  const fresh = SourceStatus.describeVerifyResult({ outcome: "verified", message: "已验证。" });
  const replayed = SourceStatus.describeVerifyResult({
    outcome: "verified",
    message: "已验证。",
    replayed: true,
  });

  assert.equal(fresh.text, "已验证。");
  assert.notEqual(replayed.text, fresh.text);
  assert.ok(replayed.text.startsWith("已验证。"));
});

test("failing to reach our own backend is indeterminate, not a failure", () => {
  const view = SourceStatus.describeVerifyError(new Error("timeout"));

  assert.equal(view.tone, "neutral");
  assert.ok(view.text.includes("timeout"));
});

test("extension-only platforms expose no writable input", () => {
  const view = SourceStatus.describeCredential({
    available: false,
    detail: "后端不保存知乎 Cookie。",
    form: { kind: "extension_only", label: "知乎登录态", actions: [{ action: "verify" }] },
  });

  assert.equal(view.form.writable, false);
  assert.equal(view.summary, "后端不保存知乎 Cookie。");
  assert.equal(view.canCopy, false);
});

test("cookie platforms are writable and copyable when a value is stored", () => {
  const view = SourceStatus.describeCredential({
    available: true,
    label: "Cookie",
    value: "SESS****DATA",
    summary: "Cookie 已保存，展开查看",
    form: {
      kind: "cookie_textarea",
      required_keys: ["SESSDATA", "bili_jct"],
      required_keys_mode: "all",
      actions: [{ action: "verify" }, { action: "copy" }],
    },
  });

  assert.equal(view.form.writable, true);
  assert.equal(view.canCopy, true);
  assert.equal(view.value, "SESS****DATA");
  assert.deepEqual(view.form.requiredKeys, ["SESSDATA", "bili_jct"]);
});

test("the backend's summary wins over the local fallback", () => {
  const view = SourceStatus.describeCredential({
    available: true,
    label: "xsec_token",
    summary: "xsec_token 已保存（不代表账号登录），展开查看",
    form: { kind: "extension_only" },
  });

  assert.ok(view.summary.includes("不代表账号登录"));
});

test("hasCredential reads the contract, not one platform's storage", () => {
  assert.equal(SourceStatus.hasCredential({ auth: { credential: "present" } }), true);
  // Present but structurally broken is not "connected" — that green light is
  // exactly what the orthogonal contract exists to stop.
  assert.equal(SourceStatus.hasCredential({ auth: { credential: "invalid" } }), false);
  assert.equal(SourceStatus.hasCredential({ auth: { credential: "none" } }), false);
  // Backend older than the contract: fall back to the coarse state.
  assert.equal(SourceStatus.hasCredential({ state: "ready" }), true);
  assert.equal(SourceStatus.hasCredential({ state: "missing" }), false);
  assert.equal(SourceStatus.hasCredential(null), false);
});

test("the cooldown makes a debounced button visibly wait", () => {
  const timers: Array<() => void> = [];
  const clock = {
    setInterval: (fn: () => void) => timers.push(fn),
    clearInterval: () => {},
  };
  const button = { textContent: "测试连接", disabled: false, dataset: {} as Record<string, string> };

  SourceStatus.startVerifyCooldown(button, 3, { clock });
  assert.equal(button.disabled, true);
  assert.ok(button.textContent.includes("3"));

  // Zero seconds means no wait at all, not a stuck button.
  SourceStatus.startVerifyCooldown(button, 0, { clock });
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, "测试连接");
});

// The side panel cannot fetch this file over HTTP (MV3 CSP), so the build copies
// it into the package. If that wiring breaks, the panel loads popup.js against
// an undefined global and every source row goes blank — worth failing loudly.
test("the side panel is wired to the copied shared module", () => {
  const root = process.cwd();
  const html = readFileSync(join(root, "popup/popup.html"), "utf8");
  const build = readFileSync(join(root, "scripts/build.mjs"), "utf8");

  const shared = html.indexOf('src="shared/source-status.js"');
  const popup = html.indexOf('src="popup.js"');
  assert.ok(shared > 0, "popup.html does not load the shared module");
  // Classic script first: it must have run before the deferred module reads it.
  assert.ok(shared < popup, "the shared module must be loaded before popup.js");
  assert.ok(
    build.includes("web/shared") && build.includes("popup/shared"),
    "build.mjs no longer copies the shared module into the package",
  );
});

// A no-auth source can still carry a verifiable credential. YouTube and Bangumi
// both report auth_required=false, but only one has a token to check. Treating
// them alike hid a network-confirmed Bangumi verdict behind a flat 无需登录 with
// no badge and no button (found in real-machine E2E, 2026-07-19).
const BANGUMI_TOKEN_VERIFIED = {
  state: "ready",
  enabled: true,
  detail: "个人令牌有效，已识别 Bangumi 账号。",
  auth: {
    auth_required: false,
    credential: "present",
    credential_origin: "config",
    verification: "verified",
    verify_method: "live_probe",
    verified_at: "2026-07-18T09:57:00+00:00",
    verify_ttl_seconds: 21600,
    can_verify_now: true,
  },
};

const YOUTUBE_NO_AUTH = {
  state: "no_auth",
  enabled: true,
  detail: "公开源 · 无需登录。",
  auth: {
    auth_required: false,
    credential: "none",
    verification: "unverified",
    verify_method: "none",
    can_verify_now: false,
  },
};

test("a no-auth source with a verified token shows its evidence, unlike YouTube", () => {
  const bgm = SourceStatus.describeAccess(BANGUMI_TOKEN_VERIFIED, { now: NOW });
  const yt = SourceStatus.describeAccess(YOUTUBE_NO_AUTH, { now: NOW });

  // Bangumi's token was confirmed against the network, so it reads as verified
  // with a live-probe badge — not folded into a flat 无需登录.
  assert.equal(bgm.evidence.rank, "direct");
  assert.ok(bgm.evidence.text.includes("◆ 联网验证"));
  assert.notEqual(bgm.label, "无需登录");

  // YouTube genuinely has nothing to verify: no badge, the public tier.
  assert.equal(yt.evidence.rank, "none");  // EVIDENCE_ABSENT.rank
  assert.equal(yt.label, "无需登录");
});
