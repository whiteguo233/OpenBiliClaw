"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

import click
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="openbiliclaw",
    help="🦀 OpenBiliClaw — 你的 B 站专属 AI 朋友",
    add_completion=False,
)
auth_app = typer.Typer(help="B 站认证命令")
browser_app = typer.Typer(help="agent-browser 浏览器命令")
app.add_typer(auth_app, name="auth")
app.add_typer(browser_app, name="browser")
console = Console()
_APP_CONTEXT: dict[str, Any] = {}


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
_INIT_DISCOVERY_PLAN = [
    ["search", "related_chain"],
    ["trending"],
    ["explore"],
]
_INIT_POOL_TARGET_COUNT = 100

if TYPE_CHECKING:
    from pathlib import Path


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
    """Load config and initialize the logging system."""
    from openbiliclaw.config import load_config
    from openbiliclaw.logging_setup import configure_logging

    config = load_config()
    configure_logging(config, console_level_override=log_level_override)


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
    from openbiliclaw.soul.engine import SoulEngine

    class _UnavailableLLM:
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

    load_config()
    memory = _build_memory_manager()
    try:
        llm = _build_registry()
    except Exception:
        llm = _UnavailableLLM()
    return SoulEngine(llm=llm, memory=memory)


def _build_recommendation_engine() -> Any:
    """Build the recommendation engine with core-memory-aware LLM access."""
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.recommendation.engine import RecommendationEngine

    memory = _build_memory_manager()
    database = _get_runtime_database()
    registry = _build_registry()
    llm_service = LLMService(registry=registry, memory=memory)
    # Build embedding service for semantic dedup (optional)
    _emb = None
    try:
        from openbiliclaw.llm.embedding import EmbeddingService
        from openbiliclaw.llm.gemini_provider import GeminiProvider
        _g = registry.get("gemini")
        if isinstance(_g, GeminiProvider):
            _emb = EmbeddingService(_g)
    except Exception:
        pass
    return RecommendationEngine(llm=llm_service, database=database, embedding_service=_emb)


def _build_dialogue(soul_engine: Any) -> Any:
    """Build the Socratic dialogue helper for interactive chat."""
    from openbiliclaw.soul.dialogue import SocraticDialogue

    return SocraticDialogue(llm=_build_registry(), soul_engine=soul_engine, session="cli")


