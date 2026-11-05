/* Phone connection links shared by desktop Web and the packaged extension. */
(function (global) {
  "use strict";

  function hostKind(host) {
    const value = String(host || "").toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    if (!value || value === "localhost" || value.endsWith(".localhost")
        || value === "::" || value === "::1" || value === "0.0.0.0"
        || value.startsWith("127.") || value.startsWith("::ffff:")) return "unavailable";
    const parts = value.split(".").map(Number);
    const ipv4 = parts.length === 4 && parts.every((n) => Number.isInteger(n) && n >= 0 && n <= 255);
    if ((ipv4 && parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127)
        || value.startsWith("fd7a:115c:a1e0:") || value.endsWith(".ts.net")) return "tailscale";
    if ((ipv4 && parts[0] === 169 && parts[1] === 254) || /^fe[89ab][\da-f]:/.test(value)) return "unavailable";
    if ((ipv4 && (parts[0] === 10 || (parts[0] === 192 && parts[1] === 168)
        || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)))
        || /^f[cd][\da-f]{2}:/.test(value) || value.endsWith(".local")) return "lan";
    return "public";
  }

  function mobileUrl(input, { embedded = false } = {}) {
    try {
      const url = new URL(input);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password
          || hostKind(url.hostname) === "unavailable") return "";
      // Retain a reverse proxy's path prefix, but remove UI/API entry paths.
      const prefix = url.pathname.replace(/\/(?:m|web|api)(?:\/index\.html)?\/?$/, "").replace(/\/+$/, "");
      url.pathname = `${prefix}/m/`;
      url.search = embedded ? "?obc_connection=tailscale" : "";
      url.hash = "";
      return url.toString();
    } catch { return ""; }
  }

  function endpointUrl({ scheme = "http", host = "", port = 8420, basePath = "" } = {}) {
    const urlHost = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
    const prefix = String(basePath || "").replace(/^\/+|\/+$/g, "");
    return `${scheme === "https" ? "https" : "http"}://${urlHost}:${port}${prefix ? `/${prefix}` : ""}`;
  }

  function connectionChoices({ endpoint = {}, info = {}, publicUrl = "" } = {}) {
    const choices = [];
    const current = mobileUrl(endpointUrl(endpoint));
    const currentKind = current ? hostKind(new URL(current).hostname) : "unavailable";
    const bind = String(info.api_host || "0.0.0.0");
    const localOnly = hostKind(bind) === "unavailable" && !["0.0.0.0", "::", "[::]"].includes(bind);
    const canListen = (host) => !localOnly && (["0.0.0.0", "::", "[::]"].includes(bind) || bind === host);
    const lanHost = String(info.lan_ip || "");
    const port = Number(info.api_port) || Number(endpoint.port) || 8420;
    const lanUrl = currentKind === "lan" ? current
      : canListen(lanHost) && lanHost && !["unavailable", "tailscale"].includes(hostKind(lanHost))
        ? mobileUrl(endpointUrl({ host: lanHost, port })) : "";
    choices.push({
      id: "lan", label: "局域网", url: lanUrl,
      hint: lanUrl ? "手机与电脑连接同一局域网；用相机打开网页版，或用 App「扫码连接」填入后端。"
        : localOnly ? "后端仅监听本机。请将 [api].host 改为 0.0.0.0 并重启，再使用局域网扫码。"
          : "未检测到可用的局域网地址。请检查电脑网络，或选择 Tailscale / 公网地址。",
    });
    if (currentKind === "tailscale") choices.push({
      id: "tailscale-current", label: "Tailscale · 当前地址", url: current,
      hint: "手机系统 Tailscale 与后端加入同一 tailnet 后，可用浏览器或 App 直接扫码连接。",
    });
    for (const [index, address] of (Array.isArray(info.tailscale_addresses) ? info.tailscale_addresses : []).entries()) {
      if (!address || !["system", "embedded"].includes(address.source)) continue;
      const embedded = address.source === "embedded";
      if (hostKind(address.host) !== "tailscale" || (!embedded && !canListen(address.host))) continue;
      const url = mobileUrl(endpointUrl({ host: address.host, port: address.port || port }), { embedded });
      if (!url || choices.some((item) => item.url === url)) continue;
      choices.push({
        id: `tailscale-${address.source}-${index}`,
        label: `Tailscale · ${embedded ? "应用内节点" : "系统节点"}${index ? ` · ${address.host}` : ""}`,
        url,
        hint: embedded
          ? "App 扫码会选择内置 Tailscale，首次需登录同一 tailnet。浏览器访问需手机系统 Tailscale 已连接该网络。"
          : "两端系统 Tailscale 加入同一 tailnet 后即可扫码。App 使用直接连接，无需重复开启内置 Tailscale。",
      });
    }
    if (!choices.some((item) => item.id.startsWith("tailscale"))) choices.push({
      id: "tailscale", label: "Tailscale", url: "",
      hint: "尚未检测到 Tailscale 地址。连接电脑系统 Tailscale，或在通用设置中启用应用内 Tailnet 后重新打开。",
    });
    choices.push({
      id: "public", label: "公网 / 自定义地址",
      url: publicUrl.trim() ? mobileUrl(publicUrl.trim()) : currentKind === "public" ? current : "",
      hint: "使用已部署的公网访问地址（推荐 HTTPS）。二维码保留协议、端口和路径前缀，App 扫码即可填入。",
    });
    return { choices, preferred: currentKind === "public" ? "public"
      : currentKind === "tailscale" ? "tailscale-current"
        : choices.find((item) => item.url)?.id || "lan" };
  }

  function createPanel({ network, publicInput, canvas, url, hint, actions = [], qr, onUrlChange = () => {} }) {
    let data = {};
    let selection = "";
    function render() {
      const { choices, preferred } = connectionChoices({ ...data, publicUrl: publicInput.value || "" });
      selection = choices.some((item) => item.id === selection) ? selection : preferred;
      network.replaceChildren(...choices.map((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        return option;
      }));
      network.value = selection;
      const item = choices.find((item) => item.id === selection);
      const value = item?.url || "";
      url.textContent = value || "暂无可用连接地址";
      hint.hidden = false;
      hint.textContent = item?.hint || "";
      if (selection === "public" && publicInput.value.trim() && !value) hint.textContent = "请输入有效的 HTTP / HTTPS 地址；不能使用本机地址或包含用户名密码的链接。";
      hint.dataset.tone = value ? "info" : "warning";
      canvas.textContent = "";
      if (value) {
        try { canvas.innerHTML = qr.createQrSvgMarkup(value); }
        catch { canvas.textContent = "地址过长，无法生成二维码，请复制链接。"; }
      }
      for (const button of actions) if (button) button.disabled = !value;
      onUrlChange(value);
    }
    network.addEventListener("change", () => { selection = network.value; render(); });
    publicInput.addEventListener("input", () => { selection = "public"; render(); });
    return {
      loading() {
        network.disabled = true;
        publicInput.disabled = true;
        canvas.textContent = "";
        url.textContent = "正在获取连接地址…";
        hint.hidden = true;
        for (const button of actions) if (button) button.disabled = true;
        onUrlChange("");
      },
      update(next) {
        if (data.endpoint && endpointUrl(data.endpoint) !== endpointUrl(next.endpoint)) {
          publicInput.value = "";
          selection = "";
        }
        data = next;
        network.disabled = false;
        publicInput.disabled = false;
        render();
      },
    };
  }

  global.OBCMobileConnection = { hostKind, mobileUrl, endpointUrl, connectionChoices, createPanel };
})(globalThis);
