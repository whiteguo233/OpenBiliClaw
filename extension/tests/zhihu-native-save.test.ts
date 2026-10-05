import assert from "node:assert/strict";
import test from "node:test";

import {
  createZhihuBrowserEnvironment,
  saveZhihu,
  verifyZhihu,
  type ZhihuFavoriteState,
  type ZhihuNativeSaveEnvironment,
} from "../src/content/native-save/zhihu.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const TASK_ID = "123e4567-e89b-42d3-a456-426614174008";

function taskFor(
  contentType: "question" | "answer" | "article",
  id: string,
): NativeSaveTask {
  const contentId = `${contentType}:${id}`;
  const contentUrl = contentType === "answer"
    ? `https://www.zhihu.com/question/101/answer/${id}`
    : contentType === "article"
      ? `https://zhuanlan.zhihu.com/p/${id}`
      : `https://www.zhihu.com/question/${id}`;
  return {
    id: TASK_ID,
    type: "native_save",
    platform: "zhihu",
    platform_slug: "zhihu",
    item_key: `zhihu:${contentId}`,
    content_id: contentId,
    content_url: contentUrl,
    content_type: contentType,
    requested_action: "favorite",
    resolved_action: "favorite",
    target_label: "知乎收藏",
  };
}

interface FixtureOptions {
  currentUrl?: string;
  loginOverlay?: boolean;
  loginAfterClick?: boolean;
  unavailable?: boolean;
  initialState?: ZhihuFavoriteState;
  confirmAfterClick?: boolean;
  clickThrows?: boolean;
  readyAfterSleeps?: number;
  savedAfterSleeps?: number;
  rateFingerprints?: string[];
}

function fixture(
  task: NativeSaveTask,
  options: FixtureOptions = {},
): ZhihuNativeSaveEnvironment & { clicks: number; sleeps: number } {
  let state = options.initialState === undefined ? "not_saved" : options.initialState;
  let loginOverlay = options.loginOverlay ?? false;
  let rateIndex = 0;
  const env = {
    currentUrl: options.currentUrl ?? task.content_url,
    clicks: 0,
    sleeps: 0,
    hasVisibleLoginOverlay: () => loginOverlay,
    isUnavailable: () => options.unavailable ?? false,
    favoriteState() {
      if (env.sleeps < (options.readyAfterSleeps ?? 0)) return null;
      if (options.savedAfterSleeps !== undefined && env.sleeps >= options.savedAfterSleeps) {
        state = "saved";
      }
      return state;
    },
    clickFavorite() {
      env.clicks += 1;
      if (options.clickThrows) throw new Error("rejected");
      if (options.loginAfterClick) loginOverlay = true;
      if (options.confirmAfterClick ?? true) state = "saved";
    },
    rateLimitFingerprint() {
      const values = options.rateFingerprints ?? [""];
      const value = values[Math.min(rateIndex, values.length - 1)] ?? "";
      rateIndex += 1;
      return value;
    },
    sleep: async () => { env.sleeps += 1; },
  } satisfies ZhihuNativeSaveEnvironment & { clicks: number; sleeps: number };
  return env;
}

test("Zhihu native save accepts exact typed question, answer, and article identities", async () => {
  for (const [type, id] of [
    ["question", "1001"],
    ["answer", "2002"],
    ["article", "3003"],
  ] as const) {
    const task = taskFor(type, id);
    const env = fixture(task);
    assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
    assert.equal(env.clicks, 1);
  }
});

test("Zhihu native save waits for one exact favorite control before mutation", async () => {
  const task = taskFor("answer", "2002");
  const env = fixture(task, { readyAfterSleeps: 3 });
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.equal(env.sleeps, 3);
  assert.equal(env.clicks, 1);
});

test("Zhihu native save reports exact control, request, and confirmation failures", async () => {
  const task = taskFor("answer", "2002");
  const cases: Array<[ZhihuNativeSaveEnvironment & { clicks: number }, string]> = [
    [fixture(task, { initialState: null }), "native_control_not_found"],
    [fixture(task, { clickThrows: true }), "native_request_rejected"],
    [fixture(task, { confirmAfterClick: false }), "native_confirmation_not_observed"],
  ];
  for (const [env, code] of cases) {
    assert.deepEqual(await saveZhihu(task, env), { status: "failed", error_code: code });
  }
  assert.equal(cases[0][0].clicks, 0);
  assert.equal(cases[1][0].clicks, 1);
  assert.equal(cases[2][0].clicks, 1);
});

