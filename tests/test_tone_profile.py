"""Tests for adaptive bilibili tone profile building."""

from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile
from openbiliclaw.soul.tone import build_tone_profile


def test_build_tone_profile_prefers_dense_for_high_information_profile() -> None:
    profile = SoulProfile(
        personality_portrait="偏好高信息密度、会主动把问题想透、看内容不太满足于表层热闹的人。",
        core_traits=["理性", "克制"],
        values=["真实"],
        life_stage="持续积累",
        deep_needs=["理解复杂问题"],
    )

    tone = build_tone_profile(
        profile=profile,
        preference_summary={},
        recent_feedback=[],
    )

    assert tone["density"] == "dense"


def test_build_tone_profile_increases_playfulness_for_open_explorer() -> None:
    profile = SoulProfile(
        personality_portrait="愿意尝试新东西，但判断并不草率。",
        core_traits=["好奇", "开放"],
        preferences=PreferenceLayer(exploration_openness=0.9),
    )

    tone = build_tone_profile(
        profile=profile,
        preference_summary={"exploration_openness": 0.9},
        recent_feedback=[],
    )

    assert tone["playfulness"] in {"medium", "high"}
