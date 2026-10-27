/** Shared native Jev form. Tokens never enter browser storage or read responses. */
(function (global) {
  "use strict";
  const mounted = new WeakMap();
  const DEFAULT_URL = "https://api.typesafe.ai/v1/systemone";
  function mount(root, evaluator) {
    if (mounted.has(root)) return mounted.get(root);
    root.classList.add("jev-settings");
    root.innerHTML = `
      <h4>Jev 连接配置</h4>
      <label class="jev-toggle"><input data-jev="enabled" type="checkbox">启用 Jev 服务连接</label>
      <div class="jev-grid">
        <label class="jev-wide">API URL（完整接口地址）<input data-jev="endpoint_url" type="url" spellcheck="false" autocomplete="off"></label>
        <label>固定模型 ID<input data-jev="model" type="text" spellcheck="false" autocomplete="off"></label>
        <label>Token<input data-jev="api_key" type="password" autocomplete="new-password" spellcheck="false" placeholder="留空保留已保存的 Token"></label>
      </div>
      <label class="jev-toggle"><input data-jev="clear_api_key" type="checkbox">清除本机保存的 Token</label>
      <p class="jev-hint" data-jev="credential-status"></p>
      <p class="jev-hint">URL 必须使用 Jev 原生协议，不是 Chat Completions；更换 URL 须重新填写 Token。Token 仅保存在后端本机 config.toml，页面不回显、不存浏览器。服务会收到画像和候选内容，请确认目标可信。</p>
      <details><summary>上下文与请求预算</summary>
        <label class="jev-toggle"><input data-jev="custom_capacity" type="checkbox">手动设置所选模型的上下文上限</label>
        <p class="jev-hint">已知模型使用登记容量；其它模型需填写服务商确认的双 token 上限，不自动换模型。运行时采用保守字节护栏，并非精确 tokenizer。</p>
        <div class="jev-grid">
          <label>总请求 token 上限<input data-jev="max_request_tokens" type="number" min="256" max="100000000" step="1"></label>
          <label>state＋单题 token 上限<input data-jev="max_state_question_tokens" type="number" min="256" max="100000000" step="1"></label>
          <label>每请求最多候选数<input data-jev="max_candidates_per_request" type="number" min="1" max="64" step="1"></label>
          <label>请求超时（秒）<input data-jev="timeout_seconds" type="number" min="0.1" step="0.1"></label>
          <label>传输最大尝试次数<input data-jev="max_attempts" type="number" min="1" step="1"></label>
        </div>
        <p class="jev-hint">每条候选的完整 10 题不会拆开；超限先减少合批候选，再本地摘录。传输重试仍可能计费，不是额外摘要或分段评估。</p>
      </details>
      <p class="jev-hint" data-jev="edit-status" role="status" aria-live="polite"></p>`;
    const field = name => root.querySelector(`[data-jev="${name}"]`);
    for (const input of root.querySelectorAll("input")) input.id = `${root.id}-${input.dataset.jev}`;
    let provider = {};
    let writable = false;
    let initial = "";
    let initialCandidates = 4;
    const modelLimits = model => Object.prototype.hasOwnProperty.call(provider.context_limits || {}, model)
      ? provider.context_limits[model] : undefined;
    const values = () => ({
      enabled: field("enabled").checked,
      endpoint_url: field("endpoint_url").value.trim(),
      model: field("model").value.trim(),
      timeout_seconds: Number(field("timeout_seconds").value),
      max_attempts: Number(field("max_attempts").value),
      custom_capacity: field("custom_capacity").checked,
      max_request_tokens: Number(field("max_request_tokens").value),
      max_state_question_tokens: Number(field("max_state_question_tokens").value),
    });
    function sync() {
      const custom = field("custom_capacity").checked;
      field("max_request_tokens").disabled = !custom;
      field("max_state_question_tokens").disabled = !custom;
      field("api_key").disabled = field("clear_api_key").checked;
      const sameURL = field("endpoint_url").value.trim() === (provider.endpoint_url || DEFAULT_URL);
      const hasToken = Boolean(field("api_key").value) || (sameURL && (
        provider.credential_source === "environment" || (!field("clear_api_key").checked && (provider.api_key_configured || provider.ready))
      ));
      const option = evaluator?.querySelector('option[value="jev"]');
      if (option) option.disabled = !(field("enabled").checked && hasToken && field("model").value.trim());
      if (!writable) for (const input of root.querySelectorAll("input")) input.disabled = true;
    }
    root.addEventListener("input", event => {
      if (event.target === field("model")) {
        // Never carry another model's manual capacity to a newly typed ID.
        const limits = modelLimits(field("model").value.trim());
        field("custom_capacity").checked = Boolean(limits);
        field("max_request_tokens").value = limits?.max_request_tokens ?? "";
        field("max_state_question_tokens").value = limits?.max_state_question_tokens ?? "";
      }
      sync();
      field("edit-status").textContent = "连接信息已修改，保存后生效；配置就绪不等于已联网验证。";
    });
    root.addEventListener("change", () => {
      if (field("clear_api_key").checked) field("api_key").value = "";
      sync();
      field("edit-status").textContent = "配置已修改，请保存。启用连接不会自动切换候选评估器。";
    });
    const api = {
      load(nextProvider = {}, evaluation = {}) {
        provider = nextProvider;
        // Older backends silently ignore unknown write sections. Do not offer
        // secret editing until they advertise the new safe credential status.
        writable = typeof provider.api_key_configured === "boolean";
        for (const input of root.querySelectorAll("input")) input.disabled = false;
        field("enabled").checked = provider.enabled === true;
        for (const [key, fallback] of Object.entries({
          endpoint_url: DEFAULT_URL, model: "jev-1.13.0", timeout_seconds: 2, max_attempts: 2,
        })) field(key).value = provider[key] ?? fallback;
        field("api_key").value = "";
        field("clear_api_key").checked = false;
        const limits = modelLimits(provider.model);
        field("custom_capacity").checked = Boolean(limits);
        field("max_request_tokens").value = limits?.max_request_tokens ?? provider.context_budget?.max_request_tokens ?? "";
        field("max_state_question_tokens").value = limits?.max_state_question_tokens ?? provider.context_budget?.max_state_question_tokens ?? "";
        initialCandidates = evaluation.max_candidates_per_request ?? 4;
        field("max_candidates_per_request").value = initialCandidates;
        field("credential-status").textContent = provider.api_key_configured
          ? "本机已保存 Token。留空保留，重新填写替换；勾选清除后保存才会删除。"
          : provider.credential_source === "environment"
            ? `使用后端环境变量 ${provider.api_key_env || "TYPESAFE_API_KEY"}。页面填写的 Token 优先；清除本机 Token 不会删除环境变量。`
            : "尚未保存 Token；也可沿用后端已配置的环境变量。";
        field("edit-status").textContent = "";
        if (!writable) field("credential-status").textContent = "当前后端仅支持模式切换；请升级后端后再在页面配置 Jev 连接。";
        initial = JSON.stringify(values());
        sync();
      },
      collect() {
        if (!writable) return {};
        const result = {};
        const current = values();
        const token = field("api_key").value;
        const clear = field("clear_api_key").checked;
        if (JSON.stringify(current) !== initial || token || clear) {
          const {custom_capacity, max_request_tokens, max_state_question_tokens, ...settings} = current;
          const limits = Object.assign(Object.create(null), provider.context_limits || {});
          if (custom_capacity) limits[current.model] = {max_request_tokens, max_state_question_tokens};
          else delete limits[current.model];
          settings.context_limits = limits;
          if (token) settings.api_key = token;
          if (clear) settings.clear_api_key = true;
          result.decision = {providers: {jev: settings}};
        }
        const count = Number(field("max_candidates_per_request").value);
        if (count !== initialCandidates) result.discovery = {jev_evaluation: {max_candidates_per_request: count}};
        return result;
      },
    };
    mounted.set(root, api);
    return api;
  }
  global.OpenBiliClawJevSettings = {mount};
})(typeof window !== "undefined" ? window : globalThis);
