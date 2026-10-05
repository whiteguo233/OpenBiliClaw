import assert from "node:assert/strict";
import test from "node:test";

import {
  createYouTubeBrowserEnvironment,
  saveYouTube,
  verifyYouTube,
  type YouTubeNativeSaveEnvironment,
  type YouTubePlaylistRow,
} from "../src/content/native-save/youtube.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const VIDEO_ID = "dQw4w9WgXcQ";
const task: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174006",
  type: "native_save",
  platform: "youtube",
  platform_slug: "yt",
  item_key: `youtube:${VIDEO_ID}`,
  content_id: VIDEO_ID,
  content_url: `https://www.youtube.com/watch?v=${VIDEO_ID}`,
  content_type: "video",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "OpenBiliClaw",
};

interface FixtureOptions {
  currentUrl?: string;
  loggedIn?: boolean;
  unavailable?: boolean;
  rateLimited?: boolean;
  rateLimitedAfterMutation?: boolean;
  initialNamedRows?: Array<{ title: string; checked?: boolean }>;
  watchLaterChecked?: boolean;
  confirmWatchLaterAfterReopen?: boolean;
  createSucceeds?: boolean;
  creationFailureCode?: "native_control_not_found" | "native_dialog_not_opened" | "native_target_not_found" | "native_request_rejected";
  createdTitle?: string;
  createdRowAfterLookups?: number;
  confirmAfterClick?: boolean;
  dialogAvailable?: boolean;
  readyAfterSleeps?: number;
  saveControlReadyAfterSleeps?: number;
  persistedAfterSleeps?: number;
  targetVisibleAfterSleeps?: number;
}

function fixture(options: FixtureOptions = {}): YouTubeNativeSaveEnvironment & {
  actions: string[];
  hasSaveControl(): boolean;
  namedLookups: number;
  mutations: number;
} {
  let open = false;
  let rows = (options.initialNamedRows ?? [{ title: "OpenBiliClaw" }]).map((row) => ({ ...row }));
  let pendingCreatedRow: { title: string; checked: boolean } | null = null;
  let watchLaterChecked = options.watchLaterChecked ?? false;
  let watchLaterClicked = false;
  let rateLimited = options.rateLimited ?? false;
  let sleeps = 0;
  const ready = () => sleeps >= (options.readyAfterSleeps ?? 0);
  const saveControlReady = () => sleeps >= (options.saveControlReadyAfterSleeps ?? 0);
  const env = {
    actions: [] as string[],
    namedLookups: 0,
    mutations: 0,
    currentUrl: options.currentUrl ?? task.content_url,
    isLoggedIn: () => ready() && (options.loggedIn ?? true),
    isUnavailable: () => options.unavailable ?? false,
    hasSaveControl: () => saveControlReady(),
    rateLimitFingerprint: () => rateLimited ? "limited" : "",
    async openSaveDialog() {
      env.actions.push("open-save-dialog");
      if (options.confirmWatchLaterAfterReopen && watchLaterClicked) watchLaterChecked = true;
      open = saveControlReady() && (options.dialogAvailable ?? true);
      return open;
    },
    async closeSaveDialog() {
      env.actions.push("close-save-dialog");
      open = false;
    },
    findNamedPlaylists(title: string): YouTubePlaylistRow[] {
      env.namedLookups += 1;
      if (pendingCreatedRow && env.namedLookups >= (options.createdRowAfterLookups ?? 0)) {
        rows.push(pendingCreatedRow);
        pendingCreatedRow = null;
      }
      if (!open) return [];
      if (sleeps < (options.targetVisibleAfterSleeps ?? 0)) return [];
      return rows.filter((candidate) => candidate.title === title).map((row) => ({
        isChecked: () => row.checked ?? (
          options.persistedAfterSleeps !== undefined && sleeps >= options.persistedAfterSleeps
        ),
        click() {
          env.actions.push(`select:${row.title}`);
          env.mutations += 1;
          if (options.rateLimitedAfterMutation) rateLimited = true;
          if (options.confirmAfterClick ?? true) row.checked = true;
        },
      }));
    },
    findWatchLater(): YouTubePlaylistRow | null {
      if (!open) return null;
      if (sleeps < (options.targetVisibleAfterSleeps ?? 0)) return null;
      return {
        isChecked: () => watchLaterChecked || (
          options.persistedAfterSleeps !== undefined && sleeps >= options.persistedAfterSleeps
        ),
        click() {
          env.actions.push("select:watch-later");
          env.mutations += 1;
          watchLaterClicked = true;
          if (options.rateLimitedAfterMutation) rateLimited = true;
          if (options.confirmAfterClick ?? true) watchLaterChecked = true;
        },
      };
    },
    async createPlaylist(title: string) {
      env.actions.push(`create:${title}`);
      env.mutations += 1;
      if (options.rateLimitedAfterMutation) rateLimited = true;
      if (!(options.createSucceeds ?? true)) return false;
      const createdRow = { title: options.createdTitle ?? title, checked: false };
      if (options.createdRowAfterLookups === undefined) rows.push(createdRow);
      else pendingCreatedRow = createdRow;
      return true;
    },
    creationFailureCode: () => options.creationFailureCode ?? "native_request_rejected",
    sleep: async () => { sleeps += 1; },
  } satisfies YouTubeNativeSaveEnvironment & {
    actions: string[];
    hasSaveControl(): boolean;
    namedLookups: number;
    mutations: number;
  };
  return env;
}

