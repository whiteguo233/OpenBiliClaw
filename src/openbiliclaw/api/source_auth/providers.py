"""Per-platform source-auth providers.

``GET /api/sources/status`` used to be a 424-line if/elif chain that flattened
heterogeneous platforms by hand (spec D8). Adding a platform meant editing
that one function, and nothing forced the new branch to answer the same
questions as the previous providers — which is how the same ``state="ready"`` came
to mean "we counted three cookie field names" for B站 and "a file exists on
disk" for Reddit.

Here each platform owns one pure function ``auth_<slug>(ctx) -> SourceAuthContract``.
Every provider must answer the *same* questions, so a new platform cannot
quietly skip one:

* ``auth_required`` — does this source need a credential at all?
* ``credential`` / ``credential_origin`` — is one stored, and where?
* ``verification`` / ``verify_method`` — what was concluded, and *how*?
* ``legacy_state`` / ``legacy_logged_in`` — the compatibility verdict in the
  old vocabulary.

**The compatibility fields remain provider-owned, never globally derived.**
The old ``state`` is provably not a function of the orthogonal fields (see
``legacy.py``), so each provider preserves its platform-specific semantics.
When a provider gains stronger evidence, it may intentionally move within the
old vocabulary too; ``check_legacy_consistency`` asserts the two views never
contradict each other.

**No provider performs network I/O.** The status endpoint is polled every ~30s
by open settings pages; the two platforms with live probes (B站, 抖音) read a
cached verdict written by the verify action instead. See ``probe_cache`` for the
full rationale.

Two conventions keep the dimensions from re-merging (invariant I2):

* Structural verdicts live in ``credential`` ("this cookie is missing fields"),
  liveness verdicts live in ``verification`` ("the platform rejected it").
  Never encode the same fact in both.
* ``credential_origin`` describes where an *existing* credential lives; it is
  ``none`` whenever ``credential == "none"``.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.api.source_auth.contract import (
    CapabilityReadiness,
    SourceAuthContract,
    SourceCapabilityAuth,
)
from openbiliclaw.api.source_auth.probe_cache import (
    LIVE_PROBES,
    PROBE_FAIL_TTL_SECONDS,
    PROBE_OK_TTL_SECONDS,
    LiveProbeCache,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.api.source_auth.contract import (
        CapabilityReadinessState,
        Credential,
        CredentialOrigin,
        Verification,
    )
    from openbiliclaw.config import Config

# ── freshness windows ────────────────────────────────────────────────
# Window for trusting the extension's privacy-preserving login heartbeat. A
# live browser pushes on startup, on cookie changes, and on the periodic
# cookie-sync alarm; stale rows fall back to missing. Verified at the boundary
# on 2026-07-18: 71h -> ready, 73h -> stale (spec D2).
_XHS_LOGIN_FRESH_HOURS = 72
_ZHIHU_LOGIN_FRESH_HOURS = 72
_WEIBO_LOGIN_FRESH_HOURS = 72
_WEIBO_CAPABILITY_AUTH_MODES = {
    "discover": "anonymous",
    "profile": "login-required",
    "bootstrap": "login-required",
    "cookie-sync": "optional-credential",
}
_V2EX_LOGIN_FRESH_HOURS = 72
_LINUXDO_LOGIN_FRESH_HOURS = 72
_HEARTBEAT_TTL_SECONDS = 72 * 3600

# Live-probe verdict windows. Imported rather than redeclared: ``probe_cache``
# owns both the verdicts and their freshness policy, and ``runtime.init_prereqs``
# reads the same constants, so the guided-init page and the settings page cannot
# drift apart on when a verdict goes stale.
_PROBE_OK_TTL_SECONDS = PROBE_OK_TTL_SECONDS
_PROBE_FAIL_TTL_SECONDS = PROBE_FAIL_TTL_SECONDS

# The window a *user-visible* "已验证" stays fresh, distinct from the probe
# cache's reuse window above. The two answer different questions and must not
# share a constant:
#   * ``PROBE_OK_TTL_SECONDS`` (60s) throttles outbound probes — short on
#     purpose, so a settings page polling every 30s never fires a real request,
#     and 抖音's per-``msToken`` cookie churn can't storm the platform.
#   * ``_VERIFIED_FRESH_SECONDS`` drives ``verify_ttl_seconds`` → the "验证已
#     过期" badge. At 60s a user who clicked 测试连接 saw it flip to expired
#     within a minute — technically the probe window lapsed, but a login that
#     was live one minute ago is not meaningfully stale, and the copy read as a
#     problem where there was none.
# Calibration (2026-07-19): 6h. Chosen as a human-scale "recently confirmed"
# window — long enough that a verify earlier in a session still reads as fresh,
# short enough that a day-old verdict correctly invites a re-check. It is a UX
# threshold, not a security one: an expired badge only nudges a re-verify, it
# never grants or denies access. Reopen if the probe endpoints' real-world
# freshness turns out shorter (CLAUDE.md pitfall #3).
_VERIFIED_FRESH_SECONDS = 6 * 3600


def _rdt_ttl_seconds() -> int:
    """The credential lifetime the Reddit gate actually enforces.

    Read from ``reddit_tasks`` rather than duplicated: this used to be a
    literal ``7 * 24 * 3600`` and silently kept advertising 7 days after the
    gate moved 6h earlier (to stay clear of rdt-cli's own browser-refresh
    subprocess), so the settings page showed a green "凭据就绪" badge for six
    hours after the backend had already stopped calling rdt. Imported lazily —
    ``reddit_tasks`` pulls in the discovery engine, which this module does not
    want at import time.
    """
    from openbiliclaw.sources.reddit_tasks import RDT_CREDENTIAL_TTL_SECONDS

    return RDT_CREDENTIAL_TTL_SECONDS


# Human-readable detail for each X (twitter) health state.
_X_STATE_DETAIL = {
    "ok": "X 来源正常，cookie 有效。",
    "missing_cookie": "未检测到登录 —— 在浏览器登录 x.com，插件会自动同步 cookie。",
    "expired_cookie": "cookie 已过期 —— 请重新登录 x.com。",
    "rate_limited": (
        "cookie 正常，只是当前被 X 限流。已进入退避冷却，到点自动重试，无需手动操作。"
    ),
    "blocked": "请求被拒绝 (403) —— 账号可能受限或需要重新验证。",
}

# X health states that are a verdict about the credential, and what they mean.
# ``rate_limited`` / ``blocked`` deliberately map to themselves: the platform
# throttled or refused us, which says nothing about whether the cookie is valid.
_X_HEALTH_VERIFICATION: dict[str, Verification] = {
    "ok": "verified",
    "missing_cookie": "failed",
    "expired_cookie": "failed",
    "rate_limited": "rate_limited",
    "blocked": "blocked",
}

# rdt-cli credential states, split across the two dimensions they each speak
# to. Structural outcomes land in ``credential``; only the "usable right now"
# question lands in ``verification``. ``login_required`` (no file) and the
# default ``error`` (malformed file) are structural, so they leave the verdict
# ``unverified`` rather than claiming a check failed that never ran.
_RDT_CREDENTIAL: dict[str, Credential] = {
    "ready": "present",
    "stale": "present",
    "login_required": "none",
}
_RDT_VERIFICATION: dict[str, Verification] = {
    "ready": "verified",
    "stale": "stale",
}

# Sources scheduled by default when the config omits the flag. Only B站, the
# project's原生 source, is on unless switched off.
_ENABLED_BY_DEFAULT: dict[str, bool] = {"bilibili": True}

# The note rendered under the status chip on the two live-probe platforms,
# keyed on what the probe actually concluded.
#
# These used to be single constants written when neither platform could be
# probed at all. Once the frontends started reading the orthogonal fields, the
# 抖音 card rendered "接入：已验证" beside a badge reading "联网验证 · 刚刚" and a
# body still reading "需在实际任务中验证" — three sentences, on one card,
# disagreeing (caught on-device, not by any test, because ``detail`` was frozen
# only against the no-verdict case).
#
# The ``unverified`` entries are the original strings verbatim: that is the only
# state reachable before this branch existed, so the frozen legacy output is
# unchanged and the new wording only ever appears in states that are themselves
# new. Both tables are keyed by ``Verification``; ``rate_limited`` / ``blocked``
# are X-only and fall through to the ``unverified`` default.
_DOUYIN_DETAIL: dict[str, str] = {
    "verified": "已登录抖音（已通过抖音登录态接口联网确认）。",
    "failed": "抖音登录态已失效 —— 联网检查返回未登录，请在浏览器重新登录后由插件同步。",
    "stale": "上次联网确认已超出有效期，可点「测试连接」重新确认。",
    "unverified": "Cookie 已同步，需在实际任务中验证。",
}

_BILIBILI_READY_DETAIL: dict[str, str] = {
    "verified": "Cookie 就绪，已联网确认登录态。",
    "failed": (
        "Cookie 字段齐全，但联网检查返回未登录或已失效 —— 请重新登录 bilibili.com 后重新同步。"
    ),
    "stale": "Cookie 就绪；上次联网确认已超出有效期，可点「测试连接」重新确认。",
    "unverified": "Cookie 就绪（含 SESSDATA / bili_jct / DedeUserID）。",
}

# Contract-side ``detail`` for Bangumi when a personal token IS configured, keyed
# on what the ``/v0/me`` probe concluded. Bangumi is anonymous-public, so this
# text is about the *optional* token, never about being able to use the source
# at all — the source works with none of these.
#
# Note this is the contract's own ``detail``; the settings chip renders the
# discovery-health ``detail`` that ``_bangumi_status_item`` keeps (Bangumi
# carries a discovery-health axis the other providers do not).
_BANGUMI_TOKEN_DETAIL: dict[str, str] = {
    "verified": "个人令牌有效，已识别 Bangumi 账号，可读取你的私密收藏。",
    "failed": (
        "个人令牌已被 Bangumi 拒绝（可能过期或无效）—— 公开发现不受影响；如需私密收藏，"
        "请到 https://next.bgm.tv/demo/access-token 重新生成后替换。"
    ),
    "stale": "个人令牌上次联网确认已超出有效期，可点「测试连接」用 /v0/me 重新确认。",
    "unverified": "已保存个人令牌，尚未联网确认；可点「测试连接」用 /v0/me 验证。",
}

# Contract-side ``detail`` for Bangumi with no token — the anonymous default.
_BANGUMI_ANONYMOUS_DETAIL = "公开源 · 无需登录；可选填个人令牌以识别账号或读取私密收藏。"

_GITHUB_TOKEN_DETAIL: dict[str, str] = {
    "verified": "GitHub PAT 有效，已通过 GET /user 确认账号；公开仓库来源已就绪。",
    "failed": "GitHub PAT 已被拒绝（可能过期或无效）；公开仓库发现仍可匿名使用。",
    "stale": "GitHub PAT 上次联网确认已过期，可点「测试连接」重新确认。",
    "unverified": "已配置 GitHub PAT，尚未联网确认；公开仓库发现仍可使用。",
}
_GITHUB_ANONYMOUS_DETAIL = (
    "GitHub 公开仓库发现无需登录；可填写公开用户名导入 Starred 仓库，"
    "或配置可选 PAT 以确认身份并提高 API 限额。"
)

_V2EX_TOKEN_DETAIL: dict[str, str] = {
    "verified": "个人令牌有效，已识别 V2EX 账号，可使用 API 2.0 增强发现。",
    "failed": "V2EX 个人令牌被拒绝（可能过期或无效）；公开发现不受影响。",
    "stale": "V2EX 个人令牌上次联网确认已超出有效期，可点「测试连接」重新确认。",
    "unverified": "已保存 V2EX 个人令牌，尚未联网确认；可点「测试连接」验证。",
}
_V2EX_ANONYMOUS_DETAIL = "公开源 · 无需登录；可选填 PAT 以识别账号并增强 Node / Topic API。"


@dataclass
class SourceAuthContext:
    """Everything the providers may read, resolved once per request.

    Deliberately narrow: a provider gets the config and the database, nothing
    else. It cannot reach an HTTP client, so "the status endpoint never goes
    out" is enforced by what is in scope, not by reviewer discipline.
    """

    cfg: Config
    database: Any
    probes: LiveProbeCache = LIVE_PROBES
    _memo: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def sources(self) -> Any:
        return self.cfg.sources

    def source_cfg(self, slug: str) -> Any:
        return getattr(self.sources, slug, None)

    def has_conn(self) -> bool:
        return hasattr(self.database, "conn")

    def x_health(self) -> dict[str, Any]:
        """X health row, read at most once per request (used twice: auth + feed_paused)."""
        if "x_health" not in self._memo:
            health: dict[str, Any] = {}
            if self.has_conn():
                from openbiliclaw.storage.x_health import XSourceHealthStore

                health = XSourceHealthStore(self.database).get()
            self._memo["x_health"] = health
        return dict(self._memo["x_health"])


# ── shared helpers ───────────────────────────────────────────────────


def _login_heartbeat(
    database: Any,
    *,
    getter: str,
    fresh_hours: int,
) -> tuple[bool, str, bool]:
    """Read an extension login heartbeat -> ``(logged_in, when_iso, is_fresh)``.

    Shared by 小红书 and 知乎, which store the identical shape: a bool plus a
    timestamp, and never a byte of the actual cookie.

    ``when`` is passed through exactly as stored, never coerced. Coercing it
    would change the verdict for a non-string value: ``str(datetime(...))``
    happens to parse as ISO, so a stringified timestamp would read as *fresh*
    where the raw value raises below and correctly reads as stale.
    """
    stored, when = False, ""
    if hasattr(database, getter):
        try:
            stored, when = getattr(database, getter)()
        except Exception:  # pragma: no cover - defensive
            stored, when = False, ""

    fresh = False
    if when:
        try:
            parsed = datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            fresh = datetime.now(UTC) - parsed.astimezone(UTC) <= timedelta(hours=fresh_hours)
        except Exception:  # pragma: no cover - defensive
            fresh = False
    return bool(stored), when, fresh


def _probe_verdict(
    ctx: SourceAuthContext,
    slug: str,
    *,
    credential: Credential,
    cookie: str = "",
) -> tuple[Verification, str]:
    """Translate a cached live-probe verdict into ``(verification, verified_at)``.

    Never probes. Four refusals to overclaim:

    * no credential -> ``unverified``; there is nothing a verdict could be about.
    * a transport failure (``network_error``) -> ``unverified``, never ``failed``.
      A flaky proxy is not an expired cookie.
    * a lapsed *failure* -> ``unverified``, not ``stale``. ``stale`` means "was
      verified once and the window lapsed"; a lapsed rejection was never a
      success, and showing it as stale-green would be backwards.
    * a verdict whose fingerprint identifies a *different* credential ->
      ``unverified``. The stored cookie can change without passing through a
      write path (an env var, an edited data file), and reporting the previous
      one's verdict against it would be the read-side twin of the write-side
      cache defect.
    """
    if credential == "none":
        return "unverified", ""
    verdict = ctx.probes.peek(slug)
    if verdict is None or verdict.network_error:
        return "unverified", ""
    if ctx.probes.contradicts(verdict, _fingerprint(slug, cookie)):
        return "unverified", ""

    # Probe *reuse* stays deliberately short, but the status badge describes
    # how recently a successful login was confirmed.  Those are separate
    # promises: after 60s the next explicit verify must go back to the platform,
    # while the previous success remains honest user-visible evidence for 6h.
    ttl = _VERIFIED_FRESH_SECONDS if verdict.authenticated else _PROBE_FAIL_TTL_SECONDS
    if not verdict.is_fresh(ttl):
        return ("stale", verdict.checked_at) if verdict.authenticated else ("unverified", "")
    return ("verified" if verdict.authenticated else "failed"), verdict.checked_at


def _fingerprint(slug: str, cookie: str) -> str:
    """Identity digest of *cookie*, or "" when it cannot be computed.

    Imported lazily and failure-tolerant: this is a display-path helper, and a
    fingerprint we cannot compute must degrade to "cannot contradict" rather
    than take down a status poll.
    """
    if not cookie.strip():
        return ""
    with suppress(Exception):
        from openbiliclaw.api.source_auth.write import credential_fingerprint

        return credential_fingerprint(slug, cookie)
    return ""


def _probe_ttl(verification: Verification) -> int:
    """User-visible freshness window for a live-probe verdict (``verify_ttl_seconds``).

    A ``verified`` verdict stays fresh for the human-scale window; a ``failed``
    one keeps the short re-check window so a credential the user just repaired
    turns green promptly rather than sitting red. This is the *display* policy —
    it is not the probe-reuse throttle, which lives in ``probe_cache`` and stays
    at 60s so status polling never triggers outbound traffic.
    """
    return _PROBE_FAIL_TTL_SECONDS if verification == "failed" else _VERIFIED_FRESH_SECONDS


def _row_value(row: Any, key: str, index: int) -> Any:
    """Read a sqlite row by name when it supports it, else positionally."""
    return row[key] if hasattr(row, "keys") else row[index]


def _json_dict(raw: Any) -> dict[str, Any]:
    parsed_dict: dict[str, Any] = {}
    with suppress(Exception):
        parsed = json.loads(str(raw or "{}"))
        if isinstance(parsed, dict):
            parsed_dict = parsed
    return parsed_dict


def _utc_timestamp(value: object) -> datetime | None:
    """Parse a SQLite/ISO timestamp for evidence ordering."""

    text = str(value or "").strip()
    if not text:
        return None
    with suppress(ValueError, TypeError):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


# ── bilibili ─────────────────────────────────────────────────────────


def auth_bilibili(ctx: SourceAuthContext) -> SourceAuthContract:
    """B站: counts the three core login field names in the resolved cookie.

    config.toml is the mirror, ``data/bilibili_cookie.json`` the runtime store
    (CLI QR login writes only the file), so both are consulted through the one
    canonical resolver (invariant I1).
    """
    cfg = ctx.cfg
    configured = str(getattr(cfg.bilibili, "cookie", "") or "")
    cookie = configured
    origin: CredentialOrigin = "config" if configured.strip() else "none"
    if not cookie.strip():
        with suppress(Exception):
            from openbiliclaw.bilibili.auth import resolve_runtime_cookie

            cookie = resolve_runtime_cookie(
                data_dir=cfg.data_path,
                configured_cookie=configured,
            )
        origin = "data_file" if cookie.strip() else "none"

    has_fields = sum(1 for f in ("SESSDATA", "bili_jct", "DedeUserID") if f"{f}=" in cookie)
    credential: Credential = (
        "present" if has_fields >= 3 else ("invalid" if cookie.strip() else "none")
    )
    if credential == "none":
        origin = "none"

    # auth_method=none is a *scheduling* statement ("do not log in"), so it
    # flips auth_required without touching what is actually stored — the
    # credential fields above stay true either way (invariant I2).
    if str(getattr(cfg.bilibili, "auth_method", "cookie")) == "none":
        return SourceAuthContract(
            auth_required=False,
            credential=credential,
            credential_origin=origin,
            verification="unverified",
            verify_method="none",
            verify_ttl_seconds=None,
            can_verify_now=False,
            detail="未启用 B 站登录（auth_method=none）。",
            legacy_state="no_auth",
            legacy_logged_in=True,
        )

    verification, verified_at = _probe_verdict(
        ctx, "bilibili", credential=credential, cookie=cookie
    )
    if has_fields >= 3:
        legacy_state, legacy_logged_in = "ready", True
        # Only the ready branch varies: with a partial or absent cookie there is
        # nothing for a probe to have concluded, so those two keep their single
        # structural sentence.
        detail = _BILIBILI_READY_DETAIL.get(verification, _BILIBILI_READY_DETAIL["unverified"])
    elif cookie.strip():
        legacy_state, legacy_logged_in = "partial", False
        detail = "Cookie 已配置，但缺少部分登录字段，可能未完整登录。"
    else:
        legacy_state, legacy_logged_in = "missing", False
        detail = "未配置 Cookie —— 在浏览器登录 bilibili.com，插件会自动同步。"

    return SourceAuthContract(
        auth_required=True,
        credential=credential,
        credential_origin=origin,
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=_probe_ttl(verification),
        can_verify_now=credential != "none",
        detail=detail,
        legacy_state=legacy_state,
        legacy_logged_in=legacy_logged_in,
    )


# ── 小红书 ───────────────────────────────────────────────────────────


def auth_xiaohongshu(ctx: SourceAuthContext) -> SourceAuthContract:
    """小红书: the extension reports a login bool; the backend stores no cookie.

    Fetching is client-side, so the backend never stores or replays the raw
    ``web_session`` cookie — which is also why it can never verify this source
    itself (spec D2). ``xsec_token`` content rows are a secondary hint, never
    the login gate: a fresh account is logged in with zero tokenized rows.
    """
    stored, when, fresh = _login_heartbeat(
        ctx.database,
        getter="get_xhs_login_state",
        fresh_hours=_XHS_LOGIN_FRESH_HOURS,
    )

    tokens = 0
    if ctx.has_conn():
        try:
            row = ctx.database.conn.execute(
                "SELECT COUNT(*) FROM content_cache "
                "WHERE source_platform = 'xiaohongshu' "
                "AND content_url LIKE '%xsec_token=%'"
            ).fetchone()
            tokens = int(row[0]) if row else 0
        except Exception:  # pragma: no cover - defensive
            tokens = 0

    credential: Credential = "present" if stored else "none"
    common: dict[str, Any] = {
        "auth_required": True,
        "credential": credential,
        "credential_origin": "extension" if credential == "present" else "none",
        "verify_method": "browser_heartbeat",
        "verify_ttl_seconds": _HEARTBEAT_TTL_SECONDS,
        # The verify action asks the extension to re-report, which is useful
        # precisely when we have never heard from it.
        "can_verify_now": True,
    }

    if not when:
        return SourceAuthContract(
            **common,
            verification="unverified",
            detail="尚未收到小红书浏览器登录态；插件连接后会在本地同步。",
            legacy_state="unverified",
            legacy_logged_in=False,
        )
    if stored and fresh:
        token_hint = f"内容令牌 {tokens} 条。" if tokens else ""
        return SourceAuthContract(
            **common,
            verification="verified",
            verified_at=str(when),
            detail=f"已登录小红书。{token_hint}",
            legacy_state="ready",
            legacy_logged_in=True,
        )
    if stored:
        return SourceAuthContract(
            **common,
            verification="stale",
            verified_at=str(when),
            detail="小红书登录态已过期，请连接插件刷新本地状态。",
            legacy_state="stale",
            legacy_logged_in=False,
        )
    # The browser actively told us it is logged out — a real negative verdict,
    # which is what separates this from the "never heard from it" case above.
    return SourceAuthContract(
        **common,
        verification="failed",
        verified_at=when,
        detail="未检测到小红书登录 —— 在浏览器登录小红书后插件会自动同步。",
        legacy_state="missing",
        legacy_logged_in=False,
    )


# ── 抖音 ─────────────────────────────────────────────────────────────


def auth_douyin(ctx: SourceAuthContext) -> SourceAuthContract:
    """抖音: cookie from env or ``data/douyin_cookie.json``.

    ``verify_method`` is ``live_probe`` because 抖音 *does* have a clean login
    discriminator — ``/aweme/v1/web/user/profile/self/`` answers
    ``status_code=0`` + a real uid when logged in and ``status_code=8``
    ("用户未登录") when not (spec D11, refuting the old "no stable nav endpoint"
    claim). The verdict is read from the probe cache, never fetched here.

    The compatibility fields follow the same cached verdict as the orthogonal
    contract. Keeping ``state='unverified'`` after a successful live probe made
    ``GET /api/sources/status`` contradict itself for legacy/agent consumers.
    """
    cfg = ctx.cfg
    dy_cfg = ctx.source_cfg("douyin")
    cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
    cookie = ""
    try:
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        cookie = resolve_douyin_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
    except Exception:  # pragma: no cover - defensive
        cookie = ""

    if not cookie.strip():
        return SourceAuthContract(
            auth_required=True,
            credential="none",
            credential_origin="none",
            verification="unverified",
            verify_method="live_probe",
            verify_ttl_seconds=_PROBE_OK_TTL_SECONDS,
            can_verify_now=False,
            detail="未配置 Cookie —— 设置环境变量，或登录抖音后由插件同步。",
            legacy_state="missing",
            legacy_logged_in=False,
        )

    origin: CredentialOrigin = "env" if os.environ.get(cookie_env, "").strip() else "data_file"
    verification, verified_at = _probe_verdict(ctx, "douyin", credential="present", cookie=cookie)
    if verification == "verified":
        legacy_state, legacy_logged_in = "ready", True
    elif verification == "stale":
        legacy_state, legacy_logged_in = "stale", False
    else:
        legacy_state, legacy_logged_in = "unverified", False
    return SourceAuthContract(
        auth_required=True,
        credential="present",
        credential_origin=origin,
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=_probe_ttl(verification),
        can_verify_now=True,
        detail=_DOUYIN_DETAIL.get(verification, _DOUYIN_DETAIL["unverified"]),
        legacy_state=legacy_state,
        legacy_logged_in=legacy_logged_in,
    )


# ── YouTube ──────────────────────────────────────────────────────────


def auth_youtube(ctx: SourceAuthContext) -> SourceAuthContract:
    """YouTube: a public source that legitimately needs no login.

    ``verify_method`` must stay ``none`` here — not because verification is
    hard, but because there is nothing to verify. That is the only honest use
    of ``none`` for a source with no credential to verify (invariant I3).
    """
    return SourceAuthContract(
        auth_required=False,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="none",
        verify_ttl_seconds=None,
        can_verify_now=False,
        detail="公开源 · 无需登录。",
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


# ── TikTok ───────────────────────────────────────────────────────────


_TIKTOK_GUEST_DETAIL = (
    "公开源 · 无需登录。Web API 后端以 TikTok 自己签发的访客身份读取公开数据"
    "（推荐流 / 创作者 / 话题标签）；可选登录 Cookie 仅用于解锁关键词搜索与更高限额。"
)
_TIKTOK_COOKIE_DETAIL = (
    "已配置可选登录 Cookie（访客身份仍是默认路径）：关键词搜索与更高限额已解锁。"
    "TikTok 凭据暂无主动探针，验证状态以运行时表现为准。"
)


def auth_tiktok(ctx: SourceAuthContext) -> SourceAuthContract:
    """TikTok: a public source with an *optional* login cookie.

    Guest identity is a legitimate, complete operating mode (the web API
    backend reads public feed / creator / hashtag data anonymously), so
    ``auth_required`` stays ``False`` and ``legacy_state`` stays ``no_auth``
    in both branches — same public-source treatment as YouTube.

    What makes it *not* exactly YouTube is the optional login cookie
    (``[sources.tiktok].cookie_env`` / ``data/tiktok_cookie.json``), which
    unlocks keyword search (gated for guests upstream, verified 2026-10-03)
    and higher rate limits. Unlike Bangumi's token there is no live probe
    for it yet, so ``verify_method`` stays ``none`` even when a cookie is
    configured — reporting ``live_probe`` without a probe would be the
    I3 violation the contract exists to prevent. The configured state is
    still surfaced honestly through ``credential`` / ``credential_origin``
    and the detail string.
    """
    tt_cfg = ctx.source_cfg("tiktok")
    cookie_env = str(getattr(tt_cfg, "cookie_env", "") or "OPENBILICLAW_TIKTOK_COOKIE")
    cookie = ""
    try:
        from openbiliclaw.sources.tiktok_auth import resolve_tiktok_cookie

        cookie = resolve_tiktok_cookie(data_dir=ctx.cfg.data_path, cookie_env=cookie_env)
    except Exception:  # pragma: no cover - defensive
        cookie = ""

    if not cookie.strip():
        return SourceAuthContract(
            auth_required=False,
            credential="none",
            credential_origin="none",
            verification="unverified",
            verify_method="none",
            verify_ttl_seconds=None,
            can_verify_now=False,
            detail=_TIKTOK_GUEST_DETAIL,
            legacy_state="no_auth",
            legacy_logged_in=True,
        )

    origin: CredentialOrigin = "env" if os.environ.get(cookie_env, "").strip() else "data_file"
    return SourceAuthContract(
        # Still False: a configured cookie is an *enhancement* (search,
        # limits), never a requirement — guest identity covers discovery.
        auth_required=False,
        credential="present",
        credential_origin=origin,
        verification="unverified",
        verify_method="none",
        verify_ttl_seconds=None,
        can_verify_now=False,
        detail=_TIKTOK_COOKIE_DETAIL,
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


# ── Weibo ───────────────────────────────────────────────────────────


def auth_weibo(ctx: SourceAuthContext) -> SourceAuthContract:
    """Weibo: anonymous public discovery plus browser-backed bootstrap."""

    stored, when, fresh = _login_heartbeat(
        ctx.database,
        getter="get_weibo_login_state",
        fresh_hours=_WEIBO_LOGIN_FRESH_HOURS,
    )
    capabilities = _weibo_capabilities(
        "ready" if stored and fresh else "stale" if stored and when else "login_required"
    )
    # An absent heartbeat means there is no browser credential to verify yet.
    # Keep the legacy contract honest (I3): ``browser_heartbeat`` is only
    # reported once the extension has actually recorded a login-state check.
    verify_method = "browser_heartbeat" if stored else "none"
    heartbeat: dict[str, Any] = {
        "auth_required": False,
        "verify_method": verify_method,
        "verify_ttl_seconds": _HEARTBEAT_TTL_SECONDS if stored else None,
        "can_verify_now": bool(stored),
        "capabilities": capabilities,
        "legacy_state": "no_auth",
        "legacy_logged_in": True,
    }
    if stored and fresh:
        return SourceAuthContract(
            **heartbeat,
            credential="present",
            credential_origin="extension",
            verification="verified",
            verified_at=str(when),
            detail="微博公开发现可匿名；浏览器已登录，可导入本人只读事件。",
        )
    if stored and when:
        return SourceAuthContract(
            **heartbeat,
            credential="present",
            credential_origin="extension",
            verification="stale",
            verified_at=str(when),
            detail="微博公开发现可匿名；浏览器登录态已过期，请连接插件刷新。",
        )
    return SourceAuthContract(
        **heartbeat,
        credential="none",
        credential_origin="none",
        verification="unverified",
        detail="微博公开发现可匿名；初始化本人收藏、关注和互动需要登录微博并连接插件。",
    )


def _weibo_capabilities(
    personal_readiness: CapabilityReadiness,
) -> dict[str, SourceCapabilityAuth]:
    """Return Weibo's anonymous-discover/private-bootstrap matrix."""

    return {
        "discover": SourceCapabilityAuth(
            mode="anonymous",
            required=True,
            readiness="ready",
            detail="搜索、热搜和公开作者时间线无需登录。",
        ),
        "profile": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            readiness=personal_readiness,
            detail="初始化本人收藏、关注和互动需要微博浏览器登录态。",
        ),
        "bootstrap": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            readiness=personal_readiness,
            detail="个人事件只在微博同源浏览器任务中读取，后端不接收 Cookie。",
        ),
        "cookie-sync": SourceCapabilityAuth(
            mode="optional-credential",
            required=False,
            readiness=personal_readiness,
            detail="插件仅上报布尔登录状态；游客 SUB 不算登录凭据。",
        ),
    }


