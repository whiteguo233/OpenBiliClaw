"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


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
app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")
app.add_typer(browser_app, name="browser")
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
_INIT_BILIBILI_HISTORY_LIMIT = 300
_INIT_BILIBILI_FAVORITE_LIMIT = 300
_INIT_BILIBILI_FOLLOW_LIMIT = 100
_INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE = 300
_DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS = 180.0
_DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS = 240.0
_DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS = 6.0
_DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS = 6.0
_EXTENSION_PRESENCE_REQUIRED_WARNING = (
    "WARN extension presence required; backend will pause background LLM work "
    "after grace period if no extension client connects"
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


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
) -> Any:
    """Run a coroutine while printing periodic progress updates.

    Init's LLM-heavy phases (analyze_events, build_initial_profile,
    discover) each take 1-5 minutes of mostly-silent waiting on
    deepseek thinking. Without a heartbeat the user can't tell
    whether the process is alive or stuck. This helper prints one
    "started, ETA Xs" line, ticks every ``tick_seconds`` with
    elapsed/ETA while the work runs, and prints a final completion
    line with actual wall time.
    """
    import time as _time
    from contextlib import suppress as _suppress

    console.print(f"  [dim]→ {label}（预计 ~{eta_seconds}s）[/dim]")
    start = _time.monotonic()

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(tick_seconds)
            elapsed = int(_time.monotonic() - start)
            remaining = max(0, eta_seconds - elapsed)
            console.print(f"  [dim]· {label}: 已用 {elapsed}s / 预计还需 ~{remaining}s[/dim]")

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


def _print_recommendation_card(item: Any, index: int) -> None:
    """Render one recommendation in a card-like format."""
    rows = [
        ("标题", item.content.title or "（暂无）"),
        ("UP 主", item.content.up_name or "（未知）"),
    ]
    if item.topic_label:
        rows.append(("话题标签", item.topic_label))
    rows.extend(
        [
            ("推荐理由", item.expression or "（暂无）"),
            ("BV号", item.content.bvid or "（暂无）"),
        ]
    )
    _print_key_value_table(f"推荐 {index}", rows)


def _print_discovered_content_preview(item: Any, index: int) -> None:
    """Render one discovered content preview row."""
    _print_key_value_table(
        f"发现 {index}",
        [
            ("标题", item.title or "（暂无）"),
            ("UP 主", item.up_name or "（未知）"),
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


def _build_auth_manager() -> Any:
    """Build the configured Bilibili auth manager."""
    from openbiliclaw.bilibili.auth import AuthManager
    from openbiliclaw.config import load_config

    return AuthManager(load_config().data_path)


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
        )
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
        satisfaction_filter_enabled=cfg.soul.preference.satisfaction_filter_enabled,
        module_overrides=module_overrides_from_config(cfg),
        llm_concurrency=cfg.llm.concurrency,
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
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    from openbiliclaw.llm.registry import build_embedding_service

    _emb = build_embedding_service(cfg, registry)
    embedding_service = cast("SupportsEmbeddingService | None", _emb)
    return RecommendationEngine(
        llm=llm_service,
        database=database,
        embedding_service=embedding_service,
    )


def _build_dialogue(soul_engine: Any) -> Any:
    """Build the Socratic dialogue helper for interactive chat."""
    from openbiliclaw.soul.dialogue import SocraticDialogue

    return SocraticDialogue(llm=_build_registry(), soul_engine=soul_engine, session="cli")


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
            "then restart the daemon."
        )
        _print_status_panel("warning", "降级模式 / Degraded mode", body)
    uvicorn.run(api_app, host=host, port=port, log_level="info")


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
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config

    memory = _build_memory_manager()
    database = _get_runtime_database()
    bilibili_client = _build_bilibili_client()
    from openbiliclaw.config import load_config

    cfg = load_config()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=2,
        # Inherit dataclass default (currently 32) — sized so an init
        # discover's ~32 batches all fan out in a single wave instead
        # of queueing behind a tight cap. See engine.py for rationale.
    )

    # Build embedding service from config (optional)
    from openbiliclaw.llm.registry import build_embedding_service

    embedding_service = build_embedding_service(cfg, registry)

    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        concurrency=concurrency,
        embedding_service=embedding_service,
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=database,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
        database=database,
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
    return build_event(
        event_type="view",
        source_platform=SOURCE_BILIBILI,
        title=title,
        url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        author=author,
        metadata={
            "bvid": bvid,
            "view_at": view_at,
        },
    )


