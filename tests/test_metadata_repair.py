from __future__ import annotations

import sqlite3

import pytest

from openbiliclaw.storage.metadata_repair import repair_metadata


def database(path):
    with sqlite3.connect(path) as db:
        for table in ("discovery_candidates", "content_cache"):
            db.execute(
                f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, bvid TEXT, content_id TEXT, "
                "source_platform TEXT, body_text TEXT, published_at TEXT, tags TEXT)"
            )
        db.execute(
            "INSERT INTO discovery_candidates VALUES "
            "(1,'x:a','a','twitter','body','2026-01-01T00:00:00Z','[]')"
        )
        db.execute("INSERT INTO content_cache VALUES (1,'x:a','a','twitter','','','[]')")
        db.execute("INSERT INTO content_cache VALUES (2,'other:a','a','zhihu','','','[]')")


def test_preview_backup_apply_idempotence_and_source_boundary(tmp_path):
    path = tmp_path / "data.db"
    backup = tmp_path / "backup.db"
    database(path)
    before = path.read_bytes()
    preview = repair_metadata(path)
    assert preview["planned_fields"] == 2
    assert path.read_bytes() == before
    assert repair_metadata(path, apply=True, backup=backup)["applied_fields"] == 2
    assert repair_metadata(path)["planned_fields"] == 0
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT body_text FROM content_cache WHERE id=1").fetchone()[0] == ""
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT body_text FROM content_cache WHERE id=2").fetchone()[0] == ""
    with pytest.raises(ValueError):
        repair_metadata(path, apply=True, backup=backup)


def test_conflicting_evidence_is_not_selected_arbitrarily(tmp_path):
    path = tmp_path / "data.db"
    database(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO discovery_candidates VALUES (2,'x:a','a','twitter','different','','[]')"
        )
    report = repair_metadata(path)
    assert report["conflicts"]["twitter.body_text"] == 1
    assert report["planned_fields"] == 2
    assert "twitter.body_text" not in report["recoverable"]
