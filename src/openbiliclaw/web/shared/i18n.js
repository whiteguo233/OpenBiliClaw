/*
 * OpenBiliClaw client localization runtime.
 *
 * Surfaces: desktop web (/web), mobile web (/m), setup wizard (/setup), and the
 * browser extension popup. Loaded as a classic script so every surface can use
 * it without a bundler; expose one global API.
 *
 * How it works
 * ------------
 * The UI copy was written inline in Simplified Chinese across a very large
 * vanilla-JS codebase. Instead of rewriting thousands of call sites, this
 * runtime localizes *rendered DOM text* by exact match against a catalog whose
 * keys are the Simplified Chinese source strings (see
 * scripts/build_i18n_catalogs.py). The source string is the stable key: moving
 * a label between files or rebuilding a panel never invalidates translations.
 *
 * Contract
 * --------
 * - Only exact trimmed text matches are translated. Model output, video
 *   titles, user input, and any string not in the catalog are left untouched.
 * - Dynamic containers that must never be translated (content cards, chat
 *   transcripts, profile prose) are marked `data-i18n-skip`.
 * - A text node's original value is remembered, so switching locales restores
 *   the source and re-translates without a reload.
 * - New copy: write Simplified Chinese as usual, then run
 *   `python scripts/build_i18n_catalogs.py` to add it to the inventory and
 *   regenerate the Traditional Chinese catalog.
 */
