"""Tests for the Storage database module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from openbiliclaw.storage.database import Database


class TestDatabase:
    """Test SQLite database operations."""

    def test_initialize(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            assert db.conn is not None
            db.close()

    def test_insert_and_get_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            row_id = db.insert_event(
                "click",
                url="https://www.bilibili.com/video/BV1234",
                title="Test Video",
                metadata={"element": "title"},
            )
            assert row_id > 0

            events = db.get_recent_events(limit=10)
            assert len(events) == 1
            assert events[0]["event_type"] == "click"
            assert events[0]["url"] == "https://www.bilibili.com/video/BV1234"

            db.close()

    def test_cache_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1test",
                title="Test Video",
                up_name="TestUP",
                tags=["AI", "编程"],
                source="search",
            )

            cursor = db.conn.execute(
                "SELECT * FROM content_cache WHERE bvid = ?", ("BV1test",)
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["title"] == "Test Video"
            assert row["up_name"] == "TestUP"

            db.close()

    def test_query_events_supports_type_keyword_and_time_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            now = datetime.now()
            older = (now - timedelta(days=2)).isoformat(sep=" ")
            recent = now.isoformat(sep=" ")

            db.conn.execute(
                """
                INSERT INTO events (event_type, url, title, context, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "view",
                    "https://www.bilibili.com/video/BVOLD",
                    "Old Video",
                    "{}",
                    '{"bvid": "BVOLD"}',
                    older,
                ),
            )
            db.conn.execute(
                """
                INSERT INTO events (event_type, url, title, context, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "search",
                    "https://search.bilibili.com/all?keyword=ai",
                    "AI Search",
                    "{}",
                    '{"keyword": "ai"}',
                    recent,
                ),
            )
            db.conn.commit()

            events = db.query_events(
                event_types=["search"],
                start_time=now - timedelta(hours=1),
                keyword="ai",
            )

            assert len(events) == 1
            assert events[0]["event_type"] == "search"
            assert "AI Search" in events[0]["title"]

            db.close()

    def test_count_events_by_type_returns_grouped_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_event("view", title="video-1")
            db.insert_event("view", title="video-2")
            db.insert_event("click", title="card")

            stats = db.count_events_by_type()

            assert stats == {"click": 1, "view": 2}

            db.close()