@app.callback()
def main(log_level: str | None = typer.Option(None, "--log-level")) -> None:
    """Global CLI options."""
    _APP_CONTEXT["log_level"] = log_level
    _bootstrap_container_runtime()
    _initialize_logging(log_level_override=log_level)


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
    """Persist the selected provider's full config triple to ``config.toml``.

    Writes ``default_provider`` plus the per-provider ``[llm.<name>]``
    block. ``api_key`` / ``base_url`` / ``model`` are only written when
    non-empty (so existing saved values aren't blown away when the
    wizard's user accepts a default by leaving the prompt blank).
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    config.llm.default_provider = provider
    provider_config = getattr(config.llm, provider, None)
    if provider_config is None:
        save_config(config, diagnostics.config_path)
        return
    if api_key and hasattr(provider_config, "api_key"):
        provider_config.api_key = api_key.strip()
    if base_url and hasattr(provider_config, "base_url"):
        provider_config.base_url = base_url.strip()
    if model and hasattr(provider_config, "model"):
        provider_config.model = model.strip()
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
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
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
                "国产代码 / agent 场景的当前 SOTA 之一 (M2.7 在 SWE-Bench 上 80%+),"
                "便宜 ($0.30 / $1.20 per M),适合做推荐这种结构化输出任务"
            ),
            "signup_url": (
                "https://platform.minimaxi.com/user-center/basic-information/interface-key "
                "（国内）/ https://platform.minimax.io （国际）"
            ),
            "supports_embedding": "false",
            "base_url": "https://api.minimax.io/v1",
            "default_model": "MiniMax-M2.7",
            "hint": (
                "MiniMax-M2.7 (默认 / 最新 / 4-2026 / 228K ctx) / "
                "MiniMax-M2.5 / MiniMax-M2.1。"
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


def _ollama_is_running(host: str = "http://localhost:11434") -> bool:
    """Probe Ollama's HTTP API; return True only on a healthy 200 response."""
    import httpx

    try:
        # trust_env=False — same rationale as OllamaProvider.embed: a
        # localhost Ollama probe must not be hijacked by the user's
        # HTTP_PROXY env (e.g. 127.0.0.1:7897 VPN client), or the CLI
        # falsely concludes "Ollama isn't running" while it's healthy.
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            response = client.get(f"{host}/api/version")
            return response.status_code == 200
    except Exception:
        return False


def _ollama_has_model(model: str, host: str = "http://localhost:11434") -> bool:
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


def _ollama_pull_model(model: str, host: str = "http://localhost:11434") -> bool:
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


