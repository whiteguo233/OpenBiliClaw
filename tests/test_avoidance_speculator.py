from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest


class _PausingAvoidanceLLM:
    def __init__(self, domain: str = "低信息密度热点复读") -> None:
        self.domain = domain
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def complete_structured_task(self, **_kwargs: object) -> object:
        self.started.set()
        await self.resume.wait()
        return SimpleNamespace(
            content=json.dumps(
                {
                    "avoidances": [
                        {
                            "domain": self.domain,
                            "reason": "用户近期对浅层热点表达了排斥。",
                            "source_mode": "negative_signal",
                            "source_signal": "dislike",
                            "specifics": ["标题党热点", "重复观点"],
                            "confidence": 0.55,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


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


def test_user_confirm_avoidance_returns_none_for_missing_domain(tmp_path) -> None:
    from openbiliclaw.soul.avoidance_speculator import AvoidanceSpeculator

    speculator = AvoidanceSpeculator(llm_service=None, data_dir=tmp_path)

    assert speculator.user_confirm_avoidance("不存在") is None


def test_user_reject_avoidance_returns_false_for_missing_domain(tmp_path) -> None:
    from openbiliclaw.soul.avoidance_speculator import AvoidanceSpeculator

    speculator = AvoidanceSpeculator(llm_service=None, data_dir=tmp_path)

    assert speculator.user_reject_avoidance("不存在") is False


async def test_force_tick_does_not_restore_user_confirmed_avoidance(tmp_path) -> None:
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        load_avoidance_state,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    save_avoidance_state(
        tmp_path,
        AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读", status="active")]),
    )
    llm = _PausingAvoidanceLLM()
    speculator = AvoidanceSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()
    assert speculator.user_confirm_avoidance("浅层热点复读") is not None
    llm.resume.set()
    await task

    state = load_avoidance_state(tmp_path)
    assert all(item.domain != "浅层热点复读" for item in state.active)


async def test_force_tick_does_not_restore_user_rejected_avoidance(tmp_path) -> None:
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        load_avoidance_state,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    save_avoidance_state(
        tmp_path,
        AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读", status="active")]),
    )
    llm = _PausingAvoidanceLLM()
    speculator = AvoidanceSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()
    assert speculator.user_reject_avoidance("浅层热点复读") is True
    llm.resume.set()
    await task

    state = load_avoidance_state(tmp_path)
    assert all(item.domain != "浅层热点复读" for item in state.active)
    assert any(item.domain == "浅层热点复读" for item in state.cooldown)


async def test_force_tick_avoidance_loader_blocks_duplicate_after_confirmed_item_promoted(
    tmp_path,
) -> None:
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        load_avoidance_state,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    save_avoidance_state(tmp_path, AvoidanceState(active=[]))
    llm = _PausingAvoidanceLLM(domain="浅层热点复读")
    speculator = AvoidanceSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    def _loader() -> list[dict[str, object]]:
        return [
            {
                "domain": "浅层热点复读",
                "response": "confirm",
                "created_at": "2026-06-09T10:00:00",
            }
        ]

    task = asyncio.create_task(
        speculator.force_tick(OnionProfile(), feedback_history_loader=_loader)
    )
    await llm.started.wait()
    llm.resume.set()
    await task

    state = load_avoidance_state(tmp_path)
    assert all(item.domain != "浅层热点复读" for item in state.active)


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


def test_avoidance_novelty_guard_blocks_same_source_topic_boundary():
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceNoveltyGuard,
        AvoidanceState,
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
    )

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="AI工具测评里的跑分式炫技",
                source_mode="positive_boundary",
                source_signal="confirmed_likes: 人工智能、技术应用、编程",
                specifics=[
                    SpeculativeAvoidanceSpecific(name="只晒效果不讲工作流的生成演示"),
                    SpeculativeAvoidanceSpecific(name="只比参数不讲场景的模型测评"),
                ],
            )
        ]
    )

    guard = AvoidanceNoveltyGuard.from_profile_and_state(None, state)

    assert (
        guard.is_duplicate_candidate(
            "AI教程里的模板照抄式伪实战",
            specifics=["只给提示词模板不讲适用边界", "拿现成工作流直接套壳当教学"],
            source_mode="positive_boundary",
            source_signal="confirmed_likes: 人工智能、技术应用、编程",
        )
        is True
    )
    assert (
        guard.is_duplicate_candidate(
            "长视频里的低密度注水闲聊",
            specifics=["十几分钟才进入正题的闲聊视频", "重复总结前文却没有新信息推进"],
            source_mode="style_boundary",
            source_signal="画像: 开始更在意注意力花得值不值",
        )
        is False
    )


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
                                    "用户可能不喜欢没有信息增量、只是在复读热梗和立场的热点内容。"
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


@pytest.mark.asyncio
async def test_avoidance_speculator_force_tick_compacts_redundant_active_boundaries(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    state = AvoidanceState(
        active=[
            SpeculativeAvoidance(
                domain="AI内容里的空泛趋势喊话",
                source_mode="positive_boundary",
                source_signal="confirmed_likes: 人工智能、技术应用、编程",
                confidence=0.73,
                specifics=[
                    SpeculativeAvoidanceSpecific(name="只讲AI将颠覆一切的空泛预测"),
                    SpeculativeAvoidanceSpecific(name="没有案例拆解的模型排行点评"),
                ],
            ),
            SpeculativeAvoidance(
                domain="AI工具测评里的跑分式炫技",
                source_mode="positive_boundary",
                source_signal="confirmed_likes: 人工智能、技术应用、编程",
                confidence=0.68,
                specifics=[
                    SpeculativeAvoidanceSpecific(name="只晒效果不讲工作流的生成演示"),
                    SpeculativeAvoidanceSpecific(name="只比参数不讲场景的模型测评"),
                ],
            ),
            SpeculativeAvoidance(
                domain="AI教程里的模板照抄式伪实战",
                source_mode="positive_boundary",
                source_signal="confirmed_likes: 人工智能、技术应用、编程",
                confidence=0.72,
                specifics=[
                    SpeculativeAvoidanceSpecific(name="只给提示词模板不讲适用边界"),
                    SpeculativeAvoidanceSpecific(name="拿现成工作流直接套壳当教学"),
                    SpeculativeAvoidanceSpecific(name="不解释为什么这样做的步骤堆砌"),
                ],
            ),
            SpeculativeAvoidance(
                domain="长视频里的低密度注水闲聊",
                source_mode="style_boundary",
                source_signal="画像: 开始更在意注意力花得值不值",
                confidence=0.67,
                specifics=[
                    SpeculativeAvoidanceSpecific(name="十几分钟才进入正题的闲聊视频"),
                    SpeculativeAvoidanceSpecific(name="重复总结前文却没有新信息推进"),
                ],
            ),
        ]
    )
    save_avoidance_state(tmp_path, state)

    speculator = AvoidanceSpeculator(llm_service=None, data_dir=tmp_path)

    result = await speculator.force_tick(OnionProfile())
    reloaded = speculator._load_state()

    active_domains = [item.domain for item in reloaded.active]
    assert sum(1 for domain in active_domains if domain.startswith("AI")) == 1
    assert "长视频里的低密度注水闲聊" in active_domains
    assert len(result.rejected) == 2
    assert len(reloaded.cooldown) == 2


@pytest.mark.asyncio
async def test_avoidance_compaction_persists_before_generation_call(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
        load_avoidance_state,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    save_avoidance_state(
        tmp_path,
        AvoidanceState(
            active=[
                SpeculativeAvoidance(
                    domain="AI内容里的空泛趋势喊话",
                    source_mode="positive_boundary",
                    source_signal="confirmed_likes: 人工智能、技术应用、编程",
                    confidence=0.73,
                    specifics=[
                        SpeculativeAvoidanceSpecific(name="只讲AI将颠覆一切的空泛预测"),
                        SpeculativeAvoidanceSpecific(name="没有案例拆解的模型排行点评"),
                    ],
                ),
                SpeculativeAvoidance(
                    domain="AI教程里的模板照抄式伪实战",
                    source_mode="positive_boundary",
                    source_signal="confirmed_likes: 人工智能、技术应用、编程",
                    confidence=0.72,
                    specifics=[
                        SpeculativeAvoidanceSpecific(name="只给提示词模板不讲适用边界"),
                        SpeculativeAvoidanceSpecific(name="拿现成工作流直接套壳当教学"),
                    ],
                ),
                SpeculativeAvoidance(
                    domain="长视频里的低密度注水闲聊",
                    source_mode="style_boundary",
                    source_signal="画像: 开始更在意注意力花得值不值",
                    confidence=0.67,
                    specifics=[
                        SpeculativeAvoidanceSpecific(name="十几分钟才进入正题的闲聊视频"),
                        SpeculativeAvoidanceSpecific(name="重复总结前文却没有新信息推进"),
                    ],
                ),
            ]
        ),
    )

    class InspectingLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            state = load_avoidance_state(tmp_path)
            active_domains = [item.domain for item in state.active]
            assert sum(1 for domain in active_domains if domain.startswith("AI")) == 1
            assert len(state.cooldown) == 1
            return SimpleNamespace(content=json.dumps({"avoidances": []}, ensure_ascii=False))

    speculator = AvoidanceSpeculator(llm_service=InspectingLLMService(), data_dir=tmp_path)

    await speculator.force_tick(OnionProfile())


@pytest.mark.asyncio
async def test_avoidance_speculator_generation_skips_existing_source_topic(tmp_path):
    from openbiliclaw.soul.avoidance_speculator import (
        AvoidanceSpeculator,
        AvoidanceState,
        SpeculativeAvoidance,
        SpeculativeAvoidanceSpecific,
        save_avoidance_state,
    )
    from openbiliclaw.soul.profile import OnionProfile

    save_avoidance_state(
        tmp_path,
        AvoidanceState(
            active=[
                SpeculativeAvoidance(
                    domain="AI工具测评里的跑分式炫技",
                    source_mode="positive_boundary",
                    source_signal="confirmed_likes: 人工智能、技术应用、编程",
                    specifics=[
                        SpeculativeAvoidanceSpecific(name="只晒效果不讲工作流的生成演示"),
                        SpeculativeAvoidanceSpecific(name="只比参数不讲场景的模型测评"),
                    ],
                )
            ]
        ),
    )

    class FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "avoidances": [
                            {
                                "domain": "AI教程里的模板照抄式伪实战",
                                "reason": (
                                    "用户更看重能否真用和原理讲清楚，可能不喜欢模板堆砌内容。"
                                ),
                                "source_mode": "positive_boundary",
                                "source_signal": "confirmed_likes: 人工智能、技术应用、编程",
                                "experience_mode": "hands_on",
                                "entry_load": "heavy",
                                "confidence": 0.72,
                                "specifics": [
                                    "只给提示词模板不讲适用边界",
                                    "拿现成工作流直接套壳当教学",
                                ],
                            },
                            {
                                "domain": "游戏争议里的单边情绪输出",
                                "reason": (
                                    "用户会补看多方解读来判断争议，可能不喜欢只站队宣泄的内容。"
                                ),
                                "source_mode": "style_boundary",
                                "source_signal": "洞察: 面对争议事件倾向多视角拼接",
                                "experience_mode": "people_story",
                                "entry_load": "light",
                                "confidence": 0.69,
                                "specifics": [
                                    "只截取一方说法的争议剪辑",
                                    "不交代时间线的情绪化站队",
                                ],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    speculator = AvoidanceSpeculator(llm_service=FakeLLMService(), data_dir=tmp_path)

    result = await speculator.force_tick(OnionProfile())

    assert [item.domain for item in result.generated] == ["游戏争议里的单边情绪输出"]
