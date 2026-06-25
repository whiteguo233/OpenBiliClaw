"""Signal channel evaluation tests.

Verifies that each of the 6 signal channels correctly routes signals to the
expected profile layers and produces semantically appropriate updates.

Test structure per channel:
1. Routing test        — layers_buffered after a single signal
2. Update test         — inject signals, verify expected layers change
3. Immediate-update    — strong signals (FEEDBACK / DIALOGUE) bypass min_signals gate
4. Composite eval report — all channels scored in one table
"""

from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.pipeline import (
    _BUFFERED_LAYERS,
    _STRONG_SIGNAL_TYPES,
    LayerThreshold,
    OnionLayer,
    ProfileUpdatePipeline,
    SignalType,
    signal_from_dialogue_turn,
    signal_from_feedback,
    signal_from_recommendation_click,
    signals_from_account_sync,
    signals_from_dialogue,
    signals_from_events,
)
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
from openbiliclaw.soul.profile_builder import ProfileBuilder

# ---------------------------------------------------------------------------
# SmartFakeService — routes LLM calls by system_instruction content
# ---------------------------------------------------------------------------

_PREF_RESP = json.dumps(
    {
        "interests": [
            {"name": "科技", "category": "知识", "weight": 0.85, "source": "events"},
            {"name": "AI", "category": "知识", "weight": 0.78, "source": "events"},
        ],
        "style": {
            "preferred_duration": "long",
            "preferred_pace": "moderate",
            "quality_sensitivity": 0.7,
            "humor_preference": 0.3,
            "depth_preference": 0.9,
        },
        "context": {
            "weekday_patterns": "",
            "weekend_patterns": "",
            "time_of_day_patterns": "",
            "session_type": "深度钻研型",
        },
        "exploration_openness": 0.65,
        "disliked_topics": ["标题党"],
        "cognitive_style": ["系统化思考，偏好结构化信息"],
        "favorite_up_users": [],
    },
    ensure_ascii=False,
)

_ROLE_RESP = json.dumps(
    {
        "changed": True,
        "life_stage": "互联网从业者在职期",
        "current_phase": "密集观看AI技术与职场内容，处于职业技能强化阶段",
        "reason": "行为证据显示用户持续关注技术与职业内容",
    },
    ensure_ascii=False,
)

_VALUES_RESP = json.dumps(
    {
        "changed": True,
        "values": ["持续学习", "创造价值", "知识自主"],
        "motivational_drivers": ["技术精进驱动", "内容创作冲动"],
        "reason": "行为证据显示用户重视知识积累与技术探索",
    },
    ensure_ascii=False,
)

_CORE_RESP = json.dumps(
    {
        "changed": False,
        "core_traits": ["好奇心强", "逻辑严谨"],
        "deep_needs": ["对事物运作原理的深层理解"],
        "mbti": {"type": "INTP", "confidence": 0.65, "dimensions": {}},
        "reason": "单批次证据不足以修改核心层，需要更长期一致的模式",
    },
    ensure_ascii=False,
)

_PORTRAIT_RESP = json.dumps(
    {
        "personality_portrait": (
            "这是一个热爱技术探索的用户，对知识有深度渴望，习惯系统化地理解新领域。"
        ),
        "core_traits": ["好奇心强", "逻辑严谨"],
        "cognitive_style": ["系统化思考，偏好结构化信息"],
        "motivational_drivers": ["技术精进驱动"],
        "current_phase": "技术探索期",
        "values": ["持续学习", "创造价值"],
        "life_stage": "互联网从业者",
        "deep_needs": ["对事物运作原理的深层理解"],
        "mbti": {
            "type": "INTP",
            "confidence": 0.65,
            "EI": {"pole": "I", "strength": 0.6},
            "SN": {"pole": "N", "strength": 0.7},
            "TF": {"pole": "T", "strength": 0.6},
            "JP": {"pole": "P", "strength": 0.6},
        },
    },
    ensure_ascii=False,
)


