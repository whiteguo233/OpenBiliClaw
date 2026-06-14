"""Tests for the unified keyword planner (Discover backpressure P1.6).

The planner generates search keywords into the ``discovery_keywords`` store
(it does NOT fetch — that is P1.7). These tests drive a ``KeywordPlanner`` with
a fake ``llm_service``, a fake deficit source, and a REAL temporary
``Database`` so the store DAO / single-flight lock exercise their actual SQL.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import DiscoveryConfig
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.runtime.keyword_planner import KeywordPlanner
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_BILI = "bilibili"
_XHS = "xiaohongshu"
_DOUYIN = "douyin"
_YOUTUBE = "youtube"
_TWITTER = "twitter"


# ── fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakeLLM:
    """Records merged-keyword calls and returns a canned per-platform payload.

    ``gate`` (optional) blocks inside the LLM call until set, so two passes can
    be made to overlap deterministically; ``entered`` fires the moment the LLM
    call is reached (used by the single-flight test instead of a busy-wait).
    """

    payload: dict[str, list[str]]
    calls: list[dict[str, str]] = field(default_factory=list)
    gate: asyncio.Event | None = None
    entered: asyncio.Event | None = None

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
        self.calls.append({"system": system_instruction, "user": user_input, "caller": caller})
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            provider="test",
            model="test-model",
        )


@dataclass
class _RaisingLLM:
    calls: list[str] = field(default_factory=list)

    async def complete_structured_task(self, *, caller: str = "", **_: object) -> Any:
        self.calls.append(caller)
        raise RuntimeError("llm down")


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
    """Minimal config exposing the two attributes the planner reads."""

    def __init__(self, discovery: DiscoveryConfig, pool_target_count: int = 300) -> None:
        self.discovery = discovery
        self.scheduler = type("_Sched", (), {"pool_target_count": pool_target_count})()


# ── helpers ──────────────────────────────────────────────────────────────


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
        "history_window_size": 150,
        "history_window_hours": 48,
        "claim_lease_minutes": 10,
        "planner_poll_seconds": 120,
        "plan_ttl_hours": 12,
    }
    base.update(overrides)
    return DiscoveryConfig(**base)  # type: ignore[arg-type]


def _make_planner(
    db: Database,
    *,
    llm: Any,
    profile: SoulProfile,
    deficit: _FakeDeficitSource,
    discovery: DiscoveryConfig | None = None,
    pool_target_count: int = 300,
) -> KeywordPlanner:
    planner = KeywordPlanner(
        llm_service=llm,
        database=db,
        config=_FakeConfig(discovery or _discovery_cfg(), pool_target_count),
        soul_engine=_FakeSoulEngine(profile),
        pool_target_count=pool_target_count,
        signal_event_threshold=6,
    )
    planner.bind_deficit_source(deficit)
    return planner


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "planner.db")
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


# ── tests ────────────────────────────────────────────────────────────────


async def test_cold_start_multiple_platforms_one_merged_call(db: Database) -> None:
    """Cold start with several platforms in deficit → exactly ONE merged LLM
    call covering all due platforms; pending rows land per platform with the
    current digest."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(
        payload={
            _BILI: ["露营 装备 盘点", "和田玉 鉴别 入门"],
            _XHS: ["露营 vlog", "和田玉 真实体验"],
            _DOUYIN: ["露营 整活"],
        }
    )
    deficit = _FakeDeficitSource(
        deficits={_BILI: 40, _XHS: 33, _DOUYIN: 33, _YOUTUBE: 0, _TWITTER: 0}
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # Exactly one merged call, tagged with the planner caller.
    assert len(llm.calls) == 1
    assert llm.calls[0]["caller"] == "discovery.keyword_planner"
    # The user prompt mentions all three due platforms but NOT the zero-deficit ones.
    user = llm.calls[0]["user"]
    assert _BILI in user and _XHS in user and _DOUYIN in user
    assert _YOUTUBE not in user and _TWITTER not in user
    # Pending rows inserted per platform under the current digest.
    assert _pending(db, _BILI, digest) == ["露营 装备 盘点", "和田玉 鉴别 入门"]
    assert _pending(db, _XHS, digest) == ["露营 vlog", "和田玉 真实体验"]
    assert _pending(db, _DOUYIN, digest) == ["露营 整活"]
    assert ledger[_BILI] == 2 and ledger[_XHS] == 2 and ledger[_DOUYIN] == 1
    # Non-due platforms untouched.
    assert _pending(db, _YOUTUBE, digest) == []
    assert _pending(db, _TWITTER, digest) == []


async def test_full_pool_no_deficit_zero_llm_calls(db: Database) -> None:
    """No platform has a deficit and B站 has no catalyst → nothing due → zero
    LLM calls, zero inserts."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_BILI: ["should not be used"]})
    deficit = _FakeDeficitSource(
        deficits=dict.fromkeys((_BILI, _XHS, _DOUYIN, _YOUTUBE, _TWITTER), 0)
    )  # all zero, bili_catalyst False
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert llm.calls == []
    assert ledger == {}
    for platform in (_BILI, _XHS, _DOUYIN, _YOUTUBE, _TWITTER):
        assert _pending(db, platform, digest) == []


async def test_digest_change_expires_old_and_regenerates(db: Database) -> None:
    """When the profile digest changes, old-digest pending is expired and new
    keywords are generated under the new digest."""
    old_profile = _profile(("露营", 0.9))
    old_digest = profile_kw_digest(old_profile)
    # Seed stale pending under the OLD digest directly in the store.
    db.insert_pending_keywords(_XHS, ["旧词1", "旧词2"], old_digest)
    assert db.count_pending_keywords(_XHS, old_digest) == 2

    # A materially different profile → different digest.
    new_profile = _profile(("露营", 0.9), ("机器学习", 0.95), ("城市规划", 0.8))
    new_digest = profile_kw_digest(new_profile)
    assert new_digest != old_digest

    llm = _FakeLLM(payload={_XHS: ["新词A", "新词B"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=new_profile, deficit=deficit)

    await planner.run_once()

    # Old-digest pending expired (no longer pending).
    assert db.count_pending_keywords(_XHS, old_digest) == 0
    old_rows = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE keyword = '旧词1'"
    ).fetchone()
    assert str(old_rows["status"]) == "expired"
    # New keywords under the new digest.
    assert _pending(db, _XHS, new_digest) == ["新词A", "新词B"]


async def test_single_flight_second_concurrent_run_does_not_double_generate(
    db: Database,
) -> None:
    """A second ``run_once`` overlapping the first finds the planner lock held
    and skips, so the merged LLM call fires only once."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    gate = asyncio.Event()
    entered = asyncio.Event()
    llm = _FakeLLM(payload={_XHS: ["w1", "w2"]}, gate=gate, entered=entered)
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    # First pass acquires the lock then parks inside the LLM call (gate).
    first = asyncio.create_task(planner.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    assert len(llm.calls) == 1, "first run_once never reached the LLM call"

    # Second pass while the lock is held → must skip without a second LLM call.
    second_ledger = await planner.run_once()
    assert second_ledger == {}
    assert len(llm.calls) == 1

    # Release the gate, let the first pass finish and write its keywords.
    gate.set()
    await asyncio.wait_for(first, timeout=5.0)
    assert _pending(db, _XHS, digest) == ["w1", "w2"]
    assert len(llm.calls) == 1


async def test_lock_held_by_other_owner_skips_generation(db: Database) -> None:
    """If another owner already holds the planner lock, ``run_once`` skips —
    no LLM call, no inserts (single-flight, deterministic)."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_XHS: ["w1"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    # A different owner holds the lock for the whole pass.
    assert db.acquire_planner_lock("other-owner", 600.0) is True

    ledger = await planner.run_once()

    assert ledger == {}
    assert llm.calls == []
    assert _pending(db, _XHS, digest) == []


async def test_llm_failure_falls_back_to_interest_names(db: Database) -> None:
    """When the merged LLM call raises, every due platform falls back to
    deterministic weight-ranked interest names — no crash, pending inserted."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    llm = _RaisingLLM()
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert llm.calls == ["discovery.keyword_planner"]
    # Weight-ranked interest names (露营 0.9 before 和田玉 0.7) on BOTH platforms.
    assert _pending(db, _BILI, digest) == ["露营", "和田玉"]
    assert _pending(db, _XHS, digest) == ["露营", "和田玉"]
    assert ledger[_BILI] == 2 and ledger[_XHS] == 2


async def test_missing_platform_in_result_falls_back(db: Database) -> None:
    """A platform the model omits falls back to interest names; the platforms
    it returned use the model output."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    # LLM returns only bilibili; xiaohongshu is omitted → fallback for XHS.
    llm = _FakeLLM(payload={_BILI: ["露营 盘点"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    await planner.run_once()

    assert _pending(db, _BILI, digest) == ["露营 盘点"]
    assert _pending(db, _XHS, digest) == ["露营", "和田玉"]


async def test_bilibili_catalyst_due_even_when_cache_not_below_low(db: Database) -> None:
    """B站 enters ``due`` on its catalyst (pool-below-target / ≥6 signals) even
    when its keyword cache is NOT below the low watermark and it has no
    plain deficit."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # Fill B站 cache ABOVE low (low=10) so cache_below_low is False.
    db.insert_pending_keywords(_BILI, [f"已有{i}" for i in range(12)], digest)
    assert db.count_pending_keywords(_BILI, digest) == 12

    llm = _FakeLLM(payload={_BILI: ["新催化词"]})
    # No plain deficit anywhere, but bili catalyst fires.
    deficit = _FakeDeficitSource(
        deficits=dict.fromkeys((_BILI, _XHS, _DOUYIN, _YOUTUBE, _TWITTER), 0),
        bili_catalyst=True,
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # Exactly one call, and it is ONLY for bilibili (others have no deficit).
    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert _BILI in user
    assert _XHS not in user and _DOUYIN not in user
    # New keyword appended on top of the existing 12.
    assert "新催化词" in _pending(db, _BILI, digest)
    assert ledger[_BILI] >= 1


async def test_bilibili_catalyst_skips_generation_when_cache_full(db: Database) -> None:
    """B站 due via catalyst but cache already at high → need=0 → no LLM call,
    no new rows (the platform is dropped from the prompt)."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    db.insert_pending_keywords(_BILI, [f"满{i}" for i in range(30)], digest)  # == high
    assert db.count_pending_keywords(_BILI, digest) == 30

    llm = _FakeLLM(payload={_BILI: ["should not fire"]})
    deficit = _FakeDeficitSource(
        deficits=dict.fromkeys((_BILI, _XHS, _DOUYIN, _YOUTUBE, _TWITTER), 0),
        bili_catalyst=True,
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # No merged generation (every due platform had need<=0).
    assert llm.calls == []
    assert ledger.get(_BILI, 0) == 0
    assert db.count_pending_keywords(_BILI, digest) == 30


async def test_sparse_profile_recycles_oldest_used(db: Database) -> None:
    """A due platform whose generation + fallback yield nothing NEW (sparse
    profile, all words already in-flight) recycles its oldest ``used`` word
    back to pending instead of starving."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # Make "露营" a USED historical row (so the interest-name fallback word is
    # not new). insert → claim → mark used.
    db.insert_pending_keywords(_XHS, ["露营"], digest)
    claimed = db.claim_keywords(_XHS, 1)
    assert claimed, "expected one claimed row"
    db.mark_keyword_used(int(claimed[0]["id"]))
    assert db.count_pending_keywords(_XHS, digest) == 0

    # LLM also returns only the already-used word → nothing new from generation
    # OR fallback → recycle path must fire.
    llm = _FakeLLM(payload={_XHS: ["露营"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # The oldest used word was recycled back to pending.
    assert _pending(db, _XHS, digest) == ["露营"]
    assert ledger[_XHS] == 1


async def test_flag_off_run_once_does_nothing(db: Database) -> None:
    """Flag OFF → ``run_once`` is a pure no-op: no LLM call, no store writes,
    even with deficits present."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_BILI: ["x"], _XHS: ["y"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33}, bili_catalyst=True)
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=deficit,
        discovery=_discovery_cfg(unified_keyword_planner_enabled=False),
    )

    ledger = await planner.run_once()

    assert ledger == {}
    assert llm.calls == []
    assert _pending(db, _BILI, digest) == []
    assert _pending(db, _XHS, digest) == []


async def test_flag_off_run_loop_does_nothing(db: Database) -> None:
    """Flag OFF → the ``run()`` poll loop never touches the LLM or the store
    (one iteration, sleep cancelled)."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_XHS: ["y"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=deficit,
        discovery=_discovery_cfg(unified_keyword_planner_enabled=False, planner_poll_seconds=1),
    )

    # Run the loop briefly, then cancel — flag off means it should only sleep.
    task = asyncio.create_task(planner.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert llm.calls == []
    assert _pending(db, _XHS, digest) == []


async def test_no_profile_returns_empty(db: Database) -> None:
    """No soul engine / no profile → run_once short-circuits (no LLM, no rows)."""
    profile = _profile(("露营", 0.9))
    llm = _FakeLLM(payload={_XHS: ["y"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)
    # Drop the soul engine so no profile can be loaded.
    planner._soul_engine = None  # type: ignore[assignment]

    ledger = await planner.run_once()
    assert ledger == {}
    assert llm.calls == []
