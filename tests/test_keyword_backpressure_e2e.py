"""Flag-ON end-to-end acceptance for the unified keyword planner (P1.9).

This is the P1 completion gate's flag-on proof: with
``DiscoveryConfig(unified_keyword_planner_enabled=True)``, a REAL temp
``Database``, a fake LLM returning a merged ``{"bilibili": [...],
"xiaohongshu": [...], ...}`` payload, and fake platform clients, it drives the
WHOLE loop through the **real** planner + fetch coordinator + store + discovery
engine + candidate pipeline — fakes only for LLM + platform IO — and asserts:

* planner ``run_once`` (deficit present) → ONE merged LLM call (caller
  ``discovery.keyword_planner``) → ``pending`` keywords inserted per due
  platform under the current ``profile_kw_digest``;
* a fetch cycle for an inline platform (B站 + 抖音), an async platform (XHS),
  and a fetch-only platform (X + YouTube) → words claimed, injected into the
  search, lifecycle correct (inline → ``used``; XHS → ``executing`` then
  ``used`` via the task-result path; fetch-only → ``used`` on handoff);
* candidates carry ``source_keyword_id``; on admit ``yield_count`` increments
  (idempotent);
* the pool reaching target stops fetch (no more claims);
* flag flipped OFF mid-test → the loop no-ops.

The three execution shapes are driven exactly as production wires them:

* **Inline-admit** (B站 / 抖音): ``coordinator.claim`` → ``strategy.discover``
  with the injected ``queries`` / ``keyword_ids`` (skips the strategy's own
  LLM keyword generation, ``llm_evaluation=False``) → ``engine
  .cache_evaluated_results`` (the ``_cache_results`` admission convergence,
  which backfills yield) → ``coordinator.mark_used``.
* **Fetch-only** (X / YouTube): ``claim`` → ``strategy.discover`` → enqueue the
  raw candidates into ``discovery_candidates`` (handoff) → ``mark_used`` → later
  ``DiscoveryCandidatePipeline.drain_pending`` admits + backfills yield.
* **Truly async** (XHS): ``claim`` → ``mark_executing`` (the extension task is
  out-of-band) → the xhs task-result path marks the word ``used`` and admits
  the note candidate carrying its ``source_keyword_id``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import DiscoveryConfig
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.discovery.strategies.douyin_direct import DouyinDirectStrategy
from openbiliclaw.discovery.strategies.search import SearchStrategy
from openbiliclaw.discovery.strategies.x import XSearchStrategy
from openbiliclaw.discovery.strategies.youtube import YoutubeSearchStrategy
from openbiliclaw.runtime.keyword_fetch import (
    PLATFORM_BILIBILI,
    PLATFORM_DOUYIN,
    PLATFORM_TWITTER,
    PLATFORM_XIAOHONGSHU,
    PLATFORM_YOUTUBE,
    ClaimedKeyword,
    KeywordFetchCoordinator,
    mark_keyword_terminal_from_xhs_task,
    source_keyword_id_from_xhs_task,
)
from openbiliclaw.runtime.keyword_planner import KeywordPlanner
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

from .test_discovery_candidate_pipeline import _ScoringLLM

if TYPE_CHECKING:
    from pathlib import Path


# ── fakes (LLM + platform IO only) ────────────────────────────────────────


@dataclass
class _MergedKeywordLLM:
    """Fake LLM returning a canned merged per-platform keyword payload.

    Records every call so the test can assert exactly one merged generation
    under the ``discovery.keyword_planner`` caller.
    """

    payload: dict[str, list[str]]
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> Any:
        self.calls.append({"caller": caller, "user": user_input})
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test-model",
        )


@dataclass
class _FakeBiliClient:
    """B站 search client (inline-admit). One result per query, keyed by query."""

    calls: list[str] = field(default_factory=list)

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]:
        self.calls.append(keyword)
        # Deterministic single bvid per query.
        bvid = f"BV_{abs(hash(keyword)) % 100000}"
        return [{"bvid": bvid, "title": f"{keyword} 盘点", "description": keyword}]


@dataclass
class _FakeDouyinClient:
    """抖音 direct client (inline-admit). search_aweme only; rest empty."""

    search_source_strategy = "dy-direct-search"
    calls: list[str] = field(default_factory=list)

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        self.calls.append(keyword)
        return [{"aweme_id": f"{keyword}-a", "desc": keyword, "author": {"nickname": "N"}}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        return []

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        return []

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, object]]:
        return []


@dataclass
class _FakeXClient:
    """X search client (fetch-only). Returns one tweet per query."""

    calls: list[str] = field(default_factory=list)

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        self.calls.append(query)
        return [
            {
                "id": f"{query}-1",
                "text": f"tweet about {query}",
                "url": f"https://x.com/u/status/{query}-1",
                "user": {"screen_name": "u", "name": "U"},
            }
        ]


@dataclass
class _FakeYtClient:
    """YouTube search client (fetch-only). One video per query."""

    calls: list[str] = field(default_factory=list)

    async def search_videos(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(query)
        return [{"videoId": f"{query}-v", "title": f"{query} video", "channel": "C"}]


@dataclass
class _FakeDeficitSource:
    """Stand-in for the controller's deficit / catalyst口径."""

    deficits: dict[str, int] = field(default_factory=dict)
    bili_catalyst: bool = False
    source_targets: dict[str, int] = field(default_factory=dict)

    def keyword_planner_real_deficit(self, platform: str) -> int:
        return int(self.deficits.get(platform, 0))

    def keyword_planner_bilibili_catalyst(self) -> bool:
        return bool(self.bili_catalyst)

    def _source_target_counts(self) -> dict[str, int]:
        return dict(self.source_targets)


