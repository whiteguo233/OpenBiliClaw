"""Tests for the unified keyword planner (Discover backpressure P1.6).

The planner generates search keywords into the ``discovery_keywords`` store
(it does NOT fetch — that is P1.7). These tests drive a ``KeywordPlanner`` with
a fake ``llm_service``, a fake deficit source, and a REAL temporary
``Database`` so the store DAO / single-flight lock exercise their actual SQL.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import DiscoveryConfig, derive_inspiration_breadth_params
from openbiliclaw.discovery.inspiration import (
    AllocationTarget,
    AxisRow,
    BrainstormBranch,
    ExaPreviewItem,
    GroundedProbe,
    MaterializeCandidate,
    SecondaryInterest,
    build_like_secondary_interest_window,
    materialize_platform_keywords,
)
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
_GITHUB = "github"
_ZHIHU = "zhihu"
_REDDIT = "reddit"
_BANGUMI = "bangumi"
_WEIBO = "weibo"
_INSTAGRAM = "instagram"
_SEARCH_PLATFORMS = (
    _BILI,
    _XHS,
    _DOUYIN,
    _YOUTUBE,
    _TWITTER,
    _GITHUB,
    _ZHIHU,
    _REDDIT,
    _BANGUMI,
    _WEIBO,
)
# ── fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakeLLM:
    """Records merged-keyword calls and returns a canned per-platform payload.

    ``gate`` (optional) blocks inside the LLM call until set, so two passes can
    be made to overlap deterministically; ``entered`` fires the moment the LLM
    call is reached (used by the single-flight test instead of a busy-wait).
    """

    payload: dict[str, object]
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
        inject_core_memory: bool = True,
    ) -> Any:
        self.calls.append(
            {
                "system": system_instruction,
                "user": user_input,
                "caller": caller,
                "reasoning_effort": reasoning_effort,
                "inject_core_memory": inject_core_memory,
            }
        )
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
class _SequentialLLM:
    payloads: list[object]
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
        **_: object,
    ) -> Any:
        self.calls.append(
            {
                "system": system_instruction,
                "user": user_input,
                "caller": caller,
                "max_tokens": str(max_tokens),
                "reasoning_effort": reasoning_effort or "",
                "inject_core_memory": str(inject_core_memory),
            }
        )
        from openbiliclaw.llm.base import LLMResponse

        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            content=str(content),
            provider="test",
            model="test-model",
        )


@dataclass
class _RawLLM:
    content: str
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
        **_: object,
    ) -> Any:
        self.calls.append(
            {
                "system": system_instruction,
                "user": user_input,
                "caller": caller,
                "max_tokens": str(max_tokens),
                "reasoning_effort": reasoning_effort or "",
                "inject_core_memory": str(inject_core_memory),
            }
        )
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content=self.content, provider="test", model="test-model")


@dataclass
class _FakeInspirationProvider:
    previews_by_query: dict[str, list[ExaPreviewItem]]
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        self.calls.append((query, limit))
        return list(self.previews_by_query.get(query, []))


@dataclass
class _LocalLedgerProvider:
    previews_by_query: dict[str, list[ExaPreviewItem]]
    calls: list[tuple[str, int]] = field(default_factory=list)
    last_search_provider: str | None = None

    def begin_stage(self) -> None:
        self.last_search_provider = None

    async def search(self, query: str, *, limit: int) -> list[ExaPreviewItem]:
        self.calls.append((query, limit))
        previews = list(self.previews_by_query.get(query, []))
        self.last_search_provider = "local_cache" if previews else None
        return previews

    def grounding_ledger(self) -> dict[str, object]:
        hits = sum(1 for _, _limit in self.calls if self.last_search_provider == "local_cache")
        return {
            "local_hits": hits,
            "local_misses": 0,
            "local_sources": {"content_cache": hits} if hits else {},
        }


@dataclass
class _FakeDeficitSource:
    """Stand-in for the controller's deficit / catalyst口径."""

    deficits: dict[str, int] = field(default_factory=dict)
    bili_catalyst: bool = False
    source_targets: dict[str, int] = field(default_factory=dict)
    explore_due_soon: bool = False
    covered_topic_groups: list[str] = field(default_factory=list)
    explore_marked: int = 0

    def keyword_planner_real_deficit(self, platform: str) -> int:
        return int(self.deficits.get(platform, 0))

    def keyword_planner_bilibili_catalyst(self) -> bool:
        return bool(self.bili_catalyst)

    def keyword_planner_explore_due_soon(self) -> bool:
        return bool(self.explore_due_soon)

    def keyword_planner_explore_covered_topic_groups(self) -> list[str]:
        return list(self.covered_topic_groups)

    def keyword_planner_mark_explore_planned(self) -> None:
        self.explore_marked += 1

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


def test_keyword_interest_hint_terms_infer_source_interest_from_grounding() -> None:
    interests = [
        SecondaryInterest(interest_id="interest:anime", label="动漫", parent="动漫"),
        SecondaryInterest(interest_id="interest:food", label="美食探店", parent="美食"),
    ]
    branches = [
        BrainstormBranch(
            secondary_interest="动漫",
            branch_id="anime-seasonal",
            branch_label="新番季度筛选",
            probe_queries=("2025年7月新番 推荐 避雷",),
        ),
        BrainstormBranch(
            secondary_interest="美食探店",
            branch_id="food-local",
            branch_label="城市美食地图",
            probe_queries=("广州地道小吃 攻略 2025",),
        ),
    ]
    grounding = [
        GroundedProbe(
            secondary_interest="美食探店",
            branch_id="food-local",
            probe_query="广州地道小吃 攻略 2025",
            source_terms=("广州地道小吃",),
            evidence_titles=("广州地道小吃 本地人推荐",),
        )
    ]

    hints = KeywordPlanner._interest_hint_terms(interests, branches, grounding)

    assert (
        KeywordPlanner._infer_source_interest_from_keyword("广州地道小吃攻略 2025", hints)
        == "美食探店"
    )
    assert (
        KeywordPlanner._infer_source_interest_from_keyword("2025年7月新番 快速推荐", hints)
        == "动漫"
    )
    assert (
        KeywordPlanner._infer_source_interest_from_keyword(
            "local restaurant guide explained",
            hints,
        )
        == "美食探店"
    )


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


# Phase-2 config collapse: the per-knob inspiration_* config fields became
# derived breadth params. Tests keep passing the old knob kwargs to
# ``_discovery_cfg`` — they are split out here and injected into the planner as
# an ``InspirationBreadthParams`` override, so no test body changed.
_INSPIRATION_TIER_KEY_TO_PARAM = {
    "inspiration_aspect_window_size": "aspect_window_size",
    "inspiration_interest_sample_size": "interest_sample_size",
    "inspiration_max_probe_searches_per_stage": "max_probe_searches_per_stage",
    "inspiration_platforms_per_probe": "platforms_per_probe",
    "inspiration_riskcontrolled_probe_budget": "riskcontrolled_probe_budget",
    "inspiration_search_pages_per_probe": "search_pages_per_probe",
    "inspiration_search_results_per_query": "search_results_per_query",
    "inspiration_max_seeds_per_aspect": "max_seeds_per_aspect",
    "inspiration_max_keywords_per_platform": "max_keywords_per_platform",
}


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
    param_overrides = {
        param: int(base.pop(key))  # type: ignore[call-overload]
        for key, param in _INSPIRATION_TIER_KEY_TO_PARAM.items()
        if key in base
    }
    cfg = DiscoveryConfig(**base)  # type: ignore[arg-type]
    if param_overrides:
        cfg._test_inspiration_params = dataclasses.replace(  # type: ignore[attr-defined]
            derive_inspiration_breadth_params("medium"), **param_overrides
        )
    return cfg


def _make_planner(
    db: Database,
    *,
    llm: Any,
    profile: SoulProfile,
    deficit: _FakeDeficitSource,
    discovery: DiscoveryConfig | None = None,
    pool_target_count: int = 300,
    inspiration_provider: Any | None = None,
) -> KeywordPlanner:
    cfg = discovery or _discovery_cfg()
    planner = KeywordPlanner(
        llm_service=llm,
        database=db,
        config=_FakeConfig(cfg, pool_target_count),
        soul_engine=_FakeSoulEngine(profile),
        pool_target_count=pool_target_count,
        signal_event_threshold=6,
        inspiration_provider=inspiration_provider,
        inspiration_params=getattr(cfg, "_test_inspiration_params", None),
    )
    planner.bind_deficit_source(deficit)
    return planner


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "planner.db")
    d.initialize()
    return d


def _pending(
    db: Database,
    platform: str,
    digest: str,
    *,
    keyword_kind: str = "regular",
) -> list[str]:
    rows = db.conn.execute(
        "SELECT keyword FROM discovery_keywords "
        "WHERE platform = ? "
        "AND keyword_kind = ? "
        "AND status = 'pending' "
        "AND profile_kw_digest = ? "
        "ORDER BY id ASC",
        (platform, keyword_kind, digest),
    ).fetchall()
    return [str(r["keyword"]) for r in rows]


async def test_plan_inspiration_axis_keywords_parses_valid_payload_once(db: Database) -> None:
    payload = {
        "axes": [
            {
                "interest": "游戏评价",
                "axis_label": "子类型:种田城建模拟",
                "axis_kind": "subgenre",
                "example_terms": ["耐玩", "沙盒"],
                "evidence_refs": ["https://example.test/a"],
                "time_sensitive": False,
            }
        ],
        "keywords": [
            {
                "interest": "游戏评价",
                "axis_id_or_label": "设计师视角/机制拆解",
                "platform": _BILI,
                "core_concept": "只狼 忍义手 设计理念",
                "decoration": "拆解",
                "recency_sensitivity": "low",
            }
        ],
    }
    llm = _RawLLM(json.dumps(payload, ensure_ascii=False))
    planner = _make_planner(
        db,
        llm=llm,
        profile=_profile(("游戏评价", 0.9)),
        deficit=_FakeDeficitSource(),
    )

    axes, candidates, telemetry = await planner.plan_inspiration_axis_keywords(
        profile_digest={"interests": ["游戏评价"]},
        platform_guides={
            _BILI: {"query_style": ["拆解", "测评"]},
            _XHS: {"query_style": ["笔记"]},
        },
        selected_interests=[
            {"label": "游戏评价", "parent": "游戏", "weight": 0.9},
            {"label": "咖啡器具", "parent": "生活", "weight": 0.7},
            {"label": "城市规划", "parent": "社会", "weight": 0.6},
            {"label": "摄影", "parent": "艺术", "weight": 0.5},
            {"label": "多余兴趣", "parent": "测试", "weight": 0.1},
        ],
        existing_axes=[
            AxisRow(
                interest_label="游戏评价",
                axis_label=f"既有轴{i}",
                axis_kind="method",
                source="test",
                example_terms=("拆解",),
            )
            for i in range(7)
        ],
        fresh_evidence=[
            {
                "interest": "游戏评价",
                "title": f"素材{i}",
                "url": f"https://example.test/{i}",
                "source": "pooled_history",
            }
            for i in range(10)
        ],
        allocation_targets={"游戏评价": {"platforms": [_BILI], "min_axes": 2}},
    )

    assert axes == [
        AxisRow(
            interest_label="游戏评价",
            axis_label="子类型:种田城建模拟",
            axis_kind="subgenre",
            source="llm_axis_keyword",
            example_terms=("耐玩", "沙盒"),
            evidence_refs=("https://example.test/a",),
        )
    ]
    assert candidates == [
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="设计师视角/机制拆解",
            platform=_BILI,
            core_concept="只狼 忍义手 设计理念",
            decoration="拆解",
            recency_sensitivity="low",
            origin="llm_axis_keyword",
        )
    ]
    assert telemetry["llm_call_failed"] is False
    assert telemetry["selected_interests_truncated"] == 1
    assert telemetry["existing_axes_truncated"] == 1
    assert telemetry["fresh_evidence_truncated"] == 2
    assert telemetry["platform_guides_dropped"] == 1
    assert len(llm.calls) == 1
    assert llm.calls[0]["caller"] == "discovery.keyword_inspiration"
    assert llm.calls[0]["max_tokens"] == "8192"
    assert "xiaohongshu" not in llm.calls[0]["user"]


