"""Free-model initialization must persist useful, explicitly tentative profile layers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulEngine
from openbiliclaw.soul.profile import OnionProfile
from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

if TYPE_CHECKING:
    from pathlib import Path


class GroundedFreeService:
    def __init__(self, model: str, kinds: list[str] | None = None) -> None:
        self.model = model
        self.kinds = kinds if kinds is not None else ["principles", "steps"]
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(
            provider="siliconflow_free",
            model=self.model,
            content=json.dumps(
                {
                    "personality_portrait": (
                        "从这些内容选择看，你似乎偏爱讲清原理、带有具体例子的解释。"
                        "这是一份可以继续纠正的初步印象。"
                    ),
                    "information_preferences": [
                        {"kind": kind, "source_index": 0} for kind in self.kinds
                    ],
                },
                ensure_ascii=False,
            ),
        )


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
async def test_initial_free_profile_persists_all_supported_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").data.update(
        {"interests": [{"name": "园艺", "category": "生活", "weight": 0.8}]}
    )
    service = GroundedFreeService(model)
    engine = SoulEngine(llm=service, memory=memory)
    monkeypatch.setattr(engine._profile_builder, "registry", service)
    profile = await engine.build_initial_profile(
        [{"event_type": "favorite", "title": "光合作用原理与分步实验教程"}]
    )
    saved = OnionProfile.from_dict(json.loads((tmp_path / "memory/soul.json").read_text()))
    for result in (profile, saved):
        for field in ("core_traits", "deep_needs", "values", "motivational_drivers"):
            assert getattr(result, field), field
            assert all("待确认" in item for item in getattr(result, field)), field
        assert result.cognitive_style
        assert result.current_phase != "暂无明确自述"
        assert result.preferences.interests
        assert result.life_stage == "暂无明确自述"
        assert not result.core.mbti.type
    markdown = (tmp_path / "memory/soul_profile.md").read_text()
    assert "待确认" in markdown


@pytest.mark.parametrize(
    "kind",
    ["principles", "examples", "steps", "comparison", "visual", "story", "concise", "exploration"],
)
async def test_each_grounded_information_kind_can_seed_the_profile(kind: str) -> None:
    profile = await ProfileBuilder(GroundedFreeService("Qwen/Qwen3-8B", [kind])).build(
        history=[{"title": "参考内容"}], preference={}, awareness_notes=[], active_insights=[]
    )
    assert all(
        getattr(profile, f)
        for f in ("core_traits", "deep_needs", "values", "motivational_drivers", "cognitive_style")
    )


async def test_invalid_free_grounding_cannot_finish_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)
    service = GroundedFreeService("Qwen/Qwen3-8B", ["invented"])
    with pytest.raises(SoulProfileBuildError, match="information preferences"):
        await ProfileBuilder(service).build(
            history=[{"title": "教程"}], preference={}, awareness_notes=[], active_insights=[]
        )
    assert len(service.calls) == 2
    assert "教程" in service.calls[-1]["user_input"]


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
async def test_normal_free_instance_adapter_keeps_all_profile_layers(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    from openbiliclaw.llm.base import LLMRegistry
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.llm.sponsored_adapter import SponsoredChatAdapter

    provider = GroundedFreeService(model)

    async def completion(*args: Any, **kwargs: Any) -> LLMResponse:
        result = await provider.complete_structured_task(**kwargs)
        result.provider = "sponsored"
        return result

    from types import SimpleNamespace

    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider",
        lambda **kwargs: SimpleNamespace(complete=completion),
    )
    registry = LLMRegistry()
    registry.register(SponsoredChatAdapter(model=model), name="free", default=True)
    profile = await ProfileBuilder(LLMService(registry=registry, memory=None)).build(
        history=[{"event_type": "favorite", "title": "光合作用原理"}],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )
    assert profile.core_traits and profile.deep_needs and profile.values
    assert profile.motivational_drivers and profile.cognitive_style
    assert provider.calls


async def test_self_description_and_content_preferences_survive_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)

    class Service(GroundedFreeService):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            data = json.loads(kwargs["user_input"])
            assert data["sources"] == [{"index": 0, "title": "光合作用原理"}]
            assert data["user_statements"] == ["我是一名教师"]
            result = await super().complete_structured_task(**kwargs)
            payload = json.loads(result.content)
            payload.update(life_stage="教师", core_traits=["我自述做事耐心"])
            result.content = json.dumps(payload, ensure_ascii=False)
            return result

    profile = await ProfileBuilder(Service("Qwen/Qwen3-8B")).build(
        history=[
            {
                "event_type": "dialogue",
                "title": "对话",
                "metadata": {"user_message": "我是一名教师"},
            },
            {"event_type": "favorite", "title": "光合作用原理"},
        ],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )
    assert profile.life_stage == "教师"
    assert profile.core_traits == ["我自述做事耐心"]
    assert profile.deep_needs and profile.values and profile.motivational_drivers


@pytest.mark.parametrize("mode", ["passive_grounding", "authored_grounding"])
def test_grounded_profile_system_prompts_are_static(mode: str) -> None:
    from openbiliclaw.llm.prompts import build_soul_profile_prompt

    first = build_soul_profile_prompt(
        history_summary={"titles": ["原理"], "authored_expressions": ["我是教师"]},
        preference_summary={},
        tone_profile=None,
        **{mode: True},
    )
    second = build_soul_profile_prompt(
        history_summary={"titles": ["故事"], "authored_expressions": ["我是护士"]},
        preference_summary={},
        tone_profile=None,
        **{mode: True},
    )
    assert first[0] == second[0]
    assert first[1] != second[1]


async def test_failed_completeness_check_preserves_existing_soul(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)
    memory = MemoryManager(tmp_path)
    memory.initialize()
    layer = memory.get_layer("soul")
    layer.data.update(OnionProfile(personality_portrait="原来的画像").to_dict())
    layer.save()
    before = (tmp_path / "memory/soul.json").read_bytes()
    service = GroundedFreeService("Qwen/Qwen3-8B", ["invented"])
    engine = SoulEngine(llm=service, memory=memory)
    monkeypatch.setattr(engine._profile_builder, "registry", service)
    with pytest.raises(SoulProfileBuildError, match="information preferences"):
        await engine.build_initial_profile([{"title": "原理教程"}])
    assert (tmp_path / "memory/soul.json").read_bytes() == before


async def test_missing_information_preferences_retry_can_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openbiliclaw.llm.sponsored_router.sponsored_enabled", lambda: True)

    class RecoveringService(GroundedFreeService):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            self.kinds = ["invented"] if not self.calls else ["steps"]
            return await super().complete_structured_task(**kwargs)

    service = RecoveringService("Qwen/Qwen3.5-4B")
    profile = await ProfileBuilder(service).build(
        history=[{"title": f"步骤教程{i}"} for i in range(30)],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )
    assert profile.core_traits and profile.deep_needs
    assert len(service.calls) == 2
    assert len(json.loads(service.calls[-1]["user_input"])["sources"]) == 12


async def test_initial_profile_keeps_existing_awareness_and_insight_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("awareness").data["notes"] = [
        {
            "observation": "多次收藏分步教程",
            "note_id": "init-note",
            "source_event_ids": [1, 2],
            "source_event_ids_approximate": True,
        }
    ]
    memory.get_layer("insight").data["hypotheses"] = [
        {"hypothesis": "可能偏好具体方法", "confidence": 0.6, "evidence": ["多次收藏教程"]},
        {"hypothesis": "已否定的旧推断", "user_verdict": "rejected"},
    ]
    service = GroundedFreeService("Qwen/Qwen3-8B")
    engine = SoulEngine(llm=service, memory=memory)
    monkeypatch.setattr(engine._profile_builder, "registry", service)
    profile = await engine.build_initial_profile([{"title": "分步教程"}])
    for result in (profile, await engine.get_profile()):
        assert [n.observation for n in result.recent_awareness] == ["多次收藏分步教程"]
        assert result.recent_awareness[0].source_event_ids == [1, 2]
        assert result.recent_awareness[0].source_event_ids_approximate
        assert [i.hypothesis for i in result.active_insights] == ["可能偏好具体方法"]
        assert not result.active_insights[0].validated


def test_general_profile_retry_still_omits_raw_titles() -> None:
    compact = ProfileBuilder._compact_history_summary(
        {"titles": ["原始标题"], "count": 1},
        original_count=1,
    )
    assert "titles" not in compact
