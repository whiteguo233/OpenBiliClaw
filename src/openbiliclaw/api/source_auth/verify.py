"""``POST /api/sources/{slug}/verify`` — the one explicit verification action.

Until now the only way to learn whether a credential worked was to save it and
wait for a discovery cycle to fail (spec D7). Verification *capability* existed
for B站, but it hid behind ``GET /api/init-status`` — a read-only GET that
quietly went out to bilibili.com on every poll — and nothing at all existed for
the other six platforms.

This module gives every registered source the same button and, crucially, keeps their answers
honest about *how* they were reached.

**Three outcomes, not two.** A verification either confirms the credential
works, confirms it does not, or **cannot tell**. The third case is the one that
matters: a proxy hiccup, an extension that never answered, a platform that
throttled us, or YouTube (which needs no login at all) must never render as
"your login expired". Collapsing indeterminate into failure is how a flaky
network turns into a user deleting a perfectly good cookie. So transport
failures map to ``verification="unverified"``, never ``"failed"`` — the same
refusal to overclaim that ``providers._probe_verdict`` already encodes for the
cached read path.

**Dispatch is on a static per-platform action, not on the live
``verify_method``.** Those differ on purpose. ``verify_method`` reports how the
*current verdict* was reached and legitimately varies with state — 知乎 reports
``browser_heartbeat`` when the extension has spoken and ``task_history`` when it
has not — whereas the *action* a click performs is a fixed property of the
platform (for 知乎, always "ask the extension to re-report"). Dispatching on the
dynamic field would leave 知乎 with no runnable action precisely when it most
needs one, and would invent a "task_history verification" that cannot exist:
history is not something you can go and re-run.

**Debounce before any I/O.** Each platform is debounced for
``_DEBOUNCE_SECONDS``; an in-flight marker additionally collapses concurrent
clicks. A verify button that a user can hold down is otherwise a
self-inflicted risk-control trigger, and 抖音 in particular is exactly the kind
of platform that notices.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from openbiliclaw.api.source_auth.legacy import check_legacy_consistency
from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES, LiveProbeCache
from openbiliclaw.api.source_auth.providers import SOURCE_AUTH_PROVIDERS, SourceAuthContext
from openbiliclaw.runtime.init_prereqs import BILI_PROBE_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from openbiliclaw.api.source_auth.contract import SourceAuthContract, Verification
    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)

# What one click actually does, per platform. Fixed — see the module docstring
# for why this is not ``contract.verify_method``.
VerifyAction = Literal[
    "live_probe",  # go out to the platform right now
    "passive_health",  # report what real traffic already concluded
    "browser_heartbeat",  # ask the extension to re-report, then wait
    "local_file",  # re-read a local credential file
    "none",  # nothing to verify
]

VERIFY_ACTIONS: dict[str, VerifyAction] = {
    "bilibili": "live_probe",
    "xiaohongshu": "browser_heartbeat",
    "douyin": "live_probe",
    "youtube": "none",
    "twitter": "live_probe",
    "zhihu": "browser_heartbeat",
    "reddit": "local_file",
    # Fixed as ``live_probe`` even though Bangumi's contract reports
    # ``verify_method='none'`` when no token is configured: the *action* is a
    # constant property of the platform (a click hits /v0/me when a token
    # exists), while the contract's ``verify_method`` legitimately varies with
    # whether there is anything to probe — the same action-vs-method split 知乎
    # makes. With no token the probe returns ``has_credential=False`` and the
    # click resolves to ``indeterminate`` without going out.
    "bangumi": "live_probe",
    "github": "live_probe",
    "linuxdo": "browser_heartbeat",
    "v2ex": "live_probe",
    "weibo": "browser_heartbeat",
    "instagram": "browser_heartbeat",
}

_BROWSER_HEARTBEAT_PREFIXES: dict[str, str] = {
    "xiaohongshu": "xhs",
    "zhihu": "zhihu",
    "linuxdo": "linuxdo",
    "weibo": "weibo",
    "instagram": "instagram",
}

# The user-facing tri-state. Deliberately computed here rather than in each
# frontend: three surfaces independently mapping six ``verification`` values to
# three tones is precisely the drift that produced D6's two divergent status
# maps, and the fix for that is one backend-owned answer (invariant I4).
VerifyOutcomeName = Literal["verified", "failed", "indeterminate"]

# ``rate_limited`` / ``blocked`` are indeterminate on purpose: the platform
# throttled or refused *us*, which says nothing about whether the credential is
# valid. ``stale`` likewise — a lapsed window is an unknown, not a rejection.
_OUTCOME_BY_VERIFICATION: dict[str, VerifyOutcomeName] = {
    "verified": "verified",
    "failed": "failed",
    "stale": "indeterminate",
    "unverified": "indeterminate",
    "rate_limited": "indeterminate",
    "blocked": "indeterminate",
}

# Repeat clicks inside this window replay the stored result and perform no I/O.
_DEBOUNCE_SECONDS = 10.0

# Ceiling for a concurrent-click marker, so a crashed probe cannot wedge a
# platform's verify button forever.
_INFLIGHT_MAX_SECONDS = 60.0

# How long to wait for the extension to answer a heartbeat request. Spec Phase 2
# fixes this at 5s: long enough for a service worker to wake, short enough that
# a settings page never feels hung.
_HEARTBEAT_WAIT_SECONDS = 5.0
_HEARTBEAT_POLL_SECONDS = 0.1

# Imported, not redeclared: guided-init and this route now write the same B站
# verdict store, so a divergent timeout would mean the two entry points disagree
# about when a slow B站 counts as unreachable.
_BILI_PROBE_TIMEOUT = BILI_PROBE_TIMEOUT_SECONDS


@dataclass(frozen=True)
class _ActionResult:
    """What one verification action managed to establish.

    ``conclusive=False`` means the action could not reach a verdict *this
    time* — the extension never answered, the proxy died, there was no
    credential to check. That is different from the platform's standing
    verdict, which may still be a perfectly good "logged in" from an hour-old
    heartbeat, and the two must not be conflated: a user clicking 测试连接 with
    their browser closed would otherwise get a green "verified" sitting next to
    a message explaining that nothing could be reached.
    """

    message: str
    conclusive: bool = True
    # Set when the action established that a round trip happened but cannot
    # itself say what the platform concluded — the caller substitutes the
    # refreshed contract's ``detail``. Without this the browser-heartbeat path
    # reports "插件已重新上报浏览器登录态。" (a round-trip fact) in failure red
    # when the extension answers "not logged in", i.e. a success-sounding
    # sentence under a failure tone. ``_verify_local_file`` avoids the problem
    # by already having the refreshed contract in hand; the heartbeat action
    # runs *before* the refresh, so it needs the caller to fill this in.
    adopt_contract_detail: bool = False


@dataclass(frozen=True)
class LiveProbeOutcome:
    """Raw result of one outbound login probe, before it is dressed for a UI.

    Exists so the *write* path can run the identical probe the verify button
    runs (``source_auth/write.py``). A second implementation of "ask 抖音
    whether this cookie is logged in" would be a second place for the answer to
    drift, which is the failure mode this whole package was written against.

    ``has_credential`` answers "was there anything to probe with", separately
    from what the platform said about it — a distinction ``_ActionResult``
    collapses into ``conclusive`` but the write gate needs kept apart.
    """

    slug: str
    has_credential: bool
    authenticated: bool
    network_error: bool
    message: str
    username: str = ""
    user_id: int = 0


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of one verification attempt.

    ``outcome`` answers "did this click verify anything", while
    ``contract.verification`` answers "what do we currently believe". Keeping
    them separate is the same orthogonality the contract is built on: the
    button reports on the action, the status chip reports on the state.

    ``replayed`` answers a third, equally separate question: "did *this call*
    do the work, or is it handing back an answer someone else already got?"
    Without it a debounced click is byte-identical to a fresh one, so a user
    who just fixed their cookie clicks again, sees the stored failure replayed,
    and concludes the fix did not take. ``retry_after_seconds`` is emitted for
    the same reason ``outcome`` is computed here rather than in each frontend:
    the debounce window is a backend constant, and two surfaces hardcoding
    ``10`` is precisely the drift invariant I4 exists to prevent.
    """

    slug: str
    contract: SourceAuthContract
    outcome: VerifyOutcomeName
    changed: bool
    message: str
    replayed: bool = False
    retry_after_seconds: float = 0.0


