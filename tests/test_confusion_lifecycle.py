"""Confusion object lifecycle tests (Phase 2 — Wave B).

Covers the confusion state machine, the two producing sources (awareness +
speculation stalemate), the DB-level clarifying budget, TTL expiry, and (Task 5)
the ask/three-exit clarification paths, topic freeze, and held-update replay
state machine with crash-recovery idempotency.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
from openbiliclaw.soul.confusion import (
    MAX_CONFUSION_CANDIDATES_PER_ROUND,
    Confusion,
    ConfusionManager,
    HeldUpdate,
)
from openbiliclaw.soul.dialogue_anchor import (
    ENTRY_CONFUSION_PROMPT,
    ENTRY_PENDING_OPEN,
    DialogueAnchorManager,
)
from openbiliclaw.soul.ledger import ProfileLedger
from openbiliclaw.soul.speculator import (
    SpeculativeInterest,
    _stalemate_from_rejected,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "confusion.db")
    db.initialize()
    return db


# --------------------------------------------------------------------------
# Producing source 1: awareness candidates
# --------------------------------------------------------------------------


def test_create_from_awareness_candidates_persists_and_validates(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    ids = mgr.create_from_awareness_candidates(
        [
            {
                "topic": "解压视频",
                "observation": "连续点开但停留很短",
                "interpretation": "可能是背景音",
                "interpretation_confidence": 0.3,
                "evidence_refs": ["note-1"],
            },
            {"observation": "", "topic": "空的"},  # dropped: no observation
        ]
    )
    assert len(ids) == 1
    stored = mgr.get(ids[0])
    assert stored is not None
    assert stored.topic == "解压视频"
    assert stored.status == "open"
    assert stored.evidence_refs == ["note-1"]


def test_create_from_awareness_candidates_caps_batch(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    cands = [
        {"observation": f"obs-{i}", "topic": f"t{i}"}
        for i in range(MAX_CONFUSION_CANDIDATES_PER_ROUND + 3)
    ]
    ids = mgr.create_from_awareness_candidates(cands)
    assert len(ids) == MAX_CONFUSION_CANDIDATES_PER_ROUND


def test_create_from_awareness_candidates_dedups_near_duplicate(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    first = mgr.create_from_awareness_candidates(
        [
            {
                "topic": "城市菜市场与社区商业观察",
                "observation": "对同城菜市场视频反复停留但从不购买，可能是社区观察型兴趣",
                "interpretation": "可能是弱兴趣",
                "interpretation_confidence": 0.4,
                "evidence_refs": ["note-a"],
            }
        ]
    )
    second = mgr.create_from_awareness_candidates(
        [
            {
                "topic": "城市菜市场与社区商业的观察",
                "observation": "对同城菜市场视频反复停留但从不购买，可能属于社区观察型兴趣",
                "interpretation": "可能还是弱兴趣",
                "interpretation_confidence": 0.45,
                "evidence_refs": ["note-b"],
            }
        ]
    )

    assert len(first) == 1
    assert second == []
    stored = mgr.get(first[0])
    assert stored is not None
    assert stored.source == "awareness"


def test_list_for_generation_context_includes_history(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    candidate = {
        "topic": "菜市场观察",
        "observation": "对菜市场视频反复停留但从不购买",
        "interpretation": "可能是弱兴趣",
        "interpretation_confidence": 0.4,
        "evidence_refs": [],
    }
    created = mgr.create_from_awareness_candidates([candidate])
    assert len(created) == 1
    cid = created[0]
    mgr.get(cid)
    mgr._db.update_confusion(cid, status="resolved")

    context = mgr.list_for_generation_context()

    assert any(item["id"] == cid for item in context)
    assert any(item["status"] == "resolved" for item in context)
    assert any("菜市场观察" in item["topic"] for item in context)


# --------------------------------------------------------------------------
# Producing source 2: speculation stalemate
# --------------------------------------------------------------------------


def test_stalemate_filter_only_partial_confirmations() -> None:
    zero = SpeculativeInterest(domain="a", confirmation_count=0, confirmation_threshold=3)
    partial = SpeculativeInterest(domain="b", confirmation_count=2, confirmation_threshold=3)
    at_thresh = SpeculativeInterest(domain="c", confirmation_count=3, confirmation_threshold=3)
    result = _stalemate_from_rejected([zero, partial, at_thresh])
    assert [s.domain for s in result] == ["b"]


def test_create_from_speculation_stalemate_persists(tmp_path: Path) -> None:
    mgr = ConfusionManager(_db(tmp_path))
    cid = mgr.create_from_speculation_stalemate(
        domain="桌游",
        confirmation_count=2,
        confirmation_threshold=3,
    )
    assert cid is not None
    stored = mgr.get(cid)
    assert stored is not None
    assert stored.source == "speculation_stalemate"
    assert stored.topic == "桌游"


# --------------------------------------------------------------------------
# Awareness analyzer parse of {"notes", "confusions"}
# --------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete_structured_task(self, **kwargs: object):  # type: ignore[no-untyped-def]
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content=self._content, model="fake", provider="fake")


async def test_analyze_with_confusions_parses_both() -> None:
    payload = """
    {
      "notes": [
        {"date": "2026-07-17", "observation": "看深度内容",
         "trend": "偏深度", "emotion_guess": "专注"}
      ],
      "confusions": [
        {"topic": "解压", "observation": "停留很短", "interpretation": "背景音",
         "interpretation_confidence": 0.3, "evidence_refs": []},
        {"observation": "", "topic": "空"}
      ]
    }
    """
    analyzer = AwarenessAnalyzer(_FakeRegistry(payload))
    notes, confusions = await analyzer.analyze_with_confusions(
        events=[{"id": 5, "event_type": "view", "title": "x"}],
        preference={},
        soul_profile={},
    )
    assert len(notes) == 1
    assert notes[0].source_event_ids == [5]
    assert len(confusions) == 1  # empty-observation dropped
    assert confusions[0]["topic"] == "解压"


async def test_analyze_with_confusions_tolerates_legacy_array() -> None:
    payload = '[{"observation": "只有笔记", "date": "2026-07-17"}]'
    analyzer = AwarenessAnalyzer(_FakeRegistry(payload))
    notes, confusions = await analyzer.analyze_with_confusions(
        events=[], preference={}, soul_profile={}
    )
    assert len(notes) == 1
    assert confusions == []


# --------------------------------------------------------------------------
# TTL expiry
# --------------------------------------------------------------------------


def test_expire_due_expires_past_ttl(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    past = (datetime.now() - timedelta(days=1)).isoformat()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    stale = db.insert_confusion(topic="stale", observation="x", expires_at=past)
    fresh = db.insert_confusion(topic="fresh", observation="y", expires_at=future)
    no_ttl = db.insert_confusion(topic="notl", observation="z")
    expired = mgr.expire_due()
    assert expired == [stale]
    assert mgr.get(stale).status == "expired"
    assert mgr.get(fresh).status == "open"
    assert mgr.get(no_ttl).status == "open"


# --------------------------------------------------------------------------
# Dataclass round-trip
# --------------------------------------------------------------------------


def test_confusion_from_row_decodes_held_updates(tmp_path: Path) -> None:
    db = _db(tmp_path)
    cid = db.insert_confusion(topic="a", observation="x")
    held = HeldUpdate(held_id="h1", topic="a", kind="upgrade", value=0.8, prev_value=0.5)
    db.update_confusion(cid, held_updates=[held.to_dict()])
    confusion = Confusion.from_row(db.get_confusion(cid))
    assert len(confusion.held_updates) == 1
    assert confusion.held_updates[0].held_id == "h1"
    assert confusion.held_updates[0].kind == "upgrade"


def test_confusion_replay_queue_migrates_legacy_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy-confusion.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE confusions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'open',
            source TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            observation TEXT NOT NULL DEFAULT '',
            interpretation TEXT NOT NULL DEFAULT '',
            interpretation_confidence REAL NOT NULL DEFAULT 0.0,
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            resolution TEXT NOT NULL DEFAULT '',
            resolution_note TEXT NOT NULL DEFAULT '',
            asked_at TIMESTAMP,
            ask_turn_id TEXT NOT NULL DEFAULT '',
            defer_count INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP,
            held_updates TEXT NOT NULL DEFAULT '[]',
            resolved_at TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(confusions)")}
    assert "replay_queue" in columns
    confusion_id = db.insert_confusion(topic="迁移", observation="仍需澄清")
    assert db.get_confusion(confusion_id)["replay_queue"] == []


def test_anchor_settlement_replay_is_fifo_and_failure_stops_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    manager = ConfusionManager(db, ledger=ProfileLedger(db))
    confusion_id = db.insert_confusion(topic="桌游", observation="待澄清")
    calls: list[str] = []
    failures_left = 2
    real_resolve = manager.resolve

    def flaky_resolve(
        target_id: int,
        *,
        resolution: str,
        note: str = "",
        now: datetime | None = None,
    ) -> str | None:
        nonlocal failures_left
        calls.append(note)
        if note == "T1" and failures_left:
            failures_left -= 1
            raise RuntimeError("injected settlement failure")
        return real_resolve(target_id, resolution=resolution, note=note, now=now)

    monkeypatch.setattr(manager, "resolve", flaky_resolve)

    assert (
        manager.process_anchor_settlement(
            confusion_id,
            action="resolve",
            interpretation="real_interest",
            note="T1",
            turn_id="turn-1",
            anchor_generation=1,
        )
        is None
    )
    assert [item["turn_id"] for item in manager.get(confusion_id).replay_queue] == ["turn-1"]

    assert (
        manager.process_anchor_settlement(
            confusion_id,
            action="resolve",
            interpretation="proxy_behavior",
            note="T2",
            turn_id="turn-2",
            anchor_generation=1,
        )
        is None
    )
    assert calls == ["T1", "T1"]
    assert [item["turn_id"] for item in manager.get(confusion_id).replay_queue] == [
        "turn-1",
        "turn-2",
    ]

    assert manager.retry_anchor_settlements(confusion_id) == "resolved"
    assert calls == ["T1", "T1", "T1", "T2"]
    assert manager.get(confusion_id).replay_queue == []


def test_confusion_replay_queue_caps_at_five_and_ledgers_oldest_drop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    manager = ConfusionManager(db, ledger=ProfileLedger(db))
    confusion_id = db.insert_confusion(topic="故障队列", observation="持续失败")

    def always_fail(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise RuntimeError("injected permanent failure")

    monkeypatch.setattr(manager, "resolve", always_fail)
    for index in range(6):
        assert (
            manager.process_anchor_settlement(
                confusion_id,
                action="resolve",
                interpretation="real_interest",
                note=f"T{index + 1}",
                turn_id=f"turn-{index + 1}",
                anchor_generation=1,
            )
            is None
        )

    assert [item["turn_id"] for item in manager.get(confusion_id).replay_queue] == [
        "turn-2",
        "turn-3",
        "turn-4",
        "turn-5",
        "turn-6",
    ]
    dropped = [
        row
        for row in db.query_profile_ledger(days=1, limit=50)
        if row["write_point"] == "confusion_replay_dropped"
    ]
    assert len(dropped) == 1
    assert dropped[0]["turn_id"] == "turn-1"


def test_replay_queue_survives_restart_and_head_pop_is_fenced(tmp_path: Path) -> None:
    path = tmp_path / "persistent-replay.db"
    db = Database(path)
    db.initialize()
    confusion_id = db.insert_confusion(topic="持久化", observation="等待重放")
    for turn_id in ("turn-1", "turn-2"):
        db.enqueue_confusion_replay(
            confusion_id,
            {
                "replay_id": turn_id,
                "turn_id": turn_id,
                "action": "resolve",
                "interpretation": "real_interest",
                "anchor_generation": 1,
            },
        )
    assert not db.pop_confusion_replay_head(confusion_id, expected_id="turn-2")
    db.close()

    restarted = Database(path)
    restarted.initialize()
    assert [item["turn_id"] for item in restarted.get_confusion(confusion_id)["replay_queue"]] == [
        "turn-1",
        "turn-2",
    ]
    assert restarted.pop_confusion_replay_head(confusion_id, expected_id="turn-1")
    assert [item["turn_id"] for item in restarted.get_confusion(confusion_id)["replay_queue"]] == [
        "turn-2"
    ]


def test_terminal_confusion_with_unpopped_head_is_recovered_by_fallback_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _db(tmp_path)
    manager = ConfusionManager(db, ledger=ProfileLedger(db))
    confusion_id = db.insert_confusion(topic="崩溃窗", observation="resolve 已提交但尚未 pop")
    assert db.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at="2026-07-22T01:00:00+00:00",
    )
    db.enqueue_confusion_replay(
        confusion_id,
        {
            "replay_id": "crash-turn",
            "turn_id": "crash-turn",
            "action": "resolve",
            "interpretation": "real_interest",
            "note": "commit-before-pop",
            "anchor_generation": 1,
        },
    )

    def crash_after_resolve(_confusion_id: int, *, expected_id: str) -> bool:
        del expected_id
        assert db.get_confusion(_confusion_id)["status"] == "resolved"
        raise RuntimeError("crash after resolve commit")

    monkeypatch.setattr(db, "pop_confusion_replay_head", crash_after_resolve)
    with pytest.raises(RuntimeError, match="crash after resolve commit"):
        manager.retry_anchor_settlements(confusion_id)

    crashed = db.get_confusion(confusion_id)
    assert crashed["status"] == "resolved"
    assert [item["turn_id"] for item in crashed["replay_queue"]] == ["crash-turn"]

    # Model an actual process crash/restart: discard the connection and recover
    # through a fresh Database + manager, not the object that injected failure.
    db.close()
    restarted_db = Database(tmp_path / "confusion.db")
    restarted_db.initialize()
    restarted = ConfusionManager(restarted_db, ledger=ProfileLedger(restarted_db))
    pending = restarted.pending_dialogue_replays()
    assert len(pending) == 1
    assert pending[0]["confusion_id"] == confusion_id
    assert pending[0]["has_replay_queue"] == 1

    assert restarted.retry_anchor_settlements(confusion_id) == "resolved"
    assert restarted.get(confusion_id).replay_queue == []


@pytest.mark.parametrize("reason", ["replaced", "ttl", "settled", "unrelated"])
def test_every_anchor_release_clears_confusion_replay_queue_with_ledger(
    tmp_path: Path,
    reason: str,
) -> None:
    db = _db(tmp_path)
    ledger = ProfileLedger(db)
    confusion_id = db.insert_confusion(topic="释放", observation="有待重放")
    assert db.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at="2026-07-22T01:00:00+00:00",
    )
    db.enqueue_confusion_replay(
        confusion_id,
        {
            "turn_id": "queued-turn",
            "action": "resolve",
            "interpretation": "real_interest",
            "note": "queued",
            "anchor_generation": 1,
        },
    )
    anchor_manager = DialogueAnchorManager(tmp_path, database=db, ledger=ledger)
    anchor_manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    if reason == "replaced":
        anchor_manager.establish(
            kind="hypothesis",
            ref="replacement",
            origin_turn_id="",
            entry=ENTRY_PENDING_OPEN,
        )
    elif reason == "ttl":
        current = anchor_manager.current()
        assert current is not None
        anchor_manager.expire(
            now=datetime.fromisoformat(current.established_at) + timedelta(hours=2)
        )
    elif reason == "settled":
        anchor_manager.release(reason="settled")
    else:
        anchor_manager.note_relation("unrelated")
        anchor_manager.note_relation("unrelated")

    assert db.get_confusion(confusion_id)["replay_queue"] == []
    dropped = [
        row
        for row in db.query_profile_ledger(days=1, limit=50)
        if row["write_point"] == "confusion_replay_dropped"
    ]
    assert len(dropped) == 1
    assert reason in dropped[0]["after_summary"]


# --------------------------------------------------------------------------
# Task 5: ask scheduling + 72h cooldown
# --------------------------------------------------------------------------


def test_schedule_ask_claims_and_enforces_cooldown(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    now = datetime(2026, 7, 17, 12, 0, 0)
    assert mgr.schedule_ask(cid, ask_turn_id="t1", now=now) is True
    assert mgr.get(cid).status == "clarifying"
    # Defer releases the slot but keeps asked_at (cooldown persists).
    mgr.defer(cid, now=now)
    assert mgr.get(cid).status == "open"
    # Re-ask within 72h is refused.
    soon = now + timedelta(hours=10)
    assert mgr.schedule_ask(cid, ask_turn_id="t2", now=soon) is False
    # After 72h it is allowed again.
    later = now + timedelta(hours=73)
    assert mgr.schedule_ask(cid, ask_turn_id="t3", now=later) is True


def test_schedule_ask_single_clarifying_budget(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    a = db.insert_confusion(topic="a", observation="x")
    b = db.insert_confusion(topic="b", observation="y")
    assert mgr.schedule_ask(a, ask_turn_id="t1") is True
    # Second ask blocked by the single clarifying slot.
    assert mgr.schedule_ask(b, ask_turn_id="t2") is False


def test_cooldown_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "cooldown.db"
    db = Database(path)
    db.initialize()
    mgr = ConfusionManager(db)
    now = datetime(2026, 7, 17, 12, 0, 0)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.schedule_ask(cid, ask_turn_id="t1", now=now) is True
    mgr.defer(cid, now=now)
    db.close()
    # Fresh connection (restart) — cooldown still blocks.
    db2 = Database(path)
    db2.initialize()
    mgr2 = ConfusionManager(db2)
    assert mgr2.schedule_ask(cid, ask_turn_id="t2", now=now + timedelta(hours=1)) is False


# --------------------------------------------------------------------------
# Task 5: three resolution exits
# --------------------------------------------------------------------------


def test_resolve_real_interest_begins_held_replay(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="桌游", observation="x")
    held = HeldUpdate(held_id="h1", topic="桌游", kind="upgrade", value=0.8, prev_value=0.4)
    db.update_confusion(cid, held_updates=[held.to_dict()])
    assert mgr.resolve(cid, resolution="real_interest") == "resolved"
    restored = mgr.get(cid)
    assert restored.status == "resolved"
    only = restored.held_updates[0]
    assert only.state == "replaying"
    assert only.replay_submitted_at  # receipt persisted in the same txn
    assert only.batch_id


def test_resolve_proxy_discards_held(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    db.update_confusion(cid, held_updates=[HeldUpdate(held_id="h1", topic="a").to_dict()])
    assert mgr.resolve(cid, resolution="proxy_behavior") == "resolved"
    assert mgr.get(cid).held_updates[0].state == "discarded"


def test_resolve_dismissed_status_and_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.resolve(cid, resolution="dismissed") == "dismissed"
    # Idempotent — a terminal confusion is not re-resolved.
    assert mgr.resolve(cid, resolution="real_interest") == "dismissed"
    mgr.defer(cid)
    assert mgr.get(cid).status == "dismissed"


def test_resolve_rejects_bad_resolution(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    assert mgr.resolve(cid, resolution="bogus") is None
    assert mgr.get(cid).status == "open"


# --------------------------------------------------------------------------
# Task 5: topic freeze
# --------------------------------------------------------------------------


def test_apply_confusion_freeze_noop_when_no_frozen_topics() -> None:
    from openbiliclaw.soul.confusion import apply_confusion_freeze

    before = {"interests": [{"name": "a", "category": "c", "weight": 0.3}]}
    after = {"interests": [{"name": "a", "category": "c", "weight": 0.9}]}
    result, held = apply_confusion_freeze(before=before, after=after, frozen_topics=set())
    assert result is after
    assert held == []


def test_apply_confusion_freeze_holds_new_and_upgrade() -> None:
    from openbiliclaw.soul.confusion import apply_confusion_freeze

    before = {"interests": [{"name": "frozen", "category": "c", "weight": 0.3}]}
    after = {
        "interests": [
            {"name": "frozen", "category": "c", "weight": 0.9},  # upgrade → hold delta
            {"name": "newfrozen", "category": "c", "weight": 0.7},  # new → hold entirely
            {"name": "normal", "category": "c", "weight": 0.5},  # passes through
        ]
    }
    result, held = apply_confusion_freeze(
        before=before, after=after, frozen_topics={"frozen", "newfrozen"}
    )
    names = {i["name"]: i["weight"] for i in result["interests"]}
    assert names["frozen"] == 0.3  # existing weight preserved (not rolled forward)
    assert "newfrozen" not in names  # new frozen topic dropped from the write
    assert names["normal"] == 0.5
    kinds = {h.topic: h.kind for h in held}
    assert kinds == {"frozen": "upgrade", "newfrozen": "new"}


def test_record_held_updates_attaches_to_active_confusion(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="frozen", observation="x")
    mgr.record_held_updates(
        [HeldUpdate(held_id="h1", topic="frozen", kind="upgrade", value=0.9, prev_value=0.3)]
    )
    stored = mgr.get(cid)
    assert len(stored.held_updates) == 1
    assert stored.held_updates[0].held_id == "h1"


def test_frozen_topics_reflects_active_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    db.insert_confusion(topic="open_topic", observation="x")
    resolved = db.insert_confusion(topic="done_topic", observation="y")
    mgr.resolve(resolved, resolution="dismissed")
    assert mgr.frozen_topics() == {"open_topic"}


# --------------------------------------------------------------------------
# Task 5: held-update replay recovery (crash idempotency, r5/R4-1)
# --------------------------------------------------------------------------


def test_recover_replaying_with_receipt_marks_applied_unverified(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    held = HeldUpdate(held_id="h1", topic="a")
    db.update_confusion(cid, held_updates=[held.to_dict()])
    mgr.resolve(cid, resolution="real_interest")  # → replaying + receipt
    # Simulate crash: applied never ran. Recovery must NOT resubmit.
    touched = mgr.recover_replaying()
    assert cid in touched
    only = mgr.get(cid).held_updates[0]
    assert only.state == "applied_unverified"


def test_recover_replaying_no_receipt_retries_then_discards(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    # Defensive branch: replaying without a receipt (should not normally happen).
    held = HeldUpdate(held_id="h1", topic="a", state="replaying", replay_attempts=1)
    db.update_confusion(
        cid, status="resolved", resolution="real_interest", held_updates=[held.to_dict()]
    )
    mgr.recover_replaying()
    assert mgr.get(cid).held_updates[0].state == "held"  # retry (attempts 1 < 2)
    # Now at the attempt ceiling → discarded.
    held2 = HeldUpdate(held_id="h1", topic="a", state="replaying", replay_attempts=2)
    db.update_confusion(cid, held_updates=[held2.to_dict()])
    mgr.recover_replaying()
    assert mgr.get(cid).held_updates[0].state == "discarded"


def test_mark_replay_applied_transitions(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    db.update_confusion(cid, held_updates=[HeldUpdate(held_id="h1", topic="a").to_dict()])
    mgr.resolve(cid, resolution="real_interest")
    mgr.mark_replay_applied(cid)
    assert mgr.get(cid).held_updates[0].state == "applied"


def test_pending_replays_lists_only_replaying(tmp_path: Path) -> None:
    db = _db(tmp_path)
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="a", observation="x")
    db.update_confusion(cid, held_updates=[HeldUpdate(held_id="h1", topic="a").to_dict()])
    assert mgr.pending_replays() == []  # still open, nothing replaying
    mgr.resolve(cid, resolution="real_interest")  # → replaying
    assert [c.id for c in mgr.pending_replays()] == [cid]
    mgr.mark_replay_applied(cid)  # → applied
    assert mgr.pending_replays() == []


# --------------------------------------------------------------------------
# Leftover wiring 2: proxy-behaviour evidence discount
# --------------------------------------------------------------------------


def test_resolve_proxy_discounts_evidence_events(tmp_path: Path) -> None:
    import json

    db = _db(tmp_path)
    eid = db.insert_event("view", title="解压视频", metadata={"signal_strength": 0.9})
    other = db.insert_event("view", title="别的", metadata={"signal_strength": 0.8})
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(
        topic="解压", observation="x", evidence_refs=[str(eid), "note-not-an-id"]
    )
    assert mgr.resolve(cid, resolution="proxy_behavior") == "resolved"
    # Read the patched event metadata back directly.
    conn = db.open_connection()
    try:
        patched = conn.execute("SELECT metadata FROM events WHERE id = ?", (eid,)).fetchone()
        untouched = conn.execute("SELECT metadata FROM events WHERE id = ?", (other,)).fetchone()
    finally:
        conn.close()
    meta = json.loads(patched["metadata"])
    assert meta["discounted_by_confusion"] is True
    assert meta["signal_strength"] == 0.2
    # An unrelated event is untouched.
    assert "discounted_by_confusion" not in json.loads(untouched["metadata"])


def test_resolve_real_interest_does_not_discount_evidence(tmp_path: Path) -> None:
    import json

    db = _db(tmp_path)
    eid = db.insert_event("view", title="桌游", metadata={"signal_strength": 0.9})
    mgr = ConfusionManager(db)
    cid = db.insert_confusion(topic="桌游", observation="x", evidence_refs=[str(eid)])
    mgr.resolve(cid, resolution="real_interest")
    conn = db.open_connection()
    try:
        patched = conn.execute("SELECT metadata FROM events WHERE id = ?", (eid,)).fetchone()
    finally:
        conn.close()
    assert "discounted_by_confusion" not in json.loads(patched["metadata"])