class SmartFakeService:
    """Routes mock LLM responses by detecting layer context in system_instruction."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
        if "生活阶段" in system_instruction:
            content = _ROLE_RESP
        elif "价值观" in system_instruction:
            content = _VALUES_RESP
        elif "核心人格特质" in system_instruction:
            content = _CORE_RESP
        elif "人格画像" in system_instruction and "偏好摘要" in system_instruction:
            content = _PORTRAIT_RESP
        else:
            content = _PREF_RESP
        return LLMResponse(content=content, provider="fake")


# ---------------------------------------------------------------------------
# Test thresholds — high min_signals, zero time-gate
# Strong signals (FEEDBACK / DIALOGUE*) bypass min_signals via pipeline logic.
# ---------------------------------------------------------------------------

_TEST_THRESHOLDS = {
    layer: LayerThreshold(min_signals=10, min_interval_seconds=0, max_buffer_size=200)
    for layer in _BUFFERED_LAYERS
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline(tmp_path: object) -> ProfileUpdatePipeline:
    from pathlib import Path

    service = SmartFakeService()
    memory = MemoryManager(Path(str(tmp_path)))
    memory.initialize()
    return ProfileUpdatePipeline(
        memory=memory,
        preference_analyzer=PreferenceAnalyzer(registry=service),
        profile_builder=ProfileBuilder(registry=service),
        thresholds=_TEST_THRESHOLDS,
    )


def _view_events(n: int = 3) -> list[dict[str, object]]:
    return [
        {"event_type": "view", "title": f"AI技术深度解析第{i}期", "up_name": "科技UP主"}
        for i in range(n)
    ]


def _engagement_events(n: int = 3) -> list[dict[str, object]]:
    return [
        {"event_type": "like", "title": f"深度学习教程第{i}期", "up_name": "ML教程君"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Channel 1: BEHAVIOR_EVENT → SURFACE + INTEREST + ROLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ch1_behavior_event_routing(tmp_path: object) -> None:
    """One BEHAVIOR_EVENT should be buffered to surface, interest, and role."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signals_from_events(_view_events(1))[0])

    assert "surface" in result.layers_buffered
    assert "interest" in result.layers_buffered
    assert "role" in result.layers_buffered
    assert "values" not in result.layers_buffered, "BEHAVIOR_EVENT must NOT route to values"


@pytest.mark.asyncio
async def test_ch1_behavior_event_triggers_interest_and_role(tmp_path: object) -> None:
    """10 BEHAVIOR_EVENTs should trigger Interest and Role updates."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest_batch(signals_from_events(_view_events(10)))

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.INTEREST in updated_layers, (
        f"Interest should be updated after BEHAVIOR_EVENTs. Got: {updated_layers}"
    )
    assert OnionLayer.ROLE in updated_layers, (
        f"Role should be updated after BEHAVIOR_EVENTs. Got: {updated_layers}"
    )
    interest_result = next(r for r in result.layers_updated if r.layer == OnionLayer.INTEREST)
    assert interest_result.changed, "Interest layer should report changed=True"
    assert any("科技" in c or "AI" in c for c in interest_result.changes), (
        f"Expected tech-related interest changes, got: {interest_result.changes}"
    )


# ---------------------------------------------------------------------------
# Channel 2: ENGAGEMENT_EVENT → INTEREST + SURFACE + ROLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ch2_engagement_event_routing(tmp_path: object) -> None:
    """like/coin/favorite events should route as ENGAGEMENT_EVENT to interest+surface+role."""
    pipeline = _make_pipeline(tmp_path)
    signals = signals_from_events(_engagement_events(1))
    assert signals[0].signal_type == SignalType.ENGAGEMENT_EVENT, (
        "like event should be classified as ENGAGEMENT_EVENT"
    )
    result = await pipeline.ingest(signals[0])

    assert "interest" in result.layers_buffered
    assert "surface" in result.layers_buffered
    assert "role" in result.layers_buffered
    assert "values" not in result.layers_buffered


@pytest.mark.asyncio
async def test_ch2_engagement_event_updates_interest(tmp_path: object) -> None:
    """10 ENGAGEMENT_EVENTs should update Interest layer with new topics."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest_batch(signals_from_events(_engagement_events(10)))

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.INTEREST in updated_changed, (
        f"Interest should actually change after engagement events. Changed: {updated_changed}"
    )