@dataclass
class VerifyDebounce:
    """Per-platform debounce plus a concurrent-click guard.

    Process-local and deliberately not persisted: after a restart the first
    click should genuinely verify.
    """

    _results: dict[str, tuple[float, VerifyOutcome]] = field(default_factory=dict)
    _inflight: dict[str, float] = field(default_factory=dict)

    def replay(self, slug: str) -> VerifyOutcome | None:
        """Stored result for *slug* when still inside the debounce window."""
        entry = self._results.get(slug)
        if entry is None:
            return None
        recorded_at, outcome = entry
        elapsed = time.monotonic() - recorded_at
        if elapsed >= _DEBOUNCE_SECONDS:
            return None
        # A replay never re-reports a change: the change, if any, was reported
        # by the call that actually performed the verification.
        return VerifyOutcome(
            slug=outcome.slug,
            contract=outcome.contract,
            outcome=outcome.outcome,
            changed=False,
            message=outcome.message,
            replayed=True,
            retry_after_seconds=max(0.0, _DEBOUNCE_SECONDS - elapsed),
        )

    def busy(self, slug: str) -> bool:
        """Whether a verification for *slug* is already running."""
        started = self._inflight.get(slug)
        if started is None:
            return False
        if time.monotonic() - started >= _INFLIGHT_MAX_SECONDS:
            self._inflight.pop(slug, None)
            return False
        return True

    def mark_started(self, slug: str) -> None:
        self._inflight[slug] = time.monotonic()

    def mark_finished(self, slug: str, outcome: VerifyOutcome) -> None:
        self._inflight.pop(slug, None)
        self._results[slug] = (time.monotonic(), outcome)

    def abandon(self, slug: str) -> None:
        """Release the in-flight marker without storing a result."""
        self._inflight.pop(slug, None)

    def clear(self, slug: str | None = None) -> None:
        if slug is None:
            self._results.clear()
            self._inflight.clear()
        else:
            self._results.pop(slug, None)
            self._inflight.pop(slug, None)


