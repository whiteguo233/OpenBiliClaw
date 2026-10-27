"""HTTP screen contract against a real director runtime and SQLite store."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI

from openbiliclaw.api.director import (
    project_feedback,
    register_director_routes,
    validate_feedback_binding,
)
from openbiliclaw.recommendation.director.store import StoreConflict
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_runtime import FakeAgent, make_runtime, seed_database

if TYPE_CHECKING:
    from pathlib import Path


async def profile() -> SoulProfile:
    return SoulProfile()


async def test_explicit_generate_restores_exact_screen_and_get_never_allocates(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "http.db")
    runtime = make_runtime(db)
    app = FastAPI()
    ctx = SimpleNamespace(
        database=db, director_runtime=runtime, soul_engine=SimpleNamespace(get_profile=profile)
    )
    register_director_routes(app, ctx)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            empty = await client.get("/api/recommendations/director/restore?stream_ref=web")
            assert empty.status_code == 200
            assert empty.json()["items"] == []
            assert empty.json()["director"]["requires_generation"]
            assert db.count_recommendations() == 0
            payload = {"stream_ref": "web", "request_id": "request1", "limit": 4}
            response = await client.post("/api/recommendations/director/generate", json=payload)
            assert response.status_code == 200
            generated = response.json()
            assert len(generated["items"]) == 4
            assert all(
                item["batch_ref"] == generated["director"]["batch_ref"]
                for item in generated["items"]
            )
            assert all(not item["presented"] for item in generated["items"])
            restored = await client.get("/api/recommendations/director/restore?stream_ref=web")
            assert restored.status_code == 200
            assert restored.json()["items"] == generated["items"]
            replay = await client.post("/api/recommendations/director/generate", json=payload)
            assert replay.json()["items"] == generated["items"]
            assert db.count_recommendations() == 4
    finally:
        await runtime.aclose()
        db.close()


async def test_presentation_ack_only_counts_rendered_subset_and_retries_once(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "present.db")
    runtime = make_runtime(db)
    app = FastAPI()
    register_director_routes(
        app,
        SimpleNamespace(
            database=db, director_runtime=runtime, soul_engine=SimpleNamespace(get_profile=profile)
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            generated = (
                await client.post(
                    "/api/recommendations/director/generate", json={"request_id": "one", "limit": 4}
                )
            ).json()
            batch_ref = generated["director"]["batch_ref"]
            item_key = generated["items"][1]["item_key"]
            ack = {"batch_ref": batch_ref, "ack_id": "visible-one", "item_keys": [item_key]}
            accepted = await client.post("/api/recommendations/director/present", json=ack)
            assert accepted.status_code == 200
            assert accepted.json()["item_keys"] == [item_key]
            assert accepted.json()["state_revision"] == 1
            retry = await client.post("/api/recommendations/director/present", json=ack)
            assert retry.json() == accepted.json()
            assert len(db.get_recent_events()) == 1
            assert runtime.store.session("default").real_feedback[0].kind == "presented"
            bad = await client.post(
                "/api/recommendations/director/present",
                json={**ack, "ack_id": "bad", "item_keys": ["not-in-batch"]},
            )
            assert bad.status_code == 409
            assert runtime.store.session("default").state_revision == 1
    finally:
        await runtime.aclose()
        db.close()


async def test_duplicate_ack_items_are_client_error_not_server_error(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "invalid-ack.db")
    runtime = make_runtime(db)
    app = FastAPI()
    register_director_routes(
        app,
        SimpleNamespace(
            database=db, director_runtime=runtime, soul_engine=SimpleNamespace(get_profile=profile)
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            generated = (
                await client.post(
                    "/api/recommendations/director/generate", json={"request_id": "one", "limit": 2}
                )
            ).json()
            key = generated["items"][0]["item_key"]
            response = await client.post(
                "/api/recommendations/director/present",
                json={
                    "batch_ref": generated["director"]["batch_ref"],
                    "ack_id": "duplicates",
                    "item_keys": [key, key],
                },
            )
            assert response.status_code in (409, 422)
            assert runtime.store.session("default").state_revision == 0
    finally:
        await runtime.aclose()
        db.close()


async def test_generation_payload_validation_and_request_conflict(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "validation.db")
    runtime = make_runtime(db)
    app = FastAPI()
    register_director_routes(
        app,
        SimpleNamespace(
            database=db, director_runtime=runtime, soul_engine=SimpleNamespace(get_profile=profile)
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            route = "/api/recommendations/director/generate"
            assert (
                await client.post(route, json={"request_id": "r", "limit": 21})
            ).status_code == 422
            assert (
                await client.post(route, json={"request_id": "r", "intent": "RESTORE"})
            ).status_code == 422
            assert (
                await client.post(route, json={"request_id": "r", "source_platform": "unknown"})
            ).status_code == 422
            assert (
                await client.post(route, json={"request_id": "r", "limit": 4})
            ).status_code == 200
            conflict = await client.post(route, json={"request_id": "r", "limit": 6})
            assert conflict.status_code == 409
            assert conflict.json()["detail"] == "request_id_reused"
            assert db.count_recommendations() == 4
    finally:
        await runtime.aclose()
        db.close()


async def test_disabled_director_routes_preserve_opt_in_contract(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "disabled.db", count=0)
    app = FastAPI()
    register_director_routes(
        app,
        SimpleNamespace(
            database=db, director_runtime=None, soul_engine=SimpleNamespace(get_profile=profile)
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            status = await client.get("/api/recommendations/director/status")
            assert status.json()["enabled"] is False
            assert status.json()["mode"] == "off"
            assert (await client.get("/api/recommendations/director/restore")).status_code == 409
            disabled = await client.post(
                "/api/recommendations/director/generate", json={"request_id": "r"}
            )
            assert disabled.status_code == 409
            assert disabled.json()["detail"] == "director_disabled"
            assert db.count_recommendations() == 0
    finally:
        db.close()


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("like", "like"),
        ("dislike", "dislike"),
        ("comment", "explicit_request"),
        ("dismiss", "skip"),
    ],
)
async def test_existing_ingress_feedback_projects_exact_batch_without_second_event(
    tmp_path: Path, kind: str, expected: str
) -> None:
    db = seed_database(tmp_path / "feedback.db")
    runtime = make_runtime(db)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=2)
        assert first is not None
        second = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r2", limit=2)
        assert second is not None
        item = first.items[0]
        event_id = db.insert_event(
            "feedback",
            metadata={
                "recommendation_id": item["recommendation_id"],
                "feedback_type": kind,
                "feedback_note": "reason",
                "bvid": item["bvid"],
            },
        )
        project_feedback(
            db,
            int(item["recommendation_id"]),
            "accepted",
            kind,
            "reason",
            batch_ref=first.batch_ref,
            existing_event_id=event_id,
        )
        project_feedback(
            db,
            int(item["recommendation_id"]),
            "accepted",
            kind,
            "reason",
            batch_ref=first.batch_ref,
            existing_event_id=event_id,
        )
        assert len(db.get_recent_events()) == 1
        session = runtime.store.session("s")
        assert session.state_revision == 1
        assert session.real_feedback[-1].batch_ref == first.batch_ref
        assert session.real_feedback[-1].kind == expected
        with pytest.raises(StoreConflict, match="batch_item_mismatch"):
            validate_feedback_binding(db, int(item["recommendation_id"]), second.batch_ref)
        with pytest.raises(StoreConflict, match="batch_item_mismatch"):
            project_feedback(
                db,
                int(item["recommendation_id"]),
                "wrong",
                kind,
                "reason",
                batch_ref=second.batch_ref,
                existing_event_id=event_id,
            )
    finally:
        await runtime.aclose()
        db.close()


async def test_full_app_routes_restore_generate_click_and_feedback_with_single_event_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openbiliclaw.api.app import create_app
    from openbiliclaw.config import Config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.recommendation.engine import RecommendationEngine

    db = seed_database(tmp_path / "full-app.db")
    memory = MemoryManager(tmp_path / "memory", database=db)
    memory.initialize()
    config = Config(data_dir=str(tmp_path))
    config.director.mode = "enforce"
    config.director.strategy = "agent_only"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_args, **_kwargs: config)
    agent = FakeAgent()
    app = create_app(
        database=db,
        memory_manager=memory,
        soul_engine=SimpleNamespace(get_profile=profile),
        recommendation_engine=RecommendationEngine(agent, db),
    )
    runtime = app.state.runtime_context.director_runtime
    assert runtime is not None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            restored_empty = await client.get("/api/recommendations")
            assert restored_empty.status_code == 200
            assert restored_empty.json()["items"] == []
            assert db.count_recommendations() == 0
            generated = await client.post(
                "/api/recommendations/director/generate", json={"request_id": "screen", "limit": 4}
            )
            assert generated.status_code == 200
            row = generated.json()["items"][0]
            batch_ref = generated.json()["director"]["batch_ref"]
            restored = await client.get("/api/recommendations")
            assert restored.json()["items"] == generated.json()["items"]
            click_payload = {
                "recommendation_id": row["id"],
                "bvid": row["bvid"],
                "batch_ref": batch_ref,
                "request_id": "click-once",
            }
            clicked = await client.post("/api/recommendation-click", json=click_payload)
            assert clicked.status_code == 200
            duplicate = await client.post("/api/recommendation-click", json=click_payload)
            assert duplicate.status_code == 200 and duplicate.json()["duplicate"]
            assert runtime.store.session("default").real_feedback[-1].kind == "click"
            feedback_payload = {
                "recommendation_id": row["id"],
                "batch_ref": batch_ref,
                "feedback_type": "like",
                "note": "helpful",
                "request_id": "feedback-once",
            }
            feedback = await client.post("/api/feedback", json=feedback_payload)
            assert feedback.status_code == 200
            feedback_retry = await client.post("/api/feedback", json=feedback_payload)
            assert feedback_retry.status_code == 200 and feedback_retry.json()["duplicate"]
            session = runtime.store.session("default")
            assert session.state_revision == 2
            assert [event.kind for event in session.real_feedback] == ["click", "like"]
            assert len(db.get_recent_events()) == 2
            wrong = await client.post(
                "/api/feedback",
                json={
                    **feedback_payload,
                    "batch_ref": "wrong",
                    "request_id": "reject-before-ingress",
                },
            )
            assert wrong.status_code == 409
            assert len(db.get_recent_events()) == 2
    finally:
        await runtime.aclose()
        db.close()


@pytest.mark.parametrize("count", [0, 2, 8])
async def test_generation_reports_post_commit_inventory_and_wakes_replenishment(
    tmp_path: Path, count: int
) -> None:
    db = seed_database(tmp_path / "inventory.db", count=count)
    runtime = make_runtime(db)
    app = FastAPI()
    wakes = []
    notifications = []

    async def replenish(**kwargs):
        wakes.append(kwargs)

    async def notify():
        notifications.append(True)
        # A notification failure cannot turn a successful durable commit into
        # a retryable generation failure.
        raise RuntimeError("notification transport unavailable")

    register_director_routes(
        app,
        SimpleNamespace(
            database=db,
            director_runtime=runtime,
            soul_engine=SimpleNamespace(get_profile=profile),
            director_replenish_callback=replenish,
            pool_inventory_commit_callback=notify,
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/recommendations/director/generate", json={"request_id": "screen", "limit": 4}
            )
            assert response.status_code == 200
            data = response.json()
            remaining = max(count - 4, 0)
            assert len(data["items"]) == min(count, 4)
            assert db.count_recommendations() == min(count, 4)
            assert data["pool_status"]["pool_available_count"] == remaining
            assert data["pool_status"]["pool_status_version"] > 0
            assert data["has_more"] is (remaining > 0)
            assert wakes == [{"force": count <= 4, "available_count": remaining}]
            assert len(notifications) == int(count > 0)
    finally:
        await runtime.aclose()
        db.close()


async def test_new_topic_ban_suppresses_restore_and_replay_without_mutating_history(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "restore-ban.db")
    runtime = make_runtime(db)
    app = FastAPI()
    bans: list[str] = []
    register_director_routes(
        app,
        SimpleNamespace(
            database=db,
            director_runtime=runtime,
            soul_engine=SimpleNamespace(
                get_profile=profile, get_effective_disliked_topics=lambda: bans
            ),
        ),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {"request_id": "screen", "limit": 4}
            generated = (
                await client.post("/api/recommendations/director/generate", json=payload)
            ).json()
            assert len(generated["items"]) == 4
            bans.append("Testing")
            for response in (
                await client.get("/api/recommendations/director/restore"),
                await client.post("/api/recommendations/director/generate", json=payload),
            ):
                assert response.status_code == 200
                assert response.json()["items"] == []
                assert response.json()["director"]["requires_generation"]
            assert db.count_recommendations() == 4
            historical = runtime.store.restore("default")
            assert historical is not None and len(historical.items) == 4
            bans.clear()
            restored = await client.get("/api/recommendations/director/restore")
            assert restored.json()["items"] == generated["items"]
    finally:
        await runtime.aclose()
        db.close()
