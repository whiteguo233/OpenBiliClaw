"""Agent-host screen contract using real SQLite and shared director execution."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import Config
from openbiliclaw.integrations.openclaw import bootstrap, cli
from openbiliclaw.integrations.openclaw.errors import (
    AdapterOperationError,
    AdapterValidationError,
)
from openbiliclaw.integrations.openclaw.operations import OpenClawAdapter
from openbiliclaw.integrations.openclaw.schemas import FeedbackRequest
from openbiliclaw.integrations.openclaw.skill import build_openclaw_skills
from openbiliclaw.llm.base import LLMRegistry
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.runtime.event_ingress import EventIngressService
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_runtime import FakeAgent, FakeDecision, make_runtime, seed_database

if TYPE_CHECKING:
    from pathlib import Path


async def _profile() -> SoulProfile:
    return SoulProfile()


def _adapter(
    tmp_path: Path, strategy: str = "agent_only", *, agent: FakeAgent | None = None
) -> OpenClawAdapter:
    db = seed_database(tmp_path / "host.db", count=40)
    runtime = make_runtime(db, strategy=strategy, llm=agent, provider=FakeDecision())
    memory = MemoryManager(tmp_path, database=db)
    memory.initialize()
    return OpenClawAdapter(
        SimpleNamespace(
            config=runtime.config,
            database=db,
            director_runtime=runtime,
            soul_engine=SimpleNamespace(get_profile=_profile),
            memory_manager=memory,
            event_ingress=EventIngressService(memory),
            # Legacy serving would fail here: enabled commands must only call
            # the shared screen runtime, not a second selection/persistence path.
            recommendation_engine=SimpleNamespace(),
            runtime_controller=SimpleNamespace(),
        )
    )


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
async def test_host_consumption_uses_whole_screen_and_distinct_next_stay(
    tmp_path: Path, strategy: str
) -> None:
    adapter = _adapter(tmp_path, strategy)
    db, runtime = adapter.services.database, adapter.services.director_runtime
    try:
        empty = await adapter.restore_recommendations()
        assert empty.requires_generation and not empty.items
        assert db.count_recommendations() == 0
        first = await adapter.recommend(limit=4, request_id="first")
        assert len(first.items) == 4 and first.batch_ref
        assert first.director_strategy == strategy
        assert first.stream_ref == "openclaw"
        assert all(item.batch_ref == first.batch_ref for item in first.items)
        assert all(item.recommendation_id > 0 for item in first.items)
        assert all(not item.presented for item in first.items)
        assert not runtime.store.session("openclaw").real_feedback
        assert await adapter.restore_recommendations() == first
        assert await adapter.recommend(limit=4, request_id="first") == first
        assert db.count_recommendations() == 4

        root_batch = runtime.store.restore("openclaw")
        replacement = await adapter.reshuffle(limit=4, request_id="replacement")
        replaced_batch = runtime.store.restore("openclaw")
        assert root_batch is not None and replaced_batch is not None
        assert replaced_batch.experience_step_seq == root_batch.experience_step_seq
        assert replaced_batch.direction_ref == root_batch.direction_ref
        assert replaced_batch.direction == root_batch.direction
        assert replaced_batch.root_batch_ref == root_batch.batch_ref
        assert not (
            {item.item_key for item in first.items} & {item.item_key for item in replacement.items}
        )
        advanced = await adapter.append_recommendations(limit=4, request_id="next")
        advanced_batch = runtime.store.restore("openclaw")
        assert advanced_batch is not None and advanced_batch.experience_step_seq == 2
        assert advanced.batch_ref != replacement.batch_ref
        assert db.count_recommendations() == 12
        # Host tool delivery has not synthesized any exposure/positive feedback.
        assert db.get_recent_events() == []
    finally:
        await adapter.aclose()


async def test_host_ack_is_explicit_subset_idempotent_and_not_restore(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    db, runtime = adapter.services.database, adapter.services.director_runtime
    try:
        screen = await adapter.recommend(limit=4, request_id="screen")
        assert not db.get_recent_events()
        ack_args = dict(
            batch_ref=screen.batch_ref,
            ack_id="visible-pair",
            item_keys=[item.item_key for item in screen.items[:2]],
        )
        accepted = await adapter.present_recommendations(**ack_args)
        assert accepted.item_keys == ack_args["item_keys"]
        assert len(accepted.event_ids) == 2
        assert await adapter.present_recommendations(**ack_args) == accepted
        assert len(db.get_recent_events()) == 2
        assert all(
            event.kind == "presented" for event in runtime.store.session("openclaw").real_feedback
        )
        restored = await adapter.restore_recommendations()
        assert [item.item_key for item in restored.items] == [
            item.item_key for item in screen.items
        ]
        assert db.count_recommendations() == 4
        with pytest.raises(AdapterValidationError):
            await adapter.present_recommendations(
                batch_ref=screen.batch_ref, ack_id="wrong", item_keys=["not-in-this-screen"]
            )
        assert len(db.get_recent_events()) == 2
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("operation", ["restore", "replay"])
async def test_new_ban_suppresses_old_screen_without_mutation_or_allocation(
    tmp_path: Path, operation: str
) -> None:
    adapter = _adapter(tmp_path)
    db, runtime = adapter.services.database, adapter.services.director_runtime
    latest: list[str] = []
    adapter.services.soul_engine.get_effective_disliked_topics = lambda: latest
    try:
        screen = await adapter.recommend(limit=4, request_id="committed")
        frozen = runtime.store.restore("openclaw")
        state = runtime.store.session("openclaw")
        latest.append("Testing")
        if operation == "restore":
            response = await adapter.restore_recommendations(batch_ref=screen.batch_ref)
        else:
            response = await adapter.recommend(limit=4, request_id="committed")
        assert response.items == [] and response.requires_generation
        assert db.count_recommendations() == 4
        assert runtime.store.restore("openclaw") == frozen
        assert runtime.store.session("openclaw") == state
        assert not db.get_recent_events()
        latest.clear()
        assert await adapter.restore_recommendations(batch_ref=screen.batch_ref) == screen
    finally:
        await adapter.aclose()


async def test_host_exact_feedback_projects_existing_event_without_duplicate(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    db, runtime = adapter.services.database, adapter.services.director_runtime
    try:
        screen = await adapter.recommend(limit=2, request_id="first")
        item = screen.items[0]
        request = FeedbackRequest(
            recommendation_id=item.recommendation_id,
            feedback_type="dislike",
            request_id="feedback-" + "x" * 300,
            batch_ref=screen.batch_ref,
            note="Prefer another direction",
        )
        receipt = await adapter.submit_feedback(request)
        assert receipt.ok and receipt.event_id > 0 and not receipt.duplicate
        duplicate = await adapter.submit_feedback(request)
        assert duplicate.duplicate and duplicate.event_id == receipt.event_id
        assert len(db.get_recent_events()) == 1
        session = runtime.store.session("openclaw")
        assert session.state_revision == 1
        assert len(session.real_feedback) == 1
        assert session.real_feedback[0].kind == "dislike"
        assert session.real_feedback[0].batch_ref == screen.batch_ref
        assert runtime.store.lookup_recommendation(item.recommendation_id) == (
            screen.batch_ref,
            item.item_key,
        )
        assert db.get_recommendation_by_id(item.recommendation_id)["feedback_type"] == "dislike"
        with pytest.raises(AdapterValidationError, match="batch_item_mismatch"):
            await adapter.submit_feedback(
                FeedbackRequest(
                    recommendation_id=item.recommendation_id,
                    feedback_type="like",
                    request_id="bad-binding",
                    batch_ref="another-batch",
                )
            )
        assert len(db.get_recent_events()) == 1
    finally:
        await adapter.aclose()


async def test_host_request_validation_and_replay_bind_whole_payload(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    try:
        with pytest.raises(AdapterValidationError, match="stable request_id"):
            await adapter.recommend(limit=4)
        with pytest.raises(AdapterValidationError, match="1–20"):
            await adapter.recommend(limit=21, request_id="too-many")
        with pytest.raises(AdapterValidationError, match="precomputed"):
            await adapter.recommend(limit=4, request_id="realtime", realtime=True)
        screen = await adapter.recommend(limit=2, request_id="payload")
        with pytest.raises(AdapterValidationError, match="request"):
            await adapter.recommend(
                limit=2, request_id="payload", excluded_item_ids=[screen.items[0].item_key]
            )
        assert adapter.services.database.count_recommendations() == 2
        scoped = await adapter.recommend(
            limit=2, request_id="scoped", stream_ref="host", source_platform="bilibili"
        )
        assert scoped.stream_ref == "host:bilibili"
        assert (
            await adapter.restore_recommendations(stream_ref="host", source_platform="bilibili")
            == scoped
        )
    finally:
        await adapter.aclose()


async def test_enabled_host_cannot_silently_fall_back_when_runtime_missing(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    runtime = adapter.services.director_runtime
    adapter.services.director_runtime = None
    try:
        with pytest.raises(AdapterOperationError, match="unavailable"):
            await adapter.recommend(request_id="screen")
    finally:
        adapter.services.director_runtime = runtime
        await adapter.aclose()


async def test_host_ban_added_during_planning_rejects_entire_screen(tmp_path: Path) -> None:
    agent = FakeAgent()
    agent.release = asyncio.Event()
    adapter = _adapter(tmp_path, agent=agent)
    latest: list[str] = []
    adapter.services.soul_engine.get_effective_disliked_topics = lambda: latest
    try:
        task = asyncio.create_task(adapter.recommend(limit=4, request_id="profile-race"))
        await asyncio.wait_for(agent.started.wait(), timeout=3)
        latest.append("Testing")
        agent.release.set()
        with pytest.raises(AdapterValidationError, match="latest_dislike_changed"):
            await task
        assert adapter.services.database.count_recommendations() == 0
        assert (await adapter.restore_recommendations()).requires_generation
    finally:
        await adapter.aclose()


async def test_descriptor_generation_restore_present_and_feedback_contract(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    descriptors = {item.name: item for item in build_openclaw_skills(adapter)}
    try:
        for name in ("recommend", "append_recommendations", "reshuffle"):
            properties = descriptors[f"openbiliclaw_{name}"].input_schema["properties"]
            assert {"request_id", "stream_ref", "explicit_intent"} <= properties.keys()
        generate = descriptors["openbiliclaw_recommend"].handler
        restore = descriptors["openbiliclaw_restore_recommendations"].handler
        present = descriptors["openbiliclaw_present_recommendations"].handler
        assert generate is not None and restore is not None and present is not None
        missing_id = await generate({"limit": 2})
        assert not missing_id["ok"] and missing_id["error_type"] == "validation_error"
        result = await generate({"limit": 2, "request_id": "tool"})
        assert result["ok"] and not adapter.services.database.get_recent_events()
        assert (await restore({}))["data"] == result["data"]
        screen = result["data"]
        ack = await present(
            {
                "batch_ref": screen["batch_ref"],
                "ack_id": "actual-visibility",
                "item_keys": [screen["items"][0]["item_key"]],
            }
        )
        assert ack["ok"] and len(adapter.services.database.get_recent_events()) == 1
        assert "batch_ref" in descriptors["openbiliclaw_submit_feedback"].input_schema["properties"]
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("filter_only", [False, True])
async def test_bootstrap_attaches_real_runtime_and_independent_filter_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filter_only: bool
) -> None:
    config = Config(data_dir=str(tmp_path))
    config.director.mode = "off" if filter_only else "enforce"
    config.director.strategy = "agent_only"
    if filter_only:
        config.discovery.semantic_filter.mode = "shadow"
    provider = FakeDecision()
    monkeypatch.setattr(bootstrap, "load_config", lambda: config)
    monkeypatch.setattr(bootstrap, "build_llm_registry", lambda _config: LLMRegistry())
    monkeypatch.setattr(
        "openbiliclaw.recommendation.director.runtime.build_decision_provider",
        lambda _config: provider,
    )
    services = bootstrap.build_openclaw_adapter_services()
    adapter = OpenClawAdapter(services)
    try:
        assert services.director_runtime is not None
        assert services.director_runtime.enabled is not filter_only
        if filter_only:
            assert services.director_runtime.semantic_filter is not None
            assert (
                services.discovery_engine._semantic_filter
                is services.director_runtime.semantic_filter
            )
        else:
            assert services.director_runtime.provider is None
        assert not provider.requests
    finally:
        await adapter.aclose()
    if filter_only:
        assert provider.closed


def test_cli_director_parameters_and_same_loop_close(
    tmp_path: Path, capsys: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # Other CLI tests may leave a console logger installed. Fixture creation is
    # outside this JSON command and asyncio DEBUG logs are not its protocol.
    caplog.set_level("WARNING")
    adapter = _adapter(tmp_path)
    provider = adapter.services.director_runtime.provider
    capsys.readouterr()
    assert (
        cli.main(
            [
                "recommend",
                "--limit",
                "2",
                "--request-id",
                "cli-generation",
                "--stream-ref",
                "cli-host",
                "--explicit-intent",
                "New ideas",
            ],
            adapter=adapter,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["data"]["stream_ref"] == "cli-host"
    assert payload["data"]["request_id"] == "cli-generation"
    assert all(not item["presented"] for item in payload["data"]["items"])
    assert provider.closed
    asyncio.run(adapter.aclose())  # Resource shutdown is idempotent.