#: Process-wide debounce. Module-level for the same reason as ``LIVE_PROBES``:
#: it must survive the RuntimeContext rebuild that saving config triggers, or
#: every config save would re-arm the button for another round of probes.
VERIFY_DEBOUNCE = VerifyDebounce()


def _outcome_name(verification: Verification) -> VerifyOutcomeName:
    return _OUTCOME_BY_VERIFICATION.get(verification, "indeterminate")


def note_credential_changed(
    slug: str,
    *,
    debounce: VerifyDebounce = VERIFY_DEBOUNCE,
) -> None:
    """Drop *slug*'s debounced result because its credential just changed.

    The debounce is keyed on the platform, so without this a stored verdict
    outlives the credential it describes. The sequence is not hypothetical, it
    is the recovery path: verify a dead cookie (the window arms with
    ``failed``), paste a working one, click 测试连接 again — and get the old
    rejection replayed, byte for byte, as if the repair had not taken. The
    likeliest next move for a user reading that is to delete the cookie that
    actually works.

    Called by every write surface once a credential has genuinely landed, which
    is also why it lives here rather than in ``write.persist_credential``: that
    function is a deliberately dumb writer, and this is a statement about the
    verify *action*'s cached history.
    """
    debounce.clear(slug)


def _contract_for(
    slug: str, cfg: Config, database: Any, probes: LiveProbeCache
) -> SourceAuthContract:
    ctx = SourceAuthContext(cfg=cfg, database=database, probes=probes)
    return SOURCE_AUTH_PROVIDERS[slug](ctx)


# ── actions ──────────────────────────────────────────────────────────


async def _probe_bilibili(
    cfg: Config, probes: LiveProbeCache, *, cookie: str | None, record: bool
) -> LiveProbeOutcome:
    """Live nav probe, recorded into the shared verdict store.

    The same store backs ``runtime.init_prereqs``, so the guided-init page and
    the settings page cannot end up holding contradictory verdicts about one
    cookie — that split was D3's shape and it is closed here rather than
    papered over with a second cache.

    ``cookie`` overrides the stored value so a *candidate* being written can be
    probed before it lands, and ``record=False`` keeps that candidate's verdict
    out of the store: a rejected paste must not overwrite what we believe about
    the credential still in use.
    """
    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.bilibili.auth import AuthManager, resolve_runtime_cookie

    if cookie is None:
        configured = str(getattr(cfg.bilibili, "cookie", "") or "")
        try:
            cookie = resolve_runtime_cookie(
                data_dir=cfg.data_path, configured_cookie=configured
            ).strip()
        except Exception:  # noqa: BLE001 - an unreadable store must not 500 the click
            logger.debug("bilibili cookie store unreadable during verify", exc_info=True)
            cookie = configured.strip()
    cookie = cookie.strip()

    if not cookie:
        # Nothing was probed, so nothing may be recorded: a verdict about a
        # credential that does not exist is invented evidence (invariant I3).
        if record:
            probes.clear("bilibili")
        return LiveProbeOutcome(
            slug="bilibili",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message="未配置 B站 Cookie —— 在浏览器登录 bilibili.com，插件会自动同步。",
        )

    # Every verdict recorded below carries the fingerprint of the cookie it is
    # about, so the write gate can tell "already confirmed *this*" from
    # "confirmed something else 5 seconds ago". A guided-init probe and a
    # settings-page save therefore still share one verdict — the point of the
    # single store — without the sharing becoming a way to skip a check.
    fingerprint = credential_fingerprint("bilibili", cookie)

    def _fail(message: str, detail: str) -> LiveProbeOutcome:
        if record:
            probes.record(
                "bilibili",
                authenticated=False,
                detail=detail,
                network_error=True,
                fingerprint=fingerprint,
            )
        return LiveProbeOutcome(
            slug="bilibili",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message=message,
        )

    proxy = str(getattr(cfg.bilibili, "proxy", "") or "").strip()
    try:
        manager = AuthManager(data_dir=cfg.data_path, proxy=proxy or None)
        status = await asyncio.wait_for(
            manager.validate_cookie(cookie), timeout=_BILI_PROBE_TIMEOUT
        )
    # The recorded ``detail`` deliberately repeats the message rather than
    # storing a terser code: guided-init renders this same string via
    # ``peek_bilibili_detail()``, and a verdict recorded here has to explain
    # itself just as well as one recorded there.
    except TimeoutError:
        detail = f"B站 检测超时（{_BILI_PROBE_TIMEOUT:.0f}s 未响应），无法判定登录态。"
        return _fail(detail, detail)
    except Exception as exc:  # noqa: BLE001 - transport seam; cause kept in the message
        detail = f"B站 检测请求失败（{exc}），无法判定登录态。"
        return _fail(detail, detail)

    message = str(getattr(status, "message", "") or "").strip()
    network_error = bool(getattr(status, "network_error", False))
    if network_error:
        return _fail(f"B站 检测请求失败（{message}），无法判定登录态。", message)

    who = str(getattr(status, "username", "") or "").strip()
    if record:
        probes.record(
            "bilibili",
            authenticated=bool(status.authenticated),
            detail=message,
            network_error=False,
            fingerprint=fingerprint,
            username=who,
            user_id=int(getattr(status, "user_id", 0) or 0),
        )
    return LiveProbeOutcome(
        slug="bilibili",
        has_credential=True,
        authenticated=bool(status.authenticated),
        network_error=False,
        message=(
            f"已登录 B站{f'（{who}）' if who else ''}。"
            if status.authenticated
            else (message or "B站 Cookie 未登录或已失效。")
        ),
        username=who,
        user_id=int(getattr(status, "user_id", 0) or 0),
    )


