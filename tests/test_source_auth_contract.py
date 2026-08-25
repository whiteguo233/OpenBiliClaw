"""Contract-freeze tests for ``GET /api/sources/status``.

Phase 0 safety net for the source-auth-contract refactor
(``docs/plans/2026-07-18-source-auth-contract-spec.md``).

Every case pins the **current** ``(state, logged_in)`` pair a platform
reports for a given credential precondition. Credential presence without a
probe remains unverified; once a live probe returns, the legacy fields must
move with the orthogonal verdict so one response cannot contradict itself.

Isolation (the hard requirement): each case runs against a temporary
project root, a temporary SQLite database, and a patched rdt credential
path. The suite must never read or write the developer's real ``data/``,
``config.toml`` or ``~/.config/rdt-cli/``. ``rdt-cli`` resolves its
credential through ``HOME`` rather than ``OPENBILICLAW_PROJECT_ROOT``, so
a real Reddit login on the dev machine would otherwise flip reddit to
``ready`` — see ``test_contract_fixture_isolates_real_user_data``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import product
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, get_args

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.source_auth import (
    Credential,
    SourceAuthContract,
    Verification,
    VerifyMethod,
    check_legacy_consistency,
)
from openbiliclaw.api.source_auth.forms import WRITABLE_FORM_KINDS
from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
from openbiliclaw.api.source_auth.providers import (
    _DOUYIN_DETAIL,
    SOURCE_AUTH_PROVIDERS,
    SourceAuthContext,
)
from openbiliclaw.api.source_auth.verify import (
    _BROWSER_HEARTBEAT_PREFIXES,
    VERIFY_ACTIONS,
    VERIFY_DEBOUNCE,
    VerifyDebounce,
    _verify_browser_heartbeat,
    verify_source,
)
from openbiliclaw.api.source_auth.write import CREDENTIAL_SPECS, FormKind
from openbiliclaw.config import Config
from openbiliclaw.runtime.init_prereqs import InitPrereqs
from openbiliclaw.sources.douyin_auth import DouyinCookieManager
from openbiliclaw.sources.douyin_direct import DouyinDirectError
from openbiliclaw.sources.linuxdo_tasks import LinuxdoTaskQueue
from openbiliclaw.sources.x_auth import XCookieManager
from openbiliclaw.sources.x_client import XAuthError, XClient
from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
from openbiliclaw.storage.database import Database
from openbiliclaw.storage.x_health import XSourceHealthStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


# A cookie carrying all three fields the bilibili branch counts.
_FULL_BILI_COOKIE = "SESSDATA=sess-abc; bili_jct=jct-def; DedeUserID=12345"


@dataclass
class _Env:
    """Everything a case's setup hook may touch — all of it disposable."""

    cfg: Config
    db: Database
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch
    rdt_credential_path: Path


@dataclass(frozen=True)
class _Case:
    """One frozen contract case: preconditions -> observed output.

    **All five legacy fields are pinned, not just ``(state, logged_in)``.**
    The compatibility promise covers the whole item — the desktop page renders
    ``detail`` verbatim beneath the status chip, the popup keys its "source is
    off" row off ``enabled``, and ``feed_paused`` drives X's circuit-breaker
    notice — so freezing two of the five left the other three free to drift
    under a refactor whose entire premise was byte-identical output. ``detail``
    is the likeliest to move by accident, being the only prose field: a
    provider rewrite that "clarified" one sentence would have sailed through a
    two-field assertion.
    """

    platform: str
    setup: Callable[[_Env], None]
    state: str
    logged_in: bool
    detail: str
    enabled: bool
    feed_paused: bool = False
    # Bangumi's optional-token axis (``ok`` / ``rejected`` / ``""``); "" for the
    # other platforms, which never set it. Frozen because it is
    # the other source-auth platforms, which never set it. Frozen because it is
    # what the frontend overlays as 「令牌已失效」, and a refactor moving Bangumi
    # onto the contract must not silently drop it.
    token_state: str = ""


# ── seeding helpers ──────────────────────────────────────────────────


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _seed_browser_login_state(db: Database, *, prefix: str, logged_in: bool, when_iso: str) -> None:
    """Write the extension's privacy-preserving login heartbeat row."""
    db.conn.executemany(
        "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
        [
            (f"{prefix}_login_state", "1" if logged_in else "0"),
            (f"{prefix}_login_state_at", when_iso),
        ],
    )
    db.conn.commit()


def _seed_x_health(db: Database, *, state: str, feed_paused: bool = False) -> None:
    """Force the X health row; constructing the store creates table + row."""
    XSourceHealthStore(db)
    db.conn.execute(
        "UPDATE x_source_health SET state = ?, feed_paused = ? WHERE key = 'x'",
        (state, 1 if feed_paused else 0),
    )
    db.conn.commit()


