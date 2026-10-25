"""Only the user's empty-value link hints may reveal query-key existence."""

from openbiliclaw.sources.workflow_service import _requested_link_patterns


def test_explicit_empty_value_link_hints_remain_independent_of_private_values():
    assert _requested_link_patterns(
        "https://forum.example/board/list",
        "只收 read.php?item= 帖子，也允许 /archive/view?entry_id=；不收导航。",
    ) == [
        {"path": "/board/read.php", "query_key": "item"},
        {"path": "/archive/view", "query_key": "entry_id"},
    ]
    assert (
        _requested_link_patterns(
            "https://forum.example/",
            "入口 /list?section=private-value，链接 /read?id=123 和 /read?token=secret",
        )
        == []
    )


def test_link_hint_budget_and_deduplication():
    hints = " ".join(f"/entry{i}?item= " for i in range(15))
    result = _requested_link_patterns("https://forum.example/", "/entry0?item= " + hints)
    assert len(result) == 8
    assert result[0] == {"path": "/entry0", "query_key": "item"}


async def test_ego_observation_receives_only_user_hints_without_expanding_plan(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openbiliclaw.sources.workflow_service import WorkflowService
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "patterns.db")
    db.initialize()
    service = WorkflowService(db)
    session = SimpleNamespace(snapshot=AsyncMock(return_value={"elements": []}))
    executor = SimpleNamespace(open_session=AsyncMock(return_value=session))
    service._executor_factory = lambda name: executor
    draft = service.store.save_draft(
        {
            "entry_url": "https://forum.example/list",
            "origin": "https://forum.example",
            "intent": "只收 /read.php?item= 帖子，不收导航。",
            "executor": "ego",
            "session_id": "isolated-session",
            "name": "Forum",
        }
    )
    try:
        observed, _ = await service.observe_session(draft)
        assert observed is session
        session.snapshot.assert_awaited_once_with(
            link_patterns=[{"path": "/read.php", "query_key": "item"}]
        )
        plan = executor.open_session.call_args.args[0]
        assert plan.allowed_origins == ("https://forum.example",)
        assert service.sources.list_sources() == []
    finally:
        db.close()
