"""Tests for the Storage database module."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.storage.database import Database


def _seed_visible(db: Database, bvid: str, **kwargs: Any) -> None:
    """v0.3.57+ shorthand: cache a row visible to the pool gate.

    ``cache_content`` + auto-fill of ``pool_expression`` / ``pool_topic_label``
    so the row passes ``get_pool_candidates``'s precompute gate. Tests
    asserting gate behavior on empty-copy rows must use ``cache_content``
    directly instead.
    """
    kwargs.setdefault("pool_expression", "测试推荐文案")
    kwargs.setdefault("pool_topic_label", "测试主题")
    kwargs.setdefault("style_key", "tutorial")
    kwargs.setdefault("topic_group", "测试分组")
    kwargs.setdefault("relevance_score", 0.90)
    db.cache_content(bvid, **kwargs)


def _v2_temporal(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "temporal_class": "current",
        "temporal_confidence": 0.95,
        "temporal_reason": "状态变化会影响核心价值",
        "temporal_policy_version": "v2",
        "temporal_validity_mode": "event_state",
        "temporal_valid_until": "",
        "temporal_scope": "core",
        "temporal_state": "active",
        "temporal_evidence": "活动仍在进行，报名入口仍然开放",
        "temporal_next_review_at": "2026-08-26T00:00:00Z",
        "temporal_evaluated_at": "2026-08-12T00:00:00Z",
        "temporal_evidence_complete": True,
    }
    fields.update(overrides)
    return fields


def _freeze_storage_clock(monkeypatch: Any, frozen: str = "2026-08-12T00:00:00+00:00") -> None:
    """把 database 模块的墙钟冻结到固定时刻。

    helper 默认 evaluated_at=2026-08-12，存储层重算 next_review=+14d
    （2026-08-26）。真实时钟越过该日期后，依赖「重算后仍在未来 →
    eligible」的测试会翻转成 review_due（2026-08-26 当天 CI 实测爆发）。
    冻结到 evaluated_at 当天，重算结果永远在冻结时钟的 14 天后，
    测试回归确定性。
    """
    import openbiliclaw.storage.database as database_module

    frozen_dt = datetime.fromisoformat(frozen)

    class _FrozenClock:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            if tz is None:
                return frozen_dt
            return frozen_dt.astimezone(tz)  # type: ignore[arg-type]

        @classmethod
        def fromisoformat(cls, value: str) -> datetime:
            return datetime.fromisoformat(value)

        @classmethod
        def strptime(cls, value: str, fmt: str) -> datetime:
            return datetime.strptime(value, fmt)

    monkeypatch.setattr(database_module, "datetime", _FrozenClock)


class TestDatabase:
    """Test SQLite database operations."""

    def test_initialize(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            assert db.conn is not None
            db.close()

    def test_initialize_adds_temporal_columns_to_existing_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / "temporal-migration.db"
        db = Database(db_path)
        db.initialize()
        db.cache_content("BVLEGACY", title="legacy", source="search")
        db.enqueue_discovery_candidates(
            [
                DiscoveryCandidateWrite(
                    candidate_key="bilibili:BVLEGACY-CANDIDATE",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id="BVLEGACY-CANDIDATE",
                    title="legacy candidate",
                )
            ]
        )
        for table_name in ("content_cache", "discovery_candidates"):
            db.conn.execute(f"DROP INDEX IF EXISTS idx_{table_name}_temporal_review")
            if table_name == "discovery_candidates":
                db.conn.execute("DROP INDEX IF EXISTS idx_discovery_candidates_temporal_retry")
            for column_name in (
                "temporal_class",
                "temporal_confidence",
                "temporal_reason",
                "temporal_policy_version",
                "temporal_validity_mode",
                "temporal_valid_until",
                "temporal_scope",
                "temporal_state",
                "temporal_evidence",
                "temporal_next_review_at",
                "temporal_evaluated_at",
                "temporal_evidence_complete",
            ):
                db.conn.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
        db.conn.execute("DROP INDEX IF EXISTS idx_content_cache_temporal_review_retry")
        for column_name in ("temporal_review_attempts", "temporal_review_retry_at"):
            db.conn.execute(f"ALTER TABLE content_cache DROP COLUMN {column_name}")
            db.conn.execute(f"ALTER TABLE discovery_candidates DROP COLUMN {column_name}")
        db.conn.commit()
        db.close()

        migrated = Database(db_path)
        migrated.initialize()

        for table_name in ("content_cache", "discovery_candidates"):
            columns = {
                str(row["name"])
                for row in migrated.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            assert {
                "temporal_class",
                "temporal_confidence",
                "temporal_reason",
                "temporal_policy_version",
                "temporal_validity_mode",
                "temporal_valid_until",
                "temporal_scope",
                "temporal_state",
                "temporal_evidence",
                "temporal_next_review_at",
                "temporal_evaluated_at",
                "temporal_evidence_complete",
            } <= columns
            if table_name == "content_cache":
                assert {"temporal_review_attempts", "temporal_review_retry_at"} <= columns
            else:
                assert {"temporal_review_attempts", "temporal_review_retry_at"} <= columns
            row = migrated.conn.execute(f"SELECT * FROM {table_name} LIMIT 1").fetchone()
            assert row["temporal_class"] == "unknown"
            assert row["temporal_confidence"] == 0.0
            assert row["temporal_reason"] == ""
            assert row["temporal_policy_version"] == "v1"
            assert row["temporal_validity_mode"] == "none"
            assert row["temporal_valid_until"] == ""
            assert row["temporal_scope"] == "none"
            assert row["temporal_state"] == "unknown"
            assert row["temporal_evidence"] == ""
            assert row["temporal_next_review_at"] == ""
            assert row["temporal_evaluated_at"] == ""
            assert row["temporal_evidence_complete"] == 0
            if table_name == "content_cache":
                assert row["temporal_review_attempts"] == 0
                assert row["temporal_review_retry_at"] == ""
            else:
                assert row["temporal_review_attempts"] == 0
                assert row["temporal_review_retry_at"] == ""

        migrated.close()

    def test_v2_deadline_hard_expires_at_cache_sink(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "v2-deadline.db")
        db.initialize()

        result = db.cache_content(
            "BVDEADLINE",
            title="报名截止：2000-01-01 00:00 +00:00",
            source="search",
            relevance_score=0.9,
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="报名截止后核心价值失效",
                temporal_validity_mode="explicit_deadline",
                temporal_valid_until="2000-01-01T00:00:00Z",
                temporal_state="unknown",
                temporal_evidence="报名截止：2000-01-01 00:00 +00:00",
                temporal_next_review_at="2000-01-01T00:00:00Z",
            ),
        )

        row = db.conn.execute("SELECT * FROM content_cache WHERE bvid = 'BVDEADLINE'").fetchone()
        assert result.temporal_decision.disposition == "expired"
        assert result.pool_status == "stale"
        assert row["temporal_validity_mode"] == "explicit_deadline"
        assert row["temporal_evidence_complete"] == 1

    def test_v2_storage_recomputes_caller_supplied_review_clock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _freeze_storage_clock(monkeypatch)
        db = Database(tmp_path / "v2-policy-clock.db")
        db.initialize()

        result = db.cache_content(
            "BVCANONICALCLOCK",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            relevance_score=0.9,
            **_v2_temporal(temporal_next_review_at="2099-01-01T00:00:00Z"),
        )

        row = db.conn.execute(
            "SELECT temporal_evaluated_at, temporal_next_review_at "
            "FROM content_cache WHERE bvid = 'BVCANONICALCLOCK'"
        ).fetchone()
        assert result.temporal_decision.disposition == "eligible"
        assert dict(row) == {
            "temporal_evaluated_at": "2026-08-12T00:00:00Z",
            "temporal_next_review_at": "2026-08-26T00:00:00Z",
        }

    def test_v2_storage_does_not_trust_ungrounded_terminal_marker(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-ungrounded-sink.db")
        db.initialize()

        result = db.cache_content(
            "BVUNGROUNDEDSTATE",
            title="普通通用教程",
            source="search",
            relevance_score=0.9,
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="赛事已经结束",
                temporal_validity_mode="event_state",
                temporal_state="expired",
                temporal_evidence="赛事已经结束",
                temporal_next_review_at="",
                temporal_evaluated_at=datetime.now().astimezone().isoformat(),
            ),
        )

        row = db.conn.execute(
            "SELECT pool_status, temporal_validity_mode, temporal_state "
            "FROM content_cache WHERE bvid = 'BVUNGROUNDEDSTATE'"
        ).fetchone()
        assert result.temporal_decision.disposition == "eligible"
        assert dict(row) == {
            "pool_status": "fresh",
            "temporal_validity_mode": "freshness_only",
            "temporal_state": "unknown",
        }

    def test_ungrounded_rereview_cannot_erase_existing_grounded_hold(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-ungrounded-merge.db")
        db.initialize()
        due = _v2_temporal(
            temporal_next_review_at="2000-01-01T00:00:00Z",
            temporal_evaluated_at="1999-01-01T00:00:00Z",
        )
        first = db.cache_content(
            "BVUNGROUNDEDMERGE",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            **due,
        )
        assert first.pool_status == "temporal_review_hold"

        rereview = db.cache_content(
            "BVUNGROUNDEDMERGE",
            title="普通通用教程",
            source="search",
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="赛事已经结束",
                temporal_validity_mode="event_state",
                temporal_state="expired",
                temporal_evidence="赛事已经结束",
                temporal_next_review_at="",
            ),
        )

        stored = db.conn.execute(
            """
            SELECT pool_status, temporal_class, temporal_validity_mode,
                   temporal_state, temporal_evidence
            FROM content_cache
            WHERE bvid = 'BVUNGROUNDEDMERGE'
            """
        ).fetchone()
        assert rereview.pool_status == "temporal_review_hold"
        assert dict(stored) == {
            "pool_status": "temporal_review_hold",
            "temporal_class": "current",
            "temporal_validity_mode": "event_state",
            "temporal_state": "active",
            "temporal_evidence": "活动仍在进行，报名入口仍然开放",
        }

    @pytest.mark.parametrize(
        "rereview_kind",
        ["low_confidence", "hook_only", "ungrounded_freshness", "conditional_active"],
    )
    def test_weak_rereview_cannot_release_existing_grounded_hold(
        self,
        tmp_path: Path,
        rereview_kind: str,
    ) -> None:
        db = Database(tmp_path / f"v2-weak-rereview-{rereview_kind}.db")
        db.initialize()
        due = _v2_temporal(
            temporal_next_review_at="2000-01-01T00:00:00Z",
            temporal_evaluated_at="1999-01-01T00:00:00Z",
        )
        db.cache_content(
            "BVWEAKREVIEW",
            title=(
                "活动仍在进行，报名入口仍然开放；标题写着今天最新；"
                "如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"
            ),
            source="search",
            **due,
        )
        if rereview_kind == "low_confidence":
            rereview = _v2_temporal(
                temporal_class="evergreen",
                temporal_confidence=0.10,
                temporal_reason="方法本身长期有效",
                temporal_validity_mode="none",
                temporal_scope="none",
                temporal_state="unknown",
                temporal_evidence="",
                temporal_next_review_at="",
            )
        elif rereview_kind == "hook_only":
            rereview = _v2_temporal(
                temporal_class="current",
                temporal_confidence=0.95,
                temporal_reason="只有标题钩子依赖新鲜度",
                temporal_validity_mode="freshness_only",
                temporal_scope="hook",
                temporal_state="unknown",
                temporal_evidence="标题写着今天最新",
            )
        elif rereview_kind == "ungrounded_freshness":
            rereview = _v2_temporal(
                temporal_class="current",
                temporal_confidence=0.95,
                temporal_reason="价格依赖今天的状态",
                temporal_validity_mode="freshness_only",
                temporal_scope="core",
                temporal_state="unknown",
                temporal_evidence="正文不存在的今日价格",
            )
        else:
            rereview = _v2_temporal(
                temporal_class="versioned",
                temporal_confidence=0.95,
                temporal_reason="版本变化后需要重新核验",
                temporal_validity_mode="version_state",
                temporal_scope="core",
                temporal_state="active",
                temporal_evidence=("如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"),
            )

        result = db.cache_content(
            "BVWEAKREVIEW",
            title=(
                "活动仍在进行，报名入口仍然开放；标题写着今天最新；"
                "如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"
            ),
            source="search",
            **rereview,
        )

        stored = db.conn.execute(
            "SELECT pool_status, temporal_class, temporal_scope, temporal_evidence "
            "FROM content_cache WHERE bvid = 'BVWEAKREVIEW'"
        ).fetchone()
        assert result.pool_status == "temporal_review_hold"
        assert dict(stored) == {
            "pool_status": "temporal_review_hold",
            "temporal_class": "current",
            "temporal_scope": "core",
            "temporal_evidence": "活动仍在进行，报名入口仍然开放",
        }

    def test_textual_evidence_complete_marker_fails_neutral(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "v2-text-marker.db")
        db.initialize()

        result = db.cache_content(
            "BVTEXTMARKER",
            title="活动已经结束",
            source="search",
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="活动已经结束",
                temporal_validity_mode="event_state",
                temporal_state="expired",
                temporal_evidence="活动已经结束",
                temporal_next_review_at="",
                temporal_evidence_complete="false",
            ),
        )

        row = db.conn.execute(
            "SELECT temporal_class, temporal_evidence_complete, pool_status "
            "FROM content_cache WHERE bvid = 'BVTEXTMARKER'"
        ).fetchone()
        assert result.temporal_decision.disposition == "eligible"
        assert dict(row) == {
            "temporal_class": "unknown",
            "temporal_evidence_complete": 0,
            "pool_status": "fresh",
        }

    @pytest.mark.parametrize(
        ("temporal_class", "mode", "state", "evidence"),
        [
            ("breaking", "event_state", "expired", "活动已经结束"),
            ("versioned", "version_state", "superseded", "版本已经被替代"),
        ],
    )
    def test_v2_terminal_state_hard_expires_without_next_review_clock(
        self,
        tmp_path: Path,
        temporal_class: str,
        mode: str,
        state: str,
        evidence: str,
    ) -> None:
        db = Database(tmp_path / f"v2-{state}.db")
        db.initialize()

        result = db.cache_content(
            f"BV{state.upper()}",
            title=evidence,
            source="search",
            relevance_score=0.9,
            **_v2_temporal(
                temporal_class=temporal_class,
                temporal_reason="核心事件或版本已经终结",
                temporal_validity_mode=mode,
                temporal_state=state,
                temporal_next_review_at="",
                temporal_evidence=evidence,
            ),
        )

        row = db.conn.execute(
            "SELECT temporal_state, temporal_next_review_at, "
            "temporal_evidence_complete, pool_status FROM content_cache"
        ).fetchone()
        assert result.temporal_decision.disposition == "expired"
        assert dict(row) == {
            "temporal_state": state,
            "temporal_next_review_at": "",
            "temporal_evidence_complete": 1,
            "pool_status": "stale",
        }

    def test_v2_review_due_is_held_and_does_not_count_as_inventory(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-review-hold.db")
        db.initialize()

        result = db.cache_content(
            "BVREVIEWHOLD",
            title="需要复核状态",
            source="search",
            relevance_score=0.9,
            pool_expression="hold",
            pool_topic_label="hold",
            style_key="tutorial",
            topic_group="hold",
            **_v2_temporal(
                temporal_next_review_at="2000-01-01T00:00:00Z",
                temporal_evaluated_at="1999-01-01T00:00:00Z",
            ),
        )

        assert result.temporal_decision.disposition == "review_due"
        assert result.pool_status == "temporal_review_hold"
        assert db.count_pool_candidates() == 0
        assert db.get_pool_candidates(limit=10) == []
        assert [row["bvid"] for row in db.get_pool_candidates_needing_evaluation(limit=10)] == [
            "BVREVIEWHOLD"
        ]

    def test_only_complete_non_neutral_review_restores_hold_to_fresh(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_storage_clock(monkeypatch)
        db = Database(tmp_path / "v2-review-restore.db")
        db.initialize()
        due = _v2_temporal(
            temporal_next_review_at="2000-01-01T00:00:00Z",
            temporal_evaluated_at="1999-01-01T00:00:00Z",
        )
        db.cache_content(
            "BVRESTORE",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            **due,
        )

        raw = db.cache_content("BVRESTORE", title="raw rediscovery", source="search")
        held = db.conn.execute("SELECT * FROM content_cache WHERE bvid = 'BVRESTORE'").fetchone()
        assert raw.pool_status == "temporal_review_hold"
        assert held["temporal_evidence"] == due["temporal_evidence"]
        assert held["temporal_next_review_at"] == "1999-01-15T00:00:00Z"

        db.conn.execute(
            "UPDATE content_cache SET temporal_review_attempts = 3, "
            "temporal_review_retry_at = '2099-01-01T00:00:00Z' "
            "WHERE bvid = 'BVRESTORE'"
        )
        db.conn.commit()

        reviewed = db.cache_content(
            "BVRESTORE",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            **_v2_temporal(temporal_next_review_at="2099-01-01T00:00:00Z"),
        )
        assert reviewed.temporal_decision.disposition == "eligible"
        assert reviewed.pool_status == "fresh"
        restored = db.conn.execute(
            "SELECT temporal_review_attempts, temporal_review_retry_at "
            "FROM content_cache WHERE bvid = 'BVRESTORE'"
        ).fetchone()
        assert dict(restored) == {
            "temporal_review_attempts": 0,
            "temporal_review_retry_at": "",
        }

    def test_review_hold_claim_uses_backoff_and_does_not_starve_next_row(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-review-backoff.db")
        db.initialize()
        due = _v2_temporal(
            temporal_next_review_at="2000-01-01T00:00:00Z",
            temporal_evaluated_at="1999-01-01T00:00:00Z",
        )
        for bvid in ("BVHOLD-A", "BVHOLD-B"):
            db.cache_content(bvid, title=bvid, source="search", **due)
        clock = datetime.fromisoformat("2026-08-13T00:00:00+00:00")

        first = db.get_pool_candidates_needing_evaluation(limit=1, now=clock)
        second = db.get_pool_candidates_needing_evaluation(limit=1, now=clock)
        immediate_third = db.get_pool_candidates_needing_evaluation(limit=1, now=clock)

        assert [row["bvid"] for row in first] == ["BVHOLD-A"]
        assert [row["bvid"] for row in second] == ["BVHOLD-B"]
        assert immediate_third == []
        leases = {
            str(row["bvid"]): (
                int(row["temporal_review_attempts"]),
                str(row["temporal_review_retry_at"]),
            )
            for row in db.conn.execute(
                "SELECT bvid, temporal_review_attempts, temporal_review_retry_at "
                "FROM content_cache ORDER BY bvid"
            )
        }
        assert leases == {
            "BVHOLD-A": (1, "2026-08-13T01:00:00Z"),
            "BVHOLD-B": (1, "2026-08-13T01:00:00Z"),
        }

        retry = db.get_pool_candidates_needing_evaluation(
            limit=1,
            now=clock + timedelta(hours=1),
        )
        assert [row["bvid"] for row in retry] == ["BVHOLD-A"]
        retried = db.conn.execute(
            "SELECT temporal_review_attempts, temporal_review_retry_at "
            "FROM content_cache WHERE bvid = 'BVHOLD-A'"
        ).fetchone()
        assert dict(retried) == {
            "temporal_review_attempts": 2,
            "temporal_review_retry_at": "2026-08-13T03:00:00Z",
        }

    def test_failed_slow_hold_review_renews_expired_retry_lease(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "v2-slow-review-lease.db")
        db.initialize()
        db.cache_content(
            "BVSLOWHOLD",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            **_v2_temporal(
                temporal_next_review_at="2000-01-01T00:00:00Z",
                temporal_evaluated_at="1999-01-01T00:00:00Z",
            ),
        )
        assert db.get_pool_candidates_needing_evaluation(limit=1)
        # Simulate an evaluator that ran longer than its claim-time lease.
        db.conn.execute(
            "UPDATE content_cache SET temporal_review_retry_at = ? WHERE bvid = ?",
            ("2000-01-01T00:00:00Z", "BVSLOWHOLD"),
        )
        db.conn.commit()

        result = db.cache_content(
            "BVSLOWHOLD",
            title="活动仍在进行，报名入口仍然开放",
            source="search",
            temporal_class="unknown",
            temporal_confidence=0.0,
            temporal_reason="",
            temporal_policy_version="v2",
            temporal_validity_mode="none",
            temporal_valid_until="",
            temporal_scope="none",
            temporal_state="unknown",
            temporal_evidence="",
            temporal_next_review_at="",
            temporal_evaluated_at=datetime.now().astimezone().isoformat(),
            temporal_evidence_complete=False,
            temporal_evaluated=False,
        )

        stored = db.conn.execute(
            "SELECT pool_status, temporal_review_attempts, temporal_review_retry_at "
            "FROM content_cache WHERE bvid = 'BVSLOWHOLD'"
        ).fetchone()
        assert result.pool_status == "temporal_review_hold"
        assert stored["pool_status"] == "temporal_review_hold"
        assert stored["temporal_review_attempts"] == 1
        assert stored["temporal_review_retry_at"] != "2000-01-01T00:00:00Z"
        assert db.get_pool_candidates_needing_evaluation(limit=1) == []

    def test_legacy_age_boundary_moves_to_review_hold_not_stale(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "legacy-review-hold.db")
        db.initialize()

        result = db.cache_content(
            "BVLEGACYDUE",
            title="旧版时间分类",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.95,
            temporal_reason="旧版 freshness 分类",
        )

        row = db.conn.execute(
            "SELECT temporal_policy_version, pool_status FROM content_cache "
            "WHERE bvid = 'BVLEGACYDUE'"
        ).fetchone()
        assert result.temporal_decision.disposition == "review_due"
        assert dict(row) == {
            "temporal_policy_version": "v1",
            "pool_status": "temporal_review_hold",
        }

    def test_final_serve_commit_applies_all_three_temporal_states(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_storage_clock(monkeypatch)
        db = Database(tmp_path / "v2-final-serve.db")
        db.initialize()
        common = {
            "source": "search",
            "relevance_score": 0.9,
            "pool_expression": "serve",
            "pool_topic_label": "serve",
            "style_key": "tutorial",
            "topic_group": "serve",
        }
        db.cache_content("BVFINALFRESH", title="fresh", **common, **_v2_temporal())
        db.cache_content("BVFINALHOLD", title="hold", **common, **_v2_temporal())
        db.cache_content(
            "BVFINALEXPIRED",
            title="截止：2099-01-01 00:00 +00:00",
            **common,
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="截止后失效",
                temporal_validity_mode="explicit_deadline",
                temporal_valid_until="2099-01-01T00:00:00Z",
                temporal_state="unknown",
                temporal_evidence="截止：2099-01-01 00:00 +00:00",
            ),
        )
        db.conn.execute(
            "UPDATE content_cache SET temporal_next_review_at = ?, "
            "temporal_evaluated_at = ? WHERE bvid = ?",
            (
                "2000-01-01T00:00:00Z",
                "1999-01-01T00:00:00Z",
                "BVFINALHOLD",
            ),
        )
        db.conn.execute(
            "UPDATE content_cache SET temporal_valid_until = ?, temporal_evidence = ? "
            "WHERE bvid = ?",
            (
                "2000-01-01T00:00:00Z",
                "截止：2000-01-01 00:00 +00:00",
                "BVFINALEXPIRED",
            ),
        )
        db.conn.commit()

        result = db.batch_insert_eligible_recommendations_and_mark_shown(
            [
                {"bvid": "BVFINALFRESH", "confidence": 0.9},
                {"bvid": "BVFINALHOLD", "confidence": 0.9},
                {"bvid": "BVFINALEXPIRED", "confidence": 0.9},
            ],
            ["BVFINALFRESH", "BVFINALHOLD", "BVFINALEXPIRED"],
        )

        assert result.committed_bvids == ("BVFINALFRESH",)
        assert result.skipped_bvids == ("BVFINALHOLD",)
        assert result.temporally_stale_bvids == ("BVFINALEXPIRED",)
        statuses = {
            str(row["bvid"]): str(row["pool_status"])
            for row in db.conn.execute("SELECT bvid, pool_status FROM content_cache ORDER BY bvid")
        }
        assert statuses == {
            "BVFINALEXPIRED": "stale",
            "BVFINALFRESH": "shown",
            "BVFINALHOLD": "temporal_review_hold",
        }

    @pytest.mark.parametrize("invalid_marker", ["false", "not-complete", "0", 2])
    def test_final_serve_fails_neutral_for_invalid_complete_marker(
        self,
        tmp_path: Path,
        invalid_marker: object,
    ) -> None:
        db = Database(tmp_path / f"v2-final-marker-{invalid_marker}.db")
        db.initialize()
        evidence = "活动已经结束"
        db.cache_content(
            "BVFINALMARKER",
            title=evidence,
            source="search",
            relevance_score=0.9,
            pool_expression="serve",
            pool_topic_label="serve",
            style_key="tutorial",
            topic_group="serve",
            **_v2_temporal(
                temporal_class="breaking",
                temporal_reason="活动结束后核心价值失效",
                temporal_validity_mode="event_state",
                temporal_state="expired",
                temporal_evidence=evidence,
                temporal_next_review_at="",
            ),
        )
        # Simulate a legacy/corrupt adapter that wrote a truthy non-marker.
        db.conn.execute(
            "UPDATE content_cache SET pool_status = 'fresh', "
            "temporal_evidence_complete = ? WHERE bvid = 'BVFINALMARKER'",
            (invalid_marker,),
        )
        db.conn.commit()

        result = db.batch_insert_eligible_recommendations_and_mark_shown(
            [{"bvid": "BVFINALMARKER", "confidence": 0.9}],
            ["BVFINALMARKER"],
        )

        assert result.committed_bvids == ("BVFINALMARKER",)
        assert result.temporally_stale_bvids == ()
        assert (
            db.conn.execute(
                "SELECT pool_status FROM content_cache WHERE bvid = 'BVFINALMARKER'"
            ).fetchone()["pool_status"]
            == "shown"
        )

    def test_review_due_candidate_is_requeued_with_evidence_intact(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-candidate-review.db")
        db.initialize()
        db.enqueue_discovery_candidates(
            [
                DiscoveryCandidateWrite(
                    candidate_key="bilibili:BVCANDIDATEREVIEW",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id="BVCANDIDATEREVIEW",
                    title="candidate review",
                )
            ]
        )
        claimed = db.claim_discovery_candidates_for_eval(
            limit=1,
            claim_token="temporal-review-token",
        )[0]
        evidence = _v2_temporal(
            temporal_next_review_at="2000-01-01T00:00:00Z",
            temporal_evaluated_at="1999-01-01T00:00:00Z",
        )

        assert db.persist_claimed_discovery_candidate_evaluations(
            [
                {
                    "candidate_id": int(claimed["id"]),
                    "status": "evaluated",
                    "relevance_score": 0.9,
                    "relevance_reason": "fit",
                    **evidence,
                }
            ],
            claim_token="temporal-review-token",
        ) == {int(claimed["id"])}

        row = db.conn.execute(
            "SELECT * FROM discovery_candidates WHERE id = ?",
            (int(claimed["id"]),),
        ).fetchone()
        assert row["status"] == "pending_eval"
        assert row["claim_token"] is None
        assert row["temporal_evidence"] == evidence["temporal_evidence"]
        assert row["temporal_evidence_complete"] == 1

    def test_temporally_stale_rows_are_excluded_and_retired_from_fresh_pool(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-retire.db")
        db.initialize()
        _seed_visible(
            db,
            "BVSTALE",
            title="过期突发内容",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.95,
            temporal_reason="价值依赖即时状态",
        )
        _seed_visible(
            db,
            "BVDURABLE",
            title="长期有效内容",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="evergreen",
            temporal_confidence=0.95,
            temporal_reason="核心价值不依赖当前时间",
        )

        # Legacy age windows schedule a review rather than claiming expiry.
        assert [row["bvid"] for row in db.get_pool_candidates(limit=10)] == ["BVDURABLE"]

        initial_statuses = {
            str(row["bvid"]): str(row["pool_status"])
            for row in db.conn.execute(
                "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
            ).fetchall()
        }
        assert initial_statuses == {
            "BVDURABLE": "fresh",
            "BVSTALE": "temporal_review_hold",
        }

        retired = db.retire_temporally_stale_pool_items(
            now=datetime.fromisoformat("2026-08-12T12:00:00+00:00")
        )

        assert retired == set()
        statuses = {
            str(row["bvid"]): str(row["pool_status"])
            for row in db.conn.execute(
                "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
            ).fetchall()
        }
        assert statuses == {
            "BVDURABLE": "fresh",
            "BVSTALE": "temporal_review_hold",
        }
        assert db.retire_temporally_stale_pool_items() == set()
        db.close()

    def test_temporally_stale_evaluated_waiter_does_not_inflate_supply(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-evaluated-waiter.db")
        db.initialize()
        db.enqueue_discovery_candidates(
            [
                DiscoveryCandidateWrite(
                    candidate_key="bilibili:BVSTALEWAIT",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id="BVSTALEWAIT",
                    title="过期等待项",
                    published_at="2000-01-01T00:00:00Z",
                ),
                DiscoveryCandidateWrite(
                    candidate_key="youtube:YTPENDING",
                    source_platform="youtube",
                    source_strategy="yt_search",
                    content_id="YTPENDING",
                    title="尚未评估项",
                ),
            ]
        )
        claimed = db.claim_discovery_candidates_for_eval(
            limit=1,
            claim_token="stale-wait-token",
        )
        assert db.persist_claimed_discovery_candidate_evaluations(
            [
                {
                    "candidate_id": int(claimed[0]["id"]),
                    "status": "evaluated",
                    "relevance_score": 0.9,
                    "relevance_reason": "fit",
                    "temporal_class": "breaking",
                    "temporal_confidence": 0.95,
                    "temporal_reason": "价值依赖即时状态",
                    "topic_group": "news",
                    "style_key": "deep_dive",
                }
            ],
            claim_token="stale-wait-token",
        ) == {int(claimed[0]["id"])}

        readiness = db.count_pool_readiness()

        # The legacy breaking row is review-due and now has a future per-row
        # retry lease, so only the genuinely ready YouTube row counts.
        assert readiness["pending_eval"] == 1
        assert readiness["evaluated_pending"] == 0
        assert readiness["raw"] == 1
        assert db.count_pool_raw_material_candidates() == 1
        assert db.count_pool_raw_material_by_source() == {"youtube": 1}
        assert db.count_evaluated_discovery_candidates_by_source() == {}
        assert db.count_admission_waiting_discovery_candidates_by_source() == {
            "youtube": 1,
        }
        db.close()

    def test_admission_filters_temporally_stale_rows_before_applying_limit(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-admission-window.db")
        db.initialize()
        temporal_now = datetime.now().astimezone()
        stale_published_at = (temporal_now - timedelta(days=10)).isoformat()
        fresh_published_at = (temporal_now - timedelta(days=1)).isoformat()
        db.conn.executemany(
            """
            INSERT INTO discovery_candidates (
                candidate_key,
                status,
                source_platform,
                source_strategy,
                content_id,
                title,
                published_at,
                temporal_class,
                temporal_confidence,
                evaluated_at
            )
            VALUES (?, 'evaluated', 'bilibili', 'search', ?, ?, ?, 'breaking', 0.95, ?)
            """,
            [
                (
                    f"bilibili:BVSTALE{index}",
                    f"BVSTALE{index}",
                    f"stale {index}",
                    stale_published_at,
                    "2026-01-01 00:00:00",
                )
                for index in range(501)
            ]
            + [
                (
                    "bilibili:BVFRESHAFTERBACKLOG",
                    "BVFRESHAFTERBACKLOG",
                    "fresh after stale backlog",
                    fresh_published_at,
                    "2026-01-02 00:00:00",
                )
            ],
        )
        db.conn.commit()

        admitted = db.get_evaluated_discovery_candidates_for_admission(limit=500)

        assert [row["candidate_key"] for row in admitted] == ["bilibili:BVFRESHAFTERBACKLOG"]
        db.close()

    def test_temporal_waiter_sweep_rereads_after_acquiring_writer_lock(
        self,
        tmp_path: Path,
    ) -> None:
        import openbiliclaw.storage.database as database_module

        db = Database(tmp_path / "temporal-sweep-rediscovery-race.db")
        db.initialize()
        temporal_now = datetime.now().astimezone()
        stale_published_at = (temporal_now - timedelta(days=10)).isoformat()
        fresh_published_at = (temporal_now - timedelta(days=1)).isoformat()
        db.conn.execute(
            """
            INSERT INTO discovery_candidates (
                candidate_key,
                status,
                source_platform,
                source_strategy,
                content_id,
                title,
                published_at,
                temporal_class,
                temporal_confidence,
                evaluated_at
            )
            VALUES (
                'bilibili:BVREDISCOVERED',
                'evaluated',
                'bilibili',
                'search',
                'BVREDISCOVERED',
                'rediscovered candidate',
                ?,
                'breaking',
                0.95,
                CURRENT_TIMESTAMP
            )
            """,
            (stale_published_at,),
        )
        db.conn.commit()

        rediscovery = db.open_connection()
        rediscovery.execute("BEGIN IMMEDIATE")
        rediscovery.execute(
            """
            UPDATE discovery_candidates
            SET published_at = ?, last_seen_at = CURRENT_TIMESTAMP
            WHERE candidate_key = 'bilibili:BVREDISCOVERED'
            """,
            (fresh_published_at,),
        )

        sweep_started = threading.Event()
        eligibility_checked = threading.Event()
        real_evaluate = database_module.evaluate_temporal_eligibility

        def observe_eligibility(*args: Any, **kwargs: Any) -> Any:
            eligibility_checked.set()
            return real_evaluate(*args, **kwargs)

        def sweep() -> int:
            sweep_started.set()
            return db.reject_temporally_stale_evaluated_candidates(now=temporal_now)

        try:
            with (
                patch.object(
                    database_module,
                    "evaluate_temporal_eligibility",
                    side_effect=observe_eligibility,
                ),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(sweep)
                assert sweep_started.wait(timeout=5)
                try:
                    # The sweep must wait at BEGIN IMMEDIATE. Evaluating the
                    # old snapshot here would recreate the rediscovery race.
                    assert not eligibility_checked.wait(timeout=0.5)
                finally:
                    rediscovery.commit()
                assert future.result(timeout=5) == 0
        finally:
            if rediscovery.in_transaction:
                rediscovery.rollback()
            rediscovery.close()

        stored = db.conn.execute(
            """
            SELECT status, published_at
            FROM discovery_candidates
            WHERE candidate_key = 'bilibili:BVREDISCOVERED'
            """
        ).fetchone()
        assert dict(stored) == {
            "status": "evaluated",
            "published_at": fresh_published_at,
        }
        db.close()

    def test_pool_snapshot_holds_legacy_content_due_for_review(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-snapshot.db")
        db.initialize()
        _seed_visible(
            db,
            "BVWAITEDTOOLONG",
            title="入池后过期的即时内容",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.9,
            temporal_reason="价值依赖即时状态",
        )

        snapshot = db.load_pool_serve_snapshot(limit=10)

        assert snapshot.candidate_rows == ()
        assert snapshot.readiness["available"] == 0
        assert (
            db.conn.execute(
                "SELECT pool_status FROM content_cache WHERE bvid='BVWAITEDTOOLONG'"
            ).fetchone()[0]
            == "temporal_review_hold"
        )
        db.close()

    def test_atomic_pool_serve_commit_rechecks_temporal_eligibility(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-final-commit.db")
        db.initialize()
        published = datetime.fromisoformat("2026-08-10T12:00:00+00:00")
        _seed_visible(
            db,
            "BVEXPIRES",
            title="提交前跨过期限",
            source="search",
            published_at=published.isoformat(),
            temporal_class="breaking",
            temporal_confidence=0.9,
            temporal_reason="价值依赖即时状态",
        )
        _seed_visible(
            db,
            "BVEVERGREEN",
            title="长期有效",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="evergreen",
            temporal_confidence=0.9,
            temporal_reason="核心价值不依赖当前时间",
        )
        items = [
            {
                "bvid": "BVEXPIRES",
                "expression": "即时内容",
                "topic": "事件",
                "confidence": 0.9,
            },
            {
                "bvid": "BVEVERGREEN",
                "expression": "长期内容",
                "topic": "知识",
                "confidence": 0.9,
            },
        ]

        result = db.batch_insert_eligible_recommendations_and_mark_shown(
            items,
            ["BVEXPIRES", "BVEVERGREEN"],
            now=published + timedelta(days=3),
        )

        assert len(result.recommendation_ids) == 1
        assert result.committed_bvids == ("BVEVERGREEN",)
        assert result.temporally_stale_bvids == ()
        assert result.skipped_bvids == ("BVEXPIRES",)
        assert [
            str(row["bvid"])
            for row in db.conn.execute("SELECT bvid FROM recommendations").fetchall()
        ] == ["BVEVERGREEN"]
        statuses = {
            str(row["bvid"]): str(row["pool_status"])
            for row in db.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()
        }
        assert statuses == {
            "BVEXPIRES": "temporal_review_hold",
            "BVEVERGREEN": "shown",
        }
        db.close()

    def test_temporally_stale_rows_do_not_inflate_readiness_or_copy_backlog(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-readiness.db")
        db.initialize()
        common = {
            "source": "search",
            "style_key": "tutorial",
            "relevance_score": 0.9,
            "published_at": "2000-01-01T00:00:00Z",
            "temporal_confidence": 0.95,
        }
        db.cache_content(
            "BVSTALECOPY",
            title="过期内容等待文案",
            topic_group="过期主题",
            temporal_class="breaking",
            temporal_reason="价值依赖即时状态",
            **common,
        )
        db.cache_content(
            "BVDURABLECOPY",
            title="长期内容等待文案",
            topic_group="长期主题",
            temporal_class="evergreen",
            temporal_reason="核心价值长期有效",
            **common,
        )

        readiness = db.count_pool_readiness()

        assert readiness["available"] == 0
        assert readiness["copy_ready"] == 0
        assert readiness["raw"] == 1
        assert readiness["pending"] == 1
        assert readiness["admitted_pending_copy"] == 1
        assert readiness["admitted_pending_available"] == 1
        assert [row["bvid"] for row in db.get_pool_candidates_needing_copy(limit=1)] == [
            "BVDURABLECOPY"
        ]
        db.close()

    def test_v2_review_due_row_does_not_inflate_raw_or_pending_readiness(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "v2-review-due-readiness.db")
        db.initialize()
        evidence = "活动仍在进行，报名入口仍然开放"
        db.cache_content(
            "BVREVIEWDUERAW",
            title=evidence,
            source="search",
            style_key="tutorial",
            topic_group="活动",
            relevance_score=0.9,
            **_v2_temporal(temporal_evidence=evidence),
        )
        # Simulate an item that crossed its policy-owned review boundary after
        # entering the pool but before the next maintenance sweep.
        db.conn.execute(
            "UPDATE content_cache SET temporal_evaluated_at = ?, "
            "temporal_next_review_at = ? WHERE bvid = ?",
            ("1999-12-18T00:00:00Z", "2000-01-01T00:00:00Z", "BVREVIEWDUERAW"),
        )
        db.conn.commit()

        readiness = db.count_pool_readiness()

        assert readiness["raw"] == 0
        assert readiness["pending"] == 0
        assert db.count_pool_raw_material_candidates() == 0
        assert db.count_pool_raw_material_by_source() == {}
        db.close()

    def test_temporally_stale_rows_do_not_influence_active_pool_taxonomy(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-taxonomy.db")
        db.initialize()
        common = {
            "source": "search",
            "source_platform": "bilibili",
            "published_at": "2000-01-01T00:00:00Z",
            "temporal_confidence": 0.95,
        }
        _seed_visible(
            db,
            "BVSTALETAXONOMY",
            title="过期主题样本",
            topic_group="过期主题",
            franchise_key="stale-ip",
            temporal_class="breaking",
            temporal_reason="价值依赖即时状态",
            **common,
        )
        _seed_visible(
            db,
            "BVDURABLETAXONOMY",
            title="长期主题样本",
            topic_group="长期主题",
            franchise_key="durable-ip",
            temporal_class="evergreen",
            temporal_reason="核心价值长期有效",
            **common,
        )

        assert db.get_distinct_topic_groups() == ["长期主题"]
        assert db.get_active_pool_topic_groups(limit=10, min_count=1) == ["长期主题"]
        assert db.get_topic_group_samples() == [("长期主题", ["长期主题样本"])]
        assert db.count_pool_by_franchise() == {"durable-ip": 1}
        assert db.get_pool_distribution_counts()["topic_group"] == {"长期主题": 1}
        assert db.get_pool_topic_counts_by_platform() == {"bilibili": {"长期主题": 1}}
        assert db.count_pool_candidates_by_source() == {"bilibili": 1}
        db.close()

    def test_temporally_stale_rows_do_not_consume_delight_limits(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-delight.db")
        db.initialize()
        common = {
            "source": "search",
            "published_at": "2000-01-01T00:00:00Z",
            "temporal_confidence": 0.95,
        }
        _seed_visible(
            db,
            "BVSTALEDELIGHT",
            title="过期惊喜",
            temporal_class="breaking",
            temporal_reason="价值依赖即时状态",
            **common,
        )
        _seed_visible(
            db,
            "BVDURABLEDELIGHT",
            title="长期惊喜",
            temporal_class="evergreen",
            temporal_reason="核心价值长期有效",
            **common,
        )
        _seed_visible(
            db,
            "BVSTALESCORE",
            title="过期高分待计算",
            relevance_score=0.99,
            temporal_class="breaking",
            temporal_reason="价值依赖即时状态",
            **common,
        )
        _seed_visible(
            db,
            "BVDURABLESCORE",
            title="长期较低分待计算",
            relevance_score=0.80,
            temporal_class="evergreen",
            temporal_reason="核心价值长期有效",
            **common,
        )
        assert db.update_delight_score(
            "BVSTALEDELIGHT",
            delight_score=0.99,
            delight_reason="测试推荐文案",
            delight_hook="测试主题",
        )
        assert db.update_delight_score(
            "BVDURABLEDELIGHT",
            delight_score=0.90,
            delight_reason="测试推荐文案",
            delight_hook="测试主题",
        )

        assert db.count_delight_candidates(min_delight_score=0.85) == 1
        assert [row["bvid"] for row in db.get_pool_candidates_needing_delight_score(limit=1)] == [
            "BVDURABLESCORE"
        ]
        db.close()

    def test_atomic_pool_serve_commit_reports_concurrent_skips(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-final-skip.db")
        db.initialize()
        _seed_visible(db, "BVCONSUMED", title="已被另一请求消费", source="search")
        db.insert_recommendation("BVCONSUMED", confidence=0.9)

        result = db.batch_insert_eligible_recommendations_and_mark_shown(
            [{"bvid": "BVCONSUMED", "confidence": 0.9}],
            ["BVCONSUMED"],
        )

        assert result.recommendation_ids == ()
        assert result.committed_bvids == ()
        assert result.skipped_bvids == ("BVCONSUMED",)
        db.close()

    def test_thread_connections_preserve_existing_foreign_key_semantics(
        self,
        tmp_path: Path,
    ) -> None:
        """Facade calls stay legacy-off; explicit transactions stay on."""
        db = Database(tmp_path / "foreign-key-semantics.db")
        db.initialize()

        primary_foreign_keys = int(db.conn.execute("PRAGMA foreign_keys").fetchone()[0])
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_foreign_keys, worker_recommendation_id = executor.submit(
                lambda: (
                    int(db.conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                    db.insert_recommendation("BVWORKERFK", confidence=0.7),
                )
            ).result(timeout=5)
        explicit = db.open_connection()
        try:
            explicit_foreign_keys = int(explicit.execute("PRAGMA foreign_keys").fetchone()[0])
        finally:
            explicit.close()

        assert primary_foreign_keys == 0
        assert worker_foreign_keys == 0
        assert worker_recommendation_id > 0
        assert explicit_foreign_keys == 1
        db.close()

    def test_active_worker_reader_never_shares_connection_with_settlement_writer(
        self,
        tmp_path: Path,
    ) -> None:
        """A pending SELECT on one worker cannot corrupt another worker's write.

        Real dialogue settlement failed with ``another row available`` while a
        concurrent status/event worker was stepping a result on the same
        ``check_same_thread=False`` connection. Keep the reader cursor active
        across the state transition and pin the generic Database boundary:
        every worker thread owns a distinct WAL connection.
        """
        db = Database(tmp_path / "thread-affine.db")
        db.initialize()
        db.create_chat_turn(
            turn_id="settlement-turn",
            message="learn",
            payload={"state": "discussing"},
        )
        for index in range(8):
            db.insert_llm_usage(
                provider="test",
                model="test",
                prompt_tokens=index,
                completion_tokens=1,
                estimated_cost_cny=0.0,
            )

        main_connection_id = id(db.conn)
        worker_connection_ids: set[int] = set()
        connection_ids_lock = threading.Lock()
        reader_ready = threading.Event()
        writer_finished = threading.Event()
        release_reader = threading.Event()

        def record_connection() -> sqlite3.Connection:
            connection = db.conn
            with connection_ids_lock:
                worker_connection_ids.add(id(connection))
            return connection

        def hold_active_reader() -> int:
            connection = record_connection()
            cursor = connection.execute("SELECT id FROM llm_usage ORDER BY id")
            first = cursor.fetchone()
            assert first is not None
            reader_ready.set()
            assert writer_finished.wait(timeout=5)
            assert release_reader.wait(timeout=5)
            return 1 + len(cursor.fetchall())

        def transition_payload() -> bool:
            assert reader_ready.wait(timeout=5)
            record_connection()
            try:
                return db.update_chat_turn_payload_state(
                    "settlement-turn",
                    expected_state="discussing",
                    new_state="pending",
                )
            finally:
                writer_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(hold_active_reader)
            writer = executor.submit(transition_payload)
            assert writer.result(timeout=5) is True
            release_reader.set()
            assert reader.result(timeout=5) == 8

        assert len(worker_connection_ids) == 2
        assert main_connection_id not in worker_connection_ids
        assert db.get_chat_turn("settlement-turn")["payload"]["state"] == "pending"
        db.close()

    def test_claim_token_prevents_stale_evaluation_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.enqueue_discovery_candidates(
                [
                    DiscoveryCandidateWrite(
                        candidate_key=f"bilibili:BV{i}",
                        source_platform="bilibili",
                        source_strategy="search",
                        content_id=f"BV{i}",
                        title=f"Candidate {i}",
                    )
                    for i in range(2)
                ]
            )

            original = db.claim_discovery_candidates_for_eval(limit=2, claim_token="claim-a")
            assert {row["claim_token"] for row in original} == {"claim-a"}
            ids = [int(row["id"]) for row in original]
            assert (
                db.reset_claimed_discovery_candidates_to_pending(
                    ids,
                    claim_token="claim-a",
                    reason="reload",
                    max_attempts=5,
                    max_batch_attempts=50,
                    increment_attempts=False,
                )
                == 2
            )

            replacement = db.claim_discovery_candidates_for_eval(limit=2, claim_token="claim-b")
            updated = db.persist_claimed_discovery_candidate_evaluations(
                [
                    {
                        "candidate_id": row["id"],
                        "status": "evaluated",
                        "relevance_score": 0.9,
                    }
                    for row in original
                ],
                claim_token="claim-a",
            )

            assert updated == set()
            assert {row["claim_token"] for row in replacement} == {"claim-b"}
            assert (
                db.reset_claimed_discovery_candidates_to_pending(
                    ids,
                    claim_token="claim-a",
                    reason="stale release",
                    max_attempts=5,
                    max_batch_attempts=50,
                    increment_attempts=False,
                )
                == 0
            )

            db.close()

    def test_tokenized_completion_and_stale_reset_clear_claim_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.enqueue_discovery_candidates(
                [
                    DiscoveryCandidateWrite(
                        candidate_key="bilibili:BVTOKEN",
                        source_platform="bilibili",
                        source_strategy="search",
                        content_id="BVTOKEN",
                        title="Token",
                    )
                ]
            )
            row = db.claim_discovery_candidates_for_eval(limit=1, claim_token="claim-a")[0]
            updated = db.persist_claimed_discovery_candidate_evaluations(
                [
                    {
                        "candidate_id": row["id"],
                        "status": "evaluated",
                        "relevance_score": 0.9,
                    }
                ],
                claim_token="claim-a",
            )
            stored = db.conn.execute(
                "SELECT status, claim_token, claimed_at FROM discovery_candidates"
            ).fetchone()

            assert updated == {int(row["id"])}
            assert dict(stored) == {
                "status": "evaluated",
                "claim_token": None,
                "claimed_at": None,
            }

            db.conn.execute(
                "UPDATE discovery_candidates SET status='evaluating', "
                "claim_token='orphan', claimed_at=NULL"
            )
            db.conn.commit()
            assert db.reset_stale_discovery_candidate_evaluations(max_age_minutes=30) == 1
            reset = db.conn.execute(
                "SELECT status, claim_token, claimed_at FROM discovery_candidates"
            ).fetchone()
            assert dict(reset) == {
                "status": "pending_eval",
                "claim_token": None,
                "claimed_at": None,
            }

            db.close()

    def test_discovery_temporal_metadata_roundtrips_claim_and_admission(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-candidate.db")
        db.initialize()
        db.enqueue_discovery_candidates(
            [
                DiscoveryCandidateWrite(
                    candidate_key="bilibili:BVTEMPORAL",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id="BVTEMPORAL",
                    title="Temporal candidate",
                )
            ]
        )

        claimed = db.claim_discovery_candidates_for_eval(limit=1, claim_token="temporal-claim")[0]
        assert claimed["temporal_class"] == "unknown"
        assert claimed["temporal_confidence"] == 0.0
        assert claimed["temporal_policy_version"] == "v1"

        updated = db.persist_claimed_discovery_candidate_evaluations(
            [
                {
                    "candidate_id": claimed["id"],
                    "status": "evaluated",
                    "relevance_score": 0.9,
                    "temporal_class": "CURRENT",
                    "temporal_confidence": 0.85,
                    "temporal_reason": "  价值依赖近期状态  ",
                    "temporal_policy_version": "v1-test",
                }
            ],
            claim_token="temporal-claim",
        )
        admitted = db.get_evaluated_discovery_candidates_for_admission(limit=1)[0]

        assert updated == {int(claimed["id"])}
        assert admitted["temporal_class"] == "current"
        assert admitted["temporal_confidence"] == 0.85
        assert admitted["temporal_reason"] == "价值依赖近期状态"
        assert admitted["temporal_policy_version"] == "v1"

        db.cache_content(
            "BVTEMPORAL",
            title=admitted["title"],
            source=admitted["source_strategy"],
            temporal_class=admitted["temporal_class"],
            temporal_confidence=admitted["temporal_confidence"],
            temporal_reason=admitted["temporal_reason"],
            temporal_policy_version=admitted["temporal_policy_version"],
        )
        db.mark_discovery_candidate_cached(int(claimed["id"]))
        db.close()

        reloaded = Database(tmp_path / "temporal-candidate.db")
        reloaded.initialize()
        cached_candidate = reloaded.conn.execute(
            "SELECT * FROM discovery_candidates WHERE id = ?",
            (int(claimed["id"]),),
        ).fetchone()
        cached_content = reloaded.conn.execute(
            "SELECT * FROM content_cache WHERE bvid = 'BVTEMPORAL'"
        ).fetchone()
        for row in (cached_candidate, cached_content):
            assert row["temporal_class"] == "current"
            assert row["temporal_confidence"] == 0.85
            assert row["temporal_reason"] == "价值依赖近期状态"
            assert row["temporal_policy_version"] == "v1"
        assert cached_candidate["status"] == "cached"
        reloaded.close()

    def test_discovery_temporal_storage_fails_neutral_for_invalid_values(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-candidate-invalid.db")
        db.initialize()
        db.enqueue_discovery_candidates(
            [
                DiscoveryCandidateWrite(
                    candidate_key="bilibili:BVTEMPORAL-INVALID",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id="BVTEMPORAL-INVALID",
                    title="Invalid temporal candidate",
                )
            ]
        )
        claimed = db.claim_discovery_candidates_for_eval(limit=1)[0]

        assert (
            db.update_discovery_candidate_evaluations(
                [
                    {
                        "candidate_id": claimed["id"],
                        "status": "evaluated",
                        "temporal_class": "instant-news",
                        "temporal_confidence": 0.9,
                        "temporal_reason": "invalid class",
                    }
                ]
            )
            == 1
        )
        stored = db.get_evaluated_discovery_candidates_for_admission(limit=1)[0]
        assert stored["temporal_class"] == "unknown"
        assert stored["temporal_confidence"] == 0.0
        assert stored["temporal_reason"] == ""
        assert stored["temporal_policy_version"] == "v1"
        db.close()

    def test_initialize_creates_recommendation_read_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            recommendation_indexes = {
                str(row["name"])
                for row in db.conn.execute("PRAGMA index_list(recommendations)").fetchall()
            }
            event_indexes = {
                str(row["name"]) for row in db.conn.execute("PRAGMA index_list(events)").fetchall()
            }
            content_indexes = {
                str(row["name"])
                for row in db.conn.execute("PRAGMA index_list(content_cache)").fetchall()
            }

            assert "idx_recommendations_created_id" in recommendation_indexes
            assert "idx_recommendations_bvid" in recommendation_indexes
            assert "idx_events_type_id" in event_indexes
            assert "idx_content_cache_content_id" in content_indexes

            db.close()

    def test_initialize_creates_saved_membership_item_key_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            membership_indexes = {
                str(row["name"])
                for row in db.conn.execute("PRAGMA index_list(saved_memberships)").fetchall()
            }

            assert "idx_saved_memberships_item_key" in membership_indexes

            db.close()

    def test_insert_and_get_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            row_id = db.insert_event(
                "click",
                url="https://www.bilibili.com/video/BV1234",
                title="Test Video",
                metadata={"element": "title"},
            )
            assert row_id > 0

            events = db.get_recent_events(limit=10)
            assert len(events) == 1
            assert events[0]["event_type"] == "click"
            assert events[0]["url"] == "https://www.bilibili.com/video/BV1234"

            db.close()

    def test_concurrent_event_inserts_use_isolated_transactions(self) -> None:
        """API/background writers must not share one SQLite transaction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            def insert(index: int) -> int:
                return db.insert_event(
                    "view",
                    url=f"https://www.bilibili.com/video/BVCONCURRENT{index}",
                    metadata={"bvid": f"BVCONCURRENT{index}"},
                )

            with ThreadPoolExecutor(max_workers=12) as executor:
                row_ids = list(executor.map(insert, range(120)))

            assert len(set(row_ids)) == 120
            assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 120
            assert db.conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0] == 120
            db.close()

    def test_cache_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1test",
                title="Test Video",
                up_name="TestUP",
                tags=["AI", "编程"],
                source="search",
            )

            cursor = db.conn.execute("SELECT * FROM content_cache WHERE bvid = ?", ("BV1test",))
            row = cursor.fetchone()
            assert row is not None
            assert row["title"] == "Test Video"
            assert row["up_name"] == "TestUP"
            assert row["relevance_score"] == 0.0

            db.close()

    def test_cache_content_validates_and_upserts_temporal_metadata(
        self,
        tmp_path: Path,
        caplog: Any,
    ) -> None:
        db = Database(tmp_path / "temporal-cache.db")
        db.initialize()
        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="VERSIONED",
            temporal_confidence=0.75,
            temporal_reason="  依赖软件版本  ",
            temporal_policy_version="v1-test",
        )
        stored = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason,
                   temporal_policy_version
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(stored) == {
            "temporal_class": "versioned",
            "temporal_confidence": 0.75,
            "temporal_reason": "依赖软件版本",
            "temporal_policy_version": "v1",
        }

        db.cache_content(
            "BVTEMPORAL",
            title="Temporal rediscovered",
            source="related",
            view_count=10,
        )
        rediscovered = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason,
                   temporal_policy_version
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(rediscovered) == dict(stored)

        caplog.clear()
        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="not-a-class",
            temporal_confidence=0.9,
            temporal_reason="invalid class",
            temporal_policy_version="",
        )
        invalid_class = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason,
                   temporal_policy_version
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(invalid_class) == dict(stored)
        assert any(
            "Invalid temporal metadata coerced to unknown" in record.message
            for record in caplog.records
        )

        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="current",
            temporal_confidence=1.5,
            temporal_reason="invalid confidence",
        )
        invalid_confidence = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(invalid_confidence) == {
            "temporal_class": "versioned",
            "temporal_confidence": 0.75,
        }

        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="current",
            temporal_confidence=0.8,
            temporal_reason={"unsafe": "repr"},
        )
        invalid_reason = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(invalid_reason) == {
            "temporal_class": "versioned",
            "temporal_confidence": 0.75,
            "temporal_reason": "依赖软件版本",
        }

        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="current",
            temporal_confidence=10**10000,
            temporal_reason="huge integer confidence",
        )
        huge_confidence = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(huge_confidence) == {
            "temporal_class": "versioned",
            "temporal_confidence": 0.75,
            "temporal_reason": "依赖软件版本",
        }

        db.cache_content(
            "BVTEMPORAL",
            title="Temporal",
            source="search",
            temporal_class="current",
            temporal_confidence=0.8,
            temporal_reason="   ",
        )
        empty_reason = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason
            FROM content_cache
            WHERE bvid = 'BVTEMPORAL'
            """
        ).fetchone()
        assert dict(empty_reason) == {
            "temporal_class": "versioned",
            "temporal_confidence": 0.75,
            "temporal_reason": "依赖软件版本",
        }
        db.close()

    def test_cache_content_holds_legacy_age_due_rows_for_review(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-cache-sink.db")
        db.initialize()

        write_result = db.cache_content(
            "BVSTALESINK",
            title="已经过期的突发内容",
            source="search",
            relevance_score=0.95,
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.95,
            temporal_reason="价值依赖即时状态",
        )

        row = db.conn.execute(
            "SELECT pool_status, temporal_class FROM content_cache WHERE bvid='BVSTALESINK'"
        ).fetchone()
        assert row["pool_status"] == "temporal_review_hold"
        assert row["temporal_class"] == "breaking"
        assert write_result.created is True
        assert write_result.admitted is False
        assert write_result.temporal_decision.rejection_reason.startswith(
            "temporal_review_due:class=breaking:"
        )
        assert db.count_pool_candidates() == 0
        db.close()

    def test_cache_content_repairs_malformed_legacy_temporal_evidence_fail_neutral(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "malformed-legacy-temporal.db")
        db.initialize()
        db.cache_content(
            "BVMALFORMED",
            title="legacy malformed",
            source="search",
            relevance_score=0.9,
        )
        db.conn.execute(
            """
            UPDATE content_cache
            SET temporal_class='breaking', temporal_confidence=0.95,
                temporal_reason='', published_at='2000-01-01T00:00:00Z',
                pool_status='fresh'
            WHERE bvid='BVMALFORMED'
            """
        )
        db.conn.commit()

        result = db.cache_content(
            "BVMALFORMED",
            **DiscoveredContent(
                bvid="BVMALFORMED",
                title="raw rediscovery",
                source_strategy="search",
                relevance_score=0.9,
            ).to_cache_kwargs(),
        )

        row = db.conn.execute(
            """
            SELECT temporal_class, temporal_confidence, temporal_reason, pool_status
            FROM content_cache WHERE bvid='BVMALFORMED'
            """
        ).fetchone()
        assert dict(row) == {
            "temporal_class": "unknown",
            "temporal_confidence": 0.0,
            "temporal_reason": "",
            "pool_status": "fresh",
        }
        assert result.admitted is True

    def test_cache_content_empty_cover_does_not_wipe_existing(self) -> None:
        """空封面的重摄入(如仅刷新互动数据)不得抹掉已有的好封面。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1cover",
                title="Cover Video",
                cover_url="//i2.hdslb.com/bfs/archive/good.jpg",
                source="search",
            )
            db.cache_content(
                "BV1cover",
                title="Cover Video",
                cover_url="",
                view_count=12345,
                source="related",
            )

            row = db.conn.execute(
                "SELECT cover_url, view_count FROM content_cache WHERE bvid = ?", ("BV1cover",)
            ).fetchone()
            assert row["cover_url"] == "//i2.hdslb.com/bfs/archive/good.jpg"
            assert row["view_count"] == 12345

            db.cache_content(
                "BV1cover",
                title="Cover Video",
                cover_url="//i2.hdslb.com/bfs/archive/new.jpg",
                source="search",
            )
            row = db.conn.execute(
                "SELECT cover_url FROM content_cache WHERE bvid = ?", ("BV1cover",)
            ).fetchone()
            assert row["cover_url"] == "//i2.hdslb.com/bfs/archive/new.jpg"

            db.close()

    def test_cache_content_persists_social_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1metrics",
                title="Metric Video",
                source="search",
                view_count=1000,
                like_count=100,
                favorite_count=90,
                collect_count=80,
                comment_count=70,
                share_count=60,
                danmaku_count=50,
                reply_count=40,
                retweet_count=30,
                bookmark_count=20,
            )

            row = db.conn.execute(
                """
                SELECT
                    view_count,
                    like_count,
                    favorite_count,
                    collect_count,
                    comment_count,
                    share_count,
                    danmaku_count,
                    reply_count,
                    retweet_count,
                    bookmark_count
                FROM content_cache
                WHERE bvid = ?
                """,
                ("BV1metrics",),
            ).fetchone()
            assert row is not None
            assert dict(row) == {
                "view_count": 1000,
                "like_count": 100,
                "favorite_count": 90,
                "collect_count": 80,
                "comment_count": 70,
                "share_count": 60,
                "danmaku_count": 50,
                "reply_count": 40,
                "retweet_count": 30,
                "bookmark_count": 20,
            }

            db.close()

    def test_search_local_inspiration_evidence_returns_content_cache_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BVlocal1",
                content_id="BVlocal1",
                source_platform="bilibili",
                title="独立游戏 机制拆解：地图叙事如何成立",
                content_url="https://www.bilibili.com/video/BVlocal1",
                description="围绕独立游戏、关卡设计、叙事节奏的分析。",
                topic_group="独立游戏",
                pool_topic_label="独立游戏机制",
                pool_status="fresh",
            )

            rows = db.search_local_inspiration_evidence(
                "独立游戏 机制",
                limit=5,
                lookback_days=365,
            )

            assert rows
            assert rows[0]["title"] == "独立游戏 机制拆解：地图叙事如何成立"
            assert rows[0]["url"] == "https://www.bilibili.com/video/BVlocal1"
            assert rows[0]["source_table"] == "content_cache"
            assert rows[0]["source_platform"] == "bilibili"
            db.close()

    def test_search_local_inspiration_evidence_matches_spaceless_cjk_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BVlocal1",
                content_id="BVlocal1",
                source_platform="bilibili",
                title="独立游戏 机制拆解：地图叙事如何成立",
                content_url="https://www.bilibili.com/video/BVlocal1",
                description="围绕独立游戏、关卡设计、叙事节奏的分析。",
                topic_group="独立游戏",
                pool_topic_label="独立游戏机制",
                pool_status="fresh",
            )

            rows = db.search_local_inspiration_evidence(
                "独立游戏机制",
                limit=5,
                lookback_days=365,
            )

            assert rows
            assert rows[0]["title"] == "独立游戏 机制拆解：地图叙事如何成立"
            db.close()

    def test_search_local_inspiration_evidence_synthesizes_bilibili_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BVlocal1",
                content_id="BVlocal1",
                source_platform="bilibili",
                title="独立游戏 机制拆解：地图叙事如何成立",
                content_url="",
                description="围绕独立游戏、关卡设计、叙事节奏的分析。",
                topic_group="独立游戏",
                pool_topic_label="独立游戏机制",
                pool_status="fresh",
            )

            rows = db.search_local_inspiration_evidence(
                "独立游戏 机制",
                limit=5,
                lookback_days=365,
            )

            assert rows
            assert rows[0]["url"] == "https://www.bilibili.com/video/BVlocal1"
            db.close()

    def test_search_local_inspiration_evidence_excludes_single_weak_token_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BVlocal2",
                content_id="BVlocal2",
                source_platform="bilibili",
                title="独立音乐人访谈实录",
                content_url="https://www.bilibili.com/video/BVlocal2",
                description="音乐创作与巡演生活。",
                topic_group="音乐",
                pool_topic_label="独立音乐",
                pool_status="fresh",
            )

            rows = db.search_local_inspiration_evidence(
                "独立游戏 机制",
                limit=5,
                lookback_days=365,
            )

            assert rows == []
            db.close()

    def test_iter_cover_lifecycle_reports_status_and_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BV1shown", cover_url="https://i1.hdslb.com/a.jpg", source="search")
            db.cache_content("BV1fresh", cover_url="https://i1.hdslb.com/b.jpg", source="search")
            db.cache_content("BV1fav", cover_url="https://i1.hdslb.com/c.jpg", source="search")
            db.cache_content("BV1wl", cover_url="https://i1.hdslb.com/d.jpg", source="search")
            db.cache_content("BV1bare", cover_url="", source="search")

            db.conn.execute("UPDATE content_cache SET pool_status='shown' WHERE bvid='BV1shown'")
            db.conn.execute("UPDATE content_cache SET pool_status='fresh' WHERE bvid='BV1fresh'")
            db.conn.commit()
            db.add_to_favorites("BV1fav")
            db.add_to_watch_later("BV1wl")

            rows = {cover: (status, saved) for cover, status, saved in db.iter_cover_lifecycle()}

            assert rows["https://i1.hdslb.com/a.jpg"] == ("shown", False)
            assert rows["https://i1.hdslb.com/b.jpg"] == ("fresh", False)
            assert rows["https://i1.hdslb.com/c.jpg"][1] is True  # favorite -> saved
            assert rows["https://i1.hdslb.com/d.jpg"][1] is True  # watch-later -> saved
            assert "" not in rows  # empty cover_url rows are excluded

            db.close()

    def test_iter_cover_lifecycle_uses_normalized_saved_memberships(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bilibili = SavedItemInput(source_platform="bilibili", content_id="BV1normalized")
            xhs = SavedItemInput(source_platform="xiaohongshu", content_id="note-normalized")
            db.cache_content(
                "BV1normalized",
                source_platform="bilibili",
                content_id="BV1normalized",
                cover_url="https://i1.hdslb.com/normalized.jpg",
                source="search",
            )
            db.cache_content(
                "xiaohongshu:note-normalized",
                source_platform="xiaohongshu",
                content_id="note-normalized",
                cover_url="https://sns-webpic-qc.xhscdn.com/normalized.jpg",
                source="xhs-feed",
            )

            db.upsert_saved_membership("favorite", bilibili)
            db.upsert_saved_membership("watch_later", xhs)

            assert db.conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
            assert db.conn.execute("SELECT COUNT(*) FROM watch_later").fetchone()[0] == 0
            rows = {cover: saved for cover, _, saved in db.iter_cover_lifecycle()}
            assert rows["https://i1.hdslb.com/normalized.jpg"] is True
            assert rows["https://sns-webpic-qc.xhscdn.com/normalized.jpg"] is True

            db.close()

    def test_iter_servable_cover_urls_recent_and_servable_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BV1fresh", cover_url="https://i1.hdslb.com/fresh.jpg", source="s")
            db.cache_content("BV1shown", cover_url="https://i1.hdslb.com/shown.jpg", source="s")
            db.cache_content("BV1stale", cover_url="https://i1.hdslb.com/stale.jpg", source="s")
            db.cache_content("BV1saved", cover_url="https://i1.hdslb.com/saved.jpg", source="s")
            db.cache_content("BV1old", cover_url="https://i1.hdslb.com/old.jpg", source="s")

            db.conn.execute("UPDATE content_cache SET pool_status='fresh' WHERE bvid='BV1fresh'")
            db.conn.execute("UPDATE content_cache SET pool_status='shown' WHERE bvid='BV1shown'")
            db.conn.execute("UPDATE content_cache SET pool_status='stale' WHERE bvid='BV1stale'")
            db.conn.execute("UPDATE content_cache SET pool_status='stale' WHERE bvid='BV1saved'")
            db.conn.execute(
                "UPDATE content_cache SET discovered_at=datetime('now','-2 days') "
                "WHERE bvid='BV1old'"
            )
            db.conn.commit()
            db.add_to_favorites("BV1saved")  # saved -> included even though stale

            urls = db.iter_servable_cover_urls(recent_hours=12, limit=100)

            assert "https://i1.hdslb.com/fresh.jpg" in urls  # fresh -> servable
            assert "https://i1.hdslb.com/shown.jpg" in urls  # shown -> servable
            assert "https://i1.hdslb.com/saved.jpg" in urls  # stale but saved -> kept
            assert "https://i1.hdslb.com/stale.jpg" not in urls  # stale + unsaved -> excluded
            assert "https://i1.hdslb.com/old.jpg" not in urls  # outside recency window

            db.close()

    def test_iter_servable_cover_urls_uses_normalized_saved_memberships(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bilibili = SavedItemInput(source_platform="bilibili", content_id="BV1normalized")
            xhs = SavedItemInput(source_platform="xiaohongshu", content_id="note-normalized")
            db.cache_content(
                "BV1normalized",
                source_platform="bilibili",
                content_id="BV1normalized",
                cover_url="https://i1.hdslb.com/normalized.jpg",
                source="search",
            )
            db.cache_content(
                "xiaohongshu:note-normalized",
                source_platform="xiaohongshu",
                content_id="note-normalized",
                cover_url="https://sns-webpic-qc.xhscdn.com/normalized.jpg",
                source="xhs-feed",
            )
            db.cache_content(
                "xiaohongshu:note-unsaved",
                source_platform="xiaohongshu",
                content_id="note-unsaved",
                cover_url="https://sns-webpic-qc.xhscdn.com/unsaved.jpg",
                source="xhs-feed",
            )
            db.conn.execute("UPDATE content_cache SET pool_status='stale'")
            db.conn.commit()

            db.upsert_saved_membership("favorite", bilibili)
            db.upsert_saved_membership("watch_later", xhs)

            urls = db.iter_servable_cover_urls(recent_hours=12, limit=100)
            assert "https://i1.hdslb.com/normalized.jpg" in urls
            assert "https://sns-webpic-qc.xhscdn.com/normalized.jpg" in urls
            assert "https://sns-webpic-qc.xhscdn.com/unsaved.jpg" not in urls

            db.close()

    def test_cache_content_persists_relevance_and_candidate_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                relevance_score=0.88,
                relevance_reason="fits profile",
                candidate_tier="primary",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["relevance_score"] == 0.88
            assert row["relevance_reason"] == "fits profile"
            assert row["candidate_tier"] == "primary"

            db.close()

    def test_cache_content_persists_topic_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1TOPIC",
                title="讲透中东局势",
                up_name="国际观察",
                source="search",
                topic_key="国际时事:地缘政治",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["topic_key"] == "国际时事:地缘政治"

            db.close()

    def test_cache_content_persists_style_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1STYLE",
                title="杀戮尖塔2 实机演示",
                up_name="游戏研究所",
                source="related_chain",
                style_key="game_strategy",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["style_key"] == "hands_on"

            db.close()

    def test_initialize_normalizes_legacy_style_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = Database(db_path)
            db.initialize()
            db.cache_content(
                "BV1LEGACY",
                title="旧版风格",
                up_name="legacy",
                source="search",
                style_key="deep_dive",
            )
            db.enqueue_discovery_candidates(
                [
                    DiscoveryCandidateWrite(
                        candidate_key="xhs:legacy-note",
                        source_platform="xiaohongshu",
                        source_strategy="xhs-extension-search",
                        content_id="legacy-note",
                        content_url="https://www.xiaohongshu.com/explore/legacy-note",
                        title="legacy note",
                    )
                ]
            )
            db.conn.execute(
                "UPDATE content_cache SET style_key = ? WHERE bvid = ?",
                ("story_doc", "BV1LEGACY"),
            )
            db.conn.execute(
                "UPDATE discovery_candidates SET style_key = ? WHERE candidate_key = ?",
                ("lifestyle", "xhs:legacy-note"),
            )
            db.conn.commit()
            db.close()

            migrated = Database(db_path)
            migrated.initialize()
            content_row = migrated.conn.execute(
                "SELECT style_key FROM content_cache WHERE bvid = ?",
                ("BV1LEGACY",),
            ).fetchone()
            candidate_row = migrated.conn.execute(
                "SELECT style_key FROM discovery_candidates WHERE candidate_key = ?",
                ("xhs:legacy-note",),
            ).fetchone()

            assert content_row["style_key"] == "story_immersion"
            assert candidate_row["style_key"] == "daily_wander"

            migrated.close()

    def test_cache_content_persists_pool_copy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1COPY",
                title="池子里的预生成文案",
                up_name="文案实验室",
                source="search",
                pool_expression="这条会接住你最近想把问题拆开的状态。",
                pool_topic_label="你最近那股想拆问题的劲头",
            )

            row = db.get_cached_content(limit=1)[0]

            assert row["pool_expression"] == "这条会接住你最近想把问题拆开的状态。"
            assert row["pool_topic_label"] == "你最近那股想拆问题的劲头"

            db.close()

    def test_cache_content_persists_body_text_and_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            content_id = "1790000000000000001"
            db.cache_content(
                content_id,
                title="A thread on systems",
                source="search",
                source_platform="twitter",
                content_id=content_id,
                content_url="https://x.com/handle/status/1790000000000000001",
                author_name="@handle",
                content_type="thread",
                body_text="1/ long-form note_tweet body ...",
            )

            cursor = db.conn.execute("SELECT * FROM content_cache WHERE bvid = ?", (content_id,))
            row = cursor.fetchone()
            assert row is not None
            # content_cache accepts and returns body_text / content_type.
            assert row["content_type"] == "thread"
            assert row["body_text"].startswith("1/ long-form")
            # bvid compatibility (Codex R1 M5): non-Bilibili rows reuse
            # content_id as bvid so existing bvid-keyed joins still match.
            assert row["bvid"] == content_id
            assert row["content_id"] == content_id

            db.close()

    def test_twitter_pool_candidate_round_trips_through_serve_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            content_id = "1790000000000000002"
            _seed_visible(
                db,
                content_id,
                title="A thread on systems",
                source="search",
                source_platform="twitter",
                content_id=content_id,
                content_url="https://x.com/handle/status/1790000000000000002",
                author_name="@handle",
                content_type="thread",
                body_text="1/ long-form note_tweet body ...",
                relevance_score=0.9,
            )

            candidates = db.get_pool_candidates(limit=10)
            row = next(c for c in candidates if c["bvid"] == content_id)
            assert row["content_type"] == "thread"
            assert row["body_text"].startswith("1/ long-form")
            assert row["source_platform"] == "twitter"

            db.close()

    def test_get_cached_content_returns_cached_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                view_count=100,
            )
            db.cache_content(
                "BV1B",
                title="Video B",
                up_name="UPB",
                source="trending",
                view_count=200,
            )

            cached = db.get_cached_content(limit=10)

            assert [item["bvid"] for item in cached] == ["BV1B", "BV1A"]
            assert cached[0]["source"] == "trending"

            db.close()

    def test_trim_explore_cluster_overflow_suppresses_excess_manufacturing_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index, score in enumerate((0.91, 0.89, 0.87, 0.83), start=1):
                db.cache_content(
                    f"BV1MFG{index}",
                    title=f"超级工厂制造纪录片 {index}",
                    up_name="工业观察",
                    source="explore",
                    topic_key="精密制造纪录片",
                    relevance_score=score,
                )
            db.cache_content(
                "BV1OTHER",
                title="科幻小说设定解析",
                up_name="科幻电台",
                source="explore",
                topic_key="科幻小说深度解析",
                relevance_score=0.8,
            )

            suppressed = db.trim_explore_cluster_overflow(max_per_cluster=2)

            assert suppressed == 2
            rows = db.get_cached_content(limit=10)
            fresh_manufacturing = [
                row
                for row in rows
                if row["source"] == "explore"
                and row["topic_key"] == "精密制造纪录片"
                and row["pool_status"] == "fresh"
            ]
            assert [row["bvid"] for row in fresh_manufacturing] == ["BV1MFG1", "BV1MFG2"]

            db.close()

    def test_trim_topic_group_overflow_suppresses_cross_source_excess(self) -> None:
        """A hot topic_group accumulated from multiple sources gets capped down
        to max_per_group, keeping the highest-scored items regardless of
        source."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # 5 items in 人工智能 group across 3 sources, varying scores
            for i, (score, source) in enumerate(
                [
                    (0.95, "related_chain"),
                    (0.92, "related_chain"),
                    (0.88, "search"),
                    (0.85, "explore"),
                    (0.80, "related_chain"),
                ]
            ):
                db.cache_content(
                    f"BV1AI{i}",
                    title=f"AI 内容 {i}",
                    up_name="UP",
                    source=source,
                    topic_key="人工智能",
                    topic_group="人工智能",
                    relevance_score=score,
                )
            # 1 item in a different group — must remain untouched
            db.cache_content(
                "BV1MUSIC",
                title="古典音乐讲解",
                up_name="UP",
                source="trending",
                topic_key="音乐",
                topic_group="音乐",
                relevance_score=0.7,
            )
            # 1 item with empty topic_group — must remain untouched
            db.cache_content(
                "BV1NOGROUP",
                title="未分组",
                up_name="UP",
                source="search",
                topic_key="random",
                topic_group="",
                relevance_score=0.6,
            )

            suppressed = db.trim_topic_group_overflow(max_per_group=2)

            assert suppressed == 3  # 5 AI items - 2 kept = 3 suppressed
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # Top 2 AI items by score survive (cross-source)
            assert by_bvid["BV1AI0"]["pool_status"] == "fresh"
            assert by_bvid["BV1AI1"]["pool_status"] == "fresh"
            assert by_bvid["BV1AI2"]["pool_status"] == "suppressed"
            assert by_bvid["BV1AI3"]["pool_status"] == "suppressed"
            assert by_bvid["BV1AI4"]["pool_status"] == "suppressed"
            # Unrelated topic + empty-group items untouched
            assert by_bvid["BV1MUSIC"]["pool_status"] == "fresh"
            assert by_bvid["BV1NOGROUP"]["pool_status"] == "fresh"

            db.close()

    def test_trim_topic_group_overflow_noop_when_under_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(3):
                db.cache_content(
                    f"BV1X{i}",
                    title=f"AI {i}",
                    up_name="UP",
                    source="search",
                    topic_group="人工智能",
                    relevance_score=0.8,
                )

            suppressed = db.trim_topic_group_overflow(max_per_group=5)
            assert suppressed == 0
            db.close()

    def test_cache_content_refreshes_previously_suppressed_items(self) -> None:
        """Re-discovering a 'suppressed' item must flip pool_status back to
        'fresh'. Suppression is an internal diversity decision (trim cuts,
        topic cap); when the discovery layer re-finds the item it deserves
        another shot. Without this, slow-churning sources like B站 trending
        get bottlenecked because hot BVIDs cached as 'suppressed' never
        recover. 'shown' / 'feedbacked' / 'purged_by_dislike' must NOT
        re-fresh — those reflect user-facing state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Seed items, then force them into different terminal states.
            for status in ("suppressed", "shown", "purged_by_dislike"):
                bvid = f"BV1{status}"
                db.cache_content(
                    bvid,
                    title=f"item {status}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.7,
                )
                db._execute_write(
                    "UPDATE content_cache SET pool_status = ? WHERE bvid = ?",
                    (status, bvid),
                )
            db.cache_content(
                "BV1suppressed_low",
                title="item suppressed low",
                up_name="UP",
                source="trending",
                relevance_score=0.20,
            )
            db._execute_write(
                "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
                ("BV1suppressed_low",),
            )
            db.cache_content(
                "BV1suppressed_expired",
                title="expired breaking item",
                up_name="UP",
                source="trending",
                relevance_score=0.8,
                published_at="2000-01-01T00:00:00Z",
                temporal_class="breaking",
                temporal_confidence=0.95,
                temporal_reason="价值依赖即时状态",
            )
            db._execute_write(
                "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
                ("BV1suppressed_expired",),
            )

            # Re-discover all three (simulates trending re-fetching same BVIDs)
            for status in ("suppressed", "shown", "purged_by_dislike"):
                db.cache_content(
                    f"BV1{status}",
                    title=f"item {status}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.8,
                )
            db.cache_content(
                "BV1suppressed_low",
                title="item suppressed low",
                up_name="UP",
                source="trending",
                relevance_score=0.20,
            )
            db.cache_content(
                "BV1suppressed_expired",
                **DiscoveredContent(
                    bvid="BV1suppressed_expired",
                    title="expired breaking item",
                    up_name="UP",
                    source_strategy="trending",
                    relevance_score=0.8,
                    published_at="2026-08-11T00:00:00Z",
                    published_label="刚刚",
                ).to_cache_kwargs(),
            )
            db.cache_content(
                "BV1suppressed_expired",
                **DiscoveredContent(
                    bvid="BV1suppressed_expired",
                    title="expired breaking item",
                    up_name="UP",
                    source_strategy="trending",
                    relevance_score=0.8,
                    published_at="2026-11-20T00:00:00Z",
                    published_label="未来",
                ).to_cache_kwargs(),
            )

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            # Suppressed re-fresh ✓
            assert by_bvid["BV1suppressed"]["pool_status"] == "fresh"
            # Low-score suppression is admission, not just diversity trim.
            assert by_bvid["BV1suppressed_low"]["pool_status"] == "suppressed"
            # A raw rediscovery cannot wash or revive legacy review evidence.
            assert by_bvid["BV1suppressed_expired"]["pool_status"] == "temporal_review_hold"
            assert by_bvid["BV1suppressed_expired"]["temporal_class"] == "breaking"
            assert by_bvid["BV1suppressed_expired"]["published_at"] == "2000-01-01T00:00:00Z"
            # Shown stays shown (user already saw)
            assert by_bvid["BV1shown"]["pool_status"] == "shown"
            # Disliked stays purged
            assert by_bvid["BV1purged_by_dislike"]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_trim_pool_share_quotas_protect_under_target_sources(self) -> None:
        """When trim is given platform quotas, over-quota platforms get
        suppressed first even if they have higher scores than under-quota
        platforms."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Bilibili has 5 items at score 0.95 (high), Douyin has 3 at 0.60 (low).
            # Total = 8, target = 6, so 2 must be suppressed.
            # Without share quotas: all low-score Douyin items get axed.
            # With quota bilibili=2: 3 of Bilibili's 5 are over-quota → those go first,
            # protecting Douyin entirely.
            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVT{i}",
                    title=f"T{i}",
                    up_name="DY",
                    source="dy-plugin-search",
                    source_platform="douyin",
                    content_url=f"https://www.douyin.com/video/{i}",
                    relevance_score=0.66,
                )

            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 2, "douyin": 4},
            )
            assert suppressed == 2

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # All Douyin kept (under quota of 4) — this is the protection.
            # Without share quotas, Douyin (low score) would get axed first.
            assert all(by_bvid[f"BVT{i}"]["pool_status"] == "fresh" for i in range(3))
            # Bilibili lost the bottom 2 (suppressed), kept top 3: 2 within quota
            # + 1 backfill from over-quota since target=6 had remaining slot.
            search_fresh = [
                bvid
                for bvid in (f"BVS{i}" for i in range(5))
                if by_bvid[bvid]["pool_status"] == "fresh"
            ]
            assert len(search_fresh) == 3
            assert by_bvid["BVS3"]["pool_status"] == "suppressed"
            assert by_bvid["BVS4"]["pool_status"] == "suppressed"
            db.close()

    def test_trim_pool_source_overflow_enforces_platform_hard_caps(self) -> None:
        """Platform shares reserve capacity; one platform must not fill another's slot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.80 + i / 100,
                )
            for i in range(8):
                db.cache_content(
                    f"XHS{i}",
                    title=f"X{i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_url=f"https://www.xiaohongshu.com/explore/XHS{i}?xsec_token=ABC=",
                    relevance_score=0.90 + i / 100,
                )
            db.cache_content(
                "dy:1",
                title="D1",
                up_name="DY",
                source="dy-plugin-search",
                source_platform="douyin",
                content_url="https://www.douyin.com/video/1",
                relevance_score=0.66,
            )

            suppressed = db.trim_pool_source_overflow(
                source_share_quotas={"bilibili": 5, "xiaohongshu": 2, "douyin": 2},
            )

            assert suppressed == 6
            assert db.count_pool_candidates_by_source() == {
                "bilibili": 5,
                "xiaohongshu": 2,
                "douyin": 1,
            }
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            xhs_fresh = [
                bvid
                for bvid in (f"XHS{i}" for i in range(8))
                if by_bvid[bvid]["pool_status"] == "fresh"
            ]
            assert len(xhs_fresh) == 2
            db.close()

    def test_trim_pool_source_overflow_sheds_pending_xhs_before_linkable_rows(self) -> None:
        """Raw-ceiling source trim should drop unopenable XHS rows first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(50):
                _seed_visible(
                    db,
                    f"xhs-linkable-{i}",
                    title=f"linkable {i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_id=f"xhs-linkable-{i}",
                    content_url=(
                        f"https://www.xiaohongshu.com/explore/xhs-linkable-{i}?xsec_token=ABC="
                    ),
                    relevance_score=0.70 + i / 1000,
                )
            for i in range(60):
                _seed_visible(
                    db,
                    f"xhs-pending-{i}",
                    title=f"pending {i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_id=f"xhs-pending-{i}",
                    content_url=f"https://www.xiaohongshu.com/explore/xhs-pending-{i}",
                    relevance_score=0.90 + i / 1000,
                )

            suppressed = db.trim_pool_source_overflow(source_share_quotas={"xiaohongshu": 60})

            assert suppressed == 50
            rows = db.get_cached_content(limit=120)
            by_bvid = {row["bvid"]: row for row in rows}
            assert all(by_bvid[f"xhs-linkable-{i}"]["pool_status"] == "fresh" for i in range(50))
            pending_suppressed = sum(
                1 for i in range(60) if by_bvid[f"xhs-pending-{i}"]["pool_status"] == "suppressed"
            )
            assert pending_suppressed == 50
            assert db.count_pool_raw_material_by_source() == {"xiaohongshu": 60}
            db.close()

    def test_trim_pool_legacy_score_only_when_no_quotas(self) -> None:
        """Without source_share_quotas, the trim must keep its old score-first
        behavior — that's the path used by callers that don't care about
        per-source diversity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(5):
                db.cache_content(
                    f"BVHIGH{i}",
                    title=f"H{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVLOW{i}",
                    title=f"L{i}",
                    up_name="UP",
                    source="trending",
                    relevance_score=0.66,
                )

            suppressed = db.trim_pool_to_target_count(target=5)
            assert suppressed == 3

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # All low-score trending suppressed, all high-score search kept
            assert all(by_bvid[f"BVHIGH{i}"]["pool_status"] == "fresh" for i in range(5))
            assert all(by_bvid[f"BVLOW{i}"]["pool_status"] == "suppressed" for i in range(3))
            db.close()

    def test_trim_pool_protects_under_quota_source_when_untracked_sources_present(
        self,
    ) -> None:
        """The bug this prevents: untracked sources eat pool slots,
        pushing total > target. The trim must suppress untracked items before
        cutting under-quota tracked sources (Douyin). Without this guard,
        sum(in_quota) > target leads to score-based cuts that hit Douyin
        first because trending scores are systematically lower."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Bilibili at quota (5/5), Douyin under quota (2 of 4), manual import
            # (4 untracked).
            # Total = 11, target = 8, so 3 must go.
            # Bug-prone behavior: Douyin scores are 0.5 (low), so naïve
            # trim would axe both Douyin items.
            # Correct behavior: untracked manual-import items get cut first.
            for i in range(5):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(2):
                db.cache_content(
                    f"BVT{i}",
                    title=f"T{i}",
                    up_name="DY",
                    source="dy-plugin-search",
                    source_platform="douyin",
                    content_url=f"https://www.douyin.com/video/{i}",
                    relevance_score=0.66,
                )
            for i in range(4):
                db.cache_content(
                    f"BVM{i}",
                    title=f"X{i}",
                    up_name="UP",
                    source="manual-import",
                    relevance_score=0.70,
                )

            suppressed = db.trim_pool_to_target_count(
                target=8,
                source_share_quotas={"bilibili": 5, "douyin": 4},
            )
            assert suppressed == 3

            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            # Douyin fully protected (under quota, no items lost)
            assert all(by_bvid[f"BVT{i}"]["pool_status"] == "fresh" for i in range(2))
            # Bilibili fully protected (at quota, no over-quota items)
            assert all(by_bvid[f"BVS{i}"]["pool_status"] == "fresh" for i in range(5))
            # 3 of the 4 manual-import items suppressed (lowest score among negotiable)
            manual_fresh = sum(1 for i in range(4) if by_bvid[f"BVM{i}"]["pool_status"] == "fresh")
            assert manual_fresh == 1
            db.close()

    def test_count_pool_candidates_by_source_collapses_xhs_source_family(self) -> None:
        """Xiaohongshu extension channels count as one source family.

        The refresh controller consumes this summary to decide which Bilibili
        strategies are deficient. If raw xhs-extension-* names leak through,
        xhs content is invisible to the source-balance accounting.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BVSOURCE",
                title="S",
                up_name="UP",
                source="search",
                relevance_score=0.90,
            )
            db.cache_content(
                "XHS-TASK-1",
                title="X1",
                up_name="XHS",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_url=("https://www.xiaohongshu.com/explore/XHS-TASK-1?xsec_token=ABC="),
                relevance_score=0.90,
            )
            db.cache_content(
                "XHS-SEARCH-1",
                title="X2",
                up_name="XHS",
                source="xhs-extension-search",
                source_platform="xiaohongshu",
                content_url=("https://www.xiaohongshu.com/explore/XHS-SEARCH-1?xsec_token=ABC="),
                relevance_score=0.90,
            )
            db.cache_content(
                "XHS-LEGACY-1",
                title="X3",
                up_name="XHS",
                source="xhs-extension-profile",
                content_url=("https://www.xiaohongshu.com/explore/XHS-LEGACY-1?xsec_token=ABC="),
                relevance_score=0.90,
            )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 1, "xiaohongshu": 3}
            db.close()

    def test_count_pool_candidates_by_source_collapses_douyin_source_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BVSOURCE",
                title="S",
                up_name="UP",
                source="search",
                relevance_score=0.90,
            )
            db.cache_content(
                "dy:1",
                title="D1",
                up_name="DY",
                source="dy-direct-search",
                source_platform="douyin",
                content_id="1",
                content_url="https://www.douyin.com/video/1",
                relevance_score=0.90,
            )
            db.cache_content(
                "dy:2",
                title="D2",
                up_name="DY",
                source="dy-direct-hot",
                source_platform="douyin",
                content_id="2",
                content_url="https://www.douyin.com/video/2",
                relevance_score=0.90,
            )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 1, "douyin": 2}
            db.close()

    def test_count_pool_candidates_by_source_collapses_bilibili_source_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for source in ("search", "related_chain", "trending", "explore"):
                db.cache_content(
                    f"BV-{source}",
                    title=source,
                    up_name="UP",
                    source=source,
                    relevance_score=0.90,
                )

            counts = db.count_pool_candidates_by_source()

            assert counts == {"bilibili": 4}
            db.close()

    def test_zhihu_pool_accounting_collapses_blank_platform_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index, source in enumerate(("zhihu-creator", "zhihu-hot", "zhihu-feed")):
                _seed_visible(
                    db,
                    f"zhihu:answer:{index}",
                    title=f"知乎回答 {index}",
                    source=source,
                    source_platform="",
                    content_id=f"answer:{index}",
                    content_url=f"https://www.zhihu.com/question/1/answer/{index}",
                )

            assert db.count_pool_available_candidates_by_source() == {"zhihu": 3}
            assert db.count_pool_raw_material_by_source() == {"zhihu": 3}
            db.close()

    def test_zhihu_pool_accounting_overrides_bilibili_cache_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(
                db,
                "zhihu:answer:cache-default",
                title="知乎缓存默认平台回归",
                source="zhihu-hot",
                source_platform="bilibili",
                content_id="answer:cache-default",
                content_url="https://www.zhihu.com/question/1/answer/cache-default",
            )

            assert db.count_pool_available_candidates_by_source() == {"zhihu": 1}
            assert db.count_pool_raw_material_by_source() == {"zhihu": 1}
            db.close()

    def test_trim_pool_share_quotas_protect_xhs_source_family(self) -> None:
        """Xiaohongshu rows are protected by the xiaohongshu quota.

        Raw xhs-extension-* sources must not be treated as generic untracked
        items; otherwise a high-scored unknown source can crowd xhs out even
        when the xiaohongshu source family is under its quota.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(3):
                db.cache_content(
                    f"BVS{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                )
            for i in range(3):
                db.cache_content(
                    f"BVMANUAL{i}",
                    title=f"M{i}",
                    up_name="UP",
                    source="manual-import",
                    relevance_score=0.90,
                )
            for i, source in enumerate(
                ("xhs-extension-task", "xhs-extension-search", "xhs-extension-profile")
            ):
                db.cache_content(
                    f"XHS-QUOTA-{i}",
                    title=f"X{i}",
                    up_name="XHS",
                    source=source,
                    source_platform="xiaohongshu",
                    content_url=(
                        f"https://www.xiaohongshu.com/explore/XHS-QUOTA-{i}?xsec_token=ABC="
                    ),
                    relevance_score=0.66,
                )

            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )

            assert suppressed == 3
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            assert all(by_bvid[f"XHS-QUOTA-{i}"]["pool_status"] == "fresh" for i in range(3))
            assert all(by_bvid[f"BVMANUAL{i}"]["pool_status"] == "suppressed" for i in range(3))
            db.close()

    def test_reactivate_under_quota_pool_sources_restores_suppressed_xhs_family(
        self,
    ) -> None:
        """A full Bilibili pool can make room for existing suppressed xhs rows.

        This covers the production shape where xhs rows were previously
        suppressed while raw xhs-extension-* sources were invisible to source
        quotas. Once xiaohongshu has its own family quota, high-scored
        suppressed rows should be allowed back into the fresh pool and then
        normal cap trimming removes over-quota sources.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(6):
                _seed_visible(
                    db,
                    f"BVSRC{i}",
                    title=f"S{i}",
                    up_name="UP",
                    source="search",
                    relevance_score=0.95,
                    topic_group=f"搜索分组{i}",
                )
            for i in range(3):
                note_id = f"xhs-reactivate-{i}"
                _seed_visible(
                    db,
                    note_id,
                    title=f"X{i}",
                    up_name="XHS",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_url=(f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=ABC="),
                    relevance_score=0.80,
                    topic_group=f"小红书分组{i}",
                )
                db._execute_write(
                    "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
                    (note_id,),
                )

            reactivated = db.reactivate_under_quota_pool_sources(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )
            suppressed = db.trim_pool_to_target_count(
                target=6,
                source_share_quotas={"bilibili": 3, "xiaohongshu": 3},
            )

            assert reactivated == 3
            assert suppressed == 3
            rows = db.get_cached_content(limit=20)
            by_bvid = {row["bvid"]: row for row in rows}
            assert all(by_bvid[f"xhs-reactivate-{i}"]["pool_status"] == "fresh" for i in range(3))
            search_fresh = sum(
                1 for i in range(6) if by_bvid[f"BVSRC{i}"]["pool_status"] == "fresh"
            )
            assert search_fresh == 3
            assert db.count_pool_candidates() == 6
            db.close()

    def test_reactivate_under_quota_pool_sources_respects_raw_material_capacity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for i in range(3):
                _seed_visible(
                    db,
                    f"xhs-pending-capacity-{i}",
                    title=f"pending {i}",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_id=f"xhs-pending-capacity-{i}",
                    content_url=f"https://www.xiaohongshu.com/explore/xhs-pending-capacity-{i}",
                    relevance_score=0.90,
                )
            for i in range(3):
                _seed_visible(
                    db,
                    f"xhs-suppressed-capacity-{i}",
                    title=f"suppressed {i}",
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_id=f"xhs-suppressed-capacity-{i}",
                    content_url=(
                        f"https://www.xiaohongshu.com/explore/xhs-suppressed-capacity-{i}"
                        "?xsec_token=ABC="
                    ),
                    relevance_score=0.80,
                )
                db._execute_write(
                    "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
                    (f"xhs-suppressed-capacity-{i}",),
                )

            reactivated = db.reactivate_under_quota_pool_sources(
                target=3,
                source_share_quotas={"xiaohongshu": 3},
                raw_source_share_quotas={"xiaohongshu": 3},
            )

            assert reactivated == 0
            assert db.count_pool_raw_material_by_source() == {"xiaohongshu": 3}
            db.close()

    def test_purge_pool_by_disliked_topics_matches_topic_key_exact(self) -> None:
        """An exact topic_key match should flip pool_status to purged_by_dislike."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1ghost",
                title="鬼畜全明星2026",
                up_name="鬼畜区UP",
                source="trending",
                topic_key="鬼畜",
                pool_topic_label="娱乐",
            )
            db.cache_content(
                "BV2ai",
                title="深度学习与Transformer",
                up_name="AI教程君",
                source="search",
                topic_key="AI技术",
                pool_topic_label="知识",
            )

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            assert by_bvid["BV1ghost"]["pool_status"] == "purged_by_dislike"
            assert by_bvid["BV2ai"]["pool_status"] == "fresh"

            db.close()

    def test_purge_pool_by_disliked_topics_matches_title_substring(self) -> None:
        """Substring match on title should catch videos even when topic_key differs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Topic_key is "年终总结" but title contains "鬼畜"
            db.cache_content(
                "BV1mix",
                title="跨年鬼畜合集",
                up_name="混剪UP",
                source="explore",
                topic_key="年终总结",
                pool_topic_label="娱乐",
            )
            db.cache_content(
                "BV2pure",
                title="纯知识内容",
                up_name="知识UP",
                source="search",
                topic_key="科技",
                pool_topic_label="知识",
            )

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            assert by_bvid["BV1mix"]["pool_status"] == "purged_by_dislike"
            assert by_bvid["BV2pure"]["pool_status"] == "fresh"

            db.close()

    def test_purge_pool_by_disliked_topics_matches_pool_topic_label(self) -> None:
        """Matching on pool_topic_label should also purge candidates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1label",
                title="各种奇葩挑战",
                up_name="挑战UP",
                source="trending",
                topic_key="挑战视频",
                pool_topic_label="整蛊",
            )

            purged = db.purge_pool_by_disliked_topics(["整蛊"])
            assert purged == 1

            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_mark_pool_purged_by_reinit_retires_active_rows_only(self) -> None:
        """Force re-init retires fresh/shown/suppressed rows, not prior purges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for bvid, status in (
                ("BV1fresh", "fresh"),
                ("BV2shown", "shown"),
                ("BV3suppressed", "suppressed"),
                ("BV4disliked", "purged_by_dislike"),
            ):
                db.cache_content(
                    bvid,
                    title=f"内容 {bvid}",
                    up_name="UP",
                    source="search",
                    topic_key="科技",
                    pool_topic_label="知识",
                )
                if status != "fresh":
                    db.conn.execute(
                        "UPDATE content_cache SET pool_status = ? WHERE bvid = ?",
                        (status, bvid),
                    )
            db.conn.commit()

            purged = db.mark_pool_purged_by_reinit()
            assert purged == 3  # fresh + shown + suppressed

            rows = db.get_cached_content(limit=10)
            by_bvid = {row["bvid"]: row for row in rows}
            assert by_bvid["BV1fresh"]["pool_status"] == "purged_by_reinit"
            assert by_bvid["BV2shown"]["pool_status"] == "purged_by_reinit"
            assert by_bvid["BV3suppressed"]["pool_status"] == "purged_by_reinit"
            # A previous dislike purge is terminal and stays as-is.
            assert by_bvid["BV4disliked"]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_purge_pool_by_disliked_topics_skips_already_recommended(self) -> None:
        """Items already in the recommendations table must not be purged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1kept",
                title="鬼畜经典",
                up_name="怀旧UP",
                source="trending",
                topic_key="鬼畜",
                pool_topic_label="娱乐",
            )
            # Insert a recommendation row pointing at this bvid
            db.conn.execute(
                """
                INSERT INTO recommendations (
                    bvid, expression, topic, confidence, created_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                ("BV1kept", "这条鬼畜应该对味", "娱乐", 0.9),
            )
            db.conn.commit()

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 0

            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "fresh", (
                "Already-recommended items must be preserved for history audit"
            )
            db.close()

    def test_purge_pool_by_disliked_topics_skips_non_fresh_items(self) -> None:
        """Only fresh candidates should be touched; shown/stale are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1shown",
                title="鬼畜视频",
                up_name="鬼畜UP",
                source="trending",
                topic_key="鬼畜",
            )
            db.cache_content(
                "BV2fresh",
                title="另一个鬼畜视频",
                up_name="鬼畜UP",
                source="trending",
                topic_key="鬼畜",
            )
            db.conn.execute(
                "UPDATE content_cache SET pool_status='shown' WHERE bvid = ?",
                ("BV1shown",),
            )
            db.conn.commit()

            purged = db.purge_pool_by_disliked_topics(["鬼畜"])
            assert purged == 1, "Only the fresh item should be purged"

            rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
            assert rows["BV1shown"]["pool_status"] == "shown"
            assert rows["BV2fresh"]["pool_status"] == "purged_by_dislike"
            db.close()

    def test_purge_pool_by_disliked_topics_empty_list_is_noop(self) -> None:
        """Empty or whitespace-only topics should do nothing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content("BV1", title="test", up_name="u", source="s", topic_key="k")
            assert db.purge_pool_by_disliked_topics([]) == 0
            assert db.purge_pool_by_disliked_topics(["", "  "]) == 0
            rows = db.get_cached_content(limit=10)
            assert rows[0]["pool_status"] == "fresh"
            db.close()

    def test_purge_pool_by_disliked_topics_multi_topic_batch(self) -> None:
        """Multiple topics in one call should purge any matching item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BV1", title="鬼畜A", up_name="u", source="s", topic_key="鬼畜")
            db.cache_content("BV2", title="恐怖片B", up_name="u", source="s", topic_key="恐怖")
            db.cache_content("BV3", title="AI教程", up_name="u", source="s", topic_key="科技")

            purged = db.purge_pool_by_disliked_topics(["鬼畜", "恐怖"])
            assert purged == 2

            rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
            assert rows["BV1"]["pool_status"] == "purged_by_dislike"
            assert rows["BV2"]["pool_status"] == "purged_by_dislike"
            assert rows["BV3"]["pool_status"] == "fresh"
            db.close()

    def test_purge_pool_by_disliked_topics_matches_topic_group(self) -> None:
        """topic_group column should be matched too (added by migration)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1",
                title="某内容",
                up_name="u",
                source="s",
                topic_key="子话题",
                topic_group="大分类",
            )
            purged = db.purge_pool_by_disliked_topics(["大分类"])
            assert purged == 1
            db.close()

    def test_purge_scan_filters_temporal_staleness_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(
                db,
                "BVSTALEPURGE",
                title="已经过期但最近重抓",
                source="search",
                published_at="2000-01-01T00:00:00Z",
                temporal_class="breaking",
                temporal_confidence=0.95,
                temporal_reason="价值依赖即时状态",
            )
            _seed_visible(
                db,
                "BVELIGIBLEPURGE",
                title="仍应接受语义避雷扫描",
                source="search",
            )
            db.conn.execute(
                "UPDATE content_cache SET discovered_at = ? WHERE bvid = ?",
                ("2099-01-01T00:00:00Z", "BVSTALEPURGE"),
            )
            db.conn.commit()

            rows = db.get_fresh_pool_candidates_for_purge_scan(limit=1)

            assert [row["bvid"] for row in rows] == ["BVELIGIBLEPURGE"]
            db.close()

    def test_query_events_supports_type_keyword_and_time_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            now = datetime.now()
            older = (now - timedelta(days=2)).isoformat(sep=" ")
            recent = now.isoformat(sep=" ")

            db.conn.execute(
                """
                INSERT INTO events (event_type, url, title, context, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "view",
                    "https://www.bilibili.com/video/BVOLD",
                    "Old Video",
                    "{}",
                    '{"bvid": "BVOLD"}',
                    older,
                ),
            )
            db.conn.execute(
                """
                INSERT INTO events (event_type, url, title, context, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "search",
                    "https://search.bilibili.com/all?keyword=ai",
                    "AI Search",
                    "{}",
                    '{"keyword": "ai"}',
                    recent,
                ),
            )
            db.conn.commit()

            events = db.query_events(
                event_types=["search"],
                start_time=now - timedelta(hours=1),
                keyword="ai",
            )

            assert len(events) == 1
            assert events[0]["event_type"] == "search"
            assert "AI Search" in events[0]["title"]

            db.close()

    def test_count_events_by_type_returns_grouped_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_event("view", title="video-1")
            db.insert_event("view", title="video-2")
            db.insert_event("click", title="card")

            stats = db.count_events_by_type()

            assert stats == {"click": 1, "view": 2}

            db.close()

    def test_list_chat_turns_returns_recent_turns_in_display_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for idx in range(5):
                db.create_chat_turn(
                    turn_id=f"turn-{idx}",
                    session="popup",
                    scope="chat",
                    message=f"message-{idx}",
                )
                db.conn.execute(
                    """
                    UPDATE chat_turns
                    SET created_at = datetime('now', ?)
                    WHERE turn_id = ?
                    """,
                    (f"+{idx} minutes", f"turn-{idx}"),
                )
                db.conn.commit()

            turns = db.list_chat_turns(session="popup", scope="chat", limit=3)

            assert [turn["turn_id"] for turn in turns] == [
                "turn-2",
                "turn-3",
                "turn-4",
            ]

            db.close()

    def test_get_unrecommended_content_excludes_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1A",
                title="Video A",
                up_name="UPA",
                source="search",
                view_count=100,
                relevance_score=0.90,
            )
            db.cache_content(
                "BV1B",
                title="Video B",
                up_name="UPB",
                source="trending",
                view_count=200,
                relevance_score=0.90,
            )
            db.insert_recommendation("BV1A", confidence=0.91, presented=0)

            items = db.get_unrecommended_content(limit=10)

            assert [item["bvid"] for item in items] == ["BV1B"]

            db.close()

    def test_get_unrecommended_content_filters_platform_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(60):
                db.cache_content(
                    f"reddit:{index}",
                    content_id=str(index),
                    source_platform="reddit",
                    source="reddit-hot",
                    relevance_score=0.99,
                )
            db.cache_content(
                "BV1BILI",
                source_platform="bilibili",
                source="trending",
                relevance_score=0.80,
            )
            # Legacy Bilibili rows may have a blank source_platform.
            db.cache_content(
                "BV1LEGACY",
                source_platform="",
                source="search",
                relevance_score=0.79,
            )

            items = db.get_unrecommended_content(
                limit=10,
                source_platforms=["bili"],
            )

            assert [item["bvid"] for item in items] == ["BV1BILI", "BV1LEGACY"]
            assert {str(item["source_platform"] or "bilibili") for item in items} == {"bilibili"}

            db.close()

    def test_get_unrecommended_content_orders_by_tier_then_relevance_and_recency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1BACK",
                title="补货高分",
                up_name="UPA",
                source="search",
                view_count=1000,
                relevance_score=0.95,
                candidate_tier="backfill",
            )
            db.cache_content(
                "BV1OLD",
                title="主候选旧",
                up_name="UPB",
                source="search",
                view_count=20,
                relevance_score=0.82,
                candidate_tier="primary",
            )
            db.cache_content(
                "BV1NEW",
                title="主候选新",
                up_name="UPC",
                source="search",
                view_count=10,
                relevance_score=0.82,
                candidate_tier="primary",
            )
            db.conn.execute(
                "UPDATE content_cache SET last_scored_at = ? WHERE bvid = ?",
                ("2026-03-09 08:00:00", "BV1OLD"),
            )
            db.conn.execute(
                "UPDATE content_cache SET last_scored_at = ? WHERE bvid = ?",
                ("2026-03-10 08:00:00", "BV1NEW"),
            )
            db.conn.commit()

            items = db.get_unrecommended_content(limit=10)

            assert [item["bvid"] for item in items] == ["BV1NEW", "BV1OLD", "BV1BACK"]

            db.close()

    def test_get_unrecommended_content_filters_temporal_stale_before_limit(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-backfill.db")
        db.initialize()
        _seed_visible(
            db,
            "BVSTALEBACKFILL",
            title="过期高分 backfill",
            source="search",
            relevance_score=0.99,
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.95,
            temporal_reason="价值依赖即时状态",
        )
        _seed_visible(
            db,
            "BVDURABLEBACKFILL",
            title="长期较低分 backfill",
            source="search",
            relevance_score=0.80,
            published_at="2000-01-01T00:00:00Z",
            temporal_class="evergreen",
            temporal_confidence=0.95,
            temporal_reason="核心价值长期有效",
        )

        items = db.get_unrecommended_content(limit=1)

        assert [item["bvid"] for item in items] == ["BVDURABLEBACKFILL"]
        assert items[0]["temporal_class"] == "evergreen"
        assert float(items[0]["temporal_confidence"]) == pytest.approx(0.95)
        db.close()

    def test_get_pool_candidates_skips_shown_and_feedbacked_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
                relevance_reason="你会想点开这种把事情讲透的内容。",
            )
            _seed_visible(
                db,
                "BV1SHOWN",
                title="已经展示",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
                relevance_reason="这条已经展示过。",
            )
            _seed_visible(
                db,
                "BV1FB",
                title="已经反馈",
                up_name="UPC",
                source="search",
                relevance_score=0.93,
                relevance_reason="这条已经被反馈过。",
            )
            _seed_visible(
                db,
                "BV1REC",
                title="已经进过推荐表",
                up_name="UPD",
                source="search",
                relevance_score=0.89,
                relevance_reason="这条已经生成过推荐。",
            )
            db.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'shown', recommended_at = CURRENT_TIMESTAMP "
                "WHERE bvid = 'BV1SHOWN'"
            )
            db.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'feedbacked', feedback_type = 'dislike', "
                "feedback_at = CURRENT_TIMESTAMP WHERE bvid = 'BV1FB'"
            )
            db.insert_recommendation("BV1REC", confidence=0.6)
            db.conn.commit()

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["BV1FRESH"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_excludes_delight_claimed_rows(self) -> None:
        """Surprise-channel rows never enter the regular feed.

        A row is delight-claimed when it was delivered as a surprise
        (delight_notified=1) or has a score above the threshold with its exact
        formal-copy snapshot ready. An unsynchronized evaluator reason must
        not claim the row, and an uncopied high-score row remains available to
        the expression-copy backlog. Sub-threshold delight scores keep the row
        servable. count_pool_candidates must agree with get_pool_candidates so
        the "还有 N 条" display never overstates what serve() can load.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1NORMAL",
                title="普通候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
                topic_group="组A",
            )
            _seed_visible(
                db,
                "BV1CLAIM",
                title="惊喜队列候选",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
                topic_group="组B",
            )
            _seed_visible(
                db,
                "BV1READ",
                title="已浏览的惊喜",
                up_name="UPC",
                source="search",
                relevance_score=0.94,
                topic_group="组C",
            )
            _seed_visible(
                db,
                "BV1LOW",
                title="delight低分未达标",
                up_name="UPD",
                source="search",
                relevance_score=0.93,
                topic_group="组D",
            )
            _seed_visible(
                db,
                "BV1UNSYNC",
                title="惊喜快照尚未同步",
                up_name="UPE",
                source="search",
                relevance_score=0.92,
                topic_group="组E",
            )
            # Currently delight-eligible: in the surprise queue right now.
            db.update_delight_score(
                "BV1CLAIM",
                delight_score=0.85,
                delight_reason="测试推荐文案",
                delight_hook="测试主题",
            )
            # Delivered as a surprise then read — must stay out of the feed.
            db.update_delight_score(
                "BV1READ",
                delight_score=0.9,
                delight_reason="测试推荐文案",
                delight_hook="测试主题",
            )
            db.mark_delight_notified("BV1READ")
            # Scored but below threshold and without metadata: not claimed.
            db.update_delight_score(
                "BV1LOW",
                delight_score=0.4,
                delight_reason="",
                delight_hook="",
            )
            # Above the default score floor, but a stale evaluator snapshot is
            # not proof that the profile-aware scorer admitted it to delight.
            db.conn.execute(
                """
                UPDATE content_cache
                SET delight_score = 0.85,
                    delight_reason = '评估器内部判断',
                    delight_hook = '高契合'
                WHERE bvid = 'BV1UNSYNC'
                """
            )
            db.conn.commit()

            items = db.get_pool_candidates(limit=10)

            assert {item["bvid"] for item in items} == {
                "BV1NORMAL",
                "BV1LOW",
                "BV1UNSYNC",
            }
            assert db.count_pool_candidates() == 3

            db.close()

    def test_get_pool_candidates_and_count_exclude_low_relevance_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1HIGH",
                title="高分候选",
                up_name="UPA",
                source="search",
                relevance_score=0.82,
            )
            _seed_visible(
                db,
                "BV1LOW",
                title="低分脏数据",
                up_name="UPB",
                source="search",
                relevance_score=0.30,
            )

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["BV1HIGH"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_pool_serving_allows_only_exact_explore_relaxed_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BVEXP", source="explore", relevance_score=0.58)
            _seed_visible(db, "BVTREND", source="trending", relevance_score=0.58)
            _seed_visible(db, "BVLOOKALIKE", source="explore-backfill", relevance_score=0.58)

            assert [row["bvid"] for row in db.get_pool_candidates(limit=10)] == ["BVEXP"]
            assert [row["bvid"] for row in db.get_unrecommended_content(limit=10)] == ["BVEXP"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_returns_topic_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1POOL",
                title="AI 模型能力边界",
                up_name="技术拆机局",
                source="search",
                relevance_score=0.91,
                topic_key="AI:大模型",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["topic_key"] == "AI:大模型"

            db.close()

    def test_get_pool_candidates_returns_style_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1STYLEPOOL",
                title="智慧城市空镜素材",
                up_name="视觉资料库",
                source="explore",
                relevance_score=0.84,
                style_key="visual_showcase",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["style_key"] == "aesthetic_browse"

            db.close()

    def test_get_pool_candidates_returns_precomputed_copy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BV1PRE",
                title="预生成测试",
                up_name="实验频道",
                source="explore",
                relevance_score=0.84,
                pool_expression="这条先给你备好了推荐理由。",
                pool_topic_label="先备好的那股味儿",
                style_key="tutorial",
                topic_group="测试分组",
            )

            items = db.get_pool_candidates(limit=10)

            assert items[0]["pool_expression"] == "这条先给你备好了推荐理由。"
            assert items[0]["pool_topic_label"] == "先备好的那股味儿"

            db.close()

    def test_get_pool_candidates_skips_rows_without_precomputed_copy(self) -> None:
        """v0.3.57+: pool gate — rows without pool_expression / pool_topic_label
        must not be returned by get_pool_candidates, even if pool_status='fresh'.

        This eliminates the race window between discovery (which writes
        pool_status='fresh' with empty copy) and precompute_pool_copy (which
        fills the LLM-generated expression/topic_label 60-90s later). Without
        this gate, serve() would pick the empty row and fall back to the
        _fallback_expression template ("这条切口挺顺的，先丢给你看看…").
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # Three rows: empty copy / only expression / fully filled.
            db.cache_content(
                "BVNOCOPY",
                title="未 precompute",
                source="search",
                relevance_score=0.9,
            )
            db.cache_content(
                "BVHALF",
                title="半 precompute",
                source="search",
                relevance_score=0.85,
                pool_expression="LLM 文案",
                # pool_topic_label intentionally empty
            )
            db.cache_content(
                "BVDONE",
                title="已 precompute",
                source="search",
                relevance_score=0.8,
                pool_expression="LLM 文案",
                pool_topic_label="LLM topic",
                style_key="tutorial",
                topic_group="测试分组",
            )

            rows = db.get_pool_candidates(limit=10)
            assert [r["bvid"] for r in rows] == ["BVDONE"]

            db.close()

    def test_count_pool_candidates_respects_precompute_gate(self) -> None:
        """v0.3.57+: count_pool_candidates must align with get_pool_candidates,
        otherwise popup '还有 N 条' would be misleading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("a", title="a", source="search", relevance_score=0.70)
            db.cache_content(
                "b",
                title="b",
                source="search",
                relevance_score=0.70,
                style_key="tutorial",
                topic_group="测试分组",
            )
            db.update_pool_copy("b", expression="x", topic_label="y")

            assert db.count_pool_candidates() == 1

            db.close()

    def test_count_pool_candidates_respects_default_topic_group_window(self) -> None:
        """The public available count uses the same topic_group cap as pool load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(5):
                _seed_visible(
                    db,
                    f"BVAI{index}",
                    title=f"AI 候选 {index}",
                    source="search",
                    topic_group="人工智能",
                    relevance_score=0.95 - index * 0.01,
                )
            _seed_visible(
                db,
                "BVDOC",
                title="纪录片候选",
                source="explore",
                topic_group="人物纪录",
                relevance_score=0.75,
            )

            assert db.count_pool_candidates() == 4
            assert db.count_pool_candidates(max_per_topic_group=0) == 6
            rows = db.get_pool_candidates(limit=10)
            assert len(rows) == 4
            assert [row["topic_group"] for row in rows].count("人工智能") == 3

            db.close()

    def test_count_pool_available_candidates_by_source_uses_global_topic_window(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV-SHARED-1",
                title="共享主题 1",
                source="search",
                topic_group="共享主题",
                relevance_score=0.99,
            )
            _seed_visible(
                db,
                "xhs-shared-1",
                title="共享主题 2",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-shared-1",
                content_url="https://www.xiaohongshu.com/explore/xhs-shared-1?xsec_token=ABC=",
                topic_group="共享主题",
                relevance_score=0.98,
            )
            _seed_visible(
                db,
                "BV-SHARED-2",
                title="共享主题 3",
                source="related_chain",
                topic_group="共享主题",
                relevance_score=0.97,
            )
            _seed_visible(
                db,
                "xhs-shared-2",
                title="共享主题 4",
                source="xhs-extension-search",
                source_platform="xiaohongshu",
                content_id="xhs-shared-2",
                content_url="https://www.xiaohongshu.com/explore/xhs-shared-2?xsec_token=ABC=",
                topic_group="共享主题",
                relevance_score=0.96,
            )
            _seed_visible(
                db,
                "dy-other-1",
                title="其他主题",
                source="dy-plugin-search",
                source_platform="douyin",
                content_id="dy-other-1",
                content_url="https://www.douyin.com/video/dy-other-1",
                topic_group="其他主题",
                relevance_score=0.66,
            )

            counts = db.count_pool_available_candidates_by_source()

            assert counts == {"bilibili": 2, "xiaohongshu": 1, "douyin": 1}
            assert sum(counts.values()) == db.count_pool_candidates()
            db.close()

    def test_pool_platform_availability_total_matches_sum_by_platform(self) -> None:
        """Platform-scoped tab counts must partition the canonical available set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BV-AVAIL-1", title="B 站一", source="search", topic_group="G1")
            _seed_visible(db, "BV-AVAIL-2", title="B 站二", source="trending", topic_group="G2")
            _seed_visible(
                db,
                "zhihu-avail-1",
                title="知乎一",
                source="zhihu-hot",
                source_platform="zhihu",
                content_id="zhihu-avail-1",
                content_url="https://www.zhihu.com/answer/1",
                topic_group="G3",
            )
            _seed_visible(
                db,
                "zhihu-avail-2",
                title="知乎二",
                source="zhihu-search",
                source_platform="zhihu",
                content_id="zhihu-avail-2",
                content_url="https://www.zhihu.com/answer/2",
                topic_group="G4",
            )
            _seed_visible(
                db,
                "xhs-avail-1",
                title="小红书一",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-avail-1",
                content_url="https://www.xiaohongshu.com/explore/xhs-avail-1?xsec_token=ABC=",
                topic_group="G5",
            )

            snapshot = db.load_pool_platform_availability()

            assert snapshot.total_available == db.count_pool_candidates()
            assert sum(snapshot.by_platform.values()) == snapshot.total_available
            assert snapshot.by_platform == {"bilibili": 2, "zhihu": 2, "xiaohongshu": 1}
            db.close()

    def test_pool_platform_availability_groups_legacy_source_strategies(self) -> None:
        """Legacy rows without source_platform still land in a canonical family."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BV-LEGACY", title="B 站", source="related_chain", topic_group="L1")
            _seed_visible(
                db,
                "zhihu-legacy",
                title="知乎旧策略",
                source="zhihu-hot",
                content_id="zhihu-legacy",
                content_url="https://www.zhihu.com/answer/legacy",
                topic_group="L2",
            )
            _seed_visible(
                db,
                "xhs-legacy",
                title="小红书旧策略",
                source="xhs-extension-search",
                content_id="xhs-legacy",
                content_url="https://www.xiaohongshu.com/explore/xhs-legacy?xsec_token=ABC=",
                topic_group="L3",
            )
            _seed_visible(
                db,
                "dy-legacy",
                title="抖音旧策略",
                source="dy-plugin-search",
                content_id="dy-legacy",
                content_url="https://www.douyin.com/video/dy-legacy",
                topic_group="L4",
            )

            snapshot = db.load_pool_platform_availability()

            assert snapshot.by_platform == {
                "bilibili": 1,
                "zhihu": 1,
                "xiaohongshu": 1,
                "douyin": 1,
            }
            assert sum(snapshot.by_platform.values()) == snapshot.total_available
            # The strict reader must resolve the same legacy rows.
            assert [row["bvid"] for row in db.get_pool_candidates_for_platform("zhihu")] == [
                "zhihu-legacy"
            ]
            assert [row["bvid"] for row in db.get_pool_candidates_for_platform("douyin")] == [
                "dy-legacy"
            ]
            db.close()

    def test_pool_platform_availability_excludes_non_servable_rows(self) -> None:
        """Counts and strict reads share one servability gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            def _seed_zhihu(bvid: str, group: str, **kwargs: Any) -> None:
                kwargs.setdefault("source", "zhihu-hot")
                kwargs.setdefault("source_platform", "zhihu")
                kwargs.setdefault("content_id", bvid)
                kwargs.setdefault("content_url", f"https://www.zhihu.com/answer/{bvid}")
                _seed_visible(db, bvid, title=bvid, topic_group=group, **kwargs)

            _seed_zhihu("zhihu-ok", "S1")
            _seed_zhihu("zhihu-recommended", "S2")
            _seed_zhihu("zhihu-viewed", "S3")
            _seed_zhihu("zhihu-delight", "S4")
            # Unclassified and copy-pending rows never reach the serve window.
            _seed_zhihu("zhihu-unclassified", "S5", style_key="")
            _seed_zhihu("zhihu-copy-pending", "S6", pool_expression="")
            # Non-linkable xhs rows mint dead links, so they are not inventory.
            _seed_visible(
                db,
                "xhs-bare",
                title="缺 token 的小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-bare",
                content_url="https://www.xiaohongshu.com/explore/xhs-bare",
                topic_group="S7",
            )

            db.insert_recommendation(
                "zhihu-recommended",
                confidence=0.9,
                expression="already recommended",
                topic="testing",
            )
            db.insert_event(
                "view",
                title="zhihu-viewed",
                url="https://www.zhihu.com/answer/zhihu-viewed",
                metadata={"source_platform": "zhihu", "content_id": "zhihu-viewed"},
            )
            db.conn.execute(
                """
                UPDATE content_cache
                SET delight_score=0.9,
                    delight_reason=pool_expression,
                    delight_hook=pool_topic_label
                WHERE bvid='zhihu-delight'
                """
            )
            db.conn.commit()

            snapshot = db.load_pool_platform_availability()

            assert snapshot.by_platform.get("zhihu") == 1
            assert "xiaohongshu" not in snapshot.by_platform
            assert snapshot.total_available == db.count_pool_candidates()
            assert sum(snapshot.by_platform.values()) == snapshot.total_available
            strict = db.get_pool_candidates_for_platform("zhihu", limit=20)
            assert [row["bvid"] for row in strict] == ["zhihu-ok"]
            assert db.get_pool_candidates_for_platform("xiaohongshu", limit=20) == []
            db.close()

    def test_get_pool_candidates_for_platform_matches_inventory_and_returns_full_rows(
        self,
    ) -> None:
        """Strict reads are a subset of the counted set and carry full rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BV-MIX-1", title="B 站", source="search", topic_group="M1")
            for index in range(3):
                _seed_visible(
                    db,
                    f"zhihu-mix-{index}",
                    title=f"知乎 {index}",
                    source="zhihu-hot",
                    source_platform="zhihu",
                    content_id=f"zhihu-mix-{index}",
                    content_url=f"https://www.zhihu.com/answer/mix-{index}",
                    topic_group=f"M-Z{index}",
                    relevance_score=0.9 - index * 0.01,
                )

            snapshot = db.load_pool_platform_availability()
            strict = db.get_pool_candidates_for_platform("zhihu", limit=20)

            assert len(strict) == snapshot.by_platform["zhihu"] == 3
            assert {row["source_platform"] for row in strict} == {"zhihu"}
            # Full candidate rows so the existing recommendation path still works.
            assert strict[0]["title"] == "知乎 0"
            assert strict[0]["pool_expression"] == "测试推荐文案"
            assert strict[0]["content_url"] == "https://www.zhihu.com/answer/mix-0"
            # Stable relevance ordering, same as the canonical available set.
            assert [row["bvid"] for row in strict] == [
                "zhihu-mix-0",
                "zhihu-mix-1",
                "zhihu-mix-2",
            ]
            db.close()

    def test_get_pool_candidates_for_platform_normalizes_platform_aliases(self) -> None:
        """Aliases canonicalize before the query, so no alias leaks into SQL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "xhs-alias",
                title="小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-alias",
                content_url="https://www.xiaohongshu.com/explore/xhs-alias?xsec_token=ABC=",
                topic_group="A1",
            )
            _seed_visible(
                db,
                "zhihu-alias",
                title="知乎",
                source="zhihu-hot",
                source_platform="zhihu",
                content_id="zhihu-alias",
                content_url="https://www.zhihu.com/answer/alias",
                topic_group="A2",
            )

            assert [row["bvid"] for row in db.get_pool_candidates_for_platform("XHS")] == [
                "xhs-alias"
            ]
            assert [row["bvid"] for row in db.get_pool_candidates_for_platform("rednote")] == [
                "xhs-alias"
            ]
            assert [row["bvid"] for row in db.get_pool_candidates_for_platform("zh")] == [
                "zhihu-alias"
            ]
            assert db.get_pool_candidates_for_platform("not-a-platform") == []
            db.close()

    def test_get_pool_candidates_for_platform_applies_topic_window(self) -> None:
        """Strict reads honour the same topic window as the displayed count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(5):
                _seed_visible(
                    db,
                    f"zhihu-window-{index}",
                    title=f"同组知乎 {index}",
                    source="zhihu-hot",
                    source_platform="zhihu",
                    content_id=f"zhihu-window-{index}",
                    content_url=f"https://www.zhihu.com/answer/window-{index}",
                    topic_group="同一个话题组",
                    relevance_score=0.9 - index * 0.01,
                )

            snapshot = db.load_pool_platform_availability()
            strict = db.get_pool_candidates_for_platform("zhihu", limit=20)

            # Default max_per_topic_group=3 caps a concentrated group in both.
            assert snapshot.by_platform["zhihu"] == 3
            assert len(strict) == 3
            assert snapshot.total_available == db.count_pool_candidates() == 3
            db.close()

    def test_scoped_serve_snapshot_balances_topics_like_the_global_window(self) -> None:
        """A platform-scoped window must be as topically rich as "全部".

        ``get_pool_candidates`` round-robins the relevance-ordered pool by
        topic_group so a few dominant groups cannot fill the candidate window.
        A scoped read that merely truncates by relevance would hand the
        downstream MMR/diversity stages a window covering a handful of groups,
        making platform tabs visibly more repetitive than the mixed feed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            # 20 groups x 3 rows, relevance clustered so plain truncation would
            # take whole groups off the head.
            for group in range(20):
                for index in range(3):
                    bvid = f"zh-{group}-{index}"
                    _seed_visible(
                        db,
                        bvid,
                        title=bvid,
                        source="zhihu-hot",
                        source_platform="zhihu",
                        content_id=bvid,
                        content_url=f"https://www.zhihu.com/answer/{bvid}",
                        topic_group=f"组{group}",
                        relevance_score=0.99 - group * 0.01 - index * 0.0001,
                    )

            snapshot = db.load_pool_serve_snapshot(limit=12, source_platform="zhihu")

            groups = {str(row["topic_group"]) for row in snapshot.candidate_rows}
            assert len(snapshot.candidate_rows) == 12
            # Pure relevance truncation would reach only 4 groups (12 / 3).
            assert len(groups) == 12
            assert {str(row["source_platform"]) for row in snapshot.candidate_rows} == {"zhihu"}
            db.close()

    async def test_load_pool_platform_availability_async_reads_in_isolation(self) -> None:
        """The async snapshot must not borrow the process-shared connection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BV-ASYNC", title="B 站", source="search", topic_group="N1")
            _seed_visible(
                db,
                "zhihu-async",
                title="知乎",
                source="zhihu-hot",
                source_platform="zhihu",
                content_id="zhihu-async",
                content_url="https://www.zhihu.com/answer/async",
                topic_group="N2",
            )

            snapshot = await db.load_pool_platform_availability_async()

            assert snapshot.total_available == 2
            assert snapshot.by_platform == {"bilibili": 1, "zhihu": 1}
            # The shared connection is left untouched by the isolated read.
            assert not db.conn.in_transaction
            db.close()

    def test_count_pool_raw_material_counts_pending_xhs_and_excludes_viewed_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BV-RAW", title="B 站素材", source="search")
            _seed_visible(
                db,
                "xhs-linkable-raw",
                title="可打开小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-linkable-raw",
                content_url=(
                    "https://www.xiaohongshu.com/explore/xhs-linkable-raw?xsec_token=ABC="
                ),
            )
            _seed_visible(
                db,
                "xhs-pending-raw",
                title="等待 token 小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-pending-raw",
                content_url="https://www.xiaohongshu.com/explore/xhs-pending-raw",
            )
            _seed_visible(
                db,
                "xhs-viewed-raw",
                title="看过的小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="xhs-viewed-raw",
                content_url="https://www.xiaohongshu.com/explore/xhs-viewed-raw",
            )
            _seed_visible(db, "BV-RECOMMENDED", title="已推荐", source="search")
            db.insert_recommendation(
                "BV-RECOMMENDED",
                confidence=0.9,
                expression="已经推过",
                topic="测试",
            )
            db.insert_event(
                "view",
                title="看过的小红书",
                url="https://www.xiaohongshu.com/explore/xhs-viewed-raw",
                metadata={"source_platform": "xiaohongshu", "note_id": "xhs-viewed-raw"},
            )

            counts = db.count_pool_raw_material_by_source()

            assert db.count_pool_raw_material_candidates() == 3
            assert counts == {"bilibili": 1, "xiaohongshu": 2}
            assert sum(counts.values()) == db.count_pool_raw_material_candidates()
            db.close()

    def test_update_pool_copy_makes_row_visible_in_pool(self) -> None:
        """v0.3.57+: round-trip — empty-copy row stays hidden until
        update_pool_copy fills both fields, then becomes visible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content(
                "BVPENDING",
                title="待生成",
                source="search",
                relevance_score=0.7,
                style_key="tutorial",
                topic_group="测试分组",
            )
            assert db.count_pool_candidates() == 0
            assert db.get_pool_candidates(limit=10) == []

            db.update_pool_copy("BVPENDING", expression="生成好了", topic_label="主题")

            assert db.count_pool_candidates() == 1
            rows = db.get_pool_candidates(limit=10)
            assert [r["bvid"] for r in rows] == ["BVPENDING"]

            db.close()

    def test_count_pool_candidates_refreshes_stale_read_snapshot(self) -> None:
        """Runtime status must not report stale availability after another
        connection consumes a pool row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db_a = Database(db_path)
            db_a.initialize()
            _seed_visible(
                db_a,
                "BVSTALE",
                title="会被另一个连接消费",
                source="search",
                relevance_score=0.9,
            )

            db_a.conn.execute("BEGIN")
            assert db_a.count_pool_candidates() == 1

            db_b = Database(db_path)
            db_b.initialize()
            db_b.insert_recommendation(
                "BVSTALE",
                confidence=0.9,
                expression="已经进入推荐历史",
                topic="测试主题",
            )

            assert db_a.count_pool_candidates() == 0

            db_b.close()
            db_a.close()

    def test_count_pool_readiness_keeps_viewed_rows_out_of_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BVREADY",
                title="可换",
                source="search",
                relevance_score=0.91,
            )
            _seed_visible(
                db,
                "BVSEEN",
                title="看过",
                source="search",
                relevance_score=0.9,
            )
            db.cache_content(
                "BVPENDING",
                title="待整理",
                source="search",
                relevance_score=0.89,
                style_key="tutorial",
                topic_group="测试分组",
            )
            db.insert_event(
                "view",
                title="看过",
                url="https://www.bilibili.com/video/BVSEEN",
                metadata={"bvid": "BVSEEN"},
            )

            assert db.count_pool_readiness() == {
                "available": 1,
                "copy_ready": 1,
                "raw": 3,
                "pending": 1,
                "admitted_pending_copy": 1,
                "admitted_pending_available": 1,
                "pending_eval": 0,
                "evaluated_pending": 0,
            }

            db.close()

    def test_copy_ready_count_is_not_reduced_by_topic_display_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            for index in range(5):
                _seed_visible(
                    db,
                    f"BVSAME_TOPIC_{index}",
                    title=f"same topic {index}",
                    topic_group="same-topic",
                )

            readiness = db.count_pool_readiness()

            assert readiness["available"] == 3
            assert readiness["copy_ready"] == 5
            db.close()

    def test_pending_available_count_and_priority_follow_public_topic_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            for index in range(3):
                _seed_visible(
                    db,
                    f"BVREADY_SATURATED_{index}",
                    topic_group="saturated-topic",
                    relevance_score=0.95 - index * 0.01,
                )
            for index in range(4):
                db.cache_content(
                    f"BVPENDING_SATURATED_{index}",
                    title=f"saturated pending {index}",
                    source="search",
                    relevance_score=0.9 - index * 0.01,
                    style_key="tutorial",
                    topic_group="saturated-topic",
                )
            db.cache_content(
                "BVPENDING_OPEN_TOPIC",
                title="open topic pending",
                source="search",
                relevance_score=0.65,
                style_key="tutorial",
                topic_group="open-topic",
            )

            readiness = db.count_pool_readiness()
            prioritized = db.get_pool_candidates_needing_copy(
                limit=5,
                eligible_available_first=True,
            )

            assert readiness["available"] == 3
            assert readiness["admitted_pending_copy"] == 5
            assert readiness["admitted_pending_available"] == 1
            assert prioritized[0]["bvid"] == "BVPENDING_OPEN_TOPIC"
            assert {row["bvid"] for row in prioritized[1:]} == {
                f"BVPENDING_SATURATED_{index}" for index in range(4)
            }
            db.close()

    def test_seen_topic_heads_do_not_block_lower_pending_copy_from_public_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            for index in range(3):
                bvid = f"BVSEEN_TOPIC_HEAD_{index}"
                _seed_visible(
                    db,
                    bvid,
                    title=f"seen head {index}",
                    topic_group="seen-head-topic",
                    relevance_score=0.99 - index * 0.01,
                )
                db.insert_event(
                    "view",
                    title=f"seen head {index}",
                    url=f"https://www.bilibili.com/video/{bvid}",
                    metadata={"bvid": bvid},
                )
            db.cache_content(
                "BVPENDING_BELOW_SEEN_HEADS",
                title="pending below seen heads",
                source="search",
                relevance_score=0.65,
                style_key="tutorial",
                topic_group="seen-head-topic",
            )

            readiness = db.count_pool_readiness()

            assert readiness["available"] == 0
            assert readiness["admitted_pending_available"] == 1
            assert [
                row["bvid"]
                for row in db.get_pool_candidates_needing_copy(
                    limit=1,
                    eligible_available_first=True,
                )
            ] == ["BVPENDING_BELOW_SEEN_HEADS"]

            db.update_pool_copy(
                "BVPENDING_BELOW_SEEN_HEADS",
                expression="可用文案",
                topic_label="可用主题",
            )

            assert db.count_pool_candidates() == 1
            assert [row["bvid"] for row in db.get_pool_candidates(limit=5)] == [
                "BVPENDING_BELOW_SEEN_HEADS"
            ]
            db.close()

    def test_topic_window_ranking_excludes_tier_but_keeps_tier_in_global_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(
                db,
                "BVLOW_PRIMARY",
                topic_group="shared-topic",
                candidate_tier="primary",
                relevance_score=0.7,
            )
            for index, score in enumerate((0.99, 0.98, 0.97)):
                _seed_visible(
                    db,
                    f"BVHIGH_SECONDARY_{index}",
                    topic_group="shared-topic",
                    candidate_tier="secondary",
                    relevance_score=score,
                )
            _seed_visible(
                db,
                "BVOTHER_PRIMARY",
                topic_group="other-topic",
                candidate_tier="primary",
                relevance_score=0.6,
            )

            rows = db.get_pool_candidates(limit=10)

            assert db.count_pool_candidates() == 4
            assert [row["bvid"] for row in rows] == [
                "BVOTHER_PRIMARY",
                "BVHIGH_SECONDARY_0",
                "BVHIGH_SECONDARY_1",
                "BVHIGH_SECONDARY_2",
            ]
            db.close()

    def test_copy_ready_budget_excludes_nonservable_pool_rows(self) -> None:
        """Only fresh, unseen rows may consume ready or pending-copy budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(db, "BVACTIVE_READY", topic_group="active-ready")
            db.cache_content(
                "BVACTIVE_PENDING",
                title="active pending copy",
                source="search",
                relevance_score=0.9,
                style_key="tutorial",
                topic_group="active-pending",
            )

            excluded_bvids: list[str] = []
            for suffix, status in (
                ("STALE", "stale"),
                ("SUPPRESSED", "suppressed"),
                ("PURGED", "purged_by_dislike"),
            ):
                ready_bvid = f"BV{suffix}_READY"
                pending_bvid = f"BV{suffix}_PENDING"
                _seed_visible(db, ready_bvid, topic_group=f"{suffix}-ready")
                db.cache_content(
                    pending_bvid,
                    title=f"{suffix} pending copy",
                    source="search",
                    relevance_score=0.9,
                    style_key="tutorial",
                    topic_group=f"{suffix}-pending",
                )
                db.conn.execute(
                    "UPDATE content_cache SET pool_status = ? WHERE bvid IN (?, ?)",
                    (status, ready_bvid, pending_bvid),
                )
                excluded_bvids.extend((ready_bvid, pending_bvid))

            for suffix, has_copy in (("VIEWED_READY", True), ("VIEWED_PENDING", False)):
                bvid = f"BV{suffix}"
                if has_copy:
                    _seed_visible(db, bvid, topic_group=suffix)
                else:
                    db.cache_content(
                        bvid,
                        title=suffix,
                        source="search",
                        relevance_score=0.9,
                        style_key="tutorial",
                        topic_group=suffix,
                    )
                db.insert_event(
                    "view",
                    title=suffix,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    metadata={"bvid": bvid},
                )
                excluded_bvids.append(bvid)

            readiness = db.count_pool_readiness()
            pending_rows = db.get_pool_candidates_needing_copy(limit=20)

            assert readiness["available"] == 1
            assert readiness["copy_ready"] == 1
            assert readiness["admitted_pending_copy"] == 1
            assert [row["bvid"] for row in pending_rows] == ["BVACTIVE_PENDING"]
            assert all(
                bvid not in {str(row["bvid"]) for row in pending_rows} for bvid in excluded_bvids
            )
            db.close()

    def test_pool_serve_snapshot_materializes_seen_ledger_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(db, "BVREADY", title="可换", source="search")
            db.insert_event(
                "view",
                title="另一个视频",
                url="https://www.bilibili.com/video/BVSEEN",
                metadata={"bvid": "BVSEEN"},
            )
            original = Database._seen_state_on
            calls = 0

            def counted(
                database: Database,
                conn: sqlite3.Connection,
            ) -> tuple[set[str], set[str]]:
                nonlocal calls
                calls += 1
                return original(database, conn)

            with patch.object(Database, "_seen_state_on", counted):
                snapshot = db.load_pool_serve_snapshot(limit=10)

            assert snapshot.readiness["available"] == 1
            assert [row["bvid"] for row in snapshot.candidate_rows] == ["BVREADY"]
            assert calls == 1
            db.close()

    def test_pool_serve_snapshot_reuses_and_invalidates_seen_ledger_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(db, "BVREADY", title="可换", source="search")
            db.insert_event(
                "view",
                title="看过一",
                url="https://www.bilibili.com/video/BVSEEN1",
                metadata={"bvid": "BVSEEN1"},
            )
            first = db.load_pool_serve_snapshot(limit=10)
            assert first.seen_bvids == frozenset({"BVSEEN1"})
            cached = dict(db._seen_state_cache)

            second = db.load_pool_serve_snapshot(limit=10)
            assert second.seen_bvids == first.seen_bvids
            assert db._seen_state_cache == cached

            db.insert_event(
                "view",
                title="看过二",
                url="https://www.bilibili.com/video/BVSEEN2",
                metadata={"bvid": "BVSEEN2"},
            )
            assert db._seen_state_cache == {}

            refreshed = db.load_pool_serve_snapshot(limit=10)
            assert refreshed.seen_bvids == frozenset({"BVSEEN1", "BVSEEN2"})

            db.close()

    def test_pool_maintenance_parses_view_history_once_per_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(db, "BVREADY", title="可换", source="search")
            original = Database._recent_viewed_content_keys_on
            calls = 0

            def counted(
                database: Database,
                conn: sqlite3.Connection,
                *,
                limit: int = 2000,
            ) -> set[str]:
                nonlocal calls
                calls += 1
                return original(database, conn, limit=limit)

            with patch.object(Database, "_recent_viewed_content_keys_on", counted):
                result = db.maintain_pool_inventory(
                    target=1,
                    raw_ceiling=2,
                    source_share_quotas={"bilibili": 1},
                    max_mutations=50,
                )

            assert result.available_after == 1
            assert calls == 1
            db.close()

    def test_pool_maintenance_holds_newly_due_fresh_rows_in_writer_transaction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _freeze_storage_clock(monkeypatch)
        db = Database(tmp_path / "maintenance-temporal-due.db")
        db.initialize()
        _seed_visible(db, "BVMAINTDUE", source="search", **_v2_temporal())
        db.conn.execute(
            "UPDATE content_cache SET temporal_next_review_at = ?, "
            "temporal_evaluated_at = ? WHERE bvid = ?",
            (
                "2000-01-01T00:00:00Z",
                "1999-01-01T00:00:00Z",
                "BVMAINTDUE",
            ),
        )
        db.conn.commit()

        result = db.maintain_pool_inventory(
            target=1,
            raw_ceiling=2,
            source_share_quotas={"bilibili": 1},
            max_mutations=50,
        )

        row = db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid = 'BVMAINTDUE'"
        ).fetchone()
        assert row["pool_status"] == "temporal_review_hold"
        assert result.available_after == 0
        assert result.mutation_count >= 1

    def test_count_pool_readiness_reports_only_canonical_admitted_pending_copy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(db, "BVREADY", source="search")
            db.cache_content(
                "BVCOPY",
                title="classified and admitted",
                source="search",
                relevance_score=0.9,
                style_key="tutorial",
                topic_group="testing",
            )
            db.cache_content(
                "BVUNCLASSIFIED",
                title="not copy ready",
                source="search",
                relevance_score=0.9,
                topic_group="testing",
            )
            for bvid in ("BVRECOMMENDED", "BVVIEWED", "BVDELIGHT"):
                db.cache_content(
                    bvid,
                    title=bvid,
                    source="search",
                    relevance_score=0.9,
                    style_key="tutorial",
                    topic_group="testing",
                )
            db.insert_recommendation(
                "BVRECOMMENDED",
                confidence=0.9,
                expression="already recommended",
                topic="testing",
            )
            db.insert_event(
                "view",
                title="BVVIEWED",
                url="https://www.bilibili.com/video/BVVIEWED",
                metadata={"bvid": "BVVIEWED"},
            )
            db.conn.execute(
                """
                UPDATE content_cache
                SET delight_score=0.9, delight_reason='surprise', delight_hook='hook'
                WHERE bvid='BVDELIGHT'
                """
            )
            xhs_url = "https://www.xiaohongshu.com/explore/note"
            for bvid, up_name, content_url in (
                ("XHSBARE", "Friend", xhs_url),
                ("XHSSELF", "Me", f"{xhs_url}?xsec_token=token"),
            ):
                db.cache_content(
                    bvid,
                    title=bvid,
                    up_name=up_name,
                    source="xhs-extension-task",
                    source_platform="xiaohongshu",
                    content_url=content_url,
                    relevance_score=0.9,
                    style_key="tutorial",
                    topic_group="testing",
                )
            db.enqueue_discovery_candidates(
                [
                    DiscoveryCandidateWrite(
                        candidate_key=f"bilibili:{bvid}",
                        source_platform="bilibili",
                        source_strategy="search",
                        content_id=bvid,
                        title=bvid,
                    )
                    for bvid in ("BVEVALUATED", "BVPENDING", "BVEVALUATING")
                ]
            )
            evaluated = db.claim_discovery_candidates_for_eval(
                limit=1, claim_token="evaluated-token"
            )
            assert db.persist_claimed_discovery_candidate_evaluations(
                [
                    {
                        "candidate_id": int(evaluated[0]["id"]),
                        "status": "evaluated",
                        "relevance_score": 0.9,
                        "relevance_reason": "fit",
                        "topic_key": "testing",
                        "topic_group": "testing",
                        "style_key": "tutorial",
                        "franchise_key": "",
                        "pool_expression": "",
                        "pool_topic_label": "",
                        "eval_error": "",
                    }
                ],
                claim_token="evaluated-token",
            )
            db.claim_discovery_candidates_for_eval(limit=1, claim_token="evaluating-token")

            readiness = db.count_pool_readiness(xhs_self_nickname="Me")

            assert readiness["available"] == 1
            # BVCOPY and the legacy BVDELIGHT row both still need formal
            # recommendation copy. A high delight score plus arbitrary
            # evaluator reason/hook must not claim the row before that copy
            # exists, otherwise it can never reach the copy backlog.
            assert readiness["admitted_pending_copy"] == 2
            assert readiness["evaluated_pending"] == 1
            assert readiness["pending_eval"] == 2
            assert [
                row["bvid"]
                for row in db.get_pool_candidates_needing_copy(limit=20, xhs_self_nickname="Me")
            ] == ["BVCOPY", "BVDELIGHT"]
            db.close()

    def test_count_pool_readiness_includes_pending_discovery_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            _seed_visible(db, "BV-ready", source="search")
            db.enqueue_discovery_candidates(
                [
                    DiscoveryCandidateWrite(
                        candidate_key="youtube:yt-pending",
                        source_platform="youtube",
                        source_strategy="yt_search",
                        content_id="yt-pending",
                        content_url="https://www.youtube.com/watch?v=yt-pending",
                        title="Pending",
                    )
                ]
            )

            readiness = db.count_pool_readiness()

            assert readiness["available"] == 1
            assert readiness["raw"] == 2
            assert readiness["pending"] == 1
            assert readiness["pending_eval"] == 1
            assert readiness["evaluated_pending"] == 0
            assert db.count_pool_raw_material_candidates() == 2
            assert db.count_pool_raw_material_by_source() == {
                "bilibili": 1,
                "youtube": 1,
            }

            db.close()

    def test_get_pool_candidates_skips_recently_viewed_bvids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "BV1FRESH",
                title="新鲜候选",
                up_name="UPA",
                source="search",
                relevance_score=0.91,
            )
            _seed_visible(
                db,
                "BV1SEEN",
                title="已经看过",
                up_name="UPB",
                source="search",
                relevance_score=0.95,
            )
            db.insert_event(
                "view",
                title="已经看过",
                url="https://www.bilibili.com/video/BV1SEEN",
                metadata={"bvid": "BV1SEEN"},
            )

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["BV1FRESH"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_recent_viewed_content_keys_extract_multi_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_event(
                "view",
                title="小红书笔记",
                url="https://www.xiaohongshu.com/explore/note-seen",
                metadata={"source_platform": "xiaohongshu", "note_id": "note-seen"},
            )
            db.insert_event(
                "view",
                title="抖音视频",
                url="https://www.douyin.com/video/7123456789012345678",
                metadata={
                    "source_platform": "douyin",
                    "aweme_id": "7123456789012345678",
                },
            )
            db.insert_event(
                "view",
                title="YouTube 视频",
                url="https://www.youtube.com/watch?v=abc1234defg",
                metadata={"source_platform": "youtube", "video_id": "abc1234defg"},
            )
            db.insert_event(
                "view",
                title="Reddit 帖子",
                url="https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/",
                metadata={"source_platform": "reddit", "post_id": "abc123"},
            )
            db.insert_event(
                "view",
                title="B 站视频",
                url="https://www.bilibili.com/video/BV1SEEN",
                metadata={"source_platform": "bilibili", "bvid": "BV1SEEN"},
            )
            db.insert_event(
                "view",
                title="知乎回答",
                url="https://www.zhihu.com/question/1/answer/42",
                metadata={"source_platform": "zh", "content_id": "answer:42"},
            )
            db.insert_event(
                "view",
                title="X 帖子",
                url="https://x.com/example/status/123",
                metadata={"source_platform": "twitter", "tweet_id": "123"},
            )
            db.insert_event(
                "view",
                title="知乎问题",
                url="https://www.zhihu.com/question/99",
                metadata={"source_platform": "zhihu", "question_id": "99"},
            )

            keys = db.get_recent_viewed_content_keys()

            assert "xiaohongshu:note-seen" in keys
            assert "douyin:7123456789012345678" in keys
            assert "youtube:abc1234defg" in keys
            assert "reddit:t3_abc123" in keys
            assert "bilibili:BV1SEEN" in keys
            assert "BV1SEEN" in keys
            assert "zhihu:answer:42" in keys
            assert "twitter:123" in keys
            assert "zhihu:99" in keys

            db.close()

    def test_view_event_uses_one_ordered_metadata_identity(self) -> None:
        with_primary_and_fallback = Database._extract_content_keys_from_view_event(
            {
                "url": "https://linux.do/t/topic/4242",
                "metadata": {
                    "source_platform": "linuxdo",
                    "content_id": "topic:4242",
                    "topic_id": 4242,
                },
            }
        )
        fallback_only = Database._extract_content_keys_from_view_event(
            {
                "url": "https://linux.do/t/topic/4243",
                "metadata": {"source_platform": "linuxdo", "topic_id": 4243},
            }
        )

        assert with_primary_and_fallback == {"linuxdo:topic:4242"}
        assert fallback_only == {"linuxdo:4243"}

    def test_seen_items_backfill_is_unbounded_and_excludes_oldest_legacy_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.db"
            db = Database(path)
            db.initialize()
            db.conn.execute("DROP TABLE seen_items")
            db.conn.execute("DROP TABLE seen_items_backfill_state")
            db.conn.executemany(
                """
                INSERT INTO events (event_type, url, metadata)
                VALUES ('view', ?, ?)
                """,
                [
                    (
                        f"https://www.bilibili.com/video/BVOLD{index:05d}",
                        json.dumps({"bvid": f"BVOLD{index:05d}"}),
                    )
                    for index in range(2005)
                ],
            )
            db.conn.commit()
            db.close()

            migrated = Database(path)
            migrated.initialize()
            _seed_visible(
                migrated,
                "BVOLD00000",
                title="超过旧 2000 条窗口的已看内容",
                source="search",
                relevance_score=0.99,
            )
            _seed_visible(
                migrated,
                "BVFRESH",
                title="没看过的新内容",
                source="search",
                relevance_score=0.90,
            )

            assert len(migrated.get_seen_bvids()) == 2005
            assert [row["bvid"] for row in migrated.get_pool_candidates(limit=10)] == ["BVFRESH"]
            migrated.close()

    def test_snapshot_marks_are_seen_immediately_and_keep_event_provenance(self) -> None:
        """快照标记没有事件 id，缓存键又是 MAX(last_event_id)——不显式失效就查不到。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.insert_event(
                "view",
                url="https://www.bilibili.com/video/BVWATCHED",
                metadata={"bvid": "BVWATCHED"},
            )
            before = db.get_seen_bvids()
            watched_row = db.conn.execute(
                "SELECT first_event_id FROM seen_items WHERE content_id = 'BVWATCHED'"
            ).fetchone()

            added = db.mark_items_seen("bilibili", ["BVSNAP1", "BVSNAP2", "BVWATCHED", ""])

            assert added == 2, "已在账本里的与空 id 都不该重复计数"
            assert "BVSNAP1" in db.get_seen_bvids(), "快照标记必须立刻对去重可见"
            assert "BVWATCHED" in before
            after_row = db.conn.execute(
                "SELECT first_event_id FROM seen_items WHERE content_id = 'BVWATCHED'"
            ).fetchone()
            assert after_row["first_event_id"] == watched_row["first_event_id"], (
                "真实事件的溯源不该被快照覆盖"
            )
            assert db.mark_items_seen("bilibili", ["BVSNAP1"]) == 0, "重复标记要幂等"
            db.close()

    def test_explicit_positive_events_join_the_seen_ledger(self) -> None:
        """收藏 / 点赞 / 投币都证明用户消费过这条内容，必须参与硬去重。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for event_type, bvid in (
                ("favorite", "BVFAV1"),
                ("like", "BVLIKE1"),
                ("coin", "BVCOIN1"),
                ("follow", "BVFOLLOW1"),
            ):
                db.insert_event(
                    event_type,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    metadata={"bvid": bvid},
                )

            seen = db.get_seen_bvids()
            assert {"BVFAV1", "BVLIKE1", "BVCOIN1"} <= seen, "收藏过的内容不该再被推荐"
            assert "BVFOLLOW1" not in seen, "关注 UP 不等于看过这条内容"
            db.close()

    def test_widening_the_seen_types_rewinds_the_backfill_cursor(self) -> None:
        """老库的游标停在最新事件上，不倒回就永远扫不到新纳入的类型。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.db"
            db = Database(path)
            db.initialize()
            db.conn.execute(
                """
                INSERT INTO events (event_type, url, metadata)
                VALUES ('favorite', ?, ?)
                """,
                ("https://www.bilibili.com/video/BVLEGACYFAV", json.dumps({"bvid": "BVLEGACYFAV"})),
            )
            # 模拟旧版本：只认 view，游标已扫过这条收藏，且没有版本列。
            db.conn.execute("DELETE FROM seen_items")
            db.conn.execute(
                "UPDATE seen_items_backfill_state SET last_scanned_event_id = "
                "(SELECT MAX(id) FROM events)"
            )
            db.conn.execute(
                "ALTER TABLE seen_items_backfill_state DROP COLUMN scanned_event_types_version"
            )
            db.conn.commit()
            db.close()

            migrated = Database(path)
            migrated.initialize()

            assert "BVLEGACYFAV" in migrated.get_seen_bvids(), "升级后老收藏必须补进去重账本"
            migrated.close()

    def test_event_batch_updates_seen_items_in_the_same_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            inserted = db.insert_events_batch(
                [
                    {
                        "event_type": "view",
                        "url": "https://www.youtube.com/watch?v=batch-seen",
                        "metadata": {
                            "source_platform": "youtube",
                            "video_id": "batch-seen",
                        },
                    },
                    {
                        "event_type": "search",
                        "metadata": {"query": "not a seen item"},
                    },
                ]
            )

            assert inserted == 2
            assert db.get_seen_content_keys() == {"youtube:batch-seen"}
            db.close()

    def test_get_pool_candidates_skips_recently_viewed_non_bilibili_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            _seed_visible(
                db,
                "xiaohongshu:note-seen",
                title="已经看过的小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="note-seen",
                content_url="https://www.xiaohongshu.com/explore/note-seen?xsec_token=token",
                relevance_score=0.96,
            )
            _seed_visible(
                db,
                "xiaohongshu:note-fresh",
                title="新小红书",
                source="xhs-extension-task",
                source_platform="xiaohongshu",
                content_id="note-fresh",
                content_url="https://www.xiaohongshu.com/explore/note-fresh?xsec_token=token",
                relevance_score=0.91,
            )
            db.insert_event(
                "view",
                title="已经看过的小红书",
                url="https://www.xiaohongshu.com/explore/note-seen?xsec_token=token",
                metadata={"source_platform": "xiaohongshu", "note_id": "note-seen"},
            )

            items = db.get_pool_candidates(limit=10)

            assert [item["bvid"] for item in items] == ["xiaohongshu:note-fresh"]
            assert db.count_pool_candidates() == 1

            db.close()

    def test_get_pool_candidates_balances_topics_in_candidate_window(self) -> None:
        """Candidate window is balanced by topic_group, not source.

        Without rebalancing, a single dominant topic at the relevance head
        would crowd out the rest. The pool sampler bucket-sorts by
        ``topic_group`` (with ``topic_key`` fallback) and round-robins so
        that no single topic monopolises the candidate window.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            for index in range(5):
                _seed_visible(
                    db,
                    f"BVAI{index}",
                    title=f"AI 候选 {index}",
                    up_name="科技频道",
                    source="search",
                    topic_group="人工智能",
                    relevance_score=0.99 - index * 0.01,
                )
            _seed_visible(
                db,
                "BVGAME",
                title="游戏候选",
                up_name="游戏频道",
                source="trending",
                topic_group="自走棋",
                relevance_score=0.8,
            )
            _seed_visible(
                db,
                "BVDOC",
                title="纪录片候选",
                up_name="纪录片频道",
                source="explore",
                topic_group="纪录片",
                relevance_score=0.79,
            )
            _seed_visible(
                db,
                "BVHIST",
                title="历史候选",
                up_name="历史频道",
                source="related_chain",
                topic_group="人文历史",
                relevance_score=0.78,
            )

            items = db.get_pool_candidates(limit=6)
            topics = [item.get("topic_group", "") for item in items]

            # Top 4 slots cover all four distinct topic groups
            assert set(topics[:4]) == {"人工智能", "自走棋", "纪录片", "人文历史"}
            # AI cluster cannot monopolise — capped at max_per_topic_group=3
            # by the SQL filter, even though it owns 5 of 9 source rows by
            # raw relevance.
            assert topics.count("人工智能") == 3

            db.close()

    def test_insert_and_get_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                expression="",
                topic="",
                presented=0,
            )

            rows = db.get_recommendations(limit=10)

            assert len(rows) == 1
            assert rows[0]["bvid"] == "BV1REC"
            assert rows[0]["confidence"] == 0.83
            assert rows[0]["presented"] == 0

            db.close()

    def test_get_recommendations_excludes_low_confidence_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.insert_recommendation(
                "BV1HIGHREC",
                confidence=0.83,
                expression="",
                topic="",
                presented=0,
            )
            db.insert_recommendation(
                "BV1LOWREC",
                confidence=0.30,
                expression="",
                topic="",
                presented=0,
            )
            db.insert_recommendation(
                "BV1ZEROREC",
                confidence=0.0,
                expression="",
                topic="",
                presented=0,
            )

            rows = db.get_recommendations(limit=10)

            assert [row["bvid"] for row in rows] == ["BV1HIGHREC"]
            assert db.suppress_low_confidence_recommendations(0.60) == 2
            feedback_rows = db.conn.execute(
                """
                SELECT bvid, feedback_type
                FROM recommendations
                ORDER BY bvid
                """
            ).fetchall()
            assert {row["bvid"]: row["feedback_type"] for row in feedback_rows} == {
                "BV1HIGHREC": None,
                "BV1LOWREC": "suppressed_low_score",
                "BV1ZEROREC": "suppressed_low_score",
            }

            db.close()

    def test_actionable_recommendations_exclude_temporally_stale_history(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-actionable-history.db")
        db.initialize()
        _seed_visible(
            db,
            "BVOLDACTION",
            title="已经过期但仍保留在历史",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="breaking",
            temporal_confidence=0.95,
            temporal_reason="价值依赖即时状态",
        )
        _seed_visible(
            db,
            "BVNEWACTION",
            title="仍可展示的长期内容",
            source="search",
            published_at="2000-01-01T00:00:00Z",
            temporal_class="evergreen",
            temporal_confidence=0.95,
            temporal_reason="核心价值长期有效",
        )
        db.insert_recommendation("BVNEWACTION", confidence=0.90, presented=0)
        db.insert_recommendation("BVOLDACTION", confidence=0.99, presented=0)

        assert [row["bvid"] for row in db.get_recommendations(limit=1, exclude_processed=True)] == [
            "BVNEWACTION"
        ]
        # Immutable history deliberately retains the expired entry.
        assert [row["bvid"] for row in db.get_recommendations(limit=2)] == [
            "BVOLDACTION",
            "BVNEWACTION",
        ]
        assert db.count_unread_recommendations() == 1
        notification = db.get_notification_candidate(min_confidence=0.82)
        assert notification is not None
        assert notification["bvid"] == "BVNEWACTION"
        db.close()

    def test_actionable_history_filters_v2_terminal_state_evidence(
        self,
        tmp_path: Path,
    ) -> None:
        db = Database(tmp_path / "temporal-v2-actionable-history.db")
        db.initialize()
        evidence = "赛事已经结束"
        db.cache_content(
            "BVSTATEEXPIRED",
            title=evidence,
            source="search",
            relevance_score=0.9,
            **_v2_temporal(
                temporal_class="current",
                temporal_reason="赛事终态会使核心信息失效",
                temporal_validity_mode="event_state",
                temporal_state="expired",
                temporal_evidence=evidence,
                temporal_next_review_at="",
            ),
        )
        _seed_visible(
            db,
            "BVSTATEFRESH",
            title="长期有效",
            source="search",
            temporal_class="evergreen",
            temporal_confidence=0.95,
            temporal_reason="核心价值长期有效",
        )
        db.insert_recommendation("BVSTATEFRESH", confidence=0.90, presented=0)
        db.insert_recommendation("BVSTATEEXPIRED", confidence=0.99, presented=0)

        assert [
            row["bvid"] for row in db.get_recommendations(limit=10, exclude_processed=True)
        ] == ["BVSTATEFRESH"]
        assert db.count_unread_recommendations() == 1
        notification = db.get_notification_candidate(min_confidence=0.82)
        assert notification is not None
        assert notification["bvid"] == "BVSTATEFRESH"
        assert [row["bvid"] for row in db.get_recommendations(limit=2)] == [
            "BVSTATEEXPIRED",
            "BVSTATEFRESH",
        ]

    def test_get_recommendations_joins_multi_source_fields(self) -> None:
        """Regression: get_recommendations must surface content_cache's
        ``content_url``/``source_platform``/``content_id`` so xhs items
        don't get rebuilt as bilibili URLs by the popup fallback.

        Previous SELECT only joined title/up_name/cover_url, so every row
        came back with ``source_platform=""`` (API defaulted to "bilibili")
        and ``content_url=""`` (popup fell back to
        ``https://www.bilibili.com/video/<note_id>``), producing broken
        links that mixed xhs content into the bilibili namespace.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            note_id = "6548fd56000000001e0223b1"
            tokenized_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=ABC="
            db.cache_content(
                bvid=note_id,
                title="咒术回战复盘",
                up_name="某老师",
                cover_url="https://example.com/cover.jpg",
                source="xhs-extension-search",
                content_id=note_id,
                content_url=tokenized_url,
                source_platform="xiaohongshu",
                author_name="某老师",
            )
            db.insert_recommendation(
                note_id,
                confidence=0.9,
                expression="",
                topic="",
                presented=0,
            )

            rows = db.get_recommendations(limit=10)
            assert len(rows) == 1
            row = rows[0]
            assert row["source_platform"] == "xiaohongshu"
            assert row["content_url"] == tokenized_url
            assert row["content_id"] == note_id

            db.close()

    def test_get_recommendation_by_id_joins_multi_source_click_fields(self) -> None:
        """Recommendation click hydration needs source-aware URL fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            video_id = "KPoJ7p9iy4Q"
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            db.cache_content(
                bvid=video_id,
                title="A YouTube deep dive",
                up_name="YT Creator",
                cover_url="https://i.ytimg.com/vi/KPoJ7p9iy4Q/hqdefault.jpg",
                source="yt_search",
                content_id=video_id,
                content_url=video_url,
                source_platform="youtube",
                author_name="YT Creator",
            )
            rec_id = db.insert_recommendation(
                video_id,
                confidence=0.9,
                expression="",
                topic="技术长视频",
                presented=0,
            )

            row = db.get_recommendation_by_id(rec_id)

            assert row is not None
            assert row["bvid"] == video_id
            assert row["topic_label"] == "技术长视频"
            assert row["content_id"] == video_id
            assert row["content_url"] == video_url
            assert row["source_platform"] == "youtube"

            db.close()

    def test_get_recommendations_filters_bare_xhs_rows(self) -> None:
        """Regression: xhs rows without ``xsec_token`` in ``content_url``
        must not be surfaced to the UI — clicking them hits xhs's 300031
        login wall. Bilibili rows and tokenized xhs rows pass through.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bare_id = "69ccda220000000021005a9b"
            tokenized_id = "69d26d2e0000000023006c95"
            bilibili_id = "BV1Xx411c7mD"

            db.cache_content(
                bvid=bare_id,
                title="裸 xhs",
                up_name="a",
                cover_url="",
                source="xhs-extension-task",
                content_id=bare_id,
                content_url=f"https://www.xiaohongshu.com/explore/{bare_id}",
                source_platform="xiaohongshu",
                author_name="a",
            )
            db.cache_content(
                bvid=tokenized_id,
                title="带 token xhs",
                up_name="b",
                cover_url="",
                source="xhs-extension-task",
                content_id=tokenized_id,
                content_url=(f"https://www.xiaohongshu.com/explore/{tokenized_id}?xsec_token=XYZ="),
                source_platform="xiaohongshu",
                author_name="b",
            )
            db.cache_content(
                bvid=bilibili_id,
                title="b 站视频",
                up_name="c",
                cover_url="",
                source="bilibili-search",
                content_id=bilibili_id,
                content_url=f"https://www.bilibili.com/video/{bilibili_id}",
                source_platform="bilibili",
                author_name="c",
            )
            for bv in (bare_id, tokenized_id, bilibili_id):
                db.insert_recommendation(
                    bv,
                    confidence=0.9,
                    expression="",
                    topic="",
                    presented=0,
                )

            rows = db.get_recommendations(limit=10)
            bvids = {r["bvid"] for r in rows}
            assert bare_id not in bvids
            assert tokenized_id in bvids
            assert bilibili_id in bvids

            db.close()

    def test_get_pool_candidates_filters_bare_xhs_rows(self) -> None:
        """Regression: ranking pool must exclude bare xhs rows too, so the
        engine never promotes them into recommendations in the first place.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            bare_id = "69ccda220000000021005a9b"
            tokenized_id = "69d26d2e0000000023006c95"

            _seed_visible(
                db,
                bvid=bare_id,
                title="bare",
                up_name="",
                cover_url="",
                source="xhs-extension-task",
                content_id=bare_id,
                content_url=f"https://www.xiaohongshu.com/explore/{bare_id}",
                source_platform="xiaohongshu",
                author_name="",
            )
            _seed_visible(
                db,
                bvid=tokenized_id,
                title="tokenized",
                up_name="",
                cover_url="",
                source="xhs-extension-task",
                content_id=tokenized_id,
                content_url=(f"https://www.xiaohongshu.com/explore/{tokenized_id}?xsec_token=XYZ="),
                source_platform="xiaohongshu",
                author_name="",
            )

            rows = db.get_pool_candidates(limit=10)
            bvids = {r["bvid"] for r in rows}
            assert bare_id not in bvids
            assert tokenized_id in bvids

            db.close()

    def test_pool_candidates_exclude_self_authored_xhs_rows(self) -> None:
        """get_pool_candidates excludes xhs rows matching self nickname."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            xhs_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=XYZ="
            # XHS row matched by up_name
            _seed_visible(
                db,
                bvid="xhs_self_up",
                title="self up_name",
                up_name="TestUser",
                source="xhs-extension-task",
                content_id="xhs_self_up",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            # XHS row matched by author_name
            _seed_visible(
                db,
                bvid="xhs_self_author",
                title="self author_name",
                up_name="",
                author_name="testuser",
                source="xhs-extension-task",
                content_id="xhs_self_author",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            # XHS row by another author
            _seed_visible(
                db,
                bvid="xhs_other",
                title="other author",
                up_name="OtherUser",
                source="xhs-extension-task",
                content_id="xhs_other",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            # Bilibili row whose up_name matches — must NOT be excluded
            _seed_visible(
                db,
                bvid="BV_bili_same_name",
                title="bili same name",
                up_name="TestUser",
                source="search",
                source_platform="bilibili",
            )

            rows = db.get_pool_candidates(limit=20, xhs_self_nickname="TestUser")
            bvids = {r["bvid"] for r in rows}
            assert "xhs_self_up" not in bvids
            assert "xhs_self_author" not in bvids
            assert "xhs_other" in bvids
            assert "BV_bili_same_name" in bvids

            db.close()

    def test_pool_count_and_readiness_exclude_self_authored_xhs_rows(self) -> None:
        """count_pool_candidates and count_pool_readiness exclude self rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            xhs_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=XYZ="
            _seed_visible(
                db,
                bvid="xhs_self",
                title="self",
                up_name="Me",
                source="xhs-extension-task",
                content_id="xhs_self",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            _seed_visible(
                db,
                bvid="xhs_other",
                title="other",
                up_name="Friend",
                source="xhs-extension-task",
                content_id="xhs_other",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            _seed_visible(
                db,
                bvid="BV_bili",
                title="bili",
                up_name="Me",
                source="search",
                source_platform="bilibili",
            )

            count = db.count_pool_candidates(max_per_topic_group=0, xhs_self_nickname="Me")
            assert count == 2  # xhs_other + BV_bili

            readiness = db.count_pool_readiness(xhs_self_nickname="Me")
            assert readiness["available"] == 2

            db.close()

    def test_pool_self_author_guard_noops_when_nickname_empty(self) -> None:
        """Empty nickname must not exclude any rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            xhs_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=XYZ="
            _seed_visible(
                db,
                bvid="xhs_row",
                title="note",
                up_name="SomeUser",
                source="xhs-extension-task",
                content_id="xhs_row",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )

            rows = db.get_pool_candidates(limit=10, xhs_self_nickname="")
            assert any(r["bvid"] == "xhs_row" for r in rows)

            count = db.count_pool_candidates(max_per_topic_group=0, xhs_self_nickname="")
            assert count >= 1

            db.close()

    def test_pool_backlog_queries_skip_self_authored_xhs_rows(self) -> None:
        """Needing-evaluation and needing-copy queries exclude self rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            xhs_url = "https://www.xiaohongshu.com/explore/abc?xsec_token=XYZ="
            # Unclassified self-authored row (needs evaluation)
            db.cache_content(
                "xhs_self_eval",
                title="self unclassified",
                up_name="",
                author_name="Me",
                source="xhs-extension-task",
                content_id="xhs_self_eval",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )
            # Classified but needs copy (self-authored)
            db.cache_content(
                "xhs_self_copy",
                title="self needs copy",
                up_name="Me",
                author_name="",
                style_key="tutorial",
                topic_group="test",
                relevance_score=0.8,
                source="xhs-extension-task",
                content_id="xhs_self_copy",
                content_url=xhs_url,
                source_platform="xiaohongshu",
            )

            eval_rows = db.get_pool_candidates_needing_evaluation(limit=20, xhs_self_nickname="Me")
            assert not any(r["bvid"] == "xhs_self_eval" for r in eval_rows)

            copy_rows = db.get_pool_candidates_needing_copy(limit=20, xhs_self_nickname="Me")
            assert not any(r["bvid"] == "xhs_self_copy" for r in copy_rows)

            db.close()

    def test_insert_recommendation_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0
                    self.rollbacks = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 7

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

                def rollback(self) -> None:
                    self.rollbacks += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            recommendation_id = db.insert_recommendation("BV1LOCK", confidence=0.6)

            assert recommendation_id == 7
            assert fake_conn.calls == 2
            assert fake_conn.commits == 1
            assert fake_conn.rollbacks == 1

    def test_update_recommendation_content_persists_expression_and_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                expression="",
                topic="",
                presented=0,
            )

            db.update_recommendation_content(
                recommendation_id,
                expression="这条视频会接住你最近想把问题想透的劲头。",
                topic="你最近那股想把问题想透的劲头",
            )

            rows = db.get_recommendations(limit=10)

            assert rows[0]["expression"] == "这条视频会接住你最近想把问题想透的劲头。"
            assert rows[0]["topic"] == "你最近那股想把问题想透的劲头"

            db.close()

    def test_update_recommendation_content_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.update_recommendation_content(
                7,
                expression="这条更贴你最近的状态。",
                topic="最近更吃这一路",
            )

            assert fake_conn.calls == 2
            assert fake_conn.commits == 1

    def test_mark_recommendations_presented_sets_presented_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            first_id = db.insert_recommendation("BV1REC1", confidence=0.83, presented=0)
            second_id = db.insert_recommendation("BV1REC2", confidence=0.71, presented=0)

            db.mark_recommendations_presented([first_id, second_id])

            rows = db.get_recommendations(limit=10)

            assert rows[0]["presented"] == 1
            assert rows[1]["presented"] == 1
            assert rows[0]["presented_at"] is not None
            assert rows[1]["presented_at"] is not None

            db.close()

    def test_mark_recommendations_presented_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: list[object]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.mark_recommendations_presented([1, 2])

            assert fake_conn.calls == 2
            assert fake_conn.commits == 1

    def test_get_recommendation_by_id_returns_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()
            db.cache_content(
                "BV1REC",
                title="讲透城市与建筑",
                up_name="城市观察局",
                source="search",
            )

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                presented=0,
            )

            row = db.get_recommendation_by_id(recommendation_id)

            assert row is not None
            assert row["id"] == recommendation_id
            assert row["bvid"] == "BV1REC"
            assert row["title"] == "讲透城市与建筑"

            db.close()

    def test_update_recommendation_feedback_persists_structured_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            recommendation_id = db.insert_recommendation(
                "BV1REC",
                confidence=0.83,
                presented=0,
            )

            db.update_recommendation_feedback(
                recommendation_id,
                feedback_type="dislike",
                feedback_note="太浅了",
            )

            row = db.get_recommendation_by_id(recommendation_id)

            assert row is not None
            assert row["feedback_type"] == "dislike"
            assert row["feedback_note"] == "太浅了"
            assert row["feedback_at"] is not None

            db.close()

    def test_update_recommendation_feedback_retries_when_database_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            class _LockingConnection:
                def __init__(self) -> None:
                    self.calls = 0
                    self.commits = 0

                def execute(self, sql: str, params: tuple[object, ...]) -> object:
                    self.calls += 1
                    if self.calls == 1:
                        raise sqlite3.OperationalError("database is locked")

                    class _Cursor:
                        lastrowid = 0

                    return _Cursor()

                def commit(self) -> None:
                    self.commits += 1

            fake_conn = _LockingConnection()
            db._conn = fake_conn  # type: ignore[assignment]

            db.update_recommendation_feedback(
                7,
                feedback_type="dislike",
                feedback_note="太浅了",
            )

            assert fake_conn.calls == 3
            assert fake_conn.commits == 2

    def test_notification_candidate_prefers_unpresented_unnotified_high_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "test.db")
            db.initialize()

            db.cache_content("BVLOW", title="普通内容", up_name="普通UP", source="search")
            db.cache_content(
                "BVHIGH",
                title="高置信内容",
                up_name="高能UP",
                source="trending",
                topic_group="运动康复",
                pool_topic_label="身体训练",
                tags=["久坐", "拉伸"],
            )
            low_id = db.insert_recommendation("BVLOW", confidence=0.7, presented=0)
            high_id = db.insert_recommendation("BVHIGH", confidence=0.91, presented=0)

            candidate = db.get_notification_candidate(min_confidence=0.82)

            assert candidate is not None
            assert candidate["id"] == high_id
            assert candidate["bvid"] == "BVHIGH"
            assert candidate["topic_group"] == "运动康复"
            assert candidate["pool_topic_label"] == "身体训练"
            assert json.loads(candidate["tags"]) == ["久坐", "拉伸"]

            db.mark_notification_sent("BVHIGH")

            next_candidate = db.get_notification_candidate(min_confidence=0.82)

            assert next_candidate is None
            assert low_id > 0

            db.close()


class TestEventSatisfactionPersistence:
    """v0.3.x event-satisfaction signal — schema + migration + filtered query."""

    def test_fresh_database_has_satisfaction_columns(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "fresh.db")
        db.initialize()
        columns = {
            str(row["name"]) for row in db.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        assert "inferred_satisfaction" in columns
        assert "satisfaction_reason" in columns
        db.close()

    def test_pre_migration_database_is_additively_upgraded(self, tmp_path: Path) -> None:
        """A v0.3.71 database (events table without the two new columns)
        must boot cleanly after the migration; existing rows get NULL."""
        path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(str(path))
        legacy.executescript(
            """
            CREATE TABLE events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                context     TEXT,
                metadata    TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO events (event_type, title) VALUES ('click', 'legacy row');
            """
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        db.initialize()
        columns = {
            str(row["name"]) for row in db.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        assert "inferred_satisfaction" in columns
        assert "satisfaction_reason" in columns
        legacy_row = db.conn.execute(
            "SELECT inferred_satisfaction, satisfaction_reason FROM events WHERE title = ?",
            ("legacy row",),
        ).fetchone()
        assert legacy_row["inferred_satisfaction"] is None
        assert legacy_row["satisfaction_reason"] is None
        db.close()

    def test_insert_event_persists_classification(self, tmp_path: Path) -> None:
        """insert_event runs classify_event_satisfaction exactly once and
        stores the result alongside the event fields."""
        db = Database(tmp_path / "classified.db")
        db.initialize()

        db.insert_event("like", title="深度教程", url="https://x")
        db.insert_event(
            "click",
            title="标题党",
            metadata={"watch_seconds": 2, "video_duration_seconds": 600},
        )

        rows = db.conn.execute(
            "SELECT event_type, inferred_satisfaction, satisfaction_reason FROM events ORDER BY id"
        ).fetchall()
        assert rows[0]["event_type"] == "like"
        assert rows[0]["inferred_satisfaction"] == "positive"
        assert rows[0]["satisfaction_reason"] == "explicit_engagement"
        assert rows[1]["event_type"] == "click"
        assert rows[1]["inferred_satisfaction"] == "negative"
        assert rows[1]["satisfaction_reason"] == "quick_exit"
        db.close()

    def test_query_events_filter_by_satisfaction_modes(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "filtered.db")
        db.initialize()

        db.insert_event("like", title="好内容")  # → positive
        db.insert_event(
            "click",
            title="标题党",
            metadata={"watch_seconds": 2, "video_duration_seconds": 600},
        )  # → negative
        db.insert_event(
            "click",
            title="未知",
            metadata={"video_duration_seconds": 600},
        )  # → unknown / missing_dwell

        # No filter → all rows.
        assert len(db.query_events(limit=10)) == 3

        # Positive only.
        positives = db.query_events(satisfaction_modes=frozenset({"positive"}), limit=10)
        assert len(positives) == 1
        assert positives[0]["title"] == "好内容"

        # Positive + unknown also includes the missing_dwell row.
        mixed = db.query_events(satisfaction_modes=frozenset({"positive", "unknown"}), limit=10)
        assert {row["title"] for row in mixed} == {"好内容", "未知"}
        db.close()

    def test_query_events_unknown_mode_includes_null_rows(self, tmp_path: Path) -> None:
        """Legacy rows have inferred_satisfaction = NULL. Requesting
        `unknown` must include them so the consumer can opt in to
        unclassified history."""
        path = tmp_path / "legacy-then-modern.db"
        legacy = sqlite3.connect(str(path))
        legacy.executescript(
            """
            CREATE TABLE events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                url         TEXT,
                title       TEXT,
                context     TEXT,
                metadata    TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO events (event_type, title) VALUES ('view', 'legacy NULL row');
            """
        )
        legacy.commit()
        legacy.close()

        db = Database(path)
        db.initialize()
        db.insert_event("like", title="新数据")  # post-migration → positive

        unknown_rows = db.query_events(satisfaction_modes=frozenset({"unknown"}), limit=10)
        titles = {row["title"] for row in unknown_rows}
        # The legacy NULL row must show up under `unknown`.
        assert "legacy NULL row" in titles
        # And the modern positive row must NOT.
        assert "新数据" not in titles
        db.close()


class TestDatabaseMaintenance:
    def test_check_database_integrity_reports_healthy_database(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import check_database_integrity

        db = Database(tmp_path / "healthy.db")
        db.initialize()
        db.insert_event("view", title="健康检查")
        db.close()

        report = check_database_integrity(tmp_path / "healthy.db")

        assert report.healthy is True
        assert report.error == ""

    def test_create_database_backup_copies_db_and_wal(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import create_database_backup

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("db", encoding="utf-8")
        wal_path = tmp_path / "openbiliclaw.db-wal"
        wal_path.write_text("wal", encoding="utf-8")

        backup = create_database_backup(
            db_path,
            tmp_path / "backups",
            timestamp="20260315-020000",
        )

        assert backup.db_backup.read_text(encoding="utf-8") == "db"
        assert backup.wal_backup is not None
        assert backup.wal_backup.read_text(encoding="utf-8") == "wal"

    @pytest.mark.skipif(os.name != "posix", reason="regression covers POSIX SQLite locks")
    def test_scheduled_backup_before_runtime_connection_preserves_wal_locking(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import maybe_create_scheduled_backup

        db_path = tmp_path / "openbiliclaw.db"
        bootstrap = sqlite3.connect(db_path)
        bootstrap.execute("PRAGMA journal_mode=WAL")
        bootstrap.execute("CREATE TABLE writes (value TEXT NOT NULL)")
        bootstrap.execute("INSERT INTO writes VALUES ('bootstrap')")
        bootstrap.commit()
        bootstrap.close()

        backup = maybe_create_scheduled_backup(
            db_path,
            tmp_path / "backups",
            now=datetime(2026, 8, 9, 0, 0, 0),
            minimum_interval=timedelta(0),
        )
        assert backup is not None

        persistent = sqlite3.connect(db_path)
        persistent.execute("PRAGMA journal_mode=WAL")
        persistent.execute("INSERT INTO writes VALUES ('persistent-before-probe')")
        persistent.commit()
        wal_path = db_path.with_name(f"{db_path.name}-wal")
        wal_inode = wal_path.stat().st_ino

        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sqlite3, sys; "
                    "connection = sqlite3.connect(sys.argv[1]); "
                    "result = connection.execute('PRAGMA integrity_check').fetchone()[0]; "
                    "connection.close(); "
                    "raise SystemExit(0 if result == 'ok' else 1)"
                ),
                str(db_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert probe.returncode == 0, probe.stderr
        assert wal_path.exists()
        assert wal_path.stat().st_ino == wal_inode

        persistent.execute("INSERT INTO writes VALUES ('persistent-after-probe')")
        persistent.commit()
        secondary = sqlite3.connect(db_path)
        secondary.execute("INSERT INTO writes VALUES ('secondary-after-probe')")
        secondary.commit()
        secondary.close()
        persistent.close()

        verification = sqlite3.connect(db_path)
        try:
            assert verification.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert verification.execute("SELECT COUNT(*) FROM writes").fetchone() == (4,)
        finally:
            verification.close()

    def test_rotate_database_backups_keeps_recent_daily_and_weekly_sets(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import rotate_database_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for index in range(10):
            stamp = f"202603{index + 1:02d}-020000"
            (backup_dir / f"openbiliclaw-{stamp}.db").write_text("db", encoding="utf-8")

        rotate_database_backups(
            backup_dir,
            keep_daily=3,
            keep_weekly=2,
            now=datetime(2026, 3, 15, 2, 0, 0),
        )

        kept = sorted(path.name for path in backup_dir.glob("*.db"))
        assert len(kept) == 5

    def test_repair_database_returns_healthy_status_without_modifying_healthy_db(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        db.insert_event("view", title="还不用修")
        db.close()
        before = (tmp_path / "openbiliclaw.db").read_bytes()

        result = repair_database(
            tmp_path / "openbiliclaw.db",
            backup_dir=tmp_path / "backups",
        )

        assert result.status == "healthy"
        assert result.repaired_db is None
        assert (tmp_path / "openbiliclaw.db").read_bytes() == before

    def test_repair_database_refuses_when_database_is_in_use(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=["python:86577"],
        )

        assert result.status == "in_use"
        assert "python:86577" in result.message

    def test_repair_database_keeps_original_when_recovery_fails(self, tmp_path: Path) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")
        original = db_path.read_bytes()

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=[],
            integrity_error="database disk image is malformed",
            recovered_sql=None,
        )

        assert result.status == "failed"
        assert db_path.read_bytes() == original
        assert result.repaired_db is None

    def test_repair_database_builds_repaired_copy_when_recovery_sql_is_available(
        self,
        tmp_path: Path,
    ) -> None:
        from openbiliclaw.storage.maintenance import repair_database

        db_path = tmp_path / "openbiliclaw.db"
        db_path.write_text("broken", encoding="utf-8")

        result = repair_database(
            db_path,
            backup_dir=tmp_path / "backups",
            holders=[],
            integrity_error="database disk image is malformed",
            recovered_sql=(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT);"
                "INSERT INTO events (id, title) VALUES (1, '恢复成功');"
            ),
        )

        assert result.status == "repaired"
        assert result.repaired_db is not None
        repaired = sqlite3.connect(result.repaired_db)
        row = repaired.execute("SELECT title FROM events").fetchone()
        repaired.close()
        assert row == ("恢复成功",)


def test_feedback_signals_return_topic_key_and_group() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV_TOPIC",
            title="动画叙事拆解",
            source="search",
            topic_key="动漫解说",
            topic_group="动漫",
        )
        recommendation_id = db.insert_recommendation("BV_TOPIC", confidence=0.9)
        db.update_recommendation_feedback(recommendation_id, feedback_type="dislike")

        rows = db.get_feedback_signals()

        assert rows[0]["topic_key"] == "动漫解说"
        assert rows[0]["topic_group"] == "动漫"


def test_serve_snapshot_computes_delight_boundary_once_and_discards_it(tmp_path) -> None:
    db = Database(tmp_path / "snapshot.db")
    db.initialize()
    _seed_visible(db, "BVBOUNDARY", title="current inventory", source="search")
    original = Database._compute_dynamic_delight_threshold_on
    calls = 0

    def counted(database, conn, *, floor):
        nonlocal calls
        calls += 1
        return original(database, conn, floor=floor)

    try:
        with patch.object(Database, "_compute_dynamic_delight_threshold_on", counted):
            first = db.load_pool_serve_snapshot(limit=10)
            assert first.readiness["available"] == 1
            assert calls == 1
            db.mark_pool_items_shown(["BVBOUNDARY"])
            second = db.load_pool_serve_snapshot(limit=10)
            assert second.readiness["available"] == 0
            assert calls == 2, "a new transaction must recalculate against current inventory"
    finally:
        db.close()


def test_serve_snapshot_reuses_sql_but_rechecks_filters_and_topic_windows(tmp_path) -> None:
    db = Database(tmp_path / "snapshot-query.db")
    db.initialize()
    for i in range(4):
        _seed_visible(db, f"BVQUERY{i}", title="query reuse", source="search")
    original_open = db.open_connection
    original_filter = Database._filter_available_pool_candidate_rows
    queries: list[str] = []
    filter_calls = 0

    def traced_open():
        conn = original_open()
        conn.set_trace_callback(queries.append)
        return conn

    def counted_filter(database, rows, *, viewed_content_keys, now=None):
        nonlocal filter_calls
        filter_calls += 1
        return original_filter(database, rows, viewed_content_keys=viewed_content_keys, now=now)

    def available_queries() -> int:
        return sum(
            statement.lstrip().startswith("SELECT bvid, content_id, source, source_platform")
            for statement in queries
        )

    try:
        with (
            patch.object(db, "open_connection", traced_open),
            patch.object(Database, "_filter_available_pool_candidate_rows", counted_filter),
        ):
            first = db.load_pool_serve_snapshot(limit=10)
            assert first.readiness["available"] == 3
            assert first.readiness["copy_ready"] == 4
            assert len(first.candidate_rows) == 3
            assert available_queries() == 1
            assert filter_calls == 4, "time/seen/linkability checks still run for each caller"
            # A returned row cannot mutate a later request's cached SQL inputs.
            first.candidate_rows[0]["title"] = "modified by caller"
            db.mark_pool_items_shown(["BVQUERY0", "BVQUERY1"])
            queries.clear()
            second = db.load_pool_serve_snapshot(limit=10)
            assert second.readiness["available"] == 2
            assert all(row["title"] == "query reuse" for row in second.candidate_rows)
            assert available_queries() == 1, "new transaction must read committed changes"
            assert filter_calls == 8
    finally:
        db.close()


def test_isolated_readiness_reuses_threshold_and_cleans_caches_on_failure(tmp_path) -> None:
    db = Database(tmp_path / "readiness-cache.db")
    db.initialize()
    _seed_visible(db, "BVREADYQUERY", title="readiness", source="search")
    wrappers: list[Database] = []
    original_isolated = db._isolated_database
    original_threshold = Database._compute_dynamic_delight_threshold_on

    def capture(**kwargs):
        isolated = original_isolated(**kwargs)
        wrappers.append(isolated)
        return isolated

    try:
        with (
            patch.object(db, "_isolated_database", capture),
            patch.object(
                Database,
                "_compute_dynamic_delight_threshold_on",
                autospec=True,
                side_effect=original_threshold,
            ) as compute,
        ):
            assert db.count_pool_readiness_isolated()["available"] == 1
            assert compute.call_count == 1
        with (
            patch.object(db, "_isolated_database", capture),
            patch.object(
                Database,
                "_filter_available_pool_candidate_rows",
                side_effect=RuntimeError("read failed"),
            ),
            pytest.raises(RuntimeError, match="read failed"),
        ):
            db.count_pool_readiness_isolated()
        for isolated in wrappers:
            assert isolated._snapshot_delight_thresholds is None
            assert isolated._snapshot_available_query_rows is None
            assert not isolated._preserve_read_transaction
            assert isolated._conn is None
    finally:
        db.close()