def _sqlite_now() -> str:
    """A timestamp in the shape SQLite's ``CURRENT_TIMESTAMP`` actually writes.

    UTC, and *without* an offset marker. The task queues stamp
    ``completed_at`` with ``CURRENT_TIMESTAMP``, so seeding a tz-aware ISO
    string here would have quietly made the fixtures friendlier than production
    and hidden the timezone defect these rows feed.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _seed_zhihu_task(db: Database, *, status: str, result: dict[str, object]) -> None:
    """Write one zhihu task so the no-heartbeat fallback path has history."""
    ZhihuTaskQueue(db)
    now = _sqlite_now()
    db.conn.execute(
        "INSERT INTO zhihu_tasks "
        "(id, type, payload_json, status, result_json, created_at, completed_at) "
        "VALUES (?, ?, '{}', ?, ?, ?, ?)",
        ("task-1", "recommend", status, json.dumps(result), now, now),
    )
    db.conn.commit()


def _seed_linuxdo_task(
    db: Database,
    *,
    task_type: str,
    status: str,
    result: dict[str, object],
) -> None:
    """Write one Linux.do task so the no-heartbeat fallback has evidence."""
    LinuxdoTaskQueue(db)
    now = _sqlite_now()
    db.conn.execute(
        "INSERT INTO linuxdo_tasks "
        "(id, type, payload_json, status, result_json, created_at, completed_at) "
        "VALUES (?, ?, '{}', ?, ?, ?, ?)",
        (
            f"linuxdo-{task_type}-{status}",
            task_type,
            status,
            json.dumps(result),
            now,
            now if status in {"completed", "failed"} else None,
        ),
    )
    db.conn.commit()


def _write_rdt_credential(path: Path, *, cookies: dict[str, str], age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": cookies, "saved_at": time.time() - age_days * 24 * 60 * 60}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_bili_cookie_file(data_dir: Path, cookie: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "bilibili_cookie.json").write_text(json.dumps({"cookie": cookie}), encoding="utf-8")


# ── bilibili preconditions ───────────────────────────────────────────


def _bili_full_cookie(env: _Env) -> None:
    env.cfg.bilibili.cookie = _FULL_BILI_COOKIE


def _bili_partial_cookie(env: _Env) -> None:
    """Only SESSDATA — the branch counts fields, so this is incomplete."""
    env.cfg.bilibili.cookie = "SESSDATA=sess-abc"


def _bili_empty_cookie(env: _Env) -> None:
    env.cfg.bilibili.cookie = ""


def _bili_cookie_file_only(env: _Env) -> None:
    """config.toml empty, data/bilibili_cookie.json full (CLI `auth login`)."""
    env.cfg.bilibili.cookie = ""
    _write_bili_cookie_file(env.cfg.data_path, _FULL_BILI_COOKIE)


def _bili_auth_method_none(env: _Env) -> None:
    env.cfg.bilibili.auth_method = "none"
    env.cfg.bilibili.cookie = ""


# ── xiaohongshu preconditions ────────────────────────────────────────


def _xhs_heartbeat_fresh(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="xhs", logged_in=True, when_iso=_iso_hours_ago(0.1))


def _xhs_heartbeat_within_window(env: _Env) -> None:
    """48h old: inside the 72h window today, and the TTL tamper sentinel."""
    _seed_browser_login_state(env.db, prefix="xhs", logged_in=True, when_iso=_iso_hours_ago(48))


def _xhs_heartbeat_stale(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="xhs", logged_in=True, when_iso=_iso_hours_ago(73))


def _xhs_heartbeat_logged_out(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="xhs", logged_in=False, when_iso=_iso_hours_ago(0.1))


def _xhs_no_heartbeat(env: _Env) -> None:
    """No row at all — never connected the extension."""


# ── douyin preconditions ─────────────────────────────────────────────


def _dy_cookie_file(env: _Env) -> None:
    env.cfg.sources.douyin.enabled = True
    DouyinCookieManager(env.cfg.data_path).set_cookie("sessionid=dy-test", source="test")


def _dy_cookie_env(env: _Env) -> None:
    env.cfg.sources.douyin.enabled = True
    env.monkeypatch.setenv("OPENBILICLAW_DOUYIN_COOKIE", "sessionid=dy-env")


def _dy_no_cookie(env: _Env) -> None:
    env.cfg.sources.douyin.enabled = True


# ── youtube preconditions ────────────────────────────────────────────


def _yt_enabled(env: _Env) -> None:
    env.cfg.sources.youtube.enabled = True


def _yt_disabled(env: _Env) -> None:
    env.cfg.sources.youtube.enabled = False


def _yt_enabled_with_unrelated_credentials(env: _Env) -> None:
    """Other platforms' credentials must not leak into youtube's verdict."""
    env.cfg.sources.youtube.enabled = True
    env.cfg.bilibili.cookie = _FULL_BILI_COOKIE


# ── weibo preconditions ─────────────────────────────────────────────


def _weibo_enabled(env: _Env) -> None:
    env.cfg.sources.weibo.enabled = True


def _weibo_disabled(env: _Env) -> None:
    env.cfg.sources.weibo.enabled = False


def _weibo_enabled_with_unrelated_credentials(env: _Env) -> None:
    """Account cookies for another source must never affect anonymous Weibo."""
    env.cfg.sources.weibo.enabled = True
    env.cfg.bilibili.cookie = _FULL_BILI_COOKIE


# ── twitter preconditions ────────────────────────────────────────────


def _x_ok_with_cookie(env: _Env) -> None:
    env.cfg.sources.twitter.enabled = True
    XCookieManager(env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")
    _seed_x_health(env.db, state="ok")


def _x_ok_after_real_success(env: _Env) -> None:
    """Same legacy verdict as above, but earned by a real request.

    The pair exists to make the distinction the legacy ``state`` cannot draw
    visible in the freeze table: both cases report ``("ok", True)``, and only
    this one has evidence behind it.

    The store is built with the cookie's fingerprint because that is what the
    producer does — a success has to say *which* credential earned it, or it is
    not attributable and does not count.
    """
    from openbiliclaw.api.source_auth.write import credential_fingerprint

    cookie = "auth_token=a; ct0=b"
    env.cfg.sources.twitter.enabled = True
    XCookieManager(env.cfg.data_path).set_cookie(cookie, source="test")
    XSourceHealthStore(
        env.db, credential_fingerprint=credential_fingerprint("twitter", cookie)
    ).record_success()


def _x_ok_without_cookie(env: _Env) -> None:
    """Health defaults to ok before any fetch; no cookie must downgrade it."""
    env.cfg.sources.twitter.enabled = True
    _seed_x_health(env.db, state="ok")


def _x_expired_cookie(env: _Env) -> None:
    env.cfg.sources.twitter.enabled = True
    XCookieManager(env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")
    _seed_x_health(env.db, state="expired_cookie")


def _x_rate_limited(env: _Env) -> None:
    env.cfg.sources.twitter.enabled = True
    XCookieManager(env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")
    _seed_x_health(env.db, state="rate_limited")


def _x_rate_limited_without_cookie(env: _Env) -> None:
    """Throttled by X, then the user removed the cookie.

    A legitimate, reachable combination — ``rate_limited`` has no timed path
    back to ``ok`` other than a later success, so the health row keeps saying
    it long after the credential is gone. The legacy state stays
    ``rate_limited`` (only ``ok`` is downgraded when no cookie resolves), which
    makes this the case that catches a consistency table asserting
    "rate_limited implies a stored credential".
    """
    env.cfg.sources.twitter.enabled = True
    _seed_x_health(env.db, state="rate_limited")


def _x_missing_cookie(env: _Env) -> None:
    env.cfg.sources.twitter.enabled = True
    _seed_x_health(env.db, state="missing_cookie")


def _x_blocked(env: _Env) -> None:
    env.cfg.sources.twitter.enabled = True
    XCookieManager(env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")
    _seed_x_health(env.db, state="blocked")


# ── zhihu preconditions ──────────────────────────────────────────────


def _zhihu_heartbeat_fresh(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="zhihu", logged_in=True, when_iso=_iso_hours_ago(0.1))


def _zhihu_heartbeat_within_window(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="zhihu", logged_in=True, when_iso=_iso_hours_ago(48))


def _zhihu_heartbeat_stale(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="zhihu", logged_in=True, when_iso=_iso_hours_ago(73))


def _zhihu_heartbeat_logged_out(env: _Env) -> None:
    _seed_browser_login_state(env.db, prefix="zhihu", logged_in=False, when_iso=_iso_hours_ago(0.1))


def _zhihu_no_heartbeat_no_history(env: _Env) -> None:
    """No heartbeat and no task rows — the default 'unverified' seed."""


def _zhihu_task_completed(env: _Env) -> None:
    """No heartbeat, but a completed task — the task_history fallback."""
    _seed_zhihu_task(env.db, status="completed", result={"items": [{"id": "1"}, {"id": "2"}]})


def _zhihu_task_login_required(env: _Env) -> None:
    _seed_zhihu_task(env.db, status="failed", result={"error": "zhihu_login_required"})


def _zhihu_task_failed(env: _Env) -> None:
    _seed_zhihu_task(env.db, status="failed", result={"error": "zhihu_timeout"})


def _zhihu_task_pending(env: _Env) -> None:
    _seed_zhihu_task(env.db, status="pending", result={})


# ── reddit preconditions (backend=rdt) ───────────────────────────────


def _reddit_credential_present(env: _Env) -> None:
    env.cfg.sources.reddit.enabled = True
    _write_rdt_credential(env.rdt_credential_path, cookies={"reddit_session": "sess"}, age_days=1)


def _reddit_credential_expired(env: _Env) -> None:
    """Older than the 7-day _RDT_CREDENTIAL_TTL_SECONDS."""
    env.cfg.sources.reddit.enabled = True
    _write_rdt_credential(env.rdt_credential_path, cookies={"reddit_session": "sess"}, age_days=8)


def _reddit_credential_missing(env: _Env) -> None:
    """No credential file — the isolated path simply does not exist."""
    env.cfg.sources.reddit.enabled = True


def _reddit_credential_without_session(env: _Env) -> None:
    env.cfg.sources.reddit.enabled = True
    _write_rdt_credential(env.rdt_credential_path, cookies={"other": "x"}, age_days=1)


# ── bangumi preconditions ────────────────────────────────────────────
# Bangumi is anonymous-public, so ``enabled`` is set on purpose per case. The
# discovery-health detail is "尚未运行 Bangumi 内容发现。" for a fresh db (no
# ``bangumi_discovery_runs`` rows), which every case here uses.

_BANGUMI_TOKEN = "bgm-personal-access-token"
_V2EX_TOKEN = "v2ex-personal-access-token"


def _bangumi_no_token(env: _Env) -> None:
    """Enabled, anonymous — the public-source default, no credential at all."""
    env.cfg.sources.bangumi.enabled = True


def _bangumi_token_unverified(env: _Env) -> None:
    """A token is configured but never live-checked (no cached verdict)."""
    env.cfg.sources.bangumi.enabled = True
    env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN


def _bangumi_token_verified(env: _Env) -> None:
    """A token whose /v0/me probe verdict is cached — like douyin's live case.

    Seeded with the token's own fingerprint because that is what the probe
    records: a verdict has to say *which* credential earned it (the same
    credential-bound rule the cookie platforms follow).
    """
    from openbiliclaw.api.source_auth.write import credential_fingerprint

    env.cfg.sources.bangumi.enabled = True
    env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN
    LIVE_PROBES.record(
        "bangumi",
        authenticated=True,
        detail="ok",
        network_error=False,
        fingerprint=credential_fingerprint("bangumi", _BANGUMI_TOKEN),
        username="215952",
    )


def _bangumi_token_disabled(env: _Env) -> None:
    """A saved token on a switched-off source — the credential is idle."""
    env.cfg.sources.bangumi.enabled = False
    env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN


# ── linuxdo preconditions ───────────────────────────────────────────


def _linuxdo_no_heartbeat(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True


def _linuxdo_heartbeat_fresh(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_browser_login_state(
        env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(0.1),
    )


def _linuxdo_heartbeat_stale(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = False
    _seed_browser_login_state(
        env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(73),
    )


def _linuxdo_heartbeat_logged_out(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_browser_login_state(
        env.db,
        prefix="linuxdo",
        logged_in=False,
        when_iso=_iso_hours_ago(0.1),
    )


def _linuxdo_bootstrap_completed(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_linuxdo_task(
        env.db,
        task_type="bootstrap_events",
        status="completed",
        result={
            "_openbiliclaw_terminal_status": "degraded",
            "items": [{"topic_id": "1"}, {"topic_id": "2"}],
        },
    )


def _linuxdo_discovery_completed(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_linuxdo_task(
        env.db,
        task_type="search",
        status="completed",
        result={
            "_openbiliclaw_terminal_status": "ok",
            "items": [{"topic_id": "1"}, {"topic_id": "2"}],
        },
    )


def _linuxdo_task_login_required(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_linuxdo_task(
        env.db,
        task_type="bootstrap_events",
        status="failed",
        result={"error": "linuxdo_login_required"},
    )


def _linuxdo_task_failed(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_linuxdo_task(
        env.db,
        task_type="search",
        status="failed",
        result={"error": "linuxdo_timeout"},
    )


def _linuxdo_task_pending(env: _Env) -> None:
    env.cfg.sources.linuxdo.enabled = True
    _seed_linuxdo_task(
        env.db,
        task_type="bootstrap_events",
        status="pending",
        result={},
    )


def _v2ex_no_token(env: _Env) -> None:
    """Enabled, anonymous — public V2EX discovery needs no credential."""
    env.cfg.sources.v2ex.enabled = True


def _v2ex_token_unverified(env: _Env) -> None:
    """An optional PAT is present but has not been live-checked yet."""
    env.cfg.sources.v2ex.enabled = True
    env.cfg.sources.v2ex.access_token = _V2EX_TOKEN


def _v2ex_token_disabled(env: _Env) -> None:
    """A saved PAT remains inert while the source is switched off."""
    env.cfg.sources.v2ex.enabled = False
    env.cfg.sources.v2ex.access_token = _V2EX_TOKEN


# ── the frozen contract ──────────────────────────────────────────────

_CASES: dict[str, _Case] = {
    # bilibili — counts SESSDATA / bili_jct / DedeUserID field names, offline.
    "bilibili-full-cookie": _Case(
        "bilibili",
        _bili_full_cookie,
        "ready",
        True,
        detail="Cookie 就绪（含 SESSDATA / bili_jct / DedeUserID）。",
        enabled=True,
    ),
    "bilibili-partial-cookie": _Case(
        "bilibili",
        _bili_partial_cookie,
        "partial",
        False,
        detail="Cookie 已配置，但缺少部分登录字段，可能未完整登录。",
        enabled=True,
    ),
    "bilibili-empty-cookie": _Case(
        "bilibili",
        _bili_empty_cookie,
        "missing",
        False,
        detail="未配置 Cookie —— 在浏览器登录 bilibili.com，插件会自动同步。",
        enabled=True,
    ),
    "bilibili-cookie-file-only": _Case(
        "bilibili",
        _bili_cookie_file_only,
        "ready",
        True,
        detail="Cookie 就绪（含 SESSDATA / bili_jct / DedeUserID）。",
        enabled=True,
    ),
    "bilibili-auth-method-none": _Case(
        "bilibili",
        _bili_auth_method_none,
        "no_auth",
        True,
        detail="未启用 B 站登录（auth_method=none）。",
        enabled=True,
    ),
    # xiaohongshu — extension heartbeat + 72h freshness window.
    "xhs-heartbeat-fresh": _Case(
        "xiaohongshu",
        _xhs_heartbeat_fresh,
        "ready",
        True,
        detail="已登录小红书。",
        enabled=False,
    ),
    "xhs-heartbeat-within-window": _Case(
        "xiaohongshu",
        _xhs_heartbeat_within_window,
        "ready",
        True,
        detail="已登录小红书。",
        enabled=False,
    ),
    "xhs-heartbeat-stale": _Case(
        "xiaohongshu",
        _xhs_heartbeat_stale,
        "stale",
        False,
        detail="小红书登录态已过期，请连接插件刷新本地状态。",
        enabled=False,
    ),
    "xhs-heartbeat-logged-out": _Case(
        "xiaohongshu",
        _xhs_heartbeat_logged_out,
        "missing",
        False,
        detail="未检测到小红书登录 —— 在浏览器登录小红书后插件会自动同步。",
        enabled=False,
    ),
    "xhs-no-heartbeat": _Case(
        "xiaohongshu",
        _xhs_no_heartbeat,
        "unverified",
        False,
        detail="尚未收到小红书浏览器登录态；插件连接后会在本地同步。",
        enabled=False,
    ),
    # weibo — anonymous discovery plus capability-specific browser heartbeat.
    "weibo-no-heartbeat": _Case(
        "weibo",
        _weibo_enabled,
        "no_auth",
        True,
        detail="尚未运行微博内容发现。 初始化本人事件需要浏览器登录态。",
        enabled=True,
    ),
    "weibo-disabled": _Case(
        "weibo",
        _weibo_disabled,
        "no_auth",
        True,
        detail="微博来源未启用。",
        enabled=False,
    ),
    "weibo-unrelated-credentials": _Case(
        "weibo",
        _weibo_enabled_with_unrelated_credentials,
        "no_auth",
        True,
        detail="尚未运行微博内容发现。 初始化本人事件需要浏览器登录态。",
        enabled=True,
    ),
    # douyin — cookie presence alone is unverified; a later live probe can move it.
    "douyin-cookie-file": _Case(
        "douyin",
        _dy_cookie_file,
        "unverified",
        False,
        detail="Cookie 已同步，需在实际任务中验证。",
        enabled=True,
    ),
    "douyin-cookie-env": _Case(
        "douyin",
        _dy_cookie_env,
        "unverified",
        False,
        detail="Cookie 已同步，需在实际任务中验证。",
        enabled=True,
    ),
    "douyin-no-cookie": _Case(
        "douyin",
        _dy_no_cookie,
        "missing",
        False,
        detail="未配置 Cookie —— 设置环境变量，或登录抖音后由插件同步。",
        enabled=True,
    ),
    # youtube — hard-coded no_auth regardless of anything else.
    "youtube-enabled": _Case(
        "youtube", _yt_enabled, "no_auth", True, detail="公开源 · 无需登录。", enabled=True
    ),
    "youtube-disabled": _Case(
        "youtube", _yt_disabled, "no_auth", True, detail="公开源 · 无需登录。", enabled=False
    ),
    "youtube-enabled-with-unrelated-credentials": _Case(
        "youtube",
        _yt_enabled_with_unrelated_credentials,
        "no_auth",
        True,
        detail="公开源 · 无需登录。",
        enabled=True,
    ),
    # twitter — passive health store, gated on a resolvable cookie.
    # The one legacy ``detail`` this wave moves on purpose. ``ok`` is the health
    # row's *default*, so this case is a cookie no request has ever used, and
    # the old string ("cookie 有效。") asserted the opposite of the verdict now
    # rendered beside it. `state` / `logged_in` are unchanged; the sibling case
    # below keeps the original string for a genuinely confirmed cookie.
    "twitter-health-ok-with-cookie": _Case(
        "twitter",
        _x_ok_with_cookie,
        "ok",
        True,
        detail="已配置 X Cookie，但还没有成功的真实请求可以确认登录态。",
        enabled=True,
    ),
    "twitter-health-ok-after-real-success": _Case(
        "twitter",
        _x_ok_after_real_success,
        "ok",
        True,
        detail="X 来源正常，cookie 有效。",
        enabled=True,
    ),
    "twitter-health-ok-without-cookie": _Case(
        "twitter",
        _x_ok_without_cookie,
        "missing_cookie",
        False,
        detail="未检测到登录 —— 在浏览器登录 x.com，插件会自动同步 cookie。",
        enabled=True,
    ),
    "twitter-expired-cookie": _Case(
        "twitter",
        _x_expired_cookie,
        "expired_cookie",
        False,
        detail="cookie 已过期 —— 请重新登录 x.com。",
        enabled=True,
    ),
    "twitter-rate-limited": _Case(
        "twitter",
        _x_rate_limited,
        "rate_limited",
        False,
        detail="cookie 正常，只是当前被 X 限流。已进入退避冷却，到点自动重试，无需手动操作。",
        enabled=True,
    ),
    "twitter-rate-limited-without-cookie": _Case(
        "twitter",
        _x_rate_limited_without_cookie,
        "rate_limited",
        False,
        detail="cookie 正常，只是当前被 X 限流。已进入退避冷却，到点自动重试，无需手动操作。",
        enabled=True,
    ),
    "twitter-missing-cookie": _Case(
        "twitter",
        _x_missing_cookie,
        "missing_cookie",
        False,
        detail="未检测到登录 —— 在浏览器登录 x.com，插件会自动同步 cookie。",
        enabled=True,
    ),
    "twitter-blocked": _Case(
        "twitter",
        _x_blocked,
        "blocked",
        False,
        detail="请求被拒绝 (403) —— 账号可能受限或需要重新验证。",
        enabled=True,
    ),
    # zhihu — heartbeat first, then the zhihu_tasks history fallback.
    "zhihu-heartbeat-fresh": _Case(
        "zhihu", _zhihu_heartbeat_fresh, "ready", True, detail="已登录知乎。", enabled=False
    ),
    "zhihu-heartbeat-within-window": _Case(
        "zhihu",
        _zhihu_heartbeat_within_window,
        "ready",
        True,
        detail="已登录知乎。",
        enabled=False,
    ),
    "zhihu-heartbeat-stale": _Case(
        "zhihu",
        _zhihu_heartbeat_stale,
        "stale",
        False,
        detail="知乎登录态已过期，请连接插件刷新本地状态。",
        enabled=False,
    ),
    "zhihu-heartbeat-logged-out": _Case(
        "zhihu",
        _zhihu_heartbeat_logged_out,
        "missing",
        False,
        detail="浏览器最近同步的状态为未登录知乎。",
        enabled=False,
    ),
    "zhihu-no-heartbeat-no-history": _Case(
        "zhihu",
        _zhihu_no_heartbeat_no_history,
        "unverified",
        False,
        detail=(
            "浏览器插件登录态源 · 尚未看到知乎任务结果，保存后可运行 init 或 discover 验证登录态。"
        ),
        enabled=False,
    ),
    "zhihu-task-history-completed": _Case(
        "zhihu",
        _zhihu_task_completed,
        "ready",
        True,
        detail="最近任务完成（recommend，2 条）。",
        enabled=False,
    ),
    "zhihu-task-history-login-required": _Case(
        "zhihu",
        _zhihu_task_login_required,
        "missing",
        False,
        detail="最近知乎任务提示需要登录知乎。请在当前浏览器登录知乎后重试。",
        enabled=False,
    ),
    "zhihu-task-history-failed": _Case(
        "zhihu",
        _zhihu_task_failed,
        "partial",
        False,
        detail="最近知乎任务失败：zhihu_timeout。可在浏览器登录态正常后重试。",
        enabled=False,
    ),
    "zhihu-task-history-pending": _Case(
        "zhihu",
        _zhihu_task_pending,
        "unverified",
        False,
        detail="知乎任务正在等待插件执行（recommend / pending）。",
        enabled=False,
    ),
    # reddit — local rdt credential file, 7-day TTL, never invokes rdt.
    "reddit-credential-present": _Case(
        "reddit",
        _reddit_credential_present,
        "ready",
        True,
        detail="Reddit 本地凭据已就绪（未实时访问 Reddit 验证）。",
        enabled=True,
    ),
    "reddit-credential-expired": _Case(
        "reddit",
        _reddit_credential_expired,
        "stale",
        False,
        detail="rdt credential 已过期，请等待插件重新同步或运行 `rdt login`。",
        enabled=True,
    ),
    "reddit-credential-missing": _Case(
        "reddit",
        _reddit_credential_missing,
        "login_required",
        False,
        detail=(
            "rdt 已安装但未同步 Reddit Cookie。请在已连接插件的浏览器登录 Reddit；"
            "插件会自动同步，也可运行 `rdt login`。"
        ),
        enabled=True,
    ),
    "reddit-credential-without-session": _Case(
        "reddit",
        _reddit_credential_without_session,
        "error",
        False,
        detail="rdt credential 缺少 reddit_session，请在已连接插件的浏览器重新登录 Reddit。",
        enabled=True,
    ),
    # bangumi — anonymous-public (auth_required=False) with an optional token.
    # ``state`` is always ``no_auth`` and the discovery-health string lives in
    # ``detail``; the optional-token verdict rides the ``auth`` contract and the
    # ``token_state`` axis, never the legacy ``state`` (that conflation is D1).
    "bangumi-no-token": _Case(
        "bangumi",
        _bangumi_no_token,
        "no_auth",
        True,
        detail="尚未运行 Bangumi 内容发现。",
        enabled=True,
        token_state="",
    ),
    "bangumi-token-unverified": _Case(
        "bangumi",
        _bangumi_token_unverified,
        "no_auth",
        True,
        detail="尚未运行 Bangumi 内容发现。",
        enabled=True,
        token_state="ok",
    ),
    "bangumi-token-verified": _Case(
        "bangumi",
        _bangumi_token_verified,
        "no_auth",
        True,
        detail="尚未运行 Bangumi 内容发现。",
        enabled=True,
        token_state="ok",
    ),
    "bangumi-token-disabled": _Case(
        "bangumi",
        _bangumi_token_disabled,
        "no_auth",
        True,
        # Frozen literal (not derived from the producer) so a wording change has
        # to be a conscious edit here. noqa: a CJK string ruff format keeps on one
        # line but pycodestyle counts as >100 — the codebase's standard escape.
        detail="已保存个人令牌，但它现在不会被使用；把 Bangumi 来源开关切到「启用」并保存后才会生效。",  # noqa: E501
        enabled=False,
        token_state="ok",
    ),
    # linuxdo — anonymous-public discovery with an optional extension heartbeat.
    "linuxdo-no-heartbeat": _Case(
        "linuxdo",
        _linuxdo_no_heartbeat,
        "no_auth",
        True,
        detail=("Linux.do 公开发现无需登录；连接已登录插件后可同步个人收藏、点赞和阅读记录。"),
        enabled=True,
    ),
    "linuxdo-heartbeat-fresh": _Case(
        "linuxdo",
        _linuxdo_heartbeat_fresh,
        "no_auth",
        True,
        detail="Linux.do 公开发现可用；浏览器登录态已连接，可同步个人收藏、点赞和阅读记录。",
        enabled=True,
    ),
    "linuxdo-heartbeat-stale-disabled": _Case(
        "linuxdo",
        _linuxdo_heartbeat_stale,
        "no_auth",
        True,
        detail="Linux.do 公开发现仍可用；个人登录态已过期，请连接插件刷新。",
        enabled=False,
    ),
    "linuxdo-heartbeat-logged-out": _Case(
        "linuxdo",
        _linuxdo_heartbeat_logged_out,
        "no_auth",
        True,
        detail="Linux.do 公开发现可用；浏览器当前未登录，个人信号同步暂不可用。",
        enabled=True,
    ),
    "linuxdo-task-history-bootstrap-completed": _Case(
        "linuxdo",
        _linuxdo_bootstrap_completed,
        "no_auth",
        True,
        detail=(
            "Linux.do 公开发现可用；最近个人信号同步降级完成（2 条），"
            "浏览器会话可用于收藏、点赞和阅读记录。"
        ),
        enabled=True,
    ),
    "linuxdo-task-history-discovery-completed": _Case(
        "linuxdo",
        _linuxdo_discovery_completed,
        "no_auth",
        True,
        detail=(
            "Linux.do 公开发现最近任务完成（search，2 条）；"
            "公开任务不证明浏览器已登录，个人信号仍需登录态。"
        ),
        enabled=True,
    ),
    "linuxdo-task-history-login-required": _Case(
        "linuxdo",
        _linuxdo_task_login_required,
        "no_auth",
        True,
        detail=(
            "Linux.do 公开发现仍可用；最近任务提示需要登录，"
            "请在当前浏览器登录 Linux.do 后重试个人信号同步。"
        ),
        enabled=True,
    ),
    "linuxdo-task-history-failed": _Case(
        "linuxdo",
        _linuxdo_task_failed,
        "no_auth",
        True,
        detail=(
            "Linux.do 最近任务失败（search）：linuxdo_timeout；"
            "公开发现仍可用，该运行错误无法判断个人登录态。"
        ),
        enabled=True,
    ),
    "linuxdo-task-history-pending": _Case(
        "linuxdo",
        _linuxdo_task_pending,
        "no_auth",
        True,
        detail=("Linux.do 任务正在等待插件执行（bootstrap_events / pending）；公开发现无需登录。"),
        enabled=True,
    ),
    # v2ex — anonymous public discovery with an optional API 2.0 PAT.
    "v2ex-no-token": _Case(
        "v2ex",
        _v2ex_no_token,
        "no_auth",
        True,
        detail="公开源 · 无需登录；可选填 PAT 以识别账号并增强 Node / Topic API。",
        enabled=True,
    ),
    "v2ex-token-unverified": _Case(
        "v2ex",
        _v2ex_token_unverified,
        "no_auth",
        True,
        detail="已保存 V2EX 个人令牌，尚未联网确认；可点「测试连接」验证。",
        enabled=True,
    ),
    "v2ex-token-disabled": _Case(
        "v2ex",
        _v2ex_token_disabled,
        "no_auth",
        True,
        detail="已保存 V2EX 个人令牌，尚未联网确认；可点「测试连接」验证。",
        enabled=False,
    ),
}


@pytest.fixture
def contract_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """Build a fully disposable environment for one contract case.

    Three separate escapes have to be closed, because the endpoint reads
    credentials from three different roots:

    1. ``OPENBILICLAW_PROJECT_ROOT`` — redirects ``cfg.data_path`` (and so
       every ``data/*.json`` cookie file) into ``tmp_path``.
    2. the credential env vars — a developer shell that exports a real
       douyin / X cookie would otherwise be read straight through.
    3. ``_rdt_credential_file`` — rdt-cli resolves through ``HOME``, which
       no ``OPENBILICLAW_*`` variable can redirect.
    """
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    for var in ("OPENBILICLAW_DOUYIN_COOKIE", "OPENBILICLAW_X_COOKIE"):
        monkeypatch.delenv(var, raising=False)

    rdt_credential_path = tmp_path / "rdt" / "credential.json"
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks._rdt_credential_file",
        lambda: rdt_credential_path,
    )

    # Both stores are process-wide on purpose — the probe verdict so guided-init
    # and the settings page cannot disagree about one cookie, the debounce so a
    # config save does not re-arm the verify button for another round of probes.
    # Process-wide also means a verdict recorded by one case would otherwise
    # answer for the next one, so each case starts from an empty store.
    LIVE_PROBES.clear()
    VERIFY_DEBOUNCE.clear()

    db = Database(tmp_path / "sources-status.db")
    db.initialize()
    cfg = Config()

    # Fail loudly rather than ever touching the developer's real data dir.
    assert cfg.data_path.is_relative_to(tmp_path.resolve()), (
        f"isolation breach: data_path={cfg.data_path} escaped tmp_path={tmp_path}"
    )

    return _Env(
        cfg=cfg,
        db=db,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        rdt_credential_path=rdt_credential_path,
    )


def _status_payload(env: _Env) -> dict[str, dict[str, object]]:
    """Apply the case's preconditions and read /api/sources/status once."""
    env.monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: env.cfg)
    app = create_app(memory_manager=object(), database=env.db, soul_engine=object())
    with TestClient(app) as client:
        response = client.get("/api/sources/status")
    assert response.status_code == 200
    return dict(response.json())


@pytest.mark.parametrize("case_id", list(_CASES), ids=list(_CASES))
def test_sources_status_legacy_fields_are_frozen(case_id: str, contract_env: _Env) -> None:
    """Pin all five legacy fields for one platform precondition.

    Recording current behaviour, not desired behaviour — see module
    docstring before changing any expectation.

    The assertion covers ``state``, ``logged_in``, ``detail``, ``enabled`` and
    ``feed_paused`` because that is the whole of what the endpoint promised not
    to move. An earlier version checked only the first two while the PR text
    claimed all five were frozen, which is a weaker guarantee wearing a
    stronger label — the exact failure mode this suite exists to catch.
    """
    case = _CASES[case_id]
    case.setup(contract_env)

    item = _status_payload(contract_env)[case.platform]

    assert (
        item["state"],
        item["logged_in"],
        item["detail"],
        item["enabled"],
        item["feed_paused"],
        item["token_state"],
    ) == (
        case.state,
        case.logged_in,
        case.detail,
        case.enabled,
        case.feed_paused,
        case.token_state,
    )

    # The orthogonal contract ships alongside the legacy verdict, and the two
    # views must never contradict each other. Not equality: ``ready`` maps
    # legitimately to either ``verified`` or ``unverified``, because the legacy
    # value never distinguished them (see source_auth/legacy.py).
    contract = SourceAuthContract.model_validate(item["auth"])
    assert (contract.legacy_state, contract.legacy_logged_in) == (case.state, case.logged_in)
    assert check_legacy_consistency(case.platform, contract) == []


@pytest.mark.parametrize(
    ("setup", "credential", "origin", "verification", "has_timestamp"),
    [
        (_linuxdo_bootstrap_completed, "present", "extension", "verified", True),
        (_linuxdo_discovery_completed, "none", "none", "unverified", False),
        (_linuxdo_task_login_required, "none", "none", "failed", True),
        (_linuxdo_task_failed, "none", "none", "unverified", False),
        (_linuxdo_task_pending, "none", "none", "unverified", False),
    ],
    ids=["bootstrap-success", "public-success", "login-required", "failed", "pending"],
)
def test_linuxdo_task_history_fallback_preserves_optional_auth(
    contract_env: _Env,
    setup: Callable[[_Env], None],
    credential: str,
    origin: str,
    verification: str,
    has_timestamp: bool,
) -> None:
    setup(contract_env)
    item = _status_payload(contract_env)["linuxdo"]
    contract = SourceAuthContract.model_validate(item["auth"])

    assert (item["state"], item["logged_in"]) == ("no_auth", True)
    assert contract.auth_required is False
    assert contract.verify_method == "task_history"
    assert (contract.credential, contract.credential_origin) == (credential, origin)
    assert contract.verification == verification
    assert bool(contract.verified_at) is has_timestamp
    assert check_legacy_consistency("linuxdo", contract) == []


def test_linuxdo_newer_session_probe_takes_precedence_over_cookie_heartbeat(
    contract_env: _Env,
) -> None:
    _linuxdo_task_login_required(contract_env)
    _seed_browser_login_state(
        contract_env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(0.1),
    )

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["linuxdo"]["auth"])
    assert contract.verify_method == "task_history"
    assert contract.verification == "failed"
    assert contract.credential == "none"
    assert contract.capabilities["discover"].readiness == "ready"
    assert contract.capabilities["profile"].readiness == "login_required"


def test_linuxdo_authoritative_logout_survives_a_newer_positive_cookie_heartbeat(
    contract_env: _Env,
) -> None:
    _linuxdo_task_login_required(contract_env)
    contract_env.db.conn.execute(
        "UPDATE linuxdo_tasks SET completed_at = ?, created_at = ?",
        (_iso_hours_ago(2), _iso_hours_ago(2)),
    )
    contract_env.db.conn.commit()
    _seed_browser_login_state(
        contract_env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(0.1),
    )

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["linuxdo"]["auth"])

    assert contract.verify_method == "task_history"
    assert contract.verification == "failed"
    assert contract.credential == "none"
    assert contract.capabilities["profile"].readiness == "login_required"


def test_linuxdo_stale_task_success_no_longer_admits_personal_capabilities(
    contract_env: _Env,
) -> None:
    _linuxdo_bootstrap_completed(contract_env)
    contract_env.db.conn.execute(
        "UPDATE linuxdo_tasks SET completed_at = ?, created_at = ?",
        (_iso_hours_ago(73), _iso_hours_ago(73)),
    )
    contract_env.db.conn.commit()

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["linuxdo"]["auth"])

    assert contract.verify_method == "task_history"
    assert contract.verification == "stale"
    assert contract.capabilities["discover"].readiness == "ready"
    assert contract.capabilities["profile"].readiness == "stale"
    assert contract.capability_ready("profile") is False


def test_linuxdo_stale_logout_can_be_retried_after_a_fresh_cookie_heartbeat(
    contract_env: _Env,
) -> None:
    _linuxdo_task_login_required(contract_env)
    contract_env.db.conn.execute(
        "UPDATE linuxdo_tasks SET completed_at = ?, created_at = ?",
        (_iso_hours_ago(73), _iso_hours_ago(73)),
    )
    contract_env.db.conn.commit()
    _seed_browser_login_state(
        contract_env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(0.1),
    )

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["linuxdo"]["auth"])

    assert contract.verify_method == "browser_heartbeat"
    assert contract.verification == "unverified"
    assert contract.capabilities["profile"].readiness == "ready"


def test_linuxdo_pending_retry_does_not_hide_last_authoritative_logout(
    contract_env: _Env,
) -> None:
    _linuxdo_task_login_required(contract_env)
    _seed_linuxdo_task(
        contract_env.db,
        task_type="bootstrap_events",
        status="pending",
        result={},
    )
    _seed_browser_login_state(
        contract_env.db,
        prefix="linuxdo",
        logged_in=True,
        when_iso=_iso_hours_ago(0.1),
    )

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["linuxdo"]["auth"])

    assert contract.verify_method == "task_history"
    assert contract.verification == "failed"
    assert contract.capabilities["bootstrap"].readiness == "login_required"


@pytest.mark.parametrize("verification", ["failed", "unverified"])
def test_linuxdo_optional_task_history_evidence_is_a_narrow_consistency_exception(
    verification: str,
) -> None:
    contract = SourceAuthContract(
        auth_required=False,
        credential="none",
        credential_origin="none",
        verification=verification,  # type: ignore[arg-type]
        verify_method="task_history",
        legacy_state="no_auth",
        legacy_logged_in=True,
    )

    assert check_legacy_consistency("linuxdo", contract) == []
    assert check_legacy_consistency("bangumi", contract) != []


def test_contract_covers_every_platform_with_at_least_three_preconditions() -> None:
    """Spec Phase 0 gate: every platform has at least three frozen cases."""
    """Spec Phase 0 gate: every registered platform has >=3 preconditions."""
    per_platform: dict[str, int] = {}
    for case in _CASES.values():
        per_platform[case.platform] = per_platform.get(case.platform, 0) + 1

    assert set(per_platform) == {
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
        "bangumi",
        "linuxdo",
        "v2ex",
        "weibo",
    }
    thin = {platform: n for platform, n in per_platform.items() if n < 3}
    assert not thin, f"platforms with fewer than 3 preconditions: {thin}"
    assert len(_CASES) >= 27


def test_auth_dimensions_are_orthogonal() -> None:
    """Invariant I2: the four auth dimensions vary independently.

    The failure this guards against is a well-meaning validator — "if
    ``verification`` is ``verified`` then ``credential`` must be ``present``" —
    which would quietly re-merge the dimensions the whole contract exists to
    separate, and re-create D1 inside the replacement model.

    Two properties per combination: every combination is *constructible* with
    its values intact, and changing any one dimension leaves the other three
    untouched.
    """
    dimensions: dict[str, tuple[object, ...]] = {
        "verification": get_args(Verification),
        "credential": get_args(Credential),
        "verify_method": get_args(VerifyMethod),
        "auth_required": (True, False),
    }
    assert [len(values) for values in dimensions.values()] == [6, 3, 6, 2]

    combos = list(product(*dimensions.values()))
    assert len(combos) == 6 * 3 * 6 * 2 == 216

    for combo in combos:
        baseline = dict(zip(dimensions, combo, strict=True))
        contract = SourceAuthContract(**baseline)  # type: ignore[arg-type]
        assert {name: getattr(contract, name) for name in dimensions} == baseline

        for name, values in dimensions.items():
            others = {key: value for key, value in baseline.items() if key != name}
            for value in values:
                mutated = SourceAuthContract(**{**baseline, name: value})  # type: ignore[arg-type]
                assert getattr(mutated, name) == value
                assert {key: getattr(mutated, key) for key in others} == others


def test_sources_status_makes_no_outbound_request(contract_env: _Env) -> None:
    """The status endpoint must stay offline no matter how much it knows.

    Open settings pages poll this route about every 30s. Two platforms now
    declare ``verify_method="live_probe"`` (B站 and, since spec D11, 抖音), and
    the tempting wiring is to probe from here — which would mean an idle
    settings tab hitting 抖音 twice a minute forever, the exact shape that gets
    an account risk-flagged. Probing belongs to the explicit verify action; this
    handler only reads cached verdicts.

    Every platform is given a credential first, so no branch short-circuits
    before reaching the code that would be tempted to call out.
    """
    for setup in (
        _bili_full_cookie,
        _xhs_heartbeat_fresh,
        _dy_cookie_file,
        _x_ok_with_cookie,
        _zhihu_heartbeat_fresh,
        _reddit_credential_present,
    ):
        setup(contract_env)

    contract_env.monkeypatch.setattr(
        "openbiliclaw.config.load_config", lambda *_a, **_kw: contract_env.cfg
    )
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())

    attempts: list[str] = []

    def _refuse(*args: object, **kwargs: object) -> object:
        attempts.append(f"{args[:2]!r}")
        raise AssertionError("/api/sources/status attempted an outbound call")

    # Only the request itself is guarded — app construction may legitimately
    # touch the network. ``ASGITransport`` (what TestClient uses) is untouched,
    # so patching the real transports cannot break the test's own request.
    with TestClient(app) as client, contract_env.monkeypatch.context() as guard:
        guard.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        guard.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
        guard.setattr(socket.socket, "connect", _refuse)
        guard.setattr(subprocess, "run", _refuse)
        response = client.get("/api/sources/status")

    assert response.status_code == 200
    assert attempts == []
    # Sanity: the payload really did exercise the credential-bearing branches.
    payload = response.json()
    assert payload["bilibili"]["auth"]["credential"] == "present"
    assert payload["douyin"]["auth"]["verify_method"] == "live_probe"


@pytest.mark.parametrize(
    ("recorded", "expected_verification", "expected_legacy"),
    [
        ({"authenticated": True}, "verified", ("ready", True)),
        ({"authenticated": False}, "failed", ("unverified", False)),
        # A proxy/timeout failure says nothing about the cookie, so it must not
        # be reported as a rejection — that is how a flaky network turns into a
        # bogus "your login expired".
        (
            {"authenticated": False, "network_error": True},
            "unverified",
            ("unverified", False),
        ),
    ],
    ids=["logged-in", "logged-out", "transport-failure"],
)
def test_live_probe_verdict_reaches_the_contract(
    contract_env: _Env,
    recorded: dict[str, bool],
    expected_verification: str,
    expected_legacy: tuple[str, bool],
) -> None:
    """A cached probe verdict drives both contract and compatibility views."""
    from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES

    _dy_cookie_file(contract_env)
    LIVE_PROBES.clear()
    try:
        LIVE_PROBES.record("douyin", **recorded)
        item = _status_payload(contract_env)["douyin"]
    finally:
        LIVE_PROBES.clear()

    assert (item["state"], item["logged_in"]) == expected_legacy

    contract = SourceAuthContract.model_validate(item["auth"])
    assert contract.verification == expected_verification
    assert contract.verify_method == "live_probe"
    assert bool(contract.verified_at) is (expected_verification in {"verified", "failed"})
    assert check_legacy_consistency("douyin", contract) == []


# ── POST /api/sources/{slug}/verify ──────────────────────────────────

#: What each platform's contract must report after a verify. Hard-coded rather
#: than read from the code so a provider quietly changing its declared evidence
#: strength fails here instead of silently downgrading a green light.
_EXPECTED_VERIFY_METHODS = {
    "bilibili": "live_probe",
    "xiaohongshu": "browser_heartbeat",
    "douyin": "live_probe",
    "youtube": "none",
    "twitter": "live_probe",
    "zhihu": "browser_heartbeat",
    "reddit": "local_file",
    # ``none`` here because this suite runs with *no credentials*: Bangumi's
    # contract reports ``live_probe`` only once a token is configured (there is
    # something to probe), and ``none`` when anonymous — the same state-dependent
    # method 知乎 has. The action ``VERIFY_ACTIONS['bangumi']`` is still
    # ``live_probe`` (see the bangumi verify tests below, which supply a token).
    "bangumi": "none",
    # Linux.do public discovery works anonymously. Without a browser heartbeat
    # there is no credential verdict to verify, so the current method is none;
    # the fixed verify action remains browser_heartbeat.
    "linuxdo": "none",
    "weibo": "none",
    # Instagram's fixed verify action can request the first heartbeat, while
    # the source-wide legacy method stays none until evidence exists (I3).
    "instagram": "none",
    # V2EX is anonymous by default; its optional PAT only becomes probeable
    # after the caller supplies a token.
    "v2ex": "none",
}


def _verify_post(env: _Env, slug: str) -> dict[str, object]:
    """POST the verify route once and return the decoded body."""
    env.monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: env.cfg)
    app = create_app(memory_manager=object(), database=env.db, soul_engine=object())
    with TestClient(app) as client:
        response = client.post(f"/api/sources/{slug}/verify")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _install_douyin_probe(env: _Env, payload: object) -> list[str]:
    """Fake 抖音's transport and return a list that records every request.

    Patched at ``DouyinDirectClient`` rather than at ``probe_douyin_login`` on
    purpose: the mapping from Douyin's status codes to a login verdict is the
    thing spec D11 established experimentally, so the test has to run the real
    mapping. Stubbing the probe itself would keep passing after somebody
    "simplified" that mapping away.

    *payload* is either the JSON body to return or an exception to raise.
    """
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *, cookie: str, http_client: object = None) -> None:
            self._cookie = cookie

        async def request_json(self, path: str, _params: dict[str, object]) -> object:
            calls.append(path)
            if isinstance(payload, Exception):
                raise payload
            return payload

        async def aclose(self) -> None:
            return None

    env.monkeypatch.setattr(
        "openbiliclaw.sources.douyin_login_probe.DouyinDirectClient", _FakeClient
    )
    return calls


def test_verify_action_table_covers_every_platform() -> None:
    """Every provider has exactly one verify action, and no orphans exist.

    A platform added to the registry without an action would raise a KeyError
    on the first click; one added to the action table without a provider would
    be a 404 nobody notices until a user reports it.
    """
    assert set(VERIFY_ACTIONS) == set(SOURCE_AUTH_PROVIDERS)
    assert set(_EXPECTED_VERIFY_METHODS) == set(SOURCE_AUTH_PROVIDERS)
    assert set(_BROWSER_HEARTBEAT_PREFIXES) == {
        slug for slug, action in VERIFY_ACTIONS.items() if action == "browser_heartbeat"
    }


@pytest.mark.parametrize("slug", sorted(_EXPECTED_VERIFY_METHODS), ids=str)
def test_verify_returns_200_and_declared_method_for_every_platform(
    slug: str, contract_env: _Env
) -> None:
    """Every platform answers the route with its declared evidence strength.

    Run with no credentials anywhere, so no platform has anything to probe —
    which is also why the outbound guard can be absolute here: a verify with
    nothing to verify must not reach for the network on any platform.
    Every registered platform answers with its declared evidence strength.

    Run with no credentials anywhere, so no platform has anything to probe —
    which is also why the outbound guard can be absolute here: a verify with
    nothing to verify must not reach for the network on any registered source.
    """
    attempts: list[str] = []

    def _refuse(*args: object, **_kw: object) -> object:
        attempts.append(f"{args[:2]!r}")
        raise AssertionError(f"verify({slug}) attempted an outbound call")

    contract_env.monkeypatch.setattr(
        "openbiliclaw.config.load_config", lambda *_a, **_kw: contract_env.cfg
    )
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())
    with TestClient(app) as client, contract_env.monkeypatch.context() as guard:
        guard.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        guard.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
        guard.setattr(subprocess, "run", _refuse)
        response = client.post(f"/api/sources/{slug}/verify")

    assert response.status_code == 200, response.text
    assert attempts == []

    body = response.json()
    assert body["slug"] == slug
    contract = SourceAuthContract.model_validate(body["auth"])
    assert contract.verify_method == _EXPECTED_VERIFY_METHODS[slug]
    # The verify response and the status endpoint must describe one reality.
    assert contract.verify_method == _status_payload(contract_env)[slug]["auth"]["verify_method"]
    # Nothing was verifiable, so nothing may claim to have been verified.
    assert body["outcome"] == "indeterminate"
    assert body["message"]
    assert check_legacy_consistency(slug, contract) == []


def test_x_verify_runs_read_only_account_probe_and_records_health(contract_env: _Env) -> None:
    contract_env.cfg.sources.twitter.enabled = True
    cookie = "auth_token=a; ct0=b"
    XCookieManager(contract_env.cfg.data_path).set_cookie(cookie, source="test")
    calls: list[str] = []

    async def _probe(self: XClient) -> object:
        calls.append(self._cookie)
        return SimpleNamespace(screen_name="alice", id="42")

    contract_env.monkeypatch.setattr(XClient, "probe", _probe)
    body = _verify_post(contract_env, "twitter")

    assert calls == [cookie]
    assert body["outcome"] == "verified"
    auth = SourceAuthContract.model_validate(body["auth"])
    assert auth.verify_method == "live_probe"
    assert auth.verification == "verified"
    assert auth.detail == "X 来源正常，cookie 有效。"
    assert XSourceHealthStore(contract_env.db).get()["last_success_at"]


def test_x_verify_turns_auth_failure_into_expired_cookie(contract_env: _Env) -> None:
    contract_env.cfg.sources.twitter.enabled = True
    XCookieManager(contract_env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")

    async def _probe(_self: XClient) -> object:
        raise XAuthError("401 unauthorized")

    contract_env.monkeypatch.setattr(XClient, "probe", _probe)
    body = _verify_post(contract_env, "twitter")

    assert body["outcome"] == "failed"
    auth = SourceAuthContract.model_validate(body["auth"])
    assert auth.verify_method == "live_probe"
    assert auth.verification == "failed"
    assert auth.legacy_state == "expired_cookie"


def test_verify_rejects_unknown_slug(contract_env: _Env) -> None:
    """An unknown platform is a 404, not a silently empty contract."""
    contract_env.monkeypatch.setattr(
        "openbiliclaw.config.load_config", lambda *_a, **_kw: contract_env.cfg
    )
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())
    with TestClient(app) as client:
        assert client.post("/api/sources/mastodon/verify").status_code == 404


def test_verify_never_claims_verified_for_a_source_with_no_verify_method(
    contract_env: _Env,
) -> None:
    """Invariant I3: YouTube needs no login, so it can never report one.

    The tempting bug is to render "no credential needed" as a green tick by
    setting ``verification="verified"``. That would make ``verified`` mean two
    different things — "we asked the platform" and "there was nothing to ask" —
    which is D1 reappearing inside the replacement contract.
    """
    body = _verify_post(contract_env, "youtube")
    contract = SourceAuthContract.model_validate(body["auth"])

    assert contract.verify_method == "none"
    assert contract.verification != "verified"
    assert contract.auth_required is False
    assert body["outcome"] == "indeterminate"
    assert body["changed"] is False
    assert check_legacy_consistency("youtube", contract) == []


def test_verify_reddit_reads_the_local_file_without_running_rdt(contract_env: _Env) -> None:
    """``local_file`` must stay local: no subprocess, no network."""
    _reddit_credential_present(contract_env)

    contract_env.monkeypatch.setattr(
        "openbiliclaw.config.load_config", lambda *_a, **_kw: contract_env.cfg
    )
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())

    def _refuse(*args: object, **_kw: object) -> object:
        raise AssertionError("reddit verify shelled out or went to the network")

    with TestClient(app) as client, contract_env.monkeypatch.context() as guard:
        guard.setattr(subprocess, "run", _refuse)
        guard.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        response = client.post("/api/sources/reddit/verify")

    body = response.json()
    contract = SourceAuthContract.model_validate(body["auth"])
    assert contract.verify_method == "local_file"
    assert contract.verification == "verified"
    assert body["outcome"] == "verified"


@pytest.mark.parametrize(
    ("payload", "expected_verification", "expected_outcome"),
    [
        (
            {"status_code": 0, "user": {"uid": "9876543210", "nickname": "小白"}},
            "verified",
            "verified",
        ),
        ({"status_code": 8, "status_msg": "用户未登录"}, "failed", "failed"),
    ],
    ids=["logged-in", "logged-out"],
)
def test_douyin_probe_distinguishes_login(
    contract_env: _Env,
    payload: dict[str, object],
    expected_verification: str,
    expected_outcome: str,
) -> None:
    """Freeze spec D11's experimental result.

    抖音 was hard-wired to "never verifiable" on the strength of a docstring
    claiming it had no endpoint that separates logged-out from soft anti-bot.
    A strip-down control experiment refuted that: ``status_code=0`` plus a real
    uid means logged in, ``status_code=8`` ("用户未登录") means logged out. This
    test exists so that a future reader who sees ``unverified`` on their own
    machine cannot conclude the probe is useless and delete it again — if the
    discriminator ever genuinely stops working, this fails and says so.
    """
    _dy_cookie_file(contract_env)
    calls = _install_douyin_probe(contract_env, payload)

    body = _verify_post(contract_env, "douyin")

    assert calls == ["/aweme/v1/web/user/profile/self/"]
    contract = SourceAuthContract.model_validate(body["auth"])
    assert contract.verification == expected_verification
    assert contract.verify_method == "live_probe"
    assert body["outcome"] == expected_outcome
    assert contract.verified_at
    assert (contract.legacy_state, contract.legacy_logged_in) == (
        ("ready", True) if expected_verification == "verified" else ("unverified", False)
    )
    assert check_legacy_consistency("douyin", contract) == []


def test_verify_debounce(contract_env: _Env) -> None:
    """A second click inside the window replays the result and does not probe.

    The failure this prevents is a user holding down a verify button and
    hand-delivering a burst of identical requests to 抖音's risk control.
    """
    _dy_cookie_file(contract_env)
    calls = _install_douyin_probe(
        contract_env, {"status_code": 0, "user": {"uid": "9876543210", "nickname": "小白"}}
    )

    first = _verify_post(contract_env, "douyin")
    second = _verify_post(contract_env, "douyin")

    assert len(calls) == 1, f"debounce let a second probe through: {calls}"
    assert first["changed"] is True
    assert second["changed"] is False
    # The replay is the same answer, not a downgraded one.
    assert second["outcome"] == first["outcome"] == "verified"
    assert second["auth"] == first["auth"]


@pytest.mark.parametrize(
    "payload",
    [
        DouyinDirectError("proxy exploded"),
        RuntimeError("connection reset"),
        {},  # transport swallowed by the client; a real response always has status_code
        {"status_code": 2154, "status_msg": "风控"},
    ],
    ids=["douyin-error", "unexpected-error", "empty-payload", "unknown-status-code"],
)
def test_transport_failure_is_not_logged_out(contract_env: _Env, payload: object) -> None:
    """A broken probe means "cannot tell", never "your cookie expired".

    Reporting a proxy hiccup as ``failed`` is how a flaky network talks a user
    into deleting a perfectly good credential. Every way the probe can fail to
    reach a verdict has to land on ``unverified`` / ``indeterminate``.
    """
    _dy_cookie_file(contract_env)
    _install_douyin_probe(contract_env, payload)

    body = _verify_post(contract_env, "douyin")
    contract = SourceAuthContract.model_validate(body["auth"])

    assert contract.verification == "unverified"
    assert body["outcome"] == "indeterminate"
    assert contract.verification != "failed"
    # An indeterminate result must not masquerade as a dated verdict.
    assert contract.verified_at == ""
    assert check_legacy_consistency("douyin", contract) == []


# ── Bangumi: anonymous-public with an optional, live-verifiable token ─────
# The eighth platform breaks the auth_required boolean: it works with no
# credential (like YouTube) yet its optional personal token *can* be verified
# against /v0/me. These reproduce the 2026-07-19 stripped-control result — real
# token identifies the account, forged/absent is rejected — and pin the three
# outcomes the button must draw (verified / failed / indeterminate).


def _install_bangumi_probe(
    env: _Env, *, payload: object = None, error: Exception | None = None
) -> list[str]:
    """Fake Bangumi's ``/v0/me`` transport; return the tokens it was called with.

    Patched at ``BangumiClient`` rather than at ``run_live_probe`` so the real
    error-code → verdict mapping runs (an ``unauthorized`` must become ``failed``
    while a timeout must become ``indeterminate``); stubbing the probe would keep
    passing if that mapping were simplified away.
    """
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *, access_token: str | None = None, **_kw: object) -> None:
            self._token = access_token

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get_me(self) -> object:
            calls.append(str(self._token or ""))
            if error is not None:
                raise error
            return payload

    env.monkeypatch.setattr("openbiliclaw.sources.bangumi_client.BangumiClient", _FakeClient)
    return calls


def test_bangumi_verify_with_a_valid_token_is_verified(contract_env: _Env) -> None:
    """A real token identifies the account, so /v0/me confirms it (verified)."""
    contract_env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN
    calls = _install_bangumi_probe(
        contract_env, payload={"username": "215952", "nickname": "demo", "id": 215952}
    )

    body = _verify_post(contract_env, "bangumi")

    assert calls == [_BANGUMI_TOKEN], "verify must actually hit /v0/me with the token"
    contract = SourceAuthContract.model_validate(body["auth"])
    assert contract.verify_method == "live_probe"
    assert contract.verification == "verified"
    assert contract.verified_at
    assert body["outcome"] == "verified"
    assert check_legacy_consistency("bangumi", contract) == []


def test_bangumi_verify_with_a_forged_token_is_failed(contract_env: _Env) -> None:
    """An invalid / expired token is rejected with ``unauthorized`` → failed.

    This is the real negative verdict, the half of the discriminator that makes
    ``live_probe`` honest (§0.1 requires a *difference* between the groups, not
    one group merely looking normal).
    """
    from openbiliclaw.sources.bangumi_client import BangumiAPIError

    contract_env.cfg.sources.bangumi.access_token = "forged-token"
    _install_bangumi_probe(
        contract_env, error=BangumiAPIError("unauthorized", "rejected", status_code=401)
    )

    body = _verify_post(contract_env, "bangumi")
    contract = SourceAuthContract.model_validate(body["auth"])

    assert contract.verification == "failed"
    assert contract.verify_method == "live_probe"
    assert body["outcome"] == "failed"
    assert check_legacy_consistency("bangumi", contract) == []


def test_bangumi_verify_without_a_token_is_indeterminate(contract_env: _Env) -> None:
    """No token is the anonymous-public default, never a "logged out" verdict.

    The probe returns before any network call — a public source with no token to
    check has nothing to verify, so the honest answer is "cannot tell", not
    "failed". The outbound guard proves the no-token path stays offline.
    """
    contract_env.cfg.sources.bangumi.enabled = True  # enabled, but no token

    contract_env.monkeypatch.setattr(
        "openbiliclaw.config.load_config", lambda *_a, **_kw: contract_env.cfg
    )
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())

    def _refuse(*_args: object, **_kw: object) -> object:
        raise AssertionError("a token-less bangumi verify went to the network")

    with TestClient(app) as client, contract_env.monkeypatch.context() as guard:
        guard.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
        response = client.post("/api/sources/bangumi/verify")

    assert response.status_code == 200, response.text
    body = response.json()
    contract = SourceAuthContract.model_validate(body["auth"])
    assert contract.verify_method == "none"
    assert contract.verification == "unverified"
    assert body["outcome"] == "indeterminate"
    assert "令牌" in body["message"]
    assert check_legacy_consistency("bangumi", contract) == []


def test_bangumi_verify_transport_failure_is_not_a_dead_token(contract_env: _Env) -> None:
    """A proxy/timeout says nothing about the token — indeterminate, not failed.

    On the current box the custom proxy cannot reach api.bgm.tv, so this is the
    realistic outcome of a real verify, and it must never read as "your token
    expired" (invariant I3).
    """
    from openbiliclaw.sources.bangumi_client import BangumiAPIError

    contract_env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN
    _install_bangumi_probe(contract_env, error=BangumiAPIError("timeout", "timed out"))

    body = _verify_post(contract_env, "bangumi")
    contract = SourceAuthContract.model_validate(body["auth"])

    assert contract.verification == "unverified"
    assert contract.verification != "failed"
    assert body["outcome"] == "indeterminate"
    assert contract.verified_at == ""
    assert check_legacy_consistency("bangumi", contract) == []


def test_bangumi_status_reflects_a_cached_token_verdict(contract_env: _Env) -> None:
    """A cached /v0/me verdict drives the status contract, like douyin's.

    ``legacy_state`` stays ``no_auth`` (Bangumi never *needs* a login) whatever
    the token verdict is — the optional-token verdict rides ``verification``,
    the axis that can honestly move.
    """
    contract_env.cfg.sources.bangumi.access_token = _BANGUMI_TOKEN
    from openbiliclaw.api.source_auth.write import credential_fingerprint

    LIVE_PROBES.clear()
    try:
        LIVE_PROBES.record(
            "bangumi",
            authenticated=False,
            detail="unauthorized",
            network_error=False,
            fingerprint=credential_fingerprint("bangumi", _BANGUMI_TOKEN),
        )
        item = _status_payload(contract_env)["bangumi"]
    finally:
        LIVE_PROBES.clear()

    assert item["state"] == "no_auth"  # anonymous-public, verdict aside
    contract = SourceAuthContract.model_validate(item["auth"])
    assert contract.auth_required is False
    assert contract.credential == "present"
    assert contract.verify_method == "live_probe"
    assert contract.verification == "failed"
    assert check_legacy_consistency("bangumi", contract) == []


def test_optional_credential_may_carry_a_live_method_but_none_may_not() -> None:
    """The consistency refinement Bangumi needed, both directions (invariant I3).

    An ``auth_required=False`` source with a credential *present* legitimately
    verifies it (Bangumi's token). The same source with *no* credential must
    not claim a live method — that is YouTube-style overclaim, and the guard
    that catches it has to survive the refinement that lets Bangumi through.
    """
    optional_present = SourceAuthContract(
        auth_required=False,
        credential="present",
        credential_origin="config",
        verification="unverified",
        verify_method="live_probe",
        verify_ttl_seconds=3600,
        legacy_state="no_auth",
        legacy_logged_in=True,
    )
    assert check_legacy_consistency("bangumi", optional_present) == []

    overclaim = SourceAuthContract(
        auth_required=False,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="live_probe",
        legacy_state="no_auth",
        legacy_logged_in=True,
    )
    assert check_legacy_consistency("bangumi", overclaim) != []


# ── B站: one credential, one verdict, two endpoints ──────────────────


def _install_bili_probe(
    env: _Env, *, authenticated: bool, network_error: bool = False, message: str = ""
) -> list[str]:
    """Fake the B站 nav probe for both entry points at once.

    Both are patched deliberately: the point of the test below is that the two
    surfaces share a verdict, so each must be able to originate one.
    """
    calls: list[str] = []

    class _FakeAuthManager:
        def __init__(self, *, data_dir: object, proxy: object = None) -> None:
            self._data_dir = data_dir

        async def validate_cookie(self, cookie: str) -> object:
            calls.append(cookie)
            return SimpleNamespace(
                has_cookie=True,
                authenticated=authenticated,
                username="白",
                message=message,
                network_error=network_error,
            )

    env.monkeypatch.setattr("openbiliclaw.bilibili.auth.AuthManager", _FakeAuthManager)
    env.monkeypatch.setattr("openbiliclaw.runtime.init_prereqs.AuthManager", _FakeAuthManager)
    return calls


def _init_prereqs_for(env: _Env) -> InitPrereqs:
    """A guided-init prereqs object over the same config the API sees."""
    return InitPrereqs(SimpleNamespace(config=env.cfg, llm_registry=None))


#: Verdict pairs that would mean the two endpoints disagree about one cookie.
#: ``("failed", "unverified")`` is deliberately absent: guided-init blocks on a
#: probe it could not complete while the contract declines to blame the cookie
#: for it. That is two honest readings of one verdict, not a contradiction.
_CONTRADICTIONS = {("ok", "failed"), ("failed", "verified")}


@pytest.mark.parametrize("authenticated", [True, False], ids=["accepted", "rejected"])
async def test_bilibili_verdict_is_shared_by_init_status_and_sources_status(
    contract_env: _Env, authenticated: bool
) -> None:
    """D3, verdict axis: neither endpoint may contradict the other.

    Task 5 unified which *cookie* the two surfaces read. They still each kept a
    private cache of what a probe concluded about it, which is the same bug one
    level up: guided-init could hold "this cookie is rejected" while the
    settings page showed a green light for the very same credential. The
    verdict now lives in one store, and this locks both directions of the
    exchange — a probe fired by either surface must be visible to the other.
    """
    _bili_full_cookie(contract_env)
    calls = _install_bili_probe(contract_env, authenticated=authenticated)

    # Direction 1: guided-init probes -> the settings page sees the verdict.
    prereqs = _init_prereqs_for(contract_env)
    init_verdict = await prereqs.bilibili_check()
    assert init_verdict == ("ok" if authenticated else "failed")
    assert len(calls) == 1

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["bilibili"]["auth"])
    assert contract.verification == ("verified" if authenticated else "failed")
    assert (init_verdict, contract.verification) not in _CONTRADICTIONS
    # The status endpoint reused the verdict rather than probing again.
    assert len(calls) == 1

    # Direction 2: the verify action probes -> guided-init sees that verdict.
    LIVE_PROBES.clear()
    VERIFY_DEBOUNCE.clear()
    body = _verify_post(contract_env, "bilibili")
    verified_contract = SourceAuthContract.model_validate(body["auth"])
    assert len(calls) == 2

    assert _init_prereqs_for(contract_env).peek_bilibili() == ("ok" if authenticated else "failed")
    assert (
        _init_prereqs_for(contract_env).peek_bilibili(),
        verified_contract.verification,
    ) not in _CONTRADICTIONS


async def test_bilibili_transport_failure_reads_honestly_on_both_endpoints(
    contract_env: _Env,
) -> None:
    """One verdict, two correct renderings — and neither of them is a lie.

    Guided-init must block a setup it could not confirm, so a transport failure
    reads as ``failed`` there. The contract must not tell the user their cookie
    expired because a proxy flaked, so the same verdict reads as ``unverified``.
    The pair is allowed; ``("failed", "verified")`` would not be.
    """
    _bili_full_cookie(contract_env)
    _install_bili_probe(
        contract_env, authenticated=False, network_error=True, message="代理连接失败"
    )

    prereqs = _init_prereqs_for(contract_env)
    assert await prereqs.bilibili_check() == "failed"
    assert "代理连接失败" in prereqs.peek_bilibili_detail()

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["bilibili"]["auth"])
    assert contract.verification == "unverified"
    assert ("failed", contract.verification) not in _CONTRADICTIONS


async def test_bilibili_verdict_is_dropped_when_the_cookie_goes_away(
    contract_env: _Env,
) -> None:
    """A verdict describes a specific credential; clearing it invalidates both.

    Otherwise a stale "this cookie works" would keep answering for a cookie the
    user has since removed.
    """
    _bili_full_cookie(contract_env)
    _install_bili_probe(contract_env, authenticated=True)

    prereqs = _init_prereqs_for(contract_env)
    assert await prereqs.bilibili_check() == "ok"

    contract_env.cfg.bilibili.cookie = ""
    assert await prereqs.bilibili_check() == "failed"
    assert LIVE_PROBES.peek("bilibili") is None

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["bilibili"]["auth"])
    assert contract.credential == "none"
    assert contract.verification == "unverified"


# ── browser-heartbeat round trip (小红书 / 知乎 / Linux.do) ─────────


async def test_unknown_browser_heartbeat_source_never_falls_back_to_zhihu() -> None:
    class _Database:
        def get_zhihu_login_state(self) -> tuple[bool, str]:
            return True, "stale"

    class _Hub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> bool:
            self.events.append(event)
            return True

    hub = _Hub()
    result = await _verify_browser_heartbeat("unregistered", _Database(), hub)

    assert hub.events == []
    assert result.conclusive is False
    assert "尚未注册" in result.message


@pytest.mark.parametrize(
    ("slug", "prefix"),
    [("xiaohongshu", "xhs"), ("zhihu", "zhihu"), ("linuxdo", "linuxdo")],
    ids=["xhs", "zhihu", "linuxdo"],
)
async def test_verify_browser_heartbeat_waits_for_the_extension(
    contract_env: _Env, slug: str, prefix: str
) -> None:
    """The extension answers, the heartbeat row moves, the verdict follows.

    These extension-backed platforms are why ``verify_method`` has to exist: the
    backend holds a login *bool*, never their cookie, so it is architecturally
    incapable of probing them itself. The only verification available is a
    round trip through the browser.
    """
    _seed_browser_login_state(
        contract_env.db, prefix=prefix, logged_in=True, when_iso=_iso_hours_ago(73)
    )
    stale = SourceAuthContract.model_validate(_status_payload(contract_env)[slug]["auth"])
    assert stale.verification == "stale"

    class _RespondingHub:
        """Stands in for a connected extension: replies with a fresh heartbeat."""

        def __init__(self) -> None:
            self.events: list[str] = []

        async def publish(self, event: dict[str, object]) -> bool:
            self.events.append(str(event.get("type", "")))
            _seed_browser_login_state(
                contract_env.db, prefix=prefix, logged_in=True, when_iso=_iso_hours_ago(0)
            )
            return True

    hub = _RespondingHub()
    result = await verify_source(
        slug, cfg=contract_env.cfg, database=contract_env.db, event_hub=hub
    )

    assert hub.events == [f"{prefix}_login_state_sync_requested"]
    assert result.contract.verify_method == "browser_heartbeat"
    expected_verification = "unverified" if slug == "linuxdo" else "verified"
    assert result.contract.verification == expected_verification
    assert result.outcome == ("indeterminate" if slug == "linuxdo" else "verified")
    assert result.changed is True
    assert check_legacy_consistency(slug, result.contract) == []


async def test_weibo_browser_heartbeat_roundtrip_updates_verification(
    contract_env: _Env,
) -> None:
    """微博 login-state callback is the sole backend auth evidence."""
    _weibo_enabled(contract_env)
    _seed_browser_login_state(
        contract_env.db, prefix="weibo", logged_in=True, when_iso=_iso_hours_ago(73)
    )

    class _RespondingHub:
        async def publish(self, event: dict[str, object]) -> bool:
            assert event["type"] == "weibo_login_state_sync_requested"
            _seed_browser_login_state(
                contract_env.db, prefix="weibo", logged_in=True, when_iso=_iso_hours_ago(0)
            )
            return True

    result = await verify_source(
        "weibo",
        cfg=contract_env.cfg,
        database=contract_env.db,
        event_hub=_RespondingHub(),
    )

    assert result.contract.verify_method == "browser_heartbeat"
    assert result.contract.verification == "verified"
    assert result.contract.capabilities["bootstrap"].readiness == "ready"
    assert result.changed is True


async def test_instagram_browser_heartbeat_roundtrip_updates_verification(
    contract_env: _Env,
) -> None:
    """Instagram verification asks the extension for boolean login evidence."""
    contract_env.cfg.sources.instagram.enabled = True
    _seed_browser_login_state(
        contract_env.db,
        prefix="instagram",
        logged_in=True,
        when_iso=_iso_hours_ago(73),
    )

    class _RespondingHub:
        async def publish(self, event: dict[str, object]) -> bool:
            assert event["type"] == "instagram_login_state_sync_requested"
            _seed_browser_login_state(
                contract_env.db,
                prefix="instagram",
                logged_in=True,
                when_iso=_iso_hours_ago(0),
            )
            return True

    result = await verify_source(
        "instagram",
        cfg=contract_env.cfg,
        database=contract_env.db,
        event_hub=_RespondingHub(),
    )

    assert result.contract.verify_method == "browser_heartbeat"
    assert result.contract.verification == "verified"
    assert result.contract.capabilities["bootstrap"].readiness == "ready"
    assert result.changed is True


async def test_instagram_first_verify_requests_initial_browser_heartbeat(
    contract_env: _Env,
) -> None:
    """No prior row must not disable the extension heartbeat action."""

    contract_env.cfg.sources.instagram.enabled = True

    class _RespondingHub:
        async def publish(self, event: dict[str, object]) -> bool:
            assert event["type"] == "instagram_login_state_sync_requested"
            _seed_browser_login_state(
                contract_env.db,
                prefix="instagram",
                logged_in=True,
                when_iso=_iso_hours_ago(0),
            )
            return True

    before = SOURCE_AUTH_PROVIDERS["instagram"](
        SourceAuthContext(cfg=contract_env.cfg, database=contract_env.db)
    )
    assert before.can_verify_now is True
    assert before.verify_method == "none"

    result = await verify_source(
        "instagram",
        cfg=contract_env.cfg,
        database=contract_env.db,
        event_hub=_RespondingHub(),
        debounce=VerifyDebounce(),
    )

    assert result.contract.verification == "verified"
    assert result.contract.capabilities["bootstrap"].readiness == "ready"
    assert result.changed is True


@pytest.mark.parametrize(
    ("slug", "prefix"),
    [("xiaohongshu", "xhs"), ("zhihu", "zhihu"), ("linuxdo", "linuxdo")],
    ids=["xhs", "zhihu", "linuxdo"],
)
async def test_verify_browser_heartbeat_answering_logged_out_says_so(
    contract_env: _Env, slug: str, prefix: str
) -> None:
    """The round trip happened; the answer was "not logged in". Say *that*.

    The action itself can only observe that the heartbeat row moved — it cannot
    tell what the extension put in it. Reporting its own fact ("插件已重新上报
    浏览器登录态。") under a failure tone produces a success-sounding sentence in
    red, which reads as a UI bug and tells the user nothing about what to fix.
    So the caller substitutes the refreshed contract's own detail.
    """
    _seed_browser_login_state(
        contract_env.db, prefix=prefix, logged_in=True, when_iso=_iso_hours_ago(1)
    )

    class _LoggedOutHub:
        """A connected extension that answers honestly: this browser is logged out."""

        async def publish(self, event: dict[str, object]) -> bool:
            _seed_browser_login_state(
                contract_env.db, prefix=prefix, logged_in=False, when_iso=_iso_hours_ago(0)
            )
            return True

    result = await verify_source(
        slug, cfg=contract_env.cfg, database=contract_env.db, event_hub=_LoggedOutHub()
    )

    assert result.outcome == "failed"
    # The round-trip fact must not be the whole message under a failure tone.
    assert result.message != "插件已重新上报浏览器登录态。"
    assert result.message == result.contract.detail
    assert result.message, "a failed verdict must explain itself"
    assert check_legacy_consistency(slug, result.contract) == []


@pytest.mark.parametrize(
    ("slug", "prefix"),
    [("xiaohongshu", "xhs"), ("zhihu", "zhihu"), ("linuxdo", "linuxdo")],
    ids=["xhs", "zhihu", "linuxdo"],
)
async def test_verify_browser_heartbeat_without_extension_is_indeterminate(
    contract_env: _Env, slug: str, prefix: str
) -> None:
    """A closed browser is not a verification, even when the state looks good.

    Seeded with a *fresh* heartbeat on purpose. The platform's standing verdict
    is therefore ``verified``, and deriving the button's answer from that alone
    would render a green "已验证" right above a message saying the plugin could
    not be reached — claiming credit for evidence this click never gathered.
    The standing verdict stays in ``auth`` where it belongs; the click reports
    only what the click achieved.

    ``publish`` returning False means no subscriber took the event, so no
    browser will ever answer it.
    """
    _seed_browser_login_state(
        contract_env.db, prefix=prefix, logged_in=True, when_iso=_iso_hours_ago(1)
    )

    class _EmptyHub:
        async def publish(self, _event: dict[str, object]) -> bool:
            return False

    result = await verify_source(
        slug, cfg=contract_env.cfg, database=contract_env.db, event_hub=_EmptyHub()
    )

    # The state we already had is unchanged and still reported honestly...
    assert result.contract.verification == ("unverified" if slug == "linuxdo" else "verified")
    # ...but this attempt verified nothing, and says so.
    assert result.outcome == "indeterminate"
    assert result.changed is False
    assert "插件" in result.message
    assert check_legacy_consistency(slug, result.contract) == []


def test_contract_fixture_isolates_real_user_data(contract_env: _Env) -> None:
    """The isolation itself is a tested property, not a convention.

    If this regresses, the suite would start reading the developer's live
    cookies and a real Reddit login would silently turn cases green.
    """
    from openbiliclaw.sources import reddit_tasks

    tmp_root = contract_env.tmp_path.resolve()

    assert contract_env.cfg.data_path.is_relative_to(tmp_root)
    assert reddit_tasks._rdt_credential_file().is_relative_to(tmp_root)
    assert contract_env.rdt_credential_path.is_relative_to(tmp_root)
    # The endpoint must not see a stray developer cookie through the env.
    import os

    assert not os.environ.get("OPENBILICLAW_DOUYIN_COOKIE")
    assert not os.environ.get("OPENBILICLAW_X_COOKIE")


# ── Wave B: the unified credential write path (plan Task 8 / Task 9) ───


#: A structurally invalid — but non-empty and non-masked — credential per
#: platform. Non-empty matters: an empty field means "not edited" to
#: ``PUT /api/config`` and is resolved by that route's partial-update
#: semantics before validation is reached, so an empty value would compare the
#: two paths on something that is not a validation decision at all.
_INVALID_CREDENTIALS: dict[str, tuple[str, str]] = {
    # One of the three fields the bilibili branch counts.
    "bilibili": ("SESSDATA=only-one-field", "cookie_invalid"),
    # A guest 抖音 jar: device cookies, no session family (spec D11's control group).
    "douyin": ("ttwid=guest-tw; odin_tt=guest-odin", "cookie_invalid"),
    # twitter-cli 401s without ct0.
    "twitter": ("auth_token=at-only; guest_id=gx", "missing_x_cookies"),
    # rdt-cli's hard requirement.
    "reddit": ("loid=abc; token_v2=tok", "missing_reddit_session"),
}

#: Where each platform's credential would land. Asserting on these is the point
#: of "invalid credentials must not be stored": a response code says what the
#: server claimed, the filesystem says what it did.
_CREDENTIAL_STORES: dict[str, str] = {
    "bilibili": "bilibili_cookie.json",
    "douyin": "douyin_cookie.json",
    "twitter": "x_cookie.json",
}


def _config_patch_for(slug: str, value: str) -> dict[str, object]:
    """The ``PUT /api/config`` body that writes *slug*'s credential.

    One route, four platforms — and its path leaf is ``config``, so no scan
    keyed on a path word root can see it (invariant I7). That blindness is
    exactly why it went unvalidated for as long as it did (spec D5).
    """
    if slug == "bilibili":
        return {"bilibili": {"cookie": value}}
    return {"sources": {slug: {"cookie": value}}}


def _write_client(env: _Env) -> TestClient:
    # ``PUT /api/config`` refuses to save a config it considers broken, and a
    # bare ``Config()`` has no usable LLM provider. Give it one, so that every
    # 400 these tests assert on is unambiguously a credential verdict rather
    # than an unrelated config-validation refusal.
    env.cfg.llm.deepseek.api_key = "sk-test-not-used"
    env.monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: env.cfg)
    return TestClient(create_app(memory_manager=object(), database=env.db, soul_engine=object()))


def _stub_live_probe(env: _Env, *, authenticated: bool = True) -> list[tuple[str, str]]:
    """Answer the shared live gate locally; return what it was asked."""
    from openbiliclaw.api.source_auth import verify as verify_module

    calls: list[tuple[str, str]] = []

    async def _probe(slug, *, cfg, cookie=None, probes=None, record=True):
        calls.append((slug, str(cookie or "")))
        return verify_module.LiveProbeOutcome(
            slug=slug,
            has_credential=True,
            authenticated=authenticated,
            network_error=False,
            message="stubbed probe",
            username="tester",
        )

    env.monkeypatch.setattr(verify_module, "run_live_probe", _probe)
    return calls


@pytest.mark.parametrize("slug", sorted(_INVALID_CREDENTIALS))
def test_write_paths_have_equal_validation(contract_env: _Env, slug: str) -> None:
    """One credential, two write surfaces, one verdict (invariant I5, spec D4).

    Before Wave B these disagreed in the worst possible direction: the endpoint
    the browser extension uses ran a live probe and refused a dead B站 cookie,
    while the settings page's paste box — ``PUT /api/config`` — wrote the same
    dead cookie to ``config.toml`` without a single check and reported success.
    Whether a user's credential was validated depended on which page they had
    open.

    Both paths are driven with the identical value here and must return the
    identical ``error_code``. The values are structurally invalid on purpose, so
    the comparison needs no network and cannot be quietly satisfied by both
    paths merely failing to reach the platform.
    """
    value, expected_code = _INVALID_CREDENTIALS[slug]

    with contract_env.monkeypatch.context() as guard:

        def _refuse(*_args: object, **_kwargs: object) -> object:
            raise AssertionError(f"{slug} write validation must not go out for an invalid value")

        guard.setattr(httpx.HTTPTransport, "handle_request", _refuse)
        guard.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
        guard.setattr(subprocess, "run", _refuse)

        with _write_client(contract_env) as client:
            posted = client.post(f"/api/sources/{slug}/credential", json={"value": value})
            put = client.put("/api/config", json=_config_patch_for(slug, value))

    assert posted.status_code == 200, posted.text
    post_body = posted.json()
    assert post_body["accepted"] is False
    assert post_body["error_code"] == expected_code

    # The config route reports a refusal as 400 (it has no per-field result
    # object to carry one), but the *verdict* underneath must be the same one.
    assert put.status_code == 400, put.text
    assert put.json()["detail"]["error"] == expected_code


@pytest.mark.parametrize("slug", sorted(_INVALID_CREDENTIALS))
def test_invalid_credential_never_reaches_the_store(contract_env: _Env, slug: str) -> None:
    """A refused credential leaves no trace on disk — checked on disk.

    Asserting only on the response would pass for an endpoint that says "not
    saved" and saves anyway, which is precisely what X and 抖音 used to do
    (spec D5: "结构校验但仍先落盘" / "无脑存").
    """
    value, _code = _INVALID_CREDENTIALS[slug]
    config_cookie_before = contract_env.cfg.bilibili.cookie

    with _write_client(contract_env) as client:
        for path, body in (
            (f"/api/sources/{slug}/credential", {"value": value}),
            ("/api/config", _config_patch_for(slug, value)),
        ):
            response = (
                client.post(path, json=body)
                if path.endswith("credential")
                else client.put(path, json=body)
            )
            assert response.status_code in {200, 400}

    if slug == "reddit":
        assert not contract_env.rdt_credential_path.exists()
    else:
        store = contract_env.cfg.data_path / _CREDENTIAL_STORES[slug]
        assert not store.exists(), f"{slug} persisted a credential it said it refused: {store}"
    if slug == "bilibili":
        # config.toml is B站's mirror, and the only credential store that is not
        # a file under data/ — a refusal has to leave it alone too.
        assert contract_env.cfg.bilibili.cookie == config_cookie_before


def test_config_route_keeps_masked_echo_and_blank_field_semantics(contract_env: _Env) -> None:
    """Empty / masked fields stay a no-op, and never reach the gate.

    These are ``PUT /api/config``'s partial-update protocol — "this field was
    not edited" — not credential verdicts. Routing them into validation would
    turn a saved settings form with an untouched, masked cookie box into a
    logout.
    """
    contract_env.cfg.bilibili.cookie = _FULL_BILI_COOKIE
    probes = _stub_live_probe(contract_env)

    with _write_client(contract_env) as client:
        masked = client.put("/api/config", json={"bilibili": {"cookie": "SESS****2345"}})
        blank = client.put("/api/config", json={"bilibili": {"cookie": ""}})

    assert masked.status_code == 202, masked.text
    assert blank.status_code == 202, blank.text
    assert contract_env.cfg.bilibili.cookie == _FULL_BILI_COOKIE
    assert probes == [], "a field that was not edited must not be probed"


def test_credential_write_states_what_it_could_not_verify(contract_env: _Env) -> None:
    """Every accepted write says how far it actually checked (invariants I3/I5).

    The platforms differ irreducibly: B站 and 抖音 can be probed, X can only be
    inferred from real traffic, Reddit is a local file read, and 小红书 / 知乎 /
    Linux.do hand the backend a bare boolean it can never audit. A single
    "saved ✓" for all of them would be the same lie the one-word ``state`` field
    told.
    """
    _stub_live_probe(contract_env)
    _seed_browser_login_state(
        contract_env.db, prefix="xhs", logged_in=True, when_iso=_iso_hours_ago(0.1)
    )

    accepted: dict[str, dict[str, object]] = {}
    with _write_client(contract_env) as client:
        for slug, body in (
            ("bilibili", {"value": _FULL_BILI_COOKIE}),
            ("douyin", {"value": "sessionid=dy-sess; ttwid=tw"}),
            ("twitter", {"value": "auth_token=at; ct0=csrf"}),
            ("reddit", {"value": "reddit_session=rs; loid=l"}),
            ("xiaohongshu", {"kind": "login_state", "value": True}),
            ("zhihu", {"kind": "login_state", "value": True}),
            ("linuxdo", {"kind": "login_state", "value": True}),
            ("v2ex", {"kind": "login_state", "value": True}),
        ):
            response = client.post(f"/api/sources/{slug}/credential", json=body)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["accepted"] is True, payload
            accepted[slug] = payload

        # YouTube needs no login, so it accepts no credential at all rather
        # than pretending to store one.
        refused = client.post("/api/sources/youtube/credential", json={"value": "anything"})
        assert refused.status_code == 200
        assert refused.json()["error_code"] == "credential_not_writable"

        assert client.post("/api/sources/nope/credential", json={"value": "x"}).status_code == 404

    # Probed platforms say so...
    assert accepted["bilibili"]["checked"] == "live_probe"
    assert accepted["douyin"]["checked"] == "live_probe"
    # ...and the ones that cannot be probed say *that*, with a reason.
    for slug in ("twitter", "reddit", "xiaohongshu", "zhihu", "linuxdo", "v2ex"):
        assert accepted[slug]["checked"] != "live_probe", slug
        assert accepted[slug]["unverified_reason"], f"{slug} accepted a write without saying why"

    # The receipt is the freshly recomputed contract, not a hopeful echo.
    for slug, payload in accepted.items():
        contract = SourceAuthContract.model_validate(payload["auth"])
        assert check_legacy_consistency(slug, contract) == []


def test_douyin_write_refuses_a_cookie_the_platform_rejects(contract_env: _Env) -> None:
    """抖音's write gate uses the D11 probe, so a logged-out jar cannot land.

    Regression lock for the claim this whole platform's status hung on: the
    endpoint stored anything it was handed because a docstring said no clean
    login probe existed. It does (spec D11), and it now gates the write.
    """
    calls = _install_douyin_probe(contract_env, {"status_code": 8, "status_msg": "用户未登录"})

    with _write_client(contract_env) as client:
        response = client.post(
            "/api/sources/douyin/credential",
            json={"value": "sessionid=dead-session; ttwid=tw"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is False
    assert body["error_code"] == "cookie_invalid"
    assert calls == ["/aweme/v1/web/user/profile/self/"]
    assert not (contract_env.cfg.data_path / "douyin_cookie.json").exists()


def test_transport_failure_refuses_rather_than_storing_an_unchecked_cookie(
    contract_env: _Env,
) -> None:
    """A probe that could not reach the platform is not permission to store.

    ``validation_network`` is the code the browser extension already backs off
    on, so this stays a retry rather than a dead end — and a credential we were
    unable to check never becomes one we silently accepted.
    """
    _install_douyin_probe(contract_env, DouyinDirectError("proxy died"))

    with _write_client(contract_env) as client:
        response = client.post(
            "/api/sources/douyin/credential",
            json={"value": "sessionid=maybe-fine; ttwid=tw"},
        )

    body = response.json()
    assert body["accepted"] is False
    assert body["error_code"] == "validation_network"
    assert not (contract_env.cfg.data_path / "douyin_cookie.json").exists()
    # Crucially not a logged-out verdict: nothing was learned about the cookie.
    assert SourceAuthContract.model_validate(body["auth"]).verification != "failed"


class _Dynamic:
    """A response field whose value cannot be a literal (timestamp, tmp path).

    Compares equal to any non-empty string, so the surrounding assertion can
    still pin every *other* field to an exact value instead of degrading the
    whole comparison to a key-set check.
    """

    def __init__(self, description: str) -> None:
        self.description = description

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and bool(other.strip())

    def __hash__(self) -> int:
        return hash(self.description)

    def __repr__(self) -> str:
        return f"<any non-empty {self.description}>"


#: Response of each superseded endpoint, frozen **value by value**. Installed
#: browser extensions parse these; a dropped key is a silent breakage that
#: surfaces as "the plugin stopped syncing my login" with no error anywhere —
#: and so is a key that survives with a hollowed-out value, which is why
#: comparing key sets alone was not enough. The bodies below are all refusals,
#: the branch where an ``error_code`` and its message have to survive intact.
_LEGACY_ENDPOINT_CONTRACTS: tuple[tuple[str, dict[str, object], dict[str, object]], ...] = (
    (
        "/api/bilibili/cookie",
        {"cookie": "SESSDATA=only-one-field"},
        {
            "ok": False,
            "authenticated": False,
            "username": "",
            "user_id": 0,
            "message": (
                "B站 Cookie 不完整（缺少 SESSDATA / bili_jct / DedeUserID），未保存。"
                "请在已登录的浏览器重新复制完整 Cookie。"
            ),
            "error_code": "cookie_invalid",
        },
    ),
    (
        "/api/sources/dy/cookie",
        {"cookie": "ttwid=guest-tw"},
        {
            "ok": False,
            "has_cookie": False,
            "cookie_names": ["ttwid"],
            "message": (
                "抖音 Cookie 缺少登录态字段（sessionid / sessionid_ss / sid_tt），未保存。"
                "请在已登录的浏览器重新复制完整 Cookie。"
            ),
            "error_code": "cookie_invalid",
        },
    ),
    (
        "/api/sources/x/cookie",
        {"cookie": "auth_token=at-only"},
        {
            "ok": False,
            "has_cookie": False,
            "cookie_names": ["auth_token"],
            "message": (
                "X Cookie 缺少 auth_token / ct0，未保存 —— twitter-cli 没有这两项会直接 401。"
            ),
            "error_code": "missing_x_cookies",
        },
    ),
    (
        "/api/sources/reddit/cookie",
        {"cookie": "loid=abc"},
        {
            "ok": False,
            "has_cookie": False,
            "cookie_names": ["loid"],
            "credential_file": _Dynamic("rdt credential path"),
            "message": (
                "Reddit Cookie 未保存：缺少 reddit_session，"
                "请从已登录 reddit.com 的浏览器复制完整 Cookie。"
            ),
            "error_code": "missing_reddit_session",
        },
    ),
    (
        "/api/sources/xhs/tokens",
        {"pairs": [{"note_id": "n1", "xsec_token": "t1"}]},
        {"ok": True, "upgraded": 0},
    ),
    (
        "/api/sources/xhs/login-state",
        {"logged_in": True},
        {"ok": True, "logged_in": True, "updated_at": _Dynamic("ISO timestamp")},
    ),
    (
        "/api/sources/zhihu/login-state",
        {"logged_in": True},
        {"ok": True, "logged_in": True, "updated_at": _Dynamic("ISO timestamp")},
    ),
)


@pytest.mark.parametrize(
    ("route", "body", "expected"),
    _LEGACY_ENDPOINT_CONTRACTS,
    ids=[route for route, _b, _e in _LEGACY_ENDPOINT_CONTRACTS],
)
def test_legacy_credential_endpoints_keep_their_response_shape(
    contract_env: _Env, route: str, body: dict[str, object], expected: dict[str, object]
) -> None:
    """Forwarding into the unified path must not change what these return.

    Values, not just keys: an extension reading ``error_code`` to decide
    whether to back off cannot tell a renamed code from a working one by the
    presence of the key.
    """
    with _write_client(contract_env) as client:
        response = client.post(route, json=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == set(expected)
    for key, want in expected.items():
        assert want == payload[key], f"{route} → {key}"


def test_legacy_credential_endpoints_keep_their_success_shape(contract_env: _Env) -> None:
    """The accepted branch too — the refusal branch alone would miss dropped keys."""
    _stub_live_probe(contract_env)

    with _write_client(contract_env) as client:
        bili = client.post(
            "/api/bilibili/cookie", json={"cookie": _FULL_BILI_COOKIE, "source": "extension"}
        )
        douyin = client.post("/api/sources/dy/cookie", json={"cookie": "sessionid=dy; ttwid=tw"})
        twitter = client.post("/api/sources/x/cookie", json={"cookie": "auth_token=at; ct0=c"})

    assert set(bili.json()) == {
        "ok",
        "authenticated",
        "username",
        "user_id",
        "message",
        "error_code",
    }
    assert bili.json()["ok"] is True
    assert bili.json()["authenticated"] is True
    assert douyin.json()["ok"] is True
    assert douyin.json()["cookie_names"] == ["sessionid", "ttwid"]
    assert twitter.json()["ok"] is True
    assert twitter.json()["has_cookie"] is True


def test_superseded_credential_endpoints_are_marked_deprecated(contract_env: _Env) -> None:
    """The deprecation flag is a contract, not documentation polish.

    ``scripts/source_contract_metrics.py`` counts *undeprecated* credential
    write shapes; leaving the marker off would keep the metric reporting four
    ways to store a credential while the code has one, which is how a green
    gate stops meaning anything.
    """
    app = create_app(memory_manager=object(), database=contract_env.db, soul_engine=object())
    paths = app.openapi()["paths"]

    for route, _body, _keys in _LEGACY_ENDPOINT_CONTRACTS:
        assert paths[route]["post"].get("deprecated") is True, route

    # ...and its replacement is emphatically not deprecated.
    assert paths["/api/sources/{slug}/credential"]["post"].get("deprecated") is not True


# ── Phase 4: credential form descriptors ──────────────────────────────
# The form descriptor exists so three frontends can render every platform
# The form descriptor exists so three frontends can render every registered platform
# with no per-platform branches (invariant I4). These tests guard the two ways
# that promise can rot: a platform shipping without a descriptor (the branch
# comes straight back), and a descriptor that advertises more than the write
# path can actually do.


def _credentials_payload(env: _Env) -> dict[str, Any]:
    app = create_app(memory_manager=object(), database=env.db, soul_engine=object())
    with TestClient(app) as client:
        response = client.get("/api/sources/credentials")
    assert response.status_code == 200
    return dict(response.json())


@pytest.mark.parametrize("slug", sorted(CREDENTIAL_SPECS))
def test_every_platform_ships_a_credential_form(contract_env: _Env, slug: str) -> None:
    """Every platform carries a ``form``, so no surface has to invent one."""
    """Every registered platform carries a form, so no surface has to invent one."""
    payload = _credentials_payload(contract_env)
    form = payload[slug]["form"]

    assert form["kind"] in get_args(FormKind), (slug, form["kind"])
    assert form["label"], slug
    # Every platform must say *something* about how to get connected, including
    # the ones that take no credential at all -- "nothing to do here" is the
    # answer a user needs most on those.
    assert form["help_text"], slug


@pytest.mark.parametrize("slug", sorted(CREDENTIAL_SPECS))
def test_form_kind_matches_actual_write_capability(contract_env: _Env, slug: str) -> None:
    """A writable form kind requires a platform that actually accepts a paste.

    The 小红书/知乎 case is the one that matters: the backend stores no cookie
    for them at all, so rendering a text box would collect input that goes
    nowhere. Encoding that as ``extension_only`` is what lets the frontends
    honour it without naming the platforms.
    """
    spec = CREDENTIAL_SPECS[slug]
    form = _credentials_payload(contract_env)[slug]["form"]
    writable = form["kind"] in WRITABLE_FORM_KINDS

    if not spec.kinds:
        assert form["kind"] == "none", slug
        assert not writable, slug
    if writable:
        # A pasteable form must correspond to a cookie the backend persists.
        assert "cookie" in spec.kinds, slug


@pytest.mark.parametrize("slug", sorted(CREDENTIAL_SPECS))
def test_form_required_keys_never_overstate_the_write_gate(contract_env: _Env, slug: str) -> None:
    """The descriptor's key list is the gate's key list, mode included.

    抖音 accepts *any one* of three session cookies. Publishing those three as
    jointly required would tell users the validator demands something it does
    not -- the same class of drift as D6, one layer up.
    """
    spec = CREDENTIAL_SPECS[slug]
    form = _credentials_payload(contract_env)[slug]["form"]

    if spec.required_keys:
        assert form["required_keys"] == list(spec.required_keys), slug
        assert form["required_keys_mode"] == "all", slug
    elif spec.any_of_keys:
        assert form["required_keys"] == list(spec.any_of_keys), slug
        assert form["required_keys_mode"] == "any", slug
    else:
        assert form["required_keys"] == [], slug


@pytest.mark.parametrize("slug", sorted(CREDENTIAL_SPECS))
def test_form_actions_are_backed_by_a_real_capability(contract_env: _Env, slug: str) -> None:
    """Every advertised action has something behind it.

    ``clear`` is the counter-example this test exists for: the spec's field
    table listed it, but no endpoint can erase a stored credential (an empty
    ``PUT /api/config`` field means "not edited", which is the opposite), so
    no descriptor may offer it until one can.
    """
    form = _credentials_payload(contract_env)[slug]["form"]
    actions = {entry["action"] for entry in form["actions"]}

    assert "clear" not in actions, slug
    assert "copy" not in actions, slug
    # POST /api/sources/{slug}/verify serves every platform, YouTube included.
    # POST /api/sources/{slug}/verify serves every registered source, YouTube included.
    assert "verify" in actions, slug
    for entry in form["actions"]:
        if entry["action"] == "open_login_window":
            assert entry["url"].startswith("https://"), slug
        assert entry["label"], slug


def test_extension_only_platforms_expose_no_writable_input(contract_env: _Env) -> None:
    """The whole point of ``extension_only``: no pasteable box, anywhere.

    Asserted on the response rather than on the spec table so that a surface
    reading the API -- which is all three of them now -- cannot be handed a
    writable kind for a platform whose credential the backend never stores.
    """
    payload = _credentials_payload(contract_env)
    extension_only = {
        slug for slug, item in payload.items() if item["form"]["kind"] == "extension_only"
    }

    assert extension_only == {
        "xiaohongshu",
        "zhihu",
        "linuxdo",
        "instagram",
    }, extension_only
    for slug in extension_only:
        form = payload[slug]["form"]
        assert form["placeholder"] == "", slug
        assert form["required_keys"] == [], slug
        # Nothing to type, but there is still somewhere to go and something to
        # check -- an unactionable row is what made these sources feel broken.
        actions = {entry["action"] for entry in form["actions"]}
        assert {"verify", "open_login_window"} <= actions, slug


def test_credential_summary_separates_a_token_from_a_login(contract_env: _Env) -> None:
    """小红书's caveat travels in the payload, not in one frontend's `if`.

    The desktop page used to special-case this platform to say a stored
    xsec_token is not proof of login; the side panel and the setup wizard never
    got that sentence. Shipping it from the backend is what removes the branch.
    """
    contract_env.db.conn.execute(
        "INSERT INTO discovery_candidates"
        " (candidate_key, source_platform, content_url, last_seen_at)"
        " VALUES ('xhs:summary-case',"
        " 'xiaohongshu', 'https://www.xiaohongshu.com/x?xsec_token=abc123', ?)",
        (datetime.now(UTC).isoformat(),),
    )
    contract_env.db.conn.commit()

    payload = _credentials_payload(contract_env)
    xhs = payload["xiaohongshu"]
    assert xhs["available"] is True
    assert "不代表账号登录" in xhs["summary"]

    # A platform whose stored value *is* a login credential says so plainly.
    assert "不代表账号登录" not in payload["bilibili"]["summary"]


# ── external review round: ten defects, ten reproductions ─────────────
# Every test below was written red against the code as reviewed, and each
# reproduces the *trigger* rather than restating the fix. They are grouped by
# the promise they defend, because that is what a future reader needs: which
# sentence in the spec stops being true if this goes red.

#: 抖音's own answer shapes on ``/aweme/v1/web/user/profile/self/`` (spec D11).
_DY_LOGGED_IN = {"status_code": 0, "user": {"uid": "9876543210", "nickname": "小白"}}
_DY_LOGGED_OUT = {"status_code": 8, "status_msg": "用户未登录"}


def _stub_probe_by_cookie(env: _Env, verdicts: dict[str, bool]) -> list[tuple[str, str]]:
    """Answer the shared live gate per *cookie value*; return what it was asked.

    Keyed on the cookie rather than on the platform because the defect this
    exists for is exactly a cache that could not tell two cookies apart. A
    platform-keyed stub would have happily reproduced the bug as if it were
    correct behaviour.
    """
    from openbiliclaw.api.source_auth import verify as verify_module

    calls: list[tuple[str, str]] = []

    async def _probe(
        slug: str,
        *,
        cfg: object,
        cookie: str | None = None,
        probes: object = None,
        record: bool = True,
    ) -> object:
        text = str(cookie or "")
        calls.append((slug, text))
        authenticated = verdicts.get(text, False)
        return verify_module.LiveProbeOutcome(
            slug=slug,
            has_credential=True,
            authenticated=authenticated,
            network_error=False,
            message="stub: 已登录" if authenticated else "stub: 未登录",
            username="白" if authenticated else "",
            user_id=42 if authenticated else 0,
        )

    env.monkeypatch.setattr(verify_module, "run_live_probe", _probe)
    return calls


def _install_douyin_probe_by_cookie(
    env: _Env, payloads: dict[str, object]
) -> list[tuple[str, str]]:
    """Fake 抖音's transport, answering per cookie value.

    Patched at the client rather than at ``run_live_probe`` so the real probe,
    the real status-code mapping *and* the real verdict recording all run —
    which matters for the debounce and cache tests, whose whole subject is what
    gets recorded.
    """
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, *, cookie: str, http_client: object = None) -> None:
            self._cookie = cookie

        async def request_json(self, path: str, _params: dict[str, object]) -> object:
            calls.append((path, self._cookie))
            payload = payloads.get(self._cookie, _DY_LOGGED_OUT)
            if isinstance(payload, Exception):
                raise payload
            return payload

        async def aclose(self) -> None:
            return None

    env.monkeypatch.setattr(
        "openbiliclaw.sources.douyin_login_probe.DouyinDirectClient", _FakeClient
    )
    return calls


def _cookie_pairs(value: str) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in value.split("; ") if "=" in pair)


def _seed_external_credential(env: _Env, slug: str, value: str) -> None:
    """Put *value* in the store *slug* actually reads, by its own writer."""
    if slug == "douyin":
        DouyinCookieManager(env.cfg.data_path).set_cookie(value, source="test")
    elif slug == "twitter":
        XCookieManager(env.cfg.data_path).set_cookie(value, source="test")
    elif slug == "reddit":
        _write_rdt_credential(env.rdt_credential_path, cookies=_cookie_pairs(value), age_days=0)
    else:  # pragma: no cover - defensive
        raise AssertionError(f"no external store for {slug}")


def _stored_credential(env: _Env, slug: str) -> str:
    """Read a credential back *from disk*, through the store's own reader.

    Asserting on a response body would pass for an endpoint that says "not
    saved" and saves anyway — the exact failure these tests are about.
    """
    if slug == "reddit":
        if not env.rdt_credential_path.exists():
            return ""
        raw = json.loads(env.rdt_credential_path.read_text(encoding="utf-8"))
        cookies = raw.get("cookies") if isinstance(raw, dict) else {}
        return "; ".join(f"{k}={v}" for k, v in sorted(dict(cookies or {}).items()))

    from openbiliclaw.api.source_auth.write import current_credential

    return current_credential(slug, cfg=env.cfg)


# ── 1. the live-probe cache must key on the credential, not the platform ──


@pytest.mark.parametrize(
    ("slug", "good", "dead"),
    [
        (
            "bilibili",
            _FULL_BILI_COOKIE,
            "SESSDATA=revoked; bili_jct=revoked; DedeUserID=99999",
        ),
        (
            "douyin",
            "sessionid=live-session; ttwid=tw",
            "sessionid=revoked-session; ttwid=tw",
        ),
    ],
)
def test_a_fresh_verdict_never_waves_through_a_different_credential(
    contract_env: _Env, slug: str, good: str, dead: str
) -> None:
    """A cached "logged in" is about one credential, not about the platform.

    Trigger: save a working cookie, then within the 60s success window save a
    different one that is structurally complete but dead. Keyed on the slug
    alone, the cache answered "already confirmed" for a credential it had never
    seen — so a dead cookie landed on disk, and the write path even refreshed
    the success timestamp on the way past. That is the one promise this module
    exists to keep ("无效凭据绝不落盘") failing in the single case where the
    user cannot possibly notice.

    Reusing a *positive* verdict is still right (抖音's ``msToken`` rotates
    constantly and re-probing on each rotation is self-inflicted risk-control
    traffic) — see the next test. It just has to be the same credential.
    """
    calls = _stub_probe_by_cookie(contract_env, {good: True, dead: False})

    with _write_client(contract_env) as client:
        first = client.post(f"/api/sources/{slug}/credential", json={"value": good})
        second = client.post(f"/api/sources/{slug}/credential", json={"value": dead})

    assert first.json()["accepted"] is True, first.text

    # The gate must have gone and asked about the *second* cookie.
    assert (slug, dead) in calls, "the dead cookie was accepted without being probed"

    body = second.json()
    assert body["accepted"] is False
    assert body["error_code"] == "cookie_invalid"
    assert _stored_credential(contract_env, slug) == good


def test_rotating_a_non_identity_cookie_still_reuses_the_verdict(contract_env: _Env) -> None:
    """...and the optimisation the cache exists for survives the fix.

    抖音's extension re-posts the whole jar every time ``msToken`` rotates. The
    fingerprint covers only the login-bearing names, so a rotation is not a new
    credential and must not cost a probe. Guarding this is the point: the
    obvious fix — hash the whole cookie string — would pass the test above and
    quietly turn every rotation into a fresh request at 抖音.
    """
    stable = "sessionid=s1; ttwid=tw; msToken=aaa"
    rotated = "sessionid=s1; ttwid=tw; msToken=bbbbbb"
    calls = _stub_probe_by_cookie(contract_env, {stable: True, rotated: True})

    with _write_client(contract_env) as client:
        assert client.post("/api/sources/douyin/credential", json={"value": stable}).json()[
            "accepted"
        ]
        assert client.post("/api/sources/douyin/credential", json={"value": rotated}).json()[
            "accepted"
        ]

    assert len(calls) == 1, f"a msToken rotation re-probed 抖音: {calls}"


# ── 2. X may not claim a verdict it has no verification for ──────────


def test_x_is_unverified_until_real_traffic_has_actually_succeeded(contract_env: _Env) -> None:
    """A stored X cookie stays unverified until a real check succeeds.

    Trigger: a brand-new database and a first-ever X cookie write. The health
    row is created with ``state='ok'`` as its *default*, so the status endpoint
    reported ``verification="verified"`` + ``verify_method="live_probe"``
    for a credential that had never been used for anything — including a cookie
    that expired months ago. Inventing a verification result is precisely what
    invariant I3 forbids.

    The legacy fields are untouched by the fix: ``state`` stays ``ok`` because
    that is what Wave A froze, and the honesty lands in the orthogonal field.
    """
    contract_env.cfg.sources.twitter.enabled = True
    XCookieManager(contract_env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")

    item = _status_payload(contract_env)["twitter"]
    contract = SourceAuthContract.model_validate(item["auth"])

    assert item["state"] == "ok"  # legacy verdict deliberately unchanged
    assert contract.credential == "present"
    assert contract.verify_method == "live_probe"
    assert contract.verification == "unverified", (
        "X claimed a verdict without a single real request behind it"
    )
    assert contract.verified_at == ""
    assert check_legacy_consistency("twitter", contract) == []

    # One genuine success, attributed to this cookie.
    XSourceHealthStore(
        contract_env.db, credential_fingerprint=_x_fingerprint("auth_token=a; ct0=b")
    ).record_success()
    after = SourceAuthContract.model_validate(_status_payload(contract_env)["twitter"]["auth"])
    assert after.verification == "verified"
    assert after.verified_at, "a verified X verdict must say when the check happened"


def test_a_relogin_unblock_does_not_inherit_the_old_credential_verdict(
    contract_env: _Env,
) -> None:
    """Clearing a block on a *new* cookie is not evidence about that cookie.

    ``clear_relogin_block`` resets the state to ``ok`` when a fresh cookie is
    synced, so the parked producer retries. If the previous credential's
    success were left standing, the new cookie would inherit its verdict — the
    same fabrication as above, arriving by a different door.
    """
    contract_env.cfg.sources.twitter.enabled = True
    XCookieManager(contract_env.cfg.data_path).set_cookie("auth_token=a; ct0=b", source="test")
    store = XSourceHealthStore(
        contract_env.db, credential_fingerprint=_x_fingerprint("auth_token=a; ct0=b")
    )
    store.record_success()
    assert (
        SourceAuthContract.model_validate(
            _status_payload(contract_env)["twitter"]["auth"]
        ).verification
        == "verified"
    )

    store.record_error(XAuthError("401"))
    store.clear_relogin_block()

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)["twitter"]["auth"])
    assert contract.verification == "unverified"


# ── 3. PUT /api/config must not write credentials it may still reject ──


_EXTERNAL_CREDENTIAL_WRITES: dict[str, tuple[str, str]] = {
    # slug -> (already stored, the value the doomed PUT also carries)
    "douyin": ("sessionid=old-session; ttwid=tw", "sessionid=new-session; ttwid=tw"),
    "twitter": ("auth_token=old; ct0=old", "auth_token=new; ct0=new"),
    "reddit": ("reddit_session=old", "reddit_session=new"),
}


@pytest.mark.parametrize("slug", sorted(_EXTERNAL_CREDENTIAL_WRITES))
def test_a_rejected_config_save_leaves_external_credentials_untouched(
    contract_env: _Env, slug: str
) -> None:
    """A 400 that says "not written" must not have already written something.

    Trigger: one PUT carrying a valid credential *and* an invalid ``[network]``
    block. The credential branches ran near the top of the handler and wrote
    straight through to their stores; the network check 400s hundreds of lines
    later, after which the response tells the user nothing was saved. The
    credential store disagreed — and unlike ``config.toml``, which has a
    snapshot and a rollback, these stores have neither.

    The same ordering is what let two concurrent PUTs pair one request's config
    with another's credentials, since only the config half is under the save
    lock.
    """
    old, new = _EXTERNAL_CREDENTIAL_WRITES[slug]
    _seed_external_credential(contract_env, slug, old)
    _stub_probe_by_cookie(contract_env, {new: True})

    patch = _config_patch_for(slug, new)
    patch["network"] = {"mode": "custom", "proxy": ""}

    with _write_client(contract_env) as client:
        response = client.put("/api/config", json=patch)

    assert response.status_code == 400, response.text
    assert _stored_credential(contract_env, slug) == old, (
        f"{slug}: PUT reported a refusal after overwriting the credential store"
    )


@pytest.mark.parametrize("slug", sorted(_EXTERNAL_CREDENTIAL_WRITES))
def test_a_valid_config_save_still_writes_external_credentials(
    contract_env: _Env, slug: str
) -> None:
    """The deferral must not turn into a drop — the write still has to happen.

    Covers all three external stores, not just the one whose bug prompted the
    change: deferring a write is exactly the kind of edit that silently loses
    one branch, and a single-platform happy path would not notice.
    """
    _old, new = _EXTERNAL_CREDENTIAL_WRITES[slug]
    _stub_probe_by_cookie(contract_env, {new: True})

    with _write_client(contract_env) as client:
        response = client.put("/api/config", json=_config_patch_for(slug, new))

    assert response.status_code == 202, response.text
    assert response.json()["apply_state"] == "queued"
    assert _stored_credential(contract_env, slug) == new


# ── 4. a credential change invalidates that platform's debounce ────────


def test_saving_a_credential_invalidates_the_verify_debounce(contract_env: _Env) -> None:
    """The debounce keys on the platform, so a fix must reset it.

    Trigger: verify a dead cookie (10s debounce arms with ``failed``), paste a
    working one, click 测试连接 again inside the window. The stored failure was
    replayed verbatim, so the repair looked like it had not taken — and the
    user's most likely next move is to delete the cookie that actually works.
    """
    _seed_external_credential(contract_env, "douyin", "sessionid=revoked; ttwid=tw")
    _install_douyin_probe_by_cookie(
        contract_env,
        {"sessionid=revoked; ttwid=tw": _DY_LOGGED_OUT, "sessionid=fixed; ttwid=tw": _DY_LOGGED_IN},
    )

    with _write_client(contract_env) as client:
        before = client.post("/api/sources/douyin/verify").json()
        assert before["outcome"] == "failed", before

        saved = client.post(
            "/api/sources/douyin/credential", json={"value": "sessionid=fixed; ttwid=tw"}
        )
        assert saved.json()["accepted"] is True, saved.text

        after = client.post("/api/sources/douyin/verify").json()

    assert after["replayed"] is False, "a verdict about the old cookie was replayed"
    assert after["outcome"] == "verified"


# ── 5. both write paths leave the same verdict behind ─────────────────


@pytest.mark.parametrize("slug", ["bilibili", "douyin"])
@pytest.mark.parametrize("surface", ["credential-endpoint", "config-put"])
def test_both_write_paths_record_the_verdict_they_paid_for(
    contract_env: _Env, slug: str, surface: str
) -> None:
    """One cookie, two save buttons, one resulting status (invariant I5).

    Trigger: save a valid cookie through the settings page (``PUT
    /api/config``). It genuinely went out and probed the platform, refused to
    save anything that failed — and then dropped the verdict on the floor, so
    the status chip still read ``unverified``. The identical cookie saved from
    the extension read ``verified``. Two write paths, equal validation
    *strength* but unequal *outcome*, which is the half of I5 that a
    verdict-free comparison of error codes cannot see.
    """
    cookie = _FULL_BILI_COOKIE if slug == "bilibili" else "sessionid=live; ttwid=tw"
    _stub_probe_by_cookie(contract_env, {cookie: True})

    with _write_client(contract_env) as client:
        if surface == "credential-endpoint":
            response = client.post(f"/api/sources/{slug}/credential", json={"value": cookie})
        else:
            response = client.put("/api/config", json=_config_patch_for(slug, cookie))
        expected_status = 202 if surface == "config-put" else 200
        assert response.status_code == expected_status, response.text

    contract = SourceAuthContract.model_validate(_status_payload(contract_env)[slug]["auth"])
    assert contract.verification == "verified", f"{surface} discarded the probe verdict it paid for"
    assert contract.verified_at


# ── 6. the deprecated B站 route keeps every field it ever returned ─────


def test_legacy_bilibili_endpoint_keeps_identity_fields_on_a_cached_verdict(
    contract_env: _Env,
) -> None:
    """A cache hit must not hollow out ``username`` / ``user_id``.

    Trigger: guided init leaves a fresh success verdict, then the extension
    re-posts the same cookie (which it does on every startup) inside the 60s
    window. The cached branch returned a bare "authenticated", so the response
    degraded to ``username="" , user_id=0`` — no longer field-for-field what
    this route returned before, on a route installed extensions parse.
    """
    calls = _stub_probe_by_cookie(contract_env, {_FULL_BILI_COOKIE: True})

    with _write_client(contract_env) as client:
        first = client.post(
            "/api/bilibili/cookie", json={"cookie": _FULL_BILI_COOKIE, "source": "extension"}
        ).json()
        second = client.post(
            "/api/bilibili/cookie", json={"cookie": _FULL_BILI_COOKIE, "source": "extension"}
        ).json()

    assert len(calls) == 1, "the second post should have been answered from the cache"
    assert first["username"] == "白"
    assert first["user_id"] == 42
    assert (second["ok"], second["authenticated"]) == (True, True)
    assert (second["username"], second["user_id"]) == ("白", 42)


# ── 7. cancellation must release the in-flight marker ─────────────────


async def test_a_cancelled_verification_releases_the_inflight_marker(
    contract_env: _Env,
) -> None:
    """``CancelledError`` is a ``BaseException``, so ``except Exception`` misses it.

    Trigger: the settings page's fetch is aborted, or an upper layer times the
    request out. The task is cancelled, the in-flight marker is never released,
    and every click for the next 60 seconds answers "该来源的验证正在进行中" —
    for a verification that is no longer running.
    """
    from openbiliclaw.api.source_auth import verify as verify_module

    async def _cancelled(*_args: object, **_kwargs: object) -> object:
        raise asyncio.CancelledError

    contract_env.monkeypatch.setattr(verify_module, "run_live_probe", _cancelled)
    VERIFY_DEBOUNCE.clear()

    with pytest.raises(asyncio.CancelledError):
        await verify_source("bilibili", cfg=contract_env.cfg, database=contract_env.db)

    assert VERIFY_DEBOUNCE.busy("bilibili") is False, (
        "a cancelled verification wedged the platform's verify button"
    )


# ── 8. the legacy consistency tables must describe reality ────────────

#: The legacy states the frozen cases prove the backend actually emits. Derived
#: from the case table rather than from a hand-kept list, because a hand-kept
#: list is what drifted: the reviewed tables constrained ``expired``, which no
#: provider can produce, while saying nothing about ``missing_cookie`` /
#: ``expired_cookie``, which X emits routinely.
_EMITTED_LEGACY_STATES = frozenset(case.state for case in _CASES.values())


def test_legacy_consistency_tables_contain_no_unreachable_state() -> None:
    """A key no provider can emit is a check that never runs.

    Worse than useless: it reads as coverage. ``expired`` sat in the credential
    table looking like the expired-cookie case was handled, while the state X
    actually sends for that condition (``expired_cookie``) was unconstrained.
    """
    from openbiliclaw.api.source_auth.legacy import (
        _REQUIRED_CREDENTIAL,
        _REQUIRED_VERIFICATION,
    )

    dead = (set(_REQUIRED_CREDENTIAL) | set(_REQUIRED_VERIFICATION)) - _EMITTED_LEGACY_STATES
    assert dead == set(), f"consistency tables constrain states nothing emits: {sorted(dead)}"


def test_every_emitted_legacy_state_is_an_explicit_decision() -> None:
    """Each reachable state must appear in the credential table.

    Including the ones that genuinely constrain nothing: those map to the full
    value set with a comment saying why. Absence and "anything goes" look
    identical at runtime but not to a reader, and the reviewed table's gaps
    were all absences nobody had decided on.
    """
    from openbiliclaw.api.source_auth.legacy import _REQUIRED_CREDENTIAL

    missing = _EMITTED_LEGACY_STATES - set(_REQUIRED_CREDENTIAL)
    assert missing == set(), f"legacy states with no recorded decision: {sorted(missing)}"


def test_consistency_check_catches_a_verified_verdict_under_missing_cookie() -> None:
    """The contradiction the missing key let through.

    ``missing_cookie`` means X's last real request found no usable cookie. A
    provider pairing that with ``verification="verified"`` is claiming a
    success and a missing credential at once — a code bug, and exactly what
    this table is for.
    """
    contract = SourceAuthContract(
        auth_required=True,
        credential="present",
        credential_origin="data_file",
        verification="verified",
        verify_method="passive_health",
        legacy_state="missing_cookie",
        legacy_logged_in=False,
    )
    assert check_legacy_consistency("twitter", contract) != []


def test_consistency_check_accepts_a_throttled_source_with_no_cookie() -> None:
    """...and the false alarm the over-tight key produced.

    ``rate_limited`` has no timed path back to ``ok``, so the health row keeps
    saying it after the user deletes the cookie. Requiring a stored credential
    there reported a legitimate state as a contract violation — noise in the
    logs that trains readers to ignore the check.
    """
    contract = SourceAuthContract(
        auth_required=True,
        credential="none",
        credential_origin="none",
        verification="unverified",
        verify_method="passive_health",
        legacy_state="rate_limited",
        legacy_logged_in=False,
    )
    assert check_legacy_consistency("twitter", contract) == []


# ── 10. the unified write endpoint has no opt-out ─────────────────────


def test_the_unified_write_endpoint_cannot_be_asked_to_skip_the_live_gate(
    contract_env: _Env,
) -> None:
    """ "绝不落盘" cannot have a request flag that turns it off.

    ``validate_live`` shipped on the new endpoint with no caller anywhere —
    not the extension, not the three frontends, not the CLI. It was a documented
    way for anything reaching localhost to downgrade the promise the endpoint
    is named after, in exchange for nothing. Removed; the legacy
    ``validate_with_bilibili`` escape hatch stays only on the deprecated route
    whose installed clients already send it, and always send ``true``.
    """
    calls = _stub_probe_by_cookie(contract_env, {_FULL_BILI_COOKIE: True})

    with _write_client(contract_env) as client:
        body = client.post(
            "/api/sources/bilibili/credential",
            json={"value": _FULL_BILI_COOKIE, "validate_live": False},
        ).json()

    assert calls, "validate_live=false skipped the probe the endpoint promises"
    assert body["accepted"] is True
    assert body["checked"] == "live_probe"


def test_the_unified_write_schema_advertises_no_validation_opt_out() -> None:
    """And the field is gone from the schema, not merely ignored.

    A field that still appears in ``openapi.json`` is a field the next
    integration will use.
    """
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    schema = app.openapi()["components"]["schemas"]["SourceCredentialWriteIn"]
    assert "validate_live" not in schema["properties"]


# ── 11. the card's three sentences must agree ─────────────────────────


@pytest.mark.parametrize(
    ("payload", "verification", "forbidden"),
    [
        (_DY_LOGGED_IN, "verified", "需在实际任务中验证"),
        (_DY_LOGGED_OUT, "failed", "需在实际任务中验证"),
    ],
    ids=["verified", "failed"],
)
def test_douyin_detail_follows_the_verdict(
    contract_env: _Env, payload: object, verification: str, forbidden: str
) -> None:
    """抖音's note may not contradict its own chip and badge.

    Trigger: verify a 抖音 cookie successfully, then look at the card. The chip
    read 已验证, the badge read 联网验证 · 刚刚, and the body still read "Cookie
    已同步，需在实际任务中验证。" — a constant written back when the platform
    genuinely had no probe (pre-D11) and never revisited once one existed. Three
    sentences on one card, disagreeing.

    It survived every existing test because ``detail`` was only ever frozen
    against the no-verdict state, which is precisely the state where the old
    string is still correct. Caught on-device, which is the argument for the
    fourth surface being part of the contract rather than an afterthought.
    """
    _seed_external_credential(contract_env, "douyin", "sessionid=live; ttwid=tw")
    _install_douyin_probe_by_cookie(contract_env, {"sessionid=live; ttwid=tw": payload})

    with _write_client(contract_env) as client:
        assert client.post("/api/sources/douyin/verify").status_code == 200

    item = _status_payload(contract_env)["douyin"]
    contract = SourceAuthContract.model_validate(item["auth"])

    assert contract.verification == verification
    assert forbidden not in contract.detail, (
        f"抖音 reports {verification!r} while its detail still says {forbidden!r}"
    )
    # The legacy field and the contract field are one string, so the old
    # frontends cannot end up rendering a different sentence than the new ones.
    assert item["detail"] == contract.detail
    # ...and the pre-verdict wording is exactly what it always was, which is
    # what keeps the frozen cases byte-identical.
    assert _DOUYIN_DETAIL["unverified"] == "Cookie 已同步，需在实际任务中验证。"


# ── 12. one timestamp format, and it carries its offset ───────────────


@pytest.mark.parametrize("case_id", list(_CASES), ids=list(_CASES))
def test_verified_at_always_carries_an_explicit_offset(case_id: str, contract_env: _Env) -> None:
    """No verdict may be dated in a format that reads as local time.

    Trigger: X's health row, and the 知乎 / Reddit task-history fallbacks, are
    read straight out of SQLite, where ``CURRENT_TIMESTAMP`` writes UTC and
    omits the marker. ``Date.parse`` treats an unmarked string as local time, so
    a UTC+8 user saw a one-minute-old verdict rendered as eight hours old — the
    error running in the direction that makes the hardest evidence look the
    stalest.

    Swept across every frozen case rather than the three known sources: the
    point of normalising centrally is that a provider *cannot* emit a naive
    timestamp, and only a sweep can assert that.
    """
    case = _CASES[case_id]
    case.setup(contract_env)

    contract = SourceAuthContract.model_validate(
        _status_payload(contract_env)[case.platform]["auth"]
    )
    if not contract.verified_at:
        pytest.skip(f"{case_id} has no verdict to date")

    parsed = datetime.fromisoformat(contract.verified_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, (
        f"{case.platform} dated its verdict {contract.verified_at!r} with no offset — "
        "every consumer east of UTC will read it as hours old"
    )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # SQLite CURRENT_TIMESTAMP, the shape that caused this.
        ("2026-07-18 09:12:33", "2026-07-18T09:12:33+00:00"),
        ("2026-07-18T09:12:33", "2026-07-18T09:12:33+00:00"),
        ("2026-07-18T09:12:33Z", "2026-07-18T09:12:33+00:00"),
        # Already qualified — must round-trip untouched, including a non-UTC
        # offset, which we must not silently rewrite to UTC.
        ("2026-07-18T17:12:33+08:00", "2026-07-18T17:12:33+08:00"),
        ("", ""),
        # Unreadable: kept rather than blanked. "" would read as "never
        # verified" and would trip the freshness rule in the consistency check.
        ("not a timestamp", "not a timestamp"),
    ],
)
def test_contract_normalises_stored_timestamps(stored: str, expected: str) -> None:
    """The normaliser itself, at the boundary every provider passes through."""
    assert SourceAuthContract(verified_at=stored).verified_at == expected


def test_no_provider_emits_a_legacy_state_without_a_frozen_case() -> None:
    """Every ``legacy_state`` literal in the providers has a case behind it.

    ``_EMITTED_LEGACY_STATES`` is derived from the case table, so a provider
    branch that no case exercises would be invisible to it — and therefore
    unconstrained by the consistency tables *and* unprotected by the freeze.
    This closes that loop from the other side.

    A regex over source is a syntax proxy, and spec I7 is explicit that a gate
    may use one while a conclusion may not. Its blind spot is real and worth
    naming: Reddit's rdt branch writes ``legacy_state=state`` from a variable,
    so this scan cannot see ``ready`` / ``stale`` / ``login_required`` /
    ``error`` at all. Those are covered by frozen cases instead, which is why
    the case table stays the primary source and this is only a backstop.
    """
    import re
    from pathlib import Path

    source = Path("src/openbiliclaw/api/source_auth/providers.py").read_text(encoding="utf-8")
    literals = set(re.findall(r'legacy_state=["\']([a-z_]+)["\']', source))
    assert literals, "the scan found no literals at all — the pattern has rotted"

    uncovered = literals - _EMITTED_LEGACY_STATES
    assert uncovered == set(), (
        f"providers emit legacy states no frozen case covers: {sorted(uncovered)} — "
        "add a case to _CASES so the freeze and the consistency tables can see them"
    )


# ── external review round 2: two fixes that only closed the example ────
# Both had the same shape: the first round verified "this *platform* succeeded"
# and "this *call* wants checking", where the contract needs "this *credential*
# succeeded" and "this *path* checks". Recorded here as the pair, because the
# resemblance is the finding.


def _x_fingerprint(cookie: str) -> str:
    from openbiliclaw.api.source_auth.write import credential_fingerprint

    return credential_fingerprint("twitter", cookie)


def _x_verification(env: _Env) -> str:
    return str(
        SourceAuthContract.model_validate(_status_payload(env)["twitter"]["auth"]).verification
    )


def test_x_does_not_inherit_a_previous_credentials_success(contract_env: _Env) -> None:
    """A success belongs to the credential that earned it, not to the platform.

    Trigger: a working cookie earns a real success, then the user swaps in a
    different one that has never made a request. The new credential inherited
    the old one's ``verified`` **and its timestamp, unchanged to the second** —
    a fresh cookie presenting evidence gathered by a different cookie.

    This is the round-one cache defect relocated: there the reuse was keyed on
    the platform slug, here the success marker was. ``clear_relogin_block()``
    does not save it — with the health row already ``ok`` there is no block to
    clear, so it returns False and leaves the marker standing. Nor would a hook
    on the write paths be enough, since a cookie can change by env var or by an
    edited data file without passing through any of them. Only binding the
    verdict to the credential's identity closes every route at once.
    """
    contract_env.cfg.sources.twitter.enabled = True
    manager = XCookieManager(contract_env.cfg.data_path)

    old = "auth_token=OLD-good; ct0=old"
    manager.set_cookie(old, source="test")
    XSourceHealthStore(contract_env.db, credential_fingerprint=_x_fingerprint(old)).record_success()

    assert _x_verification(contract_env) == "verified"
    earned_at = SourceAuthContract.model_validate(
        _status_payload(contract_env)["twitter"]["auth"]
    ).verified_at
    assert earned_at

    # The swap. Deliberately written straight to the store rather than through
    # an endpoint: a fix that only fires on the API write paths would pass a
    # test that only uses them.
    manager.set_cookie("auth_token=NEW-never-used; ct0=new", source="test")
    XSourceHealthStore(contract_env.db).clear_relogin_block()

    assert _x_verification(contract_env) == "unverified", (
        "a never-used cookie inherited the previous credential's verified verdict"
    )


def test_x_keeps_its_verdict_when_the_same_credential_is_rewritten(contract_env: _Env) -> None:
    """...and re-writing the identical cookie is not a new credential.

    The extension re-posts the same jar on every startup. If that dropped the
    verdict, X would flap to ``unverified`` on each browser launch — the
    over-correction that makes the honest answer useless.
    """
    contract_env.cfg.sources.twitter.enabled = True
    manager = XCookieManager(contract_env.cfg.data_path)
    cookie = "auth_token=steady; ct0=steady"

    manager.set_cookie(cookie, source="test")
    XSourceHealthStore(
        contract_env.db, credential_fingerprint=_x_fingerprint(cookie)
    ).record_success()
    assert _x_verification(contract_env) == "verified"

    manager.set_cookie(cookie, source="extension")
    assert _x_verification(contract_env) == "verified"


def test_deprecated_bilibili_route_cannot_disable_the_live_gate(contract_env: _Env) -> None:
    """The opt-out removed from the new endpoint must not survive on the old one.

    Trigger: ``POST /api/bilibili/cookie`` with ``validate_with_bilibili:false``
    and a structurally complete but dead cookie — 200, ``ok=true``, zero probe
    calls, cookie on disk. Round one deleted ``validate_live`` from the unified
    endpoint for exactly this reason and then left the identical switch running
    next door, on the argument that installed extensions always send ``true``.
    That is a compatibility fact, not a security one: requests do not only come
    from the extension.

    The field is still *accepted* — installed extensions send it and a 422
    would break their cookie sync — it simply no longer buys anything.
    """
    dead = "SESSDATA=structurally-complete-but-DEAD; bili_jct=x; DedeUserID=999"
    calls = _stub_probe_by_cookie(contract_env, {dead: False})

    with _write_client(contract_env) as client:
        response = client.post(
            "/api/bilibili/cookie",
            json={"cookie": dead, "source": "extension", "validate_with_bilibili": False},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls, "validate_with_bilibili=false skipped the live probe"
    assert body["ok"] is False
    assert body["error_code"] == "cookie_invalid"
    assert contract_env.cfg.bilibili.cookie == ""
    assert not (contract_env.cfg.data_path / "bilibili_cookie.json").exists()


def test_deprecated_bilibili_route_still_accepts_the_legacy_flag(contract_env: _Env) -> None:
    """Accepting the field and honouring it are different things.

    Installed extensions post ``validate_with_bilibili: true`` on every cookie
    sync. Rejecting the key outright would 422 them into a silent sync failure,
    which is the breakage the deprecated routes exist to avoid.
    """
    calls = _stub_probe_by_cookie(contract_env, {_FULL_BILI_COOKIE: True})

    with _write_client(contract_env) as client:
        for flag in (True, False):
            response = client.post(
                "/api/bilibili/cookie",
                json={
                    "cookie": _FULL_BILI_COOKIE,
                    "source": "extension",
                    "validate_with_bilibili": flag,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["ok"] is True

    # One probe, then a cache hit for the identical cookie — the flag changed
    # nothing either time.
    assert len(calls) == 1


def test_x_detail_follows_the_verdict_too(contract_env: _Env) -> None:
    """The 抖音 contradiction must not reappear one card over.

    Found by auditing the round-one fixes rather than by a report: making X
    honest about ``verification`` left ``_X_STATE_DETAIL['ok']`` — "X 来源正常，
    cookie 有效。" — sitting under a chip that now reads 待验证. Same defect as
    #11, newly created by #2's fix, in the same review round that was about
    exactly this.
    """
    contract_env.cfg.sources.twitter.enabled = True
    cookie = "auth_token=a; ct0=b"
    XCookieManager(contract_env.cfg.data_path).set_cookie(cookie, source="test")

    item = _status_payload(contract_env)["twitter"]
    contract = SourceAuthContract.model_validate(item["auth"])
    assert contract.verification == "unverified"
    assert "有效" not in contract.detail, "X claims a valid cookie it has never used"
    assert item["detail"] == contract.detail

    # A genuinely confirmed cookie keeps the original wording byte for byte.
    XSourceHealthStore(
        contract_env.db, credential_fingerprint=_x_fingerprint(cookie)
    ).record_success()
    after = _status_payload(contract_env)["twitter"]
    assert SourceAuthContract.model_validate(after["auth"]).verification == "verified"
    assert after["detail"] == "X 来源正常，cookie 有效。"


def test_a_failing_deferred_credential_write_names_what_landed(contract_env: _Env) -> None:
    """A partial credential write must say which platforms it got through.

    The deferred writes run after ``save_config`` and touch three independent
    stores; no transaction spans them, so an I/O failure on the second leaves
    the first applied and the third untouched. That is unavoidable — what is
    avoidable is reporting it as an opaque 500. The error names the platform
    that failed and the ones that already landed, because "which of my
    credentials actually saved?" is the only question worth answering here
    (pitfall #7: propagate the real cause).
    """
    _seed_external_credential(contract_env, "douyin", "sessionid=old; ttwid=tw")
    _stub_probe_by_cookie(contract_env, {"sessionid=new; ttwid=tw": True})

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk full")

    contract_env.monkeypatch.setattr(
        "openbiliclaw.sources.x_auth.XCookieManager.set_cookie", _explode
    )

    patch: dict[str, object] = {
        "sources": {
            "douyin": {"cookie": "sessionid=new; ttwid=tw"},
            "twitter": {"cookie": "auth_token=new; ct0=new"},
        }
    }

    with _write_client(contract_env) as client, pytest.raises(RuntimeError) as caught:
        client.put("/api/config", json=patch)

    message = str(caught.value)
    assert "twitter" in message, message
    assert "douyin" in message, "the error must say which credentials did land"
    assert "disk full" in message, "the real cause has to survive"