async def _probe_douyin(
    cfg: Config, probes: LiveProbeCache, *, cookie: str | None, record: bool
) -> LiveProbeOutcome:
    """Live probe on ``/aweme/v1/web/user/profile/self/`` (spec D11)."""
    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
    from openbiliclaw.sources.douyin_login_probe import probe_douyin_login

    if cookie is None:
        dy_cfg = getattr(cfg.sources, "douyin", None)
        cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
        try:
            cookie = resolve_douyin_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
        except Exception:  # noqa: BLE001 - unreadable store must not 500 the click
            logger.debug("douyin cookie store unreadable during verify", exc_info=True)
            cookie = ""

    if not cookie.strip():
        if record:
            probes.clear("douyin")
        return LiveProbeOutcome(
            slug="douyin",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message="未配置抖音 Cookie —— 设置环境变量，或登录抖音后由插件同步。",
        )

    status = await probe_douyin_login(cookie)
    who = str(getattr(status, "username", "") or "")
    if record:
        probes.record(
            "douyin",
            authenticated=bool(status.authenticated),
            detail=status.message,
            network_error=bool(status.network_error),
            # Over ``sessionid`` / ``sessionid_ss`` / ``sid_tt`` only, so the
            # constant ``msToken`` rotation does not invalidate the verdict.
            fingerprint=credential_fingerprint("douyin", cookie),
            username=who,
        )
    return LiveProbeOutcome(
        slug="douyin",
        has_credential=True,
        authenticated=bool(status.authenticated),
        network_error=bool(status.network_error),
        message=status.message or "抖音登录态探测完成。",
        username=who,
    )


async def _probe_bangumi(
    cfg: Config, probes: LiveProbeCache, *, cookie: str | None, record: bool
) -> LiveProbeOutcome:
    """Live probe on Bangumi's ``GET /v0/me`` for the *optional* personal token.

    Bangumi works anonymously, so "no credential" is a normal state, not a
    failure — ``has_credential=False`` is returned before any network call and
    the click resolves to ``indeterminate`` ("公开源，填令牌后可验证"), never a
    logged-out verdict.

    With a token the discriminator is clean (stripped-control run 2026-07-19,
    §0.1 / I3): a valid token identifies the account, an invalid / expired one
    is rejected with ``BangumiAPIError(code='unauthorized')``. That rejection is
    a real ``failed`` verdict. Every other ``BangumiAPIError`` — timeout,
    transport, 429 rate-limit, 5xx, a schema drift — is a transport-class
    "cannot tell": it says nothing about the token, so it maps to
    ``network_error=True`` (→ ``indeterminate``), never ``failed``. On this box
    the custom proxy cannot reach api.bgm.tv, so a real probe lands here — a
    configuration matter, correctly reported as indeterminate rather than as an
    expired token.

    The client is built from :func:`outbound_httpx_kwargs` (Bangumi is an
    overseas source), so it honours ``[network].mode`` rather than connecting
    bare.
    """
    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.bangumi_client import (
        BangumiAPIError,
        BangumiClient,
        me_username,
    )

    if cookie is None:
        bgm_cfg = getattr(cfg.sources, "bangumi", None)
        cookie = str(getattr(bgm_cfg, "access_token", "") or "")
    token = str(cookie or "").strip()

    if not token:
        if record:
            probes.clear("bangumi")
        return LiveProbeOutcome(
            slug="bangumi",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message=(
                "未配置 Bangumi 个人令牌 —— 公开发现无需令牌；如需识别账号或读取私密收藏，"
                "请填写个人令牌后再验证。"
            ),
        )

    fingerprint = credential_fingerprint("bangumi", token)
    try:
        async with BangumiClient(access_token=token) as client:
            payload = await client.get_me()
        username = me_username(payload)
    except BangumiAPIError as exc:
        if exc.code == "unauthorized":
            if record:
                probes.record(
                    "bangumi",
                    authenticated=False,
                    detail=str(exc),
                    network_error=False,
                    fingerprint=fingerprint,
                )
            return LiveProbeOutcome(
                slug="bangumi",
                has_credential=True,
                authenticated=False,
                network_error=False,
                message="Bangumi 拒绝了该个人令牌（缺失、无效或已过期）。",
            )
        # timeout / network_error / rate_limited / upstream_error / schema drift:
        # a round trip that could not conclude, so it is not evidence about the
        # token (invariant I3 — a flaky proxy is not an expired token).
        if record:
            probes.record(
                "bangumi",
                authenticated=False,
                detail=str(exc),
                network_error=True,
                fingerprint=fingerprint,
            )
        return LiveProbeOutcome(
            slug="bangumi",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message=f"Bangumi 令牌验证未能完成（{exc}），暂时无法判定。",
        )

    if record:
        probes.record(
            "bangumi",
            authenticated=True,
            detail=f"已识别 Bangumi 账号（{username}）。",
            network_error=False,
            fingerprint=fingerprint,
            username=username,
        )
    return LiveProbeOutcome(
        slug="bangumi",
        has_credential=True,
        authenticated=True,
        network_error=False,
        message=f"个人令牌有效，已识别 Bangumi 账号（{username}）。",
        username=username,
    )


