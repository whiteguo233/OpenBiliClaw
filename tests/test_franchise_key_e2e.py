"""End-to-end tests for the v0.3.18 franchise_key fix.

These tests exist because the v0.3.18 change spans five layers (LLM
prompt → DiscoveredContent → eval cache → DB schema → API dedup) and
unit-level tests on each layer can pass while the layers don't actually
talk to each other. The cases here:

  1. ``test_migrates_existing_v0317_database_in_place`` — start with a
     SQLite file that has the v0.3.17 schema (no franchise_key column),
     re-open with the v0.3.18 ``Database`` class, confirm the column
     was added by the migration AND existing rows are preserved.

  2. ``test_cache_content_roundtrip_persists_and_protects_franchise_key``
     — write a row with franchise_key, read back, then re-write the
     same bvid with empty franchise_key and confirm the existing tag
     is preserved (the COALESCE/NULLIF rule). Without this, a re-ingest
     from raw sources would silently wipe LLM tags.

  3. ``test_evaluator_propagates_llm_franchise_key_through_to_db`` —
     simulates a full discover round with a fake LLM that returns
     franchise_key. Verifies the value lands on DiscoveredContent,
     gets cached, and survives a DB read.

  4. ``test_user_reported_scenario_5_genshin_in_popup`` — recreates
     the exact community-reported case (5 原神 / 提瓦特 rows from
     related_chain in the recommendations table) and confirms
     /api/recommendations now caps the popup at 2 同 IP rows.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent

# ---------------------------------------------------------------------------
# 1. Migration on an existing v0.3.17-shape database
# ---------------------------------------------------------------------------


def test_migrates_existing_v0317_database_in_place(tmp_path: Path) -> None:
    """A user upgrading from v0.3.17 has a content_cache without
    franchise_key. Re-opening with v0.3.18 must run the ALTER TABLE
    migration and preserve every existing row + column."""
    db_path = tmp_path / "openbiliclaw.db"

    # Recreate a v0.3.17-era schema using raw sqlite3 (no franchise_key).
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE content_cache (
            bvid TEXT PRIMARY KEY,
            title TEXT,
            up_name TEXT,
            up_mid INTEGER,
            duration INTEGER,
            tags TEXT,
            topic_key TEXT DEFAULT '',
            topic_group TEXT DEFAULT '',
            style_key TEXT DEFAULT '',
            description TEXT,
            cover_url TEXT,
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0
        );
        INSERT INTO content_cache (bvid, title, topic_key)
        VALUES ('BV_legacy', '老内容标题', 'old_topic');
        """
    )
    raw.commit()
    raw.close()

    # Confirm v0.3.17 shape: no franchise_key column.
    pre = sqlite3.connect(db_path)
    pre_cols = {r[1] for r in pre.execute("PRAGMA table_info(content_cache)")}
    assert "franchise_key" not in pre_cols
    pre.close()

    # Open with v0.3.18 — initialize() runs the migrations.
    from openbiliclaw.storage.database import Database

    db = Database(db_path)
    db.initialize()

    # Now the column exists.
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(content_cache)").fetchall()}
    assert "franchise_key" in cols, (
        f"v0.3.18 migration didn't add franchise_key column; got: {cols}"
    )

    # Legacy row is intact and franchise_key defaults to empty string.
    row = db.conn.execute(
        "SELECT bvid, title, topic_key, franchise_key FROM content_cache WHERE bvid='BV_legacy'"
    ).fetchone()
    assert row is not None
    assert row["title"] == "老内容标题"
    assert row["topic_key"] == "old_topic"
    assert row["franchise_key"] == ""


# ---------------------------------------------------------------------------
# 2. cache_content round-trip + COALESCE protection
# ---------------------------------------------------------------------------


def test_cache_content_roundtrip_persists_and_protects_franchise_key(
    tmp_path: Path,
) -> None:
    """Write franchise_key, read back. Then re-cache the same bvid
    with empty franchise_key — the original tag must survive (COALESCE
    NULLIF rule). Without that protection a re-ingest from raw
    sources would silently wipe the LLM's classification."""
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "test.db")
    db.initialize()

    # First write: full row including franchise_key.
    db.cache_content(
        bvid="BV原神示例",
        title="提瓦特摄影集锦",
        topic_key="游戏摄影",
        topic_group="游戏摄影",
        franchise_key="原神",
    )
    row = db.conn.execute(
        "SELECT franchise_key, topic_key FROM content_cache WHERE bvid='BV原神示例'"
    ).fetchone()
    assert row["franchise_key"] == "原神"
    assert row["topic_key"] == "游戏摄影"

    # Re-cache the same bvid with empty franchise_key (simulates raw
    # extension re-ingest before the LLM has re-evaluated).
    db.cache_content(
        bvid="BV原神示例",
        title="提瓦特摄影集锦",  # same content
        topic_key="",
        franchise_key="",
    )
    row2 = db.conn.execute(
        "SELECT franchise_key, topic_key FROM content_cache WHERE bvid='BV原神示例'"
    ).fetchone()
    # Both LLM-tagged columns must be preserved.
    assert row2["franchise_key"] == "原神", (
        "re-ingest blew away the LLM-tagged franchise_key — COALESCE NULLIF protection broken"
    )
    assert row2["topic_key"] == "游戏摄影"


