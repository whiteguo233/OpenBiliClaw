"""Tests for discovery inspiration probes and lateral expansion helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery import inspiration as inspiration_module
from openbiliclaw.discovery.inspiration import (
    AllocationTarget,
    ExaPreviewItem,
    InspirationSeed,
    MaterializeCandidate,
    SecondaryInterest,
    build_grounding_probes,
    build_like_secondary_interest_window,
    derive_inspiration_axis_id,
    derive_inspiration_seeds,
    is_specific,
    materialize_platform_keywords,
    platform_style_score,
    restatement_rate,
)
from openbiliclaw.soul.profile import (
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    InterestTag,
    OnionProfile,
    PreferenceLayer,
    SoulProfile,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


_BILI = "bilibili"
_GITHUB = "github"
_DIGEST = "digest-rich-profile"
_AXIS_NOW = "2026-07-05T12:00:00Z"
_AXIS_LATER = "2026-07-06T12:00:00Z"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


def _axis_row(
    axis_label: str,
    *,
    interest_label: str = "游戏评价",
    axis_kind: str = "subgenre",
    source: str = "external_search",
    axis_id: str = "",
    interest_id: str = "",
    example_terms: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
    time_sensitive: bool = False,
    freshness_ttl_days: int | None = None,
    yield_score: float = 0.0,
    admissions: int = 0,
    use_count: int = 0,
    status: str = "active",
    created_at: str = _AXIS_NOW,
    last_used_at: str | None = None,
    last_refreshed_at: str = _AXIS_NOW,
) -> object:
    return inspiration_module.AxisRow(
        interest_label=interest_label,
        interest_id=interest_id,
        axis_label=axis_label,
        axis_kind=axis_kind,
        example_terms=example_terms,
        evidence_refs=evidence_refs,
        source=source,
        time_sensitive=time_sensitive,
        freshness_ttl_days=freshness_ttl_days,
        yield_score=yield_score,
        admissions=admissions,
        use_count=use_count,
        status=status,
        created_at=created_at,
        last_used_at=last_used_at,
        last_refreshed_at=last_refreshed_at,
        axis_id=axis_id,
    )


def test_build_grounding_probes_orders_axis_and_example_terms() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(
                interest_id="interest:game-review",
                label="游戏评价",
                parent="游戏",
                weight=0.9,
            )
        ],
        [
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="机制拆解",
                axis_kind="method",
                source="test",
                example_terms=("设计理念", "耐玩"),
            ),
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="冷门佳作",
                axis_kind="anchor",
                source="test",
                example_terms=("宝藏",),
            ),
        ],
        {},
        limit=5,
    )

    assert [probe.probe_queries[0] for probe in probes] == [
        "游戏评价",
        "游戏评价 机制拆解",
        "游戏评价 设计理念",
        "游戏评价 耐玩",
        "游戏评价 冷门佳作",
    ]


def test_build_grounding_probes_dedupes_and_caps_terms() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(
                interest_id="interest:game-review",
                label="游戏评价",
                parent="游戏",
                weight=0.9,
            )
        ],
        [
            inspiration_module.AxisRow(
                interest_label="游戏评价",
                axis_label="机制拆解",
                axis_kind="method",
                source="test",
                example_terms=("机制拆解", "设计理念"),
            )
        ],
        {"游戏评价": ("设计理念", "社区黑话")},
        limit=4,
    )

    assert [probe.probe_queries[0] for probe in probes] == [
        "游戏评价",
        "游戏评价 机制拆解",
        "游戏评价 设计理念",
        "游戏评价 社区黑话",
    ]


def test_build_grounding_probes_cold_start_keeps_plain_interest_labels() -> None:
    probes = build_grounding_probes(
        [
            SecondaryInterest(interest_id="interest:a", label="兴趣A", weight=0.9),
            SecondaryInterest(interest_id="interest:b", label="兴趣B", weight=0.8),
        ],
        [],
        {},
        limit=3,
    )

    assert [probe.probe_queries[0] for probe in probes] == ["兴趣A", "兴趣B"]


def test_like_secondary_interest_window_prefers_positive_and_downweights_covered() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(
                    name="Switch 独立游戏",
                    category="游戏",
                    weight=0.95,
                    source="like",
                ),
                InterestTag(
                    name="王者荣耀匹配机制",
                    category="游戏",
                    weight=0.9,
                    source="accepted",
                ),
                InterestTag(name="AI 工具实测", category="科技", weight=0.82, source="profile"),
            ],
            disliked_topics=["AI 焦虑贩卖"],
        )
    )
    snapshot = {
        "Switch 独立游戏": {"generated_keyword_count": 30, "admitted_count": 20},
        "王者荣耀匹配机制": {"generated_keyword_count": 0, "admitted_count": 0},
    }

    interests = build_like_secondary_interest_window(
        profile,
        coverage_snapshot=snapshot,
        max_interests=2,
    )

    assert [item.label for item in interests] == ["王者荣耀匹配机制", "AI 工具实测"]
    assert all("AI 焦虑贩卖" not in item.label for item in interests)


def test_like_secondary_interest_window_uses_onion_like_specifics_before_domains() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="游戏",
                    weight=0.95,
                    source="watch history",
                    specifics=[
                        InterestSpecific(name="王者荣耀", weight=0.75),
                        InterestSpecific(name="任天堂Switch", weight=0.65),
                    ],
                ),
                InterestDomain(domain="萌宠", weight=0.6, source="watch history"),
            ],
        )
    )

    interests = build_like_secondary_interest_window(profile, max_interests=3)

    labels = [item.label for item in interests]
    assert "游戏" not in labels
    assert set(labels) == {"王者荣耀", "任天堂Switch", "萌宠"}
    assert {item.label: item.parent for item in interests} == {
        "王者荣耀": "游戏",
        "任天堂Switch": "游戏",
        "萌宠": "萌宠",
    }
    assert next(item for item in interests if item.label == "萌宠").low_specificity is True


def test_like_secondary_interest_window_spreads_initial_window_across_parents() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="游戏",
                    weight=0.95,
                    specifics=[
                        InterestSpecific(name="王者荣耀", weight=0.8),
                        InterestSpecific(name="任天堂Switch", weight=0.78),
                    ],
                ),
                InterestDomain(
                    domain="动漫",
                    weight=0.9,
                    specifics=[InterestSpecific(name="动漫新番", weight=0.4)],
                ),
                InterestDomain(
                    domain="科技",
                    weight=0.85,
                    specifics=[InterestSpecific(name="AI工具", weight=0.7)],
                ),
                InterestDomain(
                    domain="美食",
                    weight=0.8,
                    specifics=[InterestSpecific(name="美食探店", weight=0.75)],
                ),
            ],
        )
    )

    interests = build_like_secondary_interest_window(profile, max_interests=4)

    assert [item.parent for item in interests] == ["游戏", "科技", "美食", "动漫"]
    assert [item.label for item in interests] == ["王者荣耀", "AI工具", "美食探店", "动漫新番"]


def test_like_secondary_interest_window_penalizes_overcovered_candidate_pool() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="A 高覆盖兴趣", category="测试", weight=0.8, source="like"),
                InterestTag(name="B 空白兴趣", category="测试", weight=0.8, source="like"),
            ],
        )
    )
    snapshot = {
        "A 高覆盖兴趣": {
            "candidate_count": 120,
            "candidate_share": 0.45,
            "dominant_candidate_content_type_share": 0.95,
        }
    }

    interests = build_like_secondary_interest_window(
        profile,
        coverage_snapshot=snapshot,
        max_interests=1,
    )

    assert [item.label for item in interests] == ["B 空白兴趣"]


def test_inspiration_cache_tables_are_created(db: Database) -> None:
    names = {
        str(row["name"])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    assert "discovery_inspiration_probe_cache" in names
    assert "discovery_inspiration_expansion_cache" in names


def test_inspiration_axis_table_schema_and_interest_index_created(db: Database) -> None:
    columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(discovery_inspiration_axis)").fetchall()
    }
    indexes = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA index_list(discovery_inspiration_axis)").fetchall()
    }

    assert columns == {
        "axis_id",
        "interest_label",
        "interest_id",
        "axis_label",
        "axis_kind",
        "example_terms",
        "evidence_refs",
        "source",
        "time_sensitive",
        "freshness_ttl_days",
        "yield_score",
        "admissions",
        "use_count",
        "status",
        "created_at",
        "last_used_at",
        "last_refreshed_at",
        "window_uses",
        "yield_backfilled_at",
    }
    assert "idx_discovery_inspiration_axis_interest" in indexes


def test_axis_row_derives_stable_axis_id_from_normalized_axis_label() -> None:
    first = _axis_row("  Designer／Lens!! ")
    second = _axis_row("designer lens")
    different_interest = _axis_row("designer lens", interest_label="科技新闻")

    assert first.axis_id.startswith("axis:")
    assert first.axis_id == second.axis_id
    assert first.axis_id != different_interest.axis_id


def test_upsert_inspiration_axes_merges_evidence_and_respects_preview_usage(
    db: Database,
) -> None:
    inserted = _axis_row(
        "设计师视角",
        axis_kind="creator_lens",
        interest_id="interest:game-review",
        example_terms=("拆解",),
        evidence_refs=("https://example.test/a",),
        last_used_at=_AXIS_NOW,
    )

    db.upsert_inspiration_axes([inserted])
    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["last_used_at"]) == _AXIS_NOW

    preview_refresh = _axis_row(
        "设计师视角",
        axis_id=inserted.axis_id,
        axis_kind="creator_lens",
        example_terms=("设计理念",),
        evidence_refs=("https://example.test/b",),
        created_at=_AXIS_LATER,
        last_used_at=_AXIS_LATER,
        last_refreshed_at=_AXIS_LATER,
    )
    db.upsert_inspiration_axes([preview_refresh], bump_usage=False)

    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 1
    assert str(row["last_used_at"]) == _AXIS_NOW
    assert str(row["last_refreshed_at"]) == _AXIS_LATER
    assert json.loads(str(row["evidence_refs"])) == [
        "https://example.test/a",
        "https://example.test/b",
    ]

    production_refresh = _axis_row(
        "设计师视角",
        axis_id=inserted.axis_id,
        axis_kind="creator_lens",
        evidence_refs=("https://example.test/c",),
        created_at="2026-07-07T12:00:00Z",
        last_used_at="2026-07-07T12:00:00Z",
        last_refreshed_at="2026-07-07T12:00:00Z",
    )
    db.upsert_inspiration_axes([production_refresh])

    row = db.conn.execute(
        "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
        (inserted.axis_id,),
    ).fetchone()
    assert row is not None
    assert int(row["use_count"]) == 2
    assert str(row["last_used_at"]) == "2026-07-07T12:00:00Z"
    assert json.loads(str(row["evidence_refs"])) == [
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/c",
    ]


def test_upsert_inspiration_axes_marks_lowest_ranked_overflow_stale(db: Database) -> None:
    axes = [
        _axis_row(
            f"轴 {index:02d}",
            created_at=f"2026-07-{index + 1:02d}T00:00:00Z",
            last_refreshed_at=f"2026-07-{index + 1:02d}T00:00:00Z",
        )
        for index in range(17)
    ]

    db.upsert_inspiration_axes(axes, bump_usage=False)

    rows = db.conn.execute(
        """
        SELECT axis_label, status
        FROM discovery_inspiration_axis
        WHERE interest_label = ?
        ORDER BY axis_label ASC
        """,
        ("游戏评价",),
    ).fetchall()
    active_labels = [str(row["axis_label"]) for row in rows if str(row["status"]) == "active"]
    stale_labels = [str(row["axis_label"]) for row in rows if str(row["status"]) == "stale"]

    assert len(active_labels) == 16
    assert stale_labels == ["轴 00"]


def test_list_inspiration_axes_filters_and_orders_with_zero_yield_prior(
    db: Database,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    db.upsert_inspiration_axes(
        [
            _axis_row(
                "新鲜-低使用-subgenre",
                axis_kind="subgenre",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=1,
            ),
            _axis_row(
                "新鲜-低使用-event",
                axis_kind="event",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=1,
            ),
            _axis_row(
                "新鲜-高使用",
                axis_kind="method",
                last_refreshed_at="2026-07-04T12:00:00Z",
                use_count=10,
            ),
            _axis_row(
                "较旧-零收益",
                axis_kind="subgenre",
                last_refreshed_at="2026-07-01T12:00:00Z",
            ),
            _axis_row(
                "过期时效轴",
                axis_kind="event",
                time_sensitive=True,
                freshness_ttl_days=7,
                last_refreshed_at="2026-06-01T12:00:00Z",
            ),
            _axis_row(
                "手动陈旧轴",
                axis_kind="anchor",
                status="stale",
                last_refreshed_at="2026-07-05T12:00:00Z",
            ),
        ],
        bump_usage=False,
    )

    axes = db.list_inspiration_axes(["游戏评价"], limit=10, now=now)

    assert [axis.axis_label for axis in axes] == [
        "新鲜-低使用-subgenre",
        "新鲜-低使用-event",
        "新鲜-高使用",
        "较旧-零收益",
    ]
    assert all(axis.yield_score == 0.0 for axis in axes)


def test_upsert_and_list_inspiration_seeds_round_trips_json_fields(db: Database) -> None:
    db.upsert_discovery_inspiration_seed(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:indie-games",
        query_kind="regular",
        seed_query="独立游戏 叙事设计",
        inspiration_id="isaac-like-roguelite",
        source_terms=["以撒like", "房间构筑"],
        evidence_titles=["为什么肉鸽游戏会上瘾"],
        evidence_urls=["https://example.test/roguelite"],
        reason="从叙事设计横向扩展到关卡循环。",
        risk_flags=["niche"],
        probe_backend="exa",
        freshness_digest="2026-W27",
        domain_filters=["bilibili.com"],
        source_domains=["game"],
    )

    rows = db.list_discovery_inspiration_seeds(
        _BILI,
        _DIGEST,
        aspect_id="interest:indie-games",
        query_kind="regular",
    )

    assert rows == [
        {
            "platform": _BILI,
            "profile_kw_digest": _DIGEST,
            "aspect_id": "interest:indie-games",
            "query_kind": "regular",
            "probe_backend": "exa",
            "freshness_digest": "2026-W27",
            "seed_query": "独立游戏 叙事设计",
            "domain_filters": ["bilibili.com"],
            "inspiration_id": "isaac-like-roguelite",
            "source_domains": ["game"],
            "source_terms": ["以撒like", "房间构筑"],
            "evidence_titles": ["为什么肉鸽游戏会上瘾"],
            "evidence_urls": ["https://example.test/roguelite"],
            "reason": "从叙事设计横向扩展到关卡循环。",
            "risk_flags": ["niche"],
            "selected_count": 0,
            "yielded_count": 0,
        }
    ]


def test_inspiration_seed_upsert_merges_and_yield_counter_is_incremental(db: Database) -> None:
    kwargs = {
        "platform": _BILI,
        "profile_kw_digest": _DIGEST,
        "aspect_id": "interest:finance",
        "query_kind": "explore",
        "seed_query": "供应链 瓶颈",
        "inspiration_id": "photolithography",
        "source_terms": ["光刻胶"],
        "probe_backend": "exa",
        "freshness_digest": "2026-W27",
    }
    db.upsert_discovery_inspiration_seed(**kwargs)
    assert db.increment_discovery_inspiration_yield(**kwargs) is True

    updated_kwargs = {
        **kwargs,
        "source_terms": ["光刻胶", "EUV"],
        "evidence_titles": ["EUV 光刻瓶颈"],
        "reason": "更新后的证据。",
    }
    db.upsert_discovery_inspiration_seed(**updated_kwargs)

    rows = db.list_discovery_inspiration_seeds(_BILI, _DIGEST)
    assert len(rows) == 1
    assert rows[0]["source_terms"] == ["光刻胶", "EUV"]
    assert rows[0]["evidence_titles"] == ["EUV 光刻瓶颈"]
    assert rows[0]["reason"] == "更新后的证据。"
    assert rows[0]["yielded_count"] == 1


def test_upsert_expansion_and_increment_yield(db: Database) -> None:
    db.upsert_discovery_inspiration_expansion(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        inspiration_id="deckbuilder",
        expansion_id="drafting-economy",
        relation="mechanic",
        text="卡牌构筑中的经济系统",
        detail_axes=["机制", "复盘"],
        source_terms=["杀戮尖塔", "牌组曲线"],
        curator_decision="keep",
        curator_score=0.86,
        curator_reason="和用户的系统设计兴趣强相关。",
        curator_feedback="补充更具体的卡组术语。",
        risk_flags=["low_popularity"],
        status="ready",
    )

    assert (
        db.increment_discovery_inspiration_expansion_yield(
            _BILI,
            _DIGEST,
            aspect_id="interest:game-design",
            query_kind="regular",
            inspiration_id="deckbuilder",
            expansion_id="drafting-economy",
        )
        is True
    )

    rows = db.list_discovery_inspiration_expansions(
        _BILI,
        _DIGEST,
        aspect_id="interest:game-design",
        inspiration_id="deckbuilder",
    )
    assert rows == [
        {
            "platform": _BILI,
            "profile_kw_digest": _DIGEST,
            "aspect_id": "interest:game-design",
            "query_kind": "regular",
            "inspiration_id": "deckbuilder",
            "parent_expansion_id": "",
            "expansion_id": "drafting-economy",
            "hop": 1,
            "relation": "mechanic",
            "text": "卡牌构筑中的经济系统",
            "detail_axes": ["机制", "复盘"],
            "source_terms": ["杀戮尖塔", "牌组曲线"],
            "curator_decision": "keep",
            "curator_score": 0.86,
            "curator_reason": "和用户的系统设计兴趣强相关。",
            "curator_feedback": "补充更具体的卡组术语。",
            "risk_flags": ["low_popularity"],
            "status": "ready",
            "selected_count": 0,
            "realized_count": 0,
            "yielded_count": 1,
            "failed_count": 0,
        }
    ]


def test_keyword_metadata_can_be_attached_without_changing_dedupe_key(db: Database) -> None:
    inserted = db.insert_pending_keywords(
        _BILI,
        ["EUV 光刻胶 产业链", "EUV 光刻胶 产业链"],
        _DIGEST,
        keyword_kind="explore",
        metadata_by_keyword={
            "EUV 光刻胶 产业链": {
                "aspect_id": "interest:semiconductor",
                "inspiration_backend": "exa",
                "inspiration_id": "photolithography",
                "inspiration_terms": ["EUV", "光刻胶"],
                "expansion_id": "materials-bottleneck",
                "expansion_label": "材料瓶颈",
                "angle_id": "supply-chain",
                "angle_label": "供应链",
                "query_kind": "explore",
                "source_domain": "semiconductor",
                "source_interest": "产业链瓶颈",
                "generation_reason": "从搜索证据横向扩展。",
            }
        },
    )

    assert inserted == 1
    assert (
        db.insert_pending_keywords(
            _BILI,
            ["EUV 光刻胶 产业链"],
            _DIGEST,
            keyword_kind="explore",
            metadata_by_keyword={"EUV 光刻胶 产业链": {"aspect_id": "changed"}},
        )
        == 0
    )
    row = db.conn.execute(
        """
        SELECT aspect_id, inspiration_backend, inspiration_id, inspiration_terms,
               expansion_id, expansion_label, angle_id, angle_label, query_kind,
               source_domain, source_interest, generation_reason, normalized_keyword
        FROM discovery_keywords
        WHERE keyword = ?
        """,
        ("EUV 光刻胶 产业链",),
    ).fetchone()

    assert row is not None
    assert dict(row) == {
        "aspect_id": "interest:semiconductor",
        "inspiration_backend": "exa",
        "inspiration_id": "photolithography",
        "inspiration_terms": "EUV,光刻胶",
        "expansion_id": "materials-bottleneck",
        "expansion_label": "材料瓶颈",
        "angle_id": "supply-chain",
        "angle_label": "供应链",
        "query_kind": "explore",
        "source_domain": "semiconductor",
        "source_interest": "产业链瓶颈",
        "generation_reason": "从搜索证据横向扩展。",
        "normalized_keyword": "euv 光刻胶 产业链",
    }


def test_keyword_interest_coverage_snapshot_counts_keywords_and_admitted_pool(
    db: Database,
) -> None:
    inserted = db.insert_pending_keywords(
        _BILI,
        ["Switch 冷门佳作", "Switch 双人游戏"],
        _DIGEST,
        metadata_by_keyword={
            "Switch 冷门佳作": {
                "source_interest": "Switch 独立游戏",
                "inspiration_id": "switch-hidden-gems",
                "expansion_id": "hidden-gems",
            },
            "Switch 双人游戏": {
                "source_interest": "Switch 独立游戏",
                "inspiration_id": "switch-party",
                "expansion_id": "party-games",
            },
        },
    )
    assert inserted == 2
    claimed = db.claim_keywords(_BILI, 1)
    assert len(claimed) == 1
    keyword_id = int(claimed[0]["id"])
    db.mark_keyword_used(keyword_id)
    assert db.increment_keyword_yield(keyword_id, "BV_SWITCH") is True
    db.cache_content(
        "BV_SWITCH",
        title="Switch 独立游戏盘点",
        topic_group="Switch 独立游戏",
        pool_topic_label="Switch 独立游戏",
        source_platform=_BILI,
        relevance_score=0.9,
    )
    db.cache_content(
        "BV_OTHER",
        title="AI 工具",
        topic_group="AI 工具实测",
        pool_topic_label="AI 工具实测",
        source_platform=_BILI,
        relevance_score=0.9,
    )

    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert snapshot["Switch 独立游戏"]["generated_keyword_count"] == 2
    assert snapshot["Switch 独立游戏"]["selected_keyword_count"] == 1
    assert snapshot["Switch 独立游戏"]["yield_count"] == 1
    assert snapshot["Switch 独立游戏"]["admitted_count"] == 1
    assert snapshot["Switch 独立游戏"]["admitted_share"] == pytest.approx(0.5)
    assert snapshot["AI 工具实测"]["admitted_count"] == 1


def test_keyword_interest_coverage_snapshot_counts_candidate_distribution(
    db: Database,
) -> None:
    for idx, (platform, content_type, source_interest) in enumerate(
        [
            ("bilibili", "video", "Switch 独立游戏"),
            ("bilibili", "video", "Switch 独立游戏"),
            ("reddit", "thread", "Switch 独立游戏"),
            ("bilibili", "video", "AI 工具实测"),
        ],
        start=1,
    ):
        db.conn.execute(
            """
            INSERT INTO discovery_candidates (
                candidate_key,
                status,
                source_platform,
                source_strategy,
                content_type,
                content_id,
                title,
                raw_payload
            )
            VALUES (?, 'pending_eval', ?, 'search', ?, ?, ?, ?)
            """,
            (
                f"candidate:{idx}",
                platform,
                content_type,
                f"id-{idx}",
                f"title-{idx}",
                json.dumps({"source_interest": source_interest}, ensure_ascii=False),
            ),
        )
    db.conn.commit()

    snapshot = db.get_keyword_interest_coverage_snapshot()

    switch = snapshot["Switch 独立游戏"]
    assert switch["candidate_count"] == 3
    assert switch["candidate_share"] == pytest.approx(0.75)
    assert switch["dominant_candidate_platform"] == "bilibili"
    assert switch["dominant_candidate_platform_share"] == pytest.approx(2 / 3)
    assert switch["dominant_candidate_content_type"] == "video"
    assert switch["dominant_candidate_content_type_share"] == pytest.approx(2 / 3)
    assert snapshot["AI 工具实测"]["candidate_count"] == 1


def test_keyword_interest_coverage_snapshot_normalizes_interest_labels(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["AI Agent 工具", "ai agent 实战"],
        _DIGEST,
        metadata_by_keyword={
            "AI Agent 工具": {"source_interest": " AI Agent  工具 "},
            "ai agent 实战": {"source_interest": "ai agent 工具"},
        },
    )

    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert "AI Agent 工具" in snapshot
    assert "ai agent 工具" not in snapshot
    assert snapshot["AI Agent 工具"]["generated_keyword_count"] == 2


def test_migrate_keyword_interest_labels_rewrites_and_merges_snapshot_buckets(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["旧标签 玩法", "新标签 案例"],
        _DIGEST,
        metadata_by_keyword={
            "旧标签 玩法": {"source_interest": "旧标签"},
            "新标签 案例": {"source_interest": "新标签"},
        },
    )

    updated = db.migrate_keyword_interest_labels({"旧标签": "新标签"})
    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert updated == 1
    assert "旧标签" not in snapshot
    assert snapshot["新标签"]["generated_keyword_count"] == 2


def test_migrate_keyword_interest_labels_rewrites_selection_ledger(
    db: Database,
) -> None:
    db.record_keyword_interest_selection(["旧标签"], query_kind="regular")

    updated = db.migrate_keyword_interest_labels({"旧标签": "新标签"})
    snapshot = db.get_keyword_interest_coverage_snapshot()

    assert updated == 1
    assert "旧标签" not in snapshot
    assert snapshot["新标签"]["interest_selection_count"] == 1


def test_keyword_cohort_stats_compare_inspiration_and_merged_yield(
    db: Database,
) -> None:
    db.insert_pending_keywords(
        _BILI,
        ["灵感关键词", "旧流程关键词"],
        _DIGEST,
        metadata_by_keyword={
            "灵感关键词": {
                "inspiration_id": "seed-1",
                "source_interest": "Switch 独立游戏",
            }
        },
    )
    rows = db.conn.execute("SELECT id, keyword FROM discovery_keywords ORDER BY id ASC").fetchall()
    ids = {str(row["keyword"]): int(row["id"]) for row in rows}
    for keyword_id in ids.values():
        db.conn.execute(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (keyword_id,),
        )
    db.conn.execute(
        """
        INSERT INTO content_cache (
            bvid, item_key, title, relevance_score, pool_status, pool_topic_label,
            topic_group, source_platform, delight_score, source_keyword_id
        )
        VALUES
            (
                'BV_INSPIRE_1', 'bilibili:BV_INSPIRE_1', 'inspire one',
                0.9, 'fresh', '独立游戏', '游戏', 'bilibili', 0.92, ?
            ),
            (
                'BV_INSPIRE_2', 'bilibili:BV_INSPIRE_2', 'inspire two',
                0.9, 'fresh', '独立游戏', '游戏', 'bilibili', 0.88, ?
            ),
            (
                'BV_MERGED_1', 'bilibili:BV_MERGED_1', 'merged one',
                0.9, 'fresh', 'AI 工具', '科技', 'bilibili', 0.80, ?
            )
        """,
        (ids["灵感关键词"], ids["灵感关键词"], ids["旧流程关键词"]),
    )
    assert db.increment_keyword_yield(ids["灵感关键词"], "BV_INSPIRE_1")
    assert db.increment_keyword_yield(ids["灵感关键词"], "BV_INSPIRE_2")
    assert db.increment_keyword_yield(ids["旧流程关键词"], "BV_MERGED_1")

    stats = db.get_keyword_cohort_stats(window_days=14)

    assert stats["thresholds"] == {
        "min_window_days": 14,
        "min_inspiration_claimed_keywords": 200,
        "min_admissions_per_claimed_ratio": 0.8,
        "min_mean_delight_ratio": 0.95,
    }
    assert stats["cohorts"]["inspiration"]["generated_keywords"] == 1
    assert stats["cohorts"]["inspiration"]["claimed_keywords"] == 1
    assert stats["cohorts"]["inspiration"]["admissions_per_claimed_keyword"] == 2.0
    assert stats["cohorts"]["inspiration"]["mean_delight"] == pytest.approx(0.9)
    assert stats["cohorts"]["merged"]["admissions_per_claimed_keyword"] == 1.0
    for cohort in ("inspiration", "merged"):
        assert stats["cohorts"][cohort]["claim_counts_by_day"] == {}
        assert stats["cohorts"][cohort]["claim_counts_by_platform"] == {}
        assert stats["cohorts"][cohort]["claim_counts_by_source_interest"] == {}
        assert stats["cohorts"][cohort]["grounding_mix"] == {}
        assert stats["cohorts"][cohort]["duplicate_rate_by_grounding_source"] == {}
    assert stats["gate"]["verdict"] == "insufficient_sample"


def test_keyword_cohort_stats_reports_interest_selection_ledger(
    db: Database,
) -> None:
    db.record_keyword_interest_selection(
        ["兴趣A", "兴趣B"],
        query_kind="regular",
        selection_scope="production",
    )
    db.record_keyword_interest_selection(
        ["兴趣A"],
        query_kind="explore",
        selection_scope="production",
    )
    db.record_keyword_interest_selection(
        ["预览兴趣"],
        query_kind="regular",
        selection_scope="preview",
    )

    stats = db.get_keyword_cohort_stats(window_days=14)

    production = stats["interest_selection"]["production"]
    preview = stats["interest_selection"]["preview"]
    assert production["total_selected_interests"] == 3
    assert production["distinct_interests"] == 2
    assert production["by_source_interest"] == {"兴趣A": 2, "兴趣B": 1}
    assert production["by_query_kind"] == {"explore": 1, "regular": 2}
    assert isinstance(production["last_selected_at"], str)
    assert preview["total_selected_interests"] == 1
    assert preview["by_source_interest"] == {"预览兴趣": 1}


def test_record_keyword_interest_selection_prunes_old_rows(db: Database) -> None:
    db.conn.execute(
        """
        INSERT INTO discovery_interest_selection_ledger (
            source_interest, normalized_interest, query_kind, selection_scope, selected_at
        )
        VALUES ('过期兴趣', '过期兴趣', 'regular', 'production', datetime('now', '-45 days'))
        """
    )
    db.conn.commit()

    db.record_keyword_interest_selection(["新兴趣"], query_kind="regular")

    rows = db.conn.execute(
        "SELECT source_interest FROM discovery_interest_selection_ledger ORDER BY id ASC"
    ).fetchall()
    assert [str(row["source_interest"]) for row in rows] == ["新兴趣"]


def test_keyword_yield_backfills_inspiration_and_expansion_once(db: Database) -> None:
    db.upsert_discovery_inspiration_seed(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        seed_query="独立游戏叙事",
        inspiration_id="environmental-narrative",
        source_terms=["环境叙事"],
        probe_backend="exa",
    )
    db.upsert_discovery_inspiration_expansion(
        platform=_BILI,
        profile_kw_digest=_DIGEST,
        aspect_id="interest:game-design",
        query_kind="regular",
        inspiration_id="environmental-narrative",
        expansion_id="fragmented-clues",
        relation="mechanic",
        text="碎片化线索",
        status="ready",
    )
    db.insert_pending_keywords(
        _BILI,
        ["环境叙事 碎片化线索 复盘"],
        _DIGEST,
        metadata_by_keyword={
            "环境叙事 碎片化线索 复盘": {
                "aspect_id": "interest:game-design",
                "inspiration_backend": "exa",
                "inspiration_id": "environmental-narrative",
                "expansion_id": "fragmented-clues",
                "query_kind": "regular",
            }
        },
    )
    row = db.conn.execute(
        "SELECT id FROM discovery_keywords WHERE keyword = ?",
        ("环境叙事 碎片化线索 复盘",),
    ).fetchone()
    assert row is not None
    keyword_id = int(row["id"])

    assert db.increment_keyword_yield(keyword_id, "BV1") is True
    assert db.increment_keyword_yield(keyword_id, "BV1") is False

    assert db.keyword_yield_count(keyword_id) == 1
    seed = db.list_discovery_inspiration_seeds(_BILI, _DIGEST)[0]
    expansion = db.list_discovery_inspiration_expansions(_BILI, _DIGEST)[0]
    assert seed["yielded_count"] == 1
    assert expansion["yielded_count"] == 1


def test_derive_inspiration_seeds_filters_generic_preview_terms() -> None:
    seeds = derive_inspiration_seeds(
        "独立游戏 叙事设计",
        [
            ExaPreviewItem(
                title="叙事游戏如何设计碎片化线索",
                url="https://example.test/a",
                highlights=["环境叙事", "碎片化线索", "游戏"],
            ),
            ExaPreviewItem(
                title="游戏推荐榜单",
                url="https://example.test/b",
                highlights=["游戏", "推荐", "好玩"],
            ),
        ],
        max_seeds=4,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="environmental-narrative",
            source_terms=("环境叙事",),
            evidence_titles=("叙事游戏如何设计碎片化线索",),
            evidence_urls=("https://example.test/a",),
            reason="Search preview surfaced a specific adjacent term.",
        ),
        InspirationSeed(
            inspiration_id="fragmented-clues",
            source_terms=("碎片化线索",),
            evidence_titles=("叙事游戏如何设计碎片化线索",),
            evidence_urls=("https://example.test/a",),
            reason="Search preview surfaced a specific adjacent term.",
        ),
    ]


def test_derive_inspiration_seeds_uses_title_when_highlight_is_too_long() -> None:
    seeds = derive_inspiration_seeds(
        "游戏 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="游戏玩法和策略是否属于《著作权法》保护对象？-36氪",
                url="https://example.test/copyright",
                highlights=[
                    "但是，是否所有的游戏玩法都属于作品范畴并受到《著作权法》保护？"
                    "答案当然是否定的，本案中涉诉玩法共计121个，一审法院仅对其中79个进行了认定。"
                ],
            )
        ],
        max_seeds=2,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="term-9a07486e67",
            source_terms=("游戏玩法和策略是否属于《著作权法》保护对象",),
            evidence_titles=("游戏玩法和策略是否属于《著作权法》保护对象？-36氪",),
            evidence_urls=("https://example.test/copyright",),
            reason="Search preview surfaced a specific adjacent term.",
        )
    ]


def test_derive_inspiration_seeds_strips_leading_conjunctions() -> None:
    seeds = derive_inspiration_seeds(
        "游戏 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="游戏玩法和策略是否属于《著作权法》保护对象？-36氪",
                url="https://example.test/copyright",
                highlights=["以及道具搭配"],
            )
        ],
        max_seeds=1,
    )

    assert seeds[0].source_terms == ("道具搭配",)


def test_derive_inspiration_seeds_filters_markdown_table_noise_and_fragments() -> None:
    seeds = derive_inspiration_seeds(
        "独立游戏叙事 具体案例 机制 方法 争议 深度分析",
        [
            ExaPreviewItem(
                title="独立游戏叙事设计实战",
                url="https://example.test/story",
                highlights=["| --- | --- | --- |", "---", "故事是", "环境叙事"],
            )
        ],
        max_seeds=3,
    )

    assert seeds == [
        InspirationSeed(
            inspiration_id="environmental-narrative",
            source_terms=("环境叙事",),
            evidence_titles=("独立游戏叙事设计实战",),
            evidence_urls=("https://example.test/story",),
            reason="Search preview surfaced a specific adjacent term.",
        )
    ]


def test_materialize_platform_keywords_applies_only_hard_gates_and_keeps_style_mismatch() -> None:
    candidates = [
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 机制复盘",
            decoration="案例",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 机制复盘",
            decoration="案例",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="https://example.test/story",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform=_BILI,
            core_concept="独立游戏 " + "很长" * 30,
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="独立游戏叙事",
            axis_label="机制复盘",
            platform="youtube",
            core_concept="独立游戏机制复盘",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="game design",
            axis_label="community language",
            platform="reddit",
            core_concept="game design practical workflow",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"独立游戏叙事": AllocationTarget(platforms=(_BILI, "youtube"), min_axes=1)},
        max_keywords_per_platform=4,
    )

    assert [item.keyword for item in keywords] == ["独立游戏 机制复盘 案例"]
    assert telemetry["hard_gate_rejects"] == [
        {"keyword": "独立游戏 机制复盘 案例", "platform": _BILI, "reason": "duplicate_keyword"},
        {"keyword": "https://example.test/story", "platform": _BILI, "reason": "url_keyword"},
        {
            "keyword": "独立游戏 " + "很长" * 30,
            "platform": _BILI,
            "reason": "keyword_too_long",
        },
        {"keyword": "独立游戏机制复盘", "platform": "youtube", "reason": "script_mismatch"},
    ]
    assert all(
        item["reason"] != "platform_style_mismatch" for item in telemetry["hard_gate_rejects"]
    )
    assert platform_style_score("game design practical workflow", "reddit") > 0.0
    assert platform_style_score("local agent framework repository", _GITHUB) > 0.0


def test_materialize_platform_keywords_selects_coverage_before_soft_score() -> None:
    candidates = [
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform=_BILI,
            core_concept="游戏评价 机制",
            decoration="复盘",
            recency_sensitivity="low",
            origin="llm-low",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform=_BILI,
            core_concept="游戏评价 机制 解析",
            decoration="",
            recency_sensitivity="low",
            origin="llm-high",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="社区语言",
            platform=_BILI,
            core_concept="游戏评价 社区黑话",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制",
            platform="reddit",
            core_concept="game review practical analysis",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="社区语言",
            platform="reddit",
            core_concept="game review discussion terms",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"游戏评价": AllocationTarget(platforms=(_BILI, "reddit"), min_axes=2)},
        max_keywords_per_platform=2,
    )

    by_platform = {(item.metadata["source_domain"], item.keyword) for item in keywords}
    assert by_platform == {
        (_BILI, "游戏评价 机制 解析"),
        (_BILI, "游戏评价 社区黑话"),
        ("reddit", "game review practical analysis"),
        ("reddit", "game review discussion terms"),
    }
    assert telemetry["axis_coverage"]["游戏评价"] == {
        "count": 2,
        "axes": ["机制", "社区语言"],
        "platforms": [_BILI, "reddit"],
    }
    assert telemetry["soft_score_distribution"]["count"] == 5


def test_materialize_platform_keywords_deterministically_fills_thin_pool_from_axes() -> None:
    axes = [
        inspiration_module.AxisRow(
            interest_label="game design",
            axis_label="mechanics",
            axis_kind="method",
            source="test",
            example_terms=("combat tuning",),
        ),
        inspiration_module.AxisRow(
            interest_label="game design",
            axis_label="community",
            axis_kind="community_language",
            source="test",
            example_terms=("player discourse",),
        ),
    ]
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design mechanics",
            decoration="",
            recency_sensitivity="low",
            origin="llm",
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        axes=axes,
        max_keywords_per_platform=2,
    )

    assert [item.keyword for item in keywords] == [
        "game design mechanics",
        "game design player discourse",
    ]
    assert keywords[1].metadata["origin"] == "deterministic_fill"
    assert telemetry["deterministic_fill_count"] == 1
    assert telemetry["coverage_shortfall"] == []


def test_materialize_platform_keywords_reports_script_mismatch_instead_of_garbage_fill() -> None:
    axes = [
        inspiration_module.AxisRow(
            interest_label="独立游戏叙事",
            axis_label="机制",
            axis_kind="method",
            source="test",
            example_terms=("碎片化线索",),
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        [],
        {"独立游戏叙事": AllocationTarget(platforms=("youtube", "reddit"), min_axes=1)},
        axes=axes,
        max_keywords_per_platform=1,
    )

    assert keywords == []
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "独立游戏叙事",
            "platform": "youtube",
            "reason": "script_mismatch",
            "missing_axes": ["机制"],
            "missing_platforms": ["youtube"],
        },
        {
            "interest": "独立游戏叙事",
            "platform": "reddit",
            "reason": "script_mismatch",
            "missing_axes": ["机制"],
            "missing_platforms": ["reddit"],
        },
    ]


def test_materialize_platform_keywords_reports_missing_axes_for_empty_pool() -> None:
    keywords, telemetry = materialize_platform_keywords(
        [],
        {"城市声音采样": AllocationTarget(platforms=(_BILI,), min_axes=2)},
        max_keywords_per_platform=2,
    )

    assert keywords == []
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "城市声音采样",
            "platform": _BILI,
            "reason": "missing_axes",
            "missing_axes": 2,
            "missing_platforms": [_BILI],
        }
    ]


def test_materialize_platform_keywords_degenerate_single_axis_records_shortfall() -> None:
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design mechanics",
            decoration="analysis",
            recency_sensitivity="high",
            origin="llm",
        )
    ]

    keywords, telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        max_keywords_per_platform=2,
    )

    assert [item.keyword for item in keywords] == ["game design mechanics analysis"]
    assert not any(str(year) in keywords[0].keyword for year in range(2024, 2027))
    assert telemetry["coverage_shortfall"] == [
        {
            "interest": "game design",
            "platform": "youtube",
            "reason": "missing_axes",
            "missing_axes": 1,
            "missing_platforms": ["youtube"],
        }
    ]


def test_materialize_platform_keywords_appends_decoration_only_within_budget() -> None:
    candidates = [
        MaterializeCandidate(
            interest="game design",
            axis_label="mechanics",
            platform="youtube",
            core_concept="game design combat tuning",
            decoration="designer interview breakdown",
            recency_sensitivity="high",
            origin="llm",
        ),
        MaterializeCandidate(
            interest="game design",
            axis_label="community",
            platform="youtube",
            core_concept="game design community discourse near limit",
            decoration="extra detail that would exceed budget",
            recency_sensitivity="low",
            origin="llm",
        ),
    ]

    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {"game design": AllocationTarget(platforms=("youtube",), min_axes=2)},
        max_keywords_per_platform=2,
        max_keyword_chars=48,
    )

    assert [item.keyword for item in keywords] == [
        "game design combat tuning designer interview",
        "game design community discourse near limit",
    ]


# ── Phase 2 Task 1: axis_id attribution + yield backfill ────────────────


def _insert_inspiration_keyword(
    db: Database,
    *,
    keyword: str,
    angle_id: str = "",
    angle_label: str = "",
    source_interest: str = "",
    status: str = "used",
    yield_count: int = 0,
    created_at: str = "2026-07-01 12:00:00",
    platform: str = "bilibili",
) -> None:
    db.conn.execute(
        """
        INSERT INTO discovery_keywords (
            platform, keyword, keyword_kind, profile_kw_digest,
            angle_id, angle_label, source_interest,
            inspiration_backend, status, yield_count, created_at
        )
        VALUES (?, ?, 'regular', 'digest', ?, ?, ?, 'axis_keyword', ?, ?, ?)
        """,
        (
            platform,
            keyword,
            angle_id,
            angle_label,
            source_interest,
            status,
            yield_count,
            created_at,
        ),
    )
    db.conn.commit()


def _dump_axis_table(db: Database) -> list[tuple[object, ...]]:
    rows = db.conn.execute("SELECT * FROM discovery_inspiration_axis ORDER BY axis_id").fetchall()
    return [tuple(row) for row in rows]


def test_materialize_carries_axis_id_into_realized_metadata() -> None:
    candidates = [
        MaterializeCandidate(
            interest="游戏评价",
            axis_label="机制拆解",
            platform="bilibili",
            core_concept="机制拆解 设计理念",
            decoration="",
            recency_sensitivity="low",
            origin="llm_axis_keyword",
            axis_id="axis:real-id-123",
        )
    ]

    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {"游戏评价": AllocationTarget(platforms=("bilibili",), min_axes=1)},
        max_keywords_per_platform=4,
    )

    assert keywords
    assert keywords[0].metadata["axis_id"] == "axis:real-id-123"


def test_resolve_realized_axis_id_prefers_explicit_id_then_maps_then_derives() -> None:
    from openbiliclaw.runtime.keyword_planner import KeywordPlanner

    axis = _axis_row("机制拆解", interest_label="游戏评价")
    index = KeywordPlanner._build_axis_id_index([axis])

    # Explicit axis_id wins verbatim.
    assert (
        KeywordPlanner._resolve_realized_axis_id(
            raw_axis_id="axis:explicit",
            source_interest="游戏评价",
            axis_label="机制拆解",
            axis_id_index=index,
        )
        == "axis:explicit"
    )
    # An LLM ``axis_id_or_label`` that is itself a real axis id → verbatim.
    assert (
        KeywordPlanner._resolve_realized_axis_id(
            raw_axis_id="",
            source_interest="游戏评价",
            axis_label=axis.axis_id,
            axis_id_index=index,
        )
        == axis.axis_id
    )
    # A label matching an existing axis maps to that axis's id.
    assert (
        KeywordPlanner._resolve_realized_axis_id(
            raw_axis_id="",
            source_interest="游戏评价",
            axis_label="机制拆解",
            axis_id_index=index,
        )
        == axis.axis_id
    )
    # Unknown label derives the stable id from interest + label.
    assert KeywordPlanner._resolve_realized_axis_id(
        raw_axis_id="",
        source_interest="科技",
        axis_label="发布会",
        axis_id_index=index,
    ) == derive_inspiration_axis_id("科技", "发布会")


def test_inspiration_axis_yield_columns_backfilled_on_preexisting_db(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE discovery_inspiration_axis (
            axis_id            TEXT PRIMARY KEY,
            interest_label     TEXT NOT NULL,
            interest_id        TEXT,
            axis_label         TEXT NOT NULL,
            axis_kind          TEXT NOT NULL,
            example_terms      TEXT,
            evidence_refs      TEXT,
            source             TEXT NOT NULL,
            time_sensitive     INTEGER NOT NULL DEFAULT 0,
            freshness_ttl_days INTEGER,
            yield_score        REAL NOT NULL DEFAULT 0.0,
            admissions         INTEGER NOT NULL DEFAULT 0,
            use_count          INTEGER NOT NULL DEFAULT 0,
            status             TEXT NOT NULL DEFAULT 'active',
            created_at         TEXT NOT NULL,
            last_used_at       TEXT,
            last_refreshed_at  TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(discovery_inspiration_axis)").fetchall()
    }
    assert "window_uses" in columns
    assert "yield_backfilled_at" in columns


def test_backfill_sets_yield_from_admissions_and_window_uses(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    axis = _axis_row("机制拆解", interest_label="游戏评价")
    db.upsert_inspiration_axes([axis], bump_usage=False)

    for _ in range(3):
        _insert_inspiration_keyword(
            db, keyword="k", angle_id=axis.axis_id, status="used", yield_count=2
        )
    # A still-pending row is not "consumed": excluded from window_uses.
    _insert_inspiration_keyword(db, keyword="p", angle_id=axis.axis_id, status="pending")

    db.backfill_inspiration_axis_yield(window_days=30, now=now)

    row = db.conn.execute(
        "SELECT window_uses, admissions, yield_score, yield_backfilled_at "
        "FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert row["window_uses"] == 3
    assert row["admissions"] == 6
    assert row["yield_score"] == pytest.approx((6 + 0.3) / (3 + 1.0))
    assert row["yield_backfilled_at"]


def test_backfill_attributes_legacy_rows_via_derived_id(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    axis = _axis_row("发布会", interest_label="科技")
    db.upsert_inspiration_axes([axis], bump_usage=False)

    # Phase-1-era row: angle_id == angle_label (the label, not a real id).
    _insert_inspiration_keyword(
        db,
        keyword="k",
        angle_id="发布会",
        angle_label="发布会",
        source_interest="科技",
        status="used",
        yield_count=4,
    )

    db.backfill_inspiration_axis_yield(window_days=30, now=now)

    row = db.conn.execute(
        "SELECT window_uses, admissions FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert row["window_uses"] == 1
    assert row["admissions"] == 4


def test_backfill_does_not_trust_axis_prefixed_legacy_angle_id(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    # A label that merely *looks* id-shaped. The real axis id is the hash of
    # (interest, label), NOT the literal "axis:怪标签".
    axis = _axis_row("axis:怪标签", interest_label="科技")
    assert axis.axis_id != "axis:怪标签"
    db.upsert_inspiration_axes([axis], bump_usage=False)

    _insert_inspiration_keyword(
        db,
        keyword="k",
        angle_id="axis:怪标签",
        angle_label="axis:怪标签",
        source_interest="科技",
        status="used",
        yield_count=5,
    )

    db.backfill_inspiration_axis_yield(window_days=30, now=now)

    row = db.conn.execute(
        "SELECT window_uses, admissions FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    # Attributed via derive(interest, label), not via the bogus direct id.
    assert row["window_uses"] == 1
    assert row["admissions"] == 5


def test_backfill_unused_axis_gets_prior_score(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    axis = _axis_row("冷门佳作", interest_label="游戏评价", yield_score=0.9, admissions=7)
    db.upsert_inspiration_axes([axis], bump_usage=False)

    db.backfill_inspiration_axis_yield(window_days=30, now=now)

    row = db.conn.execute(
        "SELECT window_uses, admissions, yield_score, yield_backfilled_at "
        "FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    # SET semantics: zero window rows resets to prior, continuous with unused.
    assert row["window_uses"] == 0
    assert row["admissions"] == 0
    assert row["yield_score"] == pytest.approx(0.3)
    assert row["yield_backfilled_at"]


def test_backfill_is_idempotent(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    axis = _axis_row("机制拆解", interest_label="游戏评价")
    other = _axis_row("冷门佳作", interest_label="游戏评价")
    db.upsert_inspiration_axes([axis, other], bump_usage=False)
    _insert_inspiration_keyword(
        db, keyword="k", angle_id=axis.axis_id, status="used", yield_count=3
    )

    db.backfill_inspiration_axis_yield(window_days=30, now=now)
    first = _dump_axis_table(db)
    db.backfill_inspiration_axis_yield(window_days=30, now=now)
    second = _dump_axis_table(db)

    assert first == second


def test_axis_list_sort_sinks_consumed_zero_yield_below_unused(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    bad = _axis_row("坏轴-有消费零产出", interest_label="游戏评价")
    unused = _axis_row("新轴-从未消费", interest_label="游戏评价")
    db.upsert_inspiration_axes([bad, unused], bump_usage=False)

    # Bad axis: consumed 5×, near-zero yield (0.05). Unused: window_uses 0.
    db.conn.execute(
        "UPDATE discovery_inspiration_axis SET window_uses = 5, yield_score = 0.05 "
        "WHERE axis_id = ?",
        (bad.axis_id,),
    )
    db.conn.execute(
        "UPDATE discovery_inspiration_axis SET window_uses = 0, yield_score = 0.05 "
        "WHERE axis_id = ?",
        (unused.axis_id,),
    )
    db.conn.commit()

    axes = db.list_inspiration_axes(["游戏评价"], limit=10, now=now)

    # Prior floor (0.3) protects only the never-consumed axis, so it ranks first;
    # the unconditional max(yield, prior) of Phase 1 would have tied them.
    assert [a.axis_label for a in axes] == ["新轴-从未消费", "坏轴-有消费零产出"]


# ── Phase 2 Task 2: axis lifecycle (stale / retired / purge) ─────────────


def _set_axis_yield(
    db: Database,
    axis_id: str,
    *,
    window_uses: int,
    yield_score: float,
) -> None:
    db.conn.execute(
        "UPDATE discovery_inspiration_axis SET window_uses = ?, yield_score = ? WHERE axis_id = ?",
        (window_uses, yield_score, axis_id),
    )
    db.conn.commit()


def _axis_status(db: Database, axis_id: str) -> str | None:
    row = db.conn.execute(
        "SELECT status FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis_id,),
    ).fetchone()
    return None if row is None else str(row["status"])


def test_lifecycle_marks_time_expired_axes_stale(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    expired = _axis_row(
        "过期时效轴",
        time_sensitive=True,
        freshness_ttl_days=7,
        last_refreshed_at="2026-06-01T12:00:00Z",
    )
    fresh = _axis_row(
        "新鲜时效轴",
        time_sensitive=True,
        freshness_ttl_days=7,
        last_refreshed_at="2026-07-03T12:00:00Z",
    )
    evergreen = _axis_row("常青轴", last_refreshed_at="2026-01-01T12:00:00Z")
    db.upsert_inspiration_axes([expired, fresh, evergreen], bump_usage=False)

    summary = db.apply_inspiration_axis_lifecycle(now=now)

    assert summary["staled"] == 1
    assert _axis_status(db, expired.axis_id) == "stale"
    assert _axis_status(db, fresh.axis_id) == "active"
    assert _axis_status(db, evergreen.axis_id) == "active"


def test_lifecycle_retires_consumed_low_yield_axes_on_window_uses_not_use_count(
    db: Database,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    bad = _axis_row("坏轴")
    few_uses = _axis_row("消费不足轴", use_count=50)
    good_yield = _axis_row("低分但过线轴")
    db.upsert_inspiration_axes([bad, few_uses, good_yield], bump_usage=False)
    # 5 consumption chances, near-zero score → retires.
    _set_axis_yield(db, bad.axis_id, window_uses=5, yield_score=0.05)
    # Heavy selection bookkeeping (use_count=50) but only 4 real consumptions:
    # NOT retired — retirement keys on window_uses, never use_count.
    _set_axis_yield(db, few_uses.axis_id, window_uses=4, yield_score=0.05)
    # Enough uses but score at/above the line → stays active.
    _set_axis_yield(db, good_yield.axis_id, window_uses=10, yield_score=0.08)

    summary = db.apply_inspiration_axis_lifecycle(now=now)

    assert summary["retired"] == 1
    assert _axis_status(db, bad.axis_id) == "retired"
    assert _axis_status(db, few_uses.axis_id) == "active"
    assert _axis_status(db, good_yield.axis_id) == "active"


def test_lifecycle_purges_stale_and_retired_rows_older_than_90_days(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    old_stale = _axis_row("陈旧stale", status="stale", last_refreshed_at="2026-03-01T12:00:00Z")
    old_retired = _axis_row(
        "陈旧retired", status="retired", last_refreshed_at="2026-02-01T12:00:00Z"
    )
    recent_stale = _axis_row("新近stale", status="stale", last_refreshed_at="2026-06-01T12:00:00Z")
    old_active = _axis_row("陈旧active", last_refreshed_at="2026-01-01T12:00:00Z")
    db.upsert_inspiration_axes([old_stale, old_retired, recent_stale, old_active], bump_usage=False)

    summary = db.apply_inspiration_axis_lifecycle(now=now)

    assert summary["purged"] == 2
    assert _axis_status(db, old_stale.axis_id) is None
    assert _axis_status(db, old_retired.axis_id) is None
    assert _axis_status(db, recent_stale.axis_id) == "stale"
    # Purge only touches stale/retired rows — an old but active axis survives.
    assert _axis_status(db, old_active.axis_id) == "active"


def test_lifecycle_returns_full_transition_summary(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    db.upsert_inspiration_axes([_axis_row("无事发生轴")], bump_usage=False)

    summary = db.apply_inspiration_axis_lifecycle(now=now)

    assert summary == {"staled": 0, "retired": 0, "purged": 0}


def test_upsert_does_not_resurrect_retired_axis_but_revives_stale(db: Database) -> None:
    retired = _axis_row("退休轴", status="retired", evidence_refs=("old-ref",))
    stale = _axis_row("陈旧轴", status="stale", evidence_refs=("old-ref",))
    db.upsert_inspiration_axes([retired, stale], bump_usage=False)

    # LLM re-proposes both axes as fresh active rows with new evidence.
    db.upsert_inspiration_axes(
        [
            _axis_row(
                "退休轴",
                status="active",
                evidence_refs=("new-ref",),
                last_refreshed_at=_AXIS_LATER,
            ),
            _axis_row(
                "陈旧轴",
                status="active",
                evidence_refs=("new-ref",),
                last_refreshed_at=_AXIS_LATER,
            ),
        ],
        bump_usage=False,
    )

    retired_row = db.conn.execute(
        "SELECT status, evidence_refs FROM discovery_inspiration_axis WHERE axis_id = ?",
        (retired.axis_id,),
    ).fetchone()
    # Evidence merged, status pinned: no resurrection for retired.
    assert str(retired_row["status"]) == "retired"
    assert set(json.loads(str(retired_row["evidence_refs"]))) == {"old-ref", "new-ref"}
    # Deliberate asymmetry: stale MAY come back via a fresh upsert.
    assert _axis_status(db, stale.axis_id) == "active"


# ── Phase 2.1 Task 1.5: assembler specificity ordering (F1.5) ───────────


def _mc(
    *,
    interest: str,
    axis_label: str,
    platform: str,
    core_concept: str,
    decoration: str = "",
) -> MaterializeCandidate:
    return MaterializeCandidate(
        interest=interest,
        axis_label=axis_label,
        platform=platform,
        core_concept=core_concept,
        decoration=decoration,
        recency_sensitivity="low",
        origin="llm_axis_keyword",
    )


def test_is_specific_strips_spans_by_substring_including_no_space_cjk() -> None:
    # Restatements (topic name + filler) → False, spaced AND space-free.
    assert is_specific("新游推荐 盘点", interest="游戏资讯", axis_label="新游推荐") is False
    assert is_specific("新游推荐盘点", interest="游戏资讯", axis_label="新游推荐") is False
    # Real anchors survive the strip → True, spaced AND space-free (the R3 CJK
    # case a whitespace-token equality check would wrongly mark True).
    assert is_specific("游戏资讯 士官长登陆PS5", interest="游戏资讯", axis_label="新游推荐") is True
    assert is_specific("游戏资讯士官长登陆PS5", interest="游戏资讯", axis_label="新游推荐") is True
    # A bare marker word that equals the whole core_concept → empty → False.
    assert is_specific("盘点", interest="游戏资讯", axis_label="新游推荐") is False
    # Exact interest / axis restatement → False; empty → False.
    assert is_specific("游戏资讯", interest="游戏资讯", axis_label="新游推荐") is False
    assert is_specific("", interest="游戏资讯", axis_label="新游推荐") is False


def test_materialize_prefers_specific_candidate_over_generic_in_same_slot() -> None:
    interest, axis, platform = "游戏资讯", "新游推荐", "bilibili"
    candidates = [
        # Generic restatement, higher style_score (carries a 盘点 marker).
        _mc(
            interest=interest,
            axis_label=axis,
            platform=platform,
            core_concept="新游推荐",
            decoration="盘点",
        ),
        # Specific anchor, NO style marker → lower style_score.
        _mc(interest=interest, axis_label=axis, platform=platform, core_concept="士官长登陆PS5"),
    ]
    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {interest: AllocationTarget(platforms=(platform,), min_axes=1)},
        max_keywords_per_platform=1,
    )
    # is_specific outranks the higher style_score → the anchor wins the slot.
    assert [k.keyword for k in keywords] == ["士官长登陆PS5"]


def test_materialize_falls_back_to_style_when_both_candidates_specific() -> None:
    interest, axis, platform = "游戏资讯", "新游推荐", "bilibili"
    candidates = [
        _mc(
            interest=interest,
            axis_label=axis,
            platform=platform,
            core_concept="士官长登陆PS5",
            decoration="盘点",
        ),  # style marker → higher
        _mc(
            interest=interest, axis_label=axis, platform=platform, core_concept="马里奥新作"
        ),  # specific but no marker → lower style
    ]
    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {interest: AllocationTarget(platforms=(platform,), min_axes=1)},
        max_keywords_per_platform=1,
    )
    # Both specific → tie on is_specific → original style_score order decides.
    assert [k.keyword for k in keywords] == ["士官长登陆PS5 盘点"]


def test_materialize_falls_back_to_style_when_both_candidates_generic() -> None:
    interest, axis, platform = "游戏资讯", "新游推荐", "bilibili"
    candidates = [
        _mc(
            interest=interest,
            axis_label=axis,
            platform=platform,
            core_concept="新游推荐",
            decoration="盘点",
        ),  # False, style 0.25
        _mc(
            interest=interest, axis_label=axis, platform=platform, core_concept="游戏资讯"
        ),  # restates interest → False, style 0
    ]
    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {interest: AllocationTarget(platforms=(platform,), min_axes=1)},
        max_keywords_per_platform=1,
    )
    # Both generic → tie on is_specific → higher style_score wins.
    assert [k.keyword for k in keywords] == ["新游推荐 盘点"]


def _six_platform_candidates(*, specific: bool) -> list[MaterializeCandidate]:
    # Latin interest / candidates so all 6 platforms (incl. english-script
    # youtube / reddit) accept the same keyword text without script mismatch.
    interest, axis = "game news", "new game recommendation"
    platforms = ("bilibili", "xiaohongshu", "douyin", "youtube", "reddit", "zhihu")
    out: list[MaterializeCandidate] = []
    for platform in platforms:
        # Generic restatement of the axis, carries a review marker.
        out.append(
            _mc(
                interest=interest,
                axis_label=axis,
                platform=platform,
                core_concept="new game recommendation",
                decoration="review",
            )
        )
        if specific:
            out.append(
                _mc(
                    interest=interest,
                    axis_label=axis,
                    platform=platform,
                    core_concept="halo infinite ps5",
                )
            )
    return out


def test_restatement_rate_drops_below_threshold_after_specificity_sort() -> None:
    interest = "game news"
    platforms = ("bilibili", "xiaohongshu", "douyin", "youtube", "reddit", "zhihu")
    allocation = {interest: AllocationTarget(platforms=platforms, min_axes=1)}

    # Old regime: only generic (topic-restatement) candidates exist → every slot
    # is a restatement.
    generic_only, _t1 = materialize_platform_keywords(
        _six_platform_candidates(specific=False), allocation, max_keywords_per_platform=1
    )
    assert restatement_rate(generic_only) > 0.3

    # F1.5: with a specific anchor available in each slot, the assembler now
    # selects it even though the generic candidate has the higher style_score.
    with_specific, _t2 = materialize_platform_keywords(
        _six_platform_candidates(specific=True), allocation, max_keywords_per_platform=1
    )
    assert restatement_rate(with_specific) <= 0.3
    assert all(k.keyword == "halo infinite ps5" for k in with_specific)


def test_thin_evidence_does_not_fabricate_specific_candidate() -> None:
    # Evidence has no proper noun → only topic-level candidates are produced.
    interest, axis, platform = "游戏资讯", "新游推荐", "bilibili"
    candidates = [
        _mc(
            interest=interest,
            axis_label=axis,
            platform=platform,
            core_concept="新游推荐",
            decoration="盘点",
        ),
        _mc(
            interest=interest,
            axis_label=axis,
            platform=platform,
            core_concept="游戏资讯",
            decoration="速看",
        ),
    ]
    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {interest: AllocationTarget(platforms=(platform,), min_axes=1)},
        max_keywords_per_platform=1,
    )
    # The assembler picks a real (topic-level) candidate; it never invents a
    # proper noun that was not in the evidence.
    assert len(keywords) == 1
    assert keywords[0].keyword in {"新游推荐 盘点", "游戏资讯 速看"}
    assert (
        is_specific(
            keywords[0].keyword,
            keywords[0].metadata.get("source_interest"),
            keywords[0].metadata.get("axis_label"),
        )
        is False
    )
    assert restatement_rate(keywords) == 1.0


# ── Phase 2.1 Task 3: core_concept / decoration in metadata (F3) ────────


def test_realized_metadata_carries_core_concept_and_decoration() -> None:
    candidates = [
        _mc(
            interest="游戏评价",
            axis_label="机制拆解",
            platform="bilibili",
            core_concept="忍义手 设计",
            decoration="盘点",
        )
    ]
    keywords, _telemetry = materialize_platform_keywords(
        candidates,
        {"游戏评价": AllocationTarget(platforms=("bilibili",), min_axes=1)},
        max_keywords_per_platform=1,
    )

    assert keywords
    metadata = keywords[0].metadata
    # F3: source concept + decoration are carried verbatim from the candidate.
    assert metadata["core_concept"] == "忍义手 设计"
    assert metadata["decoration"] == "盘点"
    # Observation-only: the assembled keyword text is unchanged.
    assert keywords[0].keyword == "忍义手 设计 盘点"


def test_deterministic_fill_metadata_carries_template_core_and_empty_decoration() -> None:
    axis = inspiration_module.AxisRow(
        interest_label="游戏评价",
        axis_label="机制拆解",
        axis_kind="method",
        source="test",
        example_terms=("设计理念",),
    )
    keywords, telemetry = materialize_platform_keywords(
        [],
        {"游戏评价": AllocationTarget(platforms=("bilibili",), min_axes=1)},
        axes=[axis],
        max_keywords_per_platform=1,
    )

    assert telemetry["deterministic_fill_count"] >= 1
    assert keywords
    metadata = keywords[0].metadata
    assert metadata["origin"] == "deterministic_fill"
    # Deterministic fill carries its template core + empty decoration.
    assert metadata["core_concept"] == keywords[0].keyword
    assert metadata["decoration"] == ""


# ── Phase 2.3 Task 2: source-filtered axis query DAO (E5) ───────────────


def test_list_inspiration_axes_by_source_returns_explore_axis_not_findable_by_interest(
    db: Database,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    explore = _axis_row(
        "暗物质观测",
        interest_label="宇宙探索",  # cross-domain label, not a selected like interest
        source="explore",
        yield_score=0.5,
    )
    db.upsert_inspiration_axes([explore], bump_usage=False)

    by_source = db.list_inspiration_axes_by_source("explore", limit=10, now=now)
    # (a) the explore axis IS surfaced by source...
    assert [a.axis_label for a in by_source] == ["暗物质观测"]
    assert by_source[0].source == "explore"
    # ...but the interest-keyed DAO cannot find it (its label is cross-domain).
    by_interest = db.list_inspiration_axes(["宇宙探索"], limit=10, now=now)
    assert [a.axis_label for a in by_interest] == ["暗物质观测"]  # sanity: same label works
    assert db.list_inspiration_axes(["游戏评价"], limit=10, now=now) == []


def test_list_inspiration_axes_by_source_suppresses_time_expired(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    fresh = _axis_row(
        "新引擎发布",
        interest_label="宇宙探索",
        source="explore",
        yield_score=0.6,
        last_refreshed_at="2026-07-04T12:00:00Z",
    )
    expired = _axis_row(
        "过期时效轴",
        interest_label="宇宙探索",
        source="explore",
        yield_score=0.9,
        time_sensitive=True,
        freshness_ttl_days=7,
        last_refreshed_at="2026-06-01T12:00:00Z",
    )
    db.upsert_inspiration_axes([fresh, expired], bump_usage=False)

    by_source = db.list_inspiration_axes_by_source("explore", limit=10, now=now)
    # (b) the time-expired time-sensitive explore axis is suppressed (same
    # _axis_is_time_expired filter as list_inspiration_axes).
    assert [a.axis_label for a in by_source] == ["新引擎发布"]


def test_list_inspiration_axes_by_source_applies_min_yield_floor(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    high = _axis_row("高产轴", interest_label="宇宙探索", source="explore", yield_score=0.5)
    low = _axis_row("低产轴", interest_label="宇宙探索", source="explore", yield_score=0.05)
    db.upsert_inspiration_axes([high, low], bump_usage=False)

    filtered = db.list_inspiration_axes_by_source("explore", min_yield=0.1, limit=10, now=now)
    assert [a.axis_label for a in filtered] == ["高产轴"]
    # Default floor 0.0 returns both.
    assert len(db.list_inspiration_axes_by_source("explore", limit=10, now=now)) == 2


def test_list_inspiration_axes_by_source_reuses_phase2_ordering_and_limit(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    strong = _axis_row("强轴", interest_label="宇宙探索", source="explore", yield_score=0.9)
    weak = _axis_row("弱轴", interest_label="宇宙探索", source="explore", yield_score=0.4)
    db.upsert_inspiration_axes([strong, weak], bump_usage=False)

    ranked = db.list_inspiration_axes_by_source("explore", limit=10, now=now)
    # Same _axis_list_sort_key ordering: higher effective yield ranks first.
    assert [a.axis_label for a in ranked] == ["强轴", "弱轴"]
    # Bounded limit.
    assert [
        a.axis_label for a in db.list_inspiration_axes_by_source("explore", limit=1, now=now)
    ] == ["强轴"]


def test_merge_axis_into_keeps_existing_source_across_sources() -> None:
    from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

    regular_existing = _axis_row(
        "共享轴", interest_label="宇宙探索", source="external_search", example_terms=("旧证据",)
    )
    explore_new = _axis_row(
        "共享轴", interest_label="宇宙探索", source="explore", example_terms=("新证据",)
    )

    merged = InspirationKeywordPipeline._merge_axis_into(explore_new, regular_existing)
    # Cross-source rule: an explore axis merging onto a regular axis keeps the
    # existing (regular) source and identity, unioning evidence.
    assert merged.source == "external_search"
    assert merged.axis_id == regular_existing.axis_id
    assert set(merged.example_terms) == {"旧证据", "新证据"}


def test_new_cross_domain_axis_keeps_explore_source(db: Database) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    # A genuinely new cross-domain axis (no collision) stays source='explore'.
    db.upsert_inspiration_axes(
        [_axis_row("全新跨域轴", interest_label="深海生物", source="explore", yield_score=0.3)],
        bump_usage=False,
    )
    by_source = db.list_inspiration_axes_by_source("explore", limit=10, now=now)
    assert [a.source for a in by_source] == ["explore"]


# ── Phase 2.3 Task 6: comfort-zone expansion attribution (E5 closed loop) ──


def _insert_explore_keyword(
    db: Database,
    *,
    keyword: str,
    angle_id: str,
    source_interest: str,
    status: str = "used",
    yield_count: int = 0,
    created_at: str = "2026-07-01 12:00:00",
) -> None:
    # Explicitly keyword_kind='explore' — proves the backfill cohort filter
    # (which keys only on angle_id/angle_label, not keyword_kind) includes them.
    db.conn.execute(
        """
        INSERT INTO discovery_keywords (
            platform, keyword, keyword_kind, profile_kw_digest,
            angle_id, angle_label, source_interest,
            inspiration_backend, status, yield_count, created_at
        )
        VALUES ('bilibili', ?, 'explore', 'digest', ?, ?, ?, 'axis_keyword', ?, ?, ?)
        """,
        (keyword, angle_id, source_interest, source_interest, status, yield_count, created_at),
    )
    db.conn.commit()


def test_explore_axis_yield_rises_via_phase2_backfill_and_surfaces_by_source(
    db: Database,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    axis = _axis_row("深空拍摄", interest_label="天文摄影", source="explore")
    db.upsert_inspiration_axes([axis], bump_usage=False)

    # Baseline: freshly-persisted explore axis has raw yield_score 0.0.
    before = db.conn.execute(
        "SELECT yield_score FROM discovery_inspiration_axis WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert float(before["yield_score"]) == 0.0

    # Seed explore-cohort keyword history attributed to the explore axis by
    # angle_id (3 consumed rows, 2 admissions each).
    for index in range(3):
        _insert_explore_keyword(
            db,
            keyword=f"詹姆斯韦伯 深空图像 {index}",
            angle_id=axis.axis_id,
            source_interest="天文摄影",
            status="used",
            yield_count=2,
        )

    # Phase-2 backfill attributes by axis_id (source-agnostic) — no explore logic.
    db.backfill_inspiration_axis_yield(window_days=30, now=now)

    row = db.conn.execute(
        "SELECT window_uses, admissions, yield_score, source FROM discovery_inspiration_axis "
        "WHERE axis_id = ?",
        (axis.axis_id,),
    ).fetchone()
    assert row["window_uses"] == 3
    assert row["admissions"] == 6
    # yield_score rose from 0.0 → (6 + 0.3) / (3 + 1) = 1.575, well above prior.
    assert float(row["yield_score"]) == pytest.approx((6 + 0.3) / (3 + 1.0))
    assert float(row["yield_score"]) > float(before["yield_score"])
    assert str(row["source"]) == "explore"

    # The proven explore axis now surfaces as a high-yield axis by source — the
    # comfort-zone expansion mechanism Task 4 reuses on a later cycle.
    surfaced = db.list_inspiration_axes_by_source("explore", min_yield=1.0, limit=10, now=now)
    assert [a.axis_label for a in surfaced] == ["深空拍摄"]
    assert surfaced[0].axis_id == axis.axis_id
    assert surfaced[0].source == "explore"
    # min_yield floors on the backfilled raw yield_score.
    assert db.list_inspiration_axes_by_source("explore", min_yield=2.0, limit=10, now=now) == []
