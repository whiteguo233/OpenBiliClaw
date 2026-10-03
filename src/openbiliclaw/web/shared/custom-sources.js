(function (global) {
  "use strict";

  const STATUS_LABELS = {
    ready: "正常", ok: "正常", active: "正常", idle: "等待下次发现",
    never_run: "等待首次发现", pending: "等待运行", running: "正在发现",
    needs_login: "需要重新登录", login_required: "需要重新登录",
    needs_details: "需要补充说明", failed: "运行失败", error: "运行失败",
    rate_limited: "暂时限流", unavailable: "执行环境不可用", disabled: "已停用",
    empty: "本轮暂无新内容", not_modified: "内容未变化", archived: "已归档", interrupted: "运行中断",
    stale: "本次结果已失效", superseded: "已由新流程替代", cancelled: "已取消", degraded: "发现能力受限",
  };
  const text = (value) => String(value ?? "").trim();
  const array = (value) => Array.isArray(value) ? value : [];

  function sourceName(item, fallback = "网页") {
    return text(item?.source_name) || fallback;
  }

  function statusLabel(value) {
    return STATUS_LABELS[text(value)] || text(value) || "等待首次发现";
  }

  function formatTime(value) {
    if (!value) return "";
    const raw = text(value);
    const numeric = typeof value === "number" || /^\d{10,13}(?:\.\d+)?$/.test(raw);
    const stamp = numeric ? Number(value) * (Number(value) < 1e12 ? 1000 : 1) : raw;
    const date = new Date(stamp);
    if (!Number.isFinite(date.getTime())) return raw;
    return date.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    });
  }

  function shareEntries(items) {
    return array(items).map((source) => ({
      key: text(source.source_id), label: text(source.name) || "未命名网站",
      weight: Number(source.target_share) || 1, enabled: source.enabled !== false,
    }));
  }

  function sourcePatch(values) {
    const name = text(values.name);
    const target_share = Number(values.target_share);
    const interval_minutes = Number(values.interval_minutes);
    if (!name || name.length > 200) throw new Error("来源名称需为 1–200 个字符。");
    if (!Number.isInteger(target_share) || target_share < 1 || target_share > 20) {
      throw new Error("候选池占比需为 1–20 的整数。");
    }
    if (!Number.isInteger(interval_minutes) || interval_minutes < 5 || interval_minutes > 10080) {
      throw new Error("发现间隔需为 5–10080 分钟的整数。");
    }
    return { name, target_share, interval_minutes };
  }

  function takeoverUrl(value, baseUrl) {
    const base = new URL(baseUrl);
    const url = new URL(text(value), base);
    if (!text(value) || !["http:", "https:"].includes(url.protocol)
      || url.origin !== base.origin || url.username || url.password
      || !url.pathname.endsWith("/web/source-login.html") || !url.searchParams.get("draft_id")) {
      throw new Error("后端没有返回有效的登录接管地址，请刷新后重试。");
    }
    return url.href;
  }

  function errorMessage(error) {
    const detail = error?.details?.detail;
    return text(typeof detail === "string" ? detail : detail?.message)
      || text(error?.message) || "操作失败，请稍后重试。";
  }

  // The request adapter preserves each client's existing backend auth/timeout
  // policy. Source mutations never go through the global config save form.
  function createManager({ root, request, openUrl, baseUrl, onChat, onChange = () => {} }) {
    const doc = root.ownerDocument;
    let disposed = false;
    let revision = 0;
    let items = [];
    let installTimer = null;
    let installing = false;
    let installRequest = false;
    let executorSnapshot = [];
    function node(tag, className, value) {
      const element = doc.createElement(tag);
      if (className) element.className = className;
      if (value !== undefined) element.textContent = value;
      return element;
    }
    function button(label, action) {
      const element = node("button", "custom-source-button", label);
      element.type = "button";
      element.addEventListener("click", action);
      return element;
    }
    const toolbar = node("div", "custom-source-toolbar");
    const add = button("去聊一聊添加网站", () => onChat());
    const refresh = button("刷新来源", () => void load());
    toolbar.append(add, refresh);
    const message = node("p", "custom-source-message");
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");
    message.tabIndex = -1;
    const executorArea = node("div", "custom-source-executors");
    executorArea.setAttribute("aria-live", "polite");
    const list = node("div", "custom-source-list");
    root.classList.add("custom-source-manager");
    root.replaceChildren(toolbar, message, executorArea, list);

    function announce(value, failed = false) {
      message.textContent = value;
      message.dataset.tone = failed ? "error" : "success";
    }

    function visible() {
      return !disposed && doc.visibilityState !== "hidden"
        && (!root.getClientRects || root.getClientRects().length > 0);
    }

    function stopInstallPoll() {
      if (installTimer !== null) global.clearTimeout(installTimer);
      installTimer = null;
    }

    function scheduleInstallPoll() {
      stopInstallPoll();
      if (!installing || !visible()) return;
      installTimer = global.setTimeout(() => {
        installTimer = null;
        // Polling only refreshes the executor notice; it must not discard
        // edits the user is making in a source card while Chrome downloads.
        if (visible()) void load({ refreshSources: false });
      }, 3000);
    }

    function onVisibilityChange() {
      if (!visible()) stopInstallPoll();
      else if (installing) void load({ refreshSources: false });
    }
    doc.addEventListener?.("visibilitychange", onVisibilityChange);

    function renderExecutors(executors) {
      executorSnapshot = array(executors);
      executorArea.replaceChildren();
      const browser = executorSnapshot.find((executor) => executor?.id === "playwright");
      installing = browser?.status === "installing";
      if (!browser || browser.available === true) {
        installing = false;
        scheduleInstallPoll();
        return;
      }
      const notice = node("section", "custom-source-executor-notice");
      notice.append(node("strong", "", installing ? "正在安装浏览器…" : "浏览器发现暂不可用"));
      notice.append(node("p", "custom-source-note", text(browser.detail) || "安装后可发现需要浏览器打开的网站；公开订阅仍可继续使用。"));
      const install = button(installing ? "正在安装…" : "安装浏览器", () => {
        if (installRequest || installing || disposed) return;
        installRequest = true;
        install.disabled = true;
        install.textContent = "正在开始安装…";
        void (async () => {
          try {
            const result = await request("/custom-sources/executors/playwright/install", { method: "POST" });
            if (disposed) return;
            if (result?.status === "installing") {
              renderExecutors([{ ...browser, status: "installing", detail: result.detail || browser.detail }]);
              announce("浏览器安装已开始，可继续查看来源；安装完成后会更新状态。");
            } else {
              if (await load({ refreshSources: false })) {
                announce(result?.available ? "浏览器已就绪。" : text(result?.detail) || "已提交浏览器安装，请刷新查看状态。");
              }
            }
          } catch (error) {
            if (!disposed) {
              announce(errorMessage(error), true);
              install.disabled = false;
              install.textContent = "安装浏览器";
            }
          } finally {
            installRequest = false;
            // A manual refresh may replace this button while POST is pending.
            // Restore the currently mounted control, not a detached node.
            if (!disposed && !installing) renderExecutors(executorSnapshot);
          }
        })();
      });
      install.disabled = installing || installRequest;
      notice.append(install);
      executorArea.append(notice);
      scheduleInstallPoll();
    }

    function field(label, value, type, options = {}) {
      const wrapper = node("label", "custom-source-field");
      wrapper.append(node("span", "", label));
      const input = node("input");
      input.type = type;
      input.value = String(value ?? "");
      input.required = true;
      // Explicit unassociated form prevents validation or submission through
      // a surrounding desktop settings form. No global ID is introduced.
      input.setAttribute("form", "custom-source-independent-fields");
      input.setAttribute("data-settings-ignore-dirty", "");
      for (const [key, val] of Object.entries(options)) input.setAttribute(key, String(val));
      input.addEventListener("blur", () => input.reportValidity());
      wrapper.append(input);
      return { wrapper, input };
    }

    function render() {
      list.replaceChildren();
      if (!items.length) {
        list.append(node("p", "custom-source-note", "还没有自定义网站。到聊一聊说明网址和想看的内容，试跑确认后即可定期发现。"));
        return;
      }
      for (const source of items) {
        const id = text(source.source_id);
        const path = `/custom-sources/${encodeURIComponent(id)}`;
        const card = node("article", "custom-source-card");
        card.dataset.sourceId = id;
        const title = node("div", "custom-source-title");
        title.append(node("strong", "", text(source.name) || "未命名网站"));
        const status = node("span", "custom-source-status", source.enabled === false ? "已停用" : statusLabel(source.health?.status));
        status.dataset.status = text(source.health?.status);
        title.append(status);
        card.append(title, node("p", "custom-source-url", text(source.entry_url) || text(source.origin)),
          node("p", "custom-source-note", text(source.intent)));
        const health = source.health || {};
        if (health.last_error) card.append(node("p", "custom-source-error", text(health.last_error)));
        const timing = [];
        if (health.last_success_at) timing.push(`最近成功：${formatTime(health.last_success_at)}`);
        if (source.enabled !== false && health.next_run_at) timing.push(`下次发现：${formatTime(health.next_run_at)}`);
        if (timing.length) card.append(node("p", "custom-source-note", timing.join(" · ")));
        const workflows = array(source.workflows);
        const details = node("details", "custom-source-workflows");
        details.append(node("summary", "", `${workflows.length} 个发现任务`));
        workflows.forEach((flow) => details.append(node("p", "custom-source-note",
          `${text(flow.intent) || "发现内容"} · ${text(flow.executor)} · ${statusLabel(flow.status)}`)));
        card.append(details);
        const fields = node("div", "custom-source-fields");
        const name = field("来源名称", source.name, "text", { maxlength: 200 });
        const share = field("候选池占比（份）", source.target_share, "number", { min: 1, max: 20, step: 1, inputmode: "numeric" });
        const interval = field("发现间隔（分钟）", source.interval_minutes, "number", { min: 5, max: 10080, step: 1, inputmode: "numeric" });
        fields.append(name.wrapper, share.wrapper, interval.wrapper);
        card.append(fields);
        const actions = node("div", "custom-source-actions");
        const local = node("p", "custom-source-message");
        local.setAttribute("role", "status");
        local.tabIndex = -1;
        const runs = node("div", "custom-source-runs");
        let busy = false;
        async function perform(operation, success) {
          if (busy || disposed) return;
          busy = true;
          const controls = Array.from(card.querySelectorAll("button, input"));
          controls.forEach((control) => { control.disabled = true; });
          local.textContent = "正在处理…";
          local.dataset.tone = "pending";
          try {
            await operation();
            if (!disposed) {
              local.textContent = success;
              local.dataset.tone = "success";
            }
          } catch (error) {
            if (!disposed) {
              local.textContent = errorMessage(error);
              local.dataset.tone = "error";
              local.focus();
            }
          } finally {
            busy = false;
            controls.forEach((control) => { control.disabled = false; });
          }
        }
        const update = (patch) => request(path, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
        });
        const save = button("保存来源设置", () => {
          const inputs = [name.input, share.input, interval.input];
          if (inputs.some((input) => !input.reportValidity())) return;
          let patch;
          try { patch = sourcePatch({ name: name.input.value, target_share: share.input.value, interval_minutes: interval.input.value }); }
          catch (error) { local.textContent = errorMessage(error); local.dataset.tone = "error"; local.focus(); return; }
          void perform(async () => { await update(patch); await reloadAfterMutation("来源设置已保存。"); }, "已保存。");
        });
        const toggle = button(source.enabled === false ? "启用" : "停用", () => void perform(async () => {
          await update({ enabled: source.enabled === false }); await reloadAfterMutation("来源状态已更新。");
        }, "已更新。"));
        const login = button("重新登录", () => void perform(async () => {
          const result = await request(`${path}/login`, { method: "POST" });
          const base = typeof baseUrl === "function" ? await baseUrl() : baseUrl || global.location?.href;
          const url = takeoverUrl(result?.takeover_url, base);
          await openUrl(url);
          // A normal link remains usable when a browser blocks an async popup.
          const link = node("a", "custom-source-login-link", "打开登录接管页");
          link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
          runs.replaceChildren(link);
        }, "请在接管页完成登录并继续；返回后点击刷新来源。"));
        const history = button("最近运行", () => void perform(async () => {
          const result = await request(`${path}/runs`, { method: "GET" });
          const entries = array(result?.items);
          runs.replaceChildren();
          if (!entries.length) runs.append(node("p", "custom-source-note", "尚无运行记录。"));
          entries.slice(0, 10).forEach((run) => runs.append(node("p", "custom-source-note", [
            formatTime(run.started_at || run.created_at), statusLabel(run.status),
            `发现 ${Number(run.discovered_count) || 0} / 入队待评估 ${Number(run.enqueued_count) || 0}`,
            text(run.error || run.last_error),
          ].filter(Boolean).join(" · "))));
        }, "已读取最近运行。"));
        const archive = button("归档", () => {
          if (!global.confirm(`归档「${text(source.name)}」并停止后续发现？`)) return;
          void perform(async () => { await request(path, { method: "DELETE" }); await reloadAfterMutation("来源已归档。"); }, "已归档。");
        });
        actions.append(save, toggle);
        if (workflows.some((flow) => ["playwright", "ego"].includes(flow.executor))) actions.append(login);
        actions.append(history, archive);
        card.append(actions, local, runs);
        list.append(card);
      }
    }

    async function reloadAfterMutation(success) {
      const refreshed = await load();
      if (disposed) return;
      if (refreshed) announce(success);
      else announce(`${success}列表刷新失败，请重试刷新。${message.textContent}`, true);
    }

    async function load({ refreshSources = true } = {}) {
      if (disposed) return false;
      const current = ++revision;
      list.setAttribute("aria-busy", "true");
      refresh.disabled = true;
      refresh.textContent = "正在读取…";
      try {
        const result = await request("/custom-sources", { method: "GET", cache: "no-store" });
        if (disposed || current !== revision) return false;
        const wasInstalling = installing;
        renderExecutors(result?.executors);
        if (refreshSources) {
          items = array(result?.items);
          render();
          announce(items.length ? `共 ${items.length} 个网站；各自占比与内置来源平级。` : "");
          onChange(items);
        } else if (wasInstalling && !installing) {
          const browser = array(result?.executors).find((executor) => executor?.id === "playwright");
          if (browser?.available) announce("浏览器已安装完成，可以回到聊一聊试跑网站。");
          else announce(text(browser?.detail) || "浏览器安装未完成，请重试。", true);
        }
        return true;
      } catch (error) {
        if (disposed || current !== revision) return false;
        announce(errorMessage(error), true);
        stopInstallPoll();
        return false;
      } finally {
        if (!disposed && current === revision) {
          list.setAttribute("aria-busy", "false"); refresh.disabled = false;
          refresh.textContent = "刷新来源";
        }
      }
    }
    return { load, destroy() {
      disposed = true; revision += 1; stopInstallPoll();
      doc.removeEventListener?.("visibilitychange", onVisibilityChange);
    } };
  }

  global.OpenBiliClawCustomSources = { createManager, sourceName, statusLabel, shareEntries, sourcePatch, takeoverUrl, formatTime };
})(typeof globalThis !== "undefined" ? globalThis : window);
