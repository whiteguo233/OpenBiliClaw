/** One free-model form for setup, desktop, extension and mobile. No credentials. */
(function (global) {
  "use strict";
  const mounted = new WeakMap();
  function mount(root, actions = {}) {
    if (mounted.has(root)) return mounted.get(root);
    root.classList.add("sponsored-settings");
    root.innerHTML = `
      <h3>免费模型 · 硅基流动</h3>
      <label class="sponsored-toggle"><input data-sponsored="enabled" type="checkbox">使用免费方案</label>
      <div data-sponsored="options">
        <label data-sponsored="model-label">对话模型</label><select data-sponsored="model">
          <option value="Qwen/Qwen3-8B">Qwen3-8B（默认 · 128K 上下文）</option>
          <option value="Qwen/Qwen3.5-4B">Qwen3.5-4B（256K 上下文）</option>
        </select>
        <p class="sponsored-hint">包含 BGE-M3 文本向量，无需填写 API Key。保存后新任务使用所选模型，已有画像和记录保留。</p>
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
    let ready = false;
    let supported = false;
    let busy = false;
    const collect = () => ({
      enabled: field("enabled").checked,
      model: field("model").value,
      fallback_to_other_free_model: field("free-fallback").checked,
      fallback_to_user_provider: field("paid-fallback").checked,
    });
    function sync() {
      field("options").hidden = !field("enabled").checked;
      field("prepare").hidden = ready;
      field("prepare").disabled = busy || !supported || !actions.prepare;
      field("probe").disabled = busy || !supported || !actions.probe || !ready;
      field("enabled").disabled = !supported;
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
        if (!result?.ok) throw new Error(result?.error || result?.message || "操作失败，请重试。");
        if (kind === "prepare") ready = true;
        field("status").textContent = [result.message || "组件已就绪", result.model, result.latency_ms ? `${(result.latency_ms / 1000).toFixed(1)} 秒` : ""].filter(Boolean).join(" · ");
      } catch (error) {
        field("status").textContent = error?.message || "操作失败，请重试。";
      } finally { busy = false; sync(); }
    }
    field("prepare").addEventListener("click", () => void run("prepare"));
    field("probe").addEventListener("click", () => void run("probe"));
    const api = {
      collect,
      load(value = {}) {
        supported = typeof value.enabled === "boolean";
        ready = value.runtime_available === true;
        field("enabled").checked = value.enabled === true;
        field("model").value = value.model === "Qwen/Qwen3.5-4B" ? value.model : "Qwen/Qwen3-8B";
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
  global.OpenBiliClawSponsoredSettings = { mount };
})(window);
