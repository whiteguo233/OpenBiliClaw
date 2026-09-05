from __future__ import annotations

import time
from pathlib import Path

from openbiliclaw.runtime.serve_snapshot import (
    ServeSnapshotStore,
    deserialize_snapshot,
    serialize_snapshot,
)
from openbiliclaw.storage.database import PoolServeSnapshot


def _snapshot() -> PoolServeSnapshot:
    return PoolServeSnapshot(
        readiness={"available": 3, "raw": 10, "pending": 2},
        candidate_rows=(
            {
                "bvid": "BV1",
                "title": "one",
                "source_platform": "bilibili",
                "pool_expression": "hello",
                "pool_topic_label": "tech",
            },
        ),
        loaded_count=1,
        platform_topups=(("douyin", 2),),
        seen_bvids=frozenset({"BV1"}),
        curator_signals=({"bvid": "BV1", "score": 0.5},),
        feedback_signals=({"bvid": "BV1", "type": "like"},),
    )


def test_serve_snapshot_round_trips_through_json_safe_dict() -> None:
    original = _snapshot()
    rebuilt = deserialize_snapshot(serialize_snapshot(original))

    assert rebuilt.readiness == original.readiness
    assert rebuilt.candidate_rows == original.candidate_rows
    assert rebuilt.loaded_count == original.loaded_count
    assert rebuilt.platform_topups == original.platform_topups
    assert rebuilt.seen_bvids == original.seen_bvids
    assert rebuilt.curator_signals == original.curator_signals
    assert rebuilt.feedback_signals == original.feedback_signals


def test_serve_snapshot_store_writes_and_reads_atomically(tmp_path: Path) -> None:
    store = ServeSnapshotStore(tmp_path / "serve_snapshot.json")
    snapshot = _snapshot()

    store.save(snapshot)

    assert (tmp_path / "serve_snapshot.json").is_file()
    loaded = store.load()
    assert loaded is not None
    assert loaded.candidate_rows[0]["bvid"] == "BV1"


def test_serve_snapshot_store_expires_stale_snapshot(tmp_path: Path) -> None:
    store = ServeSnapshotStore(tmp_path / "serve_snapshot.json")
    store.save(_snapshot())

    # Read with a clock that is far ahead of the written built_at; the
    # snapshot must be considered stale.
    store = ServeSnapshotStore(
        tmp_path / "serve_snapshot.json",
        max_age_seconds=1.0,
        now=lambda: time.time() + 100,
    )
    assert store.load() is None


def test_serve_snapshot_store_missing_file_returns_none(tmp_path: Path) -> None:
    store = ServeSnapshotStore(tmp_path / "missing.json")
    assert store.load() is None
