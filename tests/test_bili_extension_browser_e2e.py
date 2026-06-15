"""Opt-in real-browser E2E for Bili extension search fallback.

Run:

    BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s

The test uses a temporary FastAPI app and SQLite database. It does not touch the
production database and does not require a production debug endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from openbiliclaw.api.app import create_app
from openbiliclaw.bilibili.api import BilibiliAPIClient
from openbiliclaw.runtime.bilibili_producer import BilibiliExtensionSearchProducer
from openbiliclaw.runtime.events import RuntimeEventHub
from openbiliclaw.sources.bili_tasks import BiliTaskQueue
from openbiliclaw.storage.database import Database

_E2E_ENABLED = os.environ.get("BILI_EXTENSION_E2E", "") == "1"

pytestmark = pytest.mark.skipif(
    not _E2E_ENABLED,
    reason="BILI_EXTENSION_E2E=1 not set; skipping real browser Bili E2E",
)


class _FakeMemoryManager:
    def initialize(self) -> None:
        return None


class _FakeSoulEngine:
    def is_profile_ready(self) -> bool:
        return True

    async def get_profile(self) -> object:
        return object()


class _FakeLLM:
    async def complete_structured_task(self, **_kwargs: Any) -> Any:
        raise AssertionError("E2E passes explicit keywords; LLM should not be called")


class _NeverFullPipeline:
    def pool_full(self) -> bool:
        return False


def find_free_port() -> int:
    """Return an available loopback TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_chrome_executable(explicit_path: str | None = None) -> Path:
    """Resolve a Chrome/Chromium executable suitable for loading MV3 extensions."""

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Chrome executable not found: {path}")
        return path

    env_path = os.environ.get("BILI_EXTENSION_E2E_CHROME", "").strip()
    if env_path:
        return resolve_chrome_executable(env_path)

    candidates: list[Path] = []
    candidates.extend(
        sorted(
            Path.home().glob(
                "Library/Caches/ms-playwright/chromium-*/chrome-mac-x64/"
                "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            ),
            reverse=True,
        )
    )
    candidates.extend(
        [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    which = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "Chrome executable not found; set BILI_EXTENSION_E2E_CHROME=/path/to/chrome"
    )


def choose_bili_service_worker_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the OpenBiliClaw MV3 service worker target from CDP /json/list."""

    for target in targets:
        if target.get("type") != "service_worker":
            continue
        url = str(target.get("url", ""))
        if url.startswith("chrome-extension://") and url.endswith(
            "/dist/background/service-worker.js"
        ):
            return target
    raise RuntimeError("OpenBiliClaw extension service worker target not found")


def is_bili_extension_e2e_candidate(row: dict[str, Any], *, bvids: set[str]) -> bool:
    """Return whether a candidate row belongs to this harness' cleanup scope."""

    return (
        str(row.get("source_platform", "")) == "bilibili"
        and str(row.get("source_strategy", "")) == "bili-extension-search"
        and str(row.get("bvid", "")) in bvids
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _wait_for_backend(base_url: str, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with suppress(Exception):
            response = httpx.get(f"{base_url}/api/ping", timeout=2.0, trust_env=False)
            if response.status_code == 200:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Backend did not become ready: {base_url}")


class _ServerHandle:
    def __init__(self, server: uvicorn.Server, thread: threading.Thread, base_url: str) -> None:
        self.server = server
        self.thread = thread
        self.base_url = base_url

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _start_backend(database: Database) -> tuple[_ServerHandle, Any]:
    port = find_free_port()
    event_hub = RuntimeEventHub()
    app = create_app(
        database=database,
        memory_manager=_FakeMemoryManager(),
        soul_engine=_FakeSoulEngine(),
        runtime_event_hub=event_hub,
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_backend(base_url)
    return _ServerHandle(server, thread, base_url), app.state.runtime_context


class _PlaywrightExtensionHandle:
    def __init__(
        self,
        process: subprocess.Popen[str],
        user_data_dir: tempfile.TemporaryDirectory[str],
        done_file: Path,
    ) -> None:
        self.process = process
        self.user_data_dir = user_data_dir
        self.done_file = done_file

    def close(self) -> None:
        with suppress(Exception):
            self.done_file.write_text("done", encoding="utf-8")
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            with suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self.user_data_dir.cleanup()


def _resolve_node_playwright_path() -> Path:
    explicit = os.environ.get("BILI_EXTENSION_E2E_NODE_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not (path / "playwright" / "package.json").exists():
            raise FileNotFoundError(f"Playwright NODE_PATH is invalid: {path}")
        return path

    candidates = sorted(Path.home().glob(".npm/_npx/*/node_modules/playwright/package.json"))
    if candidates:
        return candidates[-1].parents[1]
    raise FileNotFoundError(
        "Node Playwright package not found; run the playwright CLI once or set "
        "BILI_EXTENSION_E2E_NODE_PATH=/path/to/node_modules"
    )


def _start_playwright_extension(*, host: str, port: int) -> _PlaywrightExtensionHandle:
    user_data_dir = tempfile.TemporaryDirectory(prefix="openbiliclaw-bili-pw-e2e-")
    done_file = Path(user_data_dir.name) / "done"
    extension_path = _repo_root() / "extension"
    chrome_path = resolve_chrome_executable()
    node_path = _resolve_node_playwright_path()
    script = r"""
    (async () => {
      const { chromium } = require("playwright");
      const fs = require("fs");
      const [extensionPath, executablePath, userDataDir, doneFile, host, rawPort] =
        process.argv.slice(1);
      const port = Number(rawPort);
      const context = await chromium.launchPersistentContext(userDataDir, {
        headless: false,
        executablePath,
        args: [
          `--disable-extensions-except=${extensionPath}`,
          `--load-extension=${extensionPath}`,
          "--no-first-run",
          "--no-default-browser-check",
        ],
      });
      let sw = context.serviceWorkers().find((worker) =>
        worker.url().includes("/dist/background/service-worker.js")
      );
      if (!sw) {
        sw = await context.waitForEvent("serviceworker", { timeout: 15000 });
      }
      const setup = await sw.evaluate(async ({ host, port }) => {
        await chrome.storage.local.set({ popup_backend_endpoint: { host, port } });
        const ctrl = new AbortController();
        setTimeout(() => ctrl.abort("timeout"), 8000);
        const response = await fetch(`http://${host}:${port}/api/ping`, {
          signal: ctrl.signal,
        });
        return {
          extension_id: chrome.runtime.id,
          endpoint: await chrome.storage.local.get("popup_backend_endpoint"),
          ping_status: response.status,
          ping_text: await response.text(),
        };
      }, { host, port });
      console.log(JSON.stringify({ ready: true, setup }));
      while (!fs.existsSync(doneFile)) {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      await context.close();
    })().catch((error) => {
      console.error(JSON.stringify({
        ready: false,
        error: String(error),
        stack: error && error.stack,
      }));
      process.exit(1);
    });
    """
    env = {**os.environ, "NODE_PATH": str(node_path)}
    process = subprocess.Popen(
        [
            "node",
            "-e",
            script,
            str(extension_path),
            str(chrome_path),
            user_data_dir.name,
            str(done_file),
            host,
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 30.0
    line = ""
    while time.monotonic() < deadline:
        line = process.stdout.readline().strip()
        if not line:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise RuntimeError(f"Playwright extension process exited: {stderr}")
            continue
        payload = json.loads(line)
        if payload.get("ready") is True:
            setup = payload.get("setup")
            if not isinstance(setup, dict) or setup.get("ping_status") != 200:
                raise RuntimeError(f"Extension could not reach backend: {payload!r}")
            print(f"extension setup: {setup}")
            return _PlaywrightExtensionHandle(process, user_data_dir, done_file)
        raise RuntimeError(f"Playwright extension failed: {payload!r}")
    raise RuntimeError(f"Timed out waiting for Playwright extension setup: {line}")


def _wait_for_presence(ctx: Any, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = dict(ctx.presence.snapshot())
        if int(snapshot.get("active_count") or 0) > 0:
            return snapshot
        time.sleep(0.2)
    raise RuntimeError(f"Extension presence did not become active: {snapshot!r}")


def _task_row(database: Database, task_id: str) -> dict[str, Any]:
    row = database.conn.execute("SELECT * FROM bili_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise AssertionError(f"Task not found: {task_id}")
    return dict(row)


def _wait_for_task_completed(
    database: Database, task_id: str, *, timeout_seconds: float = 90.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    row: dict[str, Any] = {}
    while time.monotonic() < deadline:
        row = _task_row(database, task_id)
        if row.get("status") in {"completed", "failed"}:
            return row
        time.sleep(1.0)
    raise AssertionError(f"Task did not finish: {row!r}")


def _cleanup_bili_cooldown() -> None:
    BilibiliAPIClient._search_cooldown_until = 0.0
    BilibiliAPIClient._search_cooldown_level = 0
    BilibiliAPIClient._search_voucher_block_streak = 0


@pytest.mark.integration
def test_bili_extension_search_producer_to_rendered_dom_result(tmp_path: Path) -> None:
    database = Database(tmp_path / "bili-extension-e2e.db")
    database.initialize()
    server, ctx = _start_backend(database)
    browser: _PlaywrightExtensionHandle | None = None
    bili_client = BilibiliAPIClient()
    task_id = ""

    try:
        endpoint = server.base_url.removeprefix("http://")
        host, raw_port = endpoint.rsplit(":", 1)
        browser = _start_playwright_extension(host=host, port=int(raw_port))
        presence = _wait_for_presence(ctx)
        print(f"presence: {presence}")

        BilibiliAPIClient._search_cooldown_until = time.monotonic() + 180.0
        BilibiliAPIClient._search_cooldown_level = 1

        async def kick() -> None:
            await ctx.event_hub.publish({"type": "bili_task_available", "source": "e2e"})

        producer = BilibiliExtensionSearchProducer(
            task_queue=BiliTaskQueue(database),
            soul_engine=_FakeSoulEngine(),
            llm_service=_FakeLLM(),
            bilibili_client=bili_client,
            presence=ctx.presence,
            min_interval_minutes=0,
            keywords_per_cycle=1,
            page_size=3,
            candidate_pipeline=_NeverFullPipeline(),
            kick=kick,
        )

        result = asyncio.run(producer.produce_if_due(keywords=["机械键盘 声音"], limit=1))
        assert result == {"enqueued": 1, "attempted": 1, "reason": "ok"}
        row = database.conn.execute(
            "SELECT id FROM bili_tasks WHERE type = 'search' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        task_id = str(row["id"])

        final_row = _wait_for_task_completed(database, task_id)
        assert final_row["status"] == "completed", final_row
        payload = json.loads(str(final_row["result_json"]))
        videos = payload.get("videos")
        assert isinstance(videos, list)
        assert videos, payload
        assert str(videos[0].get("bvid", "")).startswith("BV")
        assert str(videos[0].get("title", "")).strip()
        print(f"completed task={task_id} videos={len(videos)} first={videos[0]}")
    finally:
        with suppress(Exception):
            asyncio.run(bili_client.close())
        _cleanup_bili_cooldown()
        if browser is not None:
            browser.close()
        server.close()
