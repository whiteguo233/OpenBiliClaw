"""Tests for profile_kw_digest (P1.2): stable, quantized, path-agnostic."""

from __future__ import annotations

from typing import Any

from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _profile(**overrides: Any) -> SoulProfile:
    base: dict[str, Any] = {
        "core_traits": ["理性", "好奇"],
        "values": ["真实"],
        "motivational_drivers": ["理解底层逻辑"],
        "cognitive_style": ["结构化拆解"],
        "current_phase": "整理信息源",
        "life_stage": "工作稳定期",
        "preferences": PreferenceLayer(
            interests=[
                InterestTag(name="国际局势", category="知识", weight=0.91),
                InterestTag(name="露营", category="生活", weight=0.70),
            ],
            disliked_topics=["标题党"],
        ),
    }
    base.update(overrides)
    return SoulProfile(**base)


def _prefs(interests: list[InterestTag], disliked: list[str] | None = None) -> PreferenceLayer:
    return PreferenceLayer(interests=interests, disliked_topics=disliked or ["标题党"])


def test_digest_deterministic_and_order_independent() -> None:
    p1 = _profile()
    p2 = _profile(
        preferences=_prefs(
            [
                InterestTag(name="露营", category="生活", weight=0.70),
                InterestTag(name="国际局势", category="知识", weight=0.91),
            ]
        )
    )
    assert profile_kw_digest(p1) == profile_kw_digest(p2)


def test_small_weight_drift_does_not_flip() -> None:
    # 0.91 → 0.94 both quantize to the 0.9 bucket → same digest.
    p1 = _profile()
    p2 = _profile(
        preferences=_prefs(
            [
                InterestTag(name="国际局势", category="知识", weight=0.94),
                InterestTag(name="露营", category="生活", weight=0.70),
            ]
        )
    )
    assert profile_kw_digest(p1) == profile_kw_digest(p2)


def test_material_interest_change_flips() -> None:
    p1 = _profile()
    p2 = _profile(
        preferences=_prefs(
            [
                InterestTag(name="国际局势", category="知识", weight=0.91),
                InterestTag(name="露营", category="生活", weight=0.70),
                InterestTag(name="机器学习", category="科技", weight=0.85),
            ]
        )
    )
    assert profile_kw_digest(p1) != profile_kw_digest(p2)


def test_dislike_change_flips() -> None:
    p1 = _profile()
    p2 = _profile(
        preferences=_prefs(
            [
                InterestTag(name="国际局势", category="知识", weight=0.91),
                InterestTag(name="露营", category="生活", weight=0.70),
            ],
            disliked=["标题党", "低质混剪"],
        )
    )
    assert profile_kw_digest(p1) != profile_kw_digest(p2)


def test_excludes_non_keyword_fields() -> None:
    # personality_portrait + exploration_openness do not shape search keywords →
    # excluded from the digest (proxy for recent_awareness / active_insights,
    # which the function also never reads).
    assert profile_kw_digest(_profile(personality_portrait="A")) == profile_kw_digest(
        _profile(personality_portrait="完全不同的一段画像 B")
    )
    p3 = _profile()
    p3.preferences.exploration_openness = 0.1
    p4 = _profile()
    p4.preferences.exploration_openness = 0.9
    assert profile_kw_digest(p3) == profile_kw_digest(p4)
