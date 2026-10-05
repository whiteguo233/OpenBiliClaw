"""Durable guided-init discovery outcomes stay visible through the public status API."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "recovery",
    [
        "inventory",
        "recommendation",
        "retired_recommendation",
        "stale_snapshot",
        "planning",
        "active",
        "success",
        "skipped_retry",
    ],
)
def test_runtime_status_reports_and_retires_failed_initial_discovery(
    tmp_path: Path, recovery: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    memory = MemoryManager(tmp_path / "memory", database=db)
    memory.initialize()
    memory.get_layer("soul").update("personality_portrait", "A test profile")
    runtime = ContinuousRefreshController(memory, db, None, None, None)
    app = create_app(database=db, memory_manager=memory, runtime_controller=runtime)
    runtime = app.state.runtime_context.runtime_controller
    coord = app.state.runtime_context.init_coordinator
    assert coord.try_start("failed-discovery")
    asyncio.run(
        coord.stage_done("failed-discovery", 4, status="warning", reason="discovery_partial")
    )
    asyncio.run(
        coord.complete("failed-discovery", partial_success=True, reason="discovery_partial")
    )
    client = TestClient(app)

    failed = client.get("/api/runtime-status").json()
    assert "画像已保存" in failed.get("discovery_failure_message", "")
    assert "重试" in failed["discovery_failure_message"]

    if recovery in {"inventory", "recommendation", "retired_recommendation", "stale_snapshot"}:
        # Formal discover supply can recover without a manual refresh.
        db.cache_content(
            "recovered-instagram",
            title="Recovered",
            source="instagram-topic",
            source_platform="instagram",
            content_id="recovered-instagram",
            relevance_score=0.9,
            pool_expression="For you",
            pool_topic_label="Topic",
            topic_group="Topic",
            style_key="tutorial",
        )
        if recovery in {"recommendation", "retired_recommendation"}:
            db.batch_insert_recommendations_and_mark_shown(
                [{"bvid": "recovered-instagram", "confidence": 0.9, "expression": "For you"}],
                ["recovered-instagram"],
            )
        if recovery == "retired_recommendation":
            db.mark_pool_purged_by_reinit()
            assert coord.try_start("retired-history-failure")
            asyncio.run(
                coord.complete(
                    "retired-history-failure", partial_success=True, reason="discovery_partial"
                )
            )
            assert (
                "画像已保存"
                in client.get("/api/runtime-status").json()["discovery_failure_message"]
            )
            assert memory.load_resolved_init_discovery_run() == ""
            return
        if recovery == "stale_snapshot":
            read_counts = db.count_pool_readiness
            armed = True

            def force_reinit_after_snapshot(**kwargs: object) -> dict[str, int]:
                nonlocal armed
                counts = read_counts(**kwargs)
                if armed:
                    armed = False
                    db.mark_pool_purged_by_reinit()
                    assert coord.try_start("snapshot-new-failure")
                    asyncio.run(
                        coord.complete(
                            "snapshot-new-failure", partial_success=True, reason="discovery_partial"
                        )
                    )
                return counts

            # Inject a concurrent commit at the real database-read boundary;
            # every value still comes from the production storage methods.
            monkeypatch.setattr(db, "count_pool_readiness", force_reinit_after_snapshot)
            assert (
                "画像已保存"
                in client.get("/api/runtime-status").json()["discovery_failure_message"]
            )
            assert memory.load_resolved_init_discovery_run() == ""
            return
    elif recovery == "planning":
        runtime.keyword_planner_mark_explore_planned()
        assert "画像已保存" in client.get("/api/runtime-status").json()["discovery_failure_message"]
        return
    elif recovery == "skipped_retry":

        async def skip_locked_retry() -> None:
            # Inject contention from another worker, then observe public
            # admission/status rather than internal result flags.
            async with runtime._refresh_lock:
                assert (await runtime.trigger_manual_refresh())["accepted"] is True
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if runtime.get_runtime_status()["manual_refresh_state"] != "running":
                        break

        asyncio.run(skip_locked_retry())
        assert runtime.get_runtime_status()["manual_refresh_state"] == "success"
        assert "画像已保存" in client.get("/api/runtime-status").json()["discovery_failure_message"]
        assert coord.try_start("later-failure")
        asyncio.run(
            coord.complete("later-failure", partial_success=True, reason="discovery_partial")
        )
        assert "画像已保存" in client.get("/api/runtime-status").json()["discovery_failure_message"]
        return
    else:
        assert coord.try_start("new-discovery")
        if recovery == "success":
            asyncio.run(coord.complete("new-discovery"))
    assert client.get("/api/runtime-status").json()["discovery_failure_message"] == ""
    if recovery == "inventory":
        original_run = db.get_latest_init_run()
        db.mark_items_seen("instagram", ["recovered-instagram"])
        consumed = client.get("/api/runtime-status").json()
        assert consumed["pool_available_count"] == 0
        assert consumed["discovery_failure_message"] == ""
        memory.get_layer("soul").save()
        restored_memory = MemoryManager(tmp_path / "memory", database=db)
        restored_memory.initialize()
        restored = ContinuousRefreshController(restored_memory, db, None, None, None)
        assert restored.get_runtime_status()["discovery_failure_message"] == ""
        assert restored_memory.load_resolved_init_discovery_run() == "failed-discovery"
        receipt = tmp_path / "memory" / "memory" / "init_discovery_resolution.json"
        first_mtime = receipt.stat().st_mtime_ns
        restored.get_runtime_status()
        assert receipt.stat().st_mtime_ns == first_mtime
        assert db.get_latest_init_run() == original_run
        assert coord.try_start("after-recovery-failure")
        asyncio.run(
            coord.complete(
                "after-recovery-failure", partial_success=True, reason="discovery_partial"
            )
        )
        assert "画像已保存" in restored.get_runtime_status()["discovery_failure_message"]
        # A delayed old owner may not overwrite the bounded current receipt.
        assert not restored_memory.record_resolved_init_discovery_run("unknown-old-run")
        assert restored_memory.load_resolved_init_discovery_run() == "failed-discovery"
