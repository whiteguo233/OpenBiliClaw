"""Tests for the xhs observed-URL ingestion endpoint.

POST /api/sources/xhs/observed-urls accepts a batch of xhs note URLs
that the extension passively collected and schedules enrichment via the
XiaohongshuAdapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.storage.database import Database


class RecordingMemoryManager:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.profile_signals: list[object] = []
        self._discovery_runtime_state: dict[str, object] = {}
        self._source_bootstrap_state: dict[str, object] = {}

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    # v0.3.57+: in-memory shim for the runtime-state API used by
    # _persist_xhs_self_info / _load_xhs_self_info. Tests don't need
    # disk persistence, just round-trip equivalence inside one request
    # cycle.
    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self._discovery_runtime_state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self._discovery_runtime_state = dict(state)

    def load_source_bootstrap_state(self) -> dict[str, object]:
        return dict(self._source_bootstrap_state)

    def save_source_bootstrap_state(self, state: dict[str, object]) -> None:
        self._source_bootstrap_state = dict(state)


class RecordingProfilePipeline:
    def __init__(self, memory: RecordingMemoryManager) -> None:
        self._memory = memory

    async def ingest_batch(self, signals: list[object]) -> object:
        from types import SimpleNamespace

        self._memory.profile_signals.extend(signals)
        return SimpleNamespace(layers_updated=[])


class RecordingSoulEngine:
    def __init__(self, memory: RecordingMemoryManager) -> None:
        self.pipeline = RecordingProfilePipeline(memory)

    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a minimal TestClient with a real database but mocked adapter."""
    from types import SimpleNamespace

    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "test.db")
    db.initialize()

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


