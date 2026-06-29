from __future__ import annotations

import pytest

from openbiliclaw.soul.overrides import (
    DomainAdd,
    InterestPolarityEdit,
    ListEdit,
    ProfileEditError,
    ProfileOverrides,
    ScalarPin,
    TextPin,
    apply_edit,
    apply_overrides,
    build_edit_state,
)
from openbiliclaw.soul.profile import (
    CoreLayer,
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    OnionProfile,
    RoleLayer,
    SurfaceLayer,
    ValuesLayer,
)


def _sample_profile() -> OnionProfile:
    return OnionProfile(
        personality_portrait="AI 写的画像",
        core=CoreLayer(core_traits=["完美主义", "好奇"], deep_needs=["认可"]),
        values_layer=ValuesLayer(values=["自由"], motivational_drivers=["成长"]),
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="科技", weight=0.8, specifics=[InterestSpecific(name="AI", weight=0.7)]
                )
            ],
            dislikes=[InterestDomain(domain="八卦", weight=0.9)],
            favorite_up_users=["老高"],
        ),
        role=RoleLayer(life_stage="工作", current_phase="忙碌"),
        surface=SurfaceLayer(cognitive_style=["分析"], exploration_openness=0.6),
    )


def test_profile_overrides_default_is_empty() -> None:
    ov = ProfileOverrides()
    assert ov.is_empty()
    assert ov.to_dict()["text_pins"] == {}
    assert ov.version == 1


def test_profile_overrides_roundtrip() -> None:
    ov = ProfileOverrides(
        updated_at="2026-05-29T10:00:00",
        text_pins={
            "personality_portrait": TextPin(
                value="我改写的画像", ai_value_at_pin="AI 原值", pinned_at="t"
            )
        },
        list_edits={"core.core_traits": ListEdit(add=["务实"], remove=["完美主义"])},
        interest_edits={"dislikes": InterestPolarityEdit(remove_domains=["二次元"])},
    )

    restored = ProfileOverrides.from_dict(ov.to_dict())

    assert not restored.is_empty()
    assert restored.text_pins["personality_portrait"].value == "我改写的画像"
    assert restored.text_pins["personality_portrait"].ai_value_at_pin == "AI 原值"
    assert restored.list_edits["core.core_traits"].add == ["务实"]
    assert restored.list_edits["core.core_traits"].remove == ["完美主义"]
    assert restored.interest_edits["dislikes"].remove_domains == ["二次元"]


def test_profile_overrides_from_dict_handles_garbage() -> None:
    assert ProfileOverrides.from_dict(None).is_empty()
    assert ProfileOverrides.from_dict({"text_pins": "nope", "list_edits": 5}).is_empty()
    # version defaults safely on bad input
    assert ProfileOverrides.from_dict({"version": "bad"}).version == 1


def test_apply_overrides_empty_is_pure_copy() -> None:
    profile = _sample_profile()
    result = apply_overrides(profile, ProfileOverrides())

    assert result is not profile
    assert result.core.core_traits == ["完美主义", "好奇"]
    # mutating the copy must not touch the input
    result.core.core_traits.append("x")
    assert profile.core.core_traits == ["完美主义", "好奇"]


def test_apply_overrides_text_pin() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        text_pins={
            "personality_portrait": TextPin(value="我自己写的"),
            "role.life_stage": TextPin(value="在读研究生"),
        }
    )
    result = apply_overrides(profile, ov)
    assert result.personality_portrait == "我自己写的"
    assert result.role.life_stage == "在读研究生"


def test_apply_overrides_scalar_pin_clamps() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(scalar_pins={"surface.exploration_openness": ScalarPin(value=1.5)})
    result = apply_overrides(profile, ov)
    assert result.surface.exploration_openness == 1.0


def test_apply_overrides_list_add_remove_dedup() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        list_edits={"core.core_traits": ListEdit(add=["务实", "好奇"], remove=["完美主义"])}
    )
    result = apply_overrides(profile, ov)
    # 完美主义 removed; 好奇 kept (dedup vs add); 务实 appended
    assert result.core.core_traits == ["好奇", "务实"]


def test_apply_overrides_interest_add_remove() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        interest_edits={
            "likes": InterestPolarityEdit(
                add_domains=[DomainAdd(domain="户外", specifics=["徒步"])],
                remove_domains=["科技"],
            ),
            "dislikes": InterestPolarityEdit(add_domains=[DomainAdd(domain="标题党测评")]),
        }
    )
    result = apply_overrides(profile, ov)
    like_domains = [d.domain for d in result.interest.likes]
    assert "科技" not in like_domains
    assert "户外" in like_domains
    dislike_domains = [d.domain for d in result.interest.dislikes]
    assert "标题党测评" in dislike_domains


def test_apply_overrides_dislike_propagates_to_disliked_topics() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        interest_edits={
            "dislikes": InterestPolarityEdit(add_domains=[DomainAdd(domain="标题党测评")])
        }
    )
    result = apply_overrides(profile, ov)
    # OnionProfile.preferences synthesizes disliked_topics from interest.dislikes
    assert "标题党测评" in result.preferences.disliked_topics