test("YouTube native save accepts only canonical watch and shorts video URLs", async () => {
  for (const contentUrl of [
    `https://www.youtube.com/watch?v=${VIDEO_ID}`,
    `https://www.youtube.com/shorts/${VIDEO_ID}`,
  ]) {
    const env = fixture({ currentUrl: contentUrl });
    assert.deepEqual(await saveYouTube({ ...task, content_url: contentUrl }, env), { status: "synced" });
    assert.equal(env.mutations, 1);
  }

  for (const contentUrl of [
    `https://www.youtube.com/embed/${VIDEO_ID}`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}&list=PL123`,
    `https://www.youtube.com/shorts/${VIDEO_ID}/extra`,
    `https://www.youtube.com/watch?v=other123456`,
  ]) {
    const env = fixture({ currentUrl: contentUrl });
    assert.deepEqual(await saveYouTube({ ...task, content_url: contentUrl }, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.mutations, 0);
  }
});

test("YouTube native save waits for the authenticated watch page before mutation", async () => {
  const env = fixture({ readyAfterSleeps: 2 });
  assert.deepEqual(await saveYouTube(task, env), { status: "synced" });
  assert.equal(env.mutations, 1);
});

test("YouTube native save waits for the Save control and opens its dialog only once", async () => {
  const env = fixture({ saveControlReadyAfterSleeps: 2 });
  assert.deepEqual(await saveYouTube(task, env), { status: "synced" });
  assert.equal(env.actions.filter((action) => action === "open-save-dialog").length, 1);
});

test("YouTube native save reports the exact failed execution stage", async () => {
  const cases: Array<[YouTubeNativeSaveEnvironment, string]> = [
    [fixture({ saveControlReadyAfterSleeps: 100 }), "native_control_not_found"],
    [fixture({ dialogAvailable: false }), "native_dialog_not_opened"],
    [fixture({ confirmAfterClick: false }), "native_confirmation_not_observed"],
    [
      fixture({
        initialNamedRows: [],
        createSucceeds: false,
        creationFailureCode: "native_control_not_found",
      }),
      "native_control_not_found",
    ],
  ];

  for (const [env, errorCode] of cases) {
    assert.deepEqual(await saveYouTube(task, env), {
      status: "failed",
      error_code: errorCode,
    });
  }
});

test("YouTube persisted verification observes delayed membership without mutating", async () => {
  for (const candidate of [
    task,
    {
      ...task,
      requested_action: "watch_later" as const,
      resolved_action: "watch_later" as const,
      target_label: "YouTube Watch Later",
    },
  ]) {
    const env = fixture({ persistedAfterSleeps: 2, targetVisibleAfterSleeps: 2 });
    assert.deepEqual(await verifyYouTube(candidate, env), { status: "already_synced" });
    assert.equal(env.mutations, 0);
    assert.equal(env.actions.some((action) => action.startsWith("select:")), false);
    assert.equal(env.actions.some((action) => action.startsWith("create:")), false);
  }
});

