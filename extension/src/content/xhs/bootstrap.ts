/**
 * Pure helpers for extracting Xiaohongshu bootstrap profile notes.
 *
 * These functions read only Xiaohongshu-rendered state or already visible
 * DOM. They do not call Xiaohongshu APIs. Profile tab scrolling is only
 * enabled when a bootstrap task explicitly sets max_scroll_rounds > 0.
 */

export type XhsBootstrapScope = "saved" | "liked" | "xhs_history";

export interface XhsBootstrapNote {
  scope: XhsBootstrapScope;
  url: string;
  title: string;
  author: string;
  cover_url: string;
  note_id: string;
  xsec_token: string;
}

export interface ExtractBootstrapOptions {
  baseUrl?: string;
  maxItemsPerScope?: number;
}

export interface XhsBootstrapDebugStep {
  page_url: string;
  is_profile_page: boolean;
  has_initial_state: boolean;
  profile_content_ready?: boolean;
  requested_scopes: XhsBootstrapScope[];
  state_counts: Record<string, number>;
  dom_counts?: Record<string, number>;
  profile_url_found?: boolean;
  profile_url_source?: "document" | "state" | "";
  next_url_requested?: boolean;
  next_url_clicked?: boolean;
  tab_candidates?: Partial<Record<XhsBootstrapScope, boolean>>;
  tab_candidate_texts?: string[];
  scroll_candidates?: BootstrapScrollCandidateDebug[];
  tab_load_results?: Record<string, unknown>;
  // v0.3.48+: piggyback self user_id + nickname back to backend so the
  // ingest paths (xhs_task_result, _cache_xhs_notes) can filter out
  // the user's own notes from search / explore / saved-author content.
  self_info?: { user_id: string; nickname: string };
}

export interface BootstrapScrollDecision {
  currentCount: number;
  maxItemsPerScope: number;
  round: number;
  maxScrollRounds: number;
  stagnantRounds: number;
  maxStagnantScrollRounds?: number;
}

export interface XhsBootstrapDebugPayload {
  xhs_bootstrap: {
    steps: XhsBootstrapDebugStep[];
  };
}

export interface XhsBootstrapPartialPayloadInput {
  taskId: string;
  scope: XhsBootstrapScope;
  notes: XhsBootstrapNote[];
  scopeCounts: Record<string, number>;
  round: number;
}

export interface BootstrapScrollMetrics {
  target: string;
  scroll_top: number;
  scroll_height: number;
  client_height: number;
}

export interface BootstrapScrollCandidateDebug extends BootstrapScrollMetrics {
  overflow_y: string;
  note_count: number;
  score: number;
}

const DEFAULT_BASE_URL = "https://www.xiaohongshu.com";
const DEFAULT_MAX_ITEMS_PER_SCOPE = 20;
const MAX_BOOTSTRAP_SCROLL_ROUNDS = 30;
const DEFAULT_BOOTSTRAP_SCROLL_WAIT_MS = 1_200;
const MIN_BOOTSTRAP_SCROLL_WAIT_MS = 500;
const MAX_BOOTSTRAP_SCROLL_WAIT_MS = 5_000;
const DEFAULT_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 5;
const MIN_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 1;
const MAX_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS = 10;
const BOOTSTRAP_SCOPES: XhsBootstrapScope[] = ["saved", "liked", "xhs_history"];
const OWN_PROFILE_EXACT_SELECTORS = [
  ".main-container .user .link-wrapper a.link-wrapper[href*='/user/profile/']",
  ".main-container .user a[href*='/user/profile/']",
  "nav .user a[href*='/user/profile/']",
  "aside .user a[href*='/user/profile/']",
];
const ANCHOR_SELECTOR = 'a[href*="/explore/"], a[href*="/discovery/item/"]';
const SCROLL_CONTAINER_SELECTOR = [
  ".feeds-container",
  ".feeds-page",
  ".feeds-list",
  ".note-list",
  ".notes-container",
  ".waterfall",
  ".masonry",
  "[class*='feeds']",
  "[class*='Feeds']",
  "[class*='waterfall']",
  "[class*='Waterfall']",
  "[class*='masonry']",
  "[class*='Masonry']",
  "[class*='note-list']",
  "[class*='NoteList']",
  "[class*='scroll']",
  "[class*='Scroll']",
].join(", ");

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function elementClassName(element: Element): string {
  const className = (element as { className?: unknown }).className;
  if (typeof className === "string") return className.trim();
  if (isRecord(className) && typeof className.baseVal === "string") {
    return className.baseVal.trim();
  }
  return "";
}

