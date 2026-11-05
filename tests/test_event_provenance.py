"""Provenance contract: ingest time is not event time, imports are not behaviour.

These guards exist because a real ledger held 89,866 bilibili views inside 950
distinct seconds and 2,429 follows inside 93. Reading ``created_at`` as event
time produced "same day cross-domain scanning" awareness notes and a
"batch-followed 40 creators" portrait claim the user never performed.
"""

from __future__ import annotations

import json

from openbiliclaw.soul.event_provenance import (
    CREATORS_METADATA_KEY,
    SNAPSHOT_TOTAL_METADATA_KEY,
    TIME_QUALITY_INGEST,
    TIME_QUALITY_SNAPSHOT,
    TIME_QUALITY_SOURCE,
    apply_event_provenance,
    collapse_snapshot_creators,
    has_source_time,
    is_snapshot_event,
    prepare_cognition_events,
)


def _follow(name: str, *, mtime: int = 0, snapshot: bool = True) -> dict[str, object]:
    metadata: dict[str, object] = {"up_name": name, "source": "account_sync"}
    if mtime:
        metadata["timestamp"] = mtime
        metadata["mtime"] = mtime
    if snapshot:
        metadata["snapshot"] = True
    return {
        "event_type": "follow",
        "title": name,
        "created_at": "2026-10-27 04:56:39",
        "metadata": metadata,
    }


def test_ingest_timestamp_is_never_presented_as_event_time() -> None:
    row = {
        "event_type": "view",
        "title": "某视频",
        "created_at": "2026-08-01 02:28:16",
        "metadata": {"bvid": "BV1"},
    }

    [projected] = apply_event_provenance([row])

    assert "created_at" not in projected
    assert projected["metadata"]["time_quality"] == TIME_QUALITY_INGEST


def test_source_timestamp_survives_and_is_marked_source() -> None:
    row = {
        "event_type": "view",
        "title": "某视频",
        "created_at": "2026-08-01 02:28:16",
        "metadata": {"bvid": "BV1", "view_at": 1_785_532_358, "timestamp": 1_785_532_358},
    }

    [projected] = apply_event_provenance([row])

    assert projected["metadata"]["timestamp"] == 1_785_532_358
    assert projected["metadata"]["time_quality"] == TIME_QUALITY_SOURCE
    assert "created_at" not in projected


def test_json_string_metadata_from_database_reads_is_parsed() -> None:
    row = {
        "event_type": "follow",
        "title": "UP",
        "metadata": json.dumps({"snapshot": True, "timestamp": 1_584_271_945}),
    }

    assert has_source_time(row)
    assert is_snapshot_event(row)


def test_snapshot_follow_batch_collapses_into_one_inventory_row() -> None:
    rows = [_follow(f"UP{i}", mtime=1_584_271_945 + i * 86_400) for i in range(40)]

    collapsed = collapse_snapshot_creators(rows)

    assert len(collapsed) == 1
    row = collapsed[0]
    assert row["metadata"]["snapshot"] is True
    assert row["metadata"][SNAPSHOT_TOTAL_METADATA_KEY] == 40
    assert len(row["metadata"][CREATORS_METADATA_KEY]) == 40
    assert "账号导入存量" in str(row["context"])
    assert "不是最近发生的行为" in str(row["context"])
    # The follow window is preserved, so the model can still see it spans years.
    assert row["metadata"]["first_at"][:4] == "2020"
    assert "2020" in str(row["context"])


def test_single_new_follow_is_still_behaviour_not_inventory() -> None:
    """Steady-state sync discovering one new creator is a real action."""
    row = _follow("新关注", mtime=1_790_000_000, snapshot=False)

    collapsed = collapse_snapshot_creators([row])

    assert len(collapsed) == 1
    assert collapsed[0]["title"] == "新关注"
    assert "snapshot_total" not in collapsed[0]["metadata"]


def test_incremental_follows_without_snapshot_marker_are_kept() -> None:
    rows = [_follow("A", mtime=1_790_000_000, snapshot=False) for _ in range(3)]

    collapsed = collapse_snapshot_creators(rows)

    assert [row["title"] for row in collapsed] == ["A", "A", "A"]


def test_import_source_alone_marks_a_snapshot() -> None:
    """Douyin/XHS bootstrap producers tag ``import_source`` instead of snapshot."""
    row = {
        "event_type": "follow",
        "title": "某抖音号",
        "metadata": {"import_source": "dy_bootstrap_follow", "signal_strength": 0.6},
    }

    assert is_snapshot_event(row)


def test_collapsed_row_reports_snapshot_time_quality() -> None:
    rows = [_follow("A", mtime=1_584_271_945), _follow("B", mtime=1_700_000_000)]

    [projected] = prepare_cognition_events(rows)

    assert projected["metadata"]["time_quality"] == TIME_QUALITY_SNAPSHOT


def test_snapshot_favourites_are_not_collapsed() -> None:
    """Favourite titles are the interest signal; only creator inventory is merged."""
    rows = [
        {
            "event_type": "favorite",
            "title": f"视频{i}",
            "metadata": {"snapshot": True, "timestamp": 1_700_000_000 + i},
        }
        for i in range(5)
    ]

    collapsed = collapse_snapshot_creators(rows)

    assert [row["title"] for row in collapsed] == [f"视频{i}" for i in range(5)]


def test_prepare_cognition_events_does_not_mutate_input_rows() -> None:
    rows = [_follow("A", mtime=1_584_271_945), _follow("B", mtime=1_700_000_000)]
    before = json.dumps(rows, ensure_ascii=False, sort_keys=True)

    prepare_cognition_events(rows)

    assert json.dumps(rows, ensure_ascii=False, sort_keys=True) == before


def test_retracted_follow_is_excluded_from_the_collapsed_creator_list() -> None:
    """An unfollowed creator must not sneak back in through the inventory row."""
    rows = [_follow("A", mtime=1_584_271_945), _follow("B", mtime=1_700_000_000)]
    rows[1]["metadata"]["retracted"] = True

    [row] = collapse_snapshot_creators(rows)

    assert row["metadata"][CREATORS_METADATA_KEY] == ["A"]
    assert row["metadata"]["retracted_total"] == 1
    assert row["metadata"][SNAPSHOT_TOTAL_METADATA_KEY] == 2


def test_legacy_import_row_without_time_is_treated_as_inventory() -> None:
    """Pre-fix rows: import namespace, no source time, so no honest timeline."""
    legacy = {
        "event_type": "follow",
        "title": "老关注",
        "metadata": {"event_namespace": "import", "up_name": "老关注"},
    }
    dated = {
        "event_type": "follow",
        "title": "新关注",
        "metadata": {
            "event_namespace": "import",
            "up_name": "新关注",
            "timestamp": 1_790_000_000,
        },
    }

    assert is_snapshot_event(legacy)
    assert not is_snapshot_event(dated)
