"""Tests for the init_runs store backing guided (GUI) initialization.

See docs/specs/gui-init.md §5a and docs/plans/2026-06-07-gui-init-implementation.md A1.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "init.db")
    db.initialize()
    return db


def test_event_source_schema_is_present_in_fresh_database(tmp_path: Path) -> None:
    db = _db(tmp_path)

    columns = {
        str(row["name"]): str(row["dflt_value"])
        for row in db.conn.execute("PRAGMA table_info(events)").fetchall()
    }

    assert columns["source_platform"] == "''"
    assert columns["content_id"] == "''"
    assert columns["source_confidence"] == "'legacy_unknown'"
    assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 8
    indexes = {str(row["name"]) for row in db.conn.execute("PRAGMA index_list(events)").fetchall()}
    assert "idx_events_source_content" in indexes


def test_event_source_schema_migrates_legacy_rows_without_overclaiming(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-events.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            url TEXT,
            title TEXT,
            context TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO events (event_type, url, title, metadata)
        VALUES (
            'view',
            'https://www.xiaohongshu.com/explore/note-1',
            '小红书旧事件',
            '{"source_platform":"xiaohongshu","note_id":"note-1"}'
        );
        INSERT INTO events (event_type, url, title, metadata)
        VALUES (
            'view',
            'https://www.bilibili.com/video/BVOLD',
            'B站旧事件',
            '{"source_platform":"bilibili","bvid":"BVOLD"}'
        );
        INSERT INTO events (event_type, url, title, metadata)
        VALUES ('view', '', '只有兼容来源提示的旧事件', '{"source_platform":"bilibili"}');
        INSERT INTO events (event_type, url, title, metadata)
        VALUES ('view', '', '无法识别的旧事件', '{}');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    rows = {
        str(row["title"]): row
        for row in db.conn.execute(
            """
            SELECT title, source_platform, content_id, source_confidence
            FROM events
            ORDER BY id
            """
        ).fetchall()
    }
    assert dict(rows["小红书旧事件"]) == {
        "title": "小红书旧事件",
        "source_platform": "xiaohongshu",
        "content_id": "note-1",
        "source_confidence": "exact",
    }
    assert dict(rows["B站旧事件"]) == {
        "title": "B站旧事件",
        "source_platform": "bilibili",
        "content_id": "BVOLD",
        "source_confidence": "inferred",
    }
    assert dict(rows["只有兼容来源提示的旧事件"]) == {
        "title": "只有兼容来源提示的旧事件",
        "source_platform": "bilibili",
        "content_id": "",
        "source_confidence": "legacy_unknown",
    }
    assert dict(rows["无法识别的旧事件"]) == {
        "title": "无法识别的旧事件",
        "source_platform": "",
        "content_id": "",
        "source_confidence": "legacy_unknown",
    }

    # Startup migrations are idempotent; a second open does not rewrite the
    # attribution or fail because the columns/index already exist.
    db.close()
    reopened = Database(path)
    reopened.initialize()
    assert (
        reopened.conn.execute(
            "SELECT source_platform, content_id, source_confidence FROM events WHERE id = 1"
        ).fetchone()["source_platform"]
        == "xiaohongshu"
    )


def test_event_insert_persists_canonical_source_attribution(tmp_path: Path) -> None:
    db = _db(tmp_path)

    event_id = db.insert_event(
        "view",
        url="https://x.com/example/status/123",
        title="X 旧接口事件",
        metadata={"tweet_id": "123"},
    )
    row = db.conn.execute(
        """
        SELECT source_platform, content_id, source_confidence, metadata
        FROM events
        WHERE id = ?
        """,
        (event_id,),
    ).fetchone()

    assert row["source_platform"] == "twitter"
    assert row["content_id"] == "123"
    assert row["source_confidence"] == "inferred"
    stored_metadata = json.loads(row["metadata"])
    assert stored_metadata["source_platform"] == "twitter"
    assert stored_metadata["content_id"] == "123"


def test_event_insert_does_not_upgrade_url_inference_to_exact(tmp_path: Path) -> None:
    db = _db(tmp_path)

    event_id = db.insert_event(
        "view",
        url="https://x.com/example/status/123",
        title="只有 URL 证据的事件",
        source_confidence="exact",
    )
    row = db.conn.execute(
        "SELECT source_platform, source_confidence FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()

    assert row["source_platform"] == "twitter"
    assert row["source_confidence"] == "inferred"


def test_event_insert_keeps_unknown_slug_but_not_exact(tmp_path: Path) -> None:
    db = _db(tmp_path)

    event_id = db.insert_event(
        "view",
        title="未来平台事件",
        source_platform="threads",
        source_confidence="exact",
    )
    row = db.conn.execute(
        "SELECT source_platform, source_confidence FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()

    assert row["source_platform"] == "threads"
    assert row["source_confidence"] == "legacy_unknown"


def test_event_insert_downgrades_confidence_without_platform(tmp_path: Path) -> None:
    db = _db(tmp_path)

    event_id = db.insert_event(
        "view",
        title="没有来源标签的事件",
        source_confidence="exact",
    )
    row = db.conn.execute(
        "SELECT source_platform, source_confidence FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()

    assert row["source_platform"] == ""
    assert row["source_confidence"] == "legacy_unknown"


def test_event_source_backfill_is_not_repeated_for_existing_columns(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.insert_event("follow", title="没有内容 ID 的关注事件")

    statements: list[str] = []
    db.conn.set_trace_callback(statements.append)
    try:
        db._ensure_event_source_columns()
    finally:
        db.conn.set_trace_callback(None)

    assert not any(
        "SELECT id, url, metadata, source_platform" in statement for statement in statements
    )


def test_chat_turn_payload_schema_is_present_in_fresh_database(tmp_path: Path) -> None:
    db = _db(tmp_path)

    columns = {
        str(row["name"]): str(row["dflt_value"])
        for row in db.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }

    assert columns["payload"] == "'{}'"
    assert columns["reply_to_turn_id"] == "''"
    settlement_columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
    }
    assert settlement_columns == {
        "ref",
        "verdict",
        "turn_id",
        "payload",
        "applied",
        "result",
        "event_id",
        "created_at",
        "updated_at",
    }


def test_chat_turn_payload_schema_migrates_legacy_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy-chat.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE chat_turns (
            turn_id TEXT PRIMARY KEY,
            session TEXT NOT NULL DEFAULT 'popup',
            scope TEXT NOT NULL DEFAULT 'chat',
            subject_id TEXT NOT NULL DEFAULT '',
            subject_title TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reply TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO chat_turns (turn_id, message) VALUES ('legacy-turn', '旧消息');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    assert "payload" in {
        str(row["name"]) for row in db.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }
    assert "reply_to_turn_id" in {
        str(row["name"]) for row in db.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }
    assert db.get_chat_turn("legacy-turn")["payload"] == {}
    assert db.get_chat_turn("legacy-turn")["reply_to_turn_id"] == ""


def test_card_settlement_schema_rebuilds_wave_a_table_without_claim_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-settlement.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE card_settlements (
            ref TEXT PRIMARY KEY,
            verdict TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO card_settlements (ref, verdict, turn_id)
        VALUES ('abc12345', 'confirmed', 'legacy-card');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    row = db.get_card_settlement("abc12345")
    assert row is not None
    assert row == {
        "ref": "abc12345",
        "verdict": "confirmed",
        "turn_id": "legacy-card",
        "payload": {},
        "applied": 0,
        "result": {},
        "event_id": "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    assert row["created_at"]
    assert row["updated_at"]
    columns = {
        str(info["name"])
        for info in db.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
    }
    assert columns == {
        "ref",
        "verdict",
        "turn_id",
        "payload",
        "applied",
        "result",
        "event_id",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    ("applied", "seg_event", "expected_event_recorded"),
    [(0, 0, False), (0, 1, True), (1, 0, True)],
)
def test_card_settlement_schema_rebuilds_claim_table_preserving_winner_and_event_identity(
    tmp_path: Path,
    applied: int,
    seg_event: int,
    expected_event_recorded: bool,
) -> None:
    path = tmp_path / f"legacy-claim-{applied}-{seg_event}.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE card_settlements (
            ref TEXT PRIMARY KEY,
            verdict TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{{}}',
            applied INTEGER NOT NULL DEFAULT 0,
            apply_claim_at TEXT,
            apply_claim_token TEXT NOT NULL DEFAULT '',
            seg_event INTEGER NOT NULL DEFAULT 0,
            seg_object INTEGER NOT NULL DEFAULT 0,
            seg_marker INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO card_settlements (
            ref, verdict, turn_id, payload, applied, apply_claim_at,
            apply_claim_token, seg_event, seg_object, seg_marker
        )
        VALUES (
            'winner-ref', 'revised', 'winner-turn',
            '{{"kind":"hypothesis","title":"原赢家"}}',
            {applied}, '2026-07-22T04:00:00+00:00',
            'old-owner', {seg_event}, 1, 1
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    row = db.get_card_settlement("winner-ref")
    assert row is not None
    assert row["verdict"] == "revised"
    assert row["turn_id"] == "winner-turn"
    assert row["payload"] == {"kind": "hypothesis", "title": "原赢家"}
    assert row["applied"] == applied
    assert bool(row["event_id"]) is expected_event_recorded
    columns = {
        str(info["name"])
        for info in db.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
    }
    assert not columns.intersection(
        {
            "apply_claim_at",
            "apply_claim_token",
            "seg_event",
            "seg_object",
            "seg_marker",
        }
    )


def test_card_settlement_fresh_schema_double_initialize_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "double-init.db"
    db = Database(path)
    db.initialize()
    assert db.try_create_card_settlement(
        ref="double-init-ref",
        verdict="confirmed",
        turn_id="double-init-turn",
        payload={"kind": "hypothesis", "title": "保留赢家"},
    )
    before = db.get_card_settlement("double-init-ref")
    db.close()

    reopened = Database(path)
    reopened.initialize()
    reopened.initialize()

    assert reopened.get_card_settlement("double-init-ref") == before


@pytest.mark.parametrize(
    ("initial", "target"),
    [
        ("pending", "confirmed"),
        ("pending", "rejected"),
        ("pending", "deferred"),
        ("pending", "discussing"),
        ("discussing", "confirmed"),
        ("discussing", "rejected"),
        ("discussing", "deferred"),
        ("discussing", "pending"),
    ],
)
def test_chat_turn_payload_state_cas_allows_declared_transitions(
    tmp_path: Path,
    initial: str,
    target: str,
) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(
        turn_id=f"{initial}-{target}",
        message="卡片",
        payload={"type": "card", "state": initial, "marker": "preserved"},
    )

    assert db.update_chat_turn_payload_state(
        f"{initial}-{target}",
        expected_state=initial,
        new_state=target,
    )
    payload = db.get_chat_turn(f"{initial}-{target}")["payload"]
    assert payload == {"type": "card", "state": target, "marker": "preserved"}


def test_chat_turn_payload_state_cas_rejects_stale_or_illegal_transition(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(
        turn_id="card-cas",
        message="卡片",
        payload={"type": "card", "state": "pending"},
    )

    assert not db.update_chat_turn_payload_state(
        "card-cas",
        expected_state="discussing",
        new_state="confirmed",
    )
    with pytest.raises(ValueError, match="Unsupported card payload transition"):
        db.update_chat_turn_payload_state(
            "card-cas",
            expected_state="confirmed",
            new_state="pending",
        )
    assert db.get_chat_turn("card-cas")["payload"]["state"] == "pending"


def test_card_settlement_insert_or_ignore_arbitrates_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "settlement.db"
    seed = Database(path)
    seed.initialize()
    seed.close()
    barrier = threading.Barrier(50)

    def contend(index: int) -> tuple[bool, str, str]:
        database = Database(path)
        database.initialize()
        verdict = "confirmed" if index % 2 == 0 else "rejected"
        turn_id = f"turn-{index}"
        try:
            barrier.wait(timeout=5)
            inserted = database.try_create_card_settlement(
                ref="hypothesis:abc12345",
                verdict=verdict,
                turn_id=turn_id,
                payload={"request_index": index},
            )
            return inserted, verdict, turn_id
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=50) as executor:
        outcomes = list(executor.map(contend, range(50)))

    assert Counter(inserted for inserted, _verdict, _turn_id in outcomes) == {
        False: 49,
        True: 1,
    }
    winner = next(item for item in outcomes if item[0])
    reopened = Database(path)
    reopened.initialize()
    settlement = reopened.get_card_settlement("hypothesis:abc12345")
    assert settlement is not None
    assert (settlement["verdict"], settlement["turn_id"]) == winner[1:]
    assert settlement["applied"] == 0


def test_confirmation_turn_deduplicates_ref_session_atomically_but_not_cross_session(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    barrier = threading.Barrier(2)

    def create(index: int) -> tuple[dict[str, object], bool]:
        barrier.wait(timeout=2)
        return db.create_chat_confirmation_turn(
            turn_id=f"card-{index}",
            session="popup",
            scope="hypothesis",
            ref="abc12345",
            title="并发打开同一假设",
            message="阿b 的猜测",
            reply="",
            payload={
                "type": "card",
                "kind": "hypothesis",
                "ref": "abc12345",
                "title": "并发打开同一假设",
                "state": "pending",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))

    assert sorted(created for _row, created in results) == [False, True]
    assert len({str(row["turn_id"]) for row, _created in results}) == 1
    webui, created = db.create_chat_confirmation_turn(
        turn_id="card-webui",
        session="webui",
        scope="hypothesis",
        ref="abc12345",
        title="并发打开同一假设",
        message="阿b 的猜测",
        reply="",
        payload={
            "type": "card",
            "kind": "hypothesis",
            "ref": "abc12345",
            "title": "并发打开同一假设",
            "state": "pending",
        },
    )
    assert created is True
    assert webui["turn_id"] == "card-webui"
    count = db.conn.execute(
        "SELECT COUNT(*) FROM chat_turns WHERE subject_id = 'abc12345'"
    ).fetchone()[0]
    assert count == 2


def test_card_settlement_event_and_completion_are_idempotent_without_claims(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    assert db.try_create_card_settlement(
        ref="abc12345",
        verdict="confirmed",
        turn_id="card-popup",
        payload={"kind": "hypothesis", "title": "用户偏爱深度内容"},
    )
    event = {
        "event_type": "feedback",
        "title": "用户偏爱深度内容",
        "metadata": {"settlement_ref": "abc12345", "signal": "confirm"},
    }

    event_results = [
        db.record_card_settlement_event_once(ref="abc12345", event=event) for _ in range(10)
    ]
    assert event_results == [True, *([False] * 9)]
    completion_results = [
        db.complete_card_settlement(
            ref="abc12345",
            result={"matched": True, "state": "confirmed"},
        )
        for _ in range(10)
    ]
    assert completion_results == [True, *([False] * 9)]

    row = db.get_card_settlement("abc12345")
    assert row is not None
    assert row["event_id"].startswith("dialogue:")
    assert len(row["event_id"]) == 79
    assert row["applied"] == 1
    assert row["result"] == {"matched": True, "state": "confirmed"}
    events = db.query_events(event_types=["feedback"])
    assert len(events) == 1
    assert json.loads(events[0]["metadata"])["settlement_ref"] == "abc12345"


@pytest.mark.parametrize(
    "invalid_ref",
    ["", "   ", "bad\x00ref", "x" * 1025],
)
def test_card_settlement_ref_validation_rejects_unsafe_effect_key_input(
    tmp_path: Path,
    invalid_ref: str,
) -> None:
    db = _db(tmp_path)
    with pytest.raises(ValueError, match="ref is invalid"):
        db.try_create_card_settlement(
            ref=invalid_ref,
            verdict="confirmed",
            turn_id="unsafe-ref",
        )


def test_profile_ledger_stable_effect_key_inserts_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    effect_key = f"dialogue:{'a' * 64}:derived:{'b' * 64}"

    first = db.insert_profile_ledger(
        write_point="anchor_revise_derived",
        source="dialogue_anchor",
        source_refs=["derived"],
        turn_id="turn-1",
        effect_key=effect_key,
    )
    replay = db.insert_profile_ledger(
        write_point="anchor_revise_derived",
        source="dialogue_anchor",
        source_refs=["derived"],
        turn_id="turn-1",
        effect_key=effect_key,
    )

    assert first > 0
    assert replay == 0
    assert (
        len(
            db.query_profile_ledger(
                days=1,
                write_point="anchor_revise_derived",
            )
        )
        == 1
    )
    assert db.query_profile_ledger(days=1)[0]["effect_key"] == effect_key


def test_profile_ledger_effect_key_migrates_legacy_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy-profile-ledger.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE profile_update_ledger (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            write_point    TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT '',
            before_summary TEXT NOT NULL DEFAULT '',
            after_summary  TEXT NOT NULL DEFAULT '',
            diff           TEXT NOT NULL DEFAULT '',
            source_refs    TEXT NOT NULL DEFAULT '',
            outcome        TEXT NOT NULL DEFAULT 'success',
            turn_id        TEXT NOT NULL DEFAULT '',
            gate_verdict   TEXT NOT NULL DEFAULT '',
            held_id        TEXT NOT NULL DEFAULT '',
            error          TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO profile_update_ledger (write_point)
        VALUES ('legacy-write');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(profile_update_ledger)").fetchall()
    }
    assert "effect_key" in columns
    rows = db.query_profile_ledger(days=1)
    assert rows[0]["write_point"] == "legacy-write"
    assert rows[0]["effect_key"] == ""


@pytest.mark.parametrize(
    "effect_key",
    (
        "dialogue:raw-ref:ledger",
        f"dialogue:{'a' * 64}:derived:not-a-hash",
    ),
)
def test_profile_ledger_rejects_unsafe_stable_effect_key(
    tmp_path: Path,
    effect_key: str,
) -> None:
    db = _db(tmp_path)

    with pytest.raises(ValueError, match="effect key is invalid"):
        db.insert_profile_ledger(
            write_point="settle_insight",
            effect_key=effect_key,
        )


def test_card_projection_ignores_unapplied_receipt_and_refreshes_all_sessions(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    for session in ("popup", "webui"):
        db.create_chat_turn(
            turn_id=f"card-{session}",
            message="阿b 的猜测",
            session=session,
            scope="hypothesis",
            payload={"type": "card", "ref": "abc12345", "state": "pending"},
        )
    db.try_create_card_settlement(ref="abc12345", verdict="confirmed", turn_id="card-popup")

    assert db.project_applied_card_settlement("abc12345") == 0
    assert db.get_chat_turn("card-popup")["payload"]["state"] == "pending"

    assert db.record_card_settlement_event_once(
        ref="abc12345",
        event={
            "event_type": "feedback",
            "title": "假设",
            "metadata": {"settlement_ref": "abc12345"},
        },
    )
    assert db.complete_card_settlement(ref="abc12345", result={"matched": True})
    assert db.project_applied_card_settlement("abc12345") == 2
    assert db.get_chat_turn("card-popup")["payload"]["state"] == "confirmed"
    assert db.get_chat_turn("card-webui")["payload"]["state"] == "confirmed"


def test_legacy_card_settlement_columns_are_migration_only() -> None:
    source_path = Path(Database.__module__.replace(".", "/") + ".py")
    source = (Path(__file__).parents[1] / "src" / source_path).read_text(encoding="utf-8")
    lines = source.splitlines()
    migration_start = next(
        index
        for index, line in enumerate(lines, start=1)
        if "def _migrate_card_settlements_to_wave_2" in line
    )
    migration_end = next(
        index
        for index, line in enumerate(lines[migration_start:], start=migration_start + 1)
        if line.startswith("    def ") and "_migrate_card_settlements_to_wave_2" not in line
    )
    legacy_names = {
        "apply_claim_at",
        "apply_claim_token",
        "seg_event",
        "seg_object",
        "seg_marker",
    }
    occurrences = {
        name: [index for index, line in enumerate(lines, start=1) if name in line]
        for name in legacy_names
    }
    assert all(occurrences.values())
    assert all(
        migration_start <= line_number < migration_end
        for line_numbers in occurrences.values()
        for line_number in line_numbers
    )


def test_orphan_discussing_card_returns_to_pending_without_recovery_token(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(
        turn_id="card-discuss",
        message="阿b 的猜测",
        scope="hypothesis",
        payload={"type": "card", "ref": "abc12345", "state": "discussing"},
    )

    assert db.update_chat_turn_payload_state(
        "card-discuss",
        expected_state="discussing",
        new_state="pending",
    )
    payload = db.get_chat_turn("card-discuss")["payload"]
    assert payload == {"type": "card", "ref": "abc12345", "state": "pending"}


def test_chat_turn_list_uses_rowid_for_equal_created_at(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.create_chat_turn(turn_id="z-first", message="先插入")
    db.create_chat_turn(turn_id="a-second", message="后插入")
    db.conn.execute("UPDATE chat_turns SET created_at = '2026-07-22 01:00:00'")
    db.conn.commit()

    rows = db.list_chat_turns(limit=2)

    assert [row["turn_id"] for row in rows] == ["z-first", "a-second"]


def test_get_latest_init_run_none_when_empty(tmp_path: Path) -> None:
    assert _db(tmp_path).get_latest_init_run() is None


def test_init_runs_migrates_separate_progress_clock(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE init_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            stage INTEGER NOT NULL DEFAULT 0,
            stages_json TEXT,
            partial_success INTEGER NOT NULL DEFAULT 0,
            error_reason TEXT,
            error_detail TEXT,
            sequence INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        );
        INSERT INTO init_runs (run_id, status, sequence, updated_at)
        VALUES ('legacy-run', 'completed', 7, '2026-07-01 01:02:03');
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()
    run = db.get_latest_init_run()
    assert run["progress_sequence"] == 0
    assert str(run["progress_at"]) == "2026-07-01 01:02:03"


def test_init_run_reserve_and_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True

    run = db.get_latest_init_run()
    assert run is not None
    assert run["run_id"] == "run-1"
    assert run["status"] == "starting"
    assert run["stage"] == 0
    assert run["partial_success"] == 0
    assert run["progress_sequence"] == 0
    assert run["progress_at"] is not None

    db.update_init_run(
        "run-1",
        status="running",
        stage=2,
        sequence=5,
        stages_json=json.dumps([{"n": 1, "status": "ok"}, {"n": 2, "status": "running"}]),
    )
    run = db.get_latest_init_run()
    assert run["status"] == "running"
    assert run["stage"] == 2
    assert run["sequence"] == 5
    assert json.loads(run["stages_json"])[0]["status"] == "ok"


def test_try_reserve_is_single_flight(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True
    # A second reservation while one is active must fail (TOCTOU guard).
    assert db.try_reserve_init_starting("run-2") is False

    # Once the active run finishes, a new run can be reserved again.
    db.update_init_run("run-1", status="completed")
    assert db.try_reserve_init_starting("run-3") is True
    assert db.get_latest_init_run()["run_id"] == "run-3"


def test_reconcile_fails_stale_active_runs_on_boot(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run(
        "run-1",
        status="running",
        stage=3,
        stages_json=json.dumps(
            [
                {"n": 1, "status": "ok", "reason": None},
                {"n": 2, "status": "running", "reason": None, "progress": 0.5},
                {"n": 3, "status": "pending", "reason": None},
            ]
        ),
    )

    reconciled = db.reconcile_init_runs_on_boot()
    assert reconciled == 1

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert run["finished_at"] is not None

    # A user-facing detail is written so /api/init-status is diagnosable.
    assert run["error_detail"] == "初始化后台任务已结束，但未能写入终态；已自动释放运行锁。"

    # Running/pending stages are downgraded to failed/interrupted (no phantom
    # "running" stage survives a restart); completed stages are left intact.
    stages = json.loads(run["stages_json"])
    assert stages[0]["status"] == "ok"
    assert stages[1]["status"] == "failed"
    assert stages[1]["reason"] == "interrupted"
    assert "progress" not in stages[1]
    assert stages[2]["status"] == "failed"
    assert stages[2]["reason"] == "interrupted"

    # Idempotent: a completed run is not touched a second time.
    assert db.reconcile_init_runs_on_boot() == 0


def test_reconcile_leaves_terminal_runs_untouched(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run("run-1", status="completed")
    assert db.reconcile_init_runs_on_boot() == 0
    assert db.get_latest_init_run()["status"] == "completed"


def test_update_init_run_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    with pytest.raises(ValueError, match="unknown columns"):
        db.update_init_run("run-1", bogus="x")


def test_xhs_login_state_roundtrips_through_auth_state(tmp_path: Path) -> None:
    db = _db(tmp_path)

    assert db.get_xhs_login_state() == (False, "")

    db.set_xhs_login_state(True, when_iso="2026-07-07T01:02:03+00:00")
    assert db.get_xhs_login_state() == (True, "2026-07-07T01:02:03+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state'").fetchone()[0]
        == "1"
    )
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state_at'").fetchone()[
            0
        ]
        == "2026-07-07T01:02:03+00:00"
    )

    db.set_xhs_login_state(False, when_iso="2026-07-07T02:03:04+00:00")
    assert db.get_xhs_login_state() == (False, "2026-07-07T02:03:04+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'xhs_login_state'").fetchone()[0]
        == "0"
    )


def test_zhihu_login_state_roundtrips_through_auth_state(tmp_path: Path) -> None:
    db = _db(tmp_path)

    assert db.get_zhihu_login_state() == (False, "")

    db.set_zhihu_login_state(True, when_iso="2026-07-07T03:04:05+00:00")
    assert db.get_zhihu_login_state() == (True, "2026-07-07T03:04:05+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'zhihu_login_state'").fetchone()[
            0
        ]
        == "1"
    )
    assert (
        db.conn.execute(
            "SELECT value FROM auth_state WHERE key = 'zhihu_login_state_at'"
        ).fetchone()[0]
        == "2026-07-07T03:04:05+00:00"
    )

    db.set_zhihu_login_state(False, when_iso="2026-07-07T04:05:06+00:00")
    assert db.get_zhihu_login_state() == (False, "2026-07-07T04:05:06+00:00")
    assert (
        db.conn.execute("SELECT value FROM auth_state WHERE key = 'zhihu_login_state'").fetchone()[
            0
        ]
        == "0"
    )


def test_login_state_writes_are_safe_across_concurrent_fastapi_threads(tmp_path: Path) -> None:
    """XHS and Zhihu heartbeats arrive together on runtime-stream connect."""
    db = _db(tmp_path)

    def write_login_state(index: int) -> None:
        logged_in = index % 2 == 0
        if index % 2 == 0:
            db.set_xhs_login_state(logged_in)
        else:
            db.set_zhihu_login_state(logged_in)

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(write_login_state, index) for index in range(200)]
        for future in futures:
            future.result()

    assert db.get_xhs_login_state()[1]
    assert db.get_zhihu_login_state()[1]


def test_get_recommendations_rows_carry_card_metadata_columns(tmp_path: Path) -> None:
    """Regression (issue #75): the history join must SELECT the card-metadata
    columns, otherwise /api/recommendations serializes them all as 0 even
    though content_cache has real values (stub-based endpoint tests can't
    catch a missing SQL column)."""
    db = _db(tmp_path)
    db.cache_content(
        "BV1meta",
        title="元信息视频",
        up_name="某UP",
        up_mid=12345,
        duration=3723,
        view_count=120000,
        like_count=4567,
        danmaku_count=890,
        favorite_count=321,
        comment_count=654,
        cover_url="https://example.com/cover.jpg",
        source_platform="bilibili",
        content_type="video",
        published_at="2026-07-08T06:30:00Z",
        published_label="3 天前",
        relevance_score=0.9,
    )
    db.insert_recommendation("BV1meta", confidence=0.9, expression="试试", topic="测试")

    rows = db.get_recommendations(limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["duration"] == 3723
    assert row["view_count"] == 120000
    assert row["like_count"] == 4567
    assert row["danmaku_count"] == 890
    assert row["favorite_count"] == 321
    assert row["comment_count"] == 654
    assert row["up_mid"] == 12345
    assert row["published_at"] == "2026-07-08T06:30:00Z"
    assert row["published_label"] == "3 天前"


@pytest.mark.parametrize(
    ("incoming_at", "incoming_label", "expected_at", "expected_label"),
    [
        ("", "更新后的相对时间", "2026-07-08T06:30:00Z", "更新后的相对时间"),
        ("2026-07-09T06:30:00Z", "", "2026-07-09T06:30:00Z", "旧标签"),
    ],
)
def test_content_cache_rediscovery_preserves_each_empty_publication_field_independently(
    tmp_path: Path,
    incoming_at: str,
    incoming_label: str,
    expected_at: str,
    expected_label: str,
) -> None:
    db = _db(tmp_path)
    db.cache_content(
        "BV1TIME",
        title="A",
        published_at="2026-07-08T06:30:00Z",
        published_label="旧标签",
    )
    db.cache_content(
        "BV1TIME",
        title="A",
        published_at=incoming_at,
        published_label=incoming_label,
    )

    row = db.conn.execute(
        "SELECT published_at, published_label FROM content_cache WHERE bvid='BV1TIME'"
    ).fetchone()

    assert row["published_at"] == expected_at
    assert row["published_label"] == expected_label


def test_legacy_content_tables_gain_publication_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "init.db"
    db = Database(db_path)
    db.initialize()
    db.cache_content("BV1LEGACY", title="legacy content")
    candidate = DiscoveredContent(bvid="BV1LEGACY-CANDIDATE", title="legacy candidate")
    candidate_write = discovered_content_to_candidate_write(candidate)
    db.enqueue_discovery_candidates([candidate_write])
    for table_name in ("content_cache", "discovery_candidates"):
        existing = {
            str(row["name"])
            for row in db.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name in ("published_at", "published_label"):
            if column_name in existing:
                db.conn.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
    db.conn.commit()
    db.close()

    migrated = Database(db_path)
    migrated.initialize()

    for table_name in ("content_cache", "discovery_candidates"):
        columns = {
            str(row["name"]): row
            for row in migrated.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name in ("published_at", "published_label"):
            assert columns[column_name]["notnull"] == 1
            assert columns[column_name]["dflt_value"] == "''"
    content = migrated.conn.execute(
        "SELECT title, published_at, published_label FROM content_cache WHERE bvid = ?",
        ("BV1LEGACY",),
    ).fetchone()
    assert dict(content) == {
        "title": "legacy content",
        "published_at": "",
        "published_label": "",
    }
    candidate_row = migrated.conn.execute(
        "SELECT title, published_at, published_label "
        "FROM discovery_candidates WHERE candidate_key = ?",
        (candidate_write.candidate_key,),
    ).fetchone()
    assert dict(candidate_row) == {
        "title": "legacy candidate",
        "published_at": "",
        "published_label": "",
    }
    migrated.close()


# --- recent_event_urls (cross-source dedup helper) -------------------------


def _insert_event_with_age(
    db: Database,
    *,
    event_type: str,
    url: str,
    source: str = "extension",
    age_hours: float = 1.0,
) -> int:
    metadata: dict[str, object] = {"source": source} if source else {}
    row_id = db.insert_event(
        event_type,
        url=url,
        title="title",
        context="",
        metadata=metadata,
    )
    created = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=age_hours)).isoformat(
        sep=" "
    )
    db.conn.execute("UPDATE events SET created_at = ? WHERE id = ?", (created, row_id))
    db.conn.commit()
    return row_id


def test_recent_event_urls_returns_recent_view_urls_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    recent = "https://www.bilibili.com/video/BVRECENT"
    old = "https://www.bilibili.com/video/BVOLD"
    other_type = "https://www.bilibili.com/video/BVFAV"
    _insert_event_with_age(db, event_type="view", url=recent, age_hours=1.0)
    _insert_event_with_age(db, event_type="view", url=old, age_hours=72.0)
    _insert_event_with_age(db, event_type="favorite", url=other_type, age_hours=1.0)

    urls = db.recent_event_urls(["view"], within_hours=48)

    assert urls == {recent}


def test_recent_event_urls_excludes_empty_urls(tmp_path: Path) -> None:
    db = _db(tmp_path)
    good = "https://www.bilibili.com/video/BVGOOD"
    _insert_event_with_age(db, event_type="view", url=good, age_hours=1.0)
    _insert_event_with_age(db, event_type="view", url="", age_hours=1.0)

    urls = db.recent_event_urls(["view"], within_hours=48)

    assert urls == {good}


def test_recent_event_urls_respects_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i, age in enumerate((3.0, 2.0, 1.0)):
        _insert_event_with_age(
            db,
            event_type="view",
            url=f"https://www.bilibili.com/video/BV{i}",
            age_hours=age,
        )

    urls = db.recent_event_urls(["view"], within_hours=48, limit=2)

    # Newest two only (created_at DESC ordering, SQL LIMIT applied).
    assert len(urls) == 2
    assert "https://www.bilibili.com/video/BV0" not in urls


def test_recent_event_urls_exclude_source_drops_matching_rows(tmp_path: Path) -> None:
    db = _db(tmp_path)
    extension_url = "https://www.bilibili.com/video/BVEXT"
    account_sync_url = "https://www.bilibili.com/video/BVACC"
    _insert_event_with_age(db, event_type="view", url=extension_url, source="extension")
    _insert_event_with_age(db, event_type="view", url=account_sync_url, source="account_sync")

    urls = db.recent_event_urls(["view"], within_hours=48, exclude_source="account_sync")

    assert urls == {extension_url}


# --------------------------------------------------------------------------
# Confusion objects (Phase 2)
# --------------------------------------------------------------------------


def test_insert_and_get_confusion_roundtrips(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(
        source="awareness",
        topic="解压视频",
        observation="连续看解压视频但停留很短",
        interpretation="可能是背景音而非兴趣",
        interpretation_confidence=0.4,
        evidence_refs=["note-1", "note-2"],
    )
    assert cid > 0
    row = db.get_confusion(cid)
    assert row is not None
    assert row["status"] == "open"
    assert row["topic"] == "解压视频"
    assert row["evidence_refs"] == ["note-1", "note-2"]
    assert row["held_updates"] == []


def test_list_confusions_filters_by_status(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = db.insert_confusion(topic="a")
    db.insert_confusion(topic="b")
    db.update_confusion(a, status="resolved", resolution="real_interest")
    assert {r["topic"] for r in db.list_confusions(statuses=["open"])} == {"b"}
    assert {r["topic"] for r in db.list_confusions(statuses=["resolved"])} == {"a"}


def test_claim_confusion_clarifying_atomic_single_winner(tmp_path: Path) -> None:
    db = _db(tmp_path)
    a = db.insert_confusion(topic="a")
    b = db.insert_confusion(topic="b")
    assert db.claim_confusion_clarifying(a, ask_turn_id="t1") is True
    # Second claim (different row) violates the partial unique index → False.
    assert db.claim_confusion_clarifying(b, ask_turn_id="t2") is False
    assert db.get_confusion(a)["status"] == "clarifying"
    assert db.get_confusion(b)["status"] == "open"
    # Re-claiming an already-clarifying row is a no-op False (not 'open').
    assert db.claim_confusion_clarifying(a, ask_turn_id="t3") is False


def test_claim_confusion_clarifying_cross_connection(tmp_path: Path) -> None:
    path = tmp_path / "confusion_race.db"
    db0 = Database(path)
    db0.initialize()
    a = db0.insert_confusion(topic="a")
    b = db0.insert_confusion(topic="b")

    def _claim(cid: int, turn: str) -> bool:
        db = Database(path)
        db.initialize()
        try:
            return db.claim_confusion_clarifying(cid, ask_turn_id=turn)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_claim, a, "t1")
        f2 = pool.submit(_claim, b, "t2")
        results = [f1.result(), f2.result()]

    # Exactly one connection wins the single clarifying slot.
    assert results.count(True) == 1
    clarifying = db0.list_confusions(statuses=["clarifying"])
    assert len(clarifying) == 1


def test_orphan_recovery_does_not_release_active_claim_before_turn_creation(
    tmp_path: Path,
) -> None:
    """F4: the age fence survives a two-connection claim→create interleave."""
    path = tmp_path / "confusion_orphan_interleave.db"
    creator = Database(path)
    creator.initialize()
    reconciler = Database(path)
    reconciler.initialize()
    confusion_id = creator.insert_confusion(topic="活跃创建窗口")
    turn_id = "confirmation-active-window"

    try:
        assert creator.claim_confusion_clarifying(
            confusion_id,
            ask_turn_id=turn_id,
            asked_at=datetime.now(UTC).isoformat(),
        )

        # Connection B runs recovery in the exact claim→create gap. The fresh
        # claim is active, so it cannot report a release.
        assert (
            reconciler.release_orphan_confusion_claim(
                confusion_id,
                expected_ask_turn_id=turn_id,
                minimum_age_seconds=30.0,
            )
            is False
        )

        row, created = creator.create_chat_confirmation_turn(
            turn_id=turn_id,
            session="popup",
            scope="confusion",
            ref=str(confusion_id),
            title="活跃创建窗口",
            message="",
            reply="请告诉我实际情况",
            payload={
                "type": "question",
                "kind": "confusion",
                "ref": str(confusion_id),
                "state": "clarifying",
            },
        )
        assert created is True
        assert row["turn_id"] == turn_id
        confusion = reconciler.get_confusion(confusion_id)
        assert confusion is not None
        assert confusion["status"] == "clarifying"
        assert confusion["ask_turn_id"] == turn_id
        assert reconciler.get_chat_turn(turn_id) is not None
    finally:
        reconciler.close()
        creator.close()


def test_orphan_recovery_live_turn_fence_survives_zero_age_mutation_probe(
    tmp_path: Path,
) -> None:
    """M3: an aggressive aged-claim recovery still cannot remove a live turn."""
    path = tmp_path / "confusion_orphan_live_turn.db"
    creator = Database(path)
    creator.initialize()
    reconciler = Database(path)
    reconciler.initialize()
    confusion_id = creator.insert_confusion(topic="已有提问")
    turn_id = "confirmation-live-turn"

    try:
        assert creator.claim_confusion_clarifying(
            confusion_id,
            ask_turn_id=turn_id,
            asked_at=datetime.now(UTC).isoformat(),
        )
        row, created = creator.create_chat_confirmation_turn(
            turn_id=turn_id,
            session="popup",
            scope="confusion",
            ref=str(confusion_id),
            title="已有提问",
            message="",
            reply="请告诉我实际情况",
            payload={
                "type": "question",
                "kind": "confusion",
                "ref": str(confusion_id),
                "state": "clarifying",
            },
        )
        assert created is True
        assert row["turn_id"] == turn_id
        # Zero age deliberately removes the grace-period reason for rejection:
        # this assertion depends specifically on NOT EXISTS(chat_turns), so
        # deleting the live-turn fence (M3) makes the official test fail.
        assert (
            reconciler.release_orphan_confusion_claim(
                confusion_id,
                expected_ask_turn_id=turn_id,
                minimum_age_seconds=0.0,
            )
            is False
        )
        confusion = reconciler.get_confusion(confusion_id)
        assert confusion is not None
        assert confusion["status"] == "clarifying"
        assert confusion["ask_turn_id"] == turn_id
        assert reconciler.get_chat_turn(turn_id) is not None
    finally:
        reconciler.close()
        creator.close()


def test_orphan_recovery_ask_turn_identity_survives_zero_age_mutation_probe(
    tmp_path: Path,
) -> None:
    """F4: stale recovery ownership cannot release a newer ask-turn claim."""
    path = tmp_path / "confusion_orphan_claim_identity.db"
    creator = Database(path)
    creator.initialize()
    reconciler = Database(path)
    reconciler.initialize()
    confusion_id = creator.insert_confusion(topic="新旧提问身份")
    current_turn_id = "confirmation-current-owner"
    stale_turn_id = "confirmation-stale-owner"

    try:
        assert creator.claim_confusion_clarifying(
            confusion_id,
            ask_turn_id=current_turn_id,
            asked_at=datetime.now(UTC).isoformat(),
        )
        # Zero age removes the grace-period reason for rejection and neither
        # turn exists. Only expected_ask_turn_id identity may fence this stale
        # recovery attempt, so deleting/corrupting that SQL condition turns
        # this assertion red.
        assert (
            reconciler.release_orphan_confusion_claim(
                confusion_id,
                expected_ask_turn_id=stale_turn_id,
                minimum_age_seconds=0.0,
            )
            is False
        )
        confusion = reconciler.get_confusion(confusion_id)
        assert confusion is not None
        assert confusion["status"] == "clarifying"
        assert confusion["ask_turn_id"] == current_turn_id
        assert reconciler.get_chat_turn(current_turn_id) is None
        assert reconciler.get_chat_turn(stale_turn_id) is None
    finally:
        reconciler.close()
        creator.close()


def test_update_confusion_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(topic="a")
    with pytest.raises(ValueError, match="Unknown confusion column"):
        db.update_confusion(cid, bogus="x")


def test_visual_enrichment_provenance_requeues_old_namespace_and_sampling(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    db.cache_content(
        bvid="BVPROV",
        title="视频",
        cover_url="",
        relevance_score=0.8,
        source="search",
        pool_expression="表达",
        pool_topic_label="主题",
        topic_group="组",
        style_key="tutorial",
    )
    db.mark_keyframes_fetched(
        "BVPROV",
        keyframe_count=1,
        embedding_fingerprint="old",
        embedding_dimension=3,
        sampling_signature="sample-v1|max_frames=4",
    )
    db.update_danmaku_text(
        "BVPROV",
        danmaku_text="已保存摘要",
        embedding_fingerprint="old",
        embedding_dimension=3,
    )
    assert (
        db.get_candidates_needing_keyframes(
            embedding_fingerprint="old",
            embedding_dimension=3,
            sampling_signature="sample-v1|max_frames=4",
        )
        == []
    )
    assert (
        db.get_candidates_needing_danmaku(embedding_fingerprint="old", embedding_dimension=3) == []
    )
    assert db.get_candidates_needing_keyframes(
        embedding_fingerprint="new",
        embedding_dimension=3,
        sampling_signature="sample-v1|max_frames=4",
    )
    assert db.get_candidates_needing_danmaku(embedding_fingerprint="new", embedding_dimension=3)

    db.replace_user_visual_clusters(
        [{"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 1}]
    )
    assert db.get_user_visual_clusters(embedding_fingerprint="new") == []
    db.replace_user_visual_clusters(
        [
            {
                "polarity": "pos",
                "centroid": [1.0, 0.0, 0.0],
                "member_count": 1,
            }
        ],
        embedding_fingerprint="new",
        embedding_dimension=3,
    )
    assert len(db.get_user_visual_clusters(embedding_fingerprint="new")) == 1
    db.close()


def test_visual_enrichment_filters_temporal_staleness_before_limit(tmp_path: Path) -> None:
    db = _db(tmp_path)
    common = {
        "cover_url": "",
        "source": "search",
        "source_platform": "bilibili",
        "pool_expression": "表达",
        "pool_topic_label": "主题",
        "topic_group": "组",
        "style_key": "tutorial",
    }
    db.cache_content(
        bvid="BVEXPIRED",
        title="已经过期的突发视频",
        relevance_score=0.99,
        published_at="2000-01-01T00:00:00+00:00",
        temporal_class="breaking",
        temporal_confidence=0.95,
        temporal_reason="价值依赖即时状态",
        **common,
    )
    db.cache_content(
        bvid="BVELIGIBLE",
        title="仍可处理的视频",
        relevance_score=0.70,
        **common,
    )

    assert [row["bvid"] for row in db.get_candidates_needing_keyframes(limit=1)] == ["BVELIGIBLE"]
    assert [row["bvid"] for row in db.get_candidates_needing_danmaku(limit=1)] == ["BVELIGIBLE"]
    db.close()


def test_visual_enrichment_ignores_history_and_keeps_confirmed_empty_idempotent(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    db.cache_content(
        bvid="BVSHOWN",
        title="已展示",
        cover_url="",
        relevance_score=0.8,
        source="search",
        pool_expression="表达",
        pool_topic_label="主题",
        topic_group="组",
        style_key="tutorial",
    )
    db.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown' WHERE bvid = ?",
        ("BVSHOWN",),
    )
    db.conn.commit()
    db.mark_keyframes_fetched(
        "BVSHOWN",
        keyframe_count=1,
        embedding_fingerprint="old",
        embedding_dimension=3,
        sampling_signature="old-sampling",
    )
    db.update_danmaku_text(
        "BVSHOWN",
        danmaku_text="历史摘要",
        embedding_fingerprint="old",
        embedding_dimension=3,
    )
    assert (
        db.get_candidates_needing_keyframes(
            embedding_fingerprint="new",
            embedding_dimension=3,
            sampling_signature="new-sampling",
        )
        == []
    )
    assert (
        db.get_candidates_needing_danmaku(embedding_fingerprint="new", embedding_dimension=3) == []
    )

    db.cache_content(
        bvid="BVEMPTY",
        title="确认空结果",
        cover_url="",
        relevance_score=0.8,
        source="search",
        pool_expression="表达",
        pool_topic_label="主题",
        topic_group="组",
        style_key="tutorial",
    )
    db.mark_keyframes_fetched(
        "BVEMPTY",
        keyframe_count=0,
        embedding_fingerprint="same",
        embedding_dimension=0,
        sampling_signature="old-sampling",
    )
    db.update_danmaku_text(
        "BVEMPTY",
        danmaku_text="",
        embedding_fingerprint="same",
        embedding_dimension=0,
    )
    assert (
        db.get_candidates_needing_keyframes(
            embedding_fingerprint="same",
            embedding_dimension=3,
            sampling_signature="new-sampling",
        )
        == []
    )
    assert (
        db.get_candidates_needing_danmaku(embedding_fingerprint="same", embedding_dimension=3) == []
    )
    db.close()


def test_visual_enrichment_treats_unknown_dimension_as_unknown_until_observed(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    db.cache_content(
        bvid="BVUNKNOWNDIM",
        title="未知维度",
        cover_url="",
        relevance_score=0.8,
        source="search",
        pool_expression="表达",
        pool_topic_label="主题",
        topic_group="组",
        style_key="tutorial",
    )
    db.mark_keyframes_fetched(
        "BVUNKNOWNDIM",
        keyframe_count=1,
        embedding_fingerprint="same",
        embedding_dimension=0,
        sampling_signature="sample-v1",
    )
    db.update_danmaku_text(
        "BVUNKNOWNDIM",
        danmaku_text="已有摘要",
        embedding_fingerprint="same",
        embedding_dimension=0,
    )

    # A zero stored/current dimension means unknown, not incompatible.
    assert (
        db.get_candidates_needing_keyframes(
            embedding_fingerprint="same",
            embedding_dimension=0,
            sampling_signature="sample-v1",
        )
        == []
    )
    assert (
        db.get_candidates_needing_keyframes(
            embedding_fingerprint="same",
            embedding_dimension=3,
            sampling_signature="sample-v1",
        )
        == []
    )
    assert (
        db.get_candidates_needing_danmaku(embedding_fingerprint="same", embedding_dimension=3) == []
    )

    # Once a positive dimension has actually been stored, a later positive
    # dimension change is a real vector-space mismatch. Dimension-only calls
    # must also work when no fingerprint is available yet.
    db.mark_keyframes_fetched(
        "BVUNKNOWNDIM",
        keyframe_count=1,
        embedding_fingerprint="same",
        embedding_dimension=3,
        sampling_signature="sample-v1",
    )
    db.update_danmaku_text(
        "BVUNKNOWNDIM",
        danmaku_text="已有摘要",
        embedding_fingerprint="same",
        embedding_dimension=3,
    )
    assert db.get_candidates_needing_keyframes(
        embedding_dimension=4,
        sampling_signature="sample-v1",
    )
    assert db.get_candidates_needing_danmaku(embedding_dimension=4)
    db.close()