export function describeBootstrapScrollTarget(element: Element): string {
  const tag = element.tagName?.toLowerCase?.() || "element";
  const id = (element as HTMLElement).id ? `#${(element as HTMLElement).id}` : "";
  const className = elementClassName(element);
  const classes = className
    ? `.${className.split(/\s+/).filter(Boolean).slice(0, 3).join(".")}`
    : "";
  return `${tag}${id}${classes}`;
}

export function readBootstrapScrollMetrics(element: HTMLElement): BootstrapScrollMetrics {
  return {
    target: describeBootstrapScrollTarget(element),
    scroll_top: Math.max(0, Math.floor(element.scrollTop || 0)),
    scroll_height: Math.max(0, Math.floor(element.scrollHeight || 0)),
    client_height: Math.max(0, Math.floor(element.clientHeight || 0)),
  };
}

function countNoteAnchors(element: Element): number {
  try {
    return element.querySelectorAll(ANCHOR_SELECTOR).length;
  } catch {
    return 0;
  }
}

function readOverflowY(element: HTMLElement): string {
  const win = element.ownerDocument?.defaultView;
  if (!win?.getComputedStyle) return "unknown";
  return win.getComputedStyle(element).overflowY.toLowerCase();
}

function hasScrollableOverflowStyle(element: HTMLElement): boolean {
  const overflowY = readOverflowY(element);
  if (overflowY === "unknown") return true;
  return overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay";
}

function scrollContainerScore(element: HTMLElement): number {
  const clientHeight = element.clientHeight || 0;
  if (clientHeight < 120) return 0;
  if (!hasScrollableOverflowStyle(element)) return 0;

  const overflow = Math.max(0, (element.scrollHeight || 0) - (element.clientHeight || 0));
  if (overflow < 120) return 0;

  const descriptor = `${element.id || ""} ${elementClassName(element)}`.toLowerCase();
  if (
    descriptor.includes("channel-list") ||
    descriptor.includes("side-bar") ||
    descriptor.includes("sidebar")
  ) {
    return 0;
  }
  const keywordScore =
    descriptor.includes("feed") ||
    descriptor.includes("waterfall") ||
    descriptor.includes("masonry") ||
    descriptor.includes("note")
      ? 1_000
      : 0;
  return overflow + countNoteAnchors(element) * 2_000 + keywordScore;
}

export function findBootstrapScrollContainer(doc: Document): HTMLElement | null {
  const candidates = collectBootstrapScrollCandidates(doc, Number.POSITIVE_INFINITY);

  if (candidates.length === 0) return null;

  let bestElement: HTMLElement | null = null;
  let bestScore = 0;
  const elements = bootstrapScrollCandidateElements(doc);
  for (const element of elements) {
    const score = scrollContainerScore(element);
    if (score > bestScore) {
      bestElement = element;
      bestScore = score;
    }
  }
  return bestElement;
}

function bootstrapScrollCandidateElements(doc: Document): HTMLElement[] {
  const seen = new Set<HTMLElement>();
  const candidates: HTMLElement[] = [];
  const addCandidate = (element: HTMLElement) => {
    if (seen.has(element)) return;
    seen.add(element);
    candidates.push(element);
  };

  try {
    doc.querySelectorAll<HTMLElement>(SCROLL_CONTAINER_SELECTOR).forEach(addCandidate);
    doc.querySelectorAll<HTMLElement>("body *").forEach(addCandidate);
  } catch {
    return [];
  }
  return candidates;
}

