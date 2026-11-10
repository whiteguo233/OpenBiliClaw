"""Real browser/HTTP regression for remote setup and password login.

The backend uses temporary storage and the real auth routes. Only local-peer
trust is disabled so a loopback browser exercises the remote access contract.
No model or platform network requests are made.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from openbiliclaw.proc import backend_child_env
from openbiliclaw.recommendation_runtime import (
    RECOMMENDATION_LOCAL_HEADER,
    RECOMMENDATION_LOCAL_TOKEN_ENV,
    RECOMMENDATION_PORT_ENV,
    RECOMMENDATION_SOCK_ENV,
)

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


@pytest.mark.skipif(os.name != "posix", reason="requires Unix socket transport")
def test_fresh_local_desktop_with_recommendation_worker(tmp_path, monkeypatch):
    """A fresh install on POSIX reads recommendations without a password."""
    with tempfile.TemporaryDirectory(prefix="obc-", dir="/tmp") as socket_dir:
        socket_path = os.path.join(socket_dir, "rec.sock")
        monkeypatch.delenv(RECOMMENDATION_PORT_ENV, raising=False)
        monkeypatch.setenv(RECOMMENDATION_SOCK_ENV, socket_path)
        monkeypatch.delenv("OPENBILICLAW_RECOMMENDATION_ONLY", raising=False)
        app, db = _build_app(tmp_path, monkeypatch, enabled=False, password=None)
        child_env = backend_child_env(app, OPENBILICLAW_RECOMMENDATION_ONLY="1")
        with monkeypatch.context() as child:
            child.setenv("OPENBILICLAW_RECOMMENDATION_ONLY", "1")
            if RECOMMENDATION_LOCAL_TOKEN_ENV in child_env:
                child.setenv(
                    RECOMMENDATION_LOCAL_TOKEN_ENV, child_env[RECOMMENDATION_LOCAL_TOKEN_ENV]
                )
            child_app, child_db = _build_app(tmp_path, monkeypatch, enabled=False, password=None)

        with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as uds:
            tcp.bind(("127.0.0.1", 0))
            tcp.listen()
            uds.bind(socket_path)
            uds.listen()
            base = f"http://127.0.0.1:{tcp.getsockname()[1]}"
            servers = [
                uvicorn.Server(uvicorn.Config(service, lifespan="off", log_level="critical"))
                for service in (child_app, app)
            ]
            threads = [
                threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
                for server, sock in zip(servers, (uds, tcp), strict=True)
            ]
            try:
                for thread in threads:
                    thread.start()
                deadline = time.monotonic() + 10
                while not all(server.started for server in servers):
                    assert time.monotonic() < deadline, "temporary HTTP servers did not start"
                    time.sleep(0.02)

                with playwright_api.sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        status = page.request.get(base + "/api/auth/status").json()
                        assert (status["enabled"], status["authenticated"]) == (False, True)
                        with page.expect_response("**/api/recommendations") as response:
                            page.goto(base + "/web/")
                        result = response.value
                        assert result.ok, result.json()
                        playwright_api.expect(page.locator("#authOverlay")).to_have_count(0)

                        # A downstream auth error must not lock either shell
                        # when the main backend still confirms local access.
                        page.add_init_script(
                            """(() => {
                                let checks = 0;
                                const realFetch = window.fetch;
                                window.fetch = async (...args) => {
                                    const result = await realFetch(...args);
                                    if (String(args[0]).endsWith('/api/auth/status')) {
                                        const json = result.json.bind(result);
                                        result.json = async () => {
                                            const body = await json();
                                            if (++checks === 2) requestAnimationFrame(() => {
                                                document.body.dataset.authRechecked = 'yes';
                                            });
                                            return body;
                                        };
                                    }
                                    return result;
                                };
                            })();"""
                        )
                        for path, selector in [("/web/", "#authOverlay"), ("/m/", ".login-card")]:
                            page.route(
                                "**/api/recommendations",
                                lambda route: route.fulfill(
                                    status=401, json={"error": "auth_setup_required"}
                                ),
                            )
                            page.goto(base + path)
                            playwright_api.expect(page.locator("body")).to_have_attribute(
                                "data-auth-rechecked", "yes"
                            )
                            playwright_api.expect(page.locator(selector)).to_have_count(0)
                            page.unroute("**/api/recommendations")
                    finally:
                        browser.close()

                with httpx.Client(transport=httpx.HTTPTransport(uds=socket_path)) as direct:
                    # The socket transport alone does not imply trust.
                    denied = direct.get(base + "/api/recommendations")
                    assert denied.status_code == 401
                    # The proof has no authority over any other route.
                    denied = direct.get(
                        base + "/api/favorites/BV1AUTH",
                        headers={
                            RECOMMENDATION_LOCAL_HEADER: child_env[RECOMMENDATION_LOCAL_TOKEN_ENV]
                        },
                    )
                    assert denied.status_code == 401

                # Even an exact internal header value is not accepted at the public ingress.
                remote = TestClient(app, client=("192.168.1.50", 5000), base_url=base)
                try:
                    denied = remote.get(
                        "/api/recommendations",
                        headers={
                            RECOMMENDATION_LOCAL_HEADER: child_env[RECOMMENDATION_LOCAL_TOKEN_ENV]
                        },
                    )
                    assert denied.status_code == 401
                    assert denied.json()["error"] == "auth_setup_required"
                finally:
                    remote.close()
            finally:
                for server in servers:
                    server.should_exit = True
                for thread in threads:
                    thread.join(timeout=10)
                child_db.close()
                db.close()
