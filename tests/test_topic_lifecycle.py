"""Tests for the Phase 4 topic-lifecycle state machine (Task 8).

Spec: docs/plans/2026-07-17-cognitive-profile-pipeline-spec.md §Phase 4.
Plan: docs/plans/2026-07-17-cognitive-profile-pipeline-plan.md Task 8.

Covers the six lifecycle transitions (trial→active ×2, active→decaying,
decaying→archived, archived→active rekindle, dislike→archived), legacy
compatibility (missing fields default active), the subdivision shadow
proposal, ledger recording of transitions, and the two-state serialization
switch (off byte-identical / on excludes archived).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery.strategies._utils import (
    build_profile_summary,
    set_topic_lifecycle_serialization,
    topic_lifecycle_serialization_enabled,
)
from openbiliclaw.soul.profile import (
    InterestDomain,
    InterestLayer,
    OnionProfile,
    interest_tag_from_dict,
    interest_tag_to_dict,
    preference_layer_from_dict,
)
from openbiliclaw.soul.topic_lifecycle import (
    ACTIVE,
    ARCHIVED,
    DECAYING,
    TRIAL,
    apply_evidence,
    archive_topics,
    get_state,
    propose_subdivisions,
    scan_lifecycle,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_topic_lifecycle_serialization() -> Iterator[None]:
    """Restore the process-level serialization toggle after every test.

    ``set_topic_lifecycle_serialization`` mutates module state shared across the
    whole test session; leaking ``on`` (e.g. via a mid-test assertion failure)
    would silently change ``build_profile_summary`` for later test files. This
    guarantees the default ``off`` regardless of test outcome.
    """
    yield
    set_topic_lifecycle_serialization(False)


def _interest(name: str, category: str = "", **extra: object) -> dict[str, object]:
    return {"name": name, "category": category or name, "weight": 0.8, **extra}


# ---------------------------------------------------------------------------
# Transition 1: trial → active on evidence threshold
# ---------------------------------------------------------------------------


def test_trial_promotes_to_active_on_evidence_threshold() -> None:
    interests = [_interest("强化学习", state=TRIAL, evidence_count=1)]
    # Feed the same topic through five analyses; the fifth crosses the threshold.
    state = TRIAL
    for _ in range(4):
        interests, transitions = apply_evidence(interests, [_interest("强化学习")])
        state = get_state(interests[0])
    assert state == ACTIVE
    assert any(t.to_state == ACTIVE for t in transitions)
    assert interests[0]["evidence_count"] >= 5


# ---------------------------------------------------------------------------
# Transition 2: trial → active on sustained duration
# ---------------------------------------------------------------------------


def test_trial_promotes_to_active_on_duration() -> None:
    now = datetime(2026, 7, 17, 12, 0, 0)
    old = (now - timedelta(days=8)).isoformat()
    interests = [_interest("骑行", state=TRIAL, evidence_count=1, first_seen=old)]
    scanned, transitions = scan_lifecycle(interests, now=now)
    assert get_state(scanned[0]) == ACTIVE
    assert transitions and transitions[0].from_state == TRIAL


# ---------------------------------------------------------------------------
# Transition 3: active → decaying after 30 silent days (weight halved)
# ---------------------------------------------------------------------------


def test_active_decays_after_thirty_silent_days() -> None:
    now = datetime(2026, 7, 17, 12, 0, 0)
    silent = (now - timedelta(days=31)).isoformat()
    interests = [
        {
            "name": "摄影",
            "category": "摄影",
            "weight": 0.8,
            "state": ACTIVE,
            "last_evidence_at": silent,
        }
    ]
    scanned, transitions = scan_lifecycle(interests, now=now)
    assert get_state(scanned[0]) == DECAYING
    assert scanned[0]["weight"] == 0.4  # 0.8 * 0.5
    assert transitions[0].to_state == DECAYING


def test_active_survives_when_recently_seen() -> None:
    now = datetime(2026, 7, 17, 12, 0, 0)
    recent = (now - timedelta(days=3)).isoformat()
    interests = [
        {
            "name": "摄影",
            "category": "摄影",
            "weight": 0.8,
            "state": ACTIVE,
            "last_evidence_at": recent,
        }
    ]
    scanned, transitions = scan_lifecycle(interests, now=now)
    assert get_state(scanned[0]) == ACTIVE
    assert not transitions


# ---------------------------------------------------------------------------
# Transition 4: decaying → archived after a further 30 silent days
# ---------------------------------------------------------------------------


def test_decaying_archives_after_sixty_silent_days() -> None:
    now = datetime(2026, 7, 17, 12, 0, 0)
    silent = (now - timedelta(days=61)).isoformat()
    interests = [
        {
            "name": "手账",
            "category": "手账",
            "weight": 0.4,
            "state": DECAYING,
            "last_evidence_at": silent,
        }
    ]
    scanned, transitions = scan_lifecycle(interests, now=now)
    assert get_state(scanned[0]) == ARCHIVED
    assert transitions[0].to_state == ARCHIVED


# ---------------------------------------------------------------------------
# Transition 5: archived → active rekindle on fresh evidence
# ---------------------------------------------------------------------------


def test_archived_rekindles_to_active_on_evidence() -> None:
    interests = [_interest("露营", state=ARCHIVED, evidence_count=3)]
    updated, transitions = apply_evidence(interests, [_interest("露营")])
    assert get_state(updated[0]) == ACTIVE
    assert transitions[0].from_state == ARCHIVED
    assert transitions[0].to_state == ACTIVE


def test_decaying_rekindles_to_active_on_evidence() -> None:
    interests = [_interest("露营", state=DECAYING, evidence_count=3)]
    updated, _ = apply_evidence(interests, [_interest("露营")])
    assert get_state(updated[0]) == ACTIVE


# ---------------------------------------------------------------------------
# Transition 6: dislike → archived (归档+避雷, never deleted)
# ---------------------------------------------------------------------------


def test_dislike_archives_topic_without_deleting() -> None:
    interests = [_interest("鬼畜", state=ACTIVE), _interest("科普", state=ACTIVE)]
    archived, transitions = archive_topics(interests, ["鬼畜"])
    # Both interests survive; only the disliked one is archived.
    assert len(archived) == 2
    by_name = {item["name"]: get_state(item) for item in archived}
    assert by_name["鬼畜"] == ARCHIVED
    assert by_name["科普"] == ACTIVE
    assert transitions[0].to_state == ARCHIVED


# ---------------------------------------------------------------------------
# New topic entry as trial
# ---------------------------------------------------------------------------


def test_new_topic_enters_as_trial() -> None:
    updated, transitions = apply_evidence([], [_interest("桌游")])
    assert get_state(updated[0]) == TRIAL
    assert updated[0]["evidence_count"] == 1
    assert transitions[0].from_state == ""
    assert transitions[0].to_state == TRIAL


# ---------------------------------------------------------------------------
# Legacy compatibility: missing fields default to active
# ---------------------------------------------------------------------------


def test_legacy_interest_without_state_defaults_active() -> None:
    # get_state on a raw legacy dict.
    assert get_state({"name": "老兴趣"}) == ACTIVE
    # Round-trip through the dataclass serializers.
    tag = interest_tag_from_dict({"name": "老兴趣", "category": "老兴趣", "weight": 0.7})
    assert tag.state == "active"
    assert tag.evidence_count == 0
    # An active tag serializes without lifecycle keys (byte-identical shape).
    serialized = interest_tag_to_dict(tag)
    assert "state" not in serialized
    assert "evidence_count" not in serialized


def test_transitioned_interest_serializes_lifecycle_fields() -> None:
    tag = interest_tag_from_dict(
        {
            "name": "新兴趣",
            "category": "新兴趣",
            "weight": 0.5,
            "state": "trial",
            "evidence_count": 2,
        }
    )
    serialized = interest_tag_to_dict(tag)
    assert serialized["state"] == "trial"
    assert serialized["evidence_count"] == 2
    # Round-trips back.
    again = interest_tag_from_dict(serialized)
    assert again.state == "trial"
    assert again.evidence_count == 2


# ---------------------------------------------------------------------------
# Subdivision shadow proposal
# ---------------------------------------------------------------------------


def test_subdivision_proposal_when_child_dominates_parent() -> None:
    interests = [
        {"name": "游戏", "category": "游戏", "weight": 0.9},  # domain-level
        {"name": "塞尔达", "category": "游戏", "weight": 0.7},  # child ~78% of parent
        {"name": "扫雷", "category": "游戏", "weight": 0.1},  # child ~11%, no proposal
    ]
    proposals = propose_subdivisions(interests)
    assert len(proposals) == 1
    assert proposals[0].child == "塞尔达"
    assert proposals[0].parent == "游戏"
    assert proposals[0].ratio >= 0.6


# ---------------------------------------------------------------------------
# Serialization switch: off byte-identical / on excludes archived
# ---------------------------------------------------------------------------


def _profile_with_archived() -> OnionProfile:
    return OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(domain="AI", weight=0.9, state="active"),
                InterestDomain(domain="旧梗", weight=0.6, state="archived"),
            ]
        )
    )


def test_summary_off_is_byte_identical_and_includes_archived() -> None:
    profile = _profile_with_archived()
    summary_off = build_profile_summary(profile, exclude_archived_topics=False)
    domains = {d["domain"] for d in summary_off["interest_domains"]}  # type: ignore[index]
    # Off keeps archived topics in the serialization.
    assert domains == {"AI", "旧梗"}
    # And never leaks lifecycle field keys → byte-identical to pre-lifecycle shape.
    assert '"state"' not in json.dumps(summary_off, ensure_ascii=False)


def test_summary_on_excludes_archived() -> None:
    profile = _profile_with_archived()
    summary_on = build_profile_summary(profile, exclude_archived_topics=True)
    domains = {d["domain"] for d in summary_on["interest_domains"]}  # type: ignore[index]
    assert domains == {"AI"}
    assert '"state"' not in json.dumps(summary_on, ensure_ascii=False)


def test_summary_default_off_matches_explicit_off() -> None:
    profile = _profile_with_archived()
    # Default (module toggle off) equals explicit off — the regression baseline.
    assert build_profile_summary(profile) == build_profile_summary(
        profile, exclude_archived_topics=False
    )


def test_module_toggle_drives_default() -> None:
    profile = _profile_with_archived()
    try:
        set_topic_lifecycle_serialization(True)
        assert topic_lifecycle_serialization_enabled() is True
        summary = build_profile_summary(profile)
        domains = {d["domain"] for d in summary["interest_domains"]}  # type: ignore[index]
        assert domains == {"AI"}
    finally:
        set_topic_lifecycle_serialization(False)
    assert topic_lifecycle_serialization_enabled() is False


# ---------------------------------------------------------------------------
# Transitions reach the ledger (via SoulEngine helper) + flat compat read
# ---------------------------------------------------------------------------


class _FakeRegistry:
    async def complete(self, messages: object, **kwargs: object) -> object:  # pragma: no cover
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content="{}", provider="openai")


def test_evidence_overlay_records_ledger_transitions(tmp_path: Path) -> None:
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=_FakeRegistry(), memory=memory)

    existing = {"interests": []}
    updated = {"interests": [_interest("新话题")]}
    engine._apply_topic_lifecycle_evidence(existing, updated)
    assert get_state(updated["interests"][0]) == TRIAL

    rows = memory._database.query_profile_ledger(days=1, write_point="topic_lifecycle")
    assert rows, "a lifecycle transition should be recorded to the ledger"


def test_evidence_overlay_does_not_count_untouched_retained_topics(tmp_path: Path) -> None:
    """A full merged preference is not evidence for every retained topic."""
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=_FakeRegistry(), memory=memory)
    old_seen = "2026-09-01T12:00:00"
    new_seen = "2026-09-02T12:00:00"
    existing = {
        "interests": [
            _interest(
                "滑雪",
                category="体育",
                state=TRIAL,
                evidence_count=1,
                last_seen=old_seen,
                last_evidence_at=old_seen,
            )
        ]
    }
    updated = {
        "interests": [
            _interest("滑雪", category="体育", last_seen=old_seen),
            _interest("Python", category="科技", last_seen=new_seen),
        ]
    }

    engine._apply_topic_lifecycle_evidence(existing, updated)

    by_name = {item["name"]: item for item in updated["interests"]}
    assert by_name["滑雪"]["state"] == TRIAL
    assert by_name["滑雪"]["evidence_count"] == 1
    assert by_name["滑雪"]["last_evidence_at"] == old_seen
    assert by_name["Python"]["state"] == TRIAL
    assert by_name["Python"]["evidence_count"] == 1


def test_evidence_overlay_still_counts_a_touched_retained_topic(tmp_path: Path) -> None:
    """A retained topic whose last_seen advanced is still fresh evidence.

    Guards the false-negative direction of ``changed_interest_keys``: if the
    merge-snapshot filter ever stopped recognising touched topics, evidence
    would silently stop accumulating while the untouched-topic test stayed
    green.
    """
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    memory = MemoryManager(tmp_path)
    memory.initialize()
    engine = SoulEngine(llm=_FakeRegistry(), memory=memory)
    old_seen = "2026-09-01T12:00:00"
    new_seen = "2026-09-02T12:00:00"
    existing = {
        "interests": [
            _interest(
                "滑雪",
                category="体育",
                state=TRIAL,
                evidence_count=1,
                last_seen=old_seen,
                last_evidence_at=old_seen,
            ),
            _interest(
                "露营",
                category="户外",
                state=DECAYING,
                evidence_count=3,
                last_seen=old_seen,
                last_evidence_at=old_seen,
            ),
        ]
    }
    updated = {
        "interests": [
            _interest("滑雪", category="体育", last_seen=old_seen),
            _interest("露营", category="户外", last_seen=new_seen),
        ]
    }

    engine._apply_topic_lifecycle_evidence(existing, updated)

    by_name = {item["name"]: item for item in updated["interests"]}
    assert by_name["滑雪"]["state"] == TRIAL
    assert by_name["滑雪"]["evidence_count"] == 1
    assert by_name["露营"]["state"] == ACTIVE
    assert by_name["露营"]["evidence_count"] == 4
    assert by_name["露营"]["last_evidence_at"] != old_seen


def test_flat_preference_round_trips_lifecycle_state() -> None:
    layer = preference_layer_from_dict(
        {"interests": [{"name": "航海", "category": "航海", "weight": 0.6, "state": "archived"}]}
    )
    assert layer.interests[0].state == "archived"


class _StructuredPreferenceRegistry:
    """Registry stub whose structured task returns one brand-new interest."""

    def __init__(self, payload: str) -> None:
        self.payload = payload

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
    ) -> object:
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content=self.payload, provider="openai")

    async def complete(self, messages: list[dict[str, str]], **kwargs: object) -> object:
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content=self.payload, provider="openai")


@pytest.mark.asyncio
async def test_incremental_pipeline_stamps_trial_on_new_interests(tmp_path: Path) -> None:
    """Ordinary browsing must enter the trial period like every other path.

    The incremental pipeline (`ProfileUpdatePipeline` -> `_update_interest`) is
    where day-to-day browsing lands. It used to persist interests with no
    ``state`` at all; ``get_state()`` reads that as ``active``, so a topic seen
    once immediately competed for recommendation weight, and the 12h
    ``scan_lifecycle`` never repaired it (a stateless interest already looks
    active). Only ``analyze_events`` / dialogue / feedback-batch stamped trial.
    """
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine
    from openbiliclaw.soul.layer_updaters import update_layer
    from openbiliclaw.soul.pipeline import OnionLayer
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.topic_lifecycle import get_state

    memory = MemoryManager(tmp_path)
    memory.initialize()
    registry = _StructuredPreferenceRegistry(
        '{"interests": [{"name": "木工制作", "category": "生活", "weight": 0.9}]}'
    )
    engine = SoulEngine(llm=registry, memory=memory)

    signals: list[dict[str, object]] = [
        {"payload": {"event_type": "view", "title": "榫卯小凳制作", "id": index}}
        for index in range(1, 4)
    ]
    result = await update_layer(
        layer=OnionLayer.INTEREST,
        signals=signals,
        profile=OnionProfile(),
        memory=memory,
        preference_analyzer=engine._preference_analyzer,
        profile_builder=engine._profile_builder,
    )
    assert result.changed, "the stub returns a new interest, so the layer must change"

    interests = memory.get_layer("preference").data.get("interests", [])
    assert interests, "the new interest must be persisted"
    woodworking = next(i for i in interests if i.get("name") == "木工制作")
    assert woodworking.get("state") == "trial", (
        "a pipeline-born interest must start in trial, not go straight to active"
    )
    assert get_state(woodworking) == "trial"
    assert woodworking.get("evidence_count") == 1

    rows = [
        row
        for row in (memory._database.query_profile_ledger(days=1, limit=50) or [])
        if row.get("write_point") == "topic_lifecycle"
    ]
    assert rows, "the trial transition must be ledgered like every other path"
    assert any(row.get("source") == "pipeline" for row in rows), (
        "the ledger must name the path that stamped the transition"
    )