async def _probe_github(
    cfg: Config, probes: LiveProbeCache, *, cookie: str | None, record: bool
) -> LiveProbeOutcome:
    """Verify GitHub's optional PAT with the read-only ``GET /user`` endpoint."""

    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.github_client import (
        GitHubAPIError,
        GitHubClient,
        github_user_id,
        github_user_login,
        resolve_github_access_token,
    )

    if cookie is None:
        github_cfg = getattr(cfg.sources, "github", None)
        cookie, _ = resolve_github_access_token(
            config_token=str(getattr(github_cfg, "access_token", "") or ""),
            token_env="OPENBILICLAW_GITHUB_TOKEN",
        )
    token = str(cookie or "").strip()
    if not token:
        if record:
            probes.clear("github")
        return LiveProbeOutcome(
            slug="github",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message=(
                "GitHub 公开仓库来源无需 PAT；如需验证账号或提高 API 限额，请先配置可选 PAT。"
            ),
        )

    fingerprint = credential_fingerprint("github", token)
    try:
        async with GitHubClient(token=token) as client:
            payload = await client.get_user()
        username = github_user_login(payload)
        user_id = github_user_id(payload)
    except GitHubAPIError as exc:
        if exc.code == "unauthorized":
            if record:
                probes.record(
                    "github",
                    authenticated=False,
                    detail=str(exc),
                    network_error=False,
                    fingerprint=fingerprint,
                )
            return LiveProbeOutcome(
                slug="github",
                has_credential=True,
                authenticated=False,
                network_error=False,
                message="GitHub 拒绝了该 PAT（缺失、无效或已过期）。",
            )
        if record:
            probes.record(
                "github",
                authenticated=False,
                detail=str(exc),
                network_error=True,
                fingerprint=fingerprint,
            )
        return LiveProbeOutcome(
            slug="github",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message=f"GitHub PAT 验证未能完成（{exc}），暂时无法判定。",
        )

    if record:
        probes.record(
            "github",
            authenticated=True,
            detail=f"已识别 GitHub 账号（{username}）。",
            network_error=False,
            fingerprint=fingerprint,
            username=username,
            user_id=user_id,
        )
    return LiveProbeOutcome(
        slug="github",
        has_credential=True,
        authenticated=True,
        network_error=False,
        message=f"GitHub PAT 有效，已识别账号（{username}）。",
        username=username,
        user_id=user_id,
    )


async def _probe_v2ex(
    cfg: Config, probes: LiveProbeCache, *, cookie: str | None, record: bool
) -> LiveProbeOutcome:
    """Live probe V2EX's optional PAT with ``GET /api/v2/member``."""

    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.v2ex_client import V2EXAPIError, V2EXClient, member_username

    if cookie is None:
        v2ex_cfg = getattr(cfg.sources, "v2ex", None)
        token_env = str(
            getattr(v2ex_cfg, "token_env", "OPENBILICLAW_V2EX_TOKEN") or "OPENBILICLAW_V2EX_TOKEN"
        ).strip()
        cookie = str(os.environ.get(token_env, "") or "") if token_env else ""
        if not str(cookie).strip():
            cookie = str(getattr(v2ex_cfg, "access_token", "") or "")
    token = str(cookie or "").strip()
    if not token:
        if record:
            probes.clear("v2ex")
        return LiveProbeOutcome(
            slug="v2ex",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message="V2EX 为公开源，未配置 PAT；如需 API 2.0 身份验证，请先填写 PAT。",
        )

    fingerprint = credential_fingerprint("v2ex", token)
    try:
        async with V2EXClient(access_token=token, request_interval_seconds=0) as client:
            payload = await client.get_member()
        username = member_username(payload)
    except V2EXAPIError as exc:
        if exc.code == "unauthorized":
            if record:
                probes.record(
                    "v2ex",
                    authenticated=False,
                    detail=str(exc),
                    network_error=False,
                    fingerprint=fingerprint,
                )
            return LiveProbeOutcome(
                slug="v2ex",
                has_credential=True,
                authenticated=False,
                network_error=False,
                message="V2EX 拒绝了该 PAT（缺失、无效或已过期）。",
            )
        if record:
            probes.record(
                "v2ex",
                authenticated=False,
                detail=str(exc),
                network_error=True,
                fingerprint=fingerprint,
            )
        return LiveProbeOutcome(
            slug="v2ex",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message=f"V2EX PAT 验证未能完成（{exc}），暂时无法判定。",
        )

    if record:
        probes.record(
            "v2ex",
            authenticated=True,
            detail=f"已识别 V2EX 账号（{username}）。",
            network_error=False,
            fingerprint=fingerprint,
            username=username,
        )
    return LiveProbeOutcome(
        slug="v2ex",
        has_credential=True,
        authenticated=True,
        network_error=False,
        message=f"V2EX PAT 有效，已识别账号（{username}）。",
        username=username,
    )


