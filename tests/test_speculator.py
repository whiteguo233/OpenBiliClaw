"""Tests for speculative interest lifecycle."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from openbiliclaw.soul import speculator as speculator_module
from openbiliclaw.soul.speculator import (
    CooldownEntry,
    InterestSpeculator,
    SpeculativeInterest,
    SpeculativeSpecific,
    SpeculativeState,
    _event_matches_speculation,
    _tokenize,
    choose_next_probe_candidate,
    expire_stale,
    load_speculative_state,
    observe_events,
    promote_ready,
    save_speculative_state,
)


class _PausingSpeculationLLM:
    def __init__(self, domain: str = "城市基础设施观察") -> None:
        self.domain = domain
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def complete_structured_task(self, **_kwargs: object) -> object:
        self.started.set()
        await self.resume.wait()
        return SimpleNamespace(
            content=json.dumps(
                {
                    "interests": [
                        {
                            "domain": self.domain,
                            "category": "人文",
                            "reason": "用户喜欢系统结构拆解,这个方向提供城市系统视角。",
                            "specifics": ["排水系统", "道路层级"],
                            "confidence": 0.55,
                            "probe_mode": "near",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


def test_tokenize_basic():
    tokens = _tokenize("博弈论 科普 视频")
    assert "博弈论" in tokens
    assert "科普" in tokens
    assert "视频" in tokens


def test_tokenize_filters_short():
    tokens = _tokenize("a 好 hello")
    assert "a" not in tokens
    assert "好" not in tokens
    assert "hello" in tokens


def test_probe_novelty_guard_matches_profile_specifics():
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
                    specifics=[
                        InterestSpecific(name="ComfyUI工作流"),
                        InterestSpecific(name="图像生成实战"),
                    ],
                )
            ]
        )
    )
    guard = speculator_module.ProbeNoveltyGuard.from_profile_and_state(
        profile,
        SpeculativeState(),
    )

    assert guard.is_duplicate_domain("AI") is True
    assert guard.is_duplicate_domain("ComfyUI工作流拆解") is True
    assert guard.filter_specifics(["ComfyUI工作流", "Stable Diffusion LoRA"]) == [
        "Stable Diffusion LoRA"
    ]


def test_probe_novelty_guard_matches_recent_probe_history():
    guard = speculator_module.ProbeNoveltyGuard.from_profile_and_state(
        None,
        SpeculativeState(),
        probed_domains={"城市漫游"},
    )

    assert guard.is_duplicate_domain("城市漫游路线") is True


def test_probe_novelty_guard_matches_negative_feedback_history():
    guard = speculator_module.ProbeNoveltyGuard.from_profile_and_state(
        None,
        SpeculativeState(),
        feedback_history=[
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "specifics": ["老街路线"],
            },
            {
                "domain": "手作模型",
                "response": "chat_neutral",
                "specifics": ["拼装过程"],
            },
        ],
    )

    assert guard.is_duplicate_domain("城市漫游隐藏路线") is True
    assert guard.filter_specifics(["老街路线", "城市声音采样"]) == ["城市声音采样"]
    assert guard.is_duplicate_domain("手作模型制作") is False


def test_choose_next_probe_skips_negative_feedback_domain():
    chosen = choose_next_probe_candidate(
        [
            SimpleNamespace(
                domain="城市漫游隐藏路线",
                confirmation_count=0,
                weight=0.9,
                confidence=0.9,
                experience_mode="wander_observe",
                entry_load="light",
            ),
            SimpleNamespace(
                domain="手工模型制作",
                confirmation_count=0,
                weight=0.2,
                confidence=0.2,
                experience_mode="hands_on",
                entry_load="light",
            ),
        ],
        feedback_history=[
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "axis": "wander_observe|light",
            }
        ],
    )

    assert chosen is not None
    assert chosen.domain == "手工模型制作"


def test_choose_next_probe_prefers_axis_without_negative_feedback():
    chosen = choose_next_probe_candidate(
        [
            SimpleNamespace(
                domain="城市夜景摄影",
                confirmation_count=0,
                weight=0.9,
                confidence=0.9,
                experience_mode="aesthetic",
                entry_load="light",
            ),
            SimpleNamespace(
                domain="手作模型制作",
                confirmation_count=0,
                weight=0.2,
                confidence=0.2,
                experience_mode="hands_on",
                entry_load="light",
            ),
        ],
        feedback_history=[
            {
                "domain": "完全不同的旧方向",
                "response": "chat_negative",
                "axis": "aesthetic|light",
            }
        ],
    )

    assert chosen is not None
    assert chosen.domain == "手作模型制作"


def test_choose_next_probe_candidate_prefers_fresh_probe_mode_after_filters():
    near = SpeculativeInterest(domain="近", probe_mode="near", weight=0.9)
    bridge = SpeculativeInterest(domain="桥", probe_mode="bridge", weight=0.6)

    chosen = choose_next_probe_candidate(
        [near, bridge],
        probed_probe_modes={"near"},
    )

    assert chosen is bridge


def test_select_diverse_candidates_avoids_negative_feedback_axis():
    candidates = [
        SpeculativeInterest(
            domain="建筑旅行 vlog",
            confidence=0.9,
            weight=0.9,
            experience_mode="wander_observe",
            entry_load="light",
        ),
        SpeculativeInterest(
            domain="咖啡馆空间设计",
            confidence=0.4,
            weight=0.4,
            experience_mode="aesthetic",
            entry_load="light",
        ),
        SpeculativeInterest(
            domain="本地 Stable Diffusion 工作台",
            confidence=0.35,
            weight=0.35,
            experience_mode="hands_on",
            entry_load="heavy",
        ),
    ]

    selected = speculator_module._select_diverse_candidates(
        candidates,
        limit=2,
        existing=[
            SpeculativeInterest(
                domain="结构化知识讲解",
                experience_mode="knowledge",
                entry_load="heavy",
            )
        ],
        feedback_history=[
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "axis": "wander_observe|light",
            }
        ],
    )

    assert [item.domain for item in selected] == [
        "咖啡馆空间设计",
        "本地 Stable Diffusion 工作台",
    ]


def test_select_diverse_candidates_enforces_probe_mode_quota_when_possible():
    candidates = [
        SpeculativeInterest(domain="近1", probe_mode="near", confidence=0.9, weight=0.9),
        SpeculativeInterest(domain="近2", probe_mode="near", confidence=0.8, weight=0.8),
        SpeculativeInterest(domain="横向", probe_mode="lateral", confidence=0.6, weight=0.6),
        SpeculativeInterest(domain="桥接", probe_mode="bridge", confidence=0.55, weight=0.55),
    ]

    selected = speculator_module._select_diverse_candidates(candidates, limit=3)

    assert any(item.probe_mode != "near" for item in selected)
    assert sum(1 for item in selected if item.probe_mode == "near") <= 2


def _profile_with_ai_specifics():
    from openbiliclaw.soul.profile import (
        InterestDomain,
        InterestLayer,
        InterestSpecific,
        OnionProfile,
    )

    return OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="AI",
                    specifics=[
                        InterestSpecific(name="ComfyUI工作流"),
                        InterestSpecific(name="图像生成实战"),
                    ],
                )
            ]
        )
    )


async def test_speculator_generate_drops_duplicate_profile_interest():
    class _FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "speculations": [
                            {
                                "domain": "ComfyUI工作流拆解",
                                "category": "AI",
                                "reason": (
                                    "你已经在图像生成方向有持续观看，这个方向只是更具体的工作流拆解。"
                                ),
                                "confidence": 0.5,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["ComfyUI工作流", "节点搭建技巧"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        speculator = InterestSpeculator(
            llm_service=_FakeLLMService(),
            data_dir=Path(tmpdir),
        )

        result = await speculator.force_tick(_profile_with_ai_specifics())

        assert result.generated == []


def test_speculator_ingest_seed_skips_existing_profile_interest():
    with tempfile.TemporaryDirectory() as tmpdir:
        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=Path(tmpdir),
        )

        added = speculator.ingest_seeds(
            [{"name": "ComfyUI工作流拆解", "category": "AI", "weight": 0.5}],
            profile=_profile_with_ai_specifics(),
        )

        assert added == 0


# ---------------------------------------------------------------------------
# Event matching
# ---------------------------------------------------------------------------


def test_event_matches_domain_substring():
    spec = SpeculativeInterest(domain="博弈论", category="知识")
    event = {"title": "纳什均衡与博弈论入门", "tags": ""}
    assert _event_matches_speculation(event, spec) is True


def test_event_matches_category_substring():
    spec = SpeculativeInterest(domain="量子计算", category="前沿科技")
    event = {"title": "前沿科技趋势解读", "tags": ""}
    assert _event_matches_speculation(event, spec) is True


def test_event_no_match():
    spec = SpeculativeInterest(domain="建筑叙事", category="人文")
    event = {"title": "今天吃什么", "tags": "美食"}
    assert _event_matches_speculation(event, spec) is False


def test_event_matches_token_overlap():
    spec = SpeculativeInterest(domain="科技伦理与AI治理", category="科技哲学")
    event = {"title": "AI治理的未来走向", "tags": "科技"}
    assert _event_matches_speculation(event, spec) is True


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def test_observe_events_increments():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(domain="博弈论", category="知识", status="active"),
        ]
    )
    events = [
        {"title": "博弈论科普：囚徒困境", "event_type": "view"},
        {"title": "今天吃什么", "event_type": "view"},
        {"title": "博弈论在经济学中的应用", "event_type": "view"},
    ]
    updated, count = observe_events(events, state)
    assert count == 2
    assert updated.active[0].confirmation_count == 2
    assert len(updated.active[0].confirming_events) == 2


def test_observe_skips_non_active():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(domain="博弈论", status="promoted"),
        ]
    )
    events = [{"title": "博弈论科普", "event_type": "view"}]
    _, count = observe_events(events, state)
    assert count == 0


def test_observe_matches_long_chinese_composite_phrase():
    """Probes like 'AI图像生成工作流深度拆解' (no delimiters, no
    whitespace) used to never match real titles because the
    delimiter-split / whitespace-tokenization paths both yielded ≤1
    keyword. The bigram fallback fixes that.
    """
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="AI图像生成工作流深度拆解",
                category="技术",
                status="active",
            ),
        ]
    )
    events = [
        {"title": "ComfyUI入门：从图像生成到工作流", "event_type": "view"},
        {"title": "Stable Diffusion 工作流分享", "event_type": "view"},
        {
            "title": "深度学习入门指南",
            "event_type": "view",
        },  # only 深度 — 1 bigram, below threshold
        {"title": "吃饭睡觉打豆豆", "event_type": "view"},
    ]
    _, count = observe_events(events, state)
    assert count == 2  # the two workflow-themed titles, not 深度学习 or 打豆豆


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_promote_ready():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="博弈论",
                status="active",
                confirmation_count=3,
                confirmation_threshold=3,
            ),
            SpeculativeInterest(
                domain="建筑叙事",
                status="active",
                confirmation_count=1,
                confirmation_threshold=3,
            ),
        ]
    )
    promoted, updated = promote_ready(state)
    assert len(promoted) == 1
    assert promoted[0].domain == "博弈论"
    assert promoted[0].status == "promoted"
    assert len(updated.active) == 1
    assert updated.active[0].domain == "建筑叙事"
    assert updated.total_promoted == 1


def test_promote_none_ready():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(domain="X", confirmation_count=1, confirmation_threshold=3),
        ]
    )
    promoted, updated = promote_ready(state)
    assert promoted == []
    assert len(updated.active) == 1


def test_promote_ready_handles_user_confirmed_status():
    """Regression: ``user_confirm_speculation`` sets ``status="confirmed"``
    (not "active") and pre-fills confirmation_count to threshold. Before
    the fix, ``promote_ready`` only matched ``status == "active"``, so
    confirmed rows piled up in ``state.active`` indefinitely — eventually
    wedging probe generation because ``len(state.active) >= max_active``
    short-circuited ``_generate``. Now both paths converge here."""
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="用户主动确认的方向",
                status="confirmed",
                confirmation_count=3,
                confirmation_threshold=3,
            ),
            SpeculativeInterest(
                domain="自然累积未到阈值",
                status="active",
                confirmation_count=1,
                confirmation_threshold=3,
            ),
            SpeculativeInterest(
                domain="自然累积已到阈值",
                status="active",
                confirmation_count=3,
                confirmation_threshold=3,
            ),
        ]
    )
    promoted, updated = promote_ready(state)

    promoted_domains = sorted(s.domain for s in promoted)
    assert promoted_domains == ["用户主动确认的方向", "自然累积已到阈值"]
    # All promoted rows end up with status="promoted" so downstream
    # consumers (pipeline._run_speculator_tick) can append them to
    # profile.interest.likes uniformly.
    assert all(s.status == "promoted" for s in promoted)
    # The active list keeps only the still-incubating row.
    assert [s.domain for s in updated.active] == ["自然累积未到阈值"]
    assert updated.total_promoted == 2


async def test_force_tick_unblocked_when_active_full_of_confirmed(monkeypatch, tmp_path):
    """Regression for the 'probe wedge' bug observed in production:
    a profile with N=max_active rows all in ``status="confirmed"``
    (because the user kept clicking 喜欢 but no tick ever ran) made
    ``force_tick`` return ``generated=0`` forever. After the
    ``promote_ready`` fix, the next tick must (1) drain those confirmed
    rows out of ``state.active`` and (2) generate fresh speculations
    into the now-empty slots."""
    from openbiliclaw.soul.profile import OnionProfile

    # Seed the on-disk state with 5 confirmed rows occupying every active slot.
    state_dir = tmp_path / "memory"
    state_dir.mkdir()
    state_file = state_dir / "speculative_state.json"
    confirmed_rows = [
        {
            "domain": f"已确认方向{i}",
            "category": "",
            "reason": "user clicked 喜欢",
            "confidence": 0.5,
            "weight": 0.5,
            "created_at": datetime.now().isoformat(),
            "ttl_days": 3,
            "confirmation_threshold": 3,
            "confirmation_count": 3,
            "status": "confirmed",
            "specifics": [{"name": "x", "confirmation_count": 0}],
            "confirming_events": ["user_confirmed"],
        }
        for i in range(5)
    ]
    state_file.write_text(
        json.dumps(
            {
                "active": confirmed_rows,
                "cooldown": [],
                "total_rejected": 0,
                "total_promoted": 0,
                "last_generation_at": None,
            }
        )
    )

    # Stub LLM service to return 5 fresh probes; without the fix this
    # never gets called because _generate short-circuits on active full.
    fake_speculations = [
        {
            "domain": f"全新方向{i}",
            "category": "科技",
            "reason": "这是一段足够长的理由 用于通过质量门槛 的占位文本 abc",
            "confidence": 0.55,
            "specifics": ["sub-a", "sub-b"],
            "experience_mode": "knowledge",
            "entry_load": "light",
        }
        for i in range(5)
    ]

    class _FakeResponse:
        def __init__(self, content):
            self.content = content

    class _FakeLLMService:
        async def complete_structured_task(self, **_kw):
            return _FakeResponse(json.dumps({"speculations": fake_speculations}))

    speculator = InterestSpeculator(
        llm_service=_FakeLLMService(),
        data_dir=state_dir.parent,
        max_active=5,
    )

    result = await speculator.force_tick(OnionProfile())

    # The 5 confirmed rows graduated out of active...
    assert len(result.promoted) == 5
    assert all(s.status == "promoted" for s in result.promoted)
    # ...and 5 brand-new probes filled the freed slots.
    assert len(result.generated) == 5
    new_domains = {s.domain for s in result.generated}
    assert new_domains == {f"全新方向{i}" for i in range(5)}
    # Final on-disk state: only the new probes remain in the active list.
    persisted = speculator._load_state()
    persisted_active_domains = {s.domain for s in persisted.active if s.status == "active"}
    assert persisted_active_domains == {f"全新方向{i}" for i in range(5)}


async def test_force_tick_fills_challenge_slots_when_near_pool_full(tmp_path):
    from openbiliclaw.soul.profile import OnionProfile

    data_dir = tmp_path / "memory"
    data_dir.mkdir()
    near_rows = [
        SpeculativeInterest(
            domain=f"近距方向{i}",
            reason="已有普通兴趣探针",
            confidence=0.5,
            weight=0.5,
            created_at=datetime.now().isoformat(),
            ttl_days=3,
            status="active",
            probe_mode="near",
            specifics=[
                SpeculativeSpecific(name=f"existing-{i}-a"),
                SpeculativeSpecific(name=f"existing-{i}-b"),
            ],
        ).to_dict()
        for i in range(5)
    ]
    save_speculative_state(
        data_dir.parent,
        SpeculativeState(active=[SpeculativeInterest.from_dict(row) for row in near_rows]),
    )

    fake_speculations = [
        {
            "domain": "近距额外方向",
            "category": "娱乐",
            "reason": "这是一段足够长的普通近距方向理由，用来证明普通池已经满了不会再塞。",
            "confidence": 0.66,
            "specifics": ["sub-a", "sub-b"],
            "experience_mode": "knowledge",
            "entry_load": "light",
            "probe_mode": "near",
        },
        {
            "domain": "横向挑战方向",
            "category": "生活",
            "reason": "这是一段足够长的横向挑战理由，用来测试挑战池可以独立进入。",
            "confidence": 0.65,
            "specifics": ["横向子方向A", "横向子方向B"],
            "experience_mode": "wander_observe",
            "entry_load": "light",
            "probe_mode": "lateral",
        },
        {
            "domain": "桥接挑战方向",
            "category": "科技",
            "reason": "这是一段足够长的桥接挑战理由，用来测试挑战池可以独立进入。",
            "confidence": 0.64,
            "specifics": ["桥接子方向A", "桥接子方向B"],
            "experience_mode": "hands_on",
            "entry_load": "heavy",
            "probe_mode": "bridge",
        },
        {
            "domain": "野卡挑战方向",
            "category": "人文",
            "reason": "这是一段足够长的野卡挑战理由，用来测试挑战池可以独立进入。",
            "confidence": 0.63,
            "specifics": ["野卡子方向A", "野卡子方向B"],
            "experience_mode": "people_story",
            "entry_load": "light",
            "probe_mode": "wildcard",
        },
    ]

    class _FakeResponse:
        def __init__(self, content: str) -> None:
            self.content = content

    class _FakeLLMService:
        async def complete_structured_task(self, **_kw):
            return _FakeResponse(json.dumps({"speculations": fake_speculations}))

    speculator = InterestSpeculator(
        llm_service=_FakeLLMService(),
        data_dir=data_dir.parent,
        max_active=5,
    )

    result = await speculator.force_tick(OnionProfile())

    assert {item.probe_mode for item in result.generated} == {
        "lateral",
        "bridge",
        "wildcard",
    }
    assert "近距额外方向" not in {item.domain for item in result.generated}

    persisted = speculator._load_state()
    active = [item for item in persisted.active if item.status == "active"]
    assert sum(1 for item in active if item.probe_mode == "near") == 5
    assert sum(1 for item in active if item.challenge) == 3


# ---------------------------------------------------------------------------
# Expiry and cooldown
# ---------------------------------------------------------------------------


def test_expire_stale_creates_cooldown():
    now = datetime.now()
    old = now - timedelta(days=15)
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="过期方向",
                status="active",
                created_at=old.isoformat(),
                ttl_days=14,
            ),
            SpeculativeInterest(
                domain="还没过期",
                status="active",
                created_at=now.isoformat(),
                ttl_days=14,
            ),
        ]
    )
    rejected, updated = expire_stale(state, now, cooldown_days=30)
    assert len(rejected) == 1
    assert rejected[0].domain == "过期方向"
    assert rejected[0].status == "rejected"
    assert len(updated.active) == 1
    assert updated.active[0].domain == "还没过期"
    assert len(updated.cooldown) == 1
    assert updated.cooldown[0].domain == "过期方向"
    assert updated.total_rejected == 1


def test_expire_cleans_old_cooldowns():
    now = datetime.now()
    state = SpeculativeState(
        active=[],
        cooldown=[
            CooldownEntry(
                domain="旧冷却",
                cooldown_until=(now - timedelta(days=1)).isoformat(),
            ),
            CooldownEntry(
                domain="新冷却",
                cooldown_until=(now + timedelta(days=10)).isoformat(),
            ),
        ],
    )
    _, updated = expire_stale(state, now)
    assert len(updated.cooldown) == 1
    assert updated.cooldown[0].domain == "新冷却"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_speculative_state_roundtrip():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="博弈论",
                category="知识",
                reason="test",
                confidence=0.5,
                weight=0.4,
                created_at="2026-01-01T00:00:00",
                confirmation_count=2,
                experience_mode="knowledge",
                entry_load="heavy",
            ),
        ],
        cooldown=[
            CooldownEntry(
                domain="过期",
                rejected_at="2026-01-01",
                cooldown_until="2026-02-01",
            ),
        ],
        last_generation_at="2026-01-01T00:00:00",
        total_promoted=3,
        total_rejected=5,
    )
    data = state.to_dict()
    restored = SpeculativeState.from_dict(data)
    assert len(restored.active) == 1
    assert restored.active[0].domain == "博弈论"
    assert restored.active[0].confirmation_count == 2
    assert restored.active[0].experience_mode == "knowledge"
    assert restored.active[0].entry_load == "heavy"
    assert len(restored.cooldown) == 1
    assert restored.total_promoted == 3
    assert restored.total_rejected == 5


def test_speculative_interest_round_trips_probe_mode_and_confirmation_fields():
    spec = SpeculativeInterest(
        domain="城市基础设施观察",
        category="知识观察",
        probe_mode="bridge",
        confirmation_source="probe_confirmed",
        confirmed_at="2026-05-24T12:00:00",
    )

    restored = SpeculativeInterest.from_dict(spec.to_dict())

    assert restored.probe_mode == "bridge"
    assert restored.challenge is True
    assert restored.confirmation_source == "probe_confirmed"
    assert restored.confirmed_at == "2026-05-24T12:00:00"


def test_normalize_probe_mode_defaults_missing_or_unknown_to_near():
    assert speculator_module._normalize_probe_mode("") == "near"
    assert speculator_module._normalize_probe_mode(None) == "near"
    assert speculator_module._normalize_probe_mode("surprise") == "near"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_and_load_speculative_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(
            active=[SpeculativeInterest(domain="test", status="active")],
            total_promoted=1,
        )
        save_speculative_state(data_dir, state)

        loaded = load_speculative_state(data_dir)
        assert len(loaded.active) == 1
        assert loaded.active[0].domain == "test"
        assert loaded.total_promoted == 1


def test_load_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = load_speculative_state(Path(tmpdir))
        assert state.active == []
        assert state.cooldown == []


# ---------------------------------------------------------------------------
# InterestSpeculator
# ---------------------------------------------------------------------------


def test_speculator_observe():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        # Pre-seed a speculation
        state = SpeculativeState(
            active=[
                SpeculativeInterest(
                    domain="博弈论",
                    category="知识",
                    status="active",
                    created_at=datetime.now().isoformat(),
                ),
            ]
        )
        save_speculative_state(data_dir, state)

        matches = speculator.observe(
            [
                {"title": "博弈论入门", "event_type": "view"},
            ]
        )
        assert matches == 1

        # Verify persisted
        reloaded = load_speculative_state(data_dir)
        assert reloaded.active[0].confirmation_count == 1


def test_speculator_ingest_seeds():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        added = speculator.ingest_seeds(
            [
                {"name": "博弈论", "category": "知识", "reason": "test", "weight": 0.4},
                {"name": "科技伦理", "category": "哲学", "reason": "test2", "weight": 0.5},
            ]
        )
        assert added == 2

        state = load_speculative_state(data_dir)
        assert len(state.active) == 2
        assert state.active[0].domain == "博弈论"


def test_speculator_ingest_seeds_dedup():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        speculator.ingest_seeds(
            [
                {"name": "博弈论", "category": "知识"},
            ]
        )
        added = speculator.ingest_seeds(
            [
                {"name": "博弈论", "category": "知识"},  # duplicate
                {"name": "新方向", "category": "其他"},
            ]
        )
        assert added == 1  # Only new one


def test_speculator_ingest_seeds_respects_cooldown():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(
            cooldown=[
                CooldownEntry(
                    domain="博弈论",
                    cooldown_until=(datetime.now() + timedelta(days=10)).isoformat(),
                ),
            ]
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
        added = speculator.ingest_seeds(
            [
                {"name": "博弈论", "category": "知识"},
            ]
        )
        assert added == 0


def test_speculator_get_active():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(
            active=[
                SpeculativeInterest(domain="A", status="active"),
                SpeculativeInterest(domain="B", status="promoted"),
                SpeculativeInterest(domain="C", status="active"),
            ]
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
        active = speculator.get_active_speculations()
        assert len(active) == 2
        assert {s.domain for s in active} == {"A", "C"}


def test_user_confirm_speculation_records_source_and_confirmed_at():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
        save_speculative_state(
            data_dir,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="建筑美学",
                        status="active",
                        created_at=datetime.now().isoformat(),
                    )
                ]
            ),
        )

        speculator.user_confirm_speculation(
            "建筑美学",
            confirmation_source="profile_confirmed",
        )
        state = speculator._load_state()
        spec = next(item for item in state.active if item.domain == "建筑美学")

        assert spec.status == "confirmed"
        assert spec.confirmation_source == "profile_confirmed"
        assert spec.confirmed_at


def test_user_confirm_speculation_returns_false_for_missing_domain(tmp_path: Path) -> None:
    speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)

    assert speculator.user_confirm_speculation("不存在") is False


def test_user_reject_speculation_returns_false_for_missing_domain(tmp_path: Path) -> None:
    speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)

    assert speculator.user_reject_speculation("不存在") is False


async def test_force_tick_does_not_restore_user_confirmed_interest(tmp_path: Path) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="active")]),
    )
    llm = _PausingSpeculationLLM()
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()

    assert speculator.user_confirm_speculation("建筑美学") is True
    llm.resume.set()
    await task

    state = load_speculative_state(tmp_path)
    assert all(not (item.domain == "建筑美学" and item.status == "active") for item in state.active)


async def test_force_tick_does_not_restore_user_rejected_interest(tmp_path: Path) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    save_speculative_state(
        tmp_path,
        SpeculativeState(active=[SpeculativeInterest(domain="建筑美学", status="active")]),
    )
    llm = _PausingSpeculationLLM()
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    task = asyncio.create_task(speculator.force_tick(OnionProfile()))
    await llm.started.wait()

    assert speculator.user_reject_speculation("建筑美学") is True
    llm.resume.set()
    await task

    state = load_speculative_state(tmp_path)
    assert all(item.domain != "建筑美学" for item in state.active)
    assert any(item.domain == "建筑美学" for item in state.cooldown)


async def test_force_tick_loader_blocks_duplicate_after_confirmed_item_was_promoted(
    tmp_path: Path,
) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    save_speculative_state(tmp_path, SpeculativeState(active=[]))
    llm = _PausingSpeculationLLM(domain="建筑美学")
    speculator = InterestSpeculator(llm_service=llm, data_dir=tmp_path, max_active=5)

    def _loader() -> list[dict[str, object]]:
        return [
            {
                "domain": "建筑美学",
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

    state = load_speculative_state(tmp_path)
    assert all(item.domain != "建筑美学" for item in state.active)


async def test_speculator_tick_promotes():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(
            active=[
                SpeculativeInterest(
                    domain="已确认",
                    status="active",
                    confirmation_count=3,
                    confirmation_threshold=3,
                    created_at=datetime.now().isoformat(),
                ),
            ]
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            generation_interval_minutes=999999,  # don't generate
        )

        from openbiliclaw.soul.profile import OnionProfile

        result = await speculator.tick(OnionProfile())
        assert len(result.promoted) == 1
        assert result.promoted[0].domain == "已确认"


async def test_speculator_tick_expires():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        old = datetime.now() - timedelta(days=20)
        state = SpeculativeState(
            active=[
                SpeculativeInterest(
                    domain="过期的",
                    status="active",
                    created_at=old.isoformat(),
                    ttl_days=14,
                ),
            ]
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            generation_interval_minutes=999999,
        )

        from openbiliclaw.soul.profile import OnionProfile

        result = await speculator.tick(OnionProfile())
        assert len(result.rejected) == 1
        assert result.rejected[0].domain == "过期的"

        # Verify cooldown was created
        reloaded = load_speculative_state(data_dir)
        assert len(reloaded.cooldown) == 1
        assert reloaded.cooldown[0].domain == "过期的"


def test_speculator_max_active_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            max_active=3,
        )
        added = speculator.ingest_seeds(
            [{"name": f"兴趣{i}", "category": "测试"} for i in range(10)]
        )
        assert added == 3


def test_should_generate_respects_primary_cap():
    """Skip generation when active speculations reach the primary cap."""
    from openbiliclaw.soul.profile import InterestDomain, InterestLayer, OnionProfile

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # Many confirmed domains should not deadlock the probe loop; only active
        # speculative fanout is capped.
        profile = OnionProfile(
            interest=InterestLayer(likes=[InterestDomain(domain=f"域{i}") for i in range(30)])
        )
        state = SpeculativeState(
            active=[
                SpeculativeInterest(domain="猜A", status="active"),
                SpeculativeInterest(domain="猜B", status="active"),
            ]
        )
        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            max_primary_interests=15,
        )
        assert speculator._should_generate(state, datetime.now(), profile) is True

        capped_state = SpeculativeState(
            active=[SpeculativeInterest(domain=f"猜{i}", status="active") for i in range(15)]
        )
        assert speculator._should_generate(capped_state, datetime.now(), profile) is False


def test_should_generate_respects_secondary_cap():
    """Skip generation when active speculations reach the secondary cap."""
    from openbiliclaw.soul.profile import (
        InterestDomain,
        InterestLayer,
        InterestSpecific,
        OnionProfile,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # Rich confirmed specifics should not deadlock the probe loop; only
        # active speculative fanout is capped.
        profile = OnionProfile(
            interest=InterestLayer(
                likes=[
                    InterestDomain(
                        domain=f"域{i}",
                        specifics=[InterestSpecific(name=f"项{j}") for j in range(20)],
                    )
                    for i in range(3)
                ]
            )
        )
        state = SpeculativeState(
            active=[
                SpeculativeInterest(domain="猜A", status="active"),
            ]
        )
        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            max_secondary_interests=60,
        )
        assert speculator._should_generate(state, datetime.now(), profile) is True

        capped_state = SpeculativeState(
            active=[SpeculativeInterest(domain=f"猜{i}", status="active") for i in range(60)]
        )
        assert speculator._should_generate(capped_state, datetime.now(), profile) is False


async def test_force_tick_ignores_interval():
    """force_tick generates even if interval hasn't elapsed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # Set last_generation_at to just now — tick would skip, force_tick should not
        state = SpeculativeState(
            last_generation_at=datetime.now().isoformat(),
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            generation_interval_minutes=9999,
        )

        from openbiliclaw.soul.profile import OnionProfile

        # force_tick with no LLM service won't generate, but it should run
        result = await speculator.force_tick(OnionProfile())
        # No error, returns result (empty since no LLM)
        assert result.generated == []
        assert result.promoted == []


