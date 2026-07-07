/**
 * OpenBiliClaw — extension login for password-protected backends.
 *
 * When the backend has auth.enabled=true and the extension is running on a
 * non-localhost device (cross-device / Docker), cookie-based auth won't
 * work because SameSite=Lax cookies are not sent on cross-origin requests.
 * This module provides a password → token login flow that stores the
 * session token in chrome.storage.local so that popup-api.js and
 * popup-stream.js can inject it into every request.
 */
const AUTH_TOKEN_KEY = "obc_auth_token";
const AUTH_PASSWORD_KEY = "obc_auth_password";

export function initExtLogin(els = {}, opts = {}) {
  const getBaseUrl = opts.getBaseUrl;
  const doFetch = opts.fetchImpl || ((...args) => fetch(...args));
  const setStatus = (msg, ok = null) => {
    if (els.status) {
      els.status.textContent = msg;
      els.status.style.color =
        ok === true ? "#30b980" : ok === false ? "#ef7a86" : "";
    }
  };

  async function getCachedToken() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get([AUTH_TOKEN_KEY], (items) => {
          resolve(
            typeof items?.[AUTH_TOKEN_KEY] === "string"
              ? items[AUTH_TOKEN_KEY]
              : null,
          );
        });
      } catch {
        resolve(null);
      }
    });
  }

  async function checkAuthStatus() {
    try {
      const base = await getBaseUrl();
      const cached = await getCachedToken();
      // Include cached token so backend can report authenticated=true
      const statusUrl = cached
        ? `${base}/auth/status?token=${encodeURIComponent(cached)}`
        : `${base}/auth/status`;
      const res = await doFetch(statusUrl);
      if (!res.ok) {
        setStatus("无法连接后端", false);
        return;
      }
      const data = await res.json();
      if (!data.enabled) {
        setStatus("后端未开启密码，无需登录", true);
        hideFields();
        return;
      }
      if (data.authenticated) {
        setStatus("已登录 ✓", true);
        hideFields();
        return;
      }
      setStatus(
        cached ? "已缓存登录态（如仍无法连接请重新登录）" : "需要登录",
        cached ? true : false,
      );
      showFields();
    } catch {
      setStatus("无法连接后端", false);
    }
  }

  function hideFields() {
    if (els.password) els.password.hidden = true;
    if (els.btn) els.btn.hidden = true;
  }

  function showFields() {
    if (els.password) els.password.hidden = false;
    if (els.btn) els.btn.hidden = false;
  }

  async function handleLogin() {
    const pw = els.password?.value?.trim();
    if (!pw) {
      setStatus("请输入密码", false);
      return;
    }
    setStatus("登录中…");
    try {
      const base = await getBaseUrl();
      const res = await doFetch(`${base}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pw }),
      });
      if (!res.ok) {
        setStatus(
          res.status === 403
            ? "扩展 ID 不在白名单（检查 allowed_extension_ids）"
            : "密码错误",
          false,
        );
        return;
      }
      const data = await res.json();
      if (!data.ok || !data.token) {
        setStatus("登录失败", false);
        return;
      }
      await new Promise((r) =>
        chrome.storage.local.set(
          { [AUTH_TOKEN_KEY]: data.token, [AUTH_PASSWORD_KEY]: pw },
          r,
        ),
      );
      setStatus("登录成功 ✓ 重新连接中…", true);
      setTimeout(() => chrome.runtime.reload(), 800);
    } catch (e) {
      setStatus(`登录失败: ${e.message}`, false);
    }
  }

  if (els.btn) els.btn.addEventListener("click", handleLogin);
  if (els.password)
    els.password.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handleLogin();
    });
  void checkAuthStatus();
  return { checkAuthStatus };
}
