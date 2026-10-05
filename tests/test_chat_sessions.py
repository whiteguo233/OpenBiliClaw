"""Multi-session chat model tests (「聊一聊」 M5).

Covers the ``chat_sessions`` table migration and CRUD, the
``chat_turns.session_id`` link with default-session ownership of legacy
rows, the session API endpoints, and LLM auto-titling with its truncated
prefix fallback.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app, fallback_session_title, generate_session_title
from openbiliclaw.storage.database import (
    DEFAULT_CHAT_SESSION_ID,
    DEFAULT_CHAT_SESSION_TITLE,
    Database,
)

if TYPE_CHECKING:
    from pathlib import Path


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def _app(tmp_path: Path, dialogue: Any = None) -> Any:
    database = _database(tmp_path)
    return create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue if dialogue is not None else object(),
    )


# --- Storage: migration and default session ---


def test_initialize_creates_chat_sessions_and_default(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tables = {
        str(row["name"])
        for row in database.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "chat_sessions" in tables
    columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }
    assert "session_id" in columns

    default = database.get_chat_session(DEFAULT_CHAT_SESSION_ID)
    assert default is not None
    assert default["title"] == DEFAULT_CHAT_SESSION_TITLE
    assert default["archived"] is False
    assert default["metadata"] == {}


def test_migration_preserves_legacy_chat_turns(tmp_path: Path) -> None:
    """A pre-M5 database gains session_id and keeps its history readable."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE chat_turns (
            turn_id       TEXT PRIMARY KEY,
            session       TEXT NOT NULL DEFAULT 'popup',
            scope         TEXT NOT NULL DEFAULT 'chat',
            subject_id    TEXT NOT NULL DEFAULT '',
            subject_title TEXT NOT NULL DEFAULT '',
            message       TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            reply         TEXT NOT NULL DEFAULT '',
            error         TEXT NOT NULL DEFAULT '',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO chat_turns (turn_id, message, status, reply) "
        "VALUES ('legacy-1', '旧消息', 'completed', '旧回复')"
    )
    conn.commit()
    conn.close()

    database = Database(db_path)
    database.initialize()

    turn = database.get_chat_turn("legacy-1")
    assert turn is not None
    assert turn["message"] == "旧消息"
    assert turn["session_id"] == ""
    rows, total = database.list_chat_turns_by_session(session_id=DEFAULT_CHAT_SESSION_ID)
    assert total == 1
    assert [row["turn_id"] for row in rows] == ["legacy-1"]


# --- Storage: CRUD ---


def test_chat_session_crud(tmp_path: Path) -> None:
    database = _database(tmp_path)
    created = database.create_chat_session(
        session_id="s1", title="", metadata={"skill": "口味伙伴"}
    )
    assert created["session_id"] == "s1"
    assert created["metadata"] == {"skill": "口味伙伴"}

    # Idempotent re-create keeps the original row.
    again = database.create_chat_session(session_id="s1", title="会被忽略")
    assert again["title"] == ""

    assert database.rename_chat_session("s1", title="新标题") is True
    assert database.get_chat_session("s1")["title"] == "新标题"
    assert database.rename_chat_session("missing", title="x") is False

    assert database.set_chat_session_archived("s1", archived=True) is True
    assert [row["session_id"] for row in database.list_chat_sessions()] == [DEFAULT_CHAT_SESSION_ID]
    archived = database.list_chat_sessions(include_archived=True)
    assert {row["session_id"] for row in archived} == {DEFAULT_CHAT_SESSION_ID, "s1"}
    assert database.set_chat_session_archived("s1", archived=False) is True