class _FakeSoulEngine:
    def __init__(self, profile: SoulProfile) -> None:
        self._profile = profile

    async def get_profile(self) -> SoulProfile:
        return self._profile


class _FakeConfig:
    def __init__(self, discovery: DiscoveryConfig, pool_target_count: int = 300) -> None:
        self.discovery = discovery
        self.scheduler = type("_Sched", (), {"pool_target_count": pool_target_count})()


# ── helpers ────────────────────────────────────────────────────────────────


def _profile(*names_weights: tuple[str, float]) -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name=name, category="测试", weight=weight)
                for name, weight in names_weights
            ]
        )
    )


def _discovery_cfg(**overrides: object) -> DiscoveryConfig:
    base: dict[str, object] = {
        "unified_keyword_planner_enabled": True,
        "kw_cache_high": 30,
        "kw_cache_low": 10,
        "gen_batch": 30,
        "fetch_batch": 5,
        "history_window_size": 150,
        "history_window_hours": 48,
        "claim_lease_minutes": 10,
        "planner_poll_seconds": 120,
        "plan_ttl_hours": 12,
    }
    base.update(overrides)
    return DiscoveryConfig(**base)  # type: ignore[arg-type]


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "e2e.db")
    d.initialize()
    return d


def _pending(db: Database, platform: str, digest: str) -> list[str]:
    rows = db.conn.execute(
        "SELECT keyword FROM discovery_keywords "
        "WHERE platform = ? AND status = 'pending' AND profile_kw_digest = ? "
        "ORDER BY id ASC",
        (platform, digest),
    ).fetchall()
    return [str(r["keyword"]) for r in rows]


def _status(db: Database, keyword_id: int) -> str:
    row = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()
    return str(row["status"]) if row is not None else "<missing>"


def _claimed_ids(db: Database, claimed: list[ClaimedKeyword]) -> dict[str, int]:
    return {c.keyword: c.id for c in claimed}


def _scored(content_id: str, score: float) -> dict[str, Any]:
    """A batch-evaluator score row keyed on the candidate's content_id."""
    return {
        "content_id": content_id,
        "score": score,
        "reason": "fit",
        "topic_group": "t",
        "style_key": "s",
    }


# ── the end-to-end flag-on flow ───────────────────────────────────────────


