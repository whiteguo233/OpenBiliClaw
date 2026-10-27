/**
 * TikTok content-script executor — types + pure helpers.
 *
 * TikTok account-behavior bootstrap tasks (contract
 * docs/platform-source-contract.tiktok.toml, capability route
 * bootstrap = "extension:browser-task:session-read-only"). Module
 * isolation: zero imports from extension/src/content/dy/ — the tt/
 * tree owns its own lifecycle so divergence is allowed.
 *
 * Unlike Douyin there is no MAIN-world fetch-tap: TikTok's signed web
 * API requires X-Bogus-style request signing we deliberately do not
 * reconstruct, so collection is DOM-only against the pages the user's
 * own signed-in session renders. All harvesting code is split between
 * ./dom-extractor.ts (pure Document → rows) and ./bootstrap.ts
 * (universal-data login/identity probes); this file holds the shared
 * wire types and the pure state machines both sides use.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type TiktokScope = "tt_like" | "tt_collect" | "tt_history" | "tt_follow";

export const TIKTOK_SCOPES: readonly TiktokScope[] = [
  "tt_like",
  "tt_collect",
  "tt_history",
  "tt_follow",
] as const;

/**
 * One normalized account-behavior row handed to the backend, which owns
 * event mapping (tt_like → like, tt_collect → favorite, tt_history →
 * view, tt_follow → follow). Only bounded normalized fields cross the
 * task boundary — never raw response bodies or credential material
 * (contract [task] buffer clause).
 */
export interface TiktokBootstrapItem {
  scope: TiktokScope;
  /** TikTok numeric video id; "" for tt_follow account rows. */
  video_id: string;
  /** Canonical https://www.tiktok.com/@<handle>/video/<id> or /@<handle>. */
  url: string;
  title: string;
  /** Author handle without the leading "@"; the followed account on tt_follow rows. */
  author_handle: string;
  /** Display name for the backend's event context (handle when no nickname is known). */
  author: string;
  /** Follow-scope identity key consumed by the backend's event mapping (handle — DOM has no secUid). */
  creator_sec_uid?: string;
  cover_url: string;
  view_count?: number;
  like_count?: number;
  collect_count?: number;
  comment_count?: number;
  share_count?: number;
}

/** Content script → dispatcher wire shape (TIKTOK_SCOPE_RESULT). */
export interface TiktokScopeResult {
  task_id: string;
  scope: TiktokScope;
  items: TiktokBootstrapItem[];
  scope_count: number;
  viewer_handle?: string;
  viewer_sec_uid?: string;
  status: "ok" | "empty" | "degraded" | "failed";
  error?: string;
  debug?: Record<string, unknown>;
}

/** Dispatcher → content script wire shape (TIKTOK_SCOPE_EXECUTE). */
export interface TiktokScopeExecuteMessage {
  task_id: string;
  scope: TiktokScope;
  max_items: number;
  max_scroll_rounds: number;
  max_stagnant_scroll_rounds: number;
}

export function isValidTiktokScopeExecuteMessage(
  value: unknown,
): value is TiktokScopeExecuteMessage {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (typeof v.task_id !== "string" || !v.task_id) return false;
  if (!TIKTOK_SCOPES.includes(v.scope as TiktokScope)) return false;
  for (const key of ["max_items", "max_scroll_rounds", "max_stagnant_scroll_rounds"]) {
    const n = v[key];
    if (typeof n !== "number" || !Number.isSafeInteger(n) || n < 0) return false;
  }
  if ((v.max_items as number) > 300 || (v.max_scroll_rounds as number) > 30) return false;
  return true;
}

// ---------------------------------------------------------------------------
// TiktokItemSink — accumulates DOM harvests with dedup + cap
// ---------------------------------------------------------------------------

interface SinkOptions {
  maxItems: number;
}

function itemKey(item: TiktokBootstrapItem): string {
  // tt_follow rows identify by author handle; video scopes by video id.
  // Scope is part of the key so the same video can legitimately appear
  // under both tt_like and tt_history.
  const id = item.scope === "tt_follow" ? item.author_handle : item.video_id;
  return `${item.scope}:${id}`;
}

export class TiktokItemSink {
  private readonly maxItems: number;
  private readonly seenKeys = new Set<string>();
  private readonly items: TiktokBootstrapItem[] = [];

  constructor(opts: SinkOptions) {
    this.maxItems = Math.max(0, Math.floor(opts.maxItems));
  }

