"""FastAPI glue for the LAN password gate.

Wires the stdlib primitives in :mod:`openbiliclaw.auth_core` to Starlette
requests/responses: the auth middleware, the ``/api/auth/*`` routes, cookie and
CSRF handling, login-failure rate limiting, and startup reconciliation of the
session secret and password fingerprint.

See ``docs/plans/2026-05-30-web-password-auth-design.md``.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI  # noqa: TC002 - FastAPI needs runtime annotations for routes.
from starlette.requests import (
    Request,  # noqa: TC002 - FastAPI needs runtime annotations for routes.
)
from starlette.responses import JSONResponse, Response

from openbiliclaw import auth_core
from openbiliclaw.auth_core import COOKIE_NAME, CSRF_HEADER

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

    from openbiliclaw.config import ApiAuthConfig
    from openbiliclaw.storage.database import Database

GateGetter = Callable[[], "AuthGate"]

logger = logging.getLogger(__name__)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# GET endpoints with real side effects (claim+lock a source task; bootstrap-write
# the recommendation history; schedule a pending chat-turn completion). They need
# CSRF like unsafe methods. The custom `X-OBC-Auth` header is a complete CSRF
# defense (a credentialed cross-origin request can't set it under
# allow_origins=["*"]); the SPA sends it on every fetch. img/WS don't hit these
# paths. See review r2#2 / r3#2.
_CSRF_GET_EXACT = frozenset(
    {
        "/api/sources/xhs/next-task",
        "/api/sources/dy/next-task",
        "/api/sources/yt/next-task",
        "/api/recommendations",
    }
)
_CSRF_GET_PREFIXES = ("/api/chat/turns/",)  # GET /api/chat/turns/{id} resumes a pending turn
_NEVER_EXPIRE_MAX_AGE = 10 * 365 * 24 * 3600  # ~10 years for "remember login"


def _auth_env_overrides() -> list[str]:
    # When any auth env var is set, auth is env-managed and config-file edits
    # (incl. the local admin endpoint) won't take effect on restart — see CLI
    # guard. The canonical var list lives with the config loader so the guard
    # always matches the real override surface (every field, not just password).
    from openbiliclaw.config import API_AUTH_ENV_VARS

    return [name for name in API_AUTH_ENV_VARS if (os.environ.get(name) or "").strip()]


def _is_mutating_get(path: str) -> bool:
    return path in _CSRF_GET_EXACT or any(path.startswith(p) for p in _CSRF_GET_PREFIXES)


class _RateLimiter:
    """In-memory per-IP login-failure limiter (resets on restart)."""

    def __init__(self, *, max_failures: int = 5, window: int = 900, lockout: int = 900) -> None:
        self._max = max_failures
        self._window = window
        self._lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        until = self._locked_until.get(key)
        if until is None:
            return False
        if moment >= until:
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
            return False
        return True

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        events = [t for t in self._failures.get(key, []) if moment - t < self._window]
        events.append(moment)
        self._failures[key] = events
        if len(events) >= self._max:
            self._locked_until[key] = moment + self._lockout

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


class AuthGate:
    """Holds live auth config + database and answers per-request auth questions."""

    def __init__(self, auth: ApiAuthConfig, database: Database | None) -> None:
        self.auth = auth
        self.database = database
        self.rate = _RateLimiter()
        # When startup fingerprint reconciliation fails we cannot guarantee a
        # password change was revoked, so fail closed for all token auth until a
        # successful reconcile (loopback still bypasses). See §4.7 / review r1#2.
        self.reconcile_ok = True

    # ── request introspection ──────────────────────────────────────────

    def resolve_client(self, request: HTTPConnection) -> tuple[str | None, bool]:
        peer = request.client.host if request.client else ""
        try:
            xff_values = request.headers.getlist("x-forwarded-for")
        except AttributeError:  # pragma: no cover - non-starlette headers
            single = request.headers.get("x-forwarded-for")
            xff_values = [single] if single else []
        has_fwd = auth_core.header_present(request.headers)
        return auth_core.resolve_client_ip(
            peer,
            xff_values=xff_values,
            has_forward_header=has_fwd,
            trusted_proxies=self.auth.trusted_proxies,
        )

    def is_trusted_local(self, request: HTTPConnection) -> bool:
        if not self.auth.trust_loopback:
            return False
        client_ip, local = self.resolve_client(request)
        if not auth_core.is_trusted_local(client_ip, local):
            return False
        # A loopback peer is not enough: the user's browser can be driven by a
        # malicious page to issue cross-origin requests to http://127.0.0.1,
        # which would otherwise inherit the local bypass (localhost CSRF / DNS
        # rebinding). Only grant the bypass to non-cross-origin-browser callers:
        # no Origin (CLI/curl/non-browser), the local web UI itself (same-origin),
        # a browser extension, or an explicitly allow-listed origin. See review r7.
        return self._origin_safe_for_local(request)

    def _origin_safe_for_local(self, request: HTTPConnection) -> bool:
        origin = request.headers.get("origin")
        # A real browser extension (the primary local client) is trusted by its
        # origin scheme alone — a web page can't forge a chrome-extension origin.
        if origin and (
            origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")
        ):
            return True
        # NOTE: allowed_bearer_origins are deliberately NOT treated as
        # trusted-local. They are the cross-origin case that authenticates via a
        # bearer *token*; granting them a no-token local bypass would also hand
        # them /api/auth/admin (manage the gate) without a session. They still
        # work via the token path (pick_token). See review r1#1 (admin).
        # Fetch Metadata: a real browser reveals cross-origin intent even when it
        # omits Origin (no-cors subresources like
        # `<img src="http://127.0.0.1:8420/api/sources/xhs/next-task">`). Deny the
        # local bypass for cross-site / cross-origin-same-site browser requests so a
        # malicious page can't drive no-Origin loopback state changes. CLI/curl send
        # no Sec-Fetch-* (unaffected); the extension uses its chrome-extension Origin
        # branch above. See review r9.
        if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            return False
        eff = self.effective(request)
        # DNS-rebinding defense: a rebound browser (Host: evil.example → 127.0.0.1)
        # connects DIRECTLY, so when the peer itself is loopback the no-Origin /
        # same-origin exemptions additionally require a canonical loopback Host.
        # When the client was resolved via a configured trusted proxy (peer is the
        # proxy, not loopback), the proxy config is the trust anchor and rebinding
        # doesn't apply, so the Host (the external proxied name) isn't required to
        # be loopback.
        peer_host = request.client.host if request.client else None
        peer_is_loopback = auth_core.is_loopback_host(peer_host)
        if peer_is_loopback and (eff is None or not auth_core.is_loopback_host(eff[1])):
            return False
        if not origin:
            return True  # CLI/curl/non-browser, or same-origin GET
        parsed = auth_core.parse_origin(origin)
        return parsed is not None and auth_core.same_origin(parsed, eff)

    def effective(self, request: HTTPConnection) -> tuple[str, str, int] | None:
        peer = request.client.host if request.client else ""
        return auth_core.effective_scheme_host(
            url_scheme=request.url.scheme,
            host_header=request.headers.get("host"),
            xf_proto=request.headers.get("x-forwarded-proto"),
            xf_host=request.headers.get("x-forwarded-host"),
            peer=peer,
            trusted_proxies=self.auth.trusted_proxies,
        )

    def pick_token(self, request: HTTPConnection) -> tuple[bool, str | None]:
        """Return ``(used_cookie, token)``. Bearer/query only for allowed origins."""
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            return True, cookie
        origin = request.headers.get("origin")
        if auth_core.origin_allowed_for_bearer(origin, self.auth.allowed_bearer_origins):
            authz = request.headers.get("authorization", "")
            if authz.lower().startswith("bearer "):
                return False, authz[7:].strip() or None
            qp = request.query_params.get("token")
            if qp:
                return False, qp
        return False, None

    def current_epoch(self) -> int:
        if self.database is None:
            raise RuntimeError("auth gate has no database")
        return self.database.get_auth_epoch()

    def token_valid(self, token: str | None) -> bool:
        if not token:
            return False
        if not self.reconcile_ok:
            return False  # revocation state unverified → fail closed
        epoch = self.current_epoch()  # may raise -> caller fails closed
        return auth_core.verify_token(token, self.auth.session_secret, current_epoch=epoch)

    def csrf_ok(self, request: Request, *, require_origin: bool = True) -> bool:
        # The custom header is the core defense (cross-origin credentialed
        # requests can't set it). For unsafe methods we *also* pin Origin==Host
        # (Origin is reliably present there); GET requests may legitimately omit
        # Origin when same-origin, so the header alone gates them.
        if request.headers.get(CSRF_HEADER) is None:
            return False
        if require_origin:
            parsed = auth_core.parse_origin(request.headers.get("origin"))
            if not auth_core.same_origin(parsed, self.effective(request)):
                return False
        return True


# ── cookie helpers ──────────────────────────────────────────────────────────


def _set_session_cookie(resp: Response, token: str, *, ttl_hours: int, secure: bool) -> None:
    max_age = ttl_hours * 3600 if ttl_hours > 0 else _NEVER_EXPIRE_MAX_AGE
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.set_cookie(COOKIE_NAME, "", max_age=0, httponly=True, samesite="lax", path="/")


def _is_secure(gate: AuthGate, request: Request) -> bool:
    eff = gate.effective(request)
    return eff is not None and eff[0] == "https"


# ── whitelist (always-public paths) ─────────────────────────────────────────


def _is_public(request: Request) -> bool:
    """Paths that bypass the gate even when auth is enabled (§4.2)."""
    path = request.url.path
    method = request.method.upper()
    if method == "OPTIONS":
        return True
    if not path.startswith("/api"):
        return True  # static SPA shells, "/", favicon, etc.
    if path == "/api/health":
        return True
    if path in ("/api/auth/status", "/api/auth/login"):
        return True
    if path == "/api/autostart-status":
        return True
    if path == "/api/autostart/apply":
        return True
    # guided-init status is remote-readable (can_manage flags local-only); the
    # write endpoints (init / init/cancel) are whitelisted too but self-gate via
    # is_trusted_local in their handlers (gui-init spec §2).
    if path in ("/api/init-status", "/api/init", "/api/init/cancel"):
        return True
    # gate management bypasses the middleware so its handler can enforce
    # trusted-local itself and return a specific 403 local_only for every
    # non-local caller (remote OR cross-origin loopback), instead of a generic
    # 401 that leaks whether a token was presented. The handler is the gate.
    if path == "/api/auth/admin":
        return True
    # plain logout is public + idempotent; global revoke (?all=true) is NOT.
    return bool(path == "/api/auth/logout" and request.query_params.get("all") != "true")


# ── middleware ──────────────────────────────────────────────────────────────


def make_auth_middleware(get_gate: GateGetter) -> Any:
    """Build the ASGI http middleware dispatch closure."""

    async def auth_guard(request: Request, call_next: Any) -> Any:
        gate: AuthGate = get_gate()
        if not gate.auth.enabled:
            return await call_next(request)
        if _is_public(request):
            return await call_next(request)
        if gate.is_trusted_local(request):
            return await call_next(request)

        used_cookie, token = gate.pick_token(request)
        try:
            valid = gate.token_valid(token)
        except Exception:  # DB unavailable -> fail closed
            logger.warning("auth: epoch read failed; failing closed", exc_info=True)
            return _unauthorized(clear_cookie=False)
        if not valid:
            return _unauthorized(clear_cookie=used_cookie)

        method = request.method.upper()
        is_unsafe = method in _UNSAFE_METHODS
        if (
            used_cookie
            and (is_unsafe or _is_mutating_get(request.url.path))
            and not gate.csrf_ok(request, require_origin=is_unsafe)
        ):
            return _forbidden_csrf()
        return await call_next(request)

    return auth_guard


def authorize_websocket(gate: AuthGate, websocket: Any) -> bool:
    """Authorize a WebSocket handshake (the http middleware does NOT cover ws).

    Must be called *before* ``websocket.accept()``. Mirrors the HTTP gate plus a
    same-origin (CSWSH) check, since browsers can't set custom headers on a
    WebSocket handshake. WebSocket exposes the same ``client`` / ``headers`` /
    ``cookies`` / ``query_params`` / ``url`` attributes the gate reads.
    """
    if not gate.auth.enabled:
        return True
    if gate.is_trusted_local(websocket):
        return True
    # CSWSH defense: Origin must be same-origin or an allow-listed bearer origin.
    origin = websocket.headers.get("origin")
    parsed = auth_core.parse_origin(origin)
    same = auth_core.same_origin(parsed, gate.effective(websocket))
    allowed_bearer = auth_core.origin_allowed_for_bearer(origin, gate.auth.allowed_bearer_origins)
    if not (same or allowed_bearer):
        return False
    _used_cookie, token = gate.pick_token(websocket)
    try:
        return gate.token_valid(token)
    except Exception:
        logger.warning("auth: websocket epoch read failed; failing closed", exc_info=True)
        return False


def _cors_echo(resp: JSONResponse) -> JSONResponse:
    # middleware short-circuits run outside CORSMiddleware; echo a permissive
    # header so cross-origin desktop clients can read the status code.
    resp.headers.setdefault("Access-Control-Allow-Origin", "*")
    return resp


def _unauthorized(*, clear_cookie: bool) -> JSONResponse:
    resp = JSONResponse({"error": "auth_required"}, status_code=401)
    if clear_cookie:
        _clear_session_cookie(resp)
    return _cors_echo(resp)


def _forbidden_csrf() -> JSONResponse:
    return _cors_echo(JSONResponse({"error": "csrf"}, status_code=403))


# ── routes ──────────────────────────────────────────────────────────────────


def register_auth_routes(app: FastAPI, get_gate: GateGetter) -> None:
    """Register ``/api/auth/{status,login,logout}`` on the FastAPI app."""

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> JSONResponse:
        gate: AuthGate = get_gate()
        env_managed = bool(_auth_env_overrides())
        local = gate.is_trusted_local(request)
        # can_manage: only a trusted-local caller (extension/local UI/CLI) may
        # toggle the gate via /api/auth/admin, and not when env-managed.
        can_manage = local and not env_managed
        if not gate.auth.enabled:
            return JSONResponse(
                {
                    "enabled": False,
                    "authenticated": True,
                    "trust_loopback": gate.auth.trust_loopback,
                    "env_managed": env_managed,
                    "can_manage": can_manage,
                }
            )
        authenticated = local
        if not authenticated:
            _used, token = gate.pick_token(request)
            try:
                authenticated = gate.token_valid(token)
            except Exception:
                authenticated = False
        return JSONResponse(
            {
                "enabled": True,
                "authenticated": authenticated,
                "trust_loopback": gate.auth.trust_loopback,
                "env_managed": env_managed,
                "can_manage": can_manage,
            }
        )

    @app.post("/api/auth/login")
    async def auth_login(request: Request) -> JSONResponse:
        gate: AuthGate = get_gate()
        if not gate.auth.enabled:
            return JSONResponse({"ok": False, "error": "auth_disabled"}, status_code=400)
        client_ip, _local = gate.resolve_client(request)
        rate_key = client_ip or (request.client.host if request.client else "unknown")
        if gate.rate.is_locked(rate_key):
            return JSONResponse({"ok": False, "error": "locked"}, status_code=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        password = str(body.get("password", "")) if isinstance(body, dict) else ""
        if not gate.auth.password_hash or not auth_core.verify_password(
            password, gate.auth.password_hash
        ):
            gate.rate.record_failure(rate_key)
            return JSONResponse({"ok": False}, status_code=401)
        gate.rate.reset(rate_key)

        try:
            epoch = gate.current_epoch()
        except Exception:
            return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
        ttl = gate.auth.session_ttl_hours
        origin = request.headers.get("origin")
        req_origin = auth_core.parse_origin(origin)
        eff = gate.effective(request)
        # Server decides the mode by Origin; the client cannot ask for a token.
        is_same = req_origin is None or auth_core.same_origin(req_origin, eff)
        if is_same:
            token = auth_core.sign_token(gate.auth.session_secret, epoch=epoch, ttl_hours=ttl)
            resp = JSONResponse({"ok": True})
            _set_session_cookie(resp, token, ttl_hours=ttl, secure=_is_secure(gate, request))
            return resp
        # cross-origin → bearer mode (allow-listed + finite TTL only)
        if not auth_core.origin_allowed_for_bearer(origin, gate.auth.allowed_bearer_origins):
            return JSONResponse({"ok": False, "error": "origin_forbidden"}, status_code=403)
        if ttl <= 0:
            return JSONResponse({"ok": False, "error": "bearer_requires_ttl"}, status_code=400)
        token = auth_core.sign_token(gate.auth.session_secret, epoch=epoch, ttl_hours=ttl)
        return JSONResponse(
            {"ok": True, "token": token, "expires_at": auth_core.token_expires_at(token)}
        )

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> JSONResponse:
        gate: AuthGate = get_gate()
        resp = JSONResponse({"ok": True})
        _clear_session_cookie(resp)
        if request.query_params.get("all") == "true" and gate.database is not None:
            # global revoke; middleware already required a valid session here
            try:
                gate.database.bump_auth_epoch()
            except Exception:
                logger.warning("auth: logout-all bump failed", exc_info=True)
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
        return resp


# ── startup reconciliation ──────────────────────────────────────────────────


def ensure_session_secret(auth: ApiAuthConfig) -> bool:
    """Generate a session secret on first enable. Returns True if it changed."""
    if auth.enabled and not auth.session_secret.strip():
        auth.session_secret = secrets.token_urlsafe(32)
        return True
    return False


def reconcile_password_fingerprint(gate: AuthGate, *, plain: str | None) -> None:
    """Bump the revocation epoch if the password changed since last boot (§4.7)."""
    auth = gate.auth
    if not (auth.enabled and auth.password_hash.strip() and auth.session_secret.strip()):
        return
    if gate.database is None:
        return
    fingerprint = auth_core.password_fingerprint(
        auth.session_secret, plain=plain, password_hash=auth.password_hash
    )
    try:
        bumped = gate.database.reconcile_password_fingerprint(fingerprint)
        gate.reconcile_ok = True
        if bumped:
            logger.info("auth: password change detected, revoked existing sessions")
    except Exception:
        # Could not confirm/revoke a possible password change → fail closed for
        # all token auth (loopback still works) until a clean reconcile.
        gate.reconcile_ok = False
        logger.warning(
            "auth: fingerprint reconcile failed; token auth disabled until next "
            "successful reconcile (restart after fixing the data dir)",
            exc_info=True,
        )