test("YouTube persisted verification leaves unconfirmed membership failed without mutating", async () => {
  const env = fixture({ confirmAfterClick: false });
  assert.deepEqual(await verifyYouTube(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.mutations, 0);
});

test("YouTube native save deterministically reuses duplicate exact playlists", async () => {
  const unchecked = fixture({
    initialNamedRows: [{ title: "OpenBiliClaw" }, { title: "OpenBiliClaw" }],
  });
  assert.deepEqual(await saveYouTube(task, unchecked), { status: "synced" });
  assert.equal(unchecked.mutations, 1);

  const alreadyChecked = fixture({
    initialNamedRows: [
      { title: "OpenBiliClaw" },
      { title: "OpenBiliClaw", checked: true },
    ],
  });
  assert.deepEqual(await saveYouTube(task, alreadyChecked), { status: "already_synced" });
  assert.equal(alreadyChecked.mutations, 0);
});

test("YouTube native save correlates youtu.be tasks with safe canonical redirects", async () => {
  for (const currentUrl of [
    `https://www.youtube.com/watch?v=${VIDEO_ID}`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}&feature=share&si=redirect-token`,
    `https://www.youtube.com/shorts/${VIDEO_ID}?feature=share`,
  ]) {
    const env = fixture({ currentUrl });
    assert.deepEqual(await saveYouTube({ ...task, content_url: `https://youtu.be/${VIDEO_ID}` }, env), {
      status: "synced",
    });
  }
  for (const currentUrl of [
    `https://www.youtube.com/watch?v=${VIDEO_ID}&v=${VIDEO_ID}`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}&v=other123456`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}&secret=value`,
    `https://user:pass@www.youtube.com/watch?v=${VIDEO_ID}`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}#fragment`,
  ]) {
    const env = fixture({ currentUrl });
    assert.deepEqual(await saveYouTube({ ...task, content_url: `https://youtu.be/${VIDEO_ID}` }, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.mutations, 0);
  }
});

test("YouTube native save creates exact OpenBiliClaw then closes, reopens, and re-queries", async () => {
  const env = fixture({ initialNamedRows: [] });
  assert.deepEqual(await saveYouTube(task, env), { status: "synced" });
  assert.deepEqual(env.actions, [
    "open-save-dialog",
    "create:OpenBiliClaw",
    "close-save-dialog",
    "open-save-dialog",
    "select:OpenBiliClaw",
  ]);
  assert.equal(env.namedLookups, 2);
});

test("YouTube native save polls the reopened dialog until the created playlist materializes", async () => {
  const env = fixture({ initialNamedRows: [], createdRowAfterLookups: 4 });
  assert.deepEqual(await saveYouTube(task, env), { status: "synced" });
  assert.equal(env.namedLookups, 4);
});

test("YouTube native save uses exact Unicode case-sensitive playlist title", async () => {
  const env = fixture({ initialNamedRows: [{ title: "openbiliclaw" }] });
  assert.deepEqual(await saveYouTube(task, env), { status: "synced" });
  assert.ok(env.actions.includes("create:OpenBiliClaw"));
  assert.ok(env.actions.includes("select:OpenBiliClaw"));
  assert.ok(!env.actions.includes("select:openbiliclaw"));
});

test("YouTube native save never falls back after create failure or re-query mismatch", async () => {
  for (const [env, errorCode] of [
    [
      fixture({ initialNamedRows: [{ title: "Other" }], createSucceeds: false }),
      "native_request_rejected",
    ],
    [
      fixture({ initialNamedRows: [{ title: "Other" }], createdTitle: "openbiliclaw" }),
      "native_target_not_found",
    ],
  ] as const) {
    assert.deepEqual(await saveYouTube(task, env), {
      status: "failed",
      error_code: errorCode,
    });
    assert.ok(!env.actions.includes("select:Other"));
    assert.ok(!env.actions.includes("select:openbiliclaw"));
  }
});

test("YouTube native save maps a new create-time rate toast before missing-row failure", async () => {
  const env = fixture({
    initialNamedRows: [],
    createdTitle: "creation-rejected",
    rateLimitedAfterMutation: true,
  });
  assert.deepEqual(await saveYouTube(task, env), { status: "rate_limited" });
  assert.ok(!env.actions.some((action) => action.startsWith("select:")));
});

test("YouTube native save never mutates for action or exact-target contract mismatch", async () => {
  for (const mismatchedTask of [
    { ...task, target_label: "openbiliclaw" },
    { ...task, requested_action: "watch_later" as const },
    {
      ...task,
      requested_action: "watch_later" as const,
      resolved_action: "watch_later" as const,
      target_label: "OpenBiliClaw",
    },
  ]) {
    const env = fixture();
    assert.deepEqual(await saveYouTube(mismatchedTask, env), {
      status: "failed",
      error_code: "native_save_failed",
    });
    assert.equal(env.mutations, 0);
  }
});

test("YouTube native save returns already_synced without mutation for existing membership", async () => {
  const env = fixture({ initialNamedRows: [{ title: "OpenBiliClaw", checked: true }] });
  assert.deepEqual(await saveYouTube(task, env), { status: "already_synced" });
  assert.equal(env.mutations, 0);
});

