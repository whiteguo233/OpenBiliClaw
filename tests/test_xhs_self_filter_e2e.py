"""End-to-end verification for the XHS self-content filter.

Simulates the full lifecycle:
  1. XHS notes (including self-authored) enter the pool via API
  2. Extension sends self_info via observed-urls
  3. Already-pooled self-authored rows are immediately suppressed
  4. get_pool_candidates / count / readiness all exclude self-authored rows
  5. Backlog queries (evaluation, copy, delight) also skip self-authored rows
  6. Bilibili rows with the same author name are NOT affected
  7. Empty nickname is a safe no-op
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.storage.database import Database


# ---------------------------------------------------------------------------
# Fixtures (reuse the RecordingMemoryManager pattern from test_api_xhs_ingest)
# ---------------------------------------------------------------------------


class _RecordingMemoryManager:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.profile_signals: list[object] = []
        self._discovery_runtime_state: dict[str, object] = {}
        self._source_bootstrap_state: dict[str, object] = {}

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self._discovery_runtime_state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self._discovery_runtime_state = dict(state)

    def load_source_bootstrap_state(self) -> dict[str, object]:
        return dict(self._source_bootstrap_state)

    def save_source_bootstrap_state(self, state: dict[str, object]) -> None:
        self._source_bootstrap_state = dict(state)


class _RecordingProfilePipeline:
    def __init__(self, memory: _RecordingMemoryManager) -> None:
        self._memory = memory

    async def ingest_batch(self, signals: list[object]) -> object:
        self._memory.profile_signals.extend(signals)
        return SimpleNamespace(layers_updated=[])


class _RecordingSoulEngine:
    def __init__(self, memory: _RecordingMemoryManager) -> None:
        self.pipeline = _RecordingProfilePipeline(memory)

    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def e2e_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, _RecordingMemoryManager]:
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "e2e.db")
    db.initialize()
    memory = _RecordingMemoryManager()

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
        soul_engine=_RecordingSoulEngine(memory),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    return TestClient(app), db, memory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_XHS_TOKEN_URL = "https://www.xiaohongshu.com/explore/{note_id}?xsec_token=TOKEN="


def _seed_xhs_row(
    db: Database,
    bvid: str,
    *,
    up_name: str = "",
    author_name: str = "",
    title: str = "note",
    classified: bool = True,
) -> None:
    """Insert an XHS row into content_cache, optionally fully classified."""
    kwargs: dict[str, object] = {
        "title": title,
        "up_name": up_name,
        "author_name": author_name,
        "source": "xhs-extension-task",
        "content_id": bvid,
        "content_url": _XHS_TOKEN_URL.format(note_id=bvid),
        "source_platform": "xiaohongshu",
    }
    if classified:
        kwargs.update(
            pool_expression="推荐文案",
            pool_topic_label="主题",
            style_key="tutorial",
            topic_group="测试分组",
            relevance_score=0.8,
        )
    db.cache_content(bvid, **kwargs)


def _seed_bili_row(db: Database, bvid: str, *, up_name: str = "") -> None:
    """Insert a fully-classified Bilibili row."""
    db.cache_content(
        bvid,
        title="bilibili video",
        up_name=up_name,
        source="search",
        source_platform="bilibili",
        pool_expression="推荐文案",
        pool_topic_label="主题",
        style_key="tutorial",
        topic_group="测试分组",
        relevance_score=0.8,
    )


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


class TestXhsSelfContentFilterE2E:
    """Full lifecycle: ingest → self_info arrival → purge → serve guard."""

    def test_full_lifecycle(
        self,
        e2e_env: tuple[TestClient, Database, _RecordingMemoryManager],
    ) -> None:
        client, db, memory = e2e_env

        # ── Phase 1: Pre-seed pool rows BEFORE self_info exists ──────────
        # Self-authored XHS rows (will be caught by different columns)
        _seed_xhs_row(db, "self_by_up", up_name="屎屎", title="自发-up_name")
        _seed_xhs_row(db, "self_by_author", author_name="屎屎", title="自发-author_name")
        _seed_xhs_row(
            db, "self_by_both", up_name="屎屎", author_name="屎屎", title="自发-两列"
        )
        # Other people's XHS rows
        _seed_xhs_row(db, "friend_xhs", up_name="好朋友", title="好朋友的笔记")
        # Bilibili row whose up_name happens to match — must NOT be excluded
        _seed_bili_row(db, "BV_bili_same", up_name="屎屎")

        # Sanity: before self_info, all 5 rows are pool-visible
        pre_count = db.count_pool_candidates(max_per_topic_group=0)
        assert pre_count == 5, f"expected 5 before self_info, got {pre_count}"

        # ── Phase 2: Extension sends self_info via observed-urls ─────────
        response = client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": [
                    "https://www.xiaohongshu.com/explore/harmless?xsec_token=OK"
                ],
                "page_type": "explore",
                "self_info": {"user_id": "uid_self", "nickname": "屎屎"},
            },
        )
        assert response.status_code == 200

        # ── Phase 3: Verify immediate purge ──────────────────────────────
        suppressed_rows = db.conn.execute(
            "SELECT bvid FROM content_cache "
            "WHERE pool_status = 'suppressed' AND source_platform = 'xiaohongshu'"
        ).fetchall()
        suppressed_bvids = {row["bvid"] for row in suppressed_rows}

        assert "self_by_up" in suppressed_bvids, "up_name match should be suppressed"
        assert "self_by_author" in suppressed_bvids, "author_name match should be suppressed"
        assert "self_by_both" in suppressed_bvids, "both-column match should be suppressed"
        assert "friend_xhs" not in suppressed_bvids, "friend's note must not be suppressed"

        # Bilibili row must stay fresh
        bili_status = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'BV_bili_same'"
        ).fetchone()
        assert bili_status["pool_status"] == "fresh", "Bilibili row must stay fresh"

        # ── Phase 4: Verify self_info persisted in runtime state ─────────
        state = memory.load_discovery_runtime_state()
        assert state.get("xhs_self_info") == {
            "user_id": "uid_self",
            "nickname": "屎屎",
        }

        # ── Phase 5: Verify serve-time guard (get_pool_candidates) ───────
        # Add more self-authored rows AFTER self_info is known — these
        # won't be purged (purge is one-shot), but the SQL guard must block.
        _seed_xhs_row(db, "self_late_arrival", up_name="屎屎", title="后来的自发")

        candidates = db.get_pool_candidates(
            limit=50, xhs_self_nickname="屎屎"
        )
        candidate_bvids = {r["bvid"] for r in candidates}

        assert "self_late_arrival" not in candidate_bvids, (
            "Late self-authored row must be excluded by SQL guard"
        )
        assert "friend_xhs" in candidate_bvids, "Friend's note must appear"
        assert "BV_bili_same" in candidate_bvids, "Bilibili row must appear"
        # Suppressed rows are already pool_status='suppressed', not 'fresh'
        assert "self_by_up" not in candidate_bvids
        assert "self_by_author" not in candidate_bvids

        # ── Phase 6: Verify count and readiness consistency ──────────────
        count = db.count_pool_candidates(
            max_per_topic_group=0, xhs_self_nickname="屎屎"
        )
        readiness = db.count_pool_readiness(xhs_self_nickname="屎屎")

        # Should only count: friend_xhs + BV_bili_same + harmless (from API)
        # harmless from the observed-urls call is bare (no classification),
        # so it won't pass the precompute gate. That leaves 2.
        assert count == 2, f"expected 2 servable, got {count}"
        assert readiness["available"] == count, (
            f"readiness.available ({readiness['available']}) != count ({count})"
        )

        # ── Phase 7: Verify backlog queries skip self-authored rows ──────
        # Seed an unclassified self-authored row
        _seed_xhs_row(
            db, "self_unclassified", author_name="屎屎", classified=False
        )
        eval_rows = db.get_pool_candidates_needing_evaluation(
            limit=50, xhs_self_nickname="屎屎"
        )
        eval_bvids = {r["bvid"] for r in eval_rows}
        assert "self_unclassified" not in eval_bvids, (
            "Unclassified self-authored row must not enter evaluation queue"
        )

        # ── Phase 8: Empty nickname is a safe no-op ──────────────────────
        all_count = db.count_pool_candidates(
            max_per_topic_group=0, xhs_self_nickname=""
        )
        # With empty nickname, the late-arrival self row is also counted
        # (suppressed ones still excluded by pool_status, but late_arrival
        # and self_unclassified have pool_status='fresh').
        # self_late_arrival is classified → visible. self_unclassified is not.
        assert all_count > count, (
            f"Empty nickname should include more rows: {all_count} vs {count}"
        )

    def test_case_insensitive_matching(
        self,
        e2e_env: tuple[TestClient, Database, _RecordingMemoryManager],
    ) -> None:
        """Nickname matching must be case-insensitive."""
        _, db, _ = e2e_env

        _seed_xhs_row(db, "xhs_mixed_case", up_name="TestUser", title="mixed case")

        candidates = db.get_pool_candidates(
            limit=50, xhs_self_nickname="testuser"
        )
        assert not any(r["bvid"] == "xhs_mixed_case" for r in candidates), (
            "Case-insensitive match should exclude 'TestUser' when nickname is 'testuser'"
        )

    def test_idempotent_self_info_no_double_purge(
        self,
        e2e_env: tuple[TestClient, Database, _RecordingMemoryManager],
    ) -> None:
        """Sending the same self_info twice must not error or re-suppress."""
        client, db, _ = e2e_env

        _seed_xhs_row(db, "xhs_idem", up_name="Repeat", title="idempotent")

        payload = {
            "urls": ["https://www.xiaohongshu.com/explore/x?xsec_token=Y"],
            "page_type": "explore",
            "self_info": {"user_id": "u1", "nickname": "Repeat"},
        }

        r1 = client.post("/api/sources/xhs/observed-urls", json=payload)
        assert r1.status_code == 200

        # Second call with same self_info — should be idempotent
        r2 = client.post("/api/sources/xhs/observed-urls", json=payload)
        assert r2.status_code == 200

        row = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'xhs_idem'"
        ).fetchone()
        assert row["pool_status"] == "suppressed"

    def test_nickname_change_triggers_new_purge(
        self,
        e2e_env: tuple[TestClient, Database, _RecordingMemoryManager],
    ) -> None:
        """When the user changes their XHS nickname, the new nickname
        triggers a fresh purge pass."""
        client, db, _ = e2e_env

        # Row under old nickname — will be caught by first self_info
        _seed_xhs_row(db, "xhs_old_nick", up_name="OldNick")
        # Row under new nickname — should be caught by second self_info
        _seed_xhs_row(db, "xhs_new_nick", up_name="NewNick")

        # First self_info
        client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/a?xsec_token=T"],
                "page_type": "explore",
                "self_info": {"user_id": "u1", "nickname": "OldNick"},
            },
        )

        old_row = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'xhs_old_nick'"
        ).fetchone()
        assert old_row["pool_status"] == "suppressed"

        new_row = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'xhs_new_nick'"
        ).fetchone()
        assert new_row["pool_status"] == "fresh", "NewNick not yet known"

        # Second self_info with changed nickname
        client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/b?xsec_token=T"],
                "page_type": "explore",
                "self_info": {"user_id": "u1", "nickname": "NewNick"},
            },
        )

        new_row2 = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'xhs_new_nick'"
        ).fetchone()
        assert new_row2["pool_status"] == "suppressed", (
            "Nickname change must trigger purge for new nickname"
        )
