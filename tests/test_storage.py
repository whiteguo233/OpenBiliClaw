"""Tests for the Storage database module."""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import Database


def _seed_visible(db: Database, bvid: str, **kwargs: Any) -> None:
    """v0.3.57+ shorthand: cache a row visible to the pool gate.

    ``cache_content`` + auto-fill of ``pool_expression`` / ``pool_topic_label``
    so the row passes ``get_pool_candidates``'s precompute gate. Tests
    asserting gate behavior on empty-copy rows must use ``cache_content``
    directly instead.
    """
    kwargs.setdefault("pool_expression", "测试推荐文案")
    kwargs.setdefault("pool_topic_label", "测试主题")
    db.cache_content(bvid, **kwargs)


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

            cursor = db.conn.execute("SELECT * FROM content_cache WHERE bvid = ?", ("BV1test",))
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

    def test_trim_topic_group_overflow_suppresses_cross_source_excess(self) -> None:
        """A hot topic_group accumulated from multiple sources gets capped down
        to max_per_group, keeping the highest-scored items regardless of
        source."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # 5 items in 人工智能 group across 3 sources, varying scores
            for i, (score, source) in enumerate(
                [
                    (0.95, "related_chain"),
                    (0.92, "related_chain"),
                    (0.88, "search"),
                    (0.85, "explore"),
                    (0.80, "related_chain"),
                ]
            ):
                db.cache_content(
                    f"BV1AI{i}",
                    title=f"AI 内容 {i}",
                    up_name="UP",
                    source=source,
                    topic_key="人工智能",
                    topic_group="人工智能",
                    relevance_score=score,
                )
            # 1 item in a different group — must remain untouched
            db.cache_content(
                "BV1MUSIC",
                title="古典音乐讲解",
                up_name="UP",
                source="trending",
                topic_key="音乐",
                topic_group="音乐",
                relevance_score=0.7,
            )
            # 1 item with empty topic_group — must remain untouched
            db.cache_content(
                "BV1NOGROUP",
                title="未分组",
                up_name="UP",
                source="search",
                topic_key="random",
                topic_group="",
                relevance_score=0.6,
            )

            suppressed = db.trim_topic_group_overflow(max_per_group=2)

            assert suppressed == 3  # 5 AI items - 2 kept = 3 suppressed
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # Top 2 AI items by score survive (cross-source)
            assert by_bvid["BV1AI0"]["pool_status"] == "fresh"
            assert by_bvid["BV1AI1"]["pool_status"] == "fresh"
            assert by_bvid["BV1AI2"]["pool_status"] == "suppressed"
            assert by_bvid["BV1AI3"]["pool_status"] == "suppressed"
            assert by_bvid["BV1AI4"]["pool_status"] == "suppressed"
            # Unrelated topic + empty-group items untouched
            assert by_bvid["BV1MUSIC"]["pool_status"] == "fresh"
            assert by_bvid["BV1NOGROUP"]["pool_status"] == "fresh"

            db.close()

    def test_trim_topic_group_overflow_noop_when_under_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(3):
                db.cache_content(
                    f"BV1X{i}",
                    title=f"AI {i}",
                    up_name="UP",
                    source="search",
                    topic_group="人工智能",
                    relevance_score=0.8,
                )

            suppressed = db.trim_topic_group_overflow(max_per_group=5)
            assert suppressed == 0
            db.close()

    def test_cache_content_refreshes_previously_suppressed_items(self) -> None:
        """Re-discovering a 'suppressed' item must flip pool_status back to
        'fresh'. Suppression is an internal diversity decision (trim cuts,
        topic cap); when the discovery layer re-finds the item it deserves
        another shot. Without this, slow-churning sources like B站 trending
        get bottlenecked because hot BVIDs cached as 'suppressed' never
        recover. 'shown' / 'feedbacked' / 'purged_by_dislike' must NOT
        re-fresh — those reflect user-facing state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Seed three items, then force them into different terminal states.
            for status in ("suppressed", "shown", "purged_by_dislike"):
                bvid = f"BV1{status}"
                db.cache_content(
                    bvid,
                    title=f"item {status}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.7,
                )
                db._execute_write(
                    "UPDATE content_cache SET pool_status = ? WHERE bvid = ?",
                    (status, bvid),
                )

            # Re-discover all three (simulates trending re-fetching same BVIDs)
            for status in ("suppressed", "shown", "purged_by_dislike"):
                db.cache_content(
                    f"BV1{status}",
                    title=f"item {status}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.8,
                )

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            # Suppressed re-fresh ✓
            assert by_bvid["BV1suppressed"]["pool_status"] == "fresh"
            # Shown stays shown (user already saw)
            assert by_bvid["BV1shown"]["pool_status"] == "shown"
            # Disliked stays purged
            assert by_bvid["BV1purged_by_dislike"]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_trim_pool_share_quotas_protect_under_target_sources(self) -> None:
        """When trim is given platform quotas, over-quota platforms get
        suppressed first even if they have higher scores than under-quota
        platforms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Bilibili has 5 items at score 0.95 (high), Douyin has 3 at 0.60 (low).
            # Total = 8, target = 6, so 2 must be suppressed.
            # Without share quotas: all low-score Douyin items get axed.
            # With quota bilibili=2: 3 of Bilibili's 5 are over-quota → those go first,
            # protecting Douyin entirely.
            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVT{i}",
                    title=f"T{i}",
                    up_name="DY",
                    source="dy-plugin-search",
                    source_platform="douyin",
                    content_url=f"https://www.douyin.com/video/{i}",
                    relevance_score=0.60,
                )

            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 2, "douyin": 4},
            )
            assert suppressed == 2

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # All Douyin kept (under quota of 4) — this is the protection.
            # Without share quotas, Douyin (low score) would get axed first.
            assert all(by_bvid[f"BVT{i}"]["pool_status"] == "fresh" for i in range(3))
            # Bilibili lost the bottom 2 (suppressed), kept top 3: 2 within quota
            # + 1 backfill from over-quota since target=6 had remaining slot.
            search_fresh = [
                bvid
                for bvid in (f"BVS{i}" for i in range(5))
                if by_bvid[bvid]["pool_status"] == "fresh"
            ]
            assert len(search_fresh) == 3
            assert by_bvid["BVS3"]["pool_status"] == "suppressed"
            assert by_bvid["BVS4"]["pool_status"] == "suppressed"
            db.close()

    def test_trim_pool_source_overflow_enforces_platform_hard_caps(self) -> None:
        """Platform shares reserve capacity; one platform must not fill another's slot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.80 + i / 100,
                )
            for i in range(8):
                db.cache_content(
                    f"XHS{i}",
                    title=f"X{i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_url=f"https://www.xiaohongshu.com/explore/XHS{i}?xsec_token=ABC=",
                    relevance_score=0.90 + i / 100,
                )
            db.cache_content(
                "dy:1",
                title="D1",
                up_name="DY",
                source="dy-plugin-search",
                source_platform="douyin",
                content_url="https://www.douyin.com/video/1",
                relevance_score=0.50,
            )

            suppressed = db.trim_pool_source_overflow(
                source_share_quotas={"bilibili": 5, "xiaohongshu": 2, "douyin": 2},
            )

            assert suppressed == 6
            assert db.count_pool_candidates_by_source() == {
                "bilibili": 5,
                "xiaohongshu": 2,
                "douyin": 1,
            }
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            xhs_fresh = [
                bvid
                for bvid in (f"XHS{i}" for i in range(8))
                if by_bvid[bvid]["pool_status"] == "fresh"
            ]
            assert len(xhs_fresh) == 2
            db.close()

    def test_trim_pool_legacy_score_only_when_no_quotas(self) -> None:
        """Without source_share_quotas, the trim must keep its old score-first
        behavior — that's the path used by callers that don't care about
        per-source diversity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(5):
                db.cache_content(
                    f"BVHIGH{i}",
                    title=f"H{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVLOW{i}",
                    title=f"L{i}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.30,
                )

            suppressed = db.trim_pool_to_target_count(target=5)
            assert suppressed == 3

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # All low-score trending suppressed, all high-score search kept
            assert all(by_bvid[f"BVHIGH{i}"]["pool_status"] == "fresh" for i in range(5))
            assert all(by_bvid[f"BVLOW{i}"]["pool_status"] == "suppressed" for i in range(3))
            db.close()

    def test_trim_pool_protects_under_quota_source_when_untracked_sources_present(
        self,
    ) -> None:
        """The bug this prevents: untracked sources eat pool slots,
        pushing total > target. The trim must suppress untracked items before
        cutting under-quota tracked sources (Douyin). Without this guard,
        sum(in_quota) > target leads to score-based cuts that hit Douyin
        first because trending scores are systematically lower."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Bilibili at quota (5/5), Douyin under quota (2 of 4), manual import
            # (4 untracked).
            # Total = 11, target = 8, so 3 must go.
            # Bug-prone behavior: Douyin scores are 0.5 (low), so naïve
            # trim would axe both Douyin items.
            # Correct behavior: untracked manual-import items get cut first.
            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(2):
                db.cache_content(
                    f"BVT{i}",
                    title=f"T{i}",
                    up_name="DY",
                    source="dy-plugin-search",
                    source_platform="douyin",
                    content_url=f"https://www.douyin.com/video/{i}",
                    relevance_score=0.50,
                )
            for i in range(4):
                db.cache_content(
                    f"BVM{i}",
                    title=f"X{i}",
                    up_name="UP",
                    source="manual-import",
                    relevance_score=0.70,
                )

            suppressed = db.trim_pool_to_target_count(
                target=8,
                source_share_quotas={"bilibili": 5, "douyin": 4},
            )
            assert suppressed == 3

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # Douyin fully protected (under quota, no items lost)
            assert all(by_bvid[f"BVT{i}"]["pool_status"] == "fresh" for i in range(2))
            # Bilibili fully protected (at quota, no over-quota items)
            assert all(by_bvid[f"BVS{i}"]["pool_status"] == "fresh" for i in range(5))
            # 3 of the 4 manual-import items suppressed (lowest score among negotiable)
            manual_fresh = sum(1 for i in range(4) if by_bvid[f"BVM{i}"]["pool_status"] == "fresh")
            assert manual_fresh == 1
            db.close()

    def test_count_pool_candidates_by_source_collapses_xhs_source_family(self) -> None:
        """Xiaohongshu extension channels count as one source family.

        The refresh controller consumes this summary to decide which Bilibili
        strategies are deficient. If raw xhs-extension-* names leak through,
        xhs content is invisible to the source-balance accounting.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BVSOURCE", title="S", up_name="UP", source="search")
            db.cache_content(
                "XHS-TASK-1",
                title="X1",
                up_name="XHS",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_url=("https://www.xiaohongshu.com/explore/XHS-TASK-1?xsec_token=ABC="),
            )
            db.cache_content(
                "XHS-SEARCH-1",
                title="X2",
                up_name="XHS",
                source="xhs-extension-search",
                source_platform="xiaohongshu",
                content_url=("https://www.xiaohongshu.com/explore/XHS-SEARCH-1?xsec_token=ABC="),
            )
            db.cache_content(
                "XHS-LEGACY-1",
                title="X3",
                up_name="XHS",
                source="xhs-extension-profile",
                content_url=("https://www.xiaohongshu.com/explore/XHS-LEGACY-1?xsec_token=ABC="),
            )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 1, "xiaohongshu": 3}
            db.close()

    def test_count_pool_candidates_by_source_collapses_douyin_source_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BVSOURCE", title="S", up_name="UP", source="search")
            db.cache_content(
                "dy:1",
                title="D1",
                up_name="DY",
                source="dy-direct-search",
                source_platform="douyin",
                content_id="1",
                content_url="https://www.douyin.com/video/1",
            )
            db.cache_content(
                "dy:2",
                title="D2",
                up_name="DY",
                source="dy-direct-hot",
                source_platform="douyin",
                content_id="2",
                content_url="https://www.douyin.com/video/2",
            )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 1, "douyin": 2}
            db.close()

    def test_count_pool_candidates_by_source_collapses_bilibili_source_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for source in ("search", "related_chain", "trending", "explore"):
                db.cache_content(
                    f"BV-{source}",
                    title=source,
                    up_name="UP",
                    source=source,
                )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 4}
            db.close()

    def test_trim_pool_share_quotas_protect_xhs_source_family(self) -> None:
        """Xiaohongshu rows are protected by the xiaohongshu quota.

        Raw xhs-extension-* sources must not be treated as generic untracked
        items; otherwise a high-scored unknown source can crowd xhs out even
        when the xiaohongshu source family is under its quota.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(3):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVMANUAL{i}",
                    title=f"M{i}",
                    up_name="UP",
                    source="manual-import",
                    relevance_score=0.90,
                )
            for i, source in enumerate(
                ("xhs-extension-task", "xhs-extension-search", "xhs-extension-profile")
            ):
                db.cache_content(
                    f"XHS-QUOTA-{i}",
                    title=f"X{i}",
                    up_name="XHS",
                    source=source,
                    source_platform="xiaohongshu",
                    content_url=(
                        f"https://www.xiaohongshu.com/explore/XHS-QUOTA-{i}?xsec_token=ABC="
                    ),
                    relevance_score=0.50,
                )

            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )

            assert suppressed == 3
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            assert all(by_bvid[f"XHS-QUOTA-{i}"]["pool_status"] == "fresh" for i in range(3))
            assert all(by_bvid[f"BVMANUAL{i}"]["pool_status"] == "suppressed" for i in range(3))
            db.close()

    def test_reactivate_under_quota_pool_sources_restores_suppressed_xhs_family(
        self,
    ) -> None:
        """A full Bilibili pool can make room for existing suppressed xhs rows.

        This covers the production shape where xhs rows were previously
        suppressed while raw xhs-extension-* sources were invisible to source
        quotas. Once xiaohongshu has its own family quota, high-scored
        suppressed rows should be allowed back into the fresh pool and then
        normal cap trimming removes over-quota sources.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(6):
                _seed_visible(
                    db,
                    f"BVSRC{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                note_id = f"xhs-reactivate-{i}"
                _seed_visible(
                    db,
                    note_id,
                    title=f"X{i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_url=(f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=ABC="),
                    relevance_score=0.80,
                )
                db._execute_write(
                    "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
                    (note_id,),
                )

            reactivated = db.reactivate_under_quota_pool_sources(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )
            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )

            assert reactivated == 3
            assert suppressed == 3
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            assert all(by_bvid[f"xhs-reactivate-{i}"]["pool_status"] == "fresh" for i in range(3))
            search_fresh = sum(
                1 for i in range(6) if by_bvid[f"BVSRC{i}"]["pool_status"] == "fresh"
            )
            assert search_fresh == 3
            assert db.count_pool_candidates() == 6
            db.close()

    def test_purge_pool_by_disliked_topics_matches_topic_key_exact(self) -> None:
        """An exact topic_key match should flip pool_status to purged_by_dislike."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1ghost",
                title="鬼畜全明星2026",
                up_name="鬼畜区UP",
                source="trending",
                topic_key="鬼畜",
                pool_topic_label="娱乐",
            )
            db.cache_content(
                "BV2ai",
                title="深度学习与Transformer",
                up_name="AI教程君",
                source="search",
                topic_key="AI技术",
                pool_topic_label="知识",
            )

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            assert by_bvid["BV1ghost"]["pool_status"] == "purged_by_dislike"
            assert by_bvid["BV2ai"]["pool_status"] == "fresh"

            db.close()

    def test_purge_pool_by_disliked_topics_matches_title_substring(self) -> None:
        """Substring match on title should catch videos even when topic_key differs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Topic_key is "年终总结" but title contains "鬼畜"
            db.cache_content(
                "BV1mix",
                title="跨年鬼畜合集",
                up_name="混剪UP",
                source="explore",
                topic_key="年终总结",
                pool_topic_label="娱乐",
            )
            db.cache_content(
                "BV2pure",
                title="纯知识内容",
                up_name="知识UP",
                source="search",
                topic_key="科技",
                pool_topic_label="知识",
            )

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            assert by_bvid["BV1mix"]["pool_status"] == "purged_by_dislike"
            assert by_bvid["BV2pure"]["pool_status"] == "fresh"

            db.close()

    def test_purge_pool_by_disliked_topics_matches_pool_topic_label(self) -> None:
        """Matching on pool_topic_label should also purge candidates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1label",
                title="各种奇葩挑战",
                up_name="挑战UP",
                source="trending",
                topic_key="挑战视频",
                pool_topic_label="整蛊",
            )

            purged = db.purge_pool_by_disliked_topics(["整蛊"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_purge_pool_by_disliked_topics_skips_already_recommended(self) -> None:
        """Items already in the recommendations table must not be purged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1kept",
                title="鬼畜经典",
                up_name="怀旧UP",
                source="trending",
                topic_key="鬼畜",
                pool_topic_label="娱乐",
            )
            # Insert a recommendation row pointing at this bvid
            db.conn.execute(
                """
                INSERT INTO recommendations (
                    bvid, expression, topic, confidence, created_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                ("BV1kept", "这条鬼畜应该对味", "娱乐", 0.9),
            )
            db.conn.commit()

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 0

            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "fresh", (
                "Already-recommended items must be preserved for history audit"
            )
            db.close()

    def test_purge_pool_by_disliked_topics_skips_non_fresh_items(self) -> None:
        """Only fresh candidates should be touched; shown/stale are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1shown",
                title="鬼畜视频",
                up_name="鬼畜UP",
                source="trending",
                topic_key="鬼畜",
            )
            db.cache_content(
                "BV2fresh",
                title="另一个鬼畜视频",
                up_name="鬼畜UP",
                source="trending",
                topic_key="鬼畜",
            )
            db.conn.execute(
                "UPDATE content_cache SET pool_status='shown' WHERE bvid = ?",
                ("BV1shown",),
            )
            db.conn.commit()

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1, "Only the fresh item should be purged"

            rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
            assert rows["BV1shown"]["pool_status"] == "shown"
            assert rows["BV2fresh"]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_purge_pool_by_disliked_topics_empty_list_is_noop(self) -> None:
        """Empty or whitespace-only topics should do nothing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content("BV1", title="test", up_name="u", source="s", topic_key="k")
            assert db.purge_pool_by_disliked_topics([]) == 0
            assert db.purge_pool_by_disliked_topics(["", "  "]) == 0
            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "fresh"
            db.close()

    def test_purge_pool_by_disliked_topics_multi_topic_batch(self) -> None:
        """Multiple topics in one call should purge any matching item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BV1", title="鬼畜A", up_name="u", source="s", topic_key="鬼畜")
            db.cache_content("BV2", title="恐怖片B", up_name="u", source="s", topic_key="恐怖")
            db.cache_content("BV3", title="AI教程", up_name="u", source="s", topic_key="科技")

            purged = db.purge_pool_by_disliked_topics(["鬼畜", "恐怖"])
            assert purged == 2

            rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
            assert rows["BV1"]["pool_status"] == "purged_by_dislike"
            assert rows["BV2"]["pool_status"] == "purged_by_dislike"
            assert rows["BV3"]["pool_status"] == "fresh"
            db.close()

    def test_purge_pool_by_disliked_topics_matches_topic_group(self) -> None:
        """topic_group column should be matched too (added by migration)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1",
                title="某内容",
                up_name="u",
                source="s",
                topic_key="子话题",
                topic_group="大分类",
            )
            purged = db.purge_pool_by_disliked_topics(["大分类"])
            assert purged == 1
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

    def test_list_chat_turns_returns_recent_turns_in_display_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for idx in range(5):
                db.create_chat_turn(
                    turn_id=f"turn-{idx}",
                    session="popup",
                    scope="chat",
                    message=f"message-{idx}",
                )
                db.conn.execute(
                    """
                    UPDATE chat_turns
                    SET created_at = datetime('now', ?)
                    WHERE turn_id = ?
                    """,
                    (f"+{idx} minutes", f"turn-{idx}"),
                )
                db.conn.commit()

            turns = db.list_chat_turns(session="popup", scope="chat", limit=3)

            assert [turn["turn_id"] for turn in turns] == [
                "turn-2",
                "turn-3",
                "turn-4",
            ]

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

            _seed_visible(
                db,
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
                relevance_reason="你会想点开这种把事情讲透的内容。",
            )
            _seed_visible(
                db,
                "BV1SHOWN",
                title="已经展示",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
                relevance_reason="这条已经展示过。",
            )
            _seed_visible(
                db,
                "BV1FB",
                title="已经反馈",
                up_name="UPC",
                source="search",
                relevance_score=0.93,
                relevance_reason="这条已经被反馈过。",
            )
            _seed_visible(
                db,
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

            _seed_visible(
                db,
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

            _seed_visible(
                db,
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

    def test_get_pool_candidates_skips_rows_without_precomputed_copy(self) -> None:
        """v0.3.57+: pool gate — rows without pool_expression / pool_topic_label
        must not be returned by get_pool_candidates, even if pool_status='fresh'.

        This eliminates the race window between discovery (which writes
        pool_status='fresh' with empty copy) and precompute_pool_copy (which
        fills the LLM-generated expression/topic_label 60-90s later). Without
        this gate, serve() would pick the empty row and fall back to the
        _fallback_expression template ("这条切口挺顺的，先丢给你看看…").
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Three rows: empty copy / only expression / fully filled.
            db.cache_content(
                "BVNOCOPY",
                title="未 precompute",
                source="search",
                relevance_score=0.9,
            )
            db.cache_content(
                "BVHALF",
                title="半 precompute",
                source="search",
                relevance_score=0.85,
                pool_expression="LLM 文案",
                # pool_topic_label intentionally empty
            )
            db.cache_content(
                "BVDONE",
                title="已 precompute",
                source="search",
                relevance_score=0.8,
                pool_expression="LLM 文案",
                pool_topic_label="LLM topic",
            )

            rows = db.get_pool_candidates(limit=10)
            assert [r["bvid"] for r in rows] == ["BVDONE"]

            db.close()

    def test_count_pool_candidates_respects_precompute_gate(self) -> None:
        """v0.3.57+: count_pool_candidates must align with get_pool_candidates,
        otherwise popup '还有 N 条' would be misleading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("a", title="a", source="search", relevance_score=0.5)
            db.cache_content("b", title="b", source="search", relevance_score=0.5)
            db.update_pool_copy("b", expression="x", topic_label="y")

            assert db.count_pool_candidates() == 1

            db.close()

    def test_update_pool_copy_makes_row_visible_in_pool(self) -> None:
        """v0.3.57+: round-trip — empty-copy row stays hidden until
        update_pool_copy fills both fields, then becomes visible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BVPENDING", title="待生成", source="search", relevance_score=0.7)
            assert db.count_pool_candidates() == 0
            assert db.get_pool_candidates(limit=10) == []

            db.update_pool_copy("BVPENDING", expression="生成好了", topic_label="主题")

            assert db.count_pool_candidates() == 1
            rows = db.get_pool_candidates(limit=10)
            assert [r["bvid"] for r in rows] == ["BVPENDING"]

            db.close()

    def test_get_pool_candidates_skips_recently_viewed_bvids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
            )
            _seed_visible(
                db,
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

    def test_recent_viewed_content_keys_extract_multi_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_event(
                "view",
                title="小红书笔记",
                url="https://www.xiaohongshu.com/explore/note-seen",
                metadata={"source_platform": "xiaohongshu", "note_id": "note-seen"},
            )
            db.insert_event(
                "view",
                title="抖音视频",
                url="https://www.douyin.com/video/7123456789012345678",
                metadata={
                    "source_platform": "douyin",
                    "aweme_id": "7123456789012345678",
                },
            )
            db.insert_event(
                "view",
                title="YouTube 视频",
                url="https://www.youtube.com/watch?v=abc1234defg",
                metadata={"source_platform": "youtube", "video_id": "abc1234defg"},
            )
            db.insert_event(
                "view",
                title="B 站视频",
                url="https://www.bilibili.com/video/BV1SEEN",
                metadata={"source_platform": "bilibili", "bvid": "BV1SEEN"},
            )

            keys = db.get_recent_viewed_content_keys()

            assert "xiaohongshu:note-seen" in keys
            assert "douyin:7123456789012345678" in keys
            assert "youtube:abc1234defg" in keys
            assert "bilibili:BV1SEEN" in keys
            assert "BV1SEEN" in keys

            db.close()

    def test_get_pool_candidates_skips_recently_viewed_non_bilibili_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "note-seen",
                title="已经看过的小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="note-seen",
                content_url="https://www.xiaohongshu.com/explore/note-seen?xsec_token=token",
                relevance_score=0.96,
            )
            _seed_visible(
                db,
                "note-fresh",
                title="新小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="note-fresh",
                content_url="https://www.xiaohongshu.com/explore/note-fresh?xsec_token=token",
                relevance_score=0.91,
            )
            db.insert_event(
                "view",
                title="已经看过的小红书",
                url="https://www.xiaohongshu.com/explore/note-seen?xsec_token=token",
                metadata={"source_platform": "xiaohongshu", "note_id": "note-seen"},
            )

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["note-fresh"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_balances_topics_in_candidate_window(self) -> None:
        """Candidate window is balanced by topic_group, not source.

        Without rebalancing, a single dominant topic at the relevance head
        would crowd out the rest. The pool sampler bucket-sorts by
        ``topic_group`` (with ``topic_key`` fallback) and round-robins so
        that no single topic monopolises the candidate window.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(5):
                _seed_visible(
                    db,
                    f"BVAI{index}",
                    title=f"AI 候选 {index}",
                    up_name="科技频道",
                    source="search",
                    topic_group="人工智能",
                    relevance_score=0.99 - index * 0.01,
                )
            _seed_visible(
                db,
                "BVGAME",
                title="游戏候选",
                up_name="游戏频道",
                source="trending",
                topic_group="自走棋",
                relevance_score=0.8,
            )
            _seed_visible(
                db,
                "BVDOC",
                title="纪录片候选",
                up_name="纪录片频道",
                source="explore",
                topic_group="纪录片",
                relevance_score=0.79,
            )
            _seed_visible(
                db,
                "BVHIST",
                title="历史候选",
                up_name="历史频道",
                source="related_chain",
                topic_group="人文历史",
                relevance_score=0.78,
            )

            items = db.get_pool_candidates(limit=6)
            topics = [item.get("topic_group", "") for item in items]

            # Top 4 slots cover all four distinct topic groups
            assert set(topics[:4]) == {"人工智能", "自走棋", "纪录片", "人文历史"}
            # AI cluster cannot monopolise — capped at max_per_topic_group=3
            # by the SQL filter, even though it owns 5 of 9 source rows by
            # raw relevance.
            assert topics.count("人工智能") == 3

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

    def test_get_recommendations_joins_multi_source_fields(self) -> None:
        """Regression: get_recommendations must surface content_cache's
        ``content_url``/``source_platform``/``content_id`` so xhs items
        don't get rebuilt as bilibili URLs by the popup fallback.

        Previous SELECT only joined title/up_name/cover_url, so every row
        came back with ``source_platform=""`` (API defaulted to "bilibili")
        and ``content_url=""`` (popup fell back to
        ``https://www.bilibili.com/video/<note_id>``), producing broken
        links that mixed xhs content into the bilibili namespace.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            note_id = "6548fd56000000001e0223b1"
            tokenized_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=ABC="
            db.cache_content(
                bvid=note_id,
                title="咒术回战复盘",
                up_name="某老师",
                cover_url="https://example.com/cover.jpg",
                source="xhs-extension-search",
                content_id=note_id,
                content_url=tokenized_url,
                source_platform="xiaohongshu",
                author_name="某老师",
            )
            db.insert_recommendation(
                note_id,
                confidence=0.9,
                expression="",
                topic="",
                presented=0,
            )

            rows = db.get_recommendations(limit=10)
            assert len(rows) == 1
            row = rows[0]
            assert row["source_platform"] == "xiaohongshu"
            assert row["content_url"] == tokenized_url
            assert row["content_id"] == note_id

            db.close()

    def test_get_recommendations_filters_bare_xhs_rows(self) -> None:
        """Regression: xhs rows without ``xsec_token`` in ``content_url``
        must not be surfaced to the UI — clicking them hits xhs's 300031
        login wall. Bilibili rows and tokenized xhs rows pass through.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bare_id = "69ccda220000000021005a9b"
            tokenized_id = "69d26d2e0000000023006c95"
            bilibili_id = "BV1Xx411c7mD"

            db.cache_content(
                bvid=bare_id,
                title="裸 xhs",
                up_name="a",
                cover_url="",
                source="xhs-extension-task",
                content_id=bare_id,
                content_url=f"https://www.xiaohongshu.com/explore/{bare_id}",
                source_platform="xiaohongshu",
                author_name="a",
            )
            db.cache_content(
                bvid=tokenized_id,
                title="带 token xhs",
                up_name="b",
                cover_url="",
                source="xhs-extension-task",
                content_id=tokenized_id,
                content_url=(f"https://www.xiaohongshu.com/explore/{tokenized_id}?xsec_token=XYZ="),
                source_platform="xiaohongshu",
                author_name="b",
            )
            db.cache_content(
                bvid=bilibili_id,
                title="b 站视频",
                up_name="c",
                cover_url="",
                source="bilibili-search",
                content_id=bilibili_id,
                content_url=f"https://www.bilibili.com/video/{bilibili_id}",
                source_platform="bilibili",
                author_name="c",
            )
            for bv in (bare_id, tokenized_id, bilibili_id):
                db.insert_recommendation(
                    bv,
                    confidence=0.5,
                    expression="",
                    topic="",
                    presented=0,
                )

            rows = db.get_recommendations(limit=10)
            bvids = {r["bvid"] for r in rows}
            assert bare_id not in bvids
            assert tokenized_id in bvids
            assert bilibili_id in bvids

            db.close()

    def test_get_pool_candidates_filters_bare_xhs_rows(self) -> None:
        """Regression: ranking pool must exclude bare xhs rows too, so the
        engine never promotes them into recommendations in the first place.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bare_id = "69ccda220000000021005a9b"
            tokenized_id = "69d26d2e0000000023006c95"

            _seed_visible(
                db,
                bvid=bare_id,
                title="bare",
                up_name="",
                cover_url="",
                source="xhs-extension-task",
                content_id=bare_id,
                content_url=f"https://www.xiaohongshu.com/explore/{bare_id}",
                source_platform="xiaohongshu",
                author_name="",
            )
            _seed_visible(
                db,
                bvid=tokenized_id,
                title="tokenized",
                up_name="",
                cover_url="",
                source="xhs-extension-task",
                content_id=tokenized_id,
                content_url=(f"https://www.xiaohongshu.com/explore/{tokenized_id}?xsec_token=XYZ="),
                source_platform="xiaohongshu",
                author_name="",
            )

            rows = db.get_pool_candidates(limit=10)
            bvids = {r["bvid"] for r in rows}
            assert bare_id not in bvids
            assert tokenized_id in bvids

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


class TestEventSatisfactionPersistence:
    """v0.3.x event-satisfaction signal — schema + migration + filtered query."""

    def test_fresh_database_has_satisfaction_columns(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "fresh.db")
        db.initialize()
        columns = {
            str(row["name"]) for row in db.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        assert "inferred_satisfaction" in columns
        assert "satisfaction_reason" in columns
        db.close()

    def test_pre_migration_database_is_additively_upgraded(self, tmp_path: Path) -> None:
        """A v0.3.71 database (events table without the two new columns)
        must boot cleanly after the migration; existing rows get NULL."""
        path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(str(path))
        legacy.executescript(
            """
            CREATE TABLE events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                context     TEXT,
                metadata    TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO events (event_type, title) VALUES ('click', 'legacy row');
            """
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        db.initialize()
        columns = {
            str(row["name"]) for row in db.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        assert "inferred_satisfaction" in columns
        assert "satisfaction_reason" in columns
        legacy_row = db.conn.execute(
            "SELECT inferred_satisfaction, satisfaction_reason FROM events WHERE title = ?",
            ("legacy row",),
        ).fetchone()
        assert legacy_row["inferred_satisfaction"] is None
        assert legacy_row["satisfaction_reason"] is None
        db.close()

    def test_insert_event_persists_classification(self, tmp_path: Path) -> None:
        """insert_event runs classify_event_satisfaction exactly once and
        stores the result alongside the event fields."""
        db = Database(tmp_path / "classified.db")
        db.initialize()

        db.insert_event("like", title="深度教程", url="https://x")
        db.insert_event(
            "click",
            title="标题党",
            metadata={"watch_seconds": 2, "video_duration_seconds": 600},
        )

        rows = db.conn.execute(
            "SELECT event_type, inferred_satisfaction, satisfaction_reason "
            "FROM events ORDER BY id"
        ).fetchall()
        assert rows[0]["event_type"] == "like"
        assert rows[0]["inferred_satisfaction"] == "positive"
        assert rows[0]["satisfaction_reason"] == "explicit_engagement"
        assert rows[1]["event_type"] == "click"
        assert rows[1]["inferred_satisfaction"] == "negative"
        assert rows[1]["satisfaction_reason"] == "quick_exit"
        db.close()

    def test_query_events_filter_by_satisfaction_modes(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "filtered.db")
        db.initialize()

        db.insert_event("like", title="好内容")  # → positive
        db.insert_event(
            "click",
            title="标题党",
            metadata={"watch_seconds": 2, "video_duration_seconds": 600},
        )  # → negative
        db.insert_event(
            "click",
            title="未知",
            metadata={"video_duration_seconds": 600},
        )  # → unknown / missing_dwell

        # No filter → all rows.
        assert len(db.query_events(limit=10)) == 3

        # Positive only.
        positives = db.query_events(satisfaction_modes=frozenset({"positive"}), limit=10)
        assert len(positives) == 1
        assert positives[0]["title"] == "好内容"

        # Positive + unknown also includes the missing_dwell row.
        mixed = db.query_events(
            satisfaction_modes=frozenset({"positive", "unknown"}), limit=10
        )
        assert {row["title"] for row in mixed} == {"好内容", "未知"}
        db.close()

    def test_query_events_unknown_mode_includes_null_rows(self, tmp_path: Path) -> None:
        """Legacy rows have inferred_satisfaction = NULL. Requesting
        `unknown` must include them so the consumer can opt in to
        unclassified history."""
        path = tmp_path / "legacy-then-modern.db"
        legacy = sqlite3.connect(str(path))
        legacy.executescript(
            """
            CREATE TABLE events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                context     TEXT,
                metadata    TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO events (event_type, title) VALUES ('view', 'legacy NULL row');
            """
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        db.initialize()
        db.insert_event("like", title="新数据")  # post-migration → positive

        unknown_rows = db.query_events(satisfaction_modes=frozenset({"unknown"}), limit=10)
        titles = {row["title"] for row in unknown_rows}
        # The legacy NULL row must show up under `unknown`.
        assert "legacy NULL row" in titles
        # And the modern positive row must NOT.
        assert "新数据" not in titles
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