test("YouTube native save watch later only uses the platform Watch Later row", async () => {
  for (const checked of [false, true]) {
    const env = fixture({ watchLaterChecked: checked, initialNamedRows: [] });
    const result = await saveYouTube({
      ...task,
      requested_action: "watch_later",
      resolved_action: "watch_later",
      target_label: "YouTube Watch Later",
    }, env);
    assert.deepEqual(result, { status: checked ? "already_synced" : "synced" });
    assert.equal(env.namedLookups, 0);
    assert.equal(env.actions.some((action) => action.startsWith("create:")), false);
  }
});

test("YouTube native save preserves the browser environment target failure stage", async () => {
  const env = {
    ...fixture({ initialNamedRows: [] }),
    findWatchLater: () => null,
    targetFailureCode: () => "native_control_not_found" as const,
  };
  assert.deepEqual(await saveYouTube({
    ...task,
    requested_action: "watch_later",
    resolved_action: "watch_later",
    target_label: "YouTube Watch Later",
  }, env), {
    status: "failed",
    error_code: "native_control_not_found",
  });
});

test("YouTube watch later reopens the dialog to confirm server membership", async () => {
  const env = fixture({
    initialNamedRows: [],
    confirmAfterClick: false,
    confirmWatchLaterAfterReopen: true,
  });
  assert.deepEqual(await saveYouTube({
    ...task,
    requested_action: "watch_later",
    resolved_action: "watch_later",
    target_label: "YouTube Watch Later",
  }, env), { status: "synced" });
  assert.deepEqual(env.actions, [
    "open-save-dialog",
    "select:watch-later",
    "close-save-dialog",
    "open-save-dialog",
  ]);
});

test("YouTube native save maps logged out, unavailable, rate limited, and ambiguous DOM safely", async () => {
  const cases: Array<[YouTubeNativeSaveEnvironment, unknown]> = [
    [fixture({ loggedIn: false }), { status: "login_required" }],
    [fixture({ unavailable: true }), { status: "unsupported", error_code: "unsupported_content_type" }],
    [fixture({ rateLimitedAfterMutation: true, confirmAfterClick: false }), { status: "rate_limited" }],
    [fixture({ dialogAvailable: false }), { status: "failed", error_code: "native_dialog_not_opened" }],
    [fixture({ confirmAfterClick: false }), { status: "failed", error_code: "native_confirmation_not_observed" }],
  ];
  for (const [env, expected] of cases) assert.deepEqual(await saveYouTube(task, env), expected);
});

test("YouTube native save prefers checked membership proof over a stale rate toast", async () => {
  const env = fixture({
    initialNamedRows: [{ title: "OpenBiliClaw", checked: true }],
    rateLimited: true,
  });
  assert.deepEqual(await saveYouTube(task, env), { status: "already_synced" });
  assert.equal(env.mutations, 0);
});

test("YouTube native save ignores a stale global rate toast when no checked proof appears", async () => {
  const env = fixture({ rateLimited: true, confirmAfterClick: false });
  assert.deepEqual(await saveYouTube(task, env), {
    status: "failed",
    error_code: "native_confirmation_not_observed",
  });
  assert.equal(env.mutations, 1);
});

test("YouTube browser environment requires signed-in avatar/menu evidence", () => {
  const documentFixture = (state: "signed-in" | "signed-out" | "ambiguous") => ({
    querySelector(selector: string) {
      if (selector.includes("ServiceLogin") && state === "signed-out") return {};
      if (selector.includes("avatar-btn") && state === "signed-in") return {};
      return null;
    },
    querySelectorAll() {
      return [];
    },
  }) as unknown as Document;
  assert.equal(createYouTubeBrowserEnvironment(documentFixture("signed-in"), task.content_url).isLoggedIn(), true);
  assert.equal(createYouTubeBrowserEnvironment(documentFixture("signed-out"), task.content_url).isLoggedIn(), false);
  assert.equal(createYouTubeBrowserEnvironment(documentFixture("ambiguous"), task.content_url).isLoggedIn(), false);
});

test("YouTube browser environment correlates a newly visible dialog view model", async () => {
  let dialogVisible = false;
  const button = {
    hidden: false,
    style: {},
    parentElement: null,
    getAttribute(name: string) { return name === "aria-label" ? "Save" : null; },
    hasAttribute() { return false; },
    click() { dialogVisible = true; },
  };
  const menu = {
    querySelectorAll(selector: string) {
      return selector.includes("button") ? [button] : [];
    },
  };
  const dialog = {
    hidden: false,
    style: {},
    parentElement: null,
    getAttribute(name: string) { return name === "role" ? "dialog" : null; },
    hasAttribute() { return false; },
    querySelectorAll() { return []; },
  };
  const documentFixture = {
    defaultView: null,
    querySelector(selector: string) {
      return selector.includes("ytd-watch-metadata") ? menu : null;
    },
    querySelectorAll(selector: string) {
      return selector.includes("yt-dialog-view-model") && dialogVisible ? [dialog] : [];
    },
  } as unknown as Document;
  const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);

  assert.equal(await env.openSaveDialog(), true);
});

