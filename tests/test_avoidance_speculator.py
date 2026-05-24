from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


def test_avoidance_state_round_trips(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceCooldownEntry,
        AvoidanceState,
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
        load_avoidance_state,
        save_avoidance_state,
    )

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="浅层热点复读",
                reason="用户可能不喜欢无信息增量的热点复读。",
                source_mode="negative_signal",
                source_signal="thumbs_down",
                confidence=0.7,
                created_at="2026-05-24T10:00:00",
                confirmation_count=1,
                confirmation_threshold=3,
                specifics=[
                    SpeculativeAvoidanceSpecific(
                        name="标题党热点解读",
                        confirmation_count=1,
                        confirming_events=["不喜欢这种标题党"],
                    )
                ],
            )
        ],
        cooldown=[
            AvoidanceCooldownEntry(
                domain="营销号带货",
                source_mode="negative_signal",
                rejected_at="2026-05-24T09:00:00",
                cooldown_until="2026-05-31T09:00:00",
            )
        ],
        last_generation_at="2026-05-24T10:00:00",
        total_promoted=2,
        total_rejected=1,
    )

    save_avoidance_state(tmp_path, state)
    loaded = load_avoidance_state(tmp_path)

    assert loaded.active[0].domain == "浅层热点复读"
    assert loaded.active[0].source_mode == "negative_signal"
    assert loaded.active[0].specifics[0].name == "标题党热点解读"
    assert loaded.cooldown[0].domain == "营销号带货"
    assert loaded.total_promoted == 2
    assert loaded.total_rejected == 1


def test_promote_ready_avoidances_handles_confirmed_and_threshold(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceState,
        SpeculativeAvoidance,
        promote_ready_avoidances,
    )

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="自动确认",
                status="active",
                confirmation_count=3,
                confirmation_threshold=3,
            ),
            SpeculativeAvoidance(domain="显式确认", status="confirmed"),
            SpeculativeAvoidance(domain="未确认", status="active", confirmation_count=1),
        ]
    )

    promoted, state = promote_ready_avoidances(state)

    assert [item.domain for item in promoted] == ["自动确认", "显式确认"]
    assert [item.domain for item in state.active] == ["未确认"]
    assert state.total_promoted == 2


def test_expire_stale_avoidances_creates_cooldown():
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceState,
        SpeculativeAvoidance,
        expire_stale_avoidances,
    )

    old = datetime.now() - timedelta(days=5)
    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="过期避雷",
                source_mode="style_boundary",
                status="active",
                created_at=old.isoformat(),
                ttl_days=3,
            )
        ]
    )

    rejected, state = expire_stale_avoidances(state, datetime.now(), cooldown_days=7)

    assert [item.domain for item in rejected] == ["过期避雷"]
    assert state.active == []
    assert state.cooldown[0].domain == "过期避雷"
    assert state.cooldown[0].source_mode == "style_boundary"
    assert state.total_rejected == 1


def test_avoidance_observe_counts_only_explicit_negative_events(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        save_avoidance_state,
    )

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="浅层热点复读",
                created_at=datetime.now().isoformat(),
                specifics=[],
            )
        ]
    )
    save_avoidance_state(tmp_path, state)

    speculator = AvoidanceSpeculator(llm_service=None, data_dir=tmp_path)

    matches = speculator.observe(
        [
            {
                "title": "浅层热点复读合集",
                "event_type": "view",
                "metadata": {"inferred_satisfaction": "negative"},
            },
            {
                "title": "浅层热点复读又来了",
                "event_type": "feedback",
                "metadata": {"feedback_type": "dislike"},
            },
            {
                "title": "浅层热点复读解读",
                "event_type": "reaction",
                "metadata": {"reaction": "thumbs_down"},
            },
        ]
    )

    reloaded = speculator._load_state()
    assert matches == 2
    assert reloaded.active[0].confirmation_count == 2