# ---------------------------------------------------------------------------
# Channel 3: FEEDBACK → INTEREST + SURFACE + VALUES  (strong signal: immediate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ch3_feedback_routing(tmp_path: object) -> None:
    """FEEDBACK should route to values (not role) — key distinguisher from BEHAVIOR_EVENT."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signal_from_feedback("like", "测试视频", "好内容"))

    assert "interest" in result.layers_buffered
    assert "surface" in result.layers_buffered
    assert "values" in result.layers_buffered
    assert "role" not in result.layers_buffered, "FEEDBACK routes to VALUES, not ROLE"


@pytest.mark.asyncio
async def test_ch3_feedback_single_signal_immediate_update(tmp_path: object) -> None:
    """A single FEEDBACK signal must bypass the min_signals gate and update immediately.

    Threshold is set to min_signals=10, so only strong-signal bypass can trigger this.
    """
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signal_from_feedback("like", "深度AI内容", "强正向反馈"))

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.INTEREST in updated_layers, (
        "Single FEEDBACK must immediately update INTEREST (strong-signal bypass). "
        f"Got: {updated_layers}"
    )
    assert OnionLayer.VALUES in updated_layers, (
        "Single FEEDBACK must immediately update VALUES (strong-signal bypass). "
        f"Got: {updated_layers}"
    )


@pytest.mark.asyncio
async def test_ch3_feedback_triggers_values_update(tmp_path: object) -> None:
    """FEEDBACK should change the Values layer with content-appropriate values."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signal_from_feedback("like", "深度内容", "正向反馈"))

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.VALUES in updated_layers, (
        f"VALUES should be triggered by FEEDBACK. Got: {updated_layers}"
    )
    values_result = next(r for r in result.layers_updated if r.layer == OnionLayer.VALUES)
    assert values_result.changed, "Values layer should actually change"
    assert any("学习" in c or "价值" in c or "驱动" in c for c in values_result.changes), (
        f"Changes should mention values/drivers. Got: {values_result.changes}"
    )


# ---------------------------------------------------------------------------
# Channel 4: DIALOGUE_TURN → SURFACE + INTEREST  (strong signal: immediate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ch4_dialogue_turn_routing(tmp_path: object) -> None:
    """DIALOGUE_TURN should buffer to both surface and interest (explicit user intent)."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signal_from_dialogue_turn("推荐个科技视频", "好的，这是推荐..."))

    assert "surface" in result.layers_buffered
    assert "interest" in result.layers_buffered, (
        "DIALOGUE_TURN must reach interest — dialogue reveals explicit preferences"
    )
    assert "role" not in result.layers_buffered
    assert "values" not in result.layers_buffered


@pytest.mark.asyncio
async def test_ch4_dialogue_turn_single_signal_immediate_interest(tmp_path: object) -> None:
    """A single DIALOGUE_TURN must bypass min_signals and immediately update INTEREST.

    Threshold is set to min_signals=10, so only strong-signal bypass can trigger this.
    """
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signal_from_dialogue_turn("我最近很想了解AI技术", "好的，我来介绍一下...")
    )

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.INTEREST in updated_layers, (
        "Single DIALOGUE_TURN must immediately update INTEREST (strong-signal bypass). "
        f"Got: {updated_layers}"
    )


@pytest.mark.asyncio
async def test_ch4_dialogue_turn_skips_role_values_core(tmp_path: object) -> None:
    """DIALOGUE_TURN must never directly touch role, values, or core."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(signal_from_dialogue_turn("随便聊聊", "好的"))

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.ROLE not in updated_layers
    assert OnionLayer.VALUES not in updated_layers
    assert OnionLayer.CORE not in updated_layers


# ---------------------------------------------------------------------------
# Channel 5: DIALOGUE_INSIGHT → dynamic routing by kind  (strong signal: immediate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_layer",
    [
        ("interest", OnionLayer.INTEREST),
        ("dislike", OnionLayer.INTEREST),
        ("value", OnionLayer.VALUES),
        ("goal", OnionLayer.ROLE),
        ("state", OnionLayer.CORE),
    ],
)
async def test_ch5_dialogue_insight_routing(
    tmp_path: object, kind: str, expected_layer: OnionLayer
) -> None:
    """DIALOGUE_INSIGHT routing depends on the 'kind' field."""
    pipeline = _make_pipeline(tmp_path)
    signals = signals_from_dialogue(
        [{"kind": kind, "content": f"测试insight: {kind}", "confidence": 0.9}]
    )
    result = await pipeline.ingest(signals[0])

    assert expected_layer.value in result.layers_buffered, (
        f"kind={kind!r} should buffer {expected_layer.value}. Got: {result.layers_buffered}"
    )


