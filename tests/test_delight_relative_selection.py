"""Production delight selection: bounded Top-K of the remaining eligible pool."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database


def _seed(database: Database, key: str, score: float = 0.65, *, title: str = "候选") -> None:
    database.cache_content(
        key, title=title, relevance_score=score, style_key="deep_focus", topic_group=key
    )
    database.update_pool_copy(key, expression="正式推荐文案", topic_label=key)
    database.update_delight_score(
        key, delight_score=score, delight_reason="正式推荐文案", delight_hook=key
    )


def _queue(database: Database) -> list[dict[str, Any]]:
    return database.get_ranked_delight_candidates(
        limit=1000,
        include_liked=True,
        include_delivered=True,
    )


@pytest.mark.parametrize("size", [0, 1, 2, 9, 10, 11, 149, 150, 160])
def test_relative_selection_handles_cold_start_and_below_legacy_floor(
    tmp_path: Path, size: int
) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for index in range(size):
            _seed(database, f"BV{index:04d}", 0.65 + index * 0.0001)
        assert len(_queue(database)) == math.ceil(size * 0.1)
    finally:
        database.close()


def test_same_score_reserves_exact_quota_and_keeps_regular_pool_available(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for index in range(160):
            _seed(database, f"BV{index:04d}", 0.82)
        first = [row["bvid"] for row in _queue(database)]
        assert len(first) == 16
        assert [row["bvid"] for row in _queue(database)] == first
        ordinary = database.get_pool_candidates(limit=200, max_per_topic_group=0)
        assert len(ordinary) == 144
        assert not set(first).intersection(row["bvid"] for row in ordinary)
    finally:
        database.close()


@pytest.mark.parametrize("consume", ["mark_delight_viewed", "mark_delight_seen"])
def test_consumption_reselects_until_exhaustion_and_new_supply_recovers(
    tmp_path: Path, consume: str
) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for index in range(12):
            _seed(database, f"BV{index:04d}", 0.65 + index * 0.001)
        consumed: set[str] = set()
        for _ in range(12):
            queue = _queue(database)
            assert queue
            key = queue[0]["bvid"]
            assert key not in consumed
            consumed.add(key)
            getattr(database, consume)(key)
        assert _queue(database) == []
        _seed(database, "BV-new", 0.62)
        assert [row["bvid"] for row in _queue(database)] == ["BV-new"]
    finally:
        database.close()


def test_read_high_scores_do_not_strand_unread_candidates(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for index in range(150):
            key = f"BVread{index:04d}"
            _seed(database, key, 0.9)
            database.mark_delight_viewed(key)
        _seed(database, "BV-unread", 0.8)
        assert [row["bvid"] for row in _queue(database)] == ["BV-unread"]
    finally:
        database.close()


def test_api_filters_before_quota_and_reloads_effective_dislikes(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    dislikes = ["避雷主题"]
    try:
        for index in range(20):
            _seed(database, f"BVblocked{index:04d}", 0.95, title="避雷主题的视频")
        _seed(database, "BV-allowed", 0.65, title="喜欢的内容")
        app = create_app(
            memory_manager=object(),
            database=database,
            soul_engine=SimpleNamespace(get_effective_disliked_topics=lambda: dislikes),
        )
        client = TestClient(app)
        response = client.get("/api/delight/pending-batch?limit=20")
        assert response.status_code == 200
        assert [row["bvid"] for row in response.json()["items"]] == ["BV-allowed"]
        dislikes.append("喜欢的内容")
        assert client.get("/api/delight/pending-batch").json()["items"] == []
    finally:
        database.close()


def test_ineligible_supply_does_not_set_the_quota_or_reappear(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for key in ("suppressed", "stale", "feedbacked", "purged_by_dislike"):
            _seed(database, key, 0.99)
            database._execute_write(
                "UPDATE content_cache SET pool_status=? WHERE bvid=?", (key, key)
            )
        _seed(database, "low-score", 0.59)
        _seed(database, "uncopied", 0.99)
        database._execute_write("UPDATE content_cache SET pool_expression='' WHERE bvid='uncopied'")
        _seed(database, "ordinary-history", 0.99)
        database.insert_recommendation(bvid="ordinary-history", confidence=0.99, expression="文案")
        _seed(database, "valid", 0.62)
        assert [row["bvid"] for row in _queue(database)] == ["valid"]
        database.mark_delight_seen("valid")
        assert _queue(database) == []
    finally:
        database.close()


def test_delivery_ack_preserves_unread_state_while_new_supply_can_rank_above_it(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "original", 0.65)
        database.mark_delight_notified("original")
        assert [row["bvid"] for row in _queue(database)] == ["original"]
        assert database.get_ranked_delight_candidates() == []
        _seed(database, "new-high", 0.99)
        assert [row["bvid"] for row in _queue(database)] == ["new-high"]
        database.mark_delight_viewed("new-high")
        assert [row["bvid"] for row in _queue(database)] == ["original"]
    finally:
        database.close()


async def test_engine_prepares_promotions_without_another_model_call(tmp_path: Path) -> None:
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile

    database = Database(tmp_path / "test.db")
    database.initialize()
    llm = SimpleNamespace(complete_structured_task=AsyncMock(side_effect=AssertionError("no LLM")))
    try:
        for index in range(12):
            key = f"BV{index:04d}"
            database.cache_content(
                key,
                title="候选",
                relevance_score=0.61 + index * 0.001,
                style_key="deep_focus",
                topic_group=key,
            )
            database.update_pool_copy(key, expression="正式推荐文案", topic_label=key)
        engine = RecommendationEngine(llm=llm, database=database)
        assert (
            await engine.precompute_delight_scores(
                profile=SoulProfile(preferences=PreferenceLayer())
            )
            == 12
        )
        for _ in range(12):
            queue = _queue(database)
            assert queue
            database.mark_delight_viewed(queue[0]["bvid"])
        assert _queue(database) == []
        llm.complete_structured_task.assert_not_called()
    finally:
        database.close()


def test_api_consumption_refreshes_queue_and_ack_is_not_consumption(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    publish = AsyncMock()
    try:
        _seed(database, "best", 0.7)
        _seed(database, "next", 0.65)
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        app.state.runtime_context.event_hub = SimpleNamespace(publish=publish)
        client = TestClient(app)
        assert client.post("/api/delight/sent", json={"bvid": "best"}).status_code == 200
        assert client.get("/api/delight/pending-batch").json()["items"][0]["bvid"] == "best"
        response = client.post("/api/delight/respond", json={"bvid": "best", "response": "view"})
        assert response.status_code == 200
        assert client.get("/api/delight/pending-batch").json()["items"][0]["bvid"] == "next"
        assert any(call.args[0]["type"] == "delight.refreshed" for call in publish.call_args_list)
    finally:
        database.close()


async def test_init_accepts_one_displayable_delight_even_if_ordinary_pool_is_empty(
    tmp_path: Path,
) -> None:
    from openbiliclaw.runtime.refresh import ContinuousRefreshController
    from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile

    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "only-one", 0.65)
        assert database.count_pool_candidates() == 0
        controller = ContinuousRefreshController(
            memory_manager=SimpleNamespace(),
            database=database,
            soul_engine=SimpleNamespace(),
            discovery_engine=SimpleNamespace(),
            recommendation_engine=SimpleNamespace(),
        )
        # Empty source selection keeps this bounded to already-ready supply;
        # the real init completion gate must recognize the delight channel.
        await controller.run_init_backfill(
            SoulProfile(preferences=PreferenceLayer()),
            target_pool_count=1,
            sources=set(),
        )
        assert _queue(database)[0]["bvid"] == "only-one"
    finally:
        database.close()


async def test_engine_preserves_relaxed_explore_admission_at_cold_start(tmp_path: Path) -> None:
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile

    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        database.cache_content(
            "explore",
            title="探索候选",
            source="explore",
            relevance_score=0.58,
            style_key="deep_focus",
            topic_group="探索",
        )
        database.update_pool_copy("explore", expression="正式文案", topic_label="探索")
        engine = RecommendationEngine(llm=object(), database=database)
        assert (
            await engine.precompute_delight_scores(
                profile=SoulProfile(preferences=PreferenceLayer())
            )
            == 1
        )
        assert _queue(database)[0]["bvid"] == "explore"
    finally:
        database.close()


def test_positive_feedback_keeps_the_unread_card_until_explicit_consumption(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "liked", 0.65)
        database.insert_recommendation("liked", confidence=0.65, expression="已有展示记录")
        database._execute_write("UPDATE content_cache SET feedback_type='like' WHERE bvid='liked'")
        _seed(database, "new-high", 0.99)
        assert [row["bvid"] for row in _queue(database)] == ["liked"]
        database.mark_delight_seen("liked")
        assert [row["bvid"] for row in _queue(database)] == ["new-high"]
    finally:
        database.close()


async def test_legacy_history_backlog_cannot_starve_new_copy_ready_supply(tmp_path: Path) -> None:
    from openbiliclaw.recommendation.engine import RecommendationEngine
    from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile

    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        for key, score in (("old-history", 0.99), ("fresh", 0.65)):
            database.cache_content(
                key, title=key, relevance_score=score, style_key="deep_focus", topic_group=key
            )
            database.update_pool_copy(key, expression="正式文案", topic_label=key)
            database.update_delight_score(
                key, delight_score=score, delight_reason="", delight_hook=""
            )
        database.insert_recommendation("old-history", confidence=0.99, expression="已推荐")
        engine = RecommendationEngine(llm=object(), database=database)
        assert (
            await engine.precompute_delight_scores(
                profile=SoulProfile(preferences=PreferenceLayer()), limit=1
            )
            == 1
        )
        assert [row["bvid"] for row in _queue(database)] == ["fresh"]
    finally:
        database.close()


def test_bare_category_does_not_erase_a_more_specific_runtime_candidate(tmp_path: Path) -> None:
    from openbiliclaw.runtime.refresh import ContinuousRefreshController

    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "astronomy", 0.65, title="天文科普")
        controller = ContinuousRefreshController(
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            database=database,
            soul_engine=SimpleNamespace(get_effective_disliked_topics=lambda: ["科普"]),
            discovery_engine=object(),
            recommendation_engine=object(),
        )
        candidate = controller.get_pending_delight()
        assert candidate is not None
        assert candidate["bvid"] == "astronomy"
    finally:
        database.close()


@pytest.mark.parametrize("invalid_score", ["invalid", float("inf"), -1, 2])
def test_invalid_legacy_scores_cannot_set_the_quota(tmp_path: Path, invalid_score: object) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "invalid", 0.99)
        database._execute_write(
            "UPDATE content_cache SET delight_score=? WHERE bvid='invalid'", (invalid_score,)
        )
        _seed(database, "valid", 0.61)
        assert [row["bvid"] for row in _queue(database)] == ["valid"]
        assert database.get_ranked_delight_candidates(limit=0) == []
    finally:
        database.close()


def test_expired_candidate_is_excluded_before_relative_ranking(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        database.cache_content(
            "expired",
            title="赛事已经结束",
            relevance_score=0.99,
            temporal_class="current",
            temporal_confidence=0.95,
            temporal_reason="赛事终态会使核心信息失效",
            temporal_policy_version="v2",
            temporal_validity_mode="event_state",
            temporal_valid_until="",
            temporal_scope="core",
            temporal_state="expired",
            temporal_evidence="赛事已经结束",
            temporal_evaluated_at="2020-01-01T00:00:00Z",
            temporal_next_review_at="",
            temporal_evidence_complete=True,
        )
        database.update_pool_copy("expired", expression="正式文案", topic_label="赛事")
        database.update_delight_score(
            "expired", delight_score=0.99, delight_reason="正式文案", delight_hook="赛事"
        )
        _seed(database, "valid", 0.61)
        assert [row["bvid"] for row in _queue(database)] == ["valid"]
    finally:
        database.close()


def test_failed_view_does_not_consume_or_publish_a_refill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    publish = AsyncMock()
    try:
        _seed(database, "best", 0.7)
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        app.state.runtime_context.event_hub = SimpleNamespace(publish=publish)

        def fail(_key: str) -> None:
            raise RuntimeError("simulated SQLite failure")

        monkeypatch.setattr(database, "mark_delight_viewed", fail)
        response = TestClient(app).post(
            "/api/delight/respond", json={"bvid": "best", "response": "view"}
        )
        assert response.status_code == 500
        assert _queue(database)[0]["bvid"] == "best"
        publish.assert_not_called()
    finally:
        database.close()


def test_view_consumption_releases_raw_headroom_for_replenishment(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "best", 0.7)
        _seed(database, "next", 0.65)
        assert database.count_pool_raw_material_candidates() == 2
        database.mark_delight_viewed("best")
        assert database.count_pool_raw_material_candidates() == 1
        database.mark_delight_viewed("next")
        assert database.count_pool_raw_material_candidates() == 0
        assert _queue(database) == []
        _seed(database, "refill", 0.62)
        assert database.count_pool_raw_material_candidates() == 1
        assert _queue(database)[0]["bvid"] == "refill"
    finally:
        database.close()


def test_legacy_viewed_fresh_rows_do_not_consume_raw_headroom(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()
    try:
        _seed(database, "legacy-read", 0.7)
        database._execute_write(
            "UPDATE content_cache SET delight_seen=1, delight_notified=1 WHERE bvid='legacy-read'"
        )
        assert database.count_pool_raw_material_candidates() == 0
        assert database.count_pool_raw_material_by_source() == {}
        assert _queue(database) == []
    finally:
        database.close()