def weibo_capability_readiness(ctx: SourceAuthContext) -> dict[str, SourceCapabilityAuth]:
    """Public readiness helper used by guided init and source status."""

    return auth_weibo(ctx).capabilities


# ── X (Twitter) ──────────────────────────────────────────────────────


def auth_twitter(ctx: SourceAuthContext) -> SourceAuthContract:
    """X: explicit verification uses a read-only authenticated-account probe.

    Discovery traffic still records 401 / 403 / 429 outcomes in the health
    store, while the settings-page action can now refresh that verdict on
    demand through ``twitter-cli``'s read-only ``fetch_me`` path.
    """
    cfg = ctx.cfg
    tw_cfg = ctx.source_cfg("twitter")
    health = ctx.x_health()

    state = "missing_cookie"
    feed_paused = False
    if ctx.has_conn():
        state = str(health.get("state", "ok"))
        feed_paused = bool(health.get("feed_paused", False))

    cookie_env = str(getattr(tw_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE"))
    cookie = ""
    with suppress(Exception):
        from openbiliclaw.sources.x_auth import resolve_x_cookie

        cookie = resolve_x_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
    # The health row defaults to ``ok`` before any fetch has run, so an ``ok``
    # without an actual cookie would falsely report a logged-in source.
    if state == "ok" and not cookie.strip():
        state = "missing_cookie"

    detail = _X_STATE_DETAIL.get(state, f"X 来源状态：{state}。")

    credential: Credential = "present" if cookie.strip() else "none"
    # No credential means no verdict to hold, whatever the health row says.
    verification: Verification = (
        _X_HEALTH_VERIFICATION.get(state, "unverified") if credential == "present" else "unverified"
    )

    # ``ok`` means "a real request succeeded", so there has to have been some.
    # The health row is *created* with ``state='ok'``, which made a
    # first-ever cookie — including one that expired months ago — report
    # ``verified`` before a single request had ever gone out. That is a
    # fabricated verdict. ``ok`` is therefore only a verdict once
    # ``record_success`` has stamped a real one; until then the honest answer
    # is that we have not asked.
    #
    # The negative states need no such guard: they are only ever written by
    # ``record_error``, i.e. by traffic that genuinely happened.
    #
    # The success must also be attributable to *this* cookie. Recording only
    # "the platform succeeded" let a swapped-in, never-used credential inherit
    # the previous one's verdict together with its timestamp — the same mistake
    # as keying the live-probe cache on the platform slug, one store over. The
    # fingerprint is compared rather than a write-path hook consulted, because a
    # cookie can also change by env var or an edited data file, and those pass
    # through no hook at all. An empty stored fingerprint (a success recorded
    # before this existed, or by a reader-built store) cannot be attributed and
    # so is not evidence.
    last_success_at = str(health.get("last_success_at", "") or "").strip()
    earned_by = str(health.get("last_success_credential", "") or "").strip()
    if verification == "verified" and (
        not last_success_at or not earned_by or earned_by != _fingerprint("twitter", cookie)
    ):
        verification = "unverified"
        # ...and the prose has to follow the verdict, or this fix re-creates the
        # 抖音 contradiction on the next card over: a chip reading 待验证 beside
        # a body still asserting "cookie 有效". ``_X_STATE_DETAIL['ok']`` was
        # written when ``ok`` was taken at face value. This is the one legacy
        # ``detail`` this wave moves on purpose — the frozen case is updated
        # with it, and the confirmed path keeps the original string byte for
        # byte, so a genuinely verified X reads exactly as it always did.
        detail = "已配置 X Cookie，但还没有成功的真实请求可以确认登录态。"

    if feed_paused:
        detail += " 其中 For-You 子流因连续失败已临时熔断，下次抓取成功会自动恢复。"

    origin: CredentialOrigin = "none"
    if credential == "present":
        origin = "env" if os.environ.get(cookie_env, "").strip() else "data_file"

    # A confirmed verdict is dated by the success itself; every other verdict
    # by the row's last write, which is when that failure was observed.
    verified_at = ""
    if verification == "verified":
        verified_at = last_success_at
    elif verification != "unverified":
        verified_at = str(health.get("updated_at", ""))

    return SourceAuthContract(
        auth_required=True,
        credential=credential,
        credential_origin=origin,
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=None,
        can_verify_now=credential == "present",
        detail=detail,
        legacy_state=state,
        legacy_logged_in=state == "ok",
    )


# ── 知乎 ─────────────────────────────────────────────────────────────


def auth_zhihu(ctx: SourceAuthContext) -> SourceAuthContract:
    """知乎: extension heartbeat first, then a fallback to task history.

    The fallback must declare ``verify_method="task_history"`` rather than
    impersonating a heartbeat (invariant I3): "a task succeeded an hour ago" is
    weaker evidence than "the browser holds a login cookie right now", and
    collapsing the two is how a green light stops meaning anything.
    """
    stored, when, fresh = _login_heartbeat(
        ctx.database,
        getter="get_zhihu_login_state",
        fresh_hours=_ZHIHU_LOGIN_FRESH_HOURS,
    )
    # ``credential_origin`` is set per return rather than shared: it must track
    # ``credential``, not the heartbeat's presence, or a logged-out browser
    # would report "credential none, stored in the extension".
    heartbeat: dict[str, Any] = {
        "auth_required": True,
        "verify_method": "browser_heartbeat",
        "verify_ttl_seconds": _HEARTBEAT_TTL_SECONDS,
        "can_verify_now": True,
    }

    if when:
        if stored and fresh:
            return SourceAuthContract(
                **heartbeat,
                credential="present",
                credential_origin="extension",
                verification="verified",
                verified_at=str(when),
                detail="已登录知乎。",
                legacy_state="ready",
                legacy_logged_in=True,
            )
        if stored:
            return SourceAuthContract(
                **heartbeat,
                credential="present",
                credential_origin="extension",
                verification="stale",
                verified_at=str(when),
                detail="知乎登录态已过期，请连接插件刷新本地状态。",
                legacy_state="stale",
                legacy_logged_in=False,
            )
        return SourceAuthContract(
            **heartbeat,
            credential="none",
            credential_origin="none",
            verification="failed",
            verified_at=str(when),
            detail="浏览器最近同步的状态为未登录知乎。",
            legacy_state="missing",
            legacy_logged_in=False,
        )

    row = None
    if ctx.has_conn():
        try:
            row = ctx.database.conn.execute(
                """
                SELECT type, status, result_json, created_at, completed_at
                FROM zhihu_tasks
                WHERE status IN ('pending', 'in_progress', 'completed', 'failed')
                ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
        except Exception:
            row = None

    if row is not None:
        task_type = str(_row_value(row, "type", 0))
        status = str(_row_value(row, "status", 1))
        payload = _json_dict(_row_value(row, "result_json", 2))
        # Timestamp columns feed only the display-only ``verified_at``. The
        # legacy branch never read them, so a row that cannot supply them must
        # cost the timestamp and nothing else — without this guard an unexpected
        # row shape would raise out of the handler and 500 the entire source-status response.
        at = ""
        with suppress(Exception):
            at = str(_row_value(row, "completed_at", 4) or _row_value(row, "created_at", 3) or "")
        items = payload.get("items")
        item_count = len(items) if isinstance(items, list) else 0
        error_code = str(payload.get("error", "") or "").strip()
        debug = payload.get("debug")
        login_required = error_code == "zhihu_login_required" or (
            isinstance(debug, dict) and bool(debug.get("login_required"))
        )
        history: dict[str, Any] = {
            "auth_required": True,
            "verify_method": "task_history",
            # A past task outcome has no freshness window of its own — it is
            # simply the last thing that happened.
            "verify_ttl_seconds": None,
            # The verify action itself is still a heartbeat request; only the
            # *current* verdict came from history.
            "can_verify_now": True,
        }
        if status == "completed":
            return SourceAuthContract(
                **history,
                credential="present",
                credential_origin="extension",
                verification="verified",
                verified_at=at,
                detail=f"最近任务完成（{task_type}，{item_count} 条）。",
                legacy_state="ready",
                legacy_logged_in=True,
            )
        if login_required:
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="failed",
                verified_at=at,
                detail="最近知乎任务提示需要登录知乎。请在当前浏览器登录知乎后重试。",
                legacy_state="missing",
                legacy_logged_in=False,
            )
        if status == "failed":
            suffix = f"：{error_code}" if error_code else ""
            # An operational failure (timeout, parse error) says nothing about
            # the login, so the verdict stays ``unverified`` rather than
            # blaming the credential.
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=f"最近知乎任务失败{suffix}。可在浏览器登录态正常后重试。",
                legacy_state="partial",
                legacy_logged_in=False,
            )
        if status in {"pending", "in_progress"}:
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=f"知乎任务正在等待插件执行（{task_type} / {status}）。",
                legacy_state="unverified",
                legacy_logged_in=False,
            )

    return SourceAuthContract(
        **heartbeat,
        credential="none",
        credential_origin="none",
        verification="unverified",
        detail=(
            "浏览器插件登录态源 · 尚未看到知乎任务结果，保存后可运行 init 或 discover 验证登录态。"
        ),
        legacy_state="unverified",
        legacy_logged_in=False,
    )


# ── Reddit ───────────────────────────────────────────────────────────


def _reddit_extension(ctx: SourceAuthContext) -> SourceAuthContract:
    """Reddit via the OpenBiliClaw extension, credential still in rdt-cli's file."""
    base: dict[str, Any] = {"auth_required": True, "can_verify_now": True}

    cookie_names: tuple[str, ...] = ()
    with suppress(Exception):
        from openbiliclaw.sources.reddit_tasks import rdt_credential_cookie_names

        cookie_names = rdt_credential_cookie_names()

    if "reddit_session" in cookie_names:
        return SourceAuthContract(
            **base,
            credential="present",
            credential_origin="external_cli",
            verification="verified",
            verify_method="local_file",
            verified_at=_rdt_saved_at(),
            verify_ttl_seconds=_rdt_ttl_seconds(),
            detail="已登录 Reddit（reddit_session 已同步）。",
            legacy_state="ready",
            legacy_logged_in=True,
        )

    default = SourceAuthContract(
        **base,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="local_file",
        verify_ttl_seconds=_rdt_ttl_seconds(),
        detail="Reddit 使用 OpenBiliClaw 插件登录态；尚未看到成功任务结果。",
        legacy_state="unverified",
        legacy_logged_in=False,
    )

    db_conn = getattr(ctx.database, "conn", None)
    if db_conn is None or not hasattr(db_conn, "execute"):
        return default

    history: dict[str, Any] = {
        "auth_required": True,
        "can_verify_now": True,
        "credential": "none",
        "credential_origin": "none",
        "verify_method": "task_history",
        "verify_ttl_seconds": None,
    }
    with suppress(Exception):
        row = db_conn.execute(
            """
            SELECT type, status, result_json,
                   COALESCE(completed_at, claimed_at, created_at)
            FROM reddit_tasks
            ORDER BY COALESCE(completed_at, claimed_at, created_at) DESC,
                     created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            task_type = str(_row_value(row, "type", 0))
            status = str(_row_value(row, "status", 1))
            payload = _json_dict(_row_value(row, "result_json", 2))
            # The COALESCE column has no stable name; index works for both
            # sqlite3.Row and a plain tuple. Guarded separately from the
            # enclosing suppress: this timestamp is display-only, and letting it
            # abort the block would silently downgrade a completed task back to
            # the "no task seen yet" default.
            at = ""
            with suppress(Exception):
                at = str(row[3] or "")
            error_code = str(payload.get("error", "") or "")
            debug = payload.get("debug")
            login_required = error_code == "reddit_login_required" or bool(
                debug.get("login_required") if isinstance(debug, dict) else False
            )
            if status == "completed":
                return SourceAuthContract(
                    **{**history, "credential": "present", "credential_origin": "extension"},
                    verification="verified",
                    verified_at=at,
                    detail=f"最近 Reddit 插件任务已完成（{task_type}）。",
                    legacy_state="ready",
                    legacy_logged_in=True,
                )
            if login_required:
                return SourceAuthContract(
                    **history,
                    verification="failed",
                    verified_at=at,
                    detail="最近 Reddit 任务提示需要登录 Reddit。请在当前浏览器登录后重试。",
                    legacy_state="missing",
                    legacy_logged_in=False,
                )
            if status == "failed":
                suffix = f"：{error_code}" if error_code else ""
                return SourceAuthContract(
                    **history,
                    verification="unverified",
                    detail=f"最近 Reddit 插件任务失败{suffix}。",
                    legacy_state="partial",
                    legacy_logged_in=False,
                )
            if status in {"pending", "in_progress"}:
                return SourceAuthContract(
                    **history,
                    verification="unverified",
                    detail=f"Reddit 任务正在等待插件执行（{task_type} / {status}）。",
                    legacy_state="unverified",
                    legacy_logged_in=False,
                )
    return default


def _rdt_saved_at() -> str:
    """When rdt-cli last wrote its credential file (ISO-8601, "" if unknown)."""
    with suppress(Exception):
        from openbiliclaw.sources.reddit_tasks import rdt_credential_saved_at

        return rdt_credential_saved_at()
    return ""


def auth_reddit(ctx: SourceAuthContext) -> SourceAuthContract:
    """Reddit: a third-party CLI owns the credential, so we only read its file.

    ``local_file`` is the honest method name — ``local_reddit_credential_status``
    explicitly never invokes ``rdt`` and never touches the network, so "ready"
    here means "a file exists, has reddit_session, and is younger than 7 days".
    """
    rd_cfg = ctx.source_cfg("reddit")
    backend = str(getattr(rd_cfg, "backend", "rdt") or "rdt").strip().lower()

    if backend in {"extension", "openbiliclaw", "plugin"}:
        return _reddit_extension(ctx)

    if backend == "rdt":
        try:
            from openbiliclaw.sources.reddit_tasks import local_reddit_credential_status

            status = local_reddit_credential_status()
        except Exception:
            return SourceAuthContract(
                auth_required=True,
                credential="none",
                credential_origin="none",
                verification="unverified",
                verify_method="local_file",
                verify_ttl_seconds=_rdt_ttl_seconds(),
                can_verify_now=False,
                detail="Reddit 命令后端状态不可用，请检查 opencli / rdt 安装。",
                legacy_state="missing",
                legacy_logged_in=False,
            )

        state = status.state
        credential: Credential = _RDT_CREDENTIAL.get(state, "invalid")
        verification: Verification = _RDT_VERIFICATION.get(state, "unverified")
        return SourceAuthContract(
            auth_required=True,
            credential=credential,
            credential_origin="external_cli" if credential != "none" else "none",
            verification=verification,
            verify_method="local_file",
            verified_at=_rdt_saved_at() if verification != "unverified" else "",
            verify_ttl_seconds=_rdt_ttl_seconds(),
            can_verify_now=credential != "none",
            detail=status.message,
            legacy_state=state,
            legacy_logged_in=state == "ready",
        )

    # An unrecognised backend: we genuinely cannot check it, and saying so is
    # the point of ``verify_method="none"``.
    return SourceAuthContract(
        auth_required=True,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="none",
        verify_ttl_seconds=None,
        can_verify_now=False,
        detail=f"Reddit {backend} 后端已配置；状态页不执行命令探测，请通过显式任务验证。",
        legacy_state="unverified",
        legacy_logged_in=False,
    )


# ── Bangumi ──────────────────────────────────────────────────────────


def auth_bangumi(ctx: SourceAuthContext) -> SourceAuthContract:
    """Bangumi: anonymous-public, with an *optional* personal token it can verify.

    Bangumi is the one source that breaks the ``auth_required`` boolean. It is
    the honest ``False`` — the discovery pipeline reads public collections and
    rankings straight off the official v0 API with no credential at all, so a
    user is never told 「需要登录」 and never sees a 「失效 / 待验证」 warning for
    not having a token. That is the same public-source treatment as YouTube, and
    it is why the no-token branch below is byte-for-byte YouTube's shape.

    What makes it *not* YouTube is the optional personal access token
    (https://next.bgm.tv/demo/access-token). When one is configured it unlocks
    private collections and identifies the account, and — unlike YouTube — it
    *can* be checked: ``GET /v0/me`` returns the account for a valid token and
    ``BangumiAPIError(code='unauthorized')`` for a missing / wrong / expired one.
    A stripped-control run on 2026-07-19 pinned the discriminator (invariant I3 /
    §0.1): real token → ``username='215952'``, forged token → ``unauthorized``,
    no token → ``unauthorized`` — a genuine difference *between* the groups, not
    one group merely looking normal. So a configured token legitimately reports
    ``verify_method='live_probe'`` and the verdict is read from the shared probe
    cache exactly as B站 / 抖音's cookie verdicts are.

    The two states therefore differ in the one field that can honestly move:

    * no token  → ``verify_method='none'`` — nothing to verify, like YouTube.
    * has token → ``verify_method='live_probe'`` — the /v0/me probe backs it.

    ``auth_required`` stays ``False`` in both, because you never *need* a token.
    ``legacy_state`` stays ``'no_auth'`` for the same reason; the discovery-health
    string (``尚未运行`` / cooldown / ``rejected``) and the ``token_state`` axis
    live on the ``SourceStatusItem`` that ``_bangumi_status_item`` assembles, not
    in this contract — Bangumi carries a discovery dimension the other seven do
    not, and folding it back into ``legacy_state`` would re-create the D1
    conflation the contract exists to remove.

    The shared renderer checks ``hasVerifiableCredential()`` before applying the
    anonymous-source shortcut. No token therefore renders 「无需登录」, while a
    configured token keeps its verified/failed/unverified verdict and persistent
    evidence badge even though ``auth_required`` remains false. Runtime rejection
    still outranks cached positive evidence through the separate ``token_state``
    axis; see ``docs/modules/source-auth.md`` for the complete precedence rules.
    """
    bgm = ctx.source_cfg("bangumi")
    token = str(getattr(bgm, "access_token", "") or "").strip()

    if not token:
        return SourceAuthContract(
            auth_required=False,
            credential="none",
            credential_origin="none",
            verification="unverified",
            verify_method="none",
            verify_ttl_seconds=None,
            can_verify_now=False,
            detail=_BANGUMI_ANONYMOUS_DETAIL,
            legacy_state="no_auth",
            legacy_logged_in=True,
        )

    verification, verified_at = _probe_verdict(ctx, "bangumi", credential="present", cookie=token)
    return SourceAuthContract(
        # Still False: a configured token is an *enhancement*, not a requirement.
        auth_required=False,
        credential="present",
        credential_origin="config",
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=_probe_ttl(verification),
        can_verify_now=True,
        detail=_BANGUMI_TOKEN_DETAIL.get(verification, _BANGUMI_TOKEN_DETAIL["unverified"]),
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


# ── GitHub ───────────────────────────────────────────────────────────


def _github_capabilities(
    *,
    has_account_scope: bool,
    credential_rejected: bool = False,
) -> dict[str, SourceCapabilityAuth]:
    """Return the frozen optional-credential capability matrix for GitHub."""

    if credential_rejected:
        account_state: CapabilityReadinessState = "unavailable"
        account_ready = False
        account_detail = "GitHub PAT 已被拒绝；更新或清除 PAT 后才能导入公开 Starred 仓库。"
    else:
        account_state = "ready" if has_account_scope else "identity_required"
        account_ready = has_account_scope
        account_detail = (
            "已提供公开用户名或 PAT，可只读加载公开 Starred 仓库。"
            if has_account_scope
            else "公开发现已就绪；画像 / 初始化还需要公开用户名或可选 PAT。"
        )
    return {
        "discover": SourceCapabilityAuth(
            mode="optional-credential",
            required=True,
            ready=True,
            state="ready",
            detail="公开仓库 Search API 可匿名使用；PAT 仅用于身份确认和提高限额。",
        ),
        "profile": SourceCapabilityAuth(
            mode="optional-credential",
            required=True,
            ready=account_ready,
            state=account_state,
            detail=account_detail,
        ),
        "bootstrap": SourceCapabilityAuth(
            mode="optional-credential",
            required=True,
            ready=account_ready,
            state=account_state,
            detail=account_detail,
        ),
    }


def auth_github(ctx: SourceAuthContext) -> SourceAuthContract:
    """GitHub: public repositories, optional config/env PAT, no private scope."""

    from openbiliclaw.sources.github_client import resolve_github_access_token

    cfg = ctx.source_cfg("github")
    config_token = str(getattr(cfg, "access_token", "") or "").strip()
    # Deliberately ignore cfg.token_env here. The only accepted variable is the
    # product-specific one below; generic GITHUB_TOKEN / GH_TOKEN and renamed
    # config values must never leak into this source.
    token, origin = resolve_github_access_token(
        config_token=config_token,
        token_env="OPENBILICLAW_GITHUB_TOKEN",
    )
    username = str(getattr(cfg, "username", "") or "").strip()
    if not token:
        capabilities = _github_capabilities(has_account_scope=bool(username))
        detail = _GITHUB_ANONYMOUS_DETAIL
        if username:
            detail += f" 当前公开账号范围：{username}（不代表所有权验证）。"
        return SourceAuthContract(
            auth_required=False,
            credential="none",
            credential_origin="none",
            verification="unverified",
            verify_method="none",
            verify_ttl_seconds=None,
            can_verify_now=False,
            detail=detail,
            capabilities=capabilities,
            legacy_state="no_auth",
            legacy_logged_in=True,
        )

    verification, verified_at = _probe_verdict(
        ctx,
        "github",
        credential="present",
        cookie=token,
    )
    # A formal discovery request is also authoritative credential evidence.
    # Merge its fingerprint-scoped 401 marker so the polled source status and
    # guided-init checklist cannot advertise profile/bootstrap readiness after
    # the same PAT was rejected outside the explicit "test connection" path.
    if ctx.has_conn():
        from openbiliclaw.runtime.github_producer import github_token_rejected

        if github_token_rejected(ctx.database, token):
            verification = "failed"
            verified_at = ""
    capabilities = _github_capabilities(
        has_account_scope=True,
        credential_rejected=verification == "failed",
    )
    return SourceAuthContract(
        auth_required=False,
        credential="present",
        credential_origin="env" if origin == "env" else "config",
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=_probe_ttl(verification),
        can_verify_now=True,
        detail=_GITHUB_TOKEN_DETAIL.get(
            verification,
            _GITHUB_TOKEN_DETAIL["unverified"],
        ),
        capabilities=capabilities,
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


V2EX_CAPABILITY_AUTH_MODES: dict[str, tuple[str, bool]] = {
    "discover": ("optional-credential", True),
    "profile": ("login-required", True),
    "bootstrap": ("login-required", True),
    "incremental": ("login-required", True),
    "cookie-sync": ("login-required", False),
}


def _v2ex_capability_state(
    ctx: SourceAuthContext,
    *,
    configured_username: object | None = None,
) -> tuple[dict[str, SourceCapabilityAuth], Any, str]:
    """Return V2EX capability readiness without performing network I/O."""

    from openbiliclaw.sources.v2ex_identity import (
        resolve_v2ex_identity_state,
    )

    browser_logged_in, browser_when, browser_fresh = _login_heartbeat(
        ctx.database,
        getter="get_v2ex_login_state",
        fresh_hours=_V2EX_LOGIN_FRESH_HOURS,
    )
    resolution = resolve_v2ex_identity_state(
        cfg=ctx.cfg,
        database=ctx.database,
        probes=ctx.probes,
        configured_username=configured_username,
    )
    browser_detail = ""
    if browser_logged_in and browser_fresh:
        browser_detail = "浏览器已登录"
        browser_username = resolution.claims.get("browser", "")
        if browser_username:
            browser_detail += f" · {browser_username}"
    elif browser_when and not browser_fresh:
        browser_detail = "浏览器登录态已过期"

    discover = SourceCapabilityAuth(
        mode="optional-credential",
        required=True,
        ready=True,
        state="ready",
        detail="公开 API 与 Feed 可匿名发现；PAT 仅用于增强。",
    )
    private_state: CapabilityReadinessState
    if resolution.status == "identity_mismatch":
        private_state = "identity_mismatch"
        private_detail = "账号证据冲突；请先确认要使用的 V2EX 账号。"
    elif not browser_logged_in:
        private_state = "login_required"
        private_detail = "浏览器尚未登录 V2EX；公开发现仍可使用。"
    elif not browser_fresh:
        private_state = "stale"
        private_detail = "浏览器登录心跳已过期；请打开 V2EX 后让扩展重新同步。"
    elif not resolution.claims.get("browser"):
        private_state = "identity_required"
        private_detail = "已检测到登录态，但尚未可靠识别当前 V2EX 用户。"
    elif not resolution.private_bootstrap_available:
        private_state = "identity_required"
        private_detail = "浏览器账号尚未通过后端身份解析，账号初始化暂停。"
    else:
        private_state = "ready"
        private_detail = f"浏览器账号 {resolution.username} 已就绪，可只读初始化。"

    private_ready = private_state == "ready"
    active_profile_username = ""
    if hasattr(ctx.database, "get_v2ex_profile_identity"):
        try:
            active_profile_username = str(ctx.database.get_v2ex_profile_identity()[0] or "").strip()
        except Exception:  # pragma: no cover - defensive storage fallback
            active_profile_username = ""
    incremental_switch_required = bool(
        private_ready
        and active_profile_username
        and active_profile_username.casefold() != resolution.username.casefold()
    )
    incremental_state: CapabilityReadinessState = (
        "identity_switch_required" if incremental_switch_required else private_state
    )
    incremental_detail = (
        f"当前画像属于 V2EX 账号 {active_profile_username}；"
        f"请用账号 {resolution.username} 完整重新初始化后再恢复增量同步。"
        if incremental_switch_required
        else private_detail
    )
    capabilities = {
        "discover": discover,
        "profile": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            ready=private_ready,
            state=private_state,
            detail=private_detail,
        ),
        "bootstrap": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            ready=private_ready,
            state=private_state,
            detail=private_detail,
        ),
        "incremental": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            ready=private_ready and not incremental_switch_required,
            state=incremental_state,
            detail=incremental_detail,
        ),
        "cookie-sync": SourceCapabilityAuth(
            mode="login-required",
            required=False,
            ready=bool(browser_logged_in and browser_fresh),
            state=(
                "ready"
                if browser_logged_in and browser_fresh
                else "stale"
                if browser_when and not browser_fresh
                else "login_required"
            ),
            detail=(
                "浏览器登录状态心跳正常。"
                if browser_logged_in and browser_fresh
                else "等待浏览器扩展上报 V2EX 登录状态。"
            ),
        ),
    }
    return capabilities, resolution, browser_detail


def v2ex_capability_readiness(
    ctx: SourceAuthContext,
    *,
    configured_username: object | None = None,
) -> dict[str, SourceCapabilityAuth]:
    """Public backend-owned readiness registry used by status and guided init."""

    capabilities, _, _ = _v2ex_capability_state(
        ctx,
        configured_username=configured_username,
    )
    return capabilities


def auth_v2ex(ctx: SourceAuthContext) -> SourceAuthContract:
    """V2EX: anonymous-public, optional PAT, and separate browser heartbeat."""

    from openbiliclaw.sources.v2ex_identity import v2ex_identity_detail

    cfg = ctx.source_cfg("v2ex")
    token_env = str(getattr(cfg, "token_env", "OPENBILICLAW_V2EX_TOKEN") or "").strip()
    env_token = str(os.environ.get(token_env, "") or "").strip() if token_env else ""
    config_token = str(getattr(cfg, "access_token", "") or "").strip()
    token = env_token or config_token
    capabilities, resolution, browser_detail = _v2ex_capability_state(ctx)
    identity_detail = v2ex_identity_detail(resolution)
    if not token:
        detail = _V2EX_ANONYMOUS_DETAIL
        if browser_detail:
            detail += f" · {browser_detail}"
        if identity_detail:
            detail += f" · {identity_detail}"
        return SourceAuthContract(
            auth_required=False,
            credential="none",
            credential_origin="none",
            verification="unverified",
            verify_method="none",
            verify_ttl_seconds=None,
            can_verify_now=False,
            detail=detail,
            capabilities=capabilities,
            legacy_state="no_auth",
            legacy_logged_in=True,
        )
    verification, verified_at = _probe_verdict(
        ctx,
        "v2ex",
        credential="present",
        cookie=token,
    )
    detail = _V2EX_TOKEN_DETAIL.get(verification, _V2EX_TOKEN_DETAIL["unverified"])
    if browser_detail:
        detail += f" · {browser_detail}"
    if identity_detail:
        detail += f" · {identity_detail}"
    return SourceAuthContract(
        auth_required=False,
        credential="present",
        credential_origin="env" if env_token else "config",
        verification=verification,
        verify_method="live_probe",
        verified_at=verified_at,
        verify_ttl_seconds=_probe_ttl(verification),
        can_verify_now=True,
        detail=detail,
        capabilities=capabilities,
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


def _linuxdo_capabilities(
    personal_readiness: CapabilityReadiness,
    *,
    heartbeat_readiness: CapabilityReadiness | None = None,
) -> dict[str, SourceCapabilityAuth]:
    """Return the frozen Linux.do per-capability auth matrix."""

    personal = personal_readiness  # keep construction below compact/readable
    return {
        "discover": SourceCapabilityAuth(
            mode="anonymous",
            required=True,
            readiness="ready",
            detail="公开 search/hot/feed/creator/related 无需个人账号凭据。",
        ),
        "profile": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            readiness=personal,
            detail="画像信号需要浏览器会话，并由 /session/current.json 正面确认账号。",
        ),
        "bootstrap": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            readiness=personal,
            detail="收藏、点赞和阅读记录需要已登录的 Linux.do 浏览器会话。",
        ),
        "incremental": SourceCapabilityAuth(
            mode="login-required",
            required=True,
            readiness=personal,
            detail="周期个人信号同步需要已登录的 Linux.do 浏览器会话。",
        ),
        "cookie-sync": SourceCapabilityAuth(
            mode="optional-credential",
            required=False,
            readiness=(heartbeat_readiness or personal),
            detail="Cookie 心跳只是观察证据，不等同于账号身份已验证。",
        ),
    }


LINUXDO_CAPABILITY_AUTH_MODES: dict[str, tuple[str, bool]] = {
    "discover": ("anonymous", True),
    "profile": ("login-required", True),
    "bootstrap": ("login-required", True),
    "incremental": ("login-required", True),
    "cookie-sync": ("optional-credential", False),
}


def _linuxdo_login_required_payload(payload: dict[str, Any]) -> bool:
    error_code = str(payload.get("error", "") or "").strip()
    debug = payload.get("debug")
    codes = [error_code]
    if isinstance(debug, dict):
        codes.append(str(debug.get("code", "") or "").strip())
        for key in ("scope_errors", "input_errors"):
            errors = debug.get(key)
            if isinstance(errors, dict):
                codes.extend(str(value or "").strip() for value in errors.values())
    return bool(isinstance(debug, dict) and debug.get("login_required")) or any(
        code in {"linuxdo_login_required", "login_required", "not_logged_in"} for code in codes
    )


def _linuxdo_task_evidence_fresh(value: object) -> bool:
    """Whether a personal task still describes a plausibly live browser session."""

    timestamp = _utc_timestamp(value)
    if timestamp is None:
        return False
    age = datetime.now(UTC) - timestamp
    return -timedelta(minutes=5) <= age <= timedelta(hours=_LINUXDO_LOGIN_FRESH_HOURS)


def auth_linuxdo(ctx: SourceAuthContext) -> SourceAuthContract:
    """Linux.do public discovery plus login-required personal capabilities.

    A heartbeat admits a browser task but never verifies identity. A newer
    bootstrap outcome from ``/session/current.json`` is authoritative and may
    therefore override an older positive cookie observation.
    """
    stored, when, fresh = _login_heartbeat(
        ctx.database,
        getter="get_linuxdo_login_state",
        fresh_hours=_LINUXDO_LOGIN_FRESH_HOURS,
    )
    # A real /session/current.json result is stronger than cookie presence.
    # Read the latest personal task separately so a newer public discovery row
    # cannot hide an authoritative login failure.
    auth_row = None
    db_conn = getattr(ctx.database, "conn", None)
    if db_conn is not None and hasattr(db_conn, "execute"):
        with suppress(Exception):
            auth_row = db_conn.execute(
                """
                SELECT status, result_json,
                       COALESCE(completed_at, claimed_at, created_at)
                FROM linuxdo_tasks
                WHERE type = 'bootstrap_events'
                  AND status IN ('pending', 'in_progress', 'completed', 'failed')
                ORDER BY CASE WHEN status IN ('completed', 'failed') THEN 0 ELSE 1 END,
                         COALESCE(completed_at, claimed_at, created_at) DESC,
                         created_at DESC
                LIMIT 1
                """
            ).fetchone()
    heartbeat_at = _utc_timestamp(when)
    if auth_row is not None:
        auth_status = str(_row_value(auth_row, "status", 0) or "").strip()
        auth_payload = _json_dict(_row_value(auth_row, "result_json", 1))
        auth_at = str(auth_row[2] or "")
        auth_timestamp = _utc_timestamp(auth_at)
        auth_is_fresh = _linuxdo_task_evidence_fresh(auth_at)
        auth_is_newer = bool(
            auth_timestamp is not None and (heartbeat_at is None or auth_timestamp >= heartbeat_at)
        )
        auth_terminal = str(auth_payload.get("_openbiliclaw_terminal_status", "") or "").strip()
        auth_completed = auth_status == "completed" or auth_terminal in {
            "ok",
            "empty",
            "degraded",
            "completed",
        }
        if (
            auth_status == "failed"
            and auth_is_fresh
            and _linuxdo_login_required_payload(auth_payload)
        ):
            # A real `/session/current.json` rejection is authoritative until
            # a later personal task positively verifies another session.  A
            # newer `_t`-presence heartbeat is only an observation and must
            # never turn that rejection back into `ready` on its own.
            return SourceAuthContract(
                auth_required=False,
                credential="none",
                credential_origin="none",
                verification="failed",
                verify_method="task_history",
                verified_at=auth_at,
                verify_ttl_seconds=None,
                can_verify_now=True,
                detail=(
                    "Linux.do 公开发现仍可用；最近任务提示需要登录，"
                    "请在当前浏览器登录 Linux.do 后重试个人信号同步。"
                ),
                capabilities=_linuxdo_capabilities(
                    "login_required",
                    heartbeat_readiness=("ready" if stored and fresh else None),
                ),
                legacy_state="no_auth",
                legacy_logged_in=True,
            )
        if auth_is_newer and auth_is_fresh and auth_completed:
            items = auth_payload.get("items")
            item_count = len(items) if isinstance(items, list) else 0
            outcome = {
                "ok": "成功",
                "empty": "空结果",
                "degraded": "降级完成",
                "completed": "完成",
                "": "完成",
            }.get(auth_terminal, "完成")
            return SourceAuthContract(
                auth_required=False,
                credential="present",
                credential_origin="extension",
                verification="verified",
                verify_method="task_history",
                verified_at=auth_at,
                verify_ttl_seconds=_HEARTBEAT_TTL_SECONDS,
                can_verify_now=True,
                detail=(
                    f"Linux.do 公开发现可用；最近个人信号同步{outcome}"
                    f"（{item_count} 条），浏览器会话可用于收藏、点赞和阅读记录。"
                ),
                capabilities=_linuxdo_capabilities(
                    "ready",
                    heartbeat_readiness=("ready" if stored and fresh else None),
                ),
                legacy_state="no_auth",
                legacy_logged_in=True,
            )
    if when and stored and fresh:
        return SourceAuthContract(
            auth_required=False,
            credential="present",
            credential_origin="extension",
            verification="unverified",
            verify_method="browser_heartbeat",
            verified_at=str(when),
            verify_ttl_seconds=_HEARTBEAT_TTL_SECONDS,
            can_verify_now=True,
            detail=("Linux.do 公开发现可用；浏览器登录态已连接，可同步个人收藏、点赞和阅读记录。"),
            capabilities=_linuxdo_capabilities("ready", heartbeat_readiness="ready"),
            legacy_state="no_auth",
            legacy_logged_in=True,
        )
    if when and stored:
        return SourceAuthContract(
            auth_required=False,
            credential="present",
            credential_origin="extension",
            verification="stale",
            verify_method="browser_heartbeat",
            verified_at=str(when),
            verify_ttl_seconds=_HEARTBEAT_TTL_SECONDS,
            can_verify_now=True,
            detail="Linux.do 公开发现仍可用；个人登录态已过期，请连接插件刷新。",
            capabilities=_linuxdo_capabilities("stale", heartbeat_readiness="stale"),
            legacy_state="no_auth",
            legacy_logged_in=True,
        )
    if when:
        return SourceAuthContract(
            auth_required=False,
            credential="none",
            credential_origin="none",
            verification="failed",
            verify_method="browser_heartbeat",
            verified_at=str(when),
            verify_ttl_seconds=_HEARTBEAT_TTL_SECONDS,
            can_verify_now=True,
            detail="Linux.do 公开发现可用；浏览器当前未登录，个人信号同步暂不可用。",
            capabilities=_linuxdo_capabilities(
                "login_required", heartbeat_readiness="login_required"
            ),
            legacy_state="no_auth",
            legacy_logged_in=True,
        )

    row = None
    db_conn = getattr(ctx.database, "conn", None)
    if db_conn is not None and hasattr(db_conn, "execute"):
        with suppress(Exception):
            row = db_conn.execute(
                """
                SELECT type, status, result_json,
                       COALESCE(completed_at, claimed_at, created_at)
                FROM linuxdo_tasks
                WHERE type IN ('bootstrap_events', 'search', 'hot', 'feed', 'creator', 'related')
                  AND status IN ('pending', 'in_progress', 'completed', 'failed')
                ORDER BY COALESCE(completed_at, claimed_at, created_at) DESC,
                         created_at DESC
                LIMIT 1
                """
            ).fetchone()

    if row is not None:
        task_type = str(_row_value(row, "type", 0) or "").strip()
        status = str(_row_value(row, "status", 1) or "").strip()
        payload = _json_dict(_row_value(row, "result_json", 2))
        at = ""
        with suppress(Exception):
            # The COALESCE expression has no stable sqlite row name.
            at = str(row[3] or "")

        items = payload.get("items")
        item_count = len(items) if isinstance(items, list) else 0
        error_code = str(payload.get("error", "") or "").strip()
        debug = payload.get("debug")
        debug_codes: list[str] = []
        if isinstance(debug, dict):
            debug_codes.append(str(debug.get("code", "") or "").strip())
            for key in ("scope_errors", "input_errors"):
                errors = debug.get(key)
                if isinstance(errors, dict):
                    debug_codes.extend(str(value or "").strip() for value in errors.values())
        login_required = bool(isinstance(debug, dict) and debug.get("login_required")) or any(
            code in {"linuxdo_login_required", "login_required", "not_logged_in"}
            for code in (error_code, *debug_codes)
        )
        terminal_status = (
            str(payload.get("_openbiliclaw_terminal_status", "") or "").strip().lower()
        )
        positive_terminal = terminal_status in {"ok", "empty", "degraded", "completed"}
        completed = (status == "completed" and not terminal_status) or positive_terminal
        history: dict[str, Any] = {
            "auth_required": False,
            "verify_method": "task_history",
            "verify_ttl_seconds": _HEARTBEAT_TTL_SECONDS,
            # The explicit verify action still asks the extension for a fresh
            # heartbeat; history describes only the standing verdict.
            "can_verify_now": True,
            "legacy_state": "no_auth",
            "legacy_logged_in": True,
        }

        task_evidence_fresh = _linuxdo_task_evidence_fresh(at)
        if completed and task_type == "bootstrap_events" and not task_evidence_fresh:
            return SourceAuthContract(
                **history,
                credential="present",
                credential_origin="extension",
                verification="stale",
                verified_at=at,
                detail=(
                    "Linux.do 公开发现可用；最近个人信号任务已超过登录证据有效期，"
                    "请连接插件刷新后再同步收藏、点赞和阅读记录。"
                ),
                capabilities=_linuxdo_capabilities("stale"),
            )

        if completed and task_type == "bootstrap_events":
            outcome = {
                "ok": "成功",
                "empty": "空结果",
                "degraded": "降级完成",
                "completed": "完成",
                "": "完成",
            }.get(terminal_status, "完成")
            return SourceAuthContract(
                **history,
                credential="present",
                credential_origin="extension",
                verification="verified",
                verified_at=at,
                detail=(
                    f"Linux.do 公开发现可用；最近个人信号同步{outcome}"
                    f"（{item_count} 条），浏览器会话可用于收藏、点赞和阅读记录。"
                ),
                capabilities=_linuxdo_capabilities("ready"),
            )

        if completed:
            outcome = "降级完成" if terminal_status == "degraded" else "完成"
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=(
                    f"Linux.do 公开发现最近任务{outcome}（{task_type}，{item_count} 条）；"
                    "公开任务不证明浏览器已登录，个人信号仍需登录态。"
                ),
                capabilities=_linuxdo_capabilities("unverified"),
            )

        if status == "failed" and login_required and task_evidence_fresh:
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="failed",
                verified_at=at,
                detail=(
                    "Linux.do 公开发现仍可用；最近任务提示需要登录，"
                    "请在当前浏览器登录 Linux.do 后重试个人信号同步。"
                ),
                capabilities=_linuxdo_capabilities("login_required"),
            )

        if status == "failed" and login_required:
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                verified_at=at,
                detail=(
                    "Linux.do 公开发现仍可用；历史任务曾提示需要登录，但该证据已过期，"
                    "请连接插件重新确认后再同步个人信号。"
                ),
                capabilities=_linuxdo_capabilities("unverified"),
            )

        if status == "completed":
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=(
                    f"Linux.do 最近任务已结束（{task_type}），但结果状态无法识别；"
                    "公开发现仍可用，个人登录态尚未确认。"
                ),
                capabilities=_linuxdo_capabilities("unverified"),
            )

        if status == "failed":
            suffix = f"：{error_code}" if error_code else ""
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=(
                    f"Linux.do 最近任务失败（{task_type}）{suffix}；"
                    "公开发现仍可用，该运行错误无法判断个人登录态。"
                ),
                capabilities=_linuxdo_capabilities("unverified"),
            )

        if status in {"pending", "in_progress"}:
            return SourceAuthContract(
                **history,
                credential="none",
                credential_origin="none",
                verification="unverified",
                detail=(
                    f"Linux.do 任务正在等待插件执行（{task_type} / {status}）；公开发现无需登录。"
                ),
                capabilities=_linuxdo_capabilities("unverified"),
            )

    return SourceAuthContract(
        auth_required=False,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="none",
        verify_ttl_seconds=None,
        can_verify_now=True,
        detail="Linux.do 公开发现无需登录；连接已登录插件后可同步个人收藏、点赞和阅读记录。",
        capabilities=_linuxdo_capabilities("unverified"),
        legacy_state="no_auth",
        legacy_logged_in=True,
    )


def linuxdo_capability_readiness(ctx: SourceAuthContext) -> dict[str, SourceCapabilityAuth]:
    """Return Linux.do's public/private capability admission matrix."""

    return auth_linuxdo(ctx).capabilities


# ── registry ─────────────────────────────────────────────────────────

#: Slug -> provider. Keys and order define the ``SourcesStatusResponse`` fields,
#: so adding a platform means adding one entry and one provider here rather than
#: editing the endpoint.

# ── registry ─────────────────────────────────────────────────────────

#: Slug -> provider. Keys and order define the ``SourcesStatusResponse`` fields,
#: so adding a platform means adding one entry and one provider here rather than
#: editing the endpoint.
SOURCE_AUTH_PROVIDERS: dict[str, Callable[[SourceAuthContext], SourceAuthContract]] = {
    "bilibili": auth_bilibili,
    "xiaohongshu": auth_xiaohongshu,
    "douyin": auth_douyin,
    "youtube": auth_youtube,
    "tiktok": auth_tiktok,
    "twitter": auth_twitter,
    "zhihu": auth_zhihu,
    "reddit": auth_reddit,
    "bangumi": auth_bangumi,
    "github": auth_github,
    "linuxdo": auth_linuxdo,
    "v2ex": auth_v2ex,
    "weibo": auth_weibo,
}


def source_enabled(ctx: SourceAuthContext, slug: str) -> bool:
    """Whether the scheduler should run this source (orthogonal to auth)."""
    return bool(getattr(ctx.source_cfg(slug), "enabled", _ENABLED_BY_DEFAULT.get(slug, False)))


def source_feed_paused(ctx: SourceAuthContext, slug: str) -> bool:
    """Whether a sub-feed of this source is circuit-broken.

    X-only today: its For-You sub-feed trips after consecutive failures and
    recovers on the next successful fetch. Not an auth fact, which is why it
    lives beside the contract instead of inside it.
    """
    if slug != "twitter" or not ctx.has_conn():
        return False
    return bool(ctx.x_health().get("feed_paused", False))