@pytest.mark.asyncio
async def test_ch5_insight_interest_kind_updates_interest(tmp_path: object) -> None:
    """interest-kind insight must immediately update and change INTEREST."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signals_from_dialogue(
            [{"kind": "interest", "content": "用户明确表示喜欢AI技术内容", "confidence": 0.95}]
        )[0]
    )

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.INTEREST in updated_changed, (
        f"interest-kind insight must change INTEREST. Changed: {updated_changed}"
    )
    interest_result = next(r for r in result.layers_updated if r.layer == OnionLayer.INTEREST)
    assert any("科技" in c or "AI" in c for c in interest_result.changes), (
        f"Interest changes should reflect AI/tech content. Got: {interest_result.changes}"
    )


@pytest.mark.asyncio
async def test_ch5_insight_dislike_kind_updates_interest(tmp_path: object) -> None:
    """dislike-kind insight must immediately update INTEREST (disliked topics path)."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signals_from_dialogue(
            [{"kind": "dislike", "content": "用户明确表示不喜欢标题党内容", "confidence": 0.92}]
        )[0]
    )

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.INTEREST in updated_changed, (
        f"dislike-kind insight must change INTEREST. Changed: {updated_changed}"
    )
    interest_result = next(r for r in result.layers_updated if r.layer == OnionLayer.INTEREST)
    all_changes = " ".join(interest_result.changes)
    assert "讨厌" in all_changes or "标题党" in all_changes, (
        f"Dislike changes should mention dislikes. Got: {interest_result.changes}"
    )


@pytest.mark.asyncio
async def test_ch5_insight_goal_kind_updates_role(tmp_path: object) -> None:
    """goal-kind insight must immediately update and change ROLE."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signals_from_dialogue(
            [{"kind": "goal", "content": "用户希望转型为AI工程师", "confidence": 0.88}]
        )[0]
    )

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.ROLE in updated_changed, (
        f"goal-kind insight must change ROLE. Changed: {updated_changed}"
    )
    role_result = next(r for r in result.layers_updated if r.layer == OnionLayer.ROLE)
    all_changes = " ".join(role_result.changes)
    assert "life_stage" in all_changes or "current_phase" in all_changes, (
        f"Role changes should mention life_stage or current_phase. Got: {role_result.changes}"
    )


@pytest.mark.asyncio
async def test_ch5_insight_value_kind_updates_values(tmp_path: object) -> None:
    """value-kind insight must immediately update and change VALUES."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signals_from_dialogue(
            [{"kind": "value", "content": "用户重视持续学习和创造价值", "confidence": 0.9}]
        )[0]
    )

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.VALUES in updated_changed, (
        f"value-kind insight must change VALUES. Changed: {updated_changed}"
    )


@pytest.mark.asyncio
async def test_ch5_insight_state_kind_triggers_core(tmp_path: object) -> None:
    """state-kind insight must immediately trigger a Core update attempt."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signals_from_dialogue(
            [{"kind": "state", "content": "用户当前感到焦虑和迷茫", "confidence": 0.85}]
        )[0]
    )

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.CORE in updated_layers, (
        "state-kind insight must trigger Core update (even if conservative changed=False). "
        f"Triggered: {updated_layers}"
    )


# ---------------------------------------------------------------------------
# Channel 6: ACCOUNT_SNAPSHOT → INTEREST + SURFACE + ROLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ch6_account_snapshot_routing(tmp_path: object) -> None:
    """ACCOUNT_SNAPSHOT should route to interest, surface, and role (not values)."""
    pipeline = _make_pipeline(tmp_path)
    signals = signals_from_account_sync(
        [{"event_type": "account_sync", "title": "账号同步", "subscribed": ["AI频道"]}]
    )
    result = await pipeline.ingest(signals[0])

    assert "interest" in result.layers_buffered
    assert "surface" in result.layers_buffered
    assert "role" in result.layers_buffered
    assert "values" not in result.layers_buffered


@pytest.mark.asyncio
async def test_ch6_account_snapshot_updates_interest_and_role(tmp_path: object) -> None:
    """10 ACCOUNT_SNAPSHOT signals should update Interest and Role layers."""
    pipeline = _make_pipeline(tmp_path)
    signals = signals_from_account_sync(
        [
            {"event_type": "account_sync", "title": f"账号同步{i}", "up_name": f"UP主{i}"}
            for i in range(10)
        ]
    )
    result = await pipeline.ingest_batch(signals)

    updated_changed = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.INTEREST in updated_changed, (
        f"ACCOUNT_SNAPSHOT must update Interest. Changed: {updated_changed}"
    )


# ---------------------------------------------------------------------------
# Channel 7: RECOMMENDATION_CLICK → INTEREST + SURFACE  (strong signal)
# ---------------------------------------------------------------------------


def test_ch7_recommendation_click_is_strong_signal() -> None:
    """RECOMMENDATION_CLICK must be registered in the strong-signal set."""
    assert SignalType.RECOMMENDATION_CLICK in _STRONG_SIGNAL_TYPES


def test_ch7_recommendation_click_routing_targets_interest_and_surface() -> None:
    """A click signal should target INTEREST and SURFACE only (no ROLE/VALUES)."""
    signal = signal_from_recommendation_click(bvid="BV1xxx", title="AI原理讲解")
    assert signal.signal_type == SignalType.RECOMMENDATION_CLICK
    assert OnionLayer.INTEREST in signal.target_layers
    assert OnionLayer.SURFACE in signal.target_layers
    assert OnionLayer.ROLE not in signal.target_layers
    assert OnionLayer.VALUES not in signal.target_layers
    assert OnionLayer.CORE not in signal.target_layers


@pytest.mark.asyncio
async def test_ch7_recommendation_click_buffer_routing(tmp_path: object) -> None:
    """ingest() should buffer a click signal into interest and surface."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signal_from_recommendation_click(
            bvid="BV42test",
            title="深入理解Transformer",
            recommendation_id=7,
            topic_label="AI技术",
            up_name="ML教程君",
        )
    )

    assert "interest" in result.layers_buffered
    assert "surface" in result.layers_buffered
    assert "role" not in result.layers_buffered
    assert "values" not in result.layers_buffered


