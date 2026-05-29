(() => {
    const DEFAULT_API_BASE = "http://127.0.0.1:8420/api";
    const ENDPOINTS = {
      health: "/health",
      recommendations: "/recommendations",
      refresh: "/recommendations/refresh",
      reshuffle: "/recommendations/reshuffle",
      append: "/recommendations/append",
      runtimeStatus: "/runtime-status",
      activityFeed: "/activity-feed",
      notificationPending: "/notifications/pending",
      notificationSent: "/notifications/sent",
      delightBatch: "/delight/pending-batch",
      delightRespond: "/delight/respond",
      profile: "/profile-summary",
      feedback: "/feedback",
      click: "/recommendation-click",
      chatTurns: "/chat/turns",
      interestProbeRespond: "/interest-probes/respond",
      avoidanceProbeRespond: "/avoidance-probes/respond",
      sourceShareSuggestion: "/config/source-share-suggestion",
      config: "/config?reveal_keys=true",
      watchLater: "/watch-later",
      favorites: "/favorites",
      profileEdit: "/profile/edit",
      profileEditState: "/profile/edit-state"
    };

    const state = {
      query: "",
      filter: "全部",
      activeFeedback: null,
      profile: null,
      editingProfile: false,
      profileEditState: null,
      activity: null,
      activityItems: [],
      activityCursor: "",
      activityHasMore: false,
      profileCognitionCursor: "",
      profileCognitionHasMore: false,
      delights: [],
      delightIndex: 0,
      delight: null,
      config: null,
      runtimeStatus: null,
      runtimeSocket: null,
      videos: [
        { id: 4268, bvid: "BV1KMwuzdEcB", title: "为什么说回县城你也躺不平：县域经济的明斯基时刻", up: "硬核半佛宇宙", topic: "宏观债务", platform: "bilibili", duration: "28:41", presented: false, reason: "你最近一直在盯地缘政治和债务周期，这条用长链条把土地财政、县域就业和个人选择连起来。" },
        { id: 4269, bvid: "yt-arch-001", title: "Concrete, light and silence: why Tadao Ando still feels modern", up: "Design Essays", topic: "建筑美学", platform: "youtube", duration: "18:09", presented: false, reason: "这是系统猜测兴趣：你对结构、空间和最少元素构建最大张力的内容反应更好。" },
        { id: 4270, bvid: "dy-feed-882", title: "参数化设计不是炫技：从结构优化看复杂曲面", up: "结构可视化", topic: "参数化设计", platform: "douyin", duration: "04:36", presented: true, reason: "短视频来源用于补足你的轻量入口，但只保留解释密度较高的候选。" },
        { id: 4271, bvid: "xhs-119", title: "手冲咖啡器具选择：为什么滤杯几何会改变口感", up: "桌面实验室", topic: "咖啡器具", platform: "xiaohongshu", duration: "07:22", presented: false, reason: "小红书兴趣信号和 B 站工艺内容交叉，系统认为你可能会喜欢“器物背后的结构逻辑”。" },
        { id: 4272, bvid: "BV1OpenClaw", title: "大模型 Agent 为什么需要长期记忆，而不是更长上下文", up: "工程师的抽屉", topic: "AI Agent", platform: "bilibili", duration: "36:12", presented: false, reason: "你近期对本地化、可控和个人数据归属更敏感，这条能解释 OpenBiliClaw 的底层取向。" },
        { id: 4273, bvid: "yt-macro-144", title: "The plumbing of money markets, explained with one balance sheet", up: "Macro Notes", topic: "金融机制", platform: "youtube", duration: "22:15", presented: false, reason: "它不是新闻，而是机制解释；与你的“先看齿轮怎么咬合”的认知风格更匹配。" }
      ],
      messages: [],
      messageListSnapshot: null,
      messageListDomLocked: false,
      resolvingMessageKeys: new Set(),
      resolvedMessageResults: new Map(),
      handledProbeKeys: new Set(),
      messageScrollTop: 0,
      messageChatDomain: "",
      messageChatPrompt: "",
      messageChatScope: "probe",
      messageChatSubjectTitle: "",
      chat: [
        { role: "agent", text: "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。" }
      ]
    };

    const $ = (selector) => document.querySelector(selector);
    const grid = $("#videoGrid");
    const sourceFilterOrder = ["B 站", "YouTube", "抖音", "小红书"];
    const platformLabel = { bilibili: "B 站", youtube: "YouTube", douyin: "抖音", xiaohongshu: "小红书", xhs: "小红书" };
    const CHAT_PLACEHOLDERS = [
      "说说你最近怎么想——你是什么样的人、喜欢什么、讨厌什么，都可以直接说。",
      "比如：我喜欢慢慢讲清楚的长视频，讨厌标题党和故意搞悬念的。",
      "比如：最近老点开国际新闻和商业分析，想知道自己到底在找什么。",
      "比如：我经常刷到一半就退出，好像注意力很难集中。",
      "比如：我偏爱小众冷门内容，热门排行榜上的反而不太想看。",
      "比如：这阵子心情一般，老看一些治愈系的东西。",
      "比如：我在学一门新技能，想看看有没有靠谱教程。"
    ];
    let chatPlaceholderIndex = 0;
    let chatPlaceholderTimer = null;
    let activityRailHeightFrame = 0;
    let backendHydrationTimer = null;
    let backendHydrationInFlight = false;
    let backendHydrationPending = false;
    let activityPageRefreshTimer = null;
    let activityPageRefreshInFlight = false;
    let activityPageRefreshPending = false;

    function debounceAsync(fn, delayMs = 1000) {
      let timer = null;
      let inFlight = false;
      let pending = false;
      const run = async () => {
        if (inFlight) { pending = true; return; }
        inFlight = true;
        try { await fn(); } finally {
          inFlight = false;
          if (pending) { pending = false; timer = window.setTimeout(run, 0); }
        }
      };
      return () => {
        if (timer !== null) window.clearTimeout(timer);
        timer = window.setTimeout(() => { timer = null; run(); }, delayMs);
      };
    }

    const scheduleDelightQueueRefresh = debounceAsync(() => fetchDelightQueue(), 1000);

    async function runBackendHydration() {
      if (backendHydrationInFlight) {
        backendHydrationPending = true;
        return;
      }
      backendHydrationInFlight = true;
      try {
        await hydrateFromBackend();
      } finally {
        backendHydrationInFlight = false;
        if (backendHydrationPending) {
          backendHydrationPending = false;
          backendHydrationTimer = window.setTimeout(() => {
            backendHydrationTimer = null;
            void runBackendHydration();
          }, 0);
        }
      }
    }

    function scheduleBackendHydration() {
      if (backendHydrationTimer !== null) window.clearTimeout(backendHydrationTimer);
      backendHydrationTimer = window.setTimeout(() => {
        backendHydrationTimer = null;
        void runBackendHydration();
      }, 1000);
    }

    async function runActivityPageRefresh() {
      if (activityPageRefreshInFlight) {
        activityPageRefreshPending = true;
        return;
      }
      activityPageRefreshInFlight = true;
      try {
        await loadActivityPage({ reset: true });
      } finally {
        activityPageRefreshInFlight = false;
        if (activityPageRefreshPending) {
          activityPageRefreshPending = false;
          activityPageRefreshTimer = window.setTimeout(() => {
            activityPageRefreshTimer = null;
            void runActivityPageRefresh();
          }, 0);
        }
      }
    }

    function scheduleActivityPageRefresh() {
      if (activityPageRefreshTimer !== null) window.clearTimeout(activityPageRefreshTimer);
      activityPageRefreshTimer = window.setTimeout(() => {
        activityPageRefreshTimer = null;
        void runActivityPageRefresh();
      }, 1000);
    }

    function syncActivityRailHeight() {
      const rail = document.querySelector('[data-od-id="activity-rail"]');
      const delight = document.getElementById("delightBanner");
      if (!rail || !delight || !window.matchMedia("(min-width: 1181px)").matches) {
        rail?.style.removeProperty("--activity-rail-max-height");
        return;
      }
      const height = Math.ceil(delight.getBoundingClientRect().height);
      if (height > 0) rail.style.setProperty("--activity-rail-max-height", `${height}px`);
    }

    function scheduleActivityRailHeightSync() {
      if (activityRailHeightFrame) cancelAnimationFrame(activityRailHeightFrame);
      activityRailHeightFrame = requestAnimationFrame(() => {
        activityRailHeightFrame = 0;
        syncActivityRailHeight();
      });
    }

    function showFatal(error, context = "页面启动") {
      const message = error?.message || String(error || "未知错误");
      const banner = $("#fatalBanner");
      if (banner) {
        banner.textContent = `${context}出现问题：${message}`;
        banner.classList.add("is-open");
      }
      const status = $("#statusLabel");
      if (status) status.textContent = `${context}异常`;
      const summary = $("#runtimeSummary");
      if (summary) summary.textContent = message;
      console.error(context, error);
    }

    window.addEventListener("error", (event) => showFatal(event.error || event.message, "页面脚本"));
    window.addEventListener("unhandledrejection", (event) => showFatal(event.reason, "异步加载"));

    function storageGet(key) {
      try { return window.localStorage?.getItem(key) || ""; } catch { return ""; }
    }

    function storageSet(key, value) {
      try { window.localStorage?.setItem(key, value); } catch {}
    }

    const DISMISS_ON_RESHUFFLE_KEY = "openbiliclaw.dismissOnReshuffle";
    state.dismissOnReshuffle = storageGet(DISMISS_ON_RESHUFFLE_KEY) === "1";
    const SIDE_DRAWER_OPEN_KEY = "openbiliclaw.sideDrawerOpen";
    const DELIGHT_QUEUE_LIMIT_KEY = "openbiliclaw.webui.delightQueueLimit";

    function normalizeBackendHost(host) {
      const trimmed = String(host || "").trim();
      if (!trimmed) return "127.0.0.1";
      try { return new URL(trimmed).hostname || "127.0.0.1"; } catch { return trimmed.replace(/^https?:\/\//, "").replace(/\/.*$/, ""); }
    }

    function safeBind(selector, eventName, handler) {
      const element = $(selector);
      if (!element) { showFatal(new Error(`缺少元素 ${selector}`), "绑定交互"); return; }
      element.addEventListener(eventName, handler);
    }

    function locationApiDefault() {
      try {
        const loc = window.location;
        if (loc && /^https?:$/.test(loc.protocol) && loc.hostname) {
          return { host: loc.hostname, port: loc.port || (loc.protocol === "https:" ? "443" : "80") };
        }
      } catch { /* file:// or no window — fall through */ }
      return { host: "127.0.0.1", port: "8420" };
    }

    function getApiBase() {
      // Default to the origin the page is served from, so a non-default backend
      // port "just works" and a profile edit never silently writes to a
      // different backend on :8420. An explicit saved/typed setting still wins.
      const def = locationApiDefault();
      const host = normalizeBackendHost($("#backendHost")?.value || storageGet("openbiliclaw.webui.backendHost") || def.host);
      const port = String($("#backendPort")?.value || storageGet("openbiliclaw.webui.backendPort") || def.port).trim() || def.port;
      return `http://${host}:${port}/api`;
    }

    function restoreBackendEndpoint() {
      const host = storageGet("openbiliclaw.webui.backendHost");
      const port = storageGet("openbiliclaw.webui.backendPort");
      if (host) setInput("backendHost", normalizeBackendHost(host));
      if (port) setInput("backendPort", port);
    }

    function persistBackendEndpoint() {
      const def = locationApiDefault();
      const host = normalizeBackendHost($("#backendHost")?.value || def.host);
      const port = String($("#backendPort")?.value || def.port).trim() || def.port;
      setInput("backendHost", host);
      setInput("backendPort", port);
      storageSet("openbiliclaw.webui.backendHost", host);
      storageSet("openbiliclaw.webui.backendPort", port);
      return { host, port };
    }

    function getDelightQueueLimit() {
      const raw = $("#delightQueueLimit")?.value || storageGet(DELIGHT_QUEUE_LIMIT_KEY) || "20";
      const limit = Number.parseInt(String(raw), 10);
      if (!Number.isFinite(limit)) return 20;
      return Math.max(1, Math.min(100, limit));
    }

    function restoreFrontendSettings() {
      const limit = storageGet(DELIGHT_QUEUE_LIMIT_KEY);
      setInput("delightQueueLimit", limit || "20");
      renderReshuffleToggle();
    }

    function persistFrontendSettings() {
      const limit = getDelightQueueLimit();
      setInput("delightQueueLimit", String(limit));
      storageSet(DELIGHT_QUEUE_LIMIT_KEY, String(limit));
      storageSet(DISMISS_ON_RESHUFFLE_KEY, state.dismissOnReshuffle ? "1" : "0");
      renderReshuffleToggle();
      return { delightQueueLimit: limit, dismissOnReshuffle: state.dismissOnReshuffle };
    }

    function getRuntimeStreamUrl() {
      return `${getApiBase().replace(/^http/, "ws")}/runtime-stream`;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
    }

    // Decode source-provided entities for display text only; every later HTML or attribute output must still escape by context.
    function decodeHtmlEntities(value) {
      return String(value ?? "").replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos|#39);/g, (match, entity) => {
        if (entity === "amp") return "&";
        if (entity === "lt") return "<";
        if (entity === "gt") return ">";
        if (entity === "quot") return '"';
        if (entity === "apos" || entity === "#39") return "'";
        if (entity.startsWith("#x")) {
          const codePoint = Number.parseInt(entity.slice(2), 16);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        if (entity.startsWith("#")) {
          const codePoint = Number.parseInt(entity.slice(1), 10);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        return match;
      });
    }

    function normalizeRecommendation(item) {
      return {
        id: Number(item?.id ?? Date.now()),
        bvid: String(item?.bvid ?? item?.content_id ?? ""),
        title: decodeHtmlEntities(item?.title ?? "未命名内容"),
        up: decodeHtmlEntities(item?.up_name ?? item?.up ?? "未知创作者"),
        cover_url: normalizeImageUrl(item?.cover_url ?? item?.cover ?? item?.pic ?? item?.thumbnail_url ?? item?.thumbnail ?? item?.image_url),
        content_url: String(item?.content_url ?? ""),
        topic: decodeHtmlEntities(item?.topic_label ?? item?.topic ?? "未归类"),
        platform: String(item?.source_platform ?? item?.platform ?? "bilibili"),
        duration: String(item?.duration ?? ""),
        presented: Boolean(item?.presented),
        feedback_type: String(item?.feedback_type ?? item?.feedback ?? ""),
        pool_status: String(item?.pool_status ?? item?.status ?? ""),
        reason: decodeHtmlEntities(item?.expression ?? item?.reason ?? "后端暂未返回解释。")
      };
    }

    function recommendationKey(item) {
      return String(item?.bvid || item?.content_id || item?.id || "");
    }

    function isFeedbackedRecommendation(item) {
      const feedback = String(item?.feedback_type || item?.feedback || "").trim();
      const poolStatus = String(item?.pool_status || item?.status || "").trim().toLowerCase();
      return Boolean(feedback) || poolStatus === "feedbacked";
    }

    function normalizeRecommendationList(items) {
      return asArray(items).map(normalizeRecommendation).filter((item) => !isFeedbackedRecommendation(item));
    }

    async function requestJson(path, options = {}) {
      try {
        return await requestJsonStrict(path, { ...options, timeoutMs: options.timeoutMs ?? 15000 });
      } catch {
        return null;
      }
    }

    async function requestJsonStrict(path, options = {}) {
      const base = options.baseUrl || getApiBase() || DEFAULT_API_BASE;
      const { baseUrl, timeoutMs = 60000, signal, ...fetchOptions } = options;
      const controller = signal ? null : new AbortController();
      const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
      try {
        const response = await fetch(`${base}${path}`, { ...fetchOptions, signal: signal || controller?.signal });
        const contentType = response.headers.get("content-type") || "";
        const details = contentType.includes("application/json") ? await response.json().catch(() => null) : await response.text().catch(() => "");
        if (!response.ok) {
          const error = new Error(configErrorMessage(details) || `${path} 请求失败：HTTP ${response.status}`);
          error.status = response.status;
          error.details = details;
          throw error;
        }
        return details;
      } catch (error) {
        if (error?.name === "AbortError") throw new Error(`${path} 请求超时，请稍后刷新确认是否已写入。`);
        throw error;
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
      }
    }

    function configErrorMessage(details) {
      if (!details) return "";
      if (typeof details === "string") return details;
      const issues = details.config?.issues || details.detail?.config?.issues;
      if (Array.isArray(issues) && issues.length) {
        return issues.map((issue) => `${issue.severity || "warning"}: ${issue.message || issue.code || JSON.stringify(issue)}`).join("\n");
      }
      if (Array.isArray(details.detail)) {
        return details.detail.map((item) => `${item.loc?.join(".") || "字段"}: ${item.msg || JSON.stringify(item)}`).join("\n");
      }
      return details.message || details.detail?.message || details.detail?.error || details.error || "";
    }

    function showToast(message) {
      const toast = $("#toast");
      toast.textContent = message;
      toast.classList.add("is-open");
      window.setTimeout(() => toast.classList.remove("is-open"), 2600);
    }

    function openPanel(id) { document.getElementById(id)?.classList.add("is-open"); }
    function closePanel(id) {
      const panel = document.getElementById(id);
      panel?.classList.remove("is-open", "from-mobile-menu");
      if (id === "messagesDrawer") {
        state.messageListSnapshot = null;
        state.messageListDomLocked = false;
      }
    }

    const MAIN_PAGE_IDS = ["homePage", "watchLaterPage", "favoritesPage", "profilePage", "chatPage", "settingsPage"];

    function showMainPage(pageId) {
      MAIN_PAGE_IDS.forEach((id) => {
        const page = document.getElementById(id);
        if (!page) return;
        if (id === pageId) page.removeAttribute("hidden");
        else page.setAttribute("hidden", "");
      });
      document.body.classList.toggle("profile-page-open", pageId === "profilePage");
      document.body.classList.toggle("chat-page-open", pageId === "chatPage");
      document.body.classList.toggle("content-page-open", pageId !== "homePage");
    }

    function syncTopbarHeight() {
      const topbar = document.querySelector(".topbar");
      if (!topbar) return;
      document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(topbar.getBoundingClientRect().height)}px`);
    }

    function openHomePage() {
      showMainPage("homePage");
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openProfilePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("profilePage");
      renderProfileDetails();
      void refreshProfile().catch(() => {});
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openChatPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("chatPage");
      const input = document.getElementById("chatInput");
      window.scrollTo({ top: 0, behavior: "smooth" });
      window.setTimeout(() => input?.focus(), 100);
    }

    function openSettingsPage(panel = "models") {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((drawer) => closePanel(drawer.id));
      setActiveSettingsPanel(panel || "models");
      showMainPage("settingsPage");
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    // ── Saved pages: 稍后再看 (watch-later) & 收藏 (favorites) ──────
    // The two are independent backend collections sharing one list UI.

    function watchLaterStatus(bvid) {
      return requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`);
    }

    function favoriteStatus(bvid) {
      return requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`);
    }

    function updateSavedBadge(badgeId, total) {
      const badge = document.getElementById(badgeId);
      if (!badge) return;
      const n = Number(total) || 0;
      if (n > 0) {
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.removeAttribute("hidden");
      } else {
        badge.textContent = "";
        badge.setAttribute("hidden", "");
      }
    }

    function renderSavedList(listId, emptyId, items, onRemove) {
      const grid = document.getElementById(listId);
      const empty = document.getElementById(emptyId);
      if (!grid) return;
      const rows = Array.isArray(items) ? items : [];
      if (!rows.length) {
        grid.replaceChildren();
        if (empty) empty.removeAttribute("hidden");
        return;
      }
      if (empty) empty.setAttribute("hidden", "");
      grid.replaceChildren(...rows.map((item) => {
        const card = document.createElement("article");
        card.className = "video-card saved-card";
        const url = contentUrl(item);
        card.innerHTML = `
          <button class="cover" data-platform="${escapeHtml(item.source_platform || item.platform || "bilibili")}" type="button" aria-label="打开 ${escapeHtml(item.title || item.bvid)}">
            ${coverImg(item)}
            <span class="platform">${escapeHtml(platformName(item.source_platform || item.platform))}</span>
          </button>
          <div>
            <p class="video-title">${escapeHtml(item.title || item.bvid)}</p>
            <p class="video-meta">${escapeHtml(item.up_name || "")}</p>
          </div>
          <div class="card-actions saved-card-actions">
            <button class="small-btn saved-remove" type="button">移除</button>
          </div>`;
        card.querySelector(".cover").addEventListener("click", () => {
          if (url) window.open(url, "_blank", "noopener,noreferrer");
        });
        card.querySelector(".saved-remove").addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await onRemove(item.bvid);
            card.remove();
          } catch {
            btn.disabled = false;
          }
        });
        return card;
      }));
    }

    async function refreshWatchLater() {
      const data = await requestJson(`${ENDPOINTS.watchLater}?limit=100&offset=0`).catch(() => null);
      renderSavedList("watchLaterList", "watchLaterEmpty", data?.items, async (bvid) => {
        await requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
        await refreshWatchLater();
        syncWatchLaterButtons();
      });
      updateSavedBadge("watchLaterCountBadge", data?.total);
    }

    async function refreshFavorites() {
      const data = await requestJson(`${ENDPOINTS.favorites}?limit=100&offset=0`).catch(() => null);
      renderSavedList("favoritesList", "favoritesEmpty", data?.items, async (bvid) => {
        await requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
        await refreshFavorites();
        syncFavoriteButtons();
      });
      updateSavedBadge("favoritesCountBadge", data?.total);
    }

    // Re-sync the pressed state + count badge for all visible ☆/♥ toggles.
    function syncWatchLaterButtons() {
      requestJson(`${ENDPOINTS.watchLater}?limit=200&offset=0`).then((data) => {
        const saved = new Set((data?.items || []).map((it) => it.bvid));
        document.querySelectorAll('.video-card [data-action="watch-later"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const bvid = card?.dataset?.bvid;
          if (!bvid) return;
          const on = saved.has(bvid);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateSavedBadge("watchLaterCountBadge", data?.total);
      }).catch(() => {});
    }

    function syncFavoriteButtons() {
      requestJson(`${ENDPOINTS.favorites}?limit=200&offset=0`).then((data) => {
        const saved = new Set((data?.items || []).map((it) => it.bvid));
        document.querySelectorAll('.video-card [data-action="favorite"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const bvid = card?.dataset?.bvid;
          if (!bvid) return;
          const on = saved.has(bvid);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateSavedBadge("favoritesCountBadge", data?.total);
      }).catch(() => {});
    }

    function openWatchLaterPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("watchLaterPage");
      void refreshWatchLater();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openFavoritesPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("favoritesPage");
      void refreshFavorites();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function setSideDrawerOpen(open, { persist = true } = {}) {
      const drawer = document.getElementById("sideDrawer");
      drawer?.classList.toggle("is-open", open);
      drawer?.setAttribute("aria-hidden", open ? "false" : "true");
      document.body.classList.toggle("side-drawer-open", open);
      const button = document.getElementById("sideDrawerBtn");
      if (button) {
        button.setAttribute("aria-expanded", open ? "true" : "false");
        button.setAttribute("aria-label", open ? "收起侧边菜单" : "展开侧边菜单");
      }
      if (persist) storageSet(SIDE_DRAWER_OPEN_KEY, open ? "1" : "0");
    }

    function openSideDrawer(options) {
      setSideDrawerOpen(true, options);
    }

    function closeSideDrawer(options) {
      setSideDrawerOpen(false, options);
    }

    function toggleSideDrawer() {
      const drawer = document.getElementById("sideDrawer");
      setSideDrawerOpen(!drawer?.classList.contains("is-open"));
    }

    function isMobileViewport() {
      return window.matchMedia?.("(max-width: 820px)").matches;
    }

    function syncMobileSearch() {
      const input = $("#mobileSearchInput");
      if (input && input.value !== state.query) input.value = state.query || "";
    }

    function openMobileMenu() {
      syncMobileSearch();
      renderRail();
      document.body.classList.add("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.add("is-open");
    }

    function closeMobileMenu() {
      document.body.classList.remove("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.remove("is-open");
    }

    function openMobilePanel(id, options = {}) {
      closeMobileMenu();
      if (id === "messagesDrawer") {
        hydrateInboxFromSpeculations(state.profile?.speculative_interests);
        hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
        state.messageListSnapshot = getRenderableMessages();
        returnToMessages();
        renderMessages();
        void refreshProfile().catch(() => {});
      }
      if (id === "activityDrawer") renderActivityHistory();
      const panel = document.getElementById(id);
      panel?.classList.add("from-mobile-menu");
      openPanel(id);
    }

    function openMobilePage(id, options = {}) {
      if (id === "profilePage") openProfilePage();
      if (id === "chatPage") openChatPage();
      if (id === "settingsPage") openSettingsPage(options.settingsPanel || "models");
    }

    function returnToMobileMenu(event) {
      const panel = event.target.closest(".drawer, .overlay");
      if (panel?.id) closePanel(panel.id);
      openMobileMenu();
    }

    function platformName(value) {
      return platformLabel[String(value || "").toLowerCase()] || String(value || "").trim();
    }

    function buildFilters() {
      const sourceSet = new Set();
      for (const item of state.videos) {
        const label = platformName(item.platform);
        if (label) sourceSet.add(label);
      }
      const sources = sourceFilterOrder.filter((name) => sourceSet.has(name));
      const otherSources = [...sourceSet].filter((name) => !sourceFilterOrder.includes(name)).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
      return ["全部", ...sources, ...otherSources];
    }

    function filteredVideos() {
      const q = state.query.trim().toLowerCase();
      return state.videos.filter((item) => {
        const label = platformName(item.platform);
        const filterOk = state.filter === "全部" || state.filter === label;
        const queryOk = !q || [item.title, item.up, item.topic, item.reason, label].join(" ").toLowerCase().includes(q);
        return filterOk && queryOk;
      });
    }

    function setDismissOnReshuffle(enabled, { persist = true, toast = false } = {}) {
      state.dismissOnReshuffle = Boolean(enabled);
      if (persist) storageSet(DISMISS_ON_RESHUFFLE_KEY, state.dismissOnReshuffle ? "1" : "0");
      renderReshuffleToggle();
      if (toast) showToast(state.dismissOnReshuffle ? "换一批前会忽略当前显示的推荐" : "换一批不会自动忽略当前推荐");
    }

    function renderReshuffleToggle() {
      const toggles = [$("#dismissOnReshuffleToggle"), $("#dismissOnReshuffleSetting")];
      toggles.forEach((toggle) => {
        if (toggle && toggle.checked !== state.dismissOnReshuffle) toggle.checked = state.dismissOnReshuffle;
      });
      const settingText = $("#dismissOnReshuffleSettingText");
      if (settingText) settingText.textContent = state.dismissOnReshuffle ? "开启" : "关闭";
    }

    function renderFilters() {
      const row = $("#filterRow");
      const filters = buildFilters();
      if (!filters.includes(state.filter)) state.filter = "全部";
      row.replaceChildren(...filters.map((name) => {
        const btn = document.createElement("button");
        btn.className = `chip${state.filter === name ? " is-active" : ""}`;
        btn.type = "button";
        btn.textContent = name;
        btn.addEventListener("click", () => { state.filter = name; renderAll(); });
        return btn;
      }));
      const resetButton = $("#resetFiltersBtn");
      if (resetButton) resetButton.hidden = state.filter === "全部" && !String(state.query || "").trim();
    }

    function normalizeImageUrl(value) {
      const url = String(value || "").trim();
      if (!url) return "";
      if (url.startsWith("//")) return `https:${url}`;
      if (url.startsWith("http://")) return `https://${url.slice("http://".length)}`;
      return url;
    }

    function imageProxyUrl(value) {
      const url = normalizeImageUrl(value);
      if (!url) return "";
      try {
        new URL(url);
      } catch {
        return "";
      }
      const base = getApiBase() || DEFAULT_API_BASE;
      return `${base}/image-proxy?url=${encodeURIComponent(url)}`;
    }

    function coverImg(item) {
      const url = imageProxyUrl(item.cover_url);
      if (!url) return "";
      // loading="eager" (not lazy): cover starts fetching the moment the card is
      // in the DOM, so a card scrolled into view never shows the gradient
      // placeholder while a native lazy <img> defers its fetch ("白一下再出来").
      return `<img src="${escapeHtml(url)}" alt="${escapeHtml(item.title)} 的封面" loading="eager" fetchpriority="auto" decoding="async" referrerpolicy="no-referrer">`;
    }

    // Warm the browser cache for a batch of cover images before their cards are
    // (re)rendered. Used by appendMore so newly loaded covers paint instantly
    // instead of flashing the placeholder while they download. Resolves on a
    // timeout so one slow cover can't stall the batch.
    const warmedCoverUrls = new Set();
    function warmCoverImages(items, { waitForDecode = false, timeoutMs = 4000 } = {}) {
      if (typeof Image === "undefined") return Promise.resolve();
      const pending = [];
      for (const item of items || []) {
        const src = imageProxyUrl(item?.cover_url);
        if (!src || warmedCoverUrls.has(src)) continue;
        warmedCoverUrls.add(src);
        const img = new Image();
        img.decoding = "async";
        const loaded = new Promise((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
        img.src = src;
        let ready = loaded;
        if (typeof img.decode === "function") ready = img.decode().catch(() => {});
        if (waitForDecode) pending.push(ready);
      }
      if (!waitForDecode || pending.length === 0) return Promise.resolve();
      return Promise.race([Promise.all(pending), new Promise((resolve) => setTimeout(resolve, timeoutMs))]);
    }

    function contentUrl(item) {
      if (item.content_url) return item.content_url;
      if (item.platform === "bilibili" && item.bvid) return `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}`;
      return "";
    }

    function recommendationMeta(item) {
      return [item.up, item.topic]
        .map((part) => String(part || "").trim())
        .filter(Boolean)
        .join(" · ");
    }

    function renderVideos() {
      const items = filteredVideos();
      if (!items.length) {
        const message = state.query.trim()
          ? `没有找到包含“${escapeHtml(state.query.trim())}”的推荐。`
          : state.videos.length
            ? "当前筛选下没有推荐。"
            : "当前列表里的推荐都已处理，可以加载更多推荐或等待后端补货。";
        grid.innerHTML = `<div class="empty-state">${message}</div>`;
        return;
      }
      grid.replaceChildren(...items.map((item) => {
        const card = document.createElement("article");
        card.className = "video-card";
        card.dataset.bvid = item.bvid || item.id;
        card.innerHTML = `
          <button class="cover" data-platform="${escapeHtml(item.platform)}" type="button" aria-label="打开 ${escapeHtml(item.title)}">
            ${coverImg(item)}
            <span class="platform">${escapeHtml(platformName(item.platform))}</span>
          </button>
          <div>
            <p class="video-title">${escapeHtml(item.title)}</p>
            <p class="video-meta">${escapeHtml(recommendationMeta(item))}</p>
          </div>
          <p class="reason" role="button" tabindex="0" aria-expanded="false" title="${escapeHtml(item.reason)}"><span class="reason-text">${escapeHtml(item.reason)}</span></p>
          <div class="card-actions" aria-label="推荐反馈操作">
            <div class="card-feedback-icons" aria-label="喜欢或不感兴趣">
              <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn" data-action="dislike" type="button" aria-label="不感兴趣" title="不感兴趣">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn" data-action="dismiss" type="button" aria-label="忽略" title="忽略">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18M9.84 9.91A3 3 0 0 0 12 15c.82 0 1.57-.33 2.11-.87M6.5 6.65A10.45 10.45 0 0 0 2.46 12C3.73 16.06 7.52 19 12 19c1.99 0 3.84-.58 5.4-1.58M11 5.05c.33-.03.66-.05 1-.05 4.48 0 8.27 2.94 9.54 7a10.5 10.5 0 0 1-1.19 2.5"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn watch-later-btn" data-action="watch-later" type="button" aria-label="稍后再看" title="稍后再看" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>
              </button>
              <span class="feedback-separator" aria-hidden="true">/</span>
              <button class="feedback-icon-btn favorite-btn" data-action="favorite" type="button" aria-label="收藏" title="收藏" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>
              </button>
            </div>
            <div class="comment-field"><input placeholder="想围绕这条聊什么？" aria-label="想围绕这条聊什么？"></div>
            <button class="small-btn composer-cancel" data-action="cancel-comment" type="button" aria-label="返回" title="返回">‹</button>
            <button class="small-btn chat-action" data-action="comment" type="button">聊一聊</button>
          </div>
          <p class="status-line"></p>`;
        const reason = card.querySelector(".reason");
        const toggleReason = () => {
          const expanded = reason.classList.toggle("is-expanded");
          reason.setAttribute("aria-expanded", expanded ? "true" : "false");
        };
        reason.addEventListener("click", toggleReason);
        reason.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            toggleReason();
          }
        });
        card.querySelector(".cover").addEventListener("click", () => openRecommendation(item, card));
        card.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => handleCardAction(btn.dataset.action, item, card)));
        card.querySelector(".comment-field input").addEventListener("keydown", (event) => {
          if (event.key === "Enter") handleCardAction("send-comment", item, card);
          if (event.key === "Escape") closeCardComposer(card);
        });
        // Lazy-load watch-later state
        const wlBtn = card.querySelector('[data-action="watch-later"]');
        if (wlBtn) {
          const bvid = item.bvid || item.id;
          watchLaterStatus(bvid).then((res) => {
            if (res && res.saved) {
              wlBtn.setAttribute("aria-pressed", "true");
              wlBtn.title = "\u53D6\u6D88\u7A0D\u540E\u518D\u770B";
            }
          }).catch(() => {});
        }
        // Lazy-load favorite state
        const favBtn = card.querySelector('[data-action="favorite"]');
        if (favBtn) {
          const bvid = item.bvid || item.id;
          favoriteStatus(bvid).then((res) => {
            if (res && res.saved) {
              favBtn.setAttribute("aria-pressed", "true");
              favBtn.title = "\u53D6\u6D88\u6536\u85CF";
            }
          }).catch(() => {});
        }
        return card;
      }));
    }

    function trackRecommendationClick(item) {
      void requestJson(ENDPOINTS.click, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bvid: item.bvid, title: item.title, recommendation_id: item.id, topic_label: item.topic, up_name: item.up })
      }).catch(() => {});
    }

    function openRecommendation(item, card) {
      const url = contentUrl(item);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      trackRecommendationClick(item);
      card.querySelector(".status-line").textContent = url ? "已打开真实内容链接，点击信号会在后台记录。" : "后端没有返回可打开链接；点击信号会在后台记录。";
      showToast(url ? `打开：${item.title}` : "后端没有返回可打开链接");
    }

    async function submitFeedback(item, feedback_type, note = "") {
      return await requestJsonStrict(ENDPOINTS.feedback, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: item.id, feedback_type, note }),
        timeoutMs: 30000
      });
    }

    function recommendationRemoveDelay() {
      return isMobileViewport() ? 1000 : 2400;
    }

    function removeRecommendationCard(item, card, message, delayMs = recommendationRemoveDelay()) {
      const key = recommendationKey(item);
      window.setTimeout(() => {
        if (card) card.classList.add("is-removing");
        window.setTimeout(() => {
          state.videos = state.videos.filter((video) => recommendationKey(video) !== key);
          renderAll();
          if (message) showToast(message);
        }, card ? 180 : 0);
      }, card ? delayMs : 0);
    }

    const sendIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 12 20 4l-5 16-3.2-6.8L4 12Z"/><path d="m11.8 13.2 3.7-3.7"/></svg>';

    function openCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.action = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      requestAnimationFrame(() => card.querySelector(".comment-field input")?.focus());
    }

    function closeCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.action = "comment";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
    }

    function openDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button || !state.delight) return;
      shell?.classList.add("is-composing");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.delight = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      scheduleActivityRailHeightSync();
      requestAnimationFrame(() => $("#delightCommentInput")?.focus());
    }

    function closeDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button) return;
      shell?.classList.remove("is-composing");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.delight = "chat";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
      scheduleActivityRailHeightSync();
    }

    async function handleCardAction(action, item, card) {
      const status = card.querySelector(".status-line");
      if (card.dataset.feedbackPending === "true") return;
      if (action === "open") return openRecommendation(item, card);
      if (action === "comment") { openCardComposer(card); return; }
      if (action === "cancel-comment") { closeCardComposer(card); return; }
      if (action === "watch-later") {
        const btn = card.querySelector('[data-action="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u7A0D\u540E\u518D\u770B" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u7A0D\u540E\u518D\u770B" : "\u7A0D\u540E\u518D\u770B";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (action === "favorite") {
        const btn = card.querySelector('[data-action="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u6536\u85CF" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u6536\u85CF" : "\u6536\u85CF";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      card.dataset.feedbackPending = "true";
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = true; });
      try {
        if (action === "send-comment") {
          const input = card.querySelector(".comment-field input");
          const note = input.value.trim();
          if (!note) {
            delete card.dataset.feedbackPending;
            card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
            status.textContent = "先写一句想聊的内容，再提交这条反馈。";
            input?.focus();
            return;
          }
          await submitFeedback(item, "comment", note);
          if (input) input.value = "";
          closeCardComposer(card);
          status.textContent = "已提交聊天线索，几秒后从当前列表移除。";
          removeRecommendationCard(item, card, "已提交聊天线索");
          return;
        }
        const feedbackType = action === "like" ? "like" : action === "dismiss" ? "dismiss" : "dislike";
        await submitFeedback(item, feedbackType);
        const feedbackCopy = {
          like: ["已记录喜欢，几秒后从当前列表移除。", "已记录喜欢"],
          dislike: ["已记录不感兴趣，几秒后从当前列表移除。", "已记录不感兴趣"],
          dismiss: ["已忽略这条推荐，几秒后从当前列表移除。", "已忽略推荐"]
        }[feedbackType];
        status.textContent = feedbackCopy[0];
        removeRecommendationCard(item, card, feedbackCopy[1]);
      } catch (error) {
        delete card.dataset.feedbackPending;
        card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
        status.textContent = configErrorMessage(error?.details) || error?.message || "反馈提交失败，请稍后重试。";
        showToast(status.textContent);
      }
    }

    function renderRail() {
      const profile = state.profile;
      const portraitText = profile?.personality_portrait ? valueList(profile.personality_portrait) : "偏好结构化解释、长视频和跨学科桥接，对“为什么”比“是什么”更敏感。";
      if ($("#profilePortrait")) $("#profilePortrait").textContent = portraitText;
      if ($("#mobileProfilePortrait")) $("#mobileProfilePortrait").textContent = portraitText;
      const chips = [
        ...asArray(profile?.core_traits),
        ...asArray(profile?.cognitive_style),
        ...asArray(profile?.likes).map((item) => typeof item === "object" ? item.domain || item.name || item.title || valueList(item) : item)
      ].map(valueList).filter((text) => text && text.length <= 10 && !/[，。；、,.]/.test(text)).slice(0, 8);
      const chipTexts = chips.length ? chips : ["长解释", "机制控", "跨平台", "反信息茧房"];
      ["#profileChips", "#mobileProfileChips"].forEach((selector) => {
        const target = $(selector);
        if (!target) return;
        target.replaceChildren(...chipTexts.map((text) => {
          const chip = document.createElement("span"); chip.className = "chip"; chip.textContent = text; return chip;
        }));
      });
      const mbtiText = formatPersonalityType(profile?.mbti || profile?.personality_type) || "—";
      const opennessText = formatPercent(profile?.exploration_openness ?? profile?.openness) || "—";
      const depthText = formatPercent(profile?.style?.depth_preference ?? profile?.depth_preference ?? profile?.deep_preference ?? profile?.long_video_affinity) || "—";
      [["#railMbti", mbtiText], ["#mobileRailMbti", mbtiText], ["#railOpenness", opennessText], ["#mobileRailOpenness", opennessText], ["#railDepth", depthText], ["#mobileRailDepth", depthText]].forEach(([selector, value]) => {
        const target = $(selector);
        if (target) target.textContent = value;
      });
      const activityItems = state.activityItems.length ? state.activityItems : asArray(state.activity?.items);
      const activityHtml = activityItems.length
        ? activityItems.slice(0, 5).map((item) => `<div class="activity-item"><p>${escapeHtml(typeof item === "object" ? item.summary || item.detail || item.kind || valueList(item) : item)}</p></div>`).join("")
        : `<div class="empty-state">还没有新的动态；实时流收到 activity.added 后会自动刷新。</div>`;
      ["#activityList", "#mobileActivityList"].forEach((selector) => {
        const target = $(selector);
        if (target) target.innerHTML = activityHtml;
      });
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(getRenderableMessages().length);
    }

    function renderActivityHistory() {
      const list = $("#activityHistory");
      if (!list) return;
      if (!state.activityItems.length) {
        list.innerHTML = `<div class="empty-state">暂无历史动态。</div>`;
      } else {
        list.innerHTML = state.activityItems.map((item) => `<article class="activity-item"><p class="eyebrow">${escapeHtml(item.kind || "activity")}</p><h3>${escapeHtml(item.summary || "后台动态")}</h3><p class="video-meta">${escapeHtml(item.detail || item.created_at || "")}</p></article>`).join("");
      }
      const more = $("#activityMoreBtn");
      if (more) more.disabled = !state.activityHasMore;
    }

    async function loadActivityPage({ reset = false } = {}) {
      const cursor = reset ? "" : state.activityCursor;
      const query = new URLSearchParams({ limit: "10" });
      if (cursor) query.set("before", cursor);
      const payload = await requestJson(`${ENDPOINTS.activityFeed}?${query.toString()}`);
      if (!payload) { showToast("动态加载失败：后端不可用"); return; }
      const items = Array.isArray(payload.items) ? payload.items : [];
      state.activity = payload;
      state.activityItems = reset ? items : state.activityItems.concat(items);
      state.activityCursor = payload.next_cursor || payload.next || "";
      state.activityHasMore = Boolean(payload.has_more && state.activityCursor);
      renderRail();
      renderActivityHistory();
    }

    function formatPercent(value) {
      if (value == null || value === "") return "";
      if (typeof value === "string" && value.trim().endsWith("%")) return value.trim();
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      const normalized = Math.abs(number) <= 1 ? number * 100 : number;
      return `${Math.round(normalized)}%`;
    }

    function score01(value, fallback = 0.5) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(0, Math.min(1, Math.abs(number) <= 1 ? number : number / 100));
    }

    function formatPersonality(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      const type = value.type || value.mbti || value.name || value.label;
      const confidence = formatPercent(value.confidence);
      if (type && confidence) return `${type}（置信度 ${confidence}）`;
      if (type) return String(type);
      return valueList(value);
    }

    function formatPersonalityType(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      return String(value.type || value.mbti || value.name || value.label || "");
    }

    function formatProfileObject(value) {
      const preferred = value.domain || value.summary || value.name || value.title || value.label || value.value || value.text || value.reason || value.hypothesis || value.observation;
      if (preferred) return String(preferred);
      return Object.entries(value)
        .filter(([, val]) => val != null && val !== "")
        .map(([key, val]) => {
          if (key === "confidence") return `置信度 ${formatPercent(val)}`;
          if (key === "dimensions" && typeof val === "object") return "维度已在 MBTI 图表中展示";
          return `${key}: ${valueList(val)}`;
        })
        .filter(Boolean)
        .join(" / ");
    }

    function valueList(value) {
      if (value == null || value === "") return "";
      if (Array.isArray(value)) return value.map((item) => valueList(item)).filter(Boolean).join("、");
      if (typeof value === "object") return formatProfileObject(value);
      return String(value);
    }

    function asArray(value) {
      if (value == null || value === "") return [];
      if (Array.isArray(value)) return value;
      if (typeof value === "object") {
        if (Array.isArray(value.items)) return value.items;
        if (Array.isArray(value.domains)) return value.domains;
        if (Array.isArray(value.values)) return value.values;
        return Object.entries(value).map(([key, val]) => {
          if (val == null || val === "" || val === false) return "";
          if (val === true) return key;
          if (typeof val === "object" && !Array.isArray(val)) return { name: key, ...val };
          return `${key}: ${valueList(val)}`;
        }).filter(Boolean);
      }
      return String(value).split(/[、,\n]+/).map((item) => item.trim()).filter(Boolean);
    }

    function firstValue(...values) {
      return values.find((value) => value != null && value !== "" && (!Array.isArray(value) || value.length));
    }

    function chipsHtml(value, fallback = "这部分还在慢慢补。") {
      const items = Array.isArray(value) ? value.map(valueList).filter(Boolean) : valueList(value).split("、").filter(Boolean);
      if (!items.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-chip-list">${items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>`;
    }

    function paragraphsHtml(value, fallback = "这部分还在观察，先不急着下结论。") {
      const text = valueList(value);
      if (!text) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-portrait-copy">${String(text).split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(line)}</p>`).join("")}</div>`;
    }

    function profileItem(title, html, extraClass = "") {
      return `<article class="profile-item ${extraClass}"><h3>${escapeHtml(title)}</h3>${html}</article>`;
    }

    function profileLayer(label, items) {
      const body = items.filter(Boolean).join("");
      if (!body) return "";
      return `<div class="profile-layer"><div class="profile-layer-label">${escapeHtml(label)}</div>${body}</div>`;
    }

    function dimensionData(mbti, key) {
      if (!mbti?.dimensions) return null;
      return mbti.dimensions[key] || mbti.dimensions[`${key[0]}_${key[1]}`] || mbti.dimensions[key.toLowerCase()] || mbti.dimensions[`${key[0].toLowerCase()}_${key[1].toLowerCase()}`];
    }

    function normalizedPole(rawPole, key) {
      const pole = String(rawPole || "").trim().toUpperCase();
      if (pole.includes(key[0])) return key[0];
      if (pole.includes(key[1])) return key[1];
      return "";
    }

    function mbtiAxisHtml(mbti, config) {
      const dim = dimensionData(mbti, config.key);
      if (!dim) return "";
      const pole = normalizedPole(dim.pole, config.key) || config.key[1];
      const strength = score01(dim.strength, 0.5);
      const marker = pole === config.key[0] ? 50 - strength * 50 : 50 + strength * 50;
      const start = Math.min(50, marker);
      const width = Math.abs(marker - 50);
      return `<div class="mbti-axis">
        <span class="mbti-axis-side${pole === config.key[0] ? " is-active" : ""}">${config.left}<span> ${config.leftName}</span></span>
        <div class="mbti-axis-track" style="--start:${start}%;--width:${width}%;--marker:${marker}%"><span class="mbti-axis-fill"></span><span class="mbti-axis-marker"></span></div>
        <span class="mbti-axis-side${pole === config.key[1] ? " is-active" : ""}">${config.right}<span> ${config.rightName}</span></span>
        <span class="mbti-axis-pct">${escapeHtml(pole)} ${Math.round(strength * 100)}%</span>
      </div>`;
    }

    function mbtiHtml(value) {
      if (!value) return `<p class="video-meta">MBTI 还没推断出来，再多看一阵。</p>`;
      if (typeof value !== "object") return `<p class="video-meta">${escapeHtml(value)}</p>`;
      const type = value.type || value.mbti || value.name || "—";
      const axes = [
        { key: "EI", left: "E", right: "I", leftName: "外向", rightName: "内向" },
        { key: "SN", left: "S", right: "N", leftName: "实感", rightName: "直觉" },
        { key: "TF", left: "T", right: "F", leftName: "思考", rightName: "情感" },
        { key: "JP", left: "J", right: "P", leftName: "判断", rightName: "知觉" }
      ].map((config) => mbtiAxisHtml(value, config)).filter(Boolean).join("");
      return `<div class="mbti-block"><div class="mbti-type-row"><span class="mbti-type-label">${escapeHtml(type)}</span>${value.confidence ? `<span class="mbti-confidence">整体可信度 ${formatPercent(value.confidence)}</span>` : ""}</div>${axes ? `<div class="mbti-dimensions">${axes}</div>` : ""}</div>`;
    }

    function interestTreeHtml(value, fallback) {
      const domains = asArray(value);
      if (!domains.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-interest-tree">${domains.map((item) => {
        if (typeof item !== "object") return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(item)}</span></div></div>`;
        const title = item.domain || item.name || item.title || valueList(item);
        const weight = item.weight != null ? `<span class="profile-domain-weight">${formatPercent(item.weight)}</span>` : "";
        const specifics = asArray(item.specifics).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean);
        return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(title)}</span>${weight}</div>${specifics.length ? `<div class="profile-chip-list">${specifics.map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div>` : ""}</div>`;
      }).join("")}</div>`;
    }

    function meterHtml(label, value) {
      const score = score01(value);
      return `<div class="profile-meter"><div class="profile-meter-head"><span>${escapeHtml(label)}</span><strong>${Math.round(score * 100)}%</strong></div><div class="profile-meter-track"><div class="profile-meter-fill" style="width:${score * 100}%"></div></div></div>`;
    }

    function styleHtml(style) {
      if (!style || typeof style !== "object" || Array.isArray(style)) return paragraphsHtml(style, "内容口味还在继续归拢。");
      const textRows = [
        ["偏好时长", style.preferred_duration],
        ["偏好节奏", style.preferred_pace]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      const bars = [
        ["质量敏感度", style.quality_sensitivity],
        ["幽默偏好", style.humor_preference],
        ["深度偏好", style.depth_preference]
      ].filter(([, value]) => value != null).map(([label, value]) => meterHtml(label, value)).join("");
      return `<div class="profile-bars profile-style-bars">${textRows}${bars}</div>`;
    }

    function contextHtml(context) {
      if (!context || typeof context !== "object" || Array.isArray(context)) return paragraphsHtml(context, "使用场景还在继续观察。");
      const rows = [
        ["工作日", context.weekday_patterns],
        ["周末", context.weekend_patterns],
        ["一天中的时段", context.time_of_day_patterns],
        ["观看会话", context.session_type]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      return rows ? `<div class="profile-context">${rows}</div>` : paragraphsHtml("", "使用场景还在继续观察。");
    }

    function speculativeHtml(items, options = {}) {
      const isAvoidance = options.kind === "avoidance";
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">${isAvoidance ? "阿B 暂时没有待确认的避雷方向。" : "阿B 还没有正在试探的新方向。"}</p>`;
      const statusLabels = { active: "待确认", pending: "待观察", confirmed: "已确认", deprecated: "已弃", rejected: "已排除" };
      const fallbackTitle = isAvoidance ? "猜测避雷" : "猜测兴趣";
      return `<div class="speculative-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="speculative-item"><div class="spec-header"><span class="spec-domain">${escapeHtml(item)}</span></div></div>`;
        const domain = item.domain || item.name || item.title || fallbackTitle;
        const status = item.status || "active";
        const count = Number(item.confirmation_count ?? 0);
        const threshold = Number(item.confirmation_threshold ?? 3);
        const progress = `${count}/${threshold} 次确认`;
        const confidence = score01(item.confidence, 0);
        const specifics = asArray(item.specifics).map((s) => ({
          name: s?.name || s?.label || valueList(s),
          count: Number(s?.confirmation_count ?? 0)
        })).filter((s) => s.name);
        return `<div class="speculative-item is-status-${escapeHtml(status)}" data-spec-domain="${escapeHtml(domain)}">
          <div class="spec-header">
            <span class="spec-domain">${escapeHtml(domain)}</span>
            ${statusLabels[status] ? `<span class="spec-status">${escapeHtml(statusLabels[status])}</span>` : ""}
            <span class="spec-progress">${escapeHtml(progress)}</span>
          </div>
          ${confidence > 0 ? `<div class="spec-confidence-row"><div class="spec-confidence-bar"><div class="spec-confidence-fill" style="width:${Math.round(confidence * 100)}%"></div></div><span class="spec-confidence-label">置信度 ${Math.round(confidence * 100)}%</span></div>` : ""}
          ${item.reason ? `<p class="video-meta">${escapeHtml(item.reason)}</p>` : ""}
          ${specifics.length ? `<div class="spec-specifics">${specifics.map((s) => `<span class="spec-specific-chip">${escapeHtml(s.name)}${s.count > 0 ? `<span class="spec-specific-count">${s.count}</span>` : ""}</span>`).join("")}</div>` : ""}
          <p class="spec-help">${isAvoidance ? `置信度表示阿B认为你会避开这个方向的把握；确认次数来自后端累计的避雷确认信号，达到 ${threshold} 次后会进入更稳定的避雷画像。` : `置信度表示阿B认为你会喜欢这个方向的把握；确认次数来自后端累计的正向确认信号（包括但不限于这里的“喜欢”），达到 ${threshold} 次后会进入更稳定的兴趣画像。`}</p>
          ${status === "active" && domain ? `<div class="spec-actions"><button class="probe-btn is-confirm" type="button" data-spec-response="confirm" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "确实不喜欢" : "喜欢"}</button><button class="probe-btn is-reject" type="button" data-spec-response="reject" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "不是" : "不喜欢"}</button></div>` : ""}
        </div>`;
      }).join("")}</div>`;
    }

    function memoryHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">阿B 还在继续观察，过一阵这里会更具体。</p>`;
      return `<div class="profile-card-list">${list.slice(0, 8).map((item) => {
        if (typeof item !== "object") return `<div class="profile-memory"><p class="video-meta">${escapeHtml(item)}</p></div>`;
        const meta = item.sourceLabel || item.source_label || item.source || item.created_at || "";
        const details = asArray([item.contextLine || item.context_line, item.impact, item.reasoning, item.evidence]).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(valueList(line))}</p>`).join("");
        return `<div class="profile-memory"><div class="profile-memory-head"><strong>${escapeHtml(item.summary || item.title || "近期记忆")}</strong>${meta ? `<span class="profile-memory-meta">${escapeHtml(meta)}</span>` : ""}</div>${details}</div>`;
      }).join("")}</div>`;
    }

    function insightsHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">当前没有需要特别展示的活跃洞察。</p>`;
      return `<div class="profile-card-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`;
        const evidence = asArray(item.evidence).join("、");
        return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item.hypothesis || item.observation || valueList(item))}</span><span class="profile-confidence">${formatPercent(item.confidence)}</span></div>${evidence ? `<p class="video-meta">证据：${escapeHtml(evidence)}</p>` : ""}${item.validated ? `<p class="video-meta">已验证</p>` : ""}</div>`;
      }).join("")}</div>`;
    }

    function awarenessHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">近期观察还在沉淀。</p>`;
      return `<div class="profile-card-list">${list.map((item) => typeof item === "object" ? `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item.observation || valueList(item))}</span>${item.date ? `<span class="profile-confidence">${escapeHtml(item.date)}</span>` : ""}</div>${item.trend ? `<p class="video-meta">趋势：${escapeHtml(item.trend)}</p>` : ""}${item.emotion_guess ? `<p class="video-meta">情绪猜测：${escapeHtml(item.emotion_guess)}</p>` : ""}</div>` : `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`).join("")}</div>`;
    }

    function updateProfileMemoryButton() {
      const button = $("#profileMemoryMoreBtn");
      if (!button) return;
      button.hidden = !state.profileCognitionHasMore;
      button.disabled = !state.profileCognitionHasMore;
    }

    function syncProfileCognitionState(profile) {
      const cursor = profile?.next_cognition_cursor || profile?.next_cursor || "";
      state.profileCognitionCursor = cursor;
      state.profileCognitionHasMore = Boolean(profile?.has_more_cognition_updates && cursor);
      updateProfileMemoryButton();
    }

    function renderProfileDetails() {
      const profile = state.profile;
      if (!profile) {
        $("#profileDetails").innerHTML = profileItem("画像还没攒起来", paragraphsHtml("后端未连接或画像尚未初始化。连接 FastAPI 后会展示完整画像。"));
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      if (state.editingProfile) {
        $("#profileDetails").innerHTML = renderProfileEditPanel();
        bindProfileEditActions();
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      syncProfileCognitionState(profile);
      const html = [
        profileItem("这会儿的你", paragraphsHtml(profile.personality_portrait || profile.summary), "profile-portrait-block"),
        profileLayer("Core — 比较稳定的底色", [
          profileItem("核心特质", chipsHtml(profile.core_traits, "这部分还在慢慢补。")),
          profileItem("深层需求", chipsHtml(profile.deep_needs, "这块还要再多看一点。")),
          profileItem("MBTI / 人格推断", mbtiHtml(firstValue(profile.mbti, profile.personality_type)))
        ]),
        profileLayer("Values — 你在内容里长期在找什么", [
          profileItem("价值偏好", chipsHtml(firstValue(profile.values, profile.value_preferences), "价值偏好还在继续归拢。")),
          profileItem("内在驱动力", chipsHtml(firstValue(profile.motivational_drivers, profile.intrinsic_drives, profile.motivations), "这块还要再多看一点。"))
        ]),
        profileLayer("Interest — 你最近在看什么", [
          profileItem("感兴趣的方向", interestTreeHtml(profile.likes, "再刷一阵，这里会更准。")),
          profileItem("明显会避开", interestTreeHtml(profile.dislikes, "这块还在继续确认，先别急着下死结论。")),
          profileItem("常看的 UP 主", chipsHtml(firstValue(profile.favorite_up_users, profile.favorite_creators, profile.creators, profile.up_names), "常看的 UP 主还在统计。"))
        ]),
        profileLayer("Role — 这阵子的状态", [
          profileItem("大致处在什么阶段", paragraphsHtml(profile.life_stage, "这块还在观察，先不急着定论。")),
          profileItem("这阵子更像在经历什么", paragraphsHtml(firstValue(profile.current_phase, profile.current_stage), "这阵子的变化还在继续看。"))
        ]),
        profileLayer("Surface — 你怎么看内容", [
          profileItem("认知风格", chipsHtml(profile.cognitive_style, "这层还在继续归拢。")),
          profileItem("内容口味", styleHtml(firstValue(profile.style, profile.content_style, profile.content_preferences))),
          profileItem("使用场景", contextHtml(firstValue(profile.context, profile.current_context))),
          profileItem("探索开放度", meterHtml("愿意走出既有兴趣圈", firstValue(profile.exploration_openness, profile.openness)))
        ]),
        profileLayer("Speculate — 阿B 在试探的方向", [
          profileItem("猜测兴趣", speculativeHtml(profile.speculative_interests)),
          profileItem("猜测避雷", speculativeHtml(profile.speculative_avoidances, { kind: "avoidance" })),
          profileItem("阿B 最近新记住了什么", memoryHtml(firstValue(profile.recent_cognition_updates, profile.recent_memories)))
        ]),
        profileLayer("Signals — 正在推断中", [
          profileItem("当前活跃的洞察", insightsHtml(profile.active_insights)),
          profileItem("近期观察到的", awarenessHtml(profile.recent_awareness))
        ])
      ].join("");
      const profileEditBar = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="enter">✏️ 编辑画像</button></div>`;
      $("#profileDetails").innerHTML = profileEditBar + html;
      bindSpeculativeActions();
      bindProfileEditToggle();
    }

    // ── Editable profile (Phase 3, desktop) ──────────────────────
    const PROFILE_EDIT_LABELS = {
      personality_portrait: "人格素描",
      "core.core_traits": "核心特质",
      "core.deep_needs": "深层需求",
      "values_layer.values": "价值偏好",
      "values_layer.motivational_drivers": "内在驱动力",
      likes: "感兴趣的方向",
      dislikes: "明显会避开",
      "interest.favorite_up_users": "常看的 UP 主",
      "role.life_stage": "大致处在什么阶段",
      "role.current_phase": "这阵子更像在经历什么",
      "surface.cognitive_style": "认知风格"
    };
    const PROFILE_EDIT_ORDER = [
      "personality_portrait",
      "core.core_traits",
      "core.deep_needs",
      "values_layer.values",
      "values_layer.motivational_drivers",
      "likes",
      "dislikes",
      "interest.favorite_up_users",
      "role.life_stage",
      "role.current_phase",
      "surface.cognitive_style"
    ];

    function bindProfileEditToggle() {
      const btn = document.querySelector('#profileDetails [data-profile-edit-toggle="enter"]');
      if (btn) btn.addEventListener("click", () => { void enterProfileEdit(); });
    }

    async function enterProfileEdit() {
      state.editingProfile = true;
      state.profileEditState = null;
      renderProfileDetails();
      state.profileEditState = await requestJson(ENDPOINTS.profileEditState);
      renderProfileDetails();
    }

    async function exitProfileEdit() {
      state.editingProfile = false;
      state.profileEditState = null;
      const fresh = await requestJson(ENDPOINTS.profile);
      if (fresh) state.profile = fresh;
      renderProfileDetails();
    }

    async function applyProfileEdit(payload) {
      const res = await requestJson(ENDPOINTS.profileEdit, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res && res.edit_state && res.edit_state.initialized) {
        state.profileEditState = res.edit_state;
      } else {
        const refreshed = await requestJson(ENDPOINTS.profileEditState);
        if (refreshed) state.profileEditState = refreshed;
        if (!res) showToast("修改未保存：请检查输入或后端状态");
      }
      renderProfileDetails();
    }

    function profileEditTextField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const rows = path === "personality_portrait" ? 4 : 2;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <textarea class="edit-text-input" data-edit-text="${escapeHtml(path)}" rows="${rows}">${escapeHtml(field.value || "")}</textarea>
          ${field.ai_suggestion ? `<p class="edit-drift-hint">AI 当前想更新为：${escapeHtml(field.ai_suggestion)}</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save="${escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditListField(path, label, field) {
      const items = Array.isArray(field.items) ? field.items : [];
      const edited = (field.added?.length || 0) > 0 || (field.removed?.length || 0) > 0;
      const chips = items.length
        ? items.map((it) => `<span class="edit-chip">${escapeHtml(it)}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(it)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="添加一项" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function profileEditInterestField(path, label, field) {
      const domains = Array.isArray(field.domains) ? field.domains : [];
      const edited = (field.removed_domains?.length || 0) > 0 || domains.some((d) => d?.user_added);
      const chips = domains.length
        ? domains.map((d) => `<span class="edit-chip">${escapeHtml(d.domain)}${d.user_added ? " ＋" : ""}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(d.domain)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="${escapeHtml(placeholder)}" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function renderProfileEditPanel() {
      const editState = state.profileEditState;
      let html = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="exit">✓ 完成</button></div>`;
      if (!editState) {
        html += `<p class="video-meta">加载中…</p>`;
        return html;
      }
      if (!editState.initialized || !editState.fields) {
        html += `<p class="video-meta">画像还没攒起来，先跑一遍 openbiliclaw init 再回来编辑。</p>`;
        return html;
      }
      html += `<p class="video-meta profile-edit-note">改完即时生效，且不会被后续自动重建覆盖；删错了点「恢复 AI 建议」即可。</p>`;
      for (const path of PROFILE_EDIT_ORDER) {
        const field = editState.fields[path];
        if (!field || typeof field !== "object") continue;
        const label = PROFILE_EDIT_LABELS[path] || path;
        if (field.type === "text") html += profileEditTextField(path, label, field);
        else if (field.type === "list") html += profileEditListField(path, label, field);
        else if (field.type === "interest") html += profileEditInterestField(path, label, field);
      }
      return html;
    }

    function bindProfileEditActions() {
      const root = $("#profileDetails");
      if (!root) return;
      root.querySelector('[data-profile-edit-toggle="exit"]')?.addEventListener("click", () => { void exitProfileEdit(); });
      root.querySelectorAll("[data-edit-remove]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editRemove, op: "remove", value: btn.dataset.editValue }));
      });
      root.querySelectorAll("[data-edit-reset]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editReset, op: "reset" }));
      });
      root.querySelectorAll("[data-edit-add]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editAdd;
          const input = root.querySelector(`[data-edit-add-input="${path}"]`);
          const value = input?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-add-input]").forEach((input) => {
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: input.dataset.editAddInput, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-save]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSave;
          const textarea = root.querySelector(`[data-edit-text="${path}"]`);
          const value = textarea?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "set", value });
        });
      });
    }

    async function loadMoreProfileMemory() {
      if (!state.profileCognitionCursor) return;
      const button = $("#profileMemoryMoreBtn");
      if (button) button.disabled = true;
      const query = new URLSearchParams({ cursor: state.profileCognitionCursor });
      const nextPage = await requestJson(`${ENDPOINTS.profile}?${query.toString()}`);
      if (!nextPage) {
        showToast("近期记忆加载失败：后端不可用");
        updateProfileMemoryButton();
        return;
      }
      const current = Array.isArray(state.profile?.recent_cognition_updates) ? state.profile.recent_cognition_updates : [];
      const incoming = Array.isArray(nextPage.recent_cognition_updates) ? nextPage.recent_cognition_updates : [];
      state.profile = {
        ...(state.profile || {}),
        ...nextPage,
        recent_cognition_updates: current.concat(incoming)
      };
      syncProfileCognitionState(state.profile);
      renderProfileDetails();
      showToast(incoming.length ? `已加载 ${incoming.length} 条近期记忆` : "没有更多近期记忆");
    }

    function messageType(msg) {
      const type = msg?.type === "probe" ? "interest.probe" : (msg?.type || "interest.probe");
      return type === "avoidance" ? "avoidance.probe" : type;
    }

    function isAvoidanceProbe(type) {
      return messageType({ type }) === "avoidance.probe";
    }

    function isChallengeProbe(item) {
      const mode = String(item?.probe_mode || "").toLowerCase();
      return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
    }

    function probeKey(type, domain) {
      return `${messageType({ type })}:${String(domain || "")}`;
    }

    function messageKey(msg) {
      const type = messageType(msg);
      return `${type}:${msg?.bvid || msg?.domain || msg?.title || msg?.reason || ""}`;
    }

    function normalizeMessageItem(item) {
      if (!item) return null;
      const type = messageType(item);
      if (type === "delight") {
        return null;
      }
      if (type === "notification") {
        const bvid = item.bvid || item.id || item.recommendation_id;
        if (!bvid) return null;
        return {
          type: "notification",
          bvid: String(bvid),
          title: item.title || "有一条值得通知你的推荐",
          reason: item.reason || item.expression || "这条推荐达到了通知阈值。",
          content_url: item.content_url || (item.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}` : "")
        };
      }
      const domain = item.domain || item.name || item.title;
      if (!domain) return null;
      const probeType = type === "avoidance.probe" || item.kind === "avoidance" ? "avoidance.probe" : "interest.probe";
      return {
        type: probeType,
        domain: String(domain),
        reason: item.reason || item.message || item.description || (probeType === "avoidance.probe" ? "后端希望确认这个避雷方向。" : "后端希望确认这个兴趣方向。"),
        specifics: asArray(item.specifics || item.examples || item.children).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean),
        probe_mode: item.probe_mode || "",
        challenge: Boolean(item.challenge),
        chat_status: item.chat_status || item.status_text || "",
        chat_reply: item.chat_reply || item.reply || ""
      };
    }

    function syncMessageCount() {
      const count = getRenderableMessages(state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages).length;
      if (state.runtimeStatus) state.runtimeStatus.unread_count = count;
      const metric = $("#metricUnread");
      if (metric) metric.textContent = String(count);
      const dot = $("#messagesDot");
      if (dot) dot.hidden = count <= 0;
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(count);
      return count;
    }

    function getRenderableMessages(source = state.messages) {
      const seen = new Set();
      const items = [];
      for (const raw of source || []) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
      }
      return items;
    }

    function isMessagesDrawerOpen() {
      return Boolean($("#messagesDrawer")?.classList.contains("is-open"));
    }

    function hydrateInboxFromSpeculations(speculations, type = "interest.probe") {
      if (speculations == null || speculations === "") return;
      const normalizedType = messageType({ type });
      const items = asArray(speculations);
      const active = items.filter((item) => item && item.domain && (!item.status || item.status === "active"));
      const activeDomains = new Set(active.map((item) => String(item.domain)));
      const preserveCurrentProbeList = isMessagesDrawerOpen();
      state.messages = state.messages.filter((msg) => {
        if (messageType(msg) !== normalizedType) return true;
        const domain = String(msg.domain || "");
        if (!domain || state.handledProbeKeys.has(probeKey(normalizedType, domain))) return false;
        if (state.resolvingMessageKeys.has(messageKey(msg))) return true;
        return preserveCurrentProbeList || activeDomains.has(domain);
      });
      const existing = new Set(state.messages.filter((msg) => messageType(msg) === normalizedType).map((msg) => String(msg.domain || "")));
      for (const item of active) {
        const domain = String(item.domain);
        if (state.handledProbeKeys.has(probeKey(normalizedType, domain)) || existing.has(domain)) continue;
        state.messages.push(normalizeMessageItem({ ...item, type: normalizedType }));
        existing.add(domain);
      }
      syncMessageCount();
    }

    function isMessageListLocked() {
      return Boolean(document.querySelector("#messageList .message-item.is-resolving, #messageList .message-item.is-resolved, #messageList .message-item.is-dismissing"));
    }

    function renderMessages() {
      const list = $("#messageList");
      if (state.messageListDomLocked || isMessageListLocked()) {
        syncMessageCount();
        return;
      }
      const source = state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages;
      const messages = getRenderableMessages(source);
      if (state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = messages;
      else state.messages = messages;
      syncMessageCount();
      if (!messages.length) {
        list.innerHTML = `<div class="empty-state">暂无通知。兴趣确认、避雷确认和待通知候选都会出现在这里。</div>`;
        return;
      }
      list.replaceChildren(...messages.map((msg) => {
        const el = document.createElement("article");
        const key = messageKey(msg);
        const resolvedResult = state.resolvedMessageResults.get(key);
        el.className = "message-item";
        el.dataset.messageKey = key;
        if (messageType(msg) === "notification") {
          el.classList.add("is-notification");
          el.innerHTML = `<p class="eyebrow">待通知候选</p><h3>${escapeHtml(msg.title)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="message-note">这类消息来自后端挑出的高置信推荐，用于插件通知；标记已通知后不会反复出现。</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="通知候选状态"><button class="feedback-icon-btn" data-notification-msg="dismiss" type="button" aria-label="标记已通知" title="标记已通知"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-notification-msg="view">去看看</button></div></div>`;
          el.querySelectorAll("[data-notification-msg]").forEach((btn) => btn.addEventListener("click", () => respondNotification(msg, btn.dataset.notificationMsg, el)));
        } else {
          const isAvoidance = messageType(msg) === "avoidance.probe";
          const isChallenge = !isAvoidance && isChallengeProbe(msg);
          el.classList.add(isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe");
          const eyebrow = isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣确认";
          const actionsLabel = isAvoidance ? "确认或排除这个避雷方向" : isChallenge ? "确认或排除这个挑战方向" : "确认或排除这个兴趣";
          const confirmLabel = isAvoidance ? "确实不喜欢" : "喜欢";
          const rejectLabel = isAvoidance ? "不是" : "不喜欢";
          const kindCopy = isAvoidance
            ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。"
            : isChallenge
              ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
            : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
          el.innerHTML = `<p class="eyebrow">${eyebrow}</p><div class="message-note probe-kind-copy">${escapeHtml(kindCopy)}</div><h3>${escapeHtml(msg.domain)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="profile-chip-row">${asArray(msg.specifics).map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="${actionsLabel}"><button class="feedback-icon-btn" data-probe="confirm" type="button" aria-label="${confirmLabel}" title="${confirmLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg></button><span class="feedback-separator" aria-hidden="true">/</span><button class="feedback-icon-btn" data-probe="reject" type="button" aria-label="${rejectLabel}" title="${rejectLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-probe="chat">多聊聊</button></div></div>`;
          if (resolvedResult) {
            el.classList.add("is-resolved");
            const resolvedActions = el.querySelector(".message-card-actions");
            if (resolvedActions) resolvedActions.outerHTML = `<div class="message-note is-success">${escapeHtml(resolvedResult)}</div>`;
          } else {
            el.querySelectorAll("[data-probe]").forEach((btn) => btn.addEventListener("click", () => respondProbe(msg, btn.dataset.probe, el)));
          }
        }
        return el;
      }));
    }

    async function respondNotification(msg, response, el) {
      if (response === "view" && msg.content_url) window.open(msg.content_url, "_blank", "noopener,noreferrer");
      await requestJson(ENDPOINTS.notificationSent, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: msg.bvid }) });
      state.messages = state.messages.filter((item) => !(messageType(item) === "notification" && String(item.bvid) === String(msg.bvid)));
      renderMessages();
      if (el) el.remove();
      showToast(response === "view" ? "已打开并标记这条通知" : "已标记这条通知");
    }

    function collapseMessageItem(key, fallbackEl, onDone) {
      const target = fallbackEl?.isConnected ? fallbackEl : Array.from(document.querySelectorAll("#messageList .message-item")).find((item) => item.dataset.messageKey === key);
      const finish = () => { onDone?.(); };
      if (!target) {
        finish();
        return;
      }
      target.style.height = `${target.getBoundingClientRect().height}px`;
      target.style.minHeight = "0px";
      target.style.overflow = "hidden";
      target.style.transition = `height 240ms var(--ease-standard), opacity 180ms var(--ease-standard), padding 240ms var(--ease-standard), border-width 240ms var(--ease-standard)`;
      target.getBoundingClientRect();
      target.classList.add("is-dismissing");
      target.style.height = "0px";
      window.setTimeout(() => {
        target.remove();
        finish();
      }, 260);
    }

    async function respondProbe(msg, response, el) {
      if (!el) return;
      const actions = el.querySelector(".message-card-actions");
      if (response === "chat") {
        openMessageChat(msg);
        showToast("已在消息里打开聊天上下文");
        return;
      }
      const key = messageKey(msg);
      state.messageListDomLocked = true;
      if (!state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = getRenderableMessages();
      el.style.minHeight = `${el.getBoundingClientRect().height}px`;
      el.classList.add("is-resolving");
      state.resolvingMessageKeys.add(key);
      actions?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      try {
        const isAvoidance = messageType(msg) === "avoidance.probe";
        const endpoint = isAvoidance ? ENDPOINTS.avoidanceProbeRespond : ENDPOINTS.interestProbeRespond;
        await requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: msg.domain, response, message: "" }) });
        const result = isAvoidance
          ? response === "confirm" ? "已确认避雷方向，后续会减少类似内容。" : "已搁置，暂时不作为避雷方向。"
          : response === "confirm" ? "已确认，后续推荐会提高权重。" : "已搁置，后续会少试探这个方向。";
        state.resolvedMessageResults.set(key, result);
        el.classList.remove("is-resolving");
        el.classList.add("is-resolved");
        if (actions) {
          actions.classList.add("is-result");
          actions.innerHTML = `<div class="message-action-result" title="${escapeHtml(result)}">${escapeHtml(result)}</div>`;
        }
        showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已搁置这个避雷方向"
          : response === "confirm" ? "已确认这个兴趣方向" : "已搁置这个兴趣方向");
        setTimeout(() => {
          collapseMessageItem(key, el, () => {
            state.resolvingMessageKeys.delete(key);
            state.resolvedMessageResults.delete(key);
            if (msg.domain) state.handledProbeKeys.add(probeKey(messageType(msg), msg.domain));
            state.messages = state.messages.filter((item) => messageKey(item) !== key);
            if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((item) => messageKey(item) !== key);
            state.messageListDomLocked = false;
            renderMessages();
            void refreshProfile();
          });
        }, 1800);
      } catch (error) {
        state.resolvingMessageKeys.delete(key);
        state.resolvedMessageResults.delete(key);
        state.messageListDomLocked = false;
        el.classList.remove("is-resolving");
        el.style.minHeight = "";
        actions?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
        showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function bindSpeculativeActions() {
      document.querySelectorAll("[data-spec-response]").forEach((button) => {
        button.addEventListener("click", () => respondSpeculativeInterest(button));
      });
    }

    async function respondSpeculativeInterest(button) {
      const row = button.closest("[data-spec-domain]");
      const domain = row?.dataset.specDomain;
      const response = button.dataset.specResponse;
      if (!domain || !response) return;
      row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = true; });
      try {
        const type = button.dataset.specType || "interest.probe";
        const isAvoidance = isAvoidanceProbe(type);
        const endpoint = isAvoidance ? ENDPOINTS.avoidanceProbeRespond : ENDPOINTS.interestProbeRespond;
        const payload = { domain, response, message: "" };
        if (!isAvoidance) payload.surface = "profile";
        await requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const result = isAvoidance
          ? (response === "confirm" ? `好，「${escapeHtml(domain)}」会作为避雷方向处理。` : `好，「${escapeHtml(domain)}」不记成避雷。`)
          : (response === "confirm" ? `好，「${escapeHtml(domain)}」记住了。` : `好，「${escapeHtml(domain)}」先不看了。`);
        row.innerHTML = `<p class="spec-result">${result}</p>`;
        const key = probeKey(type, domain);
        state.handledProbeKeys.add(key);
        state.messages = state.messages.filter((msg) => messageKey(msg) !== key);
        if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((msg) => messageKey(msg) !== key);
        renderMessages();
        showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已排除这个避雷方向"
          : response === "confirm" ? "已确认这个猜测兴趣" : "已排除这个猜测兴趣");
        setTimeout(() => { void refreshProfile(); }, 1200);
      } catch (error) {
        row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = false; });
        showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function createClientTurnId(prefix = "webui") {
      const suffix = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      return `${prefix}-${suffix}`;
    }

    function normalizeDelightTurn(turn) {
      if (!turn) return null;
      const message = String(turn.message ?? turn.user_message ?? "");
      const reply = String(turn.reply ?? turn.assistant_message ?? "");
      const status = String(turn.status || (reply ? "completed" : "pending"));
      const turnId = String(turn.turn_id ?? turn.id ?? "");
      if (!turnId && !message && !reply) return null;
      return {
        turn_id: turnId,
        message,
        reply,
        status,
        error: String(turn.error ?? "")
      };
    }

    function delightTurnList(turns) {
      return asArray(turns).map(normalizeDelightTurn).filter(Boolean);
    }

    function upsertDelightTurn(turns, nextTurn) {
      const normalized = normalizeDelightTurn(nextTurn);
      const existing = delightTurnList(turns);
      if (!normalized) return existing;
      const index = existing.findIndex((turn) => turn.turn_id && turn.turn_id === normalized.turn_id);
      if (index < 0) return [...existing, normalized];
      return existing.map((turn, turnIndex) => turnIndex === index ? normalized : turn);
    }

    function mergeDelightTurnLists(currentTurns, incomingTurns) {
      let merged = delightTurnList(currentTurns);
      for (const turn of delightTurnList(incomingTurns)) merged = upsertDelightTurn(merged, turn);
      return merged;
    }

    function mergeDelightItem(current, incoming) {
      if (!current) return incoming;
      return {
        ...current,
        ...incoming,
        chat_turn_id: incoming.chat_turn_id || current.chat_turn_id || "",
        chat_reply: incoming.chat_reply || current.chat_reply || "",
        chat_draft: incoming.chat_draft || current.chat_draft || "",
        response_message: incoming.response_message || current.response_message || "",
        turns: mergeDelightTurnLists(current.turns, incoming.turns)
      };
    }

    function renderDelightTurns(delight) {
      const area = $("#delightTurns");
      if (!area) return;
      area.replaceChildren();
      const turns = delightTurnList(delight?.turns);
      if (!turns.length && !delight?.chat_reply) {
        area.hidden = true;
        scheduleActivityRailHeightSync();
        return;
      }
      area.hidden = false;
      if (!turns.length && delight?.chat_reply) {
        const bubble = document.createElement("div");
        bubble.className = "delight-turn-bubble is-assistant";
        bubble.textContent = delight.chat_reply;
        area.append(bubble);
        scheduleActivityRailHeightSync();
        return;
      }
      for (const turn of turns) {
        if (turn.message) {
          const userBubble = document.createElement("div");
          userBubble.className = "delight-turn-bubble is-user";
          userBubble.textContent = turn.message;
          area.append(userBubble);
        }
        const assistantBubble = document.createElement("div");
        const status = String(turn.status || "pending");
        assistantBubble.className = `delight-turn-bubble is-assistant${status === "pending" ? " is-thinking" : ""}${status === "failed" ? " is-error" : ""}`;
        assistantBubble.textContent = status === "pending"
          ? "阿B 正在品你这句话…"
          : status === "failed"
            ? turn.error || "这句还没发出去，稍后再试。"
            : turn.reply || "后端已完成这轮聊天。";
        area.append(assistantBubble);
      }
      scheduleActivityRailHeightSync();
    }

    function updateDelightState(bvid, updates) {
      const key = String(bvid || "");
      if (!key) return null;
      let current = null;
      state.delights = state.delights.map((item) => {
        if (String(item.bvid || "") !== key) return item;
        current = { ...item, ...updates };
        return current;
      });
      if (state.delight && String(state.delight.bvid || "") === key) {
        state.delight = { ...state.delight, ...updates };
        current = state.delight;
      }
      if (current && state.delight && String(state.delight.bvid || "") === key) {
        renderDelightTurns(state.delight);
        if ($("#delightStatus")) $("#delightStatus").textContent = state.delight.response_message || "";
      }
      return current;
    }

    function applyTurnToDelight(turn) {
      const subjectId = String(turn?.subject_id || turn?.bvid || "");
      if (!turn || (turn.scope && turn.scope !== "delight") || !subjectId) return null;
      const existing = state.delights.find((item) => String(item.bvid || "") === subjectId)
        || (state.delight && String(state.delight.bvid || "") === subjectId ? state.delight : null);
      const entry = normalizeDelightTurn(turn);
      if (!entry) return null;
      const status = String(entry.status || "pending");
      const updates = {
        chat_turn_id: entry.turn_id,
        turns: upsertDelightTurn(existing?.turns, entry),
        response_message: status === "completed" ? "这句已经记下，后面会更会试探。" : status === "failed" ? "这句还没发出去，稍后再试。" : "阿B 正在品你这句话。"
      };
      if (status === "completed") {
        updates.chat_reply = entry.reply || existing?.chat_reply || "";
        updates.chat_draft = "";
      }
      return updateDelightState(subjectId, updates);
    }

    function pollChatTurnUntilSettled(turnId, fallbackTurn) {
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest) {
          const scopedTurn = { ...fallbackTurn, ...latest, scope: latest.scope || "delight", subject_id: latest.subject_id || fallbackTurn.subject_id };
          applyTurnToDelight(scopedTurn);
          if (latest.status === "completed" || latest.status === "failed") return;
        }
        if (Date.now() - startedAt > 180000) {
          applyTurnToDelight({ ...fallbackTurn, status: "failed", error: "聊天处理超时，稍后可以在历史里继续查看。" });
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function respondDelight(delight, response, el = null) {
      if (!delight) return;
      if (response === "chat") { openDelightComposer(); return; }
      if (response === "cancel-comment") { closeDelightComposer(); return; }
      if (response === "watch-later") {
        const btn = document.querySelector('[data-delight="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "favorite") {
        const btn = document.querySelector('[data-delight="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "send-comment") {
        const input = $("#delightCommentInput");
        const note = input?.value?.trim() || "";
        if (!note) {
          if ($("#delightStatus")) $("#delightStatus").textContent = "先写一句想聊的内容，再提交这轮对话。";
          input?.focus();
          return;
        }
        const turnId = createClientTurnId("delight");
        const pendingTurn = { turn_id: turnId, session: "webui", scope: "delight", subject_id: delight.bvid, subject_title: delight.title || "", message: note, reply: "", status: "pending", error: "" };
        applyTurnToDelight(pendingTurn);
        if (input) input.value = "";
        closeDelightComposer();
        try {
          const turn = await requestJsonStrict(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pendingTurn) });
          const scopedTurn = { ...pendingTurn, ...(turn || {}), scope: turn?.scope || "delight", subject_id: turn?.subject_id || delight.bvid };
          applyTurnToDelight(scopedTurn);
          if (scopedTurn.turn_id && scopedTurn.status !== "completed" && scopedTurn.status !== "failed") pollChatTurnUntilSettled(scopedTurn.turn_id, scopedTurn);
          showToast("已提交聊天线索");
        } catch (error) {
          applyTurnToDelight({ ...pendingTurn, status: "failed", error: error.message || "聊天提交失败，请稍后再试。" });
          if (input) input.value = note;
          showToast(`聊天提交失败：${error.message || "后端不可用"}`);
        }
        return;
      }
      if (response === "view") {
        const url = delight.content_url || (delight.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(delight.bvid)}` : "");
        if (url) window.open(url, "_blank", "noopener,noreferrer");
        trackRecommendationClick(delight);
        showToast(url ? "已打开惊喜推荐" : "后端没有返回可打开链接");
        return;
      }
      const feedbackToast = response === "like" ? "惊喜推荐已喜欢" : response === "dislike" ? "这类惊喜先少来点" : "已忽略这条惊喜推荐";
      const toastImmediately = response === "like" || response === "dislike";
      if (toastImmediately) showToast(feedbackToast);
      await requestJson(ENDPOINTS.delightRespond, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: delight.bvid,
          response,
          title: delight.title,
          message: ""
        })
      });
      state.delights = state.delights.filter((item) => item.bvid !== delight.bvid);
      setActiveDelight(Math.min(state.delightIndex, state.delights.length - 1));
      if (el) el.remove();
      if (!toastImmediately) showToast(feedbackToast);
    }

    function openMessageChat(msg) {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      const input = $("#messageChatInput");
      state.messageScrollTop = panel?.scrollTop || 0;
      const type = messageType(msg);
      const isAvoidance = type === "avoidance.probe";
      state.messageChatDomain = msg.domain || "";
      state.messageChatScope = isAvoidance ? "avoidance_probe" : "probe";
      openPanel("messagesDrawer");
      drawer?.classList.add("is-chatting");
      if (view) view.hidden = false;
      const title = $("#messageChatTitle");
      const context = $("#messageChatContext");
      const prompt = msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
      state.messageChatPrompt = prompt;
      state.messageChatSubjectTitle = msg.domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向");
      if (title) title.textContent = msg.domain ? `聊聊${isAvoidance ? "避雷" : "兴趣"}「${msg.domain}」` : `聊聊这个${isAvoidance ? "避雷" : "兴趣"}`;
      if (context) context.textContent = msg.reason || `这轮对话会沿用消息里的${isAvoidance ? "避雷" : "兴趣"}上下文。`;
      if (input) {
        input.value = "";
        input.placeholder = "继续写你想补充的问题、偏好或例子";
      }
      renderChat();
      if (panel) panel.scrollTop = 0;
      window.setTimeout(() => input?.focus(), 80);
    }

    function returnToMessages() {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      drawer?.classList.remove("is-chatting");
      if (view) view.hidden = true;
      state.messageChatDomain = "";
      state.messageChatPrompt = "";
      state.messageChatScope = "probe";
      state.messageChatSubjectTitle = "";
      window.setTimeout(() => {
        if (panel) panel.scrollTop = state.messageScrollTop || 0;
      }, 0);
    }

    function chatHtml(messages) {
      return messages.map((msg) => `<div class="chat-bubble ${msg.role === "user" ? "user" : "agent"}">${escapeHtml(msg.text)}</div>`).join("");
    }

    function renderChat() {
      const chatLog = $("#chatLog");
      if (chatLog) {
        chatLog.innerHTML = chatHtml(state.chat);
        chatLog.scrollTop = chatLog.scrollHeight;
      }
      const messageChatLog = $("#messageChatLog");
      if (messageChatLog) {
        const baseMessages = state.messageChatPrompt
          ? state.chat.filter((msg) => msg.text !== "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。")
          : state.chat;
        const messages = state.messageChatPrompt ? [{ role: "user", text: state.messageChatPrompt }, ...baseMessages] : baseMessages;
        messageChatLog.innerHTML = chatHtml(messages);
        messageChatLog.scrollTop = messageChatLog.scrollHeight;
      }
    }

    async function sendChat(message, options = {}) {
      const payloadMessage = options.contextPrefix ? `${options.contextPrefix}\n\n${message}` : message;
      state.chat.push({ role: "user", text: message });
      state.chat.push({ role: "agent", text: "正在提交给后端，并等待 durable chat turn 完成。" });
      renderChat();
      const payload = {
        session: "webui",
        scope: options.scope || "chat",
        subject_id: options.subjectId || "",
        subject_title: options.subjectTitle || "",
        message: payloadMessage
      };
      const turn = await requestJson(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!turn?.turn_id) {
        state.chat[state.chat.length - 1] = { role: "agent", text: "当前没有连上后端，聊天没有提交成功。请检查 FastAPI 地址后重试。" };
        renderChat();
        showToast("聊天提交失败：后端不可用");
        return;
      }
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turn.turn_id)}`);
        if (latest?.status === "completed" || latest?.reply) {
          state.chat[state.chat.length - 1] = { role: "agent", text: latest.reply || "后端已完成这轮聊天。" };
          renderChat();
          return;
        }
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          state.chat[state.chat.length - 1] = { role: "agent", text: latest?.error || "聊天处理超时，稍后可以在历史里继续查看。" };
          renderChat();
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function refreshRecommendations() {
      const result = await requestJson(ENDPOINTS.refresh, { method: "POST" });
      if (result) {
        showToast("已请求后端开始补货");
        await hydrateFromBackend();
      } else {
        showToast("刷新失败：请检查后端连接");
      }
    }

    async function dismissVisibleRecommendationsBeforeReshuffle() {
      const visibleItems = filteredVideos().filter((item) => item?.id != null);
      if (!visibleItems.length) return { total: 0, ok: 0, failed: 0 };
      showToast(`正在忽略当前显示的 ${visibleItems.length} 张推荐…`);
      const results = await Promise.allSettled(visibleItems.map((item) => submitFeedback(item, "dismiss")));
      const dismissedKeys = new Set();
      results.forEach((result, index) => {
        if (result.status === "fulfilled") dismissedKeys.add(recommendationKey(visibleItems[index]));
      });
      if (dismissedKeys.size) {
        state.videos = state.videos.filter((item) => !dismissedKeys.has(recommendationKey(item)));
      }
      return { total: visibleItems.length, ok: dismissedKeys.size, failed: visibleItems.length - dismissedKeys.size };
    }

    async function reshuffle() {
      const reshuffleButton = $("#reshuffleBtn");
      const dismissToggle = $("#dismissOnReshuffleToggle");
      if (reshuffleButton) reshuffleButton.disabled = true;
      if (dismissToggle) dismissToggle.disabled = true;
      try {
        const dismissResult = state.dismissOnReshuffle ? await dismissVisibleRecommendationsBeforeReshuffle() : null;
        const payload = await requestJson(ENDPOINTS.reshuffle, { method: "POST" });
        if (payload?.items?.length) {
          state.videos = normalizeRecommendationList(payload.items);
          renderAll();
          if (dismissResult?.ok) {
            const failedText = dismissResult.failed ? `，${dismissResult.failed} 张忽略失败` : "";
            showToast(`已忽略 ${dismissResult.ok} 张当前推荐并换一批${failedText}`);
          } else {
            showToast("已换一批推荐");
          }
        } else {
          renderAll();
          showToast("换一批失败：请检查后端连接");
        }
      } finally {
        if (reshuffleButton) reshuffleButton.disabled = false;
        if (dismissToggle) dismissToggle.disabled = false;
      }
    }

    async function appendMore() {
      const payload = await requestJson(ENDPOINTS.append, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ excluded_bvids: state.videos.map((v) => v.bvid) }) });
      if (payload?.items?.length) {
        const freshItems = normalizeRecommendationList(payload.items);
        // Preload + decode the new covers before re-rendering so they appear
        // without the placeholder flash.
        await warmCoverImages(freshItems, { waitForDecode: true });
        state.videos = state.videos.concat(freshItems);
        renderAll();
        showToast(freshItems.length ? "已加载更多推荐" : "后端返回的内容都已反馈过");
      } else {
        showToast("加载更多失败：后端没有返回新候选");
      }
    }

    function normalizeRuntimeStatus(status) {
      if (!status) return null;
      const previous = state.runtimeStatus || {};
      const incomingType = String(status.type || status.runtime_event_type || "");
      const merged = { ...previous, ...status };
      let manualRefreshState = status.manual_refresh_state != null
        ? String(status.manual_refresh_state || "idle")
        : String(previous.manual_refresh_state || "");
      if (status.manual_refresh_state == null) {
        if (incomingType === "refresh.started" || incomingType === "refresh.strategy") manualRefreshState = "running";
        if (incomingType === "refresh.pool_updated") manualRefreshState = "success";
        if (incomingType === "refresh.failed") manualRefreshState = "failed";
      }
      return {
        initialized: merged.initialized !== false,
        recommendation_count: Number(merged.recommendation_count ?? 0),
        pending_signal_events: Number(merged.pending_signal_events ?? 0),
        last_refresh_at: String(merged.last_refresh_at ?? ""),
        last_notification_at: String(merged.last_notification_at ?? ""),
        unread_count: Number(merged.unread_count ?? state.messages.length ?? 0),
        pool_available_count: Number(merged.pool_available_count ?? merged.pool_available ?? merged.available_count ?? 0),
        pool_target_count: Number(merged.pool_target_count ?? state.config?.scheduler?.pool_target_count ?? 0),
        last_discovered_count: Number(merged.last_discovered_count ?? 0),
        last_replenished_count: Number(merged.last_replenished_count ?? 0),
        recent_pool_topics: Array.isArray(merged.recent_pool_topics) ? merged.recent_pool_topics.map(String).filter(Boolean) : [],
        manual_refresh_state: manualRefreshState || "idle",
        manual_refresh_message: String(merged.manual_refresh_message || ""),
        runtime_event_type: incomingType || String(merged.runtime_event_type || ""),
        live_summary: String(merged.live_summary || merged.message || merged.state || "")
      };
    }

    function getPoolStatusSummary(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (!runtime || !runtime.initialized) return null;
      const sufficient = runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;
      if (runtime.manual_refresh_state === "running") {
        return runtime.pool_available_count > 0
          ? { available: `还有 ${runtime.pool_available_count} 条可换`, replenished: "后台继续在找更多", topics: "可以先换一批，新的随时进" }
          : { available: "暂无可换库存", replenished: "正在补货", topics: "后台还在继续给你找新的" };
      }
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: runtime.last_replenished_count > 0
          ? `刚补进 ${runtime.last_replenished_count} 条`
          : runtime.last_discovered_count > 0
            ? "这轮找到了内容"
            : sufficient
              ? "这会儿先不补货"
              : "这轮还没补进",
        topics: runtime.recent_pool_topics.length > 0
          ? runtime.recent_pool_topics.join(" / ")
          : runtime.last_discovered_count > 0
            ? "但可立即换的库存还没变"
            : sufficient
              ? "先把这一池给你慢慢换开"
              : "还在继续摸你的口味"
      };
    }

    function configuredSourceCount() {
      const sources = state.config?.sources;
      if (!sources || typeof sources !== "object") return 0;
      const shares = state.config?.scheduler?.pool_source_shares || {};
      return Object.entries(sources).reduce((count, [key, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return count;
        if (Object.prototype.hasOwnProperty.call(value, "enabled")) {
          return count + (value.enabled !== false ? 1 : 0);
        }
        if (Object.prototype.hasOwnProperty.call(shares, key)) {
          return count + (Number(shares[key] ?? 0) > 0 ? 1 : 0);
        }
        return count;
      }, 0);
    }

    function syncSourceMetric() {
      const count = configuredSourceCount();
      $("#metricSources").textContent = count ? String(count) : "—";
    }

    function getPoolRefreshLabel(runtime) {
      if (!runtime) return "—";
      if (runtime.manual_refresh_message) return runtime.manual_refresh_message;
      if (runtime.manual_refresh_state === "running") return runtime.pool_available_count > 0 ? "后台继续补货中" : "正在补货";
      if (runtime.manual_refresh_state === "success") return "刚同步完成";
      if (runtime.manual_refresh_state === "failed") return "刷新失败";
      if (runtime.pending_signal_events > 0) return `待处理 ${runtime.pending_signal_events} 条行为信号`;
      if (runtime.runtime_event_type === "refresh.pool_updated") return "刚同步推荐池";
      return runtime.pool_available_count > 0 ? "可直接换一批" : "等待后台补货";
    }

    function renderPoolStatus(status = state.runtimeStatus) {
      const runtime = normalizeRuntimeStatus(status);
      const summary = getPoolStatusSummary(runtime);
      $("#poolAvailable").textContent = summary?.available || "后端未初始化";
      $("#poolReplenished").textContent = summary?.replenished || "—";
      $("#poolTopics").textContent = summary?.topics || "—";
      $("#poolRefreshState").textContent = getPoolRefreshLabel(runtime);
    }

    function applyRuntimeStatus(payload) {
      if (!payload) return;
      state.runtimeStatus = normalizeRuntimeStatus(payload);
      const summary = getPoolStatusSummary(state.runtimeStatus);
      $("#statusLabel").textContent = state.runtimeStatus.initialized === false ? "后端未初始化" : "已连接本地后端";
      $("#metricPool").textContent = String(state.runtimeStatus.pool_available_count);
      syncMessageCount();
      syncSourceMetric();
      $("#runtimeSummary").textContent = state.runtimeStatus.live_summary || summary?.available || "后端在线，推荐池与采集运行时可读取。";
      renderPoolStatus(state.runtimeStatus);
    }

    function setInput(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    function getInput(id) {
      return document.getElementById(id)?.value?.trim() || "";
    }

    function getIntInput(id, fallback) {
      const value = Number.parseInt(getInput(id), 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function getFloatInput(id, fallback) {
      const value = Number.parseFloat(getInput(id));
      return Number.isFinite(value) ? value : fallback;
    }

    function joinPath(directory, filename) {
      const dir = String(directory || "").trim();
      const name = String(filename || "").trim();
      if (!dir) return name;
      if (!name) return dir;
      return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
    }

    function resolveLogPath(loggingConfig) {
      if (loggingConfig?.file_path) return loggingConfig.file_path;
      return joinPath(loggingConfig?.directory || "logs", loggingConfig?.filename || "openbiliclaw.log");
    }

    function splitLogPath(rawPath, currentLogging) {
      const fallback = { directory: "logs", filename: "openbiliclaw.log" };
      const trimmed = String(rawPath || "").trim();
      if (!trimmed) return fallback;
      if (currentLogging && trimmed === resolveLogPath(currentLogging)) {
        return { directory: currentLogging.directory || fallback.directory, filename: currentLogging.filename || fallback.filename };
      }
      const normalized = trimmed.replaceAll("\\", "/").replace(/\/+$/, "");
      const slashIndex = normalized.lastIndexOf("/");
      if (slashIndex === -1) return { directory: fallback.directory, filename: normalized || fallback.filename };
      return { directory: normalized.slice(0, slashIndex) || "/", filename: normalized.slice(slashIndex + 1) || fallback.filename };
    }

    function setSelect(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    function applyConfig(config) {
      if (!config || typeof config !== "object") return;
      state.config = config;
      const scheduler = config.scheduler || {};
      setSelect("schedulerEnabled", scheduler.enabled === false ? "off" : "on");
      setSelect("pauseDisconnect", scheduler.pause_on_extension_disconnect === false ? "keep" : "pause");
      setInput("discoveryCron", scheduler.discovery_cron);
      setInput("poolTarget", scheduler.pool_target_count);
      setInput("accountSyncInterval", scheduler.account_sync_interval_hours);
      setSelect("autoUpdate", scheduler.auto_update_enabled === true ? "on" : "off");
      setInput("autoUpdateInterval", scheduler.auto_update_check_interval_hours);
      setInput("shareBilibili", scheduler.pool_source_shares?.bilibili);
      setInput("shareXhs", scheduler.pool_source_shares?.xiaohongshu);
      setInput("shareDouyin", scheduler.pool_source_shares?.douyin);
      setInput("shareYoutube", scheduler.pool_source_shares?.youtube);
      setInput("speculationInterval", scheduler.speculation_interval_minutes);
      setInput("speculationTtl", scheduler.speculation_ttl_days);
      setInput("speculationCooldown", scheduler.speculation_cooldown_days);
      setInput("speculationThreshold", scheduler.speculation_confirmation_threshold);
      setInput("speculationMaxActive", scheduler.speculation_max_active);
      setInput("speculationMaxPrimary", scheduler.speculation_max_primary_interests);
      setInput("speculationMaxSecondary", scheduler.speculation_max_secondary_interests);

      setSelect("language", config.language || "zh");
      setInput("dataDir", config.data_dir);
      setInput("storageDbPath", config.storage?.db_path);

      const llm = config.llm || {};
      const provider = llm.default_provider || llm.provider;
      setSelect("llmProvider", provider);
      const fallbackProvider = llm.fallback_provider || "";
      setSelect("llmFallbackProvider", fallbackProvider);
      setInput("llmTimeout", llm.timeout);
      setSelect("llmAuthMode", llm.openai?.auth_mode || "api_key");
      if (provider) {
        setInput("llmModel", llm[provider]?.model);
        setInput("llmApiKey", llm[provider]?.api_key);
        setInput("llmBaseUrl", llm[provider]?.base_url);
      }
      if (fallbackProvider) {
        setSelect("llmFallbackAuthMode", llm[fallbackProvider]?.auth_mode || "api_key");
        setInput("llmFallbackModel", llm[fallbackProvider]?.model);
        setInput("llmFallbackApiKey", llm[fallbackProvider]?.api_key);
        setInput("llmFallbackBaseUrl", llm[fallbackProvider]?.base_url);
      } else {
        setSelect("llmFallbackAuthMode", "api_key");
        setInput("llmFallbackModel", "");
        setInput("llmFallbackApiKey", "");
        setInput("llmFallbackBaseUrl", "");
      }
      setInput("openrouterReferer", llm.openrouter?.http_referer);
      setInput("openrouterTitle", llm.openrouter?.x_title);
      setSelect("embeddingProvider", llm.embedding?.provider || "");
      const embeddingFallbackProvider = llm.embedding?.fallback_provider || "";
      setSelect("embeddingFallbackProvider", embeddingFallbackProvider);
      setInput("embeddingModel", llm.embedding?.model);
      setInput("embeddingApiKey", llm.embedding?.api_key);
      setInput("embeddingBaseUrl", llm.embedding?.base_url);
      setInput("embeddingSimilarity", llm.embedding?.similarity_threshold);
      if (embeddingFallbackProvider) {
        setInput("embeddingFallbackModel", llm[embeddingFallbackProvider]?.model);
        setInput("embeddingFallbackApiKey", llm[embeddingFallbackProvider]?.api_key);
        setInput("embeddingFallbackBaseUrl", llm[embeddingFallbackProvider]?.base_url);
      } else {
        setInput("embeddingFallbackModel", "");
        setInput("embeddingFallbackApiKey", "");
        setInput("embeddingFallbackBaseUrl", "");
      }
      setSelect("moduleSoulProvider", llm.soul?.provider || "");
      setInput("moduleSoulModel", llm.soul?.model);
      setSelect("moduleDiscoveryProvider", llm.discovery?.provider || "");
      setInput("moduleDiscoveryModel", llm.discovery?.model);
      setSelect("moduleRecommendationProvider", llm.recommendation?.provider || "");
      setInput("moduleRecommendationModel", llm.recommendation?.model);
      setSelect("moduleEvaluationProvider", llm.evaluation?.provider || "");
      setInput("moduleEvaluationModel", llm.evaluation?.model);

      setSelect("biliAuth", config.bilibili?.auth_method || "cookie");
      setInput("biliCookie", config.bilibili?.cookie);
      setInput("biliBrowserExecutable", config.bilibili?.browser_executable);
      setSelect("biliBrowserHeaded", config.bilibili?.browser_headed === true ? "on" : "off");
      setSelect("bilibiliEnabled", config.sources?.bilibili?.enabled === false ? "off" : "on");
      setInput("sourcesBrowserCdp", config.sources?.browser?.cdp_url);
      setSelect("sourcesBrowserHeaded", config.sources?.browser?.headed === true ? "on" : "off");
      setSelect("xhsEnabled", config.sources?.xiaohongshu?.enabled === false ? "off" : "on");
      setInput("xhsDailySearchBudget", config.sources?.xiaohongshu?.daily_search_budget);
      setInput("xhsDailyCreatorBudget", config.sources?.xiaohongshu?.daily_creator_budget);
      setInput("xhsTaskInterval", config.sources?.xiaohongshu?.task_interval_seconds);
      setSelect("douyinEnabled", config.sources?.douyin?.enabled === true ? "on" : "off");
      setInput("douyinCookieEnv", config.sources?.douyin?.cookie_env);
      setInput("douyinDailySearchBudget", config.sources?.douyin?.daily_search_budget);
      setInput("douyinDailyHotBudget", config.sources?.douyin?.daily_hot_budget);
      setInput("douyinDailyFeedBudget", config.sources?.douyin?.daily_feed_budget);
      setInput("douyinRequestInterval", config.sources?.douyin?.request_interval_seconds);
      setSelect("youtubeEnabled", config.sources?.youtube?.enabled === true ? "on" : "off");
      setInput("youtubeDailySearchBudget", config.sources?.youtube?.daily_search_budget);
      setInput("youtubeDailyTrendingBudget", config.sources?.youtube?.daily_trending_budget);
      setInput("youtubeDailyChannelBudget", config.sources?.youtube?.daily_channel_budget);
      setInput("youtubeRequestInterval", config.sources?.youtube?.request_interval_seconds);

      setSelect("logLevel", config.logging?.level || "INFO");
      setSelect("logFileLevel", config.logging?.file_level || "DEBUG");
      setInput("logPath", resolveLogPath(config.logging));
      setInput("logMaxFileSize", config.logging?.max_file_size_mb);
      setInput("logBackupCount", config.logging?.backup_count);
      setInput("logAggregateBudget", config.logging?.aggregate_budget_mb);
      setInput("logUnmanagedTruncate", config.logging?.unmanaged_truncate_mb);
      setInput("logUnmanagedMaxAge", config.logging?.unmanaged_max_age_days);

      if ($("#configStatus")) $("#configStatus").value = "配置已从后端加载。";
      if (state.runtimeStatus) applyRuntimeStatus(state.runtimeStatus);
      restoreFrontendSettings();
    }

    function normalizeDelight(item) {
      if (!item) return null;
      return {
        type: "delight",
        bvid: String(item.bvid ?? item.content_id ?? ""),
        title: decodeHtmlEntities(item.title ?? "发现了一条你可能会意外喜欢的内容"),
        reason: decodeHtmlEntities(item.delight_reason ?? item.reason ?? item.delight_hook ?? item.message ?? "这条来自后端高惊喜分候选。"),
        cover_url: normalizeImageUrl(item.cover_url ?? item.cover ?? item.pic ?? item.thumbnail_url ?? item.thumbnail ?? item.image_url),
        content_url: String(item.content_url ?? ""),
        source_platform: String(item.source_platform ?? item.platform ?? "bilibili"),
        chat_turn_id: String(item.chat_turn_id ?? ""),
        chat_reply: String(item.chat_reply ?? item.reply ?? ""),
        chat_draft: String(item.chat_draft ?? ""),
        response_message: String(item.response_message ?? ""),
        turns: delightTurnList(item.turns)
      };
    }

    function renderDelightCover(delight) {
      const thumb = $("#delightBanner .thumb");
      if (!thumb) return;
      const url = imageProxyUrl(delight?.cover_url);
      thumb.replaceChildren();
      thumb.classList.toggle("has-image", Boolean(url));
      if (!url) return;
      const image = document.createElement("img");
      image.src = url;
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => {
        image.remove();
        thumb.classList.remove("has-image");
      });
      thumb.append(image);
    }

    function setActiveDelight(index = state.delightIndex) {
      const controls = Array.from(document.querySelectorAll("[data-delight]"));
      if (!state.delights.length) {
        state.delight = null;
        closeDelightComposer();
        renderDelightCover(null);
        renderDelightTurns(null);
        $("#delightTitle").textContent = "暂无惊喜队列";
        $("#delightReason").textContent = "后端产生新的高惊喜候选后会通过实时流出现在这里。";
        if ($("#delightStatus")) $("#delightStatus").textContent = "";
        if ($("#delightCount")) $("#delightCount").textContent = "0/0";
        controls.forEach((btn) => { btn.disabled = true; });
        scheduleActivityRailHeightSync();
        return;
      }
      state.delightIndex = Math.max(0, Math.min(index, state.delights.length - 1));
      state.delight = state.delights[state.delightIndex];
      closeDelightComposer();
      renderDelightCover(state.delight);
      renderDelightTurns(state.delight);
      $("#delightTitle").textContent = state.delight.title;
      $("#delightReason").textContent = state.delight.reason;
      if ($("#delightStatus")) $("#delightStatus").textContent = state.delight.response_message || "";
      if ($("#delightCount")) $("#delightCount").textContent = `${state.delightIndex + 1}/${state.delights.length}`;
      // Sync ☆ / ♥ pressed state for the current delight.
      const delightBvid = state.delight.bvid;
      const wlBtn = document.querySelector('[data-delight="watch-later"]');
      if (wlBtn && delightBvid) {
        wlBtn.setAttribute("aria-pressed", "false");
        watchLaterStatus(delightBvid).then((res) => {
          if (state.delight?.bvid === delightBvid && res?.saved) {
            wlBtn.setAttribute("aria-pressed", "true");
          }
        }).catch(() => {});
      }
      const favBtn = document.querySelector('[data-delight="favorite"]');
      if (favBtn && delightBvid) {
        favBtn.setAttribute("aria-pressed", "false");
        favoriteStatus(delightBvid).then((res) => {
          if (state.delight?.bvid === delightBvid && res?.saved) {
            favBtn.setAttribute("aria-pressed", "true");
          }
        }).catch(() => {});
      }
      controls.forEach((btn) => {
        const action = btn.dataset.delight;
        btn.disabled = (action === "prev" && state.delightIndex === 0) || (action === "next" && state.delightIndex === state.delights.length - 1);
      });
      scheduleActivityRailHeightSync();
    }

    function applyDelights(payload) {
      const hasQueuePayload = Array.isArray(payload?.items) || Boolean(payload?.item);
      if (!hasQueuePayload) return;
      const items = Array.isArray(payload?.items) ? payload.items : payload.item ? [payload.item] : [];
      const normalized = items.map(normalizeDelight).filter(Boolean);
      const previousActiveBvid = String(state.delight?.bvid || "");
      const existingByBvid = new Map(state.delights.map((item) => [String(item.bvid || ""), item]));
      state.delights = [];
      for (const item of normalized) {
        const key = String(item.bvid || "");
        if (!key) continue;
        const existingIndex = state.delights.findIndex((current) => String(current.bvid || "") === key);
        const merged = mergeDelightItem(existingByBvid.get(key) || state.delights[existingIndex], item);
        if (existingIndex >= 0) state.delights[existingIndex] = merged;
        else state.delights.push(merged);
      }
      const nextIndex = previousActiveBvid
        ? Math.max(0, state.delights.findIndex((item) => String(item.bvid || "") === previousActiveBvid))
        : 0;
      setActiveDelight(nextIndex);
    }

    function mergeMessages(items) {
      for (const raw of items) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!state.messages.some((msg) => messageKey(msg) === key)) state.messages.push(item);
      }
      renderMessages();
      applyRuntimeStatus({ unread_count: getRenderableMessages().length });
    }

    async function fetchDelightQueue() {
      const payload = await requestJson(`${ENDPOINTS.delightBatch}?limit=${getDelightQueueLimit()}`);
      applyDelights(payload);
    }

    function handleRuntimeEvent(event) {
      if (!event?.type) return;
      applyRuntimeStatus({ ...event, live_summary: event.message || event.live_summary || event.type });
      if (["refresh.pool_updated", "recommendation.reshuffled", "config_reloaded", "init_completed"].includes(event.type)) scheduleBackendHydration();
      if (event.type === "activity.added") scheduleActivityPageRefresh();
      if (
        event.type === "profile_updated" ||
        event.type === "interest.confirmed" ||
        event.type === "interest.rejected" ||
        event.type === "interest.chat" ||
        event.type === "avoidance.confirmed" ||
        event.type === "avoidance.rejected" ||
        event.type === "avoidance.chat"
      ) void refreshProfile();
      if (event.type === "delight.candidate" && event.bvid) {
        const delight = normalizeDelight(event);
        if (delight) {
          const key = String(delight.bvid || "");
          const existingIndex = state.delights.findIndex((item) => String(item.bvid || "") === key);
          if (existingIndex >= 0) {
            state.delights[existingIndex] = mergeDelightItem(state.delights[existingIndex], delight);
            if (state.delight && String(state.delight.bvid || "") === key) setActiveDelight(existingIndex);
          } else {
            state.delights.push(delight);
            setActiveDelight(state.delights.length - 1);
          }
        }
      }
      if (event.type === "delight.refreshed") scheduleDelightQueueRefresh();
      if (event.type === "notification.pending" && event.bvid) mergeMessages([{ ...event, type: "notification" }]);
      if (event.type === "interest.probe" && event.domain) mergeMessages([{ type: "interest.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个兴趣方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
      if (event.type === "avoidance.probe" && event.domain) mergeMessages([{ type: "avoidance.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个避雷方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
    }

    function connectRuntimeStream() {
      if (state.runtimeSocket) state.runtimeSocket.close();
      try {
        const socket = new WebSocket(getRuntimeStreamUrl());
        state.runtimeSocket = socket;
        socket.addEventListener("open", () => { $("#statusLabel").textContent = "实时连接中"; });
        socket.addEventListener("message", (event) => {
          try { handleRuntimeEvent(JSON.parse(event.data)); } catch {}
        });
        socket.addEventListener("close", () => {
          if (state.runtimeSocket === socket) window.setTimeout(connectRuntimeStream, 3000);
        });
        socket.addEventListener("error", () => { $("#statusLabel").textContent = "实时流断开"; });
      } catch {
        $("#statusLabel").textContent = "实时流不可用";
      }
    }

    async function refreshProfile() {
      const payload = await requestJson(ENDPOINTS.profile);
      const profile = payload?.profile || payload;
      if (profile && profile.initialized !== false) {
        state.profile = profile;
        hydrateInboxFromSpeculations(profile.speculative_interests);
        hydrateInboxFromSpeculations(profile.speculative_avoidances, "avoidance.probe");
        renderRail();
        renderProfileDetails();
        renderMessages();
      }
    }

    async function hydrateFromBackend() {
      const [health, recs, runtime, activity, profile, delights, notification, chatTurns, delightChatTurns, config] = await Promise.all([
        requestJson(ENDPOINTS.health),
        requestJson(ENDPOINTS.recommendations),
        requestJson(ENDPOINTS.runtimeStatus),
        requestJson(`${ENDPOINTS.activityFeed}?limit=5`),
        requestJson(ENDPOINTS.profile),
        requestJson(`${ENDPOINTS.delightBatch}?limit=${getDelightQueueLimit()}`),
        requestJson(ENDPOINTS.notificationPending),
        requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=chat&limit=20`),
        requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=delight&limit=80`),
        requestJson(ENDPOINTS.config)
      ]);
      if (health) $("#statusLabel").textContent = "已连接本地后端";
      const recommendationItems = Array.isArray(recs) ? recs : asArray(recs?.items);
      if (recommendationItems.length) state.videos = normalizeRecommendationList(recommendationItems);
      if (activity) {
        state.activity = activity;
        state.activityItems = asArray(activity.items);
        state.activityCursor = activity.next_cursor || activity.next || "";
        state.activityHasMore = Boolean(activity.has_more && state.activityCursor);
      }
      const profilePayload = profile?.profile || profile;
      if (profilePayload && profilePayload.initialized !== false) {
        state.profile = profilePayload;
        hydrateInboxFromSpeculations(profilePayload.speculative_interests);
        hydrateInboxFromSpeculations(profilePayload.speculative_avoidances, "avoidance.probe");
      }
      const chatItems = Array.isArray(chatTurns) ? chatTurns : asArray(chatTurns?.items);
      if (chatItems.length) {
        state.chat = chatItems.flatMap((turn) => [
          { role: "user", text: turn.message || turn.user_message || "" },
          { role: "agent", text: turn.reply || turn.assistant_message || turn.status || "等待后端回复中。" }
        ]).filter((item) => item.text);
      }
      applyRuntimeStatus(runtime?.status || runtime);
      applyDelights(delights);
      const delightChatItems = Array.isArray(delightChatTurns) ? delightChatTurns : asArray(delightChatTurns?.items);
      for (const turn of delightChatItems.filter(Boolean)) applyTurnToDelight({ ...turn, scope: turn.scope || "delight" });
      if (notification?.item) mergeMessages([{ ...notification.item, type: "notification" }]);
      applyConfig(config?.config || config);
      renderAll();
    }

    function renderAll() {
      const steps = [renderReshuffleToggle, renderFilters, renderVideos, syncSourceMetric, renderRail, renderProfileDetails, renderMessages, renderChat, renderPoolStatus];
      for (const step of steps) {
        try { step(); } catch (error) { showFatal(error, step.name || "渲染"); }
      }
      scheduleActivityRailHeightSync();
    }

    function buildConfigUpdate() {
      const provider = $("#llmProvider").value;
      const fallbackProvider = getInput("llmFallbackProvider");
      const llmProviderConfig = { model: getInput("llmModel") };
      if (provider === "openai") llmProviderConfig.auth_mode = getInput("llmAuthMode") || "api_key";
      if (getInput("llmApiKey")) llmProviderConfig.api_key = getInput("llmApiKey");
      if (getInput("llmBaseUrl")) llmProviderConfig.base_url = getInput("llmBaseUrl");
      const llmFallbackConfig = { model: getInput("llmFallbackModel") };
      if (fallbackProvider === "openai") llmFallbackConfig.auth_mode = getInput("llmFallbackAuthMode") || "api_key";
      if (getInput("llmFallbackApiKey")) llmFallbackConfig.api_key = getInput("llmFallbackApiKey");
      if (getInput("llmFallbackBaseUrl")) llmFallbackConfig.base_url = getInput("llmFallbackBaseUrl");
      const logPath = splitLogPath(getInput("logPath"), state.config?.logging);
      const embeddingFallbackProvider = getInput("embeddingFallbackProvider");
      const embeddingFallbackConfig = { model: getInput("embeddingFallbackModel") };
      if (getInput("embeddingFallbackApiKey")) embeddingFallbackConfig.api_key = getInput("embeddingFallbackApiKey");
      if (getInput("embeddingFallbackBaseUrl")) embeddingFallbackConfig.base_url = getInput("embeddingFallbackBaseUrl");
      const embedding = {
        provider: $("#embeddingProvider").value,
        fallback_enabled: Boolean(embeddingFallbackProvider),
        fallback_provider: embeddingFallbackProvider,
        model: getInput("embeddingModel"),
        similarity_threshold: getFloatInput("embeddingSimilarity", 0.82)
      };
      if (getInput("embeddingApiKey")) embedding.api_key = getInput("embeddingApiKey");
      if (getInput("embeddingBaseUrl")) embedding.base_url = getInput("embeddingBaseUrl");
      const cookie = getInput("biliCookie");
      const llm = {
        ...(state.config?.llm || {}),
        default_provider: provider,
        fallback_enabled: Boolean(fallbackProvider),
        fallback_provider: fallbackProvider,
        timeout: getIntInput("llmTimeout", 60),
        [provider]: { ...(state.config?.llm?.[provider] || {}), ...llmProviderConfig },
        embedding: { ...(state.config?.llm?.embedding || {}), ...embedding },
        soul: { ...(state.config?.llm?.soul || {}), provider: getInput("moduleSoulProvider"), model: getInput("moduleSoulModel") },
        discovery: { ...(state.config?.llm?.discovery || {}), provider: getInput("moduleDiscoveryProvider"), model: getInput("moduleDiscoveryModel") },
        recommendation: { ...(state.config?.llm?.recommendation || {}), provider: getInput("moduleRecommendationProvider"), model: getInput("moduleRecommendationModel") },
        evaluation: { ...(state.config?.llm?.evaluation || {}), provider: getInput("moduleEvaluationProvider"), model: getInput("moduleEvaluationModel") }
      };
      if (fallbackProvider && fallbackProvider !== provider) {
        llm[fallbackProvider] = {
          ...(state.config?.llm?.[fallbackProvider] || {}),
          ...llmFallbackConfig
        };
      }
      if (embeddingFallbackProvider) {
        llm[embeddingFallbackProvider] = {
          ...(llm[embeddingFallbackProvider] || state.config?.llm?.[embeddingFallbackProvider] || {}),
          ...embeddingFallbackConfig
        };
      }
      if (getInput("openrouterReferer") || getInput("openrouterTitle")) {
        llm.openrouter = {
          ...(llm.openrouter || {}),
          http_referer: getInput("openrouterReferer"),
          x_title: getInput("openrouterTitle")
        };
      }
      return {
        language: getInput("language") || "zh",
        data_dir: getInput("dataDir"),
        llm,
        bilibili: {
          auth_method: $("#biliAuth").value,
          ...(cookie ? { cookie } : {}),
          browser_executable: getInput("biliBrowserExecutable"),
          browser_headed: $("#biliBrowserHeaded").value === "on"
        },
        sources: {
          browser: {
            cdp_url: getInput("sourcesBrowserCdp"),
            headed: $("#sourcesBrowserHeaded").value === "on"
          },
          bilibili: {
            enabled: $("#bilibiliEnabled").value === "on"
          },
          xiaohongshu: {
            enabled: $("#xhsEnabled").value === "on",
            daily_search_budget: getIntInput("xhsDailySearchBudget", 30),
            daily_creator_budget: getIntInput("xhsDailyCreatorBudget", 10),
            task_interval_seconds: getIntInput("xhsTaskInterval", 45)
          },
          douyin: {
            enabled: $("#douyinEnabled").value === "on",
            mode: "direct",
            cookie_env: getInput("douyinCookieEnv"),
            daily_search_budget: getIntInput("douyinDailySearchBudget", 30),
            daily_hot_budget: getIntInput("douyinDailyHotBudget", 5),
            daily_feed_budget: getIntInput("douyinDailyFeedBudget", 30),
            request_interval_seconds: getIntInput("douyinRequestInterval", 2)
          },
          youtube: {
            enabled: $("#youtubeEnabled").value === "on",
            daily_search_budget: getIntInput("youtubeDailySearchBudget", 6),
            daily_trending_budget: getIntInput("youtubeDailyTrendingBudget", 50),
            daily_channel_budget: getIntInput("youtubeDailyChannelBudget", 10),
            request_interval_seconds: getIntInput("youtubeRequestInterval", 2)
          }
        },
        scheduler: {
          enabled: $("#schedulerEnabled").value === "on",
          pause_on_extension_disconnect: $("#pauseDisconnect").value === "pause",
          discovery_cron: getInput("discoveryCron"),
          pool_target_count: getIntInput("poolTarget", 600),
          account_sync_interval_hours: getIntInput("accountSyncInterval", 6),
          pool_source_shares: {
            bilibili: getIntInput("shareBilibili", 8),
            xiaohongshu: getIntInput("shareXhs", 1),
            douyin: getIntInput("shareDouyin", 1),
            youtube: getIntInput("shareYoutube", 1)
          },
          speculation_interval_minutes: getIntInput("speculationInterval", 10),
          speculation_ttl_days: getIntInput("speculationTtl", 3),
          speculation_cooldown_days: getIntInput("speculationCooldown", 7),
          speculation_confirmation_threshold: getIntInput("speculationThreshold", 3),
          speculation_max_active: getIntInput("speculationMaxActive", 5),
          speculation_max_primary_interests: getIntInput("speculationMaxPrimary", 15),
          speculation_max_secondary_interests: getIntInput("speculationMaxSecondary", 60),
          auto_update_enabled: $("#autoUpdate").value === "on",
          auto_update_check_interval_hours: getIntInput("autoUpdateInterval", 6)
        },
        storage: { db_path: getInput("storageDbPath") },
        logging: {
          level: getInput("logLevel") || "INFO",
          file_level: getInput("logFileLevel") || "DEBUG",
          directory: logPath.directory,
          filename: logPath.filename,
          file_path: getInput("logPath"),
          max_file_size_mb: getIntInput("logMaxFileSize", 100),
          backup_count: getIntInput("logBackupCount", 1),
          aggregate_budget_mb: getIntInput("logAggregateBudget", 500),
          unmanaged_truncate_mb: getIntInput("logUnmanagedTruncate", 200),
          unmanaged_max_age_days: getIntInput("logUnmanagedMaxAge", 30)
        }
      };
    }

    document.addEventListener("click", (event) => {
      const closeId = event.target?.dataset?.close;
      if (closeId) closePanel(closeId);
    });

    function setActiveSettingsPanel(panelName = "models") {
      document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
        const isActive = tab.dataset.settingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.settingsPanel !== panelName;
      });
    }

    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveSettingsPanel(tab.dataset.settingsTab));
    });

    function setActiveModelSettingsPanel(groupName = "llm", panelName = "default") {
      document.querySelectorAll(`[data-model-settings-tab][data-model-settings-group="${groupName}"]`).forEach((tab) => {
        const isActive = tab.dataset.modelSettingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      document.querySelectorAll(`[data-model-settings-panel][data-model-settings-group="${groupName}"]`).forEach((panel) => {
        panel.hidden = panel.dataset.modelSettingsPanel !== panelName;
      });
    }

    document.querySelectorAll("[data-model-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveModelSettingsPanel(tab.dataset.modelSettingsGroup, tab.dataset.modelSettingsTab));
    });

    function startChatPlaceholderRotation() {
      const input = $("#chatInput");
      if (!input || chatPlaceholderTimer) return;
      chatPlaceholderTimer = window.setInterval(() => {
        if (document.activeElement === input || input.value.trim()) return;
        chatPlaceholderIndex = (chatPlaceholderIndex + 1) % CHAT_PLACEHOLDERS.length;
        input.setAttribute("placeholder", CHAT_PLACEHOLDERS[chatPlaceholderIndex]);
      }, 5000);
    }

    safeBind("#sideDrawerBtn", "click", toggleSideDrawer);
    safeBind(".brand", "click", (event) => { event.preventDefault(); openHomePage(); });
    safeBind("#sideDrawerScrim", "click", closeSideDrawer);
    safeBind("#mobileMenuBtn", "click", openMobileMenu);
    safeBind("#mobileMenuClose", "click", closeMobileMenu);
    safeBind("#mobileSearchInput", "input", (event) => { state.query = event.target.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; renderAll(); });
    safeBind("#mobileSearchForm", "submit", (event) => { event.preventDefault(); state.query = $("#mobileSearchInput")?.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; renderAll(); closeMobileMenu(); });
    document.querySelectorAll("[data-mobile-panel]").forEach((button) => {
      button.addEventListener("click", () => openMobilePanel(button.dataset.mobilePanel, { settingsPanel: button.dataset.settings }));
    });
    document.querySelectorAll("[data-mobile-page]").forEach((button) => {
      button.addEventListener("click", () => {
        openMobilePage(button.dataset.mobilePage, { settingsPanel: button.dataset.settings });
      });
    });
    document.querySelectorAll("[data-mobile-back]").forEach((button) => {
      button.addEventListener("click", returnToMobileMenu);
    });

    safeBind("#profileBtn", "click", openProfilePage);
    safeBind("#homeBtn", "click", openHomePage);
    safeBind("#watchLaterBtn", "click", openWatchLaterPage);
    safeBind("#favoritesBtn", "click", openFavoritesPage);
    safeBind("#profileMemoryMoreBtn", "click", loadMoreProfileMemory);
    safeBind("#chatBtn", "click", openChatPage);
    safeBind("#messagesBtn", "click", () => {
      closeSideDrawer();
      hydrateInboxFromSpeculations(state.profile?.speculative_interests);
      hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
      state.messageListSnapshot = getRenderableMessages();
      openPanel("messagesDrawer");
      returnToMessages();
      renderMessages();
      void refreshProfile().catch(() => {});
    });
    safeBind("#activityBtn", "click", () => { closeSideDrawer(); renderActivityHistory(); openPanel("activityDrawer"); });
    safeBind("#activityMoreBtn", "click", () => loadActivityPage());
    safeBind("#settingsBtn", "click", () => openSettingsPage("models"));
    safeBind("#openSettingsHero", "click", () => openSettingsPage("models"));
    syncTopbarHeight();
    window.addEventListener("resize", syncTopbarHeight);
    ["#dismissOnReshuffleToggle", "#dismissOnReshuffleSetting"].forEach((selector) => {
      safeBind(selector, "change", (event) => {
        setDismissOnReshuffle(Boolean(event.target.checked), { toast: true });
      });
    });
    safeBind("#reshuffleBtn", "click", reshuffle);
    safeBind("#loadMoreBtn", "click", appendMore);
    safeBind("#delightThumb", "click", () => respondDelight(state.delight, "view"));
    safeBind("#delightThumb", "keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      respondDelight(state.delight, "view");
    });
    safeBind("#delightCommentInput", "keydown", (event) => {
      if (event.key === "Enter") respondDelight(state.delight, "send-comment");
      if (event.key === "Escape") closeDelightComposer();
    });
    safeBind("#resetFiltersBtn", "click", () => { state.query = ""; state.filter = "全部"; const input = $("#searchInput"); if (input) input.value = ""; renderAll(); });
    safeBind("#searchInput", "input", (event) => { state.query = event.target.value || ""; renderAll(); });
    safeBind("#searchForm", "submit", (event) => { event.preventDefault(); state.query = $("#searchInput")?.value || ""; renderAll(); });
    window.addEventListener("resize", scheduleActivityRailHeightSync);
    safeBind("#chatForm", "submit", (event) => { event.preventDefault(); const input = $("#chatInput"); const text = input?.value?.trim() || ""; if (!text) return; input.value = ""; sendChat(text); });
    safeBind("#messageChatBackBtn", "click", returnToMessages);
    safeBind("#messageChatForm", "submit", (event) => {
      event.preventDefault();
      const input = $("#messageChatInput");
      const text = input?.value?.trim() || "";
      if (!text) return;
      input.value = "";
      sendChat(text, {
        contextPrefix: state.messageChatPrompt,
        scope: state.messageChatScope,
        subjectId: state.messageChatDomain,
        subjectTitle: state.messageChatSubjectTitle
      });
    });
    safeBind("#llmProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), default_provider: $("#llmProvider")?.value || "" } }));
    safeBind("#llmFallbackProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), fallback_provider: $("#llmFallbackProvider")?.value || "" } }));
    safeBind("#embeddingFallbackProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), embedding: { ...(state.config?.llm?.embedding || {}), fallback_provider: $("#embeddingFallbackProvider")?.value || "" } } }));
    safeBind("#suggestSharesBtn", "click", async () => {
      const result = await requestJson(ENDPOINTS.sourceShareSuggestion, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled_sources: { bilibili: $("#bilibiliEnabled").value === "on", xiaohongshu: $("#xhsEnabled").value === "on", douyin: $("#douyinEnabled").value === "on", youtube: $("#youtubeEnabled").value === "on" }, configured_shares: buildConfigUpdate().scheduler.pool_source_shares }) });
      const shares = result?.pool_source_shares || result?.shares || result?.suggested_shares;
      if (shares) {
        setInput("shareBilibili", shares.bilibili);
        setInput("shareXhs", shares.xiaohongshu);
        setInput("shareDouyin", shares.douyin);
        setInput("shareYoutube", shares.youtube);
        showToast("已应用来源占比建议");
      } else {
        showToast("没有拿到占比建议");
      }
    });
    safeBind("#settingsForm", "submit", async (event) => {
      event.preventDefault();
      const submitBtn = $("#settingsForm button[type='submit']");
      const previousText = submitBtn?.textContent || "保存配置";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "保存中…";
      }
      const endpoint = persistBackendEndpoint();
      const frontend = persistFrontendSettings();
      if ($("#configStatus")) $("#configStatus").value = `正在保存到 ${endpoint.host}:${endpoint.port}，惊喜队列加载 ${frontend.delightQueueLimit} 条，换一批忽略当前${frontend.dismissOnReshuffle ? "已开启" : "已关闭"}，后端热重载可能需要几秒。`;
      try {
        const payload = buildConfigUpdate();
        const result = await requestJsonStrict(ENDPOINTS.config.replace("?reveal_keys=true", ""), {
          method: "PUT",
          timeoutMs: 60000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (result?.config) applyConfig(result.config);
        const message = result?.message || "配置已保存。";
        const suffix = result?.restart_required ? "\n当前配置需要重启后端后完全生效。" : result?.reloaded === false ? "\n后端返回未热重载，请检查运行状态。" : "";
        if ($("#configStatus")) $("#configStatus").value = `${message}${suffix}`;
        showToast(result?.restart_required ? "配置已保存，需要重启后端" : "配置已保存");
        void hydrateFromBackend();
      } catch (error) {
        const message = configErrorMessage(error.details) || error.message || "未知错误";
        if ($("#configStatus")) $("#configStatus").value = `保存失败：\n${message}`;
        showToast("保存失败：请查看配置状态");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = previousText;
        }
      }
    });
    document.querySelectorAll("[data-delight]").forEach((btn) => btn.addEventListener("click", async () => {
      const response = btn.dataset.delight;
      if (response === "prev") { setActiveDelight(state.delightIndex - 1); return; }
      if (response === "next") { setActiveDelight(state.delightIndex + 1); return; }
      await respondDelight(state.delight, response);
    }));

    restoreBackendEndpoint();
    restoreFrontendSettings();
    setSideDrawerOpen(!isMobileViewport() && storageGet(SIDE_DRAWER_OPEN_KEY) !== "0", { persist: false });
    startChatPlaceholderRotation();
    try {
      renderAll();
    } catch (error) {
      console.error("首屏渲染失败", error);
      $("#statusLabel").textContent = "首屏渲染失败";
      $("#runtimeSummary").textContent = error?.message || "请检查后端返回的数据结构。";
    }
    hydrateFromBackend()
      .then(connectRuntimeStream)
      .catch((error) => {
        console.error("后端数据加载失败", error);
        $("#statusLabel").textContent = "后端数据加载失败";
        $("#runtimeSummary").textContent = error?.message || "页面已保留离线数据，可打开设置检查 FastAPI 地址。";
        showToast("后端数据加载失败，页面已保留离线数据");
      });
    })();