export function collectBootstrapScrollCandidates(
  doc: Document,
  limit: number = 10,
): BootstrapScrollCandidateDebug[] {
  return bootstrapScrollCandidateElements(doc)
    .map((element) => {
      const metrics = readBootstrapScrollMetrics(element);
      return {
        ...metrics,
        overflow_y: readOverflowY(element),
        note_count: countNoteAnchors(element),
        score: scrollContainerScore(element),
      };
    })
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(0, Math.floor(limit)));
}

function unwrapReactive(value: unknown): unknown {
  let current = value;
  const seen = new Set<unknown>();
  while (isRecord(current) && !seen.has(current)) {
    seen.add(current);
    if ("_rawValue" in current) {
      current = current._rawValue;
      continue;
    }
    if ("_value" in current) {
      current = current._value;
      continue;
    }
    if ("value" in current && Object.keys(current).length <= 3) {
      current = current.value;
      continue;
    }
    break;
  }
  return current;
}

function getPath(value: unknown, path: string[]): unknown {
  let current = unwrapReactive(value);
  for (const part of path) {
    if (!isRecord(current)) return undefined;
    current = unwrapReactive(current[part]);
  }
  return current;
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    const raw = unwrapReactive(value);
    if (typeof raw === "string" && raw.trim()) return raw.trim();
    if (typeof raw === "number") return String(raw);
  }
  return "";
}

function firstPathString(value: unknown, paths: string[][]): string {
  for (const path of paths) {
    const found = firstString(getPath(value, path));
    if (found) return found;
  }
  return "";
}

function flattenNotes(value: unknown): unknown[] {
  const raw = unwrapReactive(value);
  if (Array.isArray(raw)) {
    return raw.flatMap((item) => flattenNotes(item));
  }
  if (!isRecord(raw)) return [];

  for (const key of ["notes", "items", "list", "data"]) {
    const nested = unwrapReactive(raw[key]);
    if (Array.isArray(nested)) return flattenNotes(nested);
  }
  return [raw];
}

function notesForScope(state: unknown, scope: XhsBootstrapScope): unknown[] {
  const userGroups = unwrapReactive(getPath(state, ["user", "notes"]));
  if (Array.isArray(userGroups)) {
    if (scope === "saved") return flattenNotes(userGroups[1]);
    if (scope === "liked") return flattenNotes(userGroups[2]);
  }

  if (scope === "saved") {
    return [
      ...flattenNotes(getPath(state, ["saved", "notes"])),
      ...flattenNotes(getPath(state, ["collect", "notes"])),
      ...flattenNotes(getPath(state, ["collections", "notes"])),
    ];
  }

  if (scope === "liked") {
    return [
      ...flattenNotes(getPath(state, ["liked", "notes"])),
      ...flattenNotes(getPath(state, ["likes", "notes"])),
    ];
  }

  return [
    ...flattenNotes(getPath(state, ["history", "notes"])),
    ...flattenNotes(getPath(state, ["footprint", "notes"])),
    ...flattenNotes(getPath(state, ["browseHistory", "notes"])),
    ...flattenNotes(getPath(state, ["browsingHistory", "notes"])),
  ];
}

function noteIdFromUrl(url: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url, DEFAULT_BASE_URL);
    const parts = parsed.pathname.split("/").filter(Boolean);
    return parts.at(-1) ?? "";
  } catch {
    return "";
  }
}

function buildNoteUrl(noteId: string, xsecToken: string, baseUrl: string): string {
  const url = new URL(`/explore/${noteId}`, baseUrl || DEFAULT_BASE_URL);
  if (xsecToken) url.searchParams.set("xsec_token", xsecToken);
  return url.toString();
}

function normalizeUrl(url: string, baseUrl: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url, baseUrl || DEFAULT_BASE_URL);
    if (
      !parsed.pathname.startsWith("/explore/") &&
      !parsed.pathname.startsWith("/discovery/item/")
    ) {
      return "";
    }
    const xsecToken = parsed.searchParams.get("xsec_token") ?? "";
    const keptParams = new URLSearchParams();
    if (xsecToken) keptParams.set("xsec_token", xsecToken);
    const query = keptParams.toString();
    return `${parsed.origin}${parsed.pathname}${query ? `?${query}` : ""}`;
  } catch {
    return "";
  }
}

