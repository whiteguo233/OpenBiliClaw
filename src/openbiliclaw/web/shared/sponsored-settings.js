/** One free-model form for setup, desktop, extension and mobile. No credentials. */
(function (global) {
  "use strict";
  const mounted = new WeakMap();
  const noticeSignatures = new WeakMap();
  const updateUrl = "https://github.com/whiteguo233/OpenBiliClaw/releases/latest";
  function renderNotice(root, value = {}, onSettings) {
    const notices = Array.isArray(value?.notices) ? value.notices : [];
    const signature = JSON.stringify(notices);
    if (noticeSignatures.get(root) === signature) return;
    noticeSignatures.set(root, signature);
    root.replaceChildren();
    root.hidden = notices.length === 0;
    root.classList.add("sponsored-notice");
    root.setAttribute("role", "status");
    root.setAttribute("aria-live", "polite");
    for (const notice of notices) {
      const heading = document.createElement("strong");
      heading.textContent = notice.title || "免费方案暂不可用";
      const description = document.createElement("p");
      description.textContent = notice.message || "请检查模型设置。";
      root.append(heading, description);
    }
    if (!notices.length) return;
    const actions = document.createElement("div");
    actions.className = "sponsored-actions";
    if (notices.some(item => item.requires_update)) {
      const update = document.createElement("a");
      update.href = updateUrl; update.target = "_blank"; update.rel = "noopener noreferrer";
      update.textContent = "查看最新版本";
      actions.append(update);
    }
    if (onSettings) {
      const settings = document.createElement("button");
      settings.type = "button"; settings.textContent = "配置自己的 Key";
      settings.addEventListener("click", onSettings);
      actions.append(settings);
    }
    root.append(actions);
  }
  function mount(root, actions = {}) {
    if (mounted.has(root)) return mounted.get(root);
    root.classList.add("sponsored-settings");
    root.innerHTML = `
      <h3>免费模型 · 硅基流动</h3>
      <div data-sponsored="notice" hidden></div>
      <label class="sponsored-toggle"><input data-sponsored="enabled" type="checkbox">使用免费方案</label>
      <div data-sponsored="options">
        <label data-sponsored="model-label">对话模型</label><select data-sponsored="model">
          <option value="Qwen/Qwen3-8B">Qwen3-8B（默认 · 128K 上下文）</option>
          <option value="Qwen/Qwen3.5-4B">Qwen3.5-4B（256K 上下文）</option>
        </select>
        <p class="sponsored-hint">无需填写 API Key。保存后新任务使用所选模型，已有画像和记录保留。</p>
        <label data-sponsored="embedding-label">文本向量（embedding）</label>
        <select data-sponsored="embedding-mode">
          <option value="cloud">硅基流动 BGE-M3（默认推荐 · 无需下载）</option>
          <option value="local">本地 Ollama（默认 bge-m3 · 不使用免费 Key）</option>
        </select>
        <p class="sponsored-hint" data-sponsored="embedding-hint"></p>
        <label class="sponsored-toggle"><input data-sponsored="free-fallback" type="checkbox">不可用时尝试另一个免费模型</label>
        <details><summary>更多选项</summary>
          <label class="sponsored-toggle"><input data-sponsored="paid-fallback" type="checkbox">两个免费模型都不可用时，使用已配置的自费调用链</label>
          <p class="sponsored-hint">开启后可能产生自费服务费用。图片理解可在相关功能中另行开启；免费方案不提供图片向量。</p>
        </details>
        <div class="sponsored-actions">
          <button data-sponsored="prepare" type="button">准备免费组件</button>
          <button data-sponsored="probe" type="button">测试所选模型与向量</button>
        </div>
      </div>
      <p class="sponsored-status" data-sponsored="status" role="status" aria-live="polite"></p>`;
    const field = name => root.querySelector(`[data-sponsored="${name}"]`);
    for (const input of root.querySelectorAll("input, select")) input.id = `${root.id}-${input.dataset.sponsored}`;
    field("model-label").htmlFor = field("model").id;
    field("embedding-label").htmlFor = field("embedding-mode").id;
    let ready = false;
    let supported = false;
    let busy = false;
    const collect = () => ({
      enabled: field("enabled").checked,
      model: field("model").value,
      embedding_mode: field("embedding-mode").value,
      fallback_to_other_free_model: field("free-fallback").checked,
      fallback_to_user_provider: field("paid-fallback").checked,
    });
    function sync() {
      field("options").hidden = !field("enabled").checked;
      field("prepare").hidden = ready;
      field("prepare").disabled = busy || !supported || !actions.prepare;
      field("probe").disabled = busy || !supported || !actions.probe || !ready;
      field("enabled").disabled = !supported;
      field("embedding-hint").textContent = field("embedding-mode").value === "local"
        ? "使用后端所在电脑的 Ollama；保留已有本地模型和地址，首次可在初始化页准备模型。占用本机资源，不受免费凭据轮换影响。切换后向量索引会逐步重建。"
        : "云端文本向量无需本地模型，使用内置免费凭据；凭据停用时需更新版本或改用本地向量。切换后向量索引会逐步重建。";
    }
    root.addEventListener("change", () => {
      sync();
      field("status").textContent = "选择已修改，保存后生效。";
    });
    async function run(kind) {
      busy = true; sync();
      field("status").textContent = kind === "prepare" ? "正在准备免费模型组件…" : "正在测试所选模型和文本向量，请稍候…";
      try {
        const result = await actions[kind](collect());
        if (result?.service_status) renderNotice(field("notice"), result.service_status, useOwn);
        if (!result?.ok) throw new Error(result?.error || result?.message || "操作失败，请重试。");
        if (kind === "prepare") ready = true;
        field("status").textContent = [result.message || "组件已就绪", result.model, result.latency_ms ? `${(result.latency_ms / 1000).toFixed(1)} 秒` : ""].filter(Boolean).join(" · ");
      } catch (error) {
        field("status").textContent = error?.message || "操作失败，请重试。";
      } finally { busy = false; sync(); }
    }
    field("prepare").addEventListener("click", () => void run("prepare"));
    field("probe").addEventListener("click", () => void run("probe"));
    function useOwn() {
      field("enabled").checked = false;
      field("enabled").dispatchEvent(new Event("change", { bubbles: true }));
      actions.useOwn?.();
      field("status").textContent = "请填写自己的模型服务和 API Key，再保存配置；不会自动调用自费服务。";
    }
    const api = {
      collect,
      updateStatus(value) { renderNotice(field("notice"), value, useOwn); },
      load(value = {}) {
        supported = typeof value.enabled === "boolean";
        ready = value.runtime_available === true;
        field("enabled").checked = value.enabled === true;
        field("model").value = value.model === "Qwen/Qwen3.5-4B" ? value.model : "Qwen/Qwen3-8B";
        field("embedding-mode").value = value.embedding_mode === "local" ? "local" : "cloud";
        renderNotice(field("notice"), value.service_status, useOwn);
        field("free-fallback").checked = value.fallback_to_other_free_model !== false;
        field("paid-fallback").checked = value.fallback_to_user_provider === true;
        field("status").textContent = !supported ? "请更新后端以使用免费模型。" : ready ? "免费模型组件已就绪，可测试连接。" : "首次使用请准备免费模型组件。";
        sync();
      },
      get ready() { return ready; },
    };
    mounted.set(root, api);
    api.load();
    return api;
  }
  global.OpenBiliClawSponsoredSettings = {
    mount, renderNotice,
    updateStatus(root, value) { mounted.get(root)?.updateStatus(value); },
  };
})(window);
