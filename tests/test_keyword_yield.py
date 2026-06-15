"""Tests for P1.8 keyword yield tracking (Discover backpressure refactor).

Covers the end-to-end ``source_keyword_id`` provenance + admit-time yield
backfill + zero-yield retirement:

* the ``discovery_keyword_yield`` ledger + ``increment_keyword_yield`` DAO
  (idempotent on ``(keyword, content)``);
* ``retire_zero_yield_keywords`` (retire barren ``used`` words; keep yielded /
  fresh ones);
* the ``DiscoveredContent`` / ``discovery_candidates`` / ``content_cache``
  round-trip of the id (additive, NULL on legacy / flag-off);
* the single ``_cache_results`` admission convergence crediting yield, and
  rejected-at-admission candidates accruing none;
* the candidate-pipeline path (X / YT / XHS) crediting yield through
  ``drain_pending`` → ``cache_evaluated_results`` → ``_cache_results``;
* the 5 search strategies stamping each produced item from a ``keyword_ids``
  map (and leaving items unstamped when not injected — flag-off parity);
* the planner ``retire_zero_yield`` housekeeping pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import (
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.storage.database import Database

from .test_discovery_candidate_pipeline import _ScoringLLM
from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from pathlib import Path

_BILI = "bilibili"


# ── fixtures / helpers ───────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


def _seed_used_keyword(db: Database, platform: str, keyword: str) -> int:
    """Insert one pending keyword, claim it, mark it used, return its id."""
    db.insert_pending_keywords(platform, [keyword], "dig")
    row = db.conn.execute(
        "SELECT id FROM discovery_keywords WHERE platform=? AND keyword=?",
        (platform, keyword),
    ).fetchone()
    keyword_id = int(row["id"])
    db.claim_keywords(platform, 1)
    db.mark_keyword_used(keyword_id)
    return keyword_id


def _age_used_at(db: Database, keyword_id: int, *, days_ago: int) -> None:
    db.conn.execute(
        "UPDATE discovery_keywords SET used_at = datetime('now', ?) WHERE id = ?",
        (f"-{days_ago} day", keyword_id),
    )
    db.conn.commit()


# ── DAO: increment idempotency + counting ────────────────────────────


class TestIncrementKeywordYield:
    def test_single_increment_sets_yield_to_one(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        assert db.increment_keyword_yield(kid, "BVc1") is True
        assert db.keyword_yield_count(kid) == 1

    def test_same_keyword_same_content_is_idempotent(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        assert db.increment_keyword_yield(kid, "BVc1") is True
        # Second admit of the SAME (keyword, content) → no double count.
        assert db.increment_keyword_yield(kid, "BVc1") is False
        assert db.keyword_yield_count(kid) == 1

    def test_two_distinct_contents_count_two(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        assert db.increment_keyword_yield(kid, "BVc1") is True
        assert db.increment_keyword_yield(kid, "BVc2") is True
        assert db.keyword_yield_count(kid) == 2

    def test_blank_or_invalid_inputs_are_noops(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        assert db.increment_keyword_yield(kid, "") is False
        assert db.increment_keyword_yield(0, "BVc1") is False
        assert db.keyword_yield_count(kid) == 0


# ── DAO: retire zero-yield ───────────────────────────────────────────


class TestRetireZeroYield:
    def test_retires_old_zero_yield_used_word(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "barren")
        _age_used_at(db, kid, days_ago=1)
        retired = db.retire_zero_yield_keywords(_BILI, min_age_minutes=60)
        assert retired == 1
        status = db.conn.execute(
            "SELECT status FROM discovery_keywords WHERE id=?", (kid,)
        ).fetchone()["status"]
        assert status == "expired"

    def test_keeps_yielded_word_even_when_old(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "productive")
        db.increment_keyword_yield(kid, "BVc1")
        _age_used_at(db, kid, days_ago=1)
        assert db.retire_zero_yield_keywords(_BILI, min_age_minutes=60) == 0
        status = db.conn.execute(
            "SELECT status FROM discovery_keywords WHERE id=?", (kid,)
        ).fetchone()["status"]
        assert status == "used"

    def test_keeps_fresh_zero_yield_word(self, db: Database) -> None:
        # Just-used word (no age) must not be retired — its async admit may be
        # pending. The age floor protects it.
        kid = _seed_used_keyword(db, _BILI, "fresh")
        assert db.retire_zero_yield_keywords(_BILI, min_age_minutes=60) == 0
        status = db.conn.execute(
            "SELECT status FROM discovery_keywords WHERE id=?", (kid,)
        ).fetchone()["status"]
        assert status == "used"

    def test_only_touches_target_platform(self, db: Database) -> None:
        kid_bili = _seed_used_keyword(db, _BILI, "x")
        kid_yt = _seed_used_keyword(db, "youtube", "y")
        _age_used_at(db, kid_bili, days_ago=1)
        _age_used_at(db, kid_yt, days_ago=1)
        assert db.retire_zero_yield_keywords(_BILI, min_age_minutes=60) == 1
        yt_status = db.conn.execute(
            "SELECT status FROM discovery_keywords WHERE id=?", (kid_yt,)
        ).fetchone()["status"]
        assert yt_status == "used"


# ── round-trip: DiscoveredContent ↔ candidate row ↔ content_cache ────


class TestSourceKeywordIdRoundTrip:
    def test_candidate_write_carries_id(self) -> None:
        item = DiscoveredContent(
            content_id="BV1",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
            source_keyword_id=42,
        )
        write = discovered_content_to_candidate_write(item)
        assert write.source_keyword_id == 42

    def test_candidate_enqueue_and_row_back_preserves_id(self, db: Database) -> None:
        item = DiscoveredContent(
            content_id="BV1",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
            source_keyword_id=7,
        )
        db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
        row = dict(
            db.conn.execute(
                "SELECT * FROM discovery_candidates WHERE candidate_key=?",
                ("bilibili:BV1",),
            ).fetchone()
        )
        assert row["source_keyword_id"] == 7
        restored = row_to_discovered_content(row)
        assert restored.source_keyword_id == 7

    def test_legacy_candidate_has_null_id(self, db: Database) -> None:
        item = DiscoveredContent(
            content_id="BV2",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
        )
        db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
        row = db.conn.execute(
            "SELECT source_keyword_id FROM discovery_candidates WHERE candidate_key=?",
            ("bilibili:BV2",),
        ).fetchone()
        assert row["source_keyword_id"] is None

    def test_cache_content_writes_and_preserves_id(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        db.cache_content(
            "BVc", title="t", content_id="BVc", source_platform="bilibili", source_keyword_id=kid
        )
        got = db.conn.execute(
            "SELECT source_keyword_id FROM content_cache WHERE bvid='BVc'"
        ).fetchone()["source_keyword_id"]
        assert got == kid
        # Re-ingest without the id must not wipe the provenance.
        db.cache_content("BVc", title="t2", content_id="BVc", source_platform="bilibili")
        got2 = db.conn.execute(
            "SELECT source_keyword_id FROM content_cache WHERE bvid='BVc'"
        ).fetchone()["source_keyword_id"]
        assert got2 == kid


# ── engine: _cache_results is the yield convergence ──────────────────


class TestCacheResultsBackfill:
    def test_admitted_item_credits_its_keyword(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        engine = ContentDiscoveryEngine(database=db)
        item = DiscoveredContent(
            content_id="BVa",
            bvid="BVa",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
            relevance_score=0.9,
            source_keyword_id=kid,
        )
        assert engine.cache_evaluated_results([item]) == 1
        assert db.keyword_yield_count(kid) == 1

    def test_re_admit_same_content_stays_one(self, db: Database) -> None:
        kid = _seed_used_keyword(db, _BILI, "kw")
        engine = ContentDiscoveryEngine(database=db)
        item = DiscoveredContent(
            content_id="BVa",
            bvid="BVa",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
            relevance_score=0.9,
            source_keyword_id=kid,
        )
        engine.cache_evaluated_results([item])
        engine.cache_evaluated_results([item])  # idempotent
        assert db.keyword_yield_count(kid) == 1

    def test_item_without_id_records_no_yield(self, db: Database) -> None:
        engine = ContentDiscoveryEngine(database=db)
        item = DiscoveredContent(
            content_id="BVb",
            bvid="BVb",
            title="t",
            source_platform="bilibili",
            source_strategy="search",
            relevance_score=0.9,
        )
        assert engine.cache_evaluated_results([item]) == 1
        rows = db.conn.execute("SELECT COUNT(*) FROM discovery_keyword_yield").fetchone()[0]
        assert rows == 0


# ── pipeline: end-to-end admit credits yield, reject does not ────────


def _scored(content_id: str, score: float) -> dict[str, Any]:
    return {
        "content_id": content_id,
        "score": score,
        "reason": "fit",
        "topic_group": "tech",
        "style_key": "deep_dive",
    }


@pytest.mark.asyncio
async def test_pipeline_admit_backfills_keyword_yield(db: Database) -> None:
    kid = _seed_used_keyword(db, "youtube", "kw")
    item = DiscoveredContent(
        content_id="yt1",
        title="YT",
        content_url="https://www.youtube.com/watch?v=yt1",
        source_platform="youtube",
        source_strategy="yt_search",
        source_keyword_id=kid,
    )
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    engine = ContentDiscoveryEngine(llm_service=_ScoringLLM([_scored("yt1", 0.90)]), database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=engine, pool_target_count=30
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["cached"] == 1
    assert db.keyword_yield_count(kid) == 1


@pytest.mark.asyncio
async def test_pipeline_score_rejected_candidate_records_no_yield(db: Database) -> None:
    kid = _seed_used_keyword(db, "youtube", "kw")
    item = DiscoveredContent(
        content_id="yt2",
        title="YT2",
        content_url="https://www.youtube.com/watch?v=yt2",
        source_platform="youtube",
        source_strategy="yt_search",
        source_keyword_id=kid,
    )
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    # 0.40 is below the yt_search threshold → rejected, never admitted.
    engine = ContentDiscoveryEngine(llm_service=_ScoringLLM([_scored("yt2", 0.40)]), database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=engine, pool_target_count=30
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["cached"] == 0
    assert db.keyword_yield_count(kid) == 0


@pytest.mark.asyncio
async def test_pipeline_two_distinct_admits_one_keyword_yield_two(db: Database) -> None:
    kid = _seed_used_keyword(db, "youtube", "kw")
    writes = [
        discovered_content_to_candidate_write(
            DiscoveredContent(
                content_id=cid,
                title=cid,
                content_url=f"https://www.youtube.com/watch?v={cid}",
                source_platform="youtube",
                source_strategy="yt_search",
                source_keyword_id=kid,
            )
        )
        for cid in ("yta", "ytb")
    ]
    db.enqueue_discovery_candidates(writes)
    engine = ContentDiscoveryEngine(
        llm_service=_ScoringLLM([_scored("yta", 0.90), _scored("ytb", 0.88)]),
        database=db,
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=engine, pool_target_count=30
    )

    result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

    assert result["cached"] == 2
    assert db.keyword_yield_count(kid) == 2


# ── XHS task-result path carries the id onto candidates ──────────────


class TestXhsTaskResultProvenance:
    def test_source_keyword_id_extracted_from_payload(self) -> None:
        from openbiliclaw.runtime.keyword_fetch import source_keyword_id_from_xhs_task

        assert source_keyword_id_from_xhs_task('{"keyword": "k", "source_keyword_id": 99}') == 99
        assert source_keyword_id_from_xhs_task('{"keyword": "k"}') is None
        assert source_keyword_id_from_xhs_task(None) is None
        assert source_keyword_id_from_xhs_task("not json") is None

    @pytest.mark.asyncio
    async def test_xhs_task_admit_credits_keyword_yield(self, db: Database) -> None:
        # Simulate the api/app.py xhs task-result ingest: an xhs *search* note
        # candidate carrying its source_keyword_id, admitted via the pipeline.
        kid = _seed_used_keyword(db, "xiaohongshu", "kw")
        item = DiscoveredContent(
            bvid="xhsnote1",
            content_id="xhsnote1",
            title="note",
            content_url="https://www.xiaohongshu.com/explore/xhsnote1",
            source_platform="xiaohongshu",
            source_strategy="xhs-extension-task",
            source_keyword_id=kid,
        )
        db.enqueue_discovery_candidates(
            [
                discovered_content_to_candidate_write(
                    item,
                    source_context="task",
                    raw_payload={"admission_policy": "observed", "score_threshold": 0.0},
                )
            ]
        )
        # admission_policy=observed → threshold 0 → any score admits.
        engine = ContentDiscoveryEngine(
            llm_service=_ScoringLLM([_scored("xhsnote1", 0.10)]),
            database=db,
        )
        pipeline = DiscoveryCandidatePipeline(
            database=db, discovery_engine=engine, pool_target_count=30
        )

        result = await pipeline.drain_pending(profile=_build_profile(), batch_size=30)

        assert result["cached"] == 1
        assert db.keyword_yield_count(kid) == 1


# ── strategy stamping: keyword_ids → source_keyword_id ───────────────


class _FakeXClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        self.queries.append(query)
        return [
            {
                "id": f"{query}-1",
                "text": f"tweet about {query}",
                "url": f"https://x.com/u/status/{query}-1",
                "user": {"screen_name": "u", "name": "U"},
            }
        ]


@pytest.mark.asyncio
async def test_x_search_stamps_source_keyword_id() -> None:
    from openbiliclaw.discovery.strategies.x import XSearchStrategy

    strategy = XSearchStrategy(client=_FakeXClient())
    results = await strategy.discover(
        _build_profile(),
        limit=10,
        queries=["alpha", "beta"],
        keyword_ids={"alpha": 11, "beta": 22},
    )
    by_kid = {r.content_id: r.source_keyword_id for r in results}
    assert by_kid["alpha-1"] == 11
    assert by_kid["beta-1"] == 22


@pytest.mark.asyncio
async def test_x_search_without_map_leaves_id_none() -> None:
    from openbiliclaw.discovery.strategies.x import XSearchStrategy

    strategy = XSearchStrategy(client=_FakeXClient())
    results = await strategy.discover(_build_profile(), limit=10, queries=["alpha"])
    assert results
    assert all(r.source_keyword_id is None for r in results)


class _FakeYtClient:
    async def search_videos(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "videoId": f"{query}-v",
                "title": f"{query} video",
                "channel": "C",
            }
        ]


@pytest.mark.asyncio
async def test_youtube_search_stamps_source_keyword_id() -> None:
    from openbiliclaw.discovery.strategies.youtube import YoutubeSearchStrategy

    strategy = YoutubeSearchStrategy(
        client=_FakeYtClient(),  # type: ignore[arg-type]
        llm_service=None,  # type: ignore[arg-type]
        llm_evaluation=False,
    )
    results = await strategy.discover(
        _build_profile(),
        limit=10,
        queries=["aa", "bb"],
        keyword_ids={"aa": 1, "bb": 2},
    )
    by_kid = {r.content_id: r.source_keyword_id for r in results}
    assert by_kid["aa-v"] == 1
    assert by_kid["bb-v"] == 2


class _FakeDouyinClient:
    search_source_strategy = "dy-direct-search"

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": f"{keyword}-a", "desc": keyword, "author": {"nickname": "N"}}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        return []

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        return []

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, object]]:
        return []


@pytest.mark.asyncio
async def test_douyin_search_stamps_source_keyword_id() -> None:
    from openbiliclaw.discovery.strategies.douyin_direct import DouyinDirectStrategy

    strategy = DouyinDirectStrategy(
        client=_FakeDouyinClient(),
        llm_service=None,
        sources=("search",),
        seed_keywords=("kw1",),
        seed_keyword_ids={"kw1": 55},
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile(), limit=10)
    assert results
    assert all(r.source_keyword_id == 55 for r in results)


@pytest.mark.asyncio
async def test_douyin_search_without_map_leaves_id_none() -> None:
    from openbiliclaw.discovery.strategies.douyin_direct import DouyinDirectStrategy

    strategy = DouyinDirectStrategy(
        client=_FakeDouyinClient(),
        llm_service=None,
        sources=("search",),
        seed_keywords=("kw1",),
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile(), limit=10)
    assert results
    assert all(r.source_keyword_id is None for r in results)


# ── planner retire housekeeping ──────────────────────────────────────


class _StubConfig:
    class _Discovery:
        unified_keyword_planner_enabled = True
        planner_poll_seconds = 120
        claim_lease_minutes = 10
        kw_cache_low = 10
        kw_cache_high = 30
        gen_batch = 30
        history_window_size = 150
        history_window_hours = 48

    def __init__(self) -> None:
        self.discovery = self._Discovery()
        self.scheduler = type("_S", (), {"pool_target_count": 300})()


def test_planner_retire_zero_yield_retires_barren_word(db: Database) -> None:
    from openbiliclaw.runtime.keyword_planner import KeywordPlanner

    kid_barren = _seed_used_keyword(db, _BILI, "barren")
    kid_good = _seed_used_keyword(db, _BILI, "good")
    db.increment_keyword_yield(kid_good, "BVc1")
    _age_used_at(db, kid_barren, days_ago=1)
    _age_used_at(db, kid_good, days_ago=1)

    planner = KeywordPlanner(llm_service=None, database=db, config=_StubConfig())
    retired = planner.retire_zero_yield()

    assert retired == 1
    barren_status = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE id=?", (kid_barren,)
    ).fetchone()["status"]
    good_status = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE id=?", (kid_good,)
    ).fetchone()["status"]
    assert barren_status == "expired"
    assert good_status == "used"
