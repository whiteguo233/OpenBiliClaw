"""Budgeted profile retrieval preserves source semantics and negative scopes."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from openbiliclaw.soul.profile import (
    AwarenessNote,
    ContextMode,
    InsightHypothesis,
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    InterestTag,
    OnionProfile,
    PreferenceLayer,
    SoulProfile,
    StylePreference,
)
from openbiliclaw.soul.profile_views import (
    PROFILE_CATALOG_VERSION,
    build_profile_catalog,
    resize_profile_context,
    select_profile_context,
)


def _bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _mature_profile() -> SoulProfile:
    interests = [InterestTag(f"通用兴趣{i}", "日常", 1 - i / 1000) for i in range(300)]
    interests[20] = InterestTag("洞箫修复", "乐器", 0.2, source="用户确认")
    interests[290] = InterestTag("古琴漆艺", "乐器", 0.01, source="用户确认")
    return SoulProfile(
        personality_portrait="PORTRAIT_SHOULD_NOT_BE_SENT",
        core_traits=["好奇但不喜欢夸张结论"],
        preferences=PreferenceLayer(
            interests=interests,
            favorite_up_users=["CREATOR_SHOULD_NOT_BE_SENT"],
            disliked_topics=["不要剧透，已经看完的剧集除外"],
        ),
    )


def test_catalog_has_no_old_48_or_256_cap_or_portrait_creators() -> None:
    catalog = build_profile_catalog(_mature_profile())
    assert catalog["schema_version"] == PROFILE_CATALOG_VERSION
    assert len(catalog["interests"]) == 300
    serialized = json.dumps(catalog, ensure_ascii=False)
    assert "PORTRAIT_SHOULD_NOT_BE_SENT" not in serialized
    assert "CREATOR_SHOULD_NOT_BE_SENT" not in serialized


@pytest.mark.parametrize("name", ["洞箫修复", "古琴漆艺"])
def test_low_weight_related_interest_from_middle_or_far_tail_survives(name: str) -> None:
    catalog = build_profile_catalog(_mature_profile())
    projection = select_profile_context(
        catalog, query_texts=[f"这期讲{name}的入门技巧"], max_bytes=1000
    )
    assert projection.fits
    assert name in projection.included_interest_names
    assert "通用兴趣0" in projection.included_interest_names
    assert len(projection.included_interest_names) < 300
    assert projection.used_bytes == _bytes(projection.payload)
    assert projection.payload["view_is_partial"] is True


def test_resize_only_retrieves_loaded_facts_and_preserves_unknown_conditions() -> None:
    catalog = build_profile_catalog(_mature_profile())
    loaded = select_profile_context(catalog, query_texts=("古琴漆艺",), max_bytes=1200)
    payload = loaded.payload
    payload["future_conditional_boundary"] = {"only_after": "确认作品已经看完才可讨论结局"}
    before = copy.deepcopy(payload)
    floor = resize_profile_context(payload, max_bytes=0)
    assert not floor.fits
    assert floor.included_interest_names == ()
    for field in (
        "schema_version",
        "purpose",
        "view_is_partial",
        "disliked_topics",
        "style",
        "context",
        "future_conditional_boundary",
    ):
        assert floor.payload[field] == payload[field]
    smaller = resize_profile_context(
        payload, max_bytes=floor.used_bytes + 200, query_texts=("古琴漆艺",)
    )
    assert smaller.fits
    assert "古琴漆艺" in smaller.included_interest_names
    assert smaller.used_bytes == _bytes(smaller.payload)
    assert all(item in payload["interests"] for item in smaller.payload["interests"])
    expanded = resize_profile_context(smaller.payload, max_bytes=1_000_000)
    assert set(expanded.included_interest_names) == set(smaller.included_interest_names)
    assert "洞箫修复" not in expanded.included_interest_names
    assert payload == before


def test_resize_mandatory_oversize_does_not_cut_long_chinese_conditions() -> None:
    catalog = build_profile_catalog(_mature_profile())
    loaded = select_profile_context(catalog, query_texts=("古琴漆艺",), max_bytes=1200).payload
    loaded["future_conditional_boundary"] = "完整的未来条件，不能截断或静默遗忘。" * 500
    floor = resize_profile_context(loaded, max_bytes=0)
    projection = resize_profile_context(loaded, max_bytes=floor.used_bytes - 1)
    assert not projection.fits
    assert projection.payload == floor.payload
    assert projection.used_bytes == floor.used_bytes
    assert (
        projection.payload["future_conditional_boundary"] == loaded["future_conditional_boundary"]
    )
    assert projection.payload["disliked_topics"] == loaded["disliked_topics"]
    exact = resize_profile_context(loaded, max_bytes=floor.used_bytes)
    assert exact.fits
    assert exact.payload == floor.payload


def test_resize_is_deterministic_with_context_records_and_preserves_facts() -> None:
    profile = _mature_profile()
    profile.recent_awareness = [
        AwarenessNote(observation="洞箫修复线索", source_event_ids=[1]),
        AwarenessNote(observation="古琴漆艺线索", source_event_ids=[2]),
    ]
    profile.active_insights = [
        InsightHypothesis("对乐器制作感兴趣", confidence=0.5, validated=False),
    ]
    original = select_profile_context(
        build_profile_catalog(profile), query_texts=("乐器",), max_bytes=1600
    ).payload
    one = resize_profile_context(original, max_bytes=1200, query_texts=("乐器",))
    two = resize_profile_context(one.payload, max_bytes=1200, query_texts=("乐器",))
    assert one.digest == two.digest
    assert one.payload == two.payload
    for field in ("recent_awareness", "active_insights", "speculative_interests"):
        assert all(item in original.get(field, []) for item in one.payload.get(field, []))


@pytest.mark.parametrize("schema", [None, "director-context.v1", "on-demand-profile-context.v2"])
def test_resize_rejects_legacy_or_unknown_schema(schema: str | None) -> None:
    with pytest.raises(ValueError, match="unsupported profile context schema"):
        resize_profile_context({"schema_version": schema}, max_bytes=1000)


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_resize_rejects_invalid_budget(budget) -> None:
    payload = select_profile_context(
        build_profile_catalog(_mature_profile()), query_texts=(), max_bytes=0
    ).payload
    with pytest.raises(ValueError, match="nonnegative integer"):
        resize_profile_context(payload, max_bytes=budget)


def test_onion_domain_scope_is_not_flattened_and_negatives_are_complete() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(domain="手工", specifics=[InterestSpecific("古琴漆艺", 0.1)]),
            ],
            dislikes=[
                InterestDomain(
                    domain="影视讨论，仅未看完的作品",
                    specifics=[InterestSpecific("剧情剧透", 0.99)],
                    source="明确要求",
                )
            ],
        ),
    )
    catalog = build_profile_catalog(profile)
    minimum = select_profile_context(catalog, query_texts=[], max_bytes=0)
    assert not minimum.fits
    assert "disliked_topics" not in minimum.payload
    domain = minimum.payload["disliked_domains"][0]
    assert domain["domain"] == "影视讨论，仅未看完的作品"
    assert domain["specifics"][0]["name"] == "剧情剧透"
    exact = select_profile_context(catalog, query_texts=[], max_bytes=minimum.used_bytes)
    assert exact.fits
    assert exact.payload == minimum.payload
    assert exact.included_interest_names == ()


def test_explicit_style_and_context_are_in_mandatory_floor() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            style=StylePreference(preferred_duration="短视频，但教程可以长一些"),
            context=ContextMode(session_type="通勤时不要需要盯屏幕的内容"),
        )
    )
    minimum = select_profile_context(build_profile_catalog(profile), query_texts=[], max_bytes=0)
    assert minimum.payload["style"]["preferred_duration"] == "短视频，但教程可以长一些"
    assert minimum.payload["context"]["session_type"] == "通勤时不要需要盯屏幕的内容"


def test_archived_domains_include_no_descendant_and_rejected_insight_not_revived() -> None:
    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    "archived-parent",
                    specifics=[InterestSpecific("hidden-child")],
                    state="archived",
                ),
                InterestDomain("live-parent", specifics=[InterestSpecific("live-child")]),
            ]
        ),
        active_insights=[
            InsightHypothesis(
                "REJECTED_HYPOTHESIS", confidence=1, validated=True, user_verdict="rejected"
            ),
            InsightHypothesis(
                "possible curiosity", evidence=["one click"], confidence=0.3, validated=False
            ),
            InsightHypothesis(
                "confirmed curiosity",
                evidence=["user reply"],
                confidence=0.9,
                validated=True,
                user_verdict="confirmed",
            ),
        ],
    )
    catalog = build_profile_catalog(profile)
    output = select_profile_context(catalog, query_texts=["hidden-child"], max_bytes=20000).payload
    serialized = json.dumps(output)
    assert "hidden-child" not in serialized
    assert "archived-parent" not in serialized
    assert "REJECTED_HYPOTHESIS" not in serialized
    insights = {item["hypothesis"]: item for item in output["active_insights"]}
    assert insights["possible curiosity"]["confidence"] == 0.3
    assert insights["possible curiosity"]["validated"] is False
    assert insights["confirmed curiosity"]["user_verdict"] == "confirmed"


def test_speculation_keeps_uncertainty_and_deferral_without_resurrection() -> None:
    profile = SoulProfile()
    profile._active_speculations = [  # type: ignore[attr-defined]
        {"domain": "可能喜欢古琴", "confidence": 0.2, "status": "active", "reason": "只看过一条"},
        {"domain": "REJECTED", "status": "rejected"},
        {"domain": "DEFERRED", "status": "deferred"},
    ]
    output = select_profile_context(
        build_profile_catalog(profile), query_texts=[], max_bytes=5000
    ).payload
    assert output["speculative_interests"] == [
        {"domain": "可能喜欢古琴", "confidence": 0.2, "status": "active", "reason": "只看过一条"}
    ]


def test_long_chinese_facts_are_omitted_whole_but_negative_overflow_fails() -> None:
    long_condition = "如果是已经看完的剧集才可以讲结局。" * 1000
    profile = SoulProfile(
        core_traits=[long_condition],
        preferences=PreferenceLayer(interests=[InterestTag("古琴", "手工", 0.5)]),
    )
    catalog = build_profile_catalog(profile)
    small = select_profile_context(catalog, query_texts=["古琴"], max_bytes=1000)
    large = select_profile_context(catalog, query_texts=["古琴"], max_bytes=100000)
    assert small.fits and "core_traits" not in small.payload
    assert large.payload["core_traits"] == [long_condition]
    assert small.used_bytes == _bytes(small.payload)
    assert large.used_bytes == _bytes(large.payload)
    profile.preferences.disliked_topics = [long_condition]
    blocked = select_profile_context(build_profile_catalog(profile), query_texts=[], max_bytes=1000)
    assert not blocked.fits
    assert blocked.payload["disliked_topics"] == [long_condition]
    assert blocked.included_interest_names == ()


def test_projection_is_deterministic_detached_and_no_input_mutation() -> None:
    profile = _mature_profile()
    profile.recent_awareness = [AwarenessNote(observation="最近在修琴", trend="尝试传统手工")]
    original_profile = copy.deepcopy(profile.to_dict())
    catalog = build_profile_catalog(profile)
    original_catalog = copy.deepcopy(catalog)
    first = select_profile_context(catalog, query_texts=["古琴", "手工"], max_bytes=2000)
    second = select_profile_context(catalog, query_texts=["手工", "古琴"], max_bytes=2000)
    assert first == second
    assert first.digest == second.digest
    assert catalog == original_catalog
    assert profile.to_dict() == original_profile
    output = first.payload
    output["interests"].clear()
    assert first.payload["interests"]
    with pytest.raises(FrozenInstanceError):
        first.fits = False  # type: ignore[misc]


def test_budget_and_purpose_changes_bind_actual_projection_digest() -> None:
    catalog = build_profile_catalog(_mature_profile())
    small = select_profile_context(catalog, query_texts=["古琴漆艺"], max_bytes=750)
    large = select_profile_context(catalog, query_texts=["古琴漆艺"], max_bytes=4000)
    director = select_profile_context(
        catalog, query_texts=["古琴漆艺"], max_bytes=750, purpose="composition"
    )
    assert small.digest != large.digest
    assert small.digest != director.digest
    assert small.used_bytes <= 750 and large.used_bytes <= 4000


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_invalid_budget_rejected(budget: object) -> None:
    with pytest.raises(ValueError, match="budget"):
        select_profile_context(
            build_profile_catalog(SoulProfile()), query_texts=[], max_bytes=budget
        )  # type: ignore[arg-type]


def test_invalid_catalog_schema_and_nonfinite_data_rejected() -> None:
    catalog = build_profile_catalog(SoulProfile())
    catalog["schema_version"] = "invented"
    with pytest.raises(ValueError, match="schema"):
        select_profile_context(catalog, query_texts=[], max_bytes=500)
    profile = SoulProfile(
        preferences=PreferenceLayer(interests=[InterestTag("broken", "x", float("nan"))])
    )
    with pytest.raises(ValueError):
        build_profile_catalog(profile)


def test_duplicate_records_deduplicate_without_conflating_different_scopes() -> None:
    tag = InterestTag("摄影", "兴趣", 0.5)
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[tag, copy.deepcopy(tag), InterestTag("摄影", "工作", 0.8)]
        )
    )
    catalog = build_profile_catalog(profile)
    assert len(catalog["interests"]) == 2
    output = select_profile_context(catalog, query_texts=["摄影"], max_bytes=5000)
    assert len(output.payload["interests"]) == 2
    assert output.included_interest_names == ("摄影",)


def test_exact_utf8_budget_accounting_across_all_optional_field_shapes() -> None:
    profile = _mature_profile()
    profile.current_phase = "研究乐器修复，但只是业余爱好"
    profile.recent_awareness = [AwarenessNote(observation="常看古琴漆艺", emotion_guess="可能好奇")]
    profile.active_insights = [
        InsightHypothesis("可能想尝试修琴", confidence=0.23, evidence=["尚未明确要求"])
    ]
    catalog = build_profile_catalog(profile)
    floor = select_profile_context(catalog, query_texts=[], max_bytes=0).used_bytes
    for budget in (floor - 1, floor, floor + 1, 550, 999, 2000, 5000, 50000):
        result = select_profile_context(catalog, query_texts=["古琴漆艺"], max_bytes=budget)
        assert result.used_bytes == _bytes(result.payload)
        assert result.fits == (result.used_bytes <= budget)
        if result.fits:
            assert result.payload["disliked_topics"] == profile.preferences.disliked_topics
