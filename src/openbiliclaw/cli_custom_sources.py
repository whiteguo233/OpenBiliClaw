"""CLI surface for the same custom-source lifecycle used by chat and settings."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum
from typing import Annotated, Any

import typer

sources_app = typer.Typer(help="自定义 Discover 来源：草稿、试跑、启用、比例和运行记录")


class SourceExecutor(StrEnum):
    """Supported choices when creating a new discovery workflow."""

    auto = "auto"
    http = "http"
    ego = "ego"


def _service(*, with_llm: bool = False) -> Any:
    from openbiliclaw import cli
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.workflow_service import WorkflowService

    return WorkflowService(
        cli._get_runtime_database(),
        config=load_config(),
        llm_service=cli._build_soul_engine()._llm_service if with_llm else None,
    )


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@sources_app.command("list")
def list_sources() -> None:
    """列出独立来源、比例、流程和运行状态。"""
    _print({"items": _service().list_sources()})


@sources_app.command("prepare")
def prepare(
    url: str,
    intent: str = typer.Option(..., "--intent"),
    name: str = "",
    executor: Annotated[
        SourceExecutor, typer.Option(help="auto：网页用 Ego Lite，识别成功的 Feed 用 HTTP")
    ] = SourceExecutor.auto,
    separate: bool = False,
) -> None:
    """根据网址和自然语言目标建草稿；试跑后再 activate。"""
    from openbiliclaw.api.custom_sources import public_draft

    result = asyncio.run(
        _service(with_llm=executor != SourceExecutor.http).prepare(
            url=url,
            intent=intent,
            name=name,
            executor=executor.value,
            separate_source=separate,
        )
    )
    _print(public_draft(result))


@sources_app.command("trial")
def trial(draft_id: str) -> None:
    """重新试跑草稿，尚不启用来源。"""
    from openbiliclaw.api.custom_sources import public_draft

    _print(public_draft(asyncio.run(_service(with_llm=True).trial(draft_id))))


@sources_app.command("activate")
def activate(
    draft_id: str,
    share: int = typer.Option(1, min=1, max=20),
    interval: int = typer.Option(30, min=5, max=10080),
) -> None:
    """明确启用试跑成功的草稿，按现有 Discover 规则定期入池。"""
    _print(_service().activate(draft_id, target_share=share, interval_minutes=interval))


@sources_app.command("set")
def update(
    source_id: str,
    name: str | None = None,
    share: int | None = typer.Option(None, min=1, max=20),
    interval: int | None = typer.Option(None, min=5, max=10080),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """修改来源名称、比例、间隔或启停。"""
    values = {
        key: value
        for key, value in {
            "name": name,
            "target_share": share,
            "interval_minutes": interval,
            "enabled": enabled,
        }.items()
        if value is not None
    }
    _print(_service().sources.update_source(source_id, **values))


@sources_app.command("runs")
def runs(source_id: str) -> None:
    """显示最近抓取的版本、状态与候选数量。"""
    _print({"items": _service().store.runs(source_id)})


@sources_app.command("login")
def login(source_id: str) -> None:
    """生成手动登录入口；需打开同一数据目录的 API 服务。"""
    from openbiliclaw.api.custom_sources import public_draft

    _print(public_draft(_service().login_draft(source_id)))


@sources_app.command("run")
def run(source_id: str, limit: int = typer.Option(20, min=1, max=50), force: bool = False) -> None:
    """执行一次有界抓取与既有评估；定期运行使用 start / serve-api。"""
    from openbiliclaw import cli
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
    from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator

    config = load_config()
    database = cli._get_runtime_database()
    soul = cli._build_soul_engine()
    producer = CustomWorkflowProducer(
        database,
        source_id,
        cli._build_discovery_candidate_pipeline(
            config=config, database=database, discovery_engine=cli._build_discovery_engine()
        ),
        soul_engine=soul,
        config=config,
        llm_service=soul._llm_service,
        keyword_fetch_coordinator=KeywordFetchCoordinator(
            database=database, discovery_config=config.discovery
        ),
    )
    _print(asyncio.run(producer.produce_if_due(limit=limit, force=force)))