async def test_speculator_generate_keeps_visible_experience_mix():
    class _FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "speculations": [
                            {
                                "domain": "博弈论科普",
                                "category": "知识解释",
                                "reason": (
                                    "你一直在看结构化推演内容，这个方向能继续提供可验证的思考乐趣。"
                                ),
                                "bridge_type": "near",
                                "confidence": 0.59,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["纳什均衡", "机制设计入门"],
                            },
                            {
                                "domain": "AI治理",
                                "category": "社会文化",
                                "reason": (
                                    "你对技术影响现实社会的链条敏感，这个方向能接住这种关注。"
                                ),
                                "bridge_type": "far",
                                "confidence": 0.57,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["监管辩论", "模型风险案例"],
                            },
                            {
                                "domain": "建筑叙事",
                                "category": "审美体验",
                                "reason": (
                                    "你会被空间里的结构和叙事吸引，"
                                    "这个方向能把抽象秩序落到具体场景。"
                                ),
                                "bridge_type": "novel",
                                "confidence": 0.55,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["城市更新", "公共空间改造"],
                            },
                            {
                                "domain": "城市漫游",
                                "category": "现实观察",
                                "reason": (
                                    "你有从具体场景观察系统的习惯，这个方向入口轻但仍有结构感。"
                                ),
                                "bridge_type": "near",
                                "confidence": 0.49,
                                "experience_mode": "wander_observe",
                                "entry_load": "light",
                                "specifics": ["街区vlog", "城市步行路线"],
                            },
                            {
                                "domain": "器物修复",
                                "category": "实操动手",
                                "reason": (
                                    "你喜欢看结构怎么被拆开再复原，这个方向能给到更直接的动手反馈。"
                                ),
                                "bridge_type": "near",
                                "confidence": 0.48,
                                "experience_mode": "hands_on",
                                "entry_load": "light",
                                "specifics": ["旧物翻新", "工具修复过程"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        from openbiliclaw.soul.profile import OnionProfile

        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=_FakeLLMService(),
            data_dir=data_dir,
            max_active=3,
        )

        result = await speculator.force_tick(OnionProfile())

        assert len(result.generated) == 3
        assert any(item.entry_load == "light" for item in result.generated)
        assert any(item.experience_mode != "knowledge" for item in result.generated)


async def test_speculator_generate_prefers_axis_missing_from_active_pool():
    class _FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "speculations": [
                            {
                                "domain": "城市夜游",
                                "category": "现实观察",
                                "reason": (
                                    "你已经会从街区场景里找结构，"
                                    "这个方向继续沿着同一种轻入口观察走。"
                                ),
                                "confidence": 0.59,
                                "experience_mode": "wander_observe",
                                "entry_load": "light",
                                "specifics": ["夜间街区vlog", "城市灯光观察"],
                            },
                            {
                                "domain": "旧物修复",
                                "category": "实操动手",
                                "reason": (
                                    "你喜欢看结构怎么被拆开再复原，这个方向能补上更直接的动手反馈。"
                                ),
                                "confidence": 0.48,
                                "experience_mode": "hands_on",
                                "entry_load": "heavy",
                                "specifics": ["工具修复过程", "旧物翻新记录"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        from openbiliclaw.soul.profile import OnionProfile

        data_dir = Path(tmpdir)
        save_speculative_state(
            data_dir,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="城市漫游",
                        status="active",
                        experience_mode="wander_observe",
                        entry_load="light",
                    ),
                    SpeculativeInterest(
                        domain="街区观察",
                        status="active",
                        experience_mode="wander_observe",
                        entry_load="light",
                    ),
                ]
            ),
        )
        speculator = InterestSpeculator(
            llm_service=_FakeLLMService(),
            data_dir=data_dir,
            max_active=3,
        )

        result = await speculator.force_tick(OnionProfile())

        assert [item.domain for item in result.generated] == ["旧物修复"]


def test_interval_uses_minutes():
    """Verify _should_generate uses minutes, not hours."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=None,
            data_dir=data_dir,
            generation_interval_minutes=10,
        )
        now = datetime.now()
        # 5 minutes ago → should NOT generate
        state5 = SpeculativeState(last_generation_at=(now - timedelta(minutes=5)).isoformat())
        assert speculator._should_generate(state5, now) is False

        # 15 minutes ago → should generate
        state15 = SpeculativeState(last_generation_at=(now - timedelta(minutes=15)).isoformat())
        assert speculator._should_generate(state15, now) is True
