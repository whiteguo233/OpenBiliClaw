"""Standard-library-only primitives for the LAN password gate.

This module is deliberately dependency-free (only Python stdlib) so it can be
imported by both ``config.py`` (which must stay low-level to avoid an import
cycle with ``openbiliclaw.api``) and the FastAPI glue in ``api/auth.py``.

It implements, per ``docs/plans/2026-05-30-web-password-auth-design.md``:

* scrypt password hashing / constant-time verification (§4.5)
* HMAC-signed stateless session tokens carrying ``iat`` / ``ep`` / optional
  ``exp`` (§4.4)
* a stable password fingerprint derived from credential material, **never** the
  salted hash (§4.7, v7 fix)
* reverse-proxy-aware real-client-IP resolution (§4.1/§6) and a single
  ``effective_origin`` / ``same_origin`` contract reused by CSRF, WebSocket,
  bearer-mode and ``Secure`` cookie decisions (§4.9)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import socket
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

COOKIE_NAME = "obc_session"
CSRF_HEADER = "x-obc-auth"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024

_TOKEN_VERSION = 1


# ── base64url helpers ───────────────────────────────────────────────────────


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ── password hashing (scrypt) ───────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Hash a password with scrypt + random salt.

    Returns ``scrypt$<n>$<r>$<p>$<b64salt>$<b64dk>``. The salt is random, so the
    same plaintext yields a different string each call — callers MUST NOT derive
    a stability fingerprint from this value (see :func:`password_fingerprint`).
    """
    import os

    salt = os.urandom(16)
    dk = hashlib.scrypt(
        plain.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64u_encode(salt)}${_b64u_encode(dk)}"


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time verify ``plain`` against a stored scrypt string."""
    try:
        scheme, n_s, r_s, p_s, salt_s, dk_s = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = _b64u_decode(salt_s)
        expected = _b64u_decode(dk_s)
    except (ValueError, TypeError):
        return False
    try:
        actual = hashlib.scrypt(
            plain.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


# ── password fingerprint (stable across restarts; §4.7 v7) ──────────────────


def password_fingerprint(session_secret: str, *, plain: str | None, password_hash: str) -> str:
    """Stable fingerprint of the *credential*, used to detect password changes.

    Crucially this is computed from the plaintext (when available) or the
    user-supplied hash string — **not** from a freshly salted scrypt hash —
    so an unchanged password produces the same fingerprint on every restart and
    never falsely revokes sessions (review r6 fix).
    """
    material = "pw:" + plain if plain else "ph:" + password_hash
    digest = hmac.new(
        session_secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).digest()
    return _b64u_encode(digest)


# ── stateless signed session tokens (§4.4) ──────────────────────────────────


def _sign(secret: str, payload_b64: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64u_encode(mac)


def sign_token(
    secret: str,
    *,
    epoch: int,
    ttl_hours: int = 0,
    now: int | None = None,
) -> str:
    """Mint a signed token. ``ttl_hours <= 0`` → no ``exp`` (never expires)."""
    issued = int(time.time()) if now is None else now
    payload: dict[str, int] = {"v": _TOKEN_VERSION, "iat": issued, "ep": epoch}
    if ttl_hours > 0:
        payload["exp"] = issued + ttl_hours * 3600
    payload_b64 = _b64u_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(secret, payload_b64)}"


def token_expires_at(token: str) -> int | None:
    """Return the ``exp`` of a (already trusted) token, or ``None``."""
    try:
        payload_b64 = token.split(".", 1)[0]
        payload = json.loads(_b64u_decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if isinstance(exp, int) else None
    except (ValueError, TypeError, KeyError):
        return None


def verify_token(
    token: str,
    secret: str,
    *,
    current_epoch: int,
    now: int | None = None,
) -> bool:
    """Verify signature, expiry and revocation epoch in constant time on the MAC."""
    if not token or not secret:
        return False
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(secret, payload_b64)):
        return False
    try:
        payload = json.loads(_b64u_decode(payload_b64))
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or payload.get("v") != _TOKEN_VERSION:
        return False
    moment = int(time.time()) if now is None else now
    exp = payload.get("exp")
    if exp is not None and (not isinstance(exp, int) or moment >= exp):
        return False
    ep = payload.get("ep")
    return not (not isinstance(ep, int) or ep < current_epoch)


# ── IP / proxy handling (§4.1, §6) ──────────────────────────────────────────

_LOOPBACK = frozenset({"127.0.0.1", "::1"})


def _detect_default_gateway() -> str | None:
    """Read ``/proc/net/route`` and return the default gateway IP.

    In a Docker container this is the bridge gateway (e.g. 172.18.0.1),
    which is the only source IP that host-local requests can carry after
    the kernel rewrites loopback through the bridge.  On a bare-metal /
    VM host this is the LAN router, which never appears as a TCP source
    for the local app — adding it is harmless.

    Returns ``None`` when the file is unavailable (Windows, macOS, etc.).
    """
    try:
        with open("/proc/net/route") as fh:
            for line in fh:
                parts = line.split()
                # default route: destination=0, mask=0
                if parts[1] == "00000000" and parts[7] == "00000000":
                    return socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))
    except (OSError, IndexError, ValueError):
        pass
    return None


_GATEWAY_IP = _detect_default_gateway()
_TRUSTED_LOCAL_IPS: frozenset[str] = (
    _LOOPBACK | frozenset({_GATEWAY_IP}) if _GATEWAY_IP else _LOOPBACK
)
_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def is_loopback_host(host: str | None) -> bool:
    """Whether a Host/host value is a canonical loopback name.

    Used to gate the loopback bypass against DNS rebinding: an attacker page on
    ``http://evil.example:8420`` rebound to 127.0.0.1 would otherwise look
    "same-origin" to the local backend. Only ``localhost`` / loopback IPs qualify.
    """
    if not host:
        return False
    if host.strip().lower() == "localhost":
        return True
    ip = norm_ip(host)
    if ip is None:
        return False
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def norm_ip(value: str | None) -> str | None:
    """Normalize an IP literal (strip brackets/port/zone); ``None`` if invalid."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    # [::1]:port  or  [::1]
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return None
        text = text[1:end]
    elif text.count(":") == 1:
        # ipv4:port
        text = text.split(":", 1)[0]
    if "%" in text:  # IPv6 zone id
        text = text.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _trusted_set(trusted_proxies: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for item in trusted_proxies:
        normalized = norm_ip(item)
        if normalized is not None:
            out.add(normalized)
    return out


def resolve_client_ip(
    peer: str,
    *,
    xff_values: list[str],
    has_forward_header: bool,
    trusted_proxies: Iterable[str],
) -> tuple[str | None, bool]:
    """Resolve the real client IP, fail-closed.

    Returns ``(client_ip, trustworthy_local)``. ``trustworthy_local`` is only
    ``True`` when the request genuinely originates from the local host (directly,
    or via a configured trusted proxy that reports a loopback client).
    """
    peer_n = norm_ip(peer)
    if not has_forward_header:
        return peer_n, True
    trusted = _trusted_set(trusted_proxies)
    if peer_n is None or peer_n not in trusted:
        # forwarded header from a non-trusted peer → treat as remote
        return peer_n, False
    chain: list[str] = []
    for value in xff_values:
        for part in value.split(","):
            normalized = norm_ip(part)
            if normalized is None:
                return peer_n, False  # malformed → fail closed, do not raise
            chain.append(normalized)
    if not chain:
        return peer_n, False
    # right-to-left: skip trusted hops, first untrusted is the real client
    real: str | None = None
    for ip in reversed(chain):
        if ip in trusted:
            continue
        real = ip
        break
    if real is None or real in trusted:
        return peer_n, False
    return real, True


def is_trusted_local(client_ip: str | None, trustworthy_local: bool) -> bool:
    return trustworthy_local and client_ip is not None and client_ip in _TRUSTED_LOCAL_IPS


# ── origin / scheme normalization (§4.9) ────────────────────────────────────

_DEFAULT_PORT = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _split_host_port(host: str | None, scheme: str) -> tuple[str, int] | None:
    if not host:
        return None
    text = host.strip()
    if not text:
        return None
    if text.startswith("["):  # [ipv6](:port)?
        end = text.find("]")
        if end == -1:
            return None
        hostname = text[1:end].lower()
        rest = text[end + 1 :]
        port_str = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        hostname, port_str = text.split(":", 1)
        hostname = hostname.lower()
    else:
        hostname, port_str = text.lower(), ""
    if port_str:
        try:
            port = int(port_str)
        except ValueError:
            return None
    else:
        port = _DEFAULT_PORT.get(scheme, 0)
    return hostname, port


def _http_scheme(scheme: str) -> str:
    # collapse ws/wss to http/https for same-origin comparison
    s = scheme.lower()
    if s in ("ws", "http"):
        return "http"
    if s in ("wss", "https"):
        return "https"
    return s


def effective_scheme_host(
    *,
    url_scheme: str,
    host_header: str | None,
    xf_proto: str | None,
    xf_host: str | None,
    peer: str,
    trusted_proxies: Iterable[str],
) -> tuple[str, str, int] | None:
    """Compute the externally-effective ``(scheme, host, port)``.

    Forwarded scheme/host are honoured **only** when the direct peer is a
    configured trusted proxy (consistent with §4.1), so a spoofed
    ``X-Forwarded-Proto`` from an untrusted client cannot influence ``Secure``
    or same-origin decisions.
    """
    peer_n = norm_ip(peer)
    trusted = _trusted_set(trusted_proxies)
    if peer_n is not None and peer_n in trusted:
        scheme = (xf_proto.split(",")[0].strip() if xf_proto else "") or url_scheme
        host = xf_host or host_header
    else:
        scheme, host = url_scheme, host_header
    hp = _split_host_port(host, _http_scheme(scheme))
    if hp is None:
        return None
    return _http_scheme(scheme), hp[0], hp[1]


def parse_origin(origin: str | None) -> tuple[str, str, int] | None:
    """Parse an ``Origin`` header into ``(scheme, host, port)``; ``None`` if absent/opaque."""
    if not origin:
        return None
    text = origin.strip()
    if text.lower() == "null" or "://" not in text:
        return None
    scheme, _, rest = text.partition("://")
    hp = _split_host_port(rest, _http_scheme(scheme))
    if hp is None:
        return None
    return _http_scheme(scheme), hp[0], hp[1]


def same_origin(
    origin: tuple[str, str, int] | None, effective: tuple[str, str, int] | None
) -> bool:
    """True only when a concrete Origin matches the effective scheme+host+port."""
    if origin is None or effective is None:
        return False
    return origin == effective


def origin_string(parts: tuple[str, str, int] | None) -> str | None:
    """Render ``(scheme, host, port)`` as ``scheme://host[:port]`` (default port elided)."""
    if parts is None:
        return None
    scheme, host, port = parts
    bracket = host if ":" not in host else f"[{host}]"
    if port == _DEFAULT_PORT.get(scheme, 0):
        return f"{scheme}://{bracket}"
    return f"{scheme}://{bracket}:{port}"


def origin_allowed_for_bearer(origin: str | None, allowed: Iterable[str]) -> bool:
    """Whether a request Origin is in the bearer allow-list (normalized compare)."""
    parsed = parse_origin(origin)
    if parsed is None:
        return False
    target = origin_string(parsed)
    return any(origin_string(parse_origin(entry)) == target for entry in allowed)


_EXT_SCHEMES = ("chrome-extension://", "moz-extension://")


def is_extension_origin(origin: str | None) -> bool:
    """True if *origin* carries a browser-extension scheme."""
    if not origin:
        return False
    return origin.startswith(_EXT_SCHEMES[0]) or origin.startswith(_EXT_SCHEMES[1])


def extract_extension_id(origin: str | None) -> str | None:
    """Extract the extension ID from a ``chrome-extension://<id>`` or
    ``moz-extension://<id>`` origin.  Returns ``None`` for non-extension origins.
    """
    if not origin:
        return None
    for scheme in _EXT_SCHEMES:
        if origin.startswith(scheme):
            return origin[len(scheme) :].rstrip("/") or None
    return None


def extension_id_allowed(ext_id: str | None, allowed_ids: Iterable[str]) -> bool:
    """Whether an extension ID is in the allow-list (case-sensitive exact match).

    Returns ``False`` when *ext_id* is ``None`` or *allowed_ids* is empty —
    the caller (``AuthGate._extension_allowed``) is responsible for deciding
    whether to call this function at all based on ``verify_extension_id``.
    """
    if ext_id is None:
        return False
    allowed = list(allowed_ids)
    if not allowed:
        return False  # empty whitelist = no extensions permitted
    return ext_id in allowed


def header_present(headers: Mapping[str, str], names: Iterable[str] = _FORWARD_HEADERS) -> bool:
    lowered = {k.lower() for k in headers}
    return any(name in lowered for name in names)
