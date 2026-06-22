"""Tests for the unified discovery-keyword store + planner single-flight lock.

Covers the P1.1 backpressure-refactor DAO on ``Database``:
``discovery_keywords`` (atomic claim, lease reclaim, partial-unique
re-generation, digest expiry, history dedup, sparse recycle) and the
``discovery_planner_lock`` CAS single-flight lock.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_BILI = "bilibili"
_XHS = "xiaohongshu"
_DIGEST_A = "digest-aaaa"
_DIGEST_B = "digest-bbbb"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


def _backdate(db: Database, keyword_id: int, column: str, *, minutes_ago: float) -> None:
    """Rewind a timestamp column on one keyword row so lease tests can fire."""
    assert column in {"claimed_at", "executing_at", "used_at", "created_at"}
    ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    db.conn.execute(
        f"UPDATE discovery_keywords SET {column} = ? WHERE id = ?",  # noqa: S608 - fixed column set
        (ts, keyword_id),
    )
    db.conn.commit()


def _status(db: Database, keyword_id: int) -> str:
    row = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE id = ?", (keyword_id,)
    ).fetchone()
    assert row is not None
    return str(row["status"])


class TestKeywordStoreBasics:
    def test_table_and_lock_table_exist(self, db: Database) -> None:
        names = {
            str(row["name"])
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "discovery_keywords" in names
        assert "discovery_planner_lock" in names

    def test_insert_pending_dedupes_blank_and_repeat_inputs(self, db: Database) -> None:
        inserted = db.insert_pending_keywords(
            _BILI, ["洛克王国", "洛克王国", "  ", "赛尔号", ""], _DIGEST_A
        )
        assert inserted == 2
        assert db.count_pending_keywords(_BILI, _DIGEST_A) == 2

    def test_count_pending_is_digest_scoped(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["a", "b"], _DIGEST_A)
        db.insert_pending_keywords(_BILI, ["c"], _DIGEST_B)
        assert db.count_pending_keywords(_BILI, _DIGEST_A) == 2
        assert db.count_pending_keywords(_BILI, _DIGEST_B) == 1


class TestAtomicClaim:
    def test_two_claims_over_same_pending_set_are_disjoint(self, db: Database) -> None:
        # 6 pending; two callers each ask for 4 → no row claimed twice.
        db.insert_pending_keywords(_BILI, [f"kw{i}" for i in range(6)], _DIGEST_A)

        first = db.claim_keywords(_BILI, 4)
        second = db.claim_keywords(_BILI, 4)

        first_ids = {row["id"] for row in first}
        second_ids = {row["id"] for row in second}
        assert len(first) == 4
        assert len(second) == 2  # only 2 left after the first claim
        assert first_ids.isdisjoint(second_ids)
        assert all(row["status"] == "claimed" for row in (*first, *second))
        # Every pending row was claimed exactly once; none remain pending.
        assert db.count_pending_keywords(_BILI, _DIGEST_A) == 0

    def test_claim_sets_claimed_at(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["only"], _DIGEST_A)
        claimed = db.claim_keywords(_BILI, 5)
        assert len(claimed) == 1
        assert claimed[0]["claimed_at"] is not None

    def test_claim_zero_or_empty_returns_empty(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["x"], _DIGEST_A)
        assert db.claim_keywords(_BILI, 0) == []
        assert db.claim_keywords(_XHS, 5) == []  # different platform, nothing pending

    def test_claim_is_fifo_oldest_pending_first(self, db: Database) -> None:
        """P2.3: ``claim_keywords`` claims the OLDEST pending rows first so
        generated words rotate through fairly. With distinct ``created_at``
        stamps the order is unambiguous; a partial claim leaves the newest."""
        # Three words inserted; rewind created_at so "oldest" is well-defined
        # (a single batch shares a second-granularity CURRENT_TIMESTAMP).
        db.insert_pending_keywords(_BILI, ["oldest", "middle", "newest"], _DIGEST_A)
        ids = {
            str(r["keyword"]): int(r["id"])
            for r in db.conn.execute(
                "SELECT id, keyword FROM discovery_keywords WHERE platform = ?", (_BILI,)
            ).fetchall()
        }
        _backdate(db, ids["oldest"], "created_at", minutes_ago=30)
        _backdate(db, ids["middle"], "created_at", minutes_ago=20)
        _backdate(db, ids["newest"], "created_at", minutes_ago=10)

        # Claim two → must be the two oldest, in oldest-first order.
        first_two = [str(r["keyword"]) for r in db.claim_keywords(_BILI, 2)]
        assert first_two == ["oldest", "middle"]
        # The newest stays pending and is claimed last.
        remaining = [str(r["keyword"]) for r in db.claim_keywords(_BILI, 2)]
        assert remaining == ["newest"]


class TestLifecycleTransitions:
    def test_used_only_from_inflight(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["w"], _DIGEST_A)
        [row] = db.claim_keywords(_BILI, 1)
        db.mark_keyword_used(int(row["id"]))
        assert _status(db, int(row["id"])) == "used"
        assert (
            db.conn.execute(
                "SELECT used_at FROM discovery_keywords WHERE id = ?", (row["id"],)
            ).fetchone()["used_at"]
            is not None
        )

    def test_executing_then_used(self, db: Database) -> None:
        db.insert_pending_keywords(_XHS, ["w"], _DIGEST_A)
        [row] = db.claim_keywords(_XHS, 1)
        db.mark_keyword_executing(int(row["id"]))
        assert _status(db, int(row["id"])) == "executing"
        db.mark_keyword_used(int(row["id"]))
        assert _status(db, int(row["id"])) == "used"

    def test_failed_bumps_attempts_and_returns_count(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["w"], _DIGEST_A)
        [row] = db.claim_keywords(_BILI, 1)
        attempts = db.mark_keyword_failed(int(row["id"]))
        assert attempts == 1
        assert _status(db, int(row["id"])) == "failed"

    def test_rollback_claimed_to_pending(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["w"], _DIGEST_A)
        [row] = db.claim_keywords(_BILI, 1)
        db.rollback_keyword_to_pending(int(row["id"]))
        assert _status(db, int(row["id"])) == "pending"
        assert (
            db.conn.execute(
                "SELECT claimed_at FROM discovery_keywords WHERE id = ?", (row["id"],)
            ).fetchone()["claimed_at"]
            is None
        )
        # Rollback only applies to claimed; an executing row is untouched.
        [row2] = db.claim_keywords(_BILI, 1)
        db.mark_keyword_executing(int(row2["id"]))
        db.rollback_keyword_to_pending(int(row2["id"]))
        assert _status(db, int(row2["id"])) == "executing"


class TestLeaseReclaim:
    def test_stale_claimed_and_executing_return_to_pending(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["a", "b", "c"], _DIGEST_A)
        claimed = db.claim_keywords(_BILI, 3)
        stale_claimed, fresh_claimed, to_execute = claimed
        db.mark_keyword_executing(int(to_execute["id"]))

        # Stale: claimed 30m ago (lease 10m) and executing 60m ago (timeout 30m).
        _backdate(db, int(stale_claimed["id"]), "claimed_at", minutes_ago=30)
        _backdate(db, int(to_execute["id"]), "executing_at", minutes_ago=60)
        # fresh_claimed stays recent → must NOT be reclaimed.

        reclaimed = db.reclaim_leased_keywords(claim_lease_minutes=10, executing_timeout_minutes=30)
        assert reclaimed == 2
        assert _status(db, int(stale_claimed["id"])) == "pending"
        assert _status(db, int(to_execute["id"])) == "pending"
        assert _status(db, int(fresh_claimed["id"])) == "claimed"
        # Reclaimed rows had their lease stamps cleared.
        assert (
            db.conn.execute(
                "SELECT claimed_at, executing_at FROM discovery_keywords WHERE id = ?",
                (stale_claimed["id"],),
            ).fetchone()["claimed_at"]
            is None
        )


class TestPartialUnique:
    def test_cannot_insert_duplicate_while_in_flight(self, db: Database) -> None:
        assert db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A) == 1
        # Same word still pending → ignored.
        assert db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A) == 0
        # Claim it → still in-flight → still blocked.
        [row] = db.claim_keywords(_BILI, 1)
        assert db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A) == 0
        # Move to executing → still blocked.
        db.mark_keyword_executing(int(row["id"]))
        assert db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A) == 0

    def test_can_reinsert_after_used(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["again"], _DIGEST_A)
        [row] = db.claim_keywords(_BILI, 1)
        db.mark_keyword_used(int(row["id"]))
        # Now only `used` history exists → same (platform, kw, digest) re-inserts.
        assert db.insert_pending_keywords(_BILI, ["again"], _DIGEST_A) == 1
        assert db.count_pending_keywords(_BILI, _DIGEST_A) == 1

    def test_can_reinsert_after_expired(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["staleword"], _DIGEST_A)
        # Expire it via a digest change, then re-generate under the new digest.
        db.expire_pending_by_digest(_BILI, _DIGEST_B)
        assert _status_of_keyword(db, "staleword") == "expired"
        assert db.insert_pending_keywords(_BILI, ["staleword"], _DIGEST_B) == 1

    def test_partial_unique_is_digest_scoped_for_inflight(self, db: Database) -> None:
        # Same word, different digest, both pending → allowed (not the same triplet).
        assert db.insert_pending_keywords(_BILI, ["w"], _DIGEST_A) == 1
        assert db.insert_pending_keywords(_BILI, ["w"], _DIGEST_B) == 1


class TestExpireByDigest:
    def test_only_other_digest_pending_is_expired(self, db: Database) -> None:
        # First insert + transition the rows that must SURVIVE expiry (in-flight
        # / history under the stale digest A), claiming them while they're the
        # only pending rows so the FIFO claim picks exactly them.
        db.insert_pending_keywords(_BILI, ["old1", "exec_old"], _DIGEST_A)
        survivors = {r["keyword"]: r for r in db.claim_keywords(_BILI, 10)}
        db.mark_keyword_executing(int(survivors["exec_old"]["id"]))
        db.mark_keyword_used(int(survivors["old1"]["id"]))
        # Now add the rows that stay pending: one stale-digest, one current.
        db.insert_pending_keywords(_BILI, ["old2"], _DIGEST_A)
        db.insert_pending_keywords(_BILI, ["keep_pending"], _DIGEST_B)

        expired = db.expire_pending_by_digest(_BILI, _DIGEST_B)
        # Only `old2` was still pending under the stale digest A.
        assert expired == 1
        assert _status_of_keyword(db, "old2") == "expired"
        assert _status_of_keyword(db, "exec_old") == "executing"  # in-flight preserved
        assert _status_of_keyword(db, "old1") == "used"  # history preserved
        assert _status_of_keyword(db, "keep_pending") == "pending"  # current digest kept


class TestHistoryAndRecycle:
    def test_history_returns_inflight_and_used_newest_first(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["h_used", "h_exec", "h_claim"], _DIGEST_A)
        rows = {r["keyword"]: r for r in db.claim_keywords(_BILI, 10)}
        db.mark_keyword_used(int(rows["h_used"]["id"]))
        db.mark_keyword_executing(int(rows["h_exec"]["id"]))
        # h_claim stays claimed.
        # A still-pending word must NOT appear in history.
        db.insert_pending_keywords(_BILI, ["h_pending"], _DIGEST_A)

        hist = db.history_keywords(_BILI, window_size=50, window_hours=48)
        assert set(hist) == {"h_used", "h_exec", "h_claim"}
        assert "h_pending" not in hist

    def test_history_respects_window_hours(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["recent", "ancient"], _DIGEST_A)
        rows = {r["keyword"]: r for r in db.claim_keywords(_BILI, 10)}
        db.mark_keyword_used(int(rows["recent"]["id"]))
        db.mark_keyword_used(int(rows["ancient"]["id"]))
        _backdate(db, int(rows["ancient"]["id"]), "used_at", minutes_ago=60 * 72)

        hist = db.history_keywords(_BILI, window_size=50, window_hours=48)
        assert hist == ["recent"]

    def test_history_caps_to_window_size(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, [f"w{i}" for i in range(10)], _DIGEST_A)
        for r in db.claim_keywords(_BILI, 10):
            db.mark_keyword_used(int(r["id"]))
        assert len(db.history_keywords(_BILI, window_size=3, window_hours=48)) == 3

    def test_recycle_oldest_used_moves_oldest_to_pending(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["first", "second", "third"], _DIGEST_A)
        rows = {r["keyword"]: r for r in db.claim_keywords(_BILI, 10)}
        for kw in ("first", "second", "third"):
            db.mark_keyword_used(int(rows[kw]["id"]))
        # Make `first` the strictly oldest used row.
        _backdate(db, int(rows["first"]["id"]), "used_at", minutes_ago=120)
        _backdate(db, int(rows["second"]["id"]), "used_at", minutes_ago=60)

        recycled = db.recycle_oldest_used(_BILI, 1, _DIGEST_A)
        assert recycled == 1
        assert _status_of_keyword(db, "first") == "pending"
        assert _status_of_keyword(db, "second") == "used"
        # Recycled row is re-stamped with the requested digest and claimable again.
        assert db.count_pending_keywords(_BILI, _DIGEST_A) == 1

    def test_recycle_skips_word_already_inflight_for_digest(self, db: Database) -> None:
        # `dup` is both used (old) and freshly pending under the same digest.
        db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A)
        [row] = db.claim_keywords(_BILI, 1)
        db.mark_keyword_used(int(row["id"]))
        _backdate(db, int(row["id"]), "used_at", minutes_ago=120)
        db.insert_pending_keywords(_BILI, ["dup"], _DIGEST_A)  # new pending row

        # Recycling the used `dup` would collide with the pending `dup` → skipped.
        recycled = db.recycle_oldest_used(_BILI, 1, _DIGEST_A)
        assert recycled == 0
        assert _status(db, int(row["id"])) == "used"


class TestPurge:
    def test_purge_removes_archived_outside_window_only(self, db: Database) -> None:
        db.insert_pending_keywords(_BILI, ["old_used", "new_used", "live"], _DIGEST_A)
        rows = {r["keyword"]: r for r in db.claim_keywords(_BILI, 10)}
        db.mark_keyword_used(int(rows["old_used"]["id"]))
        db.mark_keyword_used(int(rows["new_used"]["id"]))
        # `live` stays claimed (in-flight) — must never be purged.
        _backdate(db, int(rows["old_used"]["id"]), "used_at", minutes_ago=60 * 72)

        purged = db.purge_archived_keywords(48)
        assert purged == 1
        assert _status_of_keyword(db, "old_used") is None  # deleted
        assert _status_of_keyword(db, "new_used") == "used"
        assert _status_of_keyword(db, "live") == "claimed"


class TestPlannerLock:
    def test_acquire_blocks_second_owner(self, db: Database) -> None:
        assert db.acquire_planner_lock("loop-a", lease_seconds=60) is True
        assert db.acquire_planner_lock("loop-b", lease_seconds=60) is False
        # Same owner reacquiring (renew-via-acquire) is allowed.
        assert db.acquire_planner_lock("loop-a", lease_seconds=60) is True

    def test_expired_lock_can_be_taken_over(self, db: Database) -> None:
        assert db.acquire_planner_lock("loop-a", lease_seconds=60) is True
        # Force the lease into the past.
        past = (datetime.now(UTC) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
        db.conn.execute(
            "UPDATE discovery_planner_lock SET locked_until = ? "
            "WHERE lock_name = 'keyword_planner'",
            (past,),
        )
        db.conn.commit()
        assert db.acquire_planner_lock("loop-b", lease_seconds=60) is True
        # loop-b now owns it → loop-a is locked out.
        assert db.acquire_planner_lock("loop-a", lease_seconds=60) is False

    def test_release_frees_lock(self, db: Database) -> None:
        assert db.acquire_planner_lock("loop-a", lease_seconds=60) is True
        assert db.release_planner_lock("loop-a") is True
        assert db.acquire_planner_lock("loop-b", lease_seconds=60) is True
        # A non-owner release is a no-op.
        assert db.release_planner_lock("loop-a") is False

    def test_renew_only_for_current_owner(self, db: Database) -> None:
        assert db.acquire_planner_lock("loop-a", lease_seconds=1) is True
        assert db.renew_planner_lock("loop-a", lease_seconds=120) is True
        assert db.renew_planner_lock("loop-b", lease_seconds=120) is False


def _status_of_keyword(db: Database, keyword: str) -> str | None:
    """Return the status of the single row with this keyword, or None if absent."""
    rows = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE keyword = ?", (keyword,)
    ).fetchall()
    if not rows:
        return None
    assert len(rows) == 1, f"expected one row for {keyword!r}, found {len(rows)}"
    return str(rows[0]["status"])


def test_separate_connection_claim_serializes_with_main(db: Database, tmp_path: Path) -> None:
    """A second Database handle on the same file must not double-claim.

    Simulates two independent processes/loops (each its own connection)
    racing on the same pending set; the partial-unique + BEGIN IMMEDIATE
    claim guarantees disjoint results.
    """
    db.insert_pending_keywords(_BILI, [f"k{i}" for i in range(4)], _DIGEST_A)

    other = Database(tmp_path / "test.db")
    other.initialize()
    try:
        a = db.claim_keywords(_BILI, 3)
        b = other.claim_keywords(_BILI, 3)
    finally:
        if other._conn is not None:
            other._conn.close()

    assert {r["id"] for r in a}.isdisjoint({r["id"] for r in b})
    assert len(a) + len(b) == 4
    assert db.count_pending_keywords(_BILI, _DIGEST_A) == 0


def test_unique_index_is_truly_partial(db: Database) -> None:
    """Raw INSERT proves used/expired rows are excluded from the unique index."""
    db.insert_pending_keywords(_BILI, ["w"], _DIGEST_A)
    [row] = db.claim_keywords(_BILI, 1)
    db.mark_keyword_used(int(row["id"]))
    # Two more `used` rows for the same triplet inserted raw — no constraint fires.
    for _ in range(2):
        db.conn.execute(
            "INSERT INTO discovery_keywords (platform, keyword, profile_kw_digest, status, used_at)"
            " VALUES (?, ?, ?, 'used', CURRENT_TIMESTAMP)",
            (_BILI, "w", _DIGEST_A),
        )
    db.conn.commit()
    # But a second *pending* row for that triplet must violate the partial index.
    db.conn.execute(
        "INSERT INTO discovery_keywords (platform, keyword, profile_kw_digest, status)"
        " VALUES (?, ?, ?, 'pending')",
        (_BILI, "w", _DIGEST_A),
    )
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO discovery_keywords (platform, keyword, profile_kw_digest, status)"
            " VALUES (?, ?, ?, 'pending')",
            (_BILI, "w", _DIGEST_A),
        )