function normalizeProfileUrl(url: string, baseUrl: string): string {
  if (!url) return "";
  try {
    const parsed = new URL(url, baseUrl || DEFAULT_BASE_URL);
    if (!parsed.pathname.startsWith("/user/profile/")) return "";
    const keptParams = new URLSearchParams();
    const xsecToken = parsed.searchParams.get("xsec_token") ?? "";
    const xsecSource = parsed.searchParams.get("xsec_source") ?? "";
    if (xsecToken) keptParams.set("xsec_token", xsecToken);
    if (xsecSource) keptParams.set("xsec_source", xsecSource);
    const query = keptParams.toString();
    return `${parsed.origin}${parsed.pathname.replace(/\/$/, "")}${query ? `?${query}` : ""}`;
  } catch {
    return "";
  }
}

function anchorHref(anchor: HTMLAnchorElement): string {
  return anchor.getAttribute("href") || anchor.href || "";
}

function normalizedAnchorProfileUrl(anchor: HTMLAnchorElement, baseUrl: string): string {
  return normalizeProfileUrl(anchorHref(anchor), baseUrl);
}

function isOwnProfileNavAnchor(anchor: HTMLAnchorElement): boolean {
  const text = anchor.textContent?.trim() ?? "";
  const aria = anchor.getAttribute("aria-label")?.trim() ?? "";
  const title = anchor.getAttribute("title")?.trim() ?? "";
  const className = String(anchor.className ?? "");
  return (
    text === "我" ||
    aria === "我" ||
    title === "我" ||
    (className.includes("link-wrapper") && anchor.closest(".user, nav, aside") !== null)
  );
}

function firstBoolean(...values: unknown[]): boolean | null {
  for (const value of values) {
    const raw = unwrapReactive(value);
    if (typeof raw === "boolean") return raw;
    if (typeof raw === "string") {
      if (raw === "true") return true;
      if (raw === "false") return false;
    }
  }
  return null;
}

export function extractOwnProfileUrlFromState(
  state: unknown,
  baseUrl: string = DEFAULT_BASE_URL,
): string {
  const loggedIn = firstBoolean(getPath(state, ["user", "loggedIn"]));
  if (loggedIn !== true) return "";

  const userId = firstPathString(state, [
    ["user", "userInfo", "userId"],
    ["user", "userInfo", "user_id"],
    ["user", "userInfo", "id"],
    ["user", "userPageData", "basicInfo", "userId"],
    ["user", "userPageData", "basicInfo", "user_id"],
  ]);
  if (!userId) return "";
  return normalizeProfileUrl(`/user/profile/${userId}`, baseUrl);
}

export interface XhsSelfInfo {
  user_id: string;
  nickname: string;
}

/**
 * Extract self user_id + nickname from XHS profile-page state.
 *
 * Used to fingerprint the logged-in user so the backend can filter
 * out the user's own notes from XHS search / explore / saved-author
 * paths — they leak into the recommendation pool otherwise (XHS's
 * own search / explore feed both readily return self-authored notes).
 *
 * Returns ``null`` when the page hasn't exposed user state yet, or
 * when neither id nor nickname can be read (no value vs partial is
 * safer — the backend treats absent self-info as "don't filter").
 */
export function extractSelfInfoFromState(state: unknown): XhsSelfInfo | null {
  const loggedIn = firstBoolean(getPath(state, ["user", "loggedIn"]));
  if (loggedIn !== true) return null;
  const userId = firstPathString(state, [
    ["user", "userInfo", "userId"],
    ["user", "userInfo", "user_id"],
    ["user", "userInfo", "id"],
    ["user", "userPageData", "basicInfo", "userId"],
    ["user", "userPageData", "basicInfo", "user_id"],
  ]);
  const nickname = firstPathString(state, [
    ["user", "userInfo", "nickname"],
    ["user", "userInfo", "nickName"],
    ["user", "userInfo", "nick_name"],
    ["user", "userInfo", "name"],
    ["user", "userPageData", "basicInfo", "nickname"],
    ["user", "userPageData", "basicInfo", "nickName"],
  ]);
  if (!userId && !nickname) return null;
  return { user_id: userId, nickname };
}

