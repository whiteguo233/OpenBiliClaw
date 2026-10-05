"""Tests for xhs task queue, creator subscriptions, and API endpoints.

The task queue is the backend side of the extension's background
dispatcher: the backend enqueues search/creator tasks, the extension
polls for the next pending one, executes it (no-scroll), and posts the
result back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.models import XiaohongshuSourceConfigOut
from openbiliclaw.sources.xhs_tasks import (
    XhsCreatorStore,
    XhsTaskQueue,
    xhs_bootstrap_notes_to_events,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


@pytest.fixture
def queue(db: Database) -> XhsTaskQueue:
    return XhsTaskQueue(db)


@pytest.fixture
def creator_store(db: Database) -> XhsCreatorStore:
    return XhsCreatorStore(db)


def test_runtime_state_schema_adds_rate_limit_strikes_to_existing_database(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "legacy-xhs.db")
    database.initialize()
    database.conn.executescript("""
        DROP TABLE IF EXISTS xhs_task_runtime_state;
        CREATE TABLE xhs_task_runtime_state (
            singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
            next_claim_at   TIMESTAMP,
            cooldown_until  TIMESTAMP,
            cooldown_reason TEXT NOT NULL DEFAULT '',
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO xhs_task_runtime_state(singleton) VALUES (1);
    """)

    queue = XhsTaskQueue(database)

    columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(xhs_task_runtime_state)").fetchall()
    }
    assert "rate_limit_strikes" in columns
    assert queue.runtime_state()["rate_limit_strikes"] == 0
    database.close()


def test_xhs_api_config_model_uses_safety_defaults() -> None:
    config = XiaohongshuSourceConfigOut()

    assert config.daily_search_budget == 20
    assert config.task_interval_seconds == 1200
    assert config.min_interval_minutes == 20


class TestXhsTaskQueue:
    def test_enqueue_and_next(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "机械键盘"})
        task = queue.next_pending()

        assert task is not None
        assert task["type"] == "search"
        payload = json.loads(task["payload_json"])
        assert payload["keyword"] == "机械键盘"
        assert task["status"] == "in_progress"

    def test_next_pending_claims_task_until_terminal_status(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("bootstrap_profile", {"scopes": ["saved", "liked"]})

        first = queue.next_pending()
        assert first is not None
        assert first["status"] == "in_progress"

        assert queue.next_pending() is None

        queue.complete(first["id"], urls=[])
        assert queue.next_pending() is None

    def test_next_returns_none_when_empty(self, queue: XhsTaskQueue) -> None:
        assert queue.next_pending() is None

    def test_next_returns_oldest_first(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "first"})
        queue.enqueue("search", {"keyword": "second"})

        task = queue.next_pending()
        assert task is not None
        payload = json.loads(task["payload_json"])
        assert payload["keyword"] == "first"

    def test_search_claims_respect_persisted_task_interval(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        queue.enqueue("search", {"keyword": "first"})
        queue.enqueue("search", {"keyword": "second"})

        first = queue.next_pending(
            min_interval_seconds=60,
            jitter_ratio=0,
            now=now,
        )
        assert first is not None
        queue.complete(str(first["id"]), urls=[])

        assert (
            queue.next_pending(
                min_interval_seconds=60,
                jitter_ratio=0,
                now=now + timedelta(seconds=59),
            )
            is None
        )
        second = queue.next_pending(
            min_interval_seconds=60,
            jitter_ratio=0,
            now=now + timedelta(seconds=60),
        )
        assert second is not None
        assert json.loads(str(second["payload_json"]))["keyword"] == "second"

    def test_search_claim_interval_is_stably_jittered_around_target(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        queue.enqueue("search", {"keyword": "first"})
        queue.enqueue("search", {"keyword": "second"})

        first = queue.next_pending(min_interval_seconds=1200, now=now)
        assert first is not None
        delay = int(queue.runtime_state(now=now)["next_claim_delay_seconds"])
        assert 900 <= delay <= 1500
        queue.complete(str(first["id"]), urls=[])

        assert queue.next_pending(now=now + timedelta(seconds=delay - 1)) is None
        second = queue.next_pending(now=now + timedelta(seconds=delay))
        assert second is not None
        assert json.loads(str(second["payload_json"]))["keyword"] == "second"

    def test_search_pacing_does_not_hide_a_later_bootstrap(
        self,
        queue: XhsTaskQueue,
        db: Database,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        queue.enqueue("search", {"keyword": "paced"})
        queue.enqueue("bootstrap_profile", {"scopes": ["saved"]})
        db.conn.execute(
            """
            UPDATE xhs_task_runtime_state
            SET next_claim_at = ?
            WHERE singleton = 1
            """,
            ((now + timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S"),),
        )
        db.conn.commit()

        task = queue.next_pending(min_interval_seconds=60, now=now)

        assert task is not None
        assert task["type"] == "bootstrap_profile"

    def test_rate_limit_persists_and_blocks_all_task_claims(
        self,
        queue: XhsTaskQueue,
        db: Database,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        queue.enqueue("search", {"keyword": "first"})
        queue.enqueue("bootstrap_profile", {"scopes": ["saved"]})
        task = queue.next_pending(now=now)
        assert task is not None

        state = queue.record_rate_limit(
            str(task["id"]),
            error="xhs_rate_limited",
            cooldown_seconds=3600,
            now=now,
        )
        assert state["rate_limited"] is True
        assert state["cooldown_remaining_seconds"] == 3600
        assert state["rate_limit_strikes"] == 1

        restored_queue = XhsTaskQueue(db)
        assert restored_queue.next_pending(now=now + timedelta(seconds=3599)) is None
        resumed = restored_queue.next_pending(now=now + timedelta(seconds=3600))
        assert resumed is not None
        assert resumed["type"] == "bootstrap_profile"

        stored = restored_queue.get(str(task["id"]))
        assert stored is not None
        assert stored["status"] == "failed"
        result = json.loads(str(stored["result_json"]))
        assert result["error"] == "xhs_rate_limited"
        assert result["rate_limited"] is True
        assert result["rate_limit_strikes"] == 1

    def test_rate_limit_uses_exponential_backoff_and_caps_it(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)

        first = queue.record_rate_limit(
            cooldown_seconds=60,
            max_cooldown_seconds=180,
            now=now,
        )
        second = queue.record_rate_limit(
            cooldown_seconds=60,
            max_cooldown_seconds=180,
            now=now + timedelta(seconds=61),
        )
        third = queue.record_rate_limit(
            cooldown_seconds=60,
            max_cooldown_seconds=180,
            now=now + timedelta(seconds=182),
        )

        assert first["rate_limit_strikes"] == 1
        assert first["cooldown_remaining_seconds"] == 60
        assert second["rate_limit_strikes"] == 2
        assert second["cooldown_remaining_seconds"] == 120
        assert third["rate_limit_strikes"] == 3
        assert third["cooldown_remaining_seconds"] == 180

    def test_duplicate_rate_limit_report_reuses_active_strike(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        queue.record_rate_limit(cooldown_seconds=60, now=now)

        duplicate = queue.record_rate_limit(
            cooldown_seconds=60,
            now=now + timedelta(seconds=30),
        )

        assert duplicate["rate_limit_strikes"] == 1
        assert duplicate["cooldown_remaining_seconds"] == 60

    def test_success_after_cooldown_resets_rate_limit_backoff(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        past = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
        queue.record_rate_limit(cooldown_seconds=60, now=past)
        queue.enqueue("search", {"keyword": "healthy"})
        task = queue.next_pending()
        assert task is not None

        queue.complete(str(task["id"]), urls=[])

        assert queue.runtime_state()["rate_limit_strikes"] == 0
        next_episode = queue.record_rate_limit(cooldown_seconds=60)
        assert next_episode["rate_limit_strikes"] == 1
        assert next_episode["cooldown_remaining_seconds"] == 60

    def test_late_success_does_not_cancel_an_active_cooldown(
        self,
        queue: XhsTaskQueue,
    ) -> None:
        queue.enqueue("search", {"keyword": "already-running"})
        task = queue.next_pending()
        assert task is not None
        queue.record_rate_limit(cooldown_seconds=60)

        queue.complete(str(task["id"]), urls=[])

        state = queue.runtime_state()
        assert state["rate_limited"] is True
        assert state["rate_limit_strikes"] == 1

    def test_complete_marks_task_done(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "x"})
        task = queue.next_pending()
        assert task is not None

        queue.complete(task["id"], urls=["https://www.xiaohongshu.com/explore/abc"])

        # Should not return completed tasks
        assert queue.next_pending() is None

    def test_merge_result_enriches_existing_note_without_readding_it(
        self, queue: XhsTaskQueue
    ) -> None:
        assert queue.enqueue("bootstrap_profile", {"scopes": ["saved"]})
        task = queue.next_pending()
        assert task is not None
        note = {
            "scope": "saved",
            "title": "partial saved",
            "url": "https://www.xiaohongshu.com/explore/saved-partial",
            "note_id": "saved-partial",
        }

        assert queue.merge_result(task["id"], notes=[note]) == [note]
        assert (
            queue.merge_result(
                task["id"],
                notes=[
                    {
                        **note,
                        "title": "state title must not replace the partial title",
                        "published_at": 1783492200000,
                        "published_label": "3小时前",
                    }
                ],
                complete=True,
            )
            == []
        )

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert result["notes"][0]["title"] == "partial saved"
        assert result["notes"][0]["published_at"] == 1783492200000
        assert result["notes"][0]["published_label"] == "3小时前"

    def test_duplicate_note_at_cap_accepts_only_token_upgrade(self, queue: XhsTaskQueue) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved"], "max_items_per_scope": 1},
        )
        task = queue.next_pending()
        assert task is not None
        bare_url = "https://www.xiaohongshu.com/explore/token-upgrade"
        tokenized_url = f"{bare_url}?xsec_token=fresh-token"
        partial_note = {
            "scope": "saved",
            "note_id": "token-upgrade",
            "title": "first title",
            "url": bare_url,
            "xsec_token": "",
        }
        queue.merge_result(task["id"], urls=[bare_url], notes=[partial_note])

        canonical = queue.stage_final_result(
            task["id"],
            terminal_status="ok",
            urls=[tokenized_url],
            notes=[
                {
                    **partial_note,
                    "title": "must not replace first title",
                    "url": tokenized_url,
                    "xsec_token": "fresh-token",
                },
                {
                    "scope": "saved",
                    "note_id": "overflow",
                    "title": "overflow",
                    "url": "https://www.xiaohongshu.com/explore/overflow",
                },
            ],
        )

        assert len(canonical["notes"]) == 1
        assert canonical["notes"][0]["title"] == "first title"
        assert canonical["notes"][0]["url"] == tokenized_url
        assert canonical["notes"][0]["xsec_token"] == "fresh-token"
        assert canonical["urls"] == [tokenized_url]

    def test_duplicate_note_with_preexisting_token_upgrades_bare_url(
        self, queue: XhsTaskQueue
    ) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved"], "max_items_per_scope": 1},
        )
        task = queue.next_pending()
        assert task is not None
        bare_url = "https://www.xiaohongshu.com/explore/preexisting-token"
        tokenized_url = f"{bare_url}?xsec_token=known-token"
        partial_note = {
            "scope": "saved",
            "note_id": "preexisting-token",
            "title": "first title",
            "url": bare_url,
            "xsec_token": "known-token",
        }
        queue.merge_result(task["id"], urls=[bare_url], notes=[partial_note])

        canonical = queue.stage_final_result(
            task["id"],
            terminal_status="ok",
            urls=[tokenized_url],
            notes=[{**partial_note, "url": tokenized_url}],
        )

        assert canonical["notes"][0]["url"] == tokenized_url
        assert canonical["notes"][0]["xsec_token"] == "known-token"
        assert canonical["urls"] == [tokenized_url]

    def test_merge_result_counts_disjoint_partial_pages(self, queue: XhsTaskQueue) -> None:
        assert queue.enqueue("bootstrap_profile", {"scopes": ["saved"]})
        task = queue.next_pending()
        assert task is not None

        first_page = [
            {"scope": "saved", "note_id": f"first-{index}", "title": "first"} for index in range(5)
        ]
        second_page = [
            {"scope": "saved", "note_id": f"second-{index}", "title": "second"}
            for index in range(5)
        ]
        queue.merge_result(task["id"], notes=first_page, scope_counts={"saved": 5})
        queue.merge_result(
            task["id"],
            notes=second_page,
            scope_counts={"saved": 5},
            complete=True,
        )

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert len(result["notes"]) == 10
        assert result["scope_counts"]["saved"] == 10

    def test_merge_result_enforces_bootstrap_scope_cap_across_partial_and_final(
        self, queue: XhsTaskQueue
    ) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked"], "max_items_per_scope": 2},
        )
        task = queue.next_pending()
        assert task is not None

        partial_saved = [
            {
                "scope": "saved",
                "note_id": f"saved-partial-{index}",
                "title": "partial saved",
                "url": f"https://www.xiaohongshu.com/explore/saved-partial-{index}",
            }
            for index in range(2)
        ]
        overflow_saved = [
            {
                "scope": "saved",
                "note_id": f"saved-final-{index}",
                "title": "overflow saved",
                "url": f"https://www.xiaohongshu.com/explore/saved-final-{index}",
            }
            for index in range(2)
        ]
        final_liked = [
            {
                "scope": "liked",
                "note_id": f"liked-final-{index}",
                "title": "final liked",
                "url": f"https://www.xiaohongshu.com/explore/liked-final-{index}",
            }
            for index in range(2)
        ]

        queue.merge_result(
            task["id"],
            urls=[str(note["url"]) for note in partial_saved],
            notes=partial_saved,
            scope_counts={"saved": 2, "liked": 0},
        )
        canonical = queue.stage_final_result(
            task["id"],
            terminal_status="ok",
            urls=[str(note["url"]) for note in [*overflow_saved, *final_liked]],
            notes=[*overflow_saved, *final_liked],
            scope_counts={"saved": 2, "liked": 2},
        )

        assert [note["note_id"] for note in canonical["notes"]] == [
            "saved-partial-0",
            "saved-partial-1",
            "liked-final-0",
            "liked-final-1",
        ]
        assert canonical["scope_counts"] == {"saved": 2, "liked": 2}
        assert canonical["urls"] == [
            *(str(note["url"]) for note in partial_saved),
            *(str(note["url"]) for note in final_liked),
        ]

    def test_bootstrap_cap_drops_overflow_urls_when_other_scope_is_empty(
        self, queue: XhsTaskQueue
    ) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked"], "max_items_per_scope": 2},
        )
        task = queue.next_pending()
        assert task is not None

        notes = [
            {
                "scope": "saved",
                "note_id": f"saved-{index}",
                "url": f"https://www.xiaohongshu.com/explore/saved-{index}",
            }
            for index in range(4)
        ]
        queue.complete(
            task["id"],
            urls=[str(note["url"]) for note in notes],
            notes=notes,
            scope_counts={"saved": 4, "liked": 0},
        )

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert [note["note_id"] for note in result["notes"]] == ["saved-0", "saved-1"]
        assert result["urls"] == [
            "https://www.xiaohongshu.com/explore/saved-0",
            "https://www.xiaohongshu.com/explore/saved-1",
        ]
        assert result["scope_counts"] == {"saved": 2, "liked": 0}

    def test_bootstrap_result_accepts_only_task_scopes_and_integer_counts(
        self, queue: XhsTaskQueue
    ) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved"], "max_items_per_scope": 2},
        )
        task = queue.next_pending()
        assert task is not None
        notes = [
            {
                "scope": scope,
                "note_id": scope,
                "url": f"https://www.xiaohongshu.com/explore/{scope}",
            }
            for scope in ("saved", "liked", "xhs_history", "unknown")
        ]

        queue.complete(
            task["id"],
            urls=[str(note["url"]) for note in notes],
            notes=notes,
            scope_counts={
                "saved": "999",
                "liked": 1,
                "xhs_history": 1.5,
                "unknown": 1,
            },
        )

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert [note["scope"] for note in result["notes"]] == ["saved"]
        assert result["urls"] == ["https://www.xiaohongshu.com/explore/saved"]
        assert result["scope_counts"] == {"saved": 1}

    def test_bootstrap_missing_scopes_matches_extension_defaults(self, queue: XhsTaskQueue) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"max_items_per_scope": 1},
        )
        task = queue.next_pending()
        assert task is not None
        notes = [
            {
                "scope": scope,
                "note_id": scope,
                "url": f"https://www.xiaohongshu.com/explore/{scope}",
            }
            for scope in ("saved", "liked", "xhs_history")
        ]

        queue.complete(task["id"], notes=notes)

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert [note["scope"] for note in result["notes"]] == [
            "saved",
            "liked",
            "xhs_history",
        ]
        assert result["urls"] == [str(note["url"]) for note in notes]

    def test_rate_limit_result_uses_bootstrap_scope_policy(self, queue: XhsTaskQueue) -> None:
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved"], "max_items_per_scope": 1},
        )
        task = queue.next_pending()
        assert task is not None
        notes = [
            {
                "scope": "saved",
                "note_id": f"saved-{index}",
                "url": f"https://www.xiaohongshu.com/explore/saved-{index}",
            }
            for index in range(2)
        ]

        queue.record_rate_limit(
            task["id"],
            urls=[str(note["url"]) for note in notes],
            notes=notes,
            scope_counts={"saved": 2, "liked": 50},
        )

        stored = queue.get(task["id"])
        assert stored is not None
        result = json.loads(stored["result_json"])
        assert [note["note_id"] for note in result["notes"]] == ["saved-0"]
        assert result["urls"] == ["https://www.xiaohongshu.com/explore/saved-0"]
        assert result["scope_counts"] == {"saved": 1}

    def test_fail_marks_task_failed(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "x"})
        task = queue.next_pending()
        assert task is not None

        queue.fail(task["id"], error="timeout")

        assert queue.next_pending() is None

    def test_daily_budget_enforced(self, queue: XhsTaskQueue) -> None:
        budget = 3
        for i in range(budget):
            assert queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=budget)

        # Next enqueue should be rejected
        assert not queue.enqueue("search", {"keyword": "over"}, daily_budget=budget)

    def test_zero_daily_budget_disables_daily_cap(self, queue: XhsTaskQueue) -> None:
        for i in range(5):
            assert queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=0)

    def test_creator_tasks_have_separate_budget(self, queue: XhsTaskQueue) -> None:
        # Fill search budget
        for i in range(3):
            queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=3)

        # Creator budget should still be available
        assert queue.enqueue("creator", {"creator_url": "https://xhs.com/u/1"}, daily_budget=3)


def test_xhs_bootstrap_notes_to_events_maps_scopes() -> None:
    events = xhs_bootstrap_notes_to_events(
        [
            {
                "scope": "saved",
                "title": "收藏笔记",
                "url": "https://www.xiaohongshu.com/explore/a",
                "note_id": "a",
            },
            {
                "scope": "liked",
                "title": "点赞笔记",
                "url": "https://www.xiaohongshu.com/explore/b",
                "note_id": "b",
            },
            {
                "scope": "xhs_history",
                "title": "看过笔记",
                "url": "https://www.xiaohongshu.com/explore/c",
                "note_id": "c",
            },
        ]
    )

    assert [event["event_type"] for event in events] == ["favorite", "like", "view"]
    assert all(event["metadata"]["source_platform"] == "xiaohongshu" for event in events)


def test_xhs_bootstrap_notes_to_events_preserves_metadata_and_skips_empty() -> None:
    events = xhs_bootstrap_notes_to_events(
        [
            {
                "scope": "saved",
                "title": "手冲咖啡入门",
                "url": "https://www.xiaohongshu.com/explore/note-1",
                "note_id": "note-1",
                "xsec_token": "token-1",
                "author": "豆子老师",
                "cover_url": "https://example.com/cover.jpg",
            },
            {"scope": "liked", "title": "", "url": ""},
            {"scope": "unknown", "title": "未知", "url": "https://example.com/x"},
        ]
    )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "favorite"
    assert event["title"] == "手冲咖啡入门"
    assert event["url"] == "https://www.xiaohongshu.com/explore/note-1"
    assert event["context"] == "小红书收藏：手冲咖啡入门 作者：豆子老师"
    assert event["metadata"] == {
        "source_platform": "xiaohongshu",
        "note_id": "note-1",
        "xsec_token": "token-1",
        "author": "豆子老师",
        "cover_url": "https://example.com/cover.jpg",
        "import_source": "xhs_bootstrap_saved",
        "signal_strength": 1.0,
    }


class TestXhsCreatorStore:
    def test_add_and_list(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add(
            creator_id="uid123",
            creator_url="https://www.xiaohongshu.com/user/profile/uid123",
            display_name="键圈老用户",
        )

        subs = creator_store.list_all()
        assert len(subs) == 1
        assert subs[0]["creator_id"] == "uid123"
        assert subs[0]["display_name"] == "键圈老用户"

    def test_add_duplicate_is_ignored(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")

        assert len(creator_store.list_all()) == 1

    def test_delete(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")
        subs = creator_store.list_all()
        assert len(subs) == 1

        deleted = creator_store.delete(subs[0]["id"])
        assert deleted is True
        assert len(creator_store.list_all()) == 0

    def test_delete_nonexistent_returns_false(self, creator_store: XhsCreatorStore) -> None:
        assert creator_store.delete(9999) is False

    def test_due_for_fetch(self, creator_store: XhsCreatorStore, db: Database) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")

        # Fresh subscription should be due
        due = creator_store.due_for_fetch(hours=24)
        assert len(due) == 1

        # After marking fetched, should not be due
        creator_store.mark_fetched(due[0]["id"])
        assert len(creator_store.due_for_fetch(hours=24)) == 0


# ── API endpoint tests ────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = Database(tmp_path / "api.db")
    db.initialize()

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", proxy="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                enabled=True,
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
        ),
        scheduler=SimpleNamespace(
            enabled=True,
            pool_target_count=300,
            account_sync_interval_hours=24,
        ),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


class TestXhsTaskApi:
    def test_next_task_returns_204_when_empty(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/sources/xhs/next-task")
        assert resp.status_code == 204

    def test_default_off_cancels_queued_incremental_task_before_claim(
        self,
        api_client: TestClient,
    ) -> None:
        ctx = api_client.app.state.runtime_context
        queue = XhsTaskQueue(ctx.database)
        scheduled_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {"incremental": True, "incremental_owner": "scheduler"},
            daily_budget=0,
        )
        assert scheduled_id is not None

        blocked = api_client.get("/api/sources/xhs/next-task")

        assert blocked.status_code == 204
        stored = queue.get(scheduled_id)
        assert stored is not None
        assert stored["status"] == "failed"

        manual_id = queue.enqueue_with_id(
            "bootstrap_profile",
            {"incremental": True, "scopes": ["saved"]},
            daily_budget=0,
        )
        assert manual_id is not None
        claimed = api_client.get("/api/sources/xhs/next-task")
        assert claimed.status_code == 200
        assert claimed.json()["id"] == manual_id

    def test_disabled_source_does_not_claim_queued_discovery_task(
        self,
        api_client: TestClient,
    ) -> None:
        ctx = api_client.app.state.runtime_context
        queue = XhsTaskQueue(ctx.database)
        task_id = queue.enqueue_with_id(
            "search",
            {"keyword": "must-stay-pending"},
            daily_budget=0,
        )
        assert task_id is not None

        ctx.config.sources.xiaohongshu.enabled = False
        blocked = api_client.get("/api/sources/xhs/next-task")

        assert blocked.status_code == 204
        stored = queue.get(task_id)
        assert stored is not None
        assert stored["status"] == "pending"

        ctx.config.sources.xiaohongshu.enabled = True
        resumed = api_client.get("/api/sources/xhs/next-task")
        assert resumed.status_code == 200
        assert resumed.json()["id"] == task_id

    def test_disabled_source_status_wins_over_persisted_cooldown(
        self,
        api_client: TestClient,
    ) -> None:
        ctx = api_client.app.state.runtime_context
        XhsTaskQueue(ctx.database).record_rate_limit(cooldown_seconds=600)
        ctx.config.sources.xiaohongshu.enabled = False

        status = api_client.get("/api/sources/status")

        assert status.status_code == 200
        xhs_status = status.json()["xiaohongshu"]
        assert xhs_status["enabled"] is False
        assert xhs_status["state"] != "rate_limited"
        assert xhs_status["feed_paused"] is False

    def test_next_task_applies_configured_interval(self, api_client: TestClient) -> None:
        db = api_client.app.state.runtime_context.database
        queue = XhsTaskQueue(db)
        assert queue.enqueue("search", {"keyword": "first"})
        assert queue.enqueue("search", {"keyword": "second"})

        first = api_client.get("/api/sources/xhs/next-task")
        assert first.status_code == 200
        completed = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": first.json()["id"],
                "status": "ok",
                "urls": [],
                "notes": [],
            },
        )
        assert completed.status_code == 200

        throttled = api_client.get("/api/sources/xhs/next-task")
        assert throttled.status_code == 204
        assert int(throttled.headers["retry-after"]) > 0

        db.conn.execute(
            """
            UPDATE xhs_task_runtime_state
            SET next_claim_at = datetime('now', '-1 second')
            WHERE singleton = 1
            """
        )
        db.conn.commit()
        second = api_client.get("/api/sources/xhs/next-task")
        assert second.status_code == 200
        assert second.json()["keyword"] == "second"

    def test_next_task_default_does_not_request_published_at(
        self,
        api_client: TestClient,
    ) -> None:
        ctx = api_client.app.state.runtime_context
        queue = XhsTaskQueue(ctx.database)
        assert queue.enqueue("search", {"keyword": "default-dates"})

        claimed = api_client.get("/api/sources/xhs/next-task")

        assert claimed.status_code == 200
        assert claimed.json()["need_published_at"] is False

    def test_next_task_requests_published_at_when_date_preference_active(
        self,
        api_client: TestClient,
    ) -> None:
        ctx = api_client.app.state.runtime_context
        ctx.config.sources.xiaohongshu.recommendation_date_preset = "last_7_days"
        ctx.config.sources.xiaohongshu.recommendation_date_weight = 1.0
        queue = XhsTaskQueue(ctx.database)
        assert queue.enqueue("search", {"keyword": "recent-dates"})

        claimed = api_client.get("/api/sources/xhs/next-task")

        assert claimed.status_code == 200
        assert claimed.json()["need_published_at"] is True

    def test_search_empty_result_records_explicit_error_and_safe_debug(
        self,
        api_client: TestClient,
    ) -> None:
        db = api_client.app.state.runtime_context.database
        queue = XhsTaskQueue(db)
        assert queue.enqueue("search", {"keyword": "selector-regression"})
        claimed = api_client.get("/api/sources/xhs/next-task")
        assert claimed.status_code == 200

        debug = {
            "xhs_search_empty": {
                "reason": "no_note_anchor_after_wait",
                "pathname": "/search_result",
                "anchor_counts": {"note": 0, "search_result": 0},
            }
        }
        response = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": claimed.json()["id"],
                "status": "empty",
                "urls": [],
                "notes": [],
                "debug": debug,
            },
        )

        assert response.status_code == 200
        stored = queue.get(claimed.json()["id"])
        assert stored is not None
        assert stored["status"] == "failed"
        result = json.loads(str(stored["result_json"]))
        assert result["error"] == "xhs_empty_result"
        assert result["debug"] == debug

    def test_visible_login_gate_overrides_stale_cookie_login_state(
        self,
        api_client: TestClient,
    ) -> None:
        db = api_client.app.state.runtime_context.database
        queue = XhsTaskQueue(db)
        db.set_xhs_login_state(True)
        assert queue.enqueue("search", {"keyword": "login-check"})
        claimed = api_client.get("/api/sources/xhs/next-task")
        assert claimed.status_code == 200

        response = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": claimed.json()["id"],
                "status": "error",
                "urls": [],
                "notes": [],
                "error": "xhs_login_required",
                "debug": {
                    "xhs_auth": {
                        "reason": "visible_login_overlay",
                        "pathname": "/search_result",
                    }
                },
            },
        )

        assert response.status_code == 200
        assert db.get_xhs_login_state()[0] is False
        stored = queue.get(claimed.json()["id"])
        assert stored is not None
        assert stored["status"] == "failed"

    def test_rate_limited_result_trips_persistent_cooldown(
        self,
        api_client: TestClient,
    ) -> None:
        db = api_client.app.state.runtime_context.database
        queue = XhsTaskQueue(db)
        db.insert_pending_keywords("xiaohongshu", ["first"], "digest")
        keyword = db.claim_keywords("xiaohongshu", 1)[0]
        db.mark_keyword_executing(int(keyword["id"]))
        assert queue.enqueue(
            "search",
            {
                "keyword": "first",
                "source_keyword_id": int(keyword["id"]),
            },
        )
        assert queue.enqueue("search", {"keyword": "second"})
        claimed = api_client.get("/api/sources/xhs/next-task")
        assert claimed.status_code == 200

        limited = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": claimed.json()["id"],
                "status": "rate_limited",
                "urls": [],
                "notes": [],
                "error": "xhs_rate_limited",
                "debug": {
                    "xhs_risk_control": {
                        "reason": "security_verification",
                    }
                },
            },
        )
        assert limited.status_code == 200
        assert limited.json() == {"ok": True}

        blocked = api_client.get("/api/sources/xhs/next-task")
        assert blocked.status_code == 204
        assert int(blocked.headers["retry-after"]) > 0
        assert queue.runtime_state()["rate_limited"] is True
        keyword_after = db.conn.execute(
            "SELECT status, attempts FROM discovery_keywords WHERE id = ?",
            (int(keyword["id"]),),
        ).fetchone()
        assert keyword_after is not None
        assert keyword_after["status"] == "pending"
        assert keyword_after["attempts"] == 0

        status = api_client.get("/api/sources/status")
        assert status.status_code == 200
        xhs_status = status.json()["xiaohongshu"]
        assert xhs_status["state"] == "rate_limited"
        assert xhs_status["feed_paused"] is True
        assert "连续第 1 次" in xhs_status["detail"]
        assert "后台任务已自动暂停" in xhs_status["detail"]

    def test_task_result_completes_task(self, api_client: TestClient) -> None:
        # Enqueue via internal queue (simulating scheduler)
        api_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/abc"],
                "page_type": "search",
            },
        )

        # We can't easily enqueue via API (no public enqueue endpoint yet),
        # but we can test task-result handles missing task gracefully
        resp = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": "nonexistent",
                "status": "ok",
                "urls": ["https://www.xiaohongshu.com/explore/x"],
            },
        )
        assert resp.status_code == 409

    def test_creator_crud(self, api_client: TestClient) -> None:
        # Add
        resp = api_client.post(
            "/api/sources/xhs/creators",
            json={
                "creator_id": "uid123",
                "creator_url": "https://www.xiaohongshu.com/user/profile/uid123",
                "display_name": "键圈老用户",
            },
        )
        assert resp.status_code == 201

        # List
        resp = api_client.get("/api/sources/xhs/creators")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["creator_id"] == "uid123"

        # Delete
        sub_id = items[0]["id"]
        resp = api_client.delete(f"/api/sources/xhs/creators/{sub_id}")
        assert resp.status_code == 200

        # Verify deleted
        resp = api_client.get("/api/sources/xhs/creators")
        assert len(resp.json()["items"]) == 0

    def test_creator_add_requires_fields(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/sources/xhs/creators",
            json={"display_name": "x"},
        )
        assert resp.status_code == 422


def test_next_pending_only_ids_restricts_claim(queue: XhsTaskQueue) -> None:
    """gui-init: during init, next-task is restricted to init-owned ids so a
    stale pending task can't be claimed and starve the run."""
    stale_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": []}, daily_budget=0)
    owned_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": []}, daily_budget=0)
    assert stale_id and owned_id

    # Restricted to the owned id → returns the owned task even though the stale
    # one is older (would otherwise be claimed first).
    task = queue.next_pending(only_ids={owned_id})
    assert task is not None and str(task["id"]) == owned_id

    # Empty restriction → nothing claimable (init owns no task for this source).
    assert queue.next_pending(only_ids=set()) is None

    # No restriction (None) → normal behavior: the remaining pending task.
    remaining = queue.next_pending()
    assert remaining is not None and str(remaining["id"]) == stale_id
