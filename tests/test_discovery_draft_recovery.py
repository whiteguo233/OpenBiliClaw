"""A later chat can recover a real draft without asking the user for its ID."""

import json

from openbiliclaw.agent.tools.source_tools import build_source_tool_registry
from openbiliclaw.sources.workflow_store import WorkflowStore
from openbiliclaw.storage.database import Database


async def test_list_sources_recovers_unpublished_draft_after_database_reopen(tmp_path):
    path = tmp_path / "recovery.db"
    database = Database(path)
    database.initialize()
    store = WorkflowStore(database)
    draft = store.save_draft(
        {
            "name": "Books",
            "entry_url": "https://books.example/",
            "intent": "Latest books",
            "status": "ready",
            "workflow": {"selector": "private-plan"},
            "session_id": "private-session",
            "observation": {"cookie": "never-return"},
        }
    )
    store.save_draft({"name": "Already applied", "published_workflow_id": "published"})
    database.close()
    database = Database(path)
    database.initialize()
    try:
        registry = build_source_tool_registry(database, dynamic_web_sources=True)
        result = await registry.dispatch("list_sources", {})
        assert result.ok and draft["draft_id"] in result.content
        assert "trial_discovery_source" in result.content and "Latest books" in result.content
        for hidden in ("Already applied", "private-plan", "private-session", "never-return"):
            assert hidden not in result.content
        assert database.conn.execute("SELECT COUNT(*) FROM custom_sources").fetchone()[0] == 0
    finally:
        database.close()


def test_pending_drafts_are_recent_bounded_metadata_only(tmp_path):
    database = Database(tmp_path / "recovery.db")
    database.initialize()
    store = WorkflowStore(database)
    try:
        for index in range(12):
            store.save_draft({"name": str(index), "intent": "x" * 4000, "status": "failed"})
        summaries = store.pending_draft_summaries()
        assert len(summaries) == 10 and summaries[0]["name"] == "11"
        assert all(len(row["intent"]) == 40 for row in summaries)
        assert "workflow" not in json.dumps(summaries)
    finally:
        database.close()


async def test_recovery_ids_survive_agent_result_limit_with_many_subscriptions(
    tmp_path, monkeypatch
):
    from openbiliclaw.agent.loop import AgentLoop

    database = Database(tmp_path / "recovery.db")
    database.initialize()
    store = WorkflowStore(database)
    try:
        ids = [
            store.save_draft(
                {
                    "name": "n" * 200,
                    "entry_url": "https://books.example/" + "x" * 1000,
                    "intent": "i" * 4000,
                    "status": "ready",
                }
            )["draft_id"]
            for _ in range(10)
        ]
        monkeypatch.setattr(
            database,
            "get_all_recipes",
            lambda: [
                {
                    "name": "existing" * 1000,
                    "source_type": "web",
                    "strategy": "feed",
                    "id": "existing",
                    "enabled": True,
                }
            ],
        )
        registry = build_source_tool_registry(database, dynamic_web_sources=True)
        result = await registry.dispatch("list_sources", {})
        loop = AgentLoop(llm=None, tools=registry)
        visible, truncated = loop._truncate_result(result.content)
        assert truncated and all(draft_id in visible for draft_id in ids)
    finally:
        database.close()