def _ollama_start_serve_background() -> bool:
    """Start `ollama serve` in the background, wait up to 15s for it
    to start responding to /api/version. Returns whether the daemon is
    healthy at exit.
    """
    import shutil
    import subprocess

    if _ollama_is_running():
        return True

    ollama = shutil.which("ollama")
    if ollama is None:
        return False

    try:
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            subprocess.Popen(
                [ollama, "serve"],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                [ollama, "serve"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
    except Exception as exc:
        console.print(f"[red]启动 ollama serve 失败: {exc}[/red]")
        return False

    import time

    for _ in range(30):
        if _ollama_is_running():
            return True
        time.sleep(0.5)
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
        config.llm.embedding.base_url = "http://localhost:11434/v1"
    if api_key:
        config.llm.embedding.api_key = api_key.strip()
    save_config(config, diagnostics.config_path)


def _save_module_overrides(overrides: dict[str, dict[str, str]]) -> None:
    """Persist per-module LLM overrides to config.toml.

    ``overrides`` maps module name (``soul`` / ``discovery`` /
    ``recommendation`` / ``evaluation``) to a dict with optional
    ``provider`` and ``model`` keys. Empty values are written as empty
    strings, which the loader treats as "use global default".
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    for module, payload in overrides.items():
        module_config = getattr(config.llm, module, None)
        if module_config is None:
            continue
        if "provider" in payload:
            module_config.provider = payload["provider"].strip()
        if "model" in payload:
            module_config.model = payload["model"].strip()
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
# already have those keys; Ollama as the offline-only fallback (slow CPU
# inference, real hardware floor); "OpenAI 协议兼容自建网关" demoted to
# the final "(高级)" entry so 普通用户 don't pick it by mistake — most
# people who think they want it actually want option 2 (OpenAI 官方).
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
    (
        "ollama",
        "本地 Ollama（完全离线）",
        "默认 qwen2.5:7b (中文好)。不要 Key / 完全免费,但需 16GB+ 内存,CPU 推理首次响应 10-60s",
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
        "已经买了中转站 / OneAPI Key 选 2 (协议兼容);想完全离线选 7 (Ollama,但 CPU 推理慢)。[/dim]"
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
    return provider, default_base_url, api_key, model


def _interactive_embedding_setup(default_provider: str) -> None:
    """Phase 3 — embedding service (v0.3.20+ "有默认值的取舍提问").

    Default = 1 (本地 Ollama bge-m3). Mirrors the question shape used by
    docs/agent-install.md: each option carries a tradeoff explanation,
    "不确定就回 1". Two advanced branches (custom OpenAI-compatible
    endpoint / pin a different provider) are kept but de-emphasized so
    普通用户 don't get derailed.
    """
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
      1) Pick LLM service (Ollama-first menu; OpenAI-compat is its own entry,
         not buried inside ``openai``).
      2) Provide the fields that option actually needs.
      3) Choose how embeddings are served (separate question, not bundled).
      4) Optional per-module overrides (advanced, default skip).
    """
    _print_page_title("初始化前配置引导", "选 LLM、配 Embedding、填 B 站 Cookie")
    _print_provider_table()

    while True:
        raw = typer.prompt("\n请输入序号或名称（默认 1=Ollama）", default="1")
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
        _interactive_embedding_setup(provider)

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


def _prepare_init_runtime() -> Any:
    """Ensure runtime config and auth are ready before init proceeds."""
    error = _load_runtime_config_error(render=False)
    if error is not None:
        if not _is_interactive_terminal():
            _print_runtime_config_error(error)
            raise typer.Exit(code=1)
        _interactive_runtime_config_setup()

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
) -> int:
    """Backfill the initial discovery pool in stages until the target is reached."""
    database = _get_runtime_database()
    discovery_engine = _build_discovery_engine()
    discovered_count = 0

    for index, strategies in enumerate(_INIT_DISCOVERY_PLAN, start=1):
        current_pool_count = database.count_pool_candidates()
        if current_pool_count >= target_pool_count:
            break
        request_limit = max(20, target_pool_count - current_pool_count)
        console.print(
            f"补货阶段 {index}/{len(_INIT_DISCOVERY_PLAN)}: {_format_strategy_group(strategies)}"
            f"{label_suffix}"
        )
        console.print(
            f"当前池子 {current_pool_count}/{target_pool_count}，本轮请求上限 {request_limit}"
        )
        discovered = await _run_with_progress(
            discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=request_limit,
                # Init is latency-critical — skip the default search-first
                # phase split and let every strategy share the gather.
                fully_parallel=True,
            ),
            label=f"发现内容({_format_strategy_group(strategies)} 并发){label_suffix}",
            eta_seconds=300,
        )
        discovered_count += len(discovered)
        console.print(
            "阶段完成: "
            f"当前池子 {database.count_pool_candidates()}/{target_pool_count}，"
            f"本轮发现 {len(discovered)} 条"
        )

    return discovered_count


