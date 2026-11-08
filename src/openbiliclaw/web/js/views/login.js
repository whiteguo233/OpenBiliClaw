/**
 * Login view for the mobile web password gate.
 *
 * Rendered before any tab content when the backend reports
 * `authenticated: false` (including pending remote setup). On login the server sets the
 * HttpOnly session cookie; the SPA never holds the token itself.
 */

import { login } from "../api.js";

export function renderLoginView($app, { onSuccess, setupRequired = false } = {}) {
  $app.innerHTML = "";

  const wrap = document.createElement("section");
  wrap.className = "login-view";

  const card = document.createElement("form");
  card.className = "login-card";
  card.setAttribute("autocomplete", "off");

  const title = document.createElement("h1");
  title.className = "login-title";
  title.textContent = "OpenBiliClaw";

  const subtitle = document.createElement("p");
  subtitle.className = "login-subtitle";
  subtitle.textContent = "请输入访问密码";
  if (setupRequired) {
    subtitle.textContent = "请先在运行后端的电脑上，打开设置并启用局域网访问密码，然后返回此页面。";
    const retry = document.createElement("button");
    retry.className = "login-button";
    retry.type = "button";
    retry.textContent = "已设置，重新检查";
    retry.addEventListener("click", () => onSuccess?.());
    card.append(title, subtitle, retry);
    wrap.append(card);
    $app.append(wrap);
    return;
  }

  const input = document.createElement("input");
  input.className = "login-input";
  input.type = "password";
  input.name = "obc-password";
  input.placeholder = "密码";
  input.autocomplete = "current-password";
  input.setAttribute("aria-label", "访问密码");

  const button = document.createElement("button");
  button.className = "login-button";
  button.type = "submit";
  button.textContent = "登录";

  const error = document.createElement("p");
  error.className = "login-error";
  error.setAttribute("role", "alert");
  error.hidden = true;

  card.append(title, subtitle, input, button, error);
  wrap.append(card);
  $app.append(wrap);
  input.focus();

  let busy = false;

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const password = input.value;
    if (!password) {
      showError("请输入密码");
      return;
    }
    busy = true;
    button.disabled = true;
    button.textContent = "登录中…";
    error.hidden = true;
    try {
      const result = await login(password);
      if (result.ok) {
        if (typeof onSuccess === "function") onSuccess();
        return;
      }
      if (result.status === 429) {
        showError("尝试过于频繁，请稍后再试");
      } else {
        showError("密码错误");
      }
    } catch {
      showError("无法连接后端，请稍后重试");
    } finally {
      busy = false;
      button.disabled = false;
      button.textContent = "登录";
    }
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
    input.select();
  }

  card.addEventListener("submit", submit);
}