async def _probe_twitter(
    cfg: Config,
    database: Any,
    probes: LiveProbeCache,
    *,
    cookie: str | None,
    record: bool,
) -> LiveProbeOutcome:
    """Read X's authenticated account endpoint without performing a mutation."""
    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.x_auth import resolve_x_cookie
    from openbiliclaw.sources.x_client import (
        XAuthError,
        XBlockedError,
        XClient,
        XClientError,
        XMissingCookieError,
        XRateLimitError,
    )

    if cookie is None:
        tw_cfg = getattr(cfg.sources, "twitter", None)
        cookie_env = str(getattr(tw_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE"))
        try:
            cookie = resolve_x_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
        except Exception:  # noqa: BLE001 - an unreadable store must not 500 the click
            logger.debug("X cookie store unreadable during verify", exc_info=True)
            cookie = ""
    cookie = str(cookie or "").strip()

    if not cookie:
        if record:
            probes.clear("twitter")
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message="未配置 X Cookie —— 在浏览器登录 x.com，插件会自动同步。",
        )

    fingerprint = credential_fingerprint("twitter", cookie)
    health_store = None
    if record and hasattr(database, "conn"):
        from openbiliclaw.storage.x_health import XSourceHealthStore

        health_store = XSourceHealthStore(
            database,
            credential_fingerprint=fingerprint,
        )

    def _record_error(exc: BaseException) -> None:
        if health_store is not None:
            health_store.record_error(exc, strategy="verify")

    try:
        profile = await XClient(cookie).probe()
    except XMissingCookieError:
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=False,
            authenticated=False,
            network_error=False,
            message="X Cookie 缺少 auth_token / ct0，无法验证登录态。",
        )
    except XAuthError as exc:
        _record_error(exc)
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=True,
            authenticated=False,
            network_error=False,
            message="X Cookie 已失效 —— 请重新登录 x.com。",
        )
    except XBlockedError as exc:
        _record_error(exc)
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message="X 拒绝了验证请求（403），暂时无法判定 Cookie 是否有效。",
        )
    except XRateLimitError as exc:
        _record_error(exc)
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message="X 暂时限流，无法完成登录态验证，请稍后重试。",
        )
    except XClientError as exc:
        # A transport or schema error is not evidence about the cookie. Do not
        # overwrite the last known health state with a made-up failure.
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message=f"X 登录检查请求失败（{exc}），暂时无法判定。",
        )
    except Exception:  # noqa: BLE001 - optional X dependency / unexpected transport failure
        logger.warning("X login probe failed unexpectedly", exc_info=True)
        return LiveProbeOutcome(
            slug="twitter",
            has_credential=True,
            authenticated=False,
            network_error=True,
            message="X 登录检查请求未能完成，暂时无法判定。",
        )

    if health_store is not None:
        health_store.record_success(strategy="verify")

    username = str(getattr(profile, "screen_name", "") or "").strip()
    user_id = 0
    try:
        user_id = int(getattr(profile, "id", 0) or 0)
    except (TypeError, ValueError):
        user_id = 0
    return LiveProbeOutcome(
        slug="twitter",
        has_credential=True,
        authenticated=True,
        network_error=False,
        message=f"X 登录有效{f'（@{username}）' if username else ''}。",
        username=username,
        user_id=user_id,
    )


