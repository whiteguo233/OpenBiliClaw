"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from openbiliclaw.llm.base import safe_llm_failure_message
from openbiliclaw.llm.service import _background_admission_bypass
from openbiliclaw.published_time import format_published_time
from openbiliclaw.runtime.ollama_supervisor import (
    _is_default_ollama_endpoint,
    _ollama_is_running,
    _ollama_start_serve_background,
    effective_ollama_endpoint,
    is_loopback,
    ollama_required,
)
from openbiliclaw.soul.preference_analyzer import DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE

# ── Init stage ceilings ──────────────────────────────────────────────────
#
# Calibration provenance (2026-07-20, field report): a healthy SenseTime
# ``deepseek-v4-flash`` gateway (``openai_compatible``) needs ~140s for one
# 200-event preference chunk and up to ~300s for a worst-case chunk. The old
# ceilings were sized as *performance expectations* for a fast provider, so a
# slow-but-perfectly-healthy gateway was killed mid-run while the progress UI
# still showed batches landing ("已处理 280s", 2/6 批).
#
# EVERY constant below is now a **wedged-run backstop, not a performance
# expectation**: it exists only so a permanently stuck run eventually ends. An
# init that legitimately takes 30-60 minutes on a slow gateway must survive.
# Where a stage emits real per-unit progress we additionally guard it with an
# idle limit (see ``_INIT_PROGRESS_IDLE_*``), which is what actually catches a
# wedged run quickly; the absolute ceiling is then free to be generous.
_INIT_PROFILE_ANALYSIS_TIMEOUT_SECONDS = 360.0
# Stage 3 (``build_initial_profile``) is ONE long LLM call with no per-unit
# progress signal, so an idle limit is meaningless there — a single slow call
# is indistinguishable from a hung one. Absolute backstop only: 30 min is ~6x
# the ~300s a slow gateway needs for this single synthesis call.
_INIT_PROFILE_BUILD_TIMEOUT_SECONDS = 1800.0
# Stage 4 (discovery + scoring + copy) emits coarse per-plan-stage progress,
# so it gets the idle+absolute pair. 45 min absolute matches stage 2's ceiling:
# both are LLM fan-outs over the same gateway.
_INIT_DISCOVERY_TIMEOUT_SECONDS = 2700.0
# Stage 1 global budget across ALL selected sources. Eight sources each waiting
# up to 3-5 min for a browser extension to answer cannot fit in 10 min, so the
# old value silently starved whichever sources ran last. 30 min lets a full
# eight-source bootstrap finish while still bounding a wedged extension.
_INIT_COLLECTION_TIMEOUT_SECONDS = 1800.0
# Per-source waits. Bilibili history+favorites+following on a throttled account
# routinely walks many paginated calls; X likes/bookmarks likewise. Doubled
# from the fast-network calibration so slow/proxied networks are not clipped.
_INIT_BILIBILI_COLLECTION_TIMEOUT_SECONDS = 600.0
_INIT_X_COLLECTION_TIMEOUT_SECONDS = 480.0

# ── Progress-aware deadlines (idle + absolute) ───────────────────────────
#
# IDLE: max seconds with NO completed preference chunk. A progress marker only
# advances after the whole provider response has arrived, so this outer guard
# must exceed the configured 20-minute per-request timeout. Twenty-five minutes
# leaves five minutes for the analyzer's bounded 65s rate-limit cooldowns while
# remaining below the 45-minute absolute ceiling.
_INIT_PROGRESS_IDLE_SECONDS = 1500.0
# Stage 4's progress reports are coarser (a handful per plan stage, each
# covering a full discover+score+copy sweep), so it needs a wider idle window
# than stage 2's per-chunk cadence.
_INIT_DISCOVERY_PROGRESS_IDLE_SECONDS = 900.0
# ABSOLUTE: hard stop for a run that keeps dribbling progress forever. The
# reported case (6 chunks × ~140s, concurrency-throttled) lands in ~15 min;
# 45 min leaves ~3x headroom for a larger bootstrap on the same slow gateway
# while still bounding the lease. Wedged-run backstop, not an expectation.
_INIT_PROGRESS_ABSOLUTE_SECONDS = 2700.0

# Stage 2 fans bootstrap events into bounded LLM chunks. Real gateways can
# legitimately need about three minutes for one 200-event chunk, and the
# analyzer only starts as many chunks at once as the configured LLM service
# concurrency permits. Scale the default wall clock by concurrency waves so a
# healthy single-slot gateway is not killed halfway through a 1,100-event
# bootstrap while faster multi-slot gateways still retain a useful deadline.
# One extra wave covers the single reasoning-only / transient-limit recovery
# that a healthy run may need without turning progress into an unlimited lease.
# Explicit caller overrides (including tiny test budgets and <=0 "disabled")
# remain exact.
_INIT_PROFILE_ANALYSIS_SECONDS_PER_WAVE = 300.0
_INIT_PROFILE_ANALYSIS_RECOVERY_RESERVE_SECONDS = 300.0

_INIT_PROFILE_BUILD_TIMEOUT_MESSAGE = (
    "画像生成等待 AI 服务超过 30 分钟仍未返回结果，已自动停止，避免继续卡住。"
    "这一步是一次性的完整综合分析，没有分批进度可判断，因此只设了一个很宽松的兜底上限。"
    "常见原因是 Base URL、模型名或代理配置错误，网络无法访问模型服务，"
    "或模型服务响应过慢。请到模型设置测试 AI 服务，修正后再重试初始化。"
)


def _profile_analysis_concurrency(soul_engine: Any) -> int:
    """Return the effective preference-analysis fan-out for timeout sizing."""
    analyzer = getattr(soul_engine, "_preference_analyzer", None)
    service = getattr(analyzer, "registry", None)
    configured = getattr(service, "concurrency", 1)
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return 1


def _profile_analysis_timeout_seconds(
    *,
    event_count: int,
    requested: float | None,
    concurrency: int = 1,
) -> float | None:
    """Return stage-2's bounded wall clock for this bootstrap size."""
    if requested is not None:
        return requested if requested > 0 else None
    chunks = max(
        1,
        (max(0, event_count) + DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE - 1)
        // DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    )
    waves = (chunks + max(1, concurrency) - 1) // max(1, concurrency)
    return max(
        _INIT_PROFILE_ANALYSIS_TIMEOUT_SECONDS,
        waves * _INIT_PROFILE_ANALYSIS_SECONDS_PER_WAVE
        + _INIT_PROFILE_ANALYSIS_RECOVERY_RESERVE_SECONDS,
    )


def _profile_analysis_deadlines(
    *,
    event_count: int,
    requested: float | None,
    concurrency: int = 1,
) -> tuple[float | None, float | None]:
    """Return stage 2's ``(idle_seconds, absolute_seconds)`` deadline pair.

    An explicit caller override (API/test budget) stays an exact pure wall
    clock — callers that ask for N seconds get N seconds, and ``<=0`` still
    means "no limit". Only the default path becomes progress-aware.
    """
    if requested is not None:
        return None, (requested if requested > 0 else None)
    scaled = _profile_analysis_timeout_seconds(
        event_count=event_count,
        requested=None,
        concurrency=concurrency,
    )
    absolute = max(_INIT_PROGRESS_ABSOLUTE_SECONDS, scaled or 0.0)
    return _INIT_PROGRESS_IDLE_SECONDS, absolute


def _timeout_minutes(seconds: float) -> int:
    return max(1, (max(1, int(seconds)) + 59) // 60)


def _profile_analysis_idle_timeout_message(seconds: float) -> str:
    """Nothing came back at all — almost always a connectivity/config fault."""
    return (
        f"AI 服务长时间没有返回任何新结果（约 {_timeout_minutes(seconds)} 分钟无进展），"
        "已自动停止，避免继续卡住。常见原因是 Base URL、模型名或代理配置错误，"
        "或网络无法访问模型服务。请到模型设置测试 AI 服务，修正后再重试初始化。"
    )


def _profile_analysis_absolute_timeout_message(seconds: float, *, progress_note: str = "") -> str:
    """Results kept coming, just too slowly for a bootstrap this size."""
    progress_part = f"（{progress_note}）" if progress_note else ""
    minutes = _timeout_minutes(seconds)
    return (
        f"偏好分析总时长超过上限（约 {minutes} 分钟），已自动停止{progress_part}。"
        "AI 服务一直在返回结果，只是对这次初始化的数据量来说太慢了。"
        "建议到模型设置换一个更快的模型，或稍后重试初始化。"
    )


class _InitIdleTimeoutError(TimeoutError):
    """No progress signal within the idle limit."""


class _InitAbsoluteTimeoutError(TimeoutError):
    """Total runtime exceeded the absolute ceiling despite progress."""


class _InitProgressMarker:
    """Shared monotonic 'last progress' marker.

    The work's own per-unit progress callbacks call :meth:`touch`; the
    watchdog in :func:`_await_with_progress_deadline` reads :attr:`last`.
    Heartbeat ticks deliberately do NOT touch it — a tick fires on a timer
    regardless of whether the work advanced, so counting it as progress would
    turn the idle limit into no limit at all.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock: Callable[[], float] = clock or _loop_clock
        self.started = self._clock()
        self.last = self.started

    def now(self) -> float:
        return self._clock()

    def touch(self) -> None:
        self.last = self._clock()


def _loop_clock() -> float:
    return asyncio.get_running_loop().time()


async def _await_with_progress_deadline(
    awaitable: Awaitable[Any],
    *,
    marker: _InitProgressMarker,
    idle_seconds: float | None,
    absolute_seconds: float | None,
    poll_seconds: float = 1.0,
) -> Any:
    """Await ``awaitable`` under an idle limit AND an absolute ceiling.

    A fixed wall clock cannot tell "hung" from "slow but progressing", which
    is exactly how a healthy-but-slow gateway got killed mid-bootstrap. This
    replaces it with two limits: ``idle_seconds`` since the last progress
    signal, and ``absolute_seconds`` overall. Either limit (and cancellation)
    cancels the work task and awaits its cancellation so nothing leaks.
    """
    task: asyncio.Future[Any] = asyncio.ensure_future(awaitable)
    started = marker.now()
    marker.touch()
    try:
        while True:
            now = marker.now()
            # Sleep only until the nearest limit so a tiny injected budget is
            # honoured promptly instead of always costing a full poll interval.
            wait_for = poll_seconds
            if absolute_seconds is not None:
                wait_for = min(wait_for, started + absolute_seconds - now)
            if idle_seconds is not None:
                wait_for = min(wait_for, marker.last + idle_seconds - now)
            done_tasks, _ = await asyncio.wait({task}, timeout=max(0.0, wait_for))
            if task in done_tasks:
                return task.result()
            now = marker.now()
            if absolute_seconds is not None and now - started >= absolute_seconds:
                raise _InitAbsoluteTimeoutError(absolute_seconds)
            if idle_seconds is not None and now - marker.last >= idle_seconds:
                raise _InitIdleTimeoutError(idle_seconds)
    except BaseException:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        raise


_INIT_DISCOVERY_TIMEOUT_MESSAGE = (
    "画像已生成，但首轮内容池等待内容发现、个性化评分或推荐文案生成超过 45 分钟仍未完成，"
    "本次初始化已按“部分完成”结束，避免继续卡住。"
    "常见原因是所选内容源未登录或网络不可达，也可能是 AI 评估响应过慢。"
    "系统会在后台继续补池；你可以先进入应用，检查平台登录与网络/代理后再刷新。"
)
_INIT_DISCOVERY_IDLE_MESSAGE = (
    "画像已生成，但首轮内容池已经约 15 分钟没有任何新进展，"
    "本次初始化已按“部分完成”结束，避免继续卡住。"
    "常见原因是所选内容源未登录或网络不可达，也可能是 AI 服务无法访问。"
    "系统会在后台继续补池；你可以先进入应用，检查平台登录与网络/代理后再刷新。"
)
_INIT_DISCOVERY_PARTIAL_MESSAGE = (
    "画像已生成，但首轮内容发现、个性化评分或推荐文案生成失败，"
    "尚未产出可直接浏览的首轮内容，本次初始化已按“部分完成”结束。"
    "常见原因是所选内容源暂不可用，或 AI 评估 / 文案生成失败。"
    "系统会在后台继续补池；你可以先进入应用，检查平台登录与网络/代理后再刷新。"
)


def _force_utf8_stdout_on_windows() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows.

    Why: simplified-Chinese Windows defaults the console to GBK (cp936).
    Any emoji in our CLI output (e.g. ``⏱`` in the init banner, ``🦀``
    in the typer help text) raises UnicodeEncodeError as soon as the
    output stream tries to encode it. Users see the program crash with
    no useful message.

    Fix: force sys.stdout / sys.stderr into UTF-8 mode at import time,
    with ``errors='replace'`` as a final safety net so a stray
    untranslatable byte degrades to '?' instead of crashing the run.
    Idempotent + a no-op on POSIX (``reconfigure`` is a Python 3.7+
    method on TextIOWrapper that just rewires the codec).
    """
    if os.name != "nt":
        return
    # PYTHONUTF8=1 is the cleanest fix but only takes effect at process
    # start, not at module import — set it for any child processes we
    # spawn (subprocess calls inside the CLI inherit this).
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdout_on_windows()


app = typer.Typer(
    name="openbiliclaw",
    help="🦀 OpenBiliClaw — 你的 B 站专属 AI 朋友",
    add_completion=False,
)
auth_app = typer.Typer(help="B 站认证命令")
login_app = typer.Typer(help="账号登录命令")
browser_app = typer.Typer(help="agent-browser 浏览器命令")
autostart_app = typer.Typer(help="开机自启动命令")
ext_key_app = typer.Typer(help="浏览器扩展密钥管理命令")
tls_proxy_app = typer.Typer(help="TLS 反代管理命令（远程设备 HTTPS 访问）")
app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")
app.add_typer(browser_app, name="browser")
app.add_typer(autostart_app, name="autostart")
app.add_typer(ext_key_app, name="ext-key")
app.add_typer(tls_proxy_app, name="tls-proxy")
console = Console()
_APP_CONTEXT: dict[str, Any] = {}
_DISCOVER_STRATEGIES_OPTION = typer.Option(
    None,
    "--strategy",
    "-S",
    help=(
        "Bilibili 策略过滤，可多次传或逗号分隔："
        "search / trending / explore / related_chain。"
        "仅在 --source=bilibili 时生效。"
    ),
)
_ZHIHU_DISCOVER_KEYWORDS_ARGUMENT = typer.Argument(
    ...,
    help="知乎搜索关键词，可传多个；单个参数里也可以用逗号分隔。",
)
_ZHIHU_CREATOR_URLS_ARGUMENT = typer.Argument(
    ...,
    help="知乎作者主页 URL 或 people slug，可传多个。",
)
_ZHIHU_RELATED_URLS_ARGUMENT = typer.Argument(
    ...,
    help="知乎问题 / 回答 / 文章 URL，可传多个。",
)
_REDDIT_SUBREDDITS_ARGUMENT = typer.Argument(
    ...,
    help="subreddit 名称，支持逗号分隔。",
)
_REDDIT_RELATED_URLS_ARGUMENT = typer.Argument(
    ...,
    help="Reddit 帖子 URL，支持逗号分隔。",
)
_DOUYIN_DISCOVERY_KEYWORDS_OPTION = typer.Option(
    None,
    "--keyword",
    "-k",
    help="指定搜索关键词；可多次传或逗号分隔。不传时从 Soul 画像兴趣生成。",
)
_DOUYIN_DISCOVERY_CREATOR_SEC_UIDS_OPTION = typer.Option(
    None,
    "--creator-sec-uid",
    help=("兼容旧参数；当前公开 discovery 来源不再包含 creator。"),
)
_DOUYIN_DISCOVERY_SOURCES_OPTION = typer.Option(
    None,
    "--source",
    "-s",
    help="抖音 discovery 子来源：search、hot、feed，可多次传或逗号分隔。",
)
_DOUYIN_SEARCH_KEYWORDS_OPTION = typer.Option(
    ...,
    "--keyword",
    "-k",
    help="抖音搜索关键词，可重复传或用逗号分隔。",
)
_KEYWORD_INSPIRATION_PLATFORMS_OPTION = typer.Option(
    None,
    "--platform",
    "-p",
    help=(
        "目标平台，可重复传或逗号分隔。默认 bilibili；可选 bilibili/xiaohongshu/"
        "douyin/youtube/twitter/zhihu/reddit。"
    ),
)
_KEYWORD_INSPIRATION_KIND_OPTION = typer.Option(
    "regular",
    "--kind",
    help="关键词类型：regular 或 explore。",
)
_KEYWORD_INSPIRATION_LIMIT_OPTION = typer.Option(
    None,
    "--limit",
    min=1,
    max=48,
    help="本次 dry-run 每个平台最多生成多少关键词；不传则使用 config.toml。",
)
_KEYWORD_INSPIRATION_INTEREST_LIMIT_OPTION = typer.Option(
    None,
    "--interest-limit",
    min=1,
    max=16,
    help="本次 dry-run 最多抽取多少个二级兴趣；只影响预览成本，不写回 config.toml。",
)
_KEYWORD_INSPIRATION_PERSIST_AXES_OPTION = typer.Option(
    False,
    "--persist-axes",
    help="预览时写入 / 合并 inspiration axis 库；不增加 axis 使用计数。",
)
_CODEX_LOGIN_IMPORT_OPTION = typer.Option(
    False,
    "--import",
    help="只导入已有 Codex CLI 凭据，不调用 `codex login`。",
)
_CODEX_LOGIN_SOURCE_OPTION = typer.Option(
    None,
    "--source",
    help="Codex CLI auth.json 路径；默认读取 ~/.codex/auth.json。",
)
_CODEX_LOGIN_STATUS_OPTION = typer.Option(
    False,
    "--status",
    help="查看 Codex OAuth 登录状态。",
)
_CODEX_LOGIN_LOGOUT_OPTION = typer.Option(
    False,
    "--logout",
    help="删除 OpenBiliClaw 本地 Codex 凭据。",
)
_CONFIG_EXPORT_LEGACY_OUTPUT_OPTION = typer.Option(
    None,
    "--output",
    "-o",
    dir_okay=False,
    help="旧格式输出路径（默认：当前配置旁的 config.legacy.toml）",
)
_CONFIG_EXPORT_LEGACY_FORCE_OPTION = typer.Option(
    False,
    "--force",
    help="覆盖已存在的输出文件",
)


def _bootstrap_container_runtime() -> None:
    """Bootstrap runtime root and optional proxy env inside Docker-like runtimes."""
    if not (
        os.environ.get("OPENBILICLAW_PROJECT_ROOT")
        or os.environ.get("OPENBILICLAW_CONFIG_TEMPLATE")
    ):
        return

    from openbiliclaw.docker_runtime import bootstrap_runtime_environment

    bootstrap_runtime_environment(os.environ)


_RUNTIME_COMPONENTS: dict[str, Any] = {}
# Initial discover runs all four strategies in a single stage so the
# discovery engine's built-in concurrency kicks in: phase 1 runs
# ``search`` alone against a cookie-free client to avoid the IP-level
# search throttle, then phase 2 fans out ``trending``, ``related_chain``
# and ``explore`` concurrently via asyncio.gather. Wall time compresses
# from ``∑strategy`` to roughly ``search + max(trending, related, explore)``.
#
# Rate-limiting is already bounded by ``DiscoveryConcurrencyController``:
# ``search_budget_total=30`` splits across the three search-using
# strategies, and ``bilibili_request_concurrency=2`` caps simultaneous
# HTTP requests regardless of how many strategies run in parallel.
_INIT_DISCOVERY_PLAN = [
    ["search", "trending", "related_chain", "explore"],
]
# Initial pool target. Kept small so the discover phase finishes in
# one or two LLM-eval waves and ``_run_backfill`` doesn't trigger. The
# background refresh loop tops the pool up to
# ``scheduler.pool_target_count`` (300 by default) over the following hour, so a
# tiny init pool only delays diversity, never reduces it.
_INIT_POOL_TARGET_COUNT = 15
_INIT_BILIBILI_HISTORY_LIMIT = 500
_INIT_BILIBILI_FAVORITE_LIMIT = 500
_INIT_BILIBILI_FOLLOW_LIMIT = 100
# X (Twitter): the user's own Likes + Bookmarks, fetched server-side via
# twitter-cli (no extension task). Both are strong explicit-preference signals.
_INIT_X_LIKES_LIMIT = 200
_INIT_X_BOOKMARKS_LIMIT = 200
_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE = 300
_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS = 240.0
_DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_REDDIT_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS = 6.0
_EXTENSION_PRESENCE_REQUIRED_WARNING = (
    "WARN extension presence required; backend will pause background LLM work "
    "after grace period if no extension client connects"
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping


def _print_page_title(title: str, subtitle: str = "") -> None:
    """Render a consistent page title."""
    body = title if not subtitle else f"{title}\n[dim]{subtitle}[/dim]"
    console.print(Panel.fit(body, border_style="cyan"))


def _print_status_panel(kind: str, title: str, body: str) -> None:
    """Render a status panel with consistent visual semantics."""
    styles = {
        "success": "green",
        "warning": "yellow",
        "error": "red",
        "info": "cyan",
        "stub": "blue",
    }
    console.print(Panel(body, title=title, border_style=styles.get(kind, "cyan")))


def _print_key_value_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Render a key-value table for status-like commands."""
    table = Table(title=title, show_header=False, box=None, pad_edge=False)
    table.add_column("key", style="bold cyan", no_wrap=True)
    table.add_column("value")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def _format_pause_on_disconnect_status(*, enabled: bool, grace_seconds: int) -> str:
    if not enabled:
        return "关闭"
    return f"开启（宽限 {grace_seconds}s）"


def _warn_if_pause_on_disconnect_requires_presence() -> None:
    """Print a startup warning when background work depends on extension presence."""
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
    except Exception:
        return

    if cfg.scheduler.pause_on_extension_disconnect:
        console.print(
            f"[yellow]{_EXTENSION_PRESENCE_REQUIRED_WARNING}[/yellow]",
            soft_wrap=True,
        )


def _preflight_loopback_ollama(cfg: Any) -> None:
    if not ollama_required(cfg) or not cfg.autostart.manage_ollama:
        return
    endpoint = effective_ollama_endpoint(cfg)
    if not is_loopback(endpoint):
        return
    if _ollama_is_running(host=endpoint):
        return
    if not _is_default_ollama_endpoint(endpoint):
        console.print(
            f"[yellow]本机 Ollama 端点 {endpoint} 未响应；自定义端口不会自动执行 "
            "`ollama serve`，请自行管理该服务。[/yellow]"
        )
        return
    if not _ollama_start_serve_background():
        console.print(
            "[yellow]Ollama preflight 未能拉起本机服务；后端继续启动，"
            "后续 LLM/embedding 请求可能降级或失败。[/yellow]"
        )


def _self_heal_autostart_registration(cfg: Any) -> None:
    from openbiliclaw.runtime import autostart

    warning = autostart.reconcile(cfg)
    if warning:
        console.print(f"[yellow]{warning}[/yellow]")


def _print_section_title(title: str) -> None:
    """Render a consistent section title."""
    console.print(f"[bold cyan]{title}[/bold cyan]")


def _print_placeholder(feature: str, next_step: str = "") -> None:
    """Render a consistent placeholder panel for unfinished commands."""
    body = "功能开发中"
    if next_step:
        body = f"{body}\n[dim]下一步：{next_step}[/dim]"
    _print_page_title(feature)
    _print_status_panel("stub", "开发中", body)


async def _run_with_progress(
    coro: Any,
    *,
    label: str,
    eta_seconds: int,
    tick_seconds: int = 20,
    status_provider: Callable[[], str] | None = None,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
) -> Any:
    """Run a coroutine while printing periodic progress updates.

    Init's LLM-heavy phases (analyze_events, build_initial_profile,
    discover) each take 1-5 minutes of mostly-silent waiting on
    deepseek thinking. Without a heartbeat the user can't tell
    whether the process is alive or stuck. This helper prints one
    "started, ETA Xs" line, ticks every ``tick_seconds`` with
    elapsed/ETA while the work runs, and prints a final completion
    line with actual wall time.

    ``status_provider`` (optional) returns a short live-status suffix —
    e.g. ``"已完成 3/12 批"`` — appended to every heartbeat so a stalled
    inner batch shows a *frozen* sub-progress next to the growing
    elapsed clock, pinpointing where it hung. The ETA half never lies:
    once ``elapsed`` passes ``eta_seconds`` the heartbeat switches from a
    ``预计还需 ~Ns`` countdown (which would otherwise pin at ``~0s`` and
    read as "about to finish") to an explicit "已超预估、仍在处理" notice.
    """
    import time as _time
    from contextlib import suppress as _suppress

    console.print(f"  [dim]→ {label}（预计 ~{eta_seconds}s）[/dim]")
    start = _time.monotonic()

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(tick_seconds)
            elapsed = int(_time.monotonic() - start)
            if elapsed < eta_seconds:
                eta_part = f"预计还需 ~{eta_seconds - elapsed}s"
            else:
                eta_part = f"已超预估(~{eta_seconds}s)，仍在处理"
            status = ""
            if status_provider is not None:
                with _suppress(Exception):
                    text = status_provider()
                    if text:
                        status = f" · {text}"
            console.print(f"  [dim]· {label}: 已用 {elapsed}s / {eta_part}{status}[/dim]")
            if progress_callback is not None:
                with _suppress(Exception):
                    await progress_callback(elapsed, eta_seconds)

    ticker_task = asyncio.create_task(_ticker())
    try:
        result = await coro
    finally:
        ticker_task.cancel()
        with _suppress(asyncio.CancelledError, BaseException):
            await ticker_task
    elapsed = int(_time.monotonic() - start)
    console.print(f"  [green]✓[/green] {label} 用时 {elapsed}s")
    return result


def _content_author_row(content: Any) -> tuple[str, str]:
    """Return the (label, value) author row for one content-like object.

    Two source-agnostic rules, shared with the three GUI surfaces:

    * **Value** — ``author_name`` is the universal author field and
      ``up_name`` is the Bilibili-only legacy one. ``DiscoveredContent``
      back-fills ``author_name`` from ``up_name`` but never the reverse,
      so non-Bilibili sources (Bangumi, Zhihu, YouTube, …) populate only
      ``author_name`` and reading ``up_name`` alone rendered "（未知）"
      for all of them. Prefer ``author_name``, keep ``up_name`` as the
      fallback for legacy rows — same order as the backend's
      ``content.author_name or content.up_name``.
    * **Label** — mirrors ``formatRecommendationAuthorLine`` in
      ``extension/popup/popup-helpers.js``: Bilibili keeps the native
      "UP 主", every other platform gets the neutral "作者" (a Bangumi
      director or a Zhihu answerer is not an UP). A missing / unknown
      ``source_platform`` falls back to bilibili so legacy rows keep
      their old label.
    """
    from openbiliclaw.saved_sync.identity import canonical_source_platform

    name = str(getattr(content, "author_name", "") or getattr(content, "up_name", "") or "").strip()
    platform = canonical_source_platform(str(getattr(content, "source_platform", "") or ""))
    label = "UP 主" if (platform or "bilibili") == "bilibili" else "作者"
    return (label, name or "（未知）")


def _content_id_row(content: Any) -> tuple[str, str]:
    """Return the (label, value) identifier row for one content-like object.

    ``bvid`` is the universal identifier column, not a Bilibili-only one:
    every non-Bilibili mapper stores its own id there (``bangumi.py``'s
    ``bangumi_subject_to_content`` sets ``bvid=content_id``, i.e. the bgm
    subject id). Labelling it "BV号" unconditionally printed rows like
    ``BV号  8`` for Bangumi — 8 is a subject id, not a BV number.

    Same two rules as :func:`_content_author_row`: Bilibili keeps its native
    term, every other platform gets a neutral "内容 ID", and a missing /
    unknown ``source_platform`` falls back to bilibili so legacy rows keep
    their old label.
    """
    from openbiliclaw.saved_sync.identity import canonical_source_platform

    platform = canonical_source_platform(str(getattr(content, "source_platform", "") or ""))
    label = "BV号" if (platform or "bilibili") == "bilibili" else "内容 ID"
    return (label, str(getattr(content, "bvid", "") or "") or "（暂无）")


def _print_recommendation_card(item: Any, index: int) -> None:
    """Render one recommendation in a card-like format."""
    published = format_published_time(
        getattr(item.content, "published_at", ""),
        getattr(item.content, "published_label", ""),
    )
    rows = [
        ("标题", item.content.title or "（暂无）"),
        _content_author_row(item.content),
    ]
    if published:
        rows.append(("发布时间", published))
    if item.topic_label:
        rows.append(("话题标签", item.topic_label))
    rows.extend(
        [
            ("推荐理由", item.expression or "（暂无）"),
            _content_id_row(item.content),
        ]
    )
    _print_key_value_table(f"推荐 {index}", rows)


def _print_discovered_content_preview(item: Any, index: int) -> None:
    """Render one discovered content preview row."""
    _print_key_value_table(
        f"发现 {index}",
        [
            ("标题", item.title or "（暂无）"),
            _content_author_row(item),
            ("来源策略", item.source_strategy or "（未知）"),
            ("相关性分数", f"{float(item.relevance_score or 0.0):.2f}"),
        ],
    )


def _initialize_logging(log_level_override: str | None = None) -> None:
    """Load config and initialize the logging system.

    Skips the on-startup unmanaged-logs sweep when invoked via the
    ``logs-prune`` command — that command's whole purpose is letting
    the user inspect / control cleanup, so triggering automatic sweep
    inside the callback would defeat the dry-run contract.
    """
    import sys

    from openbiliclaw.config import load_config
    from openbiliclaw.logging_setup import configure_logging

    config = load_config()
    skip_sweep = "logs-prune" in sys.argv
    configure_logging(
        config,
        console_level_override=log_level_override,
        sweep_unmanaged=not skip_sweep,
    )


def _build_registry() -> Any:
    """Build the configured LLM registry."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm import build_llm_registry

    return build_llm_registry(load_config())


def _build_llm_concurrency_gate() -> Any:
    """Return the single LLM gate owned by this CLI process composition."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate

    cached = _RUNTIME_COMPONENTS.get("llm_concurrency_gate")
    if cached is not None:
        return cached
    gate = LLMConcurrencyGate(load_config().llm.concurrency)
    _RUNTIME_COMPONENTS["llm_concurrency_gate"] = gate
    return gate


def _build_auth_manager() -> Any:
    """Build the configured Bilibili auth manager."""
    from openbiliclaw.bilibili.auth import AuthManager
    from openbiliclaw.config import load_config

    config = load_config()
    return AuthManager(config.data_path, proxy=config.bilibili.proxy or None)


def _build_browser() -> Any:
    """Build the configured Bilibili browser integration."""
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.bilibili.browser import BilibiliBrowser
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliBrowser(
        executable=config.bilibili.browser_executable,
        headed=config.bilibili.browser_headed,
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
    )


def _build_bilibili_client() -> Any:
    """Build the configured Bilibili API client."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
        proxy=config.bilibili.proxy or None,
    )


def _build_soul_engine() -> Any:
    """Build the configured soul engine with initialized memory storage."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import module_overrides_from_config
    from openbiliclaw.soul.engine import SoulEngine

    class _UnavailableLLM:
        default_provider = ""

        def is_chat_capable(self, _name: str) -> bool:
            return False

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

        async def complete_provider(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

    cfg = load_config()
    memory = _build_memory_manager()
    try:
        llm = _build_registry()
    except Exception:
        llm = _UnavailableLLM()
    return SoulEngine(
        llm=llm,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        satisfaction_filter_enabled=cfg.soul.preference.satisfaction_filter_enabled,
        posture_gate_mode=cfg.soul.posture_gate_mode,
        posture_gate_force_enforce=cfg.soul.posture_gate_force_enforce,
        module_overrides=module_overrides_from_config(cfg),
        llm_concurrency=cfg.llm.concurrency,
        llm_concurrency_gate=_build_llm_concurrency_gate(),
        speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
        speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
        speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
        speculation_confirmation_threshold=cfg.scheduler.speculation_confirmation_threshold,
        speculation_max_active=cfg.scheduler.speculation_max_active,
        speculation_max_primary_interests=cfg.scheduler.speculation_max_primary_interests,
        speculation_max_secondary_interests=cfg.scheduler.speculation_max_secondary_interests,
        avoidance_speculation_interval_minutes=(
            cfg.scheduler.avoidance_speculation_interval_minutes
        ),
        avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
        avoidance_speculation_cooldown_days=cfg.scheduler.avoidance_speculation_cooldown_days,
        avoidance_speculation_confirmation_threshold=(
            cfg.scheduler.avoidance_speculation_confirmation_threshold
        ),
        avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
        speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
        profile_consolidation_enabled=cfg.scheduler.profile_consolidation_enabled,
        profile_consolidation_interval_hours=(cfg.scheduler.profile_consolidation_interval_hours),
        profile_consolidation_like_target_upper=(
            cfg.scheduler.profile_consolidation_like_target_upper
        ),
        profile_consolidation_like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
        profile_consolidation_archive_enabled=(cfg.scheduler.profile_consolidation_archive_enabled),
        # Feedback line config, three-surface contract with
        # ``api/runtime_context.py`` and the OpenClaw bootstrap: the CLI
        # feedback command runs the same batch (and, once the unified interest
        # line is on, the same pipeline fast line), so it must read the same
        # knobs instead of falling back to the constructor defaults.
        feedback_batch_threshold=cfg.scheduler.feedback_batch_threshold,
        unified_interest_line=cfg.scheduler.unified_interest_line,
        database=_get_runtime_database(),
    )


def _build_recommendation_engine() -> Any:
    """Build the recommendation engine with core-memory-aware LLM access."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.recommendation.engine import (
        RecommendationEngine,
        SupportsEmbeddingService,
    )

    memory = _build_memory_manager()
    database = _get_runtime_database()
    cfg = load_config()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
        concurrency_gate=_build_llm_concurrency_gate(),
    )
    from openbiliclaw.llm.registry import build_embedding_service

    _emb = build_embedding_service(cfg, registry)
    embedding_service = cast("SupportsEmbeddingService | None", _emb)

    def _xhs_self_info_provider() -> dict[str, object] | None:
        state = memory.load_discovery_runtime_state()
        info = state.get("xhs_self_info")
        return info if isinstance(info, dict) else None

    return RecommendationEngine(
        llm=llm_service,
        database=database,
        embedding_service=embedding_service,
        xhs_self_info_provider=_xhs_self_info_provider,
    )


def _build_dialogue(soul_engine: Any) -> Any:
    """Build the Socratic dialogue helper for interactive chat."""
    from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

    return SocraticDialogue(
        llm=_build_registry(),
        soul_engine=soul_engine,
        session="cli",
        learning_mode=DialogueLearningMode.LEGACY_DIRECT,
    )


def _run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Run the local FastAPI service used by the browser extension."""
    import uvicorn

    from openbiliclaw.api.app import create_app

    api_app = create_app()
    state = getattr(api_app, "state", None)
    if bool(getattr(state, "degraded", False)):
        issues = []
        for issue in list(getattr(state, "degraded_issues", [])):
            field = str(getattr(issue, "field", ""))
            message = str(getattr(issue, "message", issue))
            issues.append(f"- {field}: {message}" if field else f"- {message}")
        reason = str(getattr(state, "degraded_reason", ""))
        body = (
            f"reason: {reason or 'unknown'}\n"
            + "\n".join(issues)
            + "\n\nOpen the extension popup settings to fix the LLM credentials, "
            "then save; the daemon will recover in-process."
        )
        _print_status_panel("warning", "AI 服务配置有误 / Degraded mode", body)
    from openbiliclaw.runtime.api_server import (
        close_listener_sockets,
        create_wildcard_listener_sockets,
    )

    listeners = create_wildcard_listener_sockets(host, port)
    if listeners is None:
        uvicorn.run(api_app, host=host, port=port, log_level="info")
        return

    config = uvicorn.Config(api_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=listeners)
    finally:
        close_listener_sockets(listeners)


def _build_memory_manager() -> Any:
    """Build the initialized memory manager for event writes."""
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager

    cached = _RUNTIME_COMPONENTS.get("memory_manager")
    if cached is not None:
        return cached

    config = load_config()
    memory = MemoryManager(config.data_path, database=_get_runtime_database())
    memory.initialize()
    _RUNTIME_COMPONENTS["memory_manager"] = memory
    return memory


def _guided_init_completed_best_effort() -> bool | None:
    """Cheap pre-server read of whether guided init ever completed.

    Mirrors the runtime's soul-layer check. Returns ``None`` when the check
    itself fails — callers should stay silent on unknown state rather than
    nag a healthy install.
    """
    try:
        layer = _build_memory_manager().get_layer("soul")
        data = getattr(layer, "data", {})
        return isinstance(data, dict) and bool(data)
    except Exception:
        return None


def _build_discovery_engine() -> Any:
    """Build the discovery engine with currently implemented strategies."""
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
    )
    from openbiliclaw.discovery.strategies.strategies import (
        ExploreStrategy,
        RelatedChainStrategy,
        SearchStrategy,
        TrendingStrategy,
    )
    from openbiliclaw.llm.concurrency import background_llm_concurrency
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config

    memory = _build_memory_manager()
    database = _get_runtime_database()
    bilibili_client = _build_bilibili_client()
    from openbiliclaw.config import load_config

    cfg = load_config()
    # Topic-lifecycle serialization switch (spec Phase 4); default off.
    from openbiliclaw.discovery.strategies._utils import set_topic_lifecycle_serialization

    set_topic_lifecycle_serialization(
        str(getattr(getattr(cfg, "soul", None), "topic_lifecycle_serialization", "off"))
        .strip()
        .lower()
        == "on"
    )
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
        concurrency_gate=_build_llm_concurrency_gate(),
    )
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=2,
        llm_evaluation_concurrency=background_llm_concurrency(cfg.llm.concurrency),
    )

    # Build embedding service from config (optional)
    from openbiliclaw.llm.registry import build_embedding_service

    embedding_service = build_embedding_service(cfg, registry)
    discovery_cfg = getattr(cfg, "discovery", None)

    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        concurrency=concurrency,
        embedding_service=embedding_service,
        multimodal_evaluation_enabled=bool(
            getattr(discovery_cfg, "multimodal_evaluation_enabled", False)
        ),
        multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
        multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
        multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
        multimodal_image_timeout_seconds=int(
            getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6)
        ),
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=database,
        embedding_service=embedding_service,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
        database=database,
        embedding_service=embedding_service,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
        database=database,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        embedding_service=embedding_service,
        database=database,
    )

    engine.register_strategy(search_strategy)
    engine.register_strategy(trending_strategy)
    engine.register_strategy(related_strategy)
    engine.register_strategy(explore_strategy)
    return engine


def _get_runtime_database() -> Any:
    """Build or return the shared runtime database instance."""
    cached = _RUNTIME_COMPONENTS.get("database")
    if cached is not None:
        return cached

    from openbiliclaw.config import load_config
    from openbiliclaw.storage.database import Database

    config = load_config()
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    _RUNTIME_COMPONENTS["database"] = database
    return database


def _build_usage_recorder() -> Any:
    """Build or return the shared LLM usage recorder (cost ledger sink).

    CLI commands construct their own ``LLMService`` / ``SoulEngine``
    instead of going through ``runtime_context``, so without this every
    CLI-run LLM call was invisible in ``openbiliclaw cost``.
    """
    cached = _RUNTIME_COMPONENTS.get("usage_recorder")
    if cached is not None:
        return cached

    from openbiliclaw.llm.usage_recorder import UsageRecorder

    recorder = UsageRecorder(sink=_get_runtime_database())
    _RUNTIME_COMPONENTS["usage_recorder"] = recorder
    return recorder


def _runtime_database_path() -> Path:
    from openbiliclaw.config import load_config

    config = load_config()
    return config.data_path / "openbiliclaw.db"


def _runtime_backup_dir() -> Path:
    return _runtime_database_path().parent / "backups"


def _maybe_create_runtime_database_backup() -> None:
    from openbiliclaw.storage.maintenance import maybe_create_scheduled_backup

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    maybe_create_scheduled_backup(db_path, _runtime_backup_dir())


def _ensure_runtime_database_healthy() -> None:
    from openbiliclaw.storage.maintenance import check_database_integrity

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    report = check_database_integrity(db_path)
    if report.healthy:
        return
    _print_status_panel(
        "error",
        "数据库损坏",
        "检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。",
    )
    if report.error:
        console.print(report.error)
    raise typer.Exit(code=1)


def _run_db_repair() -> Any:
    from openbiliclaw.storage.maintenance import repair_database

    return repair_database(_runtime_database_path(), backup_dir=_runtime_backup_dir())


def _history_item_to_event(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Bilibili history item into a unified event-layer payload.

    Routes through ``build_event()`` (v0.3.22+) so the resulting dict
    has the same shape as Xiaohongshu / future-source events, with a
    natural-language ``context`` the LLM analyzer can consume directly.
    """
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI, build_event

    history_meta = item.get("history", {})
    if not isinstance(history_meta, dict):
        history_meta = {}
    bvid = str(history_meta.get("bvid", "")).strip()
    title = str(item.get("title", "")).strip()
    author = str(item.get("author_name", item.get("author", ""))).strip()
    view_at = history_meta.get("view_at", item.get("view_at", ""))
    metadata: dict[str, Any] = {
        "bvid": bvid,
        "view_at": view_at,
    }
    if bvid:
        metadata["content_id"] = bvid
    # Watch metrics decide how ``classify_event_satisfaction`` reads this row.
    # Without them every video the user ever watched lands in the ledger as
    # "unknown satisfaction" — a 100%-watched video and a 3-second bounce look
    # identical to everything downstream of init.
    for source_field, target in (
        ("progress", "watch_seconds"),
        ("duration", "video_duration_seconds"),
    ):
        value = item.get(source_field)
        if isinstance(value, (int, float)) and value > 0:
            metadata[target] = float(value)
    tag = str(item.get("tag_name", "") or "").strip()
    if tag:
        metadata["category"] = tag
    return build_event(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        author=author,
        metadata=metadata,
    )


def _x_tweet_to_event(tweet: dict[str, Any], *, event_type: str) -> dict[str, Any] | None:
    """Normalize a twitter-cli ``tweet_to_dict`` into a unified preference event.

    Mirrors ``_history_item_to_event``: routes through ``build_event()`` so X
    likes / bookmarks share the same event shape as B站 favorites and feed the
    soul analyzer identically. ``event_type`` is ``"like"`` (X likes) or
    ``"favorite"`` (X bookmarks) — both are explicit-positive signals. Returns
    ``None`` for tombstones (no ``id``). The canonical URL matches the discovery
    side (``x_normalize``): ``https://x.com/<handle>/status/<id>``.
    """
    from openbiliclaw.sources.event_format import SOURCE_TWITTER, build_event

    tweet_id = str(tweet.get("id", "") or "").strip()
    if not tweet_id:
        return None
    raw_author = tweet.get("author")
    author = raw_author if isinstance(raw_author, dict) else {}
    screen_name = str(author.get("screenName", "") or "").strip()
    author_name = f"@{screen_name}" if screen_name else str(author.get("name", "") or "").strip()
    handle = screen_name or "i"  # x.com/i/status/<id> resolves without a handle
    text = str(tweet.get("articleText") or tweet.get("text") or "").strip()
    first_line = text.splitlines()[0] if text else ""
    title = first_line[:140]
    verb = "点赞" if event_type == "like" else "收藏"
    if title and author_name:
        context = f"在 X {verb}了 {author_name} 的推文:{title}"
    elif title:
        context = f"在 X {verb}了一条推文:{title}"
    else:
        context = f"在 X {verb}了一条推文"
    return build_event(
        event_type=event_type,
        source_platform=SOURCE_TWITTER,
        title=title,
        url=f"https://x.com/{handle}/status/{tweet_id}",
        author=author_name,
        context=context,
        metadata={
            "tweet_id": tweet_id,
            "screen_name": screen_name,
            "body_text": text,
        },
    )


@app.callback()
def main(log_level: str | None = typer.Option(None, "--log-level")) -> None:
    """Global CLI options."""
    _APP_CONTEXT["log_level"] = log_level
    _bootstrap_container_runtime()
    _initialize_logging(log_level_override=log_level)
    _sync_outbound_proxy()


def _sync_outbound_proxy() -> None:
    """Mirror [network].proxy into the process-level source of truth for CLI.

    Runs once per CLI invocation so any command that builds an LLM registry or
    the updater routes overseas traffic through the configured proxy. Guarded
    so a missing/broken config never blocks a command from starting.
    """
    import contextlib

    from openbiliclaw.config import load_config
    from openbiliclaw.network import set_outbound_proxy

    # Config resolution must never block a command from starting.
    with contextlib.suppress(Exception):
        network = load_config().network
        set_outbound_proxy(network.proxy, mode=network.mode)


def _print_config_guidance(messages: list[str]) -> None:
    """Render config hints in a consistent way."""
    if not messages:
        return
    console.print("[bold yellow]配置提示[/bold yellow]")
    for message in messages:
        console.print(f"  - {message}")


def _print_auth_status(status: Any) -> None:
    """Render auth status consistently."""
    state_label = "已认证" if status.authenticated else "未认证"
    _print_page_title("认证概览", "B站认证状态")
    rows = [
        ("状态", state_label),
        ("Cookie 文件", str(status.cookie_path)),
    ]
    if status.username:
        rows.append(("用户名", str(status.username)))
    if status.user_id:
        rows.append(("UID", str(status.user_id)))
    if status.message:
        rows.append(("说明", str(status.message)))
    _print_key_value_table("认证信息", rows)


def _print_browser_status(browser: Any) -> None:
    """Render browser installation status."""
    availability = "已安装" if browser.is_available else "未安装"
    _print_page_title("浏览器集成状态", "agent-browser 状态")
    _print_key_value_table(
        "浏览器信息",
        [
            ("状态", availability),
            ("可执行文件", str(browser.executable)),
        ],
    )


def _require_runtime_config() -> None:
    """Exit with a clear message when runtime config is incomplete."""
    error = _load_runtime_config_error()
    if error is not None:
        raise typer.Exit(code=1)


def _print_runtime_config_error(error: str, hints: list[str] | None = None) -> None:
    """Render runtime config errors consistently."""
    console.print("[bold red]配置错误[/bold red]")
    _print_config_guidance(hints or [])
    console.print(f"  {error}")


def _load_runtime_config_error(*, render: bool = True) -> str | None:
    """Return a user-facing runtime config error and optionally print guidance."""
    from openbiliclaw.config import (
        ConfigError,
        load_config_with_diagnostics,
        validate_runtime_config,
    )

    config, diagnostics = load_config_with_diagnostics()
    try:
        validate_runtime_config(config)
    except ConfigError as exc:
        hints = diagnostics.messages + [
            f"{issue.field}: {issue.message}" for issue in diagnostics.issues
        ]
        if render:
            _print_runtime_config_error(str(exc), hints)
        return str(exc)
    return None


def _is_interactive_terminal() -> bool:
    """Return whether the current process is attached to an interactive TTY."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _save_runtime_provider_config(
    provider: str,
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> None:
    """Persist one complete provider instance and make it the global primary."""
    from openbiliclaw.config import (
        LLMInstanceConfig,
        effective_llm_default_chain,
        effective_llm_instances,
        effective_llm_routes,
        load_config_with_diagnostics,
        save_config,
    )

    config, diagnostics = load_config_with_diagnostics()
    provider = provider.strip().lower()
    provider_config = getattr(config.llm, provider, None)
    if provider_config is None:
        save_config(config, diagnostics.config_path)
        return

    instances = effective_llm_instances(config.llm)
    chain = effective_llm_default_chain(config.llm)
    routes = effective_llm_routes(config.llm)
    instance_id = next(
        (
            candidate
            for candidate in [*chain, *instances]
            if candidate in instances
            and instances[candidate].provider_type.strip().lower() == provider
        ),
        "",
    )
    if not instance_id:
        instance_id = f"{provider.replace('_', '-')}-main"
        suffix = 2
        while instance_id in instances:
            instance_id = f"{provider.replace('_', '-')}-{suffix}"
            suffix += 1
        instance = LLMInstanceConfig(
            api_key=provider_config.api_key,
            model=provider_config.model,
            base_url=provider_config.base_url,
            auth_mode=provider_config.auth_mode,
            api_flavor=provider_config.api_flavor,
            http_referer=provider_config.http_referer,
            x_title=provider_config.x_title,
            reasoning_effort=provider_config.reasoning_effort,
            num_ctx=provider_config.num_ctx,
            name=provider,
            provider_type=provider,
            enabled=True,
        )
        instances[instance_id] = instance
    instance = instances[instance_id]
    instance.enabled = True
    if api_key:
        instance.api_key = api_key.strip()
    if base_url:
        instance.base_url = base_url.strip()
    if model:
        instance.model = model.strip()

    config.llm.instance_routing = True
    config.llm.instances = instances
    config.llm.default_chain = [
        instance_id,
        *[candidate for candidate in chain if candidate != instance_id],
    ]
    for module_name, route in routes.items():
        setattr(config.llm, module_name, route)
    save_config(config, diagnostics.config_path)


# Default base_url + chat model per provider. The user can always override
# both in the wizard; these are just the "I picked X, what should the
# defaults look like?" answers.
# Last refreshed 2026-05. When a provider rolls a new flagship,
# update the model field here AND the matching ``_LLM_MENU`` /
# ``_PROVIDER_MODEL_HINT`` entries.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    # OpenAI: gpt-4o-mini retired from ChatGPT in Feb 2026; gpt-5-nano
    # is the cheapest current-gen ($0.05 / $0.40 per 1M).
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5-nano"},
    # Claude: Sonnet 4.6 is the current main-line Sonnet (1M context).
    # Opus 4.7 is top-tier; Haiku 4.5 is the budget option.
    "claude": {"base_url": "", "model": "claude-sonnet-4-6"},
    # Gemini: 2.5-flash is the stable budget default (3-flash is preview;
    # 3.1-pro is reasoning flagship).
    "gemini": {"base_url": "", "model": "gemini-2.5-flash"},
    # DeepSeek: V4 family. deepseek-chat / deepseek-reasoner deprecate
    # 2026-07-24.
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    # Ollama: project is Chinese-primary; qwen2.5:7b handles Chinese
    # noticeably better than llama3 at the same size.
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"},
    # OpenRouter: route to OpenAI's cheapest current-gen by default.
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-5-nano"},
}


_PROVIDER_HINTS: dict[str, str] = {
    "openai": "OpenAI 官方（api.openai.com）",
    "claude": "Anthropic Claude 官方",
    "gemini": "Google Gemini 官方",
    "deepseek": "DeepSeek 官方（OpenAI 兼容协议）",
    "ollama": "本地 Ollama（无需 Key）",
    "openrouter": "OpenRouter 聚合",
}


# One-liner shown right before the model prompt so the user knows
# what's actually on offer, instead of confirming an opaque string.
# Lists current main-line model names per provider — refresh when
# a provider deprecates / renames a model.
_PROVIDER_MODEL_HINT: dict[str, str] = {
    "deepseek": (
        "可选模型: deepseek-v4-flash (默认 / 便宜) / deepseek-v4-pro (更强)。"
        "旧名 deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用"
    ),
    "openai": (
        "可选模型: gpt-5-nano (默认 / 最便宜) / gpt-5.4-nano / "
        "gpt-5.4-mini / gpt-5.5 (旗舰 4/2026) / gpt-5.5-pro (高精度)。"
        "gpt-4o / gpt-4o-mini 已从 ChatGPT 退役,API 仍可调"
    ),
    "gemini": (
        "可选模型: gemini-2.5-flash (默认 / 稳定) / "
        "gemini-3-flash-preview (新一代 / 推理强) / "
        "gemini-3.1-pro-preview (旗舰 / Public Preview, 需付费项目) / "
        "gemini-3.1-flash-lite-preview (最便宜)"
    ),
    "claude": (
        "可选模型: claude-sonnet-4-6 (默认 / 1M 上下文) / "
        "claude-haiku-4-5 (便宜) / claude-opus-4-7 (旗舰 / agentic 最强)。"
        "claude-sonnet-4-5 仍可调"
    ),
    "openrouter": (
        "默认 openai/gpt-5-nano。OpenRouter 模型名格式: <vendor>/<model>,"
        "如 anthropic/claude-sonnet-4-6 / google/gemini-2.5-flash"
    ),
    "ollama": (
        "常见模型: qwen2.5:7b (默认 / 中文好) / llama3.2 (Meta 新版) / "
        "gemma2 (Google) / mistral (轻量) / deepseek-r1 (开源推理)。"
        "模型名要和 Ollama 库里完全一致 (`ollama list` 看)"
    ),
}


# Sub-menu shown when user picks "OpenAI 协议兼容自建网关" from
# _LLM_MENU. Order = menu order. Each entry pre-fills base_url so the
# user doesn't have to copy from a doc; default_model is a sensible
# starting point but the prompt still lets them change it. ``hint``
# is a one-liner shown right above the model prompt listing real
# main-line models for that service.
#
# When adding a new compat-protocol vendor:
# 1. Verify they speak true OpenAI Chat Completions protocol (Bearer
#    auth + ``/v1/chat/completions`` shape). Many "OpenAI compatible"
#    APIs subtly differ on tools / streaming / function_call format —
#    try a smoke call before listing here.
# 2. Pick a representative low-cost default_model so users get a
#    cheap experience by default; advanced users can switch in
#    Phase 2.
#
# Order rationale (2026-05): the OpenAI-protocol-compat menu's *primary*
# real-world purpose is to plumb in 中转站 / OneAPI / 团队 LLM 网关 keys
# — the user has already bought access from a relay vendor and just
# wants OpenBiliClaw to talk to it. That's why ``relay`` is the
# default (#1). Native Chinese vendor APIs (Kimi / MiniMax / Qwen / GLM
# / Yi) follow because some users do go straight to the vendor; Azure
# and self-hosted are infrastructure-flavor variants for企业 / 玩家;
# ``custom`` is the manual escape hatch.
_OPENAI_COMPAT_PRESETS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "relay",
        {
            "label": "★ 中转站 / OneAPI / 公司团队 LLM 网关 (大多数人选这个)",
            "description": (
                "中转站 = 第三方代理 OpenAI / Claude 的二级商家(国内付人民币用海外模型)。"
                "OneAPI / 团队 LLM 网关 = 公司自建的多模型聚合 + 计费 + 限流网关。"
                "买中转站 Key 的人选这个就对了"
            ),
            "signup_url": (
                "找你充值的那家中转站官网拿 Key (它们大多有自己的 base_url 和文档)。"
                "OneAPI 是开源自建项目: https://github.com/songquanpeng/one-api"
            ),
            "supports_embedding": "true",  # most relay services proxy embeddings too
            "base_url": "",  # user-supplied — every relay has its own
            "default_model": "gpt-5-nano",
            "hint": (
                "看你中转站后端代理到哪个真实模型。中转站 / OneAPI 通常代理 "
                "OpenAI (gpt-5-nano / gpt-5.4-mini / gpt-5.5) 或 "
                "Claude (claude-sonnet-4-6 / claude-opus-4-7) 或国产模型,"
                "按你充值的那家给你的模型清单填"
            ),
            "embedding_alt": (
                "中转站通常也代理 OpenAI text-embedding-3-small,"
                "Phase 3 高级选项里可以指向同一个 base_url"
            ),
        },
    ),
    (
        "kimi",
        {
            "label": "Kimi (Moonshot AI 月之暗面) 官方",
            "description": (
                "国产长上下文老牌 (256K ctx),长文档理解 / 网页爬阅 / "
                "学术阅读这些场景表现好,日常对话也稳。直接从 Moonshot 官方拿 Key"
            ),
            "signup_url": (
                "https://platform.moonshot.cn/console/api-keys （国内）/ "
                "https://platform.moonshot.ai （国际）"
            ),
            "supports_embedding": "false",
            "base_url": "https://api.moonshot.ai/v1",
            "default_model": "kimi-k2.6",
            "hint": (
                "kimi-k2.6 (默认 / 最新 / 256K 上下文 / 多模态) / kimi-k2.5。"
                "旧 moonshot-v1-* 和 K2-series 即将停服(K2 系列 2026-05-25 停)"
            ),
            "domain_alt": (
                "国内用户也可改 base_url 为 https://api.moonshot.cn/v1 (域名不同,Key 通用)"
            ),
        },
    ),
    (
        "minimax",
        {
            "label": "MiniMax 官方",
            "description": (
                "国产代码 / agent 场景的当前 SOTA 之一 (M3: 1M ctx / 图文视频输入),"
                "便宜 ($0.60 / $2.40 per M),适合做推荐这种结构化输出任务"
            ),
            "signup_url": (
                "https://platform.minimaxi.com/user-center/basic-information/interface-key "
                "（国内）/ https://platform.minimax.io （国际）"
            ),
            "supports_embedding": "false",
            "base_url": "https://api.minimax.io/v1",
            "default_model": "MiniMax-M3",
            "hint": (
                "MiniMax-M3 (默认 / 最新 / 5-2026 / 1M ctx) / "
                "MiniMax-M2.7 / MiniMax-M2.5 / MiniMax-M2.1。"
                "旧 abab 系列 (abab6.5*) 已被 M 系列替代"
            ),
            "domain_alt": (
                "国内用户改 base_url 为 https://api.minimaxi.com/v1 (旧 .chat 域名将停)"
            ),
        },
    ),
    (
        "qwen",
        {
            "label": "通义千问 (阿里 DashScope) 官方",
            "description": (
                "阿里出品,中文最强档之一 (qwen3.6 系列),qwen-plus 别名"
                "自动跟最新快照,无需手动升级。免费档调用次数有限,商用记得充值"
            ),
            "signup_url": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
            "supports_embedding": "true",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen-plus",
            "hint": (
                "qwen-flash (最便宜) / qwen-plus (默认 / 平衡) / qwen-max (旗舰)。"
                "都是别名,自动跟最新快照(当前 → qwen3.6-*, 2026-04 系列)"
            ),
            "embedding_alt": "DashScope 也支持 text-embedding-v3 (Phase 3 高级选项里可选)",
        },
    ),
    (
        "zhipu",
        {
            "label": "智谱 ChatGLM 官方",
            "description": (
                "清华 + 智谱出品。GLM-4.7-Flash 完全免费(每天调用次数限制),"
                "做推荐 / 画像够用;GLM-5 是付费旗舰 (745B MoE,Claude Opus 级)"
            ),
            "signup_url": "https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys",
            "supports_embedding": "true",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "default_model": "glm-4.7-flash",
            "hint": (
                "glm-4.7-flash (默认 / 免费 / 200K ctx) / glm-5 (付费旗舰 / 4/2026 / 745B MoE) / "
                "glm-4.6。注意: base_url 是 /api/paas/v4 不是 /v1"
            ),
            "embedding_alt": "智谱也有 embedding-3 (Phase 3 高级选项里可选)",
        },
    ),
    (
        "yi",
        {
            "label": "零一万物 (Yi) 官方",
            "description": (
                "李开复创业团队出品,Yi-Large 在 LMSYS 中文榜常年 top 国产之一。"
                "yi-medium 平衡好用,yi-spark 最便宜适合高频小任务"
            ),
            "signup_url": "https://platform.lingyiwanwu.com/apikeys",
            "supports_embedding": "false",
            "base_url": "https://api.lingyiwanwu.com/v1",
            "default_model": "yi-medium",
            "hint": (
                "yi-spark (最便宜) / yi-medium (默认 / 平衡) / yi-lightning (新 / 快) / "
                "yi-large (旗舰) / yi-large-turbo (平衡) / yi-medium-200k (长上下文)"
            ),
        },
    ),
    (
        "azure",
        {
            "label": "Azure OpenAI",
            "description": (
                "微软的 OpenAI 企业版。和 OpenAI 官方模型一致,但鉴权 / 模型名 / "
                "endpoint 都按 Azure 的 deployment 模式走。多用于企业合规场景"
            ),
            "signup_url": (
                "Azure portal → 创建 OpenAI resource → 创建 deployment → "
                "Keys & Endpoint 取 KEY 和 ENDPOINT"
            ),
            "supports_embedding": "true",
            "base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
            "default_model": "",
            "hint": (
                "Azure 模型名 = 你创建 deployment 时指定的 deployment name(不是底层 gpt-5)。"
                "Base URL 把 YOUR-RESOURCE / YOUR-DEPLOYMENT 替换成你自己的"
            ),
            "embedding_alt": (
                "Azure 上 embedding 模型也是单独 deployment,Phase 3 时再起一个 deployment "
                "并填那个的 endpoint"
            ),
        },
    ),
    (
        "self-hosted",
        {
            "label": "自建 vLLM / LMStudio / Ollama 网关",
            "description": (
                "你自己跑的 LLM 服务,常见: vLLM (多卡推理) / LMStudio (Mac M-series) / "
                "Ollama 的 OpenAI 兼容 shim。免费但要自备硬件"
            ),
            "signup_url": "无 (本地服务通常不需要 Key,鉴权可留空)",
            "supports_embedding": "false",  # depends — assume no
            "base_url": "http://localhost:8000/v1",
            "default_model": "",  # force user to type their deployed model
            "hint": (
                "看你网关上部署的是什么。HuggingFace 路径,如 "
                "meta-llama/Llama-3.3-70B-Instruct / Qwen/Qwen2.5-72B-Instruct / "
                "deepseek-ai/DeepSeek-V3"
            ),
            "embedding_alt": (
                "如果你的 vLLM/LMStudio 也部署了 embedding 模型,Phase 3 高级选项里"
                "可以指向同一个 base_url"
            ),
        },
    ),
    (
        "custom",
        {
            "label": "其它 (完全手填)",
            "description": (
                "上面 8 个都不匹配的兜底选项。任何 OpenAI Chat Completions 协议兼容的服务"
                "都能填(Bearer auth + /v1/chat/completions 形态)"
            ),
            "signup_url": "看你的服务方文档",
            "supports_embedding": "false",  # unknown
            "base_url": "",
            "default_model": "",
            "hint": (
                "Base URL 必须以 /v1 (或网关等价路径)结尾。"
                "模型名得是网关上真实部署 / 提供的那个,写错会 404"
            ),
        },
    ),
)


def _ollama_has_model(model: str, host: str = "http://127.0.0.1:11434") -> bool:
    """Return True if Ollama already has the named model pulled."""
    import httpx

    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(f"{host}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models", [])
            for tag in tags:
                name = str(tag.get("name", "")).strip()
                # Match "bge-m3", "bge-m3:latest", etc.
                if name == model or name.startswith(f"{model}:"):
                    return True
    except Exception:
        return False
    return False


def _ollama_pull_model(model: str, host: str = "http://127.0.0.1:11434") -> bool:
    """Stream a model pull from Ollama; print progress to console."""
    import httpx

    try:
        with (
            httpx.Client(timeout=600.0, trust_env=False) as client,
            client.stream(
                "POST",
                f"{host}/api/pull",
                json={"model": model, "stream": True},
            ) as stream,
        ):
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line:
                    continue
                import json as _json

                try:
                    evt = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                status = evt.get("status", "")
                if status:
                    console.print(f"  [dim]{status}[/dim]")
                if evt.get("error"):
                    console.print(f"  [red]{evt['error']}[/red]")
                    return False
        return True
    except Exception as exc:
        console.print(f"  [red]拉取失败: {exc}[/red]")
        return False


def _ollama_install_if_missing() -> bool:
    """If Ollama isn't installed, offer to auto-install via package mgr.

    Returns True iff the binary is available after this call. The user
    can decline (we then return False — caller should fall back to
    asking them to install manually). Mirrors agent_bootstrap.py's
    install_ollama, but with an interactive consent prompt because
    invoking package managers is a side-effect users should approve.
    """
    import shutil
    import subprocess

    if shutil.which("ollama"):
        return True

    console.print(
        "[yellow]检测不到 ollama 命令。[/yellow] "
        "OpenBiliClaw 可以帮你装上，过程透明：\n"
        "  • macOS: 通过 brew install ollama\n"
        "  • Windows: 通过 winget install Ollama.Ollama\n"
        "  • Linux: 通过官方 install.sh（curl https://ollama.com/install.sh | sh）"
    )
    if not typer.confirm("是否现在帮你装 Ollama？", default=True):
        console.print(
            "[dim]已跳过自动安装。请手动从 https://ollama.com/download 下载，"
            "然后重新跑一遍本命令。[/dim]"
        )
        return False

    if sys.platform == "darwin":
        if not shutil.which("brew"):
            console.print(
                "[red]没找到 brew。请从 https://ollama.com/download 下载 Mac 安装包，"
                "装好后重新运行本命令。[/red]"
            )
            return False
        subprocess.run(["brew", "install", "ollama"], check=False)
    elif os.name == "nt":
        if not shutil.which("winget"):
            console.print(
                "[red]没找到 winget。请从 https://ollama.com/download 下载 Windows 安装包，"
                "装好后重新运行本命令。[/red]"
            )
            return False
        subprocess.run(
            [
                "winget",
                "install",
                "-e",
                "--id",
                "Ollama.Ollama",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            check=False,
        )
    else:
        # Linux: piped curl | sh — needs sudo for systemd registration.
        subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True,
            check=False,
        )

    if shutil.which("ollama"):
        console.print("[green]Ollama 安装成功。[/green]")
        return True
    console.print(
        "[red]安装似乎没成功。请从 https://ollama.com/download 手动装一下，再重新跑本命令。[/red]"
    )
    return False


def _save_embedding_config(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    api_key: str = "",
) -> None:
    """Persist the embedding provider/model selection to config.toml.

    For OpenAI-compatible providers the wizard may collect a custom
    ``base_url`` / ``api_key`` (e.g. a self-hosted vLLM gateway running
    bge-m3 over the OpenAI protocol). These are written into
    ``[llm.embedding]`` because embedding is independent from chat
    provider configuration.
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    config.llm.embedding.provider = provider
    config.llm.embedding.model = model
    if base_url:
        config.llm.embedding.base_url = base_url.strip()
    elif provider == "ollama" and not config.llm.embedding.base_url.strip():
        config.llm.embedding.base_url = "http://127.0.0.1:11434/v1"
    if api_key:
        config.llm.embedding.api_key = api_key.strip()
    save_config(config, diagnostics.config_path)


def _save_module_overrides(overrides: dict[str, dict[str, str]]) -> None:
    """Persist per-module overrides as complete custom instance chains."""
    from openbiliclaw.config import (
        LLMInstanceConfig,
        ModuleLLMConfig,
        effective_llm_default_chain,
        effective_llm_instances,
        effective_llm_routes,
        load_config_with_diagnostics,
        save_config,
    )

    config, diagnostics = load_config_with_diagnostics()
    instances = effective_llm_instances(config.llm)
    default_chain = effective_llm_default_chain(config.llm)
    routes = effective_llm_routes(config.llm)
    for module, payload in overrides.items():
        if module not in routes:
            continue
        provider_type = payload.get("provider", "").strip().lower()
        model = payload.get("model", "").strip()
        if not provider_type and not model:
            routes[module] = ModuleLLMConfig(inherit=True)
            continue
        primary = instances.get(default_chain[0]) if default_chain else None
        if not provider_type and primary is not None:
            provider_type = primary.provider_type.strip().lower()
        instance_id = next(
            (
                candidate
                for candidate, instance in instances.items()
                if instance.provider_type.strip().lower() == provider_type
                and (not model or instance.model.strip() == model)
            ),
            "",
        )
        if not instance_id:
            base = next(
                (
                    instance
                    for instance in instances.values()
                    if instance.provider_type.strip().lower() == provider_type
                ),
                None,
            )
            if base is None:
                continue
            base_instance_id = f"{module}-{provider_type.replace('_', '-')}"
            instance_id = base_instance_id
            suffix = 2
            while instance_id in instances:
                instance_id = f"{base_instance_id}-{suffix}"
                suffix += 1
            instances[instance_id] = LLMInstanceConfig(
                api_key=base.api_key,
                model=model or base.model,
                base_url=base.base_url,
                auth_mode=base.auth_mode,
                api_flavor=base.api_flavor,
                http_referer=base.http_referer,
                x_title=base.x_title,
                reasoning_effort=base.reasoning_effort,
                num_ctx=base.num_ctx,
                name=f"{module} · {base.name}",
                provider_type=base.provider_type,
                enabled=base.enabled,
            )
        routes[module] = ModuleLLMConfig(inherit=False, chain=[instance_id])
    config.llm.instance_routing = True
    config.llm.instances = instances
    config.llm.default_chain = default_chain
    for module, route in routes.items():
        setattr(config.llm, module, route)
    save_config(config, diagnostics.config_path)


_SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
)


# Numbered menu shown in Phase 1. Order matters (v0.3.20+):
# DeepSeek first as the default zero-friction recommendation
# (¥0.001/千 token); OpenAI / Gemini / Claude / OpenRouter for users who
# already have those keys; "OpenAI 协议兼容自建网关" demoted to
# the final "(高级)" entry so 普通用户 don't pick it by mistake — most
# people who think they want it actually want option 2 (OpenAI 官方).
#
# Local Ollama is intentionally NOT offered here as a chat provider
# (v0.3.176+): the bundled Ollama is embedding-only (bge-m3), and small
# local chat models don't meet the content-pipeline quality bar. Ollama
# chat stays supported in the backend registry / desktop settings page
# for advanced users, and ``ollama`` remains a valid ``default_provider``
# when it arrives from an existing config or an explicit flag — we just
# stop *offering* it in the interactive menu.
_LLM_MENU: tuple[tuple[str, str, str], ...] = (
    (
        "deepseek",
        "DeepSeek 官方 ★默认推荐",
        "默认 deepseek-v4-flash (V4)。¥0.001/千 token 几乎免费,国内可直连",
    ),
    (
        "openai-compat",
        "★ 第二推荐 — 中转站 / OpenAI 协议兼容服务",
        "买了中转站 Key 选这个。也覆盖 Kimi / 通义 / 智谱 / Yi / MiniMax 官方 / Azure / vLLM",
    ),
    (
        "openai",
        "OpenAI 官方",
        "默认 gpt-5-nano (最便宜的 GPT-5)。api.openai.com,需要 sk- 开头的 Key",
    ),
    (
        "gemini",
        "Gemini 官方",
        "默认 gemini-2.5-flash (稳定 / 便宜)。Google AI Studio 申请 Key,免费档每天 1500 次够用",
    ),
    (
        "claude",
        "Claude 官方",
        "默认 claude-sonnet-4-6。Anthropic console,按 token 付费,质量高",
    ),
    (
        "openrouter",
        "OpenRouter 聚合",
        "默认 openai/gpt-5-nano。一个 Key 跑多家模型,按调用计费",
    ),
)


def _print_provider_table() -> None:
    """Render the provider menu — DeepSeek default, 协议兼容 second (v0.3.27+)."""
    console.print("[bold]OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。[/bold]")
    console.print("请选一个 LLM 服务：\n")
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("名称", no_wrap=True)
    table.add_column("说明")
    for index, (_, label, hint) in enumerate(_LLM_MENU, start=1):
        table.add_row(str(index), label, hint)
    console.print(table)
    console.print(
        "[dim]Tip:不确定就选 1 (DeepSeek),¥0.001/千 token 几乎免费,月度通常 ¥0.5-2。"
        "已经买了中转站 / OneAPI Key 选 2 (协议兼容)。"
        "本地 Ollama 仅用于向量检索(embedding),不作为聊天服务商;"
        "如需本地聊天模型请到设置页手动配置。[/dim]"
    )


def _resolve_menu_choice(raw: str) -> str | None:
    """Map a Phase 1 menu input to the canonical choice key.

    Accepts either the index (1..N) or the canonical name typed directly,
    e.g. "ollama" or "openai-compat". Returns None on unknown input.
    """
    raw = raw.strip().lower()
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(_LLM_MENU):
            return _LLM_MENU[index - 1][0]
        return None
    aliases = {
        "openai-compat": "openai-compat",
        "compat": "openai-compat",
        "openai兼容": "openai-compat",
    }
    if raw in aliases:
        return aliases[raw]
    if raw in {key for key, *_ in _LLM_MENU}:
        return raw
    return None


def _prompt_openai_compat() -> tuple[str, str, str, str]:
    """openai-compat sub-flow — preset menu → intro → base_url → key → model → embedding hint.

    All compat-protocol services write to the ``[llm.openai]`` section
    (the ``openai_provider.OpenAIProvider`` class is the universal
    Bearer-auth + ``/v1/chat/completions`` client). The sub-menu's job
    is to remove the four pain points普通用户 hit when self-configuring:

    1. **Where to register** — every preset surfaces ``signup_url``
       above the API Key prompt so the user can ``cmd-click`` it.
    2. **What this thing actually is** — ``description`` runs as a one-
       paragraph intro after preset selection, framing the strengths /
       sweet spot of the service so the user knows what they signed up
       for.
    3. **Base URL format** — auto-filled from the preset; the user just
       confirms.
    4. **No embedding endpoint** — Kimi / MiniMax / Yi / self-hosted
       don't ship embeddings, so we pre-warn the user that Phase 3
       will fall back to local Ollama bge-m3. For Qwen / GLM / Azure /
       relay (who DO have embeddings), we call out the advanced option
       to point Phase 3 at the same base_url.
    """
    console.print(
        "\n[bold]配置 OpenAI 协议兼容服务[/bold]\n"
        "[dim]这一项主要给三类用户:[/dim]\n"
        "[dim]  1. **买了中转站 / OneAPI Key**(国内付人民币用海外模型,最常见)→ 选 1[/dim]\n"
        "[dim]  2. **用国产大模型官方 API**(Kimi / 通义 / 智谱 / Yi / MiniMax) → 选 2-6[/dim]\n"
        "[dim]  3. **企业 Azure / 自建 vLLM-LMStudio** → 选 7-8[/dim]\n"
        r"[dim]后端会按 OpenAI 协议(Bearer 鉴权 + /v1/chat/completions)打你给的 Base URL,"
        r"配置统一写到 config.toml 的 \[llm.openai] 段。[/dim]\n"
    )
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("服务", no_wrap=True)
    table.add_column("Base URL")
    table.add_column("默认模型")
    for index, (_, preset) in enumerate(_OPENAI_COMPAT_PRESETS, start=1):
        bu = preset["base_url"] or "[dim](需自填)[/dim]"
        dm = preset["default_model"] or "[dim](需自填)[/dim]"
        table.add_row(str(index), preset["label"], bu, dm)
    console.print(table)
    console.print(
        "[dim]Tip: 不知道选哪个就看你的 API Key 是哪家发的—— "
        "买的中转站 / OneAPI(常见)选 1;Kimi/MiniMax/通义/智谱/Yi 官方选 2-6;"
        "Azure 选 7;自建本地服务选 8。[/dim]\n"
    )
    raw = typer.prompt(f"选服务类型 (1-{len(_OPENAI_COMPAT_PRESETS)})", default="1").strip()
    try:
        choice_index = max(1, min(len(_OPENAI_COMPAT_PRESETS), int(raw))) - 1
    except ValueError:
        choice_index = 0
    preset_key, preset = _OPENAI_COMPAT_PRESETS[choice_index]

    # Per-preset intro: what is this service, and where to register.
    console.print(f"\n[bold]→ 已选: {preset['label']}[/bold]")
    if preset.get("description"):
        console.print(f"[dim]  {preset['description']}[/dim]")
    if preset.get("signup_url"):
        console.print(f"[dim]  申请 Key: [cyan]{preset['signup_url']}[/cyan][/dim]")
    if preset.get("domain_alt"):
        console.print(f"[dim]  💡 {preset['domain_alt']}[/dim]")
    console.print()

    base_url_default = preset["base_url"]
    if base_url_default:
        base_url = (
            typer.prompt(
                f"Base URL (回车 = {base_url_default})",
                default=base_url_default,
                show_default=False,
            ).strip()
            or base_url_default
        )
    else:
        base_url = typer.prompt(
            "Base URL (必填,见上面的表格)",
        ).strip()

    api_key = typer.prompt(
        f"{preset['label']} 的 API Key (本地 / 不鉴权服务可留空)",
        hide_input=True,
        default="",
        show_default=False,
    ).strip()

    if preset.get("hint"):
        console.print(f"[dim]  {preset['hint']}[/dim]")
    default_model = preset["default_model"]
    if default_model:
        model = (
            typer.prompt(
                f"模型名 (回车 = {default_model})",
                default=default_model,
                show_default=False,
            ).strip()
            or default_model
        )
    else:
        model = typer.prompt("模型名 (必填,见上面的提示)").strip()

    # Embedding heads-up — most compat-protocol vendors don't ship a
    # /v1/embeddings endpoint. Pre-warn before the user gets to Phase 3
    # so they don't think the wizard is broken when it auto-falls back.
    has_embed = preset.get("supports_embedding", "false") == "true"
    if not has_embed:
        console.print(
            f"\n[yellow]ⓘ {preset['label']} 没有 OpenAI 兼容的 embedding endpoint[/yellow]\n"
            "[dim]  Phase 3 会自动选「本地 Ollama bge-m3」给推荐管线做向量化"
            "(免费 / 离线 / 不影响主 LLM)。回车跳过即可。[/dim]"
        )
    elif preset.get("embedding_alt"):
        console.print(f"\n[dim]💡 embedding 提示: {preset['embedding_alt']}[/dim]")

    # Final confirm: show the canonical triplet so the user catches typos.
    console.print(
        f"\n[bold green]✓ 即将写入 config.toml:[/bold green]\n"
        f"  [llm.openai].base_url = [cyan]{base_url}[/cyan]\n"
        f"  [llm.openai].model    = [cyan]{model}[/cyan]"
    )
    return "openai", base_url, api_key, model


def _prompt_provider_triplet(menu_choice: str) -> tuple[str, str, str, str]:
    """Phase 2 — collect (provider, base_url, api_key, model) for the choice.

    ``menu_choice`` is the value from ``_LLM_MENU`` (e.g. ``"ollama"`` or
    ``"openai-compat"``). For ``openai-compat`` we still write to the
    ``[llm.openai]`` section but force the user to give us a Base URL —
    that's the single field that distinguishes "I'll use OpenAI the
    company" from "I have my own gateway that speaks the OpenAI API."
    """
    if menu_choice == "openai-compat":
        return _prompt_openai_compat()

    provider = menu_choice
    defaults = _PROVIDER_DEFAULTS.get(provider, {})
    default_base_url = defaults.get("base_url", "")
    default_model = defaults.get("model", "")

    if provider == "ollama":
        console.print(
            "\n[bold]配置本地 Ollama[/bold]\n"
            "[dim]我会自动帮你装/启动/拉模型，无需 API Key。第一次拉模型可能要"
            "几分钟（取决于网速）。[/dim]"
        )
        # Phase 1: ensure binary exists (install if missing, with consent).
        if not _ollama_install_if_missing():
            return provider, default_base_url, "", default_model

        # Phase 2: ensure daemon is up.
        if not _ollama_start_serve_background():
            console.print("[red]Ollama 已装好但服务没起来。请手动跑 `ollama serve` 后重试。[/red]")
            return provider, default_base_url, "", default_model

        # Phase 3: ask which model and pull if missing.
        ollama_hint = _PROVIDER_MODEL_HINT.get("ollama")
        if ollama_hint:
            console.print(f"[dim]  {ollama_hint}[/dim]")
        model = (
            typer.prompt(
                "选个 Ollama 模型（按回车 = 默认 llama3）",
                default=default_model,
            ).strip()
            or default_model
        )
        if not _ollama_has_model(model):
            console.print(f"开始拉取 {model}（首次下载耗时几分钟）…")
            if not _ollama_pull_model(model):
                console.print(
                    f"[red]{model} 拉取失败。可以稍后手动跑 `ollama pull {model}` "
                    "再重启 backend。[/red]"
                )
        else:
            console.print(f"[green]模型 {model} 已就绪。[/green]")
        return provider, default_base_url, "", model

    # Cloud providers: ask for key (mandatory), let model fall to default.
    console.print(f"\n[bold]配置 {_PROVIDER_HINTS.get(provider, provider)}[/bold]")
    api_key = typer.prompt(
        "API Key",
        prompt_suffix=": ",
        hide_input=True,
        default="",
        show_default=False,
    ).strip()
    # Surface the per-provider model menu before asking, so the user
    # consciously confirms the default rather than just hitting Enter
    # on an opaque string. Particularly important for DeepSeek where
    # deepseek-chat / deepseek-reasoner are deprecating 2026-07-24.
    model_hint = _PROVIDER_MODEL_HINT.get(provider)
    if model_hint:
        console.print(f"[dim]  {model_hint}[/dim]")
    model = (
        typer.prompt(
            "模型名（直接回车 = 用默认）",
            default=default_model,
            show_default=bool(default_model),
        ).strip()
        or default_model
    )
    base_url = default_base_url
    if provider == "claude":
        # issue #72 — Claude keys bought from third-party relays need a
        # custom Anthropic-protocol (/v1/messages) endpoint. Enter = official.
        base_url = typer.prompt(
            "Base URL（直接回车 = Anthropic 官方；第三方中转填其地址）",
            default="",
            show_default=False,
        ).strip()
    return provider, base_url, api_key, model


def _interactive_embedding_setup(default_provider: str, *, auto_if_ready: bool = False) -> None:
    """Phase 3 — embedding service (v0.3.20+ "有默认值的取舍提问").

    Default = 1 (本地 Ollama bge-m3). Mirrors the question shape used by
    docs/agent-install.md: each option carries a tradeoff explanation,
    "不确定就回 1". Two advanced branches (custom OpenAI-compatible
    endpoint / pin a different provider) are kept but de-emphasized so
    普通用户 don't get derailed.

    ``auto_if_ready`` (v0.3.95+): when a local Ollama is already running
    and serving bge-m3, skip the menu entirely and just wire it up. This
    closes the "confirmed Ollama for chat but embedding stayed disabled"
    gap that silently degrades dedup. Only ``init`` passes this — the
    explicit ``setup-embedding`` command keeps the full menu so users can
    deliberately switch providers.
    """
    if auto_if_ready and _ollama_is_running() and _ollama_has_model("bge-m3"):
        _save_embedding_config(provider="ollama", model="bge-m3")
        console.print(
            "\n[bold green]检测到本地 Ollama 已就绪且装有 bge-m3,已自动启用本地 embedding"
            "(跨视频去重 / 相似度判定)。[/bold green]"
            "\n[dim]想换成 Gemini/OpenAI 或关闭,去插件设置页或重跑 "
            "`openbiliclaw setup-embedding`。[/dim]"
        )
        return
    console.print(
        "\n[bold]Embedding(向量化)服务[/bold]\n"
        "[dim]把视频标题/简介压成向量,跨视频做相似度对比 —— 决定"
        '"这条和你之前喜欢的那条是不是同一类"。和聊天 LLM 是分开的。[/dim]\n'
    )
    options = (
        (
            "1",
            "本地 Ollama bge-m3 ★默认推荐",
            "免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)",
        ),
        (
            "2",
            "云端 Gemini embedding",
            "质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key",
        ),
        (
            "3",
            "暂不启用 embedding",
            "保留独立配置为空;不会跟随主 LLM,也不会自动 fallback",
        ),
        ("4", "(高级)自定义 OpenAI 兼容服务", "vLLM / OneAPI / 自建网关 —— 自填 base_url"),
        ("5", "(高级)指定其他 provider", "手动选 provider + 模型 + 可选 base_url"),
        ("0", "跳过(不修改当前 embedding 配置)", ""),
    )
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("方案", no_wrap=True)
    table.add_column("说明")
    for label, name, desc in options:
        table.add_row(label, name, desc)
    console.print(table)
    console.print(
        "[dim]Tip:不确定就选 1。日常推荐质量已经够用且不消耗主 LLM 配额。"
        "想再准一点选 2(Gemini),需要去 https://aistudio.google.com/apikey 拿 Key。[/dim]"
    )

    choice = typer.prompt("请选择 embedding 方案", default="1").strip()

    if choice in {"0", "skip", "跳过"}:
        console.print("[dim]已跳过 embedding 配置,不修改当前设置。[/dim]")
        return

    if choice in {"1", "ollama", ""}:
        # Auto-install + start + pull. Same flow as Phase 1's Ollama
        # branch — share the helpers so the user doesn't have to learn
        # different setups for chat vs embedding.
        if not _ollama_install_if_missing():
            console.print("[yellow]Ollama 装机失败,未启用本地 embedding。[/yellow]")
            return
        if not _ollama_start_serve_background():
            console.print("[red]Ollama 已装好但服务没起来。请手动跑 `ollama serve` 后重试。[/red]")
            return

        model = "bge-m3"
        if _ollama_has_model(model):
            console.print(f"[green]已检测到本地模型 {model}[/green]")
        else:
            console.print(f"开始拉取 {model}(首次下载约 568MB,几分钟)…")
            if not _ollama_pull_model(model):
                console.print(f"[red]{model} 拉取失败,未启用本地 embedding[/red]")
                return
        _save_embedding_config(provider="ollama", model=model)
        console.print(f"[bold green]已启用本地 Ollama embedding({model})[/bold green]")
        return

    if choice in {"2", "gemini"}:
        from openbiliclaw.config import load_config

        existing_key = ""
        try:
            existing_cfg = load_config()
            existing_key = (existing_cfg.llm.gemini.api_key or "").strip()
        except Exception:
            pass

        if existing_key:
            console.print("[green]复用 [llm.gemini] 段已配置的 API Key,无需再填。[/green]")
            api_key = existing_key
        else:
            console.print(
                "[dim]去 https://aistudio.google.com/apikey 拿一个 Gemini API Key,"
                "复制粘贴到下面(免费档每天 1500 次,日常用足够)。[/dim]"
            )
            api_key = typer.prompt(
                "Gemini API Key",
                hide_input=True,
                default="",
                show_default=False,
            ).strip()
            if not api_key:
                console.print("[yellow]Key 为空,未启用 Gemini embedding。[/yellow]")
                return

        _save_embedding_config(
            provider="gemini",
            model="gemini-embedding-001",
            api_key=api_key,
        )
        console.print("[bold green]已启用 Gemini embedding(gemini-embedding-001)[/bold green]")
        return

    if choice in {"3", "follow"}:
        _save_embedding_config(provider="", model="")
        console.print(
            "[green]已设置为不启用 embedding。需要语义去重/相似度时,可之后运行 "
            "`openbiliclaw setup-embedding` 单独配置。[/green]"
        )
        return

    if choice == "4":
        base_url = typer.prompt(
            "Embedding Base URL(OpenAI 兼容,例如 http://localhost:8000/v1)"
        ).strip()
        api_key = typer.prompt(
            "Embedding API Key(如服务无鉴权可留空)",
            hide_input=True,
            default="",
            show_default=False,
        ).strip()
        model = typer.prompt("Embedding 模型名称", default="bge-m3").strip()
        _save_embedding_config(
            provider="openai",
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        console.print(
            "[bold green]已配置自定义 OpenAI 兼容 embedding 服务"
            r"(写入 \[llm.embedding] 段)。[/bold green]"
        )
        return

    if choice == "5":
        target = (
            typer.prompt(
                "选择 provider(claude / gemini / deepseek / openrouter / ollama)",
                default="gemini",
            )
            .strip()
            .lower()
        )
        if target not in _SUPPORTED_PROVIDERS:
            console.print("[red]未知 provider,跳过 embedding 配置。[/red]")
            return
        defaults = _PROVIDER_DEFAULTS.get(target, {})
        base_url = typer.prompt(
            f"{target} Base URL(留空走默认)",
            default=defaults.get("base_url", ""),
            show_default=bool(defaults.get("base_url")),
        ).strip()
        api_key = ""
        if target != "ollama":
            api_key = typer.prompt(
                f"{target} API Key",
                hide_input=True,
                default="",
                show_default=False,
            ).strip()
        model = typer.prompt(
            "Embedding 模型名称",
            default="text-embedding-3-small" if target == "openai" else "",
            show_default=False,
        ).strip()
        if not model:
            console.print("[red]模型名为空,跳过 embedding 配置。[/red]")
            return
        _save_embedding_config(
            provider=target,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        console.print(f"[bold green]已配置 {target} 作为 embedding provider。[/bold green]")
        return

    console.print("[red]未识别的选项,跳过 embedding 配置。[/red]")


def _interactive_module_overrides(default_provider: str) -> None:
    """Phase 4 — optional per-module LLM overrides (advanced, skippable)."""
    if not typer.confirm(
        "（高级，可跳过）是否为单个模块单独指定 provider/model？\n"
        "  典型场景：发现/评估走便宜模型，灵魂画像走高质量模型。",
        default=False,
    ):
        return

    overrides: dict[str, dict[str, str]] = {}
    modules = (
        ("soul", "灵魂画像（高质量模型，稳定性优先）"),
        ("discovery", "内容发现（吞吐量大，建议廉价模型）"),
        ("recommendation", "推荐文案（解释生成，平衡质量和成本）"),
        ("evaluation", "内容评估（高频调用，建议廉价模型）"),
    )
    for module, desc in modules:
        if not typer.confirm(f"为 [{module}] {desc} 配置覆盖？", default=False):
            continue
        provider = (
            typer.prompt(
                f"  {module} provider（留空 = 跟随默认 {default_provider}）",
                default="",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if provider and provider not in _SUPPORTED_PROVIDERS:
            console.print(f"  [red]未知 provider「{provider}」，跳过该模块。[/red]")
            continue
        model = typer.prompt(
            f"  {module} 模型（留空 = 跟随 provider 默认）",
            default="",
            show_default=False,
        ).strip()
        overrides[module] = {"provider": provider, "model": model}

    if overrides:
        _save_module_overrides(overrides)
        console.print(f"[green]已写入 {len(overrides)} 个模块的 LLM 覆盖配置。[/green]")
    else:
        console.print("[dim]未配置任何模块覆盖。[/dim]")


def _interactive_runtime_config_setup() -> None:
    """Guide the user through missing LLM config before init.

    Four-phase flow:
      1) Pick LLM service (DeepSeek-first menu; OpenAI-compat is its own entry,
         not buried inside ``openai``). Local Ollama is not offered as a
         chat provider — it's embedding-only here.
      2) Provide the fields that option actually needs.
      3) Choose how embeddings are served (separate question, not bundled).
      4) Optional per-module overrides (advanced, default skip).
    """
    _print_page_title("初始化前配置引导", "选 LLM、配 Embedding、填 B 站 Cookie")
    _print_provider_table()

    while True:
        raw = typer.prompt("\n请输入序号或名称（默认 1=DeepSeek）", default="1")
        choice = _resolve_menu_choice(raw)
        if choice is None:
            console.print("[bold red]看不懂这个输入，请重新输入序号或名称[/bold red]")
            continue

        provider, base_url, api_key, model = _prompt_provider_triplet(choice)

        _save_runtime_provider_config(
            provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        error = _load_runtime_config_error(render=False)
        if error is not None:
            console.print("[bold yellow]刚写入的配置仍不完整，请重新选择。[/bold yellow]")
            _print_runtime_config_error(error)
            continue

        console.print(
            "\n[bold]接下来配 Embedding[/bold]"
            "\n[dim]Embedding 是和聊天模型分开的：把视频标题/简介变成向量，"
            "用于跨视频去重和相似度判定。频次很高，所以单独拎出来配。[/dim]"
        )
        _interactive_embedding_setup(provider, auto_if_ready=True)

        console.print(
            "\n[bold]最后是 Per-module 覆盖（高级，默认可跳过）[/bold]"
            "\n[dim]给 soul / discovery / recommendation / evaluation 单独指定模型，"
            "比如发现/评估走便宜模型，画像走高质量。大多数用户不需要。[/dim]"
        )
        _interactive_module_overrides(provider)
        return


def _interactive_auth_setup(auth_manager: Any) -> Any:
    """Guide the user through Bilibili auth before init.

    Two paths since v0.3.12:
      A. Install the browser extension and let it auto-sync the cookie
         via ``POST /api/bilibili/cookie`` (recommended — zero F12).
      B. Paste the cookie manually right here (fallback for users who
         won't install the extension).
    """
    _print_page_title("初始化前认证引导", "补齐 B 站认证")
    console.print(
        "[bold]为什么需要 B 站 Cookie？[/bold]\n"
        "OpenBiliClaw 需要你的 B 站登录态来：\n"
        "  • 拉你的观看历史（用来训练画像）\n"
        "  • 以你的身份调 B 站 API 拿视频详情\n"
        "[dim]Cookie 只存在你本机 data/bilibili_cookie.json，不会上传任何地方。[/dim]\n\n"
        "[bold]两种方式（任选其一）：[/bold]\n"
        "  [cyan]1.[/cyan] 装浏览器扩展，自动同步（推荐，零配置）\n"
        "     下载: https://github.com/whiteguo233/OpenBiliClaw/releases\n"
        "     装好后扩展会几秒内自动把登录 Cookie 推到本地后端。\n"
        "     选这条会先退出 init；扩展同步完再跑 `openbiliclaw init` 即可。\n\n"
        "  [cyan]2.[/cyan] 现在手动贴 Cookie\n"
        "     1) 用 Chrome/Edge/Firefox 登录 https://www.bilibili.com\n"
        "     2) F12 → Network 标签 → 刷新 → 点任意 bilibili.com 请求\n"
        "     3) Headers 区域找到 cookie: 一行，右键复制整行 value\n"
        "     4) 把那一长串（含 SESSDATA / bili_jct / DedeUserID）粘下面\n"
    )
    choice = typer.prompt("请选 [1=装扩展自动同步 / 2=现在手贴]", default="1").strip()
    if choice in {"1", "extension", "ext", ""}:
        console.print(
            "\n[bold green]好的——退出当前 init，让扩展接手。[/bold green]\n"
            "  1. 启动后端：[cyan]openbiliclaw start[/cyan]（或保持当前 docker compose up）\n"
            "  2. 装扩展：[cyan]https://github.com/whiteguo233/OpenBiliClaw/releases[/cyan]\n"
            "  3. 确认你已登录 B 站；扩展会几秒内同步 Cookie\n"
            "  4. 再跑 [cyan]openbiliclaw init[/cyan] 完成画像生成 + 首轮发现\n"
        )
        raise typer.Exit(code=0)

    while True:
        cookie_value = typer.prompt("请粘贴 B 站 Cookie", prompt_suffix=": ")
        status = asyncio.run(auth_manager.validate_cookie(cookie_value))
        if status.authenticated:
            auth_manager.set_cookie(cookie_value)
            console.print("[bold green]登录成功[/bold green]")
            _print_auth_status(status)
            return status

        console.print("[bold red]认证失败 —— Cookie 看起来无效或过期了[/bold red]")
        _print_auth_status(status)
        if not typer.confirm("是否重试？（重新走一遍上面的步骤）", default=True):
            raise typer.Exit(code=1)


def _prepare_init_runtime(*, require_bili_auth: bool = True) -> Any:
    """Ensure runtime config and auth are ready before init proceeds.

    ``require_bili_auth`` gates the Bilibili-authentication step. Bilibili init
    needs it, but off-platform profile rebuilds (e.g. Bangumi collections feed
    only ``soul_engine.analyze_events`` + ``build_initial_profile``) never touch
    Bilibili, so they pass ``False`` to keep the runtime-config validation while
    skipping the B 站 auth gate that would otherwise abort a non-interactive run.
    """
    error = _load_runtime_config_error(render=False)
    if error is not None:
        if not _is_interactive_terminal():
            _print_runtime_config_error(error)
            raise typer.Exit(code=1)
        _interactive_runtime_config_setup()

    if not require_bili_auth:
        return None

    auth_manager = _build_auth_manager()
    status = asyncio.run(auth_manager.get_status())
    if status.authenticated:
        return status
    if not _is_interactive_terminal():
        console.print("[bold red]认证失败[/bold red]")
        console.print("请先执行 `openbiliclaw auth login` 完成 B 站认证。")
        raise typer.Exit(code=1)
    return _interactive_auth_setup(auth_manager)


def _format_strategy_group(strategies: list[str]) -> str:
    return " + ".join(strategies)


async def _run_init_discovery_backfill_async(
    profile: Any,
    *,
    target_pool_count: int = 100,
    label_suffix: str = "",
    progress_callback: Callable[[int, int, str], Awaitable[None] | None] | None = None,
) -> int:
    """Build the first serviceable discovery pool from the committed profile."""
    from openbiliclaw.discovery.pool_snapshot import build_cold_start_pool_snapshot
    from openbiliclaw.runtime.refresh import InitialPoolUnavailableError

    async def _report(done: int, total: int, note: str) -> None:
        if progress_callback is None:
            return
        result = progress_callback(done, total, note)
        if inspect.isawaitable(result):
            await result

    database = _get_runtime_database()
    discovery_engine = _build_discovery_engine()
    gate = _build_llm_concurrency_gate()
    target = max(0, int(target_pool_count))
    if target == 0:
        await _report(4, 4, "已跳过首轮内容池构建")
        return 0

    discovered_count = 0
    copy_error: BaseException | None = None

    for index, strategies in enumerate(_INIT_DISCOVERY_PLAN, start=1):
        current_pool_count = database.count_pool_candidates()
        gate.update_inventory(available=current_pool_count, target=target)
        if current_pool_count >= target:
            break
        await _report(0, 4, "正在基于完整画像生成发现方向并抓取候选")
        request_limit = max(20, target - current_pool_count)
        pool_snapshot = (
            build_cold_start_pool_snapshot(
                profile,
                pool_target_count=target,
                source_targets={"bilibili": target},
            )
            if current_pool_count <= 0
            else None
        )
        console.print(
            f"补货阶段 {index}/{len(_INIT_DISCOVERY_PLAN)}: {_format_strategy_group(strategies)}"
            f"{label_suffix}"
        )
        console.print(f"当前池子 {current_pool_count}/{target}，本轮请求上限 {request_limit}")
        discovered = await _run_with_progress(
            discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=request_limit,
                # Init is latency-critical — skip the default search-first
                # phase split and let every strategy share the gather.
                fully_parallel=True,
                pool_snapshot=pool_snapshot,
            ),
            label=f"发现内容({_format_strategy_group(strategies)} 并发){label_suffix}",
            eta_seconds=300,
        )
        discovered_count += len(discovered)
        current_pool_count = database.count_pool_candidates()
        gate.update_inventory(available=current_pool_count, target=target)
        await _report(
            2,
            4,
            f"已发现 {discovered_count} 条候选，正在生成首轮推荐文案",
        )

        if current_pool_count < target:
            recommendation_engine = _build_recommendation_engine()
            try:
                copied = await recommendation_engine.drain_pending_expression_copy(
                    profile=profile,
                    limit=max(1, target),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                copy_error = exc
                copied = max(0, int(getattr(exc, "completed", 0) or 0))
            current_pool_count = database.count_pool_candidates()
            gate.update_inventory(available=current_pool_count, target=target)
            await _report(
                3,
                4,
                f"已生成 {copied} 条推荐文案，正在验证首轮内容可用性",
            )
        console.print(
            f"阶段完成: 当前池子 {current_pool_count}/{target}，本轮发现 {len(discovered)} 条"
        )

    available = database.count_pool_candidates()
    gate.update_inventory(available=available, target=target)
    if available <= 0:
        raise InitialPoolUnavailableError(discovered_count=discovered_count) from copy_error
    await _report(4, 4, f"首轮内容池已就绪（{available} 条可直接浏览）")
    return discovered_count


def _xhs_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS


def _dy_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS


def _yt_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS


def _zhihu_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS


def _reddit_bootstrap_dedupe_hours() -> float:
    raw = os.environ.get(
        "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS",
        str(_DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS),
    )
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS


def _enqueue_xhs_bootstrap_task(*, force: bool = False, kick: bool = True) -> str | None:
    """Fire-and-forget enqueue of the bootstrap_profile task.

    Returns the task_id if enqueue succeeded, ``None`` otherwise (DB
    unavailable, daily budget exhausted, etc.). Doesn't wait — the
    extension picks the task off the queue and runs it in parallel
    with the rest of init.

    Defaults: ``max_scroll_rounds=15`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]小红书初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = XhsTaskQueue(database)
        dedupe_hours = _xhs_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if not force and dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的小红书 bootstrap 任务"
                        f"({status})；需要重新拉取可用 `openbiliclaw fetch-xhs --force`。[/dim]"
                    )
                    return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["saved", "liked", "xhs_history"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]小红书初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]小红书初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    # Wake the extension dispatcher immediately via the runtime-stream
    # WebSocket instead of waiting up to 60s for the next chrome.alarms
    # tick. The kick is best-effort — if the daemon's API isn't running
    # the existing alarm-based poll still picks up the task on next fire.
    # ``kick=False`` lets the guided-init pipeline register task ownership
    # with the coordinator *before* waking the extension (avoids a
    # register-after-kick race where an owned result is treated as foreign).
    if kick:
        _kick_task_dispatcher("xhs")
    return task_id


def _kick_task_dispatcher(source: str) -> None:
    """Fire-and-forget POST to the daemon's task-kick endpoint.

    The daemon broadcasts ``<source>_task_available`` over the
    runtime-stream WebSocket, which the extension's service-worker
    handles by triggering an immediate poll on the matching dispatcher.
    Failures are silent: if the daemon isn't running the existing
    chrome.alarms 60s poll fallback still picks the task up.
    """
    if source not in {"xhs", "dy", "yt", "zhihu", "reddit"}:
        return
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:8420/api/sources/{source}/kick"
    req = urllib.request.Request(url, method="POST", data=b"")
    # Short timeout — kick is best-effort. Daemon-not-running /
    # network blip / connection-refused all degrade silently to the
    # 60s alarm fallback.
    with suppress(urllib.error.URLError, TimeoutError, OSError):
        urllib.request.urlopen(req, timeout=1.0).close()


def _collect_xhs_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued bootstrap_profile task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of:
      - ``"ok"``         — task completed with notes
      - ``"empty"``      — task completed but extension returned 0 notes
      - ``"timeout"``    — wait window expired, task still pending / in-progress
      - ``"failed"``     — extension or backend reported error
      - ``"skipped"``    — no task_id (DB unavailable / budget exhausted)

    The wait deadline starts NOW; callers that enqueued the task earlier
    in the init flow benefit from the parallel-execution head start.
    """
    import json
    import time

    from openbiliclaw.sources.xhs_tasks import (
        XhsTaskQueue,
        xhs_bootstrap_notes_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = XhsTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return [], {}, "timeout"
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    notes = [note for note in result.get("notes", []) if isinstance(note, dict)]
    events = xhs_bootstrap_notes_to_events(notes)
    raw_counts = result.get("scope_counts", {})
    scope_counts = {"saved": 0, "liked": 0, "xhs_history": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                if source == f"xhs_bootstrap_{key}":
                    scope_counts[key] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label


def _import_xhs_bootstrap_events() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Backwards-compatible single-shot wrapper used by tests.

    For the live ``init`` flow we use the split enqueue/collect API
    above so xhs data collection runs in parallel with B站 fetches
    instead of serialising for a fixed wait. This wrapper preserves
    the old test contract.
    """
    task_id = _enqueue_xhs_bootstrap_task()
    events, counts, _status = _collect_xhs_bootstrap_events(task_id)
    return events, counts


def _enqueue_dy_bootstrap_task(*, kick: bool = True) -> str | None:
    """Fire-and-forget enqueue of the Douyin bootstrap_profile task.

    Mirror of ``_enqueue_xhs_bootstrap_task`` for the Douyin pipeline.
    No code shared between the two — separate ``DyTaskQueue`` table,
    separate env vars, separate user-visible messages. Soul-engine
    consumes the resulting events through the unified
    ``event_format.build_event`` contract, so the cross-source
    analysis remains uniform downstream.

    Defaults: ``max_scroll_rounds=15`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]抖音初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = DyTaskQueue(database)
        dedupe_hours = _dy_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                raw_result = recent.get("result_json")
                if isinstance(raw_result, dict):
                    parsed_result = raw_result
                elif isinstance(raw_result, (str, bytes, bytearray)):
                    try:
                        parsed_result = json.loads(raw_result)
                    except (TypeError, ValueError):
                        parsed_result = None
                else:
                    parsed_result = None
                recent_is_degraded = (
                    isinstance(parsed_result, dict)
                    and str(parsed_result.get("status", "")).strip().lower() == "degraded"
                )
                if recent_is_degraded:
                    console.print(
                        "  [dim]最近的抖音 bootstrap 任务仅部分完成；本次重新入队以补齐分页。[/dim]"
                    )
                else:
                    task_id = str(recent.get("id", "")).strip()
                    if task_id:
                        status = str(recent.get("status", "unknown"))
                        console.print(
                            "  [dim]复用最近的抖音 bootstrap 任务"
                            f"({status})；需要重新拉取可设 "
                            "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        )
                        return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]抖音初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]抖音初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("dy")
    return task_id


def _collect_dy_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued Douyin bootstrap task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of:
      - ``"ok"``         — task completed with videos
      - ``"degraded"``   — task completed with usable videos, but at least
        one scope could not prove pagination completeness
      - ``"empty"``      — task completed but extension returned 0 videos
        (typical when the user is not logged in to douyin.com — the
        soft anti-bot returns HTTP 200 + empty body, see design-doc
        Risk #7)
      - ``"timeout"``    — wait window expired, task still pending
      - ``"failed"``     — extension or backend reported error
      - ``"skipped"``    — no task_id (DB unavailable / budget exhausted)
    """
    import json
    import time

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_videos_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = DyTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return [], {}, "timeout"
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
    events = dy_bootstrap_videos_to_events(videos)
    raw_counts = result.get("scope_counts", {})
    scope_counts = {"dy_post": 0, "dy_collect": 0, "dy_like": 0, "dy_follow": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        # Fall back to per-event count: dy_bootstrap_videos_to_events
        # tags each event's metadata.import_source as
        # "dy_bootstrap_<scope_short>" (post / collect / like / follow).
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                short = key.removeprefix("dy_") if key.startswith("dy_") else key
                if source == f"dy_bootstrap_{short}":
                    scope_counts[key] += 1
    result_status = str(result.get("status", "")).strip()
    status_label = "degraded" if result_status == "degraded" else ("ok" if events else "empty")
    return events, scope_counts, status_label


def _enqueue_yt_bootstrap_task(*, kick: bool = True) -> str | None:
    """Enqueue a YouTube bootstrap_profile task for the browser extension.

    Defaults: ``max_scroll_rounds=10`` and ``max_items_per_scope=300``.
    Both can be overridden via env vars
    ``OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS`` and
    ``OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS``.
    """
    from openbiliclaw.sources.yt_tasks import YtTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]YouTube 初始化信号未导入: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    scroll_rounds = int(os.environ.get("OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS", "10"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None

    try:
        queue = YtTaskQueue(database)
        dedupe_hours = _yt_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_profile",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的 YouTube bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id
        task_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {
                "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
                "max_items_per_scope": max(1, max_items),
                "max_scroll_rounds": max(0, scroll_rounds),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]YouTube 初始化信号未导入: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]YouTube 初始化信号未导入: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("yt")
    return task_id


def _collect_yt_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued YouTube bootstrap task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of ``"ok"``, ``"empty"``, ``"timeout"``,
    ``"failed"``, or ``"skipped"``.
    """
    import json
    import time

    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_items_to_events,
    )

    if not task_id:
        return [], {}, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = YtTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    poll_interval = 0.5
    task: dict[str, Any] | None = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return [], {}, "timeout"
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    events = yt_bootstrap_items_to_events(items)
    raw_counts = result.get("scope_counts", {})
    scope_counts: dict[str, int] = {"yt_history": 0, "yt_subscriptions": 0, "yt_likes": 0}
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            for key in scope_counts:
                short = key.removeprefix("yt_") if key.startswith("yt_") else key
                if source == f"yt_bootstrap_{short}":
                    scope_counts[key] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label


def _enqueue_zhihu_bootstrap_task(
    *,
    profile_slug: str = "",
    kick: bool = True,
    profile_update: bool = False,
) -> str | None:
    """Enqueue a Zhihu bootstrap_events task for the browser extension.

    The extension executes same-origin Zhihu session fetches in the logged-in
    browser. This command is fetch-only; it does not trigger profile generation.
    """
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎事件未拉取: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    max_collections = int(os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS", "20"))
    task_id: str | None = None

    try:
        queue = ZhihuTaskQueue(database)
        dedupe_hours = _zhihu_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_events",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的知乎 bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id

        scopes = ["zhihu_read_history", "zhihu_collection", "zhihu_activity"]
        if not profile_slug.strip():
            console.print(
                "  [dim]未传 --profile-slug，扩展会尝试从知乎登录态识别当前用户；"
                "识别失败时只返回浏览记录和收藏夹。[/dim]"
            )
        task_id = queue.enqueue_with_id(
            "bootstrap_events",
            {
                "scopes": scopes,
                "profile_slug": profile_slug.strip(),
                "max_items_per_scope": max(1, max_items),
                "max_collections": max(1, max_collections),
                "profile_update": bool(profile_update),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]知乎事件未拉取: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]知乎事件未拉取: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued Zhihu bootstrap task."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        zhihu_bootstrap_items_to_events,
    )

    empty_counts = {
        "zhihu_read_history": 0,
        "zhihu_collection": 0,
        "zhihu_activity_like": 0,
        "zhihu_activity_favorite": 0,
    }
    if not task_id:
        return [], empty_counts, "skipped"

    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_ZHIHU_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS),
            )
        )

    try:
        database = _get_runtime_database()
    except Exception:
        return [], empty_counts, "skipped"
    if not hasattr(database, "conn"):
        return [], empty_counts, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return [], empty_counts, "timeout"
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], empty_counts, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], empty_counts, "login_required"
        return [], empty_counts, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], empty_counts, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], empty_counts, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    events = zhihu_bootstrap_items_to_events(items)
    scope_counts = dict(empty_counts)
    raw_counts = result.get("scope_counts", {})
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    if not any(scope_counts.values()):
        for event in events:
            event_type = str(event.get("event_type", ""))
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            source = str(metadata.get("import_source", ""))
            if source == "zhihu_bootstrap_read_history":
                scope_counts["zhihu_read_history"] += 1
            elif source == "zhihu_bootstrap_collection":
                scope_counts["zhihu_collection"] += 1
            elif event_type == "like":
                scope_counts["zhihu_activity_like"] += 1
            elif event_type == "favorite":
                scope_counts["zhihu_activity_favorite"] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label


def _event_memory_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    source = str(metadata.get("source_platform") or "").strip()
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    url = str(event.get("url") or "").strip()
    content_id = str(metadata.get("content_id") or "").strip()
    import_source = str(metadata.get("import_source") or "").strip()
    title = str(event.get("title") or "").strip()
    identity = content_id or url or title
    return source, event_type, identity, import_source, url


def _load_existing_event_keys(memory: Any, *, limit: int) -> set[tuple[str, str, str, str, str]]:
    query_events = getattr(memory, "query_events", None)
    if not callable(query_events):
        return set()
    try:
        rows = query_events(limit=limit)
    except Exception:
        return set()

    import json as _json

    keys: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = dict(row)
        metadata = event.get("metadata")
        if isinstance(metadata, str):
            try:
                parsed = _json.loads(metadata)
                event["metadata"] = parsed if isinstance(parsed, dict) else {}
            except _json.JSONDecodeError:
                event["metadata"] = {}
        keys.add(_event_memory_key(event))
    return keys


def _write_events_to_memory(events: list[dict[str, Any]], *, source: str = "") -> tuple[int, int]:
    """Persist collected source events to memory with a lightweight duplicate guard."""
    if not events:
        return 0, 0

    memory = _build_memory_manager()
    existing_keys = _load_existing_event_keys(memory, limit=max(10_000, len(events) * 4))
    batch_keys: set[tuple[str, str, str, str, str]] = set()
    fresh: list[dict[str, Any]] = []
    for event in events:
        key = _event_memory_key(event)
        if key in existing_keys or key in batch_keys:
            continue
        if source:
            metadata = event.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("source_platform", source)
        batch_keys.add(key)
        fresh.append(event)

    async def _propagate() -> None:
        for event in fresh:
            await memory.propagate_event(event)

    asyncio.run(_propagate())
    return len(fresh), len(events) - len(fresh)


def _enqueue_zhihu_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Zhihu plugin search task for the browser extension."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    normalized_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        console.print("  [yellow]知乎搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), "daily_search_budget", 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=budget,
        )
    except Exception as exc:
        console.print(f"  [yellow]知乎搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]知乎搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Zhihu candidates."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(items)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("zhihu_search", count) or count)
    status_label = "ok" if items else "empty"
    return items, {"zhihu_search": count}, status_label


def _enqueue_zhihu_discovery_task(
    task_type: str,
    payload: dict[str, object],
    *,
    daily_budget_key: str,
) -> str | None:
    """Enqueue a non-search Zhihu plugin discovery task."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), daily_budget_key, 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(task_type, payload, daily_budget=budget)
    except Exception as exc:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_discovery_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin Zhihu discovery task and return raw candidates."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"
    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    scope_counts = (
        {str(k): int(v) for k, v in raw_counts.items()} if isinstance(raw_counts, dict) else {}
    )
    return items, scope_counts, "ok" if items else "empty"


def _enqueue_zhihu_discovery_candidates(items: list[dict[str, Any]]) -> tuple[int, list[Any]]:
    """Convert Zhihu search result rows and enqueue them into discovery_candidates."""
    from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
    from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

    contents = zhihu_discovery_items_to_contents(items)
    if not contents:
        return 0, []
    database = _get_runtime_database()
    writes = [
        discovered_content_to_candidate_write(item, source_context=item.source_strategy)
        for item in contents
    ]
    enqueued = int(database.enqueue_discovery_candidates(writes))
    return enqueued, contents


def _enqueue_reddit_discovery_candidates(
    items: list[dict[str, Any]],
    *,
    strategy: str,
) -> tuple[int, list[Any]]:
    """Convert Reddit command result rows and enqueue them into discovery_candidates."""
    from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
    from openbiliclaw.sources.reddit_tasks import reddit_items_to_contents

    contents = reddit_items_to_contents(items, strategy=strategy)
    if not contents:
        return 0, []
    database = _get_runtime_database()
    writes = [
        discovered_content_to_candidate_write(item, source_context=item.source_strategy)
        for item in contents
    ]
    enqueued = int(database.enqueue_discovery_candidates(writes))
    return enqueued, contents


def _enqueue_reddit_bootstrap_task(
    *,
    kick: bool = True,
    profile_update: bool = False,
) -> str | None:
    """Enqueue a Reddit bootstrap_events task for the browser extension."""
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]Reddit 初始化事件未拉取: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_REDDIT_BOOTSTRAP_MAX_ITEMS",
            str(_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    task_id: str | None = None
    try:
        queue = RedditTaskQueue(database)
        dedupe_hours = _reddit_bootstrap_dedupe_hours()
        find_recent = getattr(queue, "find_recent_task", None)
        if dedupe_hours > 0 and callable(find_recent):
            recent = find_recent(
                "bootstrap_events",
                recent_hours=dedupe_hours,
                statuses=("pending", "in_progress", "completed", "failed"),
            )
            if recent is not None:
                task_id = str(recent.get("id", "")).strip()
                if task_id:
                    status = str(recent.get("status", "unknown"))
                    console.print(
                        "  [dim]复用最近的 Reddit bootstrap 任务"
                        f"({status})；需要重新拉取可设 "
                        "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                    )
                    return task_id

        task_id = queue.enqueue_with_id(
            "bootstrap_events",
            {
                "scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
                "max_items_per_scope": max(1, max_items),
                "profile_update": bool(profile_update),
            },
            daily_budget=10,
        )
    except Exception as exc:
        console.print(f"  [yellow]Reddit 初始化事件未拉取: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]Reddit 初始化事件未拉取: 今日任务预算已用完。[/yellow]")
        return None
    if kick:
        _kick_task_dispatcher("reddit")
    return task_id


def _collect_reddit_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and convert a Reddit bootstrap_events task."""
    import json
    import time

    from openbiliclaw.sources.reddit_tasks import (
        REDDIT_BOOTSTRAP_SCOPES,
        RedditTaskQueue,
        reddit_items_to_events,
    )

    empty_counts = {scope: 0 for scope in REDDIT_BOOTSTRAP_SCOPES}
    if not task_id:
        return [], empty_counts, "skipped"
    if max_wait_seconds is None:
        max_wait_seconds = float(
            os.environ.get(
                "OPENBILICLAW_REDDIT_BOOTSTRAP_WAIT_SECONDS",
                str(_DEFAULT_REDDIT_BOOTSTRAP_WAIT_SECONDS),
            )
        )
    try:
        database = _get_runtime_database()
    except Exception:
        return [], empty_counts, "skipped"
    if not hasattr(database, "conn"):
        return [], empty_counts, "skipped"

    queue = RedditTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return [], empty_counts, "timeout"
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], empty_counts, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "reddit_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], empty_counts, "login_required"
        return [], empty_counts, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], empty_counts, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], empty_counts, "failed"
    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    scope_counts = dict(empty_counts)
    if isinstance(raw_counts, dict):
        for key in scope_counts:
            with suppress(Exception):
                scope_counts[key] = int(raw_counts.get(key, 0) or 0)
    events = reddit_items_to_events(items, import_source="reddit_bootstrap_events")
    if not any(scope_counts.values()):
        for event in events:
            metadata = event.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            scope = str(metadata.get("scope", ""))
            if scope in scope_counts:
                scope_counts[scope] += 1
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label


def _reddit_discovery_payload(
    mode: str,
    target: str,
    *,
    limit: int,
) -> tuple[dict[str, object], str]:
    max_items = max(1, int(limit))
    if mode == "search":
        return {"keywords": [target], "max_items_per_keyword": max_items}, "daily_search_budget"
    if mode == "hot":
        return {"subreddit": target or "all", "max_items": max_items}, "daily_hot_budget"
    if mode == "subreddit":
        return (
            {"subreddits": [target], "max_items_per_subreddit": max_items},
            "daily_subreddit_budget",
        )
    if mode == "related":
        return {"related_urls": [target], "max_items_per_seed": max_items}, "daily_related_budget"
    raise ValueError(f"unsupported reddit mode: {mode}")


def _enqueue_reddit_discovery_task(
    task_type: str,
    payload: dict[str, object],
    *,
    daily_budget_key: str,
) -> str | None:
    """Enqueue a Reddit plugin discovery/fetch task for the browser extension."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]Reddit {task_type} 任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "reddit", None), daily_budget_key, 0))
    except Exception:
        budget = 0

    try:
        queue = RedditTaskQueue(database)
        task_id = queue.enqueue_with_id(task_type, payload, daily_budget=budget)
    except Exception as exc:
        console.print(f"  [yellow]Reddit {task_type} 任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print(f"  [yellow]Reddit {task_type} 任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("reddit")
    return task_id


def _collect_reddit_discovery_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin Reddit task and return raw candidates."""
    import json
    import time

    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue

    if not task_id:
        return [], {}, "skipped"
    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = RedditTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "reddit_login_required" or (
            isinstance(debug, dict) and debug.get("login_required") is True
        ):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    scope_counts = (
        {str(k): int(v) for k, v in raw_counts.items()} if isinstance(raw_counts, dict) else {}
    )
    return items, scope_counts, "ok" if items else "empty"


def _is_reddit_extension_backend(backend: str) -> bool:
    return str(backend or "").strip().lower() in {"extension", "openbiliclaw", "plugin"}


def _enqueue_dy_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Douyin plugin search task for the browser extension."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    normalized_keywords = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        console.print("  [yellow]抖音搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]抖音搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "douyin", None), "daily_search_budget", 0))
    except Exception:
        budget = 0

    try:
        queue = DyTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=budget,
        )
    except Exception as exc:
        console.print(f"  [yellow]抖音搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]抖音搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("dy")
    return task_id


def _collect_dy_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Douyin video candidates."""
    import json
    import time

    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = DyTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(videos)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("dy_search", count) or count)
    status_label = "ok" if videos else "empty"
    return videos, {"dy_search": count}, status_label


def _dy_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Douyin bootstrap events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves the
    natural-language ``context`` and tags ``source_platform=douyin``
    so cross-source analysis remains uniform.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "douyin",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


_INIT_IMPORT_IDENTITY_KEYS = (
    "content_id",
    "bvid",
    "note_id",
    "aweme_id",
    "video_id",
    "yt_video_id",
    "post_id",
    "up_name",
)
_INIT_IMPORT_TIMESTAMP_KEYS = ("timestamp", "view_at", "fav_time")


def _init_import_key(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Identify one imported account row: (event type, item, when it happened).

    Returns ``None`` when the row carries no usable identity, which means it
    cannot be recognised on a later import and must be kept.
    """
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    raw_metadata = event.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    identity = ""
    for key in _INIT_IMPORT_IDENTITY_KEYS:
        candidate = str(metadata.get(key, "") or "").strip()
        if candidate:
            identity = candidate
            break
    if not identity:
        identity = str(event.get("url", "") or "").strip()
    if not identity:
        return None
    stamp = ""
    for key in _INIT_IMPORT_TIMESTAMP_KEYS:
        value = metadata.get(key)
        if isinstance(value, (int, float)) and value > 0:
            stamp = str(int(value))
            break
    return (event_type, identity, stamp)


def _drop_events_already_imported(
    database: Any,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Filter init's import down to rows the ledger has not already recorded.

    Re-running ``init`` re-fetches the same account snapshot and used to persist
    all of it again: on a real re-run 56% of the ledger became duplicate rows,
    699 of them keyed to the identical watch timestamp. That is not a second
    view — it is the same behaviour counted twice, inflating every weight
    derived from event counts.

    The key includes the timestamp precisely so a genuine repeat still lands: a
    rewatch has a different ``view_at``, a re-added favourite a different
    ``fav_time``. Rows with no identity at all are kept — unrecognisable is not
    the same as duplicate. Best-effort: if the ledger cannot be read, nothing is
    dropped.
    """
    import logging

    if database is None or not events:
        return events, 0
    logger = logging.getLogger("openbiliclaw.cli")
    seen: set[tuple[str, str, str]] = set()
    try:
        for row in database.conn.execute("SELECT event_type, url, metadata FROM events"):
            metadata = row["metadata"]
            if isinstance(metadata, str):
                with suppress(Exception):
                    metadata = json.loads(metadata)
            key = _init_import_key(
                {
                    "event_type": row["event_type"],
                    "url": row["url"],
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
            if key is not None:
                seen.add(key)
    except Exception:
        logger.debug("init import dedup lookup failed; importing everything", exc_info=True)
        return events, 0

    kept: list[dict[str, Any]] = []
    skipped = 0
    for event in events:
        key = _init_import_key(event)
        if key is not None and key in seen:
            skipped += 1
            continue
        if key is not None:
            seen.add(key)
        kept.append(event)
    return kept, skipped


def _favorites_to_history_rows(favorites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn fetched favourites into individual profile-history rows.

    ``event_type`` is the load-bearing field: it earns these rows the
    strong-signal weight and the reserved share of ProfileBuilder's sample, and
    makes their context line read "收藏了" instead of "看了".
    """
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI

    rows: list[dict[str, Any]] = []
    for fav in favorites:
        title = str(fav.get("title", "")).strip()
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "author_name": str(fav.get("upper", "")).strip(),
                "event_type": "favorite",
                "source_platform": SOURCE_BILIBILI,
                "fav_time": fav.get("fav_time"),
                "duration": fav.get("duration"),
            }
        )
    return rows


def _build_bilibili_init_events(
    *,
    history: list[dict[str, Any]],
    favorites_data: list[dict[str, Any]],
    following_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Turn stage-1 Bilibili data into unified events for the ledger.

    Extracted from ``run_guided_init`` so the identity and metadata each event
    carries can be tested directly — that identity is what decides whether the
    recommender knows the user already consumed this content.
    """
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI, build_event

    events = [_history_item_to_event(item) for item in history]
    for fav in favorites_data:
        folder = str(fav.get("folder", "")).strip()
        upper = str(fav.get("upper", "")).strip()
        # Identity is what makes a favourite deduplicable. Without a bvid these
        # events carried no url and no content_id, so seen_items never learned
        # about them and a video the user had explicitly saved could come back
        # as a fresh recommendation.
        fav_bvid = str(fav.get("bvid", "") or "").strip()
        fav_metadata: dict[str, Any] = {
            "folder": folder,
            "upper": upper,
        }
        if fav_bvid:
            fav_metadata["bvid"] = fav_bvid
            fav_metadata["content_id"] = fav_bvid
        for source_field, target in (
            ("fav_time", "fav_time"),
            ("pubtime", "pubtime"),
            ("play_count", "play_count"),
        ):
            value = fav.get(source_field)
            if isinstance(value, (int, float)) and value > 0:
                fav_metadata[target] = int(value)
        intro = str(fav.get("intro", "") or "").strip()
        if intro and intro != "-":
            fav_metadata["intro"] = intro[:200]
        duration = fav.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            fav_metadata["video_duration_seconds"] = float(duration)
        events.append(
            build_event(
                event_type="favorite",
                source_platform=SOURCE_BILIBILI,
                title=str(fav.get("title", "")),
                url=f"https://www.bilibili.com/video/{fav_bvid}" if fav_bvid else "",
                author=upper,
                metadata=fav_metadata,
            )
        )
    for user in following_data:
        sign = str(user.get("sign", "")).strip()
        name = str(user.get("name", ""))
        events.append(
            build_event(
                event_type="follow",
                source_platform=SOURCE_BILIBILI,
                title=name,
                author=name,
                context=(
                    f"在 B 站关注了《{name}》,签名:{sign}" if sign else f"在 B 站关注了《{name}》"
                ),
                metadata={
                    "up_name": name,
                    "sign": sign,
                },
            )
        )
    return events


def _xhs_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert XHS bootstrap events into profile-builder history rows.

    Preserves the natural-language ``context`` field from the source
    event so downstream consumers that opt into context-aware
    summarisation can use it. Profile_builder's current
    ``_summarize_history`` doesn't read ``context``, but keeping it
    intact means the data flows uniformly across sources without
    blocking future analyzer enhancements.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                # v0.3.22+: preserve natural-language context so the
                # history list carries the same single-source-of-truth
                # description as the underlying event.
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "xiaohongshu",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


def _yt_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert YouTube bootstrap events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves natural-language
    ``context`` and tags ``source_platform=youtube`` for cross-source analysis.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "youtube",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


def _x_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert X (Twitter) init events into profile-builder history rows.

    Mirror of ``_xhs_events_to_history_items`` — preserves natural-language
    ``context`` and tags ``source_platform=twitter``. Keeps the profile
    builder fed when X is the only (or one of few) selected init sources.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "twitter",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


def _zhihu_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Zhihu bootstrap events into profile-builder history rows."""
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "zhihu",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


def _reddit_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Reddit bootstrap events into profile-builder history rows."""
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": str(metadata.get("author", "")).strip(),
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "reddit",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


def _bangumi_events_to_history_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Bangumi public-collection events into profile history rows."""
    rows: list[dict[str, Any]] = []
    for event in events:
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        rows.append(
            {
                "title": str(event.get("title", "")).strip(),
                "url": str(event.get("url", "")).strip(),
                "author": "",
                "event_type": str(event.get("event_type", "")).strip(),
                "context": str(event.get("context", "")).strip(),
                "metadata": metadata,
                "source_platform": "bangumi",
            }
        )
    return [row for row in rows if row.get("title") or row.get("url")]


@app.command("setup-embedding")
def setup_embedding() -> None:
    """配置本地 Ollama 作为 embedding 兜底服务（可选）.

    init 时已经问过；如果当时没启用、之后想加上，跑这条命令再走一次引导。
    """
    _print_page_title("配置本地 embedding", "Ollama + bge-m3")
    from openbiliclaw.config import load_config_with_diagnostics

    config, _ = load_config_with_diagnostics()
    _interactive_embedding_setup(config.llm.default_provider)


@app.command()
def cost(
    days: int = typer.Option(7, "--days", min=1, max=90, help="统计窗口(天)"),
    by: str = typer.Option(
        "all",
        "--by",
        help="单维度展开: all (默认 / 三表全显) / day / provider / caller",
    ),
) -> None:
    """显示本机 LLM 调用花费(按天 + 按 provider/model + 按 caller 模块)。

    数据来源:每次成功的 LLM 调用都会写一条到 ``llm_usage`` 表(v0.3.26+)。
    费用按 ``llm.pricing`` 里的官方单价估算,允许 ±20% 误差。本地 Ollama
    调用单价 0,只统计调用次数。

    ``--by caller`` 显示按模块(discovery / recommendation / soul / api 等)
    拆分的占比,这是排查"钱花在哪一层"最有用的视图。
    """
    _print_page_title("LLM 调用花费", f"最近 {days} 天")
    _ensure_runtime_database_healthy()
    db = _get_runtime_database()

    daily = db.query_llm_usage_by_day(days=days)
    by_provider = db.query_llm_usage_by_provider(days=days)
    by_caller = db.query_llm_usage_by_caller(days=days)
    total = db.query_llm_usage_total(days=days)

    if total["calls"] == 0:
        _print_status_panel(
            "info",
            "暂无数据",
            "这台机器最近没记录到 LLM 调用。\n"
            "如果你刚升级到 v0.3.26+,旧数据不会回填——继续运行一段时间后再来查。",
        )
        return

    show_all = by == "all"

    if show_all or by == "day":
        daily_table = Table(show_header=True, header_style="bold cyan", title="按天 (cost by day)")
        daily_table.add_column("日期", no_wrap=True)
        daily_table.add_column("调用数", justify="right")
        daily_table.add_column("input tokens", justify="right")
        daily_table.add_column("output tokens", justify="right")
        daily_table.add_column("¥ 估算", justify="right", style="bold yellow")
        for row in daily:
            daily_table.add_row(
                str(row["day"]),
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                f"¥{row['cost_cny']:.4f}",
            )
        console.print(daily_table)
        console.print()

    total_cost = total["cost_cny"] or 1e-9

    if show_all or by == "provider":
        provider_table = Table(
            show_header=True,
            header_style="bold magenta",
            title="按 Provider/Model (cost by provider)",
        )
        provider_table.add_column("Provider", no_wrap=True)
        provider_table.add_column("Model")
        provider_table.add_column("调用数", justify="right")
        provider_table.add_column("input", justify="right")
        provider_table.add_column("output", justify="right")
        provider_table.add_column("¥ 占比", justify="right", style="bold yellow")
        for row in by_provider:
            share = row["cost_cny"] / total_cost * 100
            provider_table.add_row(
                row["provider"] or "?",
                row["model"] or "(default)",
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
            )
        console.print(provider_table)
        console.print()

    if show_all or by == "caller":
        caller_table = Table(
            show_header=True,
            header_style="bold green",
            title="按模块 (cost by caller — 钱花在哪一层 / cache 命中率)",
        )
        caller_table.add_column("Caller (模块.动作)", no_wrap=True)
        caller_table.add_column("调用数", justify="right")
        caller_table.add_column("input", justify="right")
        caller_table.add_column("output", justify="right")
        # v0.3.28+: cache hit rate per caller. Low hit rate (red) on a
        # high-cost caller is the smoking gun for prompt-prefix
        # instability — that's where to focus prompt-builder audits.
        caller_table.add_column("cache 命中", justify="right")
        caller_table.add_column("¥ 占比", justify="right", style="bold yellow")
        for row in by_caller:
            share = row["cost_cny"] / total_cost * 100
            prompt_tok = int(row["prompt_tokens"])
            cached_tok = int(row.get("cached_input_tokens", 0) or 0)
            if prompt_tok > 0 and cached_tok > 0:
                hit_pct = cached_tok / prompt_tok * 100
                if hit_pct < 30:
                    cache_cell = f"[red]{hit_pct:.0f}%[/red]"
                elif hit_pct < 60:
                    cache_cell = f"[yellow]{hit_pct:.0f}%[/yellow]"
                else:
                    cache_cell = f"[green]{hit_pct:.0f}%[/green]"
                cache_cell += f" ({cached_tok:,}/{prompt_tok:,})"
            else:
                cache_cell = "[dim]—[/dim]"
            caller_table.add_row(
                row["caller"] or "[dim](untagged)[/dim]",
                f"{row['calls']:,}",
                f"{row['prompt_tokens']:,}",
                f"{row['completion_tokens']:,}",
                cache_cell,
                f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
            )
        console.print(caller_table)
        console.print()

    avg_per_day = total["cost_cny"] / max(1, len(daily))
    total_prompt = int(total["prompt_tokens"])
    total_cached = int(total.get("cached_input_tokens", 0) or 0)
    cache_summary = ""
    if total_prompt > 0 and total_cached > 0:
        overall_hit = total_cached / total_prompt * 100
        cache_summary = (
            f"\ncache 命中: [bold green]{overall_hit:.1f}%[/bold green] "
            f"({total_cached:,}/{total_prompt:,} input tokens served from cache)"
        )
    elif total_prompt > 0:
        cache_summary = "\ncache 命中: [dim]0%(还没命中或 provider 不上报 cache 字段)[/dim]"
    _print_status_panel(
        "info",
        f"近 {days} 天合计",
        f"总调用 [bold]{total['calls']:,}[/bold] 次, "
        f"总 token [bold]{total['total_tokens']:,}[/bold] "
        f"(input {total['prompt_tokens']:,} + output {total['completion_tokens']:,}), "
        f"估算消耗 [bold yellow]¥{total['cost_cny']:.4f}[/bold yellow]"
        f"{cache_summary}\n"
        f"按记录到的天数平均 ≈ ¥{avg_per_day:.4f}/天 ≈ "
        f"¥{avg_per_day * 30:.2f}/月\n"
        "[dim]（费率为公开渠道估算,与 provider 实际账单可能差 ±20%。"
        "tail daemon 日志可以看每次调用的实时 [llm-cost] INFO 行,"
        "cache 命中率 < 30% 的 caller 在 by-caller 表里会标红。）[/dim]",
    )


def _fetch_pending_confirmation_snapshot() -> dict[str, Any]:
    """Read the canonical pending-confirmation snapshot from the local API."""
    import httpx

    from openbiliclaw.config import load_config

    port = load_config().api.port
    url = f"http://127.0.0.1:{port}/api/chat/pending-confirmations"
    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"无法读取 {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("待聊确认端点返回了无效对象。")
    raw_items = payload.get("items", [])
    raw_count = payload.get("count", 0)
    if (
        not isinstance(raw_items, list)
        or not isinstance(raw_count, int)
        or isinstance(raw_count, bool)
    ):
        raise RuntimeError("待聊确认端点返回了无效列表。")
    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        raise RuntimeError("待聊确认端点返回了无效条目。")
    return {"count": raw_count, "items": items}


@app.command()
def questions() -> None:
    """只读查看当前待聊的假设与疑惑。"""
    _require_runtime_config()
    try:
        snapshot = _fetch_pending_confirmation_snapshot()
    except RuntimeError as exc:
        _print_status_panel(
            "error",
            "无法读取待聊确认",
            f"{exc}\n请确认本地 API 服务已启动。",
        )
        raise typer.Exit(code=1) from exc

    items = snapshot["items"]
    _print_page_title("待聊确认", "只读列表 · 与本地 API 同步")
    if not items:
        _print_status_panel("info", "暂无待聊确认", "当前没有高优先级的假设或疑惑。")
        return

    table = Table(show_header=True, header_style="bold cyan", title="待确认的对话话题")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("话题")
    table.add_column("置信度", justify="right", no_wrap=True)
    table.add_column("依据")
    table.add_column("Ref", no_wrap=True)
    for index, item in enumerate(items, start=1):
        kind = "疑惑" if str(item.get("kind", "")) == "confusion" else "猜测"
        try:
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        raw_evidence = item.get("evidence_refs", [])
        evidence = (
            "、".join(str(value) for value in raw_evidence)
            if isinstance(raw_evidence, list)
            else ""
        )
        table.add_row(
            str(index),
            kind,
            Text(str(item.get("title", "") or "（未命名）")),
            f"{confidence:.0%}",
            Text(evidence or "—"),
            Text(str(item.get("ref", ""))),
        )
    console.print(table)
    _print_status_panel(
        "info",
        f"共 {snapshot['count']} 条",
        "此命令只读；请在插件或桌面端的对话确认入口继续处理。",
    )


@app.command()
def ledger(
    days: int = typer.Option(30, "--days", min=1, max=365, help="统计窗口(天)"),
    line: bool = typer.Option(False, "--line", help="逐行明细模式(默认按写点聚合计数)"),
    write_point: str = typer.Option(
        "", "--write-point", help="只看某个写点(如 dialogue_preference_overwrite)"
    ),
    limit: int = typer.Option(200, "--limit", min=1, max=2000, help="逐行模式最多返回行数"),
) -> None:
    """查看画像更新台账(profile_update_ledger)。

    每个画像写点(对话学习 / 反馈批 / 12h 整理 / init 建像 / 管线各层 /
    推测确认 / 觉察同步)在动作结束后追加一行,含 ``outcome``(success|failed)、
    before/after 摘要与 source_refs。台账为只追加审计底座(v0.3.174+)。

    默认按写点聚合显示条数与成功率;``--line`` 显示逐行明细。
    """
    _print_page_title("画像更新台账", f"最近 {days} 天")
    _ensure_runtime_database_healthy()
    db = _get_runtime_database()

    rows = db.query_profile_ledger(days=days, write_point=write_point, limit=limit)
    if not rows:
        _print_status_panel(
            "info",
            "暂无数据",
            "这台机器最近没有画像写入记录。\n台账从 v0.3.174+ 开始记录,旧的画像更新不会回填。",
        )
        return

    if line:
        line_table = Table(
            show_header=True, header_style="bold cyan", title="逐行明细 (ledger --line)"
        )
        line_table.add_column("时间", no_wrap=True)
        line_table.add_column("写点", no_wrap=True)
        line_table.add_column("来源")
        line_table.add_column("结果", justify="center")
        line_table.add_column("turn_id", no_wrap=True)
        line_table.add_column("source_refs")
        for row in rows:
            outcome = str(row["outcome"])
            outcome_cell = (
                f"[green]{outcome}[/green]" if outcome == "success" else f"[red]{outcome}[/red]"
            )
            refs = row.get("source_refs") or []
            refs_text = ", ".join(str(ref) for ref in refs)[:60]
            line_table.add_row(
                str(row["timestamp"]),
                str(row["write_point"]),
                str(row["source"]),
                outcome_cell,
                str(row["turn_id"]) or "[dim]—[/dim]",
                refs_text or "[dim]—[/dim]",
            )
        console.print(line_table)
        console.print()
        _print_status_panel(
            "info",
            f"近 {days} 天",
            f"共 [bold]{len(rows)}[/bold] 行(最多显示 {limit})。",
        )
        return

    # Aggregate mode: count per write_point with success/failed split.
    agg: dict[str, dict[str, int]] = {}
    for row in rows:
        wp = str(row["write_point"])
        bucket = agg.setdefault(wp, {"success": 0, "failed": 0})
        outcome = str(row["outcome"])
        bucket[outcome if outcome in bucket else "success"] += 1
    agg_table = Table(show_header=True, header_style="bold green", title="按写点聚合 (ledger)")
    agg_table.add_column("写点", no_wrap=True)
    agg_table.add_column("成功", justify="right")
    agg_table.add_column("失败", justify="right")
    agg_table.add_column("合计", justify="right", style="bold yellow")
    for wp in sorted(agg):
        bucket = agg[wp]
        total = bucket["success"] + bucket["failed"]
        failed_cell = (
            f"[red]{bucket['failed']}[/red]" if bucket["failed"] else str(bucket["failed"])
        )
        agg_table.add_row(wp, str(bucket["success"]), failed_cell, str(total))
    console.print(agg_table)
    console.print()
    _print_status_panel(
        "info",
        f"近 {days} 天",
        f"共 [bold]{len(rows)}[/bold] 行,{len(agg)} 个写点。"
        "\n[dim]用 --line 看逐行明细,--write-point 过滤单个写点。[/dim]",
    )


@app.command("logs-prune")
def logs_prune(
    truncate_mb: int = typer.Option(
        200,
        "--truncate-mb",
        min=0,
        help="单个 unmanaged 日志文件超过此 MB 数则截断为 0 字节(0 = 关闭)",
    ),
    max_age_days: int = typer.Option(
        30,
        "--max-age-days",
        min=0,
        help="超过此天数的 unmanaged 日志文件直接删除(0 = 关闭)",
    ),
    aggregate_budget_mb: int = typer.Option(
        500,
        "--aggregate-budget-mb",
        min=0,
        help="logs/ 目录(含 unmanaged + managed)总磁盘预算 MB,超出时按 mtime 从旧到新删 unmanaged",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="实际执行删除/截断;默认是 dry-run 模式只列出会改什么",
    ),
) -> None:
    """手动 prune logs/ 目录的日志文件(默认 dry-run)。

    daemon 启动时已经会按 config 自动跑这套清理(v0.3.30+),这个命令是
    手动触发用的 —— 比如 daemon 没在运行 / 想查看会删什么 / 临时换一组
    更激进或更保守的阈值。
    """
    import time as _time

    from openbiliclaw.config import load_config
    from openbiliclaw.logging_setup import _is_managed_log

    config = load_config()
    log_dir = config.logging.directory_path
    managed = config.logging.filename

    _print_page_title("LLM 日志清理 (logs prune)", str(log_dir))
    if not log_dir.exists():
        _print_status_panel("warning", "日志目录不存在", f"{log_dir} 还没创建。")
        return

    truncate_bytes = truncate_mb * 1024 * 1024
    age_cutoff = _time.time() - max_age_days * 86400 if max_age_days > 0 else 0.0
    budget_bytes = aggregate_budget_mb * 1024 * 1024

    actions: list[tuple[str, str, int]] = []  # (action, path, size)
    total = 0
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        is_managed = _is_managed_log(path, managed)
        tag = "managed" if is_managed else "unmanaged"
        if is_managed:
            actions.append(("keep", f"{path.name}  [{tag}]", st.st_size))
            continue
        if truncate_mb > 0 and st.st_size >= truncate_bytes:
            actions.append(
                (
                    "truncate",
                    f"{path.name}  [{tag}, > {truncate_mb} MB]",
                    st.st_size,
                )
            )
            continue
        if max_age_days > 0 and st.st_mtime < age_cutoff:
            age_days = (_time.time() - st.st_mtime) / 86400
            actions.append(
                (
                    "delete (age)",
                    f"{path.name}  [{tag}, {age_days:.0f} days old]",
                    st.st_size,
                )
            )
            continue
        actions.append(("keep", f"{path.name}  [{tag}]", st.st_size))

    # Aggregate-budget pass: simulate evicting oldest unmanaged 'keep' rows
    if aggregate_budget_mb > 0 and total > budget_bytes:
        # Re-sort the not-yet-doomed unmanaged ones by mtime
        unmanaged_keep: list[tuple[Path, float, int, int]] = []
        for i, (action, label, size) in enumerate(actions):
            if action != "keep" or "[managed]" in label:
                continue
            name = label.split("  ")[0]
            try:
                st = (log_dir / name).stat()
            except OSError:
                continue
            unmanaged_keep.append((log_dir / name, st.st_mtime, size, i))
        unmanaged_keep.sort(key=lambda x: x[1])
        running = total
        for path, _mt, size, idx in unmanaged_keep:
            if running <= budget_bytes:
                break
            actions[idx] = (
                "delete (budget)",
                f"{path.name}  [unmanaged, oldest, evict to fit {aggregate_budget_mb} MB]",
                size,
            )
            running -= size

    table = Table(
        show_header=True,
        header_style="bold cyan",
        title=f"Plan ({'APPLY' if apply else 'DRY-RUN'})",
    )
    table.add_column("Action", no_wrap=True)
    table.add_column("File", overflow="fold")
    table.add_column("Size", justify="right")
    for action, label, size in actions:
        size_h = f"{size / (1024 * 1024):.1f} MB"
        style = "green" if action == "keep" else "yellow" if action == "truncate" else "red"
        table.add_row(f"[{style}]{action}[/{style}]", label, size_h)
    console.print(table)

    will_change = [a for a in actions if a[0] != "keep"]
    freed = sum(s for action, _, s in actions if action.startswith("delete")) + sum(
        s - 1
        for action, _, s in actions
        if action == "truncate"  # leaves ~1 byte stub
    )
    console.print(
        f"\n会释放约 [bold]{freed / (1024 * 1024):.1f} MB[/bold] 磁盘"
        f" / 影响 [bold]{len(will_change)}[/bold] 个文件"
    )

    if not apply:
        console.print("\n[yellow]这是 dry-run。加上 --apply 才会真的改文件。[/yellow]")
        return

    # Apply
    import time as _time2

    actually_freed = 0
    for action, label, size in actions:
        name = label.split("  ")[0]
        path = log_dir / name
        if action == "truncate":
            try:
                with path.open("w", encoding="utf-8") as f:
                    f.write(
                        f"# truncated by `openbiliclaw logs-prune` "
                        f"{_time2.strftime('%Y-%m-%d %H:%M:%S')} — was "
                        f"{size / (1024 * 1024):.0f} MB\n"
                    )
                actually_freed += size
            except OSError as exc:
                console.print(f"[red]✗ truncate {path}: {exc}[/red]")
        elif action.startswith("delete"):
            try:
                path.unlink()
                actually_freed += size
            except OSError as exc:
                console.print(f"[red]✗ unlink {path}: {exc}[/red]")
    freed_mb = actually_freed / (1024 * 1024)
    console.print(f"\n[bold green]✓ Applied — actually freed {freed_mb:.1f} MB[/bold green]")


@app.command()
def start(
    host: str = typer.Option("", "--host", help="API 监听地址（默认读 config.toml [api].host）"),
    port: int = typer.Option(
        0, "--port", min=0, max=65535, help="API 监听端口（默认读 config.toml [api].port）"
    ),
) -> None:
    """启动 OpenBiliClaw Agent."""
    from openbiliclaw.config import load_config

    cfg = load_config()
    effective_host = host if host else cfg.api.host
    effective_port = port if port else cfg.api.port
    _print_page_title("启动 OpenBiliClaw", "本地 API 服务")
    _ensure_runtime_database_healthy()
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动本地后端，当前监听 {effective_host}:{effective_port}。",
    )
    if _guided_init_completed_best_effort() is False:
        hint_host = "127.0.0.1" if effective_host == "0.0.0.0" else effective_host  # noqa: S104
        _print_status_panel(
            "warning",
            "还没初始化",
            f"启动后打开 http://{hint_host}:{effective_port}/setup/ 完成引导初始化；"
            "无浏览器环境改用 `openbiliclaw init`。",
        )
    _warn_if_pause_on_disconnect_requires_presence()
    if cfg.api.auth.enabled:
        _print_status_panel(
            "info",
            "🔒 访问控制",
            "局域网/远程访问已启用密码登录（本机访问免登录）。",
        )
        if cfg.api.auth.trust_loopback and not cfg.api.auth.trusted_proxies:
            _print_status_panel(
                "warning",
                "反向代理提醒",
                "如部署在同机反向代理后，请配置 [api.auth].trusted_proxies"
                "（并确保代理覆盖而非透传客户端转发头），或让代理自行鉴权，"
                "否则远程请求可能被误判为本机而绕过密码。",
            )
    _maybe_create_runtime_database_backup()
    _preflight_loopback_ollama(cfg)
    _self_heal_autostart_registration(cfg)
    _run_api_server(host=effective_host, port=effective_port)


def _bump_auth_epoch(cfg: Any) -> bool:
    """Bump the revocation epoch in the runtime DB (immediate logout-all)."""
    from openbiliclaw.storage.database import Database

    db = Database(cfg.data_path / "openbiliclaw.db")
    try:
        db.initialize()
        db.bump_auth_epoch()
        return True
    except Exception:
        return False
    finally:
        with suppress(Exception):
            db.close()


def _rebase_auth_fingerprint(cfg: Any) -> None:
    """Re-store the password fingerprint under cfg's CURRENT signing secret.

    Called after ``--rotate-secret`` so the next startup reconcile sees the
    fingerprint it would itself compute (under the new secret) and does NOT
    perform a redundant epoch bump on top of the one we already did. Best-effort:
    if the DB is unwritable we simply leave the stale fingerprint, which only
    costs one harmless extra reconcile bump on restart. See ``set_password_fingerprint``.
    """
    from openbiliclaw.auth_core import password_fingerprint
    from openbiliclaw.config import get_auth_plain_password
    from openbiliclaw.storage.database import Database

    auth = cfg.api.auth
    if not (auth.password_hash.strip() and auth.session_secret.strip()):
        return
    fingerprint = password_fingerprint(
        auth.session_secret,
        plain=get_auth_plain_password(),
        password_hash=auth.password_hash,
    )
    db = Database(cfg.data_path / "openbiliclaw.db")
    try:
        db.initialize()
        db.set_password_fingerprint(fingerprint)
    except Exception:
        # Best-effort: a stale fingerprint only costs one harmless reconcile bump.
        pass
    finally:
        with suppress(Exception):
            db.close()


def _autostart_reason_message(reason: str) -> str:
    if reason == "unsupported_docker_runtime":
        return "当前在 Docker / 容器环境中，不支持注册桌面登录自启动。"
    if reason == "unsupported_platform":
        return "当前平台暂不支持开机自启动。"
    if reason == "env_managed":
        return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。"
    if reason == "shadowed":
        return "config.local.toml 正在覆盖 [autostart].enabled，config.toml 修改不会生效。"
    if reason == "registration_failed":
        return "系统自启动注册失败，config 已回滚。"
    if reason == "unregister_failed":
        return "系统自启动注销失败，config 未修改。"
    return "无法完成开机自启动操作。"


def _autostart_status_rows(cfg: Any) -> list[tuple[str, str]]:
    from openbiliclaw.runtime import autostart

    state = autostart.status()
    enabled = bool(getattr(getattr(cfg, "autostart", None), "enabled", False))
    manage_ollama = bool(getattr(getattr(cfg, "autostart", None), "manage_ollama", True))
    return [
        ("配置", "开启" if enabled else "关闭"),
        ("系统注册", "已注册" if state.registered else "未注册"),
        ("支持状态", "支持" if state.supported else "不支持"),
        ("平台", state.platform),
        ("机制", state.mechanism),
        ("原因", state.reason),
        ("Ollama 预检", "开启" if manage_ollama else "关闭"),
    ]


def _print_autostart_status(cfg: Any) -> None:
    _print_page_title("开机自启动", "登录系统时自动拉起 OpenBiliClaw 后端")
    _print_key_value_table("自启动状态", _autostart_status_rows(cfg))


def _format_autostart_config_status(cfg: Any) -> str:
    from openbiliclaw.runtime import autostart

    try:
        state = autostart.status()
    except Exception:
        return "开启" if bool(getattr(cfg.autostart, "enabled", False)) else "关闭"
    enabled = "开启" if bool(getattr(cfg.autostart, "enabled", False)) else "关闭"
    registered = "已注册" if state.registered else "未注册"
    return f"{enabled}（{registered}，{state.mechanism}）"


def _autostart_manager_or_exit() -> Any:
    from openbiliclaw.runtime import autostart

    manager = autostart.get_manager()
    if manager is not None:
        return manager
    reason = autostart.status().reason
    _print_status_panel("error", "当前环境不支持开机自启动", _autostart_reason_message(reason))
    raise typer.Exit(code=1)


def _save_autostart_authoritative(cfg: Any) -> None:
    from openbiliclaw.config import save_config

    save_config(cfg, autostart_authoritative=True)


def _restore_autostart_enabled(cfg: Any, enabled: bool) -> None:
    cfg.autostart.enabled = enabled
    with suppress(Exception):
        _save_autostart_authoritative(cfg)


def _register_autostart_best_effort(manager: Any, cfg: Any, should_register: bool) -> None:
    if should_register:
        with suppress(Exception):
            manager.register(cfg)


@autostart_app.command("status")
def autostart_status() -> None:
    """显示开机自启动状态。"""
    from openbiliclaw.config import load_config

    _print_autostart_status(load_config())


@autostart_app.command("enable")
def autostart_enable() -> None:
    """开启登录系统后自动拉起后端。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.autostart.guards import (
        active_env_managed_inputs,
        autostart_shadowed,
    )

    cfg = load_config()
    manager = _autostart_manager_or_exit()
    managed = active_env_managed_inputs(cfg)
    if managed:
        _print_status_panel(
            "error",
            "检测到环境变量配置，无法开启自启动",
            f"{_autostart_reason_message('env_managed')}\n命中：{', '.join(managed)}",
        )
        raise typer.Exit(code=1)

    previous_enabled = bool(cfg.autostart.enabled)
    cfg.autostart.enabled = True
    try:
        _save_autostart_authoritative(cfg)
    except Exception as exc:
        cfg.autostart.enabled = previous_enabled
        _print_status_panel("error", "配置保存失败", str(exc))
        raise typer.Exit(code=1) from exc

    if autostart_shadowed(True):
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置被覆盖", _autostart_reason_message("shadowed"))
        raise typer.Exit(code=1)

    try:
        manager.register(cfg)
    except Exception as exc:
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel(
            "error",
            "自启动注册失败",
            f"{_autostart_reason_message('registration_failed')}\n{exc}",
        )
        raise typer.Exit(code=1) from exc

    _print_status_panel(
        "success",
        "已开启开机自启动",
        "下次登录系统时会拉起 OpenBiliClaw 后端；当前进程不会被启停。",
    )
    _print_autostart_status(cfg)


@autostart_app.command("disable")
def autostart_disable() -> None:
    """关闭登录系统后自动拉起后端。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.autostart.guards import autostart_shadowed

    cfg = load_config()
    manager = _autostart_manager_or_exit()
    previous_enabled = bool(cfg.autostart.enabled)
    was_registered = bool(manager.is_registered())

    try:
        manager.unregister()
    except Exception as exc:
        _print_status_panel(
            "error",
            "自启动注销失败",
            f"{_autostart_reason_message('unregister_failed')}\n{exc}",
        )
        raise typer.Exit(code=1) from exc

    cfg.autostart.enabled = False
    try:
        _save_autostart_authoritative(cfg)
    except Exception as exc:
        cfg.autostart.enabled = previous_enabled
        _register_autostart_best_effort(manager, cfg, was_registered)
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置保存失败", str(exc))
        raise typer.Exit(code=1) from exc

    if autostart_shadowed(False):
        cfg.autostart.enabled = previous_enabled
        _register_autostart_best_effort(manager, cfg, was_registered)
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置被覆盖", _autostart_reason_message("shadowed"))
        raise typer.Exit(code=1)

    _print_status_panel(
        "success",
        "已关闭开机自启动",
        "系统登录项已移除；当前后端进程不会被停止。",
    )
    _print_autostart_status(cfg)


@app.command("set-password")
def set_password(
    disable: bool = typer.Option(False, "--disable", help="关闭密码门禁"),
    logout_all: bool = typer.Option(
        False, "--logout-all", help="使所有设备的登录态立即失效（不改密码/密钥）"
    ),
    rotate_secret: bool = typer.Option(
        False, "--rotate-secret", help="轮换会话签名密钥（最强撤销，需重启后端生效）"
    ),
) -> None:
    """设置 / 修改局域网访问密码（或关闭门禁 / 登出所有设备）。"""
    import secrets as _secrets

    from openbiliclaw.auth_core import hash_password
    from openbiliclaw.config import load_config, save_config

    cfg = load_config()

    if logout_all:
        # DB-only revocation — always effective, independent of env/config source.
        ok = _bump_auth_epoch(cfg)
        _print_status_panel(
            "success" if ok else "error",
            "已登出所有设备" if ok else "操作失败",
            "所有设备需重新登录。"
            if ok
            else "无法访问运行库、未能撤销，请确认 data 目录可写后重试。",
        )
        if not ok:
            raise typer.Exit(code=1)
        return

    # Config-writing paths below all call save_config(cfg), which writes the WHOLE
    # [api.auth] block. cfg came from load_config(), where env vars take precedence
    # over config.toml — so ANY auth env override would be (a) re-applied on restart
    # (the file edit silently lost) and (b) baked into config.toml as a literal,
    # leaving a stale value behind once the env var is later removed (this could
    # quietly shift the trust boundary / session lifetime). Refuse loudly on the
    # full override surface — not just the password — and tell the user to manage
    # via env instead (review r3#2). `--logout-all` returned above, so it stays
    # usable for an emergency revoke even while env-managed.
    from openbiliclaw.config import API_AUTH_ENV_VARS

    _auth_env = [name for name in API_AUTH_ENV_VARS if (os.environ.get(name) or "").strip()]
    if _auth_env:
        _print_status_panel(
            "error",
            "检测到环境变量覆盖，config 修改不会生效",
            f"已设置 {', '.join(_auth_env)}；load_config 中环境变量优先于 config.toml，"
            "改写文件重启后仍会用旧的环境变量值。请改这些环境变量并重启后端；"
            "如只想立即失效现有登录态，用 `openbiliclaw set-password --logout-all`。",
        )
        raise typer.Exit(code=1)

    # config.local.toml is merged OVER config.toml (local wins). If it pins any of
    # the credential fields set-password writes, our config.toml edit silently
    # reverts on restart — refuse loudly rather than report a false success (r9).
    from openbiliclaw.config import config_local_auth_keys

    _local_keys = sorted(
        config_local_auth_keys() & {"password", "password_hash", "enabled", "session_secret"}
    )
    if _local_keys:
        _print_status_panel(
            "error",
            "config.local.toml 覆盖了 [api.auth] 字段，config.toml 修改不会生效",
            f"config.local.toml 中设置了 {', '.join(_local_keys)}；它会盖过 config.toml，"
            "改写后者重启后仍会被覆盖。请改 config.local.toml 并重启后端；"
            "如只想立即失效现有登录态，用 `openbiliclaw set-password --logout-all`。",
        )
        raise typer.Exit(code=1)

    if disable:
        cfg.api.auth.enabled = False
        save_config(cfg)
        _print_status_panel("success", "已关闭密码门禁", "重启后端 (openbiliclaw start) 后生效。")
        return

    if rotate_secret:
        cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
        save_config(cfg)
        revoked = _bump_auth_epoch(cfg)
        if not revoked:
            _print_status_panel(
                "error",
                "密钥已轮换，但未能立即撤销",
                "新密钥已写入 config，但运行库不可写、现有登录态未即时失效。"
                "请重启后端使其生效，或修复 data 目录后重试。",
            )
            raise typer.Exit(code=1)
        # Re-base the stored fingerprint under the NEW secret so the next restart's
        # reconcile doesn't perform a redundant epoch bump on top of this one.
        _rebase_auth_fingerprint(cfg)
        _print_status_panel(
            "success",
            "已轮换会话密钥",
            "所有设备需重新登录；重启后端使新密钥完全生效。",
        )
        return

    if not _is_interactive_terminal():
        _print_status_panel(
            "error",
            "无法设置密码",
            "请在交互式终端运行，或用 OPENBILICLAW_API_AUTH_PASSWORD 环境变量配置。",
        )
        raise typer.Exit(code=1)

    password = str(
        typer.prompt("设置访问密码", hide_input=True, confirmation_prompt=True) or ""
    ).strip()
    if not password:
        _print_status_panel("error", "密码为空", "未做更改。")
        raise typer.Exit(code=1)

    cfg.api.auth.password_hash = hash_password(password)
    cfg.api.auth.enabled = True
    if not cfg.api.auth.session_secret.strip():
        cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
    save_config(cfg)
    # Revoke all existing sessions immediately (read live from SQLite by any
    # running backend) so a compromised-password rotation does not leave old
    # cookies valid until the next restart. The NEW password itself only takes
    # effect once the backend reloads its config, hence the restart notice.
    revoked = _bump_auth_epoch(cfg)
    if not revoked:
        _print_status_panel(
            "error",
            "密码已保存，但未能立即撤销现有登录态",
            "新密码已写入 config，但运行库不可写、现有 cookie 未即时失效（仍可能有效到重启）。"
            "请重启后端使其生效，或修复 data 目录后重跑 `set-password`。",
        )
        raise typer.Exit(code=1)
    _print_status_panel(
        "success",
        "已设置访问密码",
        "已立即失效所有现有登录态。请重启后端 (openbiliclaw start) 使新密码生效"
        "（运行中的进程仍持旧配置，重启前请勿依赖新密码已启用）。",
    )


# ── ext-key: 浏览器扩展密钥管理 ────────────────────────────────────────


_EXT_KEY_AUTH_FIELDS = frozenset({"extension_access_enabled", "extension_access_keys"})


def _ensure_ext_key_config_writable() -> None:
    """Refuse writes that would be hidden by a higher-priority auth layer."""
    from openbiliclaw.config import API_AUTH_ENV_VARS, config_local_auth_keys

    managed = [name for name in API_AUTH_ENV_VARS if (os.environ.get(name) or "").strip()]
    if managed:
        _print_status_panel(
            "error",
            "检测到环境变量覆盖，设备密钥配置不会可靠生效",
            f"已设置 {', '.join(managed)}；请先移除环境变量覆盖，再管理设备密钥。",
        )
        raise typer.Exit(code=1)
    shadowed = sorted(config_local_auth_keys() & _EXT_KEY_AUTH_FIELDS)
    if shadowed:
        _print_status_panel(
            "error",
            "config.local.toml 正在覆盖设备密钥配置",
            f"被覆盖字段：{', '.join(shadowed)}；请直接修改 config.local.toml。",
        )
        raise typer.Exit(code=1)


@ext_key_app.command("generate")
def ext_key_generate() -> None:
    """生成并保存一个扩展设备访问密钥（明文只显示一次）。"""
    from openbiliclaw.auth_core import generate_extension_access_key
    from openbiliclaw.config import load_config, save_config

    _ensure_ext_key_config_writable()
    cfg = load_config()
    key_id, full_key, record = generate_extension_access_key()
    cfg.api.auth.extension_access_keys.append(record)
    save_config(cfg)
    _print_status_panel(
        "success",
        "设备访问密钥已生成",
        f"Key ID: {key_id}\n设备访问密钥（仅显示一次）:\n{full_key}\n\n"
        "总开关保持关闭；确认保存密钥后执行 `openbiliclaw ext-key enable`。",
    )


@ext_key_app.command("list")
def ext_key_list() -> None:
    """显示设备访问开关和已保存的 key ID。"""
    from openbiliclaw.auth_core import extension_access_key_ids
    from openbiliclaw.config import load_config

    cfg = load_config()
    auth = cfg.api.auth
    key_ids = extension_access_key_ids(auth.extension_access_keys)
    rows: list[tuple[str, str]] = [
        ("设备访问", "开启" if auth.extension_access_enabled else "关闭"),
        ("密钥数量", str(len(key_ids))),
    ]
    rows.extend((f"Key [{index}]", key_id) for index, key_id in enumerate(key_ids, start=1))
    _print_key_value_table("扩展设备访问密钥", rows)


@ext_key_app.command("enable")
def ext_key_enable() -> None:
    """开启扩展设备访问（至少需要一个有效密钥）。"""
    from openbiliclaw.auth_core import extension_access_key_ids
    from openbiliclaw.config import load_config, save_config

    _ensure_ext_key_config_writable()
    cfg = load_config()
    if cfg.api.auth.extension_access_enabled:
        _print_status_panel("info", "已开启", "扩展设备访问已是开启状态。")
        return
    if not extension_access_key_ids(cfg.api.auth.extension_access_keys):
        _print_status_panel(
            "error",
            "没有可用的设备密钥",
            "请先执行 `openbiliclaw ext-key generate`。",
        )
        raise typer.Exit(code=1)
    cfg.api.auth.extension_access_enabled = True
    save_config(cfg)
    _print_status_panel("success", "已开启", "扩展设备访问已开启；重启后端后可配对。")


@ext_key_app.command("disable")
def ext_key_disable() -> None:
    """关闭扩展设备 token 交换，保留已保存密钥。"""
    from openbiliclaw.config import load_config, save_config

    _ensure_ext_key_config_writable()
    cfg = load_config()
    if not cfg.api.auth.extension_access_enabled:
        _print_status_panel("info", "已关闭", "扩展设备访问已是关闭状态。")
        return
    cfg.api.auth.extension_access_enabled = False
    save_config(cfg)
    _print_status_panel("success", "已关闭", "新的设备会话交换已关闭；密钥记录仍保留。")


@ext_key_app.command("revoke")
def ext_key_revoke(
    key_id: str = typer.Argument(..., help="要撤销的 12 位 key ID"),
) -> None:
    """撤销一个设备密钥，并立即失效所有现有登录会话。"""
    from openbiliclaw.auth_core import extension_access_key_ids
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    _ensure_ext_key_config_writable()
    cfg, diagnostics = load_config_with_diagnostics()
    valid_ids = extension_access_key_ids(cfg.api.auth.extension_access_keys)
    if key_id not in valid_ids:
        _print_status_panel("error", "未找到设备密钥", f"没有 key ID `{key_id}`。")
        raise typer.Exit(code=1)

    config_path = diagnostics.config_path
    if config_path is None:
        _print_status_panel("error", "无法定位配置文件", "未修改任何设备密钥。")
        raise typer.Exit(code=1)
    previous = config_path.read_bytes() if config_path.exists() else None
    cfg.api.auth.extension_access_keys = [
        record
        for record in cfg.api.auth.extension_access_keys
        if not record.startswith(f"{key_id}:")
    ]
    try:
        save_config(cfg)
    except Exception as exc:
        _print_status_panel("error", "保存设备密钥失败", str(exc))
        raise typer.Exit(code=1) from exc

    if not _bump_auth_epoch(cfg):
        try:
            if previous is None:
                config_path.unlink(missing_ok=True)
            else:
                config_path.write_bytes(previous)
        except OSError as exc:
            _print_status_panel(
                "error", "撤销失败且配置回滚失败", f"请立即检查 {config_path}: {exc}"
            )
            raise typer.Exit(code=1) from exc
        _print_status_panel(
            "error",
            "未能撤销设备密钥",
            "运行库不可写，配置已回滚；现有会话和设备密钥均保持有效。",
        )
        raise typer.Exit(code=1)

    _print_status_panel(
        "success",
        "设备密钥已撤销",
        f"Key ID {key_id} 已删除；所有 Web 与扩展会话已立即失效。重启后端以重载密钥列表。",
    )


# ── tls-proxy ──────────────────────────────────────────────────────────────


@tls_proxy_app.command("enable")
def tls_proxy_enable(
    san: list[str] = typer.Option(
        [],
        "--san",
        help="客户端访问时使用的 hostname 或 IP（可多次指定）。不指定则交互式输入。",
    ),
) -> None:
    """开启 TLS 反代（启动 API 时将自动监听 HTTPS 端口）。"""
    from openbiliclaw.config import load_config, save_config

    cfg = load_config()

    # 交互式收集 SAN
    if not san and not cfg.tls_proxy.san_names:
        _print_status_panel(
            "info",
            "证书域名配置",
            "其他设备用什么地址访问本服务？\n"
            "输入 hostname 或 IP，多个用逗号分隔，直接回车跳过（仅本机可用）。\n"
            "示例：192.168.1.100, mybili.lan",
        )
        raw = typer.prompt("SAN（hostname/IP）", default="", show_default=False)
        san = [s.strip() for s in raw.split(",") if s.strip()]

    if san:
        cfg.tls_proxy.san_names = san

    if cfg.tls_proxy.enabled and not san:
        _print_status_panel("info", "已开启", "TLS 反代已是开启状态。")
        return
    cfg.tls_proxy.enabled = True
    save_config(cfg)
    san_hint = f"，SAN: {', '.join(cfg.tls_proxy.san_names)}" if cfg.tls_proxy.san_names else "（仅本机）"
    _print_status_panel(
        "success",
        "已开启",
        f"TLS 反代已开启（端口 {cfg.tls_proxy.port}{san_hint}）。下次启动 API 时将自动监听。",
    )


@tls_proxy_app.command("disable")
def tls_proxy_disable() -> None:
    """关闭 TLS 反代。"""
    from openbiliclaw.config import load_config, save_config

    cfg = load_config()
    if not cfg.tls_proxy.enabled:
        _print_status_panel("info", "已关闭", "TLS 反代已是关闭状态。")
        return
    cfg.tls_proxy.enabled = False
    save_config(cfg)
    _print_status_panel("success", "已关闭", "TLS 反代已关闭。")


@tls_proxy_app.command("status")
def tls_proxy_status() -> None:
    """查看 TLS 反代配置状态。"""
    from openbiliclaw.config import load_config

    cfg = load_config()
    status_text = "开启" if cfg.tls_proxy.enabled else "关闭"
    lines = [
        f"状态:   {status_text}",
        f"端口:   {cfg.tls_proxy.port}",
        f"证书目录: {cfg.tls_proxy.cert_dir or '(data/certs)'}",
        f"SAN:    {', '.join(cfg.tls_proxy.san_names) if cfg.tls_proxy.san_names else '(仅本机 localhost)'}",
    ]
    extra = ""
    if not cfg.tls_proxy.enabled:
        extra = "（启用: openbiliclaw tls-proxy enable）"
    _print_status_panel("info", "TLS 反代配置", "\n".join(lines) + extra)


def _start_tls_proxy_if_enabled(api_host: str, api_port: int) -> None:
    """Start the TLS proxy in a daemon thread if ``[tls_proxy].enabled``."""
    from openbiliclaw.config import load_config

    cfg = load_config()
    if not cfg.tls_proxy.enabled:
        return
    try:
        from openbiliclaw.tls_proxy import start_tls_proxy
    except ImportError:
        _print_status_panel(
            "warning", "TLS 代理未启动",
            "cryptography 未安装。安装后 TLS 代理将自动启用。（pip install cryptography）",
        )
        return
    tls_port = cfg.tls_proxy.port
    cert_dir = cfg.tls_proxy.cert_dir or os.path.join(cfg.data_dir, "certs")
    thread = threading.Thread(
        target=start_tls_proxy,
        kwargs={
            "host": "0.0.0.0",
            "port": tls_port,
            "backend_host": "127.0.0.1",
            "backend_port": api_port,
            "cert_dir": cert_dir,
            "auto_gen_certs": True,
            "san_names": cfg.tls_proxy.san_names,
        },
        daemon=True,
        name="tls-proxy",
    )
    thread.start()
    _print_status_panel(
        "info", "TLS 反代已启动",
        f"HTTPS 端口 {tls_port} → 后端 {api_port}。\n"
        f"浏览器/插件可连接 https://<本机地址>:{tls_port}",
    )


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("0.0.0.0", "--host", help="API 监听地址"),
    port: int = typer.Option(8420, "--port", min=1, max=65535, help="API 监听端口"),
) -> None:
    """启动容器友好的 API 服务入口."""
    _print_page_title("启动 OpenBiliClaw", "容器 API 服务")
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动容器友好的后端入口，当前监听 {host}:{port}。",
    )
    if _guided_init_completed_best_effort() is False:
        hint_host = "127.0.0.1" if host == "0.0.0.0" else host  # noqa: S104
        _print_status_panel(
            "warning",
            "还没初始化",
            f"打开 http://{hint_host}:{port}/setup/ 可完成 AI 配置与前置检查；"
            "容器内图形化初始化不可用，请在容器里运行 `openbiliclaw init`"
            "（宿主机执行 `docker exec -it openbiliclaw-backend openbiliclaw init`）。",
        )
    _warn_if_pause_on_disconnect_requires_presence()
    _start_tls_proxy_if_enabled(host, port)
    _run_api_server(host=host, port=port)


@app.command("db-repair")
def db_repair() -> None:
    """检查并修复本地 SQLite 数据库。"""
    result = _run_db_repair()
    console.print(result.message)
    if getattr(result, "db_backup", None) is not None:
        console.print(f"备份文件: {result.db_backup}")
    if getattr(result, "wal_backup", None) is not None:
        console.print(f"WAL 备份: {result.wal_backup}")
    if getattr(result, "repaired_db", None) is not None:
        console.print(f"恢复副本: {result.repaired_db}")
    if result.status in {"in_use", "failed"}:
        raise typer.Exit(code=1)


def _ask_xhs_inclusion() -> bool:
    """Decide whether to enqueue the xhs bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_XHS=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → False, silent.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.

    Returns True iff the caller should proceed with xhs bootstrap.
    """
    if os.environ.get("OPENBILICLAW_NO_XHS", "").strip() == "1":
        console.print("[dim]  跳过小红书数据接入(OPENBILICLAW_NO_XHS=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]🌸 小红书数据接入(可选)[/bold]")
    console.print(
        "把你的小红书[bold cyan]收藏 / 点赞[/bold cyan]混进画像,"
        "系统能读懂你跨平台的口味——\n"
        "你刷小红书喜欢的领域(咖啡 / 摄影 / 穿搭…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print(
        "  2. 浏览器登录 [link=https://www.xiaohongshu.com]https://www.xiaohongshu.com[/link]"
    )
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 只用 B 站数据建画像;以后想加随时再跑一次 init,"
        "或设 OPENBILICLAW_NO_XHS=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入小红书数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求扩展。[/dim]")
        return False

    # User said yes — walk them through the prep checklist before
    # we hit the extension. The bootstrap task has a 30-60s timeout
    # built-in, so if they say "ready" but actually aren't, the
    # collect step degrades gracefully (status="empty"/"timeout") and
    # init still completes on B站 data alone.
    console.print()
    console.print("[bold]准备小红书接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能触发小红书的瀑布流懒加载)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.xiaohongshu.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),进到你的小红书 profile 页"
        "向下滚动加载收藏/点赞。整个过程 10-30 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响滚动加载)。"
        "完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据(只读初始 state,无前台 tab,但只能拿到 ~10-20 条)。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓小红书接入,本次 init 只用 B 站数据。装好扩展+登录"
            "小红书后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_dy_inclusion() -> bool:
    """Decide whether to enqueue the Douyin bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_DOUYIN=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
         Conservative default because Douyin hits more-aggressive risk-control
         if the user isn't actually logged in, and the soft anti-bot returns
         HTTP 200 + empty body (design-doc Risk #7) which we can only
         detect after the bootstrap runs. Better to require explicit
         opt-in for Douyin than auto-fire it on every CI run.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.
    """
    if os.environ.get("OPENBILICLAW_NO_DOUYIN", "").strip() == "1":
        console.print("[dim]  跳过抖音数据接入(OPENBILICLAW_NO_DOUYIN=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]🎵 抖音数据接入(可选)[/bold]")
    console.print(
        "把你的抖音[bold cyan]发布 / 收藏 / 点赞 / 关注[/bold cyan]混进画像,"
        "系统能读懂你跨平台的口味——\n"
        "你刷抖音常停留的领域(美食 / 历史 / 知识区…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://www.douyin.com]https://www.douyin.com[/link]")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+小红书,如启用)数据建画像;"
        "以后想加随时再跑一次 init,或设 OPENBILICLAW_NO_DOUYIN=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入抖音数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求抖音数据。[/dim]")
        return False

    console.print()
    console.print("[bold]准备抖音接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能让抖音的虚拟列表分页加载)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.douyin.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),依次访问 4 个 profile sub-tab"
        "(发布 / 收藏 / 点赞 / 关注)向下滚动加载。整个过程 30-90 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响虚拟列表分页)。"
        "完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓抖音接入,本次 init 不会拉抖音数据。装好扩展+登录"
            "抖音后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_yt_inclusion() -> bool:
    """Decide whether to enqueue the YouTube bootstrap task on this init.

    Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_YOUTUBE=1`` env var → False, silent
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
         Conservative default — YouTube requires browser login and focus.
      3. Interactive terminal → ask the user with default N, then
         (if Y) walk them through a prep checklist.
    """
    if os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        console.print("[dim]  跳过 YouTube 数据接入(OPENBILICLAW_NO_YOUTUBE=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]▶ YouTube 数据接入(可选)[/bold]")
    console.print(
        "把你的 YouTube[bold cyan]观看历史 / 订阅 / 点赞[/bold cyan]混进画像,"
        "系统能读懂你跨平台的兴趣——\n"
        "你在 YouTube 常看的领域(科技 / 历史 / 音乐…)也会反映到 B 站推荐里。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print("  2. 浏览器登录 [link=https://www.youtube.com]https://www.youtube.com[/link]")
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+其他已启用平台)数据建画像;"
        "以后想加随时再跑一次 init,或设 OPENBILICLAW_NO_YOUTUBE=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入 YouTube 数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会请求 YouTube 数据。[/dim]")
        return False

    console.print()
    console.print("[bold]准备 YouTube 接入[/bold]")
    console.print("请确认以下三件事都做了:")
    console.print("  [cyan]☐[/cyan] 装好了 OpenBiliClaw 浏览器扩展")
    console.print(
        "  [cyan]☐[/cyan] 浏览器目前是打开的且是当前 [bold]活跃窗口[/bold]"
        "(扩展需要前台 tab 才能滚动加载 YouTube 历史/订阅/点赞列表)"
    )
    console.print("  [cyan]☐[/cyan] 已经登录了 https://www.youtube.com")
    console.print()
    console.print(
        "[bold yellow]⚠[/bold yellow]  接下来扩展会[bold]在你的浏览器里自动打开"
        "一个新 tab[/bold]并切到那个 tab(会抢一次焦点),依次访问 3 个页面"
        "(观看历史 / 订阅频道 / 点赞列表)向下滚动加载。整个过程 30-90 秒。"
    )
    console.print(
        "[dim]   — 期间不要关那个 tab、不要切走太久(可能影响滚动加载)。"
        "完成后扩展会自动关闭它,焦点还回来。[/dim]"
    )
    console.print(
        "[dim]   — 想跳过焦点抢占的话:Ctrl-C 退出,改用 "
        "`OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS=0 openbiliclaw init` "
        "拿浅层数据。[/dim]"
    )
    console.print()
    if not typer.confirm("准备好了吗,可以开始吗?", default=True):
        console.print(
            "[dim]  已暂缓 YouTube 接入,本次 init 不会拉 YouTube 数据。装好扩展+登录"
            "YouTube 后随时再跑一次 init 就能补上。[/dim]"
        )
        return False
    return True


def _ask_x_inclusion() -> bool:
    """Decide whether to enable the X (Twitter) discovery source on this init.

    Unlike xhs/douyin/youtube, X has no extension bootstrap task — discovery is
    server-side cookie replay. So this only flips ``[sources.twitter].enabled``;
    the actual fetch runs later via the backend producer once x.com cookies are
    synced. Resolution order (first match wins):
      1. ``OPENBILICLAW_NO_X=1`` env var → False, silent.
      2. Non-interactive terminal (CI / piped stdin) → **False**, silent.
      3. Interactive terminal → ask the user with default N (opt-in).
    """
    if os.environ.get("OPENBILICLAW_NO_X", "").strip() == "1":
        console.print("[dim]  跳过 X 数据接入(OPENBILICLAW_NO_X=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]𝕏 X (Twitter) 数据接入(可选)[/bold]")
    console.print(
        "把 X 内容混进发现池,系统会按你的画像在 X 上"
        "[bold cyan]搜索 / 拉 For-You / 追订阅作者[/bold cyan],"
        "推荐里会多出 X 的文字卡片。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print(
        "     [link=https://github.com/whiteguo233/OpenBiliClaw/releases]"
        "https://github.com/whiteguo233/OpenBiliClaw/releases[/link]"
    )
    console.print(
        "  2. 浏览器登录 [link=https://x.com]https://x.com[/link](扩展会自动把 cookie 同步给后端)"
    )
    console.print()
    console.print(
        "[dim]说 N 也没关系,init 会用 B 站(+其他已启用平台)数据建画像;"
        "以后想加随时再跑一次 init,或在设置页开启 X 来源,或设 OPENBILICLAW_NO_X=1 永久跳过。[/dim]"
    )
    console.print()

    if not typer.confirm("加入 X 数据?", default=False):
        console.print("[dim]  已选择跳过,本次 init 不会启用 X 来源。[/dim]")
        return False
    return True


def _ask_zhihu_inclusion() -> bool:
    """Decide whether to enqueue the Zhihu bootstrap task on this init."""
    if os.environ.get("OPENBILICLAW_NO_ZHIHU", "").strip() == "1":
        console.print("[dim]  跳过知乎数据接入(OPENBILICLAW_NO_ZHIHU=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]知乎数据接入(可选)[/bold]")
    console.print(
        "把你的知乎[bold cyan]浏览 / 收藏 / 点赞[/bold cyan]混进画像，"
        "知识类回答、文章和关注领域会参与首次偏好分析。"
    )
    console.print()
    console.print("启用需要:")
    console.print("  1. 装好 OpenBiliClaw 浏览器扩展")
    console.print("  2. 浏览器登录 [link=https://www.zhihu.com]https://www.zhihu.com[/link]")
    console.print()
    console.print(
        "[dim]知乎通过浏览器插件使用当前登录态抓取；说 N 也没关系，"
        "以后可在设置页开启知乎来源，或重新运行 init。[/dim]"
    )
    console.print()

    if not typer.confirm("加入知乎数据?", default=False):
        console.print("[dim]  已选择跳过，本次 init 不会请求知乎数据。[/dim]")
        return False
    return True


def _ask_reddit_inclusion() -> bool:
    """Decide whether to enable the Reddit init/discovery source."""
    if os.environ.get("OPENBILICLAW_NO_REDDIT", "").strip() == "1":
        console.print("[dim]  跳过 Reddit 来源启用(OPENBILICLAW_NO_REDDIT=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False

    console.print()
    console.print("[bold]Reddit 数据接入(可选)[/bold]")
    console.print(
        "把 Reddit [bold cyan]收藏 / 点赞 / 订阅 subreddit[/bold cyan]混进首轮画像，"
        "同时启用后续 search / hot / subreddit / related 内容发现。"
    )
    console.print()
    console.print(
        "[dim]需要当前浏览器已登录 reddit.com；扩展会在同源页面内读取只读 JSON endpoint。[/dim]"
    )
    console.print()

    if not typer.confirm("启用 Reddit 数据接入?", default=False):
        console.print("[dim]  已选择跳过，本次 init 不会启用 Reddit 来源。[/dim]")
        return False
    return True


def _ask_bangumi_inclusion() -> bool:
    """Decide whether to enable Bangumi discovery and public bootstrap."""
    if os.environ.get("OPENBILICLAW_NO_BANGUMI", "").strip() == "1":
        console.print("[dim]  跳过 Bangumi 来源(OPENBILICLAW_NO_BANGUMI=1)。[/dim]")
        return False
    if not _is_interactive_terminal():
        return False
    console.print()
    console.print("[bold]Bangumi 数据接入(可选)[/bold]")
    console.print(
        "使用 Bangumi 官方公开 API 导入[bold cyan]公开收藏[/bold cyan]，"
        "并启用动画 / 书籍 / 游戏的搜索、排名和日期浏览。"
    )
    console.print("[dim]无需登录；只读取用户主动公开的收藏，不会向 Bangumi 写入任何内容。[/dim]")
    if not typer.confirm("启用 Bangumi 数据接入?", default=False):
        console.print("[dim]  已选择跳过，本次 init 不会启用 Bangumi。[/dim]")
        return False
    return True


def _ask_network_binding() -> bool:
    """Ask whether the backend should listen on all interfaces (0.0.0.0).

    Returns True if the user confirms all-interface binding, False for
    localhost-only.  Non-interactive terminals default to True (the new
    default keeps mobile web accessible).
    """
    if not _is_interactive_terminal():
        return True

    console.print()
    console.print("[bold]📱 移动端访问[/bold]")
    console.print(
        "OpenBiliClaw 自带移动端 Web（[bold cyan]/m/[/bold cyan]），同一局域网的手机扫码即可打开。"
    )
    console.print()
    console.print(
        "为此，后端需要监听 [bold]0.0.0.0[/bold]（所有网卡），"
        "这样手机才能连上来。\n"
        "如果你只在本机使用、不需要手机端，选 N 会改为仅监听 127.0.0.1。"
    )
    console.print()
    console.print("[dim]后续可在 config.toml 的 [api].host 随时切换。[/dim]")
    console.print()
    return typer.confirm("允许局域网设备访问（推荐）?", default=True)


def _persist_api_host_choice(*, allow_lan: bool) -> None:
    """Persist the user's network binding choice to config.toml."""
    try:
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        target_host = "0.0.0.0" if allow_lan else "127.0.0.1"
        if cfg.api.host != target_host:
            cfg.api.host = target_host
            save_config(cfg)
    except Exception:
        return


def _maybe_setup_password_in_init(*, allow_lan: bool) -> None:
    """Offer to set a LAN access password during init (only when LAN is enabled)."""
    if not allow_lan or not _is_interactive_terminal():
        return
    console.print()
    console.print("[bold]🔒 访问密码（可选）[/bold]")
    console.print(
        "为局域网/远程设备访问设置登录密码？[bold]本机访问始终免登录[/bold]，"
        "只有手机和其他电脑需要输入密码。"
    )
    console.print("[dim]后续可用 `openbiliclaw set-password` 设置或修改。[/dim]")
    console.print()
    if not typer.confirm("为局域网访问设置登录密码?", default=False):
        return
    password = str(
        typer.prompt("设置访问密码", hide_input=True, confirmation_prompt=True) or ""
    ).strip()
    if not password:
        console.print("[dim]密码为空，已跳过。[/dim]")
        return
    try:
        import secrets as _secrets

        from openbiliclaw.auth_core import hash_password
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        cfg.api.auth.password_hash = hash_password(password)
        cfg.api.auth.enabled = True
        if not cfg.api.auth.session_secret.strip():
            cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
        save_config(cfg)
        console.print("[green]已设置访问密码，局域网访问将需要登录。[/green]")
    except Exception:
        console.print("[yellow]密码设置失败，可稍后用 `openbiliclaw set-password` 重试。[/yellow]")


def _persist_init_source_enabled_flags(
    *,
    include_bili: bool = True,
    include_xhs: bool,
    include_dy: bool,
    include_yt: bool,
    include_x: bool = False,
    include_zhihu: bool = False,
    include_reddit: bool = False,
    include_bangumi: bool = False,
    bangumi_username: str = "",
    bangumi_token: str = "",
) -> None:
    """Persist init source choices so background discovery obeys them."""

    try:
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        changed = False
        bilibili_cfg = getattr(cfg.sources, "bilibili", None)
        if (
            bilibili_cfg is not None
            and bool(getattr(bilibili_cfg, "enabled", True)) != include_bili
        ):
            bilibili_cfg.enabled = include_bili
            changed = True
        if bool(getattr(cfg.sources.xiaohongshu, "enabled", False)) != include_xhs:
            cfg.sources.xiaohongshu.enabled = include_xhs
            changed = True
        if bool(getattr(cfg.sources.douyin, "enabled", False)) != include_dy:
            cfg.sources.douyin.enabled = include_dy
            changed = True
        if bool(getattr(cfg.sources.youtube, "enabled", False)) != include_yt:
            cfg.sources.youtube.enabled = include_yt
            changed = True
        twitter_cfg = getattr(cfg.sources, "twitter", None)
        if twitter_cfg is not None and bool(getattr(twitter_cfg, "enabled", False)) != include_x:
            twitter_cfg.enabled = include_x
            changed = True
        zhihu_cfg = getattr(cfg.sources, "zhihu", None)
        if zhihu_cfg is not None and bool(getattr(zhihu_cfg, "enabled", False)) != include_zhihu:
            zhihu_cfg.enabled = include_zhihu
            changed = True
        reddit_cfg = getattr(cfg.sources, "reddit", None)
        if reddit_cfg is not None and bool(getattr(reddit_cfg, "enabled", False)) != include_reddit:
            reddit_cfg.enabled = include_reddit
            changed = True
        bangumi_cfg = getattr(cfg.sources, "bangumi", None)
        if (
            bangumi_cfg is not None
            and bool(getattr(bangumi_cfg, "enabled", False)) != include_bangumi
        ):
            bangumi_cfg.enabled = include_bangumi
            changed = True
        if (
            bangumi_cfg is not None
            and bangumi_username
            and str(getattr(bangumi_cfg, "username", "")) != bangumi_username
        ):
            bangumi_cfg.username = bangumi_username
            changed = True
        if (
            bangumi_cfg is not None
            and bangumi_token
            and str(getattr(bangumi_cfg, "access_token", "")) != bangumi_token
        ):
            bangumi_cfg.access_token = bangumi_token
            changed = True
        if changed:
            save_config(cfg)
    except Exception:
        # Persisting init choices is best-effort; init should continue.
        return


def _select_init_source_shares(
    event_counts: Mapping[str, int],
    *,
    enabled_sources: Mapping[str, bool],
    configured_shares: Mapping[str, int],
) -> dict[str, int]:
    """Return source shares selected during interactive init."""

    from openbiliclaw.runtime.source_policy import (
        SOURCE_ORDER,
        suggest_pool_source_shares,
    )

    configured = _merge_source_shares(configured_shares, {})
    suggestion = suggest_pool_source_shares(
        event_counts,
        enabled_sources=enabled_sources,
        configured_shares=configured,
    )
    if not _is_interactive_terminal():
        return configured

    enabled_order = [source for source in SOURCE_ORDER if enabled_sources.get(source, False)]
    console.print()
    console.print("[bold]平台发现比例[/bold]")
    console.print(
        "[dim]根据本次初始化采集到的各平台事件量，推荐后台发现池比例："
        f"{_format_source_shares(suggestion)}。[/dim]"
    )
    if typer.confirm("使用这个比例?", default=True):
        return _merge_source_shares(configured, suggestion)

    raw = typer.prompt(
        "手动输入比例",
        default=",".join(f"{source}={configured.get(source, 1)}" for source in enabled_order),
    ).strip()
    parsed = _parse_source_share_input(raw, enabled_order=enabled_order)
    if not parsed:
        console.print("[yellow]比例输入无效，保留原配置。[/yellow]")
        return configured
    return _merge_source_shares(configured, parsed)


def _maybe_update_init_source_shares(event_counts: Mapping[str, int]) -> None:
    """Ask the user to accept/update source shares after init event collection."""

    try:
        from openbiliclaw.config import load_config, save_config
        from openbiliclaw.runtime.source_policy import source_enabled_map

        cfg = load_config()
        enabled_sources = source_enabled_map(cfg)
        selected = _select_init_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=cfg.scheduler.pool_source_shares,
        )
        if selected != cfg.scheduler.pool_source_shares:
            cfg.scheduler.pool_source_shares = selected
            save_config(cfg)
    except Exception:
        return


def _merge_source_shares(
    configured_shares: Mapping[str, int],
    updates: Mapping[str, int],
) -> dict[str, int]:
    from openbiliclaw.runtime.source_policy import DEFAULT_POOL_SOURCE_SHARES, SOURCE_ORDER

    merged = dict(DEFAULT_POOL_SOURCE_SHARES)
    for source in SOURCE_ORDER:
        if source in configured_shares:
            try:
                share = int(configured_shares[source])
            except (TypeError, ValueError):
                continue
            if share > 0:
                merged[source] = share
    for source, raw_share in updates.items():
        if source not in SOURCE_ORDER:
            continue
        try:
            share = int(raw_share)
        except (TypeError, ValueError):
            continue
        if share > 0:
            merged[source] = share
    return {source: merged[source] for source in SOURCE_ORDER if source in merged}


def _parse_source_share_input(raw: str, *, enabled_order: list[str]) -> dict[str, int]:
    if not raw.strip():
        return {}

    parsed: dict[str, int] = {}
    if "=" in raw:
        for part in re.split(r"[,，\s]+", raw.strip()):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            source = key.strip().lower()
            if source not in enabled_order:
                continue
            try:
                share = int(value)
            except ValueError:
                continue
            if share > 0:
                parsed[source] = share
        return parsed

    values = [item for item in re.split(r"[:：,，\s]+", raw.strip()) if item]
    for source, value in zip(enabled_order, values, strict=False):
        try:
            share = int(value)
        except ValueError:
            continue
        if share > 0:
            parsed[source] = share
    return parsed


def _format_source_shares(shares: Mapping[str, int]) -> str:
    labels = {
        "bilibili": "B站",
        "xiaohongshu": "小红书",
        "douyin": "抖音",
        "youtube": "YouTube",
    }
    return ", ".join(f"{labels.get(source, source)}={share}" for source, share in shares.items())


def _normalize_init_bilibili_limit(value: int | None, *, default: int) -> int:
    """Normalize user-facing init signal limits.

    Callers own the meaning of 0: history treats it as "fetch all",
    while favorite/follow keep the existing "skip this signal" meaning.
    """
    if value is None:
        return default
    return max(0, int(value))


def _ask_init_bilibili_limits(
    *,
    history_limit: int | None,
    favorite_limit: int | None,
    follow_limit: int | None,
) -> tuple[int, int, int]:
    """Ask interactive users to confirm Bilibili init signal caps."""
    history = _normalize_init_bilibili_limit(
        history_limit,
        default=_INIT_BILIBILI_HISTORY_LIMIT,
    )
    favorite = _normalize_init_bilibili_limit(
        favorite_limit,
        default=_INIT_BILIBILI_FAVORITE_LIMIT,
    )
    follow = _normalize_init_bilibili_limit(
        follow_limit,
        default=_INIT_BILIBILI_FOLLOW_LIMIT,
    )
    if not _is_interactive_terminal():
        return history, favorite, follow
    if history_limit is not None and favorite_limit is not None and follow_limit is not None:
        return history, favorite, follow

    console.print(
        "\n[bold]B 站初始化信号上限[/bold]\n"
        "[dim]回车使用默认值；历史输入 0 表示拉全部，收藏 / 关注输入 0 表示跳过。[/dim]"
    )
    if history_limit is None:
        raw = typer.prompt(
            "B 站历史最多导入多少条",
            default=str(_INIT_BILIBILI_HISTORY_LIMIT),
        )
        try:
            history = max(0, int(str(raw).strip()))
        except ValueError:
            history = _INIT_BILIBILI_HISTORY_LIMIT
    if favorite_limit is None:
        raw = typer.prompt(
            "B 站收藏最多导入多少条",
            default=str(_INIT_BILIBILI_FAVORITE_LIMIT),
        )
        try:
            favorite = max(0, int(str(raw).strip()))
        except ValueError:
            favorite = _INIT_BILIBILI_FAVORITE_LIMIT
    if follow_limit is None:
        raw = typer.prompt(
            "B 站关注 UP 最多导入多少人",
            default=str(_INIT_BILIBILI_FOLLOW_LIMIT),
        )
        try:
            follow = max(0, int(str(raw).strip()))
        except ValueError:
            follow = _INIT_BILIBILI_FOLLOW_LIMIT
    return history, favorite, follow


@dataclass
class InitResult:
    """Outcome of :func:`run_guided_init`, consumed by the CLI summary
    and (gui-init) the API init endpoint."""

    history: list[dict[str, Any]]
    favorites_data: list[dict[str, Any]]
    following_data: list[dict[str, Any]]
    events: list[dict[str, Any]]
    bilibili_event_count: int
    xhs_events: list[dict[str, Any]]
    xhs_scope_counts: dict[str, Any]
    xhs_status: str
    dy_events: list[dict[str, Any]]
    dy_scope_counts: dict[str, Any]
    dy_status: str
    yt_events: list[dict[str, Any]]
    yt_scope_counts: dict[str, Any]
    yt_status: str
    zhihu_events: list[dict[str, Any]]
    zhihu_scope_counts: dict[str, Any]
    zhihu_status: str
    reddit_events: list[dict[str, Any]]
    reddit_scope_counts: dict[str, Any]
    reddit_status: str
    profile_data: Any
    discovered_count: int
    discovery_error: bool
    discover_exc: BaseException | None
    discovery_reason: str | None = None
    discovery_detail: str = ""
    bangumi_events: list[dict[str, Any]] = field(default_factory=list)
    bangumi_scope_counts: dict[str, Any] = field(default_factory=dict)
    bangumi_status: str = "skipped"


class GuidedInitError(Exception):
    """Hard failure raised inside :func:`run_guided_init`.

    ``reason`` is a stable machine code (``empty_history`` /
    ``profile_failed``) the API maps onto ``InitCoordinator.fail`` and
    the CLI maps onto a status panel + non-zero exit.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


async def _fetch_bilibili_init_data(
    client: Any,
    *,
    history_limit: int = _INIT_BILIBILI_HISTORY_LIMIT,
    favorite_limit: int,
    follow_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch B站 history / favorites / following in one event loop.

    Extracted from the old ``init`` closure so the CLI and the API
    guided-init paths share a single B站 fetch (gui-init spec §1).
    Favorites/following limits are resolved by the caller; history uses
    ``_INIT_BILIBILI_HISTORY_LIMIT`` unless a caller passes an override.
    """
    from openbiliclaw.bilibili.api import favorite_item_is_dead

    hist = await client.get_user_history(max_items=history_limit)

    favs: list[dict[str, Any]] = []
    try:
        fav_folders = (
            await client.get_all_favorites(
                max_folders=200,
                max_items_per_folder=max(1, favorite_limit),
                max_total_items=favorite_limit,
            )
            if favorite_limit > 0
            else []
        )
        for folder in fav_folders:
            folder_title = folder.folder.title if hasattr(folder, "folder") else "未知"
            for item in folder.items if hasattr(folder, "items") else []:
                if len(favs) >= favorite_limit:
                    break
                upper = item.get("upper", {}) if isinstance(item, dict) else {}
                if not isinstance(upper, dict):
                    upper = {}
                raw = item if isinstance(item, dict) else {}
                if favorite_item_is_dead(raw):
                    # Taken-down videos come back with the literal title
                    # "已失效视频" (6% of a real 200-item sample). Keeping them
                    # told the analyzer the user is into "已失效视频" and put
                    # that string in the ledger as a favourite signal. We know
                    # nothing about what the video was, so it is not a signal.
                    continue
                raw_cnt = raw.get("cnt_info")
                cnt_info: dict[str, Any] = raw_cnt if isinstance(raw_cnt, dict) else {}
                favs.append(
                    {
                        "title": raw.get("title", "") if raw else str(item),
                        "upper": str(upper.get("name", "")).strip(),
                        "folder": folder_title,
                        # Identity, time and reach: carried for the event
                        # ledger, not for prompts (stripped before _favorites
                        # below). A favourited video must never be recommended
                        # back, dedup needs a bvid, and play count is what tells
                        # a niche-digger apart from a trend-follower.
                        "bvid": str(raw.get("bvid", "") or "").strip(),
                        "fav_time": raw.get("fav_time"),
                        "duration": raw.get("duration"),
                        "pubtime": raw.get("pubtime"),
                        "play_count": cnt_info.get("play"),
                        # Kept in the ledger, deliberately out of prompts: on a
                        # real 200-item sample 154 entries had an intro, median
                        # 105 chars, but a large share is 充电 / 大会员 promo
                        # boilerplate. Storing it means a later pass can use it
                        # without re-fetching; feeding it to the portrait now
                        # would spend ~20k chars per init to dilute the signal.
                        "intro": str(raw.get("intro", "") or "").strip()[:200],
                    }
                )
            if len(favs) >= favorite_limit:
                break
    except Exception as exc:
        console.print(f"  [yellow]收藏夹拉取失败: {exc}[/yellow]")

    follows: list[dict[str, Any]] = []
    try:
        page = 1
        page_size = 50
        while len(follows) < follow_limit:
            page_users = await client.get_following(page=page, page_size=page_size)
            if not page_users:
                break
            for user in page_users:
                if len(follows) >= follow_limit:
                    break
                follows.append(
                    {
                        "name": getattr(user, "uname", str(user)),
                        "sign": getattr(user, "sign", ""),
                    }
                )
            if len(page_users) < page_size:
                break
            page += 1
    except Exception as exc:
        console.print(f"  [yellow]关注列表拉取失败: {exc}[/yellow]")

    return hist, favs, follows


async def _fetch_x_init_data(
    *,
    likes_limit: int,
    bookmarks_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch the user's own X likes + bookmarks for init preference backfill.

    X is server-side cookie replay (no extension bootstrap task), so — like
    B站 — we fetch directly here. Resolves the synced ``x.com`` cookie via the
    same path the discovery producer uses; if it's absent (user enabled X but
    hasn't logged in / the extension hasn't synced yet) we skip cleanly. All
    fetches are best-effort: a missing / expired cookie or a rate-limit must
    never hard-fail ``init``. Returns ``(likes, bookmarks)`` as
    ``tweet_to_dict`` dicts.
    """
    import logging

    from openbiliclaw.config import load_config

    logger = logging.getLogger("openbiliclaw.cli")

    cfg = load_config()
    x_cfg = getattr(getattr(cfg, "sources", None), "twitter", None)
    cookie_env = str(getattr(x_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE"))

    from openbiliclaw.sources.x_auth import resolve_x_cookie

    cookie = resolve_x_cookie(data_dir=cfg.data_path, cookie_env=cookie_env)
    if not cookie:
        console.print(
            "  [dim]X 未同步 cookie,跳过点赞/收藏历史回填"
            "(登录 x.com 后扩展会自动同步,下次 init 生效)。[/dim]"
        )
        return [], []

    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.sources.x_client import XClient
    from openbiliclaw.storage.database import Database
    from openbiliclaw.storage.x_health import XSourceHealthStore

    x_client = XClient(cookie=cookie)
    health_db: Database | None = None
    health_store: XSourceHealthStore | None = None
    try:
        health_db = Database(cfg.data_path / "openbiliclaw.db")
        health_db.initialize()
        health_store = XSourceHealthStore(
            health_db,
            credential_fingerprint=credential_fingerprint("twitter", cookie),
        )
    except Exception:
        # Health evidence is observability, not a prerequisite for the user's
        # read-only smoke/init fetch. Keep the request path available if the
        # local status database is temporarily unavailable.
        logger.debug("fetch-x: failed to open the shared X health store", exc_info=True)
        if health_db is not None:
            health_db.close()
        health_db = None
        health_store = None

    def _record_success(strategy: str) -> None:
        if health_store is None:
            return
        try:
            health_store.record_success(strategy=strategy)
        except Exception:
            logger.debug("fetch-x: failed to record %s success", strategy, exc_info=True)

    def _record_error(exc: BaseException, strategy: str) -> None:
        if health_store is None:
            return
        try:
            health_store.record_error(exc, strategy=strategy)
        except Exception:
            logger.debug("fetch-x: failed to record %s error", strategy, exc_info=True)

    likes: list[dict[str, Any]] = []
    bookmarks: list[dict[str, Any]] = []
    try:
        if likes_limit > 0:
            try:
                likes = await x_client.likes(limit=likes_limit)
                _record_success("likes")
            except Exception as exc:
                _record_error(exc, "likes")
                console.print(f"  [yellow]X 点赞拉取失败: {exc}[/yellow]")
        if bookmarks_limit > 0:
            try:
                bookmarks = await x_client.bookmarks(limit=bookmarks_limit)
                _record_success("bookmarks")
            except Exception as exc:
                _record_error(exc, "bookmarks")
                console.print(f"  [yellow]X 收藏拉取失败: {exc}[/yellow]")
        return likes, bookmarks
    finally:
        if health_db is not None:
            health_db.close()


def _load_extension_bangumi_identity() -> tuple[str, bool]:
    """Read the extension-reported Bangumi ``(username, verified)``.

    The Bangumi content script reports the logged-in account's public uid +
    username to ``POST /api/sources/bangumi/identity``, which persists
    ``bangumi_self_info`` into ``data/memory/discovery_runtime.json``. Returns
    ``("", False)`` on any miss or malformed value — the caller falls through
    to its normal error path.

    ``verified`` mirrors the backend flag: True only when bgm.tv confirmed the
    username belongs to the reported uid. Records written before the flag
    existed read back as unverified (they cannot prove a check ever ran) and
    self-heal on the next bgm.tv page view. A ``verified`` record with no
    username is likewise read as unverified — the superseded 404 path wrote
    those, and no current rule can produce one.
    """
    import json as _json

    from openbiliclaw.config import load_config
    from openbiliclaw.sources.bangumi_client import validate_bangumi_username

    try:
        state_path = load_config().data_path / "memory" / "discovery_runtime.json"
        if not state_path.exists():
            return "", False
        with open(state_path, encoding="utf-8") as file:
            state = _json.load(file)
        info = state.get("bangumi_self_info") if isinstance(state, dict) else None
        if not isinstance(info, dict):
            return "", False
        username = validate_bangumi_username(info.get("username"))
        return username, bool(username) and info.get("verified") is True
    except Exception:
        return "", False


async def _fetch_bangumi_init_data(
    *,
    username: str,
    token: str = "",
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Fetch one bounded Bangumi bootstrap sample.

    With a personal access token (arg or ``[sources.bangumi].access_token``),
    the account is resolved via ``/v0/me`` and its collections — including
    private ones — are read with a Bearer header. Without a token, the
    historical anonymous public-username path is used unchanged. A token
    rejected at fetch time (e.g. expired since the pre-flight check) returns
    status ``invalid_token`` rather than silently degrading.
    """
    import logging

    from openbiliclaw.config import load_config
    from openbiliclaw.sources.bangumi import fetch_bangumi_public_collection_events
    from openbiliclaw.sources.bangumi_client import (
        BangumiAPIError,
        BangumiClient,
        me_username,
        validate_bangumi_access_token,
    )

    logger = logging.getLogger("openbiliclaw.cli")
    config = load_config()
    bangumi_cfg = config.sources.bangumi
    effective_token = validate_bangumi_access_token(token or bangumi_cfg.access_token)

    def _summarize(events: list[dict[str, Any]]) -> tuple[dict[str, int], str]:
        counts: dict[str, int] = {}
        for event in events:
            status = str((event.get("metadata") or {}).get("collection_status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts, "ok" if events else "empty"

    if effective_token:
        async with BangumiClient(
            access_token=effective_token,
            request_interval_seconds=float(bangumi_cfg.request_interval_seconds),
        ) as bangumi_client:
            try:
                resolved_username = me_username(await bangumi_client.get_me())
            except BangumiAPIError as exc:
                if exc.code == "unauthorized":
                    logger.warning(
                        "bangumi init: access token rejected by /v0/me "
                        "(token present, length=%d); likely expired or revoked",
                        len(effective_token),
                    )
                    return [], {}, "invalid_token"
                raise
            if username.strip() and username.strip() != resolved_username:
                logger.warning(
                    "bangumi init: configured username %r differs from /v0/me %r; "
                    "using the token owner's account",
                    username.strip(),
                    resolved_username,
                )
            events = await fetch_bangumi_public_collection_events(
                bangumi_client,
                username=resolved_username,
                subject_types=tuple(bangumi_cfg.subject_types),
                limit=int(bangumi_cfg.bootstrap_limit),
                include_private=True,
            )
        counts, status = _summarize(events)
        return events, counts, status

    if not username.strip():
        return [], {}, "missing_username"
    async with BangumiClient(
        request_interval_seconds=float(bangumi_cfg.request_interval_seconds)
    ) as bangumi_client:
        events = await fetch_bangumi_public_collection_events(
            bangumi_client,
            username=username,
            subject_types=tuple(bangumi_cfg.subject_types),
            limit=int(bangumi_cfg.bootstrap_limit),
        )
    counts, status = _summarize(events)
    return events, counts, status


async def run_guided_init(
    *,
    client: Any,
    memory: Any,
    soul_engine: Any,
    favorite_limit: int,
    follow_limit: int,
    history_limit: int = _INIT_BILIBILI_HISTORY_LIMIT,
    include_bili: bool = True,
    include_xhs: bool,
    include_dy: bool,
    include_yt: bool,
    include_x: bool = False,
    include_zhihu: bool = False,
    include_reddit: bool = False,
    include_bangumi: bool = False,
    bangumi_username: str = "",
    bangumi_token: str = "",
    target_pool_count: int,
    discover_backfill: Callable[..., Coroutine[Any, Any, int]],
    coordinator: Any = None,
    run_id: str | None = None,
    profile_analysis_timeout_seconds: float | None = None,
    profile_build_timeout_seconds: float = _INIT_PROFILE_BUILD_TIMEOUT_SECONDS,
    discovery_timeout_seconds: float = _INIT_DISCOVERY_TIMEOUT_SECONDS,
    collection_timeout_seconds: float = _INIT_COLLECTION_TIMEOUT_SECONDS,
) -> InitResult:
    """Shared async init pipeline (gui-init spec §1).

    Runs the four init stages in one event loop so neither the CLI
    (``asyncio.run(run_guided_init(...))``) nor the API (``await
    run_guided_init(...)`` on the server loop) nests event loops:

      1. fetch B站 + collect cross-platform bootstrap signals → propagate
      2. analyze preferences
      3. build and durably commit the full soul profile
      4. discover, evaluate, write recommendation copy, and verify the first
         serviceable pool from that committed profile

    Bilibili is optional like every other source (``include_bili``); at
    least one selected source must yield signals or stage 1 raises
    ``GuidedInitError("empty_signals")``. ``client`` may be ``None`` when
    ``include_bili`` is False. Stage 1 has one wall-clock budget shared by all
    selected sources (30 minutes by default), with shorter Bilibili/X caps and
    cooperative cancellation for extension collectors. Collected events are
    committed in one SQLite transaction before preference analysis starts.

    Stage 4 never starts from a draft profile. ``discover_backfill`` is the one
    genuinely path-specific step: the CLI
    injects :func:`_run_init_discovery_backfill_async` (one-shot engine);
    the API injects ``controller.run_init_backfill`` (holds the refresh
    lock). When ``coordinator``/``run_id`` are supplied, stage transitions
    and enqueued bootstrap task ids are reported for live GUI progress;
    run lifecycle (mark_running / complete / fail) stays with the caller.
    """

    async def _stage_started(n: int) -> None:
        if coordinator is not None and run_id is not None:
            await coordinator.stage_started(run_id, n)

    async def _stage_done(n: int, *, status: str = "ok", reason: str | None = None) -> None:
        if coordinator is not None and run_id is not None:
            await coordinator.stage_done(run_id, n, status=status, reason=reason)

    async def _report_stage_progress(
        stage: int,
        *,
        done: int = 0,
        total: int = 0,
        note: str | None = None,
        mode: str = "determinate",
        elapsed_seconds: int | None = None,
        max_seconds: int | None = None,
        substantive: bool = True,
    ) -> None:
        """Send additive progress fields while tolerating legacy test/plugins.

        Third-party coordinators built against the older ``done/total/note``
        contract keep working; the in-tree coordinator receives the richer
        elapsed/indeterminate payload.
        """
        if coordinator is None or run_id is None:
            return
        progress = coordinator.stage_progress
        values: dict[str, Any] = {
            "done": done,
            "total": total,
            "note": note,
            "mode": mode,
            "elapsed_seconds": elapsed_seconds,
            "max_seconds": max_seconds,
            "substantive": substantive,
        }
        try:
            parameters = inspect.signature(progress).parameters.values()
            accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters)
            accepted = {p.name for p in parameters}
        except (TypeError, ValueError):
            accepts_kwargs = True
            accepted = set()
        kwargs = (
            values
            if accepts_kwargs
            else {key: value for key, value in values.items() if key in accepted}
        )
        await progress(run_id, stage, **kwargs)

    def _register_task(task_id: str | None) -> None:
        if coordinator is not None and run_id is not None and task_id:
            coordinator.register_enqueued_task(run_id, task_id)

    # Stage-1 per-source progress: stage 1 serially fetches/collects each
    # selected source (single platform can block up to ~300s), so surface which
    # source is in flight and how many are done — otherwise the GUI bar sits at
    # 13% for minutes (init-progress spec Phase 1). done counts sources that
    # finished; total is the count of selected sources (B站 is the first).
    _stage1_source_total = sum(
        (
            include_bili,
            include_xhs,
            include_dy,
            include_yt,
            include_x,
            include_zhihu,
            include_reddit,
            include_bangumi,
        )
    )
    _stage1_source_done = 0
    _stage1_started_at = asyncio.get_running_loop().time()
    _stage1_deadline = (
        _stage1_started_at + collection_timeout_seconds
        if collection_timeout_seconds > 0
        else float("inf")
    )
    _stage1_budget_exhausted = False

    def _stage1_remaining_seconds() -> float:
        return max(0.0, _stage1_deadline - asyncio.get_running_loop().time())

    async def _await_stage1_operation(
        factory: Callable[[], Awaitable[Any]],
        *,
        label: str,
        max_wait_seconds: float,
    ) -> tuple[Any | None, bool]:
        """Run one source under both its own and stage-1's global budget."""
        nonlocal _stage1_budget_exhausted
        available = _stage1_remaining_seconds()
        budget = min(max(0.0, max_wait_seconds), available)
        if budget <= 0:
            _stage1_budget_exhausted = True
            return None, True
        started = asyncio.get_running_loop().time()
        operation: asyncio.Future[Any] = asyncio.ensure_future(factory())
        timed_out = False
        try:
            while not operation.done():
                elapsed = asyncio.get_running_loop().time() - started
                remaining = min(
                    budget - elapsed,
                    _stage1_remaining_seconds(),
                )
                if remaining <= 0:
                    timed_out = True
                    _stage1_budget_exhausted = _stage1_remaining_seconds() <= 0
                    break
                done_tasks, _ = await asyncio.wait({operation}, timeout=min(10.0, remaining))
                if operation in done_tasks:
                    break
                elapsed_int = max(0, int(asyncio.get_running_loop().time() - started))
                global_left = max(0, int(_stage1_remaining_seconds()))
                await _report_stage_progress(
                    1,
                    done=_stage1_source_done,
                    total=_stage1_source_total,
                    note=f"正在采集 {label} · 已等待 {elapsed_int}s · 阶段剩余最多 {global_left}s",
                    mode="indeterminate",
                    elapsed_seconds=elapsed_int,
                    max_seconds=max(1, int(budget)),
                    substantive=False,
                )
            if timed_out:
                operation.cancel()
                with suppress(BaseException):
                    await operation
                return None, True
            return await operation, False
        except asyncio.CancelledError:
            operation.cancel()
            with suppress(BaseException):
                await operation
            raise

    def _source_wait_seconds(env_name: str, fallback: float) -> float:
        try:
            return max(0.0, float(os.environ.get(env_name, str(fallback))))
        except (TypeError, ValueError):
            return fallback

    async def _run_extension_collector(
        collector: Callable[..., tuple[list[dict[str, Any]], dict[str, int], str]],
        task_id: str | None,
        *,
        label: str,
        env_name: str,
        default_wait_seconds: float,
    ) -> tuple[list[dict[str, Any]], dict[str, int], str]:
        """Run a blocking extension poll with a cooperative stop flag."""
        cancel_event = threading.Event()
        collector_done = threading.Event()
        wait_seconds = min(
            _source_wait_seconds(env_name, default_wait_seconds),
            _stage1_remaining_seconds(),
        )

        def _collect() -> tuple[list[dict[str, Any]], dict[str, int], str]:
            try:
                kwargs: dict[str, Any] = {}
                try:
                    parameters = inspect.signature(collector).parameters.values()
                    accepts_kwargs = any(
                        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters
                    )
                    accepted = {p.name for p in parameters}
                except (TypeError, ValueError):
                    accepts_kwargs = True
                    accepted = set()
                if accepts_kwargs or "max_wait_seconds" in accepted:
                    kwargs["max_wait_seconds"] = wait_seconds
                if accepts_kwargs or "cancel_event" in accepted:
                    kwargs["cancel_event"] = cancel_event
                return collector(task_id, **kwargs)
            finally:
                collector_done.set()

        try:
            result, timed_out = await _await_stage1_operation(
                lambda: asyncio.to_thread(_collect),
                label=label,
                # Leave one poll interval of outer grace; the global budget
                # remains the hard ceiling enforced by the helper.
                max_wait_seconds=wait_seconds + 0.75,
            )
        finally:
            cancel_event.set()
            # Cancelling asyncio.to_thread() cannot stop an already-running
            # worker. Give the cooperative 0.5s poll loop a bounded drain so a
            # terminal init does not leave a collector touching the task queue
            # after its run lock has been released. Poll the threading.Event
            # from the event loop instead of consuming a second executor slot.
            drain_deadline = asyncio.get_running_loop().time() + 1.0
            while not collector_done.is_set():
                remaining = drain_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.01, remaining))
        if timed_out or result is None:
            return [], {}, "timeout"
        return cast("tuple[list[dict[str, Any]], dict[str, int], str]", result)

    async def _stage1_begin_source(label: str, *, wait_hint: str = "") -> None:
        if coordinator is not None and run_id is not None:
            note = f"正在采集 {label}"
            if wait_hint:
                note += f" · {wait_hint}"
            await _report_stage_progress(
                1,
                done=_stage1_source_done,
                total=_stage1_source_total,
                note=note,
            )

    def _stage1_finish_source() -> None:
        nonlocal _stage1_source_done
        _stage1_source_done += 1

    async def _enqueue_register_kick(
        enqueue_fn: Callable[..., str | None], source: str
    ) -> str | None:
        """Enqueue a bootstrap task off-loop, then wake the extension.

        On the API path (coordinator set) the dispatcher kick is deferred until
        AFTER the task id is registered as init-owned, so a fast extension can't
        post the result before ownership is recorded (which would make the
        task-result handler treat init's own data as foreign and skip memory
        propagation). The CLI path keeps the helper's built-in kick and has no
        ownership to register.
        """
        if coordinator is not None:
            task_id = await asyncio.to_thread(lambda: enqueue_fn(kick=False))
            _register_task(task_id)
            if task_id:
                await asyncio.to_thread(_kick_task_dispatcher, source)
            return task_id
        return await asyncio.to_thread(enqueue_fn)

    # Enqueue the XHS bootstrap task FIRST so the browser extension can
    # run it in parallel with the slow B站 history/favs/follows fetches
    # below (~10–30s). XHS is HTTP-only on B站's side so there's no
    # browser-tab focus conflict; Douyin/YouTube are enqueued LATER,
    # serialised, to avoid two active-tab focus grabs racing.
    xhs_task_id = (
        (await _enqueue_register_kick(_enqueue_xhs_bootstrap_task, "xhs")) if include_xhs else None
    )
    if xhs_task_id:
        console.print("  [dim]已请求扩展拉小红书收藏 / 点赞（后台并行,不阻塞 B 站拉取）。[/dim]")

    # ── Stage 1: fetch + cross-platform bootstrap collect → propagate ──
    await _stage_started(1)
    _print_section_title("1/4 拉取数据")
    history: list[dict[str, Any]] = []
    favorites_data: list[dict[str, Any]] = []
    following_data: list[dict[str, Any]] = []
    if include_bili:
        await _stage1_begin_source("B 站")
        bili_result, bili_timed_out = await _await_stage1_operation(
            lambda: _fetch_bilibili_init_data(
                client,
                history_limit=history_limit,
                favorite_limit=favorite_limit,
                follow_limit=follow_limit,
            ),
            label="B 站",
            max_wait_seconds=_INIT_BILIBILI_COLLECTION_TIMEOUT_SECONDS,
        )
        if bili_timed_out or bili_result is None:
            console.print("  [yellow]B 站采集超过本阶段等待上限，已跳过并继续其他来源。[/yellow]")
        else:
            history, favorites_data, following_data = cast(
                "tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]",
                bili_result,
            )
            console.print(
                f"  浏览历史 [green]{len(history)}[/green] 条"
                f" / 收藏 [green]{len(favorites_data)}[/green] 个"
                f" / 关注 [green]{len(following_data)}[/green] 人"
            )
        _stage1_finish_source()
    else:
        console.print("  [dim]未选择 B 站来源,跳过 B 站历史 / 收藏 / 关注拉取。[/dim]")

    bangumi_events: list[dict[str, Any]] = []
    bangumi_scope_counts: dict[str, int] = {}
    bangumi_status = "skipped"
    if include_bangumi:
        await _stage1_begin_source("Bangumi", wait_hint="仅读取公开收藏")
        try:
            bangumi_result, bangumi_timed_out = await _await_stage1_operation(
                lambda: _fetch_bangumi_init_data(username=bangumi_username, token=bangumi_token),
                label="Bangumi",
                max_wait_seconds=_INIT_BILIBILI_COLLECTION_TIMEOUT_SECONDS,
            )
            if bangumi_timed_out or bangumi_result is None:
                bangumi_status = "timeout"
            else:
                bangumi_events, bangumi_scope_counts, bangumi_status = cast(
                    "tuple[list[dict[str, Any]], dict[str, int], str]",
                    bangumi_result,
                )
        except Exception as exc:
            bangumi_status = "failed"
            console.print(f"  [yellow]Bangumi 公开收藏读取失败: {exc}[/yellow]")
        _stage1_finish_source()
        if bangumi_status == "ok":
            status_text = ", ".join(
                f"{key}={value}" for key, value in sorted(bangumi_scope_counts.items())
            )
            console.print(
                f"  Bangumi 公开收藏 [green]{len(bangumi_events)}[/green] 条 ({status_text})"
            )
        elif bangumi_status == "missing_username":
            console.print(
                "  [yellow]Bangumi 来源已启用，但未配置公开用户名；"
                "本次只启用后续内容发现，不导入收藏信号。[/yellow]"
            )
        elif bangumi_status == "invalid_token":
            console.print(
                "  [yellow]Bangumi 个人令牌被拒绝（可能已过期或撤销）；"
                "请到 https://next.bgm.tv/demo/access-token 重新生成。[/yellow]"
            )
        elif bangumi_status == "empty":
            console.print("  [yellow]Bangumi 用户存在，但没有读到公开收藏。[/yellow]")
        elif bangumi_status == "timeout":
            console.print("  [yellow]Bangumi 公开收藏读取超时，已跳过并继续初始化。[/yellow]")

    # Bootstrap collectors poll a DB task queue with a blocking sleep —
    # run them in a worker thread (Database is check_same_thread=False) so
    # the API event loop isn't frozen for the collect window. CLI output /
    # ordering is unchanged (it's sequential here regardless).
    if include_xhs:
        await _stage1_begin_source("小红书", wait_hint="扩展未响应会在约 3 分钟后自动跳过")
    if include_xhs:
        xhs_events, xhs_scope_counts, xhs_status = await _run_extension_collector(
            _collect_xhs_bootstrap_events,
            xhs_task_id,
            label="小红书",
            env_name="OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS",
            default_wait_seconds=_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS,
        )
    else:
        xhs_events, xhs_scope_counts, xhs_status = [], {}, "skipped"
    if include_xhs:
        _stage1_finish_source()
    if xhs_status == "ok":
        console.print(
            "  小红书 "
            f"收藏 [green]{xhs_scope_counts.get('saved', 0)}[/green] 个"
            f" / 点赞 [green]{xhs_scope_counts.get('liked', 0)}[/green] 个"
            f" / 浏览记录 [green]{xhs_scope_counts.get('xhs_history', 0)}[/green] 个"
        )
    elif xhs_status == "empty":
        console.print(
            "  [yellow]小红书任务跑通但 0 条 notes —— "
            "可能未登录小红书 / 个人主页没有公开收藏 / 页面 state 漂移。[/yellow]"
        )
    elif xhs_status == "timeout":
        console.print(
            "  [dim]小红书初始化信号未导入：扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif xhs_status == "failed":
        console.print("  [yellow]小红书任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # Now (XHS done) enqueue Douyin. Serialised so the two browser-
    # focus-grabbing dispatchers don't race for the same active tab.
    dy_task_id = (
        (await _enqueue_register_kick(_enqueue_dy_bootstrap_task, "dy")) if include_dy else None
    )
    if dy_task_id:
        console.print(
            "  [dim]已请求扩展拉抖音发布 / 收藏 / 点赞 / 关注"
            "(开始抢一次浏览器焦点,~60-90 秒)。[/dim]"
        )
    if include_dy:
        await _stage1_begin_source("抖音", wait_hint="扩展未响应会在约 3 分钟后自动跳过")
    if include_dy:
        dy_events, dy_scope_counts, dy_status = await _run_extension_collector(
            _collect_dy_bootstrap_events,
            dy_task_id,
            label="抖音",
            env_name="OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS",
            default_wait_seconds=_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS,
        )
    else:
        dy_events, dy_scope_counts, dy_status = [], {}, "skipped"
    if include_dy:
        _stage1_finish_source()
    if dy_status == "ok":
        console.print(
            "  抖音 "
            f"发布 [green]{dy_scope_counts.get('dy_post', 0)}[/green] 条"
            f" / 收藏 [green]{dy_scope_counts.get('dy_collect', 0)}[/green] 个"
            f" / 点赞 [green]{dy_scope_counts.get('dy_like', 0)}[/green] 个"
            f" / 关注 [green]{dy_scope_counts.get('dy_follow', 0)}[/green] 人"
        )
    elif dy_status == "degraded":
        console.print(
            "  [yellow]抖音已导入部分信号，但至少一个范围未完成 API 分页；"
            "本次结果按不完整处理，请检查扩展日志后重试。[/yellow]"
        )
    elif dy_status == "empty":
        console.print(
            "  [yellow]抖音任务跑通但 0 条 videos —— "
            "未登录抖音(常见,抖音对未登录返回 200+空 body),或个人主页隐私设置阻拦。[/yellow]"
        )
    elif dy_status == "timeout":
        console.print(
            "  [dim]抖音初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif dy_status == "failed":
        console.print("  [yellow]抖音任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # YouTube is enqueued AFTER Douyin completes — same serialisation
    # rationale as XHS→Douyin: each dispatcher opens a foreground tab and
    # grabs focus; running two at once causes tab-focus races.
    yt_task_id = (
        (await _enqueue_register_kick(_enqueue_yt_bootstrap_task, "yt")) if include_yt else None
    )
    if yt_task_id:
        console.print(
            "  [dim]已请求扩展拉 YouTube 观看历史 / 订阅 / 点赞"
            "(开始抢一次浏览器焦点,~30-90 秒)。[/dim]"
        )
    if include_yt:
        await _stage1_begin_source("YouTube", wait_hint="扩展未响应会在约 5 分钟后自动跳过")
    if include_yt:
        yt_events, yt_scope_counts, yt_status = await _run_extension_collector(
            _collect_yt_bootstrap_events,
            yt_task_id,
            label="YouTube",
            env_name="OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS",
            default_wait_seconds=_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS,
        )
    else:
        yt_events, yt_scope_counts, yt_status = [], {}, "skipped"
    if include_yt:
        _stage1_finish_source()
    if yt_status == "ok":
        console.print(
            "  YouTube "
            f"观看历史 [green]{yt_scope_counts.get('yt_history', 0)}[/green] 条"
            f" / 订阅 [green]{yt_scope_counts.get('yt_subscriptions', 0)}[/green] 个"
            f" / 点赞 [green]{yt_scope_counts.get('yt_likes', 0)}[/green] 个"
        )
    elif yt_status == "empty":
        console.print(
            "  [yellow]YouTube 任务跑通但 0 条记录 —— 未登录 YouTube 或页面内容为空。[/yellow]"
        )
    elif yt_status == "timeout":
        console.print(
            "  [dim]YouTube 初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_YT_BOOTSTRAP_WAIT_SECONDS=300 延长等待。[/dim]"
        )
    elif yt_status == "failed":
        console.print("  [yellow]YouTube 任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # Zhihu is also plugin-backed and uses the browser's logged-in zhihu.com
    # session. Keep it serial with the other tab-driving sources.
    zhihu_task_id = (
        (await _enqueue_register_kick(_enqueue_zhihu_bootstrap_task, "zhihu"))
        if include_zhihu
        else None
    )
    if zhihu_task_id:
        console.print(
            "  [dim]已请求扩展拉知乎浏览 / 收藏 / 点赞(使用当前浏览器登录态,~30-90 秒)。[/dim]"
        )
    if include_zhihu:
        await _stage1_begin_source("知乎", wait_hint="扩展未响应会在约 3 分钟后自动跳过")
    if include_zhihu:
        zhihu_events, zhihu_scope_counts, zhihu_status = await _run_extension_collector(
            _collect_zhihu_bootstrap_events,
            zhihu_task_id,
            label="知乎",
            env_name="OPENBILICLAW_ZHIHU_BOOTSTRAP_WAIT_SECONDS",
            default_wait_seconds=_DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS,
        )
    else:
        zhihu_events, zhihu_scope_counts, zhihu_status = [], {}, "skipped"
    if include_zhihu:
        _stage1_finish_source()
    if zhihu_status == "ok":
        zhihu_activity_favorites = int(zhihu_scope_counts.get("zhihu_activity_favorite", 0))
        zhihu_favorites = (
            int(zhihu_scope_counts.get("zhihu_collection", 0)) + zhihu_activity_favorites
        )
        console.print(
            "  知乎 "
            f"浏览 [green]{zhihu_scope_counts.get('zhihu_read_history', 0)}[/green] 条"
            f" / 收藏 [green]{zhihu_favorites}[/green] 条"
            f" / 点赞 [green]{zhihu_scope_counts.get('zhihu_activity_like', 0)}[/green] 条"
        )
    elif zhihu_status == "empty":
        console.print(
            "  [yellow]知乎任务跑通但 0 条记录 —— 可能未登录知乎，或页面数据为空。[/yellow]"
        )
    elif zhihu_status == "login_required":
        console.print("  [yellow]知乎需要登录 —— 请先在当前浏览器登录知乎后重试 init。[/yellow]")
    elif zhihu_status == "timeout":
        console.print(
            "  [dim]知乎初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_ZHIHU_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif zhihu_status == "failed":
        console.print("  [yellow]知乎任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # X (Twitter): server-side cookie replay (no extension bootstrap task), so —
    # like B站 — fetch the user's own likes + bookmarks directly here. Skips
    # cleanly when X is disabled or the cookie isn't synced yet.
    x_likes_data: list[dict[str, Any]] = []
    x_bookmarks_data: list[dict[str, Any]] = []
    if include_x:
        await _stage1_begin_source("X")
        x_result, x_timed_out = await _await_stage1_operation(
            lambda: _fetch_x_init_data(
                likes_limit=_INIT_X_LIKES_LIMIT,
                bookmarks_limit=_INIT_X_BOOKMARKS_LIMIT,
            ),
            label="X",
            max_wait_seconds=_INIT_X_COLLECTION_TIMEOUT_SECONDS,
        )
        if x_timed_out or x_result is None:
            console.print("  [yellow]X 采集超过等待上限，已跳过并继续初始化。[/yellow]")
        else:
            x_likes_data, x_bookmarks_data = cast(
                "tuple[list[dict[str, Any]], list[dict[str, Any]]]", x_result
            )
        _stage1_finish_source()
        if x_likes_data or x_bookmarks_data:
            console.print(
                f"  X 点赞 [green]{len(x_likes_data)}[/green] 条"
                f" / 收藏 [green]{len(x_bookmarks_data)}[/green] 条"
            )
    reddit_task_id = (
        (await _enqueue_register_kick(_enqueue_reddit_bootstrap_task, "reddit"))
        if include_reddit
        else None
    )
    if reddit_task_id:
        console.print(
            "  [dim]已请求扩展拉 Reddit 收藏 / 点赞 / 订阅(使用当前浏览器登录态,~30-90 秒)。[/dim]"
        )
    if include_reddit:
        await _stage1_begin_source("Reddit", wait_hint="扩展未响应会在约 3 分钟后自动跳过")
    if include_reddit:
        reddit_events, reddit_scope_counts, reddit_status = await _run_extension_collector(
            _collect_reddit_bootstrap_events,
            reddit_task_id,
            label="Reddit",
            env_name="OPENBILICLAW_REDDIT_BOOTSTRAP_WAIT_SECONDS",
            default_wait_seconds=_DEFAULT_REDDIT_BOOTSTRAP_WAIT_SECONDS,
        )
    else:
        reddit_events, reddit_scope_counts, reddit_status = [], {}, "skipped"
    if include_reddit:
        _stage1_finish_source()
    if reddit_status == "ok":
        console.print(
            "  Reddit "
            f"收藏 [green]{reddit_scope_counts.get('reddit_saved', 0)}[/green] 条"
            f" / 点赞 [green]{reddit_scope_counts.get('reddit_upvoted', 0)}[/green] 条"
            f" / 订阅 [green]{reddit_scope_counts.get('reddit_subscribed', 0)}[/green] 个"
        )
    elif reddit_status == "empty":
        console.print(
            "  [yellow]Reddit 任务跑通但 0 条记录 —— 可能未登录 Reddit，"
            "或 saved/upvoted/subscribed 为空。[/yellow]"
        )
    elif reddit_status == "login_required":
        console.print(
            "  [yellow]Reddit 需要登录 —— 请先在当前浏览器登录 Reddit 后重试 init。[/yellow]"
        )
    elif reddit_status == "timeout":
        console.print(
            "  [dim]Reddit 初始化信号未导入:扩展未连接或任务仍在后台跑。"
            "可设 OPENBILICLAW_REDDIT_BOOTSTRAP_WAIT_SECONDS=180 延长等待。[/dim]"
        )
    elif reddit_status == "failed":
        console.print("  [yellow]Reddit 任务失败 —— 检查扩展日志,或重试 init。[/yellow]")

    # Build events from all data sources via the unified event_format
    # builder so B站 / 小红书 / future-source events share one shape.
    events = _build_bilibili_init_events(
        history=history,
        favorites_data=favorites_data,
        following_data=following_data,
    )
    bilibili_event_count = len(events)
    # X likes/bookmarks are direct-fetched here (no extension task handler to
    # propagate them), so — like B站 — they must be persisted in this run.
    # Appended before the events_to_persist snapshot below; the cross-platform
    # (xhs/dy/yt) extends happen after the snapshot since those are persisted by
    # their task-result handler instead.
    x_likes_events = [
        ev for tw in x_likes_data if (ev := _x_tweet_to_event(tw, event_type="like")) is not None
    ]
    x_bookmark_events = [
        ev
        for tw in x_bookmarks_data
        if (ev := _x_tweet_to_event(tw, event_type="favorite")) is not None
    ]
    events.extend(x_likes_events)
    events.extend(x_bookmark_events)
    x_event_count = len(x_likes_events) + len(x_bookmark_events)
    # Persist B站 + X events to memory here. Cross-platform (xhs/dy/yt) events
    # are propagated by the task-result handler — which, during init, only
    # propagates init-OWNED results and reuses its bootstrap-key dedupe (so a
    # force re-init within the task-reuse window doesn't double-insert). They
    # still feed *this* run's analyze/profile via the collected ``events`` list
    # below; memory persistence is owned by the handler on both CLI and API
    # paths (gui-init review §5e).
    events_to_persist = list(events)
    events_to_persist.extend(zhihu_events)
    events_to_persist.extend(reddit_events)
    events_to_persist.extend(bangumi_events)
    events.extend(xhs_events)
    events.extend(dy_events)
    events.extend(yt_events)
    events.extend(zhihu_events)
    events.extend(reddit_events)
    events.extend(bangumi_events)
    # With bilibili now optional, the floor is "at least one selected source
    # produced signals" — an all-empty run can't build a meaningful profile.
    if not events:
        if _stage1_budget_exhausted:
            raise GuidedInitError(
                "collection_timeout",
                "数据采集已达到 10 分钟总等待上限，且暂未取得可用于画像的行为信号。"
                "系统已停止继续等待平台或扩展，避免初始化锁死；请确认平台登录和扩展连接后重试。",
            )
        if include_bili and _stage1_source_total == 1:
            raise GuidedInitError(
                "empty_history",
                "B 站历史为空，收藏和关注也没有取得可用于画像的信号。"
                "请先在 B 站产生一些观看 / 收藏 / 关注记录，或启用其他数据来源后重试 init。",
            )
        raise GuidedInitError(
            "empty_signals",
            "所选数据来源没有拉到任何行为信号，无法生成初始画像。"
            "请确认对应平台已在浏览器登录（或扩展已连接）后重试 init。",
        )
    # Source-share tuning does an unlocked load_config/save_config. That's
    # fine for the CLI (single-process, no live runtime), but on the API path
    # it would mutate config.toml outside _CONFIG_SAVE_LOCK / rebuild_from_config
    # and race a live backend — so only the CLI (coordinator is None) does it
    # (gui-init review §5e). The API keeps default shares for the first run.
    if coordinator is None:
        _maybe_update_init_source_shares(
            {
                "bilibili": bilibili_event_count,
                "xiaohongshu": len(xhs_events),
                "douyin": len(dy_events),
                "youtube": len(yt_events),
                "twitter": x_event_count,
                "zhihu": len(zhihu_events),
                "reddit": len(reddit_events),
                "bangumi": len(bangumi_events),
            }
        )
    # Re-running init re-fetches the same snapshot; without this the ledger
    # counts every one of those rows a second time (measured: 56% duplicates).
    events_to_persist, already_imported = _drop_events_already_imported(
        getattr(memory, "_database", None),
        events_to_persist,
    )
    if already_imported:
        console.print(
            f"  [dim]跳过账本里已有的 {already_imported} 条信号"
            f"（重跑 init 不会把同一次行为记两遍）[/dim]"
        )
    propagate_events = getattr(memory, "propagate_events", None)
    if callable(propagate_events):
        await propagate_events(events_to_persist)
    else:
        for event in events_to_persist:
            await memory.propagate_event(event)
    await _stage_done(
        1,
        status="warning" if dy_status == "degraded" else "ok",
        reason="douyin_degraded" if dy_status == "degraded" else None,
    )

    # ── Stage 2: analyze preferences ──
    await _stage_started(2)
    _print_section_title("2/4 分析偏好")
    console.print(f"  总信号量: [green]{len(events)}[/green] 条事件")
    profile_analysis_concurrency = _profile_analysis_concurrency(soul_engine)
    # Progress-aware deadline: the idle limit is what actually catches a wedged
    # gateway, so the absolute ceiling can stay generous for slow-but-healthy
    # ones. ``profile_analysis_budget`` remains the number published to the GUI
    # as ``progress.max_seconds`` — it is now the absolute ceiling, i.e. still
    # the only limit that can end the stage on the clock.
    profile_analysis_idle_budget, profile_analysis_budget = _profile_analysis_deadlines(
        event_count=len(events),
        requested=profile_analysis_timeout_seconds,
        concurrency=profile_analysis_concurrency,
    )
    stage2_marker = _InitProgressMarker()

    # Per-chunk progress so stage 2 (a multi-minute chunked LLM batch) advances
    # instead of sitting static (init-progress spec Phase 1). We ALWAYS echo the
    # completion line to stdout (desktop.log captures stdout, not the logger's
    # openbiliclaw.log — so without this the desktop log shows only the eta
    # heartbeat and a stall is invisible) and, on the API path, also fan the
    # count onto coordinator.stage_progress for the GUI. ``_chunk_progress`` is
    # a live mirror the heartbeat reads so every tick shows "已完成 X/N 批";
    # when a chunk hangs that count freezes next to the growing clock.
    expected_chunk_total = max(
        1,
        (len(events) + DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE - 1)
        // DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    )
    _chunk_progress = {"done": 0, "total": expected_chunk_total}
    stage2_started_at = asyncio.get_running_loop().time()

    def _stage2_elapsed() -> int:
        return max(0, int(asyncio.get_running_loop().time() - stage2_started_at))

    async def _stage2_progress(done: int, total: int) -> None:
        _chunk_progress["done"] = done
        _chunk_progress["total"] = total
        # Real per-chunk completion — the only thing that counts as progress.
        stage2_marker.touch()
        console.print(f"  [dim]分析偏好：第 {done}/{total} 批完成[/dim]")
        await _report_stage_progress(
            2,
            done=done,
            total=total,
            note=f"第 {done}/{total} 批",
            elapsed_seconds=_stage2_elapsed(),
            max_seconds=max(1, int(profile_analysis_budget)) if profile_analysis_budget else 0,
        )

    def _chunk_status() -> str:
        total = _chunk_progress["total"]
        return f"已完成 {_chunk_progress['done']}/{total} 批"

    async def _stage2_tick(elapsed: int, eta_seconds: int) -> None:
        total = _chunk_progress["total"]
        await _report_stage_progress(
            2,
            done=_chunk_progress["done"],
            total=total,
            note=f"{_chunk_status()} · AI 已处理 {elapsed}s",
            mode="determinate" if total > 0 else "indeterminate",
            elapsed_seconds=elapsed,
            max_seconds=max(1, int(profile_analysis_budget)) if profile_analysis_budget else 0,
            substantive=False,
        )

    # Chunk the event list so bootstrap does bounded batch processing
    # instead of serialising one max-thinking call over hundreds of events.
    await _report_stage_progress(
        2,
        done=0,
        total=expected_chunk_total,
        note=(
            f"已完成 0/{expected_chunk_total} 批 · "
            f"AI 开始处理（并发上限 {profile_analysis_concurrency}）"
        ),
        elapsed_seconds=0,
        max_seconds=max(1, int(profile_analysis_budget)) if profile_analysis_budget else 0,
    )
    try:
        with _background_admission_bypass():
            await _await_with_progress_deadline(
                _run_with_progress(
                    soul_engine.analyze_events(
                        events,
                        event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
                        progress_callback=_stage2_progress,
                    ),
                    label="分析偏好（分片批处理）",
                    eta_seconds=180,
                    status_provider=_chunk_status,
                    progress_callback=_stage2_tick,
                ),
                marker=stage2_marker,
                idle_seconds=profile_analysis_idle_budget,
                absolute_seconds=profile_analysis_budget,
            )
    except _InitIdleTimeoutError as exc:
        raise GuidedInitError(
            "analyze_failed",
            _profile_analysis_idle_timeout_message(
                profile_analysis_idle_budget or _INIT_PROGRESS_IDLE_SECONDS
            ),
        ) from exc
    except TimeoutError as exc:
        raise GuidedInitError(
            "analyze_failed",
            _profile_analysis_absolute_timeout_message(
                profile_analysis_budget or _INIT_PROFILE_ANALYSIS_TIMEOUT_SECONDS,
                progress_note=_chunk_status(),
            ),
        ) from exc
    except Exception as exc:
        # Surface the real LLM cause (SSL / no provider / rate limit / timeout /
        # moderation) so the init page shows *why* stage 2 stalled instead of a
        # generic crash. Mirrors the stage-3 (build_initial_profile) handling —
        # this stage is equally LLM-heavy and was the silent-retry sink behind
        # "卡在分析偏好" reports (issue #113). CancelledError is NOT caught: it
        # propagates so the wrapper records `cancelled`, never `completed`.
        from openbiliclaw.llm.base import describe_llm_failure

        llm_reason = describe_llm_failure(exc)
        message = (
            f"偏好分析失败：{llm_reason}"
            if llm_reason
            else "偏好分析阶段出错。可稍后手动重试 `openbiliclaw init`。"
        )
        raise GuidedInitError("analyze_failed", message) from exc
    await _stage_done(2)

    # ── Stage 3: build and durably commit the full profile ──
    await _stage_started(3)
    _print_section_title("3/4 生成并保存完整画像")
    await _report_stage_progress(
        3,
        note="正在综合偏好、历史与认知线索",
        mode="indeterminate",
        elapsed_seconds=0,
        max_seconds=max(1, int(profile_build_timeout_seconds)),
    )
    combined_history: list[dict[str, Any]] = list(history)
    if favorites_data:
        # One favourite is one signal, so each becomes its own history row
        # alongside the views. They used to be collapsed into a single
        # "[收藏夹汇总]" item whose ``_favorites`` list nothing ever read — the
        # portrait saw the sentence "共 200 个收藏，涵盖: 默认收藏夹" and not a
        # single title. Deliberately choosing to save something is the
        # strongest signal a user emits, and the whole of it was invisible.
        #
        # ``event_type`` is what earns them the strong-signal weight and the
        # reserved share of the sample in ProfileBuilder, and what makes their
        # context line read "收藏了" rather than "看了".
        combined_history.extend(_favorites_to_history_rows(favorites_data))
        # The folder names stay as one aggregate line: they are the user's own
        # labels for what they save ("AI Agent", "学习"), which no per-item
        # field carries.
        combined_history.append(
            {
                "title": "[收藏夹汇总]",
                "_favorites_summary": f"共 {len(favorites_data)} 个收藏，"
                + "涵盖: "
                + ", ".join(
                    set(f.get("folder", "") for f in favorites_data[:100] if f.get("folder"))
                ),
            }
        )
    if following_data:
        combined_history.append(
            {
                "title": "[关注列表汇总]",
                "_following": following_data,
                "_following_summary": f"共关注 {len(following_data)} 人，"
                + "包括: "
                + ", ".join(f["name"] for f in following_data[:100]),
            }
        )
    if xhs_events:
        combined_history.extend(_xhs_events_to_history_items(xhs_events))
    if dy_events:
        combined_history.extend(_dy_events_to_history_items(dy_events))
    if yt_events:
        combined_history.extend(_yt_events_to_history_items(yt_events))
    if zhihu_events:
        combined_history.extend(_zhihu_events_to_history_items(zhihu_events))
    if reddit_events:
        combined_history.extend(_reddit_events_to_history_items(reddit_events))
    if bangumi_events:
        combined_history.extend(_bangumi_events_to_history_items(bangumi_events))
    # X likes/bookmarks previously only fed the analyze stage; feeding the
    # profile builder too keeps cross-source flow uniform AND guarantees a
    # non-empty profile input when X is the only selected source.
    if x_likes_events or x_bookmark_events:
        combined_history.extend(_x_events_to_history_items(x_likes_events + x_bookmark_events))

    async def _build_initial_profile() -> Any:
        with _background_admission_bypass():
            return await soul_engine.build_initial_profile(combined_history)

    async def _stage3_tick(elapsed: int, eta_seconds: int) -> None:
        await _report_stage_progress(
            3,
            note=f"AI 正在综合完整画像 · 已处理 {elapsed}s",
            mode="indeterminate",
            elapsed_seconds=elapsed,
            max_seconds=max(1, int(profile_build_timeout_seconds)),
            substantive=False,
        )

    profile_data: Any = None
    discovered_count = 0
    discover_exc: BaseException | None = None
    discovery_reason: str | None = None
    discovery_detail = ""

    # Profile is load-bearing. CancelledError is deliberately NOT caught so
    # the API wrapper records `cancelled`, never `completed`.
    try:
        profile_data = await asyncio.wait_for(
            _run_with_progress(
                _build_initial_profile(),
                label="生成并保存完整画像(单次 LLM 综合分析)",
                eta_seconds=70,
                progress_callback=_stage3_tick,
            ),
            timeout=profile_build_timeout_seconds if profile_build_timeout_seconds > 0 else None,
        )
    except Exception as exc:
        # Surface the real LLM cause (moderation refusal / no provider /
        # rate limit / timeout) so the init page shows *why* it failed.
        from openbiliclaw.llm.base import describe_llm_failure

        if isinstance(exc, TimeoutError):
            message = _INIT_PROFILE_BUILD_TIMEOUT_MESSAGE
        else:
            llm_reason = describe_llm_failure(exc)
            message = (
                f"画像生成失败：{llm_reason}"
                if llm_reason
                else "画像生成阶段出错。可稍后手动重试 `openbiliclaw init`。"
            )
        raise GuidedInitError("profile_failed", message) from exc

    await _report_stage_progress(
        3,
        done=1,
        total=1,
        note="完整画像已保存，下一步将严格基于它生成内容",
    )
    await _stage_done(3)

    # ── Stage 4: only the committed full profile may drive discovery ──
    await _stage_started(4)
    _print_section_title("4/4 建立首轮可用内容池")

    _stage4_live_done = 0
    _stage4_live_total = 4
    _stage4_live_note = "准备发现候选内容"
    stage4_marker = _InitProgressMarker()
    # Stage 4 reports real progress (per plan stage, from run_init_backfill), so
    # it gets the idle+absolute pair too. Ticks are excluded for the same reason
    # as stage 2: they fire on a timer, not on work.
    stage4_idle_budget: float | None = (
        _INIT_DISCOVERY_PROGRESS_IDLE_SECONDS
        if discovery_timeout_seconds == _INIT_DISCOVERY_TIMEOUT_SECONDS
        else None
    )
    stage4_absolute_budget = discovery_timeout_seconds if discovery_timeout_seconds > 0 else None

    async def _stage4_progress(done: int, total: int, note: str) -> None:
        nonlocal _stage4_live_done, _stage4_live_total, _stage4_live_note
        stage4_marker.touch()
        _stage4_live_done = done
        _stage4_live_total = total
        _stage4_live_note = note
        if coordinator is not None and run_id is not None:
            await _report_stage_progress(4, done=done, total=total, note=note)
        else:
            console.print(f"  [dim]首轮内容池：{note}（{done}/{total}）[/dim]")

    async def _stage4_tick(elapsed: int, eta_seconds: int) -> None:
        await _report_stage_progress(
            4,
            done=_stage4_live_done,
            total=_stage4_live_total,
            note=f"{_stage4_live_note} · 已处理 {elapsed}s",
            mode="indeterminate",
            elapsed_seconds=elapsed,
            max_seconds=max(1, int(discovery_timeout_seconds)),
            substantive=False,
        )

    await _stage4_progress(0, 4, "完整画像已就绪，准备发现候选内容")
    backfill_kwargs: dict[str, Any] = {
        "target_pool_count": target_pool_count,
        "label_suffix": "",
    }
    try:
        signature = inspect.signature(discover_backfill)
    except (TypeError, ValueError):
        accepts_progress_callback = True
    else:
        accepts_progress_callback = "progress_callback" in signature.parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    if accepts_progress_callback:
        backfill_kwargs["progress_callback"] = _stage4_progress

    # Stage 4 is best-effort once the full profile exists: timeout/failure is
    # terminal *partial success*, and clients may enter the app while the
    # restarted runtime continues replenishment. Cancellation still propagates.
    try:
        discovered_count = await _await_with_progress_deadline(
            _run_with_progress(
                discover_backfill(profile_data, **backfill_kwargs),
                label="基于完整画像生成首轮内容池",
                eta_seconds=300,
                progress_callback=_stage4_tick,
            ),
            marker=stage4_marker,
            idle_seconds=stage4_idle_budget,
            absolute_seconds=stage4_absolute_budget,
        )
        await _stage4_progress(4, 4, "首轮内容已完成评分与推荐文案，可直接浏览")
    except Exception as exc:
        from openbiliclaw.runtime.refresh import InitialPoolUnavailableError

        discovered_count = max(0, int(getattr(exc, "discovered_count", 0) or 0))
        discover_exc = exc
        if isinstance(exc, _InitIdleTimeoutError):
            discovery_reason = "discovery_timeout"
            discovery_detail = _INIT_DISCOVERY_IDLE_MESSAGE
        elif isinstance(exc, TimeoutError):
            discovery_reason = "discovery_timeout"
            discovery_detail = _INIT_DISCOVERY_TIMEOUT_MESSAGE
        elif isinstance(exc, InitialPoolUnavailableError):
            discovery_reason = "discovery_partial"
            discovery_detail = (
                "画像已生成，首轮发现也已完成"
                f"（发现 {exc.discovered_count} 条候选），但尚无候选完成评分与推荐文案，"
                "因此目前没有可直接浏览的首轮内容。本次初始化已按“部分完成”结束，"
                "系统会在后台继续补齐；你可以先进入应用，并检查 AI 服务与平台登录状态。"
            )
        else:
            discovery_reason = "discovery_partial"
            discovery_detail = _INIT_DISCOVERY_PARTIAL_MESSAGE
    await _stage_done(
        4,
        status="warning" if discover_exc is not None else "ok",
        reason=discovery_reason,
    )

    return InitResult(
        history=history,
        favorites_data=favorites_data,
        following_data=following_data,
        events=events,
        bilibili_event_count=bilibili_event_count,
        xhs_events=xhs_events,
        xhs_scope_counts=xhs_scope_counts,
        xhs_status=xhs_status,
        dy_events=dy_events,
        dy_scope_counts=dy_scope_counts,
        dy_status=dy_status,
        yt_events=yt_events,
        yt_scope_counts=yt_scope_counts,
        yt_status=yt_status,
        zhihu_events=zhihu_events,
        zhihu_scope_counts=zhihu_scope_counts,
        zhihu_status=zhihu_status,
        reddit_events=reddit_events,
        reddit_scope_counts=reddit_scope_counts,
        reddit_status=reddit_status,
        profile_data=profile_data,
        discovered_count=discovered_count,
        discovery_error=discover_exc is not None,
        discover_exc=discover_exc,
        discovery_reason=discovery_reason,
        discovery_detail=discovery_detail,
        bangumi_events=bangumi_events,
        bangumi_scope_counts=bangumi_scope_counts,
        bangumi_status=bangumi_status,
    )


@app.command()
def init(
    no_bilibili: bool = typer.Option(
        False,
        "--no-bilibili",
        help="跳过 B 站数据接入(默认包含；init 至少需要保留一个数据来源)。",
    ),
    no_xhs: bool = typer.Option(
        False,
        "--no-xhs",
        help="跳过小红书数据接入(默认会问)。",
    ),
    skip_xhs_prompt: bool = typer.Option(
        False,
        "--yes-xhs",
        help="跳过小红书的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_douyin: bool = typer.Option(
        False,
        "--no-douyin",
        help="跳过抖音数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_dy_prompt: bool = typer.Option(
        False,
        "--yes-douyin",
        help="跳过抖音的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_youtube: bool = typer.Option(
        False,
        "--no-youtube",
        help="跳过 YouTube 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_yt_prompt: bool = typer.Option(
        False,
        "--yes-youtube",
        help="跳过 YouTube 的 y/n 提问,直接启用(适合脚本化场景)。",
    ),
    no_x: bool = typer.Option(
        False,
        "--no-x",
        help="跳过 X (Twitter) 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_x_prompt: bool = typer.Option(
        False,
        "--yes-x",
        help="跳过 X 的 y/n 提问,直接启用 X 来源(适合脚本化场景)。",
    ),
    no_zhihu: bool = typer.Option(
        False,
        "--no-zhihu",
        help="跳过知乎数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_zhihu_prompt: bool = typer.Option(
        False,
        "--yes-zhihu",
        help="跳过知乎的 y/n 提问,直接启用知乎来源(适合脚本化场景)。",
    ),
    no_reddit: bool = typer.Option(
        False,
        "--no-reddit",
        help="跳过 Reddit 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_reddit_prompt: bool = typer.Option(
        False,
        "--yes-reddit",
        help="跳过 Reddit 的 y/n 提问,直接启用 Reddit 数据接入(适合脚本化场景)。",
    ),
    no_bangumi: bool = typer.Option(
        False,
        "--no-bangumi",
        help="跳过 Bangumi 数据接入(默认非交互模式下就是跳过)。",
    ),
    skip_bangumi_prompt: bool = typer.Option(
        False,
        "--yes-bangumi",
        help="跳过 Bangumi 的 y/n 提问，直接启用来源。",
    ),
    bangumi_username: str = typer.Option(
        "",
        "--bangumi-username",
        help="用于初始化的公开 Bangumi 用户名；留空则读配置或交互输入。",
    ),
    bangumi_token: str = typer.Option(
        "",
        "--bangumi-token",
        help=(
            "Bangumi 个人令牌（推荐，自动识别当前用户并可读私密收藏）；"
            "留空则读 [sources.bangumi].access_token。生成: "
            "https://next.bgm.tv/demo/access-token"
        ),
    ),
    bilibili_history_limit: int | None = typer.Option(
        None,
        "--bilibili-history-limit",
        min=0,
        help="B 站历史初始化信号上限；默认 500，0 表示拉全部历史。",
    ),
    bilibili_favorite_limit: int | None = typer.Option(
        None,
        "--bilibili-favorite-limit",
        min=0,
        help="B 站收藏初始化信号上限；默认 500，0 表示跳过收藏。",
    ),
    bilibili_follow_limit: int | None = typer.Option(
        None,
        "--bilibili-follow-limit",
        min=0,
        help="B 站关注 UP 初始化信号上限；默认 100，0 表示跳过关注。",
    ),
) -> None:
    """首次运行：拉取历史、生成画像并补足首轮发现池."""
    _prepare_init_runtime()

    # Snapshot the highest llm_usage row id seen at start so the
    # post-init cost summary can scope to "this init only" rather
    # than the user's lifetime ledger. Wrapped in try/except —
    # billing is best-effort and must not block init startup.
    init_start_usage_id: int | None = None
    try:
        init_start_usage_id = _get_runtime_database().max_llm_usage_id()
    except Exception:
        init_start_usage_id = None

    # B站 is optional like every other source (v0.3.118+): --no-bilibili or
    # OPENBILICLAW_NO_BILIBILI=1 skips it, as long as ≥1 source remains.
    include_bili = not (
        no_bilibili or os.environ.get("OPENBILICLAW_NO_BILIBILI", "").strip() == "1"
    )

    client = _build_bilibili_client() if include_bili else None
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_page_title("初始化 OpenBiliClaw", "首次运行引导")
    stage1_label = (
        "拉 B 站历史 / 收藏 / 关注（时长看你的列表大小）"
        if include_bili
        else "拉取所选平台数据（B 站已跳过）"
    )
    # No total-duration forecast: it depends on the selected platforms, the
    # collected history AND the provider's latency, so any number here would be
    # wrong for someone and make a healthy long run read as broken (field
    # report 2026-07-20). State the variability, then let the per-step heartbeat
    # report elapsed time as evidence of progress.
    console.print(
        "[bold yellow]⏱  这一步首次运行耗时差别很大，取决于你勾了几个平台、"
        "拉到多少历史，以及 AI 服务的快慢，请保持网络畅通别中断。[/bold yellow]\n"
        "  只要还在出结果就不会被打断，慢一些是正常的。\n"
        "  四个阶段会严格依次执行，完整画像保存后才开始内容发现：\n"
        f"    1/4  {stage1_label}\n"
        "    2/4  分析偏好（LLM 调用，按事件量分片，每片单独计时）\n"
        "    3/4  生成并保存完整画像（单次 LLM 调用）\n"
        "    4/4  生成首轮可用推荐（发现 + 评估 + 推荐文案）\n"
        "[dim]全程会打印已用时和已完成的量，不要以为卡住了——"
        "远程 AI 服务单次响应就可能要几分钟。[/dim]\n"
    )
    if not include_bili:
        console.print(
            "[dim]  跳过 B 站数据接入"
            f"({'命令行 --no-bilibili' if no_bilibili else 'OPENBILICLAW_NO_BILIBILI=1'})。[/dim]"
        )

    # v0.3.89+: ask user whether the backend should be reachable from
    # the local network (0.0.0.0) so mobile /m/ works out of the box.
    allow_lan = _ask_network_binding()
    _persist_api_host_choice(allow_lan=allow_lan)
    _maybe_setup_password_in_init(allow_lan=allow_lan)

    if include_bili:
        (
            resolved_bilibili_history_limit,
            resolved_bilibili_favorite_limit,
            resolved_bilibili_follow_limit,
        ) = _ask_init_bilibili_limits(
            history_limit=bilibili_history_limit,
            favorite_limit=bilibili_favorite_limit,
            follow_limit=bilibili_follow_limit,
        )
    else:
        resolved_bilibili_history_limit = 0
        resolved_bilibili_favorite_limit = 0
        resolved_bilibili_follow_limit = 0

    # v0.3.27+: ask the user whether to include xhs data, with a prep
    # checklist when they opt in. Defaults stay off unless the user
    # explicitly enables XHS:
    #   --no-xhs          forces skip
    #   --yes-xhs         skips the y/n + checklist (scripted opt-in)
    #   OPENBILICLAW_NO_XHS=1   env var skip
    # Default (interactive, no flags): prompt with default N.
    if no_xhs:
        include_xhs = False
        console.print("[dim]  跳过小红书数据接入(命令行 --no-xhs)。[/dim]")
    elif skip_xhs_prompt:
        include_xhs = True
    else:
        include_xhs = _ask_xhs_inclusion()

    # Same resolution order for the Douyin opt-in. Default is
    # off-in-non-interactive (see _ask_dy_inclusion docstring).
    if no_douyin:
        include_dy = False
        console.print("[dim]  跳过抖音数据接入(命令行 --no-douyin)。[/dim]")
    elif skip_dy_prompt:
        include_dy = True
    else:
        include_dy = _ask_dy_inclusion()

    if no_youtube:
        include_yt = False
        console.print("[dim]  跳过 YouTube 数据接入(命令行 --no-youtube)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_YOUTUBE", "").strip() == "1":
        include_yt = False
        console.print("[dim]  跳过 YouTube 数据接入(OPENBILICLAW_NO_YOUTUBE=1)。[/dim]")
    elif skip_yt_prompt:
        include_yt = True
    else:
        include_yt = _ask_yt_inclusion()

    # X (Twitter) is server-side cookie replay — no init bootstrap task, so this
    # only flips [sources.twitter].enabled; the producer fetches later once the
    # x.com cookie is synced. Same resolution order as the other opt-ins.
    if no_x:
        include_x = False
        console.print("[dim]  跳过 X 数据接入(命令行 --no-x)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_X", "").strip() == "1":
        include_x = False
        console.print("[dim]  跳过 X 数据接入(OPENBILICLAW_NO_X=1)。[/dim]")
    elif skip_x_prompt:
        include_x = True
    else:
        include_x = _ask_x_inclusion()

    if no_zhihu:
        include_zhihu = False
        console.print("[dim]  跳过知乎数据接入(命令行 --no-zhihu)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_ZHIHU", "").strip() == "1":
        include_zhihu = False
        console.print("[dim]  跳过知乎数据接入(OPENBILICLAW_NO_ZHIHU=1)。[/dim]")
    elif skip_zhihu_prompt:
        include_zhihu = True
    else:
        include_zhihu = _ask_zhihu_inclusion()

    if no_reddit:
        include_reddit = False
        console.print("[dim]  跳过 Reddit 来源启用(命令行 --no-reddit)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_REDDIT", "").strip() == "1":
        include_reddit = False
        console.print("[dim]  跳过 Reddit 来源启用(OPENBILICLAW_NO_REDDIT=1)。[/dim]")
    elif skip_reddit_prompt:
        include_reddit = True
    else:
        include_reddit = _ask_reddit_inclusion()

    if no_bangumi:
        include_bangumi = False
        console.print("[dim]  跳过 Bangumi 数据接入(命令行 --no-bangumi)。[/dim]")
    elif os.environ.get("OPENBILICLAW_NO_BANGUMI", "").strip() == "1":
        include_bangumi = False
        console.print("[dim]  跳过 Bangumi 数据接入(OPENBILICLAW_NO_BANGUMI=1)。[/dim]")
    elif skip_bangumi_prompt:
        include_bangumi = True
    else:
        include_bangumi = _ask_bangumi_inclusion()

    selected_bangumi_username = ""
    selected_bangumi_token = ""
    if include_bangumi:
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.bangumi_client import (
            BangumiAPIError,
            resolve_access_token_identity,
            validate_bangumi_access_token,
            validate_bangumi_username,
        )

        bangumi_cfg = load_config().sources.bangumi
        configured_username = str(bangumi_cfg.username or "").strip()
        configured_token = str(bangumi_cfg.access_token or "").strip()
        try:
            selected_bangumi_token = validate_bangumi_access_token(
                bangumi_token or configured_token
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--bangumi-token") from exc
        raw_username = str(bangumi_username or configured_username).strip()
        if selected_bangumi_token:
            # Validate the token live and resolve the account before persisting
            # anything: reject a bad/expired token with its real cause (project
            # rule 7) instead of writing an unusable secret.
            try:
                resolved = asyncio.run(resolve_access_token_identity(selected_bangumi_token))
            except BangumiAPIError as exc:
                if exc.code == "unauthorized":
                    _print_status_panel(
                        "error",
                        "Bangumi 个人令牌无效",
                        "令牌被 Bangumi 拒绝（缺失、错误或已过期）。请到 "
                        "https://next.bgm.tv/demo/access-token 重新生成后重试。",
                    )
                    raise typer.Exit(code=1) from exc
                _print_status_panel("error", "Bangumi 令牌校验失败", str(exc))
                raise typer.Exit(code=1) from exc
            if raw_username and raw_username != resolved:
                console.print(
                    f"[dim]  Bangumi 令牌对应用户为 {resolved}，已覆盖填写的 {raw_username}。[/dim]"
                )
            selected_bangumi_username = resolved
        else:
            if not raw_username and _is_interactive_terminal():
                raw_username = str(
                    typer.prompt(
                        "公开 Bangumi 用户名(留空则只启用内容发现)",
                        default="",
                        show_default=False,
                    )
                    or ""
                ).strip()
            if not raw_username:
                # Zero-config fallback: the browser extension reports the
                # logged-in bgm.tv account's public username into runtime
                # state. Priority: token /v0/me > explicit username >
                # extension-reported username.
                extension_username, extension_verified = _load_extension_bangumi_identity()
                if extension_username:
                    raw_username = extension_username
                    if extension_verified:
                        console.print(
                            f"[dim]  Bangumi 使用浏览器扩展识别到的账号 "
                            f"{extension_username}。[/dim]"
                        )
                    else:
                        console.print(
                            f"[yellow]  Bangumi 使用浏览器扩展识别到的账号 "
                            f"{extension_username}（未经 bgm.tv 校验，可能不准）。"
                            "如果不是你本人，请用 --bangumi-username 指定。[/yellow]"
                        )
            try:
                selected_bangumi_username = validate_bangumi_username(raw_username)
            except ValueError as exc:
                raise typer.BadParameter(str(exc), param_hint="--bangumi-username") from exc

    selected_sources = (
        include_bili,
        include_xhs,
        include_dy,
        include_yt,
        include_x,
        include_zhihu,
        include_reddit,
        include_bangumi,
    )
    if not any(selected_sources):
        _print_status_panel(
            "error",
            "没有可用的数据来源",
            "已跳过 B 站且未启用任何其他平台——init 至少需要一个数据来源。"
            "去掉 --no-bilibili，或配合 --yes-xhs / --yes-douyin / "
            "--yes-youtube / --yes-x / --yes-zhihu "
            "/ --yes-reddit / --yes-bangumi 启用其他来源。",
        )
        raise typer.Exit(code=1)

    profile_signal_sources = selected_sources[:-1]
    if include_bangumi and not selected_bangumi_username and not any(profile_signal_sources):
        _print_status_panel(
            "error",
            "Bangumi 缺少令牌或用户名",
            "只选择 Bangumi 初始化时，需提供 --bangumi-token（推荐，自动识别当前用户）"
            "或 --bangumi-username（公开用户名），或先在浏览器登录 bgm.tv 让扩展自动识别；"
            "如果只想启用内容发现，请先保存来源配置而不是运行 init。",
        )
        raise typer.Exit(code=1)
    if include_bangumi and not selected_bangumi_username:
        console.print(
            "[yellow]  Bangumi 未填公开用户名：本次仅启用条目发现，"
            "画像由其他已选来源提供。[/yellow]"
        )

    _persist_init_source_enabled_flags(
        include_bili=include_bili,
        include_xhs=include_xhs,
        include_dy=include_dy,
        include_yt=include_yt,
        include_x=include_x,
        include_zhihu=include_zhihu,
        include_reddit=include_reddit,
        include_bangumi=include_bangumi,
        bangumi_username=selected_bangumi_username,
        bangumi_token=selected_bangumi_token,
    )

    # gui-init (B2): the four init stages now run inside the shared async
    # pipeline run_guided_init so the API can reuse them without nesting
    # event loops. The CLI injects the one-shot discovery backfill and
    # renders the summary below from the returned InitResult.
    try:
        result = asyncio.run(
            run_guided_init(
                client=client,
                memory=memory,
                soul_engine=soul_engine,
                history_limit=resolved_bilibili_history_limit,
                favorite_limit=resolved_bilibili_favorite_limit,
                follow_limit=resolved_bilibili_follow_limit,
                include_bili=include_bili,
                include_xhs=include_xhs,
                include_dy=include_dy,
                include_yt=include_yt,
                include_x=include_x,
                include_zhihu=include_zhihu,
                include_reddit=include_reddit,
                include_bangumi=include_bangumi,
                bangumi_username=selected_bangumi_username,
                bangumi_token=selected_bangumi_token,
                target_pool_count=_INIT_POOL_TARGET_COUNT,
                discover_backfill=_run_init_discovery_backfill_async,
            )
        )
    except GuidedInitError as exc:
        if exc.reason == "empty_history":
            _print_status_panel("warning", "历史为空", exc.message)
        elif exc.reason == "empty_signals":
            _print_status_panel("warning", "没有拉到信号", exc.message)
        else:
            _print_status_panel("error", "失败", exc.message)
        raise typer.Exit(code=1) from exc

    history = result.history
    favorites_data = result.favorites_data
    following_data = result.following_data
    events = result.events
    xhs_events = result.xhs_events
    xhs_scope_counts = result.xhs_scope_counts
    xhs_status = result.xhs_status
    dy_events = result.dy_events
    dy_scope_counts = result.dy_scope_counts
    dy_status = result.dy_status
    yt_events = result.yt_events
    yt_scope_counts = result.yt_scope_counts
    yt_status = result.yt_status
    zhihu_events = result.zhihu_events
    zhihu_scope_counts = result.zhihu_scope_counts
    zhihu_status = result.zhihu_status
    reddit_events = result.reddit_events
    reddit_scope_counts = result.reddit_scope_counts
    reddit_status = result.reddit_status
    bangumi_events = list(getattr(result, "bangumi_events", []))
    bangumi_scope_counts = dict(getattr(result, "bangumi_scope_counts", {}))
    bangumi_status = str(getattr(result, "bangumi_status", "skipped"))
    discovered_count = result.discovered_count
    discovery_error = result.discovery_error
    dy_degraded = dy_status == "degraded"
    partial_success = discovery_error or dy_degraded

    if result.discover_exc is not None:
        _print_status_panel(
            "warning",
            "部分完成",
            result.discovery_detail + " 也可稍后手动执行 `openbiliclaw discover`。",
        )
    if dy_degraded:
        _print_status_panel(
            "warning",
            "抖音采集部分完成",
            "dy_status=degraded：已采到的抖音事件仍已用于画像建模，"
            "但至少一个范围未能证明分页完整；请检查扩展日志后重试补齐。",
        )

    _print_status_panel(
        "success" if not partial_success else "warning",
        "初始化完成" if not partial_success else "初始化部分完成",
        "初始化摘要",
    )

    # v0.3.58+: explicit per-platform breakdown so the user (and the
    # AI agent driving the install) can see exactly what signals fed
    # the soul profile. Previously the summary just said "小红书事件 N"
    # which dropped to 0 when bootstrap_profile was async-pending —
    # now we surface scope-level counts (saved / liked / xhs_history)
    # AND the bilibili history / favorites / following breakdown,
    # plus a total. xhs_scope_counts is set whether the task succeeded
    # or returned empty, so this also surfaces "0 / 0 / 0" cases that
    # suggest the user wasn't logged into XHS.
    # Use the pipeline's snapshot, not a subtraction over ``events`` — the
    # event list also carries X likes/bookmarks, which the old subtraction
    # silently lumped into the B站 row (glaring once B站 itself is optional).
    bilibili_events = result.bilibili_event_count
    xhs_saved = int(xhs_scope_counts.get("saved", 0))
    xhs_liked = int(xhs_scope_counts.get("liked", 0))
    xhs_history = int(xhs_scope_counts.get("xhs_history", 0))
    dy_post = int(dy_scope_counts.get("dy_post", 0))
    dy_collect = int(dy_scope_counts.get("dy_collect", 0))
    dy_like = int(dy_scope_counts.get("dy_like", 0))
    dy_follow = int(dy_scope_counts.get("dy_follow", 0))
    yt_history_count = int(yt_scope_counts.get("yt_history", 0))
    yt_subs_count = int(yt_scope_counts.get("yt_subscriptions", 0))
    yt_likes_count = int(yt_scope_counts.get("yt_likes", 0))
    zhihu_history_count = int(zhihu_scope_counts.get("zhihu_read_history", 0))
    zhihu_favorite_count = int(zhihu_scope_counts.get("zhihu_collection", 0)) + int(
        zhihu_scope_counts.get("zhihu_activity_favorite", 0)
    )
    zhihu_like_count = int(zhihu_scope_counts.get("zhihu_activity_like", 0))
    reddit_saved_count = int(reddit_scope_counts.get("reddit_saved", 0))
    reddit_upvoted_count = int(reddit_scope_counts.get("reddit_upvoted", 0))
    reddit_subscribed_count = int(reddit_scope_counts.get("reddit_subscribed", 0))
    bangumi_wish_count = int(bangumi_scope_counts.get("wish", 0))
    bangumi_done_count = int(bangumi_scope_counts.get("done", 0))
    bangumi_doing_count = int(bangumi_scope_counts.get("doing", 0))
    summary_rows: list[tuple[str, str]] = [
        ("📺 B 站观看历史", f"{len(history)} 条"),
        ("📺 B 站收藏夹", f"{len(favorites_data)} 条"),
        ("📺 B 站关注 UP", f"{len(following_data)} 人"),
        ("🌐 B 站 入库事件", f"{bilibili_events} 条"),
        ("📕 小红书 收藏(saved)", f"{xhs_saved} 条"),
        ("📕 小红书 点赞(liked)", f"{xhs_liked} 条"),
        ("📕 小红书 浏览记录", f"{xhs_history} 条"),
        ("🌐 小红书 入库事件", f"{len(xhs_events)} 条"),
        ("🎵 抖音 发布", f"{dy_post} 条"),
        ("🎵 抖音 收藏", f"{dy_collect} 个"),
        ("🎵 抖音 点赞", f"{dy_like} 个"),
        ("🎵 抖音 关注", f"{dy_follow} 人"),
        ("🌐 抖音 入库事件", f"{len(dy_events)} 条"),
        (
            "🎵 抖音 采集状态(dy_status)",
            "部分完成 (degraded)" if dy_degraded else dy_status,
        ),
        ("▶ YouTube 观看历史", f"{yt_history_count} 条"),
        ("▶ YouTube 订阅频道", f"{yt_subs_count} 个"),
        ("▶ YouTube 点赞", f"{yt_likes_count} 个"),
        ("🌐 YouTube 入库事件", f"{len(yt_events)} 条"),
        ("知乎 浏览", f"{zhihu_history_count} 条"),
        ("知乎 收藏", f"{zhihu_favorite_count} 条"),
        ("知乎 点赞", f"{zhihu_like_count} 条"),
        ("🌐 知乎 入库事件", f"{len(zhihu_events)} 条"),
        ("Reddit 收藏(saved)", f"{reddit_saved_count} 条"),
        ("Reddit 点赞(upvoted)", f"{reddit_upvoted_count} 条"),
        ("Reddit 订阅 subreddit", f"{reddit_subscribed_count} 个"),
        ("🌐 Reddit 入库事件", f"{len(reddit_events)} 条"),
        ("Bangumi 想看/想读/想玩", f"{bangumi_wish_count} 条"),
        ("Bangumi 看过/读过/玩过", f"{bangumi_done_count} 条"),
        ("Bangumi 在看/在读/在玩", f"{bangumi_doing_count} 条"),
        ("🌐 Bangumi 入库事件", f"{len(bangumi_events)} 条"),
        ("📊 画像建模总事件", f"{len(events)} 条"),
        ("✅ 灵魂画像", "已生成"),
        ("🔍 首轮发现内容", f"{discovered_count} 条"),
    ]
    _print_key_value_table("初始化摘要", summary_rows)

    # If the XHS task didn't get any data, surface the likely cause
    # so the user knows whether to re-run with the extension installed.
    if (xhs_saved + xhs_liked + xhs_history) == 0 and xhs_status != "skipped":
        console.print(
            "[dim]ℹ️  小红书 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.xiaohongshu.com / 任务仍在后台跑。装好扩展后重新跑 "
            "[cyan]openbiliclaw init --yes-xhs[/cyan] 可补齐。[/dim]"
        )
    if (yt_history_count + yt_subs_count + yt_likes_count) == 0 and yt_status != "skipped":
        console.print(
            "[dim]ℹ️  YouTube 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.youtube.com / 任务仍在后台跑。装好扩展后重新跑 "
            "[cyan]openbiliclaw init --yes-youtube[/cyan] 可补齐。[/dim]"
        )
    if (
        zhihu_history_count + zhihu_favorite_count + zhihu_like_count
    ) == 0 and zhihu_status != "skipped":
        console.print(
            "[dim]ℹ️  知乎 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.zhihu.com / 任务仍在后台跑。装好扩展后重新跑 "
            "[cyan]openbiliclaw init --yes-zhihu[/cyan] 可补齐。[/dim]"
        )
    if (
        reddit_saved_count + reddit_upvoted_count + reddit_subscribed_count
    ) == 0 and reddit_status != "skipped":
        console.print(
            "[dim]ℹ️  Reddit 0 条信号入库。最常见原因:扩展未装 / 浏览器没登录 "
            "https://www.reddit.com / saved、upvoted、订阅列表为空或任务仍在后台跑。"
            "装好扩展后重新跑 [cyan]openbiliclaw init --yes-reddit[/cyan] 可补齐。[/dim]"
        )
    if not bangumi_events and bangumi_status not in {"skipped", "missing_username"}:
        console.print(
            "[dim]ℹ️  Bangumi 0 条信号入库。请确认用户名存在，且收藏已设为公开。"
            "可用 [cyan]openbiliclaw fetch-bangumi --username <name>[/cyan] 只读验证。[/dim]"
        )

    source_parts = []
    if bilibili_events > 0:
        source_parts.append(f"[green]{bilibili_events}[/green] 条 B 站信号")
    if len(xhs_events) > 0:
        source_parts.append(f"[green]{len(xhs_events)}[/green] 条小红书信号")
    if len(dy_events) > 0:
        source_parts.append(f"[green]{len(dy_events)}[/green] 条抖音信号")
    if len(yt_events) > 0:
        source_parts.append(f"[green]{len(yt_events)}[/green] 条 YouTube 信号")
    if len(zhihu_events) > 0:
        source_parts.append(f"[green]{len(zhihu_events)}[/green] 条知乎信号")
    if len(reddit_events) > 0:
        source_parts.append(f"[green]{len(reddit_events)}[/green] 条 Reddit 信号")
    if len(bangumi_events) > 0:
        source_parts.append(f"[green]{len(bangumi_events)}[/green] 条 Bangumi 信号")
    if len(source_parts) > 1:
        console.print(
            "[dim]ℹ️  本次画像综合了 "
            + " + ".join(source_parts)
            + "。后续 daemon 会持续从这些来源增量补充。[/dim]"
        )

    # Phase E (v0.3.28+): print cost breakdown for THIS init only,
    # scoped by the row-id snapshot taken before any LLM call ran.
    # Lets users immediately see "init 这次花了 ¥X,其中 X% 在 discovery
    # 评估" rather than having to manually run `openbiliclaw cost`.
    if init_start_usage_id is not None:
        _print_init_cost_summary(init_start_usage_id)

    # Notify the running API server so the extension refreshes immediately.
    _notify_running_server_init_completed()


def _print_init_cost_summary(since_id: int) -> None:
    """Print this-init-only LLM cost breakdown by caller."""
    try:
        db = _get_runtime_database()
        snapshot = db.query_llm_usage_since_id(since_id=since_id)
    except Exception:
        return  # never block init success on a billing query
    total = snapshot.get("total", {})
    if not total or total.get("calls", 0) == 0:
        return
    by_caller = snapshot.get("by_caller", [])
    total_cost = float(total.get("cost_cny", 0.0)) or 1e-9

    total_prompt = int(total.get("prompt_tokens", 0))
    total_cached = int(total.get("cached_input_tokens", 0) or 0)
    cache_blurb = ""
    if total_prompt > 0 and total_cached > 0:
        overall_hit = total_cached / total_prompt * 100
        cache_blurb = f" / cache 命中 {overall_hit:.0f}%"

    summary_table = Table(
        show_header=True,
        header_style="bold green",
        title=(
            f"本次 init LLM 花费 — 总 {total['calls']:,} 次调用 "
            f"≈ ¥{total['cost_cny']:.4f}{cache_blurb}"
        ),
    )
    summary_table.add_column("Caller (模块.动作)", no_wrap=True)
    summary_table.add_column("调用数", justify="right")
    summary_table.add_column("token in→out", justify="right")
    summary_table.add_column("cache", justify="right")
    summary_table.add_column("¥ 占比", justify="right", style="bold yellow")
    for row in by_caller:
        share = float(row["cost_cny"]) / total_cost * 100
        prompt_tok = int(row["prompt_tokens"])
        cached_tok = int(row.get("cached_input_tokens", 0) or 0)
        if prompt_tok > 0 and cached_tok > 0:
            hit_pct = cached_tok / prompt_tok * 100
            cache_cell = (
                f"[green]{hit_pct:.0f}%[/green]"
                if hit_pct >= 60
                else (
                    f"[yellow]{hit_pct:.0f}%[/yellow]"
                    if hit_pct >= 30
                    else f"[red]{hit_pct:.0f}%[/red]"
                )
            )
        else:
            cache_cell = "[dim]—[/dim]"
        summary_table.add_row(
            row["caller"] or "[dim](untagged)[/dim]",
            f"{row['calls']:,}",
            f"{row['prompt_tokens']:,}→{row['completion_tokens']:,}",
            cache_cell,
            f"¥{row['cost_cny']:.4f} ({share:.0f}%)",
        )
    console.print(summary_table)
    console.print(
        "[dim]💡 想看历史累积花费跑 `openbiliclaw cost` (默认 7 天) / "
        "`openbiliclaw cost --by caller --days 30` 看 30 天按模块拆分。"
        "cache 列里红色 (<30%) 的 caller 说明 prompt 前缀不稳,可以 audit 一下。[/dim]"
    )


def _notify_running_server_init_completed(
    *,
    base_url: str = "http://127.0.0.1:8420",
) -> None:
    """POST to the running API server to announce init completion.

    Best-effort: silently ignored when the server is not running.
    """
    import urllib.request

    url = f"{base_url}/api/init-completed"
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=3):
            console.print("[dim]已通知后端服务，插件将自动刷新。[/dim]")
    except Exception:
        # Server not running — nothing to notify, and that's fine.
        pass


@app.command("rebuild-profile")
def rebuild_profile(
    limit: int = typer.Option(
        5000,
        "--limit",
        help="从数据库加载的最大事件数（默认 5000）。",
    ),
    source: str = typer.Option(
        "",
        "--source",
        help="只用指定来源：bilibili / xiaohongshu / douyin / youtube，留空=全部。",
    ),
    no_analyze: bool = typer.Option(
        False,
        "--no-analyze",
        help="跳过 analyze_events，直接重跑 build_initial_profile。",
    ),
) -> None:
    """从数据库重新生成灵魂画像（调试用）。

    从已存储的行为事件重跑完整的偏好分析 + 画像生成流程，
    无需重新从任何平台拉取数据。适合：

    \\b
      - 调整了 LLM prompt 后验证效果
      - 新接入平台后补充旧数据重跑
      - init 中途中断后只补跑画像阶段
    """
    import json as _json

    _prepare_init_runtime()
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_page_title("重新生成灵魂画像", "rebuild-profile")

    init_start_usage_id: int | None = None
    with suppress(Exception):
        init_start_usage_id = _get_runtime_database().max_llm_usage_id()

    # ── 1. 从 DB 加载事件 ────────────────────────────────────────────
    console.print(f"  [dim]从数据库加载最多 {limit} 条事件...[/dim]")
    raw_rows = memory.query_events(limit=limit)

    # metadata 在 DB 中以 JSON 文本存储；context 是纯文本（v0.3.23+）。
    events: list[dict[str, Any]] = []
    for row in raw_rows:
        ev = dict(row)
        meta_raw = ev.get("metadata")
        if isinstance(meta_raw, str) and meta_raw:
            try:
                parsed = _json.loads(meta_raw)
                ev["metadata"] = parsed if isinstance(parsed, dict) else {}
            except _json.JSONDecodeError:
                ev["metadata"] = {}
        events.append(ev)

    # 来源过滤
    source = source.strip().lower()
    if source:
        events = [
            e
            for e in events
            if str((e.get("metadata") or {}).get("source_platform", "")).lower() == source
        ]

    if not events:
        console.print(
            "[yellow]  没有找到事件。"
            + (f"来源 '{source}' 不存在，或" if source else "")
            + "请先运行 [cyan]openbiliclaw init[/cyan] 拉取数据。[/yellow]"
        )
        raise typer.Exit(code=1)

    # 按来源平台打印分布
    from collections import Counter

    platform_counts: Counter[str] = Counter()
    for ev in events:
        platform_counts[str((ev.get("metadata") or {}).get("source_platform", "unknown"))] += 1
    console.print(f"  已加载 [green]{len(events)}[/green] 条事件：")
    for platform, count in sorted(platform_counts.items(), key=lambda x: -x[1]):
        console.print(f"    {platform}: [green]{count}[/green] 条")

    # ── 2. 偏好分析 ──────────────────────────────────────────────────
    if not no_analyze:
        _print_section_title("1/2 分析偏好")
        console.print(f"  总信号量: [green]{len(events)}[/green] 条")
        asyncio.run(
            _run_with_progress(
                soul_engine.analyze_events(
                    events,
                    event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
                ),
                label="分析偏好（分片并发）",
                eta_seconds=180,
            )
        )
    else:
        console.print("  [dim]跳过 analyze_events（--no-analyze）。[/dim]")

    # ── 3. 画像生成 ──────────────────────────────────────────────────
    section_label = "2/2 生成画像" if not no_analyze else "1/1 生成画像"
    _print_section_title(section_label)
    asyncio.run(
        _run_with_progress(
            soul_engine.build_initial_profile(events),
            label="生成灵魂画像（单次 LLM 综合分析）",
            eta_seconds=70,
        )
    )

    _print_status_panel("success", "完成", "灵魂画像已重新生成")

    if init_start_usage_id is not None:
        _print_init_cost_summary(init_start_usage_id)

    _notify_running_server_init_completed()


def _run_single_source_bootstrap(
    *,
    source_label: str,
    enqueue: Callable[[], str | None],
    collect: Callable[[str | None], tuple[list[dict[str, Any]], dict[str, int], str]],
    wait_seconds: float,
    summary_renderer: Callable[[dict[str, int], str, int], None],
) -> None:
    """Shared core for ``fetch-douyin`` / ``fetch-xhs`` standalone commands.

    Pure pull pipeline — enqueue → kick → wait for completion →
    render scope_counts. Does NOT touch B站 auth, does NOT propagate
    events to memory. The daemon's
    ``/api/sources/{xhs,dy}/task-result`` handler ALREADY propagates
    incoming events to memory when it receives partials, so a CLI-side
    propagate would double-write. Init still runs the soul pipeline
    (preference / awareness / soul) on top — this command is the
    isolated 'just verify the extension can pull data' rung beneath
    that, useful for testing one platform at a time.
    """
    _print_page_title(f"{source_label} 数据拉取", "扩展任务 → 后端入库")
    console.print(
        f"[dim]入队 {source_label} bootstrap 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]"
    )

    task_id = enqueue()
    if not task_id:
        console.print(
            f"[bold red]无法入队 {source_label} 任务[/bold red]"
            " — 看上面的提示(数据库 / 预算 / 任务表问题)。"
        )
        raise typer.Exit(code=1)

    events, scope_counts, status_label = collect(task_id)
    summary_renderer(scope_counts, status_label, len(events))
    if status_label in {"timeout", "failed"}:
        raise typer.Exit(code=1)


@app.command("profile-consolidate")
def profile_consolidate(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="真正写入合并结果。默认 dry-run：只打印建议，不改任何数据。",
    ),
    revert: str = typer.Option(
        "",
        "--revert",
        help="按 run_id 回滚一次已应用的整理（备份在 data/memory/consolidation_runs/）。",
    ),
    migrate_categories: bool = typer.Option(
        False,
        "--migrate-categories",
        help="一次性把存量一级分类迁移到固定词表（默认 dry-run，配 --apply 写入）。",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="把 likes 整理边界从默认 top-512 开到全量标签库（嫌疑簇 32/批送审）。",
    ),
) -> None:
    """用 LLM 整理合并画像里重复的喜欢 / 讨厌主题。

    兴趣标签和避雷主题会不断积累措辞变体（「智能体开发」vs
    「智能体开发与实现」），把进入 prompt 的兴趣名额挤占掉。
    本命令按「规则合并 → embedding 聚类 → LLM 裁决 → 校验执行」
    的流水线做同义合并（likes 看权重 top-512 + 全量避雷主题，
    LLM 裁决每批 32 簇分批执行）。

    \b
      - 默认 dry-run，先看建议再决定
      - --apply 写入,自动备份到 data/memory/consolidation_runs/
      - --migrate-categories 一次性分类词表迁移（同样 dry-run/--apply/--revert）
      - --full 一次性全量清理 likes 长尾标签（与 --migrate-categories 互斥）
      - 审计记录追加到 data/memory/soul_changelog.md
    """
    import asyncio as _asyncio

    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import build_embedding_service
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.soul.consolidator import ProfileConsolidator

    _print_page_title("画像整理", "profile-consolidate")

    cfg = load_config()
    memory = _build_memory_manager()
    llm_service = None
    registry = None
    try:
        registry = _build_registry()
        llm_service = LLMService(
            registry=registry,
            memory=memory,
            usage_recorder=_build_usage_recorder(),
            module_overrides=module_overrides_from_config(cfg),
            concurrency=cfg.llm.concurrency,
            concurrency_gate=_build_llm_concurrency_gate(),
        )
    except Exception as exc:
        console.print(f"[yellow]  LLM 不可用（{exc}）— 只做规则合并与聚类预览。[/yellow]")
    embedding_service = None
    if registry is not None:
        try:
            embedding_service = build_embedding_service(cfg, registry)
        except Exception:
            embedding_service = None
    if embedding_service is None:
        console.print("[dim]  embedding 服务不可用，退回子串聚类。[/dim]")

    if full and migrate_categories:
        console.print("[bold red]  --full 与 --migrate-categories 不能同时使用。[/bold red]")
        console.print("[dim]  推荐顺序：先 --migrate-categories --apply，再 --full --apply。[/dim]")
        raise typer.Exit(code=1)

    if full:
        raw_interests = memory.get_layer("preference").data.get("interests", [])
        interest_count = len([item for item in raw_interests if isinstance(item, dict)])
        likes_boundary = max(interest_count, 128)
        console.print(f"  [cyan]--full：likes 边界开到全量（{likes_boundary} 条）。[/cyan]")
        consolidator = ProfileConsolidator(
            memory=memory,
            llm_service=llm_service,
            embedding_service=embedding_service,
            likes_boundary=likes_boundary,
            like_target_upper=cfg.scheduler.profile_consolidation_like_target_upper,
            like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
            archive_enabled=cfg.scheduler.profile_consolidation_archive_enabled,
            database=_get_runtime_database(),
        )
    else:
        consolidator = ProfileConsolidator(
            memory=memory,
            llm_service=llm_service,
            embedding_service=embedding_service,
            like_target_upper=cfg.scheduler.profile_consolidation_like_target_upper,
            like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
            archive_enabled=cfg.scheduler.profile_consolidation_archive_enabled,
            database=_get_runtime_database(),
        )

    if revert.strip():
        ok = consolidator.revert(revert.strip())
        if ok:
            console.print(f"  [green]已回滚 run {revert.strip()}，画像与覆盖层均已恢复。[/green]")
            console.print("  [dim]被回滚的合并已记入 no-merge 记忆，下轮整理不会重做。[/dim]")
        else:
            console.print(f"[bold red]  回滚失败：找不到 run 记录 {revert.strip()}。[/bold red]")
            raise typer.Exit(code=1)
        return

    if migrate_categories:
        from openbiliclaw.soul.category_migration import CategoryMigrator

        migrator = CategoryMigrator(memory=memory, llm_service=llm_service)
        migration_report = _asyncio.run(migrator.run(dry_run=not apply))
        for err in migration_report.errors:
            console.print(f"[yellow]  ⚠ {err}[/yellow]")
        console.print(
            f"  现存分类: {len(migration_report.histogram)} 个，"
            f"标签 {sum(migration_report.histogram.values())} 条"
        )
        for old, new in sorted(
            migration_report.mapping.items(),
            key=lambda item: -migration_report.histogram.get(item[0], 0),
        ):
            console.print(f"  {old}({migration_report.histogram.get(old, 0)}) → [bold]{new}[/bold]")
        if migration_report.mapping:
            suffix = "  [yellow]⚠ 超过 10%[/yellow]" if migration_report.other_ratio > 0.10 else ""
            console.print(f"\n  「其他」占比: {migration_report.other_ratio:.1%}{suffix}")
        if not apply and migration_report.mapping:
            console.print("\n  [dim]满意的话用 --apply 真正写入。[/dim]")
        if migration_report.applied:
            console.print(
                "\n  [dim]已备份，"
                f"run_id={migration_report.run_id}（--revert {migration_report.run_id} 可回滚）"
                "[/dim]"
            )
        # 只有「LLM 服务不可用」是降级只读预览（打印 histogram 即成功，code=0）；
        # LLM 调用异常 / 映射校验失败必须非零退出，脚本化调用才能区分失败与预览。
        degraded = migration_report.errors == ["llm: service unavailable"]
        if migration_report.errors and not migration_report.mapping and not degraded:
            raise typer.Exit(code=1)
        return

    mode_label = "[bold]apply[/bold]" if apply else "dry-run（加 --apply 才会写入）"
    console.print(f"  模式: {mode_label}")
    report = _asyncio.run(consolidator.run(dry_run=not apply))

    if report.errors:
        for err in report.errors:
            console.print(f"[yellow]  ⚠ {err}[/yellow]")
    if report.likes_before > report.likes_target_upper:
        console.print(
            f"  [cyan]likes 动态聚类阈值:[/cyan] cosine ≥ {report.like_similarity_threshold:.2f}"
        )
    console.print(f"  嫌疑簇送审: {report.clusters_sent} 个")
    for rule_merge in report.rule_merges:
        console.print(f"  [cyan][规则][/cyan] {rule_merge}")
    for merge in report.merges:
        raw_members = merge.get("members", [])
        member_items = raw_members if isinstance(raw_members, list) else []
        members = " / ".join(str(m) for m in member_items)
        scope = "兴趣" if merge.get("scope") == "likes" else "避雷"
        console.print(
            f"  [green][{scope}][/green] {members} → [bold]{merge.get('canonical')}[/bold]"
        )
    for rejected in report.rejected_clusters:
        console.print(f"  [dim][放弃簇] {rejected}[/dim]")
    console.print(
        f"\n  兴趣: {report.likes_before} → {report.likes_after}"
        f"    避雷: {report.dislikes_before} → {report.dislikes_after}"
    )
    if report.archived_interests:
        console.print(
            f"  [cyan]归档低权重兴趣:[/cyan] {len(report.archived_interests)} 个"
            f"（目标 ≤ {report.likes_target_upper}，整理水位 {report.likes_target_soft}）"
        )
    if report.inventory_reason:
        console.print(f"  [yellow]库存说明:[/yellow] {report.inventory_reason}")
    if not apply and (report.merges or report.rule_merges):
        console.print("\n  [dim]满意的话用 --apply 真正写入。[/dim]")
    if apply and (report.merges or report.rule_merges or report.archived_interests):
        console.print(f"\n  [dim]已备份，run_id={report.run_id}[/dim]")


@app.command("fetch-douyin")
def fetch_douyin(
    wait_seconds: float = typer.Option(
        _DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s,4 个 scope 串行 + 滚动 + 兜底)。",
    ),
) -> None:
    """单独触发抖音 bootstrap 拉取(纯执行,不跑 init 的画像 / 发现层).

    流程:CLI 入队 → /api/sources/dy/kick(WS push 立即唤醒扩展)→ 扩展 dispatcher
    跑完 4 个 scope → POST 回 /api/sources/dy/task-result → daemon propagate
    事件到 memory(daemon 端自己干,CLI 不再 propagate 一次)。

    适合什么时候用:
      - 单独测试抖音的扩展能不能拉数据(不污染 init 的画像 / 发现池逻辑)
      - 已经 init 过画像后,补一次抖音拉取
      - 调扩展或诊断风控时反复跑

    前提:
      1. ``openbiliclaw start`` daemon 在跑(kick 才有人接)
      2. 浏览器扩展已装、service-worker 在线
      3. 浏览器登录了 https://www.douyin.com
    """

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  抖音 "
                f"发布 [green]{scope_counts.get('dy_post', 0)}[/green] 条"
                f" / 收藏 [green]{scope_counts.get('dy_collect', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('dy_like', 0)}[/green] 个"
                f" / 关注 [green]{scope_counts.get('dy_follow', 0)}[/green] 人"
            )
            console.print(f"  共 [green]{event_count}[/green] 条事件已由 daemon 写入 memory。")
        elif status_label == "degraded":
            console.print(
                "  [yellow]抖音已导入部分信号，但至少一个范围未完成 API 分页；"
                "任务已标记为不完整，请检查扩展日志后重试。[/yellow]"
            )
            console.print(f"  已保留 [yellow]{event_count}[/yellow] 条有效事件。")
        elif status_label == "empty":
            console.print(
                "  [yellow]抖音任务跑通但 0 条 videos —— 未登录抖音(常见,"
                "抖音对未登录返回 200+空 body),或风控触发。[/yellow]"
            )
        elif status_label == "timeout":
            console.print(
                "  [dim]抖音任务超时:扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 240 重试,或确认 daemon + 扩展都在跑。[/dim]"
            )
        elif status_label == "failed":
            console.print("  [yellow]抖音任务失败 —— 检查扩展日志。[/yellow]")

    _run_single_source_bootstrap(
        source_label="抖音",
        enqueue=_enqueue_dy_bootstrap_task,
        collect=lambda tid: _collect_dy_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


@app.command("search-douyin")
def search_douyin(
    keywords: list[str] = _DOUYIN_SEARCH_KEYWORDS_OPTION,
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="等扩展回搜索结果的最大秒数(默认 180s)。",
    ),
    max_items_per_keyword: int = typer.Option(
        20,
        "--max-items-per-keyword",
        min=1,
        help="每个关键词最多抓取多少条视频候选。",
    ),
) -> None:
    """通过浏览器插件执行抖音搜索 discovery smoke."""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected_keywords = split_csv_values(keywords)
    _print_page_title("抖音搜索发现", "浏览器插件任务 → dy_tasks 结果")
    console.print(f"[dim]入队抖音搜索任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _enqueue_dy_search_task(
        selected_keywords,
        max_items_per_keyword=max_items_per_keyword,
    )
    if not task_id:
        raise typer.Exit(code=1)

    videos, counts, status_label = _collect_dy_search_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "ok":
        console.print(f"  抖音搜索 [green]{counts.get('dy_search', len(videos))}[/green] 条候选")
        for index, video in enumerate(videos[:5], start=1):
            title = str(video.get("title", "") or "（无标题）")
            author = str(video.get("author", "") or "")
            url = str(video.get("url", "") or "")
            suffix = f" [dim]{author}[/dim]" if author else ""
            console.print(f"  {index}. {title}{suffix}")
            if url:
                console.print(f"     [dim]{url}[/dim]")
        return
    if status_label == "empty":
        console.print(
            "  [yellow]抖音搜索任务跑通但 0 条候选 —— 搜索页可能仍被风控软空，"
            "或页面 DOM / 接口字段漂移。[/yellow]"
        )
        return
    if status_label == "timeout":
        console.print(
            "  [dim]抖音搜索任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/dim]"
        )
        raise typer.Exit(code=1)
    if status_label == "failed":
        console.print("  [yellow]抖音搜索任务失败 —— 检查扩展日志。[/yellow]")
        raise typer.Exit(code=1)


@app.command("fetch-xhs")
def fetch_xhs(
    wait_seconds: float = typer.Option(
        _DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s)。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="忽略近期小红书 bootstrap 任务，强制重新拉取收藏 / 点赞。",
    ),
) -> None:
    """单独测试小红书 bootstrap(独立于 ``init``).

    用于在不重新跑完整 init 的情况下逐项验证小红书端到端链路。
    需要 daemon + 扩展 + 浏览器登录 https://www.xiaohongshu.com。
    """

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  小红书 "
                f"收藏 [green]{scope_counts.get('saved', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('liked', 0)}[/green] 个"
                f" / 浏览记录 [green]{scope_counts.get('xhs_history', 0)}[/green] 个"
            )
            console.print(f"  共生成 [green]{event_count}[/green] 条事件。")
        elif status_label == "empty":
            console.print(
                "  [yellow]小红书任务跑通但 0 条 notes —— 可能未登录 /"
                "个人主页没有公开收藏 / 页面 state 漂移。[/yellow]"
            )
        elif status_label == "timeout":
            console.print(
                "  [dim]小红书任务超时:扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 240 重试。[/dim]"
            )
        elif status_label == "failed":
            console.print("  [yellow]小红书任务失败 —— 检查扩展日志。[/yellow]")

    _run_single_source_bootstrap(
        source_label="小红书",
        enqueue=(lambda: _enqueue_xhs_bootstrap_task(force=True))
        if force
        else _enqueue_xhs_bootstrap_task,
        collect=lambda tid: _collect_xhs_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


@app.command("fetch-youtube")
def fetch_youtube(
    wait_seconds: float = typer.Option(
        _DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 240s，YouTube 滚动比较慢)。",
    ),
) -> None:
    """单独测试 YouTube bootstrap（独立于 ``init``）。

    用于在不重新跑完整 init 的情况下验证 YouTube 端到端链路。
    需要 daemon + 扩展 + 浏览器登录 https://www.youtube.com。

    \b
    采集范围：
      yt_history      — /feed/history        观看历史 (弱信号)
      yt_subscriptions — /feed/channels       订阅频道 (强信号)
      yt_likes        — /playlist?list=LL    点赞视频 (强信号)
    """

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            console.print(
                "  YouTube "
                f"观看历史 [green]{scope_counts.get('yt_history', 0)}[/green] 条"
                f" / 订阅 [green]{scope_counts.get('yt_subscriptions', 0)}[/green] 个"
                f" / 点赞 [green]{scope_counts.get('yt_likes', 0)}[/green] 个"
            )
            console.print(f"  共生成 [green]{event_count}[/green] 条事件。")
        elif status_label == "empty":
            console.print(
                "  [yellow]YouTube 任务跑通但 0 条数据 —— "
                "可能未登录 YouTube / 页面还未渲染完 / 选择器失效。[/yellow]"
            )
        elif status_label == "timeout":
            console.print(
                "  [dim]YouTube 任务超时：扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 360 重试。[/dim]"
            )
        elif status_label == "failed":
            console.print("  [yellow]YouTube 任务失败 —— 检查扩展日志。[/yellow]")

    _run_single_source_bootstrap(
        source_label="YouTube",
        enqueue=_enqueue_yt_bootstrap_task,
        collect=lambda tid: _collect_yt_bootstrap_events(tid, max_wait_seconds=wait_seconds),
        wait_seconds=wait_seconds,
        summary_renderer=_render,
    )


@app.command("fetch-zhihu")
def fetch_zhihu(
    profile_slug: str = typer.Option(
        "",
        "--profile-slug",
        help=(
            "知乎个人主页 slug，例如 https://www.zhihu.com/people/<slug>。"
            "不提供时扩展会尝试从当前知乎登录态自动识别。"
        ),
    ),
    wait_seconds: float = typer.Option(
        _DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数(默认 180s)。",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="忽略近期知乎 bootstrap 任务，强制重新拉取事件。",
    ),
    write_memory: bool = typer.Option(
        False,
        "--write-memory",
        help="将本次抓到的知乎事件写入 memory；默认只做抓取 smoke。",
    ),
    rebuild_profile: bool = typer.Option(
        False,
        "--rebuild-profile",
        help="写入 memory 后用本次知乎事件重建画像（会触发真实 LLM 调用）。",
    ),
) -> None:
    """单独测试知乎事件拉取(默认独立于 ``init``，不生成画像)。

    需要 daemon + 扩展 + 浏览器登录 https://www.zhihu.com。扩展会在知乎
    页面内用当前登录态拉取最近浏览、收藏夹内容和个人动态中的点赞 / 收藏。
    传 ``--profile-slug`` 可手动指定用户主页；不传时扩展会尝试自动识别。
    默认只读取任务结果并打印统计；传 ``--write-memory`` 才写入 memory，
    传 ``--rebuild-profile`` 会继续触发画像生成。
    """
    write_memory = write_memory or rebuild_profile

    def _render(scope_counts: dict[str, int], status_label: str, event_count: int) -> None:
        if status_label == "ok":
            activity_favorites = scope_counts.get("zhihu_activity_favorite", 0)
            total_favorites = scope_counts.get("zhihu_collection", 0) + activity_favorites
            console.print(
                "  知乎 "
                f"浏览 [green]{scope_counts.get('zhihu_read_history', 0)}[/green] 条"
                f" / 收藏 [green]{total_favorites}[/green] 条"
                f" / 点赞 [green]{scope_counts.get('zhihu_activity_like', 0)}[/green] 条"
            )
            if rebuild_profile:
                suffix = "将写入 memory 并重建画像。"
            elif write_memory:
                suffix = "将写入 memory。"
            else:
                suffix = "未触发画像生成。"
            console.print(f"  共抓取并转换 [green]{event_count}[/green] 条事件；{suffix}")
        elif status_label == "empty":
            console.print(
                "  [yellow]知乎任务跑通但 0 条数据 —— "
                "可能未登录知乎 / 浏览历史关闭 / 收藏夹为空 / 接口字段漂移。[/yellow]"
            )
        elif status_label == "timeout":
            console.print(
                "  [dim]知乎任务超时:扩展未连接 / 任务还在跑。可加 --wait-seconds 240 重试。[/dim]"
            )
        elif status_label == "failed":
            console.print("  [yellow]知乎任务失败 —— 检查扩展日志。[/yellow]")
        elif status_label == "login_required":
            console.print(
                "  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。"
                "请先在当前浏览器登录知乎，再用 --force 重试。[/yellow]"
            )

    def _enqueue() -> str | None:
        # A write/rebuild run must not silently reuse a previous smoke task that
        # was already collected without persistence.
        dedupe_disabled = force or write_memory
        previous = os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS")
        if dedupe_disabled:
            os.environ["OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS"] = "0"
        try:
            return _enqueue_zhihu_bootstrap_task(
                profile_slug=profile_slug,
                profile_update=False,
            )
        finally:
            if dedupe_disabled:
                if previous is None:
                    os.environ.pop("OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS", None)
                else:
                    os.environ["OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS"] = previous

    _print_page_title("知乎 数据拉取", "扩展任务 → 后端入库")
    console.print(f"[dim]入队 知乎 bootstrap 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")

    task_id = _enqueue()
    if not task_id:
        console.print(
            "[bold red]无法入队 知乎 任务[/bold red] — 看上面的提示(数据库 / 预算 / 任务表问题)。"
        )
        raise typer.Exit(code=1)

    events, scope_counts, status_label = _collect_zhihu_bootstrap_events(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    _render(scope_counts, status_label, len(events))
    if status_label != "ok":
        return

    if write_memory:
        written, skipped = _write_events_to_memory(events, source="zhihu")
        console.print(
            f"  [green]已写入 memory: {written} 条知乎事件"
            f"[/green]{f'，跳过重复 {skipped} 条。' if skipped else '。'}"
        )

    if rebuild_profile:
        _prepare_init_runtime()
        soul_engine = _build_soul_engine()
        _print_section_title("1/2 分析知乎偏好")
        asyncio.run(
            _run_with_progress(
                soul_engine.analyze_events(events, event_chunk_size=200),
                label="分析知乎偏好",
                eta_seconds=180,
            )
        )
        _print_section_title("2/2 生成画像")
        asyncio.run(
            _run_with_progress(
                soul_engine.build_initial_profile(_zhihu_events_to_history_items(events)),
                label="生成灵魂画像",
                eta_seconds=70,
            )
        )
        _print_status_panel("success", "完成", "知乎事件已写入并完成画像重建")


@app.command("fetch-bangumi")
def fetch_bangumi(
    username: str = typer.Option(
        "",
        "--username",
        "-u",
        help="公开 Bangumi 用户名；不提供时读取 [sources.bangumi].username。",
    ),
    token: str = typer.Option(
        "",
        "--token",
        help=(
            "Bangumi 个人令牌（自动识别当前用户并可读私密收藏）；"
            "不提供时读取 [sources.bangumi].access_token。"
        ),
    ),
    limit: int = typer.Option(0, "--limit", "-n", min=0, help="最多读取的公开收藏条目数。"),
    write_memory: bool = typer.Option(
        False,
        "--write-memory",
        help="将转换后的公开收藏事件写入 memory；默认只做只读 smoke。",
    ),
    rebuild_profile: bool = typer.Option(
        False,
        "--rebuild-profile",
        help="写入 memory 后用本次 Bangumi 事件重建画像（会触发真实 LLM 调用）。",
    ),
) -> None:
    """读取 Bangumi 公开收藏；默认不写本地数据也不调用 LLM。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.bangumi import fetch_bangumi_public_collection_events
    from openbiliclaw.sources.bangumi_client import (
        BangumiAPIError,
        BangumiClient,
        me_username,
        validate_bangumi_access_token,
        validate_bangumi_username,
    )

    config = load_config()
    bangumi_cfg = config.sources.bangumi
    try:
        selected_token = validate_bangumi_access_token(token or bangumi_cfg.access_token)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--token") from exc
    selected_username = ""
    if not selected_token:
        try:
            selected_username = validate_bangumi_username(username or bangumi_cfg.username)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--username") from exc
        if not selected_username:
            raise typer.BadParameter(
                "请通过 --token（推荐，自动识别当前用户）或 --username / "
                "[sources.bangumi].username 提供访问方式。",
                param_hint="--username",
            )
    selected_limit = limit or int(bangumi_cfg.bootstrap_limit)
    write_memory = write_memory or rebuild_profile
    auth_subtitle = "官方只读 API · 个人令牌" if selected_token else "官方只读 API · anonymous"

    async def _fetch() -> tuple[str, list[dict[str, Any]]]:
        async with BangumiClient(
            access_token=selected_token or None,
            request_interval_seconds=float(bangumi_cfg.request_interval_seconds),
        ) as client:
            resolved = selected_username
            if selected_token:
                resolved = me_username(await client.get_me())
            events = await fetch_bangumi_public_collection_events(
                client,
                username=resolved,
                subject_types=tuple(bangumi_cfg.subject_types),
                limit=selected_limit,
                include_private=bool(selected_token),
            )
            return resolved, events

    _print_page_title("Bangumi 公开收藏", auth_subtitle)
    try:
        selected_username, events = asyncio.run(_fetch())
    except BangumiAPIError as exc:
        if exc.code == "not_found":
            body = "用户不存在，或该用户没有可公开读取的收藏。"
        elif exc.code == "rate_limited":
            body = "Bangumi API 正在限流，请等待冷却后重试。"
        elif exc.code == "unauthorized":
            body = (
                "个人令牌被拒绝（缺失、错误或已过期）。请到 "
                "https://next.bgm.tv/demo/access-token 重新生成后重试。"
            )
        else:
            body = str(exc)
        _print_status_panel("warning", "Bangumi 读取失败", body)
        raise typer.Exit(code=1) from exc

    counts: dict[str, int] = {}
    for event in events:
        status = str((event.get("metadata") or {}).get("collection_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    _print_key_value_table(
        "抓取摘要",
        [
            ("用户名", selected_username),
            ("公开收藏事件", str(len(events))),
            ("收藏状态", ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))),
            ("写入 memory", "将写入" if write_memory else "未写入 memory"),
            ("画像生成", "将重建" if rebuild_profile else "未触发画像生成"),
        ],
    )
    for index, event in enumerate(events[:5], start=1):
        console.print(
            f"  {index}. [{event.get('event_type', '')}] {event.get('title') or '（无标题）'}"
        )
        console.print(f"     [dim]{event.get('url', '')}[/dim]")

    if write_memory:
        written, skipped = _write_events_to_memory(events, source="bangumi")
        console.print(
            f"  [green]已写入 memory: {written} 条 Bangumi 事件[/green]"
            f"{f'，跳过重复 {skipped} 条。' if skipped else '。'}"
        )
    if rebuild_profile:
        # Bangumi profile rebuild only consumes bangumi collection events; it
        # never calls Bilibili, so skip the B 站 auth gate (still validates the
        # runtime config) to keep non-interactive rebuilds from aborting.
        _prepare_init_runtime(require_bili_auth=False)
        soul_engine = _build_soul_engine()
        _print_section_title("1/2 分析 Bangumi 偏好")
        asyncio.run(
            _run_with_progress(
                soul_engine.analyze_events(events, event_chunk_size=200),
                label="分析 Bangumi 偏好",
                eta_seconds=180,
            )
        )
        _print_section_title("2/2 生成画像")
        asyncio.run(
            _run_with_progress(
                soul_engine.build_initial_profile(_bangumi_events_to_history_items(events)),
                label="生成灵魂画像",
                eta_seconds=70,
            )
        )
        _print_status_panel("success", "完成", "Bangumi 事件已写入并完成画像重建")


@app.command("fetch-reddit")
def fetch_reddit(
    target: str = typer.Argument(
        "all",
        help="Reddit 搜索关键词、subreddit 名称或 related/read URL。",
    ),
    mode: str = typer.Option(
        "search",
        "--mode",
        help="读取模式：bootstrap / search / hot / subreddit / related。",
        case_sensitive=False,
    ),
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="最多抓取的条目数。"),
    backend: str = typer.Option(
        "rdt",
        "--backend",
        help="读取后端：rdt / auto / opencli / extension；bootstrap 会使用插件后端。",
        case_sensitive=False,
    ),
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="使用 extension 后端时等插件回结果的最大秒数。",
    ),
    write_memory: bool = typer.Option(
        False,
        "--write-memory",
        help="将本次抓到的 Reddit 事件写入 memory；默认只做抓取 smoke。",
    ),
    rebuild_profile: bool = typer.Option(
        False,
        "--rebuild-profile",
        help="写入 memory 后用本次 Reddit 事件重建画像（会触发真实 LLM 调用）。",
    ),
) -> None:
    """单独测试 Reddit 数据拉取，默认使用 rdt-cli 且不生成画像。"""
    from openbiliclaw.sources.reddit_tasks import (
        build_reddit_command,
        probe_reddit_command_backend,
        reddit_items_to_events,
        run_reddit_command,
    )

    selected_mode = mode.strip().lower()
    if selected_mode == "bootstrap_events":
        selected_mode = "bootstrap"
    if selected_mode not in {"bootstrap", "search", "hot", "subreddit", "related"}:
        raise typer.BadParameter(
            f"未知的 Reddit 读取模式 `{mode}`，当前支持："
            "bootstrap、search、hot、subreddit、related。"
        )
    write_memory = write_memory or rebuild_profile

    selected_backend_option = backend.strip().lower()
    if selected_mode == "bootstrap" and not _is_reddit_extension_backend(selected_backend_option):
        console.print(
            "[dim]Reddit bootstrap 事件仍使用 OpenBiliClaw 插件；"
            "rdt-cli 默认用于 search / hot / subreddit / related。[/dim]"
        )
        selected_backend_option = "extension"
    prechecked_status: Any | None = None
    if not _is_reddit_extension_backend(selected_backend_option):
        prechecked_status = probe_reddit_command_backend(selected_backend_option)
        if prechecked_status.state != "ready":
            console.print(
                "[dim]Reddit 命令后端不可用"
                f"({prechecked_status.message})，自动切到 OpenBiliClaw 插件 fallback。[/dim]"
            )
            selected_backend_option = "extension"
    if _is_reddit_extension_backend(selected_backend_option):
        if selected_mode == "bootstrap":
            _print_page_title("Reddit 事件拉取", "OpenBiliClaw 插件 → saved/upvoted/subscribed")
            console.print(
                f"[dim]入队 Reddit bootstrap 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]"
            )
            task_id = _enqueue_reddit_bootstrap_task()
            if not task_id:
                raise typer.Exit(code=1)
            events, scope_counts, status_label = _collect_reddit_bootstrap_events(
                task_id,
                max_wait_seconds=wait_seconds,
            )
            if status_label == "login_required":
                console.print(
                    "  [yellow]Reddit 任务已到达浏览器，但当前 Reddit 页面未登录或会话不可用。"
                    "请先在当前浏览器登录 Reddit 后重试。[/yellow]"
                )
                raise typer.Exit(code=1)
            if status_label == "timeout":
                console.print(
                    "  [yellow]Reddit 任务超时:扩展未连接 / 任务还在跑。"
                    "可加 --wait-seconds 240 重试。[/yellow]"
                )
                raise typer.Exit(code=1)
            if status_label == "failed":
                console.print("  [yellow]Reddit 任务失败 —— 检查扩展日志。[/yellow]")
                raise typer.Exit(code=1)
            if not events:
                _print_status_panel("info", "没有抓到 Reddit 事件", "插件任务执行成功但结果为空。")
                return
            _print_key_value_table(
                "抓取摘要",
                [
                    ("后端", "extension"),
                    ("模式", "bootstrap"),
                    ("收藏(saved)", str(scope_counts.get("reddit_saved", 0))),
                    ("点赞(upvoted)", str(scope_counts.get("reddit_upvoted", 0))),
                    ("订阅 subreddit", str(scope_counts.get("reddit_subscribed", 0))),
                    ("转换事件", str(len(events))),
                    ("写入 memory", "将写入" if write_memory else "未写入 memory"),
                    ("画像生成", "将重建" if rebuild_profile else "未触发画像生成"),
                ],
            )
            for index, event in enumerate(events[:5], start=1):
                title = str(event.get("title") or "（无标题）")
                event_type = str(event.get("event_type") or "")
                url = str(event.get("url") or "")
                console.print(f"  {index}. [{event_type}] {title}")
                if url:
                    console.print(f"     [dim]{url}[/dim]")
        else:
            _print_page_title("Reddit 数据拉取", "OpenBiliClaw 插件 → 事件 smoke")
            payload, budget_key = _reddit_discovery_payload(selected_mode, target, limit=limit)
            console.print(
                f"[dim]入队 Reddit {selected_mode} 任务,"
                f"等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]"
            )
            task_id = _enqueue_reddit_discovery_task(
                selected_mode,
                payload,
                daily_budget_key=budget_key,
            )
            if not task_id:
                raise typer.Exit(code=1)
            rows, scope_counts, status_label = _collect_reddit_discovery_results(
                task_id,
                max_wait_seconds=wait_seconds,
            )
            if status_label == "login_required":
                console.print(
                    "  [yellow]Reddit 任务已到达浏览器，但当前 Reddit 页面未登录或会话不可用。"
                    "请先在当前浏览器登录 Reddit 后重试。[/yellow]"
                )
                raise typer.Exit(code=1)
            if status_label == "timeout":
                console.print(
                    "  [yellow]Reddit 任务超时:扩展未连接 / 任务还在跑。"
                    "可加 --wait-seconds 240 重试。[/yellow]"
                )
                raise typer.Exit(code=1)
            if status_label == "failed":
                console.print("  [yellow]Reddit 任务失败 —— 检查扩展日志。[/yellow]")
                raise typer.Exit(code=1)
            events = reddit_items_to_events(rows, import_source=f"reddit_fetch_{selected_mode}")
            if not events:
                _print_status_panel("info", "没有抓到 Reddit 事件", "插件任务执行成功但结果为空。")
                return
            _print_key_value_table(
                "抓取摘要",
                [
                    ("后端", "extension"),
                    ("模式", selected_mode),
                    ("原始条目", str(len(rows))),
                    ("转换事件", str(len(events))),
                    ("分支计数", ", ".join(f"{k}={v}" for k, v in scope_counts.items()) or "-"),
                    ("写入 memory", "将写入" if write_memory else "未写入 memory"),
                    ("画像生成", "将重建" if rebuild_profile else "未触发画像生成"),
                ],
            )
            for index, event in enumerate(events[:5], start=1):
                title = str(event.get("title") or "（无标题）")
                author = str((event.get("metadata") or {}).get("author") or "")
                url = str(event.get("url") or "")
                suffix = f" [dim]{author}[/dim]" if author else ""
                console.print(f"  {index}. {title}{suffix}")
                if url:
                    console.print(f"     [dim]{url}[/dim]")
    else:
        if selected_mode == "bootstrap":
            _print_status_panel(
                "warning",
                "Reddit bootstrap 需要插件后端",
                "saved / upvoted / subscribed 只能在已登录浏览器同源页面内读取。",
            )
            raise typer.Exit(code=1)
        _print_page_title("Reddit 数据拉取", "命令后端 → 事件 smoke")
        status = prechecked_status or probe_reddit_command_backend(selected_backend_option)
        if status.state != "ready":
            _print_status_panel("warning", "Reddit 后端不可用", status.message)
            raise typer.Exit(code=1)

        selected_backend = status.backend or (
            "rdt" if selected_backend_option == "rdt" else "opencli"
        )
        args = build_reddit_command(
            selected_backend,
            mode=selected_mode,
            query=target,
            subreddit=target if selected_mode in {"hot", "subreddit"} else "",
            limit=limit,
        )
        rows = run_reddit_command(args, timeout=max(30.0, float(limit) * 3.0))
        events = reddit_items_to_events(rows, import_source=f"reddit_fetch_{selected_mode}")

        if not events:
            _print_status_panel("info", "没有抓到 Reddit 事件", "命令执行成功但结果为空。")
            return

        _print_key_value_table(
            "抓取摘要",
            [
                ("命令后端", selected_backend),
                ("模式", selected_mode),
                ("原始条目", str(len(rows))),
                ("转换事件", str(len(events))),
                ("写入 memory", "将写入" if write_memory else "未写入 memory"),
                ("画像生成", "将重建" if rebuild_profile else "未触发画像生成"),
            ],
        )
        for index, event in enumerate(events[:5], start=1):
            title = str(event.get("title") or "（无标题）")
            author = str((event.get("metadata") or {}).get("author") or "")
            url = str(event.get("url") or "")
            suffix = f" [dim]{author}[/dim]" if author else ""
            console.print(f"  {index}. {title}{suffix}")
            if url:
                console.print(f"     [dim]{url}[/dim]")

    if write_memory:
        written, skipped = _write_events_to_memory(events, source="reddit")
        console.print(
            f"  [green]已写入 memory: {written} 条 Reddit 事件"
            f"[/green]{f'，跳过重复 {skipped} 条。' if skipped else '。'}"
        )

    if rebuild_profile:
        _prepare_init_runtime()
        soul_engine = _build_soul_engine()
        _print_section_title("1/2 分析 Reddit 偏好")
        asyncio.run(
            _run_with_progress(
                soul_engine.analyze_events(events, event_chunk_size=200),
                label="分析 Reddit 偏好",
                eta_seconds=180,
            )
        )
        _print_section_title("2/2 生成画像")
        asyncio.run(
            _run_with_progress(
                soul_engine.build_initial_profile(_reddit_events_to_history_items(events)),
                label="生成灵魂画像",
                eta_seconds=70,
            )
        )
        _print_status_panel("success", "完成", "Reddit 事件已写入并完成画像重建")


@app.command("discover-zhihu")
def discover_zhihu(
    keywords: list[str] = _ZHIHU_DISCOVER_KEYWORDS_ARGUMENT,
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        min=1,
        help="每个关键词最多抓取的搜索结果数。",
    ),
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="等扩展回结果的最大秒数。",
    ),
    no_enqueue: bool = typer.Option(
        False,
        "--no-enqueue",
        help="只预览插件搜索结果，不写入 discovery_candidates。",
    ),
) -> None:
    """通过浏览器插件触发一次知乎搜索 discovery。"""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected_keywords = split_csv_values(keywords)
    _print_page_title("知乎内容发现", "插件搜索 → discovery_candidates")
    console.print(f"[dim]入队知乎 search 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _enqueue_zhihu_search_task(
        tuple(selected_keywords),
        max_items_per_keyword=limit,
    )
    if not task_id:
        raise typer.Exit(code=1)

    items, scope_counts, status_label = _collect_zhihu_search_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "login_required":
        console.print(
            "  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。"
            "请先在当前浏览器登录知乎后重试。[/yellow]"
        )
        raise typer.Exit(code=1)
    if status_label == "timeout":
        console.print(
            "  [yellow]知乎搜索任务超时:扩展未连接 / 任务还在跑。"
            "可加 --wait-seconds 240 重试。[/yellow]"
        )
        raise typer.Exit(code=1)
    if status_label == "failed":
        console.print("  [yellow]知乎搜索任务失败 —— 检查扩展日志。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "empty" or not items:
        _print_status_panel(
            "info",
            "没有发现到知乎内容",
            "可能是搜索接口返回空、知乎未登录，或关键词没有结果。",
        )
        return

    enqueued = 0
    contents: list[Any] = []
    if no_enqueue:
        from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

        contents = zhihu_discovery_items_to_contents(items)
    else:
        enqueued, contents = _enqueue_zhihu_discovery_candidates(items)

    _print_key_value_table(
        "发现摘要",
        [
            ("搜索结果", str(scope_counts.get("zhihu_search", len(items)))),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "zhihu"),
            ("策略", "zhihu-search"),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


@app.command("discover-reddit")
def discover_reddit(
    query: str = typer.Argument(..., help="Reddit 搜索关键词。"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="最多抓取的搜索结果条数。"),
    backend: str = typer.Option(
        "rdt",
        "--backend",
        help="读取后端：rdt / auto / opencli / extension。",
        case_sensitive=False,
    ),
    wait_seconds: float = typer.Option(
        180.0,
        "--wait-seconds",
        "-w",
        help="使用 extension 后端时等插件回结果的最大秒数。",
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过 rdt-cli 或 OpenBiliClaw 插件触发一次 Reddit 搜索 discovery。"""
    from openbiliclaw.sources.reddit_tasks import (
        build_reddit_command,
        probe_reddit_command_backend,
        reddit_items_to_contents,
        run_reddit_command,
    )

    selected_backend_option = backend.strip().lower()
    prechecked_status: Any | None = None
    if not _is_reddit_extension_backend(selected_backend_option):
        prechecked_status = probe_reddit_command_backend(selected_backend_option)
        if prechecked_status.state != "ready":
            console.print(
                "[dim]Reddit 命令后端不可用"
                f"({prechecked_status.message})，自动切到 OpenBiliClaw 插件 fallback。[/dim]"
            )
            selected_backend_option = "extension"
    if _is_reddit_extension_backend(selected_backend_option):
        _print_page_title("Reddit 内容发现", "OpenBiliClaw 插件搜索 → discovery_candidates")
        console.print(f"[dim]入队 Reddit search 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
        payload, budget_key = _reddit_discovery_payload("search", query, limit=limit)
        task_id = _enqueue_reddit_discovery_task(
            "search",
            payload,
            daily_budget_key=budget_key,
        )
        if not task_id:
            raise typer.Exit(code=1)
        rows, scope_counts, status_label = _collect_reddit_discovery_results(
            task_id,
            max_wait_seconds=wait_seconds,
        )
        if status_label == "login_required":
            console.print(
                "  [yellow]Reddit 任务已到达浏览器，但当前 Reddit 页面未登录或会话不可用。"
                "请先在当前浏览器登录 Reddit 后重试。[/yellow]"
            )
            raise typer.Exit(code=1)
        if status_label == "timeout":
            console.print(
                "  [yellow]Reddit 搜索任务超时:扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 240 重试。[/yellow]"
            )
            raise typer.Exit(code=1)
        if status_label == "failed":
            console.print("  [yellow]Reddit 搜索任务失败 —— 检查扩展日志。[/yellow]")
            raise typer.Exit(code=1)
        if status_label == "empty" or not rows:
            _print_status_panel("info", "没有发现到 Reddit 内容", "插件任务执行成功但结果为空。")
            return
        search_count = scope_counts.get("reddit_search", len(rows))
    else:
        _print_page_title("Reddit 内容发现", "命令后端搜索 → discovery_candidates")
        status = prechecked_status or probe_reddit_command_backend(selected_backend_option)
        if status.state != "ready":
            _print_status_panel("warning", "Reddit 后端不可用", status.message)
            raise typer.Exit(code=1)

        selected_backend = status.backend or (
            "rdt" if selected_backend_option == "rdt" else "opencli"
        )
        args = build_reddit_command(
            selected_backend,
            mode="search",
            query=query,
            limit=limit,
        )
        rows = run_reddit_command(args, timeout=max(30.0, float(limit) * 3.0))
        if not rows:
            _print_status_panel("info", "没有发现到 Reddit 内容", "命令执行成功但结果为空。")
            return
        search_count = len(rows)

    strategy = "reddit-search"
    enqueued = 0
    contents: list[Any]
    if no_enqueue:
        contents = reddit_items_to_contents(rows, strategy=strategy)
    else:
        enqueued, contents = _enqueue_reddit_discovery_candidates(rows, strategy=strategy)

    _print_key_value_table(
        "发现摘要",
        [
            ("搜索结果", str(search_count)),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "reddit"),
            ("策略", strategy),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


def _run_reddit_discovery_smoke(
    *,
    title: str,
    task_type: str,
    strategy: str,
    scope_key: str,
    payload: dict[str, object],
    daily_budget_key: str,
    backend: str,
    wait_seconds: float,
    no_enqueue: bool,
) -> None:
    selected_backend_option = backend.strip().lower()
    prechecked_status: Any | None = None
    if not _is_reddit_extension_backend(selected_backend_option):
        from openbiliclaw.sources.reddit_tasks import probe_reddit_command_backend

        prechecked_status = probe_reddit_command_backend(selected_backend_option)
        if prechecked_status.state != "ready":
            console.print(
                "[dim]Reddit 命令后端不可用"
                f"({prechecked_status.message})，自动切到 OpenBiliClaw 插件 fallback。[/dim]"
            )
            selected_backend_option = "extension"
    if _is_reddit_extension_backend(selected_backend_option):
        _print_page_title(title, f"OpenBiliClaw 插件 {strategy} → discovery_candidates")
        console.print(
            f"[dim]入队 Reddit {task_type} 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]"
        )
        task_id = _enqueue_reddit_discovery_task(
            task_type,
            payload,
            daily_budget_key=daily_budget_key,
        )
        if not task_id:
            raise typer.Exit(code=1)

        items, scope_counts, status_label = _collect_reddit_discovery_results(
            task_id,
            max_wait_seconds=wait_seconds,
        )
        if status_label == "login_required":
            console.print(
                "  [yellow]Reddit 任务已到达浏览器，但当前 Reddit 页面未登录或会话不可用。"
                "请先在当前浏览器登录 Reddit 后重试。[/yellow]"
            )
            raise typer.Exit(code=1)
        if status_label == "timeout":
            console.print(
                "  [yellow]Reddit discovery 任务超时:扩展未连接 / 任务还在跑。"
                "可加 --wait-seconds 240 重试。[/yellow]"
            )
            raise typer.Exit(code=1)
        if status_label == "failed":
            console.print("  [yellow]Reddit discovery 任务失败 —— 检查扩展日志。[/yellow]")
            raise typer.Exit(code=1)
        if status_label == "empty" or not items:
            _print_status_panel("info", "没有发现到 Reddit 内容", f"{strategy} 返回为空。")
            return
    else:
        from openbiliclaw.sources.reddit_tasks import (
            build_reddit_command,
            probe_reddit_command_backend,
            run_reddit_command,
        )

        _print_page_title(title, f"命令后端 {strategy} → discovery_candidates")
        status = prechecked_status or probe_reddit_command_backend(selected_backend_option)
        if status.state != "ready":
            _print_status_panel("warning", "Reddit 后端不可用", status.message)
            raise typer.Exit(code=1)

        selected_backend = status.backend or (
            "rdt" if selected_backend_option == "rdt" else "opencli"
        )
        items = []
        targets: list[str]
        limit = 20

        def _payload_int(key: str, default: int) -> int:
            value = payload.get(key, default)
            with suppress(Exception):
                return max(1, int(cast("Any", value)))
            return default

        if task_type == "hot":
            targets = [str(payload.get("subreddit") or "all")]
            limit = _payload_int("max_items", 20)
        elif task_type == "subreddit":
            raw_targets = payload.get("subreddits")
            if isinstance(raw_targets, list):
                targets = [str(item).removeprefix("r/") for item in raw_targets if str(item)]
            else:
                targets = [str(raw_targets or "all").removeprefix("r/")]
            limit = _payload_int("max_items_per_subreddit", 20)
        elif task_type == "related":
            raw_targets = payload.get("related_urls")
            targets = (
                [str(item) for item in raw_targets if str(item)]
                if isinstance(raw_targets, list)
                else []
            )
            limit = _payload_int("max_items_per_seed", 20)
        else:
            targets = [str(payload.get("query") or payload.get("keyword") or "")]
            limit = _payload_int("max_items", 20)

        for target in targets:
            args = build_reddit_command(
                selected_backend,
                mode=task_type,
                query=target,
                subreddit=target if task_type in {"hot", "subreddit"} else "",
                limit=limit,
            )
            items.extend(run_reddit_command(args, timeout=max(30.0, float(limit) * 3.0)))
        scope_counts = {scope_key: len(items)}
        if not items:
            _print_status_panel("info", "没有发现到 Reddit 内容", "命令执行成功但结果为空。")
            return

    enqueued = 0
    contents: list[Any]
    if no_enqueue:
        from openbiliclaw.sources.reddit_tasks import reddit_items_to_contents

        contents = reddit_items_to_contents(items, strategy=strategy)
    else:
        enqueued, contents = _enqueue_reddit_discovery_candidates(items, strategy=strategy)

    _print_key_value_table(
        "发现摘要",
        [
            ("抓取结果", str(scope_counts.get(scope_key, len(items)))),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "reddit"),
            ("策略", strategy),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


@app.command("discover-reddit-hot")
def discover_reddit_hot(
    subreddit: str = typer.Option("all", "--subreddit", help="热门分支的 subreddit，默认 all。"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="最多抓取的热门条数。"),
    backend: str = typer.Option(
        "rdt",
        "--backend",
        help="读取后端：rdt / auto / opencli / extension。",
        case_sensitive=False,
    ),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过 rdt-cli 或浏览器插件触发一次 Reddit 热门 discovery。"""
    _run_reddit_discovery_smoke(
        title="Reddit 热门发现",
        task_type="hot",
        strategy="reddit-hot",
        scope_key="reddit_hot",
        payload={"subreddit": subreddit.strip() or "all", "max_items": max(1, int(limit))},
        daily_budget_key="daily_hot_budget",
        backend=backend,
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("discover-reddit-subreddit")
def discover_reddit_subreddit(
    subreddits: list[str] = _REDDIT_SUBREDDITS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个 subreddit 最多抓取的内容数。"),
    backend: str = typer.Option(
        "rdt",
        "--backend",
        help="读取后端：rdt / auto / opencli / extension。",
        case_sensitive=False,
    ),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过 rdt-cli 或浏览器插件触发一次 Reddit subreddit discovery。"""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected = [value.removeprefix("r/") for value in split_csv_values(subreddits)]
    _run_reddit_discovery_smoke(
        title="Reddit Subreddit 发现",
        task_type="subreddit",
        strategy="reddit-subreddit",
        scope_key="reddit_subreddit",
        payload={"subreddits": selected, "max_items_per_subreddit": max(1, int(limit))},
        daily_budget_key="daily_subreddit_budget",
        backend=backend,
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("discover-reddit-related")
def discover_reddit_related(
    related_urls: list[str] = _REDDIT_RELATED_URLS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个种子最多扩展的相关内容数。"),
    backend: str = typer.Option(
        "rdt",
        "--backend",
        help="读取后端：rdt / auto / opencli / extension。",
        case_sensitive=False,
    ),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过 rdt-cli 或浏览器插件触发一次 Reddit 相关内容 discovery。"""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected = list(split_csv_values(related_urls))
    _run_reddit_discovery_smoke(
        title="Reddit 相关发现",
        task_type="related",
        strategy="reddit-related",
        scope_key="reddit_related",
        payload={"related_urls": selected, "max_items_per_seed": max(1, int(limit))},
        daily_budget_key="daily_related_budget",
        backend=backend,
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


def _run_zhihu_discovery_smoke(
    *,
    title: str,
    task_type: str,
    strategy: str,
    scope_key: str,
    payload: dict[str, object],
    daily_budget_key: str,
    wait_seconds: float,
    no_enqueue: bool,
) -> None:
    _print_page_title(title, f"插件 {strategy} → discovery_candidates")
    console.print(f"[dim]入队知乎 {task_type} 任务,等扩展执行(最多 {wait_seconds:.0f}s)...[/dim]")
    task_id = _enqueue_zhihu_discovery_task(
        task_type,
        payload,
        daily_budget_key=daily_budget_key,
    )
    if not task_id:
        raise typer.Exit(code=1)

    items, scope_counts, status_label = _collect_zhihu_discovery_results(
        task_id,
        max_wait_seconds=wait_seconds,
    )
    if status_label == "login_required":
        console.print(
            "  [yellow]知乎任务已到达浏览器，但当前知乎页面未登录。"
            "请先在当前浏览器登录知乎后重试。[/yellow]"
        )
        raise typer.Exit(code=1)
    if status_label == "timeout":
        console.print(
            "  [yellow]知乎 discovery 任务超时:扩展未连接 / 任务还在跑。"
            "可加 --wait-seconds 240 重试。[/yellow]"
        )
        raise typer.Exit(code=1)
    if status_label == "failed":
        console.print("  [yellow]知乎 discovery 任务失败 —— 检查扩展日志。[/yellow]")
        raise typer.Exit(code=1)
    if status_label == "empty" or not items:
        _print_status_panel("info", "没有发现到知乎内容", f"{strategy} 返回为空。")
        return

    enqueued = 0
    contents: list[Any] = []
    if no_enqueue:
        from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

        contents = zhihu_discovery_items_to_contents(items)
    else:
        enqueued, contents = _enqueue_zhihu_discovery_candidates(items)

    _print_key_value_table(
        "发现摘要",
        [
            ("抓取结果", str(scope_counts.get(scope_key, len(items)))),
            ("转换候选", str(len(contents))),
            ("入池候选", "跳过（--no-enqueue）" if no_enqueue else str(enqueued)),
            ("来源", "zhihu"),
            ("策略", strategy),
        ],
    )
    for index, item in enumerate(contents[:5], start=1):
        _print_discovered_content_preview(item, index)


@app.command("discover-zhihu-hot")
def discover_zhihu_hot(
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="最多抓取的热榜条数。"),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过浏览器插件触发一次知乎热榜 discovery。"""
    _run_zhihu_discovery_smoke(
        title="知乎热榜发现",
        task_type="hot",
        strategy="zhihu-hot",
        scope_key="zhihu_hot",
        payload={"max_items": max(1, int(limit))},
        daily_budget_key="daily_hot_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("discover-zhihu-feed")
def discover_zhihu_feed(
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="最多抓取的首页推荐条数。"),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过浏览器插件触发一次知乎首页推荐 discovery。"""
    _run_zhihu_discovery_smoke(
        title="知乎首页发现",
        task_type="feed",
        strategy="zhihu-feed",
        scope_key="zhihu_feed",
        payload={"max_items": max(1, int(limit))},
        daily_budget_key="daily_feed_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("discover-zhihu-creator")
def discover_zhihu_creator(
    creator_urls: list[str] = _ZHIHU_CREATOR_URLS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个作者最多抓取的内容数。"),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过浏览器插件触发一次知乎作者 discovery。"""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected = split_csv_values(creator_urls)
    _run_zhihu_discovery_smoke(
        title="知乎作者发现",
        task_type="creator",
        strategy="zhihu-creator",
        scope_key="zhihu_creator",
        payload={"creator_urls": selected, "max_items_per_creator": max(1, int(limit))},
        daily_budget_key="daily_creator_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("discover-zhihu-related")
def discover_zhihu_related(
    related_urls: list[str] = _ZHIHU_RELATED_URLS_ARGUMENT,
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="每个种子最多扩展的相关内容数。"),
    wait_seconds: float = typer.Option(
        180.0, "--wait-seconds", "-w", help="等扩展回结果的最大秒数。"
    ),
    no_enqueue: bool = typer.Option(
        False, "--no-enqueue", help="只预览插件结果，不写入 discovery_candidates。"
    ),
) -> None:
    """通过浏览器插件触发一次知乎相关内容 discovery。"""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected = split_csv_values(related_urls)
    _run_zhihu_discovery_smoke(
        title="知乎相关发现",
        task_type="related",
        strategy="zhihu-related",
        scope_key="zhihu_related",
        payload={"related_urls": selected, "max_items_per_seed": max(1, int(limit))},
        daily_budget_key="daily_related_budget",
        wait_seconds=wait_seconds,
        no_enqueue=no_enqueue,
    )


@app.command("fetch-x")
def fetch_x(
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="每类(点赞 / 收藏)最多拉取条数(默认 50,init 回填用 200)。",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="只拉取并打印,不写入 memory / 不更新画像。",
    ),
) -> None:
    """单独触发 X(Twitter)点赞 / 收藏拉取(独立于 ``init``)。

    与 fetch-xhs / fetch-douyin / fetch-youtube 对应,但 X 是服务端 cookie
    重放(无扩展 bootstrap 任务):本命令直接用已同步的 x.com cookie 拉取你
    自己的点赞 + 收藏,转成统一事件写入 memory —— 用于在不重跑完整 ``init``
    的情况下验证 X 历史偏好回填链路。不需要 daemon。

    \b
    采集范围:
      like      — 你的点赞 timeline   (强信号 → event_type="like")
      favorite  — 你的收藏 / 书签      (强信号 → event_type="favorite")

    前提:
      1. 浏览器扩展已把 x.com cookie 同步到后端(登录 x.com 即自动同步),
         或设置环境变量 ``OPENBILICLAW_X_COOKIE``。cookie 缺失时静默跳过。
    """
    _require_runtime_config()
    _print_page_title("拉取 X 点赞 / 收藏", "服务端 cookie 重放,独立于 init")

    likes_data, bookmarks_data = asyncio.run(
        _fetch_x_init_data(likes_limit=limit, bookmarks_limit=limit)
    )
    like_events = [
        ev for tw in likes_data if (ev := _x_tweet_to_event(tw, event_type="like")) is not None
    ]
    bookmark_events = [
        ev
        for tw in bookmarks_data
        if (ev := _x_tweet_to_event(tw, event_type="favorite")) is not None
    ]
    events = like_events + bookmark_events

    console.print(
        f"  X 点赞 [green]{len(like_events)}[/green] 条"
        f" / 收藏 [green]{len(bookmark_events)}[/green] 条"
        f" → 共 [green]{len(events)}[/green] 条事件。"
    )
    for ev in events[:5]:
        console.print(f"    [dim]- {ev.get('event_type')}: {(ev.get('title') or '')[:50]}[/dim]")

    if not events:
        console.print(
            "  [yellow]没有可写入的事件 —— 未登录 X / cookie 未同步 / 账号无点赞收藏。[/yellow]"
        )
        raise typer.Exit(code=0)

    if dry_run:
        console.print("  [dim]--dry-run:未写入 memory。[/dim]")
        return

    memory = _build_memory_manager()

    async def _persist() -> None:
        for ev in events:
            await memory.propagate_event(ev)

    asyncio.run(_persist())
    console.print(
        f"  [green]已写入 memory:{len(events)} 条事件。[/green]"
        " 跑 `openbiliclaw rebuild-profile` 让画像吃进新信号。"
    )


@app.command("import-youtube")
def import_youtube(
    path: str = typer.Argument(
        ...,
        help="Google Takeout 导出路径：.zip 文件或解压后的目录。",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="只解析打印统计，不写入数据库 / 不更新画像。",
    ),
) -> None:
    """从 Google Takeout 导入 YouTube 观看历史、订阅和点赞数据。

    使用步骤：

    \b
    1. 访问 https://takeout.google.com
    2. 仅选择 "YouTube and YouTube Music"
    3. 格式选 JSON（默认 HTML 也支持，但 JSON 更精确）
    4. 下载后将 .zip 路径传给本命令，或先解压再传目录。
    """
    from openbiliclaw.youtube.takeout import parse_takeout

    _print_page_title("导入 YouTube Takeout", "冷启动画像补充")

    takeout_path = Path(path)
    if not takeout_path.exists():
        console.print(f"[red]路径不存在: {takeout_path}[/red]")
        raise typer.Exit(code=1)

    console.print(f"  解析 [cyan]{takeout_path}[/cyan] …")
    result = parse_takeout(takeout_path)

    for warning in result.warnings:
        console.print(f"  [yellow]⚠ {warning}[/yellow]")

    stats = result.stats
    console.print(
        f"\n  解析完成：\n"
        f"    观看历史  [green]{stats.watch_history}[/green] 条\n"
        f"    订阅频道  [green]{stats.subscriptions}[/green] 个\n"
        f"    点赞视频  [green]{stats.liked_videos}[/green] 个\n"
        f"    合计      [green]{stats.total}[/green] 条事件"
    )

    if stats.total == 0:
        console.print("[yellow]未找到任何 YouTube 信号，请检查 Takeout 目录结构。[/yellow]")
        raise typer.Exit(code=0)

    if dry_run:
        console.print("\n[dim]--dry-run 模式，不写入数据库，结束。[/dim]")
        raise typer.Exit(code=0)

    _require_runtime_config()
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_section_title("1/2 写入记忆层")
    console.print(f"  将 {stats.total} 条事件传播到记忆层 …")

    async def _propagate() -> None:
        for event in result.events:
            await memory.propagate_event(event)

    asyncio.run(_propagate())
    console.print("  [green]✓ 记忆层写入完成[/green]")

    _print_section_title("2/2 更新偏好画像")
    console.print(
        f"  分析 {stats.total} 条 YouTube 信号（分片 {DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE} 条）…"
    )
    asyncio.run(
        _run_with_progress(
            soul_engine.analyze_events(
                result.events,
                event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
            ),
            label="分析偏好（YouTube 信号）",
            eta_seconds=90,
        )
    )
    console.print("  [green]✓ 偏好画像已更新[/green]")

    console.print(
        "\n[bold green]✓ YouTube Takeout 导入完成。[/bold green]\n"
        "  运行 [cyan]openbiliclaw profile[/cyan] 查看更新后的用户画像。"
    )


@app.command()
def recommend() -> None:
    """查看推荐内容."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    recommendation_engine = _build_recommendation_engine()

    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        console.print("[bold yellow]尚未初始化用户画像[/bold yellow]")
        console.print("请先执行 `openbiliclaw init` 拉取历史并生成初始画像。")
        raise typer.Exit(code=1) from exc

    recommendations = asyncio.run(
        recommendation_engine.generate_recommendations(
            discovered=None,
            profile=profile_data,
            limit=5,
        )
    )

    _print_page_title("本轮推荐", "朋友式推荐列表")
    if not recommendations:
        _print_status_panel(
            "info",
            "暂无可推荐内容",
            "请先执行 `openbiliclaw discover`。",
        )
        return

    presented_ids: list[int] = []
    for index, item in enumerate(recommendations, start=1):
        _print_recommendation_card(item, index)
        presented_ids.append(item.recommendation_id)

    recommendation_engine.mark_presented(presented_ids)


@app.command()
def feedback(
    recommendation_id: int,
    signal: str,
    note: str = typer.Option("", "--note", help="补充反馈备注"),
) -> None:
    """对一条推荐记录提交反馈."""
    _require_runtime_config()
    normalized_signal = signal.strip().lower()
    if normalized_signal not in {"like", "dislike", "comment", "dismiss"}:
        _print_status_panel("error", "反馈类型无效", "仅支持: like, dislike, comment, dismiss")
        raise typer.Exit(code=1)
    if normalized_signal == "comment" and not note.strip():
        _print_status_panel("error", "comment 需要备注", "请通过 `--note` 补充一句你的想法。")
        raise typer.Exit(code=1)

    recommendation_engine = _build_recommendation_engine()
    memory = _build_memory_manager()
    recommendation = recommendation_engine.get_recommendation(recommendation_id)
    if recommendation is None:
        _print_status_panel("error", "推荐不存在", f"recommendation_id={recommendation_id}")
        raise typer.Exit(code=1)
    soul_engine = _build_soul_engine()

    asyncio.run(
        recommendation_engine.record_feedback(
            recommendation_id,
            feedback_type=normalized_signal,
            note=note.strip(),
        )
    )
    asyncio.run(
        memory.propagate_event(
            {
                "event_type": "feedback",
                "title": str(recommendation.get("title", "")),
                "metadata": {
                    "recommendation_id": recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": normalized_signal,
                    "feedback_note": note.strip(),
                },
            }
        )
    )
    record_immediate_feedback_cognition = getattr(
        soul_engine,
        "record_immediate_feedback_cognition",
        None,
    )
    if callable(record_immediate_feedback_cognition):
        with suppress(Exception):
            record_immediate_feedback_cognition(
                feedback_type=normalized_signal,
                title=str(recommendation.get("title", "")),
                note=note.strip(),
            )
    with suppress(Exception):
        asyncio.run(soul_engine.process_feedback_batch_if_needed())

    _print_status_panel("success", "反馈已记录", f"推荐ID {recommendation_id} 已更新。")
    rows = [
        ("推荐ID", str(recommendation_id)),
        ("反馈", normalized_signal),
    ]
    if note:
        rows.append(("备注", note.strip()))
    _print_key_value_table("反馈详情", rows)


@app.command()
def profile() -> None:
    """查看用户画像."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    engine = _build_soul_engine()
    try:
        profile_data = asyncio.run(engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        console.print("[bold yellow]尚未初始化用户画像[/bold yellow]")
        console.print("请先执行 `openbiliclaw init` 拉取历史并生成初始画像。")
        raise typer.Exit(code=1) from exc

    _print_page_title("用户画像概览", "当前稳定画像")

    # -- 人格描述 ------------------------------------------------------------
    # Split by Chinese sentence terminators so Rich wraps at sentence boundaries
    # instead of mid-word CJK cell breaks. Each sentence starts on its own line.
    portrait_raw = profile_data.personality_portrait or "（暂无）"
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])", portrait_raw) if s.strip()]
    portrait_body = "\n".join(sentences) if sentences else portrait_raw
    console.print(
        Panel(
            portrait_body,
            title="[bold cyan]人格描述[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # -- 核心层 Core ---------------------------------------------------------
    core = profile_data.core
    _print_section_title("核心层 Core")
    core_traits = "、".join(core.core_traits) if core.core_traits else "（暂无）"
    deep_needs = "、".join(core.deep_needs) if core.deep_needs else "（暂无）"
    console.print(f"  [bold]人格特质[/bold]：{core_traits}")
    console.print(f"  [bold]深层需求[/bold]：{deep_needs}")
    mbti = core.mbti
    if mbti.type:
        dim_parts = [
            f"{key}={dim.pole}({dim.strength:.2f})" for key, dim in mbti.dimensions.items()
        ]
        dims_text = "  ".join(dim_parts) if dim_parts else ""
        console.print(
            f"  [bold]MBTI[/bold]：{mbti.type}  置信度 {mbti.confidence:.0%}"
            + (f"  [dim]{dims_text}[/dim]" if dims_text else "")
        )

    # -- 价值层 Values -------------------------------------------------------
    values_layer = profile_data.values_layer
    _print_section_title("价值层 Values")
    values_text = "、".join(values_layer.values) if values_layer.values else "（暂无）"
    drivers_text = (
        "、".join(values_layer.motivational_drivers)
        if values_layer.motivational_drivers
        else "（暂无）"
    )
    console.print(f"  [bold]价值观[/bold]：{values_text}")
    console.print(f"  [bold]动机驱动[/bold]：{drivers_text}")

    # -- 角色层 Role ---------------------------------------------------------
    role = profile_data.role
    _print_section_title("角色层 Role")
    console.print(f"  [bold]生活阶段[/bold]：{role.life_stage or '（暂无）'}")
    console.print(f"  [bold]当前阶段[/bold]：{role.current_phase or '（暂无）'}")

    # -- 兴趣层 Interest -----------------------------------------------------
    interest = profile_data.interest
    _print_section_title("兴趣层 Interest")
    if interest.likes:
        sorted_likes = sorted(interest.likes, key=lambda d: d.weight, reverse=True)
        for dom in sorted_likes[:10]:
            spec_names = [s.name for s in dom.specifics[:5]]
            spec_text = "、".join(spec_names)
            suffix = f"  [dim]{spec_text}[/dim]" if spec_text else ""
            console.print(f"  ▸ [bold]{dom.domain}[/bold] [dim]({dom.weight:.2f})[/dim]{suffix}")
    else:
        console.print("  （暂无兴趣领域）")
    if interest.dislikes:
        dislike_text = "、".join(d.domain for d in interest.dislikes[:8])
        console.print(f"  [dim]讨厌领域：{dislike_text}[/dim]")
    if interest.favorite_up_users:
        up_total = len(interest.favorite_up_users)
        preview = "、".join(interest.favorite_up_users[:6])
        suffix = f"（共{up_total}位）" if up_total > 6 else ""
        console.print(f"  [bold]常看UP主[/bold]：{preview}{suffix}")

    # -- 表层 Surface --------------------------------------------------------
    surface = profile_data.surface
    _print_section_title("表层 Surface")
    if surface.cognitive_style:
        for idx, item in enumerate(surface.cognitive_style, start=1):
            console.print(f"  {idx}. {item}")
    else:
        console.print("  认知风格：（暂无）")
    console.print(
        f"  [bold]深度偏好[/bold]：{surface.style.depth_preference:.2f}"
        f"   [bold]探索开放度[/bold]：{surface.exploration_openness:.2f}"
    )


@app.command("keyword-inspiration-dry-run")
@app.command("keyword-inspiration-preview")
def keyword_inspiration_dry_run(
    platforms: list[str] | None = _KEYWORD_INSPIRATION_PLATFORMS_OPTION,
    query_kind: str = _KEYWORD_INSPIRATION_KIND_OPTION,
    limit: int | None = _KEYWORD_INSPIRATION_LIMIT_OPTION,
    interest_limit: int | None = _KEYWORD_INSPIRATION_INTEREST_LIMIT_OPTION,
    persist_axes: bool = _KEYWORD_INSPIRATION_PERSIST_AXES_OPTION,
) -> None:
    """预览 search-backed inspiration 关键词生成链路，不写入关键词池."""

    import dataclasses

    from openbiliclaw.config import derive_inspiration_breadth_params, load_config
    from openbiliclaw.discovery.douyin import split_csv_values
    from openbiliclaw.discovery.inspiration_provider import (
        build_inspiration_search_provider,
        build_platform_source_backends,
    )
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.runtime.keyword_planner import KeywordPlanner
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    allowed = {
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
    }
    selected_platforms = list(split_csv_values(platforms or [])) or ["bilibili"]
    unknown = [platform for platform in selected_platforms if platform not in allowed]
    if unknown:
        _print_status_panel(
            "error",
            "平台参数无效",
            f"未知平台：{', '.join(unknown)}。可选：{', '.join(sorted(allowed))}",
        )
        raise typer.Exit(code=1)
    normalized_kind = query_kind.strip().lower()
    if normalized_kind not in {"regular", "explore"}:
        _print_status_panel("error", "kind 参数无效", "仅支持 regular / explore。")
        raise typer.Exit(code=1)

    _require_runtime_config()
    config = load_config()
    config.discovery.inspiration_search_enabled = True
    # One-shot overrides apply on the DERIVED breadth params (internal config
    # view injected via planner construction) — the per-knob config fields are
    # gone (Phase-2 collapse), so nothing mutates config.discovery here.
    inspiration_params = derive_inspiration_breadth_params(
        getattr(config.discovery, "inspiration_breadth", "medium")
    )
    if limit is not None:
        inspiration_params = dataclasses.replace(
            inspiration_params, max_keywords_per_platform=int(limit)
        )
    if interest_limit is not None:
        inspiration_params = dataclasses.replace(
            inspiration_params, interest_sample_size=int(interest_limit)
        )
    memory = _build_memory_manager()
    database = _get_runtime_database()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(config),
        concurrency=config.llm.concurrency,
        concurrency_gate=_build_llm_concurrency_gate(),
    )
    soul_engine = _build_soul_engine()
    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    x_client: object | None = None
    twitter_cfg = getattr(getattr(config, "sources", None), "twitter", None)
    if twitter_cfg is not None and bool(getattr(twitter_cfg, "enabled", False)):
        from openbiliclaw.sources.x_auth import resolve_x_cookie
        from openbiliclaw.sources.x_client import XClient

        x_client = XClient(
            cookie=resolve_x_cookie(
                data_dir=config.data_path,
                cookie_env=str(getattr(twitter_cfg, "cookie_env", "OPENBILICLAW_X_COOKIE")),
            )
        )

    planner = KeywordPlanner(
        llm_service=llm_service,
        database=database,
        config=config,
        soul_engine=soul_engine,
        pool_target_count=int(getattr(config.scheduler, "pool_target_count", 300)),
        signal_event_threshold=int(getattr(config.scheduler, "signal_event_threshold", 6)),
        inspiration_provider=build_inspiration_search_provider(
            getattr(config.discovery, "inspiration_search_backends", None),
            database=database,
            platform_backends=build_platform_source_backends(
                config,
                bilibili_client=(
                    _build_bilibili_client()
                    if bool(getattr(getattr(config.sources, "bilibili", None), "enabled", True))
                    else None
                ),
                x_client=x_client,
            ),
            platforms_per_probe=int(inspiration_params.platforms_per_probe),
            riskcontrolled_probe_budget=int(inspiration_params.riskcontrolled_probe_budget),
            pages_per_probe=int(inspiration_params.search_pages_per_probe),
        ),
        inspiration_params=inspiration_params,
    )
    report = asyncio.run(
        planner.preview_inspiration_keywords(
            selected_platforms,
            profile=profile_data,
            query_kind=normalized_kind,
            persist_axes=persist_axes,
        )
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


@app.command("keyword-inspiration-report")
def keyword_inspiration_report(
    window_days: int = typer.Option(
        14,
        "--window-days",
        min=1,
        help="统计最近 N 天 additive inspiration / merged 关键词 cohort。",
    ),
) -> None:
    """输出 inspiration additive cohort 对比与 replace 启用门禁."""

    _require_runtime_config()
    database = _get_runtime_database()
    stats = database.get_keyword_cohort_stats(window_days=int(window_days))
    sys.stdout.write(json.dumps(stats, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


_BILIBILI_STRATEGY_NAMES = ("search", "trending", "explore", "related_chain")


def _normalize_strategy_names(raw: list[str] | None) -> list[str]:
    """Split comma-separated values and validate strategy names."""
    if not raw:
        return []
    names: list[str] = []
    for token in raw:
        for part in token.split(","):
            name = part.strip()
            if name:
                names.append(name)
    unknown = [n for n in names if n not in _BILIBILI_STRATEGY_NAMES]
    if unknown:
        allowed = ", ".join(_BILIBILI_STRATEGY_NAMES)
        raise typer.BadParameter(f"未知的 Bilibili 策略：{', '.join(unknown)}。可选：{allowed}")
    # Preserve first-seen order, drop duplicates.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _run_xhs_discovery(*, force: bool) -> None:
    """Trigger one Soul-driven xhs keyword production cycle."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.runtime.xhs_producer import XhsTaskProducer
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    config = load_config()
    memory = _build_memory_manager()
    database = _get_runtime_database()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(config),
        concurrency=config.llm.concurrency,
        concurrency_gate=_build_llm_concurrency_gate(),
    )

    xhs_cfg = getattr(config.sources, "xiaohongshu", None)
    producer = XhsTaskProducer(
        task_queue=XhsTaskQueue(database),
        soul_engine=soul_engine,
        llm_service=llm_service,
        enabled=True,
        daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 0)),
        min_interval_minutes=0 if force else int(getattr(xhs_cfg, "min_interval_minutes", 3)),
    )
    result = asyncio.run(producer.produce_if_due())

    reason = str(result.get("reason", ""))
    enqueued = int(cast("int", result.get("enqueued", 0)))
    attempted = int(cast("int", result.get("attempted", 0)))

    _print_page_title("小红书关键词生产", "已将关键词写入 xhs_tasks，由浏览器扩展在后台抓取")
    if reason == "ok":
        _print_key_value_table(
            "生产摘要",
            [
                ("入队关键词数", str(enqueued)),
                ("尝试关键词数", str(attempted)),
                ("今日预算", str(int(getattr(xhs_cfg, "daily_search_budget", 0)))),
                (
                    "节流开关",
                    "已跳过（--force）"
                    if force
                    else f"{int(getattr(xhs_cfg, 'min_interval_minutes', 3))} 分钟节流",
                ),
            ],
        )
        return

    messages = {
        "disabled": (
            "info",
            "xhs producer 已禁用",
            "config.scheduler.enabled = false 时无法触发。",
        ),
        "throttled": (
            "info",
            f"距离上次关键词生产不足 {int(getattr(xhs_cfg, 'min_interval_minutes', 3))} 分钟",
            "可使用 `--force` 忽略节流重新触发。",
        ),
        "no_profile": (
            "warning",
            "尚未初始化 Soul 画像",
            "请先执行 `openbiliclaw init` 生成初始画像。",
        ),
        "no_keywords": (
            "info",
            "本次未产出关键词",
            "Soul 画像兴趣列表可能为空，或 LLM 返回了空结果。",
        ),
    }
    kind, title, body = messages.get(reason, ("info", "未知状态", reason or "无详细信息"))
    _print_status_panel(kind, title, body)


def _comma_separated_env_values(name: str) -> tuple[str, ...]:
    from openbiliclaw.discovery.douyin import split_csv_values

    return split_csv_values([os.environ.get(name, "")])


def _normalize_douyin_discovery_sources(sources: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {"search", "hot", "feed"}
    normalized: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for part in str(source).split(","):
            value = part.strip().lower()
            if not value or value in seen:
                continue
            if value not in allowed:
                raise typer.BadParameter(
                    f"未知的抖音 discovery 来源 `{value}`，当前支持：search、hot、feed。"
                )
            seen.add(value)
            normalized.append(value)
    return tuple(normalized) or ("search", "hot", "feed")


def _recent_douyin_creator_sec_uids(*, limit: int = 20) -> tuple[str, ...]:
    try:
        database = _get_runtime_database()
    except Exception:
        return ()
    if not hasattr(database, "conn"):
        return ()
    try:
        from openbiliclaw.sources.dy_tasks import recent_dy_creator_sec_uids

        return recent_dy_creator_sec_uids(database, limit=limit)
    except Exception:
        return ()


def _run_douyin_discovery(
    *,
    limit: int,
    keywords: tuple[str, ...] = (),
    creator_sec_uids: tuple[str, ...] = (),
    sources: tuple[str, ...] = ("search", "hot", "feed"),
    cache: bool = True,
    evaluate: bool = True,
) -> None:
    """Run one direct-cookie Douyin discovery cycle."""
    import openbiliclaw.config as config_module
    from openbiliclaw.discovery.douyin import (
        DouyinDiscoveryOptions,
        DouyinDiscoveryResult,
        DouyinDiscoveryService,
    )
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError
    from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
    from openbiliclaw.sources.douyin_direct import DouyinDirectAuthError, DouyinDirectClient
    from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

    _require_runtime_config()
    config = config_module.load_config()
    dy_cfg = getattr(config.sources, "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "抖音 direct discovery 未启用",
            (
                "请在 config.toml 中设置 [sources.douyin].enabled = true；Cookie 可由"
                " OPENBILICLAW_DOUYIN_COOKIE 覆盖，或由浏览器扩展同步到本机。"
            ),
        )
        raise typer.Exit(code=1)

    mode = str(getattr(dy_cfg, "mode", "direct")).strip().lower()
    if mode != "direct":
        _print_status_panel(
            "warning",
            "抖音 discovery 模式暂不支持",
            f"当前 mode={mode!r}；本版本仅支持 direct。",
        )
        raise typer.Exit(code=1)

    cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
    cookie = resolve_douyin_cookie(data_dir=config.data_path, cookie_env=cookie_env)
    if not cookie:
        _print_status_panel(
            "warning",
            "缺少抖音 Cookie",
            (
                f"请设置环境变量 {cookie_env}，或保持浏览器扩展在线，"
                "让它同步 douyin.com Cookie 到本机。"
            ),
        )
        raise typer.Exit(code=1)

    soul_engine = _build_soul_engine()
    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    normalized_sources = _normalize_douyin_discovery_sources(sources)
    resolved_creator_sec_uids = creator_sec_uids or _comma_separated_env_values(
        "OPENBILICLAW_DOUYIN_CREATOR_SEC_UIDS"
    )
    if not resolved_creator_sec_uids and "creator" in normalized_sources:
        resolved_creator_sec_uids = _recent_douyin_creator_sec_uids(
            limit=max(1, min(limit * 2, 20))
        )

    async def _discover() -> DouyinDiscoveryResult:
        async with DouyinDirectClient(cookie=cookie) as direct_client:
            client: Any = direct_client
            if any(source in normalized_sources for source in ("search", "hot", "feed")):
                try:
                    database = _get_runtime_database()
                except Exception:
                    database = None
                if database is not None and hasattr(database, "conn"):
                    search_wait_seconds = float(
                        os.environ.get("OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS", "180")
                    )
                    client = DouyinPluginSearchClient(
                        database=database,
                        direct_client=direct_client,
                        wait_seconds=search_wait_seconds,
                        daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 0)),
                        daily_hot_budget=int(getattr(dy_cfg, "daily_hot_budget", 0)),
                        daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 0)),
                    )
            discovery_engine = _build_discovery_engine() if cache else None
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(
                profile_data,
                DouyinDiscoveryOptions(
                    limit=limit,
                    sources=normalized_sources,
                    keywords=keywords,
                    creator_sec_uids=resolved_creator_sec_uids,
                    cache=cache,
                    evaluate=evaluate,
                    per_source_limit=max(1, min(limit, 30)),
                ),
            )

    try:
        result = asyncio.run(_discover())
    except DouyinDirectAuthError as exc:
        _print_status_panel("warning", "抖音 Cookie 无效", str(exc))
        raise typer.Exit(code=1) from exc

    discovered = result.items
    source_counts = ", ".join(
        f"{source}:{count}" for source, count in sorted(result.source_counts.items())
    )
    _print_page_title("抖音内容发现", f"plugin/direct {' / '.join(normalized_sources)}")
    if not discovered:
        outcomes = set(result.source_outcomes.values())
        if "timeout" in outcomes:
            _print_status_panel(
                "warning",
                "抖音插件任务等待超时",
                "任务可能仍在浏览器后台执行；可提高 "
                "OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS 后重试并检查任务状态。",
            )
            raise typer.Exit(code=1)
        if "failed" in outcomes:
            _print_status_panel(
                "warning",
                "抖音插件任务执行失败",
                "任务已返回失败终态；请检查扩展日志和 dy_tasks 的结构化错误。",
            )
            raise typer.Exit(code=1)
        if outcomes and outcomes <= {"budget_exhausted"}:
            _print_status_panel(
                "info",
                "抖音 discovery 分支预算已耗尽",
                "本轮没有执行抓取；请调整对应 daily_*_budget 或等待 UTC 日预算重置。",
            )
            raise typer.Exit(code=1)
        _print_status_panel(
            "info",
            "没有发现到新抖音内容",
            "插件任务已正常完成但没有候选；可能是关键词没有结果或页面触发了软风控。",
        )
        return

    strategies = sorted({str(getattr(item, "source_strategy", "") or "") for item in discovered})
    _print_key_value_table(
        "发现摘要",
        [
            ("发现条数", str(len(discovered))),
            ("缓存状态", "已写入 content_cache" if result.cached else "未写入 content_cache"),
            ("来源", "douyin"),
            ("来源分布", source_counts or "（无）"),
            ("策略", ", ".join(s for s in strategies if s) or "douyin_direct"),
        ],
    )
    for index, item in enumerate(discovered[:5], start=1):
        _print_discovered_content_preview(item, index)


def _build_discovery_candidate_pipeline(
    *,
    config: Any,
    database: Any,
    discovery_engine: Any,
) -> Any:
    """Build the shared raw-candidate evaluator for manual producer runs."""
    from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline

    discovery_cfg = getattr(config, "discovery", None)
    admission_min_score = float(getattr(discovery_cfg, "admission_min_score", 0.60) or 0.60)
    set_admission_min_score = getattr(database, "set_admission_min_score", None)
    if callable(set_admission_min_score):
        with suppress(Exception):
            set_admission_min_score(admission_min_score)
    return DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery_engine,
        pool_target_count=int(getattr(config.scheduler, "pool_target_count", 300)),
        admission_min_score=admission_min_score,
    )


def _run_douyin_formal_discovery(*, limit: int) -> None:
    """Run the formal Douyin producer without the daemon master switch."""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.douyin_producer import build_douyin_discovery_producer
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError
    from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

    _require_runtime_config()
    config = load_config()
    dy_cfg = getattr(getattr(config, "sources", None), "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "抖音 discovery 未启用",
            "请在配置页或 config.toml 中启用 [sources.douyin].enabled。",
        )
        raise typer.Exit(code=1)
    mode = str(getattr(dy_cfg, "mode", "direct")).strip().lower()
    if mode != "direct":
        _print_status_panel(
            "warning",
            "抖音 discovery 模式暂不支持",
            f"当前 mode={mode!r}；本版本仅支持 direct。",
        )
        raise typer.Exit(code=1)

    cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
    if not resolve_douyin_cookie(data_dir=config.data_path, cookie_env=cookie_env):
        _print_status_panel(
            "warning",
            "缺少抖音 Cookie",
            f"请设置 {cookie_env}，或保持浏览器扩展在线以同步登录 Cookie。",
        )
        raise typer.Exit(code=1)

    database = _get_runtime_database()
    if not hasattr(database, "conn"):
        _print_status_panel("warning", "抖音任务表不可用", "当前数据库不支持 dy_tasks。")
        raise typer.Exit(code=1)

    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    discovery_engine = _build_discovery_engine()
    candidate_pipeline = _build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=discovery_engine,
    )
    keyword_fetch = KeywordFetchCoordinator(
        database=database,
        discovery_config=config.discovery,
    )
    producer = build_douyin_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=discovery_engine,
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
        # Manual discover is source-scoped and must not inherit the daemon's
        # scheduler master switch.
        enabled_override=True,
    )
    if producer is None:
        _print_status_panel(
            "warning",
            "抖音 discovery producer 未启动",
            "请确认抖音来源已启用、mode=direct 且任务数据库可用。",
        )
        raise typer.Exit(code=1)

    result = asyncio.run(producer.produce_if_due(limit=limit))
    reason = str(result.get("reason", ""))
    discovered = int(cast("int | float | str | bool", result.get("discovered", 0)) or 0)
    enqueued = int(cast("int | float | str | bool", result.get("enqueued", 0)) or 0)
    source_counts_raw = result.get("source_counts", {})
    source_counts = source_counts_raw if isinstance(source_counts_raw, dict) else {}
    source_counts_text = ", ".join(
        f"{source}:{count}" for source, count in sorted(source_counts.items())
    )
    source_outcomes_raw = result.get("source_outcomes", {})
    source_outcomes = source_outcomes_raw if isinstance(source_outcomes_raw, dict) else {}
    source_outcomes_text = ", ".join(
        f"{source}:{outcome}" for source, outcome in sorted(source_outcomes.items())
    )

    _print_page_title("抖音内容发现", "正式 producer · unified keywords · candidate pipeline")
    if reason in {"ok", "empty"}:
        _print_key_value_table(
            "发现摘要",
            [
                ("发现条数", str(discovered)),
                ("入池候选", str(enqueued)),
                ("来源", "douyin"),
                ("来源分布", source_counts_text or "（无）"),
                ("分支状态", source_outcomes_text or "（无）"),
            ],
        )
        if reason == "empty":
            console.print("  [dim]本轮分支正常完成，但没有产生新候选。[/dim]")
            return
        for index, item in enumerate(candidate_pipeline.last_admitted_items[:5], start=1):
            _print_discovered_content_preview(item, index)
        return

    messages = {
        "disabled": ("info", "抖音 discovery 已禁用", "请启用抖音来源后重试。"),
        "throttled": (
            "info",
            "距离上次抖音 discovery 不足最小调度间隔",
            "可在配置页调整抖音最小调度间隔分钟数。",
        ),
        "pool_full": ("info", "候选池已满", "当前无需继续补充抖音候选。"),
        "no_profile": ("warning", "尚未初始化 Soul 画像", "请先执行 `openbiliclaw init`。"),
        "budget_exhausted": (
            "warning",
            "抖音 discovery 分支预算已耗尽",
            "请调整对应 daily_*_budget 或等待 UTC 日预算重置。",
        ),
        "timeout": (
            "warning",
            "抖音插件任务等待超时",
            "任务可能仍在浏览器后台执行；请检查扩展连接和 dy_tasks 状态。",
        ),
        "error": (
            "warning",
            "抖音 discovery 执行失败",
            "请检查后端及扩展日志中的结构化错误。",
        ),
    }
    kind, title, body = messages.get(reason, ("warning", "未知状态", reason or "无详细信息"))
    _print_status_panel(kind, title, body)
    if reason not in {"throttled", "pool_full"}:
        raise typer.Exit(code=1)


def _run_zhihu_discovery(*, limit: int) -> None:
    """Run one formal Zhihu discovery cycle through the runtime producer."""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.runtime.zhihu_producer import build_zhihu_discovery_producer
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    config = load_config()
    zh_cfg = getattr(getattr(config, "sources", None), "zhihu", None)
    if zh_cfg is None or not bool(getattr(zh_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "知乎 discovery 未启用",
            "请在配置页或 config.toml 中启用 [sources.zhihu].enabled。",
        )
        raise typer.Exit(code=1)

    database = _get_runtime_database()
    if not hasattr(database, "conn"):
        _print_status_panel("warning", "知乎任务表不可用", "当前数据库不支持 zhihu_tasks。")
        raise typer.Exit(code=1)

    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    discovery_engine = _build_discovery_engine()
    candidate_pipeline = _build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=discovery_engine,
    )
    keyword_fetch = KeywordFetchCoordinator(
        database=database,
        discovery_config=config.discovery,
    )
    producer = build_zhihu_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
    )
    if producer is None:
        _print_status_panel(
            "warning",
            "知乎 discovery producer 未启动",
            "请确认知乎来源和 scheduler 均已启用。",
        )
        raise typer.Exit(code=1)

    result = asyncio.run(producer.produce_if_due(limit=limit))
    reason = str(result.get("reason", ""))
    discovered_raw = result.get("discovered", 0)
    enqueued_raw = result.get("enqueued", 0)
    discovered = int(cast("int | float | str | bool", discovered_raw) if discovered_raw else 0)
    enqueued = int(cast("int | float | str | bool", enqueued_raw) if enqueued_raw else 0)
    source_counts_raw = result.get("source_counts", {})
    source_counts = source_counts_raw if isinstance(source_counts_raw, dict) else {}
    source_counts_text = ", ".join(
        f"{source}:{count}" for source, count in sorted(source_counts.items())
    )
    source_modes = ", ".join(str(mode) for mode in getattr(zh_cfg, "source_modes", ()) or ())

    _print_page_title("知乎内容发现", f"正式 discover · {source_modes or 'search'}")
    if reason == "ok":
        _print_key_value_table(
            "发现摘要",
            [
                ("发现条数", str(discovered)),
                ("入池候选", str(enqueued)),
                ("来源", "zhihu"),
                ("来源分布", source_counts_text or "（无）"),
                ("分支", source_modes or "search"),
            ],
        )
        for index, item in enumerate(candidate_pipeline.last_admitted_items[:5], start=1):
            _print_discovered_content_preview(item, index)
        return

    messages = {
        "disabled": ("info", "知乎 discovery 已禁用", "请启用知乎来源后重试。"),
        "throttled": (
            "info",
            "距离上次知乎 discovery 不足最小调度间隔",
            "可在配置页调整知乎最小调度间隔分钟数。",
        ),
        "pool_full": ("info", "候选池已满", "当前无需继续补充知乎候选。"),
        "no_profile": ("warning", "尚未初始化 Soul 画像", "请先执行 `openbiliclaw init`。"),
        "no_keywords": ("info", "没有可用搜索词", "画像兴趣或统一关键词池为空。"),
        "no_creator_seeds": (
            "info",
            "没有作者分支 seed",
            "先跑 search/hot/feed 或手动 `discover-zhihu-creator` 积累作者 URL。",
        ),
        "no_related_seeds": (
            "info",
            "没有相关分支 seed",
            "先跑 search/hot/feed 或手动 `discover-zhihu-related` 积累内容 URL。",
        ),
        "budget_exhausted": (
            "info",
            "知乎 discovery 今日预算已用完",
            "可在配置页调整对应分支预算。",
        ),
        "empty": ("info", "知乎 discovery 返回为空", "插件任务完成但没有可转换的候选。"),
    }
    kind, title, body = messages.get(
        reason,
        ("info", "知乎 discovery 未产出内容", reason or "无详细信息"),
    )
    _print_status_panel(kind, title, body)


def _run_reddit_discovery(*, limit: int) -> None:
    """Run one formal Reddit discovery cycle through the runtime producer."""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.runtime.reddit_producer import build_reddit_discovery_producer
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    config = load_config()
    rd_cfg = getattr(getattr(config, "sources", None), "reddit", None)
    if rd_cfg is None or not bool(getattr(rd_cfg, "enabled", False)):
        _print_status_panel(
            "warning",
            "Reddit discovery 未启用",
            "请在配置页或 config.toml 中启用 [sources.reddit].enabled。",
        )
        raise typer.Exit(code=1)

    database = _get_runtime_database()
    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    discovery_engine = _build_discovery_engine()
    candidate_pipeline = _build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=discovery_engine,
    )
    keyword_fetch = KeywordFetchCoordinator(
        database=database,
        discovery_config=config.discovery,
    )
    producer = build_reddit_discovery_producer(
        config=config,
        database=database,
        soul_engine=soul_engine,
        candidate_pipeline=candidate_pipeline,
        keyword_fetch=keyword_fetch,
    )
    if producer is None:
        _print_status_panel(
            "warning",
            "Reddit discovery producer 未启动",
            "请确认 Reddit 来源和 scheduler 均已启用。",
        )
        raise typer.Exit(code=1)

    result = asyncio.run(producer.produce_if_due(limit=limit))
    reason = str(result.get("reason", ""))
    discovered_raw = result.get("discovered", 0)
    enqueued_raw = result.get("enqueued", 0)
    discovered = int(cast("int | float | str | bool", discovered_raw) if discovered_raw else 0)
    enqueued = int(cast("int | float | str | bool", enqueued_raw) if enqueued_raw else 0)
    source_counts_raw = result.get("source_counts", {})
    source_counts = source_counts_raw if isinstance(source_counts_raw, dict) else {}
    source_counts_text = ", ".join(
        f"{source}:{count}" for source, count in sorted(source_counts.items())
    )
    source_modes = ", ".join(str(mode) for mode in getattr(rd_cfg, "source_modes", ()) or ())
    backend = str(result.get("backend") or getattr(rd_cfg, "backend", "opencli") or "opencli")

    _print_page_title("Reddit 内容发现", f"正式 discover · {source_modes or 'search'}")
    if reason == "ok":
        _print_key_value_table(
            "发现摘要",
            [
                ("发现条数", str(discovered)),
                ("入池候选", str(enqueued)),
                ("来源", "reddit"),
                ("来源分布", source_counts_text or "（无）"),
                ("分支", source_modes or "search"),
                ("后端", backend),
            ],
        )
        for index, item in enumerate(candidate_pipeline.last_admitted_items[:5], start=1):
            _print_discovered_content_preview(item, index)
        return

    messages = {
        "disabled": ("info", "Reddit discovery 已禁用", "请启用 Reddit 来源后重试。"),
        "throttled": (
            "info",
            "距离上次 Reddit discovery 不足最小调度间隔",
            "可在配置页调整 Reddit 最小调度间隔分钟数。",
        ),
        "pool_full": ("info", "候选池已满", "当前无需继续补充 Reddit 候选。"),
        "no_profile": ("warning", "尚未初始化 Soul 画像", "请先执行 `openbiliclaw init`。"),
        "no_keywords": ("info", "没有可用搜索词", "画像兴趣或统一关键词池为空。"),
        "no_search_seeds": ("info", "没有搜索词", "画像兴趣或统一关键词池为空。"),
        "no_subreddit_seeds": (
            "info",
            "没有 subreddit seed",
            "先跑 search/hot，或在画像兴趣中提供可搜索的 Reddit 主题。",
        ),
        "no_related_seeds": (
            "info",
            "没有 related seed",
            "先跑 search/hot/subreddit 积累 Reddit 内容 URL。",
        ),
        "missing": (
            "warning",
            "Reddit 命令后端不可用",
            "请安装并登录 OpenCLI extension/daemon，或安装 rdt 后重试。",
        ),
        "login_required": (
            "warning",
            "Reddit 未登录",
            "请在 OpenCLI extension 所在浏览器登录 Reddit，或完成 rdt 登录后重试。",
        ),
        "error": ("warning", "Reddit discovery 执行失败", str(result.get("message", ""))),
        "empty": ("info", "Reddit discovery 返回为空", "命令后端跑通但没有可转换的候选。"),
    }
    kind, title, body = messages.get(
        reason,
        ("info", "Reddit discovery 未产出内容", reason or "无详细信息"),
    )
    _print_status_panel(kind, title, body)


def _run_bangumi_discovery_smoke(*, mode: str, keyword: str = "", limit: int) -> None:
    """Run one read-only Bangumi API branch without cache, memory, or LLM writes."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.bangumi import bangumi_subject_to_content
    from openbiliclaw.sources.bangumi_client import BangumiAPIError, BangumiClient

    config = load_config()
    bangumi_cfg = config.sources.bangumi

    async def _fetch() -> list[Any]:
        async with BangumiClient(
            request_interval_seconds=float(bangumi_cfg.request_interval_seconds)
        ) as client:
            if mode == "search":
                page = await client.search_subjects(
                    keyword,
                    subject_types=tuple(bangumi_cfg.subject_types),
                    limit=limit,
                    sort="match",
                )
            else:
                page = await client.browse_subjects(
                    str(bangumi_cfg.subject_types[0]),
                    sort="rank" if mode == "ranked" else "date",
                    limit=limit,
                )
        return [
            item
            for row in page.data
            if (item := bangumi_subject_to_content(row, strategy=f"bangumi-{mode}")) is not None
        ]

    subtitle = {
        "search": f"关键词搜索 · {keyword}",
        "ranked": "排名浏览",
        "latest": "按日期浏览（可能含未播条目）",
    }[mode]
    _print_page_title("Bangumi 内容发现 smoke", subtitle)
    try:
        items = asyncio.run(_fetch())
    except (BangumiAPIError, ValueError) as exc:
        _print_status_panel("warning", "Bangumi API 读取失败", str(exc))
        raise typer.Exit(code=1) from exc
    _print_key_value_table(
        "只读召回摘要",
        [
            ("模式", mode),
            ("条目数", str(len(items))),
            ("本地写入", "0"),
            ("LLM 调用", "0"),
        ],
    )
    for index, item in enumerate(items[:5], start=1):
        _print_discovered_content_preview(item, index)


@app.command("discover-bangumi")
def discover_bangumi(
    keyword: str = typer.Argument(..., help="Bangumi 搜索关键词。"),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50),
) -> None:
    """只读验证 Bangumi 关键词搜索。"""
    if not keyword.strip():
        raise typer.BadParameter("搜索关键词不能为空。", param_hint="keyword")
    _run_bangumi_discovery_smoke(mode="search", keyword=keyword.strip(), limit=limit)


@app.command("discover-bangumi-ranked")
def discover_bangumi_ranked(
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50),
) -> None:
    """只读验证 Bangumi 排名浏览。"""
    _run_bangumi_discovery_smoke(mode="ranked", limit=limit)


@app.command("discover-bangumi-latest")
def discover_bangumi_latest(
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=50),
) -> None:
    """只读验证 Bangumi 按日期浏览（可能含未播条目）。"""
    _run_bangumi_discovery_smoke(mode="latest", limit=limit)


def _run_bangumi_discovery(*, limit: int, force: bool = False) -> None:
    """Run one formal Bangumi cycle through the shared candidate pipeline."""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.bangumi_producer import BangumiDiscoveryProducer
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError
    from openbiliclaw.sources.bangumi_client import BangumiClient

    _require_runtime_config()
    config = load_config()
    bangumi_cfg = config.sources.bangumi
    if not bangumi_cfg.enabled:
        _print_status_panel(
            "warning",
            "Bangumi discovery 未启用",
            "请在配置页或 config.toml 中启用 [sources.bangumi].enabled。",
        )
        raise typer.Exit(code=1)
    database = _get_runtime_database()
    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel("warning", "尚未初始化用户画像", "请先执行 `openbiliclaw init`。")
        raise typer.Exit(code=1) from exc
    discovery_engine = _build_discovery_engine()
    candidate_pipeline = _build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=discovery_engine,
    )
    keyword_fetch = KeywordFetchCoordinator(
        database=database,
        discovery_config=config.discovery,
    )

    async def _produce() -> dict[str, object]:
        async with BangumiClient(
            access_token=str(bangumi_cfg.access_token or "") or None,
            request_interval_seconds=float(bangumi_cfg.request_interval_seconds),
        ) as client:
            producer = BangumiDiscoveryProducer(
                database=database,
                soul_engine=soul_engine,
                client=client,
                access_token=str(bangumi_cfg.access_token or ""),
                enabled=bool(bangumi_cfg.enabled),
                subject_types=tuple(bangumi_cfg.subject_types),
                source_modes=tuple(bangumi_cfg.source_modes),
                daily_search_budget=bangumi_cfg.daily_search_budget,
                daily_ranked_budget=bangumi_cfg.daily_ranked_budget,
                daily_latest_budget=bangumi_cfg.daily_latest_budget,
                min_interval_minutes=bangumi_cfg.min_interval_minutes,
                candidate_pipeline=candidate_pipeline,
                keyword_fetch=keyword_fetch,
            )
            return await producer.produce_if_due(limit=limit, force=force)

    result = asyncio.run(_produce())
    reason = str(result.get("reason") or "")
    discovered = int(cast("Any", result.get("discovered") or 0))
    enqueued = int(cast("Any", result.get("enqueued") or 0))
    modes = ", ".join(bangumi_cfg.source_modes)
    _print_page_title("Bangumi 内容发现", f"正式 discover · {modes}")
    if reason in {"ok", "partial"}:
        _print_key_value_table(
            "发现摘要",
            [
                ("发现条数", str(discovered)),
                ("入池候选", str(enqueued)),
                ("来源", "bangumi"),
                ("分支", modes),
                ("状态", reason),
            ],
        )
        for index, item in enumerate(candidate_pipeline.last_admitted_items[:5], start=1):
            _print_discovered_content_preview(item, index)
        return
    messages = {
        "disabled": (
            "warning",
            "Bangumi discovery 已禁用",
            "请在配置页或 config.toml 中启用 [sources.bangumi].enabled。",
        ),
        "no_profile": (
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init`。",
        ),
        "throttled": ("info", "Bangumi discovery 尚未到期", "可使用 --force 手动验证。"),
        "rate_limited": ("warning", "Bangumi API 正在冷却", "到期后会自动重试。"),
        "pool_full": ("info", "候选池已满", "当前无需补充 Bangumi 候选。"),
        "budget_exhausted": (
            "info",
            "Bangumi discovery 今日预算已用完",
            "所有启用分支的每日预算均已耗尽，可在配置页调整对应分支预算或明日重试。",
        ),
        "empty": ("info", "Bangumi discovery 返回为空", "官方 API 可达，但本轮无可转换条目。"),
        "error": ("warning", "Bangumi discovery 执行失败", str(result.get("mode_results") or "")),
    }
    kind, title, body = messages.get(
        reason,
        ("info", "Bangumi discovery 未产出内容", reason or "无详细信息"),
    )
    _print_status_panel(kind, title, body)


@app.command("discover-douyin")
def discover_douyin(
    keywords: list[str] | None = _DOUYIN_DISCOVERY_KEYWORDS_OPTION,
    creator_sec_uids: list[str] | None = _DOUYIN_DISCOVERY_CREATOR_SEC_UIDS_OPTION,
    sources: list[str] | None = _DOUYIN_DISCOVERY_SOURCES_OPTION,
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="发现结果条数上限。"),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="只跑策略并预览结果，不写入 content_cache。",
    ),
    no_evaluate: bool = typer.Option(
        False,
        "--no-evaluate",
        help="跳过 LLM 相关性评估，便于调试源接口原始召回。",
    ),
) -> None:
    """单独调试抖音 direct-cookie 内容 discovery."""
    from openbiliclaw.discovery.douyin import split_csv_values

    selected_sources = _normalize_douyin_discovery_sources(
        split_csv_values(sources) or ("search", "hot", "feed")
    )
    _run_douyin_discovery(
        limit=limit,
        keywords=split_csv_values(keywords),
        creator_sec_uids=split_csv_values(creator_sec_uids),
        sources=selected_sources,
        cache=not no_cache,
        evaluate=not no_evaluate,
    )


@app.command()
def discover(
    source: str = typer.Option(
        "bilibili",
        "--source",
        "-s",
        help="触发发现的内容源：bilibili、xiaohongshu、douyin、zhihu、reddit 或 bangumi。",
        case_sensitive=False,
    ),
    strategies: list[str] | None = _DISCOVER_STRATEGIES_OPTION,
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="发现结果条数上限。"),
    force: bool = typer.Option(
        False,
        "--force",
        help="xiaohongshu / bangumi：忽略最小调度间隔强制执行一次。",
    ),
) -> None:
    """手动触发内容发现（按来源选择渠道）."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    source_normalized = source.strip().lower()
    if source_normalized == "xiaohongshu":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "xiaohongshu 渠道走关键词生产流程，已忽略策略过滤。",
            )
        _run_xhs_discovery(force=force)
        return

    if source_normalized == "douyin":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "douyin 渠道走正式 producer，已忽略策略过滤。",
            )
        _run_douyin_formal_discovery(limit=limit)
        return

    if source_normalized == "zhihu":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "zhihu 渠道走配置页 source_modes 选择的插件 discovery 分支，已忽略策略过滤。",
            )
        _run_zhihu_discovery(limit=limit)
        return

    if source_normalized == "reddit":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "reddit 渠道走配置页 source_modes 选择的插件/兼容后端 discovery 分支，"
                "已忽略策略过滤。",
            )
        _run_reddit_discovery(limit=limit)
        return

    if source_normalized == "bangumi":
        if strategies:
            _print_status_panel(
                "info",
                "--strategy 仅对 Bilibili 生效",
                "bangumi 渠道走 source_modes 配置的官方 API discovery 分支，已忽略策略过滤。",
            )
        _run_bangumi_discovery(limit=limit, force=force)
        return

    if source_normalized != "bilibili":
        raise typer.BadParameter(
            f"未知的内容源 `{source}`，当前支持："
            "bilibili、xiaohongshu、douyin、zhihu、reddit、bangumi。"
        )

    active_strategies = _normalize_strategy_names(strategies)

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    try:
        profile_data = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    discovery_engine = _build_discovery_engine()
    discovered = asyncio.run(
        discovery_engine.discover(
            profile_data,
            strategies=active_strategies or None,
            limit=limit,
        )
    )

    subtitle = "发现结果预览"
    if active_strategies:
        subtitle += f"（策略：{', '.join(active_strategies)}）"
    _print_page_title("本次内容发现", subtitle)
    if not discovered:
        _print_status_panel("info", "没有发现到新内容", "当前没有发现到新的可缓存内容。")
        return

    _print_key_value_table(
        "发现摘要",
        [
            ("发现条数", str(len(discovered))),
            ("缓存状态", "已写入 content_cache"),
            ("来源", "bilibili"),
            ("策略", ", ".join(active_strategies) if active_strategies else "全部"),
        ],
    )
    for index, item in enumerate(discovered[:5], start=1):
        _print_discovered_content_preview(item, index)


@app.command()
def chat() -> None:
    """与 Agent 对话（苏格拉底式深度交流）."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    dialogue = _build_dialogue(soul_engine)
    _print_page_title("苏格拉底式对话", "输入 exit / quit / 空行结束")

    try:
        while True:
            try:
                user_message = typer.prompt("你", prompt_suffix="： ").strip()
            except (click.Abort, EOFError, KeyboardInterrupt):
                console.print("阿花：对话结束。")
                return

            if user_message.lower() in {"", "exit", "quit"}:
                console.print("阿花：对话结束。")
                return

            try:
                reply = asyncio.run(dialogue.respond(user_message))
            except Exception as exc:
                console.print(f"阿花：{safe_llm_failure_message(exc)}")
                continue
            console.print(f"阿花：{reply}")
    except KeyboardInterrupt:
        console.print("阿花：对话结束。")


@app.command()
def delight() -> None:
    """手动触发一次惊喜推荐检查."""
    from openbiliclaw.recommendation.delight import effective_delight_threshold
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    try:
        profile = asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    database = _get_runtime_database()
    recommendation_engine = _build_recommendation_engine()

    # Score un-scored items first
    asyncio.run(
        recommendation_engine.precompute_delight_scores(
            profile=profile,
            limit=30,
        )
    )

    prefs = getattr(profile, "preferences", None)
    exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))
    default_threshold = effective_delight_threshold(exploration_openness)
    dynamic_threshold = getattr(database, "dynamic_delight_threshold", None)
    threshold = (
        float(dynamic_threshold(default_threshold=default_threshold))
        if callable(dynamic_threshold)
        else default_threshold
    )
    candidate = database.get_delight_candidate(min_delight_score=threshold)

    _print_page_title("惊喜推荐", "从池中寻找你可能意外喜欢的内容")
    if candidate is None:
        _print_status_panel(
            "info",
            "暂时没有惊喜候选",
            "池中还没有文案已就绪的高分惊喜内容，多刷一阵会有的。",
        )
        return

    bvid = str(candidate.get("bvid", ""))
    title = str(candidate.get("title", ""))
    score = float(candidate.get("delight_score", 0.0))
    hook = str(candidate.get("delight_hook", ""))
    reason = str(candidate.get("delight_reason", ""))
    platform = str(candidate.get("source_platform", "") or "bilibili")
    url = str(candidate.get("content_url", ""))

    hook_label = f"【{hook}】" if hook else ""
    _print_key_value_table(
        f"{hook_label}阿B 觉得这条你会意外喜欢",
        [
            ("标题", title),
            ("惊喜分", f"{score:.2f}"),
            ("理由", reason or "—"),
            ("来源", platform),
            ("链接", url or f"https://www.bilibili.com/video/{bvid}"),
        ],
    )

    # Mark as notified so it won't be pushed again
    database.mark_delight_notified(bvid)
    console.print(f"  [dim]已标记 {bvid} 为已通知，不会重复推送。[/dim]")


@app.command()
def probe() -> None:
    """手动触发一次兴趣探针，确认或拒绝猜测方向."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    _require_runtime_config()
    soul_engine = _build_soul_engine()
    try:
        asyncio.run(soul_engine.get_profile())
    except SoulProfileNotInitializedError as exc:
        _print_status_panel(
            "warning",
            "尚未初始化用户画像",
            "请先执行 `openbiliclaw init` 拉取历史并生成初始画像。",
        )
        raise typer.Exit(code=1) from exc

    speculator = getattr(soul_engine, "_speculator", None)
    if speculator is None:
        _print_status_panel("info", "猜测引擎未就绪", "Speculator 未初始化。")
        raise typer.Exit(code=1)

    specs = speculator.get_active_speculations()
    _print_page_title("兴趣探针", "确认或拒绝阿B 正在试探的方向")

    if not specs:
        _print_status_panel("info", "暂时没有活跃的猜测", "过一阵阿B 会生成新的猜测方向。")
        return

    for i, spec in enumerate(specs, 1):
        specifics = [
            str(getattr(s, "name", "")).strip()
            for s in getattr(spec, "specifics", [])
            if str(getattr(s, "name", "")).strip()
        ][:3]
        hint = f"（{', '.join(specifics)}）" if specifics else ""
        progress = f"{spec.confirmation_count}/{spec.confirmation_threshold}"

        console.print(f"\n  [bold]{i}. {spec.domain}[/bold] {hint}")
        console.print(f"     理由：{spec.reason or '—'}")
        console.print(f"     确认进度：{progress}  置信度：{spec.confidence:.0%}")

    console.print()
    try:
        choice = typer.prompt(
            "输入序号确认（是），序号+n 拒绝（如 1n），或 q 退出",
            prompt_suffix="： ",
        ).strip()
    except (click.Abort, EOFError, KeyboardInterrupt):
        return

    if choice.lower() in {"q", "quit", "exit", ""}:
        return

    reject = choice.endswith("n") or choice.endswith("N")
    index_str = choice.rstrip("nN").strip()
    try:
        index = int(index_str) - 1
    except ValueError:
        console.print("[red]无效输入[/red]")
        raise typer.Exit(code=1) from None

    if index < 0 or index >= len(specs):
        console.print("[red]序号超出范围[/red]")
        raise typer.Exit(code=1)

    target = specs[index]
    domain = target.domain

    if reject:
        ok = speculator.user_reject_speculation(domain)
        if ok:
            console.print(f"  好，「{domain}」先不看了，30 天内不再猜测这个方向。")
        else:
            console.print(f"  [yellow]未找到活跃的「{domain}」猜测。[/yellow]")
    else:
        ok = speculator.user_confirm_speculation(domain)
        if ok:
            # Trigger promotion
            memory = getattr(soul_engine, "_memory", None)
            load_runtime_state = getattr(memory, "load_discovery_runtime_state", None)

            def _load_feedback_history() -> object:
                if not callable(load_runtime_state):
                    return []
                runtime_state = load_runtime_state()
                if not isinstance(runtime_state, dict):
                    return []
                return runtime_state.get("probe_feedback_history", [])

            profile = asyncio.run(soul_engine.get_profile())
            asyncio.run(
                speculator.force_tick(
                    profile,
                    feedback_history=_load_feedback_history(),
                    feedback_history_loader=_load_feedback_history,
                )
            )
            console.print(f"  好，「{domain}」记住了，已转入正式兴趣。")
        else:
            console.print(f"  [yellow]未找到活跃的「{domain}」猜测。[/yellow]")


@app.command()
def config_show() -> None:
    """显示当前配置."""
    from openbiliclaw.config import effective_llm_default_chain, load_config_with_diagnostics
    from openbiliclaw.llm import RegistryBuildError, summarize_registry

    cfg, diagnostics = load_config_with_diagnostics()
    instance_routing = bool(getattr(cfg.llm, "instance_routing", False))
    default_chain = effective_llm_default_chain(cfg.llm)
    llm_label = "LLM 默认调用链" if instance_routing else "LLM"
    llm_value = " → ".join(default_chain) if instance_routing else cfg.llm.default_provider
    _print_page_title("当前配置概览", "运行时配置")
    rows = [
        ("语言", cfg.language),
        (llm_label, llm_value or "未配置"),
        ("LLM 并发", str(cfg.llm.concurrency)),
        ("B站认证", cfg.bilibili.auth_method),
        ("定时任务", "开启" if cfg.scheduler.enabled else "关闭"),
        ("停止后台 LLM 请求", "否" if cfg.scheduler.enabled else "是"),
        (
            "浏览器断开后暂停",
            _format_pause_on_disconnect_status(
                enabled=cfg.scheduler.pause_on_extension_disconnect,
                grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
            ),
        ),
        ("开机自启动", _format_autostart_config_status(cfg)),
        (
            "海外网络模式",
            {"direct": "直连", "system": "跟随系统代理", "custom": "自定义代理"}.get(
                cfg.network.mode, cfg.network.mode
            ),
        ),
        ("海外自定义代理", cfg.network.proxy or "未设置"),
        ("收藏自动同步", "开启" if cfg.saved_sync.auto_sync_enabled else "关闭"),
        ("数据目录", str(cfg.data_path)),
    ]
    if diagnostics.config_path:
        rows.append(("配置文件", str(diagnostics.config_path)))
    _print_key_value_table("配置项", rows)

    try:
        registry = _build_registry()
        summary = summarize_registry(cfg, registry)
        provider_label = "已注册 Provider 实例" if instance_routing else "已注册 Provider"
        default_label = "最终默认 Provider 实例" if instance_routing else "最终默认 Provider"
        _print_key_value_table(
            "Provider 概览",
            [
                (provider_label, ", ".join(summary.registered_providers)),
                (default_label, summary.effective_default),
            ],
        )
    except RegistryBuildError as exc:
        _print_key_value_table(
            "Provider 概览",
            [
                ("已注册 Provider", "无"),
                ("Provider 状态", str(exc)),
            ],
        )

    hints = diagnostics.messages + [
        f"{issue.field}: {issue.message}" for issue in diagnostics.issues
    ]
    _print_config_guidance(hints)


@app.command("config-export-legacy")
def config_export_legacy(
    output: Path | None = _CONFIG_EXPORT_LEGACY_OUTPUT_OPTION,
    force: bool = _CONFIG_EXPORT_LEGACY_FORCE_OPTION,
) -> None:
    """导出可供旧版 OpenBiliClaw 读取的配置副本."""
    import tempfile

    from openbiliclaw.config import (
        load_config_with_diagnostics,
        project_config_to_legacy,
        save_config,
    )

    temp_path: Path | None = None
    try:
        cfg, diagnostics = load_config_with_diagnostics()
        source_path = diagnostics.config_path
        if source_path is None:
            raise ValueError("无法确定当前 config.toml 的路径。")
        target = (
            output.expanduser()
            if output is not None
            else source_path.with_name(f"{source_path.stem}.legacy{source_path.suffix}")
        )
        if target.resolve() == source_path.resolve():
            raise ValueError("旧格式必须导出到另一个文件，不能直接覆盖当前配置。")
        if target.exists() and not force:
            raise ValueError(f"输出文件已存在：{target}；确认后可加 --force 覆盖。")

        projected, report = project_config_to_legacy(cfg)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)

        save_config(projected, temp_path)
        verified, _ = load_config_with_diagnostics(
            temp_path,
            ensure_default_file=False,
        )
        if (
            verified.llm.instance_routing
            or verified.llm.default_provider != projected.llm.default_provider
            or verified.llm.fallback_provider != projected.llm.fallback_provider
        ):
            raise ValueError("旧格式导出后的回读校验失败，未替换目标文件。")
        temp_path.chmod(0o600)
        if target.exists() and not force:
            raise ValueError(f"输出文件已存在：{target}；确认后可加 --force 覆盖。")
        os.replace(temp_path, target)
        temp_path = None
    except (OSError, ValueError) as exc:
        _print_status_panel("error", "旧版配置导出失败", str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    _print_page_title("旧版配置已导出", "当前 v2 配置保持不变")
    _print_key_value_table(
        "导出结果",
        [
            ("源配置", str(source_path)),
            ("旧版副本", str(target)),
            ("主实例", report.primary_instance_id or "沿用旧格式"),
            ("备选实例", report.fallback_instance_id or "无"),
        ],
    )
    if report.issues:
        _print_status_panel(
            "warning",
            "降级兼容告警",
            "\n".join(f"• ({issue.code}) {issue.message}" for issue in report.issues),
        )
    else:
        _print_status_panel("success", "降级兼容", "旧格式可完整表达当前 LLM 路由。")
    permission_note = (
        "权限已设为仅当前用户可读写（0600）。"
        if os.name != "nt"
        else "Windows 会继承目标目录 ACL，请把它放在仅当前账户可访问的目录。"
    )
    console.print(
        "[bold yellow]安全提示：导出文件包含模型 API Key 等明文凭据，"
        f"{permission_note}[/bold yellow]"
    )


@auth_app.command("login")
def auth_login(
    cookie: str | None = typer.Option(None, "--cookie", help="直接传入完整 Cookie"),
) -> None:
    """交互式设置并验证 B 站 Cookie."""
    manager = _build_auth_manager()
    cookie_value = cookie or typer.prompt("请输入 B 站 Cookie", prompt_suffix=": ")
    status = asyncio.run(manager.validate_cookie(cookie_value))
    if not status.authenticated:
        console.print("[bold red]认证失败[/bold red]")
        _print_auth_status(status)
        raise typer.Exit(code=1)

    manager.set_cookie(cookie_value)
    console.print("[bold green]登录成功[/bold green]")
    _print_auth_status(status)


@auth_app.command("status")
def auth_status() -> None:
    """查看当前 B 站 Cookie 认证状态."""
    manager = _build_auth_manager()
    status = asyncio.run(manager.get_status())
    _print_auth_status(status)


@login_app.command("codex")
def login_codex(
    import_credentials: bool = _CODEX_LOGIN_IMPORT_OPTION,
    source: Path | None = _CODEX_LOGIN_SOURCE_OPTION,
    status: bool = _CODEX_LOGIN_STATUS_OPTION,
    logout: bool = _CODEX_LOGIN_LOGOUT_OPTION,
) -> None:
    """导入或管理 Codex CLI 的 ChatGPT OAuth 凭据."""
    from datetime import datetime

    from openbiliclaw.llm.codex_auth import (
        CodexAuthError,
        CodexCredentials,
        delete_codex_credentials,
        import_codex_credentials,
        load_codex_credentials,
        run_codex_cli_login,
    )

    def _print_codex_credentials(credentials: CodexCredentials) -> None:
        expires = datetime.fromtimestamp(credentials.expires_at).strftime("%Y-%m-%d %H:%M:%S")
        state = "临期/需刷新" if credentials.is_expired() else "有效"
        _print_key_value_table(
            "Codex OAuth",
            [
                ("状态", f"已登录（{state}）"),
                ("账号", credentials.account_id or "（未知）"),
                ("过期时间", expires),
            ],
        )

    if status:
        credentials = load_codex_credentials()
        if credentials is None:
            _print_status_panel(
                "warning",
                "Codex OAuth",
                "未登录。请运行 `openbiliclaw login codex` "
                "或 `openbiliclaw login codex --import`。",
            )
            return
        _print_codex_credentials(credentials)
        return

    if logout:
        deleted = delete_codex_credentials()
        body = "已登出 Codex OAuth。" if deleted else "本地没有 Codex OAuth 凭据。"
        _print_status_panel("success" if deleted else "info", "Codex OAuth", body)
        return

    try:
        if import_credentials or source is not None:
            credentials = import_codex_credentials(source=source)
        else:
            try:
                credentials = import_codex_credentials()
            except CodexAuthError:
                console.print("[dim]未找到可导入的 Codex 凭据，启动 `codex login`...[/dim]")
                run_codex_cli_login()
                credentials = import_codex_credentials()
    except CodexAuthError as exc:
        _print_status_panel("error", "Codex OAuth 登录失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_status_panel("success", "Codex OAuth", "登录凭据已导入。")
    _print_codex_credentials(credentials)


@app.command("health-check")
def health_check() -> None:
    """检查当前已注册 LLM provider 的可用性."""
    from openbiliclaw.llm import RegistryBuildError

    try:
        registry = _build_registry()
    except RegistryBuildError as exc:
        _print_status_panel("error", "Provider 健康检查失败", str(exc))
        raise typer.Exit(code=1) from exc

    results = asyncio.run(registry.health_check_all())
    _print_page_title("Provider 健康检查", "已注册 LLM Provider 状态")
    for name, result in results.items():
        status = "可用" if result.available else "不可用"
        default_label = " (default)" if result.is_default else ""
        console.print(f"  {name}{default_label}: {status}")
        if result.error:
            console.print(f"    原因: {result.error}")


@browser_app.command("status")
def browser_status() -> None:
    """检查 agent-browser 是否可用."""
    browser = _build_browser()
    _print_browser_status(browser)
    if browser.is_available:
        return
    console.print(f"  安装提示: {browser.get_install_hint()}")
    raise typer.Exit(code=1)


@browser_app.command("open")
def browser_open(url: str) -> None:
    """通过 agent-browser 打开一个页面."""
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _build_browser()
    if not browser.is_available:
        _print_status_panel("error", "agent-browser 未安装", browser.get_install_hint())
        raise typer.Exit(code=1)

    try:
        asyncio.run(browser.navigate(url))
    except BrowserCommandError as exc:
        _print_status_panel("error", "浏览器操作失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_page_title("浏览器已打开")
    _print_key_value_table("目标地址", [("URL", url)])


@browser_app.command("content")
def browser_content(url: str) -> None:
    """抓取当前页面可见文本."""
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _build_browser()
    if not browser.is_available:
        _print_status_panel("error", "agent-browser 未安装", browser.get_install_hint())
        raise typer.Exit(code=1)

    try:
        content = asyncio.run(browser.get_page_content(url))
    except BrowserCommandError as exc:
        _print_status_panel("error", "浏览器操作失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_page_title("页面内容")
    console.print(Panel(content, border_style="cyan"))


if __name__ == "__main__":
    app()