export function extractOwnProfileUrlFromDocument(doc: Document, baseUrl: string): string {
  const anchor = findOwnProfileAnchorFromDocument(doc, baseUrl);
  return anchor ? normalizedAnchorProfileUrl(anchor, baseUrl) : "";
}

export function findOwnProfileAnchorFromDocument(
  doc: Document,
  baseUrl: string,
): HTMLAnchorElement | null {
  for (const selector of OWN_PROFILE_EXACT_SELECTORS) {
    const anchor = doc.querySelector<HTMLAnchorElement>(selector);
    const url = anchor ? normalizedAnchorProfileUrl(anchor, baseUrl) : "";
    if (url && anchor) return anchor;
  }

  const anchors = Array.from(doc.querySelectorAll<HTMLAnchorElement>("a[href*='/user/profile/']"));
  for (const anchor of anchors) {
    if (!isOwnProfileNavAnchor(anchor)) continue;
    if (normalizedAnchorProfileUrl(anchor, baseUrl)) return anchor;
  }

  return null;
}

function dispatchOwnProfileMouseEvent(
  anchor: HTMLAnchorElement,
  win: Window,
  type: "mousedown" | "mouseup",
): void {
  try {
    const MouseEventCtor =
      (win as unknown as { MouseEvent?: typeof MouseEvent }).MouseEvent ??
      (typeof MouseEvent === "function" ? MouseEvent : null);
    if (!MouseEventCtor) throw new Error("MouseEvent unavailable");
    anchor.dispatchEvent(
      new MouseEventCtor(type, { bubbles: true, cancelable: true, view: win }),
    );
  } catch {
    try {
      anchor.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
    } catch {
      // Ignore synthetic event failures; anchor.click() below is the real fallback.
    }
  }
}

export function clickOwnProfileAnchorFromDocument(
  doc: Document,
  baseUrl: string,
  win: Window,
): { url: string; clicked: boolean } {
  const anchor = findOwnProfileAnchorFromDocument(doc, baseUrl);
  if (!anchor) return { url: "", clicked: false };

  const url = normalizedAnchorProfileUrl(anchor, baseUrl);
  if (!url) return { url: "", clicked: false };

  try {
    anchor.scrollIntoView({ block: "center", inline: "center" });
  } catch {
    // Non-critical; the click can still activate the link.
  }

  dispatchOwnProfileMouseEvent(anchor, win, "mousedown");
  dispatchOwnProfileMouseEvent(anchor, win, "mouseup");
  try {
    anchor.click();
  } catch {
    return { url, clicked: false };
  }
  return { url, clicked: true };
}

