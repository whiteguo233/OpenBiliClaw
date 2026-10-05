"""Tests for the M3 recommendation / discovery-pool / feedback tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
from openbiliclaw.agent.tools.feedback_tools import build_feedback_tools
from openbiliclaw.agent.tools.recommendation_tools import build_recommendation_tools


def _registry(*builders: Any, ctx: AgentToolContext) -> ToolRegistry:
    registry = ToolRegistry()
    for builder in builders:
        for tool in builder(ctx):
            registry.register(tool)
    return registry


_POOL_ROWS = [
    {
        "bvid": "BV1",
        "title": "机械键盘入门指南",
        "up_name": "外设小站",
        "source_platform": "bilibili",
        "pool_topic_label": "数码外设",
        "relevance_score": 0.87,
    },
    {
        "bvid": "xhs-1",
        "title": "露营装备清单",
        "author_name": "户外玩家",
        "source_platform": "xiaohongshu",
        "pool_topic_label": "露营",
        "relevance_score": 0.66,
    },
]


class _FakePoolDatabase:
    def __init__(self) -> None:
        self.platform_calls: list[str] = []

    def get_pool_candidates(self, limit: int = 20, **_: Any) -> list[dict[str, Any]]:
        return _POOL_ROWS[:limit]

    def get_pool_candidates_for_platform(
        self, platform: str, limit: int = 5, **_: Any
    ) -> list[dict[str, Any]]:
        self.platform_calls.append(platform)
        return [row for row in _POOL_ROWS if row["source_platform"] == platform][:limit]

    def count_pool_readiness(self, **_: Any) -> dict[str, int]:
        return {"raw": 100, "pending": 12, "available": 8, "copy_ready": 6}

    def count_pool_candidates(self, **_: Any) -> int:
        return 8

    def list_servable_pool_platforms(self, **_: Any) -> list[str]:
        return ["bilibili", "xiaohongshu"]


class TestGetRecommendations:
    async def test_happy_path_is_read_only_preview(self) -> None:
        ctx = AgentToolContext(database=_FakePoolDatabase())
        result = await _registry(build_recommendation_tools, ctx=ctx).dispatch(
            "get_recommendations", {"limit": 2}
        )
        assert result.ok
        assert "机械键盘入门指南" in result.content
        assert "未消耗推荐池" in result.content

    async def test_platform_scope(self) -> None:
        db = _FakePoolDatabase()
        ctx = AgentToolContext(database=db)
        result = await _registry(build_recommendation_tools, ctx=ctx).dispatch(
            "get_recommendations", {"source_platform": "xiaohongshu"}
        )
        assert result.ok
        assert "露营装备清单" in result.content
        assert "机械键盘" not in result.content
        assert db.platform_calls == ["xiaohongshu"]

    async def test_invalid_arguments(self) -> None:
        ctx = AgentToolContext(database=_FakePoolDatabase())
        result = await _registry(build_recommendation_tools, ctx=ctx).dispatch(
            "get_recommendations", {"limit": "五条"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        result = await _registry(build_recommendation_tools, ctx=AgentToolContext()).dispatch(
            "get_recommendations", {}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert "组件不可用" in result.content


class TestQueryDiscoveryPool:
    async def test_happy_path(self) -> None:
        ctx = AgentToolContext(database=_FakePoolDatabase())
        result = await _registry(build_recommendation_tools, ctx=ctx).dispatch(
            "query_discovery_pool", {"sample_size": 1}
        )
        assert result.ok
        assert "立即可服务候选: 8" in result.content
        assert "bilibili" in result.content
        assert "候选抽样" in result.content

    async def test_no_sampling(self) -> None:
        ctx = AgentToolContext(database=_FakePoolDatabase())
        result = await _registry(build_recommendation_tools, ctx=ctx).dispatch(
            "query_discovery_pool", {"sample_size": 0}
        )
        assert result.ok
        assert "候选抽样" not in result.content

    async def test_missing_component(self) -> None:
        result = await _registry(build_recommendation_tools, ctx=AgentToolContext()).dispatch(
            "query_discovery_pool", {}
        )
        assert not result.ok
        assert result.error == "handler_error"


class _FakeFeedbackDatabase:
    def __init__(self, recommendation: dict[str, Any] | None = None) -> None:
        self._recommendation = recommendation
        self.projections: list[dict[str, Any]] = []

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, Any] | None:
        return self._recommendation

    def update_recommendation_feedback(
        self, recommendation_id: int, *, feedback_type: str, feedback_note: str = ""
    ) -> None:
        self.projections.append(
            {
                "recommendation_id": recommendation_id,
                "feedback_type": feedback_type,
                "feedback_note": feedback_note,
            }
        )


def _make_ingress(*, accepted: int = 1, duplicate: bool = False) -> Any:
    item = SimpleNamespace(
        index=0,
        event_type="feedback",
        event_id=42,
        inserted=not duplicate,
        duplicate=duplicate,
        error="" if accepted else "bad event",
    )
    return SimpleNamespace(
        accept=lambda event, producer="": SimpleNamespace(
            items=(item,),
            accepted=accepted,
            inserted=1 if not duplicate else 0,
            duplicates=1 if duplicate else 0,
            rejected=0 if accepted else 1,
        )
    )


_RECOMMENDATION = {
    "id": 7,
    "bvid": "BV1",
    "title": "机械键盘入门指南",
    "source_platform": "bilibili",
}


class TestSubmitFeedback:
    async def test_happy_path(self) -> None:
        db = _FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION))
        ctx = AgentToolContext(database=db, event_ingress=_make_ingress())
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback", {"recommendation_id": 7, "feedback_type": "like"}
        )
        assert result.ok
        assert "点赞" in result.content
        assert "#42" in result.content
        assert db.projections == [
            {"recommendation_id": 7, "feedback_type": "like", "feedback_note": ""}
        ]

    async def test_cognition_hook_fired(self) -> None:
        calls: list[dict[str, Any]] = []
        engine = SimpleNamespace(
            record_immediate_feedback_cognition=lambda **kwargs: calls.append(kwargs)
        )
        ctx = AgentToolContext(
            database=_FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION)),
            event_ingress=_make_ingress(),
            soul_engine=engine,
        )
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback",
            {"recommendation_id": 7, "feedback_type": "dislike", "note": "太水了"},
        )
        assert result.ok
        assert calls == [
            {"feedback_type": "dislike", "title": "机械键盘入门指南", "note": "太水了"}
        ]

    async def test_duplicate_is_not_reprojected(self) -> None:
        db = _FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION))
        ctx = AgentToolContext(database=db, event_ingress=_make_ingress(duplicate=True))
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback",
            {"recommendation_id": 7, "feedback_type": "like", "request_id": "fixed-key"},
        )
        assert result.ok
        assert "幂等去重" in result.content
        assert db.projections == []

    async def test_comment_requires_note(self) -> None:
        ctx = AgentToolContext(
            database=_FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION)),
            event_ingress=_make_ingress(),
        )
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback", {"recommendation_id": 7, "feedback_type": "comment"}
        )
        assert result.ok
        assert "必须带 note" in result.content

    async def test_unknown_recommendation(self) -> None:
        ctx = AgentToolContext(
            database=_FakeFeedbackDatabase(recommendation=None),
            event_ingress=_make_ingress(),
        )
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback", {"recommendation_id": 999, "feedback_type": "like"}
        )
        assert result.ok
        assert "未找到" in result.content

    async def test_invalid_feedback_type(self) -> None:
        ctx = AgentToolContext(
            database=_FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION)),
            event_ingress=_make_ingress(),
        )
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "submit_feedback", {"recommendation_id": 7, "feedback_type": "love"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        registry = _registry(
            build_feedback_tools,
            ctx=AgentToolContext(
                database=_FakeFeedbackDatabase(recommendation=dict(_RECOMMENDATION))
            ),
        )
        result = await registry.dispatch(
            "submit_feedback", {"recommendation_id": 7, "feedback_type": "like"}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert "event_ingress" in result.content


class _FakeSavedService:
    def __init__(self, saved: bool = True) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self._saved = saved

    def save_local(self, list_kind: str, item: Any, note: str = "", auto_sync: bool = False) -> Any:
        self.calls.append((list_kind, item, note, auto_sync))
        return SimpleNamespace(saved=self._saved, item_key=item.item_key, sync_status="local")


class TestSaveItem:
    async def test_happy_path(self) -> None:
        service = _FakeSavedService()
        ctx = AgentToolContext(saved_sync_service=service)
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "save_item",
            {"content_id": "BV1xx", "title": "键盘评测", "list_kind": "watch_later"},
        )
        assert result.ok
        assert "稍后再看" in result.content
        assert "键盘评测" in result.content
        assert "未同步平台" in result.content
        list_kind, item, _note, auto_sync = service.calls[0]
        assert list_kind == "watch_later"
        assert item.content_id == "BV1xx"
        assert auto_sync is False

    async def test_missing_content_id_rejected_by_schema(self) -> None:
        ctx = AgentToolContext(saved_sync_service=_FakeSavedService())
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch("save_item", {})
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_invalid_list_kind_rejected_by_schema(self) -> None:
        ctx = AgentToolContext(saved_sync_service=_FakeSavedService())
        result = await _registry(build_feedback_tools, ctx=ctx).dispatch(
            "save_item", {"content_id": "BV1", "list_kind": "trash"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        result = await _registry(build_feedback_tools, ctx=AgentToolContext()).dispatch(
            "save_item", {"content_id": "BV1"}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert "saved_sync_service" in result.content