async def run_live_probe(
    slug: str,
    *,
    cfg: Config,
    database: Any = None,
    cookie: str | None = None,
    probes: LiveProbeCache = LIVE_PROBES,
    record: bool = True,
) -> LiveProbeOutcome:
    """Probe *slug* live. The one outbound login check in the codebase.

    Shared by the verify button and the credential write gate so the two can
    never disagree about what "logged in" means on a platform.

    Raises ``KeyError`` for a platform with no live probe — callers must decide
    what to do about that in the open, rather than receiving a fabricated
    verdict (invariant I3).
    """
    if slug == "bilibili":
        return await _probe_bilibili(cfg, probes, cookie=cookie, record=record)
    if slug == "douyin":
        return await _probe_douyin(cfg, probes, cookie=cookie, record=record)
    if slug == "twitter":
        return await _probe_twitter(
            cfg,
            database,
            probes,
            cookie=cookie,
            record=record,
        )
    if slug == "bangumi":
        return await _probe_bangumi(cfg, probes, cookie=cookie, record=record)
    if slug == "github":
        return await _probe_github(cfg, probes, cookie=cookie, record=record)
    if slug == "v2ex":
        outcome = await _probe_v2ex(cfg, probes, cookie=cookie, record=record)
        token = str(cookie or "").strip()
        if not token:
            v2ex_cfg = getattr(cfg.sources, "v2ex", None)
            token_env = str(
                getattr(v2ex_cfg, "token_env", "OPENBILICLAW_V2EX_TOKEN")
                or "OPENBILICLAW_V2EX_TOKEN"
            ).strip()
            token = str(os.environ.get(token_env, "") or "").strip() if token_env else ""
            if not token:
                token = str(getattr(v2ex_cfg, "access_token", "") or "").strip()
        from openbiliclaw.api.source_auth.write import credential_fingerprint

        fingerprint = credential_fingerprint("v2ex", token)
        if (
            record
            and outcome.authenticated
            and outcome.username
            and hasattr(database, "set_v2ex_pat_identity")
        ):
            if fingerprint:
                database.set_v2ex_pat_identity(
                    outcome.username,
                    credential_fingerprint=fingerprint,
                )
        elif (
            record
            and outcome.has_credential
            and not outcome.authenticated
            and not outcome.network_error
            and hasattr(database, "clear_v2ex_pat_identity")
        ):
            database.clear_v2ex_pat_identity(credential_fingerprint=fingerprint)
        return outcome
    raise KeyError(slug)


def _action_from_probe(outcome: LiveProbeOutcome) -> _ActionResult:
    """Dress a probe result for the verify button.

    A probe that never got a credential to check, or that died in transport,
    established nothing — whatever the platform's standing verdict says.
    """
    return _ActionResult(
        outcome.message,
        conclusive=outcome.has_credential and not outcome.network_error,
    )


async def _verify_browser_heartbeat(slug: str, database: Any, event_hub: Any) -> _ActionResult:
    """Ask the extension to re-report, then wait for the heartbeat row to move.

    小红书 and 知乎 store a login *bool*, never the cookie (``database.py``
    :11723 says so explicitly), so the backend is architecturally incapable of
    probing them itself. The only verification available is a round trip
    through the browser — which is why their green light can never mean the
    same thing as B站's, and why ``verify_method`` has to exist at all.
    """
    prefix = _BROWSER_HEARTBEAT_PREFIXES.get(slug)
    if prefix is None:
        return _ActionResult(
            f"来源 {slug} 尚未注册浏览器登录态刷新通道。",
            conclusive=False,
        )
    getter = f"get_{prefix}_login_state"
    if not hasattr(database, getter):
        return _ActionResult("登录态存储不可用（数据库未就绪）。", conclusive=False)

    def _timestamp() -> str:
        try:
            _stored, when = getattr(database, getter)()
        except Exception:  # noqa: BLE001 - defensive; a read error is not a verdict
            return ""
        return str(when or "")

    before = _timestamp()

    publish = getattr(event_hub, "publish", None)
    if not callable(publish):
        return _ActionResult("运行时事件通道不可用，无法请求插件刷新登录态。", conclusive=False)

    delivered = False
    try:
        delivered = bool(
            await publish(
                {
                    "type": f"{prefix}_login_state_sync_requested",
                    "reason": "verify_requested",
                    "source": "verify-endpoint",
                }
            )
        )
    except Exception as exc:  # noqa: BLE001 - transport seam
        logger.debug("heartbeat request publish failed for %s", slug, exc_info=True)
        return _ActionResult(f"请求插件刷新登录态失败（{exc}）。", conclusive=False)

    if not delivered:
        # No subscriber took the event, so no browser will ever answer it.
        # Reporting this as a logged-out verdict would blame the user's account
        # for the extension being closed.
        return _ActionResult(
            "浏览器插件未连接，无法刷新登录态 —— 请打开浏览器并确认插件已连接后重试。",
            conclusive=False,
        )

    deadline = time.monotonic() + _HEARTBEAT_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(_HEARTBEAT_POLL_SECONDS)
        if _timestamp() != before:
            # The round trip completed, but "the extension answered" is not the
            # same as "the account is logged in" — it may well have answered
            # "logged out". Let the caller speak with the refreshed contract.
            return _ActionResult("插件已重新上报浏览器登录态。", adopt_contract_detail=True)

    return _ActionResult(
        f"已请求插件刷新登录态，但 {_HEARTBEAT_WAIT_SECONDS:.0f} 秒内没有收到回报，"
        "当前结论仍来自上一次心跳。",
        conclusive=False,
    )