def test_default_session_cannot_be_archived(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        database.set_chat_session_archived(DEFAULT_CHAT_SESSION_ID, archived=True)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("archiving the default session must raise")
    assert database.get_chat_session(DEFAULT_CHAT_SESSION_ID)["archived"] is False


def test_list_sessions_preview_counts_and_activity_order(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_chat_session(session_id="old", title="旧会话")
    database.create_chat_turn(turn_id="t-old", message="旧话题", session_id="old")
    database.complete_chat_turn("t-old", reply="旧回复")
    database.create_chat_session(session_id="new", title="新会话")
    database.create_chat_turn(turn_id="t-new", message="新话题", session_id="new")

    sessions = database.list_chat_sessions()
    by_id = {row["session_id"]: row for row in sessions}
    assert by_id["new"]["last_message_preview"] == "新话题"
    assert by_id["new"]["active_turns"] == 1
    assert by_id["new"]["turn_count"] == 1
    assert by_id["old"]["active_turns"] == 0
    # Most recent activity sorts first.
    assert sessions[0]["session_id"] == "new"
    assert by_id[DEFAULT_CHAT_SESSION_ID]["turn_count"] == 0

    summary = database.get_chat_session_summary("new")
    assert summary is not None
    assert summary["turn_count"] == 1
    assert summary["active_turns"] == 1
    assert summary["last_message_preview"] == "新话题"
    assert database.get_chat_session_summary("missing") is None


def test_list_chat_turns_by_session_pagination(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_chat_session(session_id="s1")
    for index in range(5):
        database.create_chat_turn(turn_id=f"t-{index}", message=f"消息 {index}", session_id="s1")
    page, total = database.list_chat_turns_by_session(session_id="s1", limit=2, offset=0)
    assert total == 5
    assert [row["turn_id"] for row in page] == ["t-3", "t-4"]
    page, total = database.list_chat_turns_by_session(session_id="s1", limit=2, offset=2)
    assert [row["turn_id"] for row in page] == ["t-1", "t-2"]


# --- Title generation helpers ---


class _FakeTitleLLM:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=self._result)


def test_fallback_session_title_collapses_whitespace() -> None:
    assert fallback_session_title("  聊聊\n最近看的   番剧 ") == "聊聊 最近看的 番剧"
    long_message = "很" * 100
    assert len(fallback_session_title(long_message)) == 30


def test_generate_session_title_from_llm() -> None:
    llm = _FakeTitleLLM(result=json.dumps({"title": "追番推荐"}))
    title = asyncio.run(generate_session_title(llm, "帮我推荐几部番"))
    assert title == "追番推荐"
    assert llm.calls[0]["caller"] == "chat.session_title"


def test_generate_session_title_strips_quotes_and_truncates() -> None:
    llm = _FakeTitleLLM(result=json.dumps({"title": "「" + "长" * 100 + "」"}))
    title = asyncio.run(generate_session_title(llm, "消息"))
    assert len(title) <= 30


def test_generate_session_title_falls_back_on_error() -> None:
    llm = _FakeTitleLLM(error=RuntimeError("provider down"))
    title = asyncio.run(generate_session_title(llm, "帮我看看订阅"))
    assert title == "帮我看看订阅"


def test_generate_session_title_falls_back_on_bad_json() -> None:
    llm = _FakeTitleLLM(result="not json at all")
    title = asyncio.run(generate_session_title(llm, "帮我看看订阅"))
    assert title == "帮我看看订阅"


# --- API integration ---


def test_persona_catalog_and_session_preferences_are_independent_metadata(tmp_path: Path) -> None:
    app = _app(tmp_path)
    database = app.state.runtime_context.database
    database.create_chat_session(session_id="old", metadata={"skill": "taste-explorer"})
    with TestClient(app) as client:
        catalog_response = client.get("/api/chat/personas").json()
        assert catalog_response["example_prompt"] == "今天有点累，想休息。"
        catalog = catalog_response["personas"]
        assert [(item["id"], item["title"]) for item in catalog] == [
            ("natural", "自然朋友"),
            ("concise", "简洁直接"),
            ("warm", "温柔倾听"),
            ("playful", "轻松幽默"),
            ("analytical", "理性分析"),
            ("socratic", "循循善诱"),
        ]
        assert [item["id"] for item in catalog if item["default"]] == ["natural"]
        assert all(
            set(item) == {"id", "title", "description", "example", "default"} for item in catalog
        )
        assert all(item["description"] and item["example"] for item in catalog)
        old = client.get("/api/chat/sessions/old").json()["session"]
        assert old["metadata"] == {"skill": "taste-explorer", "persona": "natural"}
        # Reads expose a default but do not rewrite existing session metadata.
        assert database.get_chat_session("old")["metadata"] == {"skill": "taste-explorer"}

        created = client.post(
            "/api/chat/sessions",
            json={
                "session_id": "styled",
                "metadata": {
                    "persona": "warm",
                    "skill": "system-steward",
                    "nested": {"keep": True},
                },
            },
        ).json()
        assert created["metadata"]["persona"] == "warm"
        for item in catalog:
            changed = client.patch("/api/chat/sessions/styled", json={"persona": item["id"]})
            assert changed.status_code == 200
            assert changed.json()["metadata"] == {
                "persona": item["id"],
                "skill": "system-steward",
                "nested": {"keep": True},
            }
        sessions = client.get("/api/chat/sessions").json()["items"]
        assert {row["session_id"]: row["metadata"]["persona"] for row in sessions} == {
            "old": "natural",
            "styled": "socratic",
            DEFAULT_CHAT_SESSION_ID: "natural",
        }
        assert (
            client.patch("/api/chat/sessions/missing", json={"persona": "warm"}).status_code == 404
        )

    # Preference survives a new storage owner rather than an in-memory cache.
    reopened = Database(tmp_path / "openbiliclaw.db")
    reopened.initialize()
    assert reopened.get_chat_session("styled")["metadata"]["persona"] == "socratic"
    reopened.close()


@pytest.mark.parametrize("invalid", ["unknown", "", " Warm ", None, 1, {"id": "warm"}])
def test_invalid_persona_has_no_session_side_effects(tmp_path: Path, invalid: object) -> None:
    app = _app(tmp_path)
    database = app.state.runtime_context.database
    database.create_chat_session(session_id="s", title="原名", metadata={"keep": 1})
    before = database.get_chat_session("s")
    with TestClient(app) as client:
        response = client.patch(
            "/api/chat/sessions/s",
            json={
                "persona": invalid,
                "title": "不该改名",
                "archived": True,
            },
        )
        assert response.status_code == 422
        assert database.get_chat_session("s") == before
        response = client.post(
            "/api/chat/sessions",
            json={
                "session_id": "invalid",
                "metadata": {"persona": invalid},
            },
        )
        assert response.status_code == 422
        assert database.get_chat_session("invalid") is None


def test_persona_write_preserves_latest_metadata_and_handles_old_values(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_chat_session(session_id="s", metadata={"persona": "old-template", "other": 1})
    second = Database(tmp_path / "openbiliclaw.db")
    second.initialize()
    database.get_chat_session("s")
    # An unrelated metadata writer may commit after an API preflight read.
    second.conn.execute(
        "UPDATE chat_sessions SET metadata=json_set(metadata, '$.other', 2) WHERE session_id='s'"
    )
    second.conn.commit()
    assert database.set_chat_session_persona("s", persona="warm")
    assert database.get_chat_session("s")["metadata"] == {"persona": "warm", "other": 2}
    assert not database.set_chat_session_persona("missing", persona="warm")
    for old_value in ("bad json", "[]", "null"):
        second.conn.execute(
            "UPDATE chat_sessions SET metadata=? WHERE session_id='s'", (old_value,)
        )
        second.conn.commit()
        assert database.set_chat_session_persona("s", persona="concise")
        assert database.get_chat_session("s")["metadata"] == {"persona": "concise"}
    second.close()


def test_session_endpoints_create_list_patch_get(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/api/chat/sessions", json={"metadata": {"skill": "口味伙伴"}})
        assert created.status_code == 200
        session = created.json()
        session_id = session["session_id"]
        assert session["title"] == ""
        assert session["metadata"] == {"skill": "口味伙伴", "persona": "natural"}
        assert session["archived"] is False

        listed = client.get("/api/chat/sessions")
        assert listed.status_code == 200
        ids = [item["session_id"] for item in listed.json()["items"]]
        assert DEFAULT_CHAT_SESSION_ID in ids
        assert session_id in ids

        renamed = client.patch(f"/api/chat/sessions/{session_id}", json={"title": "我的话题"})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "我的话题"

        archived = client.patch(f"/api/chat/sessions/{session_id}", json={"archived": True})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        ids = [item["session_id"] for item in client.get("/api/chat/sessions").json()["items"]]
        assert session_id not in ids

        # The default session cannot be archived.
        rejected = client.patch(
            f"/api/chat/sessions/{DEFAULT_CHAT_SESSION_ID}", json={"archived": True}
        )
        assert rejected.status_code == 422


def test_session_detail_includes_paginated_turns(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions", json={}).json()["session_id"]
        for index in range(3):
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": f"sess-turn-{index}",
                    "message": f"消息 {index}",
                    "session": "desktop",
                    "scope": "chat",
                    "session_id": session_id,
                    "streaming": True,
                },
            )
            assert response.status_code == 200
            assert response.json()["session_id"] == session_id

        detail = client.get(f"/api/chat/sessions/{session_id}", params={"limit": 2})
        assert detail.status_code == 200
        body = detail.json()
        assert body["session"]["session_id"] == session_id
        assert body["session"]["turn_count"] == 3
        assert body["session"]["last_message_preview"] == "消息 2"
        assert body["total"] == 3
        assert [item["turn_id"] for item in body["items"]] == ["sess-turn-1", "sess-turn-2"]
        page2 = client.get(
            f"/api/chat/sessions/{session_id}", params={"limit": 2, "offset": 2}
        ).json()
        assert [item["turn_id"] for item in page2["items"]] == ["sess-turn-0"]

        assert client.get("/api/chat/sessions/unknown-session").status_code == 404


def test_post_turn_defaults_to_default_session(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "no-session-turn",
                "message": "没有指定会话",
                "session": "desktop",
                "scope": "chat",
                "streaming": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["session_id"] == DEFAULT_CHAT_SESSION_ID

        detail = client.get(f"/api/chat/sessions/{DEFAULT_CHAT_SESSION_ID}").json()
        assert [item["turn_id"] for item in detail["items"]] == ["no-session-turn"]


def test_post_turn_with_unknown_session_id_is_404(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "orphan-turn",
                "message": "发往不存在的会话",
                "session_id": "missing-session",
                "streaming": True,
            },
        )
        assert response.status_code == 404


def _wait_for_title(client: TestClient, session_id: str, *, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        title = client.get(f"/api/chat/sessions/{session_id}").json()["session"]["title"]
        if title:
            return title
        time.sleep(0.05)
    raise AssertionError("session title was not generated in time")


def test_session_title_generated_via_llm(tmp_path: Path) -> None:
    llm = _FakeTitleLLM(result=json.dumps({"title": "追番清单"}))
    dialogue = SimpleNamespace(_llm_service=llm)
    app = _app(tmp_path, dialogue=dialogue)
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions", json={}).json()["session_id"]
        created = client.post(
            "/api/chat/turns",
            json={
                "turn_id": "title-turn-1",
                "message": "帮我整理一下最近在追的番",
                "session": "desktop",
                "scope": "chat",
                "session_id": session_id,
                "streaming": True,
            },
        )
        assert created.status_code == 200
        assert _wait_for_title(client, session_id) == "追番清单"
        assert llm.calls, "LLM titling task should have run"

        # A follow-up message must not regenerate the title.
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "title-turn-2",
                "message": "再加上几部老番",
                "session": "desktop",
                "scope": "chat",
                "session_id": session_id,
                "streaming": True,
            },
        )
        assert len(llm.calls) == 1


def test_session_title_falls_back_when_llm_fails(tmp_path: Path) -> None:
    llm = _FakeTitleLLM(error=RuntimeError("provider down"))
    dialogue = SimpleNamespace(_llm_service=llm)
    app = _app(tmp_path, dialogue=dialogue)
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions", json={}).json()["session_id"]
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "title-fallback-turn",
                "message": "聊聊今晚吃什么",
                "session": "desktop",
                "scope": "chat",
                "session_id": session_id,
                "streaming": True,
            },
        )
        assert _wait_for_title(client, session_id) == "聊聊今晚吃什么"