test("YouTube browser environment creates a playlist through a detached dialog form", async () => {
  const originalInput = (globalThis as { HTMLInputElement?: unknown }).HTMLInputElement;
  const originalTextArea = (globalThis as { HTMLTextAreaElement?: unknown }).HTMLTextAreaElement;
  const originalInputEvent = (globalThis as { InputEvent?: unknown }).InputEvent;
  class FakeTextArea {
    hidden = false;
    style = {};
    tagName = "TEXTAREA";
    parentElement: unknown = null;
    private _value = "";
    onDispatch = () => {};
    set value(value: string) { this._value = value; }
    get value() { return this._value; }
    getAttribute() { return null; }
    hasAttribute() { return false; }
    dispatchEvent() { this.onDispatch(); return true; }
  }
  (globalThis as { HTMLTextAreaElement?: unknown }).HTMLTextAreaElement = FakeTextArea;
  (globalThis as { InputEvent?: unknown }).InputEvent = class {
    constructor(..._args: unknown[]) {}
  };
  let saveVisible = false;
  let formVisible = false;
  let createClicks = 0;
  let createEnabled = false;
  const saveButton = {
    hidden: false, style: {}, parentElement: null,
    getAttribute: (name: string) => name === "aria-label" ? "Save" : null,
    hasAttribute: () => false,
    click: () => { saveVisible = true; },
  };
  const newButton = {
    hidden: false, style: {}, parentElement: null,
    textContent: "New playlist",
    getAttribute: () => null,
    hasAttribute: () => false,
    click: () => { formVisible = true; },
  };
  const createButton = {
    hidden: false, style: {}, parentElement: null,
    textContent: "Create playlist",
    getAttribute: (name: string) => name === "aria-disabled" && !createEnabled ? "true" : null,
    hasAttribute: () => false,
    click: () => {
      if (!createEnabled) return;
      createClicks += 1;
      formVisible = false;
    },
  };
  const input = new FakeTextArea();
  input.onDispatch = () => queueMicrotask(() => { createEnabled = true; });
  const saveDialog = {
    hidden: false, style: {}, parentElement: null,
    getAttribute: () => null,
    hasAttribute: () => false,
    querySelector: () => null,
    querySelectorAll: (selector: string) => selector.includes("button") ? [newButton] : [],
    contains: () => false,
  };
  const formDialog = {
    hidden: false, style: {}, parentElement: null,
    getAttribute: () => null,
    hasAttribute: () => false,
    querySelector: (selector: string) => selector.includes("textarea") ? input : null,
    querySelectorAll: (selector: string) => selector.includes("textarea") ? [input] : [],
    contains: () => false,
  };
  const formShell = {
    hidden: false, style: {}, parentElement: null,
    getAttribute: (name: string) => name === "role" ? "dialog" : null,
    hasAttribute: () => false,
    querySelector: () => null,
    querySelectorAll: (selector: string) => selector.includes("button") ? [createButton] : [],
    contains: (candidate: unknown) => candidate === formDialog || candidate === input,
  };
  newButton.parentElement = saveDialog;
  formDialog.parentElement = formShell;
  createButton.parentElement = formShell;
  input.parentElement = formDialog;
  const menu = { querySelectorAll: () => [saveButton] };
  const documentFixture = {
    defaultView: null,
    querySelector: (selector: string) => selector.includes("ytd-watch-metadata") ? menu : null,
    querySelectorAll(selector: string) {
      const dialogs = [];
      if (selector.includes("ytd-add-to-playlist-renderer") && saveVisible) dialogs.push(saveDialog);
      if (selector.includes("yt-dialog-view-model") && formVisible) dialogs.push(formShell);
      if (selector.includes("yt-create-playlist-dialog-form-view-model") && formVisible) {
        dialogs.push(formDialog);
      }
      return dialogs;
    },
  } as unknown as Document;
  try {
    const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
    assert.equal(await env.openSaveDialog(), true);
    assert.equal(await env.createPlaylist("OpenBiliClaw"), true);
    assert.equal(input.value, "OpenBiliClaw");
    assert.equal(createClicks, 1);
  } finally {
    (globalThis as { HTMLInputElement?: unknown }).HTMLInputElement = originalInput;
    (globalThis as { HTMLTextAreaElement?: unknown }).HTMLTextAreaElement = originalTextArea;
    (globalThis as { InputEvent?: unknown }).InputEvent = originalInputEvent;
  }
});

