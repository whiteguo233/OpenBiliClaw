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


def _status(
    *,
    initialized: bool = False,
    running: bool = False,
    current_stage: int = 0,
    can_start: bool = True,
    reason: str = "none",
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "initialized": initialized,
        "running": running,
        "run_id": "test-run",
        "sequence": current_stage,
        "current_stage": current_stage,
        "total_stages": 4,
        "stages": stages
        or [
            {"n": 1, "label": "拉取数据", "status": "pending", "reason": None},
            {"n": 2, "label": "分析偏好", "status": "pending", "reason": None},
            {"n": 3, "label": "生成画像", "status": "pending", "reason": None},
            {"n": 4, "label": "发现内容池", "status": "pending", "reason": None},
        ],
        "partial_success": False,
        "can_start": can_start,
        "can_manage": True,
        "prerequisites": {
            "bilibili_logged_in": True,
            "bilibili_check": "ok",
            "llm_ready": True,
            "embedding_ready": True,
            "enabled_platforms": ["bilibili", "youtube"],
        },
        "reason": reason,
        "detail": "",
    }


class GuidedInitStub:
    def __init__(self) -> None:
        self.init_posts: list[dict[str, Any]] = []
        self.current_status = _status()
        self.post_init_error: tuple[int, dict[str, Any]] | None = None
        self.fail_next_status = False
        self.runtime_status = {
            "initialized": False,
            "pool_available_count": 0,
            "pool_size": 0,
            "pool_refresh_state": "idle",
            "pool_source_shares": {"bilibili": 1.0},
            "configured_sources": {"bilibili": {"enabled": True}},
            "unread_count": 0,
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
                {"n": 3, "label": "生成画像", "status": "pending", "reason": None},
                {"n": 4, "label": "发现内容池", "status": "pending", "reason": None},
            ],
        )

    def set_initialized(self) -> None:
        self.current_status = _status(
            initialized=True,
            stages=[
                {"n": 1, "label": "拉取数据", "status": "ok", "reason": None},
                {"n": 2, "label": "分析偏好", "status": "ok", "reason": None},
                {"n": 3, "label": "生成画像", "status": "ok", "reason": None},
                {"n": 4, "label": "发现内容池", "status": "ok", "reason": None},
            ],
        )

    def set_bilibili_blocked(self) -> None:
        self.current_status = _status(
            can_start=False,
            reason="bilibili_not_logged_in",
        )
        self.current_status["prerequisites"]["bilibili_logged_in"] = False
        self.current_status["prerequisites"]["bilibili_check"] = "failed"


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
                return _json_response(
                    self,
                    {
                        "config": {
                            "llm": {"default_provider": "ollama", "ollama": {}},
                            "bilibili": {"cookie": "SESSDATA=test"},
                            "sources": {
                                "bilibili": {"enabled": True},
                                "youtube": {"enabled": True},
                            },
                        }
                    },
                )
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
            return _json_response(self, {"ok": True})

        def do_PUT(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/config":
                return _json_response(self, {"ok": True, "config": {}})
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


def test_setup_wizard_e2e_starts_guided_init_and_finishes_on_runtime_event(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("ollama")
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
    assert stub.init_posts == [{"sources": ["bilibili", "youtube"]}]
    socket_url = chromium_page.evaluate("() => window.__obcSockets[0].url")
    assert socket_url.endswith("/api/runtime-stream")

    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_progress" })""")
    chromium_page.wait_for_function(
        "() => document.querySelector('#initProgressLabel')?.innerText.includes('1/4')"
    )
    stub.set_initialized()
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('[data-panel="3"].active')
    assert "首轮初始化" in chromium_page.locator('[data-panel="3"]').inner_text()


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

    assert stub.init_posts == [{"sources": ["bilibili", "youtube"]}]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-progress')?.innerText.includes('1/4')"
    )
    assert "✗" not in chromium_page.locator(".init-checklist").inner_text()
    fill_width = chromium_page.locator(".init-progress-fill").evaluate(
        "el => Number.parseFloat(el.style.width)"
    )
    assert fill_width > 0
    stub.set_initialized()
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('.init-onboarding[data-init-phase="completed"]')


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
    chromium_page.locator("#provider").select_option("ollama")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    chromium_page.wait_for_selector('[data-panel="3"].active')


def test_setup_wizard_e2e_default_watchdog_polls_when_runtime_stream_is_silent(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("ollama")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_function("() => window.__obcSockets.length === 1")
    chromium_page.wait_for_function("() => window.__obcInitPosted === true")
    stub.set_initialized()
    chromium_page.wait_for_selector('[data-panel="3"].active', timeout=30000)


def test_setup_wizard_e2e_blocks_missing_bilibili_without_post(
    guided_init_server: tuple[str, GuidedInitStub],
    chromium_page: Any,
) -> None:
    base_url, stub = guided_init_server
    stub.set_bilibili_blocked()
    _install_fake_runtime_stream(chromium_page)

    chromium_page.goto(f"{base_url}/setup/")
    chromium_page.locator("#provider").select_option("ollama")
    chromium_page.locator("#saveLlm").click()
    chromium_page.wait_for_selector('[data-panel="1"].active')
    chromium_page.locator("#next1").click()
    chromium_page.wait_for_selector('[data-panel="2"].active')
    chromium_page.locator("#startInit").click()

    chromium_page.wait_for_selector("#initReason.msg.show")
    assert stub.init_posts == []
    assert "还没检测到 B站 登录" in chromium_page.locator("#initReason").inner_text()
    assert "✗" in chromium_page.locator("#initChecklist").inner_text()


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

    assert stub.init_posts == [{"sources": ["bilibili"]}]
    chromium_page.wait_for_function(
        "() => document.querySelector('.init-reason')?.innerText.includes('初始化正在进行中')"
    )
    assert chromium_page.locator('[data-init-action="start"]').is_enabled()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("bilibili_not_logged_in", "还没检测到 B 站登录"),
        ("llm_not_ready", "AI 服务还没配好"),
        ("unsupported_runtime", "当前运行环境不支持"),
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

    assert stub.init_posts == [{"sources": ["bilibili"]}]
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
    chromium_page.evaluate("""() => window.__emitRuntimeEvent({ type: "init_completed" })""")
    chromium_page.wait_for_selector('.init-onboarding[data-init-phase="completed"]')
