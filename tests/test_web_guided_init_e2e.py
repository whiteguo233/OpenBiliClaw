from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


ROOT = Path(__file__).resolve().parents[1]

ANALYZE_TIMEOUT_DETAIL = (
    "偏好分析等待 AI 服务超过 6 分钟仍未返回结果，已自动停止，避免继续卡住。"
    "常见原因是 Base URL、模型名或代理配置错误。请到模型设置测试 AI 服务后重试初始化。"
)
DISCOVERY_TIMEOUT_DETAIL = (
    "画像已生成，但首轮内容池等待内容发现超过 10 分钟仍未完成，本次初始化为部分完成。"
    "系统会在后台继续补池；请检查平台登录与网络/代理。"
)
DOUYIN_DEGRADED_DETAIL = (
    "抖音采集状态 dy_status=degraded：已保留并用于画像建模 57 条已采事件，"
    "但至少一个范围未能证明分页完整。"
)


def _status(
    *,
    initialized: bool = False,
    running: bool = False,
    current_stage: int = 0,
    can_start: bool = True,
    reason: str = "none",
    detail: str = "",
    partial_success: bool = False,
    enabled_platforms: list[str] | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "initialized": initialized,
        "running": running,
        "run_id": "test-run",
        "sequence": current_stage,
        "progress_sequence": current_stage,
        "last_activity": "2026-07-15 08:00:00",
        "last_heartbeat_at": "2026-07-15 08:00:00",
        "last_progress_at": "2026-07-15 08:00:00",
        "current_stage": current_stage,
        "total_stages": 4,
        "stages": stages
        or [
            {"n": 1, "label": "拉取数据", "status": "pending", "reason": None},
            {"n": 2, "label": "分析偏好", "status": "pending", "reason": None},
            {"n": 3, "label": "生成并保存完整画像", "status": "pending", "reason": None},
            {"n": 4, "label": "生成首轮可用推荐", "status": "pending", "reason": None},
        ],
        "partial_success": partial_success,
        "can_start": can_start,
        "can_manage": True,
        "prerequisites": {
            "bilibili_logged_in": True,
            "bilibili_check": "ok",
            "llm_ready": True,
            "embedding_ready": True,
            "enabled_platforms": enabled_platforms or ["bilibili", "youtube"],
        },
        "reason": reason,
        "detail": detail,
    }