# ---------------------------------------------------------------------------
# 3. Full discover loop: LLM emits franchise_key → lands on DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_propagates_llm_franchise_key_through_to_db(
    tmp_path: Path,
) -> None:
    """Simulates the full pipeline: ContentDiscoveryEngine.evaluate_content_batch
    receives raw DiscoveredContent items, calls a fake LLM that returns
    franchise_key, and the value must end up:
      * on each ``DiscoveredContent.franchise_key`` field
      * in the ``_eval_cache`` 5-tuple (so subsequent calls hit cache
        with the value still attached)
      * persisted to ``content_cache.franchise_key`` after cache_content
    """
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.storage.database import Database

    # Fake LLM that returns the schema with franchise_key per item.
    class _FakeLLMResponse:
        def __init__(self, payload: list[dict[str, object]]) -> None:
            self.content = json.dumps(payload, ensure_ascii=False)

    class _FakeLLMService:
        def __init__(self) -> None:
            self.calls = 0

        def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            max_tokens: int,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            # Inspect user_input to figure out the input order so the
            # response array order matches.
            input_data = json.loads(
                user_input.split("<content_batch>", 1)[1].rsplit("</content_batch>", 1)[0]
            )
            payload = []
            for item in input_data:
                title = str(item["title"])
                # Hand-tag franchises based on title — simulates what a
                # real LLM would do. Only matched IPs get a franchise_key;
                # untagged content stays empty (the right behaviour).
                if "原神" in title or "提瓦特" in title or "蒙德" in title:
                    franchise = "原神"
                elif "星穹铁道" in title or "崩铁" in title:
                    franchise = "崩坏:星穹铁道"
                else:
                    franchise = ""
                payload.append(
                    {
                        "score": 0.78,
                        "reason": "fake reason",
                        "topic_group": "游戏",
                        "style_key": "visual_showcase",
                        "franchise_key": franchise,
                    }
                )

            async def _coro() -> _FakeLLMResponse:
                return _FakeLLMResponse(payload)

            return _coro()

    fake_llm = _FakeLLMService()
    db = Database(tmp_path / "test.db")
    db.initialize()

    engine = ContentDiscoveryEngine.__new__(ContentDiscoveryEngine)
    engine._llm_service = fake_llm
    engine._concurrency = None
    engine._eval_cache = {}

    # Build a batch with three items: two 原神-related, one neutral.
    contents = [
        DiscoveredContent(bvid="BV1", title="提瓦特摄影集锦"),
        DiscoveredContent(bvid="BV2", title="蒙德角色真实化"),
        DiscoveredContent(bvid="BV3", title="如何 5 分钟做番茄炒蛋"),
    ]

    # Minimum-shape profile that the prompt builder accepts. We're not
    # exercising the prompt template here — only verifying the
    # franchise_key plumbing — so an empty SoulProfile is enough.
    from openbiliclaw.soul.profile import SoulProfile

    profile = SoulProfile()

    scores = await engine.evaluate_content_batch(contents, profile, source_context="test")

    # Every item got scored.
    assert all(s > 0 for s in scores)
    # franchise_key flowed onto each DiscoveredContent.
    assert contents[0].franchise_key == "原神"
    assert contents[1].franchise_key == "原神"
    assert contents[2].franchise_key == ""

    # Cache tuple is the new 5-tuple shape and carries franchise_key.
    cache_key = engine._batch_eval_cache_key(contents[0], profile, negative_revision=None)
    cached = engine._eval_cache[cache_key]
    assert len(cached) == 5
    assert cached[4] == "原神"

    # Persist and re-read from DB.
    for c in contents:
        db.cache_content(c.bvid, **c.to_cache_kwargs())
    rows = {
        row["bvid"]: row["franchise_key"]
        for row in db.conn.execute("SELECT bvid, franchise_key FROM content_cache").fetchall()
    }
    assert rows["BV1"] == "原神"
    assert rows["BV2"] == "原神"
    assert rows["BV3"] == ""