def test_session_title_disabled_uses_prefix_without_llm(tmp_path: Path) -> None:
    llm = _FakeTitleLLM(result=json.dumps({"title": "不应出现"}))
    dialogue = SimpleNamespace(_llm_service=llm)
    app = _app(tmp_path, dialogue=dialogue)
    app.state.runtime_context.config = SimpleNamespace(
        agent=SimpleNamespace(session_title_enabled=False)
    )
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions", json={}).json()["session_id"]
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "title-disabled-turn",
                "message": "直接给我截断标题",
                "session": "desktop",
                "scope": "chat",
                "session_id": session_id,
                "streaming": True,
            },
        )
        assert _wait_for_title(client, session_id) == "直接给我截断标题"
        assert not llm.calls


def test_session_title_does_not_overwrite_manual_rename(tmp_path: Path) -> None:
    llm = _FakeTitleLLM(result=json.dumps({"title": "LLM 标题"}))
    dialogue = SimpleNamespace(_llm_service=llm)
    app = _app(tmp_path, dialogue=dialogue)
    with TestClient(app) as client:
        session_id = client.post("/api/chat/sessions", json={}).json()["session_id"]
        client.patch(f"/api/chat/sessions/{session_id}", json={"title": "手动标题"})
        client.post(
            "/api/chat/turns",
            json={
                "turn_id": "title-manual-turn",
                "message": "这条消息不应改标题",
                "session": "desktop",
                "scope": "chat",
                "session_id": session_id,
                "streaming": True,
            },
        )
        time.sleep(0.3)
        title = client.get(f"/api/chat/sessions/{session_id}").json()["session"]["title"]
        assert title == "手动标题"
        assert not llm.calls