async def test_plan_inspiration_axis_keywords_salvages_truncated_payload_once(
    db: Database,
) -> None:
    valid_axis = {
        "interest": "游戏评价",
        "axis_label": "设计师视角/机制拆解",
        "axis_kind": "creator_lens",
        "example_terms": ["设计理念"],
        "evidence_refs": ["https://example.test/a"],
        "time_sensitive": False,
    }
    valid_keyword = {
        "interest": "游戏评价",
        "axis_id_or_label": "设计师视角/机制拆解",
        "platform": _BILI,
        "core_concept": "只狼 忍义手 设计理念",
        "decoration": "拆解",
        "recency_sensitivity": "low",
    }
    content = (
        '{"axes":['
        + json.dumps(valid_axis, ensure_ascii=False)
        + '],"keywords":['
        + json.dumps(valid_keyword, ensure_ascii=False)
        + ',{"interest":"游戏评价","axis_id_or_label":"残缺"'
    )
    llm = _RawLLM(content)
    planner = _make_planner(
        db,
        llm=llm,
        profile=_profile(("游戏评价", 0.9)),
        deficit=_FakeDeficitSource(),
    )

    axes, candidates, telemetry = await planner.plan_inspiration_axis_keywords(
        profile_digest={"interests": ["游戏评价"]},
        platform_guides={_BILI: {"query_style": ["拆解"]}},
        selected_interests=[{"label": "游戏评价", "parent": "游戏", "weight": 0.9}],
        existing_axes=[],
        fresh_evidence=[],
        allocation_targets={"游戏评价": {"platforms": [_BILI], "min_axes": 1}},
    )

    assert [axis.axis_label for axis in axes] == ["设计师视角/机制拆解"]
    assert [candidate.core_concept for candidate in candidates] == ["只狼 忍义手 设计理念"]
    assert telemetry["parse_salvaged"] is True
    assert telemetry["parse_dropped_count"] == 1
    assert telemetry["llm_call_failed"] is False
    assert len(llm.calls) == 1


@pytest.mark.parametrize("content", ["", "not-json"])
async def test_plan_inspiration_axis_keywords_marks_failed_output_once(
    db: Database,
    content: str,
) -> None:
    llm = _RawLLM(content)
    planner = _make_planner(
        db,
        llm=llm,
        profile=_profile(("游戏评价", 0.9)),
        deficit=_FakeDeficitSource(),
    )

    axes, candidates, telemetry = await planner.plan_inspiration_axis_keywords(
        profile_digest={"interests": ["游戏评价"]},
        platform_guides={_BILI: {"query_style": ["拆解"]}},
        selected_interests=[{"label": "游戏评价", "parent": "游戏", "weight": 0.9}],
        existing_axes=[],
        fresh_evidence=[],
        allocation_targets={"游戏评价": {"platforms": [_BILI], "min_axes": 1}},
    )

    assert axes == []
    assert candidates == []
    assert telemetry["llm_call_failed"] is True
    assert len(llm.calls) == 1


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
    assert llm.calls[0]["reasoning_effort"] is None
    assert llm.calls[0]["inject_core_memory"] is False
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