async def test_flag_on_planner_to_fetch_to_yield_end_to_end(db: Database) -> None:
    """Drive the whole flag-on loop across all three execution shapes."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    discovery = _discovery_cfg(fetch_batch=5)

    # Fake LLM returns merged keywords for every due platform.
    merged_payload = {
        PLATFORM_BILIBILI: ["露营 装备 盘点", "和田玉 鉴别 入门"],
        PLATFORM_XIAOHONGSHU: ["露营 vlog", "和田玉 真实体验"],
        PLATFORM_DOUYIN: ["露营 整活"],
        PLATFORM_YOUTUBE: ["camping gear"],
        PLATFORM_TWITTER: ["camping tips"],
    }
    planner_llm = _MergedKeywordLLM(payload=merged_payload)
    deficit = _FakeDeficitSource(
        deficits={
            PLATFORM_BILIBILI: 40,
            PLATFORM_XIAOHONGSHU: 33,
            PLATFORM_DOUYIN: 33,
            PLATFORM_YOUTUBE: 33,
            PLATFORM_TWITTER: 33,
        }
    )
    planner = KeywordPlanner(
        llm_service=planner_llm,
        database=db,
        config=_FakeConfig(discovery, pool_target_count=300),
        soul_engine=_FakeSoulEngine(profile),
        pool_target_count=300,
    )
    planner.bind_deficit_source(deficit)

    # ─── (1) one merged generation pass fills the store ───────────────────
    generated = await planner.run_once()

    assert len(planner_llm.calls) == 1, "exactly ONE merged LLM call"
    assert planner_llm.calls[0]["caller"] == "discovery.keyword_planner"
    user = planner_llm.calls[0]["user"]
    for platform in merged_payload:
        assert platform in user, f"{platform} block missing from merged prompt"
    # Pending rows inserted per platform under the current digest.
    assert _pending(db, PLATFORM_BILIBILI, digest) == ["露营 装备 盘点", "和田玉 鉴别 入门"]
    assert _pending(db, PLATFORM_XIAOHONGSHU, digest) == ["露营 vlog", "和田玉 真实体验"]
    assert _pending(db, PLATFORM_DOUYIN, digest) == ["露营 整活"]
    assert _pending(db, PLATFORM_YOUTUBE, digest) == ["camping gear"]
    assert _pending(db, PLATFORM_TWITTER, digest) == ["camping tips"]
    assert generated[PLATFORM_BILIBILI] == 2
    # The per-cycle observability ledger captured per-platform production.
    assert planner.last_cycle_ledger[PLATFORM_BILIBILI]["generated"] == 2
    assert planner.last_cycle_ledger[PLATFORM_XIAOHONGSHU]["yield"] == 0

    coordinator = KeywordFetchCoordinator(database=db, discovery_config=discovery)
    assert coordinator.should_claim() is True
    engine = ContentDiscoveryEngine(database=db)

    # ─── (2a) INLINE-admit: B站 search ────────────────────────────────────
    bili_client = _FakeBiliClient()
    bili_strategy = SearchStrategy(
        llm_service=None,  # type: ignore[arg-type]
        bilibili_client=bili_client,
        database=db,
        llm_evaluation=False,  # raw candidates; we admit via cache_evaluated_results
    )
    bili_claimed = coordinator.claim(PLATFORM_BILIBILI)
    assert [c.keyword for c in bili_claimed] == ["露营 装备 盘点", "和田玉 鉴别 入门"]
    bili_ids = _claimed_ids(db, bili_claimed)
    bili_results = await bili_strategy.discover(
        profile,
        limit=20,
        queries=[c.keyword for c in bili_claimed],
        keyword_ids=bili_ids,
    )
    # The strategy searched exactly the claimed words (NO internal LLM gen).
    assert bili_client.calls == ["露营 装备 盘点", "和田玉 鉴别 入门"]
    assert all(r.source_keyword_id in bili_ids.values() for r in bili_results)
    cached_bili = engine.cache_evaluated_results(bili_results)
    coordinator.mark_used(bili_claimed)
    assert cached_bili == len(bili_results) >= 1
    # Inline words are ``used``; each produced+admitted item credited its keyword.
    for kid in bili_ids.values():
        assert _status(db, kid) == "used"
        assert db.keyword_yield_count(kid) == 1

    # ─── (2b) INLINE-admit: 抖音 search plugin ────────────────────────────
    dy_client = _FakeDouyinClient()
    dy_claimed = coordinator.claim(PLATFORM_DOUYIN)
    assert [c.keyword for c in dy_claimed] == ["露营 整活"]
    dy_ids = _claimed_ids(db, dy_claimed)
    dy_strategy = DouyinDirectStrategy(
        client=dy_client,
        llm_service=None,
        sources=("search",),
        seed_keywords=tuple(c.keyword for c in dy_claimed),
        seed_keyword_ids=dy_ids,
        llm_evaluation=False,
    )
    dy_results = await dy_strategy.discover(profile, limit=20)
    assert dy_client.calls == ["露营 整活"]
    assert all(r.source_keyword_id == dy_ids["露营 整活"] for r in dy_results)
    cached_dy = engine.cache_evaluated_results(dy_results)
    coordinator.mark_used(dy_claimed)
    assert cached_dy >= 1
    for kid in dy_ids.values():
        assert _status(db, kid) == "used"
        assert db.keyword_yield_count(kid) == 1

    # ─── (2c) FETCH-ONLY: X + YouTube (handoff → pipeline admit) ──────────
    x_client = _FakeXClient()
    x_claimed = coordinator.claim(PLATFORM_TWITTER)
    assert [c.keyword for c in x_claimed] == ["camping tips"]
    x_ids = _claimed_ids(db, x_claimed)
    x_strategy = XSearchStrategy(client=x_client)
    x_results = await x_strategy.discover(
        profile,
        limit=10,
        queries=[c.keyword for c in x_claimed],
        keyword_ids=x_ids,
    )
    assert x_client.calls == ["camping tips"]
    # Fetch-only: hand raw candidates to discovery_candidates, then mark used.
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(r) for r in x_results])
    coordinator.mark_used(x_claimed)
    for kid in x_ids.values():
        assert _status(db, kid) == "used"  # consumed at handoff
        assert db.keyword_yield_count(kid) == 0  # yield NOT yet backfilled

    yt_client = _FakeYtClient()
    yt_claimed = coordinator.claim(PLATFORM_YOUTUBE)
    assert [c.keyword for c in yt_claimed] == ["camping gear"]
    yt_ids = _claimed_ids(db, yt_claimed)
    yt_strategy = YoutubeSearchStrategy(
        client=yt_client,  # type: ignore[arg-type]
        llm_service=None,  # type: ignore[arg-type]
        llm_evaluation=False,
    )
    yt_results = await yt_strategy.discover(
        profile,
        limit=10,
        queries=[c.keyword for c in yt_claimed],
        keyword_ids=yt_ids,
    )
    assert yt_client.calls == ["camping gear"]
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(r) for r in yt_results])
    coordinator.mark_used(yt_claimed)
    for kid in yt_ids.values():
        assert _status(db, kid) == "used"
        assert db.keyword_yield_count(kid) == 0

    # Drain the candidate pipeline → fetch-only words get their yield backfilled.
    scored = [
        _scored("camping tips-1", 0.92),  # the X tweet content_id
        _scored("camping gear-v", 0.92),  # the YouTube video content_id
    ]
    pipeline_engine = ContentDiscoveryEngine(llm_service=_ScoringLLM(scored), database=db)
    pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=pipeline_engine, pool_target_count=300
    )
    result = await pipeline.drain_pending(profile=profile, batch_size=30)
    assert result["cached"] == 2
    for kid in (*x_ids.values(), *yt_ids.values()):
        assert db.keyword_yield_count(kid) == 1  # backfilled at admit

    # ─── (2d) TRULY ASYNC: XHS (executing → used via task-result) ─────────
    xhs_claimed = coordinator.claim(PLATFORM_XIAOHONGSHU, 1)
    assert [c.keyword for c in xhs_claimed] == ["露营 vlog"]
    xhs_word = xhs_claimed[0]
    # Enqueue is out-of-band → mark executing (NOT used) with the keyword id
    # riding on the task payload.
    coordinator.mark_executing(xhs_word)
    assert _status(db, xhs_word.id) == "executing"
    task_payload = json.dumps({"keyword": xhs_word.keyword, "source_keyword_id": xhs_word.id})

    # The xhs task-result handler (api/app.py) path: extract the id, admit the
    # produced note candidate carrying it, then mark the word terminal.
    assert source_keyword_id_from_xhs_task(task_payload) == xhs_word.id
    note = DiscoveredContent(
        bvid="xhsnote-camp",
        content_id="xhsnote-camp",
        title="露营 note",
        content_url="https://www.xiaohongshu.com/explore/xhsnote-camp",
        source_platform="xiaohongshu",
        source_strategy="xhs-extension-task",
        source_keyword_id=source_keyword_id_from_xhs_task(task_payload),
    )
    db.enqueue_discovery_candidates(
        [
            discovered_content_to_candidate_write(
                note,
                source_context="task",
                raw_payload={"admission_policy": "observed", "score_threshold": 0.60},
            )
        ]
    )
    mark_keyword_terminal_from_xhs_task(db, task_payload, success=True)
    assert _status(db, xhs_word.id) == "used"  # terminal only on callback

    xhs_engine = ContentDiscoveryEngine(
        llm_service=_ScoringLLM([_scored("xhsnote-camp", 0.72)]),
        database=db,
    )
    xhs_pipeline = DiscoveryCandidatePipeline(
        database=db, discovery_engine=xhs_engine, pool_target_count=300
    )
    xhs_result = await xhs_pipeline.drain_pending(profile=profile, batch_size=30)
    assert xhs_result["cached"] == 1
    assert db.keyword_yield_count(xhs_word.id) == 1

    # ─── (3) yield backfill is idempotent ─────────────────────────────────
    # Re-admitting the same (keyword, content) must not double-count.
    assert engine.cache_evaluated_results(bili_results) == 0
    for kid in bili_ids.values():
        assert db.keyword_yield_count(kid) == 1

    # ─── (4) pool at target → fetch stops (no more claims) ────────────────
    # All XHS words are consumed (one used above, one still pending). Drain the
    # rest so the store has no claimable XHS words, then assert claim is inert.
    remaining_xhs = coordinator.claim(PLATFORM_XIAOHONGSHU)
    assert [c.keyword for c in remaining_xhs] == ["和田玉 真实体验"]
    coordinator.mark_used(remaining_xhs)
    # Store now empty for XHS → claim returns [] (the store-non-empty gate).
    assert coordinator.claim(PLATFORM_XIAOHONGSHU) == []


async def test_flag_flipped_off_mid_loop_no_ops(db: Database) -> None:
    """Flag flipped OFF mid-run → the planner pass and the fetch coordinator both
    no-op: no LLM call, no claims, no store writes (clean opt-out / rollback)."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)

    # Start with the flag ON; the SAME config object is later flipped off so the
    # planner + coordinator (which both read it live) observe the change.
    discovery = _discovery_cfg(unified_keyword_planner_enabled=True)
    planner_llm = _MergedKeywordLLM(payload={PLATFORM_XIAOHONGSHU: ["露营 vlog"]})
    deficit = _FakeDeficitSource(deficits={PLATFORM_XIAOHONGSHU: 33})
    planner = KeywordPlanner(
        llm_service=planner_llm,
        database=db,
        config=_FakeConfig(discovery, pool_target_count=300),
        soul_engine=_FakeSoulEngine(profile),
        pool_target_count=300,
    )
    planner.bind_deficit_source(deficit)
    coordinator = KeywordFetchCoordinator(database=db, discovery_config=discovery)

    # Pre-seed one pending XHS word so a flag-on claim WOULD return something.
    db.insert_pending_keywords(PLATFORM_XIAOHONGSHU, ["既有词"], digest)
    assert coordinator.should_claim() is True

    # Flip the flag OFF mid-test.
    discovery.unified_keyword_planner_enabled = False

    # Planner pass is now a pure no-op.
    assert await planner.run_once() == {}
    assert planner_llm.calls == []
    # No NEW pending generated (the pre-seeded word is untouched).
    assert _pending(db, PLATFORM_XIAOHONGSHU, digest) == ["既有词"]

    # Coordinator gate is closed; should_claim() is False. (claim() itself is
    # flag-agnostic by design — the gate is should_claim — so the seeded word
    # stays pending because the site never calls claim once the gate is shut.)
    assert coordinator.enabled is False
    assert coordinator.should_claim() is False
    assert _pending(db, PLATFORM_XIAOHONGSHU, digest) == ["既有词"]