@pytest.fixture
def xhs_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, RecordingMemoryManager]:
    """Build an API client with an injectable memory manager for task tests."""
    from types import SimpleNamespace

    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "task.db")
    db.initialize()
    memory = RecordingMemoryManager()

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    from openbiliclaw.api.app import create_app

    app = create_app(
        database=db,
        memory_manager=memory,
        soul_engine=RecordingSoulEngine(memory),
        # v0.3.57+: _persist_xhs_self_info / _load_xhs_self_info read
        # ``ctx.runtime_controller.memory_manager``. Wire the recording
        # manager in so tests can round-trip persisted self_info.
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    return TestClient(app), db, memory


class TestXhsObservedUrls:
    def test_ingest_valid_urls(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [
                    "https://www.xiaohongshu.com/explore/abc123?xsec_token=ZZZ",
                    "https://www.xiaohongshu.com/explore/def456?xsec_token=YYY",
                ],
                "page_type": "search",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["accepted"] == 2

    def test_rejects_empty_url_list(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={"urls": [], "page_type": "search"},
        )
        assert response.status_code == 422

    def test_rejects_too_many_urls(self, app_client: TestClient) -> None:
        urls = [f"https://www.xiaohongshu.com/explore/{i:024x}" for i in range(60)]
        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={"urls": urls, "page_type": "search"},
        )
        assert response.status_code == 422

    def test_filters_invalid_url_shapes(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [
                    "https://www.xiaohongshu.com/explore/abc123",
                    "https://example.com/bad",
                    "not-even-a-url",
                ],
                "page_type": "explore",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 1  # only the valid xhs URL

    def test_stores_observations_in_db(self, app_client: TestClient, tmp_path: Path) -> None:
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "test.db")
        db.initialize()

        app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/abc123"],
                "page_type": "search",
            },
        )

        rows = db.conn.execute("SELECT * FROM xhs_observed_urls").fetchall()
        assert len(rows) >= 1
        assert rows[0]["url"] == "https://www.xiaohongshu.com/explore/abc123"
        assert rows[0]["page_type"] == "search"

    def test_notes_cache_populates_source_and_platform(
        self, app_client: TestClient, tmp_path: Path
    ) -> None:
        """Regression: xhs note caches must tag `source` (strategy label)
        AND `source_platform='xiaohongshu'`.

        An earlier bug passed the wrong kwarg (`source_strategy=` instead of
        `source=`) to `cache_content`, silently dropping the label. Paired
        with the engine re-cache path that omitted `source_platform`, this
        let xhs rows end up with `source=''` and `source_platform='bilibili'`
        after the first rescore pass.
        """
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "test.db")
        db.initialize()

        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/note-xyz-001"],
                "notes": [
                    {
                        "url": "https://www.xiaohongshu.com/explore/note-xyz-001",
                        "title": "手冲咖啡入门",
                        "author": "豆子老师",
                        "cover_url": "https://example.com/cover.jpg",
                    }
                ],
                "page_type": "search",
            },
        )
        assert response.status_code == 200

        row = db.conn.execute(
            "SELECT source, source_platform, content_id, content_url, title, up_name "
            "FROM content_cache WHERE bvid=?",
            ("note-xyz-001",),
        ).fetchone()
        assert row is not None, "xhs note was not cached"
        assert row["source"] == "xhs-extension-search"
        assert row["source_platform"] == "xiaohongshu"
        assert row["content_id"] == "note-xyz-001"
        assert row["content_url"].endswith("/note-xyz-001")
        assert row["title"] == "手冲咖啡入门"
        assert row["up_name"] == "豆子老师"

    def test_tokenized_url_upgrades_existing_bare_cache_row(
        self, app_client: TestClient, tmp_path: Path
    ) -> None:
        """Regression: when the extension reports a note URL *with*
        ``xsec_token`` after the same note was already cached from a
        search page *without* the token, the cache row must be upgraded.

        Without this backfill, users click recommendation cards and get
        dead-ended at xhs's login wall because the stored URL is missing
        the access token xhs requires for outbound sharing.
        """
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "test.db")
        db.initialize()

        note_id = "note-upgrade-001"
        bare_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        tokenized = f"{bare_url}?xsec_token=ABCXYZ123=&xsec_source=pc_feed"

        # First pass: search page sees the bare URL only
        resp1 = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [bare_url],
                "notes": [{"url": bare_url, "title": "t", "author": "a", "cover_url": ""}],
                "page_type": "search",
            },
        )
        assert resp1.status_code == 200

        row = db.conn.execute(
            "SELECT content_url FROM content_cache WHERE bvid=?", (note_id,)
        ).fetchone()
        assert row["content_url"] == bare_url  # bare URL cached

        # Second pass: explore feed observes the same note WITH a token.
        # No new `notes` payload — just the bare URL list carrying the token.
        resp2 = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={"urls": [tokenized], "page_type": "explore"},
        )
        assert resp2.status_code == 200

        row = db.conn.execute(
            "SELECT content_url FROM content_cache WHERE bvid=?", (note_id,)
        ).fetchone()
        assert "xsec_token=ABCXYZ123" in row["content_url"], (
            f"token backfill failed: still cached as {row['content_url']!r}"
        )

    def test_cache_prefers_tokenized_url_from_prior_observation(
        self, app_client: TestClient, tmp_path: Path
    ) -> None:
        """Regression: if we previously observed a tokenized URL for a
        note (e.g. from the explore feed), and a later `notes` payload
        arrives with a bare URL for the same note (from search results),
        the cache must keep/write the tokenized URL rather than overwriting
        with the bare one.
        """
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "test.db")
        db.initialize()

        note_id = "note-prefer-002"
        bare_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        tokenized = f"{bare_url}?xsec_token=PRIOR456=&xsec_source="

        # Prime: explore feed observed a tokenized URL first (no notes yet).
        app_client.post(
            "/api/sources/xhs/observed-urls",
            json={"urls": [tokenized], "page_type": "explore"},
        )

        # Now search page reports `notes` with a bare URL for the same note.
        resp = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [bare_url],
                "notes": [{"url": bare_url, "title": "t2", "author": "a2", "cover_url": ""}],
                "page_type": "search",
            },
        )
        assert resp.status_code == 200

        row = db.conn.execute(
            "SELECT content_url FROM content_cache WHERE bvid=?", (note_id,)
        ).fetchone()
        assert "xsec_token=PRIOR456" in row["content_url"], (
            f"cache overwrote tokenized URL with bare: {row['content_url']!r}"
        )

    def test_observed_urls_top_level_self_info_filters_self_authored_notes(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """v0.3.57+: passive XHS pages send self_info at the payload top level
        (extension v0.3.10's xiaohongshu.ts:runPassiveCollection); backend
        must read it and drop notes authored by the logged-in user before
        they enter content_cache.

        Reproduces the leak where the user's own posts ("自家宝安领航城...
        165㎡大五房出售") landed in the XHS recommendation pool because the
        passive collector didn't carry self_info — only bootstrap_profile
        did, and the search task pre-populated the pool first."""
        app_client, db, _ = xhs_task_client

        own_url = "https://www.xiaohongshu.com/explore/passive-own-001?xsec_token=A"
        other_url = "https://www.xiaohongshu.com/explore/passive-other-001?xsec_token=B"
        response = app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "self_info": {"user_id": "self-uid-123", "nickname": "屎屎"},
                "notes": [
                    {
                        "url": own_url,
                        "title": "自家宝安领航城165㎡",
                        "author": "屎屎",
                        "cover_url": "",
                    },
                    {
                        "url": other_url,
                        "title": "别人发的笔记",
                        "author": "Jupiter",
                        "cover_url": "",
                    },
                ],
                "page_type": "search",
            },
        )
        assert response.status_code == 200

        bvids = {
            row["bvid"]
            for row in db.conn.execute(
                "SELECT bvid FROM content_cache WHERE source_platform='xiaohongshu'"
            ).fetchall()
        }
        assert "passive-other-001" in bvids
        assert "passive-own-001" not in bvids

    def test_observed_urls_persists_self_info_for_subsequent_requests(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """v0.3.57+: once self_info arrives on any request, subsequent
        observed-urls posts (without their own self_info) must still
        filter self-authored notes via the persisted state."""
        app_client, db, _ = xhs_task_client

        # 1st request: bring self_info
        app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "self_info": {"user_id": "uid-9", "nickname": "屎屎"},
                "notes": [
                    {
                        "url": "https://www.xiaohongshu.com/explore/n0?xsec_token=Z",
                        "title": "占位",
                        "author": "Jupiter",
                        "cover_url": "",
                    }
                ],
                "page_type": "explore",
            },
        )

        # 2nd request: NO self_info, but a self-authored note
        own_url = "https://www.xiaohongshu.com/explore/n1?xsec_token=Y"
        app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "notes": [
                    {
                        "url": own_url,
                        "title": "屎屎又发一条",
                        "author": "屎屎",
                        "cover_url": "",
                    }
                ],
                "page_type": "explore",
            },
        )

        bvids = {
            row["bvid"]
            for row in db.conn.execute(
                "SELECT bvid FROM content_cache WHERE source_platform='xiaohongshu'"
            ).fetchall()
        }
        assert "n0" in bvids
        assert "n1" not in bvids

    def test_startup_purge_suppresses_existing_self_authored_pool_items(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """v0.3.57+: when self_info is already persisted (from a prior
        session), backend startup must scan content_cache for rows whose
        author matches and flip them to ``pool_status='suppressed'``,
        repairing any pool that was polluted before the filter went live.
        """
        from types import SimpleNamespace

        from openbiliclaw.api.app import create_app
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "purge.db")
        db.initialize()

        # Pre-seed the pool with one self-authored row + one foreign row.
        db.cache_content(
            "own-existing",
            title="自家165㎡大五房",
            up_name="屎屎",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            content_url="https://www.xiaohongshu.com/explore/own-existing?xsec_token=A",
        )
        db.cache_content(
            "stranger-existing",
            title="别人的笔记",
            up_name="Jupiter",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            content_url="https://www.xiaohongshu.com/explore/stranger-existing?xsec_token=B",
        )

        # Pre-seed the runtime state: self_info present from prior session.
        memory = RecordingMemoryManager()
        memory.save_discovery_runtime_state(
            {"xhs_self_info": {"user_id": "u1", "nickname": "屎屎"}}
        )

        fake_config = SimpleNamespace(
            data_path=tmp_path,
            bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
            sources=SimpleNamespace(
                browser_cdp_url="",
                browser_headed=False,
                xiaohongshu=SimpleNamespace(
                    daily_search_budget=20,
                    daily_creator_budget=10,
                    task_interval_seconds=45,
                ),
            ),
            scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
        )
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

        # create_app should fire the purge hook before returning.
        create_app(
            database=db,
            memory_manager=memory,
            soul_engine=SimpleNamespace(),
            runtime_controller=SimpleNamespace(memory_manager=memory),
            recommendation_engine=None,
        )

        rows = {
            r["bvid"]: r["pool_status"]
            for r in db.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()
        }
        assert rows["own-existing"] == "suppressed"
        assert rows["stranger-existing"] == "fresh"

    def test_task_result_top_level_self_info_takes_precedence_over_debug(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """v0.3.57+: extension v0.3.10 may send self_info at the top of
        the task-result payload too. Top-level wins over the older
        debug.xhs_bootstrap.steps[*].self_info nested location."""
        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, _ = xhs_task_client
        queue = XhsTaskQueue(db)
        assert queue.enqueue("search", {"keyword": "猫"})
        task = queue.next_pending()
        assert task is not None

        own_url = "https://www.xiaohongshu.com/explore/search-own-001?xsec_token=A"
        other_url = "https://www.xiaohongshu.com/explore/search-other-001?xsec_token=B"
        response = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "ok",
                "self_info": {"user_id": "u-top", "nickname": "屎屎"},
                "urls": [own_url, other_url],
                "notes": [
                    {
                        "scope": "saved",
                        "title": "自家",
                        "url": own_url,
                        "note_id": "search-own-001",
                        "author": "屎屎",
                    },
                    {
                        "scope": "saved",
                        "title": "别人",
                        "url": other_url,
                        "note_id": "search-other-001",
                        "author": "陌生人",
                    },
                ],
            },
        )
        assert response.status_code == 200
        bvids = {
            row["bvid"]
            for row in db.conn.execute(
                "SELECT bvid FROM content_cache WHERE source_platform='xiaohongshu'"
            ).fetchall()
        }
        assert "search-other-001" in bvids
        assert "search-own-001" not in bvids