class GuidedInitStub:
    def __init__(self) -> None:
        self.init_posts: list[dict[str, Any]] = []
        self.cancel_posts = 0
        self.config_puts: list[dict[str, Any]] = []
        self.config_put_status = 200
        self.config_put_response: dict[str, Any] = {"ok": True, "config": {}}
        self.config_apply_status_response: dict[str, Any] = {
            "state": "idle",
            "requested_revision": 0,
            "applied_revision": 0,
            "message": "",
            "error": "",
        }
        self.current_status = _status()
        self.post_init_error: tuple[int, dict[str, Any]] | None = None
        self.fail_next_status = False
        # When set, replaces the default /api/config body. Used to simulate a
        # saved default_provider the wizard no longer offers (e.g. "ollama").
        self.config_override: dict[str, Any] | None = None
        self.runtime_status = {
            "initialized": False,
            "pool_available_count": 0,
            "pool_size": 0,
            "pool_refresh_state": "idle",
            "pool_source_shares": {"bilibili": 1.0},
            "configured_sources": {"bilibili": {"enabled": True}},
            "unread_count": 0,
        }
        self.ping_response: dict[str, Any] = {
            "status": "ok",
            "service": "openbiliclaw-api",
        }

    def status(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.current_status))

    def start_response(self) -> dict[str, Any]:
        status = self.status()
        return {
            "running": status["running"],
            "run_id": status["run_id"],
            "sequence": status["sequence"],
            "current_stage": status["current_stage"],
            "total_stages": status["total_stages"],
            "stages": status["stages"],
            "partial_success": status["partial_success"],
            "status": "running" if status["running"] else "idle",
            "reason": status["reason"],
        }

    def set_running(self) -> None:
        self.current_status = _status(
            running=True,
            current_stage=1,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "running", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "pending", "reason": None},
                {"n": 3, "label": "生成并保存完整画像", "status": "pending", "reason": None},
                {"n": 4, "label": "生成首轮可用推荐", "status": "pending", "reason": None},
            ],
        )

    def set_initialized(self) -> None:
        self.current_status = _status(
            initialized=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成并保存完整画像", "status": "ok", "reason": None},
                {"n": 4, "label": "生成首轮可用推荐", "status": "ok", "reason": None},
            ],
        )

    def set_profile_ready_discovering(self) -> None:
        """Strict-init overlap: profile exists, but stage 4 still owns run."""
        self.current_status = _status(
            initialized=True,
            running=True,
            current_stage=4,
            can_start=False,
            reason="already_running",
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成并保存完整画像", "status": "ok", "reason": None},
                {
                    "n": 4,
                    "label": "生成首轮可用推荐",
                    "status": "running",
                    "reason": None,
                    "eta_seconds": 300,
                    "progress": {
                        "done": 0,
                        "total": 0,
                        "mode": "indeterminate",
                        "elapsed_seconds": 40,
                        "max_seconds": 600,
                        "note": "严格基于完整画像发现候选内容",
                    },
                },
            ],
        )

    def set_analyze_timeout(self) -> None:
        self.current_status = _status(
            can_start=True,
            reason="analyze_failed",
            detail=ANALYZE_TIMEOUT_DETAIL,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "failed", "reason": "analyze_failed"},
                {
                    "n": 3,
                    "label": "生成并保存完整画像",
                    "status": "failed",
                    "reason": "analyze_failed",
                },
                {
                    "n": 4,
                    "label": "生成首轮可用推荐",
                    "status": "failed",
                    "reason": "analyze_failed",
                },
            ],
        )

    def set_discovery_timeout(self) -> None:
        self.current_status = _status(
            initialized=True,
            can_start=False,
            reason="discovery_timeout",
            detail=DISCOVERY_TIMEOUT_DETAIL,
            partial_success=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成并保存完整画像", "status": "ok", "reason": None},
                {
                    "n": 4,
                    "label": "生成首轮可用推荐",
                    "status": "warning",
                    "reason": "discovery_timeout",
                },
            ],
        )

    def set_douyin_degraded(self) -> None:
        self.current_status = _status(
            initialized=True,
            can_start=False,
            reason="douyin_degraded",
            detail=DOUYIN_DEGRADED_DETAIL,
            partial_success=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "warning", "reason": "douyin_degraded"},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成并保存完整画像", "status": "ok", "reason": None},
                {"n": 4, "label": "生成首轮可用推荐", "status": "ok", "reason": None},
            ],
        )

    def set_bilibili_blocked(self) -> None:
        self.current_status = _status(
            can_start=False,
            reason="bilibili_not_logged_in",
        )
        self.current_status["prerequisites"]["bilibili_logged_in"] = False
        self.current_status["prerequisites"]["bilibili_check"] = "failed"

    def set_enabled_platforms(self, platforms: list[str]) -> None:
        self.current_status = _status(enabled_platforms=platforms)


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    status: int = 200,
) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.fixture()
def guided_init_server() -> tuple[str, GuidedInitStub]:
    state = GuidedInitStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith("/shared/"):
                # The desktop page and the setup wizard both load the shared
                # source-status module from the backend's /shared mount. Without
                # this route it 404s, app.js dies on the missing global, and the
                # failure surfaces as an unrelated test timing out.
                rel = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / rel)
            if path in {"/setup/", "/setup/index.html"}:
                return self._serve_file(ROOT / "src/openbiliclaw/web/setup/index.html", "text/html")
            if path in {"/web", "/web/"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html",
                    "text/html",
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/config":
                if state.config_override is not None:
                    return _json_response(self, state.config_override)
                return _json_response(
                    self,
                    {
                        "config": {
                            "llm": {
                                "default_provider": "openai_compatible",
                                "openai_compatible": {
                                    "api_key": "sk-t************alue",
                                    "model": "compat-model",
                                    "base_url": "https://compat.example/v1",
                                    "api_flavor": "responses",
                                },
                            },
                            "bilibili": {"cookie": "SESSDATA=test"},
                            "sources": {
                                "bilibili": {"enabled": True},
                                "youtube": {"enabled": True},
                            },
                        }
                    },
                )
            if path == "/api/config/apply-status":
                return _json_response(self, state.config_apply_status_response)
            if path == "/api/ping":
                return _json_response(self, state.ping_response)
            if path == "/api/init-status":
                if state.fail_next_status:
                    state.fail_next_status = False
                    return _json_response(self, {"error": "temporary"}, 500)
                return _json_response(self, state.status())
            if path == "/api/runtime-status":
                return _json_response(self, state.runtime_status)
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": [], "runtime": state.runtime_status})
            if path == "/api/delight/pending-batch":
                return _json_response(self, {"items": []})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
            if path == "/api/notifications/pending":
                return _json_response(self, {"items": []})
            if path == "/api/profile-summary":
                return _json_response(
                    self,
                    {"profile": None, "memory_items": [], "has_more": False},
                )
            if path == "/api/profile/edit-state":
                return _json_response(self, {"busy": False, "draft": ""})
            if path in {"/api/watch-later", "/api/favorites"}:
                return _json_response(self, {"items": [], "total": 0})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/init":
                state.init_posts.append(payload)
                if state.post_init_error is not None:
                    status_code, body = state.post_init_error
                    return _json_response(self, body, status_code)
                state.set_running()
                return _json_response(self, state.start_response(), 202)
            if path == "/api/init/cancel":
                state.cancel_posts += 1
                state.current_status = _status(
                    running=False,
                    can_start=True,
                    reason="cancelled",
                    stages=[
                        {
                            "n": n,
                            "label": label,
                            "status": "cancelled",
                            "reason": "cancelled",
                        }
                        for n, label in (
                            (1, "拉取数据"),
                            (2, "分析偏好"),
                            (3, "生成并保存完整画像"),
                            (4, "生成首轮可用推荐"),
                        )
                    ],
                )
                return _json_response(self, {"cancelling": True, "run_id": "test-run"}, 202)
            return _json_response(self, {"ok": True})

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/config":
                state.config_puts.append(payload)
                return _json_response(
                    self,
                    state.config_put_response,
                    state.config_put_status,
                )
            return _json_response(self, {"ok": True})

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found", "path": str(path)}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page():
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            yield page
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(
                "Playwright Chromium is not installed; "
                "run `uv run --extra browser playwright install chromium`"
            )
        raise


def _install_fake_runtime_stream(page: Any, *, fast_watchdog: bool = False) -> None:
    watchdog_setup = (
        "window.__OBC_TEST_INIT_POLL_MS = 50;"
        "window.__OBC_TEST_INIT_START_POLL_MS = 50;"
        "window.__OBC_TEST_INIT_WATCHDOG_MS = 50;"
        if fast_watchdog
        else ""
    )
    script = """
        (() => {
          __WATCHDOG_SETUP__
          window.__obcSockets = [];
          window.__obcInitPosted = false;
          const realFetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = String(input && input.url ? input.url : input);
            const method = String((init && init.method) || "GET").toUpperCase();
            const isInitPost = method === "POST" && /\\/api\\/init(?:$|[?#])/.test(url);
            return realFetch(input, init).then((response) => {
              if (isInitPost) window.__obcInitPosted = true;
              return response;
            });
          };
          window.WebSocket = class FakeWebSocket {
            constructor(url) {
              this.url = String(url);
              this.readyState = 1;
              this.listeners = new Map();
              window.__obcSockets.push(this);
              window.setTimeout(() => this.__dispatch("open", { type: "open" }), 0);
            }
            addEventListener(type, handler) {
              const list = this.listeners.get(type) || [];
              list.push(handler);
              this.listeners.set(type, list);
            }
            removeEventListener(type, handler) {
              const list = this.listeners.get(type) || [];
              this.listeners.set(type, list.filter((item) => item !== handler));
            }
            __dispatch(type, event) {
              const attr = this[`on${type}`];
              if (typeof attr === "function") attr.call(this, event);
              for (const handler of this.listeners.get(type) || []) {
                handler.call(this, event);
              }
            }
            close() {
              this.readyState = 3;
              this.__dispatch("close", { type: "close" });
            }
          };
          window.__emitRuntimeEvent = (payload) => {
            const event = { type: "message", data: JSON.stringify(payload) };
            for (const socket of window.__obcSockets) {
              socket.__dispatch("message", event);
            }
          };
        })();
        """
    page.add_init_script(script.replace("__WATCHDOG_SETUP__", watchdog_setup))


def test_setup_wizard_e2e_restores_fields_per_provider(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """Provider switches must not leak another provider's model or endpoint."""
    base_url, _ = guided_init_server
    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.wait_for_function("document.querySelector('#model').value === 'compat-model'")

    assert chromium_page.locator("#baseUrl").input_value() == "https://compat.example/v1"
    assert chromium_page.locator("#apiFlavor").input_value() == "responses"

    chromium_page.locator("#model").fill("compat-draft")
    chromium_page.locator("#provider").select_option("deepseek")
    assert chromium_page.locator("#model").input_value() == "deepseek-v4-flash"
    assert chromium_page.locator("#baseUrl").input_value() == ""

    chromium_page.locator("#provider").select_option("openai_compatible")
    assert chromium_page.locator("#model").input_value() == "compat-draft"
    assert chromium_page.locator("#baseUrl").input_value() == "https://compat.example/v1"

    # Local Ollama is embedding-only; the wizard must not offer it for chat.
    provider_values = chromium_page.evaluate(
        "() => Array.from(document.querySelectorAll('#provider option')).map((o) => o.value)"
    )
    assert "ollama" not in provider_values


def test_setup_wizard_e2e_notifies_when_saved_provider_is_unlisted(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A saved default_provider the wizard no longer offers (e.g. "ollama",
    which is embedding-only here) must not be silently dropped — the wizard
    surfaces an info notice telling the user to re-pick a chat provider."""
    base_url, stub = guided_init_server
    stub.config_override = {
        "config": {
            "llm": {
                "default_provider": "ollama",
                "ollama": {"model": "qwen2.5:7b", "base_url": "http://127.0.0.1:11434/v1"},
            },
            "bilibili": {"cookie": "SESSDATA=test"},
            "sources": {"bilibili": {"enabled": True}},
        }
    }
    chromium_page.goto(f"{base_url}/setup/")

    notice = chromium_page.locator("#msg0")
    notice.wait_for(state="visible")
    text = notice.inner_text()
    assert "ollama" in text
    assert "向量检索" in text
    # The unlisted provider must not have been forced into the select.
    assert chromium_page.locator("#provider").input_value() != "ollama"


def test_setup_wizard_e2e_starts_guided_init_and_finishes_on_runtime_event(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("label.init-source-row", has_text="YouTube").locator("input").check()
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )
    assert stub.init_posts == [
        {"sources": ["bilibili", "youtube"], "llm_concurrency": 3, "init_timeout_minutes": 60}
    ]
    socket_url = chromium_page.evaluate("() => window.__obcSockets[0].url")
    assert socket_url.endswith("/api/runtime-stream")

    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_progress" })""")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('1/4')"
    )
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('[data-panel="3"].active')
    assert "首轮初始化" in chromium_page.locator('[data-panel="3"]').inner_text()


def test_setup_wizard_e2e_partial_success_finishes_without_second_pool_wait(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )

    stub.set_discovery_timeout()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('[data-panel="3"].active')
    assert "超过 10 分钟" in chromium_page.locator("#doneInit").inner_text()
    assert "后台继续补池" in chromium_page.locator("#doneInit").inner_text()
    assert chromium_page.locator("#finish").is_enabled()


def test_setup_wizard_e2e_save_llm_does_not_start_guided_init(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')

    assert stub.init_posts == []
    assert len(stub.config_puts) == 1
    assert stub.config_puts[0]["suppress_background_llm_work"] is True


def test_setup_wizard_waits_for_queued_config_apply_before_next_step(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.config_put_status = 202
    stub.config_put_response = {
        "ok": True,
        "config": {},
        "apply_state": "queued",
        "apply_revision": 7,
    }
    stub.config_apply_status_response = {
        "state": "applying",
        "requested_revision": 7,
        "applied_revision": 0,
        "message": "正在后台应用配置",
        "error": "",
    }

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()

    chromium_page.wait_for_function(
        "() => document.querySelector('#doneLlm')?.innerText.includes('正在应用')"
    )
    assert chromium_page.locator('[data-panel="0"].active').count() == 1
    assert chromium_page.locator('[data-panel="1"].active').count() == 0

    stub.config_apply_status_response = {
        "state": "applied",
        "requested_revision": 7,
        "applied_revision": 7,
        "message": "配置已应用",
        "error": "",
    }
    chromium_page.wait_for_selector('[data-panel="1"].active', timeout=3000)


def test_setup_wizard_e2e_replaces_invalid_first_run_placeholder(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """Picking another provider repairs, rather than preserves, the fresh
    enabled/keyless DeepSeek placeholder that forced the backend degraded."""
    base_url, stub = guided_init_server
    stub.config_override = {
        "language": "zh",
        "degraded": True,
        "degraded_reason": "llm_registry_unavailable",
        "issues": [
            {
                "field": "llm.instances.deepseek.api_key",
                "message": "启用的 `deepseek` 实例缺少 API Key。",
                "severity": "blocking",
            }
        ],
        "llm": {
            "routing_version": 2,
            "instances": {
                "deepseek": {
                    "name": "DeepSeek 官方",
                    "provider_type": "deepseek",
                    "enabled": True,
                    "api_key": "",
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                },
                "openrouter-backup": {
                    "name": "已有正常备选",
                    "provider_type": "openrouter",
                    "enabled": True,
                    "api_key": "sk-o************test",
                    "model": "openai/gpt-5-nano",
                    "base_url": "https://openrouter.ai/api/v1",
                },
            },
            "default_chain": ["deepseek", "openrouter-backup"],
            "routes": {},
        },
        "sources": {"bilibili": {"enabled": True}},
    }

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("openai_compatible")
    chromium_page.locator("#apiKey").fill("sk-sensenova-test")
    chromium_page.locator("#baseUrl").fill("https://token.sensenova.cn/v1")
    chromium_page.locator("#model").fill("sensenova-6.7-flash-lite")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')

    assert len(stub.config_puts) == 1
    llm = stub.config_puts[0]["llm"]
    assert llm["instances"]["deepseek"]["enabled"] is False
    assert llm["instances"]["openrouter-backup"]["enabled"] is True
    assert llm["instances"]["openai-compatible-main"]["enabled"] is True
    assert llm["default_chain"] == ["openai-compatible-main", "openrouter-backup"]


def test_setup_wizard_e2e_renders_structured_config_validation_error(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A validation response should name the actual blocking field instead of
    dumping/truncating the JSON response body."""
    base_url, stub = guided_init_server
    stub.config_put_status = 400
    stub.config_put_response = {
        "ok": False,
        "message": "配置校验失败，未写入 config.toml。",
        "config": {
            "issues": [
                {
                    "field": "llm.instances.deepseek.api_key",
                    "message": "启用的 `deepseek` 实例缺少 API Key。",
                    "severity": "blocking",
                }
            ]
        },
    }

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.wait_for_function("document.querySelector('#model').value === 'compat-model'")
    chromium_page.locator("#saveLlm").click()
    chromium_page.locator("#msg0").wait_for(state="visible")

    text = chromium_page.locator("#msg0").inner_text()
    assert "配置校验失败" in text
    assert "deepseek" in text
    assert "API Key" in text
    assert '{"ok":' not in text


def test_setup_wizard_e2e_degraded_save_recovers_in_place_without_restart(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A repaired degraded runtime should advance immediately on hot recovery."""
    base_url, stub = guided_init_server
    degraded_config = {
        "language": "zh",
        "degraded": True,
        "degraded_reason": "llm_registry_unavailable",
        "issues": [
            {
                "field": "llm.instances.deepseek.api_key",
                "message": "启用的 `deepseek` 实例缺少 API Key。",
                "severity": "blocking",
            }
        ],
        "llm": {
            "routing_version": 2,
            "instances": {
                "deepseek": {
                    "name": "DeepSeek 官方",
                    "provider_type": "deepseek",
                    "enabled": True,
                    "api_key": "",
                    "model": "deepseek-v4-flash",
                }
            },
            "default_chain": ["deepseek"],
            "routes": {},
        },
        "sources": {"bilibili": {"enabled": True}},
    }
    recovered_config = {
        **degraded_config,
        "degraded": False,
        "degraded_reason": "",
        "issues": [],
    }
    stub.config_override = degraded_config
    stub.config_put_response = {
        "ok": True,
        "config": recovered_config,
        "message": "配置已保存，后端已从降级模式原地恢复，无需重启。",
        "reloaded": True,
        "rollback_applied": False,
        "restart_required": False,
    }

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#apiKey").fill("sk-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')

    assert len(stub.config_puts) == 1
    assert stub.config_puts[0]["suppress_background_llm_work"] is True
    assert (
        chromium_page.evaluate(
            "() => localStorage.getItem('openbiliclaw.setup.resume_after_restart')"
        )
        is None
    )
    assert "请重启" not in chromium_page.locator("#msg0").inner_text()


def test_setup_wizard_e2e_supports_restart_fallback_when_hot_recovery_is_unavailable(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """Older backends/exceptional bootstrap paths may still require restart.

    The fallback must not advance into init against a degraded process; after
    restart, the non-secret marker resumes at account setup.
    """
    base_url, stub = guided_init_server
    chromium_page.add_init_script("window.__OBC_TEST_RESTART_POLL_MS = 30;")
    degraded_config = {
        "language": "zh",
        "degraded": True,
        "degraded_reason": "llm_registry_unavailable",
        "issues": [
            {
                "field": "llm.instances.deepseek.api_key",
                "message": "启用的 `deepseek` 实例缺少 API Key。",
                "severity": "blocking",
            }
        ],
        "llm": {
            "routing_version": 2,
            "instances": {
                "deepseek": {
                    "name": "DeepSeek 官方",
                    "provider_type": "deepseek",
                    "enabled": True,
                    "api_key": "",
                    "model": "deepseek-v4-flash",
                }
            },
            "default_chain": ["deepseek"],
            "routes": {},
        },
        "sources": {"bilibili": {"enabled": True}},
    }
    recovered_config = {
        "language": "zh",
        "degraded": False,
        "issues": [],
        "llm": {
            "routing_version": 2,
            "instances": {
                "deepseek": {
                    "name": "DeepSeek 官方",
                    "provider_type": "deepseek",
                    "enabled": True,
                    "api_key": "sk-t************test",
                    "model": "deepseek-v4-flash",
                }
            },
            "default_chain": ["deepseek"],
            "routes": {},
        },
        "sources": {"bilibili": {"enabled": True}},
    }
    stub.config_override = degraded_config
    stub.ping_response = {
        "status": "ok",
        "service": "openbiliclaw-api",
        "degraded": True,
        "degraded_reason": "llm_registry_unavailable",
    }
    stub.config_put_response = {
        "ok": True,
        "config": recovered_config,
        "message": "配置已保存，请重启后端。",
        "reloaded": False,
        "rollback_applied": False,
        "restart_required": True,
    }

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#apiKey").fill("sk-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_function(
        "() => document.querySelector('#msg0')?.innerText.includes('请重启')"
    )

    assert chromium_page.locator('[data-panel="0"].active').count() == 1
    marker = chromium_page.evaluate(
        "() => localStorage.getItem('openbiliclaw.setup.resume_after_restart')"
    )
    assert marker

    # Simulate the daemon restart: both liveness and authoritative config now
    # come from the rebuilt, non-degraded process.
    stub.config_override = recovered_config
    stub.ping_response = {"status": "ok", "service": "openbiliclaw-api"}

    chromium_page.wait_for_selector('[data-panel="1"].active')
    marker_after = chromium_page.evaluate(
        "() => localStorage.getItem('openbiliclaw.setup.resume_after_restart')"
    )
    assert marker_after is None


def test_setup_wizard_e2e_selected_sources_do_not_require_prior_settings_enable(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.set_enabled_platforms(["bilibili"])
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("label.init-source-row", has_text="小红书").locator("input").check()
    chromium_page.locator("label.init-source-row", has_text="抖音").locator("input").check()
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    assert stub.init_posts == [
        {
            "sources": ["bilibili", "xiaohongshu", "douyin"],
            "llm_concurrency": 3,
            "init_timeout_minutes": 60,
        }
    ]


def test_desktop_web_e2e_shows_init_cta_and_starts_same_init_endpoint(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    assert chromium_page.locator(".video-card").count() == 0
    assert chromium_page.locator("#loadMoreBtn").is_hidden()

    chromium_page.locator("label.init-source-row", has_text="YouTube").locator("input").check()
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [
        {"sources": ["bilibili", "youtube"], "llm_concurrency": 3, "init_timeout_minutes": 60}
    ]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-progress')?.innerText.includes('1/4')"
    )
    assert "✗" not in chromium_page.locator(".init-checklist").inner_text()
    fill_width = chromium_page.locator(".init-progress-fill").evaluate(
        "el => Number.parseFloat(el.style.width)"
    )
    assert fill_width > 0
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")
    assert chromium_page.locator("#loadMoreBtn").is_visible()


def test_desktop_web_e2e_partial_success_does_not_restore_init_card(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    stub.set_discovery_timeout()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")
    assert chromium_page.locator("#loadMoreBtn").is_visible()


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_surfaces_timeout_cause_and_recovery_actions(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    if surface == "setup":
        chromium_page.goto(f"{base_url}/setup/")
        chromium_page.locator("#provider").select_option("deepseek")
        chromium_page.locator("#apiKey").fill("sk-e2e-test")
        chromium_page.locator("#saveLlm").click()
        chromium_page.wait_for_selector('[data-panel="1"].active')
        chromium_page.locator("#next1").click()
        chromium_page.wait_for_selector('[data-panel="2"].active')
        chromium_page.locator("#startInit").click()
        label = chromium_page.locator("#initProgressLabel")
        retry = chromium_page.locator("#startInit")
    else:
        chromium_page.goto(f"{base_url}/web/")
        chromium_page.wait_for_selector(".init-onboarding", state="attached")
        chromium_page.locator('[data-init-action="start"]').click()
        label = chromium_page.locator(".init-progress p")
        retry = chromium_page.locator('[data-init-action="start"]')

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_analyze_timeout()
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_failed" })""")
    chromium_page.wait_for_function(
        "() => document.body.innerText.includes('超过 6 分钟') && "
        "document.body.innerText.includes('Base URL')"
    )

    text = label.inner_text()
    assert "偏好分析" in text
    assert "超过 6 分钟" in text
    assert "Base URL" in text
    assert "模型设置" in text
    assert label.get_attribute("role") == "alert"
    assert retry.inner_text() == "重试初始化"
    if surface == "desktop":
        assert chromium_page.locator('[data-init-action="settings"]').is_visible()


def test_desktop_web_e2e_matches_popup_when_runtime_has_post_init_signals(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.runtime_status.update(
        {
            "initialized": False,
            "recommendation_count": 4,
            "pool_available_count": 12,
            "pool_pending_count": 3,
            "last_discovered_count": 9,
            "last_replenished_count": 5,
        }
    )
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")

    chromium_page.wait_for_selector(".empty-state")
    assert chromium_page.locator(".init-onboarding").count() == 0
    assert chromium_page.locator("#loadMoreBtn").is_visible()


def test_setup_wizard_e2e_watchdog_polls_when_runtime_stream_is_silent(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.wait_for_selector('[data-panel="3"].active')


def test_setup_wizard_e2e_default_watchdog_polls_when_runtime_stream_is_silent(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.wait_for_selector('[data-panel="3"].active', timeout=30000)


def test_setup_wizard_e2e_blocks_missing_bilibili_without_post(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.set_bilibili_blocked()
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_selector("#initReason.msg.show")
    assert stub.init_posts == []
    assert "还没检测到 B站 登录" in chromium_page.locator("#initReason").inner_text()
    assert "✗" in chromium_page.locator("#initChecklist").inner_text()


def test_setup_wizard_e2e_shows_neutral_hint_when_bilibili_never_syncs(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A never-synced B站 login must stop the spinner and show a neutral,
    skippable hint — while the poll keeps running so it can still flip green."""
    base_url, _ = guided_init_server
    # /api/sources/status 404s in the stub, so checkBili always returns null.
    # Speed the 3s poll up so the negative state lands within the test budget.
    chromium_page.add_init_script("window.__OBC_TEST_BILI_POLL_MS = 30;")

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("deepseek")
    chromium_page.locator("#apiKey").fill("sk-e2e-test")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')

    # After a few ticks the spinner row becomes the neutral hint.
    chromium_page.wait_for_function(
        "() => document.querySelector('#biliStatus')"
        "?.textContent.includes('还没检测到 B站 登录同步')"
    )
    box = chromium_page.locator("#biliStatus")
    # Not painted as success, and the spinner element is gone.
    assert "ok" not in (box.get_attribute("class") or "")
    assert chromium_page.locator("#biliStatus .spin").count() == 0
    # Still on step 1 (the poll keeps running; it did not crash or advance).
    assert chromium_page.locator('[data-panel="1"].active').count() == 1


def test_desktop_web_e2e_surfaces_init_start_conflict(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.post_init_error = (409, {"error": "already_running"})
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [
        {
            "sources": ["bilibili"],
            "llm_concurrency": 3,
            "init_timeout_minutes": 60,
        }
    ]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-reason')?.innerText.includes('初始化正在进行中')"
    )
    assert chromium_page.locator('[data-init-action="start"]').is_enabled()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("bilibili_not_logged_in", "还没检测到 B 站登录"),
        ("llm_not_ready", "AI 服务还没配好"),
        ("unsupported_runtime", "docker exec -it openbiliclaw-backend openbiliclaw init"),
        ("already_initialized", "已经初始化过了"),
    ],
)
def test_desktop_web_e2e_surfaces_post_init_prereq_race_errors(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    code: str,
    expected: str,
) -> None:
    base_url, stub = guided_init_server
    stub.post_init_error = (409, {"error": code})
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    assert stub.init_posts == [
        {
            "sources": ["bilibili"],
            "llm_concurrency": 3,
            "init_timeout_minutes": 60,
        }
    ]
    chromium_page.wait_for_function(
        "(expected) => document.querySelector('.init-reason')?.innerText.includes(expected)",
        arg=expected,
    )
    assert chromium_page.locator('[data-init-action="start"]').is_enabled()


def test_desktop_web_e2e_retries_status_after_terminal_event_fetch_failure(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-progress')?.innerText.includes('1/4')"
    )

    stub.fail_next_status = True
    stub.set_initialized()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")


def test_setup_wizard_e2e_resumes_running_init_on_page_load(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """A reload mid-init must land on the live progress, not silently on step 0."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)
    stub.set_running()

    chromium_page.goto(f"{base_url}/setup/")

    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgress')?.hidden === false"
    )
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('1/4')"
    )
    assert chromium_page.locator("#startInit").is_disabled()
    # Re-attach only observes: no second POST /api/init.
    assert stub.init_posts == []


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_profile_ready_does_not_finish_while_discovery_is_running(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    """Regression for PR #117: running wins when both booleans are true."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)
    stub.set_profile_ready_discovering()

    if surface == "setup":
        chromium_page.goto(f"{base_url}/setup/")
        chromium_page.wait_for_selector('[data-panel="2"].active')
        assert chromium_page.locator('[data-panel="3"].active').count() == 0
        assert chromium_page.locator("#cancelInit").is_visible()
        label = chromium_page.locator("#initProgressLabel")
    else:
        chromium_page.goto(f"{base_url}/web/")
        chromium_page.wait_for_selector(".init-onboarding", state="attached")
        assert chromium_page.locator('[data-init-action="cancel"]').is_visible()
        label = chromium_page.locator(".init-progress p")

    assert "4/4" in label.inner_text()
    assert "严格基于完整画像" in label.inner_text()
    # Indeterminate work has a moving visual but does not claim an item pct.
    assert "%" not in label.inner_text()


def test_desktop_web_e2e_rehydrates_runtime_when_profile_ready_run_completes(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """The stage-4 terminal edge must replace the pre-init runtime snapshot."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)
    stub.set_profile_ready_discovering()

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.wait_for_function(
        "() => document.querySelector('#poolAvailable')?.innerText.includes('后端未初始化')"
    )

    stub.set_initialized()
    stub.runtime_status.update(
        {
            "initialized": True,
            "pool_available_count": 12,
            "pool_target_count": 300,
        }
    )
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")

    chromium_page.wait_for_function("() => document.querySelector('.init-onboarding') === null")
    chromium_page.wait_for_function(
        "() => document.querySelector('#statusLabel')?.innerText.includes('已连接本地后端')"
    )
    chromium_page.wait_for_function(
        "() => document.querySelector('#poolAvailable')?.innerText.includes('还有 12 条可换')"
    )


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_running_init_can_be_cancelled_from_progress_panel(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page, fast_watchdog=True)
    stub.set_running()

    if surface == "setup":
        chromium_page.goto(f"{base_url}/setup/")
        chromium_page.wait_for_selector('[data-panel="2"].active')
        chromium_page.locator("#cancelInit").click()
        retry = chromium_page.locator("#startInit")
    else:
        chromium_page.goto(f"{base_url}/web/")
        chromium_page.wait_for_selector(".init-onboarding", state="attached")
        chromium_page.locator('[data-init-action="cancel"]').click()
        retry = chromium_page.locator('[data-init-action="start"]')

    chromium_page.wait_for_function("() => document.body.innerText.includes('初始化已取消')")
    assert stub.cancel_posts == 1
    assert retry.is_enabled()
    assert "重试初始化" in retry.inner_text()


def test_setup_wizard_e2e_partial_success_enters_completion_screen(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    """Terminal partial success never becomes a frontend-owned 95% wait."""
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)
    stub.set_discovery_timeout()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 0})

    chromium_page.goto(f"{base_url}/setup/")

    chromium_page.wait_for_selector('[data-panel="3"].active')
    chromium_page.wait_for_function(
        "() => document.querySelector('#doneTitle')?.innerText.includes('初始化部分完成')"
    )
    assert "后台继续补池" in chromium_page.locator("#doneInit").inner_text()
    assert chromium_page.locator("#finish").is_enabled()


def test_setup_wizard_e2e_douyin_partial_uses_source_specific_detail(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)
    stub.set_douyin_degraded()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})

    chromium_page.goto(f"{base_url}/setup/")

    chromium_page.wait_for_selector('[data-panel="3"].active')
    assert "初始化部分完成" in chromium_page.locator("#doneTitle").inner_text()
    assert "dy_status=degraded" in chromium_page.locator("#doneInit").inner_text()
    assert "57 条" in chromium_page.locator("#doneInit").inner_text()
    assert "后台继续补池" not in chromium_page.locator("#doneSummary").inner_text()
    assert "未知初始化状态" not in chromium_page.locator("#doneInit").inner_text()


def test_desktop_web_e2e_douyin_partial_toast_uses_source_specific_detail(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/web/")
    chromium_page.wait_for_selector(".init-onboarding", state="attached")
    chromium_page.locator('[data-init-action="start"]').click()
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")

    stub.set_douyin_degraded()
    stub.runtime_status.update({"initialized": True, "pool_available_count": 12})
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")

    chromium_page.wait_for_function(
        "() => document.querySelector('#toastContainer')?.innerText.includes('dy_status=degraded')"
    )
    toast_text = chromium_page.locator("#toastContainer").inner_text()
    assert "57 条" in toast_text
    assert "后台继续补池" not in toast_text
    assert "未知初始化状态" not in toast_text


def _open_init_sources(page: Any, base_url: str, surface: str) -> tuple[Any, Any]:
    """Land on the source picker of either surface; return (start, reason)."""
    if surface == "setup":
        page.goto(f"{base_url}/setup/")
        page.locator("#provider").select_option("deepseek")
        page.locator("#apiKey").fill("sk-e2e-test")
        page.locator("#saveLlm").click()
        page.wait_for_selector('[data-panel="1"].active')
        page.locator("#next1").click()
        page.wait_for_selector('[data-panel="2"].active')
        return page.locator("#startInit"), page.locator("#initReason")
    page.goto(f"{base_url}/web/")
    page.wait_for_selector(".init-onboarding", state="attached")
    return page.locator('[data-init-action="start"]'), page.locator(".init-reason")


def _select_bangumi_only(page: Any) -> None:
    page.locator('input[data-init-source="bilibili"]').uncheck()
    page.locator('input[data-init-source="bangumi"]').check()


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_bangumi_only_without_username_still_reaches_backend(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    """Regression: a client-side Bangumi-only guard blocked /api/init entirely.

    Every GUI surface used to refuse to POST when Bangumi was the only source
    and neither a username nor a token was typed — the two covered here plus
    the extension popup, which carried the same block under a different
    variable name (``selectedSources``), so a narrow grep made it look like an
    unaffected control. That copy of the backend's admission rule predated the
    third tier of the account ladder — the identity the browser extension
    reports from a logged-in bgm.tv page — so zero-config users (the
    recommended path) could not start an init from any GUI at all. The
    frontend must hand the decision to the backend.
    """
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    start, _ = _open_init_sources(chromium_page, base_url, surface)
    _select_bangumi_only(chromium_page)
    start.click()

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    assert stub.init_posts == [
        {
            "sources": ["bangumi"],
            "llm_concurrency": 3,
            "init_timeout_minutes": 60,
        }
    ]


@pytest.mark.parametrize("surface", ["setup", "desktop"])
def test_web_e2e_bangumi_only_renders_backend_rejection_naming_the_extension(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
    surface: str,
) -> None:
    """With all three tiers genuinely empty, the backend 409 is what shows.

    The rejection copy must name the extension tier too, otherwise deleting
    the frontend guard just moves the same misleading "填令牌或用户名" text onto a
    different code path.
    """
    base_url, stub = guided_init_server
    stub.post_init_error = (
        409,
        {
            "error": "no_profile_signal_sources",
            "detail": (
                "只选择 Bangumi 初始化时，需提供个人令牌（推荐，自动识别当前用户）、"
                "公开用户名，或先在浏览器登录 bgm.tv 让扩展自动识别。"
            ),
        },
    )
    _install_fake_runtime_stream(chromium_page)

    start, reason = _open_init_sources(chromium_page, base_url, surface)
    _select_bangumi_only(chromium_page)
    start.click()

    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    assert stub.init_posts == [
        {
            "sources": ["bangumi"],
            "llm_concurrency": 3,
            "init_timeout_minutes": 60,
        }
    ]
    chromium_page.wait_for_function("() => document.body.innerText.includes('bgm.tv')")
    text = reason.inner_text()
    assert "Bangumi" in text
    assert "个人令牌" in text
    # The extension tier is named, not just token / username.
    assert "bgm.tv" in text
    assert start.is_enabled()