function normalizeStateNote(
  rawNote: unknown,
  scope: XhsBootstrapScope,
  baseUrl: string,
): XhsBootstrapNote | null {
  if (!isRecord(rawNote)) return null;
  const title = firstPathString(rawNote, [
    ["title"],
    ["display_title"],
    ["displayTitle"],
    ["desc"],
    ["name"],
    ["noteCard", "display_title"],
    ["noteCard", "displayTitle"],
    ["noteCard", "title"],
    ["note_card", "display_title"],
    ["note_card", "displayTitle"],
    ["note_card", "title"],
  ]);
  const noteId = firstPathString(rawNote, [
    ["note_id"],
    ["noteId"],
    ["id"],
    ["noteCard", "note_id"],
    ["noteCard", "noteId"],
    ["noteCard", "id"],
    ["note_card", "note_id"],
    ["note_card", "id"],
  ]);
  const xsecToken = firstPathString(rawNote, [
    ["xsec_token"],
    ["xsecToken"],
    ["xsec"],
    ["noteCard", "xsec_token"],
    ["noteCard", "xsecToken"],
    ["note_card", "xsec_token"],
  ]);
  const explicitUrl = normalizeUrl(
    firstPathString(rawNote, [
      ["url"],
      ["link"],
      ["href"],
      ["shareUrl"],
      ["share_url"],
      ["noteCard", "url"],
      ["note_card", "url"],
    ]),
    baseUrl,
  );
  const url = explicitUrl || (noteId ? buildNoteUrl(noteId, xsecToken, baseUrl) : "");
  const normalizedNoteId = noteId || noteIdFromUrl(url);

  const author = firstPathString(rawNote, [
    ["author"],
    ["nickname"],
    ["user", "nickname"],
    ["user", "nickName"],
    ["user", "nick_name"],
    ["user", "name"],
    ["user_info", "nickname"],
    ["userInfo", "nickname"],
    ["noteCard", "user", "nickname"],
    ["noteCard", "user", "nickName"],
    ["note_card", "user", "nickname"],
    ["note_card", "user", "nickName"],
  ]);
  const coverUrl = firstPathString(rawNote, [
    ["cover_url"],
    ["coverUrl"],
    ["cover", "url"],
    ["cover", "urlDefault"],
    ["cover", "src"],
    ["image", "url"],
    ["images_list", "0", "url"],
    ["imageList", "0", "url"],
    ["noteCard", "cover", "url"],
    ["noteCard", "cover", "urlDefault"],
    ["note_card", "cover", "url"],
    ["note_card", "cover", "urlDefault"],
  ]);

  if (!title && !url) return null;

  return {
    scope,
    url,
    title,
    author,
    cover_url: coverUrl,
    note_id: normalizedNoteId,
    xsec_token: xsecToken,
  };
}

export function normalizeBootstrapScopes(scopes?: readonly string[]): XhsBootstrapScope[] {
  if (!scopes?.length) return [...BOOTSTRAP_SCOPES];
  const out: XhsBootstrapScope[] = [];
  for (const scope of scopes) {
    if (
      (scope === "saved" || scope === "liked" || scope === "xhs_history") &&
      !out.includes(scope)
    ) {
      out.push(scope);
    }
  }
  return out.length ? out : [...BOOTSTRAP_SCOPES];
}

export function extractBootstrapNotesFromState(
  state: unknown,
  scopes?: readonly string[],
  options: ExtractBootstrapOptions = {},
): XhsBootstrapNote[] {
  const requestedScopes = normalizeBootstrapScopes(scopes);
  const baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
  const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
  const notes: XhsBootstrapNote[] = [];

  for (const scope of requestedScopes) {
    const seen = new Set<string>();
    for (const raw of notesForScope(state, scope)) {
      if (notes.filter((note) => note.scope === scope).length >= maxItems) break;
      const note = normalizeStateNote(raw, scope, baseUrl);
      if (!note) continue;
      const key = note.note_id || note.url || note.title;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      notes.push(note);
    }
  }

  return notes;
}

export function countBootstrapStateNotesByScope(
  state: unknown,
  scopes?: readonly string[],
  options: ExtractBootstrapOptions = {},
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const scope of normalizeBootstrapScopes(scopes)) {
    counts[scope] = extractBootstrapNotesFromState(state, [scope], options).length;
  }
  return counts;
}

export function buildBootstrapDebugPayload(
  step: XhsBootstrapDebugStep,
): XhsBootstrapDebugPayload {
  return { xhs_bootstrap: { steps: [step] } };
}

export function buildBootstrapPartialPayload(
  input: XhsBootstrapPartialPayloadInput,
): {
  task_id: string;
  status: "partial";
  urls: string[];
  notes: XhsBootstrapNote[];
  scope_counts: Record<string, number>;
  debug: { xhs_bootstrap_partial: { scope: XhsBootstrapScope; round: number; count: number } };
} {
  return {
    task_id: input.taskId,
    status: "partial",
    urls: [...new Set(input.notes.map((note) => note.url).filter(Boolean))],
    notes: input.notes,
    scope_counts: input.scopeCounts,
    debug: {
      xhs_bootstrap_partial: {
        scope: input.scope,
        round: input.round,
        count: input.notes.length,
      },
    },
  };
}