test("Zhihu persisted verification is strictly read-only", async () => {
  const task = taskFor("answer", "2002");
  const saved = fixture(task, { initialState: "saved" });
  assert.deepEqual(await verifyZhihu(task, saved), { status: "already_synced" });
  assert.equal(saved.clicks, 0);

  const missing = fixture(task, { initialState: "not_saved" });
  assert.deepEqual(await verifyZhihu(task, missing), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(missing.clicks, 0);
});

test("Zhihu native save rejects task, page, item, and content-type mismatches before mutation", async () => {
  const task = taskFor("answer", "2002");
  const cases = [
    { ...task, platform: "reddit" as const },
    { ...task, platform_slug: "reddit" as const },
    { ...task, item_key: "zhihu:answer:9999" },
    { ...task, content_type: "question" },
    { ...task, content_url: "https://www.zhihu.com/question/999/answer/2002" },
  ];
  for (const candidate of cases) {
    const env = fixture(task);
    assert.deepEqual(await saveZhihu(candidate, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.clicks, 0);
  }
});

test("Zhihu native save handles unavailable and login states without mutation", async () => {
  const task = taskFor("answer", "2002");
  const unavailable = fixture(task, { unavailable: true });
  assert.deepEqual(await saveZhihu(task, unavailable), {
    status: "unsupported",
    error_code: "unsupported_content_type",
  });
  assert.equal(unavailable.clicks, 0);

  const login = fixture(task, { loginOverlay: true });
  assert.deepEqual(await saveZhihu(task, login), { status: "login_required" });
  assert.equal(login.clicks, 0);

  const prompted = fixture(task, { loginAfterClick: true, confirmAfterClick: false });
  assert.deepEqual(await saveZhihu(task, prompted), { status: "login_required" });
  assert.equal(prompted.clicks, 1);
});

test("Zhihu favorite and watch_later resolve only to the exact global favorite target", async () => {
  const task = taskFor("answer", "2002");
  for (const requestedAction of ["favorite", "watch_later"] as const) {
    const env = fixture(task);
    assert.deepEqual(await saveZhihu({ ...task, requested_action: requestedAction }, env), {
      status: "synced",
    });
  }
  for (const target of ["OpenBiliClaw", "Zhihu Favorites", " 知乎收藏", "知乎收藏 "]) {
    const env = fixture(task);
    assert.deepEqual(await saveZhihu({ ...task, target_label: target }, env), {
      status: "failed",
      error_code: "native_save_failed",
    });
    assert.equal(env.clicks, 0);
  }
});

test("Zhihu native save never clicks an already-saved control", async () => {
  const task = taskFor("answer", "2002");
  const env = fixture(task, { initialState: "saved" });
  assert.deepEqual(await saveZhihu(task, env), { status: "already_synced" });
  assert.equal(env.clicks, 0);
});

test("Zhihu native save detects only directional new action-local rate evidence", async () => {
  const task = taskFor("answer", "2002");
  const rateLimited = fixture(task, {
    confirmAfterClick: false,
    rateFingerprints: ["1:stale", "1:stale\n2:操作频繁"],
  });
  assert.deepEqual(await saveZhihu(task, rateLimited), { status: "rate_limited" });

  const stale = fixture(task, {
    confirmAfterClick: false,
    rateFingerprints: ["1:stale", "1:stale"],
  });
  assert.deepEqual(await saveZhihu(task, stale), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
});

test("Zhihu native save rejects extra-colon typed identities before mutation", async () => {
  for (const invalidContentId of [
    "question:1001:extra",
    "answer:2002:extra",
    "article:3003:extra",
  ]) {
    const type = invalidContentId.split(":", 1)[0] as "question" | "answer" | "article";
    const validTask = taskFor(type, invalidContentId.split(":")[1]);
    const invalidTask = {
      ...validTask,
      content_id: invalidContentId,
      item_key: `zhihu:${invalidContentId}`,
    };
    const env = fixture(validTask);
    assert.deepEqual(await saveZhihu(invalidTask, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.clicks, 0);
  }
});

function domElement(options: {
  attrs?: Record<string, string>;
  getAttr?: (name: string) => string | null;
  hidden?: boolean;
  parent?: unknown;
  text?: string;
  query?: (selector: string) => unknown[];
  click?: () => void;
} = {}): HTMLElement {
  const attrs = new Map(Object.entries(options.attrs ?? {}));
  return {
    hidden: options.hidden ?? false,
    style: { display: "", visibility: "" },
    parentElement: options.parent ?? null,
    textContent: options.text ?? "",
    hasAttribute(name: string) { return attrs.has(name); },
    getAttribute(name: string) { return options.getAttr?.(name) ?? attrs.get(name) ?? null; },
    querySelectorAll(selector: string) { return options.query?.(selector) ?? []; },
    click() { options.click?.(); },
  } as unknown as HTMLElement;
}

test("Zhihu browser environment applies full ancestor visibility to login and unavailable overlays", () => {
  const hiddenAncestor = domElement({ hidden: true });
  const hiddenLogin = domElement({ parent: hiddenAncestor });
  const hiddenDeleted = domElement({ parent: hiddenAncestor, text: "内容已删除" });
  const visibleLogin = domElement();
  let loginNodes = [hiddenLogin];
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      if (selector.includes("SignFlow")) return loginNodes;
      if (selector.includes("ErrorPage")) return [hiddenDeleted];
      return [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, taskFor("question", "1001").content_url);
  assert.equal(env.hasVisibleLoginOverlay(), false);
  assert.equal(env.isUnavailable(), false);
  loginNodes = [visibleLogin];
  assert.equal(env.hasVisibleLoginOverlay(), true);
});

test("Zhihu browser environment binds the global toggle to the exact closest identity", async () => {
  const task = taskFor("answer", "2002");
  let targetLabel = "收藏";
  let targetClicks = 0;
  let relatedClicks = 0;
  let targetContainer: HTMLElement;
  let relatedContainer: HTMLElement;
  const targetButton = domElement({
    getAttr: (name) => name === "aria-label" ? targetLabel : null,
    text: "\u200b 209",
    click: () => { targetClicks += 1; targetLabel = "已收藏"; },
  });
  const relatedButton = domElement({
    attrs: { "aria-label": "收藏" },
    click: () => { relatedClicks += 1; },
  });
  relatedContainer = domElement({ attrs: { "data-answer-id": "9999" } });
  targetContainer = domElement({
    attrs: { "data-answer-id": "2002" },
    query: (selector) => selector.includes("button") ? [targetButton, relatedButton] : [],
  });
  (targetButton as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (relatedContainer as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (relatedButton as unknown as { parentElement: HTMLElement }).parentElement = relatedContainer;
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("data-answer-id") || selector.includes("data-zop")
        ? [targetContainer, relatedContainer]
        : [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, task.content_url);
  assert.equal(env.favoriteState(), "not_saved");
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.equal(env.favoriteState(), "saved");
  assert.equal(targetClicks, 1);
  assert.equal(relatedClicks, 0);
  assert.deepEqual(await verifyZhihu(task, env), { status: "already_synced" });
  assert.equal(targetClicks, 1);
});

test("Zhihu browser environment rejects cancel labels and ambiguous exact controls", () => {
  const task = taskFor("answer", "2002");
  let clicks = 0;
  const container = domElement({ attrs: { "data-answer-id": "2002" } });
  const cancel = domElement({
    attrs: { "aria-label": "取消收藏" },
    parent: container,
    click: () => { clicks += 1; },
  });
  let controls = [cancel];
  (container as unknown as { querySelectorAll(selector: string): unknown[] }).querySelectorAll =
    (selector: string) => selector.includes("button") ? controls : [];
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("data-answer-id") || selector.includes("data-zop")
        ? [container]
        : [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, task.content_url);
  assert.equal(env.favoriteState(), null);
  assert.throws(() => env.clickFavorite());
  assert.equal(clicks, 0);

  const first = domElement({ attrs: { "aria-label": "收藏" }, parent: container });
  const second = domElement({ attrs: { "aria-label": "收藏" }, parent: container });
  controls = [first, second];
  assert.equal(env.favoriteState(), null);
  assert.throws(() => env.clickFavorite());
});

test("Zhihu browser rate evidence ignores stale nested alerts and detects a newly visible target alert", () => {
  const task = taskFor("question", "1001");
  const stale = domElement({ text: "操作频繁，请稍后再试" });
  const fresh = domElement({ hidden: true, text: "操作频繁，请稍后再试" });
  const nested = domElement({ text: "操作频繁，请稍后再试" });
  let targetContainer: HTMLElement;
  const nestedRecommendation = domElement({ attrs: { "data-answer-id": "9999" } });
  targetContainer = domElement({
    attrs: { "data-question-id": "1001" },
    query: (selector) => selector.includes("role='alert'") ? [stale, fresh, nested] : [],
  });
  (stale as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (fresh as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (nestedRecommendation as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (nested as unknown as { parentElement: HTMLElement }).parentElement = nestedRecommendation;
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("data-question-id") || selector.includes("data-zop")
        ? [targetContainer, nestedRecommendation]
        : [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, task.content_url);
  const before = env.rateLimitFingerprint();
  assert.match(before, /^1:操作频繁/);
  assert.equal(before.split("\n").length, 1);
  fresh.hidden = false;
  const after = env.rateLimitFingerprint();
  assert.equal(after.split("\n").length, 2);
  assert.notEqual(after, before);
});