test("YouTube browser environment identifies only renderer playlist id WL as Watch Later", () => {
  const watchLaterRow = {
    data: { playlistId: "WL" },
    getAttribute: () => null,
    querySelector(selector: string) {
      if (selector.includes("checkbox")) {
        return { getAttribute: (name: string) => name === "aria-checked" ? "true" : null, hasAttribute: () => false };
      }
      return null;
    },
  };
  const namedRow = { ...watchLaterRow, data: { playlistId: "PL-user" } };
  const dialog = {
    hasAttribute: () => false,
    querySelectorAll(selector: string) {
      if (selector === "ytd-playlist-add-to-option-renderer") return [namedRow, watchLaterRow];
      return [];
    },
  };
  const documentFixture = {
    querySelectorAll(selector: string) {
      if (selector.includes("ytd-add-to-playlist-renderer")) return [dialog];
      return [];
    },
  } as unknown as Document;
  const row = createYouTubeBrowserEnvironment(documentFixture, task.content_url).findWatchLater();
  assert.ok(row);
  assert.equal(row.isChecked(), true);
});

test("YouTube browser environment reads the exact title from renderer data before empty DOM text", () => {
  const row = {
    data: { playlistId: "PL-openbiliclaw", title: "OpenBiliClaw" },
    hidden: false,
    style: {},
    parentElement: null,
    getAttribute: () => null,
    hasAttribute: () => false,
    querySelector(selector: string) {
      if (selector.startsWith("#label")) return { textContent: "" };
      if (selector.includes("checkbox")) {
        return { getAttribute: () => "false", hasAttribute: () => false };
      }
      return null;
    },
    querySelectorAll: () => [],
  };
  const dialog = {
    hidden: false,
    style: {},
    parentElement: null,
    hasAttribute: () => false,
    getAttribute: () => null,
    querySelectorAll(selector: string) {
      return selector === "ytd-playlist-add-to-option-renderer" ? [row] : [];
    },
    contains(candidate: unknown) {
      return candidate === row || candidate === createRenderer || candidate === nestedSheet;
    },
  };
  const createRenderer = {
    hidden: false,
    style: {},
    parentElement: dialog,
    hasAttribute: () => false,
    getAttribute: () => null,
    querySelectorAll: () => [],
    contains: () => false,
  };
  const nestedSheet = {
    hidden: false,
    style: {},
    parentElement: dialog,
    hasAttribute: () => false,
    getAttribute: () => null,
    querySelectorAll: () => [],
    contains: () => false,
  };
  row.parentElement = dialog;
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      const roots = [];
      if (selector.includes("ytd-add-to-playlist-renderer")) roots.push(dialog);
      if (selector.includes("ytd-add-to-playlist-create-renderer")) roots.push(createRenderer);
      if (selector.includes("yt-sheet-view-model")) roots.push(nestedSheet);
      return roots;
    },
  } as unknown as Document;
  assert.equal(
    createYouTubeBrowserEnvironment(documentFixture, task.content_url)
      .findNamedPlaylists("OpenBiliClaw").length,
    1,
  );
});