export function bootstrapProfileTabLabels(scope: XhsBootstrapScope): string[] {
  if (scope === "saved") return ["收藏"];
  if (scope === "liked") return ["赞过", "喜欢", "点赞"];
  return [];
}

export function normalizeBootstrapScrollRounds(rounds?: number): number {
  if (!Number.isFinite(rounds) || rounds === undefined || rounds <= 0) return 0;
  return Math.min(Math.floor(rounds), MAX_BOOTSTRAP_SCROLL_ROUNDS);
}

export function normalizeBootstrapScrollWaitMs(waitMs?: number): number {
  if (!Number.isFinite(waitMs) || waitMs === undefined) return DEFAULT_BOOTSTRAP_SCROLL_WAIT_MS;
  return Math.min(
    Math.max(Math.floor(waitMs), MIN_BOOTSTRAP_SCROLL_WAIT_MS),
    MAX_BOOTSTRAP_SCROLL_WAIT_MS,
  );
}

export function normalizeBootstrapStagnantScrollRounds(rounds?: number): number {
  if (!Number.isFinite(rounds) || rounds === undefined) {
    return DEFAULT_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS;
  }
  return Math.min(
    Math.max(Math.floor(rounds), MIN_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS),
    MAX_BOOTSTRAP_STAGNANT_SCROLL_ROUNDS,
  );
}

export function bootstrapScrollShouldContinue(decision: BootstrapScrollDecision): boolean {
  if (decision.maxScrollRounds <= 0) return false;
  if (decision.currentCount >= decision.maxItemsPerScope) return false;
  if (decision.round >= decision.maxScrollRounds) return false;
  return decision.stagnantRounds < normalizeBootstrapStagnantScrollRounds(
    decision.maxStagnantScrollRounds,
  );
}

export function extractBootstrapNotesFromDocument(
  doc: Document,
  scope: XhsBootstrapScope,
  baseUrl: string,
  options: ExtractBootstrapOptions = {},
): XhsBootstrapNote[] {
  const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
  const notes: XhsBootstrapNote[] = [];
  const seen = new Set<string>();
  const anchors = doc.querySelectorAll<HTMLAnchorElement>(ANCHOR_SELECTOR);

  anchors.forEach((anchor) => {
    if (notes.length >= maxItems) return;
    const url = normalizeUrl(anchor.href, baseUrl);
    if (!url) return;
    const noteId = noteIdFromUrl(url);
    const key = noteId || url;
    if (!key || seen.has(key)) return;
    seen.add(key);

    const card =
      anchor.closest(".note-item, section, [class*='note'], [class*='card']") ??
      anchor;
    const titleEl = card.querySelector(
      ".title, .note-title, [class*='title'] span, [class*='title']",
    );
    const authorEl = card.querySelector(
      ".author-wrapper .name, .author .name, .user-name, [class*='author'] .name, .nickname",
    );
    const coverImg = card.querySelector(
      "img.cover, .cover img, img[src*='xhscdn'], img[src*='sns-img'], img",
    );
    const parsed = new URL(url);

    notes.push({
      scope,
      url,
      title: titleEl?.textContent?.trim() || anchor.title || "",
      author: authorEl?.textContent?.trim() || "",
      cover_url:
        coverImg?.getAttribute("src") || coverImg?.getAttribute("data-src") || "",
      note_id: noteId,
      xsec_token: parsed.searchParams.get("xsec_token") ?? "",
    });
  });

  return notes.filter((note) => note.title || note.url);
}

export function extractBootstrapNotesFromProfileDocument(
  doc: Document,
  scope: XhsBootstrapScope,
  baseUrl: string,
  options: ExtractBootstrapOptions = {},
): XhsBootstrapNote[] {
  if (scope === "xhs_history") return [];
  return extractBootstrapNotesFromDocument(doc, scope, baseUrl, options);
}

