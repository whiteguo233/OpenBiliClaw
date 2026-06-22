"""Tests for the deficit-driven keyword fetch coordinator + word lifecycle (P1.7).

Covers the shared :class:`KeywordFetchCoordinator` (flag gate, atomic claim, the
``used`` / ``failed`` / ``executing`` terminals, budget-rejection rollback) and
the xhs-task-result lifecycle helper. Per-producer flag-on / flag-off wiring for
the five search fetch sites lives in the producer test files
(``test_xhs_producer.py`` / ``test_x_producer.py`` / ``test_douyin_producer.py``)
and the B站 controller path in ``test_refresh_runtime.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.keyword_fetch import (
    ClaimedKeyword,
    KeywordFetchCoordinator,
    mark_keyword_terminal_from_xhs_task,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _DiscoveryCfg:
    """Minimal stand-in for ``config.discovery`` (the flag + fetch_batch)."""

    unified_keyword_planner_enabled: bool = False
    fetch_batch: int = 5


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "keyword_fetch.db")
    d.initialize()
    return d


def _statuses(db: Database, platform: str = "xiaohongshu") -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT keyword, status FROM discovery_keywords WHERE platform = ? ORDER BY id",
        (platform,),
    ).fetchall()
    return {str(r["keyword"]): str(r["status"]) for r in rows}


# ── flag gate ────────────────────────────────────────────────────────────


def test_should_claim_is_false_when_flag_off(db: Database) -> None:
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(False))
    assert coord.enabled is False
    assert coord.should_claim() is False


def test_should_claim_is_true_when_flag_on(db: Database) -> None:
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    assert coord.enabled is True
    assert coord.should_claim() is True


def test_fetch_batch_reads_config_and_floors_at_one(db: Database) -> None:
    coord = KeywordFetchCoordinator(
        database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=3)
    )
    assert coord.fetch_batch == 3
    coord0 = KeywordFetchCoordinator(
        database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=0)
    )
    assert coord0.fetch_batch == 1


# ── claim ────────────────────────────────────────────────────────────────


def test_claim_returns_empty_when_store_empty(db: Database) -> None:
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    assert coord.claim("xiaohongshu") == []


def test_claim_claims_up_to_fetch_batch(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a", "b", "c", "d"], "dig")
    coord = KeywordFetchCoordinator(
        database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=2)
    )
    claimed = coord.claim("xiaohongshu")
    assert [c.keyword for c in claimed] == ["a", "b"]
    assert all(isinstance(c, ClaimedKeyword) and c.id > 0 for c in claimed)
    # The two claimed words left ``pending`` state; the rest remain pending.
    assert _statuses(db) == {"a": "claimed", "b": "claimed", "c": "pending", "d": "pending"}


def test_claim_honors_explicit_count(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a", "b", "c"], "dig")
    coord = KeywordFetchCoordinator(
        database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=5)
    )
    claimed = coord.claim("xiaohongshu", 1)
    assert [c.keyword for c in claimed] == ["a"]


# ── lifecycle terminals ───────────────────────────────────────────────────


def test_mark_used_moves_claimed_to_used(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a", "b"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 2)
    coord.mark_used(claimed)
    assert _statuses(db) == {"a": "used", "b": "used"}


def test_mark_failed_moves_claimed_to_failed(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.mark_failed(claimed)
    assert _statuses(db) == {"a": "failed"}


def test_mark_executing_moves_claimed_to_executing(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.mark_executing(claimed[0])
    assert _statuses(db) == {"a": "executing"}


def test_rollback_returns_claimed_to_pending(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.rollback(claimed[0])
    assert _statuses(db) == {"a": "pending"}
    # A rolled-back word can be re-claimed.
    again = coord.claim("xiaohongshu", 1)
    assert [c.keyword for c in again] == ["a"]


# ── xhs task-result lifecycle helper ──────────────────────────────────────


def test_xhs_terminal_helper_marks_used_on_success(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.mark_executing(claimed[0])
    payload = json.dumps({"keyword": "a", "source_keyword_id": claimed[0].id})
    mark_keyword_terminal_from_xhs_task(db, payload, success=True)
    assert _statuses(db) == {"a": "used"}


def test_xhs_terminal_helper_marks_failed_on_failure(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.mark_executing(claimed[0])
    payload = json.dumps({"keyword": "a", "source_keyword_id": claimed[0].id})
    mark_keyword_terminal_from_xhs_task(db, payload, success=False)
    assert _statuses(db) == {"a": "failed"}


def test_xhs_terminal_helper_is_noop_without_source_keyword_id(db: Database) -> None:
    db.insert_pending_keywords("xiaohongshu", ["a"], "dig")
    coord = KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True))
    claimed = coord.claim("xiaohongshu", 1)
    coord.mark_executing(claimed[0])
    # Legacy task payload (no source_keyword_id) → no keyword state change.
    mark_keyword_terminal_from_xhs_task(db, json.dumps({"keyword": "a"}), success=True)
    assert _statuses(db) == {"a": "executing"}


def test_xhs_terminal_helper_tolerates_bad_payload(db: Database) -> None:
    # Must not raise on missing / malformed payloads.
    mark_keyword_terminal_from_xhs_task(db, None, success=True)
    mark_keyword_terminal_from_xhs_task(db, "not-json", success=True)
    mark_keyword_terminal_from_xhs_task(db, json.dumps([1, 2]), success=False)


# ── disabled-DAO safety (coordinator never assumes the DAO exists) ─────────


class _BareDb:
    """A database object exposing none of the keyword-store DAO methods."""


def test_coordinator_is_inert_against_a_db_without_the_dao() -> None:
    coord = KeywordFetchCoordinator(database=_BareDb(), discovery_config=_DiscoveryCfg(True))
    assert coord.claim("xiaohongshu") == []
    # The terminal markers are no-ops, not crashes.
    item = ClaimedKeyword(id=1, keyword="a")
    coord.mark_used([item])
    coord.mark_failed([item])
    coord.mark_executing(item)
    coord.rollback(item)


def test_xhs_terminal_helper_inert_against_db_without_dao() -> None:
    payload = json.dumps({"source_keyword_id": 1})
    mark_keyword_terminal_from_xhs_task(_BareDb(), payload, success=True)  # no raise


# ── plumbing surface check ────────────────────────────────────────────────


def test_claim_passes_platform_and_count_through(db: Database) -> None:
    calls: list[tuple[str, int]] = []

    class _SpyDb:
        def claim_keywords(self, platform: str, n: int) -> list[dict[str, Any]]:
            calls.append((platform, n))
            return []

    coord = KeywordFetchCoordinator(database=_SpyDb(), discovery_config=_DiscoveryCfg(True, 7))
    coord.claim("douyin")
    coord.claim("youtube", 2)
    assert calls == [("douyin", 7), ("youtube", 2)]