def _build_draft_profile_for_discover(memory: Any) -> Any:
    """Build a preference-only ``OnionProfile`` so discover can start
    in parallel with ``build_initial_profile`` (P3).

    The full profile builder runs an LLM synthesis call over history +
    preference + awareness + insights to produce
    ``personality_portrait``, ``deep_needs``, ``core_traits`` etc. —
    fields that *colour* discover's evaluation prompt but aren't
    load-bearing for relevance scoring (interests + style +
    favorite_up_users carry the signal). Letting discover use a
    preference-only draft while the real profile builds in the
    background overlaps two phases that previously serialised.
    """
    from openbiliclaw.soul.profile import OnionProfile

    preference_layer = memory.get_layer("preference").data
    draft = OnionProfile()
    draft.populate_from_flat_preference(preference_layer)
    return draft


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


def _enqueue_xhs_bootstrap_task(*, force: bool = False) -> str | None:
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
    if source not in {"xhs", "dy", "yt"}:
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


def _enqueue_dy_bootstrap_task() -> str | None:
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
    _kick_task_dispatcher("dy")
    return task_id


def _collect_dy_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for and harvest a previously-enqueued Douyin bootstrap task.

    Returns ``(events, scope_counts, status_label)`` where
    ``status_label`` is one of:
      - ``"ok"``         — task completed with videos
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
    status_label = "ok" if events else "empty"
    return events, scope_counts, status_label


def _enqueue_yt_bootstrap_task() -> str | None:
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
    _kick_task_dispatcher("yt")
    return task_id


