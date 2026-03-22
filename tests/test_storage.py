"""Tests for the Storage database module."""

import sqlite3
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

    def test_cache_content_persists_relevance_and_candidate_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                relevance_score=0.88,
                relevance_reason="fits profile",
                candidate_tier="primary",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["relevance_score"] == 0.88
            assert row["relevance_reason"] == "fits profile"
            assert row["candidate_tier"] == "primary"

            db.close()

    def test_cache_content_persists_topic_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1TOPIC",
                title="讲透中东局势",
                up_name="国际观察",
                source="search",
                topic_key="国际时事:地缘政治",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["topic_key"] == "国际时事:地缘政治"

            db.close()

    def test_cache_content_persists_style_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1STYLE",
                title="杀戮尖塔2 实机演示",
                up_name="游戏研究所",
                source="related_chain",
                style_key="game_strategy",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["style_key"] == "game_strategy"

            db.close()

    def test_cache_content_persists_pool_copy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1COPY",
                title="池子里的预生成文案",
                up_name="文案实验室",
                source="search",
                pool_expression="这条会接住你最近想把问题拆开的状态。",
                pool_topic_label="你最近那股想拆问题的劲头",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["pool_expression"] == "这条会接住你最近想把问题拆开的状态。"
            assert row["pool_topic_label"] == "你最近那股想拆问题的劲头"

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

    def test_trim_explore_cluster_overflow_suppresses_excess_manufacturing_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index, score in enumerate((0.91, 0.89, 0.87, 0.83), start=1):
                db.cache_content(
                    f"BV1MFG{index}",
                    title=f"超级工厂制造纪录片 {index}",
                    up_name="工业观察",
                    source="explore",
                    topic_key="精密制造纪录片",
                    relevance_score=score,
                )
            db.cache_content(
                "BV1OTHER",
                title="科幻小说设定解析",
                up_name="科幻电台",
                source="explore",
                topic_key="科幻小说深度解析",
                relevance_score=0.8,
            )

            suppressed = db.trim_explore_cluster_overflow(max_per_cluster=2)

            assert suppressed == 2
            rows = db.get_cached_content(limit=10)
            fresh_manufacturing = [
                row
                for row in rows
                if row["source"] == "explore"
                and row["topic_key"] == "精密制造纪录片"
                and row["pool_status"] == "fresh"
            ]
            assert [row["bvid"] for row in fresh_manufacturing] == ["BV1MFG1", "BV1MFG2"]

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

    def test_get_unrecommended_content_orders_by_tier_then_relevance_and_recency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1BACK",
                title="补货高分",
                up_name="UPA",
                source="search",
                view_count=1000,
                relevance_score=0.95,
                candidate_tier="backfill",
            )
            db.cache_content(
                "BV1OLD",
                title="主候选旧",
                up_name="UPB",
                source="search",
                view_count=20,
                relevance_score=0.82,
                candidate_tier="primary",
            )
            db.cache_content(
                "BV1NEW",
                title="主候选新",
                up_name="UPC",
                source="search",
                view_count=10,
                relevance_score=0.82,
                candidate_tier="primary",
            )
            db.conn.execute(
                "UPDATE content_cache SET last_scored_at = ? WHERE bvid = ?",
                ("2026-03-09 08:00:00", "BV1OLD"),
            )
            db.conn.execute(
                "UPDATE content_cache SET last_scored_at = ? WHERE bvid = ?",
                ("2026-03-10 08:00:00", "BV1NEW"),
            )
            db.conn.commit()

            items = db.get_unrecommended_content(limit=10)

            assert [item["bvid"] for item in items] == ["BV1NEW", "BV1OLD", "BV1BACK"]

            db.close()

    def test_get_pool_candidates_skips_shown_and_feedbacked_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
                relevance_reason="你会想点开这种把事情讲透的内容。",
            )
            db.cache_content(
                "BV1SHOWN",
                title="已经展示",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
                relevance_reason="这条已经展示过。",
            )
            db.cache_content(
                "BV1FB",
                title="已经反馈",
                up_name="UPC",
                source="search",
                relevance_score=0.93,
                relevance_reason="这条已经被反馈过。",
            )
            db.cache_content(
                "BV1REC",
                title="已经进过推荐表",
                up_name="UPD",
                source="search",
                relevance_score=0.89,
                relevance_reason="这条已经生成过推荐。",
            )
            db.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'shown', recommended_at = CURRENT_TIMESTAMP "
                "WHERE bvid = 'BV1SHOWN'"
            )
            db.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'feedbacked', feedback_type = 'dislike', "
                "feedback_at = CURRENT_TIMESTAMP WHERE bvid = 'BV1FB'"
            )
            db.insert_recommendation("BV1REC", confidence=0.6)
            db.conn.commit()

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["BV1FRESH"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_returns_topic_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1POOL",
                title="AI 模型能力边界",
                up_name="技术拆机局",
                source="search",
                relevance_score=0.91,
                topic_key="AI:大模型",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["topic_key"] == "AI:大模型"

            db.close()

    def test_get_pool_candidates_returns_style_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1STYLEPOOL",
                title="智慧城市空镜素材",
                up_name="视觉资料库",
                source="explore",
                relevance_score=0.84,
                style_key="visual_showcase",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["style_key"] == "visual_showcase"

            db.close()

    def test_get_pool_candidates_returns_precomputed_copy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1PRE",
                title="预生成测试",
                up_name="实验频道",
                source="explore",
                relevance_score=0.84,
                pool_expression="这条先给你备好了推荐理由。",
                pool_topic_label="先备好的那股味儿",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["pool_expression"] == "这条先给你备好了推荐理由。"
            assert items[0]["pool_topic_label"] == "先备好的那股味儿"

            db.close()

    def test_get_pool_candidates_skips_recently_viewed_bvids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
            )
            db.cache_content(
                "BV1SEEN",
                title="已经看过",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
            )
            db.insert_event(
                "view",
                title="已经看过",
                url="https://www.bilibili.com/video/BV1SEEN",
                metadata={"bvid": "BV1SEEN"},
            )

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["BV1FRESH"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_balances_sources_in_candidate_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(5):
                db.cache_content(
                    f"BVEXP{index}",
                    title=f"探索候选 {index}",
                    up_name="探索频道",
                    source="explore",
                    relevance_score=0.99 - index * 0.01,
                )
            db.cache_content(
                "BVSEARCH",
                title="搜索候选",
                up_name="搜索频道",
                source="search",
                relevance_score=0.8,
            )
            db.cache_content(
                "BVTREND",
                title="热榜候选",
                up_name="热榜频道",
                source="trending",
                relevance_score=0.79,
            )
            db.cache_content(
                "BVREL",
                title="相关推荐候选",
                up_name="相关频道",
                source="related_chain",
                relevance_score=0.78,
            )

            items = db.get_pool_candidates(limit=6)
            sources = [item["source"] for item in items]

            assert sources[:4] == ["search", "trending", "related_chain", "explore"]
            assert sources.count("explore") == 3

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

    def test_insert_recommendation_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 7

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            recommendation_id = db.insert_recommendation("BV1LOCK", confidence=0.6)

            assert recommendation_id == 7
            assert fake_conn.calls == 2
            assert fake_conn.commits == 1

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

    def test_update_recommendation_content_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.update_recommendation_content(
                7,
                expression="这条更贴你最近的状态。",
                topic="最近更吃这一路",
            )

            assert fake_conn.calls == 2
            assert fake_conn.commits == 1

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

    def test_mark_recommendations_presented_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: list[object]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.mark_recommendations_presented([1, 2])

            assert fake_conn.calls == 2
            assert fake_conn.commits == 1

    def test_get_recommendation_by_id_returns_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BV1REC",
                title="讲透城市与建筑",
                up_name="城市观察局",
                source="search",
            )

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                presented=0,
            )

            row = db.get_recommendation_by_id(recommendation_id)

            assert row is not None
            assert row["id"] == recommendation_id
            assert row["bvid"] == "BV1REC"
            assert row["title"] == "讲透城市与建筑"

            db.close()

    def test_update_recommendation_feedback_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                presented=0,
            )

            db.update_recommendation_feedback(
                recommendation_id,
                feedback_type="dislike",
                feedback_note="太浅了",
            )

            row = db.get_recommendation_by_id(recommendation_id)

            assert row is not None
            assert row["feedback_type"] == "dislike"
            assert row["feedback_note"] == "太浅了"
            assert row["feedback_at"] is not None

            db.close()

    def test_update_recommendation_feedback_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.update_recommendation_feedback(
                7,
                feedback_type="dislike",
                feedback_note="太浅了",
            )

            assert fake_conn.calls == 3
            assert fake_conn.commits == 2

    def test_notification_candidate_prefers_unpresented_unnotified_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BVLOW", title="普通内容", up_name="普通UP", source="search")
            db.cache_content("BVHIGH", title="高置信内容", up_name="高能UP", source="trending")
            low_id = db.insert_recommendation("BVLOW", confidence=0.7, presented=0)
            high_id = db.insert_recommendation("BVHIGH", confidence=0.91, presented=0)

            candidate = db.get_notification_candidate(min_confidence=0.82)

            assert candidate is not None
            assert candidate["id"] == high_id
            assert candidate["bvid"] == "BVHIGH"

            db.mark_notification_sent("BVHIGH")

            next_candidate = db.get_notification_candidate(min_confidence=0.82)

            assert next_candidate is None
            assert low_id > 0

            db.close()