@pytest.mark.asyncio
async def test_ch7_recommendation_click_single_signal_immediate_update(
    tmp_path: object,
) -> None:
    """A single RECOMMENDATION_CLICK must bypass min_signals=10 and update now.

    Threshold is min_signals=10; only a strong-signal bypass explains update.
    """
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signal_from_recommendation_click(
            bvid="BVclick",
            title="强正向AI内容",
            recommendation_id=1,
        )
    )

    updated_layers = {r.layer for r in result.layers_updated}
    assert OnionLayer.INTEREST in updated_layers, (
        "Single recommendation click must immediately update INTEREST "
        f"(strong-signal bypass). Got: {updated_layers}"
    )
    # SURFACE may or may not actually *change* (depends on view_count>=2
    # compute path), but it should be TRIGGERED for evaluation.
    layers_triggered = {r.layer for r in result.layers_updated}
    assert OnionLayer.SURFACE in layers_triggered


@pytest.mark.asyncio
async def test_ch7_recommendation_click_changes_interest_content(
    tmp_path: object,
) -> None:
    """The click's title/topic should actually affect the interest layer."""
    pipeline = _make_pipeline(tmp_path)
    result = await pipeline.ingest(
        signal_from_recommendation_click(
            bvid="BVai",
            title="Transformer架构深度解析",
            recommendation_id=99,
            topic_label="AI",
            up_name="ML教程君",
        )
    )

    changed_layers = {r.layer for r in result.layers_updated if r.changed}
    assert OnionLayer.INTEREST in changed_layers, (
        f"Click should actually change INTEREST. Changed: {changed_layers}"
    )
    interest_result = next(r for r in result.layers_updated if r.layer == OnionLayer.INTEREST)
    assert any("科技" in c or "AI" in c for c in interest_result.changes), (
        f"Click-induced interest changes should reflect AI/tech. Got: {interest_result.changes}"
    )


# ---------------------------------------------------------------------------
# Key distinction: FEEDBACK vs BEHAVIOR_EVENT (VALUES vs ROLE split)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_targets_values_not_role_vs_behavior(tmp_path: object) -> None:
    """FEEDBACK→VALUES but not ROLE; BEHAVIOR_EVENT→ROLE but not VALUES."""
    from pathlib import Path

    pipeline_fb = _make_pipeline(Path(str(tmp_path)) / "fb")
    pipeline_bh = _make_pipeline(Path(str(tmp_path)) / "bh")

    fb_result = await pipeline_fb.ingest(signal_from_feedback("like", "测试", ""))
    bh_result = await pipeline_bh.ingest(
        signals_from_events([{"event_type": "view", "title": "测试"}])[0]
    )

    assert "values" in fb_result.layers_buffered
    assert "role" not in fb_result.layers_buffered
    assert "role" in bh_result.layers_buffered
    assert "values" not in bh_result.layers_buffered


