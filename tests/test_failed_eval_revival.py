"""Regression tests for failed_eval dead-letter revival.

Field log 2026-08: a deepseek 401/404 misconfiguration burned every
candidate's attempt budget into ``failed_eval``, a status with no path back
to ``pending_eval``; the pool then only drained. Revival re-queues those
rows when evaluation may work again, bounded per call and per candidate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.runtime.candidate_eval import (
    CandidateEvalCoordinator,
    CandidateEvalSnapshot,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "failed-eval-revival.db")
    db.initialize()
    return db


def _enqueue(db: Database, count: int, *, prefix: str) -> list[int]:
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:{prefix}-{index}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"{prefix}-{index}",
                title=f"Candidate {index}",
            )
            for index in range(count)
        ]
    )
    return [
        int(row["id"])
        for row in db.conn.execute(
            "SELECT id FROM discovery_candidates WHERE candidate_key LIKE ? ORDER BY id",
            (f"bilibili:{prefix}-%",),
        ).fetchall()
    ]


def _mark_failed_eval(
    db: Database,
    ids: list[int],
    *,
    eval_error: str = "auth_failed: deepseek 401",
) -> None:
    placeholders = ", ".join("?" for _ in ids)
    db.conn.execute(
        f"""
        UPDATE discovery_candidates
        SET status = 'failed_eval',
            eval_attempts = 5,
            batch_eval_attempts = 3,
            eval_error = ?,
            claim_token = 'dead-token',
            claimed_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders})
        """,
        (eval_error, *ids),
    )
    db.conn.commit()


def _candidate_rows(db: Database, ids: list[int]) -> list[dict[str, object]]:
    placeholders = ", ".join("?" for _ in ids)
    return [
        dict(row)
        for row in db.conn.execute(
            f"""
            SELECT id, status, eval_attempts, batch_eval_attempts, eval_error,
                   eval_revive_count, claim_token
            FROM discovery_candidates
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            (*ids,),
        ).fetchall()
    ]


def test_revive_resets_status_attempts_error_and_claim(tmp_path: Path) -> None:
    db = _database(tmp_path)
    ids = _enqueue(db, 3, prefix="revive-basic")
    _mark_failed_eval(db, ids)

    assert db.revive_failed_eval_candidates() == 3

    for row in _candidate_rows(db, ids):
        assert row["status"] == "pending_eval"
        assert row["eval_attempts"] == 0
        assert row["batch_eval_attempts"] == 0
        assert row["eval_error"] == ""
        assert row["eval_revive_count"] == 1
        assert row["claim_token"] is None


def test_revive_respects_per_call_limit(tmp_path: Path) -> None:
    db = _database(tmp_path)
    ids = _enqueue(db, 5, prefix="revive-limit")
    _mark_failed_eval(db, ids)

    assert db.revive_failed_eval_candidates(limit=2) == 2

    rows = _candidate_rows(db, ids)
    # The bounded batch takes the oldest rows first (id ASC).
    assert [row["status"] for row in rows[:2]] == ["pending_eval", "pending_eval"]
    assert [row["status"] for row in rows[2:]] == ["failed_eval"] * 3
    assert [row["eval_revive_count"] for row in rows] == [1, 1, 0, 0, 0]


def test_revive_respects_persistent_per_candidate_cap(tmp_path: Path) -> None:
    db = _database(tmp_path)
    ids = _enqueue(db, 1, prefix="revive-cap")
    _mark_failed_eval(db, ids)

    assert db.revive_failed_eval_candidates(max_revives=2) == 1
    _mark_failed_eval(db, ids)
    assert db.revive_failed_eval_candidates(max_revives=2) == 1
    assert _candidate_rows(db, ids)[0]["eval_revive_count"] == 2

    # A process restarting against a still-broken provider must not
    # resurrect (and re-burn LLM attempts on) the same row forever.
    _mark_failed_eval(db, ids)
    assert db.revive_failed_eval_candidates(max_revives=2) == 0
    row = _candidate_rows(db, ids)[0]
    assert row["status"] == "failed_eval"
    assert row["eval_revive_count"] == 2


