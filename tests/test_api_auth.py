"""Integration tests for the LAN password gate (middleware + /api/auth/*)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from openbiliclaw import auth_core as ac
from openbiliclaw.api.app import create_app
from openbiliclaw.api.auth import _CSRF_GET_EXACT
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_SECRET = "fixed-test-session-secret-aaaaaaaaaaaa"
_ORIGIN = "http://testserver"  # TestClient default Host/Origin


def _build_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    password: str | None = "hunter2",
    password_hash: str | None = None,
    session_secret: str = _SECRET,
    session_ttl_hours: int = 0,
    trust_loopback: bool = True,
    trusted_proxies: list[str] | None = None,
    allowed_bearer_origins: list[str] | None = None,
    extension_access_enabled: bool = False,
    extension_access_keys: list[str] | None = None,
    extension_token_ttl_hours: int = 24,
    runtime_event_hub: object | None = None,
    env_password: str | None = None,
) -> tuple[object, Database]:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    if env_password is not None:
        monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", env_password)
    cfg = Config()
    cfg.scheduler.enabled = False
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    cfg.api.auth.enabled = enabled
    cfg.api.auth.session_secret = session_secret
    cfg.api.auth.session_ttl_hours = session_ttl_hours
    cfg.api.auth.trust_loopback = trust_loopback
    cfg.api.auth.trusted_proxies = trusted_proxies or []
    cfg.api.auth.allowed_bearer_origins = allowed_bearer_origins or []
    cfg.api.auth.extension_access_enabled = extension_access_enabled
    cfg.api.auth.extension_access_keys = extension_access_keys or []
    cfg.api.auth.extension_token_ttl_hours = extension_token_ttl_hours
    if password_hash is not None:
        cfg.api.auth.password_hash = password_hash
    elif password is not None and env_password is None:
        cfg.api.auth.password_hash = ac.hash_password(password)
    save_config(cfg, project_root / "config.toml")

    db = Database(tmp_path / "auth.db")
    db.initialize()
    db.cache_content(
        "BV1AUTH",
        title="t",
        up_name="u",
        source="test",
        source_platform="bilibili",
        content_url="https://www.bilibili.com/video/BV1AUTH",
    )
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
        runtime_event_hub=runtime_event_hub,
    )
    return app, db


def _remote(app: object) -> TestClient:
    return TestClient(app, client=("192.168.1.50", 5000))


_LOOPBACK_ORIGIN = "http://127.0.0.1:8420"


def _loopback(app: object) -> TestClient:
    # base_url gives a loopback Host header so the (DNS-rebind-hardened) local
    # bypass applies — a real local client connects to 127.0.0.1, not some name.
    return TestClient(app, client=("127.0.0.1", 5000), base_url=_LOOPBACK_ORIGIN)


# ── basic gate behaviour ────────────────────────────────────────────────────


def test_status_reports_enabled_and_unauthenticated_for_remote(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    r = _remote(app).get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["authenticated"] is False
    assert body["trust_loopback"] is True
    assert body["can_manage"] is False  # remote can't manage the gate


def test_remote_without_token_is_blocked(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    r = _remote(app).get("/api/favorites/BV1AUTH")
    assert r.status_code == 401
    assert r.json() == {"error": "auth_required"}


def test_remote_saved_sync_mutations_require_authentication(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    remote = _remote(app)

    for path, payload in (
        (
            "/api/saved/favorite",
            {"source_platform": "bilibili", "content_id": "BV1AUTH"},
        ),
        ("/api/saved/favorite/remove", {"item_key": "bilibili:BV1AUTH"}),
        ("/api/saved/favorite/sync", {"item_keys": ["bilibili:BV1AUTH"]}),
    ):
        response = remote.post(path, json=payload)
        assert response.status_code == 401, path
        assert response.json() == {"error": "auth_required"}


def test_remote_source_result_and_kick_routes_require_authentication(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    remote = _remote(app)

    for slug in ("xhs", "dy", "yt", "x", "zhihu", "reddit", "linuxdo"):
        for suffix in ("task-result", "kick"):
            response = remote.post(f"/api/sources/{slug}/{suffix}", json={})
            assert response.status_code == 401, (slug, suffix)
            assert response.json() == {"error": "auth_required"}


def test_loopback_bypasses_gate(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    r = _loopback(app).get("/api/favorites/BV1AUTH")
    assert r.status_code == 200
    assert _loopback(app).get("/api/auth/status").json()["authenticated"] is True


def test_loopback_bypass_denied_for_cross_origin_web_page(tmp_path, monkeypatch) -> None:
    # localhost CSRF / confused-deputy: a malicious web page can drive the
    # browser to hit http://127.0.0.1, arriving from a loopback peer. The
    # loopback bypass must NOT apply to such cross-origin browser requests.
    app, _ = _build_app(tmp_path, monkeypatch)
    evil = {"origin": "http://evil.example"}
    # gated data endpoint: cross-origin from loopback → still requires auth
    assert _loopback(app).get("/api/favorites/BV1AUTH", headers=evil).status_code == 401
    # config must not be readable by a cross-origin page via the loopback bypass
    assert _loopback(app).get("/api/config", headers=evil).status_code == 401
    # status reflects unauthenticated for the cross-origin caller
    assert _loopback(app).get("/api/auth/status", headers=evil).json()["authenticated"] is False
    # state-changing POST likewise blocked
    assert (
        _loopback(app).post("/api/favorites", json={"bvid": "BV1AUTH"}, headers=evil).status_code
        == 401
    )


def test_loopback_bypass_allowed_for_local_ui_and_extension(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    # same-origin local web UI (Origin == loopback Host) → bypass
    assert (
        _loopback(app)
        .get("/api/favorites/BV1AUTH", headers={"origin": _LOOPBACK_ORIGIN})
        .status_code
        == 200
    )
    # browser extension origin → bypass (the primary local client)
    assert (
        _loopback(app)
        .get("/api/favorites/BV1AUTH", headers={"origin": "chrome-extension://abcdef"})
        .status_code
        == 200
    )
    # no Origin (CLI / curl / non-browser) → bypass
    assert _loopback(app).get("/api/favorites/BV1AUTH").status_code == 200


def test_loopback_bypass_denied_for_cross_site_subresource(tmp_path, monkeypatch) -> None:
    # A no-cors cross-site subresource (<img src="http://127.0.0.1:8420/api/...">)
    # omits Origin and hits a canonical loopback Host, but Fetch Metadata marks it
    # cross-site → must NOT inherit the local bypass (would otherwise claim a task).
    app, _ = _build_app(tmp_path, monkeypatch)
    attack = {
        "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "image",
    }
    for path in (
        "/api/sources/xhs/next-task",
        "/api/sources/dy/next-task",
        "/api/sources/yt/next-task",
        "/api/sources/v2ex/next-task",
        "/api/favorites/BV1AUTH",
    ):
        assert _loopback(app).get(path, headers=attack).status_code == 401, path
    # a genuine same-origin browser request (Sec-Fetch-Site: same-origin) still bypasses
    assert (
        _loopback(app)
        .get("/api/favorites/BV1AUTH", headers={"sec-fetch-site": "same-origin"})
        .status_code
        == 200
    )


def test_loopback_bypass_denied_for_dns_rebinding_host(tmp_path, monkeypatch) -> None:
    # A page served from http://evil.example:8420 rebound to 127.0.0.1 reaches
    # the backend from a loopback peer but with an attacker-controlled Host. The
    # no-Origin and same-origin exemptions must NOT apply (canonical-host check).
    app, _ = _build_app(tmp_path, monkeypatch)
    evil_host = {"host": "evil.example:8420"}
    evil_same = {"host": "evil.example:8420", "origin": "http://evil.example:8420"}
    assert _loopback(app).get("/api/favorites/BV1AUTH", headers=evil_host).status_code == 401
    assert _loopback(app).get("/api/favorites/BV1AUTH", headers=evil_same).status_code == 401
    assert _loopback(app).get("/api/config", headers=evil_same).status_code == 401
    assert (
        _loopback(app).get("/api/auth/status", headers=evil_same).json()["authenticated"] is False
    )


def test_disabled_gate_allows_everything(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    assert _remote(app).get("/api/favorites/BV1AUTH").status_code == 200
    assert _remote(app).get("/api/auth/status").json()["enabled"] is False


def test_health_stays_public_when_enabled(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    assert _remote(app).get("/api/health").status_code == 200


def test_qr_info_stays_public_when_enabled(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    response = _remote(app).get("/api/qr-info")
    assert response.status_code == 200
    assert "lan_ip" in response.json()


# ── login (cookie mode, no token in body) ───────────────────────────────────


def test_login_cookie_mode_sets_cookie_without_body_token(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    r = client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "token" not in r.json()
    assert ac.COOKIE_NAME in r.cookies or ac.COOKIE_NAME in client.cookies
    # subsequent gated GET now authorized via the cookie jar
    assert client.get("/api/favorites/BV1AUTH").status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_login_wrong_password_401_then_rate_limited(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    # 6th attempt is locked out
    assert client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 429


def test_never_expire_login_never_returns_body_token_even_cross_attempt(
    tmp_path, monkeypatch
) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, session_ttl_hours=0)
    # same-origin always cookie-only
    r = _remote(app).post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN}
    )
    assert "token" not in r.json()


# ── CSRF ────────────────────────────────────────────────────────────────────


def test_cookie_unsafe_method_requires_csrf(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    # POST with cookie but no Origin/X-OBC-Auth -> CSRF 403
    r = client.post("/api/favorites", json={"bvid": "BV1AUTH"})
    assert r.status_code == 403
    assert r.json() == {"error": "csrf"}
    # with Origin==Host + custom header -> allowed
    ok = client.post(
        "/api/favorites",
        json={"bvid": "BV1AUTH"},
        headers={"origin": _ORIGIN, "x-obc-auth": "1"},
    )
    assert ok.status_code == 200


def _claim_route_paths(app: object) -> list[str]:
    """Registered claim GETs (``*/next-task``), derived from the app itself.

    Reading the routes instead of hand-copying a list keeps this regression
    honest when a source is added later: a new ``next-task`` route appears here
    automatically and must already be covered by the CSRF set.
    """
    return sorted(
        path
        for path in (str(getattr(route, "path", "")) for route in getattr(app, "routes", []))
        if path.endswith("/next-task")
    )


def test_csrf_get_set_covers_every_registered_claim_route(tmp_path, monkeypatch) -> None:
    """Set equality between the exact CSRF paths and the live claim routes.

    Every ``next-task`` endpoint claims (pending → in_progress) and is therefore
    write-shaped; a source that registers one without listing it in
    ``_CSRF_GET_EXACT`` would ship an uncovered state change, and a stale entry
    for a removed route would keep a dead path protected. Both directions fail
    here.
    """
    app, _ = _build_app(tmp_path, monkeypatch)

    registered = set(_claim_route_paths(app))
    declared = {path for path in _CSRF_GET_EXACT if path.endswith("/next-task")}

    assert registered, "the app under test registers no claim route"
    assert declared == registered


def test_mutating_get_task_claim_requires_csrf(tmp_path, monkeypatch) -> None:
    # /api/sources/*/next-task are GETs that claim+lock a task, so they
    # must be CSRF-protected for cookie auth (review r2#2). The middleware rejects
    # before the handler runs, so this is deterministic regardless of source state.
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    for path in (
        *_claim_route_paths(app),
        "/api/recommendations",  # serve() bootstrap-writes rows
        "/api/chat/turns/abc123",  # GET resumes a pending turn
    ):
        r = client.get(path)  # cookie, no X-OBC-Auth
        assert r.status_code == 403, path
        assert r.json() == {"error": "csrf"}
    # the SPA sends the header on GET → passes the gate (read-only reads stay
    # uncovered: cookie alone authorizes them)
    ok = client.get("/api/recommendations", headers={"x-obc-auth": "1"})
    assert ok.status_code == 200


def test_cross_origin_cookie_post_blocked(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    r = client.post(
        "/api/favorites",
        json={"bvid": "BV1AUTH"},
        headers={"origin": "http://evil.example", "x-obc-auth": "1"},
    )
    assert r.status_code == 403


# ── logout ──────────────────────────────────────────────────────────────────


def test_plain_logout_is_public_and_clears_cookie(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    # public even without a valid session (so an expired cookie can be cleared)
    r = _remote(app).post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_invalid_cookie_401_clears_cookie(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.cookies.set(ac.COOKIE_NAME, "bogus.token")
    r = client.get("/api/favorites/BV1AUTH")
    assert r.status_code == 401
    # response instructs the browser to drop the stale cookie
    assert "set-cookie" in {k.lower() for k in r.headers}


def test_logout_all_requires_auth_and_revokes_all_tokens(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    # unauthenticated logout-all is rejected by the gate (not whitelisted)
    assert client.post("/api/auth/logout?all=true").status_code == 401

    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert client.get("/api/favorites/BV1AUTH").status_code == 200
    epoch_before = db.get_auth_epoch()
    r = client.post("/api/auth/logout?all=true", headers={"origin": _ORIGIN, "x-obc-auth": "1"})
    assert r.status_code == 200
    assert db.get_auth_epoch() == epoch_before + 1
    # the cookie the client still holds is now revoked
    assert client.get("/api/auth/status").json()["authenticated"] is False


# ── bearer mode (server-decided) ────────────────────────────────────────────


def test_extension_token_exchange_is_disabled_by_default(tmp_path, monkeypatch) -> None:
    _key_id, full_key, _record = ac.generate_extension_access_key()
    app, _ = _build_app(tmp_path, monkeypatch)

    response = _remote(app).post("/api/auth/extension-token", json={"key": full_key})

    assert response.status_code == 403
    assert response.json() == {"error": "extension_access_disabled"}


def test_extension_token_exchange_rejects_when_auth_is_disabled(tmp_path, monkeypatch) -> None:
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        enabled=False,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )

    response = _remote(app).post("/api/auth/extension-token", json={"key": full_key})

    assert response.status_code == 400
    assert response.json() == {"error": "auth_disabled"}


def test_extension_token_exchange_rejects_invalid_device_keys_identically(
    tmp_path, monkeypatch
) -> None:
    key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )
    remote = _remote(app)

    for key in ("not-a-key", full_key.replace(key_id, "f" * 12), full_key + "x"):
        response = remote.post("/api/auth/extension-token", json={"key": key})
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_device_key"}


def test_extension_token_exchange_is_rate_limited_per_client(tmp_path, monkeypatch) -> None:
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )
    remote = _remote(app)

    for _ in range(5):
        assert remote.post("/api/auth/extension-token", json={"key": "invalid"}).status_code == 401
    response = remote.post("/api/auth/extension-token", json={"key": full_key})

    assert response.status_code == 429
    assert response.json() == {"error": "locked"}


def test_extension_token_exchange_issues_finite_session(tmp_path, monkeypatch) -> None:
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
        extension_token_ttl_hours=1,
    )

    response = _remote(app).post("/api/auth/extension-token", json={"key": full_key})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert ac.token_expires_at(body["token"]) is not None
    assert body["expires_at"] == ac.token_expires_at(body["token"])


def test_spoofed_extension_origin_cannot_bypass_device_key(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    response = _remote(app).get(
        "/api/favorites/BV1AUTH",
        headers={"origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "auth_required"}


def test_extension_bearer_header_authorizes_gated_api(tmp_path, monkeypatch) -> None:
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )
    remote = _remote(app)
    token = remote.post("/api/auth/extension-token", json={"key": full_key}).json()["token"]

    response = remote.get(
        "/api/favorites/BV1AUTH",
        headers={
            "origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200


def test_extension_style_bearer_without_origin_authorizes_gated_api(tmp_path, monkeypatch) -> None:
    """Chrome extension GET fetches may omit Origin despite carrying Authorization."""
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )
    remote = _remote(app)
    token = remote.post("/api/auth/extension-token", json={"key": full_key}).json()["token"]

    response = remote.get(
        "/api/favorites/BV1AUTH",
        headers={"authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_login_rejects_extension_origin_even_when_bearer_allowed(tmp_path, monkeypatch) -> None:
    extension_origin = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        session_ttl_hours=24,
        allowed_bearer_origins=[extension_origin],
    )

    response = _remote(app).post(
        "/api/auth/login",
        json={"password": "hunter2"},
        headers={"origin": extension_origin},
    )

    assert response.status_code == 403
    assert response.json() == {"ok": False, "error": "origin_forbidden"}


def test_login_rejects_mixed_case_extension_origin_when_lowercase_allowed(
    tmp_path, monkeypatch
) -> None:
    extension_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        session_ttl_hours=24,
        allowed_bearer_origins=[
            f"chrome-extension://{extension_id}",
            f"moz-extension://{extension_id}",
        ],
    )

    for origin in (
        f"Chrome-Extension://{extension_id}",
        f"mOz-ExTeNsIoN://{extension_id}",
    ):
        response = _remote(app).post(
            "/api/auth/login",
            json={"password": "hunter2"},
            headers={"origin": origin},
        )
        assert response.status_code == 403
        assert response.json() == {"ok": False, "error": "origin_forbidden"}


def test_general_http_query_token_is_rejected(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch)
    token = ac.sign_token(_SECRET, epoch=db.get_auth_epoch(), ttl_hours=1)

    response = _remote(app).get(
        f"/api/favorites/BV1AUTH?token={token}",
        headers={"origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )

    assert response.status_code == 401


def test_image_proxy_query_token_reaches_handler(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch)
    token = ac.sign_token(_SECRET, epoch=db.get_auth_epoch(), ttl_hours=1)

    response = _remote(app).get(f"/api/image-proxy?token={token}")

    assert response.status_code == 422


def test_bearer_mode_requires_allowed_origin_and_ttl(tmp_path, monkeypatch) -> None:
    bearer_origin = "http://desktop.local:3000"
    # ttl=0 + allowed origin -> 400 (never-expire token must not go to JS)
    app0, _ = _build_app(
        tmp_path / "a",
        monkeypatch,
        session_ttl_hours=0,
        allowed_bearer_origins=[bearer_origin],
    )
    r0 = _remote(app0).post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": bearer_origin}
    )
    assert r0.status_code == 400

    # cross-origin not in the allow-list -> 403
    app1, _ = _build_app(tmp_path / "b", monkeypatch, session_ttl_hours=24)
    r1 = _remote(app1).post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": bearer_origin}
    )
    assert r1.status_code == 403

    # allowed origin + ttl>0 -> token returned in body
    app2, _ = _build_app(
        tmp_path / "c",
        monkeypatch,
        session_ttl_hours=24,
        allowed_bearer_origins=[bearer_origin],
    )
    r2 = _remote(app2).post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": bearer_origin}
    )
    assert r2.status_code == 200
    assert r2.json()["token"]
    token = r2.json()["token"]
    # the bearer token authorizes a gated GET from the allowed origin
    ok = _remote(app2).get(
        "/api/favorites/BV1AUTH",
        headers={"origin": bearer_origin, "authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200


# ── secret never leaks via /api/config ──────────────────────────────────────


def test_config_endpoint_never_exposes_auth_secrets(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _loopback(app)  # loopback so the (gated) config read is allowed
    for url in ("/api/config", "/api/config?reveal_keys=true"):
        body = client.get(url).json()
        blob = str(body)
        assert _SECRET not in blob
        assert "password_hash" not in blob
        assert "session_secret" not in blob


# ── proxy / XFF spoofing ────────────────────────────────────────────────────


def test_spoofed_xff_loopback_through_trusted_proxy_is_blocked(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, trusted_proxies=["192.168.1.50"])
    # peer is the trusted proxy; attacker forged a leading 127.0.0.1, real IP appended
    r = _remote(app).get(
        "/api/favorites/BV1AUTH",
        headers={"x-forwarded-for": "127.0.0.1, 203.0.113.9"},
    )
    assert r.status_code == 401  # rightmost-untrusted = remote, not trusted-local


def test_genuine_local_behind_trusted_proxy_is_allowed(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, trusted_proxies=["192.168.1.50"])
    r = _remote(app).get(
        "/api/favorites/BV1AUTH",
        headers={"x-forwarded-for": "127.0.0.1"},
    )
    assert r.status_code == 200


def test_loopback_caddy_hop_preserves_external_https_auth_contract(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client = TestClient(
        proxied,
        client=("127.0.0.1", 5000),
        base_url="http://obc.example.com",
    )
    forwarded = {
        "host": "obc.example.com",
        "origin": "https://obc.example.com",
        "x-forwarded-for": "203.0.113.9",
        "x-forwarded-proto": "https",
    }

    unauthenticated = client.get("/api/favorites/BV1AUTH", headers=forwarded)
    assert unauthenticated.status_code == 401

    response = client.post(
        "/api/auth/login",
        json={"password": "hunter2"},
        headers=forwarded,
    )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


# ── password fingerprint: no false revoke; real change revokes ──────────────


def test_embedded_tailnet_proxy_never_inherits_loopback_auth_bypass(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = TestClient(
        app,
        client=("127.0.0.1", 5000),
        base_url="http://openbiliclaw-host.example.ts.net:8420",
    )
    forwarded = {
        "host": "openbiliclaw-host.example.ts.net:8420",
        "origin": "http://openbiliclaw-host.example.ts.net:8420",
        "x-forwarded-for": "100.100.100.25",
        "x-forwarded-host": "openbiliclaw-host.example.ts.net:8420",
        "x-forwarded-proto": "http",
    }

    unauthenticated = client.get("/api/favorites/BV1AUTH", headers=forwarded)
    assert unauthenticated.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"password": "hunter2"},
        headers=forwarded,
    )
    assert login.status_code == 200
    assert client.get("/api/favorites/BV1AUTH", headers=forwarded).status_code == 200


def test_unchanged_env_password_across_restart_keeps_sessions(tmp_path, monkeypatch) -> None:
    # boot 1: env password -> login
    app1, db = _build_app(tmp_path, monkeypatch, password=None, env_password="envpass")
    c1 = _remote(app1)
    c1.post("/api/auth/login", json={"password": "envpass"}, headers={"origin": _ORIGIN})
    assert c1.get("/api/favorites/BV1AUTH").status_code == 200
    epoch_after_boot1 = db.get_auth_epoch()
    token = c1.cookies.get(ac.COOKIE_NAME)
    assert token

    # boot 2: SAME env password, SAME db (scrypt re-salts the hash) -> must NOT bump epoch
    app2 = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    assert db.get_auth_epoch() == epoch_after_boot1  # no false revoke
    c2 = _remote(app2)
    c2.cookies.set(ac.COOKIE_NAME, token)
    assert c2.get("/api/favorites/BV1AUTH").status_code == 200  # old cookie still valid


def test_password_change_revokes_old_sessions(tmp_path, monkeypatch) -> None:
    app1, db = _build_app(tmp_path, monkeypatch, password=None, env_password="envpass")
    c1 = _remote(app1)
    c1.post("/api/auth/login", json={"password": "envpass"}, headers={"origin": _ORIGIN})
    old_token = c1.cookies.get(ac.COOKIE_NAME)
    epoch_before = db.get_auth_epoch()

    # boot with a DIFFERENT env password, same db -> fingerprint changes -> epoch bumps
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "newpass")
    app2 = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    assert db.get_auth_epoch() == epoch_before + 1
    c2 = _remote(app2)
    c2.cookies.set(ac.COOKIE_NAME, old_token)
    assert c2.get("/api/favorites/BV1AUTH").status_code == 401  # old session revoked


def test_first_enable_does_not_bump_epoch(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch)
    # fresh enable: fingerprint recorded but epoch stays 0
    assert db.get_auth_epoch() == 0


# ── local admin endpoint (settings-page / extension toggle) ─────────────────


def test_admin_enable_disable_from_local(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch, enabled=False, password=None)
    loop = _loopback(app)
    # starts disabled → remote is open
    assert _remote(app).get("/api/favorites/BV1AUTH").status_code == 200
    # local (extension/UI) enables + sets a password
    r = loop.post("/api/auth/admin", json={"enabled": True, "password": "newpw"})
    assert r.status_code == 200 and r.json()["enabled"] is True
    # now remote is gated
    assert _remote(app).get("/api/favorites/BV1AUTH").status_code == 401
    # and the new password actually works for a remote login
    rc = _remote(app)
    assert rc.post(
        "/api/auth/login", json={"password": "newpw"}, headers={"origin": _ORIGIN}
    ).json()["ok"]
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200
    # local disables → remote open again
    assert loop.post("/api/auth/admin", json={"enabled": False}).status_code == 200
    assert _remote(app).get("/api/favorites/BV1AUTH").status_code == 200


def test_admin_change_password_revokes_old_sessions(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)  # enabled, password hunter2
    rc = _remote(app)
    rc.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200
    # local changes the password → existing remote session must be revoked
    assert (
        _loopback(app)
        .post("/api/auth/admin", json={"enabled": True, "password": "changed"})
        .status_code
        == 200
    )
    assert rc.get("/api/favorites/BV1AUTH").status_code == 401


def test_admin_rejected_for_remote_even_authenticated(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    rc = _remote(app)
    rc.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    # an authenticated REMOTE session must not be able to manage the gate
    r = rc.post(
        "/api/auth/admin",
        json={"enabled": False},
        headers={"origin": _ORIGIN, "x-obc-auth": "1"},
    )
    assert r.status_code == 403
    assert r.json()["error"] == "local_only"
    # the gate is still on
    assert _remote(app).get("/api/favorites/BV1AUTH").status_code == 401


def test_admin_enable_requires_password(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False, password=None)
    r = _loopback(app).post("/api/auth/admin", json={"enabled": True})
    assert r.status_code == 400
    assert r.json()["error"] == "password_required"


def test_admin_refused_when_env_managed(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch, password=None, env_password="envpw")
    r = _loopback(app).post("/api/auth/admin", json={"enabled": False})
    assert r.status_code == 409
    assert r.json()["error"] == "env_managed"


def test_status_reports_can_manage(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    assert _loopback(app).get("/api/auth/status").json()["can_manage"] is True
    assert _remote(app).get("/api/auth/status").json()["can_manage"] is False


def test_bearer_origin_is_not_trusted_local_admin(tmp_path, monkeypatch) -> None:
    # An allow-listed bearer origin authenticates via TOKEN, not a no-token local
    # bypass — even on the daemon host it must NOT get the gate or admin without
    # a session (review r1#1 admin).
    bearer = "http://desktop.local:3000"
    app, _ = _build_app(
        tmp_path, monkeypatch, allowed_bearer_origins=[bearer], session_ttl_hours=24
    )
    loop = _loopback(app)
    # loopback peer + bearer origin + no token → still gated (not trusted-local)
    assert loop.get("/api/favorites/BV1AUTH", headers={"origin": bearer}).status_code == 401
    # and cannot manage the gate
    r = loop.post("/api/auth/admin", json={"enabled": False}, headers={"origin": bearer})
    assert r.status_code == 403
    assert r.json()["error"] == "local_only"


def test_admin_revoke_failure_leaves_old_state_intact(tmp_path, monkeypatch) -> None:
    # If the durable revocation (epoch bump) fails, the password change must NOT
    # be published/persisted — old sessions stay valid under the OLD password and
    # the endpoint reports failure (review r1#2).
    app, _ = _build_app(tmp_path, monkeypatch)  # enabled, password hunter2
    rc = _remote(app)
    rc.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200

    gate = app.state.auth_gate

    def _boom(_fp, *, force_bump):  # noqa: ANN001
        raise RuntimeError("db down")

    monkeypatch.setattr(gate.database, "revoke_and_set_fingerprint", _boom)
    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "password": "changed"})
    assert r.status_code == 503
    # the change was NOT applied: the OLD password still works, the new one doesn't
    assert gate.auth.enabled is True
    fresh = _remote(app)
    assert fresh.post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN}
    ).json()["ok"]
    assert (
        _remote(app)
        .post("/api/auth/login", json={"password": "changed"}, headers={"origin": _ORIGIN})
        .json()["ok"]
        is False
    )


def test_admin_save_preserves_other_config(tmp_path, monkeypatch) -> None:
    # admin writes the whole config.toml; an unrelated field (LLM key) must survive.
    from openbiliclaw.config import load_config

    app, _ = _build_app(tmp_path, monkeypatch, enabled=False, password=None)
    root = tmp_path / "runtime"
    # inject an unrelated config value, then toggle the gate via admin
    cfg = load_config()
    cfg.llm.openai.api_key = "sk-keep-me"
    from openbiliclaw.config import save_config

    save_config(cfg, root / "config.toml")
    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "password": "pw"})
    assert r.status_code == 200
    assert load_config().llm.openai.api_key == "sk-keep-me"
    assert load_config().api.auth.enabled is True


def _stored_fingerprint(db: Database) -> str | None:
    conn = db.open_connection()
    try:
        row = conn.execute(
            "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_admin_save_failure_leaves_durable_state_intact(tmp_path, monkeypatch) -> None:
    # The persist-first ordering: if config save fails, revocation must NOT have
    # run — the DB epoch + fingerprint and every existing session stay on the OLD
    # password, and the endpoint reports failure (review r2#1).
    app, db = _build_app(tmp_path, monkeypatch)  # enabled, password hunter2
    rc = _remote(app)
    rc.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200

    gate = app.state.auth_gate
    epoch_before = db.get_auth_epoch()
    fp_before = _stored_fingerprint(db)
    hash_before = gate.auth.password_hash

    def _boom(_cfg, *_a, **_k):  # noqa: ANN001, ANN002, ANN003
        raise OSError("disk full")

    monkeypatch.setattr("openbiliclaw.config.save_config", _boom)
    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "password": "changed"})
    assert r.status_code == 503

    # durable DB state untouched: no epoch bump, no fingerprint rewrite
    assert db.get_auth_epoch() == epoch_before
    assert _stored_fingerprint(db) == fp_before
    # live gate untouched (new password never published)
    assert gate.auth.password_hash == hash_before
    assert gate.auth.enabled is True
    # the existing session is still valid, and the OLD password still works while
    # the never-persisted new one is rejected
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200
    assert (
        _remote(app)
        .post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
        .json()["ok"]
    )
    assert (
        _remote(app)
        .post("/api/auth/login", json={"password": "changed"}, headers={"origin": _ORIGIN})
        .json()["ok"]
        is False
    )


def test_admin_password_change_survives_restart(tmp_path, monkeypatch) -> None:
    # A successful admin password change must NOT cause a spurious epoch bump on the
    # next startup reconcile. The stored fingerprint must be derived from the same
    # material reconcile reads after save (the persisted hash, plain=None) — not the
    # plaintext request — or every session minted after the change is revoked on the
    # next restart (review r3#1).
    app, db = _build_app(tmp_path, monkeypatch)  # enabled, password hunter2
    assert (
        _loopback(app)
        .post("/api/auth/admin", json={"enabled": True, "password": "newpw"})
        .status_code
        == 200
    )

    # log in remotely with the NEW password and keep that session cookie
    rc = _remote(app)
    assert rc.post(
        "/api/auth/login", json={"password": "newpw"}, headers={"origin": _ORIGIN}
    ).json()["ok"]
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200
    cookie = rc.cookies.get(ac.COOKIE_NAME)
    assert cookie
    epoch_after_change = db.get_auth_epoch()

    # simulate a backend restart: a fresh app over the SAME db + config file re-runs
    # the startup fingerprint reconcile
    app2 = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    # no spurious revocation: the reconcile left the epoch alone …
    assert db.get_auth_epoch() == epoch_after_change
    # … and the post-change session still authenticates after the "restart"
    rc2 = TestClient(app2, client=("192.168.1.50", 5000))
    rc2.cookies.set(ac.COOKIE_NAME, cookie)
    assert rc2.get("/api/favorites/BV1AUTH").status_code == 200


def test_admin_no_password_update_revokes_on_out_of_band_hash_change(tmp_path, monkeypatch) -> None:
    # If password_hash drifted on disk while the backend ran (e.g. `set-password`
    # before a restart), an admin {enabled:true} with NO password must still bump
    # the epoch when it hot-publishes that hash — otherwise a session minted under
    # the old live password survives under the newly published one (review r4#2).
    from openbiliclaw.config import load_config, save_config

    app, db = _build_app(tmp_path, monkeypatch)  # enabled, live password hunter2
    root = tmp_path / "runtime"

    # simulate an out-of-band `set-password`: new hash written to config.toml and
    # the DB epoch bumped, but the stored fingerprint untouched and the running
    # gate still validating the OLD password (no in-process reload).
    cfg = load_config()
    cfg.api.auth.password_hash = ac.hash_password("rotated-pw")
    save_config(cfg, root / "config.toml")
    db.bump_auth_epoch()

    # a session minted while the live gate still accepts the OLD password
    rc = _remote(app)
    assert rc.post(
        "/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN}
    ).json()["ok"]
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200

    # admin hot-reloads + publishes the drifted hash with NO password in the body
    assert _loopback(app).post("/api/auth/admin", json={"enabled": True}).status_code == 200
    # the out-of-band credential change is detected → old session revoked
    assert rc.get("/api/favorites/BV1AUTH").status_code == 401
    # and the rotated password is now the live one
    assert (
        _remote(app)
        .post("/api/auth/login", json={"password": "rotated-pw"}, headers={"origin": _ORIGIN})
        .json()["ok"]
    )


def test_admin_refused_when_env_managed_ttl(tmp_path, monkeypatch) -> None:
    # The env-managed guard must cover SESSION_TTL_HOURS, not just the password —
    # else admin would publish a TTL that the env override wins back on restart
    # (review r2#2). can_manage must also report False.
    app, _ = _build_app(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS", "24")
    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "session_ttl_hours": 48})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "env_managed"
    assert "OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS" in body["vars"]
    assert _loopback(app).get("/api/auth/status").json()["can_manage"] is False


def test_admin_refused_when_env_managed_trust_loopback(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK", "false")
    r = _loopback(app).post("/api/auth/admin", json={"enabled": False})
    assert r.status_code == 409
    assert "OPENBILICLAW_API_AUTH_TRUST_LOOPBACK" in r.json()["vars"]
    assert _loopback(app).get("/api/auth/status").json()["can_manage"] is False


def _app_over(db: Database) -> object:
    return create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )


def test_plaintext_password_survives_unrelated_save_without_revocation(
    tmp_path, monkeypatch
) -> None:
    # An operator using the plaintext `password` convenience must not have their
    # remembered sessions revoked when an UNRELATED save (settings UI / cookie
    # sync) rewrites config.toml. The plaintext must be preserved so the reconcile
    # fingerprint basis stays "pw:"+plain and the next restart sees no change (r8).
    from openbiliclaw.config import load_config, save_config

    project_root = tmp_path / "runtime"
    project_root.mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    (project_root / "config.toml").write_text(
        f'[api.auth]\nenabled = true\npassword = "secret"\nsession_secret = "{_SECRET}"\n'
        '\n[llm]\ndefault_provider = "ollama"\n\n[llm.ollama]\nmodel = "llama3"\n',
        encoding="utf-8",
    )
    db = Database(tmp_path / "auth.db")
    db.initialize()
    db.cache_content(
        "BV1AUTH",
        title="t",
        up_name="u",
        source="test",
        source_platform="bilibili",
        content_url="https://www.bilibili.com/video/BV1AUTH",
    )
    app = _app_over(db)  # startup reconcile stores "pw:"+secret
    rc = _remote(app)
    assert rc.post(
        "/api/auth/login", json={"password": "secret"}, headers={"origin": _ORIGIN}
    ).json()["ok"]
    assert rc.get("/api/favorites/BV1AUTH").status_code == 200
    cookie = rc.cookies.get(ac.COOKIE_NAME)
    epoch_before = db.get_auth_epoch()

    # an unrelated settings save must keep the plaintext password line
    cfg = load_config()
    cfg.llm.openai.api_key = "sk-unrelated"
    save_config(cfg)
    assert 'password = "secret"' in (project_root / "config.toml").read_text(encoding="utf-8")

    # "restart": fresh app over the same db + config → reconcile must NOT bump
    app2 = _app_over(db)
    assert db.get_auth_epoch() == epoch_before
    rc2 = TestClient(app2, client=("192.168.1.50", 5000))
    rc2.cookies.set(ac.COOKIE_NAME, cookie)
    assert rc2.get("/api/favorites/BV1AUTH").status_code == 200


def test_admin_password_change_refused_when_shadowed_by_config_local(tmp_path, monkeypatch) -> None:
    # config.local.toml is merged OVER config.toml (local wins). A plaintext
    # password pinned there shadows an admin password change written to
    # config.toml — it would look successful then silently revert on restart. The
    # admin endpoint must detect the shadow (post-save effective reload) and return
    # 409 instead of a false success (review r9).
    app, _ = _build_app(tmp_path, monkeypatch)  # config.toml: enabled, hunter2
    root = tmp_path / "runtime"
    (root / "config.local.toml").write_text('[api.auth]\npassword = "localpw"\n', encoding="utf-8")

    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "password": "newpw"})
    assert r.status_code == 409
    assert r.json()["error"] == "shadowed"
    # live gate untouched + config.toml rolled back → the new password never took
    assert (
        _remote(app)
        .post("/api/auth/login", json={"password": "newpw"}, headers={"origin": _ORIGIN})
        .json()["ok"]
        is False
    )


def test_admin_shadowed_change_does_not_create_config_when_absent(tmp_path, monkeypatch) -> None:
    # When config.toml does NOT exist (auth comes only from config.local.toml), a
    # shadowed admin change must roll back to the ABSENT state — not leave a newly
    # created config.toml carrying the other requested fields (review r11#2).
    project_root = tmp_path / "runtime"
    project_root.mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    # auth lives ONLY in config.local.toml; there is no config.toml
    (project_root / "config.local.toml").write_text(
        f'[api.auth]\nenabled = true\npassword = "localpw"\nsession_secret = "{_SECRET}"\n'
        '\n[llm]\ndefault_provider = "ollama"\n\n[llm.ollama]\nmodel = "llama3"\n',
        encoding="utf-8",
    )
    assert not (project_root / "config.toml").exists()

    db = Database(tmp_path / "auth.db")
    db.initialize()
    db.cache_content(
        "BV1AUTH",
        title="t",
        up_name="u",
        source="test",
        source_platform="bilibili",
        content_url="https://www.bilibili.com/video/BV1AUTH",
    )
    app = _app_over(db)

    r = _loopback(app).post("/api/auth/admin", json={"enabled": True, "password": "newpw"})
    assert r.status_code == 409
    assert r.json()["error"] == "shadowed"
    # the failed shadowed change left NO config.toml behind
    assert not (project_root / "config.toml").exists()


# ── WebSocket is gated too (http middleware does not cover ws scope) ─────────


def test_websocket_blocked_for_unauthenticated_remote(tmp_path, monkeypatch) -> None:
    from starlette.websockets import WebSocketDisconnect

    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    raised = False
    try:
        with client.websocket_connect("/api/runtime-stream"):
            pass
    except WebSocketDisconnect:
        raised = True
    assert raised, "unauthenticated remote WebSocket should be rejected"


def test_websocket_allowed_for_loopback(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _loopback(app)
    # loopback + canonical local Host → trusted-local → handshake accepted.
    # (TestClient otherwise hardcodes Host=testserver for ws; a real local browser
    # sends the loopback Host, so we set it explicitly here.)
    with client.websocket_connect("/api/runtime-stream", headers={"host": "127.0.0.1:8420"}):
        pass


def test_websocket_allowed_with_valid_cookie(tmp_path, monkeypatch) -> None:
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    # same-origin handshake with the session cookie → accepted
    with client.websocket_connect("/api/runtime-stream", headers={"origin": _ORIGIN}):
        pass


def test_websocket_accepts_extension_query_token(tmp_path, monkeypatch) -> None:
    _key_id, full_key, record = ac.generate_extension_access_key()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
    )
    client = _remote(app)
    token = client.post("/api/auth/extension-token", json={"key": full_key}).json()["token"]

    with client.websocket_connect(
        f"/api/runtime-stream?token={token}",
        headers={"origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ):
        pass


def test_extension_query_token_survives_live_revocation_checks(tmp_path, monkeypatch) -> None:
    from openbiliclaw.runtime.events import RuntimeEventHub

    _key_id, full_key, record = ac.generate_extension_access_key()
    hub = RuntimeEventHub()
    app, _ = _build_app(
        tmp_path,
        monkeypatch,
        extension_access_enabled=True,
        extension_access_keys=[record],
        runtime_event_hub=hub,
    )
    client = _remote(app)
    token = client.post("/api/auth/extension-token", json={"key": full_key}).json()["token"]

    with client.websocket_connect(
        f"/api/runtime-stream?token={token}",
        headers={"origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ) as websocket:
        asyncio.run(hub.publish({"type": "extension_session_verified"}))
        assert websocket.receive_json() == {"type": "extension_session_verified"}


def test_websocket_rejected_after_revocation(tmp_path, monkeypatch) -> None:
    from starlette.websockets import WebSocketDisconnect

    app, db = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    with client.websocket_connect("/api/runtime-stream", headers={"origin": _ORIGIN}):
        pass  # valid cookie connects
    db.bump_auth_epoch()  # logout-all / password change revokes everything
    raised = False
    try:
        with client.websocket_connect("/api/runtime-stream", headers={"origin": _ORIGIN}):
            pass
    except WebSocketDisconnect:
        raised = True
    assert raised, "WebSocket must re-read the revocation epoch at handshake"


# ── fail-closed invariants (corrupt epoch / reconcile failure) ──────────────


def test_corrupt_auth_epoch_fails_closed(tmp_path, monkeypatch) -> None:
    app, db = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert client.get("/api/favorites/BV1AUTH").status_code == 200
    # damage the epoch row: must NOT be silently treated as 0 (would resurrect tokens)
    db.conn.execute(
        "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', 'garbage')"
    )
    db.conn.commit()
    assert client.get("/api/favorites/BV1AUTH").status_code == 401


def test_reconcile_failure_fails_closed(tmp_path, monkeypatch) -> None:
    def _raise(self, _fp):  # noqa: ANN001
        raise RuntimeError("db boom")

    monkeypatch.setattr(Database, "reconcile_password_fingerprint", _raise)
    app, _ = _build_app(tmp_path, monkeypatch)
    client = _remote(app)
    # login still sets a cookie, but token auth is failed-closed until reconcile succeeds
    client.post("/api/auth/login", json={"password": "hunter2"}, headers={"origin": _ORIGIN})
    assert client.get("/api/favorites/BV1AUTH").status_code == 401
    # loopback still bypasses the gate
    assert _loopback(app).get("/api/favorites/BV1AUTH").status_code == 200


def test_get_auth_plain_password_reads_env_then_config(tmp_path, monkeypatch) -> None:
    from openbiliclaw.config import get_auth_plain_password

    root = tmp_path / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_PASSWORD", raising=False)
    (root / "config.toml").write_text('[api.auth]\npassword = "plainpw"\n', encoding="utf-8")
    # config plaintext is a stable fingerprint source (no false revoke across restarts)
    assert get_auth_plain_password() == "plainpw"
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "envpw")
    assert get_auth_plain_password() == "envpw"  # env wins
