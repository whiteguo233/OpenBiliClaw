/** Built-in provider fields and credential-free notices shared by all clients. */
(function (global) {
  "use strict";
  const noticeSignatures = new WeakMap();
  const noticeCallbacks = new WeakMap();
  const updateUrl = "https://github.com/whiteguo233/OpenBiliClaw/releases/latest";
  function renderNotice(root, value = {}, onSettings) {
    const notices = Array.isArray(value?.notices) ? value.notices : [];
    if (onSettings) noticeCallbacks.set(root, onSettings);
    const settingsCallback = noticeCallbacks.get(root);
    const signature = JSON.stringify([notices, Boolean(settingsCallback)]);
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
    if (settingsCallback) {
      const settings = document.createElement("button");
      settings.type = "button"; settings.textContent = "配置自己的 Key";
      settings.addEventListener("click", () => noticeCallbacks.get(root)?.(notices));
      actions.append(settings);
    }
    root.append(actions);
  }
  const freeModels = ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"];
  function syncInstance({ provider, model, key, base, status, prepare }) {
    const free = provider === "siliconflow_free";
    let picker = document.getElementById(`${model.id}FreeChoice`);
    if (!picker) {
      picker = document.createElement("select");
      picker.id = `${model.id}FreeChoice`;
      picker.setAttribute("aria-label", "模型");
      for (const [index, value] of freeModels.entries()) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = `${value.split("/").pop()} · ${index ? "256K" : "128K"} 上下文`;
        picker.append(option);
      }
      picker.addEventListener("change", () => {
        model.value = picker.value;
        model.dispatchEvent(new Event("change", { bubbles: true }));
      });
      model.after(picker);
    }
    picker.hidden = !free;
    model.hidden = free;
    for (const field of [key, base]) {
      if (!field) continue;
      field.closest(".settings-field, label").hidden = free;
      if (free) field.value = "";
    }
    if (free) {
      if (!freeModels.includes(model.value)) model.value = freeModels[0];
      picker.value = model.value;
      if (status) status.textContent = "内置免费实例，无需填写 Key；按调用链顺序使用。可新建第二个实例作为备用。";
    }
    let button = document.getElementById(`${model.id}Prepare`);
    if (!button && prepare) {
      button = document.createElement("button");
      button.id = `${model.id}Prepare`; button.type = "button";
      button.textContent = "准备免费组件";
      button.className = "pill-btn settings-secondary-btn";
      (status || model).after(button);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await prepare();
          if (!result?.ok) throw new Error(result?.error || "组件准备失败");
          if (status) status.textContent = "免费组件已就绪，可保存实例并测试连接。";
        } catch (error) { if (status) status.textContent = error.message || "组件准备失败"; }
        finally { button.disabled = false; }
      });
    }
    if (button) button.hidden = !free;
  }
  function syncEmbedding({ provider, model, key, base, dimension, multimodal, reset = false, prepare }) {
    if (reset && provider === "ollama" && model.value === "BAAI/bge-m3") {
      model.value = "bge-m3";
      if (base) base.value = "http://127.0.0.1:11434/v1";
    }
    const free = provider === "siliconflow_free";
    for (const field of [key, base]) {
      if (!field) continue;
      field.closest(".settings-field, label").hidden = free;
      if (free) field.value = "";
    }
    model.readOnly = free;
    if (dimension) dimension.readOnly = free;
    if (multimodal) { multimodal.disabled = free; if (free) multimodal.checked = false; }
    let prepareButton = document.getElementById(`${model.id}Prepare`);
    if (!prepareButton && prepare) {
      prepareButton = document.createElement("button");
      prepareButton.id = `${model.id}Prepare`; prepareButton.type = "button";
      prepareButton.textContent = "准备免费组件";
      prepareButton.className = "pill-btn settings-secondary-btn";
      model.closest(".settings-field, label").after(prepareButton);
      prepareButton.addEventListener("click", async () => {
        prepareButton.disabled = true;
        try {
          const result = await prepare();
          prepareButton.textContent = result?.ok ? "免费组件已就绪" : result?.error || "准备失败，点击重试";
        } catch (error) { prepareButton.textContent = error.message || "准备失败，点击重试"; }
        finally { prepareButton.disabled = false; }
      });
    }
    if (prepareButton) prepareButton.hidden = !free;
    if (free) {
      model.value = "BAAI/bge-m3";
      if (dimension) dimension.value = "1024";
    }
  }
  global.OpenBiliClawSponsoredSettings = {
    renderNotice, syncInstance, syncEmbedding,
    updateStatus(root, value) { if (root) renderNotice(root, value); },
  };
})(window);