def test_revive_skips_temporal_review_rows_and_other_statuses(tmp_path: Path) -> None:
    db = _database(tmp_path)
    temporal_ids = _enqueue(db, 1, prefix="revive-temporal")
    _mark_failed_eval(db, temporal_ids, eval_error="temporal_review_due:2026-10-01")
    alive_ids = _enqueue(db, 2, prefix="revive-alive")

    assert db.revive_failed_eval_candidates() == 0

    temporal_row = _candidate_rows(db, temporal_ids)[0]
    assert temporal_row["status"] == "failed_eval"
    assert temporal_row["eval_error"] == "temporal_review_due:2026-10-01"
    assert temporal_row["eval_revive_count"] == 0
    for row in _candidate_rows(db, alive_ids):
        assert row["status"] == "pending_eval"
        assert row["eval_revive_count"] == 0


def test_pipeline_delegate_revives_through_database(tmp_path: Path) -> None:
    db = _database(tmp_path)
    ids = _enqueue(db, 2, prefix="revive-pipeline")
    _mark_failed_eval(db, ids)
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=object(),  # type: ignore[arg-type]
    )

    assert pipeline.revive_failed_eval_candidates() == 2
    assert all(row["status"] == "pending_eval" for row in _candidate_rows(db, ids))


def test_pipeline_delegate_returns_zero_without_storage_support() -> None:
    pipeline = DiscoveryCandidatePipeline(
        database=object(),
        discovery_engine=object(),  # type: ignore[arg-type]
    )
    assert pipeline.revive_failed_eval_candidates() == 0


def _coordinator(revive_callback: object) -> CandidateEvalCoordinator:
    return CandidateEvalCoordinator(
        pipeline=object(),
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=10,
            pending_eval=0,
            evaluating=0,
            evaluated_pending_admission=0,
            admitted_pending_copy=0,
        ),
        profile_provider=lambda: None,
        revive_failed_eval_callback=revive_callback,  # type: ignore[arg-type]
    )


def test_resume_notifications_trigger_failed_eval_revival() -> None:
    calls: list[int] = []
    coordinator = _coordinator(lambda: calls.append(1) or 2)

    coordinator.notify("startup")
    coordinator.notify("config_saved")
    coordinator.notify("manual_retry")

    assert len(calls) == 3


def test_non_resume_notifications_do_not_trigger_revival() -> None:
    calls: list[int] = []
    coordinator = _coordinator(lambda: calls.append(1) or 0)

    coordinator.notify("inventory_consumed")
    coordinator.notify("candidate_enqueued:pipeline")
    coordinator.notify("safety_wake")

    assert calls == []


def test_provider_recovery_unpauses_and_revives() -> None:
    calls: list[int] = []
    coordinator = _coordinator(lambda: calls.append(1) or 1)
    coordinator._paused = True  # auth/no_provider failure path parks the loop

    coordinator.notify("config_saved")

    assert coordinator._paused is False
    assert len(calls) == 1


def test_revival_failure_does_not_break_notify(caplog: pytest.LogCaptureFixture) -> None:
    def _boom() -> int:
        raise RuntimeError("db down")

    coordinator = _coordinator(_boom)
    with caplog.at_level(logging.WARNING):
        coordinator.notify("config_saved")

    assert "failed_eval candidate revival failed" in caplog.text


def test_revival_runs_once_per_coordinator_startup(tmp_path: Path) -> None:
    """Startup fires revival once; the per-candidate cap bounds later resumes."""
    db = _database(tmp_path)
    ids = _enqueue(db, 2, prefix="revive-startup")
    _mark_failed_eval(db, ids)
    coordinator = _coordinator(db.revive_failed_eval_candidates)

    coordinator.notify("startup")
    assert all(row["status"] == "pending_eval" for row in _candidate_rows(db, ids))

    # Rows that die again revive again on the next resume signal, but only
    # until the persistent cap is reached (covered by the cap test above).
    _mark_failed_eval(db, ids)
    coordinator.notify("startup")
    assert [row["eval_revive_count"] for row in _candidate_rows(db, ids)] == [2, 2]
