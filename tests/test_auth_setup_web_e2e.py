"""Real browser/HTTP regression for remote setup and password login.

The backend uses temporary storage and the real auth routes. Only local-peer
trust is disabled so a loopback browser exercises the remote access contract.
No model or platform network requests are made.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from .test_api_auth import _build_app

playwright_api = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/web/", "/m/"])
def test_remote_setup_then_real_password_login(tmp_path, monkeypatch, path):
    app, db = _build_app(tmp_path, monkeypatch, enabled=False)
    app.state.auth_gate.is_trusted_local = lambda request: False
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "temporary HTTP server did not start"
            time.sleep(0.02)
        with playwright_api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(base + path)
                retry = page.get_by_role("button", name="已设置，重新检查")
                playwright_api.expect(retry).to_be_visible()
                form = page.locator("#authOverlay" if path == "/web/" else ".login-card")
                assert form.locator('input[type="password"]').count() == 0
                denied = page.request.get(base + "/api/favorites/BV1AUTH")
                assert denied.status == 401
                assert denied.json()["error"] == "auth_setup_required"

                # Equivalent to enabling the gate locally; real login still
                # validates the stored scrypt hash, same-origin and session.
                app.state.auth_gate.auth.enabled = True
                retry.click()
                # Password inputs do not have the implicit textbox role.
                password = form.locator('input[type="password"]')
                playwright_api.expect(password).to_be_visible()
                password.fill("hunter2")
                with page.expect_response("**/api/auth/login") as response:
                    page.get_by_role("button", name="登录", exact=True).click()
                assert response.value.status == 200
                status = page.request.get(base + "/api/auth/status").json()
                assert status["authenticated"] is True
                assert page.request.get(base + "/api/favorites/BV1AUTH").status == 200
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=10)
        sock.close()
        db.close()
