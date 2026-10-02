(function installDialogueConfirmation(global) {
  "use strict";

  const CARD_ACTIONS = ["confirm", "reject", "discuss", "defer"];
  const CARD_ACTION_LABELS = {
    confirm: "准",
    reject: "不准",
    discuss: "聊聊",
    defer: "稍后",
  };
  const CARD_STATE_LABELS = {
    confirmed: "已确认",
    rejected: "已标记不准",
    // A revise is not a rejection — the user corrected the wording and
    // accepted it, and a derived hypothesis was recorded.
    revised: "已按你的修正记下",
    discussing: "正在聊这条",
    deferred: "已稍后再聊",
    processing: "正在处理，以后端结算为准",
    retryable_error: "处理结果暂未同步，可刷新或重试",
  };
  const CARD_STATES = new Set([
    "pending",
    "confirmed",
    "rejected",
    "revised",
    "discussing",
    "deferred",
    "processing",
    "retryable_error",
  ]);
  const TERMINAL_CARD_STATES = new Set(["confirmed", "rejected", "revised", "deferred"]);
  const POLL_TERMINAL_CARD_STATES = new Set([
    "confirmed",
    "rejected",
    "revised",
    "deferred",
    "discussing",
  ]);
  const CARD_ACTION_POLL_BACKOFF_MS = Object.freeze([1_000, 2_000, 5_000]);
  // Calibrated for several local 1/2/5s publication reads. This bounds a
  // non-durable restart spinner; it deliberately does not wait for a 300s
  // provider timeout or introduce a durable job table.
  const CARD_ACTION_POLL_DEADLINE_MS = 30_000;
  const PENDING_OPEN_RETRY_BACKOFF_MS = Object.freeze([1_000, 2_000, 3_000, 5_000]);
  // Match the backend's safe hot-reload drain window. The page/popup abort
  // signal still stops retries immediately when its owner goes away.
  const PENDING_OPEN_RETRY_DEADLINE_MS = 25 * 60_000;
  // Probe chat is a durable conversational turn too. Keep delight chat out
  // of the main dialogue because it belongs to the recommendation card's
  // own contextual history, but show both probe polarities in the shared
  // dialogue view so closing the message inbox cannot hide the conversation.
  const DIALOGUE_SCOPES = new Set([
    "chat",
    "hypothesis",
    "confusion",
    "probe",
    "avoidance_probe",
  ]);
  const DIALOGUE_REPLY_SCOPES = new Set(["chat", "probe", "avoidance_probe"]);
  // Backend refuses settlement when another card owns the dialogue anchor.
  // These outcomes are honest failures — never fall back to the optimistic
  // terminal state, or the UI will claim "已确认" while nothing was written.
  const ANCHOR_REFUSAL_OUTCOMES = new Set(["stale_anchor", "anchor_dependency_failed"]);

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function text(value) {
    return typeof value === "string" ? value.trim() : String(value ?? "").trim();
  }

  function escapeHtml(value) {
    return text(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function escapeHtmlRaw(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function markdownSlot(slots, markup) {
    const token = `\u0000md${slots.length}\u0000`;
    slots.push(markup);
    return token;
  }

  function restoreMarkdownSlots(value, slots) {
    return value.replace(/\u0000md(\d+)\u0000/g, (_match, index) => slots[Number(index)] || "");
  }

  function safeMarkdownHref(value) {
    const href = String(value ?? "").trim();
    return /^https?:\/\//i.test(href) ? escapeHtmlRaw(href) : "";
  }

  function bareLinkTarget(value) {
    let href = value.replace(/[.,;:!?]+$/, "");
    // Keep parentheses in real paths, but leave prose wrappers outside links.
    for (const [open, close] of [["(", ")"], ["[", "]"]]) {
      let excess = href.split(close).length - href.split(open).length;
      while (excess > 0 && href.endsWith(close)) {
        href = href.slice(0, -1);
        excess -= 1;
      }
    }
    try {
      const parsed = new URL(href);
      return parsed.hostname && /^https?:$/.test(parsed.protocol) ? href : "";
    } catch {
      return "";
    }
  }

  function renderMarkdownInline(value, allowLinks = true) {
    const slots = [];
    let source = String(value ?? "");

    // Protect escaped punctuation, code spans, and links before escaping the
    // remaining text. Everything that reaches the formatting replacements is
    // already HTML-escaped, so raw HTML can never become executable markup.
    source = source.replace(
      /\\([\\`*_[\]{}()#+.!~>-])/g,
      (_match, character) => markdownSlot(slots, escapeHtmlRaw(character)),
    );
    source = source.replace(
      /`([^`\n]+)`/g,
      (_match, code) => markdownSlot(slots, `<code>${escapeHtmlRaw(code)}</code>`),
    );
    if (allowLinks) {
      source = source.replace(
        /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/gi,
        (match, label, href) => {
          const safeHref = safeMarkdownHref(href);
          if (!safeHref) return match;
          return markdownSlot(
            slots,
            `<a href="${safeHref}" target="_blank" rel="noopener noreferrer">${renderMarkdownInline(label, false)}</a>`,
          );
        },
      );
      // Operate on unrendered text after links/code have become opaque slots.
      // Never run a URL replacement over HTML or recursively link a link label.
      source = source.replace(
        /\bhttps?:\/\/[^\s\u0000-\u001f<>"'`*\\，。；：！？、（）【】《》“”‘’]+/gi,
        (match) => {
          const href = bareLinkTarget(match);
          if (!href) return match;
          return markdownSlot(
            slots,
            `<a href="${safeMarkdownHref(href)}" target="_blank" rel="noopener noreferrer">${escapeHtmlRaw(href)}</a>`,
          ) + match.slice(href.length);
        },
      );
    }

    let rendered = escapeHtmlRaw(source);
    rendered = rendered.replace(/\*\*\*([^*\n]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
    rendered = rendered.replace(/___([^_\n]+?)___/g, "<strong><em>$1</em></strong>");
    rendered = rendered.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
    rendered = rendered.replace(/(^|[^\w])__([^_\n]+?)__(?!\w)/g, "$1<strong>$2</strong>");
    rendered = rendered.replace(/~~([^~\n]+?)~~/g, "<del>$1</del>");
    rendered = rendered.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");
    rendered = rendered.replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
    rendered = rendered.replace(/ {2,}\n/g, "<br>\n").replace(/\n/g, "<br>\n");
    return restoreMarkdownSlots(rendered, slots);
  }

  function renderMarkdown(value) {
    const source = String(value ?? "").replace(/\r\n?/g, "\n").trim();
    if (!source) return "";

    const lines = source.split("\n");
    const blocks = [];
    let paragraph = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      const content = paragraph.join("\n").trim();
      paragraph = [];
      if (content) blocks.push(`<p>${renderMarkdownInline(content)}</p>`);
    };

    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      if (!line.trim()) {
        flushParagraph();
        index += 1;
        continue;
      }

      const fence = line.match(/^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)\s*$/);
      if (fence) {
        flushParagraph();
        const marker = fence[1];
        const language = fence[2];
        const codeLines = [];
        index += 1;
        while (index < lines.length) {
          const closing = lines[index].match(/^\s*(`{3,}|~{3,})\s*$/);
          if (
            closing &&
            closing[1][0] === marker[0] &&
            closing[1].length >= marker.length
          ) {
            index += 1;
            break;
          }
          codeLines.push(lines[index]);
          index += 1;
        }
        const languageClass = language ? ` class="language-${escapeHtml(language)}"` : "";
        blocks.push(`<pre><code${languageClass}>${escapeHtmlRaw(codeLines.join("\n"))}</code></pre>`);
        continue;
      }

      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        flushParagraph();
        const level = heading[1].length;
        blocks.push(`<h${level}>${renderMarkdownInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^\s{0,3}>\s?/.test(line)) {
        flushParagraph();
        const quoteLines = [];
        while (index < lines.length && /^\s{0,3}>\s?/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ""));
          index += 1;
        }
        blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
        continue;
      }

      const unordered = line.match(/^\s{0,3}[-+*]\s+(.+)$/);
      const ordered = line.match(/^\s{0,3}\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        const tag = unordered ? "ul" : "ol";
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(
            unordered ? /^\s{0,3}[-+*]\s+(.+)$/ : /^\s{0,3}\d+[.)]\s+(.+)$/,
          );
          if (!item) break;
          items.push(`<li>${renderMarkdownInline(item[1])}</li>`);
          index += 1;
        }
        blocks.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }

      if (/^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/.test(line)) {
        flushParagraph();
        blocks.push("<hr>");
        index += 1;
        continue;
      }

      paragraph.push(line);
      index += 1;
    }
    flushParagraph();
    return blocks.join("");
  }

  function cloneTurn(turn) {
    const source = isRecord(turn) ? turn : {};
    const payload = isRecord(source.payload) ? { ...source.payload } : {};
    if (Array.isArray(payload.actions)) payload.actions = [...payload.actions];
    if (Array.isArray(payload.evidence_refs)) payload.evidence_refs = [...payload.evidence_refs];
    return { ...source, payload };
  }

  function normalizedCardState(turn) {
    const state = text(isRecord(turn?.payload) ? turn.payload.state : "").toLowerCase();
    return CARD_STATES.has(state) ? state : "pending";
  }

  function withCardState(turn, state) {
    const next = cloneTurn(turn);
    next.payload.state = CARD_STATES.has(state) ? state : "pending";
    return next;
  }

  function isCardTurn(turn) {
    return isRecord(turn?.payload) && turn.payload.type === "card";
  }

  function isTerminalCardTurn(turn) {
    return isCardTurn(turn) && TERMINAL_CARD_STATES.has(normalizedCardState(turn));
  }

  function isQuestionTurn(turn) {
    return isRecord(turn?.payload) && turn.payload.type === "question";
  }

  function isDialogueTurn(turn) {
    return isRecord(turn) && DIALOGUE_SCOPES.has(text(turn.scope));
  }

  function isDialogueReplyTurn(turn) {
    return isRecord(turn) && DIALOGUE_REPLY_SCOPES.has(text(turn.scope));
  }

  function cardActionPath(turnId) {
    return `/chat/cards/${encodeURIComponent(text(turnId))}/action`;
  }

  function pendingConfirmationOpenPath(ref) {
    return `/chat/pending-confirmations/${encodeURIComponent(text(ref))}/open`;
  }

  function pendingOpenErrorCode(error) {
    return text(error?.details?.detail?.code || error?.details?.code).toLowerCase();
  }

  async function executePendingConfirmationOpen(ref, options = {}) {
    const {
      request,
      session = "popup",
      signal,
      onWaiting,
      sleep = waitForPoll,
      now = () => Date.now(),
      deadlineMs = PENDING_OPEN_RETRY_DEADLINE_MS,
    } = options;
    if (typeof request !== "function") {
      throw new TypeError("executePendingConfirmationOpen requires request");
    }
    const path = pendingConfirmationOpenPath(ref);
    const startedAt = now();
    let attempt = 0;
    while (true) {
      if (signal?.aborted) throw signal.reason || abortError();
      try {
        return await request(path, { session }, { signal });
      } catch (error) {
        if (Number(error?.status) !== 503 || pendingOpenErrorCode(error) !== "dialogue_busy") {
          throw error;
        }
        if (now() - startedAt >= deadlineMs) throw error;
        if (typeof onWaiting === "function") {
          onWaiting({
            attempt,
            message: text(error?.details?.detail?.message) || "后台正在整理上一段对话",
          });
        }
        const delay = PENDING_OPEN_RETRY_BACKOFF_MS[
          Math.min(attempt, PENDING_OPEN_RETRY_BACKOFF_MS.length - 1)
        ];
        attempt += 1;
        await sleep(delay, signal);
      }
    }
  }

  function applyOptimisticCardAction(turn, action) {
    const normalizedAction = text(action).toLowerCase();
    if (!CARD_ACTIONS.includes(normalizedAction)) {
      throw new TypeError(`Unsupported card action: ${normalizedAction}`);
    }
    const state = normalizedCardState(turn);
    if (TERMINAL_CARD_STATES.has(state)) return cloneTurn(turn);
    const nextState = {
      confirm: "confirmed",
      reject: "rejected",
      discuss: "discussing",
      defer: "deferred",
    }[normalizedAction];
    return withCardState(turn, nextState);
  }

  function responseCardState(response, fallbackState) {
    const state = text(response?.state || response?.verdict).toLowerCase();
    return CARD_STATES.has(state) ? state : fallbackState;
  }

  function abortError() {
    if (typeof DOMException === "function") {
      return new DOMException("Card action polling aborted", "AbortError");
    }
    const error = new Error("Card action polling aborted");
    error.name = "AbortError";
    return error;
  }

  function isAbort(error, signal) {
    return Boolean(signal?.aborted) || error?.name === "AbortError";
  }

  function waitForPoll(milliseconds, signal) {
    if (signal?.aborted) return Promise.reject(signal.reason || abortError());
    return new Promise((resolve, reject) => {
      let timeoutId = null;
      const onAbort = () => {
        if (timeoutId !== null) clearTimeout(timeoutId);
        reject(signal.reason || abortError());
      };
      timeoutId = setTimeout(() => {
        if (signal) signal.removeEventListener("abort", onAbort);
        resolve();
      }, milliseconds);
      if (signal) {
        signal.addEventListener("abort", onAbort, { once: true });
        Promise.resolve().then(() => {
          if (signal.aborted) onAbort();
        });
      }
    });
  }

  function retryableCardResult(turn, action, reason, onUpdate) {
    const retryable = withCardState(turn, "retryable_error");
    retryable.payload.retry_action = text(action).toLowerCase();
    const response = {
      ok: false,
      outcome: "retryable_error",
      state: "retryable_error",
      reason,
    };
    onUpdate(retryable, response);
    return { turn: retryable, response };
  }

  async function pollProcessingCard(original, action, initialResponse, options) {
    if (typeof options.fetchTurn !== "function") {
      return retryableCardResult(
        original,
        action,
        "poll_unavailable",
        options.onUpdate,
      );
    }
    const now = typeof options.now === "function" ? options.now : Date.now;
    const sleep = typeof options.sleep === "function" ? options.sleep : waitForPoll;
    const startedAt = now();
    let backoffIndex = 0;
    let latest = cloneTurn(original);

    while (true) {
      if (options.signal?.aborted) {
        return retryableCardResult(latest, action, "aborted", options.onUpdate);
      }
      const remaining = CARD_ACTION_POLL_DEADLINE_MS - Math.max(0, now() - startedAt);
      if (remaining <= 0) {
        return retryableCardResult(latest, action, "deadline", options.onUpdate);
      }
      const configuredDelay = CARD_ACTION_POLL_BACKOFF_MS[
        Math.min(backoffIndex, CARD_ACTION_POLL_BACKOFF_MS.length - 1)
      ];
      const delay = Math.min(configuredDelay, remaining);
      try {
        await sleep(delay, options.signal);
      } catch (error) {
        if (isAbort(error, options.signal)) {
          return retryableCardResult(latest, action, "aborted", options.onUpdate);
        }
        return retryableCardResult(latest, action, "poll_failed", options.onUpdate);
      }
      if (options.signal?.aborted) {
        return retryableCardResult(latest, action, "aborted", options.onUpdate);
      }
      if (now() - startedAt >= CARD_ACTION_POLL_DEADLINE_MS) {
        return retryableCardResult(latest, action, "deadline", options.onUpdate);
      }
      try {
        const fetchTimeoutMs = Math.max(
          1,
          CARD_ACTION_POLL_DEADLINE_MS - Math.max(0, now() - startedAt),
        );
        latest = cloneTurn(
          await options.fetchTurn(text(original.turn_id), {
            signal: options.signal,
            timeoutMs: fetchTimeoutMs,
          }),
        );
      } catch (error) {
        if (isAbort(error, options.signal)) {
          return retryableCardResult(latest, action, "aborted", options.onUpdate);
        }
        backoffIndex += 1;
        continue;
      }
      const durableState = normalizedCardState(latest);
      if (POLL_TERMINAL_CARD_STATES.has(durableState)) {
        const response = {
          ...initialResponse,
          ok: true,
          outcome: "settled",
          state: durableState,
          verdict: durableState,
        };
        options.onUpdate(latest, response);
        return { turn: latest, response };
      }
      const processing = withCardState(latest, "processing");
      options.onUpdate(processing, initialResponse);
      backoffIndex += 1;
    }
  }

  async function executeCardAction(turn, action, options = {}) {
    if (typeof options.request !== "function" || typeof options.onUpdate !== "function") {
      throw new TypeError("card action requires request and onUpdate callbacks");
    }
    const original = cloneTurn(turn);
    const optimistic = applyOptimisticCardAction(original, action);
    options.onUpdate(optimistic);
    try {
      const response = await options.request(cardActionPath(original.turn_id), {
        action: text(action).toLowerCase(),
      });
      const outcome = text(response?.outcome).toLowerCase();
      if (outcome === "processing" || responseCardState(response, "") === "processing") {
        const processing = withCardState(original, "processing");
        options.onUpdate(processing, response);
        return await pollProcessingCard(original, action, response, options);
      }
      // Anchor owned by another card: backend wrote nothing. Reuse the
      // retryable path so the optimistic "confirmed" flash is rolled back.
      if (ANCHOR_REFUSAL_OUTCOMES.has(outcome)) {
        return retryableCardResult(original, action, outcome, options.onUpdate);
      }
      // `already_settled` is authoritative, including the opposite verdict.
      // Replacing the optimistic state here is the cross-screen rollback path.
      const settled = withCardState(
        optimistic,
        responseCardState(response, normalizedCardState(optimistic)),
      );
      options.onUpdate(settled, response);
      return { turn: settled, response };
    } catch (error) {
      if (isAbort(error, options.signal)) {
        return retryableCardResult(original, action, "aborted", options.onUpdate);
      }
      options.onUpdate(original, { outcome: "error", error });
      throw error;
    }
  }

  function isOpaqueEvidenceId(value) {
    const item = text(value);
    if (!item) return false;
    if (/^\d{1,24}$/.test(item)) return true;
    if (/^[0-9a-f]{8,64}$/i.test(item)) return true;
    if (/^[0-9a-f]{8}(?:-[0-9a-f]{4}){2,4}-[0-9a-f]{8,12}$/i.test(item)) return true;
    if (/^(?:BV[0-9A-Za-z]{10,}|av\d+|cv\d+)$/i.test(item)) return true;
    if (/^(?:event|evt|note|content|awareness|hypothesis|confusion|insight|turn)[#:/_-][A-Za-z0-9._:/-]+$/i.test(item)) {
      return true;
    }
    return (
      item.length >= 20 &&
      !/^https?:\/\//i.test(item) &&
      /^[A-Za-z0-9._:/+-]+$/.test(item)
    );
  }

  function readableEvidenceValues(values) {
    if (!Array.isArray(values)) return [];
    return [
      ...new Set(
        values
          .map((item) => (typeof item === "string" || typeof item === "number" ? text(item) : ""))
          .filter((item) => item && !isOpaqueEvidenceId(item))
          .map((item) => item.slice(0, 240)),
      ),
    ].slice(0, 5);
  }

  function evidenceRefs(payload) {
    return readableEvidenceValues(payload?.evidence_refs);
  }

  function contextPath(turnId) {
    return `/chat/contexts/${encodeURIComponent(text(turnId))}`;
  }

  function normalizeContextPreview(value) {
    if (!isRecord(value) || value.active !== true) return null;
    const replyToTurnId = text(value.reply_to_turn_id);
    const sourceType = text(value.source_type).toLowerCase();
    const kind = text(value.kind).toLowerCase();
    const generation = Number(value.generation);
    const title = text(value.title);
    const digest = text(value.context_digest);
    if (
      !replyToTurnId ||
      !["card", "question"].includes(sourceType) ||
      !["hypothesis", "confusion"].includes(kind) ||
      !Number.isInteger(generation) ||
      generation <= 0 ||
      !title ||
      !digest
    ) return null;
    return {
      active: true,
      reply_to_turn_id: replyToTurnId,
      source_type: sourceType,
      kind,
      generation,
      title,
      evidence_labels: readableEvidenceValues(value.evidence_labels),
      context_digest: digest,
    };
  }

  function contextSelectionFromTurn(turn, preview) {
    const normalized = normalizeContextPreview(preview);
    if (!normalized || text(turn?.turn_id) !== normalized.reply_to_turn_id) return null;
    return normalized;
  }

  function replaceContextSelection(current, next) {
    const normalized = normalizeContextPreview(next);
    return normalized || normalizeContextPreview(current);
  }

  function clearContextSelection() {
    return null;
  }

  function contextSelectionForSubmit(selection) {
    const normalized = normalizeContextPreview(selection);
    return normalized ? { reply_to_turn_id: normalized.reply_to_turn_id } : {};
  }

  function contextStorageKey(surface) {
    const normalized = text(surface).toLowerCase().replace(/[^a-z0-9_-]/g, "-") || "default";
    return `openbiliclaw.dialogue-context.${normalized}`;
  }

  function readContextSelection(storage, surface) {
    if (!storage || typeof storage.getItem !== "function") return null;
    try {
      return normalizeContextPreview(JSON.parse(storage.getItem(contextStorageKey(surface)) || "null"));
    } catch {
      return null;
    }
  }

  function writeContextSelection(storage, surface, selection) {
    const normalized = normalizeContextPreview(selection);
    if (!storage || typeof storage.setItem !== "function") return normalized;
    try {
      if (normalized) storage.setItem(contextStorageKey(surface), JSON.stringify(normalized));
      else if (typeof storage.removeItem === "function") storage.removeItem(contextStorageKey(surface));
    } catch {
      // Storage is an optimization only; server validation remains authoritative.
    }
    return normalized;
  }

  function contextBarMarkup(selection) {
    const normalized = normalizeContextPreview(selection);
    if (!normalized) return "";
    const kindLabel = normalized.kind === "confusion" ? "疑惑" : "阿B 的猜测";
    return `<div class="dialogue-context-bar" role="status"><span class="dialogue-context-copy"><span class="dialogue-context-label">正在回复 ${kindLabel}</span><strong title="${escapeHtml(normalized.title)}">${escapeHtml(normalized.title)}</strong></span><button type="button" class="dialogue-context-clear" data-context-clear="true" aria-label="清除当前对话上下文">清除</button></div>`;
  }

  function contextTitleFromTurn(turn) {
    const payload = isRecord(turn?.payload) ? turn.payload : {};
    return text(payload.title) || text(turn?.subject_title) || text(turn?.message) || "已选对话上下文";
  }

  function replyQuoteMarkup(turn, targetTurns) {
    const targetId = text(turn?.reply_to_turn_id);
    if (!targetId) return "";
    const candidates = Array.isArray(targetTurns)
      ? targetTurns
      : targetTurns instanceof Map
        ? [...targetTurns.values()]
        : [];
    const target = candidates.find((item) => text(item?.turn_id) === targetId);
    const bindingContext = isRecord(turn?.payload?.dialogue_binding?.context)
      ? turn.payload.dialogue_binding.context
      : {};
    const title = text(bindingContext.title) || contextTitleFromTurn(target);
    const kind = text(bindingContext.kind).toLowerCase() === "confusion"
      || isQuestionTurn(target)
      ? "疑惑"
      : "阿B 的猜测";
    return `<div class="dialogue-reply-quote" data-reply-quote-target="${escapeHtml(targetId)}"><button type="button" data-reply-quote-target-id="${escapeHtml(targetId)}" aria-label="回到${escapeHtml(kind)}：${escapeHtml(title)}"><span>${escapeHtml(kind)}</span><strong title="${escapeHtml(title)}">${escapeHtml(title)}</strong></button></div>`;
  }

  function activateReplyQuote(event, root) {
    const target = event?.target instanceof Element
      ? event.target.closest("[data-reply-quote-target-id]")
      : null;
    if (!(target instanceof HTMLElement)) return false;
    const turnId = text(target.dataset.replyQuoteTargetId);
    if (!turnId || !root || typeof root.querySelector !== "function") return false;
    const escaped = typeof CSS !== "undefined" && typeof CSS.escape === "function"
      ? CSS.escape(turnId)
      : turnId.replace(/[^a-zA-Z0-9_-]/g, "\\$&");
    const destination = root.querySelector(
      `[data-dialogue-turn-container="${escaped}"], [data-dialogue-turn-id="${escaped}"]`,
    );
    if (!destination) return false;
    destination.scrollIntoView?.({ behavior: "smooth", block: "center" });
    destination.setAttribute?.("tabindex", "-1");
    destination.focus?.({ preventScroll: true });
    return true;
  }

  function contextErrorCode(error) {
    return text(error?.details?.detail?.code || error?.details?.code).toLowerCase();
  }

  function contextErrorMessage(error) {
    const code = contextErrorCode(error);
    const serverMessage = text(error?.details?.detail?.message || error?.details?.message);
    const localizedMessage = {
      reply_target_not_found: "这条卡片已经不在了，请重新打开一条待聊内容。",
      reply_target_inactive: "这条上下文已经失效，请重新打开后再发。",
      reply_target_processing: "这条上下文正在处理，稍后可以重试。",
      turn_id_conflict: "这条消息已经提交过，当前草稿没有覆盖原记录。",
      reserved_payload_key: "这次请求包含了不受信任的上下文字段。",
      invalid_reply_target: "请选择一条仍在讨论中的卡片或疑惑。",
      dialogue_busy: "后台正在整理上一段对话，请稍后重试。",
    }[code];
    return localizedMessage || serverMessage || "这句暂时没有发出去，请保留草稿后重试。";
  }

  function evidenceMarkup(payload) {
    const evidence = evidenceRefs(payload);
    if (!evidence.length) return "";
    return `<details class="dialogue-evidence"><summary>依据（${evidence.length}）</summary><ul>${evidence
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("")}</ul></details>`;
  }

  function turnAttributes(turn, extraClass = "") {
    const turnId = escapeHtml(turn?.turn_id);
    return `class="${extraClass}" data-dialogue-turn-id="${turnId}"`;
  }

  function textBubbleMarkup(role, content, turn, part, surface, extraClass = "") {
    const cleanContent = text(content);
    if (!cleanContent) return "";
    const isUser = role === "user";
    const turnId = escapeHtml(turn?.turn_id);
    const safePart = escapeHtml(part);
    const renderedContent = isUser ? escapeHtml(cleanContent) : renderMarkdown(cleanContent);
    if (surface === "desktop") {
      return `<div class="chat-bubble ${isUser ? "user" : "agent"}${extraClass ? ` ${extraClass}` : ""}" data-dialogue-turn-id="${turnId}" data-part="${safePart}">${isUser ? renderedContent : `<div class="chat-markdown">${renderedContent}</div>`}</div>`;
    }
    return `<div class="chat-message${isUser ? " user" : ""}${extraClass ? ` ${extraClass}` : ""}" data-dialogue-turn-id="${turnId}" data-part="${safePart}"><span class="chat-role">${isUser ? "你" : "助手"}</span><div class="chat-content${isUser ? "" : " chat-markdown"}">${renderedContent}</div></div>`;
  }

  function cardActions(payload, state) {
    if (TERMINAL_CARD_STATES.has(state)) return "";
    const configured = Array.isArray(payload?.actions)
      ? payload.actions.map((item) => text(item).toLowerCase()).filter((item) => CARD_ACTIONS.includes(item))
      : [];
    const actions = configured.length ? [...new Set(configured)] : CARD_ACTIONS;
    return `<div class="dialogue-card-actions" aria-label="确认这条猜测">${actions
      .map((action) => {
        const disabled = state === "processing" || (state === "discussing" && action === "discuss");
        return `<button type="button" class="dialogue-card-action is-${action}" data-card-action="${action}"${disabled ? " disabled" : ""}>${escapeHtml(CARD_ACTION_LABELS[action])}</button>`;
      })
      .join("")}</div>`;
  }

  function renderCardMarkup(turn) {
    const payload = turn.payload;
    const state = normalizedCardState(turn);
    const title = text(payload.title) || text(turn.subject_title) || text(turn.message) || "这条猜测";
    const stateLabel = CARD_STATE_LABELS[state] || "";
    return `<article ${turnAttributes(turn, "dialogue-card")} data-card-state="${state}"><p class="dialogue-card-kicker">阿B 的猜测</p><h3 class="dialogue-card-title">${escapeHtml(title)}</h3>${evidenceMarkup(payload)}${stateLabel ? `<p class="dialogue-card-state" role="status">${escapeHtml(stateLabel)}</p>` : ""}${cardActions(payload, state)}</article>`;
  }

  function renderQuestionMarkup(turn, surface) {
    const payload = turn.payload;
    const reply = text(turn.reply) || text(payload.title) || text(turn.subject_title);
    const bubble = textBubbleMarkup("agent", reply, turn, "assistant", surface, "dialogue-question");
    if (!bubble || !evidenceRefs(payload).length) return bubble;
    return `<div ${turnAttributes(turn, "dialogue-question-shell")}>${bubble}${evidenceMarkup(payload)}</div>`;
  }

  function renderTextTurnMarkup(turn, surface) {
    const failed = ["error", "failed"].includes(text(turn?.status).toLowerCase());
    const reply = failed
      ? text(turn?.error) || "这句还没发出去，稍后再试。"
      : text(turn?.reply) || text(turn?.assistant_message);
    return [
      textBubbleMarkup("user", turn?.message || turn?.user_message, turn, "user", surface),
      textBubbleMarkup("agent", reply, turn, "assistant", surface),
    ].join("");
  }

  function renderTurnMarkup(turn, options = {}) {
    const surface = options.surface === "desktop" ? "desktop" : "popup";
    if (isCardTurn(turn)) return renderCardMarkup(turn);
    if (isQuestionTurn(turn)) return renderQuestionMarkup(turn, surface);
    return renderTextTurnMarkup(turn, surface);
  }

  function renderPendingListMarkup(items) {
    const list = Array.isArray(items) ? items.filter(isRecord) : [];
    if (!list.length) {
      return '<p class="dialogue-pending-empty">暂时没有待聊的确认。</p>';
    }
    return list
      .map((item) => {
        const kind = text(item.kind) === "confusion" ? "有点疑惑" : "想确认";
        const confidence = Number(item.confidence);
        const confidenceText = Number.isFinite(confidence)
          ? `<span class="dialogue-pending-confidence">${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%</span>`
          : "";
        return `<article class="dialogue-pending-item" data-confirmation-kind="${escapeHtml(item.kind)}"><div class="dialogue-pending-copy"><span class="dialogue-pending-kind">${kind}</span><strong>${escapeHtml(item.title || "这件事")}</strong>${confidenceText}</div><button type="button" data-confirmation-ref="${escapeHtml(item.ref)}">打开</button></article>`;
      })
      .join("");
  }

  function selectDialogueTurns(items) {
    const list = Array.isArray(items) ? items : [];
    return list
      .map((turn, index) => ({ turn, index }))
      .filter(({ turn }) => isDialogueTurn(turn))
      .sort((left, right) => {
        const byTime = text(left.turn.created_at).localeCompare(text(right.turn.created_at));
        return byTime || left.index - right.index;
      })
      .map(({ turn }) => turn);
  }

  const api = {
    CARD_ACTIONS,
    applyOptimisticCardAction,
    cardActionPath,
    executeCardAction,
    executePendingConfirmationOpen,
    activateReplyQuote,
    clearContextSelection,
    contextBarMarkup,
    contextErrorCode,
    contextErrorMessage,
    contextPath,
    contextSelectionForSubmit,
    contextSelectionFromTurn,
    contextStorageKey,
    normalizeContextPreview,
    isCardTurn,
    isDialogueReplyTurn,
    isDialogueTurn,
    isTerminalCardTurn,
    isQuestionTurn,
    pendingConfirmationOpenPath,
    readableEvidenceValues,
    replaceContextSelection,
    replyQuoteMarkup,
    renderMarkdown,
    renderPendingListMarkup,
    renderTurnMarkup,
    writeContextSelection,
    readContextSelection,
    selectDialogueTurns,
  };
  global.OpenBiliClawDialogueConfirmation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