class TestXhsTaskResults:
    def test_xhs_bootstrap_partial_results_accumulate_until_final(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        import json

        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, memory = xhs_task_client
        queue = XhsTaskQueue(db)
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked", "xhs_history"]},
        )
        task = queue.next_pending()
        assert task is not None

        saved_url = "https://www.xiaohongshu.com/explore/saved-partial"
        liked_url = "https://www.xiaohongshu.com/explore/liked-final"

        partial = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "partial",
                "urls": [saved_url],
                "notes": [
                    {
                        "scope": "saved",
                        "title": "partial saved",
                        "url": saved_url,
                        "note_id": "saved-partial",
                    }
                ],
                "scope_counts": {"saved": 1, "liked": 0, "xhs_history": 0},
                "debug": {"xhs_bootstrap": {"steps": [{"partial": 1}]}},
            },
        )
        assert partial.status_code == 200
        assert partial.json() == {"ok": True}

        row = db.conn.execute(
            "SELECT status, result_json, completed_at FROM xhs_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        assert row["status"] == "in_progress"
        assert row["completed_at"] is None
        partial_result = json.loads(row["result_json"])
        assert partial_result["scope_counts"] == {
            "saved": 1,
            "liked": 0,
            "xhs_history": 0,
        }
        assert [note["note_id"] for note in partial_result["notes"]] == ["saved-partial"]
        assert len(memory.events) == 1
        assert memory.events[0]["event_type"] == "favorite"

        final = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "ok",
                "urls": [saved_url, liked_url],
                "notes": [
                    {
                        "scope": "saved",
                        "title": "partial saved",
                        "url": saved_url,
                        "note_id": "saved-partial",
                    },
                    {
                        "scope": "liked",
                        "title": "final liked",
                        "url": liked_url,
                        "note_id": "liked-final",
                    },
                ],
                "scope_counts": {"saved": 1, "liked": 1, "xhs_history": 0},
                "debug": {"xhs_bootstrap": {"steps": [{"final": 1}]}},
            },
        )

        assert final.status_code == 200
        assert len(memory.events) == 2
        assert memory.events[1]["event_type"] == "like"
        row = db.conn.execute(
            "SELECT status, result_json, completed_at FROM xhs_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        result = json.loads(row["result_json"])
        assert result["urls"] == [saved_url, liked_url]
        assert [note["note_id"] for note in result["notes"]] == [
            "saved-partial",
            "liked-final",
        ]
        assert result["scope_counts"] == {"saved": 1, "liked": 1, "xhs_history": 0}

    def test_xhs_bootstrap_empty_result_preserves_scope_counts(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        import json

        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, memory = xhs_task_client
        queue = XhsTaskQueue(db)
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked", "xhs_history"]},
        )
        task = queue.next_pending()
        assert task is not None

        response = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "empty",
                "urls": [],
                "notes": [],
                "scope_counts": {"saved": 0, "liked": 0, "xhs_history": 0},
                "debug": {
                    "xhs_bootstrap": {
                        "steps": [
                            {
                                "page_url": "https://www.xiaohongshu.com/explore",
                                "has_initial_state": True,
                                "profile_url_found": False,
                            }
                        ]
                    }
                },
            },
        )

        assert response.status_code == 200
        assert memory.events == []
        row = db.conn.execute(
            "SELECT status, result_json FROM xhs_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        assert row["status"] == "completed"
        assert json.loads(row["result_json"]) == {
            "urls": [],
            "scope_counts": {"saved": 0, "liked": 0, "xhs_history": 0},
            "debug": {
                "xhs_bootstrap": {
                    "steps": [
                        {
                            "page_url": "https://www.xiaohongshu.com/explore",
                            "has_initial_state": True,
                            "profile_url_found": False,
                        }
                    ]
                }
            },
        }

    def test_xhs_bootstrap_task_result_records_events(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        import json

        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, memory = xhs_task_client
        queue = XhsTaskQueue(db)
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked", "xhs_history"]},
        )
        task = queue.next_pending()
        assert task is not None

        note_url = "https://www.xiaohongshu.com/explore/note-task-001"
        response = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "ok",
                "urls": [note_url],
                "notes": [
                    {
                        "scope": "saved",
                        "title": "手冲咖啡入门",
                        "url": note_url,
                        "note_id": "note-task-001",
                        "xsec_token": "token-task-001",
                        "author": "豆子老师",
                        "cover_url": "https://example.com/cover.jpg",
                    }
                ],
            },
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert len(memory.events) == 1
        assert memory.events[0]["event_type"] == "favorite"
        metadata = memory.events[0]["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["source_platform"] == "xiaohongshu"
        assert metadata["import_source"] == "xhs_bootstrap_saved"
        assert len(memory.profile_signals) == 1
        signal = memory.profile_signals[0]
        assert signal.payload["event_type"] == "favorite"
        assert signal.payload["metadata"]["source_platform"] == "xiaohongshu"

        row = db.conn.execute(
            "SELECT status, result_json FROM xhs_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        assert row["status"] == "completed"
        result = json.loads(row["result_json"])
        assert result["notes"][0]["note_id"] == "note-task-001"

        cache_row = db.conn.execute(
            "SELECT title, source, source_platform FROM content_cache WHERE bvid=?",
            ("note-task-001",),
        ).fetchone()
        assert cache_row is not None
        assert cache_row["title"] == "手冲咖啡入门"
        assert cache_row["source"] == "xhs-extension-task"
        assert cache_row["source_platform"] == "xiaohongshu"

    def test_xhs_bootstrap_skips_notes_already_seen_in_previous_task(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """A second bootstrap task returning the same note must not replay
        old profile signals into memory / incremental profile updates."""
        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, memory = xhs_task_client
        queue = XhsTaskQueue(db)

        for _ in range(2):
            assert queue.enqueue(
                "bootstrap_profile",
                {"scopes": ["saved", "liked", "xhs_history"]},
            )
            task = queue.next_pending()
            assert task is not None
            response = app_client.post(
                "/api/sources/xhs/task-result",
                json={
                    "task_id": task["id"],
                    "status": "ok",
                    "urls": ["https://www.xiaohongshu.com/explore/repeated-note"],
                    "notes": [
                        {
                            "scope": "saved",
                            "title": "重复收藏",
                            "url": "https://www.xiaohongshu.com/explore/repeated-note",
                            "note_id": "repeated-note",
                        }
                    ],
                },
            )
            assert response.status_code == 200

        assert [event["title"] for event in memory.events] == ["重复收藏"]
        assert len(memory.profile_signals) == 1
        assert memory.load_source_bootstrap_state()["xhs_seen_note_keys"] == ["saved:repeated-note"]

    def test_xhs_self_authored_notes_are_filtered(
        self,
        xhs_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """Notes whose author matches the logged-in user must NOT enter the pool.

        Reproduces 2026-05-05 user complaint: "屎屎/三花/etc. 都是我自己发
        布的，怎么进推荐里了". XHS search / explore / saved-author paths
        all readily return self-authored content; the bootstrap task
        carries self_info in the debug payload so backend can filter.
        """
        from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

        app_client, db, memory = xhs_task_client
        queue = XhsTaskQueue(db)
        assert queue.enqueue(
            "bootstrap_profile",
            {"scopes": ["saved", "liked", "xhs_history"]},
        )
        task = queue.next_pending()
        assert task is not None

        # 1st request — bootstrap brings self_info AND a self-authored note.
        # The self-authored one should be dropped from cache + event flow.
        own_url = "https://www.xiaohongshu.com/explore/own-note-001"
        other_url = "https://www.xiaohongshu.com/explore/other-note-001"
        response = app_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": task["id"],
                "status": "ok",
                "urls": [own_url, other_url],
                "notes": [
                    {
                        "scope": "saved",
                        "title": "屎屎美貌精华版",
                        "url": own_url,
                        "note_id": "own-note-001",
                        "author": "猫主自己",
                    },
                    {
                        "scope": "saved",
                        "title": "手冲咖啡入门",
                        "url": other_url,
                        "note_id": "other-note-001",
                        "author": "豆子老师",
                    },
                ],
                "debug": {
                    "xhs_bootstrap": {
                        "steps": [
                            {
                                "self_info": {
                                    "user_id": "self-uid-123",
                                    "nickname": "猫主自己",
                                }
                            }
                        ]
                    }
                },
            },
        )
        assert response.status_code == 200

        # Self-authored note dropped from event propagation —
        # only "豆子老师" → favorite makes it through.
        assert len(memory.events) == 1
        assert memory.events[0]["title"] == "手冲咖啡入门"

        # Self-authored note dropped from content_cache too.
        own_row = db.conn.execute(
            "SELECT bvid FROM content_cache WHERE bvid=?",
            ("own-note-001",),
        ).fetchone()
        assert own_row is None
        other_row = db.conn.execute(
            "SELECT bvid FROM content_cache WHERE bvid=?",
            ("other-note-001",),
        ).fetchone()
        assert other_row is not None


class TestXhsTokens:
    """Regression tests for POST /api/sources/xhs/tokens.

    The MAIN-world sniffer discovers ``(note_id, xsec_token)`` pairs from
    xhs's own API responses and POSTs them here so cached bare URLs can
    be upgraded to tokenized URLs that xhs will accept for outbound
    sharing.
    """

    def test_backfills_token_onto_existing_bare_cache(
        self, app_client: TestClient, tmp_path: Path
    ) -> None:
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "test.db")
        db.initialize()

        note_id = "note-sniffer-001"
        bare_url = f"https://www.xiaohongshu.com/explore/{note_id}"

        # Seed a bare-URL cache row as the search-page path would.
        app_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [bare_url],
                "notes": [{"url": bare_url, "title": "t", "author": "a", "cover_url": ""}],
                "page_type": "search",
            },
        )

        resp = app_client.post(
            "/api/sources/xhs/tokens",
            json={"pairs": [{"note_id": note_id, "xsec_token": "SNIFFED_TOKEN_42"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["upgraded"] >= 1

        row = db.conn.execute(
            "SELECT content_url FROM content_cache WHERE bvid=?", (note_id,)
        ).fetchone()
        assert "xsec_token=SNIFFED_TOKEN_42" in row["content_url"], (
            f"token backfill failed: {row['content_url']!r}"
        )

    def test_empty_pairs_is_noop(self, app_client: TestClient) -> None:
        resp = app_client.post("/api/sources/xhs/tokens", json={"pairs": []})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "upgraded": 0}

    def test_skips_malformed_pairs(self, app_client: TestClient, tmp_path: Path) -> None:
        resp = app_client.post(
            "/api/sources/xhs/tokens",
            json={
                "pairs": [
                    {"note_id": "", "xsec_token": "tok"},
                    {"note_id": "note-x", "xsec_token": ""},
                    "not-a-dict",
                    {"note_id": "note-y"},
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["upgraded"] == 0
