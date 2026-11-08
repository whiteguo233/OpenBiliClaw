import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const root = new URL("../../", import.meta.url);
const appSource = readFileSync(new URL("src/openbiliclaw/web/desktop/assets/js/app.js", root), "utf8");
const connectionSource = readFileSync(new URL("src/openbiliclaw/web/shared/mobile-connection.js", root), "utf8");
const qrSource = readFileSync(new URL("src/openbiliclaw/web/desktop/assets/js/mobile-qr.js", root), "utf8");

async function openDrawer({ lanIp = null, typedHost = "", typedPort = "", pageHost = "127.0.0.1", pagePath = "/web/", scheme = "http", port = 8420 } = {}) {
  const elements = new Map();
  const generated = [];
  const context = vm.createContext({
    URL, TextEncoder, console,
    window: { location: { protocol: `${scheme}:`, hostname: pageHost, pathname: pagePath, port: String(port) } },
    $: (selector) => {
      if (!elements.has(selector)) elements.set(selector, { textContent: "", innerHTML: "", value: "", dataset: {}, hidden: false, replaceChildren() {}, addEventListener() {} });
      return elements.get(selector);
    },
    markMobileQrSeen() {}, openPanel() {},
    ENDPOINTS: { qrInfo: "/api/qr-info" },
    requestJson: async () => ({ lan_ip: lanIp }),
    locationApiDefault: () => ({ host: pageHost, port }),
    storageGet: (key) => key.endsWith("backendHost") ? typedHost : typedPort,
    _cachedLanIp: "", mobileQrPanel: null, mobileQrRequest: 0,
    document: { createElement: () => ({}) },
  });
  vm.runInContext(qrSource, context);
  vm.runInContext(connectionSource, context);
  context.window.OBCMobileConnection = context.OBCMobileConnection;
  context.window.OBCMobileQr.createQrSvgMarkup = (url) => { generated.push(url); return "<svg/>"; };
  const body = appSource.split("async function openMobileQrDrawer() {", 2)[1].split('safeBind("#mobileQrBtn"', 1)[0];
  await vm.runInContext(`(async function () {${body.slice(0, body.lastIndexOf("}"))}})()`, context);
  return { generated, elements };
}

test("desktop QR replaces a saved loopback backend with the fresh LAN address", async () => {
  const { generated } = await openDrawer({ lanIp: "192.168.1.7", typedHost: "127.0.0.1" });
  assert.deepEqual(generated, ["http://192.168.1.7:8420/m/"]);
});

test("desktop QR does not offer a loopback QR when no phone address is available", async () => {
  const { generated } = await openDrawer();
  assert.deepEqual(generated, []);
});

const shared = vm.createContext({ URL });
vm.runInContext(connectionSource, shared);
const { mobileUrl, connectionChoices, hostKind } = shared.OBCMobileConnection;

test("links preserve HTTPS, IPv6, custom ports and reverse proxy prefixes", () => {
  assert.equal(mobileUrl("https://obc.example.com:9443/prefix/web/"), "https://obc.example.com:9443/prefix/m/");
  assert.equal(mobileUrl("http://[fd12:3456:789a::7]:8420"), "http://[fd12:3456:789a::7]:8420/m/");
  assert.equal(mobileUrl("https://obc.example.com/m/?token=secret#password"), "https://obc.example.com/m/");
});

test("loopback, wildcard, credentials and non-HTTP URLs never become phone links", () => {
  for (const input of ["http://127.0.0.1:8420", "http://127.2.3.4", "http://localhost", "http://localhost.", "http://[::1]", "http://0.0.0.0", "http://[::]", "http://0", "http://user:pass@example.com", "javascript:alert(1)"]) {
    assert.equal(mobileUrl(input), "", input);
  }
});