@pytest.mark.asyncio
async def test_avoidance_speculator_tick_promotes_without_io_writeback(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="已确认避雷",
                status="active",
                confirmation_count=3,
                confirmation_threshold=3,
                created_at=datetime.now().isoformat(),
            )
        ]
    )
    save_avoidance_state(tmp_path, state)

    speculator = AvoidanceSpeculator(
        llm_service=None,
        data_dir=tmp_path,
        generation_interval_minutes=999999,
    )

    result = await speculator.tick(OnionProfile())

    assert [item.domain for item in result.promoted] == ["已确认避雷"]
    assert speculator._load_state().active == []


def test_avoidance_novelty_guard_blocks_positive_like_domain():
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceNoveltyGuard,
        AvoidanceState,
    )
    from openbiliclaw.soul.profile import (
        InterestDomain,
        InterestLayer,
        InterestSpecific,
        OnionProfile,
    )

    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="AI",
                    weight=0.9,
                    specifics=[InterestSpecific(name="大模型", weight=0.8)],
                )
            ]
        )
    )

    guard = AvoidanceNoveltyGuard.from_profile_and_state(profile, AvoidanceState())

    assert guard.is_duplicate_domain("AI") is True
    assert guard.is_duplicate_domain("AI大模型") is True


def test_choose_next_avoidance_probe_skips_denied_feedback_domain():
    from openbiliclaw.soul.avoidance_speculator import (
        SpeculativeAvoidance,
        choose_next_avoidance_candidate,
    )

    chosen = choose_next_avoidance_candidate(
        [
            SpeculativeAvoidance(
                domain="浅层热点复读",
                confirmation_count=0,
                confidence=0.9,
                weight=0.9,
                experience_mode="knowledge",
                entry_load="light",
            ),
            SpeculativeAvoidance(
                domain="营销号带货",
                confirmation_count=0,
                confidence=0.4,
                weight=0.4,
                experience_mode="people_story",
                entry_load="light",
            ),
        ],
        feedback_history=[
            {
                "domain": "浅层热点",
                "response": "reject",
                "axis": "knowledge|light",
            }
        ],
    )

    assert chosen is not None
    assert chosen.domain == "营销号带货"


def test_choose_next_avoidance_probe_prefers_fresh_axis():
    from openbiliclaw.soul.avoidance_speculator import (
        SpeculativeAvoidance,
        choose_next_avoidance_candidate,
    )

    chosen = choose_next_avoidance_candidate(
        [
            SpeculativeAvoidance(
                domain="浅层热点复读",
                confirmation_count=0,
                confidence=0.9,
                weight=0.9,
                experience_mode="knowledge",
                entry_load="light",
            ),
            SpeculativeAvoidance(
                domain="过度情绪站队",
                confirmation_count=0,
                confidence=0.4,
                weight=0.4,
                experience_mode="people_story",
                entry_load="light",
            ),
        ],
        probed_axes={"knowledge|light"},
    )

    assert chosen is not None
    assert chosen.domain == "过度情绪站队"


@pytest.mark.asyncio
async def test_avoidance_speculator_force_tick_generates_candidates(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import AvoidanceSpeculator
    from openbiliclaw.soul.profile import OnionProfile

    class FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            assert "negative_signal" in kwargs["system_instruction"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "avoidances": [
                            {
                                "domain": "浅层热点复读",
                                "reason": (
                                    "用户可能不喜欢没有信息增量、"
                                    "只是在复读热梗和立场的热点内容。"
                                ),
                                "source_mode": "negative_signal",
                                "source_signal": "thumbs_down: 热点复读",
                                "experience_mode": "knowledge",
                                "entry_load": "light",
                                "confidence": 0.66,
                                "specifics": ["标题党热点解读", "无信息增量复读", "情绪化站队剪辑"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    speculator = AvoidanceSpeculator(llm_service=FakeLLMService(), data_dir=tmp_path)

    result = await speculator.force_tick(OnionProfile())

    assert [item.domain for item in result.generated] == ["浅层热点复读"]
    assert result.generated[0].source_mode == "negative_signal"
    assert [item.name for item in result.generated[0].specifics] == [
        "标题党热点解读",
        "无信息增量复读",
        "情绪化站队剪辑",
    ]
