import assert from "node:assert/strict";
import test from "node:test";

import {
  executeAction,
  registerE2EExecutor,
} from "../src/content/e2e-executor.ts";
import type { E2EAction, E2EPlatform } from "../src/shared/e2e.ts";

interface RectLike {
  width: number;
  height: number;
  top: number;
  left: number;
  bottom: number;
  right: number;
}

class FakeElement {
  public clicked = false;
  public scrolled = false;
  public scrollTop = 0;
  public scrollHeight = 0;
  public clientHeight = 0;
  public readonly dispatchedEvents: string[] = [];
  public readonly textContent: string;
  private readonly attrs: Record<string, string>;
  private readonly rect: RectLike;
  private readonly selectorMatches: Set<string>;
  private readonly style: {
    display: string;
    visibility: string;
    pointerEvents: string;
    opacity: string;
  };

  constructor(
    textContent: string,
    attrs: Record<string, string> = {},
    rect: RectLike = {
      width: 80,
      height: 24,
      top: 10,
      left: 10,
      bottom: 34,
      right: 90,
    },
    options: {
      selectors?: string[];
      style?: Partial<{
        display: string;
        visibility: string;
        pointerEvents: string;
        opacity: string;
      }>;
    } = {},
  ) {
    this.textContent = textContent;
    this.attrs = attrs;
    this.rect = rect;
    this.selectorMatches = new Set(options.selectors);
    this.style = {
      display: "block",
      visibility: "visible",
      pointerEvents: "auto",
      opacity: "1",
      ...options.style,
    };
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  getBoundingClientRect(): RectLike {
    return this.rect;
  }

  scrollIntoView(): void {
    this.scrolled = true;
  }

  click(): void {
    this.clicked = true;
  }

  scrollBy(options: { top?: number }): void {
    this.scrollTop += options.top ?? 0;
  }

  dispatchEvent(event: Event): boolean {
    this.dispatchedEvents.push(event.type);
    return true;
  }

  matches(selector: string): boolean {
    return selector
      .split(",")
      .map((value) => value.trim())
      .some((value) => {
        if (this.selectorMatches.has(value)) return true;
        if (value === "button") return this.attrs.tag === "button";
        if (value === "[role=\"button\"]") return this.attrs.role === "button";
        if (value === "a") return this.attrs.tag === "a";
        if (value === "div") return this.attrs.tag === "div";
        if (value === "span") return this.attrs.tag === "span";
        if (value === "video") return this.attrs.tag === "video";
        return false;
      });
  }

  getComputedStyle(): {
    display: string;
    visibility: string;
    pointerEvents: string;
    opacity: string;
  } {
    return this.style;
  }
}

function fakeEnv(elements: FakeElement[] = []) {
  const scrollCalls: unknown[] = [];
  const windowEvents: string[] = [];
  return {
    scrollCalls,
    windowEvents,
    document: {
      querySelectorAll(selector: string): FakeElement[] {
        if (selector === "*") return elements;
        return elements.filter((element) => element.matches(selector));
      },
    },
    window: {
      innerHeight: 800,
      scrollBy(options: unknown): void {
        scrollCalls.push(options);
      },
      dispatchEvent(event: Event): boolean {
        windowEvents.push(event.type);
        return true;
      },
    },
    sleep: async () => {},
  };
}

function envWithButtonLabel(label: string): ReturnType<typeof fakeEnv> & { button: FakeElement } {
  const button = new FakeElement(label, { tag: "button", "aria-label": label });
  return { ...fakeEnv([button]), button };
}

test.before(() => {
  const globals = globalThis as { getComputedStyle?: (element: FakeElement) => ReturnType<FakeElement["getComputedStyle"]> };
  globals.getComputedStyle = (element) => element.getComputedStyle();
});

test("twitter share clicks a visible matching target", async () => {
  const share = new FakeElement("", { "aria-label": "Share post", tag: "button" });
  const env = fakeEnv([share]);

  const result = await executeAction("twitter", "share", false, env);

  assert.deepEqual(result, { action: "share", status: "ok", detail: "clicked" });
  assert.equal(share.scrolled, true);
  assert.equal(share.clicked, true);
});

test("xiaohongshu share can click an icon-only class selector", async () => {
  const share = new FakeElement("", { tag: "div" }, undefined, {
    selectors: ['[class*="share" i]'],
  });
  const env = fakeEnv([share]);

  const result = await executeAction("xiaohongshu", "share", false, env);

  assert.deepEqual(result, { action: "share", status: "ok", detail: "clicked" });
  assert.equal(share.scrolled, true);
  assert.equal(share.clicked, true);
});

test("state-changing actions are skipped when not allowed", async () => {
  const like = new FakeElement("Like", { tag: "button" });
  const env = fakeEnv([like]);

  const result = await executeAction("twitter", "like", false, env);

  assert.deepEqual(result, {
    action: "like",
    status: "skipped",
    detail: "state_changing_action_blocked",
  });
  assert.equal(like.clicked, false);
});

test("state-changing actions click when allowed and matching text exists", async () => {
  const favorite = new FakeElement("收藏", { tag: "button" });
  const env = fakeEnv([favorite]);

  const result = await executeAction("xiaohongshu", "favorite", true, env);

  assert.deepEqual(result, { action: "favorite", status: "ok", detail: "clicked" });
  assert.equal(favorite.clicked, true);
});

test("click fails when no platform target is found", async () => {
  const hidden = new FakeElement("tweet", {}, {
    width: 0,
    height: 0,
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
  }, { selectors: ['[data-testid="tweet"]'] });
  const env = fakeEnv([hidden]);

  const result = await executeAction("twitter", "click", false, env);

  assert.deepEqual(result, {
    action: "click",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(hidden.clicked, false);
});

test("default click does not fall back to a plain button control", async () => {
  const button = new FakeElement("More actions", { role: "button" });
  const env = fakeEnv([button]);

  const result = await executeAction("douyin", "click", false, env);

  assert.deepEqual(result, {
    action: "click",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(button.clicked, false);
});

test("state action skips active english labels", async () => {
  const unlike = new FakeElement("Unlike", { tag: "button", "aria-label": "Unlike" });
  const env = fakeEnv([unlike]);

  const result = await executeAction("twitter", "like", true, env);

  assert.deepEqual(result, {
    action: "like",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(unlike.clicked, false);
});

test("state action skips active chinese labels", async () => {
  const following = new FakeElement("已关注", { tag: "button", "aria-label": "已关注" });
  const env = fakeEnv([following]);

  const result = await executeAction("xiaohongshu", "follow", true, env);

  assert.deepEqual(result, {
    action: "follow",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(following.clicked, false);
});

test("like action skips cancel-like chinese labels", async () => {
  const env = envWithButtonLabel("取消点赞");

  const result = await executeAction("douyin", "like", true, env);

  assert.deepEqual(result, {
    action: "like",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(env.button.clicked, false);
});

test("state action can click an inactive target after skipping active targets", async () => {
  const following = new FakeElement("Following", { tag: "button", "aria-label": "Following" });
  const follow = new FakeElement("Follow", { tag: "button", "aria-label": "Follow" });
  const env = fakeEnv([following, follow]);

  const result = await executeAction("twitter", "follow", true, env);

  assert.deepEqual(result, { action: "follow", status: "ok", detail: "clicked" });
  assert.equal(following.clicked, false);
  assert.equal(follow.clicked, true);
});

test("disabled and aria-disabled targets are skipped", async () => {
  const disabled = new FakeElement("Like", { tag: "button", disabled: "" });
  const ariaDisabled = new FakeElement("Like", { tag: "button", "aria-disabled": "true" });
  const env = fakeEnv([disabled, ariaDisabled]);

  const result = await executeAction("twitter", "like", true, env);

  assert.deepEqual(result, {
    action: "like",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(disabled.clicked, false);
  assert.equal(ariaDisabled.clicked, false);
});

test("pointer-events none targets are skipped", async () => {
  const target = new FakeElement(
    "Like",
    { tag: "button" },
    undefined,
    { style: { pointerEvents: "none" } },
  );
  const env = fakeEnv([target]);

  const result = await executeAction("twitter", "like", true, env);

  assert.deepEqual(result, {
    action: "like",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(target.clicked, false);
});

test("offscreen targets are skipped", async () => {
  const target = new FakeElement("Like", { tag: "button" }, {
    width: 80,
    height: 24,
    top: 1300,
    left: 10,
    bottom: 1324,
    right: 90,
  });
  const env = fakeEnv([target]);

  const result = await executeAction("twitter", "like", true, env);

  assert.deepEqual(result, {
    action: "like",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(target.clicked, false);
});

test("scroll calls window.scrollBy with a smooth viewport-sized step", async () => {
  const env = fakeEnv();

  const result = await executeAction("douyin", "scroll", false, env);

  assert.deepEqual(result, { action: "scroll", status: "ok", detail: "scrolled" });
  assert.deepEqual(env.scrollCalls, [{ top: 600, behavior: "smooth" }]);
  assert.deepEqual(env.windowEvents, ["scroll"]);
});

test("scroll prefers a visible internal scroll container", async () => {
  const scroller = new FakeElement(
    "",
    { tag: "div" },
    {
      width: 400,
      height: 500,
      top: 0,
      left: 0,
      bottom: 500,
      right: 400,
    },
  );
  scroller.scrollHeight = 1800;
  scroller.clientHeight = 500;
  const env = fakeEnv([scroller]);

  const result = await executeAction("douyin", "scroll", false, env);

  assert.deepEqual(result, { action: "scroll", status: "ok", detail: "scrolled" });
  assert.deepEqual(env.scrollCalls, []);
  assert.equal(scroller.scrollTop, 600);
  assert.deepEqual(scroller.dispatchedEvents, ["scroll"]);
});

test("registerE2EExecutor registers an async chrome message listener", async () => {
  const listeners: Array<
    (
      message: unknown,
      sender: unknown,
      sendResponse: (response: unknown) => void,
    ) => boolean | undefined
  > = [];
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: {
        addListener(listener: (typeof listeners)[number]): void {
          listeners.push(listener);
        },
      },
    },
  };

  try {
    registerE2EExecutor("twitter");
    assert.equal(listeners.length, 1);

    const responsePromise = new Promise<unknown>((resolve) => {
      const keepAlive = listeners[0](
        {
          action: "OBC_E2E_EXECUTE",
          platform: "twitter" satisfies E2EPlatform,
          runId: "run-1",
          actions: ["snapshot"] satisfies E2EAction[],
          allowStateChanging: false,
        },
        {},
        resolve,
      );
      assert.equal(keepAlive, true);
    });

    assert.deepEqual(await responsePromise, {
      status: "ok",
      actions: [{ action: "snapshot", status: "ok", detail: "snapshot", executed: true }],
    });
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("registerE2EExecutor ignores messages for other platforms", () => {
  const listeners: Array<
    (
      message: unknown,
      sender: unknown,
      sendResponse: (response: unknown) => void,
    ) => boolean | undefined
  > = [];
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: {
        addListener(listener: (typeof listeners)[number]): void {
          listeners.push(listener);
        },
      },
    },
  };

  try {
    registerE2EExecutor("twitter");
    assert.equal(listeners.length, 1);

    let responseCalled = false;
    const keepAlive = listeners[0](
      {
        action: "OBC_E2E_EXECUTE",
        platform: "douyin" satisfies E2EPlatform,
        runId: "run-1",
        actions: ["snapshot"] satisfies E2EAction[],
        allowStateChanging: false,
      },
      {},
      () => {
        responseCalled = true;
      },
    );

    assert.equal(keepAlive, false);
    assert.equal(responseCalled, false);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});

test("registerE2EExecutor does not add duplicate listeners for a platform", () => {
  const listeners: Array<
    (
      message: unknown,
      sender: unknown,
      sendResponse: (response: unknown) => void,
    ) => boolean | undefined
  > = [];
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: {
        addListener(listener: (typeof listeners)[number]): void {
          listeners.push(listener);
        },
      },
    },
  };

  try {
    registerE2EExecutor("xiaohongshu");
    registerE2EExecutor("xiaohongshu");

    assert.equal(listeners.length, 1);
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});
