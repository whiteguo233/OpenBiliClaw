export type PageType = string;

export interface BehaviorContext {
  pageType: PageType;
  domSnapshot?: string;
  viewport: { width: number; height: number };
  scrollPosition: number;
}

export interface BehaviorEvent {
  type: string;
  url: string;
  title: string;
  timestamp: number;
  source_platform: string;
  context: BehaviorContext;
  metadata: Record<string, unknown>;
}

export interface ActionHint {
  text: string | null;
  ariaLabel: string | null;
  className: string;
}

/**
 * Platform-specific logic injected into the generic collector kernel.
 *
 * One adapter per site (bilibili, xiaohongshu, ...). The kernel handles
 * DOM observation, debouncing, and transport; adapters handle what
 * counts as a "card", how to extract a content id, and how to classify
 * pages/actions for that site.
 */
export interface PlatformAdapter {
  /** Identifier stored on every event, e.g. "bilibili" | "xiaohongshu". */
  readonly sourcePlatform: string;

  /** Classify the current URL into a coarse page type for context. */
  detectPageType(url: string): PageType;

  /**
   * Pull the platform's canonical content identifier from a URL
   * (bvid for bilibili, note_id for xiaohongshu, etc.). Null if the
   * URL doesn't point at a single piece of content.
   */
  extractContentId(url: string): string | null;

  /**
   * CSS selector for clickable content cards in the feed. Used by
   * hover observation and click target detection.
   */
  readonly cardSelector: string;

  /**
   * CSS selector for search input fields on this platform. Enter
   * keypresses inside matching inputs emit `search` events.
   */
  readonly searchInputSelector: string;

  /**
   * CSS selector for the main video element (if any). When null the
   * kernel skips video observation — xhs and most web sources don't
   * have a single play/pause-able player worth tracking.
   */
  readonly videoSelector: string | null;

  /** Map a clicked element's text/aria/className hint to a strong-signal action type. */
  inferActionType(hint: ActionHint): string | null;

  /**
   * Build platform-specific metadata to attach to every event
   * (e.g. `{bvid}` for bilibili, `{note_id}` for xhs). The kernel
   * always sets `source_platform` + `content_id` separately.
   */
  buildEventMetadata(url: string): Record<string, unknown>;

  /**
   * Optional target-specific metadata for click/action events. This is
   * needed on feed pages where the current URL is a list page but the user
   * clicked a specific card's action button.
   */
  buildTargetMetadata?(target: Element, currentUrl: string): Record<string, unknown>;
}
