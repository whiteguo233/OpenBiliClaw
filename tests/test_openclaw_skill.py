"""Tests for OpenClaw skill descriptors."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from openbiliclaw.integrations.openclaw.errors import (
    AdapterOperationError,
    AdapterValidationError,
)
from openbiliclaw.integrations.openclaw.schemas import (
    AvoidanceProbeFeedbackResponse,
    AvoidanceProbeItem,
    AvoidanceProbeResponse,
    ChatResponse,
    DelightItem,
    DelightResponse,
    FeedbackResponse,
    InterestProbeItem,
    InterestProbeResponse,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SyncAccountResponse,
)
from openbiliclaw.integrations.openclaw.skill import build_openclaw_skills


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def sync_account(self) -> SyncAccountResponse:
        self.calls.append(("sync_account",))
        return SyncAccountResponse(synced=True, new_event_count=5, errors=[])

    async def get_profile(self) -> ProfileResponse:
        self.calls.append(("get_profile",))
        return ProfileResponse(
            initialized=True,
            personality_portrait="你会自己往深处追问题。",
            core_traits=["深究"],
            deep_needs=["把问题想透"],
            top_interests=["国际时事"],
        )

    async def recommend(
        self,
        *,
        limit: int = 5,
        refresh_if_needed: bool = True,
    ) -> RecommendationResponse:
        self.calls.append(("recommend", limit, refresh_if_needed))
        return RecommendationResponse(
            items=[
                RecommendationItem(
                    recommendation_id=12,
                    bvid="BV1SKILL",
                    title="把问题讲到结构层",
                    up_name="结构控",
                    cover_url="https://example.com/cover.jpg",
                    reason="这条会接住你最近那股往深处看的劲头。",
                    topic_label="你最近那股往深处看的劲头",
                    confidence=0.88,
                )
            ]
        )

    async def submit_feedback(
        self,
        request,  # noqa: ANN001
    ) -> FeedbackResponse:
        self.calls.append(
            ("submit_feedback", request.recommendation_id, request.feedback_type, request.note)
        )
        return FeedbackResponse(
            ok=True,
            recommendation_id=request.recommendation_id,
            feedback_type=request.feedback_type,
        )

    async def get_delight(self) -> DelightResponse:
        self.calls.append(("get_delight",))
        return DelightResponse(
            item=DelightItem(
                bvid="BV1DELIGHT",
                title="跨域探索的意外发现",
                delight_reason="你之前聊到过想搞明白复杂系统，这条从完全不同的角度切入了。",
                delight_score=0.92,
                delight_hook="深层共鸣",
                cover_url="https://example.com/delight-cover.jpg",
            ),
        )

    async def get_runtime_status(self) -> RuntimeStatusResponse:
        self.calls.append(("get_runtime_status",))
        return RuntimeStatusResponse(
            initialized=True,
            recommendation_count=7,
            pending_signal_events=2,
            unread_count=1,
            pool_available_count=14,
            pool_target_count=30,
            last_refresh_at="2026-03-15T12:00:00+08:00",
            last_account_sync_at="2026-03-15T12:05:00+08:00",
            last_account_sync_error="",
        )

    async def chat(self, request) -> ChatResponse:  # noqa: ANN001
        self.calls.append(("chat", request.message, request.session))
        return ChatResponse(
            reply="你说的这个方向我有个猜测——你是不是其实更在意底层结构而不只是结论？",
            session=request.session,
        )

    async def get_next_probe(self) -> InterestProbeResponse:
        self.calls.append(("get_next_probe",))
        return InterestProbeResponse(
            probe=InterestProbeItem(
                domain="建筑美学",
                category="人文",
                reason="你最近看了很多关于结构和空间的内容。",
                confidence=0.45,
                weight=0.4,
                specifics=["参数化设计", "混凝土美学"],
                question=(
                    "我从你最近的轨迹里嗅到你可能对【建筑美学】"
                    "（比如：参数化设计、混凝土美学）感兴趣"
                    "——你最近看了很多关于结构和空间的内容。 这个方向你自己认不认？"
                ),
            )
        )

    async def get_next_avoidance_probe(self) -> AvoidanceProbeResponse:
        self.calls.append(("get_next_avoidance_probe",))
        return AvoidanceProbeResponse(
            probe=AvoidanceProbeItem(
                domain="浅层热点复读",
                reason="用户可能想避开无信息增量的热点复读内容。",
                source_mode="negative_signal",
                source_signal="thumbs_down",
                confidence=0.55,
                weight=0.5,
                specifics=["标题党热点解读", "无信息增量复读"],
                question=(
                    "我猜【浅层热点复读】（比如：标题党热点解读、无信息增量复读）"
                    "可能是你想避开的方向。"
                ),
            )
        )

    async def respond_avoidance_probe(self, request) -> AvoidanceProbeFeedbackResponse:  # noqa: ANN001
        self.calls.append(
            ("respond_avoidance_probe", request.domain, request.response, request.message)
        )
        return AvoidanceProbeFeedbackResponse(
            ok=True,
            action="confirmed",
            domain=request.domain,
        )


def test_build_openclaw_skills_returns_expected_names() -> None:
    adapter = _FakeAdapter()

    skills = build_openclaw_skills(adapter)

    assert [skill.name for skill in skills] == [
        "openbiliclaw_sync_account",
        "openbiliclaw_get_profile",
        "openbiliclaw_recommend",
        "openbiliclaw_submit_feedback",
        "openbiliclaw_get_delight",
        "openbiliclaw_get_runtime_status",
        "openbiliclaw_chat",
        "openbiliclaw_next_probe",
        "openbiliclaw_next_avoidance_probe",
        "openbiliclaw_respond_avoidance_probe",
    ]


@pytest.mark.asyncio
async def test_recommend_skill_delegates_to_adapter() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_recommend")

    payload = await skill.handler({"limit": 3, "refresh_if_needed": True})

    assert payload == {
        "ok": True,
        "data": {
            "items": [
                {
                    "recommendation_id": 12,
                    "bvid": "BV1SKILL",
                    "title": "把问题讲到结构层",
                    "up_name": "结构控",
                    "cover_url": "https://example.com/cover.jpg",
                    "reason": "这条会接住你最近那股往深处看的劲头。",
                    "topic_label": "你最近那股往深处看的劲头",
                    "confidence": 0.88,
                }
            ]
        },
    }
    assert adapter.calls == [("recommend", 3, True)]


@pytest.mark.asyncio
async def test_recommend_skill_defaults_to_fast_path() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_recommend")

    payload = await skill.handler({})

    assert payload["ok"] is True
    assert adapter.calls == [("recommend", 5, False)]


@pytest.mark.asyncio
async def test_submit_feedback_skill_builds_request_and_delegates() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_submit_feedback")

    payload = await skill.handler(
        {
            "recommendation_id": 9,
            "feedback_type": "comment",
            "note": "方向对，但想更深一点。",
        }
    )

    assert payload == {
        "ok": True,
        "data": asdict(
            FeedbackResponse(
                ok=True,
                recommendation_id=9,
                feedback_type="comment",
            )
        ),
    }
    assert adapter.calls == [("submit_feedback", 9, "comment", "方向对，但想更深一点。")]


@pytest.mark.asyncio
async def test_skill_returns_validation_error_payload() -> None:
    class ValidationAdapter(_FakeAdapter):
        async def submit_feedback(  # type: ignore[override]
            self,
            request,  # noqa: ANN001
        ) -> FeedbackResponse:
            raise AdapterValidationError(f"bad input: {request.feedback_type}")

    skill = next(
        item
        for item in build_openclaw_skills(ValidationAdapter())
        if item.name == "openbiliclaw_submit_feedback"
    )

    payload = await skill.handler(
        {
            "recommendation_id": 9,
            "feedback_type": "like",
        }
    )

    assert payload == {
        "ok": False,
        "error": "bad input: like",
        "error_type": "validation_error",
    }


@pytest.mark.asyncio
async def test_skill_returns_operation_error_payload() -> None:
    class OperationAdapter(_FakeAdapter):
        async def get_profile(self) -> ProfileResponse:  # type: ignore[override]
            raise AdapterOperationError("profile unavailable")

    skill = next(
        item
        for item in build_openclaw_skills(OperationAdapter())
        if item.name == "openbiliclaw_get_profile"
    )

    payload = await skill.handler({})

    assert payload == {
        "ok": False,
        "error": "profile unavailable",
        "error_type": "operation_error",
    }


@pytest.mark.asyncio
async def test_get_delight_skill_delegates_to_adapter() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_get_delight")

    payload = await skill.handler({})

    assert payload["ok"] is True
    assert payload["data"]["item"]["bvid"] == "BV1DELIGHT"
    assert payload["data"]["item"]["delight_hook"] == "深层共鸣"


@pytest.mark.asyncio
async def test_chat_skill_delegates_to_adapter() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_chat")

    payload = await skill.handler({"message": "我最近对建筑很感兴趣", "session": "test"})

    assert payload["ok"] is True
    assert "底层结构" in payload["data"]["reply"]
    assert adapter.calls == [("chat", "我最近对建筑很感兴趣", "test")]


@pytest.mark.asyncio
async def test_next_probe_skill_delegates_to_adapter() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_next_probe")

    payload = await skill.handler({})

    assert payload["ok"] is True
    assert payload["data"]["probe"]["domain"] == "建筑美学"
    assert "参数化设计" in payload["data"]["probe"]["specifics"]
    assert adapter.calls == [("get_next_probe",)]


@pytest.mark.asyncio
async def test_next_avoidance_probe_skill_delegates_to_adapter() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_next_avoidance_probe")

    payload = await skill.handler({})

    assert payload["ok"] is True
    assert payload["data"]["probe"]["domain"] == "浅层热点复读"
    assert "标题党热点解读" in payload["data"]["probe"]["specifics"]
    assert adapter.calls == [("get_next_avoidance_probe",)]


@pytest.mark.asyncio
async def test_respond_avoidance_probe_skill_builds_request_and_delegates() -> None:
    adapter = _FakeAdapter()
    skills = build_openclaw_skills(adapter)
    skill = next(item for item in skills if item.name == "openbiliclaw_respond_avoidance_probe")

    payload = await skill.handler(
        {
            "domain": "浅层热点复读",
            "response": "confirm",
            "message": "对，就是不想看",
        }
    )

    assert payload["ok"] is True
    assert payload["data"]["action"] == "confirmed"
    assert adapter.calls == [
        ("respond_avoidance_probe", "浅层热点复读", "confirm", "对，就是不想看")
    ]
