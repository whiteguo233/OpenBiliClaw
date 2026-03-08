export type PageType = "video" | "search" | "home" | "category" | "user";

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
  context: BehaviorContext;
  metadata: Record<string, unknown>;
}

export interface ActionHint {
  text: string | null;
  ariaLabel: string | null;
  className: string;
}
