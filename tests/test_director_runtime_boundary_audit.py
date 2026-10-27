"""Integration boundaries found by an independent director contract audit."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI

from openbiliclaw.api.director import register_director_routes
from openbiliclaw.recommendation.director.store import StoreConflict
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_runtime import FakeAgent, FakeDecision, make_runtime, seed_database

if TYPE_CHECKING:
    from pathlib import Path


async def test_stay_after_agent_to_jev_switch_keeps_chain_without_plan_binding(
    tmp_path: Path,
) -> None:
    database = seed_database(tmp_path / "strategy-switch.db")
    old_runtime = make_runtime(database)
    new_runtime = None
    try:
        root = await old_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="agent-root", limit=4
        )
        assert root is not None and root.plan_id is not None
        await old_runtime.aclose()
        new_runtime = make_runtime(database, strategy="jev_recurrent", provider=FakeDecision())
        replacement = await new_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="jev-stay", intent="STAY", limit=4
        )
        assert replacement is not None
        assert replacement.root_batch_ref == root.batch_ref
        assert replacement.direction == root.direction
        assert replacement.execution_binding == "SESSION_STAY"
        assert replacement.plan_id is None
    finally:
        await old_runtime.aclose()
        if new_runtime is not None:
            await new_runtime.aclose()
        database.close()


async def test_expired_future_plan_does_not_prevent_current_direction_stay(
    tmp_path: Path,
) -> None:
    database = seed_database(tmp_path / "expired-future.db")
    runtime = make_runtime(database)
    try:
        root = await runtime.generate(SoulProfile(), stream_ref="s", request_id="root", limit=4)
        assert root is not None
        stored = runtime.store.load_plan("s", include_stale=True)
        assert stored is not None
        expired = dict(stored.plan)
        expired.update(created_at=0.0, expires_at=1.0)
        connection = database.open_connection()
        try:
            connection.execute(
                "UPDATE director_plans SET expires_at = 1, plan_json = ? WHERE stream_ref = 's'",
                (json.dumps(expired),),
            )
            connection.commit()
        finally:
            connection.close()
        replacement = await runtime.generate(
            SoulProfile(), stream_ref="s", request_id="stay", intent="STAY", limit=4
        )
        assert replacement is not None
        assert replacement.direction == root.direction
        assert replacement.experience_step_seq == root.experience_step_seq
    finally:
        await runtime.aclose()
        database.close()


async def test_obsolete_process_cannot_reactivate_previous_configuration(
    tmp_path: Path,
) -> None:
    database = seed_database(tmp_path / "obsolete-process.db")
    old_runtime = make_runtime(database)
    new_runtime = None
    try:
        assert await old_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="old-first", limit=4
        )
        # Another process activates the new strategy; it cannot mutate this
        # process's local `_closed` flag. SQLite must be the authority.
        new_runtime = make_runtime(database, strategy="jev_recurrent", provider=FakeDecision())
        assert await new_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="new-first", limit=4
        )
        with pytest.raises(StoreConflict):
            await old_runtime.generate(
                SoulProfile(), stream_ref="s", request_id="old-late", limit=4
            )
        restored = await new_runtime.restore("s")
        assert restored is not None and restored.request_id == "new-first"
    finally:
        await old_runtime.aclose()
        if new_runtime is not None:
            await new_runtime.aclose()
        database.close()


async def test_committed_request_retry_after_strategy_change_restores_same_batch(
    tmp_path: Path,
) -> None:
    database = seed_database(tmp_path / "committed-retry.db")
    old_runtime = make_runtime(database)
    new_runtime = None
    try:
        committed = await old_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="lost-response", limit=4
        )
        assert committed is not None
        await old_runtime.aclose()
        # The browser retains the request ID after losing the response. A
        # route change is not a different user generation request.
        new_runtime = make_runtime(database, strategy="jev_recurrent", provider=FakeDecision())
        replay = await new_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="lost-response", limit=4
        )
        assert replay == committed
        assert database.count_recommendations() == 4
    finally:
        await old_runtime.aclose()
        if new_runtime is not None:
            await new_runtime.aclose()
        database.close()


async def test_profile_topic_ban_during_planning_cannot_commit_old_candidates(
    tmp_path: Path,
) -> None:
    database = seed_database(tmp_path / "latest-ban.db")
    agent = FakeAgent()
    agent.release = asyncio.Event()
    runtime = make_runtime(database, llm=agent)
    current_profile = SoulProfile()

    async def latest_profile() -> SoulProfile:
        return current_profile

    app = FastAPI()
    register_director_routes(
        app,
        SimpleNamespace(
            database=database,
            director_runtime=runtime,
            soul_engine=SimpleNamespace(
                get_profile=latest_profile,
                get_effective_disliked_topics=lambda: current_profile.preferences.disliked_topics,
            ),
        ),
    )
    pending = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/api/recommendations/director/generate",
                    json={"request_id": "ban-race", "limit": 4},
                )
            )
            await asyncio.wait_for(agent.started.wait(), timeout=5)
            # A profile edit / chat may not belong to any recommendation batch
            # and therefore does not increment this stream's feedback cursor.
            current_profile = SoulProfile()
            current_profile.preferences.disliked_topics = ["Testing"]
            assert runtime.store.session("default").state_revision == 0
            agent.release.set()
            response = await pending
            assert response.status_code in (200, 409)
            if response.status_code == 200:
                assert response.json()["items"] == []
            assert database.count_recommendations() == 0
    finally:
        agent.release.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.aclose()
        database.close()
