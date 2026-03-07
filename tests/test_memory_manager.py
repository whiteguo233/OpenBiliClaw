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