test("YouTube browser environment reads exact modern toggleable playlist rows", () => {
  const modernRow = (playlistId: string, title: string, initiallyToggled: boolean) => {
    let toggled = initiallyToggled;
    let presentationClicks = 0;
    const presentationRow = {
      getAttribute: (name: string) => name === "role" ? "presentation" : null,
      click: () => { presentationClicks += 1; },
    };
    const actionButton = {
      getAttribute: (name: string) => name === "aria-pressed" ? (toggled ? "true" : "false") : null,
      click: () => { toggled = true; },
    };
    return {
    data: {
      defaultListItem: {
        listItemViewModel: {
          rendererContext: {
            commandContext: {
              onTap: { innertubeCommand: { playlistEditEndpoint: { playlistId } } },
            },
          },
        },
      },
    },
    hidden: false,
    style: {},
    parentElement: null,
    hasAttribute: () => false,
    getAttribute: () => null,
    querySelector(selector: string) {
      if (selector.includes("ytListItemViewModelTitle")) return { textContent: title };
      if (selector.includes("[role='menuitem'][aria-pressed]")) return actionButton;
      if (selector === "yt-list-item-view-model") return presentationRow;
      return null;
    },
    querySelectorAll: () => [],
    click() {},
    presentationClicks: () => presentationClicks,
    };
  };
  const favoriteRow = modernRow("PL-openbiliclaw", "OpenBiliClaw", false);
  const watchLaterRow = modernRow("PL-modern-without-wl-id", "Watch later", false);
  const dialog = {
    hidden: false,
    style: {},
    parentElement: null,
    hasAttribute: () => false,
    getAttribute: () => null,
    querySelectorAll(selector: string) {
      return selector === "toggleable-list-item-view-model" ? [favoriteRow, watchLaterRow] : [];
    },
  };
  favoriteRow.parentElement = dialog;
  watchLaterRow.parentElement = dialog;
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("ytd-add-to-playlist-renderer") ? [dialog] : [];
    },
  } as unknown as Document;
  const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
  const favorite = env.findNamedPlaylists("OpenBiliClaw")[0];
  assert.ok(favorite);
  assert.equal(favorite.isChecked(), false);
  favorite.click();
  assert.equal(favorite.isChecked(), true);
  const watchLater = env.findWatchLater();
  assert.equal(watchLater?.isChecked(), false);
  watchLater?.click();
  assert.equal(watchLater?.isChecked(), true);
  assert.equal(favoriteRow.presentationClicks(), 0);
  assert.equal(watchLaterRow.presentationClicks(), 0);
});

test("YouTube browser environment rejects WL-prefix hrefs and accepts exact parsed list WL", () => {
  const makeRow = (href: string) => ({
    data: {},
    getAttribute: () => null,
    querySelector(selector: string) {
      if (selector.includes("a[href*='list=WL']")) return { href };
      if (selector.includes("checkbox")) return { getAttribute: () => "false", hasAttribute: () => false };
      return null;
    },
    querySelectorAll(selector: string) {
      return selector === "a[href]" ? [{ href }] : [];
    },
  });
  const rows = [makeRow("https://www.youtube.com/playlist?list=WL123")];
  const dialog = {
    hasAttribute: () => false,
    parentElement: null,
    querySelectorAll(selector: string) {
      return selector === "ytd-playlist-add-to-option-renderer" ? rows : [];
    },
  };
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("ytd-add-to-playlist-renderer") ? [dialog] : [];
    },
  } as unknown as Document;
  const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
  assert.equal(env.findWatchLater(), null);
  rows[0] = makeRow("https://www.youtube.com/playlist?list=WL");
  assert.ok(env.findWatchLater());
});

test("YouTube browser environment ignores renderers hidden by an ancestor", () => {
  const hiddenAncestor = {
    hidden: true,
    hasAttribute(name: string) { return name === "hidden"; },
    getAttribute: () => null,
    parentElement: null,
  };
  const staleDialog = {
    hasAttribute: () => false,
    getAttribute: () => null,
    parentElement: hiddenAncestor,
    querySelectorAll(selector: string) {
      if (selector === "ytd-playlist-add-to-option-renderer") {
        return [{
          data: { playlistId: "WL" },
          getAttribute: () => null,
          parentElement: staleDialog,
          querySelector(query: string) {
            if (query.startsWith("#label")) return { textContent: "OpenBiliClaw" };
            if (query.includes("checkbox")) return { getAttribute: () => "true", hasAttribute: () => true };
            return null;
          },
          querySelectorAll: () => [],
        }];
      }
      return [];
    },
  };
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("ytd-add-to-playlist-renderer") ? [staleDialog] : [];
    },
  } as unknown as Document;
  const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
  assert.deepEqual(env.findNamedPlaylists("OpenBiliClaw"), []);
  assert.equal(env.findWatchLater(), null);
});

test("YouTube browser environment excludes hidden playlist rows from proof and mutation", () => {
  let clicks = 0;
  const hiddenNamedRow = {
    hidden: true,
    data: { playlistId: "PL-hidden" },
    hasAttribute(name: string) { return name === "hidden"; },
    getAttribute: () => null,
    parentElement: null,
    querySelector(selector: string) {
      if (selector.startsWith("#label")) return { textContent: "OpenBiliClaw" };
      if (selector.includes("checkbox")) {
        return {
          click() { clicks += 1; },
          getAttribute: (name: string) => name === "aria-checked" ? "true" : null,
          hasAttribute: () => true,
        };
      }
      return null;
    },
    querySelectorAll: () => [],
  };
  const hiddenWatchLaterRow = {
    ...hiddenNamedRow,
    data: { playlistId: "WL" },
    querySelector(selector: string) {
      if (selector.startsWith("#label")) return { textContent: "Watch later" };
      return hiddenNamedRow.querySelector(selector);
    },
  };
  const dialog = {
    hasAttribute: () => false,
    getAttribute: () => null,
    parentElement: null,
    querySelectorAll(selector: string) {
      return selector === "ytd-playlist-add-to-option-renderer"
        ? [hiddenNamedRow, hiddenWatchLaterRow]
        : [];
    },
  };
  hiddenNamedRow.parentElement = dialog;
  hiddenWatchLaterRow.parentElement = dialog;
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("ytd-add-to-playlist-renderer") ? [dialog] : [];
    },
  } as unknown as Document;
  const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
  const named = env.findNamedPlaylists("OpenBiliClaw");
  const watchLater = env.findWatchLater();
  assert.deepEqual(named, []);
  assert.equal(watchLater, null);
  named[0]?.click();
  watchLater?.click();
  assert.equal(clicks, 0);
});

