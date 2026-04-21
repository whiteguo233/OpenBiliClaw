"""Tests for adaptive bilibili tone profile building."""

from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile, StylePreference
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


def test_build_tone_profile_does_not_force_dense_when_depth_preference_is_low() -> None:
    profile = SoulProfile(
        personality_portrait="会主动把复杂问题想透，但并不喜欢每次都被塞进很硬的表达里。",
        preferences=PreferenceLayer(
            style=StylePreference(
                preferred_duration="short",
                humor_preference=0.75,
                depth_preference=0.25,
            )
        ),
    )

    tone = build_tone_profile(
        profile=profile,
        preference_summary={
            "style": {
                "preferred_duration": "short",
                "humor_preference": 0.75,
                "depth_preference": 0.25,
            }
        },
        recent_feedback=[],
    )

    assert tone["density"] in {"light", "balanced"}


def test_build_tone_profile_ignores_non_mapping_style_summary() -> None:
    profile = SoulProfile(
        personality_portrait="更想看轻一点、短一点，但不排斥偶尔认真聊。",
        preferences=PreferenceLayer(
            style=StylePreference(
                preferred_duration="short",
                humor_preference=0.6,
                depth_preference=0.2,
            )
        ),
    )

    tone = build_tone_profile(
        profile=profile,
        preference_summary={"style": ["not", "a", "mapping"]},
        recent_feedback=[],
    )

    assert tone["density"] == "light"
