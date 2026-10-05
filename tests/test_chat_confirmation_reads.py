"""Batch confirmation reads preserve the existing per-reference contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_batch_active_refs_match_individual_confirmation_reads(tmp_path: Path) -> None:
    database = Database(tmp_path / "confirmation-reads.db")
    database.initialize()
    cases = [
        ("pending", "popup", "hypothesis", "card", "pending", "completed", "pending"),
        ("discussing", "popup", "hypothesis", "card", "discussing", "completed", "discussing"),
        ("settled", "popup", "hypothesis", "card", "confirmed", "completed", "settled"),
        ("question", "popup", "confusion", "question", "", "completed", "question"),
        ("wrong-session", "desktop", "hypothesis", "card", "pending", "completed", "wrong-session"),
        ("wrong-scope", "popup", "chat", "card", "pending", "completed", "wrong-scope"),
        ("mismatch", "popup", "hypothesis", "card", "pending", "completed", "other-ref"),
        ("unfinished", "popup", "hypothesis", "card", "pending", "pending", "unfinished"),
    ]
    try:
        for ref, session, scope, kind, state, status, payload_ref in cases:
            database.create_chat_turn(
                turn_id=ref,
                session=session,
                scope=scope,
                subject_id=ref,
                message="test",
                payload={"type": kind, "ref": payload_ref, "state": state},
            )
            if status == "completed":
                database.complete_chat_turn(ref, reply="test")
        for session in ("popup", "desktop"):
            expected = {
                ref
                for ref, *_rest in cases
                if database.get_chat_confirmation_turn(ref=ref, session=session) is not None
            }
            assert database.get_chat_confirmation_refs(session=session) == expected
        assert database.get_chat_confirmation_refs(session="popup") == {
            "pending",
            "discussing",
            "question",
        }
    finally:
        database.close()