(function (global) {
  "use strict";

  const DEFAULT_LOCALE = "zh-Hans";
  const CATALOG_LOCALES = ["zh-Hans", "zh-Hant", "en"];
  const LOCALE_OPTIONS = [
    { id: "zh-Hans", label: "简体中文" },
    { id: "zh-Hant", label: "繁體中文" },
    { id: "en", label: "English" }
  ];
  const ALIASES = {
    zh: "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh-hant": "zh-Hant",
    en: "en",
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    "en-ca": "en"
  };
  const TRANSLATABLE_ATTRS = ["placeholder", "title", "aria-label", "alt"];
  const SKIP_TAGS = new Set([
    "SCRIPT",
    "STYLE",
    "CODE",
    "PRE",
    "KBD",
    "SAMP",
    "TEXTAREA",
    "SVG",
    "MATH"
  ]);
  // Note: `translate="no"` is deliberately NOT honoured. The app sets it on
  // <select> elements to stop *browser* auto-translation from mangling provider
  // / model names, but our catalog only matches known UI copy, so those option
  // labels still need localizing (e.g. the empty "Disabled" option).
  const SKIP_SELECTOR = "[data-i18n-skip], [contenteditable='true']";

  const state = {
    locale: DEFAULT_LOCALE,
    catalog: new Map(),
    // Compiled pattern entries: { regex, replacement, order, weight }.
    patterns: [],
    catalogs: new Map(),
    basePath: "/i18n/ui/",
    fetchCatalog: null,
    ready: false,
    observer: null,
    applying: false,
    listener: null,
    pending: null
  };

  const textSources = new WeakMap(); // Text -> { source, rendered }
  const attrSources = new WeakMap(); // Element -> { attr: { source, rendered } }

  function normalizeLocale(value) {
    const tag = String(value == null ? "" : value)
      .trim()
      .toLowerCase()
      .replace(/_/g, "-")
      .replace(/\s+/g, "-");
    if (!tag) return DEFAULT_LOCALE;
    if (ALIASES[tag]) return ALIASES[tag];
    const parts = tag.split("-");
    if (parts[0] === "zh") {
      if (parts.includes("hant") || parts.includes("tw") || parts.includes("hk") || parts.includes("mo")) {
        return "zh-Hant";
      }
      return "zh-Hans";
    }
    if (parts[0] === "en") return "en";
    return DEFAULT_LOCALE;
  }

  function localeLabel(locale) {
    const normalized = normalizeLocale(locale);
    const option = LOCALE_OPTIONS.find((item) => item.id === normalized);
    return option ? option.label : normalized;
  }

  // Compact counts and short dates follow the active locale. Number units are
  // not catalog strings (「33.1万」 is a computation), so they need code-level
  // formatting: K/M/B in English, 萬/億 in Traditional, 万/亿 in Simplified.
  function formatCount(value) {
    const count = Math.floor(Number(value) || 0);
    if (count <= 0) return "";
    const compact = (number, unit) => {
      const scaled = Math.floor(number * 10) / 10;
      return `${scaled.toFixed(1).replace(/\.0$/, "")}${unit}`;
    };
    if (state.locale === "en") {
      if (count >= 1000000000) return compact(count / 1000000000, "B");
      if (count >= 1000000) return compact(count / 1000000, "M");
      if (count >= 1000) return compact(count / 1000, "K");
      return String(count);
    }
    const wan = state.locale === "zh-Hant" ? "萬" : "万";
    const yi = state.locale === "zh-Hant" ? "億" : "亿";
    if (count >= 100000000) return compact(count / 100000000, yi);
    if (count >= 10000) return compact(count / 10000, wan);
    return String(count);
  }

  function formatShortDate(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    if (state.locale === "en") {
      return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
    }
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }

  function mergeCatalog(base, extra) {
    const merged = new Map(base);
    extra.forEach((value, key) => {
      if (typeof value === "string" && value.trim()) merged.set(key, value);
    });
    return merged;
  }

  async function loadCatalog(locale) {
    if (state.catalogs.has(locale)) return state.catalogs.get(locale);
    let catalog = { messages: new Map(), patterns: {} };
    try {
      if (typeof state.fetchCatalog === "function") {
        catalog = toCatalog(await state.fetchCatalog(locale));
      } else {
        const response = await fetch(`${state.basePath}${locale}.json`, { cache: "no-cache" });
        if (response.ok) catalog = toCatalog(await response.json());
      }
    } catch (error) {
      // A missing catalog is not fatal: the surface keeps its source copy.
      if (typeof console !== "undefined" && console.warn) {
        console.warn(`[i18n] catalog ${locale} unavailable`, error);
      }
    }
    state.catalogs.set(locale, catalog);
    return catalog;
  }

  function toCatalog(payload) {
    const messages = (payload && payload.messages) || payload || {};
    const patterns = (payload && payload.patterns) || {};
    return {
      messages: new Map(Object.entries(messages)),
      patterns: patterns && typeof patterns === "object" ? patterns : {}
    };
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function compilePatterns(entries) {
    const compiled = [];
    entries.forEach((replacement, source) => {
      if (typeof replacement !== "string" || !replacement) return;
      const parts = String(source).split(/\{(\d+)\}/);
      let regex = "^";
      const order = [];
      for (let index = 0; index < parts.length; index += 1) {
        if (index % 2 === 0) {
          regex += escapeRegExp(parts[index]);
        } else {
          regex += "(.*?)";
          order.push(Number(parts[index]));
        }
      }
      regex += "$";
      compiled.push({
        regex: new RegExp(regex, "s"),
        replacement,
        order,
        // Longer literal text wins: the most specific pattern matches first.
        weight: String(source).replace(/\{\d+\}/g, "").length
      });
    });
    compiled.sort((left, right) => right.weight - left.weight);
    return compiled;
  }

  function mergePatterns(base, extra) {
    const merged = new Map(Object.entries(base || {}));
    Object.entries(extra || {}).forEach(([source, replacement]) => {
      if (typeof replacement === "string" && replacement) merged.set(source, replacement);
    });
    return merged;
  }

  async function activate(locale) {
    const normalized = normalizeLocale(locale);
    const fallback = await loadCatalog(DEFAULT_LOCALE);
    const primary = normalized === DEFAULT_LOCALE ? fallback : await loadCatalog(normalized);
    // Traditional Chinese gets its own complete catalog; English is filled in
    // incrementally, so untranslated copy falls back to the Chinese source
    // rather than disappearing.
    state.locale = normalized;
    state.catalog = mergeCatalog(fallback.messages, primary.messages);
    state.patterns = compilePatterns(mergePatterns(fallback.patterns, primary.patterns));
    document.documentElement.lang = normalized;
    document.documentElement.dir = "ltr";
    apply(document.documentElement);
    if (typeof state.listener === "function") {
      try {
        state.listener(normalized);
      } catch (error) {
        console.warn("[i18n] locale listener failed", error);
      }
    }
  }

  function shouldSkip(element) {
    if (!element) return true;
    if (SKIP_TAGS.has(element.tagName)) return true;
    const closest = element.closest ? element.closest(SKIP_SELECTOR) : null;
    return Boolean(closest);
  }

  function lookup(source) {
    const value = state.catalog.get(source);
    return typeof value === "string" && value ? value : null;
  }

  // A captured value is often itself a UI label (「来源：启用」 → the value
  // 「启用」 has its own translation). Translate exact catalog hits only; never
  // recurse into patterns, so a capture can't re-trigger the same pattern.
  function translateCapture(value) {
    const text = String(value == null ? "" : value);
    const trimmed = text.trim();
    if (!trimmed) return text;
    let exact = lookup(trimmed);
    if (exact === null) {
      // Status labels are composed as 「◆ 联网验证」 — the symbol is not part
      // of the catalog key, so retry on the text after leading punctuation.
      const parts = trimmed.match(/^([^\p{L}\p{N}]*)(.+)$/u);
      if (parts) {
        const inner = lookup(parts[2].trim());
        if (inner !== null) exact = `${parts[1]}${inner}`;
      }
    }
    if (exact === null || exact === trimmed) return text;
    return text.replace(trimmed, exact);
  }

  function matchPattern(source) {
    if (!state.patterns.length || !/[\u3400-\u4dbf\u4e00-\u9fff]/.test(source)) return null;
    for (const pattern of state.patterns) {
      const match = pattern.regex.exec(source);
      if (!match) continue;
      const values = {};
      pattern.order.forEach((index, position) => {
        values[index] = translateCapture(match[position + 1]);
      });
      return pattern.replacement.replace(/\{(\d+)\}/g, (_all, index) => values[Number(index)] ?? "");
    }
    return null;
  }

  function translateSource(source) {
    const exact = lookup(source);
    return exact === null ? matchPattern(source) : exact;
  }

  function translateTextNode(node) {
    const parent = node.parentElement;
    if (!parent || shouldSkip(parent)) return;
    const raw = node.nodeValue;
    if (!raw) return;
    const trimmed = raw.trim();
    if (!trimmed) return;

    let record = textSources.get(node);
    // The app re-rendered this node's text after we localized it: treat the
    // fresh value as the new source instead of translating a translation.
    if (record && trimmed !== record.rendered && trimmed !== record.source) {
      record = null;
    }
    if (!record) {
      record = { source: trimmed, rendered: trimmed };
      textSources.set(node, record);
    }

    const translated = translateSource(record.source);
    const next = translated === null ? record.source : translated;
    if (next === trimmed) {
      // Already in the desired form (source for zh-Hans / untranslated text).
      if (trimmed === record.source) record.rendered = record.source;
      return;
    }
    const index = raw.indexOf(trimmed);
    const replaced = index >= 0 ? raw.slice(0, index) + next + raw.slice(index + trimmed.length) : next;
    node.nodeValue = replaced;
    record.rendered = next;
  }

  function translateAttributes(element) {
    if (!element.hasAttribute || shouldSkip(element)) return;
    let record = attrSources.get(element);
    if (!record) {
      record = {};
      attrSources.set(element, record);
    }
    for (const attr of TRANSLATABLE_ATTRS) {
      const raw = element.getAttribute(attr);
      if (!raw) continue;
      const trimmed = raw.trim();
      if (!trimmed) continue;
      let entry = record[attr];
      if (entry && trimmed !== entry.rendered && trimmed !== entry.source) entry = null;
      if (!entry) {
        entry = { source: trimmed, rendered: trimmed };
        record[attr] = entry;
      }
      const translated = translateSource(entry.source);
      const next = translated === null ? entry.source : translated;
      if (next === trimmed) continue;
      element.setAttribute(attr, raw.replace(trimmed, next));
      entry.rendered = next;
    }
  }

  function apply(root) {
    if (!root) return;
    if (state.applying) return;
    state.applying = true;
    try {
      if (root.nodeType === Node.TEXT_NODE) {
        translateTextNode(root);
        return;
      }
      if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
      if (root.nodeType === Node.ELEMENT_NODE) {
        if (shouldSkip(root)) return;
        translateAttributes(root);
      }

      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          const parent = node.parentElement;
          if (!parent || shouldSkip(parent)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        }
      });
      let node = walker.nextNode();
      while (node) {
        translateTextNode(node);
        node = walker.nextNode();
      }

      if (root.querySelectorAll) {
        const selector = TRANSLATABLE_ATTRS.map((attr) => `[${attr}]`).join(",");
        root.querySelectorAll(selector).forEach((element) => translateAttributes(element));
      }
    } finally {
      state.applying = false;
    }
  }

  // Roots accumulate until the next frame. Replacing the pending batch instead
  // of appending drops work: a later mutation (even a removal-only record with
  // no added nodes) would cancel the queued tabs/panels before they translate.
  const pendingRoots = [];

  function scheduleApply(nodes) {
    if (!nodes || nodes.length === 0) return;
    pendingRoots.push(...nodes);
    if (state.pending) return;
    state.pending = requestAnimationFrame(() => {
      state.pending = null;
      const roots = pendingRoots.splice(0, pendingRoots.length);
      roots.forEach((node) => {
        if (node && node.isConnected !== false) apply(node);
      });
    });
  }

  function observe() {
    if (state.observer || typeof MutationObserver === "undefined") return;
    state.observer = new MutationObserver((records) => {
      if (state.applying) return;
      const roots = [];
      for (const record of records) {
        if (record.type === "childList") {
          record.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE || node.nodeType === Node.TEXT_NODE) roots.push(node);
          });
        } else if (record.type === "characterData") {
          roots.push(record.target);
        }
      }
      if (roots.length) scheduleApply(roots);
    });
    state.observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true
    });
  }

  async function init(options) {
    const config = options || {};
    if (config.basePath) state.basePath = config.basePath.endsWith("/") ? config.basePath : `${config.basePath}/`;
    if (typeof config.fetchCatalog === "function") state.fetchCatalog = config.fetchCatalog;
    if (typeof config.onChange === "function") state.listener = config.onChange;
    await activate(config.locale || document.documentElement.lang || DEFAULT_LOCALE);
    observe();
    state.ready = true;
    return state.locale;
  }

  async function setLocale(locale) {
    await activate(locale);
    return state.locale;
  }

  function translate(source, params) {
    const translated = translateSource(String(source));
    let text = translated === null ? String(source) : translated;
    if (params) {
      Object.keys(params).forEach((key) => {
        text = text.replace(new RegExp(`\\{${key}\\}`, "g"), String(params[key]));
      });
    }
    return text;
  }

  global.OpenBiliClawI18n = {
    DEFAULT_LOCALE,
    CATALOG_LOCALES,
    LOCALE_OPTIONS,
    init,
    setLocale,
    apply,
    translate,
    formatCount,
    formatShortDate,
    normalizeLocale,
    localeLabel,
    get locale() {
      return state.locale;
    },
    get ready() {
      return state.ready;
    }
  };
})(globalThis);