export function hasBootstrapProfileContent(doc: Document): boolean {
  try {
    if (extractBootstrapStateFromDocument(doc) !== null) return true;
  } catch {
    // Some tests and early-loading documents do not expose the full Document API yet.
  }

  const text = doc.body?.textContent?.replace(/\s+/g, "") ?? "";
  if (
    text.includes("收藏") ||
    text.includes("赞过") ||
    text.includes("喜欢") ||
    text.includes("点赞")
  ) {
    return true;
  }

  try {
    return doc.querySelector(ANCHOR_SELECTOR) !== null;
  } catch {
    return false;
  }
}

export function profileDocumentNoteKeys(doc: Document, baseUrl: string): string[] {
  return extractBootstrapNotesFromDocument(doc, "saved", baseUrl).map(
    (note) => note.note_id || note.url || note.title,
  );
}

export function hasDifferentProfileDocumentNotes(
  notes: readonly XhsBootstrapNote[],
  previousKeys: readonly string[],
): boolean {
  if (notes.length === 0) return false;
  if (previousKeys.length === 0) return true;
  const previous = new Set(previousKeys);
  return notes.some((note) => !previous.has(note.note_id || note.url || note.title));
}

export function limitBootstrapNewNotesToRemainingCapacity(
  currentNotes: readonly XhsBootstrapNote[],
  newNotes: readonly XhsBootstrapNote[],
  maxItemsPerScope: number,
): XhsBootstrapNote[] {
  if (newNotes.length === 0) return [];
  const scope = newNotes[0].scope;
  const currentCount = currentNotes.filter((note) => note.scope === scope).length;
  const remaining = Math.max(0, Math.floor(maxItemsPerScope) - currentCount);
  if (remaining <= 0) return [];
  return newNotes.slice(0, remaining);
}

export function isActiveBootstrapProfileTab(tab: HTMLElement): boolean {
  const selected = tab.getAttribute("aria-selected");
  if (selected === "true") return true;
  const className = String(tab.className ?? "").toLowerCase();
  return (
    className.includes("active") ||
    className.includes("selected") ||
    className.includes("current")
  );
}

function sliceBalancedObject(source: string, start: number): string | null {
  let depth = 0;
  let quote: string | null = null;
  let escaped = false;
  for (let i = start; i < source.length; i += 1) {
    const ch = source[i];
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === "{") depth += 1;
    if (ch === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  return null;
}

function parseInitialStateText(text: string): unknown | null {
  const markerIndex = text.indexOf("__INITIAL_STATE__");
  if (markerIndex < 0) return null;
  const objectStart = text.indexOf("{", markerIndex);
  if (objectStart < 0) return null;
  const objectText = sliceBalancedObject(text, objectStart);
  if (!objectText) return null;
  try {
    return JSON.parse(objectText);
  } catch {
    return null;
  }
}

export function extractBootstrapStateFromDocument(doc: Document): unknown | null {
  const win = doc.defaultView as (Window & { __INITIAL_STATE__?: unknown }) | null;
  if (win?.__INITIAL_STATE__) return win.__INITIAL_STATE__;

  const scripts = doc.querySelectorAll<HTMLScriptElement>("script");
  for (const script of Array.from(scripts)) {
    const parsed = parseInitialStateText(script.textContent ?? "");
    if (parsed) return parsed;
  }
  return null;
}

export function mergeBootstrapNotes(
  notes: Iterable<XhsBootstrapNote>,
  scopes?: readonly string[],
  options: ExtractBootstrapOptions = {},
): XhsBootstrapNote[] {
  const requestedScopes = normalizeBootstrapScopes(scopes);
  const maxItems = Math.max(1, options.maxItemsPerScope ?? DEFAULT_MAX_ITEMS_PER_SCOPE);
  const counts = new Map<XhsBootstrapScope, number>();
  const seen = new Set<string>();
  const out: XhsBootstrapNote[] = [];

  for (const note of notes) {
    if (!requestedScopes.includes(note.scope)) continue;
    const count = counts.get(note.scope) ?? 0;
    if (count >= maxItems) continue;
    const key = `${note.scope}:${note.note_id || note.url || note.title}`;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    counts.set(note.scope, count + 1);
    out.push(note);
  }

  return out;
}