test("YouTube rate fingerprint ignores unrelated alert mutation beside a stale rate toast", () => {
  let observerCallback: MutationCallback | null = null;
  const originalMutationObserver = globalThis.MutationObserver;
  class FakeMutationObserver {
    constructor(callback: MutationCallback) { observerCallback = callback; }
    observe() {}
    disconnect() {}
    takeRecords(): MutationRecord[] { return []; }
  }
  (globalThis as { MutationObserver?: typeof MutationObserver }).MutationObserver = FakeMutationObserver as unknown as typeof MutationObserver;
  const staleRateToast = {
    textContent: "Quota exceeded, try again later",
    hasAttribute: () => false,
    getAttribute: () => null,
    parentElement: null,
    closest: () => staleRateToast,
  };
  const unrelatedAlert = {
    textContent: "Playlist saved",
    hasAttribute: () => false,
    getAttribute: () => null,
    parentElement: null,
    closest: () => unrelatedAlert,
  };
  const unrelatedChild = {
    nodeType: 1,
    closest: () => null,
    querySelectorAll: () => [],
  };
  const broadCommonAncestor = {
    nodeType: 1,
    closest: () => null,
    querySelectorAll: () => [staleRateToast, unrelatedAlert],
  };
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("tp-yt-paper-toast") ? [staleRateToast, unrelatedAlert] : [];
    },
  } as unknown as Document;
  try {
    const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
    const before = env.rateLimitFingerprint();
    observerCallback?.([{ target: unrelatedAlert, type: "attributes", attributeName: "aria-hidden" } as unknown as MutationRecord], {} as MutationObserver);
    assert.equal(env.rateLimitFingerprint(), before);
    observerCallback?.([{
      target: broadCommonAncestor,
      type: "childList",
      addedNodes: [unrelatedChild],
    } as unknown as MutationRecord], {} as MutationObserver);
    assert.equal(env.rateLimitFingerprint(), before);
    env.dispose?.();
  } finally {
    (globalThis as { MutationObserver?: typeof MutationObserver }).MutationObserver = originalMutationObserver;
  }
});

test("YouTube rate fingerprint advances when a reused toast node is shown again", () => {
  let visible = false;
  let observerCallback: MutationCallback | null = null;
  const originalMutationObserver = globalThis.MutationObserver;
  class FakeMutationObserver {
    constructor(callback: MutationCallback) { observerCallback = callback; }
    observe() {}
    disconnect() {}
    takeRecords(): MutationRecord[] { return []; }
  }
  (globalThis as { MutationObserver?: typeof MutationObserver }).MutationObserver = FakeMutationObserver as unknown as typeof MutationObserver;
  const toast = {
    textContent: "Quota exceeded, try again later",
    hasAttribute(name: string) { return name === "hidden" && !visible; },
    getAttribute(name: string) { return name === "aria-hidden" ? (visible ? "false" : "true") : null; },
    parentElement: null,
    closest: () => toast,
  };
  const documentFixture = {
    defaultView: null,
    querySelectorAll(selector: string) {
      return selector.includes("tp-yt-paper-toast") ? [toast] : [];
    },
  } as unknown as Document;
  try {
    const env = createYouTubeBrowserEnvironment(documentFixture, task.content_url);
    const before = env.rateLimitFingerprint();
    visible = true;
    observerCallback?.([{ target: toast } as unknown as MutationRecord], {} as MutationObserver);
    const after = env.rateLimitFingerprint();
    assert.equal(before, "");
    assert.notEqual(after, before);
    env.dispose?.();
  } finally {
    (globalThis as { MutationObserver?: typeof MutationObserver }).MutationObserver = originalMutationObserver;
  }
});
