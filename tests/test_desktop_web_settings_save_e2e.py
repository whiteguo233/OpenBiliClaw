"""桌面 Web 配置保存与后台应用状态的真实浏览器回归。"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]


def _initial_config() -> dict[str, Any]:
    return {
        "language": "zh",
        "llm": {
            "default_provider": "ollama",
            "default_chain": ["ollama"],
            "instances": {
                "ollama": {
                    "provider": "ollama",
                    "enabled": True,
                    "model": "qwen3:8b",
                }
            },
            "ollama": {"model": "qwen3:8b"},
            "embedding": {"provider": "ollama", "model": "bge-m3"},
        },
        "sources": {"bilibili": {"enabled": True}},
        "discovery": {"eval_scorer": "llm"},
        "scheduler": {
            "pool_source_shares": {
                "bilibili": 5,
                "xiaohongshu": 1,
                "douyin": 1,
                "youtube": 1,
                "twitter": 1,
                "zhihu": 1,
                "reddit": 1,
                "bangumi": 1,
            }
        },
    }


class SettingsSaveStub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.revision = 6
        self.apply_state = "idle"
        self.applied_revision = 6
        self.complete_before_response = False
        self.config = _initial_config()
        self.saved_payloads: list[dict[str, Any]] = []


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: Any,
    status: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


@pytest.fixture()
def settings_save_server() -> Iterator[tuple[str, SettingsSaveStub]]:
    state = SettingsSaveStub()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html",
                    "text/html",
                )
            if path.startswith("/web/assets/"):
                relative = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / relative)
            if path.startswith("/shared/"):
                relative = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / relative)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/config":
                with state.lock:
                    config = json.loads(json.dumps(state.config))
                return _json_response(self, {"config": config})
            if path == "/api/config/apply-status":
                with state.lock:
                    snapshot = {
                        "state": state.apply_state,
                        "requested_revision": state.revision,
                        "applied_revision": state.applied_revision,
                        "message": "配置已保存，正在后台应用。",
                        "error": "",
                        "updated_at": "2026-08-06T12:00:00+08:00",
                    }
                return _json_response(self, snapshot)
            if path == "/api/runtime-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": 0,
                        "pool_size": 0,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 1.0},
                        "configured_sources": {"bilibili": {"enabled": True}},
                        "unread_count": 0,
                    },
                )
            if path == "/api/init-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "running": False,
                        "can_start": False,
                        "reason": "already_initialized",
                        "stages": [],
                        "prerequisites": {
                            "bilibili_logged_in": True,
                            "llm_ready": True,
                            "embedding_ready": True,
                            "enabled_platforms": ["bilibili"],
                        },
                    },
                )
            if path == "/api/recommendations":
                return _json_response(self, {"items": []})
            if path == "/api/recommendations/platform-availability":
                return _json_response(self, {"total_available": 0, "by_platform": {}})
            if path == "/api/profile-summary":
                return _json_response(self, {"initialized": True})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
            if path in {"/api/delight/pending-batch", "/api/notifications/pending"}:
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            if path == "/api/chat/pending-confirmations":
                return _json_response(
                    self,
                    {
                        "count": 3,
                        "items": [
                            {
                                "kind": "confusion",
                                "title": f"待聊确认 {index}",
                                "ref": f"pending-{index}",
                                "confidence": 0.8,
                            }
                            for index in range(1, 4)
                        ],
                    },
                )
            if path == "/api/qr-info":
                return _json_response(self, {"lan_ip": "127.0.0.1"})
            return _json_response(self, {}, 404)

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path != "/api/config":
                return _json_response(self, {}, 404)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            with state.lock:
                state.revision += 1
                state.apply_state = "applied" if state.complete_before_response else "applying"
                state.config = payload
                state.saved_payloads.append(payload)
                revision = state.revision
                if state.complete_before_response:
                    state.applied_revision = revision
            return _json_response(
                self,
                {
                    "ok": True,
                    "config": payload,
                    "message": "配置已保存，正在后台应用。",
                    "reloaded": False,
                    "rollback_applied": False,
                    "restart_required": False,
                    "apply_state": "queued",
                    "apply_revision": revision,
                },
                202,
            )

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


_FAKE_WEBSOCKET = """
window.__obcSockets = [];
window.WebSocket = class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this._listeners = {};
    window.__obcSockets.push(this);
    setTimeout(() => this._emit('open', { type: 'open' }), 0);
  }
  addEventListener(type, handler) {
    (this._listeners[type] = this._listeners[type] || []).push(handler);
  }
  removeEventListener(type, handler) {
    const listeners = this._listeners[type] || [];
    this._listeners[type] = listeners.filter((item) => item !== handler);
  }
  _emit(type, event) {
    for (const handler of (this._listeners[type] || []).slice()) handler(event);
    const inline = this['on' + type];
    if (typeof inline === 'function') inline(event);
  }
  send() {}
  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this._emit('close', { type: 'close', code: 1000, reason: '', wasClean: true });
  }
};
window.__obcPushRuntime = (payload) => {
  const socket = window.__obcSockets.at(-1);
  if (!socket) throw new Error('no live runtime socket');
  socket._emit('message', { data: JSON.stringify(payload) });
};
"""


@pytest.fixture()
def chromium_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception:  # pragma: no cover - 取决于本机浏览器安装
            browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(_FAKE_WEBSOCKET)
        yield page
        browser.close()


def test_settings_save_unlocks_before_runtime_apply_finishes(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="平台源").click()
    share = page.get_by_label("Bilibili 候选池占比")
    save = page.get_by_role("button", name="保存配置")
    status = page.locator("#settingsSaveMsg")
    bar = page.locator("#settingsSaveBar")

    share.fill("2")
    save.click()

    expect(status).to_have_text("配置已保存，正在后台应用…")
    expect(save).to_have_text("保存配置")
    expect(bar).to_have_attribute("data-save-state", "applying")
    assert len(stub.saved_payloads) == 1
    assert stub.saved_payloads[0]["scheduler"]["pool_source_shares"]["bilibili"] == 2

    page.evaluate("window.__obcPushRuntime({type: 'config_reloaded', revision: 7})")
    expect(status).to_have_text("配置已应用")
    expect(bar).to_have_attribute("data-save-state", "applied")

    share.fill("3")
    save.click()
    expect(status).to_have_text("配置已保存，正在后台应用…")
    expect(bar).to_have_attribute("data-save-state", "applying")

    page.evaluate("window.__obcPushRuntime({type: 'config_reloaded', revision: 7})")
    expect(status).to_have_text("配置已保存，正在后台应用…")
    expect(bar).to_have_attribute("data-save-state", "applying")

    share.fill("4")
    page.evaluate("window.__obcPushRuntime({type: 'config_reloaded', revision: 8})")
    expect(status).to_have_text("已修改 1 项，未保存")
    expect(bar).to_have_attribute("data-save-state", "dirty")


def test_advanced_evaluator_mode_defaults_to_agent_and_round_trips(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="高级功能").click()
    mode = page.locator("#evalScorer")
    expect(mode).to_have_value("llm")
    expect(mode.locator("option:checked")).to_have_text("Agent（默认）")

    mode.select_option("shadow")
    page.get_by_role("button", name="保存配置").click()

    expect(mode).to_have_value("shadow")
    assert len(stub.saved_payloads) == 1
    assert stub.saved_payloads[0]["discovery"]["eval_scorer"] == "shadow"


def test_external_runtime_config_event_rehydrates_settings(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="平台源").click()
    share = page.get_by_label("Bilibili 候选池占比")
    expect(share).to_have_value("5")

    with stub.lock:
        stub.revision = 7
        stub.applied_revision = 7
        stub.apply_state = "applied"
        stub.config["scheduler"]["pool_source_shares"]["bilibili"] = 7
    page.evaluate("window.__obcPushRuntime({type: 'config_reloaded', revision: 7})")

    expect(share).to_have_value("7", timeout=3000)
    expect(page.locator("#settingsSaveMsg")).to_have_text("配置已应用")


def test_settings_save_recovers_terminal_status_that_wins_the_response_race(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    stub.complete_before_response = True
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="平台源").click()
    page.get_by_label("Bilibili 候选池占比").fill("2")
    page.get_by_role("button", name="保存配置").click()

    expect(page.locator("#settingsSaveMsg")).to_have_text("配置已应用")
    expect(page.locator("#settingsSaveBar")).to_have_attribute("data-save-state", "applied")


def test_settings_failure_rehydrates_rollback_without_overwriting_new_drafts(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="平台源").click()
    share = page.get_by_label("Bilibili 候选池占比")
    share.fill("2")
    page.get_by_role("button", name="保存配置").click()
    expect(page.locator("#settingsSaveMsg")).to_have_text("配置已保存，正在后台应用…")

    with stub.lock:
        stub.apply_state = "failed"
        stub.config["scheduler"]["pool_source_shares"]["bilibili"] = 5
    page.evaluate("window.__obcPushRuntime({type: 'config_reload_failed', revision: 7})")

    expect(page.locator("#settingsSaveMsg")).to_have_text("配置应用失败，已恢复上一次生效配置")
    expect(share).to_have_value("5")

    share.fill("4")
    page.evaluate("window.__obcPushRuntime({type: 'config_reload_failed', revision: 7})")
    expect(page.locator("#settingsSaveMsg")).to_have_text("已修改 1 项，未保存")
    expect(share).to_have_value("4")


def test_failed_apply_refreshes_canonical_snapshot_behind_new_draft(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="平台源").click()
    share = page.get_by_label("Bilibili 候选池占比")
    share.fill("2")
    page.get_by_role("button", name="保存配置").click()
    expect(page.locator("#settingsSaveMsg")).to_have_text("配置已保存，正在后台应用…")

    share.fill("4")
    with stub.lock:
        stub.revision = 8
        stub.apply_state = "failed"
        stub.config["scheduler"]["pool_source_shares"]["bilibili"] = 5
    page.evaluate("window.__obcPushRuntime({type: 'config_reload_failed', revision: 8})")

    expect(share).to_have_value("4")
    page.get_by_role("button", name="放弃修改").click()
    expect(share).to_have_value("5")


def test_settings_data_migration_download_reconcile_and_cancel_do_not_dirty_config(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, stub = settings_save_server
    page = chromium_page
    page.add_init_script(
        """
        (() => {
          const nativeCrypto = globalThis.crypto;
          const compatibilityCrypto = { randomUUID: undefined };
          if (typeof nativeCrypto?.getRandomValues === "function") {
            compatibilityCrypto.getRandomValues = nativeCrypto.getRandomValues.bind(nativeCrypto);
          }
          Object.defineProperty(globalThis, "crypto", {
            configurable: true,
            value: compatibilityCrypto,
          });
          Object.defineProperty(globalThis, "showSaveFilePicker", {
            configurable: true,
            value: undefined,
          });
        })();
        """
    )
    requests: list[tuple[str, dict[str, str], bytes, str]] = []
    migration_state: dict[str, str] = {
        "state": "idle",
        "request_id": "",
        "migration_id": "migration-e2e-001",
    }

    def migration_route(route: Any, request: Any) -> None:
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/migration/status"):
            payload: dict[str, object] = {
                "state": migration_state["state"],
                "restart_required": migration_state["state"] == "staged",
            }
            if migration_state["state"] == "staged":
                payload.update(
                    {
                        "migration_id": migration_state["migration_id"],
                        "request_id": migration_state["request_id"],
                        "source_omitted_environment_variables": ["OPENBILICLAW_SOURCE_ONLY"],
                        "target_active_environment_variables": ["OPENBILICLAW_TARGET_ONLY"],
                        "frontend": {
                            "theme_mode": "dark",
                            "theme_hue": 210,
                            "accent_style": "modern",
                            "auto_load_on_scroll": False,
                            "side_drawer_open": True,
                        },
                    }
                )
            elif migration_state["state"] == "applied":
                payload["migration_id"] = migration_state["migration_id"]
                payload["frontend"] = {
                    "theme_mode": "dark",
                    "theme_hue": 210,
                    "accent_style": "modern",
                    "auto_load_on_scroll": False,
                    "side_drawer_open": True,
                }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            )
            return
        body = request.post_data_buffer or b""
        headers = dict(request.headers)
        requests.append((path, headers, body, request.method))
        if path.endswith("/api/migration/export"):
            route.fulfill(
                status=200,
                headers={
                    "Content-Type": "application/vnd.openbiliclaw.backup+zip",
                    "Content-Disposition": 'attachment; filename="portable.obcbackup"',
                },
                body=b"portable-backup",
            )
            return
        if path.endswith("/api/migration/import"):
            migration_state["state"] = "staged"
            migration_state["request_id"] = headers["x-obc-migration-request-id"].replace("-", "")
            route.abort("connectionfailed")
            return
        if path.endswith("/api/migration/pending") and request.method == "DELETE":
            migration_state["state"] = "cancelled"
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "state": "cancelled",
                        "cancelled": True,
                        "restart_required": False,
                        "message": "待导入迁移包已取消，当前数据未改动。",
                    }
                ),
            )
            return
        route.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/api/migration/**", migration_route)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{base_url}/web/")
    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="通用").click()

    expect(page.locator("#migrationStatus")).to_contain_text("只能在运行后端的本机操作")
    expect(page.locator("#settingsSaveBar")).to_have_attribute("data-dirty", "false")

    with page.expect_download() as download_info:
        page.get_by_role("button", name="导出全部信息").click()
    assert download_info.value.suggested_filename == "portable.obcbackup"
    expect(page.locator("#migrationStatus")).to_contain_text("迁移包已保存")

    page.locator("#migrationImportFile").set_input_files(
        {
            "name": "portable.obcbackup",
            "mimeType": "application/vnd.openbiliclaw.backup+zip",
            "buffer": b"portable-backup",
        }
    )
    expect(page.locator("#migrationStatus")).to_contain_text("请完全退出并重新启动", timeout=15_000)
    expect(page.locator("#migrationStatus")).to_contain_text("OPENBILICLAW_SOURCE_ONLY")
    expect(page.locator("#migrationStatus")).to_contain_text("OPENBILICLAW_TARGET_ONLY")
    assert page.evaluate("localStorage.getItem('obc.theme')") != "dark"
    expect(page.locator("#settingsSaveBar")).to_have_attribute("data-dirty", "false")
    assert stub.saved_payloads == []

    export_request = next(item for item in requests if item[0].endswith("/migration/export"))
    import_request = next(item for item in requests if item[0].endswith("/migration/import"))
    exported_frontend = json.loads(export_request[2])["frontend"]
    assert set(exported_frontend) == {
        "theme_mode",
        "theme_hue",
        "accent_style",
        "auto_load_on_scroll",
        "side_drawer_open",
    }
    assert export_request[1]["x-obc-auth"] == "1"
    assert import_request[1]["x-obc-auth"] == "1"
    assert import_request[1]["x-obc-migration-confirm"] == "replace-all"
    request_id = import_request[1]["x-obc-migration-request-id"]
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        request_id,
        re.IGNORECASE,
    )
    assert migration_state["request_id"] == request_id.replace("-", "")

    cancel = page.get_by_role("button", name="取消待导入")
    expect(cancel).to_be_visible()
    cancel.click()
    expect(page.locator("#migrationStatus")).to_contain_text("已取消")
    expect(cancel).to_be_hidden()
    cancel_request = next(item for item in requests if item[0].endswith("/migration/pending"))
    assert cancel_request[3] == "DELETE"
    assert cancel_request[1]["x-obc-auth"] == "1"
    expect(page.locator("#settingsSaveBar")).to_have_attribute("data-dirty", "false")

    migration_state["state"] = "applied"
    page.reload()
    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="通用").click()
    expect(page.locator("#migrationStatus")).to_contain_text("成功载入")
    page.wait_for_function("localStorage.getItem('obc.theme') === 'dark'")
    assert page.evaluate("localStorage.getItem('obc.themeHue')") == "210"
    assert page.evaluate("localStorage.getItem('obc.accentStyle')") == "modern"
    assert page.evaluate("localStorage.getItem('openbiliclaw.webui.autoLoadOnScroll')") == "0"
    assert (
        page.evaluate("localStorage.getItem('openbiliclaw.webui.appliedMigrationFrontend')")
        == migration_state["migration_id"]
    )

    # A durable applied status is only a one-shot preference handoff. A later
    # browser reload must not overwrite the user's post-migration choice.
    page.evaluate("localStorage.setItem('obc.theme', 'light')")
    page.reload()
    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="通用").click()
    expect(page.locator("#migrationStatus")).to_contain_text("成功载入")
    assert page.evaluate("localStorage.getItem('obc.theme')") == "light"


def test_migration_request_id_final_fallback_is_rfc4122_uuid(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    base_url, _stub = settings_save_server
    page = chromium_page
    page.add_init_script(
        """
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: { randomUUID: undefined, getRandomValues: undefined },
        });
        """
    )
    request_ids: list[str] = []

    def migration_route(route: Any, request: Any) -> None:
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/migration/status"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"state": "idle", "restart_required": False}),
            )
            return
        if path.endswith("/api/migration/import"):
            request_id = dict(request.headers)["x-obc-migration-request-id"]
            request_ids.append(request_id)
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "state": "staged",
                        "request_id": request_id.replace("-", ""),
                        "restart_required": True,
                    }
                ),
            )
            return
        route.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/api/migration/**", migration_route)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{base_url}/web/")
    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="通用").click()
    page.locator("#migrationImportFile").set_input_files(
        {
            "name": "portable.obcbackup",
            "mimeType": "application/vnd.openbiliclaw.backup+zip",
            "buffer": b"portable-backup",
        }
    )
    expect(page.locator("#migrationStatus")).to_contain_text("请完全退出并重新启动", timeout=15_000)
    assert len(request_ids) == 1
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        request_ids[0],
        re.IGNORECASE,
    )


def test_pending_chat_count_toggle_hides_badge(
    settings_save_server: tuple[str, SettingsSaveStub],
    chromium_page: Page,
) -> None:
    """Issue #217: 关闭「显示待聊未读数」后 chatPendingCountBadge 始终隐藏。"""
    base_url, _stub = settings_save_server
    page = chromium_page
    page.goto(f"{base_url}/web/")

    badge = page.locator("#chatPendingCountBadge")
    expect(badge).to_have_text("3")

    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="前端").click()
    toggle = page.locator("#showPendingChatCountSetting")
    expect(toggle).to_be_checked()

    toggle.uncheck(force=True)
    expect(badge).to_be_hidden()
    assert page.evaluate("localStorage.getItem('openbiliclaw.webui.showPendingChatCount')") == "0"

    page.reload()
    page.get_by_role("button", name="设置", exact=True).click()
    page.get_by_role("tab", name="前端").click()
    expect(page.locator("#showPendingChatCountSetting")).not_to_be_checked()
    expect(page.locator("#chatPendingCountBadge")).to_be_hidden()