def _verify_local_file(contract: SourceAuthContract) -> _ActionResult:
    """Reddit: the provider *is* the file read, so re-running it is the check.

    ``local_reddit_credential_status`` never invokes ``rdt`` and never reaches
    the network, so this stays a pure local re-read even though the credential
    lives in a third-party CLI's store.
    """
    return _ActionResult(contract.detail or "已重新读取本地 Reddit 凭据。")


# ── orchestration ────────────────────────────────────────────────────


async def verify_source(
    slug: str,
    *,
    cfg: Config,
    database: Any,
    event_hub: Any = None,
    probes: LiveProbeCache = LIVE_PROBES,
    debounce: VerifyDebounce = VERIFY_DEBOUNCE,
) -> VerifyOutcome:
    """Verify one platform and return its refreshed contract.

    Raises ``KeyError`` for an unknown slug; the route turns that into a 404.
    """
    if slug not in SOURCE_AUTH_PROVIDERS:
        raise KeyError(slug)

    replayed = debounce.replay(slug)
    if replayed is not None:
        return replayed

    before = _contract_for(slug, cfg, database, probes)

    if debounce.busy(slug):
        # A concurrent click. Answer from current state rather than firing a
        # second probe at the platform.
        return VerifyOutcome(
            slug=slug,
            contract=before,
            outcome="indeterminate",
            changed=False,
            message="该来源的验证正在进行中，请稍候。",
            # Not this call's work either — the click that is still running owns
            # it. No retry hint: the in-flight probe has no known finish time.
            replayed=True,
        )

    debounce.mark_started(slug)
    try:
        action = VERIFY_ACTIONS[slug]
        probe_outcome: LiveProbeOutcome | None = None
        if action == "live_probe":
            probe_outcome = await run_live_probe(
                slug,
                cfg=cfg,
                database=database,
                probes=probes,
            )
            result = _action_from_probe(probe_outcome)
        elif action == "passive_health":
            result = _ActionResult(
                "该来源只能提供被动健康记录，当前没有可执行的主动验证。",
                conclusive=False,
            )
        elif action == "browser_heartbeat":
            result = await _verify_browser_heartbeat(slug, database, event_hub)
        elif action == "local_file":
            result = _ActionResult("")  # filled in below from the refreshed contract
        else:
            result = _ActionResult(
                "该来源使用公开或匿名访客数据，无需登录，因此没有可执行的账号验证。",
                # Nothing was verified, because there is nothing to verify. The
                # one platform where that is the honest answer (invariant I3).
                conclusive=False,
            )

        if slug == "github" and probe_outcome is not None and probe_outcome.authenticated:
            # A successful official GET /user supersedes a producer-side 401
            # marker for the same configured PAT. Clear it before rebuilding
            # the contract; otherwise the marker would override the fresh probe
            # and make the recovery condition permanently unreachable.
            from openbiliclaw.runtime.github_producer import (
                clear_github_token_rejection,
            )

            clear_github_token_rejection(database)
        after = _contract_for(slug, cfg, database, probes)
        if action == "local_file":
            result = _verify_local_file(after)
    except BaseException:
        # ``BaseException``, not ``Exception``: ``asyncio.CancelledError`` has
        # inherited from it since 3.8, and cancellation is not exotic here — an
        # aborted fetch from the settings page or an upper-layer timeout both
        # produce one. Missing it left the in-flight marker set, so every click
        # for the next ``_INFLIGHT_MAX_SECONDS`` answered "验证正在进行中" for a
        # verification that had already stopped running.
        debounce.abandon(slug)
        raise

    # Only the two dimensions a verification can actually move are compared.
    # ``verified_at`` is excluded on purpose: a live probe rewrites it every
    # time, so including it would report "changed" on every single click.
    changed = (before.credential, before.verification) != (after.credential, after.verification)

    # Invariant I3 honesty gate. A contradiction here means a provider produced
    # a self-inconsistent contract — a code bug, not an environment problem, so
    # it is logged loudly and locked down by the contract tests rather than
    # turned into a 500 that hides the (still accurate) status from the user.
    problems = check_legacy_consistency(slug, after)
    if problems:
        logger.error("verify(%s) produced an inconsistent contract: %s", slug, "; ".join(problems))

    # An action that reached no verdict reports none, even when the platform's
    # standing verification is a perfectly good ``verified`` from an earlier
    # heartbeat. Deriving the outcome from the contract alone would put a green
    # "已验证" directly above the message "浏览器插件未连接" — the button would be
    # claiming credit for evidence it did not gather.
    outcome = VerifyOutcome(
        slug=slug,
        contract=after,
        outcome=_outcome_name(after.verification) if result.conclusive else "indeterminate",
        changed=changed,
        message=(
            (after.detail or result.message) if result.adopt_contract_detail else result.message
        ),
        # This call did the work, and it just armed the window for the next one.
        replayed=False,
        retry_after_seconds=_DEBOUNCE_SECONDS,
    )
    debounce.mark_finished(slug, outcome)
    return outcome
