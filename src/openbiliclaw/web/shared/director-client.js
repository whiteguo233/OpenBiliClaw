/**
 * Shared director generation/visibility protocol for desktop, mobile and popup.
 * The extension build copies this file; there are no three policy variants.
 * Reads and preloads never acknowledge exposure. Only intersecting mounted
 * cards in a foreground document do. Failed writes reuse their request identity.
 */
(function installDirectorClient(global) {
  "use strict";

  const GENERATION_TIMEOUT_MS = 150_000;
  const RETRY_DELAYS_MS = Object.freeze([600, 1800]);

  function storageOrNull() {
    try { return global.localStorage || null; } catch { return null; }
  }

  function newId() {
    if (global.crypto?.randomUUID) return global.crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  function createClient({ request, storage = storageOrNull(), document: doc = global.document,
    Observer = global.IntersectionObserver, setTimer = global.setTimeout.bind(global),
    clearTimer = global.clearTimeout.bind(global), namespace = "director", baseStream = "default" } = {}) {
    if (typeof request !== "function") throw new TypeError("director request adapter is required");
    const bindings = new Map();
    const pending = new Map();
    const inFlight = new Map();
    const bootstrapActions = new Set();
    const acknowledgements = new Map();
    const observed = new Map();
    let active = false;
    let destroyed = false;

    function pendingId(key) {
      const storageKey = `openbiliclaw.${namespace}.pending.${key}`;
      if (pending.has(storageKey)) return { storageKey, id: pending.get(storageKey) };
      let id = "";
      try { id = String(storage?.getItem(storageKey) || ""); } catch { /* memory fallback */ }
      if (!id) id = newId();
      pending.set(storageKey, id);
      try { storage?.setItem(storageKey, id); } catch { /* memory still retains retry identity */ }
      return { storageKey, id };
    }

    function forget({ storageKey, id }) {
      if (pending.get(storageKey) !== id) return;
      pending.delete(storageKey);
      try { storage?.removeItem(storageKey); } catch { /* conservative stale replay on reload */ }
    }

    function ingest(payload) {
      if (Array.isArray(payload) || Array.isArray(payload?.items)) {
        active = ["shadow", "enforce"].includes(payload?.director?.mode);
      }
      const items = Array.isArray(payload?.items) ? payload.items : [];
      for (const item of items) {
        const batch = String(item?.batch_ref || payload?.director?.batch_ref || "");
        if (!batch || !item?.item_key || !(Number(item.id) > 0)) continue;
        item.batch_ref = batch;
        item.stream_ref = String(item.stream_ref || payload?.director?.stream_ref || "");
        bindings.set(String(item.id), { batch_ref: batch, item_key: String(item.item_key) });
      }
      // A bounded identity registry; never synthesize a batch for a legacy card.
      while (bindings.size > 1000) bindings.delete(bindings.keys().next().value);
      return payload;
    }

    async function generate(path, body = {}, { forceDirector = false } = {}) {
      if (!active && !forceDirector) {
        return ingest(await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
      }
      // Scope is sent separately. The backend derives its strategy stream once;
      // response.stream_ref may already include scope and must not be appended.
      const content = { ...body, stream_ref: baseStream };
      const key = JSON.stringify([path, content.source_platform || "", content.intent || "", content.excluded_bvids || [], content.explicit_intent || "", content.limit || 10]);
      if (inFlight.has(key)) return inFlight.get(key);
      const identity = pendingId(key);
      const operation = (async () => {
        const bootstrapKey = `${identity.storageKey}.bootstrap`;
        let bootstrap = bootstrapActions.has(key);
        try { bootstrap ||= storage?.getItem(bootstrapKey) === "NEXT"; } catch { /* memory fallback */ }
        const bootstrapNext = () => generate("/recommendations/director/generate", { ...body, intent: "NEXT", limit: body.limit || 10 }, { forceDirector: true });
        let response;
        if (bootstrap) {
          response = await bootstrapNext();
        } else {
          try {
            response = await request(path, {
              method: "POST", timeoutMs: GENERATION_TIMEOUT_MS,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ...content, request_id: identity.id }),
            });
          } catch (error) {
            const detail = error?.details?.detail;
            const code = typeof detail === "string" ? detail : detail?.code || detail?.reason || error?.details?.code;
            // A definitive missing-root 409 proves STAY did not commit. Only
            // this case becomes an explicit NEXT with a new id. Persist the
            // redirect before sending NEXT so a lost NEXT response is replayed,
            // never retried as an additional successful STAY.
            if (path !== "/recommendations/reshuffle" || error?.status !== 409 || code !== "stay_without_batch") throw error;
            bootstrapActions.add(key);
            try { storage?.setItem(bootstrapKey, "NEXT"); } catch { /* memory fallback */ }
            response = await bootstrapNext();
          }
        }
        if (!response || !Array.isArray(response.items)) throw new Error("director generation unavailable");
        bootstrapActions.delete(key);
        try { storage?.removeItem(bootstrapKey); } catch { /* stale redirect stays idempotent */ }
        forget(identity);
        return ingest(response);
      })();
      inFlight.set(key, operation);
      try { return await operation; } finally { inFlight.delete(key); }
    }

    async function ensureInitial(payload, { allowGenerate = true, sourcePlatform = "" } = {}) {
      ingest(payload);
      if (!active || !payload?.director?.requires_generation || !allowGenerate || doc?.hidden) return payload;
      return generate("/recommendations/director/generate", {
        intent: "NEXT", limit: 10, ...(sourcePlatform ? { source_platform: sourcePlatform } : {}),
      }, { forceDirector: true });
    }

    function feedback(payload) {
      const binding = bindings.get(String(payload?.recommendation_id));
      return !payload?.batch_ref && binding ? { ...payload, batch_ref: binding.batch_ref } : payload;
    }

    function isVisible(element) {
      if (destroyed || doc?.hidden || !element?.isConnected) return false;
      const rect = element.getBoundingClientRect?.();
      if (!rect || rect.width <= 0 || rect.height <= 0) return false;
      if (element.getClientRects && element.getClientRects().length === 0) return false;
      const view = doc?.defaultView || global;
      const height = view.innerHeight || doc?.documentElement?.clientHeight || 0;
      const width = view.innerWidth || doc?.documentElement?.clientWidth || 0;
      return rect.bottom > 0 && rect.right > 0 && rect.top < height && rect.left < width;
    }

    async function acknowledge(entry) {
      if (entry.done || entry.inFlight || entry.attempts > RETRY_DELAYS_MS.length || !entry.intersecting || !isVisible(entry.element)) return;
      entry.inFlight = true;
      entry.attempts += 1;
      try {
        const response = await request("/recommendations/director/present", {
          method: "POST", timeoutMs: 12_000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ batch_ref: entry.batch, ack_id: entry.ackId, item_keys: [entry.itemKey] }),
        });
        if (!response) throw new Error("presentation acknowledgement unavailable");
        entry.done = true;
      } catch {
        const delay = RETRY_DELAYS_MS[entry.attempts - 1];
        if (delay != null && !destroyed) {
          entry.timer = setTimer(() => { entry.timer = null; void acknowledge(entry); }, delay);
        }
      } finally {
        entry.inFlight = false;
      }
    }

    const observer = typeof Observer === "function" ? new Observer((entries) => {
      for (const observedEntry of entries) {
        const entry = observed.get(observedEntry.target);
        if (!entry) continue;
        if (observedEntry.target.isConnected) entry.mounted = true;
        entry.intersecting = Boolean(observedEntry.isIntersecting && observedEntry.intersectionRatio > 0);
        if (entry.intersecting) void acknowledge(entry);
      }
    }, { threshold: 0 }) : null;

    function bindCard(element, item) {
      if (!element || !item?.batch_ref || !item?.item_key || !(Number(item.id) > 0)) return;
      const batch = String(item.batch_ref);
      const itemKey = String(item.item_key);
      const key = `${batch}:${itemKey}`;
      element.dataset.batchRef = batch;
      element.dataset.streamRef = String(item.stream_ref || "");
      element.dataset.itemKey = itemKey;
      element.dataset.recommendationId = String(item.id);
      if (!observer) return;
      let entry = acknowledgements.get(key);
      if (!entry) {
        entry = { element, batch, itemKey, ackId: `visible:${batch}:${item.id}`, attempts: 0, done: Boolean(item.presented), inFlight: false, intersecting: false, mounted: Boolean(element.isConnected), timer: null };
        acknowledgements.set(key, entry);
      } else {
        if (entry.element !== element) {
          observer.unobserve(entry.element);
          observed.delete(entry.element);
        }
        entry.element = element;
        entry.intersecting = false;
      }
      observed.set(element, entry);
      observer.observe(element);
      // Avoid retaining detached cards as the rolling list is replaced.
      for (const [node, previous] of observed) {
        if (node !== element && previous.mounted && !node.isConnected) {
          observer.unobserve(node);
          observed.delete(node);
          if (previous.element === node) previous.intersecting = false;
        }
      }
      while (acknowledgements.size > 1000) {
        const oldestKey = acknowledgements.keys().next().value;
        const oldest = acknowledgements.get(oldestKey);
        if (oldest?.timer != null) clearTimer(oldest.timer);
        acknowledgements.delete(oldestKey);
      }
    }

    const onVisibility = () => {
      if (doc?.hidden) return;
      for (const entry of observed.values()) if (entry.intersecting) void acknowledge(entry);
    };
    doc?.addEventListener?.("visibilitychange", onVisibility);
    return Object.freeze({ ingest, generate, ensureInitial, feedback, bindCard,
      get enabled() { return active; },
      destroy() {
        destroyed = true;
        observer?.disconnect();
        for (const entry of acknowledgements.values()) if (entry.timer != null) clearTimer(entry.timer);
        doc?.removeEventListener?.("visibilitychange", onVisibility);
        observed.clear();
      },
    });
  }

  global.OpenBiliClawDirectorClient = Object.freeze({ createClient });
})(globalThis);