def test_apply_overrides_survives_regeneration() -> None:
    """The same overrides apply cleanly to a freshly regenerated AI profile."""
    ov = ProfileOverrides(
        text_pins={"personality_portrait": TextPin(value="我自己写的")},
        list_edits={"core.core_traits": ListEdit(add=["务实"])},
    )
    # Simulate a rebuild: a brand-new AI profile object.
    rebuilt = _sample_profile()
    rebuilt.personality_portrait = "AI 重新生成的不同画像"
    result = apply_overrides(rebuilt, ov)
    assert result.personality_portrait == "我自己写的"
    assert "务实" in result.core.core_traits


def test_apply_overrides_remove_suppresses_rederived_trait() -> None:
    """A removed trait stays gone even if the AI re-derives it next cycle."""
    ov = ProfileOverrides(list_edits={"core.core_traits": ListEdit(remove=["完美主义"])})
    # AI profile still contains the disliked trait (re-derived)
    rebuilt = _sample_profile()
    assert "完美主义" in rebuilt.core.core_traits
    result = apply_overrides(rebuilt, ov)
    assert "完美主义" not in result.core.core_traits


# --- apply_edit reducer ---------------------------------------------------


def test_apply_edit_does_not_mutate_input() -> None:
    base = ProfileOverrides()
    new_ov, res = apply_edit(base, target="core.core_traits", op="add", value="务实")
    assert base.is_empty()
    assert new_ov.list_edits["core.core_traits"].add == ["务实"]
    assert res.ok


def test_apply_edit_text_set_trims_and_reset() -> None:
    ov, _ = apply_edit(
        ProfileOverrides(), target="personality_portrait", op="set", value="  我的画像  "
    )
    assert ov.text_pins["personality_portrait"].value == "我的画像"
    ov2, _ = apply_edit(ov, target="personality_portrait", op="reset")
    assert "personality_portrait" not in ov2.text_pins


def test_apply_edit_text_validation() -> None:
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="role.life_stage", op="set", value="   ")
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="personality_portrait", op="set", value="x" * 1201)
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="role.life_stage", op="add", value="x")


def test_apply_edit_scalar_clamps() -> None:
    ov, _ = apply_edit(
        ProfileOverrides(), target="surface.exploration_openness", op="set", value=2.0
    )
    assert ov.scalar_pins["surface.exploration_openness"].value == 1.0


def test_apply_edit_list_mutual_exclusion_and_idempotency() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="core.core_traits", op="add", value="务实")
    ov, _ = apply_edit(ov, target="core.core_traits", op="add", value="务实")
    assert ov.list_edits["core.core_traits"].add == ["务实"]
    ov, _ = apply_edit(ov, target="core.core_traits", op="remove", value="务实")
    edit = ov.list_edits["core.core_traits"]
    assert edit.add == []
    assert edit.remove == ["务实"]


def test_apply_edit_list_reset_item_drops_empty_field() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="values_layer.values", op="remove", value="自由")
    ov, _ = apply_edit(ov, target="values_layer.values", op="reset", value="自由")
    assert "values_layer.values" not in ov.list_edits


def test_apply_edit_list_max_adds() -> None:
    ov = ProfileOverrides()
    for i in range(30):
        ov, _ = apply_edit(ov, target="core.deep_needs", op="add", value=f"需求{i}")
    with pytest.raises(ProfileEditError):
        apply_edit(ov, target="core.deep_needs", op="add", value="超额")


def test_apply_edit_unknown_target_and_op() -> None:
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="core.nonexistent", op="add", value="x")
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="core.core_traits", op="frobnicate", value="x")


def test_apply_edit_list_rejects_set_op() -> None:
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="core.core_traits", op="set", value="x")


def test_apply_edit_interest_domain_add_remove() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="dislikes", op="add", value="标题党测评")
    assert ov.interest_edits["dislikes"].add_domains[0].domain == "标题党测评"
    ov, _ = apply_edit(ov, target="dislikes", op="remove", value="标题党测评")
    edit = ov.interest_edits["dislikes"]
    assert all(d.domain != "标题党测评" for d in edit.add_domains)
    assert "标题党测评" in edit.remove_domains


def test_apply_edit_interest_specific_via_parent() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="likes", op="add", value="自托管", parent="科技")
    assert ov.interest_edits["likes"].specific_edits["科技"].add == ["自托管"]


def test_apply_edit_interest_weight_pin_requires_weight() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="likes", op="set", value="科技", weight=0.95)
    assert ov.interest_edits["likes"].weight_pins["科技"] == 0.95
    with pytest.raises(ProfileEditError):
        apply_edit(ProfileOverrides(), target="likes", op="set", value="科技")


def test_apply_edit_feeds_apply_overrides() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="dislikes", op="add", value="营销号")
    result = apply_overrides(_sample_profile(), ov)
    assert "营销号" in result.preferences.disliked_topics


def test_build_edit_state_marks_interest_specific_edits() -> None:
    raw = _sample_profile()
    ov, _ = apply_edit(ProfileOverrides(), target="likes", op="add", value="自托管", parent="科技")
    effective = apply_overrides(raw, ov)

    state = build_edit_state(raw, effective, ov)

    likes = state["fields"]["likes"]
    assert likes["specific_edits"]["科技"]["add"] == ["自托管"]
    tech = next(item for item in likes["domains"] if item["domain"] == "科技")
    assert "自托管" in tech["specifics"]


def test_apply_edit_interest_specific_add_then_remove_clears_override() -> None:
    ov, _ = apply_edit(ProfileOverrides(), target="likes", op="add", value="自托管", parent="科技")

    ov, _ = apply_edit(ov, target="likes", op="remove", value="自托管", parent="科技")

    assert "likes" not in ov.interest_edits
