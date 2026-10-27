"""Only request-local card aliases cross the planner model seam."""

from __future__ import annotations

import copy

import pytest

from openbiliclaw.recommendation.director.planner_refs import alias_planner_candidates


def _candidates():
    return [
        {"ref": "c_123456789012345678901234", "title": "Untrusted c0 text", "metadata": {"x": [1]}},
        {"ref": "c_987654321098765432109876", "title": "Untrusted c1 text"},
        {"ref": "c_abcdefabcdefabcdefabcdef", "title": "Third"},
    ]


def _output():
    return {
        "screens": [
            {
                "pairs": [{"card_refs": ["c1", "c0"], "intent": "c1 remains prose"}],
                "brief": {"evidence_refs": ["c0"], "objective": "c1 remains prose"},
            }
        ],
        "completion": {
            "reason": "supply_exhausted",
            "omissions": [{"refs": ["c2"], "reason": "off_intent", "note": "c0 remains prose"}],
        },
    }


def test_short_aliases_preserve_order_and_deepcopy_candidate_payloads():
    candidates = _candidates()
    before = copy.deepcopy(candidates)
    prompt, _ = alias_planner_candidates(candidates)
    assert [card["ref"] for card in prompt] == ["c0", "c1", "c2"]
    assert prompt[0]["title"] == "Untrusted c0 text"
    prompt[0]["metadata"]["x"].append(2)
    assert candidates == before


def test_restore_only_card_references_never_evidence_or_free_text():
    candidates = _candidates()
    _, refs = alias_planner_candidates(candidates)
    raw = _output()
    before = copy.deepcopy(raw)
    result = refs.restore_output(raw)
    assert result["screens"][0]["pairs"][0]["card_refs"] == [
        candidates[1]["ref"],
        candidates[0]["ref"],
    ]
    assert result["completion"]["omissions"][0]["refs"] == [candidates[2]["ref"]]
    assert result["screens"][0]["brief"] == before["screens"][0]["brief"]
    assert result["screens"][0]["pairs"][0]["intent"] == "c1 remains prose"
    assert result["completion"]["omissions"][0]["note"] == "c0 remains prose"
    result["screens"][0]["brief"]["evidence_refs"].append("changed")
    assert raw == before


@pytest.mark.parametrize(
    "bad", ["c3", "c01", "C0", "c-1", " c0", "c0 ", "c_123456789012345678901234", None, 0, [], {}]
)
@pytest.mark.parametrize("location", ["selected", "omitted"])
def test_unknown_malformed_or_canonical_ref_is_not_accepted_as_an_alias(bad, location):
    _, refs = alias_planner_candidates(_candidates())
    raw = _output()
    if location == "selected":
        raw["screens"][0]["pairs"][0]["card_refs"] = [bad]
    else:
        raw["completion"]["omissions"][0]["refs"] = [bad]
    before = copy.deepcopy(raw)
    with pytest.raises(ValueError, match="unknown_planner_alias"):
        refs.restore_output(raw)
    assert raw == before


def test_duplicate_and_selected_omitted_overlap_are_preserved_for_existing_validator():
    candidates = _candidates()
    _, refs = alias_planner_candidates(candidates)
    raw = _output()
    raw["screens"][0]["pairs"][0]["card_refs"] = ["c0", "c0"]
    raw["completion"]["omissions"][0]["refs"] = ["c0", "c2", "c2"]
    result = refs.restore_output(raw)
    assert result["screens"][0]["pairs"][0]["card_refs"] == [candidates[0]["ref"]] * 2
    assert result["completion"]["omissions"][0]["refs"] == [
        candidates[0]["ref"],
        candidates[2]["ref"],
        candidates[2]["ref"],
    ]


def test_canonical_spelling_collision_never_bypasses_position_mapping():
    prompt, refs = alias_planner_candidates([{"ref": "c1"}, {"ref": "c0"}, {"ref": "c01"}])
    assert [card["ref"] for card in prompt] == ["c0", "c1", "c2"]
    raw = {
        "screens": [{"pairs": [{"card_refs": ["c0", "c1"]}]}],
        "completion": {"omissions": [{"refs": ["c2"]}]},
    }
    restored = refs.restore_output(raw)
    assert restored["screens"][0]["pairs"][0]["card_refs"] == ["c1", "c0"]
    assert restored["completion"]["omissions"][0]["refs"] == ["c01"]


def test_remapping_is_per_request_and_mapping_does_not_reference_mutable_input():
    candidates = [{"ref": "canonical-a"}, {"ref": "canonical-b"}]
    _, first = alias_planner_candidates(candidates)
    _, second = alias_planner_candidates(list(reversed(candidates)))
    candidates[0]["ref"] = "MUTATED"
    raw = {"screens": [{"pairs": [{"card_refs": ["c0"]}]}]}
    assert first.restore_output(raw)["screens"][0]["pairs"][0]["card_refs"] == ["canonical-a"]
    assert second.restore_output(raw)["screens"][0]["pairs"][0]["card_refs"] == ["canonical-b"]


@pytest.mark.parametrize("count", [0, 1, 40])
def test_engineering_candidate_bound(count):
    prompt, refs = alias_planner_candidates([{"ref": f"canonical-{i}"} for i in range(count)])
    assert len(prompt) == count
    raw = {"screens": [], "completion": {"omissions": []}}
    assert refs.restore_output(raw) == raw


def test_more_than_forty_candidates_fails_before_projection():
    with pytest.raises(ValueError, match="planner_candidate_limit"):
        alias_planner_candidates([{"ref": f"canonical-{i}"} for i in range(41)])


@pytest.mark.parametrize(
    "candidates", [[{}], [{"ref": 1}], [{"ref": ""}], [{"ref": "a"}, {"ref": "a"}]]
)
def test_invalid_canonical_candidate_identity_is_rejected(candidates):
    with pytest.raises(ValueError, match="invalid_planner_candidate_refs"):
        alias_planner_candidates(candidates)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "not-object",
        {"screens": None},
        {"screens": [None]},
        {"screens": [{"pairs": None}]},
        {"screens": [{"pairs": [{"card_refs": "c0"}]}]},
        {"completion": {"omissions": [{"refs": "c0"}]}},
    ],
)
def test_malformed_structure_is_never_repaired_or_silently_coerced(raw):
    _, refs = alias_planner_candidates(_candidates())
    with pytest.raises(ValueError, match="invalid_planner_ref_structure"):
        refs.restore_output(raw)