async def test_reddit_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("本地 AI agent", 0.95), ("开源 LLM", 0.8))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_REDDIT: ["local LLM agents", "open source AI tooling"]})
    deficit = _FakeDeficitSource(deficits={_REDDIT: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_REDDIT: 2}
    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert _REDDIT in user
    assert _pending(db, _REDDIT, digest) == ["local LLM agents", "open source AI tooling"]


async def test_zhihu_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("认知科学", 0.93), ("职场沟通", 0.81))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_ZHIHU: ["认知科学 经验", "职场沟通 问答"]})
    deficit = _FakeDeficitSource(deficits={_ZHIHU: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_ZHIHU: 2}
    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert _ZHIHU in user
    assert _pending(db, _ZHIHU, digest) == ["认知科学 经验", "职场沟通 问答"]


async def test_github_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("本地 AI agent", 0.95), ("开源工具", 0.82))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_GITHUB: ["local llm agent framework", "open source cli toolkit"]})
    deficit = _FakeDeficitSource(deficits={_GITHUB: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_GITHUB: 2}
    assert _GITHUB in llm.calls[0]["user"]
    assert _pending(db, _GITHUB, digest) == [
        "local llm agent framework",
        "open source cli toolkit",
    ]


async def test_bangumi_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("赛博朋克动画", 0.93), ("独立游戏", 0.81))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_BANGUMI: ["赛博朋克 动画", "时间循环 独立游戏"]})
    deficit = _FakeDeficitSource(deficits={_BANGUMI: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_BANGUMI: 2}
    assert len(llm.calls) == 1
    assert _BANGUMI in llm.calls[0]["user"]
    assert _pending(db, _BANGUMI, digest) == ["赛博朋克 动画", "时间循环 独立游戏"]


async def test_weibo_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("AI Agent", 0.93), ("动画制作", 0.81))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_WEIBO: ["AI Agent 热议", "动画制作 业内回应"]})
    deficit = _FakeDeficitSource(deficits={_WEIBO: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_WEIBO: 2}
    assert len(llm.calls) == 1
    assert _WEIBO in llm.calls[0]["user"]
    assert _pending(db, _WEIBO, digest) == ["AI Agent 热议", "动画制作 业内回应"]


async def test_instagram_deficit_is_included_in_unified_keyword_generation(db: Database) -> None:
    profile = _profile(("urban photography", 0.93), ("indie game art", 0.81))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_INSTAGRAM: ["urban photography", "indie game art"]})
    deficit = _FakeDeficitSource(deficits={_INSTAGRAM: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert ledger == {_INSTAGRAM: 2}
    assert len(llm.calls) == 1
    assert _INSTAGRAM in llm.calls[0]["user"]
    assert _pending(db, _INSTAGRAM, digest) == ["urban photography", "indie game art"]


async def test_keyword_planner_uses_layered_profile_prefix(db: Database) -> None:
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    llm = _FakeLLM(payload={_BILI: ["露营 装备 盘点"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 40})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    await planner.run_once()

    user = llm.calls[0]["user"]
    assert "<profile_summary>" not in user
    assert user.index("<profile_core>") < user.index("<profile_interests>")
    assert user.index("<profile_interests>") < user.index("<platforms>")


async def test_cold_start_merged_prompt_carries_diversity_hints(db: Database) -> None:
    profile = _profile(("人工智能", 0.96), ("篮球战术", 0.72), ("电影拉片", 0.68))
    llm = _FakeLLM(
        payload={
            _BILI: ["篮球战术 复盘", "电影拉片 结构"],
            _XHS: ["篮球训练 真实体验"],
        }
    )
    deficit = _FakeDeficitSource(deficits={_BILI: 20, _XHS: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    await planner.run_once()

    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert '"cold_start": true' in user
    assert '"prefer_axes"' in user
    assert "人工智能" in user
    assert "篮球战术" in user
    assert "电影拉片" in user


async def test_full_pool_no_deficit_zero_llm_calls(db: Database) -> None:
    """No platform has a deficit and B站 has no catalyst → nothing due → zero
    LLM calls, zero inserts."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_BILI: ["should not be used"]})
    deficit = _FakeDeficitSource(
        deficits=dict.fromkeys(_SEARCH_PLATFORMS, 0)
    )  # all zero, bili_catalyst False
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert llm.calls == []
    assert ledger == {}
    for platform in _SEARCH_PLATFORMS:
        assert _pending(db, platform, digest) == []


async def test_planner_reuses_generation_when_profile_and_pool_snapshot_match(
    db: Database,
) -> None:
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(payload={_BILI: ["露营 装备 盘点", "和田玉 鉴别 入门"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 20})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    first = await planner.run_once()
    claimed = db.claim_keywords(_BILI, 10)
    for row in claimed:
        db.mark_keyword_failed(int(row["id"]))
    second = await planner.run_once()

    assert first == {_BILI: 2}
    assert second == {_BILI: 2}
    assert len(llm.calls) == 1
    assert _pending(db, _BILI, digest) == ["露营 装备 盘点", "和田玉 鉴别 入门"]


async def test_planner_appends_due_explore_domains_to_bili_query_cache(
    db: Database,
) -> None:
    profile = _profile(("人工智能", 0.9), ("城市观察", 0.65))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(
        payload={
            _BILI: ["人工智能 盘点"],
            "explore_domains": [
                {
                    "domain": "城市声音采样",
                    "novelty_level": 0.84,
                    "queries": ["城市 声音 采样 纪录片", "街头 声音 设计 vlog"],
                }
            ],
        }
    )
    deficit = _FakeDeficitSource(
        deficits={_BILI: 40},
        explore_due_soon=True,
        covered_topic_groups=["人工智能"],
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert len(llm.calls) == 1
    user = llm.calls[0]["user"]
    assert "<explore_domains>" in user
    assert "人工智能" in user
    assert _pending(db, _BILI, digest) == ["人工智能 盘点"]
    assert _pending(db, _BILI, digest, keyword_kind="explore") == [
        "城市 声音 采样 纪录片",
        "街头 声音 设计 vlog",
    ]
    assert ledger[_BILI] == 3
    assert deficit.explore_marked == 1


async def test_planner_does_not_request_explore_domains_when_not_due(
    db: Database,
) -> None:
    profile = _profile(("人工智能", 0.9))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(
        payload={
            _BILI: ["人工智能 盘点"],
            "explore_domains": [
                {
                    "domain": "城市声音采样",
                    "novelty_level": 0.84,
                    "queries": ["城市 声音 采样 纪录片"],
                }
            ],
        }
    )
    deficit = _FakeDeficitSource(deficits={_BILI: 40}, explore_due_soon=False)
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert "<explore_domains>" not in llm.calls[0]["user"]
    assert _pending(db, _BILI, digest) == ["人工智能 盘点"]
    assert ledger[_BILI] == 1
    assert deficit.explore_marked == 0


async def test_inspiration_replace_mode_skips_merged_keyword_planner(
    db: Database,
) -> None:
    profile = _profile(("独立游戏叙事", 0.93))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "aspect_id": "interest:term-cab3f7dbd3",
                        "inspiration_id": "environmental-narrative",
                        "expansion_id": "fragmented-clues",
                        "text": "碎片化线索",
                        "relation": "mechanic",
                        "detail_axes": ["机制"],
                        "keywords": ["环境叙事 碎片化线索 复盘"],
                        "curator_decision": "keep",
                        "curator_score": 0.91,
                        "curator_reason": "具体且贴合叙事设计兴趣。",
                    }
                ]
            },
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "独立游戏叙事": [
                ExaPreviewItem(
                    title="环境叙事设计",
                    url="https://example.test/story",
                    highlights=("环境叙事",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=3,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner.run_once()

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert provider.calls == [("独立游戏叙事", 1)]
    assert _pending(db, _BILI, digest) == [
        "环境叙事 碎片化线索 复盘",
        "独立游戏叙事 碎片化线索",
    ]
    assert ledger == {_BILI: 2}


async def test_inspiration_stage_brainstorms_probe_queries_before_exa_search(
    db: Database,
) -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(
                    name="Switch 独立游戏",
                    category="游戏",
                    weight=0.95,
                    source="like",
                )
            ]
        )
    )
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "inspiration_id": "balatro",
                        "expansion_id": "switch-hidden-gems-keywords",
                        "text": "Switch 独立游戏隐藏佳作",
                        "relation": "artifact",
                        "detail_axes": ["work_entity"],
                        "platform_keywords": {_BILI: ["Switch 独立游戏 冷门佳作 盘点"]},
                        "curator_decision": "keep",
                        "curator_score": 0.91,
                    }
                ]
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Switch hidden gems list",
                    url="https://example.test/switch-cn",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_aspect_window_size=2,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=3,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner.run_once()

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert provider.calls == [("Switch 独立游戏", 1)]
    assert not any("具体案例 机制 方法 争议 深度分析" in query for query, _ in provider.calls)
    assert _pending(db, _BILI, digest) == [
        "Switch 独立游戏 冷门佳作 盘点",
        "Switch 独立游戏 Switch 独立游戏隐藏佳作",
    ]
    assert ledger == {_BILI: 2}


async def test_inspiration_stage_uses_deterministic_grounding_probes_without_brainstorm_llm(
    db: Database,
) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "inspiration_id": "balatro",
                        "expansion_id": "deterministic-probe-keyword",
                        "text": "Balatro",
                        "relation": "artifact",
                        "platform_keywords": {_BILI: ["Balatro Switch 盘点"]},
                        "curator_decision": "keep",
                    }
                ]
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Balatro Switch hidden gem",
                    url="https://example.test/balatro",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner.run_once()

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert provider.calls == [("Switch 独立游戏", 1)]
    assert _pending(db, _BILI, digest) == [
        "Balatro Switch 盘点",
        "Switch 独立游戏 Balatro",
    ]
    assert ledger == {_BILI: 2}


async def test_regular_inspiration_stage_uses_single_axis_keyword_llm_call(
    db: Database,
) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "Switch 独立游戏",
                        "axis_label": "冷门佳作",
                        "axis_kind": "community_vocab",
                        "example_terms": ["冷门佳作"],
                    }
                ],
                "keywords": [
                    {
                        "interest": "Switch 独立游戏",
                        "axis_id_or_label": "冷门佳作",
                        "platform": _BILI,
                        "core_concept": "Switch 独立游戏 冷门佳作",
                        "decoration": "盘点",
                    }
                ],
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Balatro Switch hidden gem",
                    url="https://example.test/balatro",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=1,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner._run_inspiration_stage([_BILI], profile=profile, digest=digest)

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert _pending(db, _BILI, digest) == ["Switch 独立游戏 冷门佳作 盘点"]
    assert ledger == {_BILI: 1}


async def test_shared_inspiration_stage_uses_single_axis_keyword_llm_call(
    db: Database,
) -> None:
    profile = _profile(("独立游戏叙事", 0.93))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "独立游戏叙事",
                        "axis_label": "环境叙事",
                        "axis_kind": "method",
                        "example_terms": ["环境叙事"],
                    }
                ],
                "keywords": [
                    {
                        "interest": "独立游戏叙事",
                        "axis_id_or_label": "环境叙事",
                        "platform": _BILI,
                        "core_concept": "B站 环境叙事 案例",
                    },
                    {
                        "interest": "独立游戏叙事",
                        "axis_id_or_label": "环境叙事",
                        "platform": _REDDIT,
                        "core_concept": "environmental storytelling game design",
                    },
                ],
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "独立游戏叙事": [
                ExaPreviewItem(
                    title="环境叙事设计",
                    url="https://example.test/story",
                    highlights=("environmental storytelling",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=1,
        ),
        inspiration_provider=provider,
    )

    ledger, explore_inserted = await planner._run_shared_inspiration_stage(
        [_BILI],
        explore_platforms=[_REDDIT],
        profile=profile,
        digest=digest,
    )

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert _pending(db, _BILI, digest) == ["B站 环境叙事 案例"]
    assert _pending(db, _REDDIT, digest, keyword_kind="explore") == [
        "environmental storytelling game design"
    ]
    assert ledger == {_BILI: 1, _REDDIT: 1}
    assert explore_inserted == 1


async def test_inspiration_llm_failure_uses_deterministic_candidates_without_retry(
    db: Database,
) -> None:
    profile = _profile(("AI tooling", 0.95))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(payloads=["not json"])
    provider = _FakeInspirationProvider(previews_by_query={})
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner._run_inspiration_stage(
        [_YOUTUBE, _REDDIT],
        profile=profile,
        digest=digest,
    )

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert _pending(db, _YOUTUBE, digest) == ["AI tooling"]
    assert _pending(db, _REDDIT, digest) == ["AI tooling"]
    assert ledger == {_YOUTUBE: 1, _REDDIT: 1}


async def test_chinese_interest_failure_records_script_mismatch_shortfall_without_garbage(
    db: Database,
) -> None:
    profile = _profile(("婚恋关系", 0.95))
    llm = _SequentialLLM(payloads=["not json"])
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    report = await planner.preview_inspiration_keywords(
        [_YOUTUBE, _REDDIT],
        profile=profile,
        query_kind="regular",
    )

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert report["platform_keywords"] == {_YOUTUBE: [], _REDDIT: []}
    telemetry = report["materialize_telemetry"]
    shortfalls = telemetry["coverage_shortfall"]
    assert {item["platform"] for item in shortfalls if item["reason"] == "script_mismatch"} == {
        _YOUTUBE,
        _REDDIT,
    }


async def test_preview_report_surfaces_axis_pipeline_telemetry(db: Database) -> None:
    profile = _profile(("游戏评价", 0.95))
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "游戏评价",
                        "axis_label": "机制拆解",
                        "axis_kind": "method",
                        "example_terms": ["设计理念"],
                        "evidence_refs": ["https://example.test/axis"],
                        "time_sensitive": False,
                    },
                    {
                        "interest": "游戏评价",
                        "axis_label": "社区语言",
                        "axis_kind": "community_language",
                        "example_terms": ["玩家黑话"],
                        "evidence_refs": ["https://example.test/community"],
                        "time_sensitive": False,
                    },
                ],
                "keywords": [
                    {
                        "interest": "游戏评价",
                        "axis_id_or_label": "机制拆解",
                        "platform": _BILI,
                        "core_concept": "只狼 忍义手 设计理念",
                        "decoration": "拆解",
                        "recency_sensitivity": "low",
                    },
                    {
                        "interest": "游戏评价",
                        "axis_id_or_label": "社区语言",
                        "platform": _BILI,
                        "core_concept": "只狼 玩家黑话",
                        "decoration": "讨论",
                        "recency_sensitivity": "low",
                    },
                ],
            }
        ]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    report = await planner.preview_inspiration_keywords([_BILI], profile=profile)

    assert "<allocation_targets>" in llm.calls[0]["user"]
    assert '"min_axes": 2' in llm.calls[0]["user"]
    assert report["axis_coverage"] == {
        "游戏评价": {"count": 2, "axes": ["机制拆解", "社区语言"], "platforms": [_BILI]}
    }
    assert report["soft_score_summary"]["count"] == 2
    assert report["deterministic_fill"] == 0
    assert report["coverage_shortfall"] == []
    assert report["parse_salvaged"] is False
    assert report["llm_call_failed"] is False
    assert report["repair_applied"] == {_BILI: False}
    assert all(
        item.get("reason") != "platform_style_mismatch"
        for reasons in report["rejected_reasons"].values()
        for item in reasons
    )


async def test_preview_single_axis_llm_response_fills_second_library_axis(
    db: Database,
) -> None:
    profile = _profile(("游戏评价", 0.95))
    db.upsert_inspiration_axes(
        [
            AxisRow(
                interest_label="游戏评价",
                axis_label="社区语言",
                axis_kind="community_language",
                source="test",
                example_terms=("玩家黑话",),
            )
        ],
        bump_usage=False,
    )
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "游戏评价",
                        "axis_label": "机制拆解",
                        "axis_kind": "method",
                        "example_terms": ["设计理念"],
                        "evidence_refs": ["https://example.test/axis"],
                        "time_sensitive": False,
                    }
                ],
                "keywords": [
                    {
                        "interest": "游戏评价",
                        "axis_id_or_label": "机制拆解",
                        "platform": _BILI,
                        "core_concept": "只狼 忍义手 设计理念",
                        "decoration": "拆解",
                        "recency_sensitivity": "low",
                    }
                ],
            }
        ]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    report = await planner.preview_inspiration_keywords([_BILI], profile=profile)

    assert report["axis_coverage"] == {
        "游戏评价": {"count": 2, "axes": ["机制拆解", "社区语言"], "platforms": [_BILI]}
    }
    assert report["platform_keywords"] == {
        _BILI: ["只狼 忍义手 设计理念 拆解", "游戏评价 玩家黑话"]
    }
    assert report["deterministic_fill"] == 1
    assert report["coverage_shortfall"] == []


async def test_preview_persist_axes_flag_controls_axis_writes(db: Database) -> None:
    profile = _profile(("游戏评价", 0.95))
    payload = {
        "axes": [
            {
                "interest": "游戏评价",
                "axis_label": "机制拆解",
                "axis_kind": "method",
                "example_terms": ["设计理念"],
                "evidence_refs": ["https://example.test/axis"],
                "time_sensitive": False,
            }
        ],
        "keywords": [],
    }
    planner_without_persist = _make_planner(
        db,
        llm=_SequentialLLM(payloads=[payload]),
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_max_probe_searches_per_stage=1,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    await planner_without_persist.preview_inspiration_keywords(
        [_BILI],
        profile=profile,
        persist_axes=False,
    )

    now = datetime.now(UTC)
    assert db.list_inspiration_axes(["游戏评价"], limit=10, now=now) == []

    planner_with_persist = _make_planner(
        db,
        llm=_SequentialLLM(payloads=[payload]),
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_max_probe_searches_per_stage=1,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    await planner_with_persist.preview_inspiration_keywords(
        [_BILI],
        profile=profile,
        persist_axes=True,
    )

    axes = db.list_inspiration_axes(["游戏评价"], limit=10, now=now)
    assert [axis.axis_label for axis in axes] == ["机制拆解"]
    assert axes[0].use_count == 0
    assert axes[0].last_used_at is None


async def test_preview_axis_upsert_does_not_bump_usage_fields(db: Database) -> None:
    profile = _profile(("游戏评价", 0.95))
    existing = AxisRow(
        interest_label="游戏评价",
        axis_label="机制拆解",
        axis_kind="method",
        source="test",
        example_terms=("旧术语",),
        evidence_refs=("https://example.test/old",),
        use_count=7,
        last_used_at="2026-01-01T00:00:00+00:00",
        last_refreshed_at="2026-01-01T00:00:00+00:00",
    )
    db.upsert_inspiration_axes([existing], bump_usage=False)
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "游戏评价",
                        "axis_label": "机制拆解",
                        "axis_kind": "method",
                        "example_terms": ["新术语"],
                        "evidence_refs": ["https://example.test/new"],
                        "time_sensitive": False,
                    }
                ],
                "keywords": [],
            }
        ]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_max_probe_searches_per_stage=1,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    await planner.preview_inspiration_keywords([_BILI], profile=profile, persist_axes=True)

    axes = db.list_inspiration_axes(["游戏评价"], limit=10, now=datetime.now(UTC))
    assert len(axes) == 1
    assert axes[0].use_count == 7
    assert axes[0].last_used_at == "2026-01-01T00:00:00+00:00"
    assert set(axes[0].evidence_refs) == {
        "https://example.test/old",
        "https://example.test/new",
    }


async def test_two_persisted_axis_previews_select_identical_interests(db: Database) -> None:
    profile = _profile(("游戏评价", 0.95), ("咖啡器具", 0.9))
    payload = {
        "axes": [
            {
                "interest": "游戏评价",
                "axis_label": "机制拆解",
                "axis_kind": "method",
                "example_terms": ["设计理念"],
                "evidence_refs": ["https://example.test/game"],
                "time_sensitive": False,
            },
            {
                "interest": "咖啡器具",
                "axis_label": "冲煮参数",
                "axis_kind": "method",
                "example_terms": ["研磨度"],
                "evidence_refs": ["https://example.test/coffee"],
                "time_sensitive": False,
            },
        ],
        "keywords": [],
    }
    planner = _make_planner(
        db,
        llm=_SequentialLLM(payloads=[payload, payload]),
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=2,
            inspiration_interest_sample_size=2,
            inspiration_max_probe_searches_per_stage=2,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    first = await planner.preview_inspiration_keywords([_BILI], profile=profile, persist_axes=True)
    second = await planner.preview_inspiration_keywords([_BILI], profile=profile, persist_axes=True)

    first_labels = [item["label"] for item in first["selected_secondary_interests"]]
    second_labels = [item["label"] for item in second["selected_secondary_interests"]]
    assert second_labels == first_labels


async def test_preview_and_production_share_materialize_platform_keywords_output(
    db: Database,
) -> None:
    candidates = [
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制拆解",
            platform=_BILI,
            core_concept="只狼 忍义手 设计理念",
            decoration="拆解",
            recency_sensitivity="low",
            origin="test",
        )
    ]
    allocation = {"游戏评价": AllocationTarget(platforms=(_BILI,), min_axes=1)}

    preview_keywords, _ = materialize_platform_keywords(candidates, allocation)
    production_keywords, _ = materialize_platform_keywords(candidates, allocation)

    assert [item.keyword for item in preview_keywords] == [
        item.keyword for item in production_keywords
    ]


def test_materialize_spreads_platform_cap_across_interests_before_second_axis() -> None:
    candidates = [
        MaterializeCandidate(
            interest=interest,
            axis_label=axis,
            platform=_BILI,
            core_concept=core,
            decoration="解析",
            recency_sensitivity="low",
            origin="test",
        )
        for interest, axis, core in (
            ("兴趣A", "A轴1", "A实体1"),
            ("兴趣A", "A轴2", "A实体2"),
            ("兴趣B", "B轴1", "B实体1"),
            ("兴趣B", "B轴2", "B实体2"),
        )
    ]
    allocation = {
        "兴趣A": AllocationTarget(platforms=(_BILI,), min_axes=2),
        "兴趣B": AllocationTarget(platforms=(_BILI,), min_axes=2),
    }

    realized, _telemetry = materialize_platform_keywords(
        candidates,
        allocation,
        max_keywords_per_platform=2,
    )

    assert [item.metadata["source_interest"] for item in realized] == ["兴趣A", "兴趣B"]


def test_inspiration_rejects_core_anchored_to_another_profile_interest(
    db: Database,
) -> None:
    profile = _profile(("Overlord 故事", 0.95), ("无职转生", 0.9))
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={}),
        profile=profile,
        deficit=_FakeDeficitSource(),
    )
    selected = [
        SecondaryInterest(
            interest_id="overlord",
            label="Overlord 故事",
            weight=0.95,
        )
    ]
    candidates = [
        MaterializeCandidate(
            interest="Overlord 故事",
            axis_label="下架争议",
            platform=_BILI,
            core_concept="无职转生 B站下架",
            decoration="争议 复盘",
            recency_sensitivity="high",
            origin="test",
        ),
        MaterializeCandidate(
            interest="Overlord 故事",
            axis_label="人物解析",
            platform=_BILI,
            core_concept="安兹 乌尔 恭",
            decoration="解析",
            recency_sensitivity="low",
            origin="test",
        ),
    ]

    kept, rejected = planner._inspiration_pipeline._filter_source_interest_drift(
        profile,
        selected,
        candidates,
    )

    assert [item.core_concept for item in kept] == ["安兹 乌尔 恭"]
    assert rejected == [
        {
            "keyword": "无职转生 B站下架 争议 复盘",
            "platform": _BILI,
            "reason": "source_interest_mismatch",
            "source_interest": "Overlord 故事",
            "conflicting_interest": "无职转生",
        }
    ]


def test_interest_selection_count_cools_down_previously_selected_interests() -> None:
    profile = _profile(
        ("兴趣A", 0.95),
        ("兴趣B", 0.94),
        ("兴趣C", 0.93),
    )

    window = build_like_secondary_interest_window(
        profile,
        coverage_snapshot={"兴趣A": {"interest_selection_count": 1}},
        max_interests=2,
    )

    assert [item.label for item in window] == ["兴趣B", "兴趣C"]


def test_selected_inspiration_interests_caps_selection_at_four(db: Database) -> None:
    profile = _profile(
        ("兴趣A", 0.99),
        ("兴趣B", 0.98),
        ("兴趣C", 0.97),
        ("兴趣D", 0.96),
        ("兴趣E", 0.95),
        ("兴趣F", 0.94),
    )
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={}),
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(inspiration_interest_sample_size=6),
    )

    selected = planner._selected_inspiration_interests(profile, {})

    assert [item.label for item in selected] == ["兴趣A", "兴趣B", "兴趣C", "兴趣D"]


def test_selected_inspiration_interests_ignores_preview_selection_ledger(
    db: Database,
) -> None:
    profile = _profile(
        ("兴趣A", 0.95),
        ("兴趣B", 0.94),
    )
    db.record_keyword_interest_selection(
        ["兴趣A"],
        query_kind="regular",
        selection_scope="preview",
    )
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={}),
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(inspiration_interest_sample_size=1),
    )

    selected = planner._selected_inspiration_interests(
        profile,
        db.get_keyword_interest_coverage_snapshot(selection_scope="preview"),
    )

    assert [item.label for item in selected] == ["兴趣A"]


def test_selected_inspiration_interests_penalizes_production_frequency_and_saturated_axes(
    db: Database,
) -> None:
    profile = _profile(
        ("兴趣A", 0.99),
        ("兴趣B", 0.92),
    )
    for _ in range(3):
        db.record_keyword_interest_selection(
            ["兴趣A"],
            query_kind="regular",
            selection_scope="production",
        )
    db.upsert_inspiration_axes(
        [
            AxisRow(
                interest_label="兴趣A",
                axis_label="已用轴",
                axis_kind="method",
                source="test",
                last_used_at="2999-01-01T00:00:00Z",
                last_refreshed_at="2999-01-01T00:00:00Z",
            )
        ],
        bump_usage=False,
    )
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={}),
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(inspiration_interest_sample_size=1),
    )

    selected = planner._selected_inspiration_interests(profile, {})

    assert [item.label for item in selected] == ["兴趣B"]


async def test_preview_inspiration_records_preview_interest_selection(
    db: Database,
) -> None:
    profile = _profile(
        ("兴趣A", 0.95),
        ("兴趣B", 0.94),
        ("兴趣C", 0.93),
    )
    llm = _SequentialLLM(
        payloads=[
            {
                "interest_branches": [
                    {
                        "secondary_interest": "兴趣A",
                        "branch_id": "a-method",
                        "lens_family": "method",
                        "probe_queries": ["兴趣A probe"],
                    }
                ]
            },
            {"expansions": []},
            {
                "interest_branches": [
                    {
                        "secondary_interest": "兴趣B",
                        "branch_id": "b-method",
                        "lens_family": "method",
                        "probe_queries": ["兴趣B probe"],
                    }
                ]
            },
            {"expansions": []},
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "兴趣A probe": [
                ExaPreviewItem(
                    title="兴趣A evidence",
                    url="https://example.test/a",
                    highlights=("兴趣A detail",),
                )
            ],
            "兴趣B probe": [
                ExaPreviewItem(
                    title="兴趣B evidence",
                    url="https://example.test/b",
                    highlights=("兴趣B detail",),
                )
            ],
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_interest_sample_size=1,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=1,
        ),
        inspiration_provider=provider,
    )

    first = await planner.preview_inspiration_keywords([_BILI], profile=profile)
    second = await planner.preview_inspiration_keywords([_BILI], profile=profile)

    assert [item["label"] for item in first["selected_secondary_interests"]] == ["兴趣A"]
    assert [item["label"] for item in second["selected_secondary_interests"]] == ["兴趣A"]
    production_snapshot = db.get_keyword_interest_coverage_snapshot()
    preview_snapshot = db.get_keyword_interest_coverage_snapshot(selection_scope="preview")
    assert production_snapshot.get("兴趣A", {}).get("interest_selection_count", 0) == 0
    assert preview_snapshot["兴趣A"]["interest_selection_count"] == 2


async def test_inspiration_selection_ledger_records_only_realized_interests(
    db: Database,
) -> None:
    profile = _profile(("兴趣A", 0.95), ("兴趣B", 0.94))
    llm = _SequentialLLM(
        payloads=[
            {
                "axes": [
                    {
                        "interest": "兴趣A",
                        "axis_label": "具体轴",
                        "axis_kind": "method",
                        "example_terms": ["具体案例"],
                    }
                ],
                "keywords": [
                    {
                        "interest": "兴趣A",
                        "axis_id_or_label": "具体轴",
                        "platform": _BILI,
                        "core_concept": "兴趣A 具体案例",
                        "decoration": "解析",
                    }
                ],
            }
        ]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_interest_sample_size=2,
            inspiration_max_probe_searches_per_stage=2,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    report = await planner.preview_inspiration_keywords([_BILI], profile=profile)

    assert [item["label"] for item in report["selected_secondary_interests"]] == ["兴趣A", "兴趣B"]
    assert report["realized_secondary_interests"] == ["兴趣A"]
    snapshot = db.get_keyword_interest_coverage_snapshot(selection_scope="preview")
    assert snapshot["兴趣A"]["interest_selection_count"] == 1
    assert snapshot.get("兴趣B", {}).get("interest_selection_count", 0) == 0


async def test_inspiration_stage_repairs_unparsed_brainstorm_before_fallback(
    db: Database,
) -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(
                    name="Switch 独立游戏",
                    category="游戏",
                    weight=0.95,
                    source="like",
                )
            ]
        )
    )
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "inspiration_id": "balatro",
                        "expansion_id": "repaired-keyword",
                        "text": "Balatro",
                        "relation": "artifact",
                        "platform_keywords": {_BILI: ["Balatro Switch 修复后盘点"]},
                        "curator_decision": "keep",
                    }
                ]
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Balatro Switch hidden gem",
                    url="https://example.test/balatro",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=3,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner.run_once()

    assert [call["caller"] for call in llm.calls] == ["discovery.keyword_inspiration"]
    assert provider.calls == [("Switch 独立游戏", 1)]
    assert _pending(db, _BILI, digest) == [
        "Balatro Switch 修复后盘点",
        "Switch 独立游戏 Balatro",
    ]
    assert ledger == {_BILI: 2}


async def test_inspiration_dry_run_reports_intermediate_keywords_without_inserting(
    db: Database,
) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "inspiration_id": "balatro",
                        "expansion_id": "switch-1",
                        "text": "Balatro",
                        "detail_axes": ["work_entity"],
                        "platform_keywords": {_BILI: ["Balatro Switch 玩法 盘点"]},
                        "curator_decision": "keep",
                    }
                ]
            }
        ]
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Switch hidden gems",
                    url="https://example.test/switch",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=provider,
    )

    report = await planner.preview_inspiration_keywords(
        [_BILI],
        profile=profile,
        query_kind="regular",
    )

    assert report["query_kind"] == "regular"
    assert report["selected_secondary_interests"][0]["label"] == "Switch 独立游戏"
    assert report["brainstorm_branches"][0]["branch_id"] == "grounding-switch-switch"
    assert report["grounding_records"][0]["probe_query"] == "Switch 独立游戏"
    assert report["grounding_ledger"]["searches"] == 1
    assert report["platform_keywords"] == {
        _BILI: ["Balatro Switch 玩法 盘点", "Switch 独立游戏 Balatro"]
    }
    assert report["inserted"] is False
    assert _pending(db, _BILI, digest) == []


async def test_local_grounding_dry_run_does_not_consume_external_budget(
    db: Database,
) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    llm = _SequentialLLM(
        payloads=[
            {
                "expansions": [
                    {
                        "inspiration_id": "balatro",
                        "expansion_id": "switch-1",
                        "text": "Balatro",
                        "detail_axes": ["work_entity"],
                        "platform_keywords": {_BILI: ["Balatro Switch 玩法 盘点"]},
                        "curator_decision": "keep",
                    }
                ]
            }
        ]
    )
    provider = _LocalLedgerProvider(
        previews_by_query={
            "Switch 独立游戏": [
                ExaPreviewItem(
                    title="Switch hidden gems",
                    url="https://example.test/switch",
                    highlights=("Balatro",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_replace_merged_keywords=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=1,
            inspiration_max_probe_searches_per_stage=1,
            inspiration_max_keywords_per_platform=2,
        ),
        inspiration_provider=provider,
    )

    report = await planner.preview_inspiration_keywords(
        [_BILI],
        profile=profile,
        query_kind="regular",
    )

    assert report["grounding_ledger"]["searches"] == 0
    assert report["grounding_ledger"]["local_hits"] == 1
    assert report["grounding_ledger"]["local_sources"] == {"content_cache": 1}
    assert report["grounding_ledger"]["external_searches_saved"] == 1


async def test_local_grounding_persists_grounding_source_on_keywords(
    db: Database,
) -> None:
    profile = _profile(("独立游戏叙事", 0.93))
    digest = profile_kw_digest(profile)
    llm = _SequentialLLM(
        payloads=[
            {_BILI: ["独立游戏 叙事设计"]},
            {
                "expansions": [
                    {
                        "aspect_id": "interest:term-cab3f7dbd3",
                        "inspiration_id": "environmental-narrative",
                        "expansion_id": "fragmented-clues",
                        "text": "碎片化线索",
                        "relation": "mechanic",
                        "detail_axes": ["机制", "复盘"],
                        "keywords": ["环境叙事 碎片化线索 复盘"],
                        "curator_decision": "keep",
                    }
                ]
            },
        ]
    )
    provider = _LocalLedgerProvider(
        previews_by_query={
            "独立游戏叙事": [
                ExaPreviewItem(
                    title="环境叙事设计",
                    url="https://example.test/story",
                    highlights=("环境叙事",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=3,
            inspiration_max_keywords_per_platform=3,
        ),
        inspiration_provider=provider,
    )

    await planner.run_once()

    row = db.conn.execute(
        """
        SELECT grounding_source
        FROM discovery_keywords
        WHERE keyword = ?
          AND profile_kw_digest = ?
        """,
        ("环境叙事 碎片化线索 复盘", digest),
    ).fetchone()
    assert row is not None
    assert row["grounding_source"] == "local_cache"


async def test_inspiration_stage_accepts_fenced_curator_json(
    db: Database,
) -> None:
    profile = _profile(("独立游戏叙事", 0.93))
    digest = profile_kw_digest(profile)
    llm = _RawLLM(
        content="""可以，下面是 JSON：
```json
{
  "expansions": [
    {
      "aspect_id": "interest:term-cab3f7dbd3",
      "inspiration_id": "environmental-narrative",
      "expansion_id": "fragmented-clues",
      "text": "碎片化线索",
      "relation": "case",
      "detail_axes": "案例",
      "platform_keywords": {
        "bilibili": "B站 环境叙事 案例",
        "reddit": ["environmental storytelling game design"]
      },
      "curator_decision": "keep",
      "curator_score": 0.88,
      "curator_reason": "真实模型可能包 code fence。"
    }
  ]
}
```
""",
    )
    provider = _FakeInspirationProvider(
        previews_by_query={
            "独立游戏叙事": [
                ExaPreviewItem(
                    title="环境叙事设计",
                    url="https://example.test/story",
                    highlights=("环境叙事",),
                )
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_aspect_window_size=1,
            inspiration_search_results_per_query=1,
            inspiration_max_keywords_per_platform=3,
        ),
        inspiration_provider=provider,
    )

    ledger = await planner._run_inspiration_stage(
        [_BILI, _REDDIT],
        profile=profile,
        digest=digest,
    )

    assert ledger == {_BILI: 2, _REDDIT: 1}
    assert _pending(db, _BILI, digest) == ["B站 环境叙事 案例", "独立游戏叙事 碎片化线索"]
    assert _pending(db, _REDDIT, digest) == ["environmental storytelling game design"]


async def test_planner_requires_bili_deficit_before_requesting_explore_domains(
    db: Database,
) -> None:
    profile = _profile(("人工智能", 0.9), ("城市观察", 0.65))
    digest = profile_kw_digest(profile)
    llm = _FakeLLM(
        payload={
            _XHS: ["人工智能 真实体验"],
            "explore_domains": [
                {
                    "domain": "城市声音采样",
                    "novelty_level": 0.84,
                    "queries": ["城市 声音 采样 纪录片"],
                }
            ],
        }
    )
    deficit = _FakeDeficitSource(
        deficits={_BILI: 0, _XHS: 40},
        explore_due_soon=True,
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert len(llm.calls) == 1
    assert "<explore_domains>" not in llm.calls[0]["user"]
    assert _pending(db, _BILI, digest) == []
    assert _pending(db, _XHS, digest) == ["人工智能 真实体验"]
    assert ledger == {_XHS: 1}
    assert deficit.explore_marked == 0


async def test_digest_change_expires_old_and_regenerates(db: Database) -> None:
    """The zero-hour rollback expires old-digest pending and regenerates."""
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
    planner = _make_planner(
        db,
        llm=llm,
        profile=new_profile,
        deficit=deficit,
        discovery=_discovery_cfg(keyword_digest_grace_hours=0),
    )

    await planner.run_once()

    # Old-digest pending expired (no longer pending).
    assert db.count_pending_keywords(_XHS, old_digest) == 0
    old_rows = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE keyword = '旧词1'"
    ).fetchone()
    assert str(old_rows["status"]) == "expired"
    # New keywords under the new digest.
    assert _pending(db, _XHS, new_digest) == ["新词A", "新词B"]


async def test_digest_change_keeps_disliked_query_but_respects_supply_avoid(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_profile = _profile(("露营", 0.9))
    old_digest = profile_kw_digest(old_profile)
    db.insert_pending_keywords(
        _XHS,
        ["AI 教程", "机器学习 入门", "露营路线"],
        old_digest,
    )

    new_profile = _profile(("城市规划", 0.9))
    new_profile.preferences.disliked_topics = ["AI"]
    llm = _FakeLLM(payload={_XHS: ["不应生成"]})
    planner = _make_planner(
        db,
        llm=llm,
        profile=new_profile,
        deficit=_FakeDeficitSource(deficits={_XHS: 20}),
        discovery=_discovery_cfg(kw_cache_low=1),
    )
    hints = planner._avoid_hints(new_profile)
    hints[_XHS]["avoid_topics"] = ["机器学习"]
    monkeypatch.setattr(planner, "_avoid_hints", lambda _profile: hints)

    ledger = await planner.run_once()

    assert ledger == {}
    assert llm.calls == []
    assert db.count_pending_keywords_all_digests(_XHS) == 2
    retained = db.conn.execute(
        """
        SELECT keyword, status, profile_kw_digest
        FROM discovery_keywords
        WHERE keyword IN ('AI 教程', '露营路线')
        ORDER BY keyword
        """
    ).fetchall()
    assert [str(row["keyword"]) for row in retained] == ["AI 教程", "露营路线"]
    assert all(str(row["status"]) == "pending" for row in retained)
    assert all(str(row["profile_kw_digest"]) == old_digest for row in retained)
    machine_learning = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE keyword = '机器学习 入门'"
    ).fetchone()
    assert machine_learning is not None
    assert str(machine_learning["status"]) == "expired"
    assert planner.last_digest_grace_ledger[_XHS] == {
        "current": 0,
        "reused": 2,
        "expired_aged": 0,
        "expired_blocked": 1,
        "expired_excess": 0,
    }


async def test_reused_pending_history_blocks_family_regeneration(db: Database) -> None:
    old_profile = _profile(("旧主题", 0.9))
    old_digest = profile_kw_digest(old_profile)
    db.insert_pending_keywords(_XHS, ["旧主题"], old_digest)

    new_profile = _profile(("城市观察", 0.9))
    llm = _FakeLLM(payload={_XHS: ["旧主题 解析"]})
    planner = _make_planner(
        db,
        llm=llm,
        profile=new_profile,
        deficit=_FakeDeficitSource(deficits={_XHS: 20}),
        discovery=_discovery_cfg(kw_cache_low=2, kw_cache_high=3, gen_batch=3),
    )

    ledger = await planner.run_once()

    assert len(llm.calls) == 1
    assert "旧主题" in llm.calls[0]["user"]
    assert ledger[_XHS] == 0
    assert db.count_pending_keywords_all_digests(_XHS) == 1
    assert _pending(db, _XHS, old_digest) == ["旧主题"]


async def test_reconciliation_failure_falls_back_to_hard_expiration(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_profile = _profile(("露营", 0.9))
    old_digest = profile_kw_digest(old_profile)
    db.insert_pending_keywords(_XHS, ["旧词"], old_digest)
    new_profile = _profile(("城市规划", 0.9))
    new_digest = profile_kw_digest(new_profile)
    original_reconcile = db.reconcile_pending_keyword_digests

    def fail_xhs(platform: str, *args: object, **kwargs: object) -> dict[str, int]:
        if platform == _XHS:
            raise RuntimeError("synthetic reconciliation failure")
        return original_reconcile(platform, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "reconcile_pending_keyword_digests", fail_xhs)
    llm = _FakeLLM(payload={_XHS: ["新词"]})
    planner = _make_planner(
        db,
        llm=llm,
        profile=new_profile,
        deficit=_FakeDeficitSource(deficits={_XHS: 20}),
    )

    await planner.run_once()

    assert len(llm.calls) == 1
    assert db.count_pending_keywords(_XHS, old_digest) == 0
    assert _pending(db, _XHS, new_digest) == ["新词"]
    assert _XHS not in planner._grace_inventory_ready


async def test_malformed_reconciliation_ledger_falls_back_to_hard_expiration(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_profile = _profile(("露营", 0.9))
    old_digest = profile_kw_digest(old_profile)
    db.insert_pending_keywords(_XHS, ["旧词"], old_digest)
    new_profile = _profile(("城市规划", 0.9))
    new_digest = profile_kw_digest(new_profile)
    original_reconcile = db.reconcile_pending_keyword_digests

    def malformed_xhs(platform: str, *args: object, **kwargs: object) -> object:
        if platform == _XHS:
            return None
        return original_reconcile(platform, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "reconcile_pending_keyword_digests", malformed_xhs)
    llm = _FakeLLM(payload={_XHS: ["新词"]})
    planner = _make_planner(
        db,
        llm=llm,
        profile=new_profile,
        deficit=_FakeDeficitSource(deficits={_XHS: 20}),
    )

    await planner.run_once()

    assert len(llm.calls) == 1
    assert db.count_pending_keywords(_XHS, old_digest) == 0
    assert _pending(db, _XHS, new_digest) == ["新词"]
    assert _XHS not in planner._grace_inventory_ready


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


# ── P2.2 decline vs failure ───────────────────────────────────────────────


async def test_explicit_empty_platform_declines_no_fallback(db: Database) -> None:
    """A SUCCESSFUL merged call where a platform returns an explicit ``[]`` is an
    intentional decline (P2.2): that platform gets NO interest-name fallback and
    NO pending row, while a different platform that returned words still gets
    them. The declined platform keeps its (here empty) pending for next cycle."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    # bilibili gets keywords; xiaohongshu explicitly declines with [].
    llm = _FakeLLM(payload={_BILI: ["露营 盘点", "和田玉 入门"], _XHS: []})
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # bilibili used the model output.
    assert _pending(db, _BILI, digest) == ["露营 盘点", "和田玉 入门"]
    assert ledger[_BILI] == 2
    # xiaohongshu declined → NO interest-name fallback, NO pending row.
    assert _pending(db, _XHS, digest) == []
    assert ledger[_XHS] == 0
    # Crucially the fallback interest names were NOT inserted for XHS.
    assert "露营" not in _pending(db, _XHS, digest)


async def test_declined_platform_does_not_recycle(db: Database) -> None:
    """A declined platform is left fully alone — even when it has ``used`` words
    that recycle-on-shortfall could otherwise top up, decline wins (no recycle,
    no fallback). Distinguishes decline from the sparse-profile recycle path."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # Seed a used word so a recycle WOULD be possible if the platform were not
    # declining (proves decline suppresses recycle-on-shortfall too).
    db.insert_pending_keywords(_XHS, ["老词"], digest)
    [seed] = db.claim_keywords(_XHS, 1)
    db.mark_keyword_used(int(seed["id"]))
    assert db.count_pending_keywords(_XHS, digest) == 0

    llm = _FakeLLM(payload={_XHS: []})  # explicit decline
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # Declined → still empty, the used word was NOT recycled back.
    assert _pending(db, _XHS, digest) == []
    assert ledger[_XHS] == 0


async def test_call_failure_falls_back_for_all_due_even_with_decline_shape(
    db: Database,
) -> None:
    """When the merged LLM call FAILS entirely, every due platform falls back to
    interest names — there is no 'decline' on a failed call (P2.2: decline is
    only inferred from a successful, parsed response)."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    llm = _RaisingLLM()
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33, _DOUYIN: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    assert llm.calls == ["discovery.keyword_planner"]
    # ALL three due platforms fell back (none treated as a decline).
    for platform in (_BILI, _XHS, _DOUYIN):
        assert _pending(db, platform, digest) == ["露营", "和田玉"]
        assert ledger[platform] == 2


# ── P2.3 recycle-on-shortfall ─────────────────────────────────────────────


async def test_recycle_on_shortfall_tops_up_low_non_declined_platform(db: Database) -> None:
    """A non-declined platform that produced SOME new words but whose pending is
    still below ``kw_cache_low`` is topped up from its oldest ``used`` words
    (P2.3) — no extra LLM call, conservative top-up only to the gap."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # Three used words available to recycle (oldest-first).
    db.insert_pending_keywords(_XHS, ["旧1", "旧2", "旧3"], digest)
    for row in db.claim_keywords(_XHS, 3):
        db.mark_keyword_used(int(row["id"]))
        db.increment_keyword_yield(int(row["id"]), f"content-{row['id']}")
    db.conn.execute(
        "UPDATE discovery_keywords SET used_at=datetime('now', '-72 hours') "
        "WHERE platform=? AND status='used'",
        (_XHS,),
    )
    db.conn.commit()
    assert db.count_pending_keywords(_XHS, digest) == 0

    # low=5; the model returns only 1 NEW word → pending=1 < low → recycle tops
    # up the 4-word gap from the 3 available used words (capped by availability).
    cfg = _discovery_cfg(kw_cache_low=5, kw_cache_high=30)
    llm = _FakeLLM(payload={_XHS: ["新词"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit, discovery=cfg)

    ledger = await planner.run_once()

    pending = _pending(db, _XHS, digest)
    # The new word plus the recycled used words (all 3, since 3 < the 4-word gap).
    assert "新词" in pending
    assert {"旧1", "旧2", "旧3"}.issubset(set(pending))
    # ledger counts the new insert (1) + recycled rows (3) = 4.
    assert ledger[_XHS] == 4


async def test_recent_used_keywords_are_not_recycled_on_shortfall(db: Database) -> None:
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    db.insert_pending_keywords(_XHS, ["刚搜过的词"], digest)
    [seed] = db.claim_keywords(_XHS, 1)
    db.mark_keyword_used(int(seed["id"]))

    cfg = _discovery_cfg(kw_cache_low=3, kw_cache_high=30, history_window_hours=48)
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={_XHS: ["新词"]}),
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_XHS: 33}),
        discovery=cfg,
    )

    ledger = await planner.run_once()

    assert _pending(db, _XHS, digest) == ["新词"]
    assert ledger[_XHS] == 1


async def test_generation_rejects_recent_keyword_format_variants(db: Database) -> None:
    profile = _profile(("AI Agent", 0.9))
    digest = profile_kw_digest(profile)
    db.insert_pending_keywords(_BILI, ["AI Agent 教程"], digest)
    [seed] = db.claim_keywords(_BILI, 1)
    db.mark_keyword_used(int(seed["id"]))
    llm = _FakeLLM(payload={_BILI: ["ai-agent教程", "AI Agent 工程实战"]})
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
    )

    ledger = await planner.run_once()

    assert _pending(db, _BILI, digest) == ["AI Agent 工程实战"]
    assert ledger[_BILI] == 1


async def test_generation_rejects_recent_keyword_suffix_only_variants(db: Database) -> None:
    profile = _profile(("无职转生", 0.9))
    digest = profile_kw_digest(profile)
    db.insert_pending_keywords(_BILI, ["无职转生B站下架 争议"], digest)
    [seed] = db.claim_keywords(_BILI, 1)
    db.mark_keyword_used(int(seed["id"]))
    llm = _FakeLLM(
        payload={
            _BILI: [
                "无职转生B站下架 争议 复盘",
                "无职转生 鲁迪成长线",
            ]
        }
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=_FakeDeficitSource(deficits={_BILI: 40}),
    )

    ledger = await planner.run_once()

    assert _pending(db, _BILI, digest) == ["无职转生 鲁迪成长线"]
    assert ledger[_BILI] == 1


def test_generation_cache_key_rotates_with_recent_keyword_history(db: Database) -> None:
    profile = _profile(("露营", 0.9))
    planner = _make_planner(
        db,
        llm=_FakeLLM(payload={}),
        profile=profile,
        deficit=_FakeDeficitSource(),
    )
    base = {
        "platform": _BILI,
        "need": 10,
        "recent_keywords": ["露营 装备"],
        "avoid_topics": [],
        "avoid_styles": [],
        "avoid_franchises": [],
        "prefer_axes": [],
        "cold_start": False,
        "supply_hint": [],
    }
    changed = {**base, "recent_keywords": ["露营 路线"]}

    assert planner._generation_cache_key("same-digest", [base]) != planner._generation_cache_key(
        "same-digest", [changed]
    )


async def test_no_recycle_when_pending_already_at_or_above_low(db: Database) -> None:
    """When a platform's pending is already at / above ``kw_cache_low`` after the
    insert, recycle-on-shortfall does NOT fire (it stays conservative)."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # A used word that COULD be recycled if shortfall fired.
    db.insert_pending_keywords(_XHS, ["可回收"], digest)
    [seed] = db.claim_keywords(_XHS, 1)
    db.mark_keyword_used(int(seed["id"]))

    # low=2; model returns 2 new words → pending=2 == low → no shortfall.
    cfg = _discovery_cfg(kw_cache_low=2, kw_cache_high=30)
    llm = _FakeLLM(payload={_XHS: ["新1", "新2"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit, discovery=cfg)

    ledger = await planner.run_once()

    pending = _pending(db, _XHS, digest)
    assert set(pending) == {"新1", "新2"}
    assert "可回收" not in pending  # not recycled
    assert ledger[_XHS] == 2


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
        deficits=dict.fromkeys(_SEARCH_PLATFORMS, 0),
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
        deficits=dict.fromkeys(_SEARCH_PLATFORMS, 0),
        bili_catalyst=True,
    )
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    ledger = await planner.run_once()

    # No merged generation (every due platform had need<=0).
    assert llm.calls == []
    assert ledger.get(_BILI, 0) == 0
    assert db.count_pending_keywords(_BILI, digest) == 30


async def test_sparse_profile_recycles_oldest_used(db: Database) -> None:
    """A sparse profile may reuse a proven word after the freshness cooldown."""
    profile = _profile(("露营", 0.9))
    digest = profile_kw_digest(profile)
    # Make "露营" a USED historical row (so the interest-name fallback word is
    # not new). insert → claim → mark used.
    db.insert_pending_keywords(_XHS, ["露营"], digest)
    claimed = db.claim_keywords(_XHS, 1)
    assert claimed, "expected one claimed row"
    db.mark_keyword_used(int(claimed[0]["id"]))
    db.increment_keyword_yield(int(claimed[0]["id"]), "historical-content")
    db.conn.execute(
        "UPDATE discovery_keywords SET used_at=datetime('now', '-72 hours') WHERE id=?",
        (int(claimed[0]["id"]),),
    )
    db.conn.commit()
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


# ── per-cycle observability ledger (P1.9) ─────────────────────────────────


async def test_cycle_ledger_captures_per_platform_generated_and_yield(db: Database) -> None:
    """The per-cycle ledger (P1.9) records ``{platform: {generated, yield}}`` for
    every platform generated this pass — generated counts from this pass plus
    each platform's cumulative admit-credited yield — even though the merged LLM
    call is a single ``discovery.keyword_planner`` caller (no per-platform token
    split). One platform is pre-credited with yield to prove it is surfaced."""
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    digest = profile_kw_digest(profile)
    # Seed bilibili with an already-used keyword that has produced 2 admitted
    # items, so its platform-wide yield total is non-zero going into this pass.
    db.insert_pending_keywords(_BILI, ["历史种子"], digest)
    seeded = db.claim_keywords(_BILI, 1)
    seed_id = int(seeded[0]["id"])
    db.mark_keyword_used(seed_id)
    assert db.increment_keyword_yield(seed_id, "BV_a") is True
    assert db.increment_keyword_yield(seed_id, "BV_b") is True
    assert db.keyword_yield_total(_BILI) == 2
    assert db.keyword_yield_total(_XHS) == 0

    llm = _FakeLLM(payload={_BILI: ["露营 盘点", "和田玉 入门"], _XHS: ["露营 vlog"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 40, _XHS: 33})
    # kw_cache_low=1 keeps this an observability-only assertion: the 2/1
    # generated counts already clear the watermark, so P2.3 recycle-on-shortfall
    # does not fire and the ledger reflects the raw model output.
    planner = _make_planner(
        db, llm=llm, profile=profile, deficit=deficit, discovery=_discovery_cfg(kw_cache_low=1)
    )

    generated = await planner.run_once()

    # run_once still returns the plain {platform: generated} ledger.
    assert generated[_BILI] == 2 and generated[_XHS] == 1
    # The structured per-cycle ledger carries both production and yield.
    structured = planner.last_cycle_ledger
    assert structured[_BILI] == {"generated": 2, "yield": 2}
    assert structured[_XHS] == {"generated": 1, "yield": 0}
    # Only platforms generated this cycle appear (no zero-deficit platforms).
    assert set(structured) == {_BILI, _XHS}


async def test_cycle_ledger_logs_structured_line(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    """The generation pass emits one structured ledger log line carrying the
    per-platform generated/yield counts (operator observability)."""
    import logging

    profile = _profile(("露营", 0.9))
    llm = _FakeLLM(payload={_XHS: ["露营 vlog", "露营 踩坑"]})
    deficit = _FakeDeficitSource(deficits={_XHS: 33})
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.keyword_planner"):
        await planner.run_once()

    ledger_lines = [r.getMessage() for r in caplog.records if "cycle ledger" in r.getMessage()]
    assert len(ledger_lines) == 1
    assert "xiaohongshu=generated:2/yield:0" in ledger_lines[0]


async def test_cycle_ledger_empty_when_nothing_generated(db: Database) -> None:
    """No due platforms → no generation → the ledger stays empty (no log spam)."""
    profile = _profile(("露营", 0.9))
    llm = _FakeLLM(payload={_BILI: ["unused"]})
    deficit = _FakeDeficitSource(deficits=dict.fromkeys(_SEARCH_PLATFORMS, 0))
    planner = _make_planner(db, llm=llm, profile=profile, deficit=deficit)

    await planner.run_once()

    assert planner.last_cycle_ledger == {}


# ── P3.2 dynamic cache high-water ─────────────────────────────────────────


class _YieldDB:
    """Minimal db exposing only the two aggregates ``_target_high`` reads."""

    def __init__(self, used: int, total: int) -> None:
        self._used = used
        self._total = total

    def used_keyword_count(self, platform: str) -> int:
        return self._used

    def keyword_yield_total(self, platform: str) -> int:
        return self._total


def _target_planner(used: int, total: int, deficit: int) -> KeywordPlanner:
    cfg = _discovery_cfg(kw_cache_high=30, kw_cache_low=10, fetch_batch=5)
    planner = KeywordPlanner(
        llm_service=object(),
        database=_YieldDB(used, total),  # type: ignore[arg-type]
        config=_FakeConfig(cfg),
        signal_event_threshold=6,
    )
    planner.bind_deficit_source(_FakeDeficitSource(deficits={_BILI: deficit}))
    return planner


def test_target_high_low_yield_generates_more() -> None:
    # 20 used, total yield 10 → avg 0.5; deficit 30 → ceil(30/0.5)=60 (> static 30).
    assert _target_planner(used=20, total=10, deficit=30)._target_high(_BILI) == 60


def test_target_high_high_yield_generates_fewer() -> None:
    # 20 used, total 100 → avg 5; deficit 30 → ceil(6); floor low+fetch=15 → 15 (< static 30).
    assert _target_planner(used=20, total=100, deficit=30)._target_high(_BILI) == 15


def test_target_high_cold_start_uses_static() -> None:
    # Below _DYNAMIC_MIN_SAMPLES used keywords → noisy → static kw_cache_high (30).
    assert _target_planner(used=3, total=100, deficit=30)._target_high(_BILI) == 30


def test_target_high_no_deficit_uses_static() -> None:
    assert _target_planner(used=50, total=10, deficit=0)._target_high(_BILI) == 30


def test_target_high_clamped_to_cap() -> None:
    # avg 0.05; deficit 30 → ceil(600) capped at kw_cache_high * 3 = 90.
    assert _target_planner(used=20, total=1, deficit=30)._target_high(_BILI) == 90


# ── P3.1 per-platform topic avoid ─────────────────────────────────────────


class _AvoidDB:
    """Fake db exposing what ``_avoid_hints`` reads (per-platform + global)."""

    def __init__(
        self,
        per_platform_topics: dict[str, dict[str, int]],
        global_topics: dict[str, int] | None = None,
    ) -> None:
        self._pp = per_platform_topics
        self._global = {
            "topic_group": dict(global_topics or {}),
            "style_key": {},
            "franchise_key": {},
        }

    def get_pool_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        return self._pp

    def count_pool_candidates(self) -> int:
        return 0

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        return {}

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        return self._global


def _avoid_planner(
    per_platform_topics: dict[str, dict[str, int]],
    global_topics: dict[str, int] | None = None,
) -> KeywordPlanner:
    planner = KeywordPlanner(
        llm_service=object(),
        database=_AvoidDB(per_platform_topics, global_topics),  # type: ignore[arg-type]
        config=_FakeConfig(_discovery_cfg()),
        signal_event_threshold=6,
    )
    planner.bind_deficit_source(_FakeDeficitSource())
    return planner


def test_avoid_hints_are_per_platform_for_topics() -> None:
    hints = _avoid_planner(
        {
            _BILI: {"国际局势": 40, "数码": 2},  # total 42 → thr max(5,8)=8
            _XHS: {"美妆": 30},  # total 30 → thr max(5,6)=6
        }
    )._avoid_hints()
    assert hints[_BILI]["avoid_topics"] == ["国际局势"]
    assert hints[_XHS]["avoid_topics"] == ["美妆"]
    # The fix: a topic saturated only on 小红书 is NOT avoided on B站.
    assert "美妆" not in hints[_BILI]["avoid_topics"]
    assert "数码" not in hints[_BILI]["avoid_topics"]  # below per-platform threshold


def test_avoid_hints_below_floor_falls_back_to_global() -> None:
    hints = _avoid_planner(
        {_DOUYIN: {"x": 3}},  # total 3 < floor 10 → global topic avoid
        global_topics={"全局热点": 50},  # global topic_threshold(300)=15 → avoided
    )._avoid_hints()
    assert hints[_DOUYIN]["avoid_topics"] == ["全局热点"]
    assert hints[_YOUTUBE]["avoid_topics"] == ["全局热点"]  # no own data → global too


def test_avoid_hints_use_profile_cold_start_when_pool_is_empty() -> None:
    profile = _profile(("人工智能", 0.96), ("篮球战术", 0.72), ("电影拉片", 0.68))

    hints = _avoid_planner({})._avoid_hints(profile)

    assert hints[_BILI]["cold_start"] is True
    assert hints[_BILI]["avoid_topics"] == ["人工智能"]
    assert "篮球战术" in hints[_BILI]["prefer_axes"]
    assert "电影拉片" in hints[_XHS]["prefer_axes"]


# ── P3.3 data-driven supply advantage ─────────────────────────────────────


class _SupplyDB:
    """Fake db exposing only the admitted-topic aggregate ``_supply_hints`` reads."""

    def __init__(self, admitted: dict[str, dict[str, int]]) -> None:
        self._admitted = admitted

    def get_admitted_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        return self._admitted


def _supply_planner(admitted: dict[str, dict[str, int]]) -> KeywordPlanner:
    planner = KeywordPlanner(
        llm_service=object(),
        database=_SupplyDB(admitted),  # type: ignore[arg-type]
        config=_FakeConfig(_discovery_cfg()),
        signal_event_threshold=6,
    )
    planner.bind_deficit_source(_FakeDeficitSource())
    return planner


def test_supply_hints_surface_per_platform_top_admitted_topics() -> None:
    hints = _supply_planner(
        {
            _BILI: {"学习区": 40, "梗文化": 20, "数码": 1},  # total 61 → thr max(3,6)=6
            _XHS: {"美妆": 30, "穿搭": 12},  # total 42 → thr max(3,4)=4
        }
    )._supply_hints({})
    assert hints[_BILI] == ["学习区", "梗文化"]  # 数码 (1) below threshold
    assert hints[_XHS] == ["美妆", "穿搭"]
    assert hints[_DOUYIN] == []  # no admit history → static table only


def test_supply_hints_subtract_avoid_set() -> None:
    # 学习区 is this platform's top strength AND currently saturated (in avoid).
    # It must stay only in avoid, never echoed back as a "lean in" hint.
    hints = _supply_planner(
        {_BILI: {"学习区": 40, "梗文化": 20}},
    )._supply_hints({_BILI: {"avoid_topics": ["学习区"]}})
    assert hints[_BILI] == ["梗文化"]
    assert "学习区" not in hints[_BILI]


def test_supply_hints_cold_start_below_floor_is_empty() -> None:
    # Fewer than _PER_PLATFORM_SUPPLY_FLOOR (10) admitted rows → untrusted → [].
    hints = _supply_planner({_BILI: {"学习区": 5}})._supply_hints({})
    assert hints[_BILI] == []


# ── merged ask cap + max_tokens budget ────────────────────────────────────


@dataclass
class _CaptureLLM:
    """Fake that records the user prompt + the max_tokens it was called with."""

    payload: dict[str, list[str]]
    calls: list[dict[str, str]] = field(default_factory=list)
    max_tokens_seen: list[int] = field(default_factory=list)

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
        inject_core_memory: bool = True,
    ) -> Any:
        del inject_core_memory
        self.calls.append({"user": user_input, "caller": caller})
        self.max_tokens_seen.append(max_tokens)
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False), provider="t", model="t"
        )


async def test_merged_ask_capped_at_gen_batch(db: Database) -> None:
    # Static cache target 30 > gen_batch 10: the ask shown to the model is capped
    # at gen_batch (we only keep that many per cycle; asking for the full 30 gap
    # only bloats the JSON and risks truncating the trailing platforms).
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    llm = _CaptureLLM(payload={_BILI: ["露营 装备", "和田玉 鉴别"]})
    deficit = _FakeDeficitSource(deficits={_BILI: 40})
    cfg = _discovery_cfg(kw_cache_high=30, gen_batch=10)
    await _make_planner(db, llm=llm, profile=profile, deficit=deficit, discovery=cfg).run_once()
    user = llm.calls[0]["user"]
    assert '"need": 10' in user
    assert '"need": 30' not in user
    # Small ask (10) → max_tokens floored at the 8192 default (reasoning-first
    # instances need headroom to think before the merged JSON is emitted).
    assert llm.max_tokens_seen[0] == 8192


async def test_merged_max_tokens_scales_with_total_ask(db: Database) -> None:
    # Every due platform × gen_batch(30) contributes to max_tokens, so the trailing
    # platforms in the merged JSON are never truncated onto the fallback.
    profile = _profile(("露营", 0.9), ("和田玉", 0.7))
    plats = _SEARCH_PLATFORMS
    llm = _CaptureLLM(payload={p: ["w1", "w2"] for p in plats})
    deficit = _FakeDeficitSource(deficits=dict.fromkeys(plats, 40))
    cfg = _discovery_cfg(kw_cache_high=30, gen_batch=30)
    await _make_planner(db, llm=llm, profile=profile, deficit=deficit, discovery=cfg).run_once()
    assert llm.max_tokens_seen[0] == len(plats) * 30 * 48 + 1024


# ── Phase 2 Task 3: axis backfill tick wiring + ordering regression ──────


class _RecordingDb:
    """Delegating db wrapper recording learning-tick + axis-fetch call order."""

    _SPIED = frozenset(
        {
            "backfill_inspiration_axis_yield",
            "apply_inspiration_axis_lifecycle",
            "list_inspiration_axes",
        }
    )

    def __init__(self, real: Database) -> None:
        self._real = real
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._real, name)
        if name in self._SPIED and callable(value):

            def wrapper(*args: Any, **kwargs: Any) -> Any:
                self.calls.append(name)
                return value(*args, **kwargs)

            return wrapper
        return value


def _seed_axis(db: Database, label: str, *, interest: str, refreshed_at: str) -> AxisRow:
    axis = AxisRow(
        interest_label=interest,
        axis_label=label,
        axis_kind="subgenre",
        source="external_search",
        created_at=refreshed_at,
        last_refreshed_at=refreshed_at,
    )
    db.upsert_inspiration_axes([axis], bump_usage=False)
    return axis


def _seed_consumed_keywords(
    db: Database,
    axis: AxisRow,
    *,
    count: int,
    yield_each: int,
    created_at: str = "2026-07-01 12:00:00",
) -> None:
    for index in range(count):
        db.conn.execute(
            """
            INSERT INTO discovery_keywords (
                platform, keyword, keyword_kind, profile_kw_digest,
                angle_id, angle_label, source_interest,
                inspiration_backend, status, yield_count, created_at
            )
            VALUES (?, ?, 'regular', 'digest', ?, ?, ?, 'axis_keyword', 'used', ?, ?)
            """,
            (
                _BILI,
                f"{axis.axis_label}-{index}",
                axis.axis_id,
                axis.axis_label,
                axis.interest_label,
                yield_each,
                created_at,
            ),
        )
    db.conn.commit()


def _tick_planner(spy_db: _RecordingDb, *, profile: SoulProfile) -> KeywordPlanner:
    planner = _make_planner(
        spy_db,  # type: ignore[arg-type]
        llm=_SequentialLLM(payloads=[{"axes": [], "keywords": []}] * 4),
        profile=profile,
        deficit=_FakeDeficitSource(),
        discovery=_discovery_cfg(
            inspiration_search_enabled=True,
            inspiration_search_results_per_query=1,
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )
    planner._inspiration_pipeline._clock = lambda: datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    return planner


async def test_production_stage_runs_backfill_and_lifecycle_before_axis_fetch(
    db: Database,
) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    axis = _seed_axis(
        db,
        "冷门佳作",
        interest="Switch 独立游戏",
        refreshed_at="2026-07-04T12:00:00Z",
    )
    _seed_consumed_keywords(db, axis, count=2, yield_each=1)
    spy = _RecordingDb(db)
    planner = _tick_planner(spy, profile=profile)

    await planner._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    assert spy.calls[0] == "backfill_inspiration_axis_yield"
    assert spy.calls[1] == "apply_inspiration_axis_lifecycle"
    assert "list_inspiration_axes" in spy.calls
    assert spy.calls.index("backfill_inspiration_axis_yield") < spy.calls.index(
        "list_inspiration_axes"
    )
    assert planner.last_axis_backfill["ran"] is True
    row = db.conn.execute(
        "SELECT window_uses, admissions, yield_backfilled_at "
        "FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert row["window_uses"] == 2
    assert row["admissions"] == 2
    assert row["yield_backfilled_at"]


async def test_shared_production_stage_runs_backfill_tick(db: Database) -> None:
    profile = _profile(("独立游戏叙事", 0.93))
    _seed_axis(db, "环境叙事", interest="独立游戏叙事", refreshed_at="2026-07-04T12:00:00Z")
    spy = _RecordingDb(db)
    planner = _tick_planner(spy, profile=profile)

    await planner._run_shared_inspiration_stage(
        [_BILI], explore_platforms=[_REDDIT], profile=profile, digest="d1"
    )

    assert spy.calls[0] == "backfill_inspiration_axis_yield"
    assert spy.calls[1] == "apply_inspiration_axis_lifecycle"
    assert planner.last_axis_backfill["ran"] is True


async def test_second_production_stage_within_six_hours_skips_backfill(db: Database) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    _seed_axis(db, "冷门佳作", interest="Switch 独立游戏", refreshed_at="2026-07-04T12:00:00Z")
    spy = _RecordingDb(db)
    planner = _tick_planner(spy, profile=profile)

    await planner._run_inspiration_stage([_BILI], profile=profile, digest="d1")
    assert planner.last_axis_backfill["ran"] is True
    first_backfills = spy.calls.count("backfill_inspiration_axis_yield")

    await planner._run_inspiration_stage([_BILI], profile=profile, digest="d1")

    # yield_backfilled_at was just written → the second stage is inside the 6h
    # throttle window and must skip (telemetry says so, spy confirms no call).
    assert planner.last_axis_backfill == {"ran": False, "staled": 0, "retired": 0, "purged": 0}
    assert spy.calls.count("backfill_inspiration_axis_yield") == first_backfills


async def test_preview_never_triggers_backfill_or_lifecycle(db: Database) -> None:
    profile = _profile(("Switch 独立游戏", 0.95))
    axis = _seed_axis(
        db, "冷门佳作", interest="Switch 独立游戏", refreshed_at="2026-07-04T12:00:00Z"
    )
    _seed_consumed_keywords(db, axis, count=2, yield_each=1)
    spy = _RecordingDb(db)
    planner = _tick_planner(spy, profile=profile)

    report = await planner.preview_inspiration_keywords([_BILI], profile=profile)
    report_second = await planner.preview_inspiration_keywords([_BILI], profile=profile)

    # No backfill timestamp exists (maximally stale) — preview still never ticks.
    assert "backfill_inspiration_axis_yield" not in spy.calls
    assert "apply_inspiration_axis_lifecycle" not in spy.calls
    assert report["axis_backfill"] == {"ran": False, "staled": 0, "retired": 0, "purged": 0}
    assert report_second["axis_backfill"] == report["axis_backfill"]
    row = db.conn.execute(
        "SELECT yield_backfilled_at, window_uses FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert row["yield_backfilled_at"] is None
    assert row["window_uses"] == 0


def test_backfilled_yield_reorders_axis_list_end_to_end(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    interest = "游戏评价"
    # X: older but genuinely yielding. W: fresher, consumed, zero admissions.
    # Z: unused (== prior). Y: less fresh than W, consumed, zero admissions.
    x = _seed_axis(db, "X-有产出", interest=interest, refreshed_at="2026-06-25T12:00:00Z")
    w = _seed_axis(db, "W-新鲜零产出", interest=interest, refreshed_at="2026-07-05T11:00:00Z")
    _seed_axis(db, "Z-未使用", interest=interest, refreshed_at="2026-07-05T11:00:00Z")
    y = _seed_axis(db, "Y-较旧零产出", interest=interest, refreshed_at="2026-06-30T12:00:00Z")
    _seed_consumed_keywords(db, x, count=3, yield_each=2)
    _seed_consumed_keywords(db, w, count=5, yield_each=0)
    _seed_consumed_keywords(db, y, count=5, yield_each=0)

    db.backfill_inspiration_axis_yield(window_days=30, now=now)
    axes = db.list_inspiration_axes([interest], limit=10, now=now)

    # Spec AC2: X (yield) > Z (unused == prior) > W/Y (consumed, zero
    # admissions); the freshness crossover holds — old-but-yielding X outranks
    # the much fresher zero-yield W.
    assert [a.axis_label for a in axes] == ["X-有产出", "Z-未使用", "W-新鲜零产出", "Y-较旧零产出"]


# ── Phase 2.3 Task 5: coexist explore dispatch (rich + degrade) ─────────


@dataclass
class _CoexistLLM:
    """Dispatches by caller: merged-keyword payload for discovery.keyword_planner,
    the axis-keyword payload (or a raise) for discovery.keyword_inspiration."""

    merged_payload: dict[str, object]
    inspiration_payload: dict[str, object] | None
    calls: list[dict[str, str]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str = "",
        user_input: str = "",
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
        **_: object,
    ) -> Any:
        self.calls.append({"caller": caller, "user": user_input})
        if caller == "discovery.keyword_planner":
            payload: dict[str, object] | None = self.merged_payload
        else:
            payload = self.inspiration_payload
        if payload is None:
            raise RuntimeError("inspiration llm down")
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(
            content=json.dumps(payload, ensure_ascii=False), provider="test", model="test"
        )


def _merged_explore_payload() -> dict[str, object]:
    return {
        _BILI: ["人工智能 盘点"],
        "explore_domains": [
            {
                "domain": "天文摄影",
                "novelty_level": 0.84,
                "queries": ["天文摄影 入门 盘点", "星空 拍摄 教程"],
            }
        ],
    }


def _explore_axis_payload() -> dict[str, object]:
    return {
        "axes": [
            {
                "interest": "天文摄影",
                "axis_label": "深空拍摄",
                "axis_kind": "method",
                "example_terms": ["深空拍摄"],
            }
        ],
        "keywords": [
            {
                "interest": "天文摄影",
                "axis_id_or_label": "深空拍摄",
                "platform": _BILI,
                "core_concept": "詹姆斯韦伯 深空图像",
                "decoration": "盘点",
            }
        ],
    }


async def test_coexist_explore_routes_through_rich_stage_when_due(db: Database) -> None:
    profile = _profile(("人工智能", 0.9))
    digest = profile_kw_digest(profile)
    llm = _CoexistLLM(
        merged_payload=_merged_explore_payload(), inspiration_payload=_explore_axis_payload()
    )
    deficit = _FakeDeficitSource(
        deficits={_BILI: 40}, explore_due_soon=True, covered_topic_groups=["人工智能"]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=deficit,
        discovery=_discovery_cfg(
            inspiration_search_enabled=True, inspiration_max_keywords_per_platform=1
        ),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    await planner.run_once()

    # Explore pool carries the RICH cross-domain keyword, NOT the flat domain
    # queries — the new stage handled it, not _explore_domain_queries.
    explore_pool = _pending(db, _BILI, digest, keyword_kind="explore")
    assert explore_pool == ["詹姆斯韦伯 深空图像 盘点"]
    assert "天文摄影 入门 盘点" not in explore_pool
    assert deficit.explore_marked == 1
    assert planner.last_explore_inspiration_degraded is False
    # Regular channel is unaffected.
    assert _pending(db, _BILI, digest) == ["人工智能 盘点"]


async def test_coexist_explore_not_triggered_when_not_due(db: Database) -> None:
    profile = _profile(("人工智能", 0.9))
    digest = profile_kw_digest(profile)
    llm = _CoexistLLM(
        merged_payload=_merged_explore_payload(), inspiration_payload=_explore_axis_payload()
    )
    deficit = _FakeDeficitSource(deficits={_BILI: 40}, explore_due_soon=False)
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=deficit,
        discovery=_discovery_cfg(inspiration_search_enabled=True),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    await planner.run_once()

    assert _pending(db, _BILI, digest, keyword_kind="explore") == []
    assert deficit.explore_marked == 0
    assert "<explore_domains>" not in llm.calls[0]["user"]


async def test_coexist_explore_degrades_to_flatten_never_bare(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(("人工智能", 0.9))
    digest = profile_kw_digest(profile)
    llm = _CoexistLLM(
        merged_payload=_merged_explore_payload(), inspiration_payload=_explore_axis_payload()
    )
    deficit = _FakeDeficitSource(
        deficits={_BILI: 40}, explore_due_soon=True, covered_topic_groups=[]
    )
    planner = _make_planner(
        db,
        llm=llm,
        profile=profile,
        deficit=deficit,
        discovery=_discovery_cfg(inspiration_search_enabled=True),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )

    async def _degraded_stage(
        explore_platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        explore_domains: Any,
        covered_topic_groups: Any,
    ) -> tuple[dict[str, int], dict[str, object]]:
        return {}, {"explore_degraded": True}

    monkeypatch.setattr(planner, "_run_explore_inspiration_stage", _degraded_stage)

    await planner.run_once()

    # Rich-gen degraded → fall back to the flat merged explore_domains queries so
    # the explore pool still replenishes (never bare).
    explore_pool = _pending(db, _BILI, digest, keyword_kind="explore")
    assert explore_pool == ["天文摄影 入门 盘点", "星空 拍摄 教程"]
    assert explore_pool  # non-empty
    assert deficit.explore_marked == 1
    assert planner.last_explore_inspiration_degraded is True


async def test_coexist_explore_budget_one_extra_call_when_due(db: Database) -> None:
    profile = _profile(("人工智能", 0.9))

    def _fresh() -> tuple[_CoexistLLM, _FakeDeficitSource]:
        return (
            _CoexistLLM(
                merged_payload=_merged_explore_payload(),
                inspiration_payload=_explore_axis_payload(),
            ),
            _FakeDeficitSource(deficits={_BILI: 40}, covered_topic_groups=["人工智能"]),
        )

    # Not-due cycle.
    llm_off, deficit_off = _fresh()
    deficit_off.explore_due_soon = False
    planner_off = _make_planner(
        db,
        llm=llm_off,
        profile=profile,
        deficit=deficit_off,
        discovery=_discovery_cfg(inspiration_search_enabled=True),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )
    await planner_off.run_once()

    # Due cycle (fresh db digest is the same; use a second planner + fresh fakes).
    llm_on, deficit_on = _fresh()
    deficit_on.explore_due_soon = True
    planner_on = _make_planner(
        db,
        llm=llm_on,
        profile=profile,
        deficit=deficit_on,
        discovery=_discovery_cfg(inspiration_search_enabled=True),
        inspiration_provider=_FakeInspirationProvider(previews_by_query={}),
    )
    await planner_on.run_once()

    def _by_caller(calls: list[dict[str, str]], caller: str) -> int:
        return sum(1 for c in calls if c["caller"] == caller)

    # Exactly ONE more explore rich-gen (keyword_inspiration) call when due...
    assert _by_caller(llm_on.calls, "discovery.keyword_inspiration") == (
        _by_caller(llm_off.calls, "discovery.keyword_inspiration") + 1
    )
    # ...while the regular merged channel's call count is unchanged.
    assert _by_caller(llm_on.calls, "discovery.keyword_planner") == _by_caller(
        llm_off.calls, "discovery.keyword_planner"
    )