# ---------------------------------------------------------------------------
# Comprehensive eval report (all 6 channels × routing + update scores)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_eval_report(tmp_path: object, capsys: object) -> None:
    """
    End-to-end evaluation of all 6 channels.

    Scores:
      routing_score — fraction of expected layers that were buffered (0.0–1.0)
      update_score  — fraction of expected layers that actually changed (0.0–1.0)
                      (layers with no expected changes score 1.0 automatically)

    Strong signals (FEEDBACK, DIALOGUE_TURN, DIALOGUE_INSIGHT) use 1-signal batches
    to verify the immediate-update bypass; others use 10-signal batches.
    """
    from pathlib import Path

    channel_specs = [
        {
            "name": "BEHAVIOR_EVENT",
            "signals": lambda: signals_from_events(_view_events(10)),
            "expected_buffered": {"surface", "interest", "role"},
            "expected_changed": {"interest", "role"},
        },
        {
            "name": "ENGAGEMENT_EVENT",
            "signals": lambda: signals_from_events(_engagement_events(10)),
            "expected_buffered": {"interest", "surface", "role"},
            "expected_changed": {"interest", "role"},
        },
        {
            "name": "FEEDBACK (1 signal)",
            "signals": lambda: [signal_from_feedback("like", "深度AI内容", "强正向反馈")],
            "expected_buffered": {"interest", "surface", "values"},
            "expected_changed": {"interest", "values"},
        },
        {
            "name": "DIALOGUE_TURN (1 signal)",
            "signals": lambda: [signal_from_dialogue_turn("我想了解AI技术", "好的...")],
            "expected_buffered": {"surface", "interest"},
            "expected_changed": {"interest"},
        },
        {
            "name": "DIALOGUE_INSIGHT/value (1)",
            "signals": lambda: signals_from_dialogue(
                [{"kind": "value", "content": "用户重视持续学习", "confidence": 0.9}]
            ),
            "expected_buffered": {"values"},
            "expected_changed": {"values"},
        },
        {
            "name": "ACCOUNT_SNAPSHOT",
            "signals": lambda: signals_from_account_sync(
                [
                    {"event_type": "account_sync", "title": f"账号同步{i}", "up_name": f"UP{i}"}
                    for i in range(10)
                ]
            ),
            "expected_buffered": {"interest", "surface", "role"},
            "expected_changed": {"interest", "role"},
        },
    ]

    print("\n\n=== 渠道评测报告 ===")
    header = f"{'渠道':<28} {'路由分':>6} {'更新分':>6}  {'实际缓冲':<28} {'实际变更':<28} 结果"
    print(header)
    print("─" * len(header))

    all_passed = True
    for i, spec in enumerate(channel_specs):
        pipeline = _make_pipeline(Path(str(tmp_path)) / f"ch{i}")
        signals = spec["signals"]()
        result = await pipeline.ingest_batch(signals)

        actual_buffered = set(result.layers_buffered)
        actual_changed = {r.layer.value for r in result.layers_updated if r.changed}

        expected_buffered: set[str] = spec["expected_buffered"]
        expected_changed: set[str] = spec["expected_changed"]

        routing_score = (
            len(expected_buffered & actual_buffered) / len(expected_buffered)
            if expected_buffered
            else 1.0
        )
        update_score = (
            len(expected_changed & actual_changed) / len(expected_changed)
            if expected_changed
            else 1.0
        )

        passed = routing_score == 1.0 and update_score == 1.0
        if not passed:
            all_passed = False

        status = "PASS ✓" if passed else "FAIL ✗"
        print(
            f"{spec['name']:<28} {routing_score:>6.2f} {update_score:>6.2f}  "
            f"{str(sorted(actual_buffered)):<28} {str(sorted(actual_changed)):<28} {status}"
        )

        if not passed:
            if routing_score < 1.0:
                print(f"  → 路由缺失: {expected_buffered - actual_buffered}")
            if update_score < 1.0:
                print(f"  → 未变更层: {expected_changed - actual_changed}")

    print("─" * len(header))
    print(f"整体结果: {'全部通过 ✓' if all_passed else '存在失败项 ✗'}\n")

    assert all_passed, "部分渠道未达到评测标准，请查看上方报告"
