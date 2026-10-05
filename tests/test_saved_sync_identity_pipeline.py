from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    config = Config()
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    save_config(config, project_root / "config.toml")
    database = Database(tmp_path / "identity-pipeline.db")
    database.initialize()
    return database


def test_same_raw_content_id_survives_two_platform_recommendation_outputs(
    db: Database,
) -> None:
    rows = [
        DiscoveredContent(content_id="123", source_platform="twitter", title="x"),
        DiscoveredContent(content_id="123", source_platform="douyin", title="dy"),
    ]
    for item in rows:
        db.cache_content(item.item_key, **item.to_cache_kwargs())
    cached = db.conn.execute(
        "SELECT item_key, source_platform, content_id FROM content_cache "
        "WHERE content_id='123' ORDER BY item_key"
    ).fetchall()
    assert [tuple(row) for row in cached] == [
        ("douyin:123", "douyin", "123"),
        ("twitter:123", "twitter", "123"),
    ]


def test_recommendation_rows_preserve_canonical_identity(db: Database) -> None:
    item = DiscoveredContent(
        content_id="123",
        source_platform="twitter",
        content_url="https://x.com/u/status/123",
        content_type="tweet",
        title="x",
    )
    db.cache_content(item.item_key, **item.to_cache_kwargs())

    [recommendation_id] = db.batch_insert_recommendations(
        [
            {
                "bvid": item.item_key,
                "item_key": item.item_key,
                "expression": "给你看条推文",
                "topic": "X",
                "confidence": 0.9,
            }
        ]
    )

    row = db.conn.execute(
        "SELECT item_key FROM recommendations WHERE id = ?",
        (recommendation_id,),
    ).fetchone()
    assert row is not None
    assert row["item_key"] == "twitter:123"

    [output] = db.get_recommendations()
    assert output["item_key"] == "twitter:123"
    assert output["content_id"] == "123"
    assert output["source_platform"] == "twitter"
    assert output["content_url"] == "https://x.com/u/status/123"
    assert output["content_type"] == "tweet"


def test_recommendation_api_preserves_canonical_identity(db: Database) -> None:
    item = DiscoveredContent(
        content_id="123",
        source_platform="twitter",
        content_url="https://x.com/u/status/123",
        content_type="tweet",
        title="x",
    )
    db.cache_content(item.item_key, **item.to_cache_kwargs())
    db.insert_recommendation(
        item.item_key,
        item_key=item.item_key,
        confidence=0.9,
        expression="给你看条推文",
    )
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )

    response = TestClient(app).get("/api/recommendations")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["item_key"] == "twitter:123"
    assert payload["content_id"] == "123"
    assert payload["source_platform"] == "twitter"
    assert payload["content_url"] == "https://x.com/u/status/123"
    assert payload["content_type"] == "tweet"


def test_non_bilibili_storage_key_never_becomes_a_bilibili_url() -> None:
    item = DiscoveredContent(
        bvid="twitter:123",
        content_id="123",
        source_platform="twitter",
        content_type="tweet",
    )

    assert item.item_key == "twitter:123"
    assert item.content_url == ""


def test_pending_delight_api_preserves_canonical_identity(db: Database) -> None:
    runtime = SimpleNamespace(
        get_pending_delight=lambda: {
            "bvid": "twitter:123",
            "item_key": "twitter:123",
            "content_id": "123",
            "source_platform": "twitter",
            "content_url": "https://x.com/u/status/123",
            "content_type": "tweet",
        }
    )
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
        runtime_controller=runtime,
    )

    response = TestClient(app).get("/api/delight/pending")

    assert response.status_code == 200
    payload = response.json()["item"]
    assert payload["item_key"] == "twitter:123"
    assert payload["content_id"] == "123"
    assert payload["source_platform"] == "twitter"
    assert payload["content_url"] == "https://x.com/u/status/123"
    assert payload["content_type"] == "tweet"


def test_candidate_filter_does_not_cross_platform_dedupe_raw_ids(db: Database) -> None:
    twitter = DiscoveredContent(content_id="123", source_platform="twitter", title="x")
    db.cache_content(twitter.item_key, **twitter.to_cache_kwargs())
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=object(),  # type: ignore[arg-type]
        pool_target_count=30,
    )

    enqueued = pipeline.enqueue_candidates(
        [DiscoveredContent(content_id="123", source_platform="douyin", title="dy")]
    )

    assert enqueued == 1
    row = db.conn.execute(
        "SELECT candidate_key FROM discovery_candidates WHERE candidate_key = 'douyin:123'"
    ).fetchone()
    assert row is not None