def _run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Run the local FastAPI service used by the browser extension."""
    import uvicorn

    from openbiliclaw.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


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
    from openbiliclaw.llm.service import LLMService

    memory = _build_memory_manager()
    database = _get_runtime_database()
    bilibili_client = _build_bilibili_client()
    llm_service = LLMService(registry=_build_registry(), memory=memory)
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=2,
        llm_evaluation_concurrency=2,
    )

    # Build embedding service for semantic topic dedup (optional)
    embedding_service = None
    try:
        from openbiliclaw.llm.embedding import EmbeddingService
        from openbiliclaw.llm.gemini_provider import GeminiProvider
        registry = _build_registry()
        gemini = registry.get("gemini")
        if isinstance(gemini, GeminiProvider):
            embedding_service = EmbeddingService(gemini)
    except Exception:
        pass  # Embedding is optional; falls back to exact string dedup

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
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
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
    """Normalize a Bilibili history item into an event-layer payload."""
    history_meta = item.get("history", {})
    if not isinstance(history_meta, dict):
        history_meta = {}
    bvid = str(history_meta.get("bvid", "")).strip()
    title = str(item.get("title", "")).strip()
    author = str(item.get("author_name", item.get("author", ""))).strip()
    view_at = history_meta.get("view_at", item.get("view_at", ""))
    return {
        "event_type": "view",
        "title": title,
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "metadata": {
            "bvid": bvid,
            "author": author,
            "view_at": view_at,
        },
    }


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


def _save_runtime_provider_config(provider: str, api_key: str) -> None:
    """Persist the selected provider and API key to runtime config.toml."""
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    config.llm.default_provider = provider
    provider_config = getattr(config.llm, provider)
    if hasattr(provider_config, "api_key"):
        provider_config.api_key = api_key.strip()
    save_config(config, diagnostics.config_path)


def _interactive_runtime_config_setup() -> None:
    """Guide the user through missing LLM config before init."""
    supported_providers = ["openai", "claude", "gemini", "deepseek", "ollama", "openrouter"]
    _print_page_title("初始化前配置引导", "补齐 LLM 运行时配置")
    console.print("支持的默认 provider: " + ", ".join(supported_providers))

    while True:
        provider = typer.prompt("请选择默认 LLM provider", default="gemini").strip().lower()
        if provider not in supported_providers:
            console.print("[bold red]不支持的 provider[/bold red]")
            continue

        api_key = ""
        if provider != "ollama":
            api_key = typer.prompt(
                f"请输入 {provider} API Key",
                prompt_suffix=": ",
                hide_input=True,
            )
        _save_runtime_provider_config(provider, api_key)
        error = _load_runtime_config_error()
        if error is None:
            return
        console.print("[bold yellow]刚写入的配置仍不完整，请重新输入。[/bold yellow]")


def _interactive_auth_setup(auth_manager: Any) -> Any:
    """Guide the user through Bilibili auth before init."""
    _print_page_title("初始化前认证引导", "补齐 B 站认证")
    while True:
        cookie_value = typer.prompt("请输入 B 站 Cookie", prompt_suffix=": ")
        status = asyncio.run(auth_manager.validate_cookie(cookie_value))
        if status.authenticated:
            auth_manager.set_cookie(cookie_value)
            console.print("[bold green]登录成功[/bold green]")
            _print_auth_status(status)
            return status

        console.print("[bold red]认证失败[/bold red]")
        _print_auth_status(status)
        if not typer.confirm("Cookie 无效，是否重试？", default=True):
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


def _run_init_discovery_backfill(profile: Any, *, target_pool_count: int = 100) -> int:
    """Backfill the initial discovery pool in stages until the target is reached."""
    database = _get_runtime_database()
    discovery_engine = _build_discovery_engine()
    discovered_count = 0

    for index, strategies in enumerate(_INIT_DISCOVERY_PLAN, start=1):
        current_pool_count = database.count_pool_candidates()
        if current_pool_count >= target_pool_count:
            break
        request_limit = max(30, target_pool_count - current_pool_count)
        console.print(
            f"补货阶段 {index}/{len(_INIT_DISCOVERY_PLAN)}: {_format_strategy_group(strategies)}"
        )
        console.print(
            f"当前池子 {current_pool_count}/{target_pool_count}，本轮请求上限 {request_limit}"
        )
        discovered = asyncio.run(
            discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=request_limit,
            )
        )
        discovered_count += len(discovered)
        console.print(
            "阶段完成: "
            f"当前池子 {database.count_pool_candidates()}/{target_pool_count}，"
            f"本轮发现 {len(discovered)} 条"
        )

    return discovered_count


@app.command()
def start(
    host: str = typer.Option("127.0.0.1", "--host", help="API 监听地址"),
    port: int = typer.Option(8420, "--port", min=1, max=65535, help="API 监听端口"),
) -> None:
    """启动 OpenBiliClaw Agent."""
    _print_page_title("启动 OpenBiliClaw", "本地 API 服务")
    _ensure_runtime_database_healthy()
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动本地后端，当前监听 {host}:{port}。",
    )
    _maybe_create_runtime_database_backup()
    _run_api_server(host=host, port=port)


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


@app.command()
def init() -> None:
    """首次运行：拉取历史、生成画像并补足首轮发现池."""
    _prepare_init_runtime()

    client = _build_bilibili_client()
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_page_title("初始化 OpenBiliClaw", "首次运行引导")

    # Fetch all data sources in a single event loop to avoid httpx session closure
    async def _fetch_all_data() -> (
        tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]
    ):
        hist = await client.get_user_history(max_items=500)

        favs: list[dict[str, Any]] = []
        try:
            fav_folders = await client.get_all_favorites(
                max_folders=20, max_items_per_folder=200,
            )
            for folder in fav_folders:
                folder_title = folder.folder.title if hasattr(folder, "folder") else "未知"
                for item in folder.items if hasattr(folder, "items") else []:
                    favs.append({
                        "title": getattr(item, "title", str(item)),
                        "upper": getattr(item, "upper", ""),
                        "folder": folder_title,
                    })
        except Exception as exc:
            console.print(f"  [yellow]收藏夹拉取失败: {exc}[/yellow]")

        follows: list[dict[str, Any]] = []
        try:
            for page in range(1, 6):
                page_users = await client.get_following(page=page, page_size=50)
                if not page_users:
                    break
                for user in page_users:
                    follows.append({
                        "name": getattr(user, "uname", str(user)),
                        "sign": getattr(user, "sign", ""),
                    })
                if len(page_users) < 50:
                    break
        except Exception as exc:
            console.print(f"  [yellow]关注列表拉取失败: {exc}[/yellow]")

        return hist, favs, follows

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

    # Build events from all data sources
    events = [_history_item_to_event(item) for item in history]
    for fav in favorites_data:
        events.append({
            "event_type": "favorite",
            "title": str(fav.get("title", "")),
            "metadata": {
                "folder": str(fav.get("folder", "")),
                "upper": str(fav.get("upper", "")),
            },
        })
    for user in following_data:
        events.append({
            "event_type": "follow",
            "title": str(user.get("name", "")),
            "metadata": {
                "up_name": str(user.get("name", "")),
                "sign": str(user.get("sign", "")),
            },
        })
    for event in events:
        asyncio.run(memory.propagate_event(event))

    _print_section_title("2/4 分析偏好")
    console.print(f"  总信号量: [green]{len(events)}[/green] 条事件")
    asyncio.run(soul_engine.analyze_events(events))

    _print_section_title("3/4 生成画像")
    # Merge favorites and following into history for profile builder
    combined_history: list[dict[str, Any]] = list(history)
    if favorites_data:
        combined_history.append({
            "title": "[收藏夹汇总]",
            "_favorites": favorites_data,
            "_favorites_summary": f"共 {len(favorites_data)} 个收藏，"
            + "涵盖: " + ", ".join(
                set(f.get("folder", "") for f in favorites_data[:30] if f.get("folder"))
            ),
        })
    if following_data:
        combined_history.append({
            "title": "[关注列表汇总]",
            "_following": following_data,
            "_following_summary": f"共关注 {len(following_data)} 人，"
            + "包括: " + ", ".join(f["name"] for f in following_data[:20]),
        })
    profile_data = asyncio.run(soul_engine.build_initial_profile(combined_history))

    _print_section_title("4/4 发现内容")
    discovered_count = 0
    discovery_error = False
    try:
        discovered_count = _run_init_discovery_backfill(
            profile_data,
            target_pool_count=_INIT_POOL_TARGET_COUNT,
        )
    except Exception:
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
    _print_key_value_table(
        "初始化摘要",
        [
            ("浏览历史", str(len(history))),
            ("收藏", str(len(favorites_data))),
            ("关注", str(len(following_data))),
            ("总事件数", str(len(events))),
            ("画像状态", "已生成"),
            ("发现内容数", str(discovered_count)),
        ],
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
    if normalized_signal not in {"like", "dislike", "comment"}:
        _print_status_panel("error", "反馈类型无效", "仅支持: like, dislike, comment")
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
    _print_section_title("人格描述")
    console.print(profile_data.personality_portrait or "（暂无）")
    _print_section_title("核心特质")
    traits_text = "、".join(profile_data.core_traits) if profile_data.core_traits else "（暂无）"
    console.print(f"  {traits_text}")
    _print_section_title("价值观")
    values_text = "、".join(profile_data.values) if profile_data.values else "（暂无）"
    console.print(f"  {values_text}")
    _print_section_title("当前阶段")
    console.print(f"  {profile_data.life_stage or '（暂无）'}")
    _print_section_title("深层需求")
    needs_text = "、".join(profile_data.deep_needs) if profile_data.deep_needs else "（暂无）"
    console.print(f"  {needs_text}")


@app.command()
def discover() -> None:
    """手动触发内容发现."""
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

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
    discovered = asyncio.run(discovery_engine.discover(profile_data, limit=30))

    _print_page_title("本次内容发现", "发现结果预览")
    if not discovered:
        _print_status_panel("info", "没有发现到新内容", "当前没有发现到新的可缓存内容。")
        return

    _print_key_value_table(
        "发现摘要",
        [
            ("发现条数", str(len(discovered))),
            ("缓存状态", "已写入 content_cache"),
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
def config_show() -> None:
    """显示当前配置."""
    from openbiliclaw.config import load_config_with_diagnostics
    from openbiliclaw.llm import RegistryBuildError, summarize_registry

    cfg, diagnostics = load_config_with_diagnostics()
    _print_page_title("当前配置概览", "运行时配置")
    rows = [
        ("语言", cfg.language),
        ("LLM", cfg.llm.default_provider),
        ("B站认证", cfg.bilibili.auth_method),
        ("定时任务", "开启" if cfg.scheduler.enabled else "关闭"),
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
