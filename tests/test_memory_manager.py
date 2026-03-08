from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.memory.manager import MemoryManager

if TYPE_CHECKING:
    from pathlib import Path


def test_initialize_sets_up_database(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)

    memory.initialize()

    events = memory.query_events()
    assert events == []


@pytest.mark.asyncio
async def test_propagate_event_persists_to_sqlite(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    await memory.propagate_event(
        {
            "event_type": "view",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "测试视频",
            "metadata": {"bvid": "BV1xx411c7mD"},
        }
    )

    events = memory.query_events(event_types=["view"])
    assert len(events) == 1
    assert events[0]["title"] == "测试视频"
    assert "BV1xx411c7mD" in events[0]["metadata"]


@pytest.mark.asyncio
async def test_propagate_event_accepts_extension_behavior_types(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    for event_type in ["snapshot", "scroll", "hover", "pause", "seek", "coin"]:
        await memory.propagate_event(
            {
                "event_type": event_type,
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": f"{event_type} 事件",
                "metadata": {"bvid": "BV1xx411c7mD"},
            }
        )

    events = memory.query_events(limit=20)
    persisted_types = {event["event_type"] for event in events}

    for event_type in ["snapshot", "scroll", "hover", "pause", "seek", "coin"]:
        assert event_type in persisted_types


def test_query_events_and_stats_delegate_to_database(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()

    older = datetime.now() - timedelta(days=3)
    memory._database.conn.execute(
        """
        INSERT INTO events (event_type, url, title, context, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "search",
            "https://search.bilibili.com/all?keyword=music",
            "music",
            "{}",
            '{"keyword": "music"}',
            older.isoformat(sep=" "),
        ),
    )
    memory._database.conn.execute(
        """
        INSERT INTO events (event_type, url, title, context, metadata)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "feedback",
            "https://www.bilibili.com/video/BV1feedback",
            "feedback",
            "{}",
            '{"value": "like"}',
        ),
    )
    memory._database.conn.commit()

    queried = memory.query_events(keyword="like")
    stats = memory.get_event_stats()

    assert len(queried) == 1
    assert queried[0]["event_type"] == "feedback"
    assert stats == {"feedback": 1, "search": 1}


def test_get_core_memory_returns_trimmed_summary(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update(
        {
            "personality_portrait": "portrait",
            "core_traits": ["理性", "谨慎"],
            "values": ["成长", "真实"],
            "life_stage": "探索阶段",
            "deep_needs": ["被理解"],
        }
    )
    memory.get_layer("preference").data.update(
        {
            "interests": [
                {"name": "科技", "category": "知识", "weight": 0.9},
                {"name": "历史", "category": "知识", "weight": 0.8},
            ],
            "favorite_up_users": ["何同学", "影视飓风"],
            "disliked_topics": ["标题党"],
        }
    )
    memory.get_layer("awareness").data.update(
        {
            "notes": [
                {"date": "2026-03-08", "observation": "最近更专注。"},
                {"date": "2026-03-07", "observation": "晚上更容易进入深度浏览。"},
            ]
        }
    )
    memory.get_layer("insight").data.update(
        {
            "hypotheses": [
                {"hypothesis": "可能在寻找掌控感。", "confidence": 0.7},
                {"hypothesis": "内容选择偏向结构清晰的表达。", "confidence": 0.62},
            ]
        }
    )

    core = memory.get_core_memory()

    assert core["soul_summary"]["personality_portrait"] == "portrait"
    assert core["preference_summary"]["top_interests"][0]["name"] == "科技"
    assert core["recent_awareness"][0]["observation"] == "最近更专注。"
    assert core["active_insights"][0]["hypothesis"] == "可能在寻找掌控感。"


def test_render_core_memory_prompt_uses_stable_section_order(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("soul").data.update({"personality_portrait": "portrait"})
    memory.get_layer("preference").data.update(
        {"interests": [{"name": "科技", "category": "知识", "weight": 0.9}]}
    )
    memory.get_layer("awareness").data.update(
        {"notes": [{"date": "2026-03-08", "observation": "最近更专注。"}]}
    )
    memory.get_layer("insight").data.update(
        {"hypotheses": [{"hypothesis": "可能在寻找掌控感。", "confidence": 0.7}]}
    )

    prompt = memory.render_core_memory_prompt()

    assert prompt.index("## 用户画像") < prompt.index("## 偏好摘要")
    assert prompt.index("## 偏好摘要") < prompt.index("## 近期观察")
    assert prompt.index("## 近期观察") < prompt.index("## 当前洞察")