def _collect_yt_bootstrap_events(
    task_id: str | None,
    *,
    max_wait_seconds: float | None = None,
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


def _enqueue_dy_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Douyin plugin search task for the browser extension."""
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
        queue = DyTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=20,
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
    _warn_if_pause_on_disconnect_requires_presence()
    _maybe_create_runtime_database_backup()
    _run_api_server(host=effective_host, port=effective_port)


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
    _warn_if_pause_on_disconnect_requires_presence()
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
      3. Interactive terminal → ask the user with default Y, then
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

    if not typer.confirm("加入抖音数据?", default=True):
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
      3. Interactive terminal → ask the user with default Y, then
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

    if not typer.confirm("加入 YouTube 数据?", default=True):
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


def _persist_init_source_enabled_flags(
    *,
    include_xhs: bool,
    include_dy: bool,
    include_yt: bool,
) -> None:
    """Persist init source choices so background discovery obeys them."""

    try:
        from openbiliclaw.config import load_config, save_config

        cfg = load_config()
        changed = False
        if bool(getattr(cfg.sources.xiaohongshu, "enabled", False)) != include_xhs:
            cfg.sources.xiaohongshu.enabled = include_xhs
            changed = True
        if bool(getattr(cfg.sources.douyin, "enabled", False)) != include_dy:
            cfg.sources.douyin.enabled = include_dy
            changed = True
        if bool(getattr(cfg.sources.youtube, "enabled", False)) != include_yt:
            cfg.sources.youtube.enabled = include_yt
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
    """Normalize user-facing init signal limits; 0 means skip that signal."""
    if value is None:
        return default
    return max(0, int(value))


def _ask_init_bilibili_limits(
    *,
    favorite_limit: int | None,
    follow_limit: int | None,
) -> tuple[int, int]:
    """Ask interactive users to confirm Bilibili init signal caps."""
    favorite = _normalize_init_bilibili_limit(
        favorite_limit,
        default=_INIT_BILIBILI_FAVORITE_LIMIT,
    )
    follow = _normalize_init_bilibili_limit(
        follow_limit,
        default=_INIT_BILIBILI_FOLLOW_LIMIT,
    )
    if not _is_interactive_terminal():
        return favorite, follow
    if favorite_limit is not None and follow_limit is not None:
        return favorite, follow

    console.print(
        "\n[bold]B 站初始化信号上限[/bold]\n[dim]回车使用默认值；输入 0 可跳过对应信号。[/dim]"
    )
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
    return favorite, follow


@app.command()
def init(
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
    bilibili_favorite_limit: int | None = typer.Option(
        None,
        "--bilibili-favorite-limit",
        min=0,
        help="B 站收藏初始化信号上限；默认 300，0 表示跳过收藏。",
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

    client = _build_bilibili_client()
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_page_title("初始化 OpenBiliClaw", "首次运行引导")
    console.print(
        "[bold yellow]⏱  这一步首次运行预计需要 2–5 分钟，"
        "请保持网络畅通别中断。[/bold yellow]\n"
        "  四个阶段会依次跑：\n"
        "    1/4  拉 B 站历史 / 收藏 / 关注（≈ 20–60s，看你的列表大小）\n"
        "    2/4  分析偏好（LLM 调用，≈ 30–90s）\n"
        "    3/4  生成灵魂画像（LLM 调用，≈ 30–60s）\n"
        "    4/4  发现首轮内容池（多策略并发 + LLM 评估，≈ 1–3 分钟）\n"
        "[dim]全程会打印进度，不要以为卡住了——LLM 单次响应可能就要 10–30s。[/dim]\n"
    )

    # Fetch all data sources in a single event loop to avoid httpx session closure.
    # Init signal mix:
    #   - 300 most-recent watch history items (truncated; older history
    #     decays into noise quickly)
    #   - up to 300 favorites across folders (high-signal user curation,
    #     but too many low-recency saves dominate init cost)
    #   - up to 100 followed creators (high-signal subscription intent)
    resolved_bilibili_favorite_limit = _INIT_BILIBILI_FAVORITE_LIMIT
    resolved_bilibili_follow_limit = _INIT_BILIBILI_FOLLOW_LIMIT

    async def _fetch_all_data() -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
    ]:
        hist = await client.get_user_history(max_items=_INIT_BILIBILI_HISTORY_LIMIT)

        favs: list[dict[str, Any]] = []
        try:
            fav_folders = (
                await client.get_all_favorites(
                    max_folders=200,
                    max_items_per_folder=max(1, resolved_bilibili_favorite_limit),
                )
                if resolved_bilibili_favorite_limit > 0
                else []
            )
            for folder in fav_folders:
                folder_title = folder.folder.title if hasattr(folder, "folder") else "未知"
                for item in folder.items if hasattr(folder, "items") else []:
                    if len(favs) >= resolved_bilibili_favorite_limit:
                        break
                    upper = item.get("upper", {}) if isinstance(item, dict) else {}
                    if not isinstance(upper, dict):
                        upper = {}
                    favs.append(
                        {
                            "title": item.get("title", "") if isinstance(item, dict) else str(item),
                            "upper": str(upper.get("name", "")).strip(),
                            "folder": folder_title,
                        }
                    )
                if len(favs) >= resolved_bilibili_favorite_limit:
                    break
        except Exception as exc:
            console.print(f"  [yellow]收藏夹拉取失败: {exc}[/yellow]")

        follows: list[dict[str, Any]] = []
        try:
            page = 1
            page_size = 50
            while len(follows) < resolved_bilibili_follow_limit:
                page_users = await client.get_following(page=page, page_size=page_size)
                if not page_users:
                    break
                for user in page_users:
                    if len(follows) >= resolved_bilibili_follow_limit:
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

    # v0.3.89+: ask user whether the backend should be reachable from
    # the local network (0.0.0.0) so mobile /m/ works out of the box.
    allow_lan = _ask_network_binding()
    _persist_api_host_choice(allow_lan=allow_lan)

    resolved_bilibili_favorite_limit, resolved_bilibili_follow_limit = _ask_init_bilibili_limits(
        favorite_limit=bilibili_favorite_limit,
        follow_limit=bilibili_follow_limit,
    )

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

    _persist_init_source_enabled_flags(
        include_xhs=include_xhs,
        include_dy=include_dy,
        include_yt=include_yt,
    )

    # Enqueue the XHS bootstrap task FIRST so the browser extension
    # can run it in parallel with the slow B站 history/favs/follows
    # fetches below (~10–30s). XHS is HTTP-only on B站's side so
    # there's no browser-tab focus conflict; XHS extension WILL grab
    # focus but that's fine because nothing else fights it.
    #
    # Douyin is enqueued LATER, AFTER XHS finishes, to avoid two
    # active-tab grabs racing each other (each platform's dispatcher
    # opens its own foreground tab; running both at once means tabs
    # interrupt each other's scrolling and Douyin's risk control sees
    # rapid focus changes as automation). v0.3.66+: serialised XHS→DY
    # to fix the focus war user reported.
    xhs_task_id = _enqueue_xhs_bootstrap_task() if include_xhs else None
    if xhs_task_id:
        console.print("  [dim]已请求扩展拉小红书收藏 / 点赞（后台并行,不阻塞 B 站拉取）。[/dim]")

    _print_section_title("1/4 拉取数据")
    history, favorites_data, following_data = asyncio.run(_fetch_all_data())
    if not history:
        _print_status_panel("warning", "历史为空", "当前无法从 B 站历史中生成初始画像。")
        raise typer.Exit(code=1)
    console.print(
        f"  浏览历史 [green]{len(history)}[/green] 条"
        f" / 收藏 [green]{len(favorites_data)}[/green] 个"
        f" / 关注 [green]{len(following_data)}[/green] 人"
    )

    # Now collect the XHS task. By this point the extension has had
    # the duration of B站 fetches to run; max_wait_seconds is the
    # *additional* wait on top of that. 30s default covers the
    # tail-end of normal completions on slow networks.
    xhs_events, xhs_scope_counts, xhs_status = _collect_xhs_bootstrap_events(xhs_task_id)
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
    # status == "skipped" is silent (DB unavailable / budget exhausted —
    # already printed by _enqueue_xhs_bootstrap_task)

    # Now (XHS done) enqueue Douyin. Serialised so the two browser-
    # focus-grabbing dispatchers don't race for the same active tab.
    dy_task_id = _enqueue_dy_bootstrap_task() if include_dy else None
    if dy_task_id:
        console.print(
            "  [dim]已请求扩展拉抖音发布 / 收藏 / 点赞 / 关注"
            "(开始抢一次浏览器焦点,~60-90 秒)。[/dim]"
        )
    dy_events, dy_scope_counts, dy_status = _collect_dy_bootstrap_events(dy_task_id)
    if dy_status == "ok":
        console.print(
            "  抖音 "
            f"发布 [green]{dy_scope_counts.get('dy_post', 0)}[/green] 条"
            f" / 收藏 [green]{dy_scope_counts.get('dy_collect', 0)}[/green] 个"
            f" / 点赞 [green]{dy_scope_counts.get('dy_like', 0)}[/green] 个"
            f" / 关注 [green]{dy_scope_counts.get('dy_follow', 0)}[/green] 人"
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
    # rationale as XHS→Douyin: each dispatcher opens a foreground tab
    # and grabs focus; running two at once causes tab-focus races and
    # confuses YouTube's lazy-loader.
    yt_task_id = _enqueue_yt_bootstrap_task() if include_yt else None
    if yt_task_id:
        console.print(
            "  [dim]已请求扩展拉 YouTube 观看历史 / 订阅 / 点赞"
            "(开始抢一次浏览器焦点,~30-90 秒)。[/dim]"
        )
    yt_events, yt_scope_counts, yt_status = _collect_yt_bootstrap_events(yt_task_id)
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

    # Build events from all data sources via the unified event_format
    # builder (v0.3.22+) so B站 / 小红书 / future-source events all carry
    # the same shape — including a natural-language ``context`` the
    # soul-pipeline LLM analyzers can read uniformly.
    from openbiliclaw.sources.event_format import SOURCE_BILIBILI, build_event

    events = [_history_item_to_event(item) for item in history]
    for fav in favorites_data:
        folder = str(fav.get("folder", "")).strip()
        upper = str(fav.get("upper", "")).strip()
        events.append(
            build_event(
                event_type="favorite",
                source_platform=SOURCE_BILIBILI,
                title=str(fav.get("title", "")),
                author=upper,
                metadata={
                    "folder": folder,
                    # ``upper`` kept for backwards compatibility with
                    # downstream consumers that still grep for it.
                    "upper": upper,
                },
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
                # ``follow`` rendering benefits from showing the user's
                # signature line — that's where their stated identity
                # lives. ``extra`` flows through to format_event_context
                # only via custom override; pre-build the context here.
                context=(
                    f"在 B 站关注了《{name}》,签名:{sign}" if sign else f"在 B 站关注了《{name}》"
                ),
                metadata={
                    "up_name": name,
                    "sign": sign,
                },
            )
        )
    bilibili_event_count = len(events)
    events_to_persist = list(events)
    events.extend(xhs_events)
    events.extend(dy_events)
    events.extend(yt_events)
    _maybe_update_init_source_shares(
        {
            "bilibili": bilibili_event_count,
            "xiaohongshu": len(xhs_events),
            "douyin": len(dy_events),
            "youtube": len(yt_events),
        }
    )
    for event in events_to_persist:
        asyncio.run(memory.propagate_event(event))

    _print_section_title("2/4 分析偏好")
    console.print(f"  总信号量: [green]{len(events)}[/green] 条事件")
    # Chunk the event list so multiple analysis calls run concurrently
    # instead of serialising a single max-thinking call over ~800 events.
    # ``merge_preferences`` folds the partial results back together.
    asyncio.run(
        _run_with_progress(
            soul_engine.analyze_events(events, event_chunk_size=200),
            label="分析偏好(4 个并发分片)",
            eta_seconds=180,
        )
    )

    _print_section_title("3/4 生成画像 + 4/4 发现内容(并发)")
    # Merge favorites and following into history for profile builder
    combined_history: list[dict[str, Any]] = list(history)
    if favorites_data:
        combined_history.append(
            {
                "title": "[收藏夹汇总]",
                "_favorites": favorites_data,
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

    # Parallel: build_initial_profile (P3) and discover (P4) overlap.
    # Discover starts with a preference-only draft profile so trending /
    # search / related_chain / explore can begin scoring candidates
    # while the LLM synthesizes the rich personality_portrait /
    # deep_needs fields. Once the build completes, the full profile is
    # already saved to the soul memory layer for downstream callers.
    draft_profile = _build_draft_profile_for_discover(memory)

    discovered_count = 0
    discovery_error = False
    profile_data: Any = None

    async def _run_p3_p4_parallel() -> tuple[Any, int, BaseException | None]:
        profile_task = asyncio.create_task(
            _run_with_progress(
                soul_engine.build_initial_profile(combined_history),
                label="生成画像(单次 LLM 综合分析)",
                eta_seconds=70,
            )
        )
        discover_task = asyncio.create_task(
            _run_init_discovery_backfill_async(
                draft_profile,
                target_pool_count=_INIT_POOL_TARGET_COUNT,
                label_suffix=" — 用 P2 草稿画像并发预热",
            )
        )
        try:
            built_profile = await profile_task
        except Exception as exc:
            # Propagate profile failure but let discover finish so we
            # at least get some pool content.
            discover_task.cancel()
            with suppress(BaseException):
                await discover_task
            raise exc
        try:
            disc_count = await discover_task
            disc_err: BaseException | None = None
        except BaseException as exc:
            disc_count = 0
            disc_err = exc
        return built_profile, disc_count, disc_err

    try:
        profile_data, discovered_count, discover_exc = asyncio.run(_run_p3_p4_parallel())
    except Exception as exc:
        discovery_error = True
        _print_status_panel(
            "error",
            "失败",
            "画像生成阶段出错。可稍后手动重试 `openbiliclaw init`。",
        )
        raise typer.Exit(code=1) from exc

    if discover_exc is not None:
        discovery_error = True
        _print_status_panel(
            "warning",
            "部分完成",
            "画像已生成，但 discover 阶段失败，可稍后手动执行 `openbiliclaw discover`。",
        )

    _print_status_panel(
        "success" if not discovery_error else "warning",
        "初始化完成" if not discovery_error else "初始化部分完成",
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
    bilibili_events = len(events) - len(xhs_events) - len(dy_events) - len(yt_events)
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
        ("▶ YouTube 观看历史", f"{yt_history_count} 条"),
        ("▶ YouTube 订阅频道", f"{yt_subs_count} 个"),
        ("▶ YouTube 点赞", f"{yt_likes_count} 个"),
        ("🌐 YouTube 入库事件", f"{len(yt_events)} 条"),
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

    source_parts = [f"[green]{bilibili_events}[/green] 条 B 站信号"]
    if len(xhs_events) > 0:
        source_parts.append(f"[green]{len(xhs_events)}[/green] 条小红书信号")
    if len(dy_events) > 0:
        source_parts.append(f"[green]{len(dy_events)}[/green] 条抖音信号")
    if len(yt_events) > 0:
        source_parts.append(f"[green]{len(yt_events)}[/green] 条 YouTube 信号")
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
                soul_engine.analyze_events(events, event_chunk_size=200),
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
        return
    if status_label == "failed":
        console.print("  [yellow]抖音搜索任务失败 —— 检查扩展日志。[/yellow]")


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
    console.print(f"  分析 {stats.total} 条 YouTube 信号（并发分片 200 条）…")
    asyncio.run(
        _run_with_progress(
            soul_engine.analyze_events(result.events, event_chunk_size=200),
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
        module_overrides=module_overrides_from_config(config),
        concurrency=config.llm.concurrency,
    )

    xhs_cfg = getattr(config.sources, "xiaohongshu", None)
    producer = XhsTaskProducer(
        task_queue=XhsTaskQueue(database),
        soul_engine=soul_engine,
        llm_service=llm_service,
        enabled=True,
        daily_budget=int(getattr(xhs_cfg, "daily_search_budget", 30)),
        min_interval_hours=0 if force else 4,
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
                ("今日预算", str(int(getattr(xhs_cfg, "daily_search_budget", 30)))),
                ("节流开关", "已跳过（--force）" if force else "4 小时节流"),
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
            "距离上次关键词生产不足 4 小时",
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
                        daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 30)),
                        daily_hot_budget=int(getattr(dy_cfg, "daily_hot_budget", 5)),
                        daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 30)),
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
        _print_status_panel(
            "info",
            "没有发现到新抖音内容",
            "可能是 Cookie 失效、签名被拒绝，或本轮关键词没有结果。",
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
        help="触发发现的内容源：bilibili、xiaohongshu 或 douyin。",
        case_sensitive=False,
    ),
    strategies: list[str] | None = _DISCOVER_STRATEGIES_OPTION,
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="发现结果条数上限。"),
    force: bool = typer.Option(
        False,
        "--force",
        help="xiaohongshu：忽略 4 小时节流强制生产一次关键词。",
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
                "douyin 渠道走 direct-cookie discovery，已忽略策略过滤。",
            )
        _run_douyin_discovery(limit=limit)
        return

    if source_normalized != "bilibili":
        raise typer.BadParameter(
            f"未知的内容源 `{source}`，当前支持：bilibili、xiaohongshu、douyin。"
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

            reply = asyncio.run(dialogue.respond(user_message))
            console.print(f"阿花：{reply}")
    except KeyboardInterrupt:
        console.print("阿花：对话结束。")


@app.command()
def delight() -> None:
    """手动触发一次惊喜推荐检查."""
    from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
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

    candidate = database.get_delight_candidate(min_delight_score=DEFAULT_DELIGHT_THRESHOLD)

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
            speculator.force_tick(asyncio.run(soul_engine.get_profile()))
            console.print(f"  好，「{domain}」记住了，已转入正式兴趣。")
        else:
            console.print(f"  [yellow]未找到活跃的「{domain}」猜测。[/yellow]")


@app.command()
def config_show() -> None:
    """显示当前配置."""
    from openbiliclaw.config import load_config_with_diagnostics
    from openbiliclaw.llm import RegistryBuildError, summarize_registry

    cfg, diagnostics = load_config_with_diagnostics()
    _print_page_title("当前配置概览", "运行时配置")
    rows = [
        ("语言", cfg.language),
        ("LLM", cfg.llm.default_provider),
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
        ("数据目录", str(cfg.data_path)),
    ]
    if diagnostics.config_path:
        rows.append(("配置文件", str(diagnostics.config_path)))
    _print_key_value_table("配置项", rows)

    try:
        registry = _build_registry()
        summary = summarize_registry(cfg, registry)
        _print_key_value_table(
            "Provider 概览",
            [
                ("已注册 Provider", ", ".join(summary.registered_providers)),
                ("最终默认 Provider", summary.effective_default),
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
