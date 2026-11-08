import {
  pairDeviceKey,
  popupAuthenticatedFetch,
} from "./popup-device-auth.js";

const AUTH_ERRORS = {
  auth_disabled: "请先在后端电脑上启用访问密码，再配置扩展设备密钥",
  invalid_device_key: "设备访问密钥无效或已撤销",
  extension_access_disabled: "后端尚未开启扩展设备访问",
  locked: "尝试次数过多，请稍后重试",
  missing_device_key: "请输入设备访问密钥",
  backend_unreachable: "无法连接后端",
};

export function initExtLogin(els = {}, opts = {}) {
  const getBaseUrl = opts.getBaseUrl;
  const doFetch = opts.fetchImpl || ((...args) => fetch(...args));
  const setStatus = (msg, ok = null) => {
    if (!els.status) return;
    els.status.textContent = msg;
    els.status.style.color = ok === true ? "#30b980" : ok === false ? "#ef7a86" : "";
  };
  const showFields = (visible) => {
    if (els.deviceKey) els.deviceKey.hidden = !visible;
    if (els.btn) els.btn.hidden = !visible;
  };

  async function checkAuthStatus() {
    try {
      const base = await getBaseUrl();
      const response = await popupAuthenticatedFetch(`${base}/auth/status`, {}, doFetch);
      if (!response.ok) throw new Error("unreachable");
      const data = await response.json();
      if (data.setup_required) {
        setStatus(AUTH_ERRORS.auth_disabled, false);
        showFields(false);
      } else if (!data.enabled) {
        setStatus("本机访问无需配对", true);
        showFields(false);
      } else if (data.authenticated) {
        setStatus("设备已配对", true);
        showFields(false);
      } else {
        setStatus("需要设备访问密钥", false);
        showFields(true);
      }
    } catch {
      setStatus("无法连接后端", false);
      showFields(true);
    }
  }

  async function handleLogin() {
    const key = els.deviceKey?.value?.trim();
    if (!key) {
      setStatus(AUTH_ERRORS.missing_device_key, false);
      return;
    }
    setStatus("配对中…");
    try {
      await pairDeviceKey(key, { getBaseUrl, fetchImpl: doFetch });
      if (els.deviceKey) els.deviceKey.value = "";
      setStatus("配对成功，正在重新连接…", true);
      showFields(false);
      opts.onPaired?.();
    } catch (error) {
      setStatus(AUTH_ERRORS[error?.message] || "设备配对失败", false);
    }
  }

  if (els.btn) els.btn.addEventListener("click", handleLogin);
  if (els.deviceKey) {
    els.deviceKey.addEventListener("keydown", (event) => {
      if (event.key === "Enter") void handleLogin();
    });
  }
  void checkAuthStatus();
  return { checkAuthStatus, handleLogin };
}
