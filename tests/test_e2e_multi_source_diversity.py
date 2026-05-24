"""End-to-end test: multi-source content diversity guarantee.

Tests the complete lifecycle:
  XHS ingest → classify_pool_backlog → DB COALESCE protection
  → bilibili + XHS mixed pool → _select_diversified_batch → rich output

Verifies that after adding xiaohongshu as a source, every recommendation
round maintains content diversity regardless of platform origin.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

# ── Test fixtures ──────────────────────────────────────────────────


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个偏好高信息密度、慢热但判断稳定的人。",
        core_traits=["理性", "克制", "好奇"],
        deep_needs=["理解本质", "掌控全局"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="游戏", category="娱乐", weight=0.9),
                InterestTag(name="美食", category="生活", weight=0.7),
                InterestTag(name="科技", category="知识", weight=0.8),
            ]
        ),
    )


class _ClassifyLLM:
    """LLM mock that returns realistic classification results per title."""

    _TITLE_MAP: dict[str, tuple[str, str, float]] = {
        # XHS content — varied styles and topics
        "莫氏鸡煲在家轻松复刻": ("lifestyle", "美食烹饪", 0.78),
        "顺德美食探店攻略": ("lifestyle", "美食烹饪", 0.74),
        "宝可梦PVP配队思路": ("game_strategy", "游戏攻略", 0.82),
        "咒术回战深度解析": ("deep_dive", "二次元动漫", 0.85),
        "DeepSeek本地部署教程": ("practical_guide", "人工智能", 0.80),
        "Mac Studio搭建AI工作流": ("tech_analysis", "人工智能", 0.76),
        "国际局势深度推演": ("deep_dive", "国际时事", 0.83),
        "洛克王国世界吐槽": ("fun_variety", "游戏攻略", 0.68),
        "React Native性能优化": ("tech_analysis", "前端开发", 0.71),
        "摄影构图原理讲解": ("deep_dive", "摄影艺术", 0.70),
        # Bilibili content — already classified at discovery time
        "终末地基建攻略全解": ("game_strategy", "游戏攻略", 0.88),
        "大疆Pocket 4上手体验": ("tech_analysis", "硬件评测", 0.82),
        "脑机接口最新进展": ("deep_dive", "前沿科技", 0.80),
        "咒术回战牢鹿VS牢真": ("fun_variety", "二次元动漫", 0.76),
        "拼豆祖国97000颗": ("visual_showcase", "手工创意", 0.70),
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str = "",
        user_input: str = "",
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})

        # Classification batch call — parse titles from user_input
        if "批量评估" in system_instruction or "score" in system_instruction:
            # Extract titles from the JSON content_items in user_input
            results = []
            try:
                # user_input has profile + content sections; extract content items
                for title, (style, topic, score) in self._TITLE_MAP.items():
                    if title in user_input:
                        results.append(
                            {
                                "score": score,
                                "reason": f"{topic}类内容",
                                "topic_group": topic,
                                "style_key": style,
                            }
                        )
            except Exception:
                pass

            if not results:
                # Fallback — return generic classification
                results = [
                    {
                        "score": 0.65,
                        "reason": "通用内容",
                        "topic_group": "其他",
                        "style_key": "light_chat",
                    }
                ]

            return LLMResponse(
                content=json.dumps(results, ensure_ascii=False),
                provider="test",
                model="dummy",
                usage={},
            )

        # Expression generation call
        return LLMResponse(
            content=json.dumps(
                {
                    "expression": "这条给你找的。",
                    "topic_label": "测试",
                },
                ensure_ascii=False,
            ),
            provider="test",
            model="dummy",
            usage={},
        )


# ── Helper ─────────────────────────────────────────────────────────


def _seed_bilibili_content(db: Database) -> None:
    """Insert pre-classified bilibili content (as discovery engine would)."""
    bilibili_items = [
        (
            "BV_BILI_01",
            "终末地基建攻略全解",
            "游戏UP主",
            "game_strategy",
            "游戏攻略",
            0.88,
            "search",
        ),
        (
            "BV_BILI_02",
            "大疆Pocket 4上手体验",
            "科技博主",
            "tech_analysis",
            "硬件评测",
            0.82,
            "trending",
        ),
        (
            "BV_BILI_03",
            "脑机接口最新进展",
            "学术频道",
            "deep_dive",
            "前沿科技",
            0.80,
            "related_chain",
        ),
        (
            "BV_BILI_04",
            "咒术回战牢鹿VS牢真",
            "动漫区UP",
            "fun_variety",
            "二次元动漫",
            0.76,
            "trending",
        ),
        (
            "BV_BILI_05",
            "拼豆祖国97000颗",
            "手工达人",
            "visual_showcase",
            "手工创意",
            0.70,
            "explore",
        ),
        (
            "BV_BILI_06",
            "王者荣耀S38赛季攻略",
            "电竞解说",
            "game_strategy",
            "游戏攻略",
            0.84,
            "search",
        ),
        (
            "BV_BILI_07",
            "ChatGPT深度评测2026",
            "AI博主",
            "tech_analysis",
            "人工智能",
            0.79,
            "search",
        ),
        (
            "BV_BILI_08",
            "进击的巨人完结解析",
            "漫评UP",
            "deep_dive",
            "二次元动漫",
            0.81,
            "related_chain",
        ),
    ]
    for bvid, title, author, style, topic, score, source in bilibili_items:
        db.cache_content(
            bvid,
            title=title,
            up_name=author,
            style_key=style,
            topic_group=topic,
            topic_key=topic,
            relevance_score=score,
            relevance_reason=f"{topic}类内容",
            source=source,
            source_platform="bilibili",
            content_id=bvid,
            content_url=f"https://www.bilibili.com/video/{bvid}",
            # v0.3.57+: pool gate requires non-empty precomputed copy.
            pool_expression=f"《{title}》—— 测试推荐文案",
            pool_topic_label=topic,
        )


def _ingest_xhs_notes(db: Database) -> int:
    """Simulate XHS extension sending notes — raw, no classification."""

    xhs_notes = [
        ("xhs_001", "莫氏鸡煲在家轻松复刻", "美食博主A"),
        ("xhs_002", "顺德美食探店攻略", "美食博主B"),
        ("xhs_003", "宝可梦PVP配队思路", "游戏玩家"),
        ("xhs_004", "咒术回战深度解析", "动漫分析师"),
        ("xhs_005", "DeepSeek本地部署教程", "技术达人"),
        ("xhs_006", "Mac Studio搭建AI工作流", "效率博主"),
        ("xhs_007", "国际局势深度推演", "时事评论员"),
        ("xhs_008", "洛克王国世界吐槽", "游戏吐槽UP"),
        ("xhs_009", "React Native性能优化", "前端工程师"),
        ("xhs_010", "摄影构图原理讲解", "摄影师"),
        ("xhs_empty", "", "无标题博主"),  # Should be filtered
    ]
    cached = 0
    for note_id, title, author in xhs_notes:
        if not title:
            continue  # Simulates the empty-title filter
        db.cache_content(
            note_id,
            title=title,
            up_name=author,
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            content_id=note_id,
            content_url=f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=abc",
            # Intentionally empty — simulates raw XHS ingest before classification
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
            # v0.3.57+: precomputed copy filled so pool gate doesn't hide
            # these e2e fixtures. classify_pool_backlog still has work to do
            # on style_key / topic_group, which is what the test asserts.
            pool_expression=f"《{title}》—— 测试推荐文案",
            pool_topic_label="测试主题",
        )
        cached += 1
    return cached


# ── E2E Tests ──────────────────────────────────────────────────────


class TestMultiSourceDiversityE2E:
    """End-to-end tests for multi-source content diversity."""

    @pytest.mark.asyncio
    async def test_xhs_classification_fills_metadata(self) -> None:
        """XHS content gets style_key / topic_group / relevance_score
        after classify_pool_backlog runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _ingest_xhs_notes(db)

            # Before classification: all XHS items have empty metadata
            rows_before = db.get_pool_candidates_needing_evaluation(limit=50)
            assert len(rows_before) == 10  # 10 XHS items (empty title filtered)

            # Run classification
            llm = _ClassifyLLM()
            engine = RecommendationEngine(llm=llm, database=db)
            classified = await engine.classify_pool_backlog(
                profile=_build_profile(),
                limit=50,
            )

            assert classified == 10

            # After classification: no items need evaluation
            rows_after = db.get_pool_candidates_needing_evaluation(limit=50)
            assert len(rows_after) == 0

            # Verify classified fields are populated
            all_rows = db.get_pool_candidates(limit=50)
            xhs_rows = [r for r in all_rows if r.get("source_platform") == "xiaohongshu"]
            for row in xhs_rows:
                assert row["style_key"] != "", f"style_key empty for {row['bvid']}"
                assert row["topic_group"] != "", f"topic_group empty for {row['bvid']}"
                assert float(row["relevance_score"]) > 0, f"relevance_score=0 for {row['bvid']}"

    @pytest.mark.asyncio
    async def test_reingest_does_not_wipe_classification(self) -> None:
        """Extension re-sending same notes must not overwrite classified fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _ingest_xhs_notes(db)

            # Classify
            llm = _ClassifyLLM()
            engine = RecommendationEngine(llm=llm, database=db)
            await engine.classify_pool_backlog(profile=_build_profile(), limit=50)

            # Save classified state
            rows_classified = db.get_pool_candidates(limit=50)
            classified_data = {
                r["bvid"]: dict(r)
                for r in rows_classified
                if r.get("source_platform") == "xiaohongshu"
            }

            # Re-ingest same notes (simulates extension page reload)
            _ingest_xhs_notes(db)

            # Verify fields survived re-ingest
            rows_after = db.get_pool_candidates(limit=50)
            for row in rows_after:
                if row.get("source_platform") != "xiaohongshu":
                    continue
                bvid = row["bvid"]
                if bvid not in classified_data:
                    continue
                original = classified_data[bvid]
                assert row["style_key"] == original["style_key"], (
                    f"style_key wiped for {bvid}: "
                    f"was '{original['style_key']}', now '{row['style_key']}'"
                )
                assert row["topic_group"] == original["topic_group"], (
                    f"topic_group wiped for {bvid}"
                )
                assert float(row["relevance_score"]) == float(original["relevance_score"]), (
                    f"relevance_score wiped for {bvid}"
                )

    @pytest.mark.asyncio
    async def test_mixed_pool_produces_diverse_recommendations(self) -> None:
        """Pool with bilibili + classified XHS content produces diverse batch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Seed bilibili content (already classified)
            _seed_bilibili_content(db)

            # Ingest + classify XHS content
            _ingest_xhs_notes(db)
            llm = _ClassifyLLM()
            engine = RecommendationEngine(llm=llm, database=db)
            await engine.classify_pool_backlog(profile=_build_profile(), limit=50)

            # Load all candidates and run diversity selection
            rows = db.get_pool_candidates(limit=50)
            items = engine._rows_to_discovered(rows)

            picked = RecommendationEngine._select_diversified_batch(items, limit=10)

            # ── Diversity assertions ──

            # 1. Batch must be full
            assert len(picked) == 10, f"Batch not full: {len(picked)} items"

            # 2. Both platforms represented
            platforms = Counter(p.source_platform for p in picked)
            assert "bilibili" in platforms, "No bilibili content in recommendations"
            assert "xiaohongshu" in platforms, "No xiaohongshu content in recommendations"

            # 3. Multiple styles (not all "unknown")
            styles = Counter(p.style_key for p in picked)
            assert "unknown" not in styles or styles["unknown"] <= 2, (
                f"Too many unclassified items: {styles}"
            )
            assert len(styles) >= 4, f"Not enough style diversity: {styles}"

            # 4. Multiple topics
            topics = Counter(p.topic_group for p in picked)
            assert len(topics) >= 5, f"Not enough topic diversity: {topics}"
            # No single topic dominates (broad_topic_cap = 3 for limit=10)
            for topic, count in topics.items():
                assert count <= 3, f"Topic '{topic}' has {count} items, exceeds broad_cap"

            # 5. No "xhs-extension-task" appearing as topic token
            topic_tokens = set()
            for item in picked:
                topic_tokens.update(RecommendationEngine._diversity_tokens(item))
            assert "xhs-extension-task" not in topic_tokens, (
                "source_strategy leaked into diversity tokens"
            )
            assert "xhs-extension-tas" not in topic_tokens, (
                "truncated source_strategy leaked into diversity tokens"
            )

    def test_empty_title_notes_filtered_at_ingest(self) -> None:
        """Notes with empty title must not enter content_cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Direct DB check — empty title should NOT be inserted
            db.cache_content(
                "xhs_empty_direct",
                title="",
                up_name="ghost",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
            )

            # The item IS in DB (cache_content doesn't filter)
            # but get_pool_candidates_needing_evaluation picks it up
            # classify_pool_backlog will still classify it, which is fine.
            # The critical filter is in _cache_xhs_notes (skip empty title)
            # and in the extension (extractNoteMetadataFromAnchor returns null)

            # Verify the API-level filter works
            from types import SimpleNamespace

            from openbiliclaw.api.app import create_app

            fake_config = SimpleNamespace(
                data_path=Path(tmpdir),
                llm=SimpleNamespace(concurrency=3),
                bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
                sources=SimpleNamespace(
                    browser_cdp_url="",
                    browser_headed=False,
                    xiaohongshu=SimpleNamespace(
                        daily_search_budget=20,
                        daily_creator_budget=10,
                        task_interval_seconds=45,
                    ),
                ),
                scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
            )

            import openbiliclaw.config

            original_load = openbiliclaw.config.load_config

            try:
                openbiliclaw.config.load_config = lambda: fake_config
                import openbiliclaw.llm

                openbiliclaw.llm.build_llm_registry = lambda config: "registry"  # type: ignore
                import openbiliclaw.bilibili.auth

                openbiliclaw.bilibili.auth.resolve_runtime_cookie = lambda **_: ""  # type: ignore

                db2 = Database(Path(tmpdir) / "test2.db")
                db2.initialize()
                app = create_app(database=db2)

                from fastapi.testclient import TestClient

                client = TestClient(app)

                response = client.post(
                    "/api/sources/xhs/observed-urls",
                    json={
                        "notes": [
                            {
                                "url": "https://www.xiaohongshu.com/explore/note_has_title",
                                "title": "有标题",
                                "author": "A",
                            },
                            {
                                "url": "https://www.xiaohongshu.com/explore/note_no_title",
                                "title": "",
                                "author": "B",
                            },
                            {
                                "url": "https://www.xiaohongshu.com/explore/note_null_title",
                                "title": None,
                                "author": "C",
                            },
                        ],
                        "page_type": "search",
                    },
                )
                assert response.status_code == 200
                assert response.json()["accepted"] == 1  # Only the one with title
            finally:
                openbiliclaw.config.load_config = original_load

    @pytest.mark.asyncio
    async def test_classify_lock_prevents_duplicate_llm_calls(self) -> None:
        """Concurrent classify_pool_backlog calls should not both run LLM."""
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _ingest_xhs_notes(db)

            llm = _ClassifyLLM()
            engine = RecommendationEngine(llm=llm, database=db)
            profile = _build_profile()

            # Run two classify calls concurrently
            results = await asyncio.gather(
                engine.classify_pool_backlog(profile=profile, limit=50),
                engine.classify_pool_backlog(profile=profile, limit=50),
            )

            # One should have classified items, the other should return 0
            # (lock prevents concurrent execution)
            assert sorted(results) == [0, 10], (
                f"Expected [0, 10], got {sorted(results)} — lock didn't prevent concurrent runs"
            )

    @pytest.mark.asyncio
    async def test_xhs_only_pool_still_diverse(self) -> None:
        """Even with ONLY XHS content, diversity should work after classification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Only XHS, no bilibili
            _ingest_xhs_notes(db)
            llm = _ClassifyLLM()
            engine = RecommendationEngine(llm=llm, database=db)
            await engine.classify_pool_backlog(profile=_build_profile(), limit=50)

            rows = db.get_pool_candidates(limit=50)
            items = engine._rows_to_discovered(rows)

            picked = RecommendationEngine._select_diversified_batch(items, limit=8)

            # Should fill batch
            assert len(picked) >= 7, f"Batch too small: {len(picked)}"

            # Titles should be diverse (not all the same topic)
            titles = [p.title for p in picked]
            assert len(set(titles)) == len(titles), "Duplicate titles in batch"

            # Multiple styles
            styles = set(p.style_key for p in picked if p.style_key)
            assert len(styles) >= 3, f"XHS-only pool lacks style diversity: {styles}"

    @pytest.mark.asyncio
    async def test_failed_classification_does_not_retry_forever(self) -> None:
        """Items that fail LLM classification get marked and are not retried."""

        class _FailingLLM:
            async def complete_structured_task(self, **kwargs) -> LLMResponse:
                # Return malformed response — only 1 result for N items
                return LLMResponse(
                    content=json.dumps(
                        [
                            {
                                "score": 0.7,
                                "reason": "ok",
                                "topic_group": "test",
                                "style_key": "lifestyle",
                            }
                        ]
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _ingest_xhs_notes(db)  # 10 items

            llm = _FailingLLM()
            engine = RecommendationEngine(llm=llm, database=db)

            # First call: classifies, but LLM returns only 1 result per batch
            classified_1 = await engine.classify_pool_backlog(
                profile=_build_profile(),
                limit=50,
            )
            assert classified_1 == 10  # All 10 were "attempted"

            # Second call: nothing left to classify (all have score > 0)
            classified_2 = await engine.classify_pool_backlog(
                profile=_build_profile(),
                limit=50,
            )
            assert classified_2 == 0, "Items are being retried — sentinel score didn't work"