@pytest.mark.asyncio
async def test_evaluate_content_batch_default_size_30_uses_single_llm_call(
    tmp_path: Path,
) -> None:
    """v0.3.25+ regression: default ``batch_size`` was raised from 10 → 30
    so a typical strategy's full candidate slate (capped at
    ``_EVALUATE_BATCH_HARD_CAP=30``) goes through the LLM in a single
    call instead of three. This amortises the ~3500-token fixed prompt
    overhead (system rules + profile_summary) across more items.

    Concretely: 25 candidates evaluated in one batch should produce
    exactly 1 LLM call, not 3 (which the old 10-item batch_size would
    have caused: ceil(25/10) = 3).
    """
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine

    class _Resp:
        def __init__(self, payload: list[dict[str, object]]) -> None:
            self.content = json.dumps(payload, ensure_ascii=False)

    class _FakeLLMService:
        def __init__(self) -> None:
            self.call_count = 0

        def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            max_tokens: int,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.call_count += 1
            input_data = json.loads(
                user_input.split("<content_batch>", 1)[1].rsplit("</content_batch>", 1)[0]
            )
            payload = [
                {
                    "score": 0.5,
                    "reason": "ok",
                    "topic_group": "test",
                    "style_key": "deep_dive",
                    "franchise_key": "",
                }
                for _ in input_data
            ]

            async def _coro() -> _Resp:
                return _Resp(payload)

            return _coro()

    fake_llm = _FakeLLMService()
    engine = ContentDiscoveryEngine.__new__(ContentDiscoveryEngine)
    engine._llm_service = fake_llm
    engine._concurrency = None
    engine._eval_cache = {}

    from openbiliclaw.soul.profile import SoulProfile

    profile = SoulProfile()
    contents = [DiscoveredContent(bvid=f"BV{i}", title=f"item {i}") for i in range(25)]

    scores = await engine.evaluate_content_batch(contents, profile, source_context="test")

    assert len(scores) == 25
    assert fake_llm.call_count == 1, (
        f"expected single LLM call with batch_size=30 default, got {fake_llm.call_count}"
    )


# ---------------------------------------------------------------------------
# 4. The exact user-reported scenario, end-to-end through /api/recommendations
# ---------------------------------------------------------------------------


def test_user_reported_scenario_5_genshin_in_popup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recreates the community-reported popup case:

    The user clicks one "AI 重绘原神地图" video. Then related_chain
    fans out and produces 5 同 franchise rows that all land in the
    recommendations table. Pre-fix /api/recommendations returned all
    five in a single 20-row response. With v0.3.18 the API caps at
    2 同 franchise per response.
    """
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.storage.database import Database
    from types import SimpleNamespace

    monkeypatch.setattr(
        "openbiliclaw.config.load_config",
        lambda: SimpleNamespace(
            data_path=tmp_path,
            llm=SimpleNamespace(concurrency=3),
            bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
            sources=SimpleNamespace(
                browser_cdp_url="",
                browser_headed=False,
                xiaohongshu=SimpleNamespace(
                    daily_search_budget=20,
                    daily_creator_budget=10,
                    task_interval_seconds=45,
                ),
                douyin=SimpleNamespace(enabled=False),
                youtube=SimpleNamespace(enabled=False, replace_bilibili_reposts=False),
            ),
            scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
        ),
    )
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    db = Database(tmp_path / "test.db")
    db.initialize()

    # Seed five 原神-tagged content rows (LLM tagged them at eval time)
    # plus one untagged neutral row. Insert sample recommendations
    # pointing at each, ordered DESC by created_at like real prod.
    samples = [
        ("BV原神1", "提瓦特摄影 4.0", "原神"),
        ("BV原神2", "AI 重绘原神地图", "原神"),
        ("BV原神3", "蒙德角色真实化", "原神"),
        ("BV原神4", "枫丹海域旅拍", "原神"),
        ("BV原神5", "原神 4.5 须弥版", "原神"),
        ("BV番茄", "番茄炒蛋 5 分钟教程", ""),
    ]
    for bvid, title, franchise in samples:
        db.cache_content(
            bvid=bvid,
            title=title,
            franchise_key=franchise,
            content_id=bvid,
            content_url=f"https://www.bilibili.com/video/{bvid}",
            source_platform="bilibili",
        )
        db.conn.execute(
            """
            INSERT INTO recommendations (bvid, expression, topic, presented)
            VALUES (?, ?, ?, 0)
            """,
            (bvid, "fake expression", "游戏"),
        )
    db.conn.commit()

    app = create_app(database=db)
    client = TestClient(app)

    response = client.get("/api/recommendations")
    assert response.status_code == 200
    items = response.json()["items"]

    # The cap kicks in: at most 2 of the 5 原神 rows survive.
    genshin_count = sum(1 for it in items if it["title"] not in {"番茄炒蛋 5 分钟教程"})
    assert genshin_count <= 2, (
        f"v0.3.18 franchise cap broken — popup carries {genshin_count} 原神 rows: "
        f"{[it['title'] for it in items]}"
    )
    # Untagged neutral content always passes through.
    assert any(it["title"] == "番茄炒蛋 5 分钟教程" for it in items)