class TestDatabaseMaintenance:
    def test_check_database_integrity_reports_healthy_database(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import check_database_integrity

        db = Database(tmp_path / "healthy.db")
        db.initialize()
        db.insert_event("view", title="健康检查")
        db.close()

        report = check_database_integrity(tmp_path / "healthy.db")

        assert report.healthy is True
        assert report.error == ""

    def test_create_database_backup_copies_db_and_wal(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import create_database_backup

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("db", encoding="utf-8")
        wal_path = tmp_path / "openbiliclaw.db-wal"
        wal_path.write_text("wal", encoding="utf-8")

        backup = create_database_backup(
            db_path,
            tmp_path / "backups",
            timestamp="20260315-020000",
        )

        assert backup.db_backup.read_text(encoding="utf-8") == "db"
        assert backup.wal_backup is not None
        assert backup.wal_backup.read_text(encoding="utf-8") == "wal"

    def test_rotate_database_backups_keeps_recent_daily_and_weekly_sets(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import rotate_database_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for index in range(10):
            stamp = f"202603{index + 1:02d}-020000"
            (backup_dir / f"openbiliclaw-{stamp}.db").write_text("db", encoding="utf-8")

        rotate_database_backups(
            backup_dir,
            keep_daily=3,
            keep_weekly=2,
            now=datetime(2026, 3, 15, 2, 0, 0),
        )

        kept = sorted(path.name for path in backup_dir.glob("*.db"))
        assert len(kept) == 5

    def test_repair_database_returns_healthy_status_without_modifying_healthy_db(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        db.insert_event("view", title="还不用修")
        db.close()
        before = (tmp_path / "openbiliclaw.db").read_bytes()

        result = repair_database(
            tmp_path / "openbiliclaw.db",
            backup_dir=tmp_path / "backups",
        )

        assert result.status == "healthy"
        assert result.repaired_db is None
        assert (tmp_path / "openbiliclaw.db").read_bytes() == before

    def test_repair_database_refuses_when_database_is_in_use(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=["python:86577"],
        )

        assert result.status == "in_use"
        assert "python:86577" in result.message

    def test_repair_database_keeps_original_when_recovery_fails(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")
        original = db_path.read_bytes()

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=[],
            integrity_error="database disk image is malformed",
            recovered_sql=None,
        )

        assert result.status == "failed"
        assert db_path.read_bytes() == original
        assert result.repaired_db is None

    def test_repair_database_builds_repaired_copy_when_recovery_sql_is_available(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=[],
            integrity_error="database disk image is malformed",
            recovered_sql=(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT);"
                "INSERT INTO events (id, title) VALUES (1, '恢复成功');"
            ),
        )

        assert result.status == "repaired"
        assert result.repaired_db is not None
        repaired = sqlite3.connect(result.repaired_db)
        row = repaired.execute("SELECT title FROM events").fetchone()
        repaired.close()
        assert row == ("恢复成功",)
