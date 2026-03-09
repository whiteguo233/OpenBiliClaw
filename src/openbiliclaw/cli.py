"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

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
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    class _UnavailableLLM:
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

    config = load_config()
    memory = MemoryManager(config.data_path)
    memory.initialize()
    try:
        llm = _build_registry()
    except Exception:
        llm = _UnavailableLLM()
    return SoulEngine(llm=llm, memory=memory)


def _build_recommendation_engine() -> Any:
    """Build the recommendation engine with core-memory-aware LLM access."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.storage.database import Database

    config = load_config()
    memory = MemoryManager(config.data_path)
    memory.initialize()
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    llm_service = LLMService(registry=_build_registry(), memory=memory)
    return RecommendationEngine(llm=llm_service, database=database)


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

    config = load_config()
    memory = MemoryManager(config.data_path)
    memory.initialize()
    return memory


def _build_discovery_engine() -> Any:
    """Build the discovery engine with currently implemented strategies."""
    from openbiliclaw.config import load_config
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import (
        ExploreStrategy,
        RelatedChainStrategy,
        SearchStrategy,
        TrendingStrategy,
    )
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.storage.database import Database

    config = load_config()
    memory = MemoryManager(config.data_path)
    memory.initialize()
    database = Database(config.data_path / "openbiliclaw.db")
    database.initialize()
    bilibili_client = _build_bilibili_client()
    llm_service = LLMService(registry=_build_registry(), memory=memory)

    engine = ContentDiscoveryEngine(llm_service=llm_service, database=database)
    search_strategy = SearchStrategy(llm_service=llm_service, bilibili_client=bilibili_client)
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
    )

    engine.register_strategy(search_strategy)
    engine.register_strategy(trending_strategy)
    engine.register_strategy(related_strategy)
    engine.register_strategy(explore_strategy)
    return engine


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
    from openbiliclaw.config import (
        ConfigError,
        load_config_with_diagnostics,
        validate_runtime_config,
    )

    config, diagnostics = load_config_with_diagnostics()
    try:
        validate_runtime_config(config)
    except ConfigError as exc:
        console.print("[bold red]配置错误[/bold red]")
        hints = diagnostics.messages + [
            f"{issue.field}: {issue.message}" for issue in diagnostics.issues
        ]
        _print_config_guidance(hints)
        console.print(f"  {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def start() -> None:
    """启动 OpenBiliClaw Agent."""
    _print_page_title("启动 OpenBiliClaw", "本地 API 服务")
    _print_status_panel(
        "info",
        "API 服务",
        "正在启动本地后端，默认监听 127.0.0.1:8420。",
    )
    _run_api_server(host="127.0.0.1", port=8420)


@app.command()
def init() -> None:
    """首次运行：拉取历史、生成画像并自动执行一次内容发现."""
    _require_runtime_config()
    auth_manager = _build_auth_manager()
    status = asyncio.run(auth_manager.get_status())
    if not status.authenticated:
        console.print("[bold red]认证失败[/bold red]")
        console.print("请先执行 `openbiliclaw auth login` 完成 B 站认证。")
        raise typer.Exit(code=1)

    client = _build_bilibili_client()
    memory = _build_memory_manager()
    soul_engine = _build_soul_engine()

    _print_page_title("初始化 OpenBiliClaw", "首次运行引导")
    _print_section_title("1/4 拉取历史")
    history = asyncio.run(client.get_user_history(max_items=200))
    if not history:
        _print_status_panel("warning", "历史为空", "当前无法从 B 站历史中生成初始画像。")
        raise typer.Exit(code=1)

    events = [_history_item_to_event(item) for item in history]
    for event in events:
        asyncio.run(memory.propagate_event(event))

    _print_section_title("2/4 分析偏好")
    asyncio.run(soul_engine.analyze_events(events))

    _print_section_title("3/4 生成画像")
    profile_data = asyncio.run(soul_engine.build_initial_profile(history))

    _print_section_title("4/4 发现内容")
    discovered_count = 0
    discovery_error = False
    try:
        discovery_engine = _build_discovery_engine()
        discovered = asyncio.run(discovery_engine.discover(profile_data, limit=30))
        discovered_count = len(discovered)
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
            ("历史条数", str(len(history))),
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
    with suppress(Exception):
        asyncio.run(_build_soul_engine().process_feedback_batch_if_needed())

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
