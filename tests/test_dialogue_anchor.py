"""Dialogue-anchor lifecycle contract for confirmation conversations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.soul.dialogue_anchor import (
    ENTRY_CARD_DISCUSS,
    ENTRY_CONFUSION_PROMPT,
    ENTRY_PENDING_OPEN,
    DialogueAnchorManager,
)
from openbiliclaw.soul.dialogue_insight_analyzer import DialogueInsightAnalyzer
from openbiliclaw.soul.ledger import ProfileLedger
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


_START = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "anchor.db")
    db.initialize()
    return db


def _manager(
    tmp_path: Path,
    *,
    db: Database | None = None,
    now: datetime = _START,
) -> DialogueAnchorManager:
    database = db or _database(tmp_path)
    return DialogueAnchorManager(
        tmp_path,
        database=database,
        ledger=ProfileLedger(database),
        now_provider=lambda: now,
    )


@pytest.mark.parametrize(
    "entry",
    [ENTRY_CONFUSION_PROMPT, ENTRY_CARD_DISCUSS, ENTRY_PENDING_OPEN],
)
def test_anchor_can_be_established_from_each_declared_entry(
    tmp_path: Path,
    entry: str,
) -> None:
    manager = _manager(tmp_path)

    anchor = manager.establish(
        kind="hypothesis",
        ref="abc12345",
        origin_turn_id="origin-1",
        entry=entry,
    )

    assert anchor.kind == "hypothesis"
    assert anchor.ref == "abc12345"
    assert anchor.generation == 1
    assert anchor.origin_turn_id == "origin-1"
    assert anchor.unrelated_streak == 0
    assert anchor.ambiguous_count == 0


def test_anchor_replacement_keeps_only_one_and_increments_generation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="hypothesis",
        ref="first",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    second = manager.establish(
        kind="confusion",
        ref="2",
        origin_turn_id="question-2",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    assert first.generation == 1
    assert second.generation == 2
    assert manager.current() == second
    rows = manager.database.query_profile_ledger(days=1, limit=10)
    assert [row["write_point"] for row in rows[:2]] == [
        "anchor_established",
        "anchor_released",
    ]
    assert json.loads(rows[1]["after_summary"])["reason"] == "replaced"


def test_reestablishing_identical_anchor_is_idempotent(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="hypothesis",
        ref="same",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    retried = manager.establish(
        kind="hypothesis",
        ref="same",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    assert retried == first
    assert len(manager.database.query_profile_ledger(days=1)) == 1


def test_settlement_releases_anchor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="confusion",
        ref="7",
        origin_turn_id="question-7",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    released = manager.release(reason="settled")

    assert released is not None
    assert manager.current() is None


def test_two_consecutive_unrelated_turns_release_anchor(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="topic",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    after_first = manager.note_relation("unrelated")
    after_second = manager.note_relation("unrelated")

    assert after_first is not None and after_first.unrelated_streak == 1
    assert after_second is None
    assert manager.current() is None


def test_related_turn_breaks_unrelated_streak(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="topic",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    manager.note_relation("unrelated")
    related = manager.note_relation("support")
    still_active = manager.note_relation("unrelated")

    assert related is not None and related.unrelated_streak == 0
    assert still_active is not None and still_active.unrelated_streak == 1


def test_ttl_releases_at_two_hours_but_not_before(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="ttl",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    assert manager.expire(now=_START + timedelta(hours=2) - timedelta(seconds=1)) is False
    assert manager.current() is not None
    assert manager.expire(now=_START + timedelta(hours=2)) is True
    assert manager.current() is None


@pytest.mark.parametrize("reason", ["replaced", "ttl", "unrelated"])
def test_non_settlement_confusion_release_reopens_clarifying_slot(
    tmp_path: Path,
    reason: str,
) -> None:
    db = _database(tmp_path)
    confusion_id = db.insert_confusion(topic="桌游", observation="待澄清")
    assert db.claim_confusion_clarifying(
        confusion_id,
        ask_turn_id="question",
        asked_at=_START.isoformat(),
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="confusion",
        ref=str(confusion_id),
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )

    if reason == "replaced":
        manager.establish(
            kind="hypothesis",
            ref="replacement",
            origin_turn_id="",
            entry=ENTRY_PENDING_OPEN,
        )
    elif reason == "ttl":
        assert manager.expire(now=_START + timedelta(hours=2))
    else:
        manager.note_relation("unrelated")
        manager.note_relation("unrelated")

    assert db.get_confusion(confusion_id)["status"] == "open"
    other_id = db.insert_confusion(topic="电影", observation="另一个")
    assert db.claim_confusion_clarifying(other_id, ask_turn_id="other", asked_at="now")


def test_anchor_survives_restart_with_original_ttl_clock(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    established = manager.establish(
        kind="hypothesis",
        ref="persisted",
        origin_turn_id="origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    restarted = DialogueAnchorManager(
        tmp_path,
        database=manager.database,
        ledger=ProfileLedger(manager.database),
        now_provider=lambda: _START + timedelta(hours=1),
    )

    assert restarted.current() == established
    assert not restarted.expire(now=_START + timedelta(hours=1, minutes=59))
    assert restarted.expire(now=_START + timedelta(hours=2))


def test_stale_generation_snapshot_is_dropped_with_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _manager(tmp_path)
    old = manager.establish(
        kind="hypothesis",
        ref="same-ref",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )
    assert manager.release(reason="replaced", expected_generation=old.generation) == old
    current = manager.establish(
        kind="hypothesis",
        ref=old.ref,
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    assert current.ref == old.ref
    assert current.generation == old.generation + 1
    with caplog.at_level("WARNING"):
        assert manager.validate_snapshot(old.ref, old.generation) is None

    assert manager.validate_snapshot(current.ref, current.generation) == current
    assert "stale dialogue anchor snapshot" in caplog.text


def test_settlement_patches_origin_card_terminal_state(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.create_chat_turn(
        turn_id="card-origin",
        message="聊聊这个",
        payload={"type": "card", "state": "discussing"},
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="hypothesis",
        ref="card-ref",
        origin_turn_id="card-origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    manager.release(reason="settled", card_state="confirmed")

    assert db.get_chat_turn("card-origin")["payload"]["state"] == "confirmed"


def test_non_settlement_release_returns_origin_card_to_pending(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.create_chat_turn(
        turn_id="card-origin",
        message="聊聊这个",
        payload={"type": "card", "state": "discussing"},
    )
    manager = _manager(tmp_path, db=db)
    manager.establish(
        kind="hypothesis",
        ref="card-ref",
        origin_turn_id="card-origin",
        entry=ENTRY_CARD_DISCUSS,
    )

    manager.note_relation("unrelated")
    manager.note_relation("unrelated")

    assert db.get_chat_turn("card-origin")["payload"]["state"] == "pending"


def test_ambiguous_count_accumulates_and_non_ambiguous_resets(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.establish(
        kind="hypothesis",
        ref="ambiguous",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    first = manager.note_relation("ambiguous")
    second = manager.note_relation("ambiguous")
    reset = manager.note_relation("support")

    assert first is not None and first.ambiguous_count == 1
    assert second is not None and second.ambiguous_count == 2
    assert reset is not None and reset.ambiguous_count == 0


def test_generation_remains_monotonic_after_release(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.establish(
        kind="confusion",
        ref="1",
        origin_turn_id="question",
        entry=ENTRY_CONFUSION_PROMPT,
    )
    manager.release(reason="settled")

    second = manager.establish(
        kind="hypothesis",
        ref="next",
        origin_turn_id="",
        entry=ENTRY_PENDING_OPEN,
    )

    assert second.generation == first.generation + 1


def test_no_anchor_dialogue_insight_prompt_bytes_match_pre_task_baseline() -> None:
    """Byte baseline for the no-anchor dialogue-insight prompt.

    Re-captured once, deliberately: system rule 9 now forbids turning
    "operating this product" into a taste candidate. The baseline still
    guards that the no-anchor path carries no anchor contract.
    """
    from openbiliclaw.llm.prompts import build_dialogue_insight_prompt

    messages = build_dialogue_insight_prompt(
        user_message="我最近在玩桌游",
        assistant_reply="听起来你很享受",
        core_memory={"soul": "重视深度"},
        active_list={"speculations": [{"domain": "桌游"}]},
    )
    raw = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "816cbe40eb03ac35c90d9d72bd16635e04436c9c9f5f3dd07933d7e31a9f1f50"
    )


def test_anchored_prompt_adds_contract_only_to_user_message() -> None:
    from openbiliclaw.llm.prompts import build_dialogue_insight_prompt

    base = build_dialogue_insight_prompt(
        user_message="不完全是这样",
        assistant_reply="你愿意修正一下吗？",
        core_memory={},
        active_list={},
    )
    anchored = build_dialogue_insight_prompt(
        user_message="不完全是这样",
        assistant_reply="你愿意修正一下吗？",
        core_memory={},
        active_list={},
        anchor={
            "kind": "hypothesis",
            "ref": "abcd1234",
            "text": "用户偏好深度内容",
            "generation": 2,
        },
    )

    assert anchored[0] == base[0]
    assert "<current_anchor>" in anchored[1]["content"]
    assert '"relation": "support"' in anchored[1]["content"]
    assert "归锚内容禁止重复写进 candidates" in anchored[1]["content"]


class _UnusedStructuredRegistry:
    async def complete_structured_task(self, **_: object) -> object:  # pragma: no cover
        raise AssertionError("parser tests do not call the registry")


@pytest.mark.parametrize(
    ("kind", "relation", "expected"),
    [
        ("hypothesis", "support", "support"),
        ("hypothesis", "contradict", "contradict"),
        ("hypothesis", "revise", "revise"),
        ("hypothesis", "ambiguous", "ambiguous"),
        ("hypothesis", "unrelated", "unrelated"),
        ("confusion", "answer", "answer"),
        ("confusion", "ambiguous", "ambiguous"),
        ("confusion", "unrelated", "unrelated"),
        ("hypothesis", "answer", "unrelated"),
    ],
)
def test_anchor_relation_parser_enforces_kind_matrix(
    kind: str,
    relation: str,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    analyzer = DialogueInsightAnalyzer(_UnusedStructuredRegistry())  # type: ignore[arg-type]

    with caplog.at_level("WARNING"):
        result = analyzer._parse_response(
            json.dumps(
                {
                    "candidates": [],
                    "settles": [],
                    "anchor": {
                        "relation": relation,
                        "interpretation": "real_interest",
                        "derived": [],
                    },
                }
            ),
            anchor_kind=kind,
        )

    assert result["anchor"]["relation"] == expected  # type: ignore[index]
    if relation != expected:
        assert "outside kind matrix" in caplog.text


@pytest.mark.parametrize(
    "raw_anchor",
    [None, {}, {"relation": "made_up"}, {"relation": 42}],
)
def test_bad_anchor_output_keeps_decision_empty(
    raw_anchor: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    analyzer = DialogueInsightAnalyzer(_UnusedStructuredRegistry())  # type: ignore[arg-type]

    with caplog.at_level("WARNING"):
        result = analyzer._parse_response(
            json.dumps({"candidates": [], "settles": [], "anchor": raw_anchor}),
            anchor_kind="hypothesis",
        )

    assert result["anchor"] is None
    assert "anchor decision dropped" in caplog.text
