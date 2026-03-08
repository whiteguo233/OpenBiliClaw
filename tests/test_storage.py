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

    def test_get_cached_content_returns_cached_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                view_count=100,
            )
            db.cache_content(
                "BV1B",
                title="Video B",
                up_name="UPB",
                source="trending",
                view_count=200,
            )

            cached = db.get_cached_content(limit=10)

            assert [item["bvid"] for item in cached] == ["BV1B", "BV1A"]
            assert cached[0]["source"] == "trending"

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

    def test_get_unrecommended_content_excludes_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                view_count=100,
            )
            db.cache_content(
                "BV1B",
                title="Video B",
                up_name="UPB",
                source="trending",
                view_count=200,
            )
            db.insert_recommendation("BV1A", confidence=0.91, presented=0)

            items = db.get_unrecommended_content(limit=10)

            assert [item["bvid"] for item in items] == ["BV1B"]

            db.close()

    def test_insert_and_get_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                expression="",
                topic="",
                presented=0,
            )

            rows = db.get_recommendations(limit=10)

            assert len(rows) == 1
            assert rows[0]["bvid"] == "BV1REC"
            assert rows[0]["confidence"] == 0.83
            assert rows[0]["presented"] == 0

            db.close()

    def test_update_recommendation_content_persists_expression_and_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                expression="",
                topic="",
                presented=0,
            )

            db.update_recommendation_content(
                recommendation_id,
                expression="这条视频会接住你最近想把问题想透的劲头。",
                topic="你最近那股想把问题想透的劲头",
            )

            rows = db.get_recommendations(limit=10)

            assert rows[0]["expression"] == "这条视频会接住你最近想把问题想透的劲头。"
            assert rows[0]["topic"] == "你最近那股想把问题想透的劲头"

            db.close()

    def test_mark_recommendations_presented_sets_presented_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            first_id = db.insert_recommendation("BV1REC1", confidence=0.83, presented=0)
            second_id = db.insert_recommendation("BV1REC2", confidence=0.71, presented=0)

            db.mark_recommendations_presented([first_id, second_id])

            rows = db.get_recommendations(limit=10)

            assert rows[0]["presented"] == 1
            assert rows[1]["presented"] == 1
            assert rows[0]["presented_at"] is not None
            assert rows[1]["presented_at"] is not None

            db.close()
