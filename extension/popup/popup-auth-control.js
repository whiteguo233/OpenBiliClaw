/**
 * OpenBiliClaw popup — LAN password-gate control.
 *
 * Lets the user enable/disable the password gate and set/change the password
 * straight from the extension popup. The extension talks to 127.0.0.1, so it is
 * a trusted-local client (`/api/auth/admin` is local-only): the only surface
 * that can manage the gate without risk of locking itself out.
 *
 * Pure logic + a DOM-agnostic wiring helper so it can be unit-tested with a
 * fake fetch and duck-typed elements (no jsdom).
 */

export function createAuthApi({ getBaseUrl, fetchImpl } = {}) {
  const doFetch = fetchImpl || ((...args) => fetch(...args));

  async function status() {
    try {
      const base = await getBaseUrl();
      const res = await doFetch(`${base}/auth/status`);
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  async function setEnabled(enabled, password = "") {
    const base = await getBaseUrl();
    const payload = enabled ? { enabled: true, password } : { enabled: false };
    const res = await doFetch(`${base}/auth/admin`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-OBC-Auth": "1" },
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    return { ok: res.ok && Boolean(data && data.ok), status: res.status, data };
  }

  return { status, setEnabled };
}

/**
 * Wire popup DOM controls to the auth API.
 *
 * @param els {checkbox, password, saveBtn, hint} — duck-typed elements.
 * @param opts {getBaseUrl, fetchImpl}
 */
export function initAuthControl(els = {}, opts = {}) {
  const api = createAuthApi(opts);
  let current = null;

  const setHint = (msg) => {
    if (els.hint) els.hint.textContent = msg;
  };

  // Show/hide the password field + Save button to match the checkbox, WITHOUT
  // touching the checkbox itself (so the user's mid-toggle intent is preserved).
  function syncEditing() {
    const can = Boolean(current && current.can_manage);
    const enabling = Boolean(els.checkbox && els.checkbox.checked);
    if (els.password) els.password.hidden = !(can && enabling);
    if (els.saveBtn) els.saveBtn.hidden = !(can && enabling);
  }

  // Reflect the authoritative server state into all controls + hint.
  function applyServerState() {
    const can = Boolean(current && current.can_manage);
    if (els.checkbox) {
      els.checkbox.checked = Boolean(current && current.enabled);
      els.checkbox.disabled = !can;
    }
    syncEditing();
    if (!current) {
      setHint("无法读取后端鉴权状态。");
    } else if (!can) {
      setHint(
        current.env_managed
          ? "由环境变量管理，请改环境变量并重启后端。"
          : "仅本机 / 浏览器插件可修改此设置。",
      );
    } else if (current.enabled) {
      setHint("已开启：局域网 / 远程设备访问需要登录密码（本机与插件免登录）。");
    } else {
      setHint("已关闭：仅允许本机访问，远程设备需先启用密码。");
    }
  }

  async function load() {
    current = await api.status();
    applyServerState();
    return current;
  }

  async function apply(enabled) {
    const password = els.password ? String(els.password.value || "") : "";
    if (enabled && !password.trim()) {
      setHint("请输入要设置的访问密码。");
      if (els.password && typeof els.password.focus === "function") els.password.focus();
      return;
    }
    setHint("保存中…");
    let result;
    try {
      result = await api.setEnabled(enabled, password);
    } catch {
      setHint("无法连接后端，请稍后重试。");
      return;
    }
    if (result.ok) {
      if (els.password) els.password.value = "";
      await load();
      return;
    }
    if (result.status === 403) setHint("仅本机 / 插件可修改此设置。");
    else if (result.status === 409) setHint("由环境变量管理，无法在此修改。");
    else if (result.status === 400) setHint("开启密码门禁需要先设置密码。");
    else setHint("保存失败，请重试。");
    // reflect the true server state again
    await load();
  }

  if (els.checkbox && typeof els.checkbox.addEventListener === "function") {
    els.checkbox.addEventListener("change", () => {
      if (!els.checkbox.checked) {
        // unchecking disables immediately
        void apply(false);
      } else {
        // checking reveals the password field; the user confirms via Save
        syncEditing();
        if (els.password && typeof els.password.focus === "function") els.password.focus();
      }
    });
  }
  if (els.saveBtn && typeof els.saveBtn.addEventListener === "function") {
    els.saveBtn.addEventListener("click", () => void apply(true));
  }

  void load();
  return { reload: load };
}