def test_legacy_duplicate_item_keys_consolidate_without_losing_recommendations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-identity.db"
    database = Database(path)
    database.initialize()
    database.conn.execute("DROP INDEX idx_content_cache_item_key")
    database.conn.executescript(
        """
        DROP TABLE native_save_states;
        DROP TABLE saved_memberships;
        DROP TABLE saved_items;
        DROP TABLE saved_sync_migrations;
        DROP TABLE watch_later;
        DROP TABLE favorites;
        CREATE TABLE watch_later (
            bvid TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT DEFAULT ''
        );
        CREATE TABLE favorites (
            bvid TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT DEFAULT ''
        );
        """
    )
    database.conn.executemany(
        """
        INSERT INTO content_cache (
            bvid, item_key, title, source_platform, content_id, content_url, content_type
        ) VALUES (?, '', ?, 'twitter', '123', ?, 'tweet')
        """,
        [
            ("legacy-123", "legacy title", ""),
            ("twitter:123", "", "https://x.com/u/status/123"),
        ],
    )
    database.conn.execute(
        """
        UPDATE content_cache
        SET temporal_class='breaking', temporal_confidence=0.95,
            temporal_reason='价值依赖即时状态',
            published_at='2000-01-01T00:00:00Z'
        WHERE bvid='legacy-123'
        """
    )
    database.conn.execute(
        """
        UPDATE content_cache
        SET temporal_class='evergreen', temporal_confidence=0.99,
            temporal_reason='长期价值不依赖当前时间',
            published_at='2026-08-11T00:00:00Z',
            published_label='刚刚'
        WHERE bvid='twitter:123'
        """
    )
    database.conn.execute(
        """
        INSERT INTO recommendations (bvid, item_key, expression, confidence)
        VALUES ('legacy-123', '', 'legacy rec', 0.9)
        """
    )
    database.conn.execute(
        "INSERT INTO watch_later (bvid, note) VALUES ('legacy-123', 'watch note')"
    )
    database.conn.execute(
        "INSERT INTO favorites (bvid, note) VALUES ('legacy-123', 'favorite note')"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()

    cached = migrated.conn.execute(
        """
        SELECT bvid, item_key, title, content_url,
               published_at, published_label,
               temporal_class, temporal_confidence, temporal_reason,
               pool_status
        FROM content_cache
        WHERE item_key = 'twitter:123'
        """
    ).fetchall()
    assert [tuple(row) for row in cached] == [
        (
            "twitter:123",
            "twitter:123",
            "legacy title",
            "https://x.com/u/status/123",
            "2000-01-01T00:00:00Z",
            "",
            "breaking",
            0.95,
            "价值依赖即时状态",
            "temporal_review_hold",
        )
    ]
    recommendation = migrated.conn.execute(
        "SELECT bvid, item_key FROM recommendations WHERE expression = 'legacy rec'"
    ).fetchone()
    assert recommendation is not None
    assert tuple(recommendation) == ("twitter:123", "twitter:123")
    watch = migrated.get_saved_membership("watch_later", "twitter:123")
    favorite = migrated.get_saved_membership("favorite", "twitter:123")
    assert watch is not None
    assert watch["note"] == "watch note"
    assert favorite is not None
    assert favorite["note"] == "favorite note"
    assert migrated.get_saved_membership("watch_later", "bilibili:legacy-123") is None
    assert migrated.get_saved_membership("favorite", "bilibili:legacy-123") is None
    assert migrated.count_watch_later() == 1
    assert migrated.count_favorites() == 1
    for legacy_table in ("watch_later", "favorites"):
        legacy = migrated.conn.execute(
            f"SELECT item_key FROM {legacy_table} WHERE bvid = 'legacy-123'"
        ).fetchone()
        assert legacy is not None
        assert legacy["item_key"] == "twitter:123"


def test_legacy_duplicate_keeps_selected_temporal_evidence_publication_as_one_group(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-temporal-group.db"
    database = Database(path)
    database.initialize()
    database.conn.execute("DROP INDEX idx_content_cache_item_key")
    database.conn.executemany(
        """
        INSERT INTO content_cache (
            bvid, item_key, title, source_platform, content_id,
            temporal_class, temporal_confidence, temporal_reason,
            published_at, relevance_score, source, pool_status
        ) VALUES (?, '', ?, 'twitter', '456', ?, ?, ?, ?, 0.9, 'search', 'fresh')
        """,
        [
            (
                "legacy-broken-456",
                "不完整的旧时效行",
                "breaking",
                0.95,
                "",
                "1999-01-01T00:00:00Z",
            ),
            (
                "legacy-456",
                "旧的未知重复行",
                "unknown",
                0.0,
                "",
                "2000-01-01T00:00:00Z",
            ),
            (
                "twitter:456",
                "当前事件",
                "current",
                0.95,
                "近期事件仍有效",
                "2026-08-11T00:00:00Z",
            ),
        ],
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()

    row = migrated.conn.execute(
        """
        SELECT temporal_class, temporal_confidence, temporal_reason,
               published_at, pool_status
        FROM content_cache
        WHERE item_key='twitter:456'
        """
    ).fetchone()
    assert dict(row) == {
        "temporal_class": "current",
        "temporal_confidence": 0.95,
        "temporal_reason": "近期事件仍有效",
        "published_at": "2026-08-11T00:00:00Z",
        "pool_status": "fresh",
    }


def test_legacy_writer_can_omit_item_key_until_current_runtime_repairs_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-writer-identity.db"
    database = Database(path)
    database.initialize()

    index_row = database.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_content_cache_item_key'"
    ).fetchone()
    assert index_row is not None
    assert "WHERE item_key != ''" in str(index_row["sql"])

    # v0.3.166 and older writers do not know about item_key. They must be able
    # to keep filling the pool after a newer runtime has migrated the shared DB.
    database.conn.executemany(
        """
        INSERT INTO content_cache (bvid, title, source_platform, content_id)
        VALUES (?, ?, 'bilibili', ?)
        """,
        [
            ("BV1legacyA", "legacy A", "BV1legacyA"),
            ("BV1legacyB", "legacy B", "BV1legacyB"),
        ],
    )
    database.conn.commit()
    blank_count = database.conn.execute(
        "SELECT COUNT(*) FROM content_cache WHERE item_key = ''"
    ).fetchone()[0]
    assert blank_count == 2
    database.close()

    repaired = Database(path)
    repaired.initialize()

    rows = repaired.conn.execute(
        "SELECT bvid, item_key FROM content_cache ORDER BY bvid"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("BV1legacyA", "bilibili:BV1legacyA"),
        ("BV1legacyB", "bilibili:BV1legacyB"),
    ]


def test_identity_repair_merges_legacy_blank_row_with_existing_canonical_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-writer-duplicate.db"
    database = Database(path)
    database.initialize()
    current = DiscoveredContent(
        content_id="123",
        source_platform="twitter",
        content_url="https://x.com/u/status/123",
        content_type="tweet",
        title="current title",
    )
    database.cache_content(current.item_key, **current.to_cache_kwargs())
    # Recreate the pre-fix full unique index found in databases migrated by a
    # newer source checkout and then reopened by desktop v0.3.166.
    database.conn.execute("DROP INDEX idx_content_cache_item_key")
    database.conn.execute(
        "CREATE UNIQUE INDEX idx_content_cache_item_key ON content_cache (item_key)"
    )
    database.conn.execute(
        """
        INSERT INTO content_cache (
            bvid, item_key, title, source_platform, content_id, content_type
        ) VALUES ('legacy-twitter-123', '', 'legacy title', 'twitter', '123', 'tweet')
        """
    )
    database.conn.commit()
    database.close()

    repaired = Database(path)
    repaired.initialize()

    rows = repaired.conn.execute(
        """
        SELECT bvid, item_key, title, content_url
        FROM content_cache
        WHERE item_key = 'twitter:123'
        """
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (
            "twitter:123",
            "twitter:123",
            "current title",
            "https://x.com/u/status/123",
        )
    ]
    index_row = repaired.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_content_cache_item_key'"
    ).fetchone()
    assert index_row is not None
    assert "WHERE item_key != ''" in str(index_row["sql"])


def test_ambiguous_legacy_raw_recommendation_does_not_cross_link(db: Database) -> None:
    for platform in ("twitter", "douyin"):
        item = DiscoveredContent(
            content_id="123",
            source_platform=platform,
            content_url=f"https://example.com/{platform}/123",
            content_type="tweet" if platform == "twitter" else "video",
            relevance_score=0.9,
        )
        db.cache_content(item.item_key, **item.to_cache_kwargs())
    db.conn.execute(
        """
        INSERT INTO recommendations (bvid, item_key, expression, confidence)
        VALUES ('123', '', 'ambiguous', 0.9)
        """
    )
    db.conn.commit()

    [row] = db.get_recommendations()

    assert row["item_key"] == ""
    assert row["content_id"] == "123"
    assert row["source_platform"] == ""
    assert row["content_url"] == ""
    assert row["content_type"] == "video"


def test_unambiguous_legacy_raw_recommendation_resolves_full_identity(db: Database) -> None:
    item = DiscoveredContent(
        content_id="123",
        source_platform="twitter",
        content_url="https://x.com/u/status/123",
        content_type="tweet",
        relevance_score=0.9,
    )
    db.cache_content(item.item_key, **item.to_cache_kwargs())
    db.conn.execute(
        """
        INSERT INTO recommendations (bvid, item_key, expression, confidence)
        VALUES ('123', '', 'unambiguous', 0.9)
        """
    )
    db.conn.commit()

    [row] = db.get_recommendations()

    assert row["item_key"] == "twitter:123"
    assert row["content_id"] == "123"
    assert row["source_platform"] == "twitter"
    assert row["content_url"] == "https://x.com/u/status/123"
    assert row["content_type"] == "tweet"


def test_namespaced_storage_key_infers_non_bilibili_identity_without_url() -> None:
    item = DiscoveredContent(bvid="twitter:123")

    assert item.source_platform == "twitter"
    assert item.content_id == "123"
    assert item.item_key == "twitter:123"
    assert item.content_url == ""


def test_raw_bilibili_id_keeps_legacy_identity_and_url() -> None:
    item = DiscoveredContent(bvid="BV1abc123")

    assert item.source_platform == "bilibili"
    assert item.content_id == "BV1abc123"
    assert item.item_key == "bilibili:BV1abc123"
    assert item.content_url == "https://www.bilibili.com/video/BV1abc123"