  /**
   * Ingest a batch and return only genuinely new rows (not duplicates,
   * not over the cap). Callers forward exactly these so partial payloads
   * carry fresh data only.
   */
  ingest(items: TiktokBootstrapItem[]): TiktokBootstrapItem[] {
    const newOnes: TiktokBootstrapItem[] = [];
    for (const item of items) {
      if (!item || !TIKTOK_SCOPES.includes(item.scope)) continue;
      const key = itemKey(item);
      if (key.endsWith(":")) continue; // empty identity
      if (this.seenKeys.has(key)) continue;
      if (this.items.length >= this.maxItems) continue;
      this.seenKeys.add(key);
      this.items.push(item);
      newOnes.push(item);
    }
    return newOnes;
  }

  count(): number {
    return this.items.length;
  }

  snapshot(): TiktokBootstrapItem[] {
    return [...this.items];
  }
}

// ---------------------------------------------------------------------------
// Scroll-loop stopping condition
// ---------------------------------------------------------------------------

const MAX_SCROLL_ROUNDS = 30;

export interface TiktokScrollContinueOptions {
  currentCount: number;
  maxItems: number;
  round: number;
  maxScrollRounds: number;
  stagnantRounds: number;
  maxStagnantScrollRounds: number;
}

export function ttShouldContinueScroll(opts: TiktokScrollContinueOptions): boolean {
  if (opts.currentCount >= opts.maxItems) return false;
  if (opts.round >= opts.maxScrollRounds) return false;
  if (opts.stagnantRounds >= opts.maxStagnantScrollRounds) return false;
  return true;
}

/** Clamp max_scroll_rounds to [0, 30]; invalid input means "no scrolling". */
export function normalizeTiktokScrollRounds(rounds: number | undefined): number {
  if (rounds === undefined) return 0;
  if (!Number.isFinite(rounds)) return 0;
  const floored = Math.floor(rounds);
  if (floored <= 0) return 0;
  return Math.min(floored, MAX_SCROLL_ROUNDS);
}

// ---------------------------------------------------------------------------
// Completion classification
// ---------------------------------------------------------------------------

export type TiktokAccessState = "signed_in" | "login_wall" | "logged_out";

export interface TiktokScopeCompletionInput {
  accessState: TiktokAccessState;
  itemCount: number;
  /** Recognized logged-in empty state was positively observed. */
  explicitEmpty: boolean;
  /** Pagination ended with a recognized end-of-feed marker. */
  endOfFeed: boolean;
  /** Navigation to the scope view failed (tab/menu entry missing). */
  navigationFailed: boolean;
  /** Detail for failure classification, e.g. "history_entry_not_found". */
  navigationError?: string;
}

/**
 * Map executor observations to a scope status. Fail closed: logged-out
 * tabs and login walls are explicit failures, and a zero-row result is
 * "empty" ONLY when TikTok's own logged-in empty state was recognized —
 * contract terminal_policy ("only explicit empty means empty; login
 * walls and logged-out task tabs are failed, never valid-empty").
 * A capped/exhausted scroll with rows stays "degraded" because the
 * scope remains incomplete (contract partial_policy).
 */
export function classifyTiktokScopeCompletion(
  input: TiktokScopeCompletionInput,
): { status: TiktokScopeResult["status"]; error?: string } {
  if (input.accessState === "login_wall") {
    return { status: "failed", error: "login_wall" };
  }
  if (input.accessState !== "signed_in") {
    return { status: "failed", error: "logged_out" };
  }
  if (input.navigationFailed) {
    return { status: "failed", error: input.navigationError || "navigation_failed" };
  }
  if (input.itemCount > 0) {
    if (input.endOfFeed) return { status: "ok" };
    return { status: "degraded", error: "pagination_incomplete" };
  }
  if (input.explicitEmpty) return { status: "empty" };
  return { status: "failed", error: "unrecognized_empty_state" };
}

/**
 * Task-level final status for the single-scope TikTok task. Mirrors
 * Douyin's finalizeDyBootstrapStatus: an explicit logged-in empty scope
 * is a successful observation (ok), degraded stays degraded, failed
 * stays failed.
 */
export function finalizeTiktokTaskStatus(
  scopeStatus: TiktokScopeResult["status"],
): "ok" | "degraded" | "failed" {
  if (scopeStatus === "failed") return "failed";
  if (scopeStatus === "degraded") return "degraded";
  return "ok";
}