test("LAN, system Tailscale and embedded Tailnet are separate scan targets", () => {
  const info = { lan_ip: "192.168.1.7", api_port: 19090, tailscale_addresses: [
    { host: "100.100.1.2", port: 19090, source: "system" },
    { host: "100.100.1.3", port: 8421, source: "embedded" },
  ] };
  const { choices, preferred } = connectionChoices({ endpoint: { host: "127.0.0.1", port: 8420 }, info });
  assert.equal(preferred, "lan");
  assert.deepEqual(Array.from(choices.filter((item) => item.url), (item) => item.url), [
    "http://192.168.1.7:19090/m/", "http://100.100.1.2:19090/m/", "http://100.100.1.3:8421/m/?obc_connection=tailscale",
  ]);
});

test("a loopback listener suppresses LAN/system links but supports embedded Tailnet", () => {
  const { choices, preferred } = connectionChoices({ info: { lan_ip: "192.168.1.7", api_host: "127.0.0.1", tailscale_addresses: [
    { host: "100.100.1.2", port: 8420, source: "system" },
    { host: "100.100.1.3", port: 8420, source: "embedded" },
  ] } });
  assert.equal(choices[0].url, "");
  assert.equal(choices.filter((item) => item.url).length, 1);
  assert.equal(preferred, "tailscale-embedded-1");
});

test("a public endpoint remains preferred over detected LAN addresses", () => {
  const { choices, preferred } = connectionChoices({ endpoint: { scheme: "https", host: "obc.example.com", port: 443 }, info: { lan_ip: "192.168.1.7" } });
  assert.equal(preferred, "public");
  assert.equal(choices.find((item) => item.id === "public").url, "https://obc.example.com/m/");
  assert.equal(hostKind("100.63.1.2"), "public");
  assert.equal(hostKind("100.64.1.2"), "tailscale");
  assert.equal(hostKind("fd7a:115c:a1e0::1234"), "tailscale");
});

test("panel clears the previous URL when fresh discovery is empty", () => {
  const events = new Map();
  const field = () => ({ value: "", dataset: {}, replaceChildren() {}, addEventListener(type, callback) { events.set(this, { ...events.get(this), [type]: callback }); } });
  const document = { createElement: () => ({}) };
  const context = vm.createContext({ URL, document });
  vm.runInContext(connectionSource, context);
  const network = field(), publicInput = field(), canvas = field(), url = field(), hint = field();
  const copy = {};
  let currentUrl = "";
  const panel = context.OBCMobileConnection.createPanel({ network, publicInput, canvas, url, hint, actions: [copy], qr: { createQrSvgMarkup: () => "<svg/>" }, onUrlChange: (value) => { currentUrl = value; } });
  const endpoint = { host: "127.0.0.1", port: 8420 };
  panel.update({ endpoint, info: { lan_ip: "192.168.1.7" } });
  assert.equal(currentUrl, "http://192.168.1.7:8420/m/");
  panel.loading();
  assert.equal(currentUrl, "");
  assert.equal(copy.disabled, true);
  panel.update({ endpoint, info: { lan_ip: null } });
  assert.equal(currentUrl, "");
  assert.equal(copy.disabled, true);
  assert.equal(network.disabled, false);
});

test("malformed Tailscale entries do not suppress a valid LAN choice", () => {
  const { choices } = connectionChoices({ info: { lan_ip: "192.168.1.7", tailscale_addresses: [null, {}, "invalid"] } });
  assert.equal(choices[0].url, "http://192.168.1.7:8420/m/");
});

test("desktop QR retains the public page's reverse proxy prefix", async () => {
  const { generated } = await openDrawer({ pageHost: "obc.example.com", pagePath: "/prefix/web/", scheme: "https", port: 443, lanIp: "192.168.1.7" });
  assert.deepEqual(generated, ["https://obc.example.com/prefix/m/"]);
});

test("saved loopback settings cannot replace a public page's port", async () => {
  const { generated } = await openDrawer({ pageHost: "obc.example.com", pagePath: "/web/", scheme: "https", port: 443, typedHost: "127.0.0.1", typedPort: "8420" });
  assert.deepEqual(generated, ["https://obc.example.com/m/"]);
});

test("a saved non-default loopback port is retained for legacy LAN discovery", async () => {
  const { generated } = await openDrawer({ lanIp: "192.168.1.7", typedHost: "127.0.0.1", typedPort: "18420" });
  assert.deepEqual(generated, ["http://192.168.1.7:18420/m/"]);
});
