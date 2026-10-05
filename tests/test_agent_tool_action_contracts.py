"""Read tools must preserve the identities required by follow-up agent actions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.agent.tools import AgentToolContext, build_agent_tool_registry
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "agent-action-contracts.db")
    database.initialize()
    yield database
    database.close()


def _references(content: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.partition("定位信息: ")[2])
        for line in content.splitlines()
        if "定位信息: " in line
    ]


async def test_listed_source_can_be_identified_for_toggle(database: Database) -> None:
    registry = build_agent_tool_registry(AgentToolContext(database=database))
    await registry.dispatch(
        "create_source",
        {"source_type": "web", "name": "同名订阅", "strategy": "search", "query": "机械键盘"},
    )
    await registry.dispatch(
        "create_source",
        {"source_type": "web", "name": "同名订阅", "strategy": "search", "query": "露营"},
    )

    listed = await registry.dispatch("list_sources", {})
    recipes = database.get_all_recipes()
    for recipe in recipes:
        assert recipe["id"] in listed.content
        assert recipe["config"]["query"] in listed.content
    result = await registry.dispatch("toggle_source", {"id": recipes[0]["id"], "enabled": False})
    assert result.ok
    assert not database.get_all_recipes()[0]["enabled"]


async def test_pool_preview_can_be_saved_without_guessing_content_identity(
    database: Database,
) -> None:
    database.cache_content(
        "xiaohongshu:note-contract",
        source_platform="xiaohongshu",
        content_id="note-contract",
        content_type="note",
        content_url="https://www.xiaohongshu.com/explore/note-contract?xsec_token=public-link",
        title="收藏这篇笔记",
        author_name="测试作者",
        relevance_score=0.9,
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_expression='推荐理由', pool_topic_label='测试', "
        "style_key='deep_focus', topic_group='test'"
    )
    database.conn.commit()
    registry = build_agent_tool_registry(
        AgentToolContext(
            database=database,
            saved_sync_service=SavedSyncService(database, NativeSaveRouter()),
        )
    )
    preview = await registry.dispatch("get_recommendations", {"limit": 1})
    references = _references(preview.content)
    assert len(references) == 1, preview.content
    identity = references[0]
    assert identity["content_id"] == "note-contract"
    assert identity["source_platform"] == "xiaohongshu"
    assert "recommendation_id" not in identity  # An unserved pool row has no feedback ID.
    saved = await registry.dispatch("save_item", {**identity, "list_kind": "favorite"})
    assert saved.ok, saved.content
    row = database.get_saved_membership("favorite", "xiaohongshu:note-contract")
    assert row is not None
    assert row["content_url"].endswith("?xsec_token=public-link")
    assert row["content_type"] == "note"
    assert database.count_pool_candidates() == 1  # Reading/saving never consumes the pool.


@pytest.mark.parametrize("kind", ["shown", "favorite"])
async def test_history_preserves_content_and_feedback_identifiers(
    database: Database, kind: str
) -> None:
    item = SavedItemInput(
        source_platform="bilibili",
        content_id="BV1CONTRACT",
        title="同标题内容",
        content_url="https://www.bilibili.com/video/BV1CONTRACT",
    )
    database.cache_content(
        item.content_id,
        title=item.title,
        content_id=item.content_id,
        content_url=item.content_url,
        relevance_score=0.9,
    )
    recommendation_id = database.insert_recommendation(item.content_id, confidence=0.9)
    database.upsert_saved_membership("favorite", item)
    registry = build_agent_tool_registry(AgentToolContext(database=database))

    history = await registry.dispatch("get_watch_history", {"kind": kind})
    references = _references(history.content)
    assert len(references) == 1, history.content
    assert references[0]["content_id"] == item.content_id
    assert references[0]["content_url"] == item.content_url
    if kind == "shown":
        assert references[0]["recommendation_id"] == recommendation_id
