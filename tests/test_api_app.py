"""Tests for the backend API app."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from openbiliclaw import __version__
from openbiliclaw.api.app import _recommendation_snapshot_rows_and_expiry, create_app
from openbiliclaw.llm.service import LLMResponseContentError


def _wait_for_config_apply(
    client: Any,
    expected: str = "applied",
    *,
    revision: int | None = None,
) -> dict[str, object]:
    """等待后台配置应用进入预期终态。"""
    for _ in range(200):
        status = client.get("/api/config/apply-status").json()
        status_revision = (
            status.get("applied_revision")
            if expected == "applied"
            else status.get("requested_revision")
        )
        if status["state"] == expected and (revision is None or status_revision == revision):
            return status
        time.sleep(0.01)
    pytest.fail(f"后台配置状态未进入 {expected}（revision={revision}）")


async def _wait_for_config_apply_async(
    client: Any,
    expected: str = "applied",
    *,
    revision: int | None = None,
) -> dict[str, object]:
    """异步等待后台配置应用进入预期终态。"""
    for _ in range(200):
        status = (await client.get("/api/config/apply-status")).json()
        status_revision = (
            status.get("applied_revision")
            if expected == "applied"
            else status.get("requested_revision")
        )
        if status["state"] == expected and (revision is None or status_revision == revision):
            return status
        await asyncio.sleep(0.01)
    pytest.fail(f"后台配置状态未进入 {expected}（revision={revision}）")


def test_extension_debug_relay_route_is_not_registered() -> None:
    """Temporary extension debug events must not have a production API route."""
    source = inspect.getsource(create_app)
    assert "/api/sources/_debug/log" not in source
    assert "ext_debug_log" not in source


class _EventPersistenceSpy:
    """Minimal pre-receipt persistence adapter for injected API tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)


def _dialogue_entry_source(symbol: str, branch_predicate: str = "") -> str:
    """Return one current entry function without matching unrelated branches."""
    if symbol.startswith("SoulEngine."):
        from openbiliclaw.soul.engine import SoulEngine

        selected = inspect.getsource(getattr(SoulEngine, symbol.removeprefix("SoulEngine.")))
    elif symbol.startswith("SocraticDialogue."):
        from openbiliclaw.soul.dialogue import SocraticDialogue

        selected = inspect.getsource(
            getattr(SocraticDialogue, symbol.removeprefix("SocraticDialogue."))
        )
    else:
        source = inspect.getsource(create_app)
        tree = ast.parse(source)
        matches = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == symbol
        ]
        assert len(matches) == 1, f"expected one create_app nested function named {symbol}"
        node = matches[0]
        assert node.end_lineno is not None
        lines = source.splitlines()
        selected = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    selected = textwrap.dedent(selected)
    if not branch_predicate:
        return selected
    branch_tree = ast.parse(selected)
    matches = [
        node
        for node in ast.walk(branch_tree)
        if isinstance(node, ast.If) and ast.unparse(node.test) == branch_predicate
    ]
    assert len(matches) == 1, f"expected one {symbol} branch {branch_predicate!r}"
    return "\n".join(
        segment
        for statement in matches[0].body
        if (segment := ast.get_source_segment(selected, statement)) is not None
    )


@pytest.mark.parametrize(
    ("entry", "branch_predicate", "submission_tokens", "direct_mutation_tokens"),
    [
        pytest.param(
            "act_on_chat_card",
            "",
            ("_settle_hypothesis(",),
            (
                "_apply_hypothesis_settlement(",
                "_defer_hypothesis_card(",
                "_discuss_hypothesis_card(",
            ),
            id="card-confirm-reject",
        ),
        pytest.param(
            "act_on_chat_card",
            "",
            ("DialogueJobKind.CARD_DEFER",),
            ("anchor_manager.release(", "update_payload(", "_defer_dialogue_confirmation("),
            id="card-defer",
        ),
        pytest.param(
            "act_on_chat_card",
            "",
            ("DialogueJobKind.CARD_DISCUSS",),
            ("begin(", "anchor_manager.establish(", "rollback("),
            id="card-discuss",
        ),
        pytest.param(
            "insight_feedback",
            "",
            ("result = await _settle_hypothesis(",),
            ("_apply_hypothesis_settlement(",),
            id="legacy-feedback",
        ),
        pytest.param(
            "_prepare_confusion_confirmation",
            "",
            ("DialogueJobKind.CONFUSION_OPEN_SYNC",),
            ("confusion_manager.schedule_ask(", "update_confusion("),
            id="pending-open-sync",
        ),
        pytest.param(
            "_create_confirmation_turn",
            "",
            ("DialogueJobKind.ANCHOR_ESTABLISH",),
            ("anchor_manager.establish(",),
            id="pending-open-anchor",
        ),
        pytest.param(
            "SocraticDialogue.respond",
            "self._learning_mode is DialogueLearningMode.QUEUED",
            ("queue.submit(", "DialogueJobKind.LEARN"),
            ("learn_fn(", "_apply_dialogue_settlement("),
            id="ordinary-chat-settle",
        ),
        pytest.param(
            "_apply_durable_chat_success_side_effects",
            "turn.scope == 'probe'",
            ("DialogueJobKind.PROBE_REPLY_APPLY",),
            ("_record_probe_feedback_history(", "_record_probe_cognition("),
            id="durable-probe-reply",
        ),
        pytest.param(
            "_apply_durable_chat_success_side_effects",
            "turn.scope == 'confusion'",
            ("DialogueJobKind.CONFUSION_REPLY_APPLY",),
            ("_record_probe_cognition(", "_publish_probe_event("),
            id="durable-confusion-reply",
        ),
    ],
)
def test_declared_dialogue_entries_submit_without_direct_mutation(
    entry: str,
    branch_predicate: str,
    submission_tokens: tuple[str, ...],
    direct_mutation_tokens: tuple[str, ...],
) -> None:
    """Q1/F3: every declared entry submits and performs no protected mutation."""
    source = _dialogue_entry_source(entry, branch_predicate)
    observed = {
        "submitted": all(token in source for token in submission_tokens),
        "direct_mutation": any(token in source for token in direct_mutation_tokens),
    }
    assert observed == {"submitted": True, "direct_mutation": False}


def test_source_platform_helpers_delegate_to_canonical_registry() -> None:
    from openbiliclaw.api.app import _infer_source_platform_from_url, _normalize_source_platform

    assert _normalize_source_platform("zh") == "zhihu"
    assert _normalize_source_platform("") == "bilibili"
    assert _infer_source_platform_from_url("https://www.zhihu.com/question/1/answer/2") == "zhihu"
    assert _infer_source_platform_from_url("https://example.com/zhihu.com/question/1") == ""


def test_discovery_config_response_caps_candidate_eval_concurrency_at_three() -> None:
    from pydantic import ValidationError

    from openbiliclaw.api.models import DiscoveryConfigOut

    assert DiscoveryConfigOut(candidate_eval_concurrency=3).candidate_eval_concurrency == 3
    with pytest.raises(ValidationError):
        DiscoveryConfigOut(candidate_eval_concurrency=4)
    assert DiscoveryConfigOut(keyword_digest_grace_hours=0).keyword_digest_grace_hours == 0
    with pytest.raises(ValidationError):
        DiscoveryConfigOut(keyword_digest_grace_hours=169)


def test_discovery_config_response_defaults_to_hybrid_with_visual_features_off() -> None:
    from openbiliclaw.api.models import DiscoveryConfigOut

    config = DiscoveryConfigOut()

    assert config.keyword_generation_mode == "hybrid"
    assert config.multimodal_evaluation_enabled is False
    assert config.visual_profile_enabled is False
    assert config.keyframe_enabled is False


def assert_publication(payload: dict[str, object]) -> None:
    assert payload["published_at"] == "2026-07-08T06:30:00Z"
    assert payload["published_label"] == "3 days ago"


def _wait_for_presence_count(ctx: object, expected: int) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        snapshot = ctx.presence.snapshot()
        if snapshot["active_count"] == expected:
            return
        time.sleep(0.01)
    assert ctx.presence.snapshot()["active_count"] == expected


def _injected_soul_engine(gate: object) -> object:
    from openbiliclaw.soul.engine import SoulEngine

    class Registry:
        default_provider = "fake"

        def is_chat_capable(self, name: str) -> bool:
            return name == "fake"

        async def complete(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("provider call was not expected")

        async def complete_provider(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("provider call was not expected")

    memory = SimpleNamespace(_data_dir=None)
    return SoulEngine(
        llm=Registry(),  # type: ignore[arg-type]
        memory=memory,  # type: ignore[arg-type]
        llm_concurrency_gate=gate,
    )


def test_injected_runtime_adopts_shared_soul_controller_gate(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate

    gate = LLMConcurrencyGate(2)
    soul = _injected_soul_engine(gate)
    controller = SimpleNamespace(llm_concurrency_gate=gate, event_hub=None)
    config = Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(
            default_provider="deepseek",
            deepseek=LLMProviderConfig(api_key="test", model="test-model"),
        ),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)

    class ProbeRegistry:
        default_provider = "deepseek"

        def is_chat_capable(self, name: str) -> bool:
            return name == "deepseek"

        async def complete_provider(self, *args: object, **kwargs: object) -> LLMResponse:
            assert gate.status_payload()["llm_total_active"] == 1
            assert gate.status_payload()["llm_background_active"] == 0
            return LLMResponse(content="OK", provider="deepseek")

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry", lambda _config: ProbeRegistry()
    )
    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=soul,
        runtime_controller=controller,
    )
    ctx = app.state.runtime_context

    assert ctx.llm_concurrency_gate is gate
    assert soul._llm_service.concurrency_gate is gate  # type: ignore[attr-defined]
    assert ctx.dialogue._build_service() is soul._llm_service  # type: ignore[attr-defined]
    response = TestClient(app).post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {}},
    )
    assert response.json()["ok"] is True


def test_injected_runtime_adopts_one_sided_gate_and_rejects_conflict(monkeypatch, tmp_path) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    soul_gate = LLMConcurrencyGate(2)
    soul = _injected_soul_engine(soul_gate)
    controller = SimpleNamespace(llm_concurrency_gate=None, event_hub=None)

    soul_app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=soul,
        runtime_controller=controller,
    )
    assert soul_app.state.runtime_context.llm_concurrency_gate is soul_gate
    assert controller.llm_concurrency_gate is soul_gate
    assert soul_gate.status_payload()["llm_total_concurrency"] == 2

    controller_gate = LLMConcurrencyGate(3)
    gate_less_soul = SimpleNamespace(
        _llm_concurrency_gate=None,
        _llm_service=SimpleNamespace(concurrency_gate=None),
    )
    controller = SimpleNamespace(llm_concurrency_gate=controller_gate, event_hub=None)
    controller_app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=gate_less_soul,
        runtime_controller=controller,
    )
    assert controller_app.state.runtime_context.llm_concurrency_gate is controller_gate
    assert gate_less_soul._llm_concurrency_gate is controller_gate
    assert gate_less_soul._llm_service.concurrency_gate is controller_gate

    with pytest.raises(ValueError, match="different LLM concurrency gates"):
        create_app(
            memory_manager=SimpleNamespace(),
            database=SimpleNamespace(),
            soul_engine=_injected_soul_engine(LLMConcurrencyGate(2)),
            runtime_controller=SimpleNamespace(
                llm_concurrency_gate=LLMConcurrencyGate(2), event_hub=None
            ),
        )


def test_injected_compatibility_doubles_receive_fresh_shared_gate(monkeypatch, tmp_path) -> None:
    from openbiliclaw.config import Config, LLMConfig

    config = Config(data_dir=str(tmp_path), llm=LLMConfig(concurrency=3))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    soul = SimpleNamespace()
    controller = SimpleNamespace(event_hub=None)

    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=soul,
        runtime_controller=controller,
    )
    gate = app.state.runtime_context.llm_concurrency_gate

    assert gate.status_payload()["llm_total_concurrency"] == 3
    assert controller.llm_concurrency_gate is gate
    assert soul._llm_concurrency_gate is gate


def test_injected_runtime_initializes_inventory_from_database_and_controller_target(
    monkeypatch, tmp_path
) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState, LLMConcurrencyGate

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)

    class EmptyDatabase:
        def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
            return 0

    gate = LLMConcurrencyGate(4)
    controller = SimpleNamespace(
        llm_concurrency_gate=gate,
        pool_target_count=30,
        event_hub=None,
    )
    app = create_app(
        memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
        database=EmptyDatabase(),
        soul_engine=_injected_soul_engine(gate),
        runtime_controller=controller,
        recommendation_engine=SimpleNamespace(),
    )

    assert gate.inventory_priority_state is InventoryPriorityState.EMPTY
    response = TestClient(app).post("/api/recommendations/append", json={"excluded_bvids": []})
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert gate.inventory_priority_state is InventoryPriorityState.EMPTY


def test_injected_inventory_sync_keeps_compatibility_double_without_target_healthy(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        runtime_controller=SimpleNamespace(event_hub=None),
    )

    assert (
        app.state.runtime_context.llm_concurrency_gate.inventory_priority_state
        is InventoryPriorityState.HEALTHY
    )


def test_failed_late_hot_reload_does_not_mutate_stable_gate_inventory(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState

    current = Config(data_dir=str(tmp_path / "data"))
    current.llm.default_provider = "ollama"
    current.llm.ollama.model = "llama3"
    current.scheduler.pool_target_count = 10
    ctx = build_runtime_context(current)
    gate = ctx.llm_concurrency_gate
    gate.update_inventory(available=10, target=10)
    update_calls: list[tuple[int, int]] = []
    real_update = gate.update_inventory

    def record_update(*, available: int, target: int) -> None:
        update_calls.append((available, target))
        real_update(available=available, target=target)

    monkeypatch.setattr(gate, "update_inventory", record_update)

    class LateFailure:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("late dialogue construction failed")

    monkeypatch.setattr("openbiliclaw.soul.dialogue.SocraticDialogue", LateFailure)
    proposed = Config(data_dir=str(tmp_path / "data"))
    proposed.llm.default_provider = "ollama"
    proposed.llm.ollama.model = "llama3"
    proposed.scheduler.pool_target_count = 30

    with pytest.raises(RuntimeError, match="late dialogue"):
        ctx._rebuild_components(proposed)

    assert update_calls == []
    assert gate.inventory_priority_state is InventoryPriorityState.HEALTHY
    assert ctx.config is current


def test_successful_hot_reload_commits_new_inventory_target(monkeypatch, tmp_path) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState

    current = Config(data_dir=str(tmp_path / "data"))
    current.llm.default_provider = "ollama"
    current.llm.ollama.model = "llama3"
    current.scheduler.pool_target_count = 10
    ctx = build_runtime_context(current)
    gate = ctx.llm_concurrency_gate
    monkeypatch.setattr(ctx.database, "count_pool_candidates", lambda **_kwargs: 10)
    update_calls: list[tuple[int, int]] = []
    real_update = gate.update_inventory

    def record_update(*, available: int, target: int) -> None:
        update_calls.append((available, target))
        real_update(available=available, target=target)

    monkeypatch.setattr(gate, "update_inventory", record_update)
    proposed = Config(data_dir=str(tmp_path / "data"))
    proposed.llm.default_provider = "ollama"
    proposed.llm.ollama.model = "llama3"
    proposed.scheduler.pool_target_count = 30

    ctx._rebuild_components(proposed)

    assert update_calls[-1] == (10, 30)
    assert gate.inventory_priority_state is InventoryPriorityState.REFILL
    assert ctx.config is proposed


def test_api_candidate_snapshot_uses_exact_durable_readiness_and_available_gate(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState

    config = Config(data_dir=str(tmp_path / "data"))
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    config.scheduler.pool_target_count = 10
    ctx = build_runtime_context(config)
    ctx._rebuild_components(config)
    for index in range(4):
        ctx.database.cache_content(
            f"BVPENDINGCOPY{index}",
            title=f"classified admitted row {index}",
            source="search",
            relevance_score=0.9,
            style_key="tutorial",
            topic_group="testing",
        )
    monkeypatch.setattr(
        ctx.database,
        "count_discovery_candidates_by_status",
        lambda: {"pending_eval": 500, "evaluating": 60, "evaluated": 3},
    )
    real_readiness = ctx.runtime_controller._pool_readiness_counts  # noqa: SLF001

    def readiness_with_evaluated() -> dict[str, int]:
        counts = dict(real_readiness())
        counts["evaluated_pending"] = 3
        return counts

    monkeypatch.setattr(
        ctx.runtime_controller,
        "_pool_readiness_counts",
        readiness_with_evaluated,
    )

    snapshot = ctx.runtime_controller.candidate_eval_coordinator._snapshot()

    assert snapshot.available == 0
    assert snapshot.pending_eval == 500
    assert snapshot.evaluating == 60
    assert snapshot.evaluated_pending_admission == 3
    assert snapshot.evaluated_waiting_total == 3
    assert snapshot.admitted_pending_copy == 4
    assert snapshot.admitted_pending_available == 3
    assert ctx.recommendation_engine.pool_available_target_count == 10
    assert ctx.llm_concurrency_gate.inventory_priority_state is InventoryPriorityState.EMPTY


def test_api_candidate_supply_callback_uses_quota_aware_supply_wave(monkeypatch, tmp_path) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    config = Config(data_dir=str(tmp_path / "data"))
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    ctx = build_runtime_context(config)
    calls: list[str] = []

    async def supply_candidates_once(*, reason: str) -> dict[str, object]:
        calls.append(reason)
        return {"supply_productive": True, "supply_progress_count": 2}

    monkeypatch.setattr(
        ctx.runtime_controller,
        "supply_candidates_once",
        supply_candidates_once,
    )

    callback = ctx.runtime_controller.candidate_eval_coordinator.supply_callback
    result = asyncio.run(callback("candidate_supply"))

    assert calls == ["candidate_supply"]
    assert result == {"supply_productive": True, "supply_progress_count": 2}


@pytest.mark.asyncio
async def test_copy_ready_target_clamps_and_rebinds_provider_on_rebuild(tmp_path) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    initial = Config(data_dir=str(tmp_path / "data"))
    initial.llm.default_provider = "ollama"
    initial.llm.ollama.model = "llama3"
    initial.scheduler.pool_target_count = 10
    initial.scheduler.copy_ready_target_count = 25
    initial.soul.preference_prompt_view = "compact-v1"
    initial.soul.awareness_prompt_view = "legacy"
    initial.soul.insight_prompt_view = "compact-v1"
    ctx = build_runtime_context(initial)
    for index in range(4):
        ctx.database.cache_content(
            f"BVCOPYREBUILD{index}",
            title=f"pending copy {index}",
            source="search",
            relevance_score=0.9,
            style_key="tutorial",
            topic_group=f"copy-rebuild-{index}",
        )

    old_engine = ctx.recommendation_engine
    old_soul_engine = ctx.soul_engine
    old_coordinator = ctx.runtime_controller.expression_copy_coordinator
    old_provider = old_coordinator.pending_count_provider
    assert old_soul_engine._preference_prompt_view == "compact-v1"
    assert old_soul_engine._awareness_prompt_view == "legacy"
    assert old_soul_engine._insight_prompt_view == "compact-v1"
    assert old_engine.copy_ready_target_count == 10
    assert old_engine.pool_available_target_count == 10
    assert getattr(old_provider, "__self__", None) is old_engine
    assert old_provider() == 4

    reloaded = Config(data_dir=str(tmp_path / "data"))
    reloaded.llm.default_provider = "ollama"
    reloaded.llm.ollama.model = "llama3"
    reloaded.scheduler.pool_target_count = 2
    reloaded.scheduler.copy_ready_target_count = 8
    reloaded.soul.preference_prompt_view = "legacy"
    reloaded.soul.awareness_prompt_view = "compact-v1"
    reloaded.soul.insight_prompt_view = "legacy"
    await ctx.rebuild_from_config(reloaded)

    new_engine = ctx.recommendation_engine
    new_soul_engine = ctx.soul_engine
    new_coordinator = ctx.runtime_controller.expression_copy_coordinator
    new_provider = new_coordinator.pending_count_provider
    assert new_engine is not old_engine
    assert new_soul_engine is not old_soul_engine
    assert new_soul_engine._preference_prompt_view == "legacy"
    assert new_soul_engine._awareness_prompt_view == "compact-v1"
    assert new_soul_engine._insight_prompt_view == "legacy"
    assert new_coordinator is not old_coordinator
    assert new_engine.copy_ready_target_count == 2
    assert new_engine.pool_available_target_count == 2
    assert getattr(new_provider, "__self__", None) is new_engine
    assert new_provider() == 2


@pytest.mark.asyncio
async def test_old_engine_commit_callback_uses_current_controller_after_two_reloads(
    tmp_path,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import InventoryPriorityState

    config = Config(data_dir=str(tmp_path / "data"))
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    config.scheduler.pool_target_count = 30
    ctx = build_runtime_context(config)
    for index in range(15):
        ctx.database.cache_content(
            f"BVHOT{index:02d}",
            title=f"hot {index}",
            source="search",
            relevance_score=0.9,
            pool_expression=f"expression {index}",
            pool_topic_label=f"topic {index}",
            style_key="tutorial",
            topic_group=f"group {index}",
        )

    first = Config(data_dir=str(tmp_path / "data"))
    first.llm.default_provider = "ollama"
    first.llm.ollama.model = "llama3"
    first.scheduler.pool_target_count = 30
    await ctx.rebuild_from_config(first)
    old_engine = ctx.recommendation_engine
    old_callback = old_engine._pool_inventory_commit_callback
    assert old_callback is ctx.pool_inventory_commit_callback
    assert ctx.llm_concurrency_gate.inventory_priority_state is InventoryPriorityState.REFILL

    second = Config(data_dir=str(tmp_path / "data"))
    second.llm.default_provider = "ollama"
    second.llm.ollama.model = "llama3"
    second.scheduler.pool_target_count = 10
    await ctx.rebuild_from_config(second)
    assert ctx.recommendation_engine._pool_inventory_commit_callback is old_callback
    assert ctx.llm_concurrency_gate.inventory_priority_state is InventoryPriorityState.HEALTHY

    result = old_callback()
    if asyncio.iscoroutine(result):
        await result
    assert ctx.llm_concurrency_gate.inventory_priority_state is InventoryPriorityState.HEALTHY


@pytest.mark.asyncio
async def test_api_pool_commit_publication_survives_multiple_reloads(monkeypatch, tmp_path) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    class EventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> bool:
            self.events.append(dict(event))
            return True

    config = Config(data_dir=str(tmp_path / "data"))
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    config.scheduler.pool_target_count = 30
    hub = EventHub()
    built = build_runtime_context(config, event_hub=hub)
    for index in range(4):
        built.database.cache_content(
            f"BVEVENT{index}",
            title=f"event {index}",
            source="search",
            relevance_score=0.9,
            pool_expression=f"expression {index}",
            pool_topic_label=f"topic {index}",
            style_key="tutorial",
            topic_group=f"event-group {index}",
        )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    app = create_app(
        memory_manager=built.memory_manager,
        database=built.database,
        soul_engine=built.soul_engine,
        dialogue=built.dialogue,
        runtime_controller=built.runtime_controller,
        recommendation_engine=built.recommendation_engine,
        runtime_event_hub=hub,
        account_sync_service=built.account_sync_service,
        auto_update_service=built.auto_update_service,
    )
    ctx = app.state.runtime_context

    first = Config(data_dir=str(tmp_path / "data"))
    first.llm.default_provider = "ollama"
    first.llm.ollama.model = "llama3"
    first.scheduler.pool_target_count = 30
    await ctx.rebuild_from_config(first)
    first_reloaded_engine = ctx.recommendation_engine
    first_callback = first_reloaded_engine._pool_inventory_commit_callback

    second = Config(data_dir=str(tmp_path / "data"))
    second.llm.default_provider = "ollama"
    second.llm.ollama.model = "llama3"
    second.scheduler.pool_target_count = 10
    await ctx.rebuild_from_config(second)
    current_callback = ctx.recommendation_engine._pool_inventory_commit_callback
    assert current_callback is first_callback

    await current_callback()
    assert hub.events[-1]["type"] == "refresh.pool_updated"
    assert hub.events[-1]["pool_available_count"] == 4

    await first_callback()
    assert hub.events[-1]["pool_available_count"] == 4
    assert hub.events[-1]["pool_target_count"] == 10


def test_injected_runtime_adopts_real_dialogue_gate_and_injects_gate_less_service(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    dialogue_gate = LLMConcurrencyGate(2)
    soul = _injected_soul_engine(dialogue_gate)
    dialogue_service = LLMService(
        registry=soul._llm,  # type: ignore[attr-defined]
        memory=soul._memory,  # type: ignore[attr-defined]
        concurrency_gate=dialogue_gate,
    )
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul,  # type: ignore[arg-type]
        llm_service=dialogue_service,
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    controller = SimpleNamespace(llm_concurrency_gate=dialogue_gate, event_hub=None)

    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=soul,
        dialogue=dialogue,
        runtime_controller=controller,
    )

    assert app.state.runtime_context.llm_concurrency_gate is dialogue_gate
    assert dialogue._llm_service is dialogue_service
    assert dialogue._llm_service.concurrency_gate is dialogue_gate
    assert dialogue._build_service().concurrency_gate is dialogue_gate

    controller_gate = LLMConcurrencyGate(3)
    gate_less_soul = SimpleNamespace(
        _llm_concurrency_gate=None,
        _llm_service=SimpleNamespace(concurrency_gate=None),
    )
    gate_less_dialogue_service = SimpleNamespace(concurrency_gate=None)
    gate_less_dialogue = SocraticDialogue(
        llm=None,
        soul_engine=gate_less_soul,
        llm_service=gate_less_dialogue_service,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    gate_less_app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=gate_less_soul,
        dialogue=gate_less_dialogue,
        runtime_controller=SimpleNamespace(llm_concurrency_gate=controller_gate, event_hub=None),
    )
    assert gate_less_app.state.runtime_context.llm_concurrency_gate is controller_gate
    assert gate_less_dialogue._build_service().concurrency_gate is controller_gate


def test_injected_runtime_adopts_dialogue_only_gate_and_rejects_dialogue_conflict(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate
    from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    dialogue_gate = LLMConcurrencyGate(2)
    soul = SimpleNamespace(
        _llm_concurrency_gate=None,
        _llm_service=SimpleNamespace(concurrency_gate=None),
    )
    dialogue_service = SimpleNamespace(concurrency_gate=dialogue_gate)
    dialogue = SocraticDialogue(
        llm=None,
        soul_engine=soul,
        llm_service=dialogue_service,  # type: ignore[arg-type]
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )
    controller = SimpleNamespace(llm_concurrency_gate=None, event_hub=None)

    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=soul,
        dialogue=dialogue,
        runtime_controller=controller,
    )
    assert app.state.runtime_context.llm_concurrency_gate is dialogue_gate
    assert soul._llm_service.concurrency_gate is dialogue_gate
    assert controller.llm_concurrency_gate is dialogue_gate

    with pytest.raises(ValueError, match="different LLM concurrency gates"):
        create_app(
            memory_manager=SimpleNamespace(),
            database=SimpleNamespace(),
            soul_engine=_injected_soul_engine(LLMConcurrencyGate(2)),
            dialogue=SocraticDialogue(
                llm=None,
                soul_engine=SimpleNamespace(),  # type: ignore[arg-type]
                llm_service=SimpleNamespace(  # type: ignore[arg-type]
                    concurrency_gate=LLMConcurrencyGate(2)
                ),
                learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
            ),
            runtime_controller=SimpleNamespace(event_hub=None),
        )


def test_injected_compatibility_dialogue_double_without_gate_is_supported(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.config import Config

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    dialogue = SimpleNamespace()

    app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        dialogue=dialogue,
        runtime_controller=SimpleNamespace(event_hub=None),
    )

    assert app.state.runtime_context.dialogue is dialogue


def test_injected_controller_nested_discovery_gate_is_validated_and_injected(
    monkeypatch, tmp_path
) -> None:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate

    config = Config(data_dir=str(tmp_path))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    adopted_gate = LLMConcurrencyGate(2)

    gate_less_service = SimpleNamespace(concurrency_gate=None)
    gate_less_controller = SimpleNamespace(
        event_hub=None,
        llm_concurrency_gate=adopted_gate,
        discovery_engine=SimpleNamespace(_llm_service=gate_less_service),
    )
    gate_less_app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        runtime_controller=gate_less_controller,
    )
    assert gate_less_app.state.runtime_context.llm_concurrency_gate is adopted_gate
    assert gate_less_service.concurrency_gate is adopted_gate

    common_service = SimpleNamespace(concurrency_gate=adopted_gate)
    common_controller = SimpleNamespace(
        event_hub=None,
        llm_concurrency_gate=adopted_gate,
        discovery_engine=SimpleNamespace(_llm_service=common_service),
    )
    common_app = create_app(
        memory_manager=SimpleNamespace(),
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        runtime_controller=common_controller,
    )
    assert common_app.state.runtime_context.llm_concurrency_gate is adopted_gate
    assert common_service.concurrency_gate is adopted_gate

    conflicting_gate = LLMConcurrencyGate(2)
    conflicting_service = SimpleNamespace(concurrency_gate=conflicting_gate)
    with pytest.raises(ValueError, match="different LLM concurrency gates"):
        create_app(
            memory_manager=SimpleNamespace(),
            database=SimpleNamespace(),
            soul_engine=SimpleNamespace(),
            runtime_controller=SimpleNamespace(
                event_hub=None,
                llm_concurrency_gate=adopted_gate,
                discovery_engine=SimpleNamespace(_llm_service=conflicting_service),
            ),
        )
    assert conflicting_service.concurrency_gate is conflicting_gate


@pytest.mark.parametrize(
    ("qualified_name", "runtime_attribute"),
    [
        ("openbiliclaw.soul.engine.SoulEngine", "_llm_service"),
        ("openbiliclaw.soul.dialogue.SocraticDialogue", "_llm_service"),
        ("openbiliclaw.recommendation.engine.RecommendationEngine", "_llm"),
        ("openbiliclaw.discovery.engine.ContentDiscoveryEngine", "_llm_service"),
        ("openbiliclaw.runtime.account_sync.AccountSyncService", "soul_engine"),
    ],
)
def test_injected_llm_owner_attribute_audit(qualified_name: str, runtime_attribute: str) -> None:
    module_name, class_name = qualified_name.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    owner_type = getattr(module, class_name)
    source = inspect.getsource(owner_type)

    assert f"self.{runtime_attribute}" in source or runtime_attribute in getattr(
        owner_type, "__annotations__", {}
    ), f"{qualified_name} no longer stores its injected LLM owner at {runtime_attribute}"

    app_source = inspect.getsource(create_app)
    if class_name == "ContentDiscoveryEngine":
        assert f'getattr(controller_discovery, "{runtime_attribute}", None)' in app_source


def _store_xhs_login_state(db: object, *, logged_in: bool, when_iso: str) -> None:
    db.conn.executemany(
        "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
        [
            ("xhs_login_state", "1" if logged_in else "0"),
            ("xhs_login_state_at", when_iso),
        ],
    )
    db.conn.commit()


def _store_zhihu_login_state(db: object, *, logged_in: bool, when_iso: str) -> None:
    db.conn.executemany(
        "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
        [
            ("zhihu_login_state", "1" if logged_in else "0"),
            ("zhihu_login_state_at", when_iso),
        ],
    )
    db.conn.commit()


class _ReadySoulEngine:
    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep create_app() route tests independent from the developer machine.

    Several API tests intentionally exercise routes with partial fake runtime
    components. create_app() still loads runtime config up front, so without
    this fixture CI sees the repo's empty template while local runs may see a
    private config.toml with real credentials.
    """

    from openbiliclaw.api import app as api_app
    from openbiliclaw.config import Config, save_config
    from openbiliclaw.runtime import embedding_progress

    embedding_progress.reset()

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")
    yield

    deadline = time.monotonic() + 0.5
    while any(not task.done() for task in api_app._fire_and_forget_tasks):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    for task in tuple(api_app._fire_and_forget_tasks):
        if not task.done():
            task.cancel()
    embedding_progress.reset()


class TestBackendAPI:
    """Route-level tests for the plugin backend API."""

    def test_project_stats_returns_local_success_when_github_is_unavailable(self) -> None:
        from fastapi.testclient import TestClient

        class FakeProjectStatsService:
            async def get_snapshot(self) -> dict[str, object]:
                return {"github_stars": None, "stale": True, "source": "unavailable"}

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            project_stats_service=FakeProjectStatsService(),
        )

        response = TestClient(app).get("/api/project-stats")

        assert response.status_code == 200
        assert response.json() == {"stale": True, "source": "unavailable"}

    @pytest.mark.parametrize(
        ("endpoint", "id_field"),
        (
            ("/api/events", "event_id"),
            ("/api/feedback", "request_id"),
            ("/api/recommendation-click", "request_id"),
        ),
        ids=("events", "feedback", "recommendation-click"),
    )
    @pytest.mark.parametrize(
        ("case", "invalid_id"),
        (
            ("omitted", None),
            ("empty", ""),
            ("whitespace", " \t "),
            ("non-string", 123),
            ("over-400", "x" * 401),
        ),
        ids=("omitted", "empty", "whitespace", "non-string", "over-400"),
    )
    def test_event_ingress_requires_bounded_nonblank_id_before_any_write(
        self,
        endpoint: str,
        id_field: str,
        case: str,
        invalid_id: object | None,
    ) -> None:
        from fastapi.testclient import TestClient

        class WriteSpyMemory:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class ProjectionSpyDatabase:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object]:
                self.calls.append(f"get:{recommendation_id}")
                return {
                    "id": recommendation_id,
                    "bvid": "BV1VALIDATION",
                    "title": "validation",
                }

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                self.calls.append(f"update:{recommendation_id}:{feedback_type}:{feedback_note}")

        if endpoint == "/api/events":
            event = {
                "type": "click",
                "url": "https://www.bilibili.com/video/BV1VALIDATION",
                "title": "validation",
                "timestamp": 1710000000000,
            }
            if invalid_id is not None:
                event[id_field] = invalid_id
            payload: dict[str, object] = {"events": [event]}
        elif endpoint == "/api/feedback":
            payload = {
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
            }
            if invalid_id is not None:
                payload[id_field] = invalid_id
        else:
            payload = {"bvid": "BV1VALIDATION", "title": "validation"}
            if invalid_id is not None:
                payload[id_field] = invalid_id

        memory = WriteSpyMemory()
        database = ProjectionSpyDatabase()
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=database,
                soul_engine=object(),
            )
        )

        response = client.post(endpoint, json=payload)

        assert response.status_code == 422, (case, response.text)
        assert memory.events == []
        assert database.calls == []

    @pytest.mark.parametrize(
        ("model_name", "payload", "id_field"),
        (
            (
                "BehaviorEventIn",
                {
                    "type": "click",
                    "event_id": "  event-trimmed  ",
                    "timestamp": 1710000000000,
                },
                "event_id",
            ),
            (
                "FeedbackIn",
                {
                    "recommendation_id": 1,
                    "feedback_type": "like",
                    "request_id": "  feedback-trimmed  ",
                },
                "request_id",
            ),
            (
                "RecommendationClickIn",
                {"bvid": "BV1", "request_id": "  click-trimmed  "},
                "request_id",
            ),
        ),
        ids=("events", "feedback", "recommendation-click"),
    )
    def test_event_ingress_id_is_trimmed(
        self,
        model_name: str,
        payload: dict[str, object],
        id_field: str,
    ) -> None:
        from openbiliclaw.api import models

        model_type = getattr(models, model_name)
        parsed = model_type.model_validate(payload)

        assert getattr(parsed, id_field) == str(payload[id_field]).strip()

    def test_recommendation_and_delight_models_default_publication_fields(self) -> None:
        from openbiliclaw.api.models import PendingDelightOut, RecommendationOut

        recommendation = RecommendationOut(id=1, bvid="BV1").model_dump()
        delight = PendingDelightOut(bvid="BV1").model_dump()

        assert recommendation["published_at"] == ""
        assert recommendation["published_label"] == ""
        assert delight["published_at"] == ""
        assert delight["published_label"] == ""

    def test_feedback_api_persists_strong_card_feedback_signal_strength(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager

        class FakeDatabase:
            def __init__(self) -> None:
                self.updated: list[tuple[int, str, str]] = []

            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
                return {
                    "id": recommendation_id,
                    "bvid": "BV1REC",
                    "title": "讲透城市与建筑",
                    "topic_label": "建筑",
                    "up_name": "建筑师",
                }

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                self.updated.append((recommendation_id, feedback_type, feedback_note))

        class FakeSoulEngine:
            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str,
            ) -> None:
                pass

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                return {"triggered": False}

        memory = MemoryManager(tmp_path)
        memory.initialize()
        database = FakeDatabase()
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        comment = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "comment",
                "note": "方向对，但我想看更深一点。",
                "request_id": "feedback-card-comment",
            },
        )
        dismiss = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 8,
                "feedback_type": "dismiss",
                "note": "",
                "request_id": "feedback-card-dismiss",
            },
        )

        assert comment.status_code == 200
        assert dismiss.status_code == 200
        assert database.updated == [
            (7, "comment", "方向对，但我想看更深一点。"),
            (8, "dismiss", ""),
        ]

        events = memory.query_events(event_types=["feedback"], limit=10)
        metadata_by_type = {
            json.loads(str(event["metadata"]))["feedback_type"]: json.loads(str(event["metadata"]))
            for event in events
        }
        assert metadata_by_type["comment"]["signal_strength"] == 0.8
        assert metadata_by_type["dismiss"]["signal_strength"] == 0.5

    def test_feedback_api_schedules_post_feedback_batch_without_inline_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.memory.manager import MemoryManager

        schedules = 0

        class FakeFeedbackBatchScheduler:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def schedule(self) -> None:
                nonlocal schedules
                schedules += 1

            async def close(self) -> None:
                pass

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
                return {
                    "id": recommendation_id,
                    "bvid": "BV1REC",
                    "title": "讲透城市与建筑",
                    "topic_label": "建筑",
                    "up_name": "建筑师",
                }

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                pass

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.batch_calls = 0

            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str,
            ) -> None:
                pass

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                self.batch_calls += 1
                return {"triggered": False}

        monkeypatch.setattr(api_app, "FeedbackBatchScheduler", FakeFeedbackBatchScheduler)
        memory = MemoryManager(tmp_path)
        memory.initialize()
        soul = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul,
        )
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
                "request_id": "feedback-scheduler-like",
            },
        )

        assert response.status_code == 200
        assert schedules == 1
        assert soul.batch_calls == 0

    def test_feedback_request_id_replay_is_idempotent_on_production_database(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / "feedback-idempotency.db")
        memory = MemoryManager(tmp_path / "data", database=database)
        memory.initialize()
        recommendation_id = database.insert_recommendation(
            "BV1FEEDBACK",
            confidence=0.9,
            topic="建筑",
        )
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=database,
                soul_engine=object(),
            )
        )
        payload = {
            "recommendation_id": recommendation_id,
            "feedback_type": "comment",
            "note": "方向对，但希望更深入。",
            "request_id": "feedback-production-replay",
        }

        first = client.post("/api/feedback", json=payload)
        replay = client.post("/api/feedback", json=payload)

        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["duplicate"] is False
        assert replay.json()["duplicate"] is True
        assert replay.json()["event_id"] == first.json()["event_id"]
        events = memory.query_events(event_types=["feedback"], limit=10)
        assert len(events) == 1
        row = database.get_recommendation_by_id(recommendation_id)
        assert row is not None
        assert row["feedback_type"] == "comment"
        assert row["feedback_note"] == payload["note"]

    @pytest.mark.parametrize(
        "conflict_kind",
        ("recommendation", "feedback_type", "note"),
        ids=("changed-recommendation", "changed-type", "changed-note"),
    )
    def test_feedback_request_id_conflict_is_rejected_on_production_database(
        self,
        tmp_path: Path,
        conflict_kind: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / f"feedback-conflict-{conflict_kind}.db")
        memory = MemoryManager(tmp_path / "data", database=database)
        memory.initialize()
        first_recommendation_id = database.insert_recommendation(
            "BV1FIRST",
            confidence=0.9,
        )
        other_recommendation_id = database.insert_recommendation(
            "BV1OTHER",
            confidence=0.8,
        )
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=database,
                soul_engine=object(),
            )
        )
        original = {
            "recommendation_id": first_recommendation_id,
            "feedback_type": "like",
            "note": "首写备注",
            "request_id": "feedback-production-conflict",
        }
        changed = dict(original)
        if conflict_kind == "recommendation":
            changed["recommendation_id"] = other_recommendation_id
        elif conflict_kind == "feedback_type":
            changed["feedback_type"] = "dislike"
        else:
            changed["note"] = "变化后的备注"

        first = client.post("/api/feedback", json=original)
        conflict = client.post("/api/feedback", json=changed)

        assert first.status_code == 200
        assert conflict.status_code == 409
        assert len(memory.query_events(event_types=["feedback"], limit=10)) == 1
        first_row = database.get_recommendation_by_id(first_recommendation_id)
        other_row = database.get_recommendation_by_id(other_recommendation_id)
        assert first_row is not None and first_row["feedback_type"] == "like"
        assert first_row["feedback_note"] == "首写备注"
        assert other_row is not None and other_row["feedback_type"] is None

    def test_feedback_request_id_retry_repairs_projection_after_commit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / "feedback-projection-repair.db")
        memory = MemoryManager(tmp_path / "data", database=database)
        memory.initialize()
        recommendation_id = database.insert_recommendation(
            "BV1REPAIR",
            confidence=0.9,
        )
        real_update = database.update_recommendation_feedback
        attempts = 0

        def fail_first_projection(
            current_recommendation_id: int,
            *,
            feedback_type: str,
            feedback_note: str = "",
        ) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("crash after event commit")
            real_update(
                current_recommendation_id,
                feedback_type=feedback_type,
                feedback_note=feedback_note,
            )

        monkeypatch.setattr(
            database,
            "update_recommendation_feedback",
            fail_first_projection,
        )
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=database,
                soul_engine=object(),
            ),
            raise_server_exceptions=False,
        )
        payload = {
            "recommendation_id": recommendation_id,
            "feedback_type": "comment",
            "note": "首写必须用于修复",
            "request_id": "feedback-production-repair",
        }

        failed = client.post("/api/feedback", json=payload)
        row_after_failure = database.get_recommendation_by_id(recommendation_id)
        repaired = client.post("/api/feedback", json=payload)

        assert failed.status_code == 500
        assert row_after_failure is not None and row_after_failure["feedback_type"] is None
        assert repaired.status_code == 200
        assert repaired.json()["duplicate"] is True
        assert attempts == 2
        assert len(memory.query_events(event_types=["feedback"], limit=10)) == 1
        repaired_row = database.get_recommendation_by_id(recommendation_id)
        assert repaired_row is not None
        assert repaired_row["feedback_type"] == "comment"
        assert repaired_row["feedback_note"] == payload["note"]

    def test_desktop_web_index_cache_busts_static_assets(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/web")

        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-store"
        assert 'href="/web/assets/css/app.css?v=' in response.text
        assert 'href="/web/assets/css/classic.css?v=' in response.text
        assert 'src="/web/assets/js/app.js?v=' in response.text

    def test_mobile_web_index_exposes_home_screen_metadata(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/m/")

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/html")
        assert '<link rel="manifest" href="manifest.json">' in response.text
        assert '<meta name="mobile-web-app-capable" content="yes">' in response.text
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in response.text
        assert '<meta name="apple-mobile-web-app-title" content="BiliClaw">' in response.text
        assert (
            '<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png?v=4">'
        ) in response.text

    def test_mobile_web_manifest_is_installable_and_assets_resolve(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/m/manifest.json")

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("application/json")
        manifest = response.json()
        assert manifest["id"] == "/m/"
        assert manifest["scope"] == "/m/"
        assert manifest["start_url"] == "/m/"
        assert manifest["display"] == "standalone"
        assert manifest["name"] == "OpenBiliClaw"
        assert manifest["short_name"] == "BiliClaw"
        assert manifest.get("prefer_related_applications") is not True

        icons = manifest["icons"]
        sizes = {icon["sizes"] for icon in icons}
        assert {"192x192", "512x512"}.issubset(sizes)
        purposes = {(icon["sizes"], icon.get("purpose")) for icon in icons}
        assert ("192x192", "any") in purposes
        assert ("512x512", "any") in purposes
        assert ("192x192", "maskable") in purposes
        assert ("512x512", "maskable") in purposes

        for icon in icons:
            assert icon["type"] == "image/png"
            icon_response = client.get(f"/m/{icon['src']}")
            assert icon_response.status_code == 200
            assert icon_response.headers.get("content-type", "").startswith("image/png")

        favicon_response = client.get("/favicon.ico")
        assert favicon_response.status_code == 200
        assert favicon_response.headers.get("content-type", "").startswith("image/png")

    @pytest.mark.asyncio
    async def test_runtime_context_presence_survives_rebuild(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        ctx = RuntimeContext()
        original_presence = ctx.presence

        def _fake_rebuild_components(self: RuntimeContext, new_config: Config) -> None:
            self.config = new_config

        monkeypatch.setattr(RuntimeContext, "_rebuild_components", _fake_rebuild_components)

        await ctx.rebuild_from_config(Config())

        assert ctx.presence is original_presence

    @pytest.mark.asyncio
    async def test_runtime_context_skips_startup_one_shots_when_llm_work_blocked(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class FakeSpeculator:
            def __init__(self) -> None:
                self.force_tick_calls = 0

            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                self.force_tick_calls += 1

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()
                self.profile_calls = 0

            async def get_profile(self) -> dict[str, object]:
                self.profile_calls += 1
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.prewarm_calls = 0

            async def prewarm_pool_mmr_embeddings(self) -> int:
                self.prewarm_calls += 1
                return 1

        cfg = Config()
        cfg.scheduler.enabled = False
        soul = FakeSoulEngine()
        rec = FakeRecommendationEngine()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=soul,
            recommendation_engine=rec,
        )
        app = SimpleNamespace(state=SimpleNamespace())

        await ctx.restart_background_tasks(app)

        assert soul._speculator.force_tick_calls == 0
        assert rec.prewarm_calls == 0

    @pytest.mark.asyncio
    async def test_runtime_context_can_suppress_post_reload_llm_one_shots(self) -> None:
        """Setup config saves must not kick profile/probe/pool LLM work."""
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class FakeSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                raise AssertionError("setup config save must not generate probes")

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()
                self.profile_calls = 0

            async def get_profile(self) -> dict[str, object]:
                self.profile_calls += 1
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            async def precompute_pool_copy(self, *_args: object, **_kwargs: object) -> None:
                raise AssertionError("setup config save must not precompute pool copy")

            async def prewarm_pool_mmr_embeddings(self) -> int:
                raise AssertionError("setup config save must not prewarm embeddings")

        soul = FakeSoulEngine()
        ctx = RuntimeContext(
            config=Config(),
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=soul,
            recommendation_engine=FakeRecommendationEngine(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        await ctx.restart_background_tasks(app, run_post_reload_llm_work=False)

        assert soul.profile_calls == 0
        assert ctx.task_registry.stats().get("post_reload_speculate") is None
        assert ctx.task_registry.stats().get("post_reload_precompute_pool_copy") is None
        assert ctx.task_registry.stats().get("prewarm_pool_mmr_embeddings") is None

    @pytest.mark.asyncio
    async def test_runtime_context_replaces_one_independent_source_scheduler_owner(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        started: list[str] = []
        release = asyncio.Event()
        new_started = asyncio.Event()

        class Controller:
            def __init__(self, label: str) -> None:
                self.label = label
                self.source_incremental_sync = object()

            async def run_forever(self) -> None:
                started.append(self.label)
                if self.label == "new":
                    new_started.set()
                await release.wait()

        old = Controller("old")
        new = Controller("new")
        old_task = asyncio.create_task(old.run_forever())
        ctx = RuntimeContext(
            config=Config(),
            runtime_controller=old,
            account_sync_service=object(),
            auto_update_service=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace(refresh_task=old_task))

        try:
            await asyncio.sleep(0)
            ctx.runtime_controller = new
            await ctx.restart_background_tasks(app)
            for _ in range(5):
                await asyncio.sleep(0)
                if new_started.is_set():
                    break

            assert old_task.cancelled()
            assert started == ["old", "new"]
            assert app.state.refresh_task is not old_task
            assert ctx.task_registry.stats().get("refresh_loop") == 1
        finally:
            release.set()
            await ctx.task_registry.cancel_all()
            if not old_task.done():
                old_task.cancel()
                with suppress(asyncio.CancelledError):
                    await old_task

    @pytest.mark.asyncio
    async def test_guided_init_setup_reload_keeps_whole_controller_suspended(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        started = False

        class Controller:
            source_incremental_sync = object()

            async def run_forever(self) -> None:
                nonlocal started
                started = True

        ctx = RuntimeContext(
            config=Config(),
            runtime_controller=Controller(),
            account_sync_service=object(),
            auto_update_service=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        await ctx.restart_background_tasks(app, run_post_reload_llm_work=False)
        await asyncio.sleep(0)

        assert app.state.refresh_task is None
        assert started is False
        assert ctx.task_registry.stats().get("refresh_loop") is None

    @pytest.mark.asyncio
    async def test_restart_background_tasks_bounds_cancel_ignoring_coroutine(
        self, monkeypatch
    ) -> None:
        """Hot reload must return even when an old provider loop swallows cancel."""
        from contextlib import suppress
        from types import SimpleNamespace

        import openbiliclaw.api.runtime_context as runtime_context_module
        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        stop = asyncio.Event()

        async def _stubborn() -> None:
            while not stop.is_set():
                try:
                    await stop.wait()
                except asyncio.CancelledError:
                    # Simulate a third-party coroutine that consumes cancel.
                    continue

        stale = asyncio.create_task(_stubborn())
        app = SimpleNamespace(
            state=SimpleNamespace(
                refresh_task=stale,
                account_sync_task=None,
                auto_update_task=None,
            )
        )
        ctx = RuntimeContext(
            config=Config(),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
        )
        monkeypatch.setattr(
            runtime_context_module,
            "_BACKGROUND_TASK_CANCEL_TIMEOUT_SECONDS",
            0.01,
        )
        await asyncio.sleep(0)  # let the coroutine enter its cancel-swallowing loop
        try:
            await asyncio.wait_for(
                ctx.restart_background_tasks(app, run_post_reload_llm_work=False),
                timeout=0.2,
            )
            # The live old task remains owned; no duplicate replacement is
            # started merely to make the reload look successful.
            assert app.state.refresh_task is stale
            assert not stale.done()
        finally:
            stop.set()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(stale, timeout=0.2)

    @pytest.mark.asyncio
    async def test_restart_background_tasks_accepts_done_task_from_prior_loop(self) -> None:
        """Embedded/TestClient hosts may preserve app.state across loop lifetimes."""
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class PriorLoopTask:
            def get_loop(self) -> object:
                return object()

            def done(self) -> bool:
                return True

            def result(self) -> None:
                return None

            def cancel(self) -> None:
                raise AssertionError("a completed foreign-loop task must not be cancelled")

        app = SimpleNamespace(
            state=SimpleNamespace(
                refresh_task=PriorLoopTask(),
                account_sync_task=None,
                auto_update_task=None,
            )
        )
        ctx = RuntimeContext(
            config=Config(),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
        )

        await ctx.restart_background_tasks(app, run_post_reload_llm_work=False)

        assert app.state.refresh_task is None

    @pytest.mark.asyncio
    async def test_restart_tasks_detaches_speculator_tick(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class HangingSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                await asyncio.sleep(60)

        class FakeSoulEngine:
            _speculator = HangingSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        try:
            await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=0.5)
            assert ctx.task_registry.stats().get("post_reload_speculate") == 1
        finally:
            await ctx.task_registry.cancel_all()

    @pytest.mark.asyncio
    async def test_restart_tasks_detaches_avoidance_speculator_tick(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class HangingAvoidanceSpeculator:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.calls: list[tuple[object, object | None]] = []

            async def force_tick(
                self,
                profile: object,
                *,
                feedback_history: object | None = None,
            ) -> None:
                self.calls.append((profile, feedback_history))
                self.started.set()
                await asyncio.sleep(60)

        class FakeSoulEngine:
            def __init__(self, avoidance_speculator: HangingAvoidanceSpeculator) -> None:
                self._avoidance_speculator = avoidance_speculator

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        feedback_history = [{"domain": "浅层热点复读", "response": "reject"}]
        avoidance_speculator = HangingAvoidanceSpeculator()
        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(
                load_discovery_runtime_state=lambda: {
                    "avoidance_probe_feedback_history": feedback_history,
                }
            ),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(avoidance_speculator),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        try:
            await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=0.5)
            assert ctx.task_registry.stats().get("post_reload_avoidance_speculate") == 1
            await asyncio.wait_for(avoidance_speculator.started.wait(), timeout=0.5)
            assert avoidance_speculator.calls == [
                ({"profile": "ok"}, feedback_history),
            ]
        finally:
            await ctx.task_registry.cancel_all()

    @pytest.mark.asyncio
    async def test_restart_tasks_swallows_detached_speculator_failure(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class BrokenSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("boom")

        class FakeSoulEngine:
            _speculator = BrokenSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())
        captured_tasks: list[asyncio.Task[object]] = []
        original_track = ctx.task_registry.track

        def _track(name: str, coro):
            task = original_track(name, coro)
            if name == "post_reload_speculate":
                captured_tasks.append(task)
            return task

        ctx.task_registry.track = _track  # type: ignore[method-assign]

        await ctx.restart_background_tasks(app)
        assert len(captured_tasks) == 1
        await asyncio.wait_for(captured_tasks[0], timeout=0.5)
        assert captured_tasks[0].exception() is None

    @pytest.mark.asyncio
    async def test_restart_tasks_rekicks_pool_precompute_drain(self) -> None:
        """Lever 2a: hot-reload re-kicks the classify→copy→delight drain.

        ``rebuild_from_config``'s ``cancel_all`` kills any in-flight pool
        precompute; ``restart_background_tasks`` must re-kick it on the
        freshly-built engine so a user saving config mid-cold-start doesn't
        strand pool-fill until the next refresh tick.
        """
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.calls: list[object] = []
                self.started = asyncio.Event()

            async def precompute_pool_copy(self, *, profile: object) -> int:
                self.calls.append(profile)
                self.started.set()
                return 0

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        engine = FakeRecommendationEngine()
        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=engine,
        )
        app = SimpleNamespace(state=SimpleNamespace())
        captured: dict[str, asyncio.Task[object]] = {}
        original_track = ctx.task_registry.track

        def _track(name: str, coro: object) -> object:
            task = original_track(name, coro)
            captured[name] = task
            return task

        ctx.task_registry.track = _track  # type: ignore[method-assign]

        try:
            await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=0.5)
            assert "post_reload_precompute_pool_copy" in captured
            await asyncio.wait_for(engine.started.wait(), timeout=0.5)
            await asyncio.wait_for(captured["post_reload_precompute_pool_copy"], timeout=0.5)
            assert engine.calls == [{"profile": "ok"}]
        finally:
            await ctx.task_registry.cancel_all()

    @pytest.mark.asyncio
    async def test_e2e_hot_reload_resumes_real_pool_fill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """E2E (lever 2a): a config reload makes the *real* engine fill copy for
        a pending pool candidate against a *real* DB — pool-fill actually
        resumes (the seeded row becomes serveable), not just 'a task was
        scheduled'. Only the LLM (copy text) is faked.
        """
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config
        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.recommendation.engine import RecommendationEngine
        from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile
        from openbiliclaw.storage.database import Database

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        class _CopyLLM:
            def __init__(self) -> None:
                self.callers: list[str] = []

            async def complete_structured_task(
                self, *, caller: str = "", **_kw: object
            ) -> LLMResponse:
                self.callers.append(caller)
                content = json.dumps(
                    [
                        {
                            "bvid": "BVe2e",
                            "expression": "这条接住你最近的状态。",
                            "topic_label": "你最近在意的方向",
                        }
                    ],
                    ensure_ascii=False,
                )
                return LLMResponse(content=content, provider="test", model="dummy", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(Path(tmpdir) / "e2e.db")
            db.initialize()
            # Classified but un-copied → "needs copy", not yet serveable.
            db.cache_content(
                "BVe2e",
                title="测试视频",
                up_name="UP",
                source="search",
                style_key="tutorial",
                topic_group="测试分组",
                topic_key="测试分组",
                # Keep this fixture below the delight threshold: this E2E
                # verifies regular-pool copy refill, not surprise-channel
                # claiming.
                relevance_score=0.65,
                pool_expression="",
                pool_topic_label="",
            )
            assert db.count_pool_candidates() == 0  # gated: copy missing

            profile = SoulProfile(
                personality_portrait="p",
                core_traits=["好奇"],
                preferences=PreferenceLayer(),
            )
            llm = _CopyLLM()
            engine = RecommendationEngine(llm=llm, database=db)

            class FakeSoulEngine:
                async def get_profile(self) -> object:
                    return profile

            ctx = RuntimeContext(
                config=Config(),
                memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
                runtime_controller=object(),
                account_sync_service=object(),
                auto_update_service=object(),
                soul_engine=FakeSoulEngine(),
                recommendation_engine=engine,
            )
            engine.task_registry = ctx.task_registry  # mirror production wiring

            # Capture the post-reload precompute task so we can await it.
            captured: dict[str, asyncio.Task[object]] = {}
            original_track = ctx.task_registry.track

            def _track(name: str, coro: object) -> object:
                task = original_track(name, coro)
                captured[name] = task
                return task

            ctx.task_registry.track = _track  # type: ignore[method-assign]

            app = SimpleNamespace(state=SimpleNamespace())
            try:
                await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=2.0)
                assert "post_reload_precompute_pool_copy" in captured
                await asyncio.wait_for(captured["post_reload_precompute_pool_copy"], timeout=2.0)

                # The reload drove the REAL engine to write copy for the pending
                # candidate against the REAL DB — it is now serveable.
                assert "recommendation.write_expression" in llm.callers
                assert db.count_pool_candidates() == 1
            finally:
                await ctx.task_registry.cancel_all()

    @pytest.mark.asyncio
    async def test_startup_prewarm_wrapper_skips_retries_on_nothing_to_warm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lever 4: the startup prewarm wrapper skips its retry loop when prewarm
        returns -1 ("nothing to warm" — empty pool / embeddings off), so a fresh
        deploy no longer emits 5 alarming "warmed=0 — retry" lines that read like
        a real Ollama outage. A 0 return (candidates present, backend down) still
        retries.
        """
        from openbiliclaw.api.runtime_context import RuntimeContext

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        # -1 → benign cold start: called exactly once, no retry loop.
        benign_calls = 0

        async def _benign() -> int:
            nonlocal benign_calls
            benign_calls += 1
            return -1

        await RuntimeContext._safe_prewarm_pool_mmr_embeddings(_benign)
        assert benign_calls == 1

        # 0 → backend unreachable: retried up to 5 times before giving up.
        down_calls = 0

        async def _down() -> int:
            nonlocal down_calls
            down_calls += 1
            return 0

        await RuntimeContext._safe_prewarm_pool_mmr_embeddings(_down)
        assert down_calls == 5

    @pytest.mark.asyncio
    async def test_put_config_does_not_block_on_speculator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        import httpx

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config, save_config

        config_path = tmp_path / "config.toml"
        cfg = Config()
        cfg.llm.default_provider = "openai"
        cfg.llm.openai.api_key = "sk-test-openai"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        class HangingSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                await asyncio.sleep(60)

        class FakeSoulEngine:
            _speculator = HangingSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        async def _fake_rebuild(self: RuntimeContext, new_config: Config) -> None:
            self.config = new_config
            self.memory_manager = SimpleNamespace(load_discovery_runtime_state=lambda: {})
            self.runtime_controller = object()
            self.account_sync_service = object()
            self.auto_update_service = object()
            self.soul_engine = FakeSoulEngine()
            self.recommendation_engine = object()

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await asyncio.wait_for(
                client.put("/api/config", json={"language": "zh"}),
                timeout=0.5,
            )

        assert response.status_code == 202
        body = response.json()
        assert body["apply_state"] == "queued"
        assert body["reloaded"] is False

    @pytest.mark.asyncio
    async def test_put_config_setup_suppression_flag_skips_post_reload_llm_work(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import httpx

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config, save_config

        config_path = tmp_path / "config.toml"
        cfg = Config()
        cfg.llm.default_provider = "openai"
        cfg.llm.openai.api_key = "sk-test-openai"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        async def _fake_rebuild(self: RuntimeContext, new_config: Config) -> None:
            self.config = new_config

        restart_flags: list[bool] = []

        async def _fake_restart(
            self: RuntimeContext,
            app: object,
            *,
            run_post_reload_llm_work: bool = True,
        ) -> None:
            restart_flags.append(run_post_reload_llm_work)

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _fake_restart)
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.put(
                "/api/config",
                json={"language": "zh", "suppress_background_llm_work": True},
            )
            body = response.json()
            await _wait_for_config_apply_async(
                client,
                revision=body["apply_revision"],
            )

        assert response.status_code == 202
        assert body["apply_state"] == "queued"
        assert body["reloaded"] is False
        assert restart_flags == [False]

    def test_create_app_bootstrap_shares_database_with_memory_manager(
        self,
        monkeypatch,
    ) -> None:
        from types import SimpleNamespace

        import openbiliclaw.api.app as app_module
        import openbiliclaw.bilibili.api as bilibili_api_module
        import openbiliclaw.llm.service as llm_service_module
        import openbiliclaw.memory.manager as memory_module
        import openbiliclaw.storage.database as database_module

        created_databases: list[object] = []
        created_memories: list[object] = []

        class FakeDatabase:
            def __init__(self, path) -> None:
                self.path = path
                self.initialized = 0
                created_databases.append(self)

            def initialize(self) -> None:
                self.initialized += 1

        class FakeMemoryManager:
            def __init__(self, data_path, database=None) -> None:
                self.data_path = data_path
                self.database = database
                self.initialized = 0
                created_memories.append(self)

            def initialize(self) -> None:
                self.initialized += 1

        class FakeLLMService:
            def __init__(
                self,
                *,
                registry: object,
                memory: object,
                usage_recorder: object | None = None,
                module_overrides: object | None = None,
                concurrency: int = 1,
                concurrency_gate: object | None = None,
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder
                self.module_overrides = module_overrides
                self.concurrency = concurrency
                self.concurrency_gate = concurrency_gate

        class FakeBilibiliClient:
            def __init__(self, *, cookie: str, proxy: str | None = None) -> None:
                self.cookie = cookie
                self.proxy = proxy

        fake_config = SimpleNamespace(
            data_path=Path("/tmp/openbiliclaw-test-data"),
            bilibili=SimpleNamespace(cookie="", proxy=""),
        )

        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
        monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
        monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")
        monkeypatch.setattr(database_module, "Database", FakeDatabase)
        monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
        monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
        monkeypatch.setattr(bilibili_api_module, "BilibiliAPIClient", FakeBilibiliClient)

        app_module.create_app(
            soul_engine=object(),
            recommendation_engine=object(),
            runtime_controller=object(),
            account_sync_service=object(),
            dialogue=object(),
        )

        assert len(created_databases) == 1
        assert created_databases[0].initialized == 1
        assert len(created_memories) == 1
        assert created_memories[0].initialized == 1
        assert created_memories[0].database is created_databases[0]

    def test_runtime_context_wires_llm_module_overrides(self, tmp_path: Path) -> None:
        from openbiliclaw.api.runtime_context import build_runtime_context
        from openbiliclaw.config import Config

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.llm.soul.provider = "ollama"
        config.llm.soul.model = "llama3-soul"
        config.llm.discovery.model = "llama3-discovery"

        ctx = build_runtime_context(config)

        assert ctx.llm_service.module_overrides["soul"].model == "llama3-soul"
        assert ctx.llm_service.module_overrides["discovery"].model == "llama3-discovery"
        assert ctx.soul_engine._llm_service.module_overrides["soul"].provider == "ollama"

    def test_runtime_context_wires_reddit_producer_when_enabled(self, tmp_path: Path) -> None:
        from openbiliclaw.api.runtime_context import build_runtime_context
        from openbiliclaw.config import Config
        from openbiliclaw.runtime.reddit_producer import RedditDiscoveryProducer

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.sources.reddit.enabled = True
        config.scheduler.pool_source_shares["reddit"] = 2

        ctx = build_runtime_context(config)

        assert isinstance(ctx.runtime_controller.reddit_producer, RedditDiscoveryProducer)
        assert ctx.runtime_controller.pool_source_shares["reddit"] == 2

    def test_runtime_context_wires_linuxdo_producer_when_enabled(self, tmp_path: Path) -> None:
        from openbiliclaw.api.runtime_context import build_runtime_context
        from openbiliclaw.config import Config
        from openbiliclaw.runtime.linuxdo_producer import LinuxdoDiscoveryProducer

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.sources.linuxdo.enabled = True
        config.sources.linuxdo.request_interval_seconds = 7
        config.scheduler.pool_source_shares["linuxdo"] = 2

        ctx = build_runtime_context(config)

        producer = ctx.runtime_controller.linuxdo_producer
        assert isinstance(producer, LinuxdoDiscoveryProducer)
        assert producer.candidate_pipeline is ctx.runtime_controller.discovery_candidate_pipeline
        assert producer.keyword_fetch is ctx.runtime_controller.keyword_fetch
        assert producer.candidate_evaluation_owned_by_coordinator is True
        assert producer.poll_interval_seconds == 7
        assert ctx.runtime_controller.pool_source_shares["linuxdo"] == 2

    def test_runtime_context_delegates_runtime_producer_evaluation_to_shared_coordinator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import openbiliclaw.api.runtime_context as runtime_context_module
        import openbiliclaw.runtime.douyin_producer as douyin_producer_module
        import openbiliclaw.runtime.x_producer as x_producer_module
        import openbiliclaw.runtime.zhihu_producer as zhihu_producer_module
        from openbiliclaw.config import Config

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.sources.douyin.enabled = True
        config.sources.youtube.enabled = True
        config.sources.zhihu.enabled = True
        producers: dict[str, SimpleNamespace] = {}
        douyin_kwargs: list[dict[str, object]] = []
        x_kwargs: list[dict[str, object]] = []

        def build_producer(kind: str) -> SimpleNamespace:
            producer = SimpleNamespace(kind=kind)
            producers[kind] = producer
            return producer

        monkeypatch.setattr(
            douyin_producer_module,
            "build_douyin_discovery_producer",
            lambda **kwargs: douyin_kwargs.append(kwargs) or build_producer("douyin"),
        )
        monkeypatch.setattr(
            runtime_context_module,
            "build_youtube_discovery_producer",
            lambda **_kwargs: build_producer("youtube"),
        )
        monkeypatch.setattr(
            zhihu_producer_module,
            "build_zhihu_discovery_producer",
            lambda **_kwargs: build_producer("zhihu"),
        )
        monkeypatch.setattr(
            x_producer_module,
            "build_x_discovery_producer",
            lambda **kwargs: x_kwargs.append(kwargs) or build_producer("twitter"),
        )

        ctx = runtime_context_module.build_runtime_context(config)

        assert set(producers) == {"douyin", "youtube", "zhihu", "twitter"}
        assert douyin_kwargs[0]["presence"] is ctx.presence
        assert douyin_kwargs[0]["presence_grace_seconds"] == (
            config.scheduler.extension_disconnect_grace_seconds
        )
        assert all(
            producer.candidate_evaluation_owned_by_coordinator is True
            for kind, producer in producers.items()
            if kind != "twitter"
        )
        notifications: list[str] = []
        ctx.runtime_controller.candidate_eval_coordinator.notify = notifications.append
        pipeline = ctx.runtime_controller.discovery_candidate_pipeline
        assert callable(pipeline.on_candidates_enqueued)
        pipeline.on_candidates_enqueued(1)
        assert notifications == ["candidate_enqueued:pipeline"]
        assert x_kwargs[0]["candidate_pipeline"] is pipeline

    def test_create_app_bootstrap_wires_discovery_concurrency_controller(
        self,
        monkeypatch,
    ) -> None:
        from types import SimpleNamespace

        import openbiliclaw.api.app as app_module
        import openbiliclaw.bilibili.api as bilibili_api_module
        import openbiliclaw.discovery.candidate_pipeline as candidate_pipeline_module
        import openbiliclaw.discovery.engine as discovery_engine_module
        import openbiliclaw.discovery.strategies.strategies as strategies_module
        import openbiliclaw.llm.service as llm_service_module
        import openbiliclaw.memory.manager as memory_module
        import openbiliclaw.recommendation.engine as recommendation_module
        import openbiliclaw.runtime.account_sync as account_sync_module
        import openbiliclaw.runtime.bilibili_producer as bilibili_producer_module
        import openbiliclaw.runtime.events as runtime_events_module
        import openbiliclaw.runtime.refresh as refresh_module
        import openbiliclaw.soul.dialogue as dialogue_module
        import openbiliclaw.soul.engine as soul_engine_module
        import openbiliclaw.sources.bili_tasks as bili_tasks_module
        import openbiliclaw.sources.dy_tasks as dy_tasks_module
        import openbiliclaw.sources.x_tasks as x_tasks_module
        import openbiliclaw.sources.xhs_tasks as xhs_tasks_module
        import openbiliclaw.sources.yt_tasks as yt_tasks_module
        import openbiliclaw.sources.zhihu_tasks as zhihu_tasks_module
        import openbiliclaw.storage.database as database_module

        captured: dict[str, object] = {}

        class FakeDiscoveryConcurrencyController:
            def __init__(
                self,
                *,
                bilibili_request_concurrency: int,
                llm_evaluation_concurrency: int,
            ) -> None:
                captured["controller"] = self
                captured["bilibili_request_concurrency"] = bilibili_request_concurrency
                captured["llm_evaluation_concurrency"] = llm_evaluation_concurrency

        class FakeContentDiscoveryEngine:
            def __init__(
                self,
                *,
                llm_service: object,
                database: object,
                concurrency=None,
                embedding_service=None,
                **_extras: object,
            ) -> None:
                captured["engine_concurrency"] = concurrency

            def register_strategy(self, strategy: object) -> None:
                return None

            def register_adapter(self, adapter: object) -> None:
                return None

        class _FakeStrategy:
            def __init__(self, *args, concurrency=None, **kwargs) -> None:
                captured.setdefault("strategy_concurrency", []).append(concurrency)

        class FakeDatabase:
            def __init__(self, path) -> None:
                self.path = path
                self.conn = object()

            def initialize(self) -> None:
                return None

        class FakeMemoryManager:
            def __init__(self, data_path, database=None) -> None:
                self.data_path = data_path
                self.database = database

            def initialize(self) -> None:
                return None

        class FakeLLMService:
            def __init__(
                self,
                *,
                registry: object,
                memory: object,
                usage_recorder: object | None = None,
                module_overrides: object | None = None,
                concurrency: int = 1,
                concurrency_gate: object | None = None,
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder
                self.module_overrides = module_overrides
                self.concurrency = concurrency
                self.concurrency_gate = concurrency_gate

        class FakeBilibiliClient:
            def __init__(self, *, cookie: str, proxy: str | None = None) -> None:
                self.cookie = cookie
                self.proxy = proxy

        class FakeSoulEngine:
            def __init__(
                self,
                *,
                llm: object,
                memory: object,
                usage_recorder: object = None,
                **_extras: object,
            ) -> None:
                self.llm = llm
                self.memory = memory
                self.usage_recorder = usage_recorder
                captured["soul_engine_kwargs"] = _extras

        class FakeRecommendationEngine:
            def __init__(
                self,
                *,
                llm: object,
                database: object,
                curator: object = None,
                embedding_service: object = None,
                task_registry: object = None,
                xhs_self_info_provider: object = None,
                **_extras: object,
            ) -> None:
                self.llm = llm
                self.database = database
                self.task_registry = task_registry

            def count_pending_expression_copy_demand(self) -> int:
                return 0

        class FakeRuntimeController:
            def __init__(self, **kwargs) -> None:
                captured["runtime_controller_kwargs"] = kwargs

        class FakeDiscoveryCandidatePipeline:
            min_eval_batch_size = 23

            def __init__(self, **kwargs: object) -> None:
                captured["candidate_pipeline_kwargs"] = kwargs

        class FakeAccountSyncService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                captured["account_sync_kwargs"] = kwargs

        class FakeRuntimeEventHub:
            pass

        class FakeBiliTaskQueue:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeXhsTaskQueue:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeXhsCreatorStore:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeDyTaskQueue:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeYtTaskQueue:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeZhihuTaskQueue:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeXCreatorStore:
            def __init__(self, database: object) -> None:
                self.database = database

        class FakeBiliProducer:
            def __init__(self, **kwargs: object) -> None:
                captured["bilibili_producer_kwargs"] = kwargs

        class FakeDialogue:
            def __init__(
                self,
                *,
                llm: object | None = None,
                soul_engine: object,
                llm_service: object | None = None,
                session: str,
                tools: object | None = None,
                tool_dispatcher: object | None = None,
                database: object | None = None,
                learning_mode: object,
                settlement_queue: object | None = None,
            ) -> None:
                self.llm = llm
                self.soul_engine = soul_engine
                self.llm_service = llm_service
                self.session = session
                self.database = database
                self.learning_mode = learning_mode
                self.settlement_queue = settlement_queue

        fake_config = SimpleNamespace(
            data_path=Path("/tmp/openbiliclaw-test-data"),
            bilibili=SimpleNamespace(
                cookie="", proxy="", browser_executable="", browser_headed=False
            ),
            llm=SimpleNamespace(concurrency=3),
            sources=SimpleNamespace(
                browser_cdp_url="",
                browser_headed=False,
                bilibili=SimpleNamespace(enabled=True),
                xiaohongshu=SimpleNamespace(
                    enabled=False,
                    daily_search_budget=20,
                    daily_creator_budget=10,
                    task_interval_seconds=45,
                ),
                douyin=SimpleNamespace(enabled=False),
                youtube=SimpleNamespace(enabled=False),
                twitter=SimpleNamespace(enabled=False),
            ),
            scheduler=SimpleNamespace(
                enabled=True,
                pause_on_extension_disconnect=False,
                pool_target_count=300,
                eval_min_batch_size=23,
                eval_max_wait_seconds=45.5,
                account_sync_interval_hours=24,
                refresh_check_interval_seconds=77,
                signal_event_threshold=9,
                trending_refresh_minutes=5,
                explore_refresh_minutes=18,
                discovery_limit=17,
                proactive_push_interval_seconds=155,
                speculation_interval_minutes=22,
                speculation_ttl_days=8,
                speculation_cooldown_days=9,
                speculation_confirmation_threshold=4,
                speculation_max_active=6,
                speculation_max_primary_interests=17,
                speculation_max_secondary_interests=66,
                speculator_idle_interval_minutes=11,
            ),
        )

        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
        monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
        monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")
        monkeypatch.setattr(
            discovery_engine_module,
            "DiscoveryConcurrencyController",
            FakeDiscoveryConcurrencyController,
        )
        monkeypatch.setattr(
            discovery_engine_module,
            "ContentDiscoveryEngine",
            FakeContentDiscoveryEngine,
        )
        monkeypatch.setattr(strategies_module, "SearchStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "TrendingStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "RelatedChainStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "ExploreStrategy", _FakeStrategy)
        monkeypatch.setattr(database_module, "Database", FakeDatabase)
        monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
        monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
        monkeypatch.setattr(bilibili_api_module, "BilibiliAPIClient", FakeBilibiliClient)
        monkeypatch.setattr(soul_engine_module, "SoulEngine", FakeSoulEngine)
        monkeypatch.setattr(recommendation_module, "RecommendationEngine", FakeRecommendationEngine)
        monkeypatch.setattr(refresh_module, "ContinuousRefreshController", FakeRuntimeController)
        monkeypatch.setattr(
            candidate_pipeline_module,
            "DiscoveryCandidatePipeline",
            FakeDiscoveryCandidatePipeline,
        )
        monkeypatch.setattr(account_sync_module, "AccountSyncService", FakeAccountSyncService)
        monkeypatch.setattr(bili_tasks_module, "BiliTaskQueue", FakeBiliTaskQueue)
        monkeypatch.setattr(dy_tasks_module, "DyTaskQueue", FakeDyTaskQueue)
        monkeypatch.setattr(x_tasks_module, "XCreatorStore", FakeXCreatorStore)
        monkeypatch.setattr(xhs_tasks_module, "XhsTaskQueue", FakeXhsTaskQueue)
        monkeypatch.setattr(xhs_tasks_module, "XhsCreatorStore", FakeXhsCreatorStore)
        monkeypatch.setattr(yt_tasks_module, "YtTaskQueue", FakeYtTaskQueue)
        monkeypatch.setattr(zhihu_tasks_module, "ZhihuTaskQueue", FakeZhihuTaskQueue)
        monkeypatch.setattr(
            bilibili_producer_module,
            "BilibiliExtensionSearchProducer",
            FakeBiliProducer,
        )
        monkeypatch.setattr(runtime_events_module, "RuntimeEventHub", FakeRuntimeEventHub)
        monkeypatch.setattr(dialogue_module, "SocraticDialogue", FakeDialogue)

        app = app_module.create_app()

        assert captured["bilibili_request_concurrency"] == 2
        assert captured["llm_evaluation_concurrency"] == 2
        assert (
            app.state.runtime_context.llm_service.concurrency_gate
            is (captured["soul_engine_kwargs"]["llm_concurrency_gate"])
        )
        assert captured["runtime_controller_kwargs"]["llm_concurrency_gate"] is (
            app.state.runtime_context.llm_service.concurrency_gate
        )
        assert captured["engine_concurrency"] is captured["controller"]
        assert all(item is captured["controller"] for item in captured["strategy_concurrency"])
        assert captured["runtime_controller_kwargs"]["scheduler_config"] is fake_config.scheduler
        assert (
            captured["runtime_controller_kwargs"]["presence"] is app.state.runtime_context.presence
        )
        source_sync = captured["runtime_controller_kwargs"]["source_incremental_sync"]
        assert source_sync.database is app.state.runtime_context.database
        assert source_sync.memory_manager is app.state.runtime_context.memory_manager
        assert source_sync.presence is app.state.runtime_context.presence
        assert source_sync.scheduler_config is fake_config.scheduler
        assert source_sync.source_enabled == {
            "xhs": False,
            "dy": False,
            "yt": False,
            "zhihu": False,
            "reddit": False,
            "linuxdo": False,
            "v2ex": False,
        }
        assert captured["runtime_controller_kwargs"]["bilibili_producer"] is not None
        assert (
            captured["bilibili_producer_kwargs"]["presence"] is app.state.runtime_context.presence
        )
        assert captured["bilibili_producer_kwargs"]["bilibili_client"].cookie == ""
        assert captured["runtime_controller_kwargs"]["check_interval_seconds"] == 77
        assert captured["runtime_controller_kwargs"]["signal_event_threshold"] == 9
        assert captured["runtime_controller_kwargs"]["trending_refresh_minutes"] == 5
        assert captured["runtime_controller_kwargs"]["explore_refresh_minutes"] == 18
        assert captured["runtime_controller_kwargs"]["discovery_limit"] == 17
        assert captured["runtime_controller_kwargs"]["proactive_push_interval_seconds"] == 155
        assert captured["candidate_pipeline_kwargs"]["min_eval_batch_size"] == 23
        assert captured["candidate_pipeline_kwargs"]["max_eval_wait_seconds"] == 45.5
        assert captured["soul_engine_kwargs"]["speculation_interval_minutes"] == 22
        assert captured["soul_engine_kwargs"]["speculation_ttl_days"] == 8
        assert captured["soul_engine_kwargs"]["speculation_cooldown_days"] == 9
        assert captured["soul_engine_kwargs"]["speculation_confirmation_threshold"] == 4
        assert captured["soul_engine_kwargs"]["speculation_max_active"] == 6
        assert captured["soul_engine_kwargs"]["speculation_max_primary_interests"] == 17
        assert captured["soul_engine_kwargs"]["speculation_max_secondary_interests"] == 66
        assert captured["soul_engine_kwargs"]["speculator_idle_interval_minutes"] == 11
        assert callable(captured["account_sync_kwargs"]["llm_work_allowed"])

        runtime_context = app.state.runtime_context
        old_service = runtime_context.llm_service
        shared_gate = old_service.concurrency_gate
        fake_config.llm.concurrency = 2

        runtime_context._rebuild_components(fake_config)

        assert runtime_context.llm_service is not old_service
        assert captured["runtime_controller_kwargs"]["source_incremental_sync"] is not source_sync
        assert (
            captured["runtime_controller_kwargs"]["source_incremental_sync"].scheduler_config
            is fake_config.scheduler
        )
        assert old_service.concurrency_gate is shared_gate
        assert runtime_context.llm_service.concurrency_gate is shared_gate
        assert captured["soul_engine_kwargs"]["llm_concurrency_gate"] is shared_gate
        assert shared_gate.status_payload()["llm_total_concurrency"] == 2

    def test_cap_by_franchise_keeps_at_most_n_per_franchise(self) -> None:
        """Regression for the 'one popup full of 原神' bug. The API
        layer caps how many same-``franchise_key`` items reach the
        client. franchise_key is the LLM-tagged IP column on
        content_cache (NOT a heuristic from titles).

        Items with empty franchise_key (general-interest content) must
        always pass through — the cap only fires for tagged IPs.
        """
        from openbiliclaw.api.app import _cap_by_franchise

        rows = [
            {"id": 1, "title": "原神 4.0 须弥探索", "franchise_key": "原神"},
            {"id": 2, "title": "提瓦特 摄影集锦", "franchise_key": "原神"},
            {"id": 3, "title": "番茄炒蛋 5 分钟教程", "franchise_key": ""},
            {"id": 4, "title": "蒙德角色真实化", "franchise_key": "原神"},
            {"id": 5, "title": "塞尔达 王国之泪", "franchise_key": "塞尔达传说"},
            {"id": 6, "title": "枫丹海域旅拍", "franchise_key": "原神"},
            {"id": 7, "title": "原神 AI 重制 2024", "franchise_key": "原神"},
        ]
        out = _cap_by_franchise(rows, max_per_franchise=2)
        # First two 原神 rows survive (id=1, 2); subsequent ones drop.
        # 番茄炒蛋 has empty franchise_key so it always passes.
        # 塞尔达 is a different franchise, also passes.
        assert [r["id"] for r in out] == [1, 2, 3, 5]

    def test_cap_by_franchise_zero_disables_cap(self) -> None:
        """max_per_franchise=0 is the escape hatch for ops who want to
        debug without re-deploying. Returns input unchanged."""
        from openbiliclaw.api.app import _cap_by_franchise

        rows = [
            {"id": 1, "franchise_key": "原神"},
            {"id": 2, "franchise_key": "原神"},
            {"id": 3, "franchise_key": "原神"},
        ]
        out = _cap_by_franchise(rows, max_per_franchise=0)
        assert len(out) == 3

    def test_health_endpoint_returns_ok(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "openbiliclaw-api"

    def test_ping_endpoint_is_pure_liveness(self) -> None:
        """/api/ping answers instantly with no probes — the extension badge
        depends on it never inheriting /api/health's embedding-probe latency."""
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/ping")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "openbiliclaw-api"}

    def test_qr_info_endpoint_returns_lan_ip_without_embedding_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module

        class _FailingProbeService:
            async def probe(self) -> bool:
                raise AssertionError("/api/qr-info must not probe embedding readiness")

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _FailingProbeService()

        monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.7")

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/qr-info")

        assert response.status_code == 200
        assert response.json() == {"lan_ip": "192.168.1.7"}

    def test_qr_info_endpoint_stays_available_in_degraded_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module

        monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: "192.168.1.7")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.runtime_context.degraded = True
        app.state.runtime_context.degraded_reason = "test_degraded"
        client = TestClient(app)

        response = client.get("/api/qr-info")

        assert response.status_code == 200
        assert response.json() == {"lan_ip": "192.168.1.7"}

    def test_qr_info_endpoint_detects_lan_ip_fresh_on_every_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Wi-Fi switch must reach the QR panel at once, not after the TTL.

        ``/api/health`` caches ``lan_ip`` for 30s. If the QR endpoint shared
        that cache, a code scanned right after a network change would still
        encode the old, phone-unreachable host.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module

        addresses = iter(["192.168.1.7", "192.168.31.98"])
        monkeypatch.setattr(app_module, "_detect_lan_ip", lambda: next(addresses))

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        first = client.get("/api/qr-info")
        second = client.get("/api/qr-info")

        assert first.json() == {"lan_ip": "192.168.1.7"}
        assert second.json() == {"lan_ip": "192.168.31.98"}
        # The fresh probe also refreshes the cache /api/health serves, so health
        # never regresses to the superseded address (a third _detect_lan_ip call
        # would exhaust the iterator and raise).
        assert client.get("/api/health").json()["lan_ip"] == "192.168.31.98"

    def test_sources_status_returns_every_source(self) -> None:
        """Unified /api/sources/status reports a status item per source."""
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/sources/status")

        assert response.status_code == 200
        body = response.json()
        # One status item per source, each with the unified shape.
        for key in (
            "bilibili",
            "xiaohongshu",
            "douyin",
            "youtube",
            "twitter",
            "zhihu",
            "reddit",
            "bangumi",
            "linuxdo",
            "weibo",
        ):
            assert key in body, f"{key} missing from sources status"
            item = body[key]
            assert set(item) >= {"enabled", "state", "detail", "logged_in"}
            assert isinstance(item["enabled"], bool)
            assert isinstance(item["state"], str) and item["state"]
        # YouTube needs no login -> always no_auth.
        assert body["youtube"]["state"] == "no_auth"
        assert body["youtube"]["logged_in"] is True
        assert body["zhihu"]["state"] in {"unverified", "ready", "missing", "stale"}
        assert body["reddit"]["state"] in {
            "unverified",
            "missing",
            "login_required",
            "ready",
            "stale",
            "error",
        }
        # Bangumi is now on the contract: anonymous-public, so state is always
        # no_auth and logged_in True (discovery-health rides detail, not state,
        # and the enable switch stays its own field).
        assert body["bangumi"]["state"] == "no_auth"
        assert body["bangumi"]["logged_in"] is True
        assert body["bangumi"]["auth"] is not None
        assert body["bangumi"]["auth"]["auth_required"] is False
        assert body["linuxdo"]["state"] == "no_auth"
        assert body["linuxdo"]["logged_in"] is True
        assert body["linuxdo"]["auth"]["auth_required"] is False
        assert body["weibo"]["state"] == "no_auth"
        assert body["weibo"]["logged_in"] is True
        assert body["weibo"]["auth"]["auth_required"] is False

    def test_bangumi_status_is_no_auth_with_discovery_health_in_detail(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """On the contract, Bangumi's auth verdict is fixed; discovery rides detail.

        It used to fold discovery readiness into ``state`` (``unverified`` →
        ``ready``) and derive ``logged_in`` from it — the D1 conflation the
        contract removes. Now ``state`` is always ``no_auth`` (anonymous-public)
        and ``logged_in`` always True; a completed discovery run moves the
        *detail*, not the auth verdict.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.bangumi.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        database = Database(tmp_path / "bangumi-source-status.db")
        database.initialize()
        client = TestClient(
            create_app(memory_manager=object(), database=database, soul_engine=object())
        )

        before = client.get("/api/sources/status").json()["bangumi"]

        assert before["state"] == "no_auth"
        assert before["logged_in"] is True
        assert before["detail"] == "尚未运行 Bangumi 内容发现。"
        assert before["auth"]["auth_required"] is False
        assert before["auth"]["verify_method"] == "none"  # no token configured

        database.conn.execute(
            "INSERT INTO bangumi_discovery_runs(mode, units, discovered, reason) "
            "VALUES ('search', 1, 1, 'ok')"
        )
        database.conn.commit()
        after = client.get("/api/sources/status").json()["bangumi"]

        # Auth verdict unchanged; only the discovery-health detail moved.
        assert after["state"] == "no_auth"
        assert after["logged_in"] is True
        assert after["detail"] != before["detail"]

    def test_bangumi_status_exposes_token_state_three_states(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.runtime.bangumi_producer import (
            BangumiDiscoveryProducer,
            _persist_token_rejection,
            _token_fingerprint,
        )
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.bangumi.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        database = Database(tmp_path / "bangumi-token-state.db")
        database.initialize()
        client = TestClient(
            create_app(memory_manager=object(), database=database, soul_engine=object())
        )

        # No token configured: the token_state dimension stays empty.
        no_token = client.get("/api/sources/status").json()["bangumi"]
        assert no_token["token_state"] == ""

        # Token configured, no rejection marker: ok.
        cfg.sources.bangumi.access_token = "tok"
        ok = client.get("/api/sources/status").json()["bangumi"]
        assert ok["token_state"] == "ok"
        assert "无需登录" not in ok["detail"]

        # A persisted rejection marker surfaces the actionable warning.
        BangumiDiscoveryProducer(
            database=database,
            soul_engine=object(),
            client=object(),
            enabled=True,
        )._ensure_tables()
        _persist_token_rejection(database, _token_fingerprint("tok"))
        rejected = client.get("/api/sources/status").json()["bangumi"]
        assert rejected["token_state"] == "rejected"
        assert "已被拒绝" in rejected["detail"]

    def test_bangumi_disabled_status_surfaces_a_saved_credential(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A saved-but-unused credential is a state, not silence.

        The settings page validates the token and echoes the resolved account
        back, so the user believes Bangumi is configured; only the enable
        switch is still off. ``/api/sources/status`` has to say so.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.bangumi.enabled = False
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        database = Database(tmp_path / "bangumi-disabled-credential.db")
        database.initialize()
        client = TestClient(
            create_app(memory_manager=object(), database=database, soul_engine=object())
        )

        bare = client.get("/api/sources/status").json()["bangumi"]
        # state is the auth verdict (no_auth) now, never the enable switch (D12);
        # "off" lives in ``enabled``, and the saved-but-idle credential in detail.
        assert bare["state"] == "no_auth"
        assert bare["enabled"] is False
        assert bare["token_state"] == ""
        assert bare["detail"] == "Bangumi 来源未启用。"

        cfg.sources.bangumi.access_token = "tok"
        with_token = client.get("/api/sources/status").json()["bangumi"]
        assert with_token["state"] == "no_auth"
        assert with_token["enabled"] is False
        assert with_token["logged_in"] is True
        # "ok" (not "rejected") so the desktop / popup renderers keep the
        # neutral "来源未启用" tone instead of the red token warning.
        assert with_token["token_state"] == "ok"
        assert "已保存个人令牌" in with_token["detail"]

        cfg.sources.bangumi.access_token = ""
        cfg.sources.bangumi.username = "215952"
        with_username = client.get("/api/sources/status").json()["bangumi"]
        assert with_username["token_state"] == ""
        assert "已保存公开用户名" in with_username["detail"]

    def test_sources_credentials_returns_current_local_credentials(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config
        from openbiliclaw.sources.douyin_auth import DouyinCookieManager
        from openbiliclaw.sources.x_auth import XCookieManager
        from openbiliclaw.storage.database import Database

        project_root = tmp_path / "credentials-root"
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
        cfg = Config()
        cfg.bilibili.cookie = "SESSDATA=bili; bili_jct=jct; DedeUserID=1;"
        save_config(cfg, project_root / "config.toml")
        DouyinCookieManager(cfg.data_path).set_cookie("msToken=dy; ttwid=tw;", source="test")
        XCookieManager(cfg.data_path).set_cookie("auth_token=x; ct0=csrf;", source="test")
        db = Database(tmp_path / "credentials.db")
        db.initialize()
        db.conn.execute(
            "INSERT INTO content_cache (bvid, source_platform, content_url) "
            "VALUES ('xhs1', 'xiaohongshu', "
            "'https://www.xiaohongshu.com/explore/xhs1?xsec_token=xhs-token')"
        )
        db.conn.commit()

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        body = client.get("/api/sources/credentials?reveal_keys=true").json()

        # The legacy reveal flag is intentionally a no-op: settings snapshots
        # report presence with a mask and never export the stored credential.
        assert "SESSDATA=bili" not in body["bilibili"]["value"]
        assert "msToken=dy" not in body["douyin"]["value"]
        assert "auth_token=x" not in body["twitter"]["value"]
        assert "*" in body["bilibili"]["value"]
        assert "*" in body["douyin"]["value"]
        assert "*" in body["twitter"]["value"]
        assert body["xiaohongshu"]["label"] == "xsec_token"
        assert body["xiaohongshu"]["value"] != "xhs-token"
        assert "*" in body["xiaohongshu"]["value"]
        assert "不代表账号登录" in body["xiaohongshu"]["detail"]
        assert body["youtube"]["available"] is False
        assert body["zhihu"]["available"] is False
        assert body["bangumi"]["available"] is False
        assert body["bangumi"]["label"] == "可选个人令牌"

        masked = client.get("/api/sources/credentials").json()
        assert masked["bilibili"]["value"] == body["bilibili"]["value"]

    def test_sources_status_xhs_recent_login_state_ready_without_tokens(
        self, tmp_path: Path
    ) -> None:
        """A fresh web_session signal is the xhs login gate, not xsec_token rows."""
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        _store_xhs_login_state(
            db,
            logged_in=True,
            when_iso=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["xiaohongshu"]
        assert item["state"] == "ready"
        assert item["logged_in"] is True
        assert "已登录小红书" in item["detail"]

    def test_sources_status_xhs_without_login_signal_is_unverified(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["xiaohongshu"]
        assert item["state"] == "unverified"
        assert item["logged_in"] is False
        assert "尚未收到" in item["detail"]

    def test_sources_status_xhs_logged_out_state_wins_over_fresh_tokens(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        _store_xhs_login_state(
            db,
            logged_in=False,
            when_iso=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )
        db.conn.execute(
            "INSERT INTO content_cache (bvid, source_platform, content_url) "
            "VALUES ('xhsnew', 'xiaohongshu', "
            "'https://www.xiaohongshu.com/explore/xhsnew?xsec_token=live')"
        )
        db.conn.commit()

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["xiaohongshu"]
        assert item["state"] == "missing"
        assert item["logged_in"] is False
        assert "未检测到小红书登录" in item["detail"]

    def test_sources_status_xhs_stale_login_state_needs_refresh_even_with_fresh_tokens(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        _store_xhs_login_state(
            db,
            logged_in=True,
            when_iso=(datetime.now(UTC) - timedelta(hours=73)).isoformat(),
        )
        db.conn.execute(
            "INSERT INTO content_cache (bvid, source_platform, content_url) "
            "VALUES ('xhsnew', 'xiaohongshu', "
            "'https://www.xiaohongshu.com/explore/xhsnew?xsec_token=live')"
        )
        db.conn.commit()

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["xiaohongshu"]
        assert item["state"] == "stale"
        assert item["logged_in"] is False
        assert "刷新" in item["detail"]

    def test_sources_status_douyin_cookie_is_unverified_not_logged_in(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources.douyin_auth import DouyinCookieManager
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.douyin.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        DouyinCookieManager(cfg.data_path).set_cookie("sessionid=dy", source="test")
        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["douyin"]
        assert item["state"] == "unverified"
        assert item["logged_in"] is False
        assert "实际任务" in item["detail"]

    def test_xhs_login_state_endpoint_persists_state(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/sources/xhs/login-state", json={"logged_in": True})

        assert response.status_code == 200
        assert response.json()["ok"] is True
        logged_in, updated_at = db.get_xhs_login_state()
        assert logged_in is True
        assert updated_at

    def test_xhs_login_state_endpoint_rejects_non_bool(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/sources/xhs/login-state", json={"logged_in": "true"})

        assert response.status_code == 422

    def test_sources_status_reddit_extension_backend_uses_synced_session_without_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources import reddit_tasks
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.reddit.enabled = True
        cfg.sources.reddit.backend = "extension"
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            reddit_tasks,
            "_rdt_credential_file",
            lambda: tmp_path / "rdt" / "credential.json",
        )
        sync_result = reddit_tasks.sync_rdt_credential_from_cookie_header(
            "reddit_session=rs; loid=loid", source="test"
        )
        assert sync_result.has_cookie is True

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["reddit"]
        assert item["enabled"] is True
        assert item["state"] == "ready"
        assert item["logged_in"] is True
        assert "reddit_session" in item["detail"]

    def test_sources_status_reddit_extension_backend_without_session_keeps_unverified(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources import reddit_tasks
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.reddit.enabled = True
        cfg.sources.reddit.backend = "extension"
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            reddit_tasks,
            "_rdt_credential_file",
            lambda: tmp_path / "rdt" / "credential.json",
        )

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["reddit"]
        assert item["enabled"] is True
        assert item["state"] == "unverified"
        assert item["logged_in"] is False

    def test_sources_status_reddit_extension_backend_login_required_without_session_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources import reddit_tasks
        from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.reddit.enabled = True
        cfg.sources.reddit.backend = "extension"
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            reddit_tasks,
            "_rdt_credential_file",
            lambda: tmp_path / "rdt" / "credential.json",
        )

        db = Database(tmp_path / "status.db")
        db.initialize()
        queue = RedditTaskQueue(db)
        task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["reddit_saved"]})
        assert task_id is not None
        queue.fail(task_id, error="reddit_login_required", debug={"login_required": True})

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["reddit"]
        assert item["enabled"] is True
        assert item["state"] == "missing"
        assert item["logged_in"] is False

    def test_sources_status_zhihu_recent_login_state_ready_without_tasks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.zhihu.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        db = Database(tmp_path / "status.db")
        db.initialize()
        _store_zhihu_login_state(
            db,
            logged_in=True,
            when_iso=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["zhihu"]
        assert item["enabled"] is True
        assert item["state"] == "ready"
        assert item["logged_in"] is True
        assert "已登录知乎" in item["detail"]

    @pytest.mark.parametrize(
        ("stored_logged_in", "age_hours", "task_case", "expected_state", "expected_logged_in"),
        [
            (False, 0.1, "completed", "missing", False),
            (True, 73.0, "login_required", "stale", False),
            (False, 0.1, "failed", "missing", False),
            (True, 73.0, "pending", "stale", False),
        ],
    )
    def test_sources_status_zhihu_explicit_login_signal_wins_over_task_history(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        stored_logged_in: bool,
        age_hours: float,
        task_case: str,
        expected_state: str,
        expected_logged_in: bool,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.zhihu.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        db = Database(tmp_path / "status.db")
        db.initialize()
        _store_zhihu_login_state(
            db,
            logged_in=stored_logged_in,
            when_iso=(datetime.now(UTC) - timedelta(hours=age_hours)).isoformat(),
        )
        queue = ZhihuTaskQueue(db)
        task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
        assert task_id is not None
        if task_case == "completed":
            queue.merge_result(
                task_id,
                items=[
                    {
                        "scope": "zhihu_read_history",
                        "title": "知乎阅读",
                        "url": "https://www.zhihu.com/question/1/answer/2",
                    }
                ],
                scope_counts={"zhihu_read_history": 1},
                complete=True,
            )
        elif task_case == "login_required":
            queue.fail(task_id, error="zhihu_login_required", debug={"login_required": True})
        elif task_case == "failed":
            queue.fail(task_id, error="zhihu_fetch_failed")

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["zhihu"]
        assert item["enabled"] is True
        assert item["state"] == expected_state
        assert item["logged_in"] is expected_logged_in

    def test_zhihu_login_state_endpoint_persists_state(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/sources/zhihu/login-state", json={"logged_in": True})

        assert response.status_code == 200
        assert response.json()["ok"] is True
        logged_in, updated_at = db.get_zhihu_login_state()
        assert logged_in is True
        assert updated_at

    def test_zhihu_login_state_endpoint_rejects_non_bool(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "status.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/sources/zhihu/login-state", json={"logged_in": "true"})

        assert response.status_code == 422

    def test_zhihu_incremental_task_result_enters_durable_profile_ingress(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bootstrap_state import default_source_bootstrap_state
        from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
        from openbiliclaw.storage.database import Database

        class MemorySpy:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []
                self.state = default_source_bootstrap_state()

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

            def load_source_bootstrap_state(self) -> dict[str, object]:
                return dict(self.state)

            def update_source_bootstrap_state(self, mutator: Any) -> dict[str, object]:
                result = mutator(self.state)
                self.state = result if isinstance(result, dict) else self.state
                return self.state

        db = Database(tmp_path / "zhihu-incremental-result.db")
        db.initialize()
        queue = ZhihuTaskQueue(db)
        task_id = queue.enqueue_with_id(
            "bootstrap_events",
            {"scopes": ["zhihu_read_history"], "incremental": True},
        )
        assert task_id is not None
        memory = MemorySpy()
        client = TestClient(create_app(memory_manager=memory, database=db, soul_engine=object()))

        response = client.post(
            "/api/sources/zhihu/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "items": [
                    {
                        "scope": "zhihu_read_history",
                        "content_type": "answer",
                        "content_id": "answer-42",
                        "title": "Incremental Zhihu signal",
                        "url": "https://www.zhihu.com/question/1/answer/42",
                    }
                ],
                "scope_counts": {"zhihu_read_history": 1},
            },
        )

        assert response.status_code == 200
        assert len(memory.events) == 1
        assert memory.events[0]["event_type"] == "view"
        assert memory.events[0]["metadata"]["profile_update_owner"] == "generic"  # type: ignore[index]
        assert queue.get(task_id)["status"] == "completed"

    def test_sources_status_zhihu_login_required_reports_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.zhihu.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        db = Database(tmp_path / "status.db")
        db.initialize()
        queue = ZhihuTaskQueue(db)
        task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
        assert task_id is not None
        queue.fail(
            task_id,
            error="zhihu_login_required",
            debug={"current_url": "https://www.zhihu.com/signin"},
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["zhihu"]
        assert item["enabled"] is True
        assert item["state"] == "missing"
        assert item["logged_in"] is False
        assert "登录知乎" in item["detail"]

    def test_sources_status_zhihu_recent_completed_reports_ready(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
        from openbiliclaw.storage.database import Database

        cfg = Config()
        cfg.sources.zhihu.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        db = Database(tmp_path / "status.db")
        db.initialize()
        queue = ZhihuTaskQueue(db)
        task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
        assert task_id is not None
        queue.merge_result(
            task_id,
            items=[
                {
                    "scope": "zhihu_read_history",
                    "title": "知乎阅读",
                    "url": "https://www.zhihu.com/question/1/answer/2",
                }
            ],
            scope_counts={"zhihu_read_history": 1},
            complete=True,
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["zhihu"]
        assert item["enabled"] is True
        assert item["state"] == "ready"
        assert item["logged_in"] is True
        assert "最近任务完成" in item["detail"]

    def test_sources_status_bilibili_incomplete_cookie_reports_partial(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A cookie missing core login fields renders yellow, not green."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        project_root = tmp_path / "partial-cookie"
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
        cfg = Config()
        cfg.bilibili.cookie = "SESSDATA=abc"
        save_config(cfg, project_root / "config.toml")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/sources/status").json()["bilibili"]
        assert item["state"] == "partial"
        assert item["logged_in"] is False

    def test_favicon_endpoint_serves_mobile_web_icon(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/favicon.ico")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert (
            response.content
            == (
                Path(__file__).resolve().parent.parent
                / "src"
                / "openbiliclaw"
                / "web"
                / "icon-32.png"
            ).read_bytes()
        )

    def test_health_endpoint_reports_profile_ready_when_available(self) -> None:
        from fastapi.testclient import TestClient

        class ReadySoulEngine:
            def is_profile_ready(self) -> bool:
                return True

        app = create_app(memory_manager=object(), database=object(), soul_engine=ReadySoulEngine())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "openbiliclaw-api"
        assert body["profile_ready"] is True

    def test_health_endpoint_reports_embedding_not_ready_without_service(self) -> None:
        from fastapi.testclient import TestClient

        # A bare object() soul engine has no _embedding_service attribute,
        # so embedding is reported as not ready — the popup turns this into
        # the "enable local Ollama" banner.
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

    def test_health_endpoint_reports_embedding_ready_when_service_present(self) -> None:
        from fastapi.testclient import TestClient

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = object()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is True

    def test_health_endpoint_embedding_ready_true_when_probe_succeeds(self) -> None:
        from fastapi.testclient import TestClient

        class _ProbeService:
            async def probe(self) -> bool:
                return True

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _ProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is True

    def test_health_endpoint_embedding_not_ready_when_probe_fails(self) -> None:
        from fastapi.testclient import TestClient

        # The service object exists but the provider can't produce a vector
        # (e.g. bge-m3 never pulled, so every embed 404s). The live probe must
        # report not-ready instead of the old build-only ``True`` that left the
        # popup banner green while semantic dedup was 100% broken.
        class _FailingProbeService:
            async def probe(self) -> bool:
                return False

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _FailingProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

    def test_health_endpoint_caches_embedding_probe_result(self) -> None:
        from fastapi.testclient import TestClient

        # Frequent /health polls (Docker healthcheck + popup re-poll on focus)
        # must share one provider round-trip within the TTL window.
        class _CountingProbeService:
            def __init__(self) -> None:
                self.calls = 0

            async def probe(self) -> bool:
                self.calls += 1
                return True

        service = _CountingProbeService()

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = service

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        client.get("/api/health")
        client.get("/api/health")

        assert service.calls == 1

    def test_health_endpoint_treats_loopback_ollama_timeout_as_cold_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as appmod

        monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

        class _SlowProbeService:
            async def probe(self) -> bool:
                await asyncio.sleep(0.2)
                return True

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _SlowProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        from openbiliclaw.config import Config

        app.state.runtime_context.config = Config()
        app.state.runtime_context.config.llm.embedding.provider = "ollama"
        app.state.runtime_context.config.llm.embedding.base_url = "http://127.0.0.1:11434/v1"
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is True

    @pytest.mark.parametrize(
        ("provider", "base_url"),
        [
            ("ollama", "http://ollama:11434/v1"),
            ("openai", "https://api.openai.com/v1"),
        ],
    )
    def test_health_endpoint_keeps_nonlocal_timeout_not_ready(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider: str,
        base_url: str,
    ) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as appmod

        monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

        class _SlowProbeService:
            async def probe(self) -> bool:
                await asyncio.sleep(0.2)
                return True

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _SlowProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        from openbiliclaw.config import Config

        app.state.runtime_context.config = Config()
        app.state.runtime_context.config.llm.embedding.provider = provider
        app.state.runtime_context.config.llm.embedding.base_url = base_url
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

    def test_detect_lan_ip_prefers_rfc1918_interface_over_benchmark_tun(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.api import app as app_module

        monkeypatch.setattr(app_module, "_default_route_ip", lambda: "198.18.0.1")
        monkeypatch.setattr(
            app_module,
            "_interface_ipv4_candidates",
            lambda: ["198.18.0.1", "192.168.31.98"],
        )

        assert app_module._detect_lan_ip() == "192.168.31.98"

    def test_detect_lan_ip_falls_back_to_ipv6_when_ipv4_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.api import app as app_module

        monkeypatch.setattr(app_module, "_default_route_ip", lambda: None)
        monkeypatch.setattr(app_module, "_interface_ipv4_candidates", lambda: [])
        monkeypatch.setattr(app_module, "_default_route_ipv6", lambda: "2001:db8:1::8")
        monkeypatch.setattr(
            app_module,
            "_interface_ipv6_candidates",
            lambda: ["fe80::1", "fd12:3456:789a::7"],
        )

        assert app_module._detect_lan_ip() == "fd12:3456:789a::7"

    def test_interface_ipv6_probe_ignores_link_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.api import app as app_module

        monkeypatch.setattr(app_module.socket, "has_ipv6", True)
        monkeypatch.setattr(app_module.os, "name", "posix")
        monkeypatch.setattr(
            app_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout=(
                    "inet6 fe80::1%en0 prefixlen 64 scopeid 0x4\n"
                    "inet6 fd12:3456:789a::7 prefixlen 64\n"
                    "inet6 2001:db8:1::8/64 scope global\n"
                ),
            ),
        )

        assert app_module._interface_ipv6_candidates() == [
            "fe80::1",
            "fd12:3456:789a::7",
            "2001:db8:1::8",
        ]

    def test_windows_interface_ipv4_probe_hides_ipconfig_console(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from openbiliclaw.api import app as app_module

        calls: list[dict[str, object]] = []

        def _fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append({"command": command, **kwargs})
            return SimpleNamespace(
                returncode=0,
                stdout="IPv4 Address . . . . . . . . . . : 192.168.1.7",
            )

        monkeypatch.setattr(app_module.os, "name", "nt")
        monkeypatch.setattr(app_module.subprocess, "run", _fake_run)
        monkeypatch.setattr(app_module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

        assert app_module._interface_ipv4_candidates() == ["192.168.1.7"]

        assert calls
        assert calls[0]["command"] == ["ipconfig"]
        assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW

    def test_health_endpoint_caches_lan_ip_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module

        calls = 0

        def _fake_detect_lan_ip() -> str:
            nonlocal calls
            calls += 1
            return "192.168.1.7"

        monkeypatch.setattr(app_module, "_detect_lan_ip", _fake_detect_lan_ip)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        assert client.get("/api/health").json()["lan_ip"] == "192.168.1.7"
        assert client.get("/api/health").json()["lan_ip"] == "192.168.1.7"
        assert calls == 1

    def test_bilibili_cookie_endpoint_persists_and_validates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The extension's cookie-sync endpoint must:

        1. Validate the incoming cookie against B 站 nav (not blindly trust).
        2. Persist to data/bilibili_cookie.json AND config.toml [bilibili].cookie.
        3. Reject the request when validation fails (don't clobber a working cookie).

        Uses the real AuthManager but stubs the API client factory so
        we never actually hit api.bilibili.com.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        # Sandboxed config + data dir; OPENBILICLAW_PROJECT_ROOT redirects
        # config.toml + data/ to tmp_path so the test can't touch the
        # developer's real config.
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        # Fake B 站 nav: returns a logged-in response so validation passes.
        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        # Patch the auth-manager default client factory globally — the
        # endpoint constructs its own AuthManager so we can't pass
        # the factory through; we monkeypatch the staticmethod instead.
        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        cookie_value = "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999"
        response = client.post(
            "/api/bilibili/cookie",
            json={
                "cookie": cookie_value,
                "source": "extension",
                "validate_with_bilibili": True,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["authenticated"] is True
        assert body["username"] == "test_user"
        assert body["user_id"] == 12345

        # Side effect 1: data/bilibili_cookie.json got written.
        cookie_file = tmp_path / "data" / "bilibili_cookie.json"
        assert cookie_file.exists()
        import json

        assert json.loads(cookie_file.read_text())["cookie"] == cookie_value

        # Side effect 2: config.toml [bilibili].cookie mirrors the cookie.
        config_text = (tmp_path / "config.toml").read_text()
        assert cookie_value in config_text

    def test_bilibili_cookie_sync_restarts_background_tasks_after_rebuild(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cookie hot-reload must restart refresh loops after cancelling them.

        ``RuntimeContext.rebuild_from_config`` cancels tracked background tasks
        before replacing runtime components.  The cookie endpoint therefore
        must call ``restart_background_tasks`` too, otherwise the refresh loop
        that drives XHS / Douyin producers stays stopped after cookie sync.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        calls: list[str] = []

        async def _fake_rebuild(self: RuntimeContext, config: object) -> None:
            calls.append("rebuild")

        async def _fake_restart(self: RuntimeContext, app: object) -> None:
            calls.append("restart")

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )
        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _fake_restart)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        with TestClient(app) as client:
            calls.clear()
            response = client.post(
                "/api/bilibili/cookie",
                json={
                    "cookie": "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999",
                    "source": "extension",
                    "validate_with_bilibili": True,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert calls == ["rebuild", "restart"]

    def test_bilibili_cookie_sync_skips_hot_reload_when_cookie_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Repeated extension sync for the same cookie must be idempotent."""
        from fastapi.testclient import TestClient

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        cookie_value = "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999"
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        config = Config()
        config.bilibili.cookie = cookie_value
        save_config(config, tmp_path / "config.toml")
        AuthManager(tmp_path / "data").set_cookie(cookie_value)

        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        calls: list[str] = []

        async def _fake_rebuild(self: RuntimeContext, config: object) -> None:
            calls.append("rebuild")

        async def _fake_restart(self: RuntimeContext, app: object) -> None:
            calls.append("restart")

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )
        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _fake_restart)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        with TestClient(app) as client:
            calls.clear()
            response = client.post(
                "/api/bilibili/cookie",
                json={
                    "cookie": cookie_value,
                    "source": "extension",
                    "validate_with_bilibili": True,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert calls == []

    def test_bilibili_cookie_endpoint_rejects_invalid_cookie(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When B 站 nav says the cookie isn't logged in, do NOT persist."""
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        class _FakeNavLoggedOut:
            is_login = False
            uname = ""
            mid = 0

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                pass

            async def get_nav_info(self) -> _FakeNavLoggedOut:
                return _FakeNavLoggedOut()

            async def close(self) -> None:
                pass

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/bilibili/cookie",
            json={
                "cookie": "SESSDATA=expired; bili_jct=stale",
                "validate_with_bilibili": True,
            },
        )

        body = response.json()
        assert body["ok"] is False
        assert body["authenticated"] is False
        # No file written (because validation failed before persistence).
        assert not (tmp_path / "data" / "bilibili_cookie.json").exists()

    def test_douyin_cookie_endpoint_persists_cookie_without_config_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import json

        from fastapi.testclient import TestClient

        from openbiliclaw.api.source_auth import verify
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        # Douyin cookies are now live-checked before they land — the endpoint
        # used to store whatever arrived, on the strength of a docstring claim
        # that no clean login probe existed (refuted in spec D11). Stub the one
        # shared probe so this test stays offline.
        probed: list[str] = []

        async def _probe(slug, *, cfg, cookie=None, probes=None, record=True):
            probed.append(str(cookie or ""))
            return verify.LiveProbeOutcome(
                slug=slug,
                has_credential=True,
                authenticated=True,
                network_error=False,
                message="stubbed probe",
            )

        monkeypatch.setattr(verify, "run_live_probe", _probe)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        cookie_value = "msToken=abc; ttwid=tw; sessionid=sess"
        response = client.post(
            "/api/sources/dy/cookie",
            json={"cookie": cookie_value, "source": "extension"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["has_cookie"] is True
        assert body["cookie_names"] == ["msToken", "sessionid", "ttwid"]

        cookie_file = tmp_path / "data" / "douyin_cookie.json"
        assert cookie_file.exists()
        payload = json.loads(cookie_file.read_text(encoding="utf-8"))
        assert payload["cookie"] == cookie_value
        assert payload["source"] == "extension"
        assert cookie_value not in (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert probed == [cookie_value]

    def test_events_endpoint_persists_batch(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-persist-click",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1TEST",
                        "title": "测试标题",
                        "timestamp": 1710000000000,
                        "context": {"pageType": "video"},
                        "metadata": {"href": "https://www.bilibili.com/video/BV1TEST"},
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 1
        assert memory.events[0]["event_type"] == "click"
        assert memory.events[0]["url"] == "https://www.bilibili.com/video/BV1TEST"
        assert memory.events[0]["metadata"]["timestamp"] == 1710000000000
        # A legacy payload without source_platform can still be classified by
        # its canonical URL, without treating the compatibility fallback as
        # exact evidence.
        assert memory.events[0]["metadata"]["source_platform"] == "bilibili"
        assert memory.events[0]["source_platform"] == "bilibili"
        assert memory.events[0]["source_confidence"] == "inferred"

    def test_events_endpoint_ignores_pre_init_behavior_events(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeSoulEngine:
            def is_profile_ready(self) -> bool:
                return False

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=FakeSoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-pre-init-click",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1PREINIT",
                        "title": "初始化前不该入库",
                        "timestamp": 1710000000000,
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "accepted": 0,
            "duplicates": 0,
            "receipts": [],
            "rejected": [
                {
                    "index": 0,
                    "type": "click",
                    "reason": "not_initialized",
                }
            ],
        }
        assert memory.events == []

    def test_events_endpoint_preserves_source_platform(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-source-xhs",
                        "type": "click",
                        "url": "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad",
                        "title": "测试笔记",
                        "timestamp": 1710000000000,
                        "source_platform": "xiaohongshu",
                        "context": {"pageType": "note"},
                        "metadata": {"note_id": "69dea966000000001a0280ad"},
                    },
                    {
                        "event_id": "events-source-blank",
                        "type": "scroll",
                        "url": "https://www.xiaohongshu.com/explore",
                        "title": "",
                        "timestamp": 1710000000001,
                        "source_platform": "   ",
                        "context": {"pageType": "home"},
                        "metadata": {},
                    },
                    {
                        "event_id": "events-source-reddit",
                        "type": "favorite",
                        "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/",
                        "title": "Reddit post",
                        "timestamp": 1710000000002,
                        "source_platform": "reddit",
                        "context": {"pageType": "post"},
                        "metadata": {"content_id": "t3_abc123", "post_id": "abc123"},
                    },
                    {
                        "event_id": "events-source-legacy-fallback",
                        "type": "search",
                        "title": "没有来源和 URL 的旧事件",
                        "timestamp": 1710000000003,
                    },
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 4
        assert memory.events[0]["metadata"]["source_platform"] == "xiaohongshu"
        assert memory.events[0]["source_platform"] == "xiaohongshu"
        assert memory.events[0]["content_id"] == "69dea966000000001a0280ad"
        assert memory.events[0]["source_confidence"] == "exact"
        assert memory.events[0]["metadata"]["note_id"] == "69dea966000000001a0280ad"
        # Blank source_platform is inferred from the canonical URL before the
        # compatibility B站 fallback.
        assert memory.events[1]["metadata"]["source_platform"] == "xiaohongshu"
        assert memory.events[1]["source_platform"] == "xiaohongshu"
        assert memory.events[1]["source_confidence"] == "inferred"
        assert memory.events[2]["metadata"]["source_platform"] == "reddit"
        assert memory.events[2]["source_platform"] == "reddit"
        assert memory.events[2]["content_id"] == "t3_abc123"
        assert memory.events[2]["source_confidence"] == "exact"
        assert memory.events[2]["metadata"]["content_id"] == "t3_abc123"
        assert memory.events[2]["metadata"]["post_id"] == "abc123"
        assert memory.events[3]["metadata"]["source_platform"] == "bilibili"
        assert memory.events[3]["source_platform"] == "bilibili"
        assert memory.events[3]["source_confidence"] == "legacy_unknown"

    def test_events_endpoint_preserves_top_level_dwell_fields(self) -> None:
        """v0.3.x event-satisfaction: top-level watch_seconds /
        video_duration_seconds get folded into metadata so the storage
        classifier sees them."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-top-level-dwell",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BVquick",
                        "title": "标题党",
                        "timestamp": 1710000000000,
                        "watch_seconds": 2,
                        "video_duration_seconds": 120,
                    }
                ]
            },
        )

        assert response.status_code == 200
        ev = memory.events[0]
        assert ev["metadata"]["watch_seconds"] == 2
        assert ev["metadata"]["video_duration_seconds"] == 120

    def test_events_endpoint_preserves_metadata_dwell_fields(self) -> None:
        """Same fields also accepted when the extension nests them in metadata."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-metadata-dwell",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BVdeep",
                        "title": "深度教程",
                        "timestamp": 1710000000000,
                        "metadata": {"watch_seconds": 600, "video_duration_seconds": 700},
                    }
                ]
            },
        )

        assert response.status_code == 200
        ev = memory.events[0]
        assert ev["metadata"]["watch_seconds"] == 600
        assert ev["metadata"]["video_duration_seconds"] == 700

    def test_events_endpoint_projects_v2ex_engaged_topic_once_for_active_identity(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.sources.v2ex_affinity import V2EXNodeAffinityStore
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / "v2ex-dwell.db")
        database.initialize()
        database.activate_v2ex_profile_identity("alice")
        database.set_v2ex_browser_identity("alice")
        memory = MemoryManager(tmp_path / "v2ex-dwell", database=database)
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=_ReadySoulEngine(),
        )
        client = TestClient(app)
        payload = {
            "events": [
                {
                    "event_id": "v2ex-engaged-topic-42",
                    "type": "click",
                    "url": "https://www.v2ex.com/t/42",
                    "title": "Local-first agents",
                    "timestamp": 1786310400000,
                    "source_platform": "v2ex",
                    "context": {"pageType": "topic"},
                    "metadata": {
                        "content_id": "42",
                        "topic_id": "42",
                        "node_name": "programmer",
                        "node_title": "程序员",
                        "dwell_source": "content_page_exit",
                        "watch_seconds": 45,
                    },
                }
            ]
        }

        first = client.post("/api/events", json=payload)
        second = client.post("/api/events", json=payload)

        assert first.status_code == 200, first.text
        assert first.json()["receipts"][0]["inserted"] is True
        assert second.status_code == 200, second.text
        assert second.json()["receipts"][0]["duplicate"] is True
        scores = V2EXNodeAffinityStore(database).scores(username="alice")
        assert scores[0]["engaged_view_count"] == 1
        assert scores[0]["score"] == 0.3
        assert database.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

    def test_events_endpoint_normalizes_dislike_to_feedback(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-normalize-dislike",
                        "type": "dislike",
                        "url": "https://www.bilibili.com/video/BV1TEST",
                        "title": "不想看",
                        "timestamp": 1710000000000,
                        "context": {"pageType": "video"},
                        "metadata": {"bvid": "BV1TEST"},
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 1
        assert response.json()["rejected"] == []
        assert memory.events[0]["event_type"] == "feedback"
        assert memory.events[0]["metadata"]["feedback_type"] == "dislike"
        assert memory.events[0]["metadata"]["reaction"] == "thumbs_down"

    def test_unified_line_gives_extension_feedback_one_cursor_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every committed batch only wakes the durable owners; HTTP never ingests."""
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app

        schedules = 0

        class FakeFeedbackBatchScheduler:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def schedule(self) -> None:
                nonlocal schedules
                schedules += 1

            async def close(self) -> None:
                return None

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class SpyPipeline:
            def __init__(self) -> None:
                self.batches: list[list[object]] = []

            async def ingest_batch(self, signals: list[object]) -> object:
                self.batches.append(signals)
                return object()

        class FakeSoulEngine:
            unified_interest_line_enabled = True

            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

            def is_profile_ready(self) -> bool:
                return True

        monkeypatch.setattr(api_app, "FeedbackBatchScheduler", FakeFeedbackBatchScheduler)
        memory = FakeMemoryManager()
        soul = FakeSoulEngine()
        client = TestClient(create_app(memory_manager=memory, soul_engine=soul))

        dislike = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-owner-dislike",
                        "type": "dislike",
                        "url": "https://www.bilibili.com/video/BV1OWNER",
                        "title": "交给 durable cursor",
                        "timestamp": 1710000000000,
                    }
                ]
            },
        )
        retraction = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-owner-retraction",
                        "type": "feedback",
                        "url": "https://www.bilibili.com/video/BV1OWNER",
                        "title": "撤回仍走折价路径",
                        "timestamp": 1710000000001,
                        "metadata": {
                            "feedback_type": "retraction",
                            "retracted_action": "like",
                        },
                    }
                ]
            },
        )
        unrelated = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-owner-unrelated",
                        "type": "feedback",
                        "title": "假设结算不是内容反馈",
                        "timestamp": 1710000000002,
                        "metadata": {"hypothesis": "可能喜欢建筑", "signal": "like"},
                    }
                ]
            },
        )

        assert dislike.status_code == 200
        assert retraction.status_code == 200
        assert unrelated.status_code == 200
        assert schedules == 3
        assert soul.pipeline.batches == []

    def test_events_endpoint_rejects_bad_event_without_failing_batch(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                if event["event_type"] == "unsupported":
                    raise ValueError("Unsupported event type: unsupported")
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, soul_engine=_ReadySoulEngine())
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-partial-good",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1OK",
                        "title": "正常事件",
                        "timestamp": 1710000000000,
                    },
                    {
                        "event_id": "events-partial-bad",
                        "type": "unsupported",
                        "url": "https://www.bilibili.com/video/BV1BAD",
                        "title": "未知事件",
                        "timestamp": 1710000000001,
                    },
                ]
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 1
        assert body["rejected"] == [
            {
                "index": 1,
                "type": "unsupported",
                "reason": "Unsupported event type: unsupported",
            }
        ]
        assert [event["event_type"] for event in memory.events] == ["click"]

    def test_events_endpoint_wakes_durable_owner_without_direct_pipeline_write(
        self,
    ) -> None:
        from fastapi.testclient import TestClient

        calls: list[str] = []

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                if event["event_type"] == "unsupported":
                    raise ValueError("Unsupported event type: unsupported")
                self.events.append(event)

        class SpyPipeline:
            def __init__(self) -> None:
                self.batches: list[list[object]] = []

            async def ingest_batch(self, signals: list[object]) -> object:
                calls.append("pipeline")
                self.batches.append(signals)
                return object()

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

            def is_profile_ready(self) -> bool:
                return True

        class FakeRuntimeController:
            async def request_replenishment(
                self,
                *,
                reason: str,
                force: bool = False,
            ) -> dict[str, object]:
                calls.append(f"request:{reason}:{force}")
                return {"accepted": True, "state": "queued", "reason": reason}

            async def refresh_after_event_ingest(self) -> dict[str, object]:
                raise AssertionError("/api/events should not directly refresh after ingest")

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=memory,
            soul_engine=soul_engine,
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-durable-owner-good",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1OK",
                        "title": "正常事件",
                        "timestamp": 1710000000000,
                    },
                    {
                        "event_id": "events-durable-owner-bad",
                        "type": "unsupported",
                        "url": "https://www.bilibili.com/video/BV1BAD",
                        "title": "未知事件",
                        "timestamp": 1710000000001,
                    },
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 1
        assert calls == ["request:event_ingest:False"]
        assert soul_engine.pipeline.batches == []
        assert app.state.feedback_batch_scheduler.status_payload()["event_lane_depth"] == 1

    def test_events_endpoint_leaves_pending_history_to_durable_consumer(
        self,
    ) -> None:
        from fastapi.testclient import TestClient

        calls: list[str] = []

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []
                self.runtime_state: dict[str, object] = {"last_processed_event_id": 10}

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def update_discovery_runtime_state(self, mutator: object) -> dict[str, object]:
                result = mutator(self.runtime_state)  # type: ignore[operator]
                if isinstance(result, dict):
                    self.runtime_state = result
                return dict(self.runtime_state)

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_latest_event_id(self) -> int:
                return 11

            def query_events_since(
                self,
                *,
                after_event_id: int,
                event_types: list[str],
            ) -> list[dict[str, object]]:
                assert after_event_id == 10
                assert "favorite" in event_types
                return [
                    {
                        "id": 11,
                        "event_type": "favorite",
                        "title": "旧 pending 收藏",
                        "metadata": "{}",
                    }
                ]

        class SpyPipeline:
            def __init__(self) -> None:
                self.titles_by_batch: list[list[object]] = []

            async def ingest_batch(self, signals: list[object]) -> object:
                calls.append("pipeline")
                self.titles_by_batch.append([signal.payload["title"] for signal in signals])
                return object()

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

            def is_profile_ready(self) -> bool:
                return True

        class FakeRuntimeController:
            async def refresh_after_event_ingest(self) -> dict[str, object]:
                calls.append("refresh")
                return {"refreshed": False}

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-pending-history-current",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1NOW",
                        "title": "当前点击",
                        "timestamp": 1710000000000,
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 1
        assert calls == ["refresh"]
        assert soul_engine.pipeline.titles_by_batch == []
        assert memory.runtime_state == {"last_processed_event_id": 10}

    def test_events_endpoint_does_not_read_legacy_profile_cursor(
        self,
    ) -> None:
        from fastapi.testclient import TestClient

        queried_after: list[int] = []

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "last_processed_event_id": 15,
                    "last_profile_pipeline_event_id": 10,
                }

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def update_discovery_runtime_state(self, mutator: object) -> dict[str, object]:
                result = mutator(self.runtime_state)  # type: ignore[operator]
                if isinstance(result, dict):
                    self.runtime_state = result
                return dict(self.runtime_state)

            async def propagate_event(self, event: dict[str, object]) -> None:
                return None

        class FakeDatabase:
            def get_latest_event_id(self) -> int:
                return 15

            def query_events_since(
                self,
                *,
                after_event_id: int,
                event_types: list[str],
            ) -> list[dict[str, object]]:
                queried_after.append(after_event_id)
                return [
                    {
                        "id": 11,
                        "event_type": "like",
                        "title": "discovery 已推进但画像未补",
                    }
                ]

        class SpyPipeline:
            def __init__(self) -> None:
                self.titles: list[object] = []

            async def ingest_batch(self, signals: list[object]) -> object:
                self.titles.extend(signal.payload["title"] for signal in signals)
                return object()

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

            def is_profile_ready(self) -> bool:
                return True

        class FakeRuntimeController:
            async def refresh_after_event_ingest(self) -> dict[str, object]:
                return {"refreshed": False}

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "event_id": "events-no-legacy-cursor",
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1NOW",
                        "title": "当前点击",
                        "timestamp": 1710000000000,
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert queried_after == []
        assert soul_engine.pipeline.titles == []
        assert memory.runtime_state["last_profile_pipeline_event_id"] == 10

    @pytest.mark.asyncio
    async def test_events_endpoint_concurrent_writes_do_not_claim_profile_owner(self) -> None:
        import httpx

        batches: list[list[object]] = []

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class SpyPipeline:
            async def ingest_batch(self, signals: list[object]) -> object:
                titles = [signal.payload["title"] for signal in signals]
                batches.append(titles)
                return object()

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

            def is_profile_ready(self) -> bool:
                return True

        class FakeRuntimeController:
            async def refresh_after_event_ingest(self) -> dict[str, object]:
                return {"refreshed": False}

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=object(),
            soul_engine=FakeSoulEngine(),
            runtime_controller=FakeRuntimeController(),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/api/events",
                    json={
                        "events": [
                            {
                                "event_id": "events-concurrent-1",
                                "type": "click",
                                "url": "https://www.bilibili.com/video/BV1NOW",
                                "title": "当前点击 1",
                                "timestamp": 1710000000000,
                            }
                        ]
                    },
                )
            )
            second = asyncio.create_task(
                client.post(
                    "/api/events",
                    json={
                        "events": [
                            {
                                "event_id": "events-concurrent-2",
                                "type": "click",
                                "url": "https://www.bilibili.com/video/BV2NOW",
                                "title": "当前点击 2",
                                "timestamp": 1710000000001,
                            }
                        ]
                    },
                )
            )
            responses = await asyncio.gather(first, second)

        assert [response.status_code for response in responses] == [200, 200]
        assert [response.json()["accepted"] for response in responses] == [1, 1]
        assert batches == []

    def test_extension_e2e_rejects_state_changing_action_without_opt_in(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": ["douyin"], "actions": {"douyin": ["like"]}},
        )

        assert response.status_code == 400
        assert "allow_state_changing" in response.json()["detail"]

    def test_extension_e2e_run_rejects_remote_clients_with_valid_payload(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": ["douyin"], "actions": {"douyin": ["snapshot"]}},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "local_only"

    def test_extension_e2e_result_rejects_remote_clients_with_valid_payload(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/result",
            json={
                "run_id": "e2e-test",
                "token": "good-token",
                "platforms": [
                    {
                        "platform": "douyin",
                        "actions": [
                            {
                                "action": "snapshot",
                                "status": "ok",
                                "detail": "captured",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "local_only"

    def test_extension_e2e_rejects_unknown_platform_via_schema(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": ["youtube"], "actions": {"youtube": ["snapshot"]}},
        )

        assert response.status_code == 422

    def test_extension_e2e_rejects_empty_platforms(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": [], "actions": {}},
        )

        assert response.status_code == 422

    def test_extension_e2e_rejects_empty_action_list(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": ["douyin"], "actions": {"douyin": []}},
        )

        assert response.status_code == 422

    def test_extension_e2e_run_rejects_concurrent_run(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: True
        app.state.extension_e2e_runs["e2e-active"] = SimpleNamespace(token="active")
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={"platforms": ["douyin"], "actions": {"douyin": ["snapshot"]}},
        )

        assert response.status_code == 409
        assert "e2e_run_in_progress" in response.json()["detail"]

    def test_extension_e2e_result_rejects_bad_token(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        app.state.auth_gate.is_trusted_local = lambda request: True
        app.state.extension_e2e_runs["e2e-test"] = SimpleNamespace(token="good-token")
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/result",
            json={
                "run_id": "e2e-test",
                "token": "bad-token",
                "platforms": [],
            },
        )

        assert response.status_code == 403

    def test_match_e2e_event_matches_platform_action_and_uses_event_once(self) -> None:
        from openbiliclaw.api.app import _match_e2e_event

        events = [
            {
                "id": 1,
                "event_type": "click",
                "url": "https://www.douyin.com/video/1",
                "title": "Douyin click",
                "metadata": {"source_platform": "douyin"},
            },
            {
                "id": 2,
                "event_type": "click",
                "url": "https://www.xiaohongshu.com/explore/1",
                "title": "XHS click",
                "metadata": {"source_platform": "xiaohongshu"},
            },
        ]
        used_event_ids: set[int] = set()

        assert _match_e2e_event(
            events,
            platform="douyin",
            action="click",
            used_event_ids=used_event_ids,
        ) == {
            "event_id": 1,
            "event_type": "click",
            "url": "https://www.douyin.com/video/1",
            "title": "Douyin click",
        }
        assert used_event_ids == {1}
        assert (
            _match_e2e_event(
                events,
                platform="twitter",
                action="click",
                used_event_ids=used_event_ids,
            )
            is None
        )
        assert (
            _match_e2e_event(
                events,
                platform="douyin",
                action="click",
                used_event_ids=used_event_ids,
            )
            is None
        )

    def test_match_e2e_event_does_not_treat_click_as_like(self) -> None:
        from openbiliclaw.api.app import _match_e2e_event

        events = [
            {
                "id": 1,
                "event_type": "click",
                "url": "https://www.douyin.com/video/1",
                "title": "Douyin click",
                "metadata": {"source_platform": "douyin"},
            }
        ]
        used_event_ids: set[int] = set()

        assert (
            _match_e2e_event(
                events,
                platform="douyin",
                action="like",
                used_event_ids=used_event_ids,
            )
            is None
        )
        assert used_event_ids == set()

    def test_match_e2e_event_separates_safe_share_from_repost(self) -> None:
        from openbiliclaw.api.app import _match_e2e_event

        events = [
            {
                "id": 1,
                "event_type": "share",
                "url": "https://x.com/example/status/1",
                "title": "X repost mutation",
                "metadata": {"source_platform": "twitter"},
            },
            {
                "id": 2,
                "event_type": "click",
                "url": "https://x.com/example/status/2",
                "title": "X share control click",
                "metadata": {"source_platform": "twitter"},
            },
        ]

        assert _match_e2e_event(
            events,
            platform="twitter",
            action="share",
            used_event_ids=set(),
        ) == {
            "event_id": 2,
            "event_type": "click",
            "url": "https://x.com/example/status/2",
            "title": "X share control click",
        }
        assert _match_e2e_event(
            events,
            platform="twitter",
            action="repost",
            used_event_ids=set(),
        ) == {
            "event_id": 1,
            "event_type": "share",
            "url": "https://x.com/example/status/1",
            "title": "X repost mutation",
        }

    def test_extension_e2e_run_publishes_runtime_event_and_returns_timeout_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as app_module

        async def _instant_timeout(awaitable: object, timeout: float) -> object:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return True

        class FakeMemoryManager:
            def query_events(self, **_kwargs: object) -> list[dict[str, object]]:
                return []

        monkeypatch.setattr(app_module.asyncio, "wait_for", _instant_timeout)
        hub = FakeEventHub()
        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={
                "platforms": ["douyin"],
                "actions": {"douyin": ["snapshot", "scroll"]},
                "timeout_seconds": 5,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"].startswith("e2e-")
        assert body["status"] == "timeout"
        assert body["platforms"][0]["platform"] == "douyin"
        assert [item["action"] for item in body["platforms"][0]["actions"]] == [
            "snapshot",
            "scroll",
        ]
        assert hub.events
        event = hub.events[0]
        assert event["type"] == "extension_e2e_run"
        assert event["platforms"] == ["douyin"]
        assert event["actions"] == {"douyin": ["snapshot", "scroll"]}
        assert event["run_id"] == body["run_id"]
        assert isinstance(event["token"], str) and event["token"]

    def test_extension_e2e_run_fails_fast_when_runtime_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as app_module

        async def _unexpected_wait(awaitable: object, timeout: float) -> object:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise AssertionError("extension e2e run should not wait without subscribers")

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return False

        class FakeMemoryManager:
            def query_events(self, **_kwargs: object) -> list[dict[str, object]]:
                return []

        monkeypatch.setattr(app_module.asyncio, "wait_for", _unexpected_wait)
        hub = FakeEventHub()
        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post(
            "/api/extension/e2e/run",
            json={
                "platforms": ["douyin"],
                "actions": {"douyin": ["snapshot"]},
                "timeout_seconds": 5,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert "extension_runtime_unavailable" in body["error"]
        assert hub.events
        assert app.state.extension_e2e_runs == {}

    def test_events_endpoint_handles_extension_cors_preflight(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.options(
            "/api/events",
            headers={
                "Origin": "chrome-extension://alolnnalhpddolgelnhfkmmiehhcmokl",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
        assert "POST" in response.headers["access-control-allow-methods"]

    def test_recommendations_endpoint_returns_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                # v0.3.18: the endpoint pulls 2x the visible window so
                # the per-franchise cap still has 20 survivors after
                # dropping over-represented IPs.
                assert limit == 40
                return [
                    {
                        "id": 7,
                        "bvid": "BV1REC",
                        "title": "讲透城市与建筑",
                        "up_name": "城市观察局",
                        "cover_url": "https://i0.hdslb.com/bfs/archive/cover.jpg",
                        "expression": "这条很对你最近的状态。",
                        "topic": "你最近那股想把结构想透的劲头",
                        "presented": 1,
                        "franchise_key": "",  # general-interest content
                        "duration": 3723,
                        "view_count": 12000,
                        "like_count": 3400,
                        "danmaku_count": 890,
                        "up_mid": 112233,
                        "published_at": "2026-07-08T06:30:00Z",
                        "published_label": "3 days ago",
                    }
                ]

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == 7
        assert data["items"][0]["title"] == "讲透城市与建筑"
        assert data["items"][0]["cover_url"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"
        assert data["items"][0]["duration"] == 3723
        assert data["items"][0]["view_count"] == 12000
        assert data["items"][0]["like_count"] == 3400
        assert data["items"][0]["danmaku_count"] == 890
        assert data["items"][0]["up_mid"] == 112233
        assert_publication(data["items"][0])

    def test_recommendations_endpoint_coalesces_immediate_duplicate_reads(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.reads = 0

            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                assert limit == 40
                assert exclude_processed is True
                self.reads += 1
                return []

        database = FakeDatabase()
        app = create_app(database=database)
        client = TestClient(app)

        assert client.get("/api/recommendations").status_code == 200
        assert client.get("/api/recommendations").status_code == 200

        assert database.reads == 1

    def test_recommendation_snapshot_cache_expires_at_temporal_boundary(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)
        rows = [
            {
                "bvid": "BV-NEAR-EXPIRY",
                "published_at": (now - timedelta(days=3) + timedelta(milliseconds=200)).isoformat(),
                "temporal_class": "breaking",
                "temporal_confidence": 0.95,
            }
        ]

        eligible, expires_at = _recommendation_snapshot_rows_and_expiry(
            rows,
            now=now,
            monotonic_now=100.0,
        )
        stale, _ = _recommendation_snapshot_rows_and_expiry(
            rows,
            now=now + timedelta(milliseconds=200),
            monotonic_now=100.2,
        )

        assert eligible == rows
        assert expires_at == pytest.approx(100.2)
        assert stale == []

    def test_recommendation_snapshot_anchors_monotonic_before_wall_clock(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Clock-read latency must shorten, never extend, the final TTL window."""
        import openbiliclaw.api.app as app_module

        wall_now = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)
        calls: list[str] = []

        def _monotonic() -> float:
            calls.append("monotonic")
            return 100.0

        class _WallClock:
            @classmethod
            def now(cls, tz: object) -> datetime:
                assert calls == ["monotonic"]
                calls.append("wall")
                return wall_now

        monkeypatch.setattr(app_module.time, "monotonic", _monotonic)
        monkeypatch.setattr(app_module, "datetime", _WallClock)

        rows = [
            {
                "bvid": "BV-CLOCK-ORDER",
                "published_at": (
                    wall_now - timedelta(days=3) + timedelta(milliseconds=200)
                ).isoformat(),
                "temporal_class": "breaking",
                "temporal_confidence": 0.95,
            }
        ]
        eligible, expires_at = _recommendation_snapshot_rows_and_expiry(rows)

        assert calls == ["monotonic", "wall"]
        assert eligible == rows
        assert expires_at == pytest.approx(100.2)

    def test_recommendations_cache_rechecks_latest_dislikes_immediately(self) -> None:
        """A preference write must invalidate visibility even inside the 1s TTL."""
        from fastapi.testclient import TestClient

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.disliked_topics: list[str] = []

            def get_effective_disliked_topics(self) -> list[str]:
                return list(self.disliked_topics)

        class FakeDatabase:
            def __init__(self) -> None:
                self.reads = 0

            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                assert limit == 40
                assert exclude_processed is True
                self.reads += 1
                return [
                    {
                        "id": 1,
                        "bvid": "BV-REHAB",
                        "title": "腰椎保护实操训练",
                        "topic": "运动康复",
                        "topic_group": "运动康复",
                        "confidence": 0.90,
                        "franchise_key": "",
                    },
                    {
                        "id": 2,
                        "bvid": "BV-SQLITE",
                        "title": "SQLite 查询优化",
                        "topic": "数据库",
                        "topic_group": "数据库",
                        "confidence": 0.90,
                        "franchise_key": "",
                    },
                ]

        soul = FakeSoulEngine()
        database = FakeDatabase()
        app = create_app(database=database, soul_engine=soul)
        client = TestClient(app)

        first = client.get("/api/recommendations")
        soul.disliked_topics = ["运动康复"]
        second = client.get("/api/recommendations")

        assert [item["bvid"] for item in first.json()["items"]] == [
            "BV-REHAB",
            "BV-SQLITE",
        ]
        assert [item["bvid"] for item in second.json()["items"]] == ["BV-SQLITE"]
        assert database.reads == 2

    def test_recommendations_endpoint_caps_same_franchise(self) -> None:
        """End-to-end: when the DB returns 5 同 IP rows in the
        franchise_key column, the API trims down to ``max_per_franchise=2``
        before serving."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                # Five 原神 rows + one 番茄炒蛋. Without the franchise
                # cap, the response would carry all 5 原神; with cap=2,
                # only 2 survive.
                base: list[dict[str, object]] = []
                for i in range(5):
                    base.append(
                        {
                            "id": i,
                            "bvid": f"BV原神{i}",
                            "title": f"原神 番外 {i}",
                            "up_name": "某 UP",
                            "cover_url": "",
                            "expression": "",
                            "topic": "游戏",
                            "presented": 0,
                            "franchise_key": "原神",
                        }
                    )
                base.append(
                    {
                        "id": 99,
                        "bvid": "BV番茄",
                        "title": "番茄炒蛋 5 分钟",
                        "up_name": "美食 UP",
                        "cover_url": "",
                        "expression": "",
                        "topic": "美食",
                        "presented": 0,
                        "franchise_key": "",
                    }
                )
                return base

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")
        assert response.status_code == 200
        items = response.json()["items"]
        franchise_count = sum(1 for it in items if str(it["title"]).startswith("原神"))
        # 5 同 IP 行被砍到 2，番茄炒蛋（无 franchise）仍保留
        assert franchise_count == 2
        assert any(it["title"].startswith("番茄炒蛋") for it in items)

    def test_recommendations_endpoint_filters_low_confidence_history(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                assert limit == 40
                return [
                    {
                        "id": 1,
                        "bvid": "BVLOW",
                        "title": "低分历史推荐",
                        "up_name": "UP",
                        "cover_url": "",
                        "expression": "",
                        "topic": "",
                        "presented": 0,
                        "confidence": 0.30,
                        "franchise_key": "",
                    },
                    {
                        "id": 2,
                        "bvid": "BVHIGH",
                        "title": "达标历史推荐",
                        "up_name": "UP",
                        "cover_url": "",
                        "expression": "",
                        "topic": "",
                        "presented": 0,
                        "confidence": 0.83,
                        "franchise_key": "",
                    },
                ]

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")

        assert response.status_code == 200
        assert [item["bvid"] for item in response.json()["items"]] == ["BVHIGH"]

    def test_runtime_status_endpoint_returns_runtime_summary(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 3,
                    "last_refresh_at": "2026-03-10T12:00:00",
                    "last_notification_at": "2026-03-10T12:30:00",
                    "unread_count": 2,
                    "pool_available_count": 28,
                    "pool_target_count": 30,
                    "last_discovered_count": 14,
                    "last_replenished_count": 6,
                    "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
                }

        class FakeAccountSyncService:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "last_account_sync_at": "2026-03-14T18:00:00+00:00",
                    "last_account_sync_error": "",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
            account_sync_service=FakeAccountSyncService(),
        )
        client = TestClient(app)

        response = client.get("/api/runtime-status")

        assert response.status_code == 200
        assert response.json() == {
            "initialized": True,
            "recommendation_count": 5,
            "pending_signal_events": 3,
            "last_refresh_at": "2026-03-10T12:00:00",
            "last_notification_at": "2026-03-10T12:30:00",
            "unread_count": 2,
            "pool_available_count": 28,
            "pool_raw_count": 0,
            "pool_pending_count": 0,
            "pool_pending_eval_count": 0,
            "pool_evaluated_pending_count": 0,
            "pool_target_count": 30,
            "candidate_eval_state": "idle",
            "candidate_eval_workers": 0,
            "candidate_eval_in_flight": 0,
            "candidate_eval_pending": 0,
            "candidate_eval_backoff_until": 0.0,
            "candidate_eval_last_error": "",
            "candidate_eval_last_batch_seconds": 0.0,
            "candidate_eval_last_cached": 0,
            "candidate_eval_last_rejected": 0,
            "expression_pending_count": 0,
            "expression_batch_state": "idle",
            "expression_batch_deadline": 0.0,
            "expression_last_completed": 0,
            "expression_last_error": "",
            "llm_total_concurrency": 0,
            "llm_background_concurrency": 0,
            "llm_total_active": 0,
            "llm_total_waiting": 0,
            "llm_background_active": 0,
            "llm_background_waiting": 0,
            "llm_refill_active": 0,
            "llm_refill_waiting": 0,
            "llm_maintenance_active": 0,
            "llm_maintenance_waiting": 0,
            "llm_refill_priority_active": False,
            "inventory_priority_state": "healthy",
            "last_discovered_count": 14,
            "last_replenished_count": 6,
            "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
            "manual_refresh_state": "idle",
            "manual_refresh_message": "",
            "last_account_sync_at": "2026-03-14T18:00:00+00:00",
            "last_account_sync_error": "",
            "last_account_sync_error_kind": "",
            "last_account_sync_issues": [],
            # Display copy is rendered backend-side so every surface shows the
            # same sentence; the raw error above stays for diagnostics.
            "last_account_sync_message": "",
            "last_account_sync_severity": "",
            "event_lane_depth": 0,
            "event_lane_active": False,
            "event_lane_paused": False,
            "event_lane_last_error": "",
            "event_lane_processed": 0,
            "chat_reply_depth": 0,
            "chat_reply_active": False,
            "chat_reply_last_error": "",
            "chat_reply_processed": 0,
            "image_fetch_active": 0,
            "image_fetch_waiting": 0,
            "image_fetch_inflight_keys": 0,
            "image_fetch_upstream_started": 0,
            "image_fetch_singleflight_joins": 0,
            "image_fetch_peak_active": 0,
            "image_fetch_peak_background": 0,
            "auto_update_enabled": False,
            # The shared fixture points OPENBILICLAW_PROJECT_ROOT at a tmp dir
            # without .git, so the real AutoUpdateService reports unsupported.
            "install_mode": "unsupported",
            "current_version": __version__,
            "latest_remote_version": "",
            "last_update_check_at": "",
            "last_update_error": "",
            "backend_update_state": "disabled",
            "backend_update_reason": "none",
        }

    def test_runtime_status_endpoint_surfaces_account_sync_error_kind(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 0,
                    "pending_signal_events": 0,
                    "unread_count": 0,
                }

        class FakeAccountSyncService:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "last_account_sync_at": "2026-03-14T18:00:00+00:00",
                    "last_account_sync_error": "logged out",
                    "last_account_sync_error_kind": "auth_expired",
                    "last_account_sync_issues": [
                        {"stage": "bilibili_history", "kind": "auth_expired"}
                    ],
                    "last_account_sync_message": "B 站登录已失效，请重新登录。",
                    "last_account_sync_severity": "warning",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
            account_sync_service=FakeAccountSyncService(),
        )
        client = TestClient(app)

        response = client.get("/api/runtime-status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["last_account_sync_error_kind"] == "auth_expired"
        assert payload["last_account_sync_issues"] == [
            {"stage": "bilibili_history", "kind": "auth_expired"}
        ]
        # Surfaces render this instead of the provider's raw English error.
        assert payload["last_account_sync_message"] == "B 站登录已失效，请重新登录。"
        assert payload["last_account_sync_severity"] == "warning"

    def test_runtime_status_endpoint_includes_backend_update_summary(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 3,
                    "unread_count": 2,
                }

        class FakeAutoUpdateService:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "auto_update_enabled": False,
                    "current_version": "0.3.91",
                    "latest_remote_version": "0.3.92",
                    "last_update_check_at": "2026-05-31T12:00:00+00:00",
                    "last_update_error": "",
                    "backend_update_state": "update_available",
                    "backend_update_reason": "none",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
            auto_update_service=FakeAutoUpdateService(),
        )
        client = TestClient(app)

        response = client.get("/api/runtime-status")

        assert response.status_code == 200
        body = response.json()
        assert body["auto_update_enabled"] is False
        assert body["current_version"] == "0.3.91"
        assert body["latest_remote_version"] == "0.3.92"
        assert body["last_update_check_at"] == "2026-05-31T12:00:00+00:00"
        assert body["last_update_error"] == ""
        assert body["backend_update_state"] == "update_available"
        assert body["backend_update_reason"] == "none"

    def test_update_status_returns_backend_only_and_ignores_extension_metadata(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def get_update_status(self) -> dict[str, object]:
                return {
                    "state": "update_available",
                    "auto_update_enabled": False,
                    "current_version": "0.3.91",
                    "latest_version": "0.3.92",
                    "latest_tag": "backend-v0.3.92",
                    "last_check_at": "2026-05-31T12:00:00+00:00",
                    "last_error": "",
                    "reason": "none",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=FakeAutoUpdateService(),
        )
        client = TestClient(app)

        response = client.get(
            "/api/update-status?extension_version=0.3.92&extension_family=chrome",
            headers={
                "X-OpenBiliClaw-Extension-Version": "0.3.92",
                "X-OpenBiliClaw-Extension-Family": "chrome",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "backend": {
                "state": "update_available",
                "auto_update_enabled": False,
                "install_mode": "",
                "current_version": "0.3.91",
                "latest_version": "0.3.92",
                "latest_tag": "backend-v0.3.92",
                "last_check_at": "2026-05-31T12:00:00+00:00",
                "last_error": "",
                "reason": "none",
            }
        }

    def test_update_check_ignores_legacy_extension_metadata(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def __init__(self) -> None:
                self.calls = 0

            async def check_now(self) -> dict[str, object]:
                self.calls += 1
                return {
                    "state": "up_to_date",
                    "auto_update_enabled": False,
                    "current_version": "0.3.92",
                    "latest_version": "0.3.92",
                    "latest_tag": "backend-v0.3.92",
                    "last_check_at": "2026-05-31T12:00:00+00:00",
                    "last_error": "",
                    "reason": "none",
                }

        service = FakeAutoUpdateService()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=service,
        )
        client = TestClient(app)

        response = client.post(
            "/api/update/check?extension_version=0.3.92&extension_family=firefox",
            headers={"X-OpenBiliClaw-Extension-Version": "0.3.92"},
            json={"include_backend": True, "include_extension": True},
        )

        assert response.status_code == 200
        assert service.calls == 1
        assert response.json()["backend"]["state"] == "up_to_date"
        assert "extension" not in response.json()

    def test_update_apply_validates_backend_only_target_before_lock(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def __init__(self) -> None:
                self.apply_calls: list[dict[str, object]] = []

            async def request_apply(self, *, tag: str = "") -> tuple[int, dict[str, object]]:
                self.apply_calls.append({"tag": tag})
                return (
                    202,
                    {
                        "target": "backend",
                        "state": "applying",
                        "reason": "none",
                        "accepted": True,
                        "observe_via": "runtime-stream",
                    },
                )

        service = FakeAutoUpdateService()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=service,
        )
        client = TestClient(app)

        accepted = client.post(
            "/api/update/apply?extension_version=0.3.92",
            json={"target": "backend", "tag": "backend-v0.3.92"},
        )
        rejected = client.post("/api/update/apply", json={"target": "extension"})

        assert accepted.status_code == 202
        assert accepted.json()["state"] == "applying"
        assert service.apply_calls == [{"tag": "backend-v0.3.92"}]
        assert rejected.status_code == 422
        assert service.apply_calls == [{"tag": "backend-v0.3.92"}]

    def test_runtime_stream_websocket_receives_published_events(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream") as websocket:
            asyncio.run(hub.publish({"type": "refresh.started", "message": "开始给你补候选了"}))
            assert websocket.receive_json() == {
                "type": "refresh.started",
                "message": "开始给你补候选了",
            }

    def test_runtime_stream_sends_idle_heartbeat(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        monkeypatch.setattr(
            "openbiliclaw.api.app._RUNTIME_STREAM_HEARTBEAT_SECONDS",
            0.01,
        )
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=RuntimeEventHub(),
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream") as websocket:
            heartbeat = websocket.receive_json()

        assert heartbeat["type"] == "runtime.heartbeat"
        assert heartbeat["sent_at"]

    def test_extension_reload_reports_runtime_stream_delivery(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        assert client.post("/api/extension/reload").json() == {
            "ok": True,
            "delivered": False,
        }
        with client.websocket_connect("/api/runtime-stream") as websocket:
            assert client.post("/api/extension/reload").json() == {
                "ok": True,
                "delivered": True,
            }
            assert websocket.receive_json() == {
                "type": "extension_reload",
                "source": "dev",
            }

    def test_runtime_stream_accepts_and_discards_extension_metadata(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect(
            "/api/runtime-stream?extension_version=0.3.92&extension_family=chrome"
        ) as websocket:
            websocket.send_json(
                {
                    "type": "extension_metadata",
                    "extension_version": "0.3.92",
                    "extension_family": "chrome",
                }
            )
            asyncio.run(
                hub.publish(
                    {
                        "type": "backend_update_available",
                        "latest_tag": "backend-v0.3.92",
                    }
                )
            )
            assert websocket.receive_json() == {
                "type": "backend_update_available",
                "latest_tag": "backend-v0.3.92",
            }

    def test_runtime_stream_websocket_updates_shared_presence(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream"):
            _wait_for_presence_count(ctx, 1)

        _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_websocket_keeps_presence_for_second_client(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream"):
            _wait_for_presence_count(ctx, 1)
            with client.websocket_connect("/api/runtime-stream"):
                _wait_for_presence_count(ctx, 2)
            _wait_for_presence_count(ctx, 1)
            assert ctx.presence.is_present(grace_seconds=1) is True

        _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_idle_disconnect_decrements_presence_promptly(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream") as websocket:
            _wait_for_presence_count(ctx, 1)
            websocket.close()
            _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_requests_cookie_sync_for_background_client(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config
        from openbiliclaw.runtime.events import RuntimeEventHub

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        cfg = Config()
        cfg.sources.twitter.enabled = True
        save_config(cfg, tmp_path / "config.toml")

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream?client=background") as websocket:
            assert websocket.receive_json() == {
                "type": "xhs_login_state_sync_requested",
                "reason": "runtime_connected",
                "source": "runtime-stream",
            }
            assert websocket.receive_json() == {
                "type": "zhihu_login_state_sync_requested",
                "reason": "runtime_connected",
                "source": "runtime-stream",
            }
            assert websocket.receive_json() == {
                "type": "x_cookie_sync_requested",
                "reason": "runtime_connected",
                "source": "runtime-stream",
            }
            assert websocket.receive_json() == {
                "type": "bilibili_cookie_sync_requested",
                "reason": "missing_cookie",
                "source": "runtime-stream",
            }

    def test_runtime_stream_requests_reddit_cookie_sync_for_background_client(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config
        from openbiliclaw.runtime.events import RuntimeEventHub

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        cfg = Config()
        cfg.bilibili.cookie = "SESSDATA=bili; bili_jct=jct; DedeUserID=1"
        cfg.sources.reddit.enabled = True
        cfg.sources.reddit.backend = "rdt"
        save_config(cfg, tmp_path / "config.toml")
        monkeypatch.setattr(
            "openbiliclaw.sources.reddit_tasks._rdt_credential_file",
            lambda: tmp_path / "rdt" / "credential.json",
        )

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream?client=background") as websocket:
            assert websocket.receive_json() == {
                "type": "xhs_login_state_sync_requested",
                "reason": "runtime_connected",
                "source": "runtime-stream",
            }
            assert websocket.receive_json() == {
                "type": "zhihu_login_state_sync_requested",
                "reason": "runtime_connected",
                "source": "runtime-stream",
            }
            assert websocket.receive_json() == {
                "type": "reddit_cookie_sync_requested",
                "reason": "missing_cookie",
                "source": "runtime-stream",
            }

    def test_activity_feed_endpoint_returns_live_summary_headline_and_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                assert limit in {10, 20}
                return [
                    {
                        "id": 7,
                        "title": "讲透贸易逆差",
                        "topic": "你最近还挺想把因果链理顺",
                        "expression": "这条会对上你最近那股想把事情想透的劲头。",
                        "created_at": "2026-03-15T10:00:00+08:00",
                        "feedback_type": "comment",
                        "feedback_note": "想看更深一点的。",
                        "feedback_at": "2026-03-15T10:05:00+08:00",
                    }
                ]

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 刚记下了：你最近更吃把因果链讲透的内容。",
                        "created_at": "2026-03-15T10:10:00+08:00",
                    }
                ]

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 2,
                    "last_refresh_at": "2026-03-15T10:06:00+08:00",
                    "last_notification_at": "",
                    "unread_count": 1,
                    "pool_available_count": 42,
                    "pool_target_count": 30,
                    "last_replenished_count": 6,
                    "recent_pool_topics": ["国际时事", "宏观经济"],
                    "manual_refresh_state": "running",
                    "manual_refresh_message": "正在给你补候选…",
                }

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.get("/api/activity-feed")

        assert response.status_code == 200
        data = response.json()
        assert data["live_summary"] == "正在给你补候选…"
        assert data["headline"] == "阿B 刚记下了：你最近更吃把因果链讲透的内容。"
        assert data["items"][0]["kind"] == "interest_added"
        assert any(item["kind"] == "feedback" for item in data["items"])
        assert any(item["kind"] == "pool_update" for item in data["items"])

    def test_activity_feed_uninitialized_does_not_surface_pending_signals(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                return []

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return []

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": False,
                    "recommendation_count": 0,
                    "pending_signal_events": 219,
                    "pool_available_count": 0,
                    "pool_pending_count": 0,
                    "last_replenished_count": 0,
                    "last_discovered_count": 0,
                    "manual_refresh_state": "idle",
                    "manual_refresh_message": "",
                }

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.get("/api/activity-feed")

        assert response.status_code == 200
        data = response.json()
        assert "开始初始化" in data["live_summary"]
        assert data["headline"] == data["live_summary"]
        assert "219" not in data["live_summary"]
        assert "记下" not in data["live_summary"]

    def test_activity_feed_pending_signals_are_described_as_discovery_context(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
                return []

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return []

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 7,
                    "pool_available_count": 42,
                    "pool_pending_count": 0,
                    "last_replenished_count": 0,
                    "last_discovered_count": 0,
                    "manual_refresh_state": "idle",
                    "manual_refresh_message": "",
                }

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.get("/api/activity-feed")

        assert response.status_code == 200
        data = response.json()
        assert data["live_summary"] == "阿B 已记下 7 个新动作，下一轮补货会拿来参考。"
        assert "待处理" not in data["live_summary"]

    def test_refresh_recommendations_endpoint_triggers_runtime_refresh(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            async def trigger_manual_refresh(self) -> dict[str, object]:
                return {
                    "accepted": True,
                    "state": "running",
                    "reason": "started",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": True,
            "state": "running",
            "reason": "started",
        }

    def test_refresh_recommendations_endpoint_reports_uninitialized_runtime(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            async def trigger_manual_refresh(self) -> dict[str, object]:
                return {
                    "accepted": False,
                    "state": "idle",
                    "reason": "not_initialized",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": False,
            "state": "idle",
            "reason": "not_initialized",
        }

    def test_refresh_recommendations_endpoint_uses_force_replenishment_request(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.called: list[str] = []

            async def refresh_if_needed(self) -> dict[str, object]:
                self.called.append("normal")
                return {
                    "refreshed": False,
                    "strategies": [],
                    "reason": "below_threshold",
                    "recommendation_count": 0,
                }

            async def request_replenishment(
                self,
                *,
                reason: str,
                force: bool = False,
            ) -> dict[str, object]:
                self.called.append(f"request:{reason}:{force}")
                return {
                    "accepted": True,
                    "state": "running",
                    "reason": reason,
                }

            async def trigger_manual_refresh(self) -> dict[str, object]:
                raise AssertionError("new runtimes should use request_replenishment")

        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": True,
            "state": "running",
            "reason": "manual",
        }
        assert runtime.called == ["request:manual:True"]

    def test_init_completed_runs_forced_replenishment(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bool]] = []

            async def request_replenishment(
                self,
                *,
                reason: str,
                force: bool = False,
            ) -> dict[str, object]:
                self.calls.append((reason, force))
                return {"accepted": True, "state": "running", "reason": reason}

            async def trigger_manual_refresh(self) -> dict[str, object]:
                raise AssertionError("new runtimes should use request_replenishment")

        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post("/api/init-completed", json={})

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert runtime.calls == [("init_completed", True)]

    def test_reshuffle_recommendations_endpoint_returns_immediate_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return True

        class FakeRuntimeController:
            def __init__(self, hub: FakeEventHub) -> None:
                self.event_hub = hub
                self.pool_available_count = 3

            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "pool_available_count": self.pool_available_count,
                    "pool_pending_count": 5,
                    "last_replenished_count": 0,
                    "last_discovered_count": 0,
                }

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeRecommendationEngine:
            def __init__(self, runtime: FakeRuntimeController) -> None:
                self.runtime = runtime

            async def reshuffle_recommendations(
                self,
                *,
                profile: object,
                excluded_bvids: list[str],
                limit: int = 10,
            ) -> list[object]:
                assert profile == {"profile": "ok"}
                assert excluded_bvids == []
                assert limit == 10
                self.runtime.pool_available_count = 0
                from openbiliclaw.discovery.engine import DiscoveredContent
                from openbiliclaw.recommendation.engine import Recommendation

                return [
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV1NEW",
                            title="新的一批",
                            up_name="UPA",
                            cover_url="https://i0.hdslb.com/bfs/archive/new-cover.jpg",
                            duration=3671,
                            view_count=12500,
                            like_count=3400,
                            danmaku_count=890,
                            up_mid=987654321,
                            published_at="2026-07-08T06:30:00Z",
                            published_label="3 days ago",
                        ),
                        recommendation_id=11,
                        expression="先给你捞一条新的。",
                        topic_label="刚补进来的新东西",
                        confidence=0.88,
                        presented=False,
                    ),
                    SimpleNamespace(
                        content=SimpleNamespace(
                            bvid="BV1PLAIN",
                            title="朴素对象",
                            up_name="UPB",
                            cover_url="https://i0.hdslb.com/bfs/archive/plain-cover.jpg",
                        ),
                        recommendation_id=12,
                        expression="没有扩展字段也要稳。",
                        topic_label="兼容旧对象",
                        presented=False,
                    ),
                ]

        hub = FakeEventHub()
        runtime = FakeRuntimeController(hub)
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=FakeRecommendationEngine(runtime),
            runtime_controller=runtime,
        )
        with TestClient(app) as client:
            response = client.post("/api/recommendations/reshuffle")
            deadline = time.monotonic() + 1.0
            while not hub.events and time.monotonic() < deadline:
                time.sleep(0.01)

        assert response.status_code == 200
        assert [event["event_type"] for event in memory.events] == ["reshuffle"]
        assert response.json() == {
            "items": [
                {
                    "id": 11,
                    "bvid": "BV1NEW",
                    "item_key": "bilibili:BV1NEW",
                    "title": "新的一批",
                    "up_name": "UPA",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
                    "expression": "先给你捞一条新的。",
                    "topic_label": "刚补进来的新东西",
                    "presented": False,
                    "feedback_type": "",
                    "content_id": "BV1NEW",
                    "content_url": "https://www.bilibili.com/video/BV1NEW",
                    "source_platform": "bilibili",
                    "content_type": "video",
                    "body_text": "",
                    "duration": 3671,
                    "view_count": 12500,
                    "like_count": 3400,
                    "danmaku_count": 890,
                    "favorite_count": 0,
                    "comment_count": 0,
                    "share_count": 0,
                    "rating_score": 0.0,
                    "rating_count": 0,
                    "source_rank": 0,
                    "up_mid": 987654321,
                    "published_at": "2026-07-08T06:30:00Z",
                    "published_label": "3 days ago",
                },
                {
                    "id": 12,
                    "bvid": "BV1PLAIN",
                    "item_key": "bilibili:BV1PLAIN",
                    "title": "朴素对象",
                    "up_name": "UPB",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/plain-cover.jpg",
                    "expression": "没有扩展字段也要稳。",
                    "topic_label": "兼容旧对象",
                    "presented": False,
                    "feedback_type": "",
                    "content_id": "BV1PLAIN",
                    "content_url": "",
                    "source_platform": "bilibili",
                    "content_type": "video",
                    "body_text": "",
                    "duration": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "danmaku_count": 0,
                    "favorite_count": 0,
                    "comment_count": 0,
                    "share_count": 0,
                    "rating_score": 0.0,
                    "rating_count": 0,
                    "source_rank": 0,
                    "up_mid": 0,
                    "published_at": "",
                    "published_label": "",
                },
            ]
        }
        assert_publication(response.json()["items"][0])
        assert hub.events[-1]["type"] == "refresh.pool_updated"
        assert hub.events[-1]["message"] == "推荐池已同步"
        assert hub.events[-1]["pool_available_count"] == 0
        assert hub.events[-1]["pool_pending_count"] == 5

    def test_reshuffle_with_result_skips_duplicate_api_pool_precheck(self) -> None:
        from fastapi.testclient import TestClient

        counts = {
            "available": 4,
            "raw": 9,
            "pending": 2,
            "pending_eval": 1,
            "evaluated_pending": 1,
        }

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return True

        class FakeDatabase:
            def __init__(self) -> None:
                self.count_calls = 0

            def count_pool_candidates(self, **_kwargs: object) -> int:
                self.count_calls += 1
                return 9

            async def count_pool_readiness_isolated_async(self) -> dict[str, int]:
                return dict(counts)

        class FakeRuntimeController:
            def __init__(self, hub: FakeEventHub) -> None:
                self.event_hub = hub
                self.pool_target_count = 30

            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "pool_available_count": 9,
                    "pool_pending_count": 0,
                }

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.callback: object | None = None

            def set_pool_inventory_commit_callback(self, callback: object) -> None:
                self.callback = callback

            async def reshuffle_recommendations_with_result(
                self,
                *,
                profile: object,
                excluded_bvids: list[str],
                limit: int,
            ) -> object:
                assert profile == {"profile": "ok"}
                assert excluded_bvids == []
                assert limit == 10
                assert callable(self.callback)
                callback_result = self.callback(dict(counts))
                if inspect.isawaitable(callback_result):
                    await callback_result
                return SimpleNamespace(
                    items=[],
                    pool_counts_after=dict(counts),
                    timings=SimpleNamespace(
                        pool_snapshot_ms=1.0,
                        embedding_ms=2.0,
                        selector_worker_ms=3.0,
                        event_loop_resume_delay_ms=4.0,
                        persist_ms=5.0,
                    ),
                )

        hub = FakeEventHub()
        database = FakeDatabase()
        engine = FakeRecommendationEngine()
        app = create_app(
            memory_manager=object(),
            database=database,
            soul_engine=FakeSoulEngine(),
            recommendation_engine=engine,
            runtime_controller=FakeRuntimeController(hub),
        )
        count_calls_after_construction = database.count_calls
        client = TestClient(app)

        response = client.post("/api/recommendations/reshuffle")

        assert response.status_code == 200
        assert response.json() == {"items": []}
        assert database.count_calls == count_calls_after_construction
        assert any(
            event.get("pool_available_count") == 4
            and event.get("pool_raw_count") == 9
            and event.get("pool_pending_count") == 2
            for event in hub.events
        )

    def test_reshuffle_rechecks_dislike_committed_during_inflight_serve(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.discovery.engine import DiscoveredContent
        from openbiliclaw.recommendation.engine import Recommendation

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.disliked_topics: list[str] = []

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "old-snapshot"}

            def get_effective_disliked_topics(self) -> list[str]:
                return list(self.disliked_topics)

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeRecommendationEngine:
            def __init__(self, soul: FakeSoulEngine) -> None:
                self.soul = soul

            async def reshuffle_recommendations_with_result(self, **_kwargs: object) -> object:
                items = [
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV-REHAB",
                            title="腰椎保护实操训练",
                            topic_group="运动康复",
                        ),
                        recommendation_id=1,
                        topic_label="运动康复",
                    ),
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV-SQLITE",
                            title="SQLite 查询优化",
                            topic_group="数据库",
                        ),
                        recommendation_id=2,
                        topic_label="数据库",
                    ),
                ]
                # Simulate a durable preference write after profile capture but
                # before the serve result reaches the HTTP boundary.
                self.soul.disliked_topics = ["运动康复"]
                return SimpleNamespace(
                    items=items,
                    pool_counts_after={"available": 2},
                    timings=SimpleNamespace(),
                )

        soul = FakeSoulEngine()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul,
            recommendation_engine=FakeRecommendationEngine(soul),
            runtime_controller=object(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/reshuffle")

        assert response.status_code == 200
        assert [item["bvid"] for item in response.json()["items"]] == ["BV-SQLITE"]
        assert memory.events[0]["metadata"]["returned_item_ids"] == ["BV-SQLITE"]

    def test_append_recommendations_endpoint_excludes_existing_bvids(self) -> None:
        from fastapi.testclient import TestClient

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return True

        class FakeRuntimeController:
            def __init__(self, hub: FakeEventHub) -> None:
                self.event_hub = hub
                self.pool_available_count = 4

            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "pool_available_count": self.pool_available_count,
                    "pool_pending_count": 2,
                    "last_replenished_count": 0,
                    "last_discovered_count": 0,
                }

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            def __init__(self, runtime: FakeRuntimeController) -> None:
                self.runtime = runtime
                self.calls: list[tuple[object, list[str], int]] = []

            async def append_recommendations(
                self,
                *,
                profile: object,
                excluded_bvids: list[str],
                limit: int = 10,
            ) -> list[object]:
                self.calls.append((profile, excluded_bvids, limit))
                self.runtime.pool_available_count = 1
                from openbiliclaw.discovery.engine import DiscoveredContent
                from openbiliclaw.recommendation.engine import Recommendation

                return [
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV1NEXT",
                            title="下一批 1",
                            up_name="UPB",
                            cover_url="https://i0.hdslb.com/bfs/archive/next-cover.jpg",
                        ),
                        recommendation_id=22,
                        expression="这条接在你刚刚看的后面也顺。",
                        topic_label="下一条",
                        confidence=0.81,
                        presented=False,
                    )
                ]

        hub = FakeEventHub()
        runtime = FakeRuntimeController(hub)
        recommendation_engine = FakeRecommendationEngine(runtime)
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=recommendation_engine,
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendations/append",
            json={"excluded_bvids": ["BV1A", "BV1B"]},
        )

        assert response.status_code == 200
        assert recommendation_engine.calls == [({"profile": "ok"}, ["BV1A", "BV1B"], 10)]
        assert response.json() == {
            "items": [
                {
                    "id": 22,
                    "bvid": "BV1NEXT",
                    "item_key": "bilibili:BV1NEXT",
                    "title": "下一批 1",
                    "up_name": "UPB",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/next-cover.jpg",
                    "expression": "这条接在你刚刚看的后面也顺。",
                    "topic_label": "下一条",
                    "presented": False,
                    "feedback_type": "",
                    "content_id": "BV1NEXT",
                    "content_url": "https://www.bilibili.com/video/BV1NEXT",
                    "source_platform": "bilibili",
                    "content_type": "video",
                    "body_text": "",
                    "duration": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "danmaku_count": 0,
                    "favorite_count": 0,
                    "comment_count": 0,
                    "share_count": 0,
                    "rating_score": 0.0,
                    "rating_count": 0,
                    "source_rank": 0,
                    "up_mid": 0,
                    "published_at": "",
                    "published_label": "",
                }
            ]
        }
        assert hub.events[-1]["type"] == "refresh.pool_updated"
        assert hub.events[-1]["message"] == "推荐池已同步"
        assert hub.events[-1]["pool_available_count"] == 1
        assert hub.events[-1]["pool_pending_count"] == 2

    def test_empty_pool_append_and_reshuffle_skip_recommendation_path_and_debounce_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as app_module

        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)

        class FakeDatabase:
            def count_pool_candidates(self) -> int:
                return 0

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.profile_calls = 0

            async def get_profile(self) -> dict[str, object]:
                self.profile_calls += 1
                raise AssertionError("empty pool should not load the profile")

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.calls = 0

            async def reshuffle_recommendations(
                self,
                *,
                profile: object,
                excluded_bvids: list[str],
                limit: int = 10,
            ) -> list[object]:
                assert excluded_bvids == []
                self.calls += 1
                raise AssertionError("empty pool should not call reshuffle")

            async def append_recommendations(
                self, *, profile: object, excluded_bvids: list[str], limit: int = 10
            ) -> list[object]:
                self.calls += 1
                raise AssertionError("empty pool should not call append")

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.requests: list[tuple[str, bool]] = []

            def get_runtime_status(self) -> dict[str, object]:
                return {"pool_available_count": 0}

            async def request_replenishment(
                self,
                *,
                reason: str,
                force: bool = False,
            ) -> dict[str, object]:
                self.requests.append((reason, force))
                return {"accepted": True, "state": "running", "reason": reason}

            async def trigger_manual_refresh(self) -> dict[str, object]:
                raise AssertionError("new runtimes should use request_replenishment")

        soul = FakeSoulEngine()
        rec = FakeRecommendationEngine()
        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=soul,
            recommendation_engine=rec,
            runtime_controller=runtime,
        )
        client = TestClient(app)

        reshuffle = client.post("/api/recommendations/reshuffle")
        append = client.post(
            "/api/recommendations/append",
            json={"excluded_bvids": ["BV1OLD"]},
        )

        assert reshuffle.status_code == 200
        assert reshuffle.json() == {"items": []}
        assert append.status_code == 200
        assert append.json() == {"items": []}
        assert soul.profile_calls == 0
        assert rec.calls == 0
        assert runtime.requests == [("pool_empty", True)]

    def test_pending_notification_endpoint_returns_single_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_notification_candidate(
                self, *, min_confidence: float = 0.82
            ) -> dict[str, object] | None:
                assert min_confidence == 0.82
                return {
                    "id": 9,
                    "bvid": "BV1PENDING",
                    "title": "新的高置信推荐",
                    "expression": "这条很对你现在的口味。",
                }

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/notifications/pending")

        assert response.status_code == 200
        assert response.json() == {
            "item": {
                "recommendation_id": 9,
                "bvid": "BV1PENDING",
                "title": "新的高置信推荐",
                "reason": "这条很对你现在的口味。",
            }
        }

    def test_notification_sent_endpoint_marks_delivery(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.marked: list[str] = []

            def mark_notification_sent(self, bvid: str) -> None:
                self.marked.append(bvid)

        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post("/api/notifications/sent", json={"bvid": "BV1ACK"})

        assert response.status_code == 200
        assert response.json() == {"ok": True, "bvid": "BV1ACK"}
        assert runtime.marked == ["BV1ACK"]

    def test_feedback_endpoint_updates_recommendation_and_records_event(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def __init__(self) -> None:
                self.updated: list[tuple[int, str, str]] = []

            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                if recommendation_id != 7:
                    return None
                return {"id": 7, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                self.updated.append((recommendation_id, feedback_type, feedback_note))

        memory = FakeMemoryManager()
        database = FakeDatabase()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "这条确实对胃口",
                "request_id": "feedback-route-like",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "recommendation_id": 7,
            "feedback_type": "like",
            "event_id": 1,
            "duplicate": False,
            "processing": "queued",
        }
        assert database.updated == [(7, "like", "这条确实对胃口")]
        assert memory.events[0]["event_type"] == "feedback"
        assert memory.events[0]["metadata"]["recommendation_id"] == 7
        assert memory.events[0]["metadata"]["feedback_type"] == "like"

    def test_feedback_endpoint_accepts_dismiss(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def __init__(self) -> None:
                self.updated: list[tuple[int, str, str]] = []

            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                if recommendation_id != 7:
                    return None
                return {"id": 7, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                self.updated.append((recommendation_id, feedback_type, feedback_note))

        memory = FakeMemoryManager()
        database = FakeDatabase()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "dismiss",
                "note": "",
                "request_id": "feedback-route-dismiss",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "recommendation_id": 7,
            "feedback_type": "dismiss",
            "event_id": 1,
            "duplicate": False,
            "processing": "queued",
        }
        assert database.updated == [(7, "dismiss", "")]
        assert memory.events[0]["event_type"] == "feedback"
        assert memory.events[0]["metadata"]["feedback_type"] == "dismiss"
        assert "忽略了" in str(memory.events[0].get("context", ""))

    def test_feedback_endpoint_preserves_recommendation_source_platform(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
                return {
                    "id": recommendation_id,
                    "bvid": "zhihu:answer:42",
                    "title": "如何理解城市更新",
                    "source_platform": "zhihu",
                }

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        memory = FakeMemoryManager()
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=FakeDatabase(),
                soul_engine=object(),
            )
        )

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "dislike",
                "note": "这个方向不适合我",
                "request_id": "feedback-route-zhihu",
            },
        )

        assert response.status_code == 200
        event = memory.events[0]
        assert event["metadata"]["source_platform"] == "zhihu"
        assert "在知乎" in str(event["context"])
        assert "标记不喜欢" in str(event["context"])
        assert "备注:这个方向不适合我" in str(event["context"])

    def test_feedback_endpoint_falls_back_to_bilibili_for_legacy_rows(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
                return {"id": recommendation_id, "bvid": "BV1LEGACY", "title": "旧推荐"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        memory = FakeMemoryManager()
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=FakeDatabase(),
                soul_engine=object(),
            )
        )
        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 8,
                "feedback_type": "dismiss",
                "note": "",
                "request_id": "feedback-route-legacy",
            },
        )

        assert response.status_code == 200
        assert memory.events[0]["metadata"]["source_platform"] == "bilibili"
        assert "在B 站忽略了" in str(memory.events[0]["context"])

    def test_feedback_endpoint_preserves_bangumi_source_platform(self) -> None:
        """Bangumi recommendation feedback (点赞 like + 不感兴趣 dismiss) must
        carry ``source_platform='bangumi'`` onto the propagated event so profile
        signals stay attributed to Bangumi rather than the bilibili default, and
        the context string renders the Bangumi platform label."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
                return {
                    "id": recommendation_id,
                    "bvid": "326",
                    "title": "Cowboy Bebop",
                    "source_platform": "bangumi",
                }

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        for feedback_type, verb in (("like", "点赞了"), ("dismiss", "忽略了")):
            memory = FakeMemoryManager()
            client = TestClient(
                create_app(
                    memory_manager=memory,
                    database=FakeDatabase(),
                    soul_engine=object(),
                )
            )
            response = client.post(
                "/api/feedback",
                json={
                    "recommendation_id": 11,
                    "feedback_type": feedback_type,
                    "note": "",
                    "request_id": f"feedback-route-bangumi-{feedback_type}",
                },
            )
            assert response.status_code == 200, response.text
            event = memory.events[0]
            assert event["metadata"]["source_platform"] == "bangumi"
            assert event["metadata"]["feedback_type"] == feedback_type
            assert f"在Bangumi{verb}" in str(event["context"])

    def test_saved_endpoint_round_trips_bangumi_item_key(self, tmp_path: Path) -> None:
        """A Bangumi card saved via /api/saved canonicalizes to item_key
        'bangumi:<id>' and passes item-key validation on the status read-back —
        proving the saved surface accepts the Bangumi platform end-to-end."""
        from fastapi.testclient import TestClient

        from openbiliclaw.saved_sync.router import NativeSaveRouter
        from openbiliclaw.saved_sync.service import SavedSyncService
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / "saved-bangumi.db")
        database.initialize()
        app = create_app(
            memory_manager=SimpleNamespace(
                load_discovery_runtime_state=lambda: {},
                load_cognition_updates=lambda: [],
            ),
            database=database,
            soul_engine=SimpleNamespace(get_profile=lambda: None),
        )
        # Bangumi is read-only (no native write-back adapter); the local save
        # still commits and canonicalizes the item_key.
        app.state.runtime_context.saved_sync_service = SavedSyncService(
            database, NativeSaveRouter([])
        )
        client = TestClient(app)

        response = client.post(
            "/api/saved/favorite",
            json={
                "source_platform": "bangumi",
                "content_id": "326",
                "content_url": "https://bgm.tv/subject/326",
                "content_type": "anime",
                "title": "攻壳机动队",
                "author_name": "",
                "cover_url": "",
                "note": "",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["item_key"] == "bangumi:326"
        assert database.get_saved_membership("favorite", "bangumi:326") is not None

        status = client.get("/api/saved/favorite/status", params={"item_key": "bangumi:326"})
        assert status.status_code == 200, status.text
        assert status.json()["saved"] is True

    def test_chat_turn_endpoint_accepts_bangumi_delight_subject(self, tmp_path: Path) -> None:
        """The 聊一聊 delight entry must accept a Bangumi card payload (subject_id
        + subject_title) without erroring; the dialogue receives the context and
        the turn completes with the Bangumi subject echoed back."""
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(
                self,
                user_message: str,
                *,
                scope: str = "chat",
                turn_id: str = "",
            ) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.01)
                return "这部番像是从另一个角度补上你的口味。"

        db = Database(tmp_path / "chat-bangumi.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-bangumi-1",
                    "session": "popup",
                    "scope": "delight",
                    "subject_id": "326",
                    "subject_title": "Cowboy Bebop",
                    "message": "为什么这部番会推荐给我",
                },
            )
            assert response.status_code == 200, response.text
            turn = response.json()
            for _ in range(50):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-bangumi-1").json()
                if turn["status"] == "completed":
                    break
            assert turn["status"] == "completed"
            assert turn["scope"] == "delight"
            assert turn["subject_id"] == "326"
            assert dialogue.messages  # bangumi subject payload reached the dialogue
            assert "Cowboy Bebop" in dialogue.messages[0]

    def test_feedback_endpoint_rejects_unknown_feedback_type(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "x"}

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "spam",
                "note": "",
                "request_id": "feedback-invalid-type",
            },
        )

        assert response.status_code == 422

    def test_feedback_endpoint_rejects_comment_without_note(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "comment",
                "note": "",
                "request_id": "feedback-comment-without-note",
            },
        )

        assert response.status_code == 422

    def test_feedback_endpoint_reports_missing_recommendation(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return None

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "dislike",
                "note": "太浅了",
                "request_id": "feedback-missing-recommendation",
            },
        )

        assert response.status_code == 404

    def test_feedback_endpoint_schedules_profile_refresh_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app

        schedules = 0

        class FakeFeedbackBatchScheduler:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def schedule(self) -> None:
                nonlocal schedules
                schedules += 1

            async def close(self) -> None:
                pass

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                return None

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.called = False
                self.immediate_calls: list[tuple[str, str, str]] = []

            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str = "",
            ) -> None:
                self.immediate_calls.append((feedback_type, title, note))

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                self.called = True
                return {"triggered": False}

        monkeypatch.setattr(api_app, "FeedbackBatchScheduler", FakeFeedbackBatchScheduler)
        fake_soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=fake_soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
                "request_id": "feedback-profile-refresh",
            },
        )

        assert response.status_code == 200
        assert schedules == 1
        assert fake_soul_engine.called is False
        assert fake_soul_engine.immediate_calls == [("like", "讲透城市与建筑", "")]

    def test_feedback_endpoint_does_not_block_on_post_feedback_refresh(self) -> None:
        import time

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                return None

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        class SlowSoulEngine:
            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str = "",
            ) -> None:
                return None

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                await asyncio.sleep(0.2)
                return {"triggered": False}

        class SlowRuntimeController:
            async def refresh_after_feedback(self) -> dict[str, object]:
                await asyncio.sleep(0.2)
                return {"refreshed": False}

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=SlowSoulEngine(),
            runtime_controller=SlowRuntimeController(),
        )
        client = TestClient(app)

        started_at = time.perf_counter()
        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
                "request_id": "feedback-nonblocking-refresh",
            },
        )
        elapsed = time.perf_counter() - started_at

        assert response.status_code == 200
        assert elapsed < 0.15

    def test_autostart_status_reports_intent_and_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.get("/api/autostart-status")

        assert response.status_code == 200
        assert response.json() == {
            "supported": True,
            "enabled": True,
            "registered": False,
            "can_manage": True,
            "platform": "darwin",
            "mechanism": "launchd",
            "manage_ollama": True,
            "ollama_required": False,
            "reason": "none",
            "detail": "开机自启动配置已开启，但系统自启动项缺失。",
        }

    def test_autostart_status_remote_is_readable_but_not_manageable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.get("/api/autostart-status", headers={"sec-fetch-site": "cross-site"})

        assert response.status_code == 200
        assert response.json()["can_manage"] is False

    def test_autostart_status_surfaces_residual_os_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = False
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, True, "win32", "windows_run"),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.get("/api/autostart-status")

        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert response.json()["registered"] is True
        assert "残留项" in response.json()["detail"]

    def test_autostart_status_remote_hides_env_managed_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            guards,
            "active_env_managed_inputs",
            lambda loaded_cfg: ["GOOGLE_API_KEY", "OPENBILICLAW_LLM_OPENAI_API_KEY"],
        )
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.get("/api/autostart-status", headers={"sec-fetch-site": "cross-site"})

        assert response.status_code == 200
        body = response.json()
        assert body["can_manage"] is False
        assert body["reason"] == "local_only"
        assert "GOOGLE_API_KEY" not in body["detail"]
        assert "OPENBILICLAW_LLM_OPENAI_API_KEY" not in body["detail"]

    def test_config_response_includes_autostart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        cfg = Config()
        cfg.autostart.enabled = True
        cfg.autostart.manage_ollama = False
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        app = create_app()
        client = TestClient(app)

        response = client.get("/api/config")

        assert response.status_code == 200
        assert response.json()["autostart"] == {"enabled": True, "manage_ollama": False}

    def test_autostart_apply_rejects_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 403
        assert response.json()["reason"] == "local_only"

    def test_autostart_apply_rejects_unsupported_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(
                False, False, "darwin", "none", reason="unsupported_docker_runtime"
            ),
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "unsupported_docker_runtime"
        assert response.json()["enabled"] is False
        assert response.json()["registered"] is False

    def test_autostart_apply_rejects_env_managed_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            guards, "active_env_managed_inputs", lambda loaded_cfg: ["GOOGLE_API_KEY"]
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "env_managed"
        assert "GOOGLE_API_KEY" in response.json()["detail"]

    def test_autostart_apply_enable_writes_config_then_registers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        registered = False
        calls: list[str] = []

        def fake_register(loaded_cfg: object) -> None:
            nonlocal registered
            calls.append("register")
            registered = True

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "register", fake_register)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 200
        assert calls == ["register"]
        assert response.json()["enabled"] is True
        assert response.json()["registered"] is True
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

    def test_autostart_apply_disable_unregisters_before_writing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        registered = True
        calls: list[str] = []

        def fake_unregister() -> None:
            nonlocal registered
            calls.append(f"unregister_before_config={load_config().autostart.enabled}")
            registered = False

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "unregister", fake_unregister)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 200
        assert calls == ["unregister_before_config=True"]
        assert response.json()["enabled"] is False
        assert response.json()["registered"] is False
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_register_failure_rolls_back_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "register",
            lambda loaded_cfg: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "registration_failed"
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_unregister_failure_keeps_config_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, True, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "unregister",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 409
        assert response.json()["reason"] == "unregister_failed"
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

    def test_autostart_apply_shadowed_enable_does_not_register(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        (tmp_path / "runtime" / "config.local.toml").write_text(
            "[autostart]\nenabled = false\n",
            encoding="utf-8",
        )
        register_calls: list[object] = []
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "register",
            lambda loaded_cfg: register_calls.append(loaded_cfg),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "shadowed"
        assert register_calls == []
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_disable_save_failure_reregisters_and_rolls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw import config as config_module
        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        registered = True
        calls: list[str] = []

        def fake_unregister() -> None:
            nonlocal registered
            calls.append("unregister")
            registered = False

        def fake_register(loaded_cfg: object) -> None:
            nonlocal registered
            calls.append("register")
            registered = True

        def fake_save(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "unregister", fake_unregister)
        monkeypatch.setattr(autostart, "register", fake_register)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(config_module, "save_config", fake_save)
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 503
        assert response.json()["reason"] == "unavailable"
        assert calls == ["unregister", "register"]
        assert registered is True
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

    def test_profile_summary_endpoint_returns_initialized_profile(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-2",
                        "kind": "profile_shift",
                        "summary": "我对你又对上了一点：你不是只看热闹的人。",
                        "notified": True,
                    },
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "context_line": "基于最近内容：《中东局势深拆》 / 《国际秩序观察》",
                        "impact": "画像里\u201c国际新闻 / 深度分析\u201d这条偏好会更靠前。",
                        "reasoning": "这更像是连续强化后的稳定兴趣，不只是一次随手点开。",
                        "evidence": "因为你最近连续点开相关内容，还主动提到了国际时事。",
                        "source": "chat",
                        "source_label": "聊天",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-14T22:30:00",
                        "notified": False,
                    },
                ]

        class FakeProfile:
            personality_portrait = "这是一个喜欢把问题想透、信息密度偏高的用户。"
            core_traits = ["理性", "好奇", "克制", "耐心", "敏感", "深究", "自驱"]
            cognitive_style = ["会先看结构", "对证据比较敏感", "偏好把问题讲透", "不太吃空话"]
            motivational_drivers = ["建立判断确定性", "持续扩展理解边界", "在复杂信息里找到秩序感"]
            current_phase = "最近更像在一边吸收高密度信息，一边整理自己的判断框架。"
            deep_needs = ["理解世界", "持续成长", "高质量独处", "智性共鸣", "掌控感", "审美沉浸"]
            values = ["独立思考", "真实", "深度"]
            life_stage = "职业上升期，开始关注更宏观的议题。"
            preferences = type(
                "Preferences",
                (),
                {
                    "interests": [
                        type("Interest", (), {"name": "国际新闻"})(),
                        type("Interest", (), {"name": "深度分析"})(),
                        type("Interest", (), {"name": "工业设计"})(),
                        type("Interest", (), {"name": "城市观察"})(),
                        type("Interest", (), {"name": "纪录片"})(),
                        type("Interest", (), {"name": "商业案例"})(),
                        type("Interest", (), {"name": "复杂系统"})(),
                        type("Interest", (), {"name": "技术史"})(),
                        type("Interest", (), {"name": "冷知识考据"})(),
                    ],
                    "disliked_topics": [
                        "标题党",
                        "浅层热点复读",
                        "尬笑段子",
                        "纯情绪输出",
                        "过度说教",
                        "工业糖精",
                    ],
                    "favorite_up_users": ["经济观察", "构图实验室"],
                    "exploration_openness": 0.72,
                },
            )()

        class FakeSoulEngine:
            async def get_profile(self) -> FakeProfile:
                return FakeProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=FakeMemoryManager(),
            database=object(),
        )
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert data["personality_portrait"] == "这是一个喜欢把问题想透、信息密度偏高的用户。"
        assert data["core_traits"] == ["理性", "好奇", "克制", "耐心", "敏感", "深究"]
        assert data["deep_needs"] == ["理解世界", "持续成长", "高质量独处", "智性共鸣", "掌控感"]
        assert data["values"] == ["独立思考", "真实", "深度"]
        assert data["motivational_drivers"] == [
            "建立判断确定性",
            "持续扩展理解边界",
            "在复杂信息里找到秩序感",
        ]
        assert data["cognitive_style"] == [
            "会先看结构",
            "对证据比较敏感",
            "偏好把问题讲透",
            "不太吃空话",
        ]
        assert data["current_phase"] == "最近更像在一边吸收高密度信息，一边整理自己的判断框架。"
        assert data["life_stage"] == "职业上升期，开始关注更宏观的议题。"
        assert data["favorite_up_users"] == ["经济观察", "构图实验室"]
        assert data["exploration_openness"] == 0.72
        assert data["speculative_interests"] == []
        assert data["speculative_avoidances"] == []
        # mbti, likes, dislikes, style, context come from OnionProfile layers
        # FakeProfile has no OnionProfile.interest or .core.mbti so these are defaults
        assert data["mbti"]["type"] == ""
        assert isinstance(data["likes"], list)
        assert isinstance(data["dislikes"], list)
        assert isinstance(data["style"], dict)
        assert isinstance(data["context"], dict)
        assert len(data["recent_cognition_updates"]) == 2
        assert "summary" in data["recent_cognition_updates"][0]
        assert data["has_more_cognition_updates"] is False
        assert data["next_cognition_cursor"] == ""

    def test_profile_summary_endpoint_includes_speculative_avoidances(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )
        from openbiliclaw.soul.profile import OnionProfile

        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        experience_mode="knowledge",
                        entry_load="light",
                        confidence=0.66,
                        specifics=[
                            SpeculativeAvoidanceSpecific(name="标题党热点解读"),
                            SpeculativeAvoidanceSpecific(name="无信息增量复读"),
                        ],
                    ),
                    SpeculativeAvoidance(domain="已确认", status="confirmed"),
                ]
            ),
        )

        class FakeSoulEngine:
            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        data = response.json()
        assert data["speculative_avoidances"][0]["domain"] == "浅层热点复读"
        assert data["speculative_avoidances"][0]["source_mode"] == "negative_signal"
        assert [item["name"] for item in data["speculative_avoidances"][0]["specifics"]] == [
            "标题党热点解读",
            "无信息增量复读",
        ]

    def test_profile_summary_endpoint_includes_probe_mode_challenge_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.profile import OnionProfile
        from openbiliclaw.soul.speculator import (
            SpeculativeInterest,
            SpeculativeSpecific,
            SpeculativeState,
            save_speculative_state,
        )

        save_speculative_state(
            tmp_path,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="城市基础设施观察",
                        reason="从城市漫游兴趣桥接到更结构化的空间理解。",
                        confidence=0.67,
                        probe_mode="bridge",
                        specifics=[SpeculativeSpecific(name="地铁换乘设计")],
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        item = response.json()["speculative_interests"][0]
        assert item["domain"] == "城市基础设施观察"
        assert item["probe_mode"] == "bridge"
        assert item["challenge"] is True
        assert item["specifics"][0]["name"] == "地铁换乘设计"

    def test_pending_interest_probes_include_probe_mode_challenge_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.speculator import (
            SpeculativeInterest,
            SpeculativeState,
            save_speculative_state,
        )

        save_speculative_state(
            tmp_path,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="公共空间设计",
                        reason="这是从建筑美学延伸出去的横向试探。",
                        confidence=0.61,
                        probe_mode="lateral",
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            _speculator = object()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/interest-probes/pending")

        assert response.status_code == 200
        assert response.json()["items"] == [
            {
                "domain": "公共空间设计",
                "reason": "这是从建筑美学延伸出去的横向试探。",
                "confidence": 0.61,
                "status": "active",
                "probe_mode": "lateral",
                "challenge": True,
            }
        ]

    def test_interest_probe_confirm_e2e_removes_from_pending_and_profile(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.profile import OnionProfile
        from openbiliclaw.soul.speculator import (
            InterestSpeculator,
            SpeculativeInterest,
            SpeculativeState,
            load_speculative_state,
            save_speculative_state,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_speculative_state(
            tmp_path,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="城市基础设施观察",
                        category="城市",
                        reason="从城市漫游兴趣桥接到空间系统理解。",
                        confidence=0.67,
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = InterestSpeculator(llm_service=None, data_dir=tmp_path)

            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        assert client.get("/api/interest-probes/pending").json()["items"][0]["domain"] == (
            "城市基础设施观察"
        )
        assert client.get("/api/profile-summary").json()["speculative_interests"][0]["domain"] == (
            "城市基础设施观察"
        )

        response = client.post(
            "/api/interest-probes/respond",
            json={"domain": "城市基础设施观察", "response": "confirm"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert client.get("/api/interest-probes/pending").json()["items"] == []
        assert client.get("/api/profile-summary").json()["speculative_interests"] == []
        history = memory.load_discovery_runtime_state()["probe_feedback_history"]
        assert history[0]["domain"] == "城市基础设施观察"
        assert history[0]["response"] == "confirm"
        spec_state = load_speculative_state(tmp_path)
        assert all(item.domain != "城市基础设施观察" for item in spec_state.active)

    def test_interest_probe_trigger_runtime_event_includes_probe_mode_challenge_metadata(
        self,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub
        from openbiliclaw.runtime.refresh import ContinuousRefreshController

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "probed_domains": {},
                    "probed_axes": {},
                    "probed_distance_bands": {},
                }

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

        class FakeSpeculator:
            def get_active_speculations(self) -> list[object]:
                return [
                    SimpleNamespace(
                        domain="城市基础设施观察",
                        category="城市",
                        reason="从城市漫游兴趣桥接到空间系统理解。",
                        confidence=0.67,
                        weight=0.5,
                        experience_mode="wander_observe",
                        entry_load="light",
                        probe_mode="bridge",
                        specifics=[SimpleNamespace(name="地铁换乘设计")],
                    )
                ]

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()

        memory = FakeMemoryManager()
        hub = RuntimeEventHub()
        runtime = ContinuousRefreshController(
            memory_manager=memory,
            database=object(),
            soul_engine=FakeSoulEngine(),
            discovery_engine=object(),
            recommendation_engine=object(),
            event_hub=hub,
        )
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=runtime.soul_engine,
            runtime_controller=runtime,
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream") as websocket:
            response = client.post("/api/interest-probes/trigger")

            assert response.status_code == 200
            event = websocket.receive_json()

        assert event["type"] == "interest.probe"
        assert event["domain"] == "城市基础设施观察"
        assert event["probe_mode"] == "bridge"
        assert event["challenge"] is True

    def test_profile_summary_endpoint_paginates_cognition_history(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "第一条更新",
                        "context_line": "来自：《第一条内容》",
                        "impact": "第一条影响",
                        "reasoning": "第一条原因",
                        "evidence": "第一条证据",
                        "source": "feedback",
                        "source_label": "推荐反馈",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-15T09:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-2",
                        "kind": "interest_added",
                        "summary": "第二条更新",
                        "context_line": "来自最近这轮聊天",
                        "impact": "第二条影响",
                        "reasoning": "第二条原因",
                        "evidence": "第二条证据",
                        "source": "chat",
                        "source_label": "聊天",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-15T08:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-3",
                        "kind": "profile_shift",
                        "summary": "第三条更新",
                        "created_at": "2026-03-14T22:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-4",
                        "kind": "profile_shift",
                        "summary": "第四条更新",
                        "context_line": "基于最近几条相关内容",
                        "impact": "第四条影响",
                        "reasoning": "第四条原因",
                        "evidence": "第四条证据",
                        "source": "refresh",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-13T21:00:00",
                        "notified": True,
                    },
                ]

        class FakeProfile:
            personality_portrait = "这是一个喜欢把问题想透、信息密度偏高的用户。"
            core_traits = ["理性", "好奇"]
            deep_needs = ["理解世界", "持续成长"]
            preferences = type(
                "Preferences",
                (),
                {
                    "interests": [
                        type("Interest", (), {"name": "国际新闻"})(),
                        type("Interest", (), {"name": "深度分析"})(),
                    ]
                },
            )()

        class FakeSoulEngine:
            async def get_profile(self) -> FakeProfile:
                return FakeProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=FakeMemoryManager(),
            database=object(),
        )
        client = TestClient(app)

        first_page = client.get("/api/profile-summary?limit=3")

        assert first_page.status_code == 200
        assert first_page.json()["recent_cognition_updates"] == [
            {
                "summary": "第一条更新",
                "context_line": "来自：《第一条内容》",
                "impact": "第一条影响",
                "reasoning": "第一条原因",
                "evidence": "第一条证据",
                "source": "feedback",
                "source_label": "推荐反馈",
                "expand_hint": "expandable",
                "created_at": "2026-03-15T09:00:00",
            },
            {
                "summary": "第二条更新",
                "context_line": "来自最近这轮聊天",
                "impact": "第二条影响",
                "reasoning": "第二条原因",
                "evidence": "第二条证据",
                "source": "chat",
                "source_label": "聊天",
                "expand_hint": "expandable",
                "created_at": "2026-03-15T08:00:00",
            },
            {
                "summary": "第三条更新",
                "context_line": "基于最近几条相关内容",
                "impact": "",
                "reasoning": "",
                "evidence": "",
                "source": "",
                "source_label": "",
                "expand_hint": "summary_only",
                "created_at": "2026-03-14T22:00:00",
            },
        ]
        assert first_page.json()["has_more_cognition_updates"] is True
        assert first_page.json()["next_cognition_cursor"] == "3"

        second_page = client.get("/api/profile-summary?limit=3&cursor=3")

        assert second_page.status_code == 200
        assert second_page.json()["recent_cognition_updates"] == [
            {
                "summary": "第四条更新",
                "context_line": "基于最近几条相关内容",
                "impact": "第四条影响",
                "reasoning": "第四条原因",
                "evidence": "第四条证据",
                "source": "refresh",
                "source_label": "",
                "expand_hint": "expandable",
                "created_at": "2026-03-13T21:00:00",
            }
        ]
        assert second_page.json()["has_more_cognition_updates"] is False
        assert second_page.json()["next_cognition_cursor"] == ""

    def test_profile_summary_endpoint_handles_missing_profile(self) -> None:
        from fastapi.testclient import TestClient

        class FakeSoulEngine:
            async def get_profile(self) -> object:
                raise RuntimeError("not initialized")

        app = create_app(soul_engine=FakeSoulEngine(), memory_manager=object(), database=object())
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        assert response.json()["initialized"] is False

    def test_pending_cognition_update_endpoint_returns_latest_unnotified_item(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "confidence": 0.86,
                        "created_at": "2026-03-10T12:00:00",
                        "source": "feedback",
                        "notified": False,
                    },
                    {
                        "id": "cog-2",
                        "kind": "profile_shift",
                        "summary": "我对你又对上了一点：你不是只看热闹的人。",
                        "confidence": 0.9,
                        "created_at": "2026-03-10T11:00:00",
                        "source": "profile_refresh",
                        "notified": True,
                    },
                ]

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=object(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.get("/api/cognition-updates/pending")

        assert response.status_code == 200
        assert response.json() == {
            "item": {
                "id": "cog-1",
                "kind": "interest_added",
                "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
            }
        }

    def test_seen_cognition_update_endpoint_marks_item_notified(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self._updates = [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "notified": False,
                    }
                ]

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self._updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self._updates = list(updates)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/cognition-updates/seen", json={"id": "cog-1"})

        assert response.status_code == 200
        assert response.json() == {"ok": True, "id": "cog-1"}
        assert memory._updates[0]["notified"] is True

    def test_chat_endpoint_returns_dialogue_reply(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                assert user_message == "我最近总在看国际新闻"
                return "你更在意的是它背后的逻辑，还是事件本身的冲突感？"

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "我最近总在看国际新闻"})

        assert response.status_code == 200
        assert response.json() == {"reply": "你更在意的是它背后的逻辑，还是事件本身的冲突感？"}

    def test_chat_endpoint_returns_classified_safe_failure_without_changing_shape(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm.service import LLMResponseContentError

        class FakeDialogue:
            async def respond(
                self, _user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                raise LLMResponseContentError("LLM returned an empty response")

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "继续聊"})

        assert response.status_code == 200
        assert set(response.json()) == {"reply"}
        assert "空响应" in response.json()["reply"]
        assert "LLM returned an empty response" not in response.json()["reply"]

    def test_chat_endpoint_rejects_empty_message(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return user_message

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "   "})

        assert response.status_code == 422

    def test_interest_probe_reject_records_feedback_history(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "probed_domains": {},
                    "probed_axes": {},
                    "probe_feedback_history": [],
                }
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.rejected: list[tuple[str, int]] = []
                self._active = [
                    SimpleNamespace(
                        domain="城市漫游路线",
                        category="生活方式",
                        reason="用户最近对城市空间和徒步路线表现出探索意愿。",
                        experience_mode="wander_observe",
                        entry_load="light",
                        specifics=[SimpleNamespace(name="老街路线")],
                    )
                ]

            def get_active_speculations(self) -> list[object]:
                return list(self._active)

            def user_reject_speculation(
                self,
                domain: str,
                cooldown_days: int = 30,
            ) -> bool:
                self.rejected.append((domain, cooldown_days))
                self._active = []
                return True

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={"domain": "城市漫游路线", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "rejected"
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history == [
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "axis": "wander_observe|light",
                "category": "生活方式",
                "reason": "用户最近对城市空间和徒步路线表现出探索意愿。",
                "specifics": ["老街路线"],
                "created_at": history[0]["created_at"],
            }
        ]
        assert soul_engine._speculator.rejected == [("城市漫游路线", 30)]

    def test_interest_probe_confirm_from_profile_uses_profile_confirmed_source(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.confirmed: list[tuple[str, str]] = []
                self._active = [SimpleNamespace(domain="建筑美学")]

            def get_active_speculations(self) -> list[object]:
                return list(self._active)

            def user_confirm_speculation(
                self,
                domain: str,
                *,
                confirmation_source: str = "probe_confirmed",
            ) -> bool:
                self.confirmed.append((domain, confirmation_source))
                return True

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={"domain": "建筑美学", "response": "confirm", "surface": "profile"},
        )

        assert response.status_code == 200
        assert soul_engine._speculator.confirmed == [("建筑美学", "profile_confirmed")]

    def test_interest_probe_chat_strong_positive_direct_confirms(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "懂，这就是你想看的那类。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.confirmed: list[tuple[str, str]] = []
                self.rejected: list[tuple[str, int]] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="建筑美学")]

            def user_confirm_speculation(
                self,
                domain: str,
                *,
                confirmation_source: str = "probe_confirmed",
            ) -> bool:
                self.confirmed.append((domain, confirmation_source))
                return True

            def user_reject_speculation(
                self,
                domain: str,
                cooldown_days: int = 30,
            ) -> bool:
                self.rejected.append((domain, cooldown_days))
                return True

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "建筑美学",
                "response": "chat",
                "message": "这就是我想看的，以后多推这种",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == [("建筑美学", "chat_confirmed")]
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["response"] == "chat_confirmed"
        assert history[0]["classification"] == "strong_positive"
        assert history[0]["classifier"] == "keyword"
        assert history[0]["resulting_action"] == "confirmed"
        assert history[0]["raw_text_excerpt"] == "这就是我想看的，以后多推这种"

    def test_interest_probe_chat_weak_positive_records_without_confirming(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "可以，先把它当作一个轻量方向。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.confirmed: list[object] = []
                self.observed: list[object] = []
                self.rejected: list[object] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="城市基础设施观察")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.confirmed.append((_args, _kwargs))
                return True

            def observe(self, events: object) -> None:
                self.observed.append(events)

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.rejected.append((_args, _kwargs))
                return True

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "城市基础设施观察",
                "response": "chat",
                "message": "有点意思，可以看看",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == []
        assert speculator.observed == []
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["response"] == "weak_positive"
        assert history[0]["classification"] == "weak_positive"
        assert history[0]["resulting_action"] == "weak_positive_deferred"

    def test_interest_probe_chat_weak_positive_records_buffer_event(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "可以，先轻量试试。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="城市基础设施观察")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                return True

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                return True

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=FakeSpeculator()),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "城市基础设施观察",
                "response": "chat",
                "message": "有点意思，可以看看",
            },
        )

        assert response.status_code == 200
        buffer_state = memory.runtime_state["short_term_exploration_buffer"]
        assert buffer_state["entries"][0]["domain"] == "城市基础设施观察"
        assert buffer_state["entries"][0]["recent_evidence"][0]["source_event"] == (
            "weak_positive_chat"
        )

    def test_interest_probe_chat_classifier_failure_defaults_to_neutral(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class BrokenLLM:
            async def complete_with_core_memory(self, **_kwargs: object) -> object:
                raise RuntimeError("classifier unavailable")

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "我先理解成你还在犹豫。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.confirmed: list[object] = []
                self.rejected: list[object] = []
                self.deferred: list[object] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="抽象雕塑")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.confirmed.append((_args, _kwargs))
                return True

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.rejected.append((_args, _kwargs))
                return True

            def user_defer_speculation(self, *_args: object, **_kwargs: object) -> object:
                self.deferred.append((_args, _kwargs))
                from openbiliclaw.soul.speculator import DeferResult

                return DeferResult(outcome="deferred")

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
            recommendation_engine=SimpleNamespace(_llm=BrokenLLM()),
        )
        client = TestClient(app)

        # An ambiguous message with no explicit defer/positive/negative keyword —
        # LLM fails, keyword finds nothing → defaults to plain neutral.
        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "抽象雕塑",
                "response": "chat",
                "message": "嗯，我再想想看",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == []
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["classification"] == "neutral"
        assert history[0]["resulting_action"] == "none"

    def test_avoidance_probe_pending_returns_active_items(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )

        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        confidence=0.66,
                        specifics=[SpeculativeAvoidanceSpecific(name="标题党热点解读")],
                    ),
                    SpeculativeAvoidance(domain="已确认避雷", status="confirmed"),
                ]
            ),
        )

        class FakeSoulEngine:
            pass

        app = create_app(soul_engine=FakeSoulEngine(), memory_manager=object(), database=object())
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/avoidance-probes/pending")

        assert response.status_code == 200
        assert response.json()["items"][0]["domain"] == "浅层热点复读"
        assert len(response.json()["items"]) == 1

    def test_avoidance_probe_confirm_e2e_removes_from_pending_and_profile(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )
        from openbiliclaw.soul.profile import OnionProfile

        async def fake_apply_new_dislikes(**_kwargs: object) -> list[str]:
            return []

        monkeypatch.setattr(
            app_module,
            "apply_new_dislikes",
            fake_apply_new_dislikes,
            raising=False,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        confidence=0.66,
                        specifics=[SpeculativeAvoidanceSpecific(name="标题党热点解读")],
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = None

            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        assert client.get("/api/avoidance-probes/pending").json()["items"][0]["domain"] == (
            "浅层热点复读"
        )
        assert (
            client.get("/api/profile-summary").json()["speculative_avoidances"][0]["domain"]
            == "浅层热点复读"
        )

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "浅层热点复读", "response": "confirm"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert client.get("/api/avoidance-probes/pending").json()["items"] == []
        assert client.get("/api/profile-summary").json()["speculative_avoidances"] == []
        history = memory.load_discovery_runtime_state()["avoidance_probe_feedback_history"]
        assert history[0]["domain"] == "浅层热点复读"
        assert history[0]["response"] == "confirm"

    def test_avoidance_probe_confirm_adds_disliked_specific_topics(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )
        from openbiliclaw.soul.profile import OnionProfile

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        specifics=[
                            SpeculativeAvoidanceSpecific(name="标题党热点解读"),
                            SpeculativeAvoidanceSpecific(name="无信息增量复读"),
                        ],
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = None

            async def get_profile(self) -> OnionProfile:
                soul_data = memory.get_layer("soul").data
                return OnionProfile.from_dict(soul_data) if soul_data else OnionProfile()

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "浅层热点复读", "response": "confirm"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "confirmed"
        disliked_topics = memory.get_layer("preference").data["disliked_topics"]
        assert "标题党热点解读" in disliked_topics
        assert "无信息增量复读" in disliked_topics
        assert "浅层热点复读" not in disliked_topics

        profile_response = client.get("/api/profile-summary")
        assert {item["domain"] for item in profile_response.json()["dislikes"]} >= {
            "标题党热点解读",
            "无信息增量复读",
        }

    def test_avoidance_probe_reject_does_not_add_disliked_topic(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            save_avoidance_state,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读")]),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = None

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "浅层热点复读", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "rejected"
        assert "浅层热点复读" not in memory.get_layer("preference").data.get(
            "disliked_topics",
            [],
        )
        history = memory.load_discovery_runtime_state()["avoidance_probe_feedback_history"]
        assert history[0]["response"] == "reject"

    def test_stale_avoidance_probe_reject_does_not_record_feedback_history(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "avoidance_probe_feedback_history": [],
                }
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeAvoidanceSpeculator:
            def __init__(self) -> None:
                self.rejected: list[tuple[str, int]] = []

            def get_active_avoidances(self) -> list[object]:
                return []

            def user_reject_avoidance(self, domain: str, cooldown_days: int = 30) -> bool:
                self.rejected.append((domain, cooldown_days))
                return False

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = FakeAvoidanceSpeculator()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "比赛话题里的情绪型切片复读", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert memory.runtime_state["avoidance_probe_feedback_history"] == []
        assert soul_engine._avoidance_speculator.rejected == [("比赛话题里的情绪型切片复读", 30)]

    def test_avoidance_probe_confirm_schedules_dislike_writeback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        specifics=[SpeculativeAvoidanceSpecific(name="标题党热点解读")],
                    )
                ]
            ),
        )
        embedding_service = object()
        calls: list[dict[str, object]] = []

        async def fake_apply_new_dislikes(**kwargs: object) -> list[str]:
            await asyncio.sleep(0.02)
            calls.append(dict(kwargs))
            return ["新增不喜欢方向: 标题党热点解读"]

        monkeypatch.setattr(
            app_module,
            "apply_new_dislikes",
            fake_apply_new_dislikes,
            raising=False,
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = embedding_service

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/api/avoidance-probes/respond",
                json={"domain": "浅层热点复读", "response": "confirm"},
            )

            assert response.status_code == 200
            assert calls == []
            for _ in range(20):
                time.sleep(0.02)
                if calls:
                    break

        assert calls
        assert calls[0]["topics"] == ["标题党热点解读"]
        assert calls[0]["embedding_service"] is embedding_service
        history = memory.load_discovery_runtime_state()["avoidance_probe_feedback_history"]
        assert history[0]["response"] == "confirm"
        assert history[0]["source_mode"] == "negative_signal"

    def test_chat_turn_endpoint_persists_pending_turn_until_reply(self, tmp_path: Path) -> None:
        import asyncio
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.05)
                return "你更在意的是它背后的逻辑。"

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            start = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-test-1",
                    "session": "popup",
                    "scope": "chat",
                    "message": "我最近总在看国际新闻",
                },
            )

            assert start.status_code == 200
            assert start.json()["turn_id"] == "turn-test-1"
            assert start.json()["status"] == "pending"

            turn = start.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-test-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["reply"] == "你更在意的是它背后的逻辑。"
            assert dialogue.messages == ["我最近总在看国际新闻"]

            history = client.get("/api/chat/turns", params={"session": "popup"}).json()
            assert history["items"] == [turn]

        # Re-open the app on the same database to simulate a popup/backend
        # client lifecycle boundary: completed turns must be recoverable.
        app2 = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )
        client2 = TestClient(app2)
        restored = client2.get("/api/chat/turns", params={"session": "popup"}).json()

        assert restored["items"][0]["turn_id"] == "turn-test-1"
        assert restored["items"][0]["status"] == "completed"
        assert restored["items"][0]["reply"] == "你更在意的是它背后的逻辑。"

    def test_confusion_scope_establishes_anchor_without_legacy_direct_settlement(
        self,
        tmp_path: Path,
    ) -> None:
        """The durable path establishes the anchor but never owns settlement."""
        import time
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.confusion import ConfusionManager
        from openbiliclaw.soul.dialogue_anchor import DialogueAnchorManager
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJob, DialogueJobKind
        from openbiliclaw.soul.ledger import ProfileLedger
        from openbiliclaw.storage.database import Database

        class TrackingConfusionManager(ConfusionManager):
            def __init__(self, database: Database) -> None:
                super().__init__(database)
                self.direct_resolve_calls = 0
                self.direct_defer_calls = 0

            def resolve(self, *args: object, **kwargs: object) -> str | None:
                self.direct_resolve_calls += 1
                return super().resolve(*args, **kwargs)  # type: ignore[arg-type]

            def defer(self, *args: object, **kwargs: object) -> None:
                self.direct_defer_calls += 1
                super().defer(*args, **kwargs)  # type: ignore[arg-type]

        class FakeDialogue:
            def __init__(self, anchor_manager: DialogueAnchorManager) -> None:
                self._anchor_manager = anchor_manager
                self.anchor_seen_before_reply = False

            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                del user_message, scope, turn_id
                self.anchor_seen_before_reply = self._anchor_manager.current() is not None
                return "明白了"

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        confusion_id = db.insert_confusion(topic="解压视频", observation="停留很短")
        manager = TrackingConfusionManager(db)
        anchor_manager = DialogueAnchorManager(
            tmp_path,
            database=db,
            ledger=ProfileLedger(db),
        )
        dialogue = FakeDialogue(anchor_manager)
        soul_engine = SimpleNamespace(
            _confusion_manager=manager,
            _dialogue_anchor_manager=anchor_manager,
        )
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=soul_engine,
            dialogue=dialogue,
        )
        queue = app.state.runtime_context.dialogue_settlement_queue
        runtime_dispatch = queue._dispatcher
        observed: list[tuple[DialogueJobKind, bool]] = []

        async def observe(job: DialogueJob):  # type: ignore[no-untyped-def]
            observed.append((job.kind, asyncio.current_task() is queue.worker_task))
            return await runtime_dispatch(job)

        queue._dispatcher = observe

        with TestClient(app) as client:
            client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-confusion-1",
                    "session": "popup",
                    "scope": "confusion",
                    "subject_id": str(confusion_id),
                    "subject_title": "解压视频",
                    "message": "我就喜欢",
                },
            )
            for _ in range(50):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-confusion-1").json()
                if turn["status"] == "completed":
                    break
            assert turn["status"] == "completed"

        stored = manager.get(confusion_id)
        assert stored is not None
        assert stored.status == "clarifying"
        assert stored.resolution == ""
        assert manager.direct_resolve_calls == 0
        assert manager.direct_defer_calls == 0
        assert dialogue.anchor_seen_before_reply is True
        active_anchor = anchor_manager.current()
        assert active_anchor is not None
        assert active_anchor.kind == "confusion"
        assert active_anchor.ref == str(confusion_id)
        stored_turn = db.get_chat_turn("turn-confusion-1")
        assert stored_turn is not None
        assert stored_turn["payload"]["confusion_reply_apply"]["effects"] == {
            "cognition": True,
            "event": True,
        }
        assert observed == [
            (DialogueJobKind.CONFUSION_OPEN_SYNC, True),
            (DialogueJobKind.ANCHOR_ESTABLISH, True),
            (DialogueJobKind.CONFUSION_REPLY_APPLY, True),
        ]

    def test_probe_result_handoff_runs_exploration_outside_worker_once(
        self,
        tmp_path: Path,
    ) -> None:
        """F8: classify in-worker once, then consume exploration in the producer task."""
        import copy
        import time
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.dialogue_learn_queue import DialogueJobKind
        from openbiliclaw.storage.database import Database

        class FakeMemory:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
                self.cognition_updates: list[dict[str, object]] = []
                self.mutations: list[tuple[frozenset[str], bool]] = []
                self.queue: object | None = None

            def update_discovery_runtime_state(self, mutate: object) -> None:
                before = copy.deepcopy(self.runtime_state)
                mutate(self.runtime_state)  # type: ignore[operator]
                changed = frozenset(
                    key
                    for key in set(before) | set(self.runtime_state)
                    if before.get(key) != self.runtime_state.get(key)
                )
                worker = getattr(self.queue, "worker_task", None)
                self.mutations.append((changed, asyncio.current_task() is worker))

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSentimentLLM:
            def __init__(self) -> None:
                self.calls = 0

            async def complete_with_core_memory(self, **_kwargs: object) -> object:
                self.calls += 1
                return SimpleNamespace(content="weak_positive")

        class FakeSpeculator:
            def get_active_speculations(self) -> list[object]:
                return [
                    SimpleNamespace(
                        domain="城市基础设施观察",
                        category="城市",
                        reason="近期关注城市空间",
                        experience_mode="wander_observe",
                        entry_load="light",
                        specifics=[SimpleNamespace(name="交通节点")],
                    )
                ]

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()
                self.queue: object | None = None

            def bind_dialogue_settlement_queue(self, queue: object) -> None:
                self.queue = queue

        class FakeDialogue:
            async def respond(
                self,
                _message: str,
                *,
                scope: str = "chat",
                turn_id: str = "",
                session: str = "",
            ) -> str:
                del scope, turn_id, session
                return "可以，先作为轻量方向观察。"

        database = Database(tmp_path / "openbiliclaw.db")
        database.initialize()
        memory = FakeMemory()
        sentiment_llm = FakeSentimentLLM()
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=FakeSoulEngine(),
            dialogue=FakeDialogue(),
            recommendation_engine=SimpleNamespace(_llm=sentiment_llm),
        )
        queue = app.state.runtime_context.dialogue_settlement_queue
        memory.queue = queue

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "durable-probe-handoff",
                    "session": "popup",
                    "scope": "probe",
                    "subject_id": "城市基础设施观察",
                    "subject_title": "城市基础设施观察",
                    "message": "有点意思，可以看看",
                },
            )
            assert response.status_code == 200
            for _ in range(50):
                time.sleep(0.02)
                state = memory.runtime_state
                if "short_term_exploration_buffer" in state:
                    break

            duplicate = client.portal.call(
                queue.submit_and_wait,
                DialogueJobKind.PROBE_REPLY_APPLY,
                {
                    "turn_id": "durable-probe-handoff",
                    "domain": "城市基础设施观察",
                    "message": "有点意思，可以看看",
                    "reply": "可以，先作为轻量方向观察。",
                },
            )

        assert duplicate.classification == "weak_positive"
        assert duplicate.exploration_intent is not None
        assert duplicate.exploration_intent.evidence_id == "durable-probe-handoff"
        assert sentiment_llm.calls == 1
        stored_turn = database.get_chat_turn("durable-probe-handoff")
        assert stored_turn is not None
        stored_receipt = stored_turn["payload"]["probe_reply_apply"]
        assert stored_receipt["classification"] == "weak_positive"
        assert stored_receipt["effects"] == {
            "settlement": True,
            "history": True,
            "cognition": True,
            "event": True,
        }
        exploration_mutations = [
            in_worker
            for changed, in_worker in memory.mutations
            if "short_term_exploration_buffer" in changed
        ]
        assert exploration_mutations == [False]
        feedback_mutations = [
            in_worker
            for changed, in_worker in memory.mutations
            if "probe_feedback_history" in changed
        ]
        assert feedback_mutations == [True]

    def test_explicit_terminal_chat_turn_is_safe_and_same_id_is_idempotent(
        self,
        tmp_path: Path,
    ) -> None:
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.calls = 0

            async def respond(
                self, _user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                self.calls += 1
                raise LLMResponseContentError("LLM returned an empty response")

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )
        payload = {
            "turn_id": "turn-failed-1",
            "session": "popup",
            "scope": "chat",
            "message": "这轮应该失败",
        }

        with TestClient(app) as client:
            assert client.post("/api/chat/turns", json=payload).status_code == 200
            turn: dict[str, object] = {}
            for _ in range(30):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-failed-1").json()
                if turn["status"] != "pending":
                    break

            assert turn["status"] == "failed"
            assert turn["reply"] == ""
            assert "空响应" in str(turn["error"])

            retry = client.post("/api/chat/turns", json=payload)
            assert retry.status_code == 200
            assert retry.json() == turn
            time.sleep(0.05)
            assert dialogue.calls == 1

    @pytest.mark.parametrize(
        "failure",
        [
            RuntimeError("provider configuration is temporarily unavailable"),
            TimeoutError("socket stalled"),
        ],
    )
    def test_transient_chat_failure_stays_pending_then_retries_to_completion(
        self,
        tmp_path: Path,
        failure: Exception,
    ) -> None:
        import asyncio
        import threading
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.calls = 0
                self.first_failed = threading.Event()
                self.retry_started = threading.Event()
                self.release_retry: asyncio.Event | None = None

            async def respond(
                self, _user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                del scope, turn_id
                self.calls += 1
                if self.calls == 1:
                    self.first_failed.set()
                    raise failure
                self.release_retry = asyncio.Event()
                self.retry_started.set()
                await self.release_retry.wait()
                return "重试后的真实回复"

            async def unblock(self) -> None:
                assert self.release_retry is not None
                self.release_retry.set()

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )
        app.state.chat_reply_scheduler.retry_base_seconds = 0.01
        app.state.chat_reply_scheduler.retry_max_seconds = 0.01

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-transient-1",
                    "session": "popup",
                    "scope": "chat",
                    "message": "这轮先遇到瞬态失败",
                },
            )
            assert response.status_code == 200
            assert dialogue.first_failed.wait(timeout=1)
            assert dialogue.retry_started.wait(timeout=1)

            pending = db.get_chat_turn("turn-transient-1")
            assert pending is not None
            assert pending["status"] == "pending"
            assert pending["error"] == ""

            client.portal.call(dialogue.unblock)
            for _ in range(50):
                turn = client.get("/api/chat/turns/turn-transient-1").json()
                if turn["status"] == "completed":
                    break
                time.sleep(0.01)

        assert turn["status"] == "completed"
        assert turn["reply"] == "重试后的真实回复"
        assert dialogue.calls == 2

    def test_empty_chat_turn_reply_is_failed_instead_of_completed(self, tmp_path: Path) -> None:
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            async def respond(
                self, _user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "   "

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )

        with TestClient(app) as client:
            client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-empty-1",
                    "session": "popup",
                    "scope": "chat",
                    "message": "不要完成空回复",
                },
            )
            turn: dict[str, object] = {}
            for _ in range(30):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-empty-1").json()
                if turn["status"] != "pending":
                    break

        assert turn["status"] == "failed"
        assert turn["reply"] == ""
        assert "空响应" in str(turn["error"])

    def test_durable_reply_completes_before_best_effort_context_side_effects(
        self, tmp_path: Path
    ) -> None:
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        order: list[str] = []

        class OrderedDatabase(Database):
            def complete_chat_turn(self, turn_id: str, *, reply: str) -> None:
                order.append("complete")
                super().complete_chat_turn(turn_id, reply=reply)

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                order.append("dialogue")
                return "这是真实回复"

        class FakeMemory:
            def __init__(self) -> None:
                self.updates: list[dict[str, object]] = []

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                order.append("cognition")
                self.updates = list(updates)

        class FailingEventHub:
            async def publish(self, _event: dict[str, object]) -> None:
                order.append("publish")
                raise RuntimeError("publish unavailable")

        db = OrderedDatabase(tmp_path / "openbiliclaw.db")
        db.initialize()
        app = create_app(
            memory_manager=FakeMemory(),
            database=db,
            soul_engine=object(),
            dialogue=FakeDialogue(),
            runtime_controller=SimpleNamespace(event_hub=FailingEventHub()),
        )

        with TestClient(app) as client:
            client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-side-effect-failure",
                    "session": "popup",
                    "scope": "delight",
                    "subject_id": "BV1SAFE",
                    "subject_title": "惊喜",
                    "message": "聊聊",
                },
            )
            turn: dict[str, object] = {}
            for _ in range(30):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-side-effect-failure").json()
                if turn["status"] != "pending":
                    break

        assert turn["status"] == "completed"
        assert turn["reply"] == "这是真实回复"
        assert turn["error"] == ""
        assert order == ["dialogue", "complete", "cognition", "publish"]

    def test_completion_persistence_failure_does_not_mark_genuine_reply_failed(
        self, tmp_path: Path
    ) -> None:
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        completion_attempted = threading.Event()
        failed_calls: list[tuple[str, str, str]] = []

        class FailingCompletionDatabase(Database):
            def complete_chat_turn(self, turn_id: str, *, reply: str) -> None:
                completion_attempted.set()
                raise RuntimeError("completion persistence unavailable")

            def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> None:
                failed_calls.append((turn_id, error, reply))
                super().fail_chat_turn(turn_id, error=error, reply=reply)

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                return "已经完成的真实回复"

        db = FailingCompletionDatabase(tmp_path / "openbiliclaw.db")
        db.initialize()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-completion-write-failure",
                    "session": "popup",
                    "scope": "chat",
                    "message": "这轮模型成功",
                },
            )
            assert response.status_code == 200
            assert completion_attempted.wait(timeout=1)

        row = db.get_chat_turn("turn-completion-write-failure")
        assert row is not None
        assert row["status"] == "pending"
        assert failed_calls == []

    def test_chat_turn_endpoint_records_delight_scope_context(self, tmp_path: Path) -> None:
        import asyncio
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.01)
                return "这条像是从另一个角度补上你的问题。"

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-delight-1",
                    "session": "popup",
                    "scope": "delight",
                    "subject_id": "BV1DL",
                    "subject_title": "复杂系统入门",
                    "message": "我想知道它为什么会推荐给我",
                },
            )
            assert response.status_code == 200

            turn = response.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-delight-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["scope"] == "delight"
            assert turn["subject_id"] == "BV1DL"
            assert "关于惊喜推荐「复杂系统入门」的反馈" in dialogue.messages[0]

            delight_history = client.get(
                "/api/chat/turns",
                params={"session": "popup", "scope": "delight"},
            ).json()
            assert [item["turn_id"] for item in delight_history["items"]] == ["turn-delight-1"]

    def test_chat_turn_endpoint_records_avoidance_probe_scope_context(self, tmp_path: Path) -> None:
        import asyncio
        import time
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(
                self, user_message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.01)
                return "懂，这类你更像是在避开低信息密度。"

        class FakeAvoidanceSpeculator:
            def __init__(self) -> None:
                self.observed: list[object] = []

            def get_active_avoidances(self) -> list[object]:
                return [
                    SimpleNamespace(
                        domain="浅层热点复读",
                        reason="信息密度低",
                        source_mode="negative_signal",
                        source_signal="dislike",
                        specifics=[SimpleNamespace(name="标题党热点解读")],
                        experience_mode="knowledge",
                        entry_load="light",
                    )
                ]

            def observe(self, events: object) -> None:
                self.observed.append(events)

            def user_reject_avoidance(self, *_args: object, **_kwargs: object) -> bool:
                return True

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.cognition_updates: list[object] = []
                self.runtime_state: dict[str, object] = {"avoidance_probe_feedback_history": []}

            def load_cognition_updates(self) -> list[object]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[object]) -> None:
                self.cognition_updates = list(updates)

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        speculator = FakeAvoidanceSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=db,
            soul_engine=SimpleNamespace(_avoidance_speculator=speculator),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-avoidance-1",
                    "session": "popup",
                    "scope": "avoidance_probe",
                    "subject_id": "浅层热点复读",
                    "subject_title": "浅层热点复读",
                    "message": "对，这类我不喜欢",
                },
            )
            assert response.status_code == 200

            turn = response.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-avoidance-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["scope"] == "avoidance_probe"
            assert "关于避雷方向「浅层热点复读」的反馈" in dialogue.messages[0]
            assert speculator.observed

            history = client.get(
                "/api/chat/turns",
                params={"session": "popup", "scope": "avoidance_probe"},
            ).json()
            assert [item["turn_id"] for item in history["items"]] == ["turn-avoidance-1"]
            feedback_history = memory.runtime_state["avoidance_probe_feedback_history"]
            assert feedback_history[0]["response"] == "avoidance_chat_confirmed"

    def test_recommendation_click_endpoint_ingests_strong_signal(self) -> None:
        """POST /api/recommendation-click should push a strong signal through the pipeline."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                if recommendation_id != 99:
                    return None
                return {
                    "id": 99,
                    "bvid": "BV1REC99",
                    "title": "深入理解Transformer",
                    "topic_label": "AI技术",
                    "up_name": "ML教程君",
                }

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)

                from openbiliclaw.soul.pipeline import (
                    IngestResult,
                    LayerUpdateResult,
                    OnionLayer,
                )

                return IngestResult(
                    signals_accepted=1,
                    layers_buffered=["interest", "surface"],
                    layers_updated=[
                        LayerUpdateResult(
                            layer=OnionLayer.INTEREST,
                            changed=True,
                            changes=["新增兴趣: AI"],
                        ),
                        LayerUpdateResult(
                            layer=OnionLayer.SURFACE,
                            changed=False,
                        ),
                    ],
                )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        database = FakeDatabase()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"recommendation_id": 99, "request_id": "click-bilibili-99"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["bvid"] == "BV1REC99"
        assert body["layers_updated"] == []
        assert body["processing"] == "queued"

        # Click should have been persisted as an event and ingested as a signal.
        assert memory.events, "Click should be persisted as an event"
        assert memory.events[0]["event_type"] == "click"
        assert memory.events[0]["metadata"]["bvid"] == "BV1REC99"
        assert memory.events[0]["metadata"]["recommendation_id"] == 99

        assert soul_engine.pipeline.ingested == []

    def test_recommendation_click_request_id_ignores_rotating_url_after_db_hydration(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.storage.database import Database

        database = Database(tmp_path / "click-idempotency.db")
        memory = MemoryManager(tmp_path / "data", database=database)
        memory.initialize()
        recommendation_id = database.insert_recommendation(
            "xhs-note-stable",
            confidence=0.9,
        )
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=database,
                soul_engine=object(),
            )
        )
        request_id = "click-token-rotation"
        first_payload = {
            "recommendation_id": recommendation_id,
            "source_platform": "xiaohongshu",
            "content_url": ("https://www.xiaohongshu.com/explore/xhs-note-stable?xsec_token=old"),
            "title": "首写标题",
            "request_id": request_id,
        }
        rotated_payload = {
            **first_payload,
            "content_url": ("https://www.xiaohongshu.com/explore/xhs-note-stable?xsec_token=new"),
            "title": "重渲染标题",
        }

        first = client.post("/api/recommendation-click", json=first_payload)
        replay = client.post("/api/recommendation-click", json=rotated_payload)
        conflict = client.post(
            "/api/recommendation-click",
            json={
                **rotated_payload,
                "bvid": "truly-different-content",
                "content_id": "truly-different-content",
            },
        )

        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["duplicate"] is False
        assert replay.json()["duplicate"] is True
        assert replay.json()["event_id"] == first.json()["event_id"]
        assert conflict.status_code == 409
        events = memory.query_events(event_types=["click"], limit=10)
        assert len(events) == 1
        assert events[0]["title"] == "首写标题"
        assert events[0]["url"] == first_payload["content_url"]
        metadata = json.loads(str(events[0]["metadata"]))
        assert metadata["content_id"] == "xhs-note-stable"
        assert metadata["content_url"] == first_payload["content_url"]

    def test_recommendation_click_endpoint_keeps_youtube_click_source_aware(self) -> None:
        """YouTube recommendation clicks must not be persisted as Bilibili URLs."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                if recommendation_id != 42:
                    return None
                return {
                    "id": 42,
                    "bvid": "KPoJ7p9iy4Q",
                    "content_id": "KPoJ7p9iy4Q",
                    "content_url": "https://www.youtube.com/watch?v=KPoJ7p9iy4Q",
                    "source_platform": "youtube",
                    "title": "A YouTube deep dive",
                    "topic_label": "技术长视频",
                    "up_name": "YT Creator",
                }

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1)

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"recommendation_id": 42, "request_id": "click-youtube-42"},
        )

        assert response.status_code == 200
        assert response.json()["bvid"] == "KPoJ7p9iy4Q"
        assert memory.events, "YouTube click should be persisted"
        event = memory.events[0]
        assert event["url"] == "https://www.youtube.com/watch?v=KPoJ7p9iy4Q"
        assert "YouTube" in event["context"]
        assert event["metadata"]["source_platform"] == "youtube"
        assert event["metadata"]["content_id"] == "KPoJ7p9iy4Q"
        assert event["metadata"]["content_url"] == "https://www.youtube.com/watch?v=KPoJ7p9iy4Q"

        assert soul_engine.pipeline.ingested == []

    def test_recommendation_click_endpoint_infers_x_click_source_from_url(self) -> None:
        """X recommendation clicks with only a URL should persist as twitter."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1)

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "content_id": "1790000000000000001",
                "content_url": "https://x.com/h/status/1790000000000000001",
                "title": "A text tweet",
                "request_id": "click-twitter-direct",
            },
        )

        assert response.status_code == 200
        assert memory.events, "X click should be persisted"
        event = memory.events[0]
        assert event["url"] == "https://x.com/h/status/1790000000000000001"
        assert "X" in event["context"]
        assert event["metadata"]["source_platform"] == "twitter"
        assert soul_engine.pipeline.ingested == []

    def test_recommendation_click_endpoint_builds_reddit_fallback_url(self) -> None:
        """Reddit recommendation clicks should stay source-aware even without content_url."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1)

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "content_id": "t3_abc123",
                "source_platform": "reddit",
                "title": "A Reddit post",
                "request_id": "click-reddit-direct",
            },
        )

        assert response.status_code == 200
        assert memory.events, "Reddit click should be persisted"
        event = memory.events[0]
        assert event["url"] == "https://www.reddit.com/comments/abc123/"
        assert "Reddit" in event["context"]
        assert event["metadata"]["source_platform"] == "reddit"
        assert event["metadata"]["content_id"] == "t3_abc123"
        assert event["metadata"]["content_url"] == "https://www.reddit.com/comments/abc123/"
        assert soul_engine.pipeline.ingested == []

    def test_recommendation_click_builds_bangumi_fallback_url(self) -> None:
        """Bangumi subjects must never fall back to a Bilibili video URL."""
        from openbiliclaw.api.app import _fallback_recommendation_click_url

        assert (
            _fallback_recommendation_click_url(
                source_platform="bangumi",
                content_id="326",
                bvid="326",
            )
            == "https://bgm.tv/subject/326"
        )

    def test_recommendation_click_builds_only_numeric_linuxdo_fallback_url(self) -> None:
        """Linux.do topic identities map to canonical topic URLs, never Bilibili."""
        from openbiliclaw.api.app import _fallback_recommendation_click_url

        assert (
            _fallback_recommendation_click_url(
                source_platform="linuxdo",
                content_id="topic:326",
                bvid="topic:326",
            )
            == "https://linux.do/t/326"
        )
        assert (
            _fallback_recommendation_click_url(
                source_platform="linuxdo",
                content_id="topic:not-numeric",
                bvid="topic:not-numeric",
            )
            == ""
        )

    def test_recommendation_click_endpoint_persists_dwell_fields(self) -> None:
        """When the extension reports dwell on the click-through, those
        fields flow into the persisted click event so storage can classify
        the recommendation outcome (meaningful_dwell vs quick_exit)."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class StubPipeline:
            async def ingest(self, signal: object) -> object:
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1, layers_buffered=[], layers_updated=[])

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = StubPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "bvid": "BVdwell",
                "title": "深度教程",
                "watch_seconds": 600,
                "video_duration_seconds": 700,
                "request_id": "click-with-dwell",
            },
        )

        assert response.status_code == 200
        assert memory.events, "click should be persisted"
        ev = memory.events[0]
        assert ev["event_type"] == "click"
        assert ev["metadata"]["watch_seconds"] == 600
        assert ev["metadata"]["video_duration_seconds"] == 700
        assert ev["metadata"]["source"] == "recommendation_click"

    def test_recommendation_click_endpoint_persists_without_dwell_fields(self) -> None:
        """No dwell fields supplied → click still persists (storage will
        classify it as unknown / missing_dwell, but the endpoint must
        not require the fields)."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class StubPipeline:
            async def ingest(self, signal: object) -> object:
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1, layers_buffered=[], layers_updated=[])

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = StubPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "bvid": "BVnoDwell",
                "title": "未知",
                "request_id": "click-without-dwell",
            },
        )

        assert response.status_code == 200
        assert memory.events, "click should still persist without dwell"
        ev = memory.events[0]
        assert "watch_seconds" not in ev["metadata"]
        assert "video_duration_seconds" not in ev["metadata"]

    def test_recommendation_click_endpoint_accepts_bvid_without_db_lookup(self) -> None:
        """When no recommendation_id is supplied, use the bvid from the payload directly."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None  # should not be called

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1)

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "bvid": "BV1DIRECT",
                "title": "直接点击",
                "request_id": "click-direct-bvid",
            },
        )

        assert response.status_code == 200
        assert response.json()["bvid"] == "BV1DIRECT"
        assert soul_engine.pipeline.ingested == []

    def test_recommendation_click_endpoint_rejects_missing_bvid(self) -> None:
        """Without a bvid (either from payload or DB lookup), return 422."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                pass

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None  # unknown recommendation

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=None,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"recommendation_id": 999, "request_id": "click-missing-bvid"},
        )

        assert response.status_code == 422
        assert "bvid" in response.json()["detail"].lower()

    def test_recommendation_click_endpoint_survives_pipeline_exception(self) -> None:
        """If the pipeline raises during ingest, the endpoint should still return 200.

        A click is user-visible — we must never propagate a backend failure back
        to the extension popup. The click is already persisted via propagate_event.
        """
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class BrokenPipeline:
            async def ingest(self, signal: object) -> object:
                raise RuntimeError("pipeline is broken")

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = BrokenPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "bvid": "BVresilient",
                "title": "即便后端出错也不应阻塞",
                "request_id": "click-resilient",
            },
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        # Click should still have been persisted as an event.
        assert len(memory.events) == 1
        assert memory.events[0]["metadata"]["bvid"] == "BVresilient"
        # But layers_updated should be empty because ingest raised.
        assert response.json()["layers_updated"] == []

    def test_delight_like_records_feedback_without_consuming_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        memory = _EventPersistenceSpy()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "like"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "liked"
        assert [event["event_type"] for event in memory.events] == ["feedback"]
        assert database.notified == []
        assert any("feedback_type='like'" in query for query, _params in database.writes)

    def test_delight_chat_records_context_without_consuming_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.notified: list[str] = []

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        class FakeMemory:
            def __init__(self) -> None:
                self.updates: list[dict[str, object]] = []

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return self.updates

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates

        class FakeDialogue:
            async def respond(self, message: str, *, scope: str = "chat", turn_id: str = "") -> str:
                assert "惊喜推荐" in message
                return "继续聊"

        database = FakeDatabase()
        app = create_app(
            memory_manager=FakeMemory(),
            database=database,
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={
                "bvid": "BV1DL",
                "title": "惊喜",
                "response": "chat",
                "message": "这个方向挺有意思",
            },
        )

        assert response.status_code == 200
        assert response.json()["action"] == "chat"
        assert database.notified == []

    @pytest.mark.parametrize(
        ("endpoint", "payload", "identity_key", "identity_value"),
        [
            (
                "/api/delight/respond",
                {"bvid": "BV1FAIL", "title": "惊喜", "response": "chat", "message": "聊聊"},
                "bvid",
                "BV1FAIL",
            ),
            (
                "/api/interest-probes/respond",
                {"domain": "建筑美学", "response": "chat", "message": "聊聊"},
                "domain",
                "建筑美学",
            ),
            (
                "/api/avoidance-probes/respond",
                {"domain": "浅层热点复读", "response": "chat", "message": "聊聊"},
                "domain",
                "浅层热点复读",
            ),
        ],
    )
    def test_contextual_chat_failure_is_safe_and_has_no_success_side_effects(
        self,
        endpoint: str,
        payload: dict[str, str],
        identity_key: str,
        identity_value: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm.service import LLMResponseContentError

        class FakeDialogue:
            async def respond(
                self, _message: str, *, scope: str = "chat", turn_id: str = ""
            ) -> str:
                raise LLMResponseContentError("LLM returned an empty response")

        class FakeMemory:
            def __init__(self) -> None:
                self.updates: list[dict[str, object]] = []

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.updates = list(updates)

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemory()
        event_hub = FakeEventHub()
        speculator = object()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(
                _speculator=speculator,
                _avoidance_speculator=speculator,
            ),
            dialogue=FakeDialogue(),
            runtime_controller=SimpleNamespace(event_hub=event_hub),
        )
        client = TestClient(app)

        response = client.post(endpoint, json=payload)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"ok", "action", identity_key, "reply"}
        assert body == {
            "ok": False,
            "action": "chat",
            identity_key: identity_value,
            "reply": body["reply"],
        }
        assert "空响应" in body["reply"]
        assert "LLM returned an empty response" not in body["reply"]
        assert memory.updates == []
        assert event_hub.events == []

    def test_delight_dislike_marks_candidate_consumed(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        memory = _EventPersistenceSpy()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "dislike"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "disliked"
        assert [event["event_type"] for event in memory.events] == ["feedback"]
        assert database.notified == ["BV1DL"]
        assert any("feedback_type='dislike'" in query for query, _params in database.writes)

    def test_delight_view_marks_candidate_read_without_feedback(self) -> None:
        """Browsing a delight marks it read (no re-hydration) like the rec pool's shown flag."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        memory = _EventPersistenceSpy()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "view"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "viewed"
        assert database.notified == ["BV1DL"]
        # view is read-marking only — no feedback_type write, no learning purge.
        assert database.writes == []

    def test_delight_pending_batch_keeps_liked_candidates_with_state(self) -> None:
        """Liked delights survive queue re-hydration and come back as state=liked."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                self.calls.append(
                    {
                        "min_delight_score": min_delight_score,
                        "limit": limit,
                        "include_liked": include_liked,
                    }
                )
                return [
                    {
                        "bvid": "BV1LIKED",
                        "title": "已喜欢的惊喜",
                        "delight_reason": "liked reason",
                        "delight_score": 0.95,
                        "delight_hook": "liked hook",
                        "feedback_type": "like",
                    },
                    {
                        "bvid": "BV1FRESH",
                        "title": "新惊喜",
                        "delight_reason": "fresh reason",
                        "delight_score": 0.94,
                        "delight_hook": "fresh hook",
                        "feedback_type": "",
                    },
                ]

        database = FakeDatabase()
        memory = _EventPersistenceSpy()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/delight/pending-batch")

        assert response.status_code == 200
        items = response.json()["items"]
        assert [(item["bvid"], item["state"]) for item in items] == [
            ("BV1LIKED", "liked"),
            ("BV1FRESH", "pending"),
        ]
        assert database.calls and database.calls[0]["include_liked"] is True

    def test_delight_pending_surfaces_publication_fields(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_pending_delight(self) -> dict[str, object]:
                return {
                    "bvid": "BV1DELIGHT",
                    "title": "惊喜候选",
                    "published_at": "2026-07-08T06:30:00Z",
                    "published_label": "3 days ago",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.get("/api/delight/pending")

        assert response.status_code == 200
        assert_publication(response.json()["item"])

    def test_delight_manual_trigger_publishes_publication_fields(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
            ) -> list[dict[str, object]]:
                return [
                    {
                        "bvid": "BV1DELIGHT",
                        "title": "惊喜候选",
                        "delight_score": 0.95,
                        "published_at": "2026-07-08T06:30:00Z",
                        "published_label": "3 days ago",
                        "share_count": 321,
                    }
                ]

        class FakeEventHub:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def publish(self, event: dict[str, object]) -> bool:
                self.events.append(event)
                return True

        event_hub = FakeEventHub()
        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
            runtime_event_hub=event_hub,
        )
        client = TestClient(app)

        response = client.post("/api/delight/trigger", json={"count": 1})

        assert response.status_code == 200
        assert_publication(event_hub.events[0])
        assert event_hub.events[0]["share_count"] == 321

    def test_delight_pending_batch_surfaces_body_text_and_content_type(self) -> None:
        """The delight card derives a readable title for legacy answer_<id> rows
        from body_text/content_type (issue #79), so the batch payload must carry
        both fields through — the delight card was the exact
        <h3 id="delightTitle">answer_<id> the report screenshotted."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                return [
                    {
                        "bvid": "answer:2001",
                        "title": "answer_2001",
                        "delight_reason": "讲透了数据需求",
                        "delight_score": 0.95,
                        "delight_hook": "偷偷翻到一条好东西",
                        "content_type": "answer",
                        "body_text": "深度学习为什么需要这么多数据？其实关键在于泛化。",
                        "source_platform": "zhihu",
                        "feedback_type": "",
                    },
                ]

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/delight/pending-batch").json()["items"][0]
        assert item["content_type"] == "answer"
        assert item["body_text"].startswith("深度学习为什么需要这么多数据")

    def test_delight_pending_batch_surfaces_engagement_stats(self) -> None:
        """The delight card renders the same ▶/👍/💬 row as the grid, so the
        batch payload must carry the engagement counts from content_cache
        (field report 2026-07-07: some cards showed stats, the surprise card
        never did)."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                return [
                    {
                        "bvid": "BV1stats",
                        "title": "带统计的候选",
                        "delight_score": 0.95,
                        "source_platform": "bilibili",
                        "view_count": 69000,
                        "like_count": 3200,
                        "comment_count": 880,
                        "danmaku_count": 150,
                        "favorite_count": 12,
                        "published_at": "2026-07-08T06:30:00Z",
                        "published_label": "3 days ago",
                        "feedback_type": "",
                    },
                ]

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/delight/pending-batch").json()["items"][0]
        assert item["view_count"] == 69000
        assert item["like_count"] == 3200
        assert item["comment_count"] == 880
        assert item["danmaku_count"] == 150
        assert item["favorite_count"] == 12
        assert_publication(item)

    def test_delight_pending_batch_maps_xhs_collect_to_favorite(self) -> None:
        """Xiaohongshu stores 收藏 in collect_count, but the card's ⭐ renders
        favorite_count — so favorite_count falls back to collect_count, letting
        XHS favorites show like every other platform (field report 2026-07-07)."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                return [
                    {
                        "bvid": "xhsnote1",
                        "title": "小红书笔记",
                        "delight_score": 0.9,
                        "source_platform": "xiaohongshu",
                        "like_count": 3200,
                        "comment_count": 880,
                        "collect_count": 455,  # XHS 收藏 lands here, favorite_count absent
                        "feedback_type": "",
                    },
                ]

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        client = TestClient(app)

        item = client.get("/api/delight/pending-batch").json()["items"][0]
        assert item["favorite_count"] == 455  # surfaced from collect_count
        assert item["like_count"] == 3200
        assert item["comment_count"] == 880

    def test_delight_pending_batch_uses_configured_default_limit(self) -> None:
        """Clients that omit ``limit`` should inherit the shared queue setting."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                self.calls.append(
                    {
                        "min_delight_score": min_delight_score,
                        "limit": limit,
                        "include_liked": include_liked,
                    }
                )
                return []

        database = FakeDatabase()
        app = create_app(
            memory_manager=object(),
            database=database,
            soul_engine=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(
            scheduler=SimpleNamespace(delight_queue_limit=7)
        )
        client = TestClient(app)

        response = client.get("/api/delight/pending-batch")

        assert response.status_code == 200
        assert database.calls[0]["limit"] == 7

    def test_delight_pending_batch_query_limit_overrides_config_default(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def get_delight_candidates(
                self,
                *,
                min_delight_score: float,
                limit: int,
                include_liked: bool = False,
            ) -> list[dict[str, object]]:
                self.calls.append(limit)
                return []

        database = FakeDatabase()
        app = create_app(
            memory_manager=object(),
            database=database,
            soul_engine=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(
            scheduler=SimpleNamespace(delight_queue_limit=7)
        )
        client = TestClient(app)

        response = client.get("/api/delight/pending-batch?limit=11")

        assert response.status_code == 200
        assert database.calls == [11]

    def test_delight_dismiss_marks_candidate_consumed(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []
                self.seen: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

            def mark_delight_seen(self, bvid: str) -> bool:
                self.seen.append(bvid)
                return True

        database = FakeDatabase()
        memory = _EventPersistenceSpy()
        app = create_app(memory_manager=memory, database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "dismiss"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "dismissed"
        assert [event["event_type"] for event in memory.events] == ["feedback"]
        assert database.seen == ["BV1DL"]
        assert database.notified == []
        assert database.writes == []

    def test_delight_dismiss_reports_persistence_failure(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def mark_delight_seen(self, bvid: str) -> bool:
                raise RuntimeError(f"write failed for {bvid}")

        memory = _EventPersistenceSpy()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1FAIL", "title": "惊喜", "response": "dismiss"},
        )

        assert response.status_code == 500
        assert [event["event_type"] for event in memory.events] == ["feedback"]
        assert response.json() == {
            "ok": False,
            "action": "dismiss",
            "bvid": "BV1FAIL",
            "error": "persist_failed",
        }

    def test_get_config_returns_llm_and_embedding_settings(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="gemini",
                fallback_provider="openai",
                openai=LLMProviderConfig(api_key="test-openai-key"),
                gemini=LLMProviderConfig(api_key="test-gemini-key", model="gemini-2.5-flash"),
                embedding=EmbeddingConfig(
                    provider="gemini",
                    model="gemini-embedding-001",
                    similarity_threshold=0.85,
                    fallback_enabled=True,
                ),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/config", params={"reveal_keys": "true"})

        assert response.status_code == 200
        data = response.json()

        # LLM provider fields
        assert data["llm"]["default_provider"] == "gemini"
        assert data["llm"]["fallback_provider"] == "openai"
        assert "fallback_enabled" not in data["llm"]  # removed legacy flag
        assert data["llm"]["gemini"]["api_key"] != "test-gemini-key"
        assert "*" in data["llm"]["gemini"]["api_key"]
        assert data["llm"]["gemini"]["model"] == "gemini-2.5-flash"

        # Embedding fields
        assert data["llm"]["embedding"]["provider"] == "gemini"
        assert data["llm"]["embedding"]["model"] == "gemini-embedding-001"
        assert data["llm"]["embedding"]["similarity_threshold"] == 0.85
        assert data["llm"]["embedding"]["fallback_enabled"] is True

    def test_get_config_masks_api_keys_by_default(
        self,
        monkeypatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="sk-abcdef1234567890xyzw", model="gpt-4o"),
            ),
        )
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/config")

        assert response.status_code == 200
        data = response.json()
        # Key should be masked (not equal to the original)
        assert data["llm"]["openai"]["api_key"] != "sk-abcdef1234567890xyzw"
        assert "****" in data["llm"]["openai"]["api_key"] or "*" in data["llm"]["openai"]["api_key"]

    def test_put_config_updates_embedding_settings(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
                embedding=EmbeddingConfig(
                    provider="",
                    model="gemini-embedding-001",
                    similarity_threshold=0.82,
                ),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        # Patch load_config to return our config
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        # Patch save_config to write to our temp path
        saved_configs: list[Config] = []

        def fake_save(c, path=None):
            saved_configs.append(c)
            save_config(c, config_path)

        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            fake_save,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "ollama",
                    "fallback_enabled": True,
                    "embedding": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "similarity_threshold": 0.78,
                        "fallback_enabled": True,
                    },
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["ok"] is True
        # Chat-side "fallback_enabled" from legacy clients is accepted but
        # ignored and no longer echoed (removed field); embedding keeps its.
        assert "fallback_enabled" not in data["config"]["llm"]
        assert data["config"]["llm"]["embedding"]["fallback_enabled"] is True

        # Verify the embedding was updated on the config object
        assert cfg.llm.embedding.provider == "openai"
        assert cfg.llm.embedding.model == "text-embedding-3-small"
        assert cfg.llm.embedding.similarity_threshold == 0.78

    def test_put_config_persists_twitter_enable_and_pool_share(self, monkeypatch, tmp_path) -> None:
        """PUT /api/config must persist sources.twitter (enable + budgets) and
        the twitter pool share — previously the handler silently dropped the
        whole sources.twitter block, so the settings-page X toggle was lost on
        reload."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            ),
        )
        cfg.sources.twitter.enabled = False
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "sources": {"twitter": {"enabled": True, "daily_feed_budget": 9}},
                "scheduler": {"pool_source_shares": {"bilibili": 8, "twitter": 4}},
            },
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["ok"] is True
        # Write path: no longer dropped.
        assert cfg.sources.twitter.enabled is True
        assert cfg.sources.twitter.daily_feed_budget == 9
        assert cfg.scheduler.pool_source_shares["twitter"] == 4
        # Read path: surfaced in the response so the settings page can reload it.
        assert data["config"]["sources"]["twitter"]["enabled"] is True
        assert data["config"]["scheduler"]["pool_source_shares"]["twitter"] == 4

    def test_put_config_persists_reddit_modes_budgets_and_pool_share(
        self, monkeypatch, tmp_path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "sources": {
                    "reddit": {
                        "enabled": True,
                        "backend": "rdt",
                        "source_modes": ["search", "hot", "subreddit", "related"],
                        "daily_search_budget": 8,
                        "daily_hot_budget": 3,
                        "daily_subreddit_budget": 4,
                        "daily_related_budget": 5,
                        "request_interval_seconds": 6,
                        "min_interval_minutes": 45,
                    }
                },
                "scheduler": {"pool_source_shares": {"bilibili": 8, "reddit": 3}},
            },
        )

        assert response.status_code == 202, response.text
        data = response.json()
        assert data["ok"] is True
        assert cfg.sources.reddit.enabled is True
        assert cfg.sources.reddit.backend == "rdt"
        assert cfg.sources.reddit.source_modes == ("search", "hot", "subreddit", "related")
        assert cfg.sources.reddit.daily_subreddit_budget == 4
        assert cfg.scheduler.pool_source_shares["reddit"] == 3
        assert data["config"]["sources"]["reddit"]["enabled"] is True
        assert data["config"]["sources"]["reddit"]["daily_subreddit_budget"] == 4
        assert data["config"]["scheduler"]["pool_source_shares"]["reddit"] == 3

    def test_put_config_persists_and_validates_bangumi_source(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            )
        )
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "sources": {
                    "bangumi": {
                        "enabled": True,
                        "username": " sai ",
                        "subject_types": ["anime", "book", "music"],
                        "source_modes": ["search", "ranked", "latest"],
                        "daily_search_budget": 21,
                        "daily_ranked_budget": 8,
                        "daily_latest_budget": 5,
                        "request_interval_seconds": 2,
                        "min_interval_minutes": 30,
                        "bootstrap_limit": 250,
                    }
                },
                "scheduler": {"pool_source_shares": {"bilibili": 8, "bangumi": 3}},
            },
        )

        assert response.status_code == 202, response.text
        assert cfg.sources.bangumi.enabled is True
        assert cfg.sources.bangumi.username == "sai"
        assert cfg.sources.bangumi.subject_types == ("anime", "book", "music")
        assert cfg.sources.bangumi.source_modes == ("search", "ranked", "latest")
        assert cfg.sources.bangumi.bootstrap_limit == 250
        assert cfg.scheduler.pool_source_shares["bangumi"] == 3
        assert response.json()["config"]["sources"]["bangumi"]["username"] == "sai"

        invalid = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"username": "bad/name"}}},
        )
        assert invalid.status_code == 400
        assert "username" in invalid.json()["detail"].lower()

        invalid_mode = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"source_modes": ["hot"]}}},
        )
        assert invalid_mode.status_code == 400
        assert "source_modes" in invalid_mode.json()["detail"]

    def test_put_config_bounds_and_normalizes_v2ex_source(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            )
        )
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        unsafe_slug = client.put(
            "/api/config",
            json={"sources": {"v2ex": {"node_allowlist": ["programmer", "../private"]}}},
        )
        assert unsafe_slug.status_code == 400
        assert "node_allowlist" in unsafe_slug.json()["detail"]

        oversized = client.put(
            "/api/config",
            json={"sources": {"v2ex": {"max_topic_chars": 20_001}}},
        )
        assert oversized.status_code == 400
        assert "max_topic_chars" in oversized.json()["detail"]

        unknown = client.put(
            "/api/config",
            json={"sources": {"v2ex": {"max_pages": 20}}},
        )
        assert unknown.status_code == 400
        assert "max_pages" in unknown.json()["detail"]

        valid = client.put(
            "/api/config",
            json={
                "sources": {
                    "v2ex": {
                        "enabled": True,
                        "source_modes": ["SEARCH", "node", "search"],
                        "tab_modes": ["Tech", "QNA"],
                        "node_allowlist": ["Programmer", "programmer"],
                        "max_topic_chars": 20_000,
                    }
                }
            },
        )
        assert valid.status_code == 202, valid.text
        source = valid.json()["config"]["sources"]["v2ex"]
        assert source["source_modes"] == ["search", "node"]
        assert source["tab_modes"] == ["tech", "qna"]
        assert source["node_allowlist"] == ["programmer"]
        assert source["max_topic_chars"] == 20_000

    def _bangumi_token_put_app(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config
        from openbiliclaw.storage.database import Database

        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            )
        )
        cfg.sources.bangumi.enabled = True
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )
        database = Database(tmp_path / "bangumi-token-put.db")
        database.initialize()
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        return cfg, database, TestClient(app)

    def test_put_config_bangumi_token_validates_live_and_writes_username(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.runtime.bangumi_producer import (
            BangumiDiscoveryProducer,
            _persist_token_rejection,
            _read_token_rejection,
            _token_fingerprint,
        )

        cfg, database, client = self._bangumi_token_put_app(monkeypatch, tmp_path)

        async def _fake_resolve(token, **_kw):
            assert token == "live-token"
            return "resolveduser"

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _fake_resolve,
        )
        # Pre-seed a stale rejection marker; a successful save must clear it.
        BangumiDiscoveryProducer(
            database=database, soul_engine=object(), client=object(), enabled=True
        )._ensure_tables()
        _persist_token_rejection(database, _token_fingerprint("old-token"))

        response = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"access_token": "live-token"}}},
        )

        assert response.status_code == 202, response.text
        assert cfg.sources.bangumi.access_token == "live-token"
        # /v0/me is the source of truth for the username.
        assert cfg.sources.bangumi.username == "resolveduser"
        assert _read_token_rejection(database) is None

    def test_put_config_bangumi_token_401_rejected(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        cfg, _database, client = self._bangumi_token_put_app(monkeypatch, tmp_path)

        async def _reject(_token, **_kw):
            raise BangumiAPIError("unauthorized", "denied", status_code=401)

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _reject,
        )

        response = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"access_token": "bad-token"}}},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_bangumi_access_token"
        # A rejected token is never persisted.
        assert cfg.sources.bangumi.access_token == ""

    def test_put_config_bangumi_token_check_failed_on_network(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        cfg, _database, client = self._bangumi_token_put_app(monkeypatch, tmp_path)

        async def _down(_token, **_kw):
            raise BangumiAPIError("network_error", "unreachable")

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _down,
        )

        response = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"access_token": "some-token"}}},
        )

        assert response.status_code == 502
        assert response.json()["error"] == "bangumi_token_check_failed"
        assert cfg.sources.bangumi.access_token == ""

    def test_put_config_bangumi_clear_token_is_offline_and_clears_marker(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.runtime.bangumi_producer import (
            BangumiDiscoveryProducer,
            _persist_token_rejection,
            _read_token_rejection,
            _token_fingerprint,
        )

        cfg, database, client = self._bangumi_token_put_app(monkeypatch, tmp_path)
        cfg.sources.bangumi.access_token = "existing"

        def _boom(*_a, **_kw):
            raise AssertionError("clearing a token must not hit the network")

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _boom,
        )
        BangumiDiscoveryProducer(
            database=database, soul_engine=object(), client=object(), enabled=True
        )._ensure_tables()
        _persist_token_rejection(database, _token_fingerprint("existing"))

        response = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"access_token": ""}}},
        )

        assert response.status_code == 202, response.text
        assert cfg.sources.bangumi.access_token == ""
        assert _read_token_rejection(database) is None

    def test_put_config_bangumi_masked_or_omitted_token_no_network(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg, _database, client = self._bangumi_token_put_app(monkeypatch, tmp_path)
        cfg.sources.bangumi.access_token = "keepme"

        def _boom(*_a, **_kw):
            raise AssertionError("unchanged token must not trigger a /v0/me call")

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _boom,
        )

        # Omitted key: token untouched.
        omitted = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"username": "sai"}}},
        )
        assert omitted.status_code == 202, omitted.text
        assert cfg.sources.bangumi.access_token == "keepme"

        # Masked echo (contains the **** mask marker): treated as unchanged.
        masked = client.put(
            "/api/config",
            json={"sources": {"bangumi": {"access_token": "keep****eep"}}},
        )
        assert masked.status_code == 202, masked.text
        assert cfg.sources.bangumi.access_token == "keepme"

    def test_put_config_updates_embedding_credentials(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        """v0.3.32+ — embedding owns api_key/base_url. PUT /api/config
        must accept the new fields and round-trip them through GET (with
        the api_key masked on the way out)."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(api_key="ds-key", model="deepseek-v4-flash"),
                embedding=EmbeddingConfig(),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        # PUT — supply dedicated embedding credentials.
        put_resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "api_key": "sk-dedicated-embedding-xyz1234567890",
                        "base_url": "https://embed.example.com/v1",
                    },
                },
            },
        )
        assert put_resp.status_code == 202
        assert cfg.llm.embedding.api_key == "sk-dedicated-embedding-xyz1234567890"
        assert cfg.llm.embedding.base_url == "https://embed.example.com/v1"

        # GET (default — masked). api_key contains '*' but never the raw key.
        get_resp = client.get("/api/config")
        emb = get_resp.json()["llm"]["embedding"]
        assert emb["provider"] == "openai"
        assert emb["model"] == "text-embedding-3-small"
        assert emb["base_url"] == "https://embed.example.com/v1"
        assert "*" in emb["api_key"]
        assert "sk-dedicated-embedding-xyz1234567890" not in emb["api_key"]

        # PUT again with the masked key echoed back — must NOT overwrite
        # the real key with asterisks.
        masked_echo = emb["api_key"]
        client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "api_key": masked_echo,
                        "model": "text-embedding-3-large",
                    },
                },
            },
        )
        # Real key preserved; model still updated.
        assert cfg.llm.embedding.api_key == "sk-dedicated-embedding-xyz1234567890"
        assert cfg.llm.embedding.model == "text-embedding-3-large"

    def test_put_config_updates_provider_api_key_and_model(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="", model="gpt-4o"),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "deepseek",
                    "deepseek": {
                        "api_key": "sk-new-deepseek-key",
                        "model": "deepseek-chat",
                        "base_url": "https://api.deepseek.com",
                    },
                },
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["ok"] is True

        # Verify provider switch and key update
        assert cfg.llm.default_provider == "deepseek"
        assert cfg.llm.deepseek.api_key == "sk-new-deepseek-key"
        assert cfg.llm.deepseek.model == "deepseek-chat"
        assert cfg.llm.deepseek.base_url == "https://api.deepseek.com"


class TestDialogueConfirmationCards:
    """Durable cards, serialized settlement, and terminal projection."""

    @staticmethod
    def _build(tmp_path: Path):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine

        class Registry:
            async def complete(self, *_args: object, **_kwargs: object) -> LLMResponse:
                return LLMResponse(content="[]", provider="fake")

        class Dialogue:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def respond(
                self,
                message: str,
                *,
                scope: str = "chat",
                turn_id: str = "",
                session: str = "",
            ) -> str:
                del turn_id
                self.calls.append((scope, session))
                return f"回复：{message}"

        memory = MemoryManager(tmp_path / "data")
        memory.initialize()
        engine = SoulEngine(llm=Registry(), memory=memory)  # type: ignore[arg-type]
        dialogue = Dialogue()
        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=engine,
            dialogue=dialogue,
        )
        return TestClient(app), memory, engine, dialogue

    @staticmethod
    def _seed_hypothesis(memory: object, hypothesis: str, confidence: float = 0.66) -> str:
        from openbiliclaw.soul.identity import insight_hash8

        layer = memory.get_layer("insight")
        items = list(layer.data.get("hypotheses", []))
        items.append(
            {
                "hypothesis": hypothesis,
                "evidence": [f"依据：{hypothesis}"],
                "confidence": confidence,
                "validated": False,
                "created_at": "2026-07-22",
            }
        )
        layer.data["hypotheses"] = items
        layer.save()
        return insight_hash8(hypothesis)

    @staticmethod
    def _create_card(
        client: object,
        *,
        turn_id: str,
        ref: str,
        hypothesis: str,
        session: str = "popup",
    ) -> dict[str, object]:
        response = client.post(
            "/api/chat/turns",
            json={
                "turn_id": turn_id,
                "session": session,
                "scope": "hypothesis",
                "subject_id": ref,
                "subject_title": hypothesis,
                "message": "阿b 的猜测",
                "payload": {"evidence_refs": [f"依据：{hypothesis}"]},
            },
        )
        assert response.status_code == 200
        return response.json()

    def test_card_actions_and_legacy_submit_typed_jobs_to_actual_worker(
        self,
        tmp_path: Path,
    ) -> None:
        """Q1/F3: all card actions and legacy feedback execute in the one worker."""
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJob, DialogueJobKind

        client, memory, _engine, _dialogue = self._build(tmp_path)
        queue = client.app.state.runtime_context.dialogue_settlement_queue
        assert queue is not None
        real_dispatcher = queue._dispatcher
        observed: list[tuple[DialogueJobKind, bool]] = []

        async def observe(job: DialogueJob):
            observed.append((job.kind, asyncio.current_task() is queue.worker_task))
            return await real_dispatcher(job)

        queue._dispatcher = observe
        for action in ("confirm", "reject", "defer", "discuss"):
            hypothesis = f"typed-job-{action}"
            ref = self._seed_hypothesis(memory, hypothesis)
            turn_id = f"typed-job-{action}"
            self._create_card(
                client,
                turn_id=turn_id,
                ref=ref,
                hypothesis=hypothesis,
            )
            response = client.post(
                f"/api/chat/cards/{turn_id}/action",
                json={"action": action},
            )
            assert response.status_code == 200

        legacy_hypothesis = "typed-job-legacy"
        self._seed_hypothesis(memory, legacy_hypothesis)
        legacy = client.post(
            "/api/insights/feedback",
            json={"hypothesis": legacy_hypothesis, "signal": "confirm"},
        )
        # The `discuss` action above left its card owning the dialogue anchor,
        # so this legacy settle is refused. It must still be refused *honestly*
        # (409, nothing written) rather than the old silent 200 {"ok":true}.
        # The point of this test is unchanged: the typed job is still submitted
        # to — and executed by — the one worker, which `observed` below asserts.
        assert legacy.status_code == 409, legacy.text
        assert observed == [
            (DialogueJobKind.SETTLE_HYPOTHESIS, True),
            (DialogueJobKind.SETTLE_HYPOTHESIS, True),
            (DialogueJobKind.CARD_DEFER, True),
            (DialogueJobKind.CARD_DISCUSS, True),
            (DialogueJobKind.SETTLE_HYPOTHESIS, True),
        ]

    def test_hypothesis_card_is_completed_structured_turn_and_skips_worker(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, dialogue = self._build(tmp_path)
        hypothesis = "用户偏爱追问底层原理"
        ref = self._seed_hypothesis(memory, hypothesis)

        card = self._create_card(
            client,
            turn_id="card-completed",
            ref=ref,
            hypothesis=hypothesis,
        )
        time.sleep(0.03)

        assert card["status"] == "completed"
        assert card["scope"] == "hypothesis"
        assert card["payload"] == {
            "type": "card",
            "kind": "hypothesis",
            "ref": ref,
            "title": hypothesis,
            "evidence_refs": [f"依据：{hypothesis}"],
            "actions": ["confirm", "reject", "discuss", "defer"],
            "state": "pending",
        }
        assert dialogue.calls == []

    @pytest.mark.parametrize(
        ("action", "terminal", "validated"),
        [("confirm", "confirmed", True), ("reject", "rejected", False)],
    )
    def test_confirm_and_reject_apply_serialized_settlement(
        self,
        tmp_path: Path,
        action: str,
        terminal: str,
        validated: bool,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = f"假设-{action}"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id=f"card-{action}", ref=ref, hypothesis=hypothesis)

        response = client.post(
            f"/api/chat/cards/card-{action}/action",
            json={"action": action},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "applied"
        assert response.json()["state"] == terminal
        settlement = memory._database.get_card_settlement(ref)
        assert settlement is not None
        assert settlement["applied"] == 1
        assert str(settlement["event_id"]).startswith("dialogue:")
        assert str(settlement["event_id"]).endswith(":event")
        stored = next(
            item
            for item in memory.get_layer("insight").data["hypotheses"]
            if item["hypothesis"] == hypothesis
        )
        assert stored["validated"] is validated
        events = memory.query_events(event_types=["feedback"])
        assert len(events) == 1
        assert json.loads(events[0]["metadata"])["settlement_ref"] == ref

    def test_defer_persists_cooldown_and_hides_from_pending_list(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户也许偏爱长视频"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id="card-defer", ref=ref, hypothesis=hypothesis)

        response = client.post(
            "/api/chat/cards/card-defer/action",
            json={"action": "defer"},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "deferred"
        assert memory._database.get_card_settlement(ref) is None
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["objects"][ref]["deferred_until"]
        pending_refs = {
            item["ref"] for item in client.get("/api/chat/pending-confirmations").json()["items"]
        }
        assert ref not in pending_refs

    def test_defer_expiry_returns_hypothesis_to_pending_list(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "冷却结束后可以重新确认"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id="card-defer-expiry", ref=ref, hypothesis=hypothesis)

        deferred = client.post(
            "/api/chat/cards/card-defer-expiry/action",
            json={"action": "defer"},
        )
        assert deferred.status_code == 200
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["objects"][ref]["deferred_until"] = ""
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        pending_refs = {
            item["ref"] for item in client.get("/api/chat/pending-confirmations").json()["items"]
        }
        assert ref in pending_refs

    @pytest.mark.parametrize(
        ("settle_action", "terminal_state"),
        [("confirm", "confirmed"), ("reject", "rejected")],
    )
    def test_defer_on_terminal_card_returns_truth_without_writing_cooldown(
        self,
        tmp_path: Path,
        settle_action: str,
        terminal_state: str,
    ) -> None:
        """F3: defer cannot contradict or add cooldown to a terminal card."""
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = f"终态后 defer-{settle_action}"
        ref = self._seed_hypothesis(memory, hypothesis)
        turn_id = f"terminal-defer-{settle_action}"
        self._create_card(client, turn_id=turn_id, ref=ref, hypothesis=hypothesis)
        settled = client.post(
            f"/api/chat/cards/{turn_id}/action",
            json={"action": settle_action},
        )
        assert settled.status_code == 200
        assert settled.json()["state"] == terminal_state
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        cooldown_before = state_path.read_bytes() if state_path.exists() else None

        deferred = client.post(
            f"/api/chat/cards/{turn_id}/action",
            json={"action": "defer"},
        )

        assert deferred.status_code == 200
        assert deferred.json() == {
            "ok": True,
            "outcome": "already_settled",
            "verdict": terminal_state,
            "state": terminal_state,
        }
        cooldown_after = state_path.read_bytes() if state_path.exists() else None
        assert cooldown_after == cooldown_before
        assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == terminal_state

    @pytest.mark.parametrize(
        ("failure_stage", "checkpoint"),
        [
            ("event", "after_event"),
            ("object", "after_object"),
            ("marker", "after_rebuild_marker"),
        ],
    )
    def test_fault_injection_retries_stable_effects_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        failure_stage: str,
        checkpoint: str,
    ) -> None:
        from fastapi.testclient import TestClient

        client, memory, engine, _dialogue = self._build(tmp_path)
        hypothesis = f"故障注入-{failure_stage}"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(
            client,
            turn_id=f"card-fault-{failure_stage}",
            ref=ref,
            hypothesis=hypothesis,
        )
        app = client.app
        client.close()
        failed = False

        def fail_checkpoint_once(observed: str, settlement_ref: str) -> None:
            nonlocal failed
            assert settlement_ref == ref
            if observed == checkpoint and not failed:
                failed = True
                raise RuntimeError(f"{failure_stage} fault")

        monkeypatch.setattr(
            engine,
            "_dialogue_settlement_checkpoint",
            fail_checkpoint_once,
        )
        with TestClient(app, raise_server_exceptions=False) as fault_client:
            first = fault_client.post(
                f"/api/chat/cards/card-fault-{failure_stage}/action",
                json={"action": "confirm"},
            )
            assert first.status_code == 500
            partial = memory._database.get_card_settlement(ref)
            assert partial is not None
            assert partial["applied"] == 0
            assert str(partial["event_id"]).endswith(":event")

            retry = fault_client.post(
                f"/api/chat/cards/card-fault-{failure_stage}/action",
                json={"action": "confirm"},
            )

        assert retry.status_code == 200
        assert retry.json()["outcome"] == "applied"
        final = memory._database.get_card_settlement(ref)
        assert final is not None
        assert final["applied"] == 1
        assert str(final["event_id"]).endswith(":event")
        assert len(memory.query_events(event_types=["feedback"])) == 1

    def test_unapplied_winner_retry_immediately_applies_original_winner(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户会追问证据链"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id="card-owner", ref=ref, hypothesis=hypothesis)
        self._create_card(
            client,
            turn_id="card-contender",
            ref=ref,
            hypothesis=hypothesis,
            session="webui",
        )
        assert memory._database.try_create_card_settlement(
            ref=ref,
            verdict="confirmed",
            turn_id="card-owner",
            payload={
                "kind": "hypothesis",
                "title": hypothesis,
                "action": "confirmed",
                "derived": [],
                "anchor_generation": 0,
                "source": "card_action",
            },
        )
        recovered_response = client.post(
            "/api/chat/cards/card-contender/action",
            json={"action": "reject"},
        )
        recovered = recovered_response.json()

        assert recovered_response.status_code == 200
        assert recovered["outcome"] == "applied"
        assert recovered["verdict"] == "confirmed"
        settlement = memory._database.get_card_settlement(ref)
        assert settlement is not None
        assert (settlement["verdict"], settlement["turn_id"], settlement["applied"]) == (
            "confirmed",
            "card-owner",
            1,
        )
        assert settlement["payload"]["source"] == "card_action"
        assert memory._database.get_chat_turn("card-owner")["payload"]["state"] == "confirmed"
        assert memory._database.get_chat_turn("card-contender")["payload"]["state"] == "confirmed"

    def test_applied_conflict_is_already_settled_and_refreshes_other_session(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户偏好机制分析"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id="card-popup", ref=ref, hypothesis=hypothesis)
        self._create_card(
            client,
            turn_id="card-webui",
            ref=ref,
            hypothesis=hypothesis,
            session="webui",
        )

        assert (
            client.post("/api/chat/cards/card-popup/action", json={"action": "confirm"}).status_code
            == 200
        )
        conflict = client.post("/api/chat/cards/card-webui/action", json={"action": "reject"})

        assert conflict.status_code == 200
        assert conflict.json()["outcome"] == "already_settled"
        assert conflict.json()["state"] == "confirmed"
        popup = client.get("/api/chat/turns", params={"session": "popup"}).json()["items"]
        webui = client.get("/api/chat/turns", params={"session": "webui"}).json()["items"]
        assert [item["turn_id"] for item in popup] == ["card-popup"]
        assert [item["turn_id"] for item in webui] == ["card-webui"]
        assert popup[0]["payload"]["state"] == webui[0]["payload"]["state"] == "confirmed"

    def test_blocked_worker_card_action_returns_processing_without_cancelling_job(
        self,
        tmp_path: Path,
    ) -> None:
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.dialogue_learn_queue import (
            DialogueJob,
            DialogueJobKind,
            DialogueJobResult,
        )

        initial_client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户重视排队后的最终一致性"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(
            initial_client,
            turn_id="card-processing",
            ref=ref,
            hypothesis=hypothesis,
        )
        app = initial_client.app
        initial_client.close()
        queue = app.state.runtime_context.dialogue_settlement_queue
        real_dispatcher = queue._dispatcher
        blocker_entered = threading.Event()
        release_blocker = threading.Event()
        submitted_action: list[DialogueJob] = []

        async def blocking_dispatcher(job: DialogueJob):
            if (
                job.kind is DialogueJobKind.LEARN
                and job.payload.get("source") == "task-3.3-blocker"
            ):
                blocker_entered.set()
                while not release_blocker.is_set():
                    await asyncio.sleep(0.005)
                return DialogueJobResult(outcome="completed")
            return await real_dispatcher(job)

        real_submit = queue.submit

        def observe_submit(
            kind: DialogueJobKind | str,
            payload: dict[str, object],
            *,
            completion: bool = False,
        ) -> DialogueJob | None:
            job = real_submit(kind, payload, completion=completion)
            if DialogueJobKind(kind) is DialogueJobKind.SETTLE_HYPOTHESIS and job is not None:
                submitted_action.append(job)
            return job

        queue._dispatcher = blocking_dispatcher
        queue.submit = observe_submit
        with TestClient(app) as client:
            client.portal.call(
                queue.submit,
                DialogueJobKind.LEARN,
                {"source": "task-3.3-blocker"},
            )
            assert blocker_entered.wait(timeout=1)

            started_at = time.monotonic()
            response = client.post(
                "/api/chat/cards/card-processing/action",
                json={"action": "confirm"},
            )
            elapsed = time.monotonic() - started_at

            assert response.status_code == 202
            assert response.json()["outcome"] == "processing"
            assert elapsed <= 1.5
            assert len(submitted_action) == 1
            assert submitted_action[0].completion is not None
            assert not submitted_action[0].completion.cancelled()
            assert not submitted_action[0].completion.done()

            release_blocker.set()
            deadline = time.monotonic() + 2.0
            durable_state = ""
            while time.monotonic() < deadline:
                durable = client.get("/api/chat/turns/card-processing")
                assert durable.status_code == 200
                durable_state = str(durable.json()["payload"]["state"])
                if durable_state == "confirmed":
                    break
                time.sleep(0.01)

        assert durable_state == "confirmed"
        assert submitted_action[0].completion is not None
        assert submitted_action[0].completion.done()
        assert not submitted_action[0].completion.cancelled()

    @pytest.mark.parametrize(
        "receipt_gap",
        ["no_receipt", "applied_0_winner", "applied_1_publication_only"],
    )
    def test_processing_job_lost_on_restart_can_be_resubmitted(
        self,
        tmp_path: Path,
        receipt_gap: str,
    ) -> None:
        import threading
        from contextlib import suppress

        from fastapi.testclient import TestClient

        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.soul.dialogue_learn_queue import DialogueJob, DialogueJobKind
        from openbiliclaw.soul.engine import SoulEngine

        initial_client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = f"重启重投-{receipt_gap}"
        ref = self._seed_hypothesis(memory, hypothesis)
        turn_id = f"lost-on-restart-{receipt_gap}"
        self._create_card(
            initial_client,
            turn_id=turn_id,
            ref=ref,
            hypothesis=hypothesis,
        )
        first_app = initial_client.app
        initial_client.close()
        first_queue = first_app.state.runtime_context.dialogue_settlement_queue
        real_dispatcher = first_queue._dispatcher
        blocker_entered = threading.Event()

        async def blocking_dispatcher(job: DialogueJob):
            if (
                job.kind is DialogueJobKind.LEARN
                and job.payload.get("source") == "restart-loss-blocker"
            ):
                blocker_entered.set()
                await asyncio.Event().wait()
            return await real_dispatcher(job)

        async def crash_and_discard_pending() -> None:
            first_queue._accepting = False
            first_queue._closed = True
            worker = first_queue._worker
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
                first_queue._worker = None
            while not first_queue._queue.empty():
                lost_job = first_queue._queue.get_nowait()
                first_queue._complete_cancelled(lost_job)
                first_queue._registry.release(lost_job.anchor_snapshot)
                first_queue._queue.task_done()
            first_queue._registry.clear()

        first_queue._dispatcher = blocking_dispatcher
        with TestClient(first_app) as first_runtime:
            first_runtime.portal.call(
                first_queue.submit,
                DialogueJobKind.LEARN,
                {"source": "restart-loss-blocker"},
            )
            assert blocker_entered.wait(timeout=1)
            first = first_runtime.post(
                f"/api/chat/cards/{turn_id}/action",
                json={"action": "confirm"},
            )
            assert first.status_code == 202
            assert first.json()["outcome"] == "processing"
            assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == "pending"
            first_runtime.portal.call(crash_and_discard_pending)

        winning_payload = {
            "kind": "hypothesis",
            "title": hypothesis,
            "action": "confirmed",
            "derived": [],
            "anchor_generation": 0,
            "source": "card_action",
        }
        if receipt_gap != "no_receipt":
            assert memory._database.try_create_card_settlement(
                ref=ref,
                verdict="confirmed",
                turn_id=turn_id,
                payload=winning_payload,
            )
        if receipt_gap == "applied_1_publication_only":
            memory._database.record_card_settlement_event_once(
                ref=ref,
                event={
                    "event_type": "feedback",
                    "title": hypothesis,
                    "metadata": {
                        "settlement_ref": ref,
                        "settlement_kind": "hypothesis",
                        "settlement_verdict": "confirmed",
                        "turn_id": turn_id,
                        "source": "card_action",
                    },
                },
            )
            layer = memory.get_layer("insight")
            for item in layer.data["hypotheses"]:
                if item["hypothesis"] == hypothesis:
                    item["validated"] = True
            layer.save()
            assert memory._database.complete_card_settlement(
                ref=ref,
                result={
                    "matched": True,
                    "hypothesis": hypothesis,
                    "signal": "confirm",
                    "validated": True,
                    "confidence": 0.66,
                },
            )
        assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == "pending"

        class Registry:
            async def complete(self, *_args: object, **_kwargs: object) -> LLMResponse:
                return LLMResponse(content="[]", provider="fake")

        class RestartedDialogue:
            async def respond(
                self,
                message: str,
                *,
                scope: str = "chat",
                turn_id: str = "",
                session: str = "",
            ) -> str:
                del scope, turn_id, session
                return f"回复：{message}"

        restarted_engine = SoulEngine(llm=Registry(), memory=memory)  # type: ignore[arg-type]
        object_calls = 0
        real_object_apply = restarted_engine._apply_dialogue_settlement_object

        async def count_object_apply(**kwargs: object):
            nonlocal object_calls
            object_calls += 1
            return await real_object_apply(**kwargs)

        restarted_engine._apply_dialogue_settlement_object = count_object_apply  # type: ignore[method-assign]
        restarted_app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=restarted_engine,
            dialogue=RestartedDialogue(),
        )
        with TestClient(restarted_app) as restarted:
            retry = restarted.post(
                f"/api/chat/cards/{turn_id}/action",
                json={"action": "confirm"},
            )
            assert retry.status_code == 200
            assert retry.json()["state"] == "confirmed"
            assert restarted.get(f"/api/chat/turns/{turn_id}").json()["payload"]["state"] == (
                "confirmed"
            )

        expected_object_calls = 0 if receipt_gap == "applied_1_publication_only" else 1
        assert object_calls == expected_object_calls
        settlement = memory._database.get_card_settlement(ref)
        assert settlement is not None
        assert settlement["applied"] == 1
        assert settlement["payload"] == winning_payload

    def test_get_reconcile_projects_applied_receipt_without_reapplying_object_semantics(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.dialogue_learn_queue import (
            DialogueDispatchReturn,
            DialogueJob,
            DialogueJobKind,
        )

        client, memory, engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户重视可复核证据"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(
            client,
            turn_id="reconcile-popup",
            ref=ref,
            hypothesis=hypothesis,
        )
        self._create_card(
            client,
            turn_id="reconcile-webui",
            ref=ref,
            hypothesis=hypothesis,
            session="webui",
        )
        app = client.app
        client.close()

        object_calls = 0
        derived_calls = 0
        rebuild_calls = 0
        real_object = engine.apply_feedback_object
        real_derived = engine._apply_dialogue_settlement_derived
        real_rebuild = engine._mark_rebuild_pending

        async def count_object(feedback: dict[str, object]) -> dict[str, object]:
            nonlocal object_calls
            object_calls += 1
            return await real_object(feedback)

        async def count_derived(
            *,
            settlement: dict[str, object],
            payload: dict[str, object],
        ) -> list[str]:
            nonlocal derived_calls
            derived_calls += 1
            return await real_derived(settlement=settlement, payload=payload)

        async def count_rebuild(trigger_refs: list[str]) -> None:
            nonlocal rebuild_calls
            rebuild_calls += 1
            await real_rebuild(trigger_refs)

        injected = False

        def fail_after_applied(checkpoint: str, settlement_ref: str) -> None:
            nonlocal injected
            assert settlement_ref == ref
            if checkpoint == "after_applied_before_projection" and not injected:
                injected = True
                raise RuntimeError("injected applied publication gap")

        monkeypatch.setattr(engine, "apply_feedback_object", count_object)
        monkeypatch.setattr(engine, "_apply_dialogue_settlement_derived", count_derived)
        monkeypatch.setattr(engine, "_mark_rebuild_pending", count_rebuild)
        monkeypatch.setattr(engine, "_dialogue_settlement_checkpoint", fail_after_applied)

        reconciled = threading.Event()
        handlers = app.state.runtime_context.dialogue_settlement_handlers
        runtime_reconcile = handlers[DialogueJobKind.CARD_RECONCILE]

        async def observe_reconcile(job: DialogueJob) -> DialogueDispatchReturn:
            result = await runtime_reconcile(job)
            reconciled.set()
            return result

        handlers[DialogueJobKind.CARD_RECONCILE] = observe_reconcile
        reconcile_queue = app.state.runtime_context.dialogue_settlement_queue
        worker_projection_calls = 0
        request_projection_calls = 0
        real_project = memory._database.project_applied_card_settlement

        def count_projection(settlement_ref: str) -> int:
            nonlocal request_projection_calls, worker_projection_calls
            if asyncio.current_task() is reconcile_queue.worker_task:
                worker_projection_calls += 1
            else:
                request_projection_calls += 1
            return real_project(settlement_ref)

        monkeypatch.setattr(
            memory._database,
            "project_applied_card_settlement",
            count_projection,
        )
        with TestClient(app, raise_server_exceptions=False) as runtime_client:
            first = runtime_client.post(
                "/api/chat/cards/reconcile-popup/action",
                json={"action": "confirm"},
            )
            assert first.status_code == 500
            receipt = memory._database.get_card_settlement(ref)
            assert receipt is not None and receipt["applied"] == 1
            popup_payload = memory._database.get_chat_turn("reconcile-popup")["payload"]
            webui_payload = memory._database.get_chat_turn("reconcile-webui")["payload"]
            assert popup_payload["state"] == "pending"
            assert webui_payload["state"] == "pending"
            assert (object_calls, derived_calls, rebuild_calls) == (1, 1, 1)

            first_get = runtime_client.get("/api/chat/turns/reconcile-popup")
            assert first_get.status_code == 200
            assert first_get.json()["payload"]["state"] == "pending"
            assert request_projection_calls == 0
            assert reconciled.wait(timeout=2)

            second_get = runtime_client.get("/api/chat/turns/reconcile-webui")

        assert second_get.status_code == 200
        assert second_get.json()["payload"]["state"] == "confirmed"
        assert worker_projection_calls == 1
        assert request_projection_calls == 0
        assert (object_calls, derived_calls, rebuild_calls) == (1, 1, 1)
        assert memory._database.get_chat_turn("reconcile-popup")["payload"]["state"] == "confirmed"
        assert memory._database.get_chat_turn("reconcile-webui")["payload"]["state"] == "confirmed"

    def test_discuss_worker_builds_anchor_and_failure_rolls_back(self, tmp_path: Path) -> None:
        client, memory, engine, _dialogue = self._build(tmp_path)
        first = "用户也许重视可证伪性"
        first_ref = self._seed_hypothesis(memory, first)
        self._create_card(client, turn_id="card-discuss-ok", ref=first_ref, hypothesis=first)

        response = client.post("/api/chat/cards/card-discuss-ok/action", json={"action": "discuss"})
        assert response.status_code == 200
        assert response.json()["state"] == "discussing"
        anchor = engine._dialogue_anchor_manager.current()
        assert anchor is not None
        assert anchor.origin_turn_id == "card-discuss-ok"
        payload = memory._database.get_chat_turn("card-discuss-ok")["payload"]
        assert payload["state"] == "discussing"
        assert "attempt_token" not in payload
        assert "discussing_at" not in payload

        second = "用户也许偏爱长链条论证"
        second_ref = self._seed_hypothesis(memory, second)
        self._create_card(client, turn_id="card-discuss-fail", ref=second_ref, hypothesis=second)
        original = engine._dialogue_anchor_manager.establish

        def fail_establish(**_kwargs: object) -> object:
            raise RuntimeError("anchor store unavailable")

        engine._dialogue_anchor_manager.establish = fail_establish  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError, match="anchor store unavailable"):
                client.post(
                    "/api/chat/cards/card-discuss-fail/action",
                    json={"action": "discuss"},
                )
        finally:
            engine._dialogue_anchor_manager.establish = original  # type: ignore[method-assign]

        rolled_back = memory._database.get_chat_turn("card-discuss-fail")["payload"]
        assert rolled_back["state"] == "pending"
        assert "attempt_token" not in rolled_back

    def test_orphan_discussion_get_submits_immediate_worker_reconcile(
        self,
        tmp_path: Path,
    ) -> None:
        import threading

        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户也许更信任一手资料"
        ref = self._seed_hypothesis(memory, hypothesis)
        self._create_card(client, turn_id="card-stale-discuss", ref=ref, hypothesis=hypothesis)
        assert memory._database.update_chat_turn_payload_state(
            "card-stale-discuss",
            expected_state="pending",
            new_state="discussing",
        )
        queue = client.app.state.runtime_context.dialogue_settlement_queue
        repaired = threading.Event()
        worker_calls: list[bool] = []
        real_update = memory._database.update_chat_turn_payload_state

        def observe_update(
            turn_id: str,
            *,
            expected_state: str,
            new_state: str,
        ) -> bool:
            if expected_state == "discussing" and new_state == "pending":
                worker_calls.append(asyncio.current_task() is queue.worker_task)
                repaired.set()
            return real_update(
                turn_id,
                expected_state=expected_state,
                new_state=new_state,
            )

        memory._database.update_chat_turn_payload_state = observe_update

        first = client.get("/api/chat/turns/card-stale-discuss")

        assert first.status_code == 200
        assert first.json()["payload"]["state"] == "discussing"
        assert repaired.wait(timeout=2)
        second = client.get("/api/chat/turns/card-stale-discuss")
        assert second.json()["payload"]["state"] == "pending"
        assert worker_calls == [True]

    def test_legacy_feedback_forwards_to_common_settlement_with_deprecated_source(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        hypothesis = "用户偏爱完整因果链"
        ref = self._seed_hypothesis(memory, hypothesis)

        response = client.post(
            "/api/insights/feedback",
            json={"hypothesis": hypothesis, "signal": "confirm"},
        )

        assert response.status_code == 200
        assert response.headers["deprecation"] == "true"
        settlement = memory._database.get_card_settlement(ref)
        assert settlement is not None and settlement["applied"] == 1
        rows = memory._database.query_profile_ledger(days=1, limit=20)
        settled = next(row for row in rows if row["write_point"] == "settle_insight")
        assert settled["source"] == "legacy_endpoint"


class TestPendingDialogueConfirmations:
    """Wave B Task 5: pending list, active open, and deterministic throws."""

    @staticmethod
    def _build(tmp_path: Path):  # type: ignore[no-untyped-def]
        return TestDialogueConfirmationCards._build(tmp_path)

    @staticmethod
    def _seed_hypothesis(memory: object, title: str, confidence: float = 0.66) -> str:
        return TestDialogueConfirmationCards._seed_hypothesis(memory, title, confidence)

    @staticmethod
    def _post_user_turn(
        client: object,
        *,
        turn_id: str,
        session: str = "popup",
        message: str = "继续聊聊",
    ) -> object:
        return client.post(
            "/api/chat/turns",
            json={
                "turn_id": turn_id,
                "session": session,
                "scope": "chat",
                "message": message,
            },
        )

    def test_pending_list_filters_high_priority_caps_three_and_has_count_only(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        high_refs = {
            self._seed_hypothesis(memory, "高优先假设一", 0.91),
            self._seed_hypothesis(memory, "高优先假设二", 0.81),
            self._seed_hypothesis(memory, "高优先假设三", 0.71),
            self._seed_hypothesis(memory, "高优先假设四", 0.61),
        }
        low_ref = self._seed_hypothesis(memory, "低置信假设", 0.59)
        validated_ref = self._seed_hypothesis(memory, "已经确认的假设", 0.95)
        insight = memory.get_layer("insight")
        for item in insight.data["hypotheses"]:
            if item["hypothesis"] == "已经确认的假设":
                item["validated"] = True
        insight.save()
        confusion_id = memory._database.insert_confusion(
            source="awareness",
            topic="高优先疑惑",
            observation="这次行为与长期偏好相反",
            interpretation_confidence=0.72,
            evidence_refs=["event-7"],
        )
        memory._database.insert_confusion(
            source="awareness",
            topic="低优先疑惑",
            observation="证据还很弱",
            interpretation_confidence=0.49,
        )

        response = client.get("/api/chat/pending-confirmations")
        count = client.get(
            "/api/chat/pending-confirmations",
            params={"count_only": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 3
        assert len(body["items"]) == 3
        refs = {item["ref"] for item in body["items"]}
        assert str(confusion_id) in refs
        assert refs <= high_refs | {str(confusion_id)}
        assert low_ref not in refs
        assert validated_ref not in refs
        assert count.status_code == 200
        assert count.json() == {"count": 3}

    def test_confusion_keeps_a_seat_when_every_hypothesis_scores_higher(
        self,
        tmp_path: Path,
    ) -> None:
        """A confusion must surface even when it scores below every hypothesis.

        The two kinds measure confidence on opposite scales — a hypothesis'
        score is "how sure am I this is true", a confusion's is "how sure am I
        of my guess", and the awareness prompt only emits a confusion when that
        guess is weak. One descending sort plus a top-3 cap therefore buried
        confusions permanently: a real profile carries hundreds of ≥0.60
        hypotheses (334 on the author's data, cutoff at 0.76) while genuine
        confusions land around 0.3–0.5.
        """
        client, memory, _engine, _dialogue = self._build(tmp_path)
        for index in range(6):
            self._seed_hypothesis(memory, f"高置信假设-{index}", 0.90 - index / 100)
        confusion_id = memory._database.insert_confusion(
            source="awareness",
            topic="露营兴趣的突然性",
            observation="只看几十秒就收藏，和木工的深看模式完全不同",
            interpretation_confidence=0.50,
        )

        body = client.get("/api/chat/pending-confirmations").json()

        assert body["count"] == 3
        refs = [item["ref"] for item in body["items"]]
        assert str(confusion_id) in refs, (
            "the confusion holds a reserved seat despite scoring lowest"
        )
        kinds = [item["kind"] for item in body["items"]]
        assert kinds.count("confusion") == 1, "exactly one seat is reserved, not more"
        assert kinds.count("hypothesis") == 2, "the remaining seats still go to hypotheses"

    def test_unused_confusion_seat_falls_back_to_hypotheses(self, tmp_path: Path) -> None:
        """With no confusion pending, the reserved seat must not be wasted."""
        client, memory, _engine, _dialogue = self._build(tmp_path)
        for index in range(5):
            self._seed_hypothesis(memory, f"只有假设-{index}", 0.90 - index / 100)

        body = client.get("/api/chat/pending-confirmations").json()

        assert body["count"] == 3
        assert all(item["kind"] == "hypothesis" for item in body["items"])

    def test_manual_open_three_items_ignores_both_cooldowns(self, tmp_path: Path) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        refs = [
            self._seed_hypothesis(memory, f"主动待聊-{index}", 0.66 + index / 100)
            for index in range(3)
        ]
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "global_last_thrown_at": datetime.now(UTC).isoformat(),
                    "objects": {
                        ref: {
                            "last_asked_at": datetime.now(UTC).isoformat(),
                            "deferred_until": "2099-01-01T00:00:00+00:00",
                        }
                        for ref in refs
                    },
                }
            ),
            encoding="utf-8",
        )

        opened = [
            client.post(
                f"/api/chat/pending-confirmations/{ref}/open",
                json={"session": "popup"},
            )
            for ref in refs
        ]

        assert [response.status_code for response in opened] == [200, 200, 200]
        assert len({response.json()["turn_id"] for response in opened}) == 3
        assert all(response.json()["payload"]["state"] == "pending" for response in opened)
        turns = client.get(
            "/api/chat/turns",
            params={"session": "popup", "scope": "hypothesis"},
        ).json()["items"]
        assert len(turns) == 3

    def test_open_reuses_ref_in_same_session_and_creates_one_per_other_session(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        ref = self._seed_hypothesis(memory, "同一大脑多个屏幕", 0.73)

        first = client.post(
            f"/api/chat/pending-confirmations/{ref}/open",
            json={"session": "popup"},
        )
        retry = client.post(
            f"/api/chat/pending-confirmations/{ref}/open",
            json={"session": "popup"},
        )
        other = client.post(
            f"/api/chat/pending-confirmations/{ref}/open",
            json={"session": "webui"},
        )

        assert first.status_code == retry.status_code == other.status_code == 200
        assert first.json()["turn_id"] == retry.json()["turn_id"]
        assert other.json()["turn_id"] != first.json()["turn_id"]
        assert (
            len(
                client.get(
                    "/api/chat/turns",
                    params={"session": "popup", "scope": "hypothesis"},
                ).json()["items"]
            )
            == 1
        )
        assert (
            len(
                client.get(
                    "/api/chat/turns",
                    params={"session": "webui", "scope": "hypothesis"},
                ).json()["items"]
            )
            == 1
        )

    def test_open_hypothesis_settlement_releases_anchor_and_leaves_pending_list(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, engine, _dialogue = self._build(tmp_path)
        ref = self._seed_hypothesis(memory, "主动打开后确认要解锚", 0.73)
        opened = client.post(
            f"/api/chat/pending-confirmations/{ref}/open",
            json={"session": "popup"},
        )
        assert opened.status_code == 200
        assert engine._dialogue_anchor_manager.current() is not None

        settled = client.post(
            f"/api/chat/cards/{opened.json()['turn_id']}/action",
            json={"action": "confirm"},
        )

        assert settled.status_code == 200
        assert settled.json()["state"] == "confirmed"
        assert engine._dialogue_anchor_manager.current() is None
        refs = {
            item["ref"] for item in client.get("/api/chat/pending-confirmations").json()["items"]
        }
        assert ref not in refs

    def test_pending_open_hypothesis_defer_releases_pending_anchor(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, engine, _dialogue = self._build(tmp_path)
        ref = self._seed_hypothesis(memory, "主动打开后稍后聊也必须解锚", 0.73)
        opened = client.post(
            f"/api/chat/pending-confirmations/{ref}/open",
            json={"session": "webui"},
        )
        assert opened.status_code == 200
        turn_id = opened.json()["turn_id"]
        anchor = engine._dialogue_anchor_manager.current()
        assert anchor is not None
        assert anchor.origin_turn_id == turn_id
        assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == "pending"

        deferred = client.post(
            f"/api/chat/cards/{turn_id}/action",
            json={"action": "defer"},
        )

        assert deferred.status_code == 200
        assert deferred.json()["state"] == "deferred"
        assert engine._dialogue_anchor_manager.current() is None
        assert memory._database.get_chat_turn(turn_id)["payload"]["state"] == "deferred"

    def test_confusion_open_ignores_ask_cooldown_and_builds_question_anchor(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, engine, _dialogue = self._build(tmp_path)
        confusion_id = memory._database.insert_confusion(
            source="awareness",
            topic="收藏后马上退出",
            observation="收藏动作和停留时长互相冲突",
            interpretation="可能是代理行为",
            interpretation_confidence=0.78,
            evidence_refs=["event-11"],
        )
        memory._database.update_confusion(
            confusion_id,
            asked_at=datetime.now(UTC).isoformat(),
        )

        response = client.post(
            f"/api/chat/pending-confirmations/{confusion_id}/open",
            json={"session": "popup"},
        )

        assert response.status_code == 200
        turn = response.json()
        assert turn["scope"] == "confusion"
        assert turn["status"] == "completed"
        assert turn["payload"]["kind"] == "confusion"
        assert turn["reply"]
        confusion = memory._database.get_confusion(confusion_id)
        assert confusion is not None
        assert confusion["status"] == "clarifying"
        assert confusion["ask_turn_id"] == turn["turn_id"]
        anchor = engine._dialogue_anchor_manager.current()
        assert anchor is not None
        assert anchor.kind == "confusion"
        assert anchor.ref == str(confusion_id)
        assert anchor.origin_turn_id == turn["turn_id"]

    def test_clarifying_confusion_replaces_unopenable_rows_across_sessions(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, engine, _dialogue = self._build(tmp_path)
        active_id = memory._database.insert_confusion(
            source="awareness",
            topic="已经在插件里聊的疑惑",
            observation="它持有全局澄清位置",
            interpretation_confidence=0.61,
        )
        blocked_id = memory._database.insert_confusion(
            source="awareness",
            topic="下一条尚未开始的疑惑",
            observation="当前不能取得全局位置",
            interpretation_confidence=0.92,
        )
        popup = client.post(
            f"/api/chat/pending-confirmations/{active_id}/open",
            json={"session": "popup"},
        )
        assert popup.status_code == 200

        pending = client.get(
            "/api/chat/pending-confirmations",
            params={"session": "webui"},
        ).json()["items"]
        confusion_refs = [item["ref"] for item in pending if item["kind"] == "confusion"]
        assert confusion_refs == [str(active_id)]
        assert str(blocked_id) not in {item["ref"] for item in pending}

        desktop = client.post(
            f"/api/chat/pending-confirmations/{active_id}/open",
            json={"session": "webui"},
        )
        assert desktop.status_code == 200
        assert desktop.json()["turn_id"] != popup.json()["turn_id"]
        active = memory._database.get_confusion(active_id)
        assert active is not None
        assert active["ask_turn_id"] == desktop.json()["turn_id"]
        anchor = engine._dialogue_anchor_manager.current()
        assert anchor is not None
        assert anchor.ref == str(active_id)
        desktop_pending = client.get(
            "/api/chat/pending-confirmations",
            params={"session": "webui"},
        ).json()["items"]
        assert str(active_id) not in {item["ref"] for item in desktop_pending}

    def test_pending_open_returns_retryable_busy_without_mutating_confusion(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        confusion_id = memory._database.insert_confusion(
            source="awareness",
            topic="队列忙时先别落半截状态",
            observation="应由前端自动重试",
            interpretation_confidence=0.75,
        )
        orphan_id = memory._database.insert_confusion(
            source="awareness",
            topic="旧进程留下的半截澄清",
            observation="只读列表不能排在长 LLM 后面",
            interpretation_confidence=0.72,
        )
        memory._database.update_confusion(
            orphan_id,
            status="clarifying",
            ask_turn_id="missing-turn",
            asked_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        )
        queue = client.app.state.runtime_context.dialogue_settlement_queue
        queue.pause()
        try:
            pending = client.get(
                "/api/chat/pending-confirmations",
                params={"session": "webui"},
            )
            response = client.post(
                f"/api/chat/pending-confirmations/{confusion_id}/open",
                json={"session": "webui"},
            )
        finally:
            queue.resume()

        assert pending.status_code == 200
        assert [item["ref"] for item in pending.json()["items"] if item["kind"] == "confusion"] == [
            str(orphan_id)
        ]
        assert response.status_code == 503
        assert response.headers["retry-after"] == "2"
        assert response.json()["detail"]["code"] == "dialogue_busy"
        confusion = memory._database.get_confusion(confusion_id)
        assert confusion is not None
        assert confusion["status"] == "open"
        assert confusion["ask_turn_id"] == ""

    def test_pending_open_confusion_schedule_retarget_rollback_only_in_worker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Q1/F3: every pending-open raw sink runs in the registered worker."""
        client, memory, engine, _dialogue = self._build(tmp_path)
        queue = client.app.state.runtime_context.dialogue_settlement_queue
        first_id = memory._database.insert_confusion(
            source="awareness",
            topic="先排队的疑惑",
            observation="需要验证 schedule 与 retarget",
            interpretation_confidence=0.8,
        )
        second_id = memory._database.insert_confusion(
            source="awareness",
            topic="创建失败的疑惑",
            observation="需要验证 rollback",
            interpretation_confidence=0.79,
        )
        operations: list[tuple[str, bool]] = []
        real_schedule = engine._confusion_manager.schedule_ask
        real_update = memory._database.update_confusion
        real_establish = engine._dialogue_anchor_manager.establish

        def observe_schedule(*args: object, **kwargs: object) -> bool:
            operations.append(("schedule", asyncio.current_task() is queue.worker_task))
            return bool(real_schedule(*args, **kwargs))

        def observe_update(confusion_id: int, **fields: object) -> None:
            if fields.get("status") == "open" and fields.get("ask_turn_id") == "":
                operation = "rollback"
            elif "ask_turn_id" in fields:
                operation = "retarget"
            else:
                operation = "other"
            operations.append((operation, asyncio.current_task() is queue.worker_task))
            real_update(confusion_id, **fields)

        def observe_establish(**fields: object) -> object:
            operations.append(("establish", asyncio.current_task() is queue.worker_task))
            return real_establish(**fields)

        monkeypatch.setattr(engine._confusion_manager, "schedule_ask", observe_schedule)
        monkeypatch.setattr(memory._database, "update_confusion", observe_update)
        monkeypatch.setattr(engine._dialogue_anchor_manager, "establish", observe_establish)

        first = client.post(
            f"/api/chat/pending-confirmations/{first_id}/open",
            json={"session": "popup"},
        )
        retarget = client.post(
            f"/api/chat/pending-confirmations/{first_id}/open",
            json={"session": "webui"},
        )
        assert first.status_code == retarget.status_code == 200

        real_update(first_id, status="resolved")
        real_create = memory._database.create_chat_confirmation_turn

        def fail_second_create(**fields: object) -> object:
            if str(fields.get("ref", "")) == str(second_id):
                raise RuntimeError("injected confirmation create failure")
            return real_create(**fields)

        monkeypatch.setattr(
            memory._database,
            "create_chat_confirmation_turn",
            fail_second_create,
        )
        with pytest.raises(RuntimeError, match="injected confirmation create failure"):
            client.post(
                f"/api/chat/pending-confirmations/{second_id}/open",
                json={"session": "mobile"},
            )

        assert operations == [
            ("schedule", True),
            ("establish", True),
            ("retarget", True),
            ("establish", True),
            ("schedule", True),
            ("rollback", True),
        ]

    def test_restart_releases_clarifying_claim_without_corresponding_turn(
        self,
        tmp_path: Path,
    ) -> None:
        """F4: a crash after durable claim cannot invisibly pin the unique slot."""
        client, memory, _engine, _dialogue = self._build(tmp_path)
        orphan_id = memory._database.insert_confusion(
            source="awareness",
            topic="claim 后崩溃的疑惑",
            observation="turn 尚未创建",
            interpretation_confidence=0.84,
        )
        next_id = memory._database.insert_confusion(
            source="awareness",
            topic="下一条疑惑",
            observation="应能取得释放后的 slot",
            interpretation_confidence=0.82,
        )
        assert memory._database.claim_confusion_clarifying(
            orphan_id,
            ask_turn_id="missing-after-crash",
            asked_at=datetime.now(UTC).isoformat(),
        )
        memory._database.conn.execute(
            """
            UPDATE confusions
               SET updated_at = datetime('now', '-31 seconds')
             WHERE id = ?
            """,
            (orphan_id,),
        )
        memory._database.conn.commit()
        assert memory._database.get_chat_turn("missing-after-crash") is None
        client.close()
        memory._database.close()

        client2, memory2, _engine2, _dialogue2 = self._build(tmp_path)
        pending = client2.get("/api/chat/pending-confirmations")

        assert pending.status_code == 200
        orphan = memory2._database.get_confusion(orphan_id)
        assert orphan is not None
        assert orphan["status"] == "open"
        assert orphan["ask_turn_id"] == ""
        assert not orphan["asked_at"]
        assert str(orphan_id) in {item["ref"] for item in pending.json()["items"]}

        opened = client2.post(
            f"/api/chat/pending-confirmations/{next_id}/open",
            json={"session": "popup"},
        )
        assert opened.status_code == 200
        claimed_next = memory2._database.get_confusion(next_id)
        assert claimed_next is not None
        assert claimed_next["status"] == "clarifying"
        assert claimed_next["ask_turn_id"] == opened.json()["turn_id"]

    def test_system_throw_global_12h_survives_restart_and_same_turn_retry(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        ref = self._seed_hypothesis(memory, "系统抛出后要全局节流", 0.72)

        first = self._post_user_turn(client, turn_id="user-global-1", session="popup")
        assert first.status_code == 200
        popup = client.get("/api/chat/turns", params={"session": "popup"}).json()["items"]
        assert [item["scope"] for item in popup] == ["hypothesis", "chat"]
        assert popup[0]["payload"]["ref"] == ref
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["global_last_thrown_at"]
        client.close()
        time.sleep(0.03)
        memory._database.close()

        client2, _memory2, _engine2, _dialogue2 = self._build(tmp_path)
        retry = self._post_user_turn(client2, turn_id="user-global-1", session="popup")
        blocked = self._post_user_turn(client2, turn_id="user-global-2", session="webui")

        assert retry.status_code == blocked.status_code == 200
        popup_after = client2.get("/api/chat/turns", params={"session": "popup"}).json()["items"]
        webui_after = client2.get("/api/chat/turns", params={"session": "webui"}).json()["items"]
        assert [item["scope"] for item in popup_after] == ["hypothesis", "chat"]
        assert [item["scope"] for item in webui_after] == ["chat"]

    def test_concurrent_user_messages_atomically_claim_one_global_throw(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        first_client, memory, _engine, _dialogue = self._build(tmp_path)
        self._seed_hypothesis(memory, "并发消息只能触发一次系统抛出", 0.74)
        second_client = TestClient(first_client.app)

        def send(index: int) -> int:
            client = first_client if index == 0 else second_client
            response = self._post_user_turn(
                client,
                turn_id=f"user-concurrent-{index}",
                session=f"screen-{index}",
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(send, range(2)))

        assert statuses == [200, 200]
        rows = memory._database.conn.execute(
            "SELECT session FROM chat_turns WHERE scope = 'hypothesis'"
        ).fetchall()
        assert len(rows) == 1

    def test_system_throw_requires_global_12h_and_object_72h(self, tmp_path: Path) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        ref = self._seed_hypothesis(memory, "对象冷却要叠加全局冷却", 0.74)
        assert (
            self._post_user_turn(
                client,
                turn_id="user-object-1",
                session="popup",
            ).status_code
            == 200
        )
        state_path = memory._data_dir / "memory" / "dialogue_confirmation_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["global_last_thrown_at"] = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
        state_path.write_text(json.dumps(state), encoding="utf-8")

        blocked = self._post_user_turn(
            client,
            turn_id="user-object-2",
            session="webui",
        )
        assert blocked.status_code == 200
        assert [
            item["scope"]
            for item in client.get("/api/chat/turns", params={"session": "webui"}).json()["items"]
        ] == ["chat"]

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["global_last_thrown_at"] = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
        state["objects"][ref]["last_asked_at"] = (
            datetime.now(UTC) - timedelta(hours=73)
        ).isoformat()
        state_path.write_text(json.dumps(state), encoding="utf-8")
        allowed = self._post_user_turn(
            client,
            turn_id="user-object-3",
            session="desktop-2",
        )

        assert allowed.status_code == 200
        assert [
            item["scope"]
            for item in client.get("/api/chat/turns", params={"session": "desktop-2"}).json()[
                "items"
            ]
        ] == ["hypothesis", "chat"]

    def test_attachment_is_before_user_and_empty_or_retry_never_creates_extra_turn(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        self._seed_hypothesis(memory, "附着必须稳定保序", 0.69)

        empty = self._post_user_turn(
            client,
            turn_id="user-attach-empty",
            message="   ",
        )
        assert empty.status_code == 422
        assert client.get("/api/chat/turns", params={"session": "popup"}).json()["items"] == []

        first = self._post_user_turn(client, turn_id="user-attach-1")
        retry = self._post_user_turn(client, turn_id="user-attach-1")

        assert first.status_code == retry.status_code == 200
        turns = client.get("/api/chat/turns", params={"session": "popup"}).json()["items"]
        assert [item["scope"] for item in turns] == ["hypothesis", "chat"]
        assert turns[0]["payload"]["attached_to_turn_id"] == "user-attach-1"
        assert turns[1]["turn_id"] == "user-attach-1"

    def test_restart_repairs_card_inserted_before_user_without_duplicate_attachment(
        self,
        tmp_path: Path,
    ) -> None:
        client, memory, _engine, _dialogue = self._build(tmp_path)
        title = "崩溃缝隙也不能重复附着"
        ref = self._seed_hypothesis(memory, title, 0.7)
        memory._database.create_chat_turn(
            turn_id="orphan-attachment",
            session="popup",
            scope="hypothesis",
            subject_id=ref,
            subject_title=title,
            message="阿b 的猜测",
            payload={
                "type": "card",
                "kind": "hypothesis",
                "ref": ref,
                "title": title,
                "evidence_refs": [],
                "actions": ["confirm", "reject", "discuss", "defer"],
                "state": "pending",
                "attached_to_turn_id": "user-after-crash",
            },
        )
        memory._database.complete_chat_turn("orphan-attachment", reply="")
        client.close()
        memory._database.close()

        client2, memory2, _engine2, _dialogue2 = self._build(tmp_path)
        response = self._post_user_turn(client2, turn_id="user-after-crash")

        assert response.status_code == 200
        turns = client2.get("/api/chat/turns", params={"session": "popup"}).json()["items"]
        assert [item["turn_id"] for item in turns] == [
            "orphan-attachment",
            "user-after-crash",
        ]
        state = json.loads(
            (memory2._data_dir / "memory" / "dialogue_confirmation_state.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["global_last_thrown_at"]
        assert state["objects"][ref]["last_asked_at"]


class TestEmbeddingAndCompatProviderE2E:
    """End-to-end coverage for the v0.3.32 changes through the HTTP boundary.

    Two related shifts ship in v0.3.32:

      1. ``[llm.embedding]`` owns its own ``api_key`` / ``base_url`` —
         embedding is fully decoupled from the chat ``[llm.<provider>]``
         blocks.
      2. ``openai_compatible`` becomes a first-class registered provider
         (separate ``[llm.openai_compatible]`` block, distinct registry
         entry from ``openai``) — Groq / Together / Azure OpenAI / vLLM
         and friends get a dedicated home instead of hijacking
         ``[llm.openai].base_url``.

    These tests exercise both end-to-end through ``/api/config`` so we
    catch any regression in serialization, masking, partial-update
    merging, hot-reload, or ConfigIssue surfacing.
    """

    @staticmethod
    def _make_client(monkeypatch, tmp_path, initial_cfg):
        """Wire up a TestClient with load_config/save_config patched to
        round-trip against a real on-disk config in tmp_path."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import save_config

        config_path = tmp_path / "config.toml"
        save_config(initial_cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        # `cfg` is a single mutable instance that both load_config and
        # save_config see — that mirrors how the FastAPI lifecycle reads
        # one config object and mutates it in place across requests.
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: initial_cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        return TestClient(app)

    # ── GET masking & shape ─────────────────────────────────────────

    def test_get_config_exposes_openai_compatible_block(self, monkeypatch, tmp_path) -> None:
        """The /api/config response must include the new
        [llm.openai_compatible] block so the popup can populate its
        fields. api_key is masked by default."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="sk-real-openai-1234567890"),
                openai_compatible=LLMProviderConfig(
                    api_key="gsk-groq-secret-key-1234567890",
                    model="llama-3.1-70b-versatile",
                    base_url="https://api.groq.com/openai/v1",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()

        compat = data["llm"]["openai_compatible"]
        # Shape: all expected fields are present, with the api_key masked
        # but model / base_url surfaced verbatim.
        assert compat["model"] == "llama-3.1-70b-versatile"
        assert compat["base_url"] == "https://api.groq.com/openai/v1"
        assert "*" in compat["api_key"]
        assert "gsk-groq-secret-key-1234567890" not in compat["api_key"]

    def test_get_config_exposes_embedding_credentials_masked(self, monkeypatch, tmp_path) -> None:
        """v0.3.32+ embedding owns api_key/base_url. They must surface
        in /api/config (so the popup knows what's configured) with
        api_key masked."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(api_key="ds-key"),
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="text-embedding-3-large",
                    api_key="sk-embed-secret-1234567890",
                    base_url="https://api.openai.com/v1",
                    similarity_threshold=0.91,
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")
        emb = response.json()["llm"]["embedding"]

        assert emb["provider"] == "openai"
        assert emb["model"] == "text-embedding-3-large"
        assert emb["base_url"] == "https://api.openai.com/v1"
        assert emb["similarity_threshold"] == 0.91
        assert "*" in emb["api_key"]
        assert "sk-embed-secret-1234567890" not in emb["api_key"]

    def test_get_config_reveal_keys_compat_flag_still_masks_secrets(
        self, monkeypatch, tmp_path
    ) -> None:
        """Legacy reveal requests remain compatible but never export secrets."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                openai_compatible=LLMProviderConfig(
                    api_key="gsk-raw-1234567890",
                    base_url="https://api.groq.com/openai/v1",
                ),
                embedding=EmbeddingConfig(api_key="sk-emb-raw-1234567890"),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        revealed = client.get("/api/config", params={"reveal_keys": "true"}).json()
        assert revealed["llm"]["openai_compatible"]["api_key"] != "gsk-raw-1234567890"
        assert revealed["llm"]["embedding"]["api_key"] != "sk-emb-raw-1234567890"
        assert "*" in revealed["llm"]["openai_compatible"]["api_key"]
        assert "*" in revealed["llm"]["embedding"]["api_key"]

    # ── PUT round-trip: openai_compatible ───────────────────────────

    def test_put_openai_compatible_round_trips_through_get(self, monkeypatch, tmp_path) -> None:
        """PUT a full [llm.openai_compatible] block, then GET — the
        non-secret fields come back identical, api_key comes back
        masked but the in-memory config object holds the real value."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        put_resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-fresh-groq-key-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )
        assert put_resp.status_code == 202
        body = put_resp.json()
        assert body["ok"] is True

        # In-memory config has the real key
        assert cfg.llm.default_provider == "openai_compatible"
        assert cfg.llm.openai_compatible.api_key == "gsk-fresh-groq-key-1234567890"
        assert cfg.llm.openai_compatible.model == "llama-3.1-70b-versatile"
        assert cfg.llm.openai_compatible.base_url == "https://api.groq.com/openai/v1"

        # Subsequent GET round-trips with masking
        get_resp = client.get("/api/config")
        compat = get_resp.json()["llm"]["openai_compatible"]
        assert compat["model"] == "llama-3.1-70b-versatile"
        assert compat["base_url"] == "https://api.groq.com/openai/v1"
        assert "*" in compat["api_key"]
        assert "gsk-fresh-groq-key-1234567890" not in compat["api_key"]

    def test_put_openai_compatible_does_not_stomp_openai_block(self, monkeypatch, tmp_path) -> None:
        """Partial PUT with only [llm.openai_compatible] must NOT clear
        the existing [llm.openai] block. Both providers can coexist
        (the whole point of the v0.3.32 split)."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(
                    api_key="sk-original-openai-1234567890",
                    model="gpt-5-nano",
                    base_url="",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        client.put(
            "/api/config",
            json={
                "llm": {
                    "openai_compatible": {
                        "api_key": "gsk-groq-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )

        # openai block survived intact
        assert cfg.llm.openai.api_key == "sk-original-openai-1234567890"
        assert cfg.llm.openai.model == "gpt-5-nano"
        assert cfg.llm.default_provider == "openai"  # unchanged
        # openai_compatible block freshly populated
        assert cfg.llm.openai_compatible.api_key == "gsk-groq-1234567890"
        assert cfg.llm.openai_compatible.base_url == "https://api.groq.com/openai/v1"

    # ── ConfigIssue surfacing ───────────────────────────────────────

    def test_put_default_openai_compatible_without_base_url_surfaces_issue(
        self, monkeypatch, tmp_path
    ) -> None:
        """If the user picks openai_compatible as default but forgets
        base_url, ``_collect_config_issues`` flags it and the issue
        appears in the PUT response so the popup can highlight the
        offending field — without this, the bad config would silently
        save and the daemon would 401 against api.openai.com on first
        request."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-test",
                        "model": "llama-3.1-70b-versatile",
                        # base_url deliberately omitted
                    },
                },
            },
        )
        assert resp.status_code == 202

        issues = resp.json()["config"]["issues"]
        fields = [i["field"] for i in issues]
        assert "llm.openai_compatible.base_url" in fields, f"expected base_url issue in {fields}"

    # ── Embedding round-trip + masked-echo protection ───────────────

    def test_put_embedding_via_openai_compatible_round_trip(self, monkeypatch, tmp_path) -> None:
        """Embedding can independently target an openai_compatible
        backend (vLLM / Together / Azure OpenAI), with its own api_key
        and base_url — no need to also fill [llm.openai_compatible]."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        put = client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "provider": "openai_compatible",
                        "model": "bge-large-en-v1.5",
                        "api_key": "vllm-token-1234567890",
                        "base_url": "http://vllm.internal:8000/v1",
                        "similarity_threshold": 0.85,
                    },
                },
            },
        )
        assert put.status_code == 202
        assert cfg.llm.embedding.provider == "openai_compatible"
        assert cfg.llm.embedding.api_key == "vllm-token-1234567890"
        assert cfg.llm.embedding.base_url == "http://vllm.internal:8000/v1"
        assert cfg.llm.embedding.similarity_threshold == 0.85

        # Round-trip via GET — base_url + provider + threshold come back
        # raw, api_key masked.
        emb = client.get("/api/config").json()["llm"]["embedding"]
        assert emb["provider"] == "openai_compatible"
        assert emb["base_url"] == "http://vllm.internal:8000/v1"
        assert emb["similarity_threshold"] == 0.85
        assert "*" in emb["api_key"]
        assert "vllm-token-1234567890" not in emb["api_key"]

    def test_put_embedding_masked_echo_does_not_overwrite_real_key(
        self, monkeypatch, tmp_path
    ) -> None:
        """Workflow: open settings → backend returns masked key → user
        edits an unrelated field (model) → submits — the masked api_key
        gets echoed back. Backend must detect the mask (any '*') and
        keep the real key. Otherwise every save would silently destroy
        the user's secret."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                openai=LLMProviderConfig(api_key="sk-openai"),
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="text-embedding-3-small",
                    api_key="sk-real-secret-do-not-overwrite-1234567890",
                    base_url="",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        # Step 1 — popup loads the config and gets a masked key.
        masked = client.get("/api/config").json()["llm"]["embedding"]["api_key"]
        assert "*" in masked

        # Step 2 — popup re-submits with the masked key and a new model.
        client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "api_key": masked,
                        "model": "text-embedding-3-large",
                    },
                },
            },
        )

        # Real key preserved; the model field still updated.
        assert cfg.llm.embedding.api_key == "sk-real-secret-do-not-overwrite-1234567890"
        assert cfg.llm.embedding.model == "text-embedding-3-large"

    # ── Hot-reload verification ─────────────────────────────────────

    def test_put_triggers_runtime_hot_reload(self, monkeypatch, tmp_path) -> None:
        """有效配置写盘返回后，后台必须成功完成运行时重建。"""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-old")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-new",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )
        body = resp.json()
        assert resp.status_code == 202
        assert body["reloaded"] is False
        status = _wait_for_config_apply(
            client,
            revision=body["apply_revision"],
        )
        assert status["state"] == "applied"

    # ── Coexistence: both providers usable in one config ────────────

    def test_get_after_dual_put_returns_both_provider_blocks(self, monkeypatch, tmp_path) -> None:
        """Set both [llm.openai] (real OpenAI for chat) and
        [llm.openai_compatible] (Groq for fast drafting) in one PUT.
        Both blocks must round-trip independently — the v0.3.32 split
        explicitly enables this dual-stack scenario."""
        from openbiliclaw.config import Config, LLMConfig

        cfg = Config(llm=LLMConfig())
        client = self._make_client(monkeypatch, tmp_path, cfg)

        client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai",
                    "openai": {
                        "api_key": "sk-real-openai-1234567890",
                        "model": "gpt-5-nano",
                    },
                    "openai_compatible": {
                        "api_key": "gsk-groq-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )

        data = client.get("/api/config").json()["llm"]
        assert data["default_provider"] == "openai"
        # Two distinct masked secrets, each pointing at its own block.
        assert data["openai"]["model"] == "gpt-5-nano"
        assert data["openai_compatible"]["model"] == "llama-3.1-70b-versatile"
        assert data["openai_compatible"]["base_url"] == "https://api.groq.com/openai/v1"
        # api_keys are both masked but distinct (different last 4 chars
        # in the mask), proving they're stored as separate values.
        openai_mask = data["openai"]["api_key"]
        compat_mask = data["openai_compatible"]["api_key"]
        assert "*" in openai_mask and "*" in compat_mask
        assert openai_mask != compat_mask

    def test_get_config_exposes_sources_and_advanced_settings(self, monkeypatch, tmp_path) -> None:
        """The config API should expose persisted advanced fields so the
        extension settings page can stay aligned with config.toml."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            data_dir="runtime-data",
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(
                    api_key="ds-key",
                    model="deepseek-v4-flash",
                    base_url="https://api.deepseek.com",
                    reasoning_effort="high",
                ),
                openrouter=LLMProviderConfig(
                    api_key="or-key",
                    model="openai/gpt-5-nano",
                    base_url="https://openrouter.ai/api/v1",
                    http_referer="https://example.com",
                    x_title="Example App",
                ),
            ),
        )
        cfg.bilibili.browser_executable = "/Applications/Chrome.app"
        cfg.bilibili.browser_headed = True
        cfg.sources.browser_cdp_url = "http://localhost:9222"
        cfg.sources.browser_headed = True
        cfg.sources.bilibili.enabled = False
        cfg.sources.xiaohongshu.enabled = False
        cfg.sources.xiaohongshu.daily_search_budget = 11
        cfg.sources.xiaohongshu.daily_creator_budget = 3
        cfg.sources.xiaohongshu.task_interval_seconds = 66
        cfg.sources.douyin.enabled = True
        cfg.sources.douyin.cookie_env = "CUSTOM_DY_COOKIE"
        cfg.sources.douyin.daily_search_budget = 12
        cfg.sources.douyin.daily_hot_budget = 4
        cfg.sources.douyin.daily_feed_budget = 13
        cfg.sources.douyin.request_interval_seconds = 5
        cfg.sources.youtube.enabled = True
        cfg.sources.youtube.daily_search_budget = 4
        cfg.sources.youtube.daily_trending_budget = 44
        cfg.sources.youtube.daily_channel_budget = 8
        cfg.sources.youtube.request_interval_seconds = 3
        cfg.sources.twitter.enabled = True
        cfg.sources.twitter.cookie_env = "CUSTOM_X_COOKIE"
        cfg.sources.twitter.daily_search_budget = 7
        cfg.sources.twitter.daily_feed_budget = 14
        cfg.sources.twitter.daily_creator_budget = 5
        cfg.sources.bangumi.enabled = True
        cfg.sources.bangumi.username = "sai"
        cfg.sources.bangumi.subject_types = ("anime", "book")
        cfg.sources.bangumi.source_modes = ("search", "ranked")
        cfg.sources.bangumi.daily_search_budget = 23
        cfg.scheduler.pool_source_shares = {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
            "twitter": 3,
            "bangumi": 4,
        }
        cfg.scheduler.account_sync_interval_hours = 9
        cfg.scheduler.refresh_check_interval_seconds = 75
        cfg.scheduler.signal_event_threshold = 9
        cfg.scheduler.trending_refresh_minutes = 5
        cfg.scheduler.explore_refresh_minutes = 18
        cfg.scheduler.discovery_limit = 17
        cfg.scheduler.delight_queue_limit = 37
        cfg.scheduler.proactive_push_interval_seconds = 155
        cfg.scheduler.speculator_idle_interval_minutes = 11
        cfg.scheduler.speculation_interval_minutes = 21
        cfg.scheduler.speculation_ttl_days = 8
        cfg.scheduler.auto_update_enabled = True
        cfg.scheduler.auto_update_check_interval_hours = 10
        cfg.scheduler.auto_update_allow_prerelease = True
        cfg.scheduler.auto_update_allowed_remotes = [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
        cfg.discovery.multimodal_evaluation_enabled = True
        cfg.discovery.candidate_eval_concurrency = 3
        cfg.discovery.multimodal_batch_size = 4
        cfg.discovery.multimodal_image_max_px = 512
        cfg.discovery.multimodal_image_quality = 80
        cfg.discovery.multimodal_image_timeout_seconds = 10
        cfg.logging.file_level = "WARNING"
        cfg.logging.directory = "runtime-logs"
        cfg.logging.filename = "backend.log"
        cfg.logging.max_file_size_mb = 123
        cfg.logging.aggregate_budget_mb = 456
        cfg.logging.unmanaged_truncate_mb = 78
        cfg.logging.unmanaged_max_age_days = 9

        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config", params={"reveal_keys": "true"})
        assert response.status_code == 200
        data = response.json()

        assert data["data_dir"] == "runtime-data"
        assert data["llm"]["concurrency"] == 3
        assert data["llm"]["deepseek"]["reasoning_effort"] == "high"
        assert data["llm"]["openrouter"]["http_referer"] == "https://example.com"
        assert data["llm"]["openrouter"]["x_title"] == "Example App"
        assert data["bilibili"]["browser_executable"] == "/Applications/Chrome.app"
        assert data["bilibili"]["browser_headed"] is True
        assert data["sources"]["browser"]["cdp_url"] == "http://localhost:9222"
        assert data["sources"]["browser"]["headed"] is True
        assert data["sources"]["bilibili"]["enabled"] is False
        assert data["sources"]["xiaohongshu"]["enabled"] is False
        assert data["sources"]["xiaohongshu"]["daily_search_budget"] == 11
        assert data["sources"]["douyin"]["enabled"] is True
        assert data["sources"]["douyin"]["daily_feed_budget"] == 13
        assert data["sources"]["youtube"]["enabled"] is True
        assert data["sources"]["youtube"]["daily_search_budget"] == 4
        assert data["sources"]["youtube"]["daily_trending_budget"] == 44
        assert data["sources"]["youtube"]["daily_channel_budget"] == 8
        assert data["sources"]["youtube"]["request_interval_seconds"] == 3
        assert data["sources"]["twitter"]["enabled"] is True
        assert data["sources"]["twitter"]["cookie_env"] == "CUSTOM_X_COOKIE"
        assert data["sources"]["twitter"]["daily_search_budget"] == 7
        assert data["sources"]["twitter"]["daily_feed_budget"] == 14
        assert data["sources"]["twitter"]["daily_creator_budget"] == 5
        assert data["sources"]["reddit"]["enabled"] is False
        assert data["sources"]["reddit"]["source_modes"] == [
            "search",
            "hot",
            "subreddit",
            "related",
        ]
        assert data["sources"]["bangumi"] == {
            "recommendation_date_preset": "all",
            "recommendation_date_start": "",
            "recommendation_date_end": "",
            "recommendation_date_weight": 0.5,
            "enabled": True,
            "username": "sai",
            "subject_types": ["anime", "book"],
            "source_modes": ["search", "ranked"],
            "daily_search_budget": 23,
            "daily_ranked_budget": 100,
            "daily_latest_budget": 100,
            "request_interval_seconds": 1,
            "min_interval_minutes": 3,
            "bootstrap_limit": 300,
            "access_token_set": False,
        }
        assert data["scheduler"]["pool_source_shares"] == {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
            "twitter": 3,
            "zhihu": 1,
            "reddit": 1,
            "bangumi": 4,
            "linuxdo": 1,
            "weibo": 1,
            "v2ex": 1,
        }
        assert data["scheduler"]["account_sync_interval_hours"] == 9
        assert data["scheduler"]["refresh_check_interval_seconds"] == 75
        assert data["scheduler"]["signal_event_threshold"] == 9
        assert data["scheduler"]["trending_refresh_minutes"] == 5
        assert data["scheduler"]["explore_refresh_minutes"] == 18
        assert data["scheduler"]["discovery_limit"] == 17
        assert data["scheduler"]["delight_queue_limit"] == 37
        assert data["scheduler"]["proactive_push_interval_seconds"] == 155
        assert data["scheduler"]["speculator_idle_interval_minutes"] == 11
        assert data["scheduler"]["speculation_interval_minutes"] == 21
        assert data["scheduler"]["speculation_ttl_days"] == 8
        assert data["scheduler"]["auto_update_enabled"] is True
        assert data["scheduler"]["auto_update_check_interval_hours"] == 10
        assert data["scheduler"]["auto_update_allow_prerelease"] is True
        assert data["scheduler"]["auto_update_allowed_remotes"] == [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
        assert data["discovery"]["multimodal_evaluation_enabled"] is True
        assert data["discovery"]["candidate_eval_concurrency"] == 3
        assert data["discovery"]["multimodal_batch_size"] == 4
        assert data["discovery"]["multimodal_image_max_px"] == 512
        assert data["discovery"]["multimodal_image_quality"] == 80
        assert data["discovery"]["multimodal_image_timeout_seconds"] == 10
        assert data["logging"]["file_level"] == "WARNING"
        assert data["logging"]["directory"] == "runtime-logs"
        assert data["logging"]["filename"] == "backend.log"
        assert data["logging"]["file_path"] == str(tmp_path / "runtime-logs" / "backend.log")
        assert data["logging"]["max_file_size_mb"] == 123
        assert data["logging"]["aggregate_budget_mb"] == 456
        assert data["logging"]["unmanaged_truncate_mb"] == 78
        assert data["logging"]["unmanaged_max_age_days"] == 9

    @pytest.mark.parametrize("invalid_interval", [0, -1, "2"])
    def test_put_config_rejects_invalid_auto_update_interval(
        self,
        monkeypatch,
        tmp_path,
        invalid_interval,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"scheduler": {"auto_update_check_interval_hours": invalid_interval}},
        )

        assert response.status_code == 400
        assert cfg.scheduler.auto_update_check_interval_hours == 6

    def test_put_config_reddit_cookie_paste_writes_rdt_credential_store(
        self, monkeypatch, tmp_path
    ) -> None:
        """Manual Reddit Cookie paste rides PUT /api/config like douyin/x,
        but lands in rdt-cli's credential store — never in config.toml."""
        import json as _json

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
        from openbiliclaw.sources import reddit_tasks

        credential_file = tmp_path / "rdt" / "credential.json"
        monkeypatch.setattr(reddit_tasks, "_rdt_credential_file", lambda: credential_file)

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"sources": {"reddit": {"cookie": "reddit_session=abc123; token_v2=tok; loid=x"}}},
        )
        assert response.status_code == 202

        stored = _json.loads(credential_file.read_text(encoding="utf-8"))
        assert stored["cookies"]["reddit_session"] == "abc123"
        assert stored["source"] == "openbiliclaw:config-update"
        assert "abc123" not in (tmp_path / "config.toml").read_text(encoding="utf-8")

    def test_put_config_reddit_cookie_without_session_rejected_visibly(
        self, monkeypatch, tmp_path
    ) -> None:
        """A pasted Cookie missing reddit_session cannot be stored by
        rdt-cli — the save must fail loudly instead of silently no-oping."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
        from openbiliclaw.sources import reddit_tasks

        credential_file = tmp_path / "rdt" / "credential.json"
        monkeypatch.setattr(reddit_tasks, "_rdt_credential_file", lambda: credential_file)

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"sources": {"reddit": {"cookie": "token_v2=tok; loid=x"}}},
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error"] == "missing_reddit_session"
        assert "reddit_session" in detail["message"]
        assert not credential_file.exists()

    def test_put_config_caps_candidate_eval_concurrency_at_three(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "discovery": {
                    "candidate_eval_concurrency": 8,
                    "multimodal_evaluation_enabled": "true",
                    "multimodal_batch_size": 4,
                    "multimodal_image_max_px": 512,
                    "multimodal_image_quality": 80,
                    "multimodal_image_timeout_seconds": 10,
                },
            },
        )

        assert response.status_code == 202
        assert cfg.discovery.candidate_eval_concurrency == 3
        assert cfg.discovery.multimodal_evaluation_enabled is True
        assert cfg.discovery.multimodal_batch_size == 4
        assert cfg.discovery.multimodal_image_max_px == 512
        assert cfg.discovery.multimodal_image_quality == 80
        assert cfg.discovery.multimodal_image_timeout_seconds == 10
        discovery = response.json()["config"]["discovery"]
        assert discovery["candidate_eval_concurrency"] == 3
        assert discovery["multimodal_evaluation_enabled"] is True
        assert discovery["multimodal_batch_size"] == 4
        assert discovery["multimodal_image_max_px"] == 512
        assert discovery["multimodal_image_quality"] == 80
        assert discovery["multimodal_image_timeout_seconds"] == 10

    def test_put_config_round_trips_visual_enrichment_limits(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "discovery": {
                    "keyframe_max_frames": 9,
                    "keyframe_fetch_limit": 17,
                    "danmaku_fetch_limit": 23,
                    "danmaku_max_chars": 1800,
                }
            },
        )

        assert response.status_code == 202
        assert cfg.discovery.keyframe_max_frames == 9
        assert cfg.discovery.keyframe_fetch_limit == 17
        assert cfg.discovery.danmaku_fetch_limit == 23
        assert cfg.discovery.danmaku_max_chars == 1800
        discovery = response.json()["config"]["discovery"]
        assert discovery["keyframe_max_frames"] == 9
        assert discovery["keyframe_fetch_limit"] == 17
        assert discovery["danmaku_fetch_limit"] == 23
        assert discovery["danmaku_max_chars"] == 1800

    def test_put_config_normalizes_bad_multimodal_discovery_settings(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "discovery": {
                    "multimodal_evaluation_enabled": "on",
                    "multimodal_batch_size": 99,
                    "multimodal_image_max_px": 99,
                    "multimodal_image_quality": 99,
                    "multimodal_image_timeout_seconds": 99,
                },
            },
        )

        assert response.status_code == 202
        discovery = response.json()["config"]["discovery"]
        assert discovery["multimodal_evaluation_enabled"] is True
        assert discovery["multimodal_batch_size"] == 8
        assert discovery["multimodal_image_max_px"] == 384
        assert discovery["multimodal_image_quality"] == 72
        assert discovery["multimodal_image_timeout_seconds"] == 6

    def test_get_config_exposes_scheduler_pause_on_extension_disconnect(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config

        cfg = Config()
        cfg.scheduler.pause_on_extension_disconnect = True
        cfg.scheduler.extension_disconnect_grace_seconds = 45
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")

        assert response.status_code == 200
        scheduler = response.json()["scheduler"]
        assert scheduler["pause_on_extension_disconnect"] is True
        assert scheduler["extension_disconnect_grace_seconds"] == 45

    def test_scheduler_source_incremental_api_round_trip_and_null_inheritance(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        cfg.scheduler.source_incremental_enabled = False
        cfg.scheduler.source_incremental_hours = 36
        cfg.scheduler.xhs_incremental_hours = 0
        cfg.scheduler.douyin_incremental_hours = 168
        client = self._make_client(monkeypatch, tmp_path, cfg)

        initial = client.get("/api/config")
        assert initial.status_code == 200
        assert initial.json()["scheduler"]["source_incremental_enabled"] is False
        assert initial.json()["scheduler"]["source_incremental_hours"] == 36
        assert initial.json()["scheduler"]["xhs_incremental_hours"] == 0
        assert initial.json()["scheduler"]["douyin_incremental_hours"] == 168
        assert initial.json()["scheduler"]["youtube_incremental_hours"] is None

        updated = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "source_incremental_enabled": True,
                    "source_incremental_hours": 48,
                    "xhs_incremental_hours": None,
                    "douyin_incremental_hours": 12,
                    "youtube_incremental_hours": 0,
                    "zhihu_incremental_hours": 7,
                    "reddit_incremental_hours": 168,
                }
            },
        )

        assert updated.status_code == 202
        assert cfg.scheduler.source_incremental_enabled is True
        assert cfg.scheduler.source_incremental_hours == 48
        assert cfg.scheduler.xhs_incremental_hours is None
        assert cfg.scheduler.douyin_incremental_hours == 12
        assert cfg.scheduler.youtube_incremental_hours == 0
        assert cfg.scheduler.zhihu_incremental_hours == 7
        assert cfg.scheduler.reddit_incremental_hours == 168
        assert updated.json()["config"]["scheduler"]["xhs_incremental_hours"] is None

        reset_douyin = client.put(
            "/api/config",
            json={"scheduler": {"douyin_incremental_hours": None}},
        )
        assert reset_douyin.status_code == 202
        assert cfg.scheduler.douyin_incremental_hours == 0
        assert reset_douyin.json()["config"]["scheduler"]["douyin_incremental_hours"] == 0

    def test_source_incremental_enabled_per_source_api_round_trip(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        cfg.sources.xiaohongshu.incremental_enabled = True
        cfg.sources.douyin.incremental_enabled = True
        cfg.sources.youtube.incremental_enabled = False
        cfg.sources.zhihu.incremental_enabled = True
        cfg.sources.reddit.incremental_enabled = False
        cfg.sources.linuxdo.incremental_enabled = True
        cfg.sources.v2ex.incremental_enabled = False
        client = self._make_client(monkeypatch, tmp_path, cfg)

        initial = client.get("/api/config")
        assert initial.status_code == 200
        sources = initial.json()["sources"]
        assert sources["xiaohongshu"]["incremental_enabled"] is True
        assert sources["douyin"]["incremental_enabled"] is True
        assert sources["youtube"]["incremental_enabled"] is False
        assert sources["zhihu"]["incremental_enabled"] is True
        assert sources["reddit"]["incremental_enabled"] is False
        assert sources["linuxdo"]["incremental_enabled"] is True
        assert sources["v2ex"]["incremental_enabled"] is False

        updated = client.put(
            "/api/config",
            json={
                "sources": {
                    "xiaohongshu": {"incremental_enabled": False},
                    "douyin": {"incremental_enabled": False},
                    "youtube": {"incremental_enabled": True},
                    "zhihu": {"incremental_enabled": False},
                    "reddit": {"incremental_enabled": True},
                    "linuxdo": {"incremental_enabled": False},
                    "v2ex": {"incremental_enabled": True},
                },
                "scheduler": {"source_incremental_enabled": True},
            },
        )

        assert updated.status_code == 202
        assert cfg.sources.xiaohongshu.incremental_enabled is False
        assert cfg.sources.douyin.incremental_enabled is False
        assert cfg.sources.youtube.incremental_enabled is True
        assert cfg.sources.zhihu.incremental_enabled is False
        assert cfg.sources.reddit.incremental_enabled is True
        assert cfg.sources.linuxdo.incremental_enabled is False
        assert cfg.sources.v2ex.incremental_enabled is True
        assert cfg.scheduler.source_incremental_enabled is True

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("source_incremental_hours", -1),
            ("source_incremental_hours", 169),
            ("source_incremental_hours", None),
            ("xhs_incremental_hours", -1),
            ("xhs_incremental_hours", 169),
        ],
    )
    def test_scheduler_source_incremental_api_rejects_invalid_intervals(
        self, monkeypatch, tmp_path, field: str, value: object
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put("/api/config", json={"scheduler": {field: value}})

        assert response.status_code == 400
        expected = 24 if field == "source_incremental_hours" else None
        assert getattr(cfg.scheduler, field) == expected

    def test_copy_ready_target_round_trips_through_config_api(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        initial = client.get("/api/config")
        updated = client.put(
            "/api/config",
            json={"scheduler": {"copy_ready_target_count": 47}},
        )

        assert initial.status_code == 200
        assert initial.json()["scheduler"]["copy_ready_target_count"] == 90
        assert updated.status_code == 202
        assert updated.json()["config"]["scheduler"]["copy_ready_target_count"] == 47
        assert cfg.scheduler.copy_ready_target_count == 47

    def test_task_scoped_cognition_views_round_trip_through_config_api(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        initial = client.get("/api/config")
        updated = client.put(
            "/api/config",
            json={
                "soul": {
                    "preference_prompt_view": "compact-v1",
                    "awareness_prompt_view": "legacy",
                    "insight_prompt_view": "compact-v1",
                }
            },
        )

        assert initial.status_code == 200
        initial_soul = initial.json()["soul"]
        assert initial_soul["preference_prompt_view"] == "legacy"
        assert initial_soul["awareness_prompt_view"] == "compact-v1"
        assert initial_soul["insight_prompt_view"] == "legacy"
        assert "cognition_prompt_view" not in initial_soul
        assert updated.status_code == 202
        updated_soul = updated.json()["config"]["soul"]
        assert updated_soul["preference_prompt_view"] == "compact-v1"
        assert updated_soul["awareness_prompt_view"] == "legacy"
        assert updated_soul["insight_prompt_view"] == "compact-v1"
        assert cfg.soul.preference_prompt_view == "compact-v1"
        assert cfg.soul.awareness_prompt_view == "legacy"
        assert cfg.soul.insight_prompt_view == "compact-v1"
        rendered = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert "cognition_prompt_view" not in rendered

    def test_cognition_budget_knobs_round_trip_through_config_api(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        initial = client.get("/api/config")
        updated = client.put(
            "/api/config",
            json={
                "soul": {
                    "awareness_event_batch_size": 80,
                    "insight_note_batch_size": 40,
                    "cognition_max_tokens": 8192,
                }
            },
        )

        assert initial.status_code == 200
        initial_soul = initial.json()["soul"]
        assert initial_soul["awareness_event_batch_size"] == 300
        assert initial_soul["insight_note_batch_size"] == 150
        assert initial_soul["cognition_max_tokens"] == 32768
        assert updated.status_code == 202
        updated_soul = updated.json()["config"]["soul"]
        assert updated_soul["awareness_event_batch_size"] == 80
        assert updated_soul["insight_note_batch_size"] == 40
        assert updated_soul["cognition_max_tokens"] == 8192
        assert cfg.soul.awareness_event_batch_size == 80
        assert cfg.soul.insight_note_batch_size == 40
        assert cfg.soul.cognition_max_tokens == 8192
        rendered = (tmp_path / "config.toml").read_text(encoding="utf-8")
        assert "awareness_event_batch_size = 80" in rendered
        assert "insight_note_batch_size = 40" in rendered
        assert "cognition_max_tokens = 8192" in rendered

    @pytest.mark.parametrize(("raw_bool", "bad_grace"), [("true", -1), ("on", 0), ("true", "abc")])
    def test_put_config_updates_scheduler_pause_on_extension_disconnect(
        self,
        monkeypatch,
        tmp_path,
        raw_bool: str,
        bad_grace: object,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "pause_on_extension_disconnect": raw_bool,
                    "extension_disconnect_grace_seconds": bad_grace,
                },
            },
        )

        assert response.status_code == 202
        assert cfg.scheduler.pause_on_extension_disconnect is True
        assert cfg.scheduler.extension_disconnect_grace_seconds == 90
        scheduler = response.json()["config"]["scheduler"]
        assert scheduler["pause_on_extension_disconnect"] is True
        assert scheduler["extension_disconnect_grace_seconds"] == 90

    def test_put_config_rebuilds_runtime_with_pause_on_disconnect(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))

        async def _fake_rebuild(self: RuntimeContext, config: Config) -> None:
            self.config = config
            self.runtime_controller = SimpleNamespace(scheduler_config=config.scheduler)

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "pause_on_extension_disconnect": True,
                    "extension_disconnect_grace_seconds": 12,
                },
            },
        )

        assert response.status_code == 202
        body = response.json()
        _wait_for_config_apply(
            client,
            revision=body["apply_revision"],
        )
        runtime_scheduler = client.app.state.runtime_context.runtime_controller.scheduler_config
        assert runtime_scheduler.pause_on_extension_disconnect is True
        assert runtime_scheduler.extension_disconnect_grace_seconds == 12

    def test_put_config_updates_sources_and_advanced_settings(self, monkeypatch, tmp_path) -> None:
        """PUT /api/config should update the same advanced fields that the
        extension settings page exposes."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "data_dir": "runtime-data",
                "llm": {
                    "concurrency": 5,
                    "timeout": 1200,
                    "deepseek": {"reasoning_effort": "high"},
                    "openrouter": {
                        "http_referer": "https://example.com",
                        "x_title": "Example App",
                    },
                    "soul": {"provider": "claude", "model": "claude-sonnet-4-6"},
                    "discovery": {"provider": "deepseek", "model": "deepseek-v4-flash"},
                    "recommendation": {"provider": "gemini", "model": "gemini-2.5-flash"},
                    "evaluation": {"provider": "openai", "model": "gpt-5-nano"},
                },
                "bilibili": {
                    "browser_executable": "/Applications/Chrome.app",
                    "browser_headed": True,
                },
                "sources": {
                    "browser": {"cdp_url": "http://localhost:9222", "headed": True},
                    "bilibili": {"enabled": False},
                    "xiaohongshu": {
                        "enabled": False,
                        "daily_search_budget": 11,
                        "daily_creator_budget": 3,
                        "task_interval_seconds": 66,
                    },
                    "douyin": {
                        "enabled": True,
                        "mode": "direct",
                        "cookie_env": "CUSTOM_DY_COOKIE",
                        "daily_search_budget": 12,
                        "daily_hot_budget": 4,
                        "daily_feed_budget": 13,
                        "request_interval_seconds": 5,
                    },
                    "youtube": {
                        "enabled": True,
                        "daily_search_budget": 5,
                        "daily_trending_budget": 41,
                        "daily_channel_budget": 9,
                        "request_interval_seconds": 4,
                        "min_interval_minutes": 30,
                    },
                },
                "scheduler": {
                    "account_sync_interval_hours": 9,
                    "pool_source_shares": {
                        "bilibili": 6,
                        "xiaohongshu": 2,
                        "douyin": 2,
                        "youtube": 1,
                    },
                    "refresh_check_interval_seconds": 75,
                    "eval_min_batch_size": 23,
                    "eval_max_wait_seconds": 45.5,
                    "signal_event_threshold": 9,
                    "trending_refresh_minutes": 5,
                    "explore_refresh_minutes": 18,
                    "discovery_limit": 17,
                    "delight_queue_limit": 37,
                    "proactive_push_interval_seconds": 155,
                    "llm_budget_max_calls": 45,
                    "llm_budget_window_seconds": 1800,
                    "speculator_idle_interval_minutes": 11,
                    "speculation_interval_minutes": 21,
                    "speculation_ttl_days": 8,
                    "speculation_cooldown_days": 9,
                    "speculation_confirmation_threshold": 4,
                    "speculation_max_active": 6,
                    "speculation_max_primary_interests": 17,
                    "speculation_max_secondary_interests": 66,
                    "auto_update_enabled": True,
                    "auto_update_check_interval_hours": 10,
                    "auto_update_allow_prerelease": True,
                    "auto_update_allowed_remotes": [
                        "https://github.com/example/OpenBiliClaw.git",
                        "git@github.com:example/OpenBiliClaw.git",
                    ],
                },
                "discovery": {
                    "eval_prefilter_mode": "enforce",
                    "eval_scorer": "shadow",
                    "admission_min_score": 0.72,
                },
                "storage": {"db_path": "runtime-data/openbiliclaw.db"},
                "logging": {
                    "file_level": "WARNING",
                    "directory": "runtime-logs",
                    "filename": "backend.log",
                    "max_file_size_mb": 123,
                    "backup_count": 3,
                    "aggregate_budget_mb": 456,
                    "unmanaged_truncate_mb": 78,
                    "unmanaged_max_age_days": 9,
                },
            },
        )

        assert response.status_code == 202
        assert cfg.data_dir == "runtime-data"
        assert cfg.llm.concurrency == 5
        assert response.json()["config"]["llm"]["concurrency"] == 5
        assert cfg.llm.timeout == 1200
        assert response.json()["config"]["llm"]["timeout"] == 1200
        assert cfg.llm.deepseek.reasoning_effort == "high"
        assert cfg.llm.openrouter.http_referer == "https://example.com"
        assert cfg.llm.openrouter.x_title == "Example App"
        assert cfg.llm.soul.provider == "claude"
        assert cfg.llm.discovery.provider == "deepseek"
        assert cfg.llm.recommendation.provider == "gemini"
        assert cfg.llm.evaluation.provider == "openai"
        assert cfg.bilibili.browser_executable == "/Applications/Chrome.app"
        assert cfg.bilibili.browser_headed is True
        assert cfg.sources.browser_cdp_url == "http://localhost:9222"
        assert cfg.sources.browser_headed is True
        assert cfg.sources.bilibili.enabled is False
        assert cfg.sources.xiaohongshu.enabled is False
        assert cfg.sources.xiaohongshu.daily_search_budget == 11
        assert cfg.sources.douyin.enabled is True
        assert cfg.sources.douyin.cookie_env == "CUSTOM_DY_COOKIE"
        assert cfg.sources.douyin.daily_feed_budget == 13
        assert cfg.sources.youtube.enabled is True
        assert cfg.sources.youtube.daily_search_budget == 5
        assert cfg.sources.youtube.daily_trending_budget == 41
        assert cfg.sources.youtube.daily_channel_budget == 9
        assert cfg.sources.youtube.request_interval_seconds == 4
        assert cfg.sources.youtube.min_interval_minutes == 30
        assert response.json()["config"]["sources"]["youtube"]["min_interval_minutes"] == 30
        assert cfg.scheduler.pool_source_shares == {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
            "twitter": 1,
            "zhihu": 1,
            "reddit": 1,
            "bangumi": 1,
            "linuxdo": 1,
            "weibo": 1,
            "v2ex": 1,
        }
        assert cfg.scheduler.refresh_check_interval_seconds == 75
        assert cfg.scheduler.eval_min_batch_size == 23
        assert cfg.scheduler.eval_max_wait_seconds == 45.5
        assert response.json()["config"]["scheduler"]["eval_min_batch_size"] == 23
        assert response.json()["config"]["scheduler"]["eval_max_wait_seconds"] == 45.5
        assert cfg.scheduler.signal_event_threshold == 9
        assert cfg.scheduler.trending_refresh_minutes == 5
        assert cfg.scheduler.explore_refresh_minutes == 18
        assert cfg.scheduler.discovery_limit == 17
        assert cfg.scheduler.delight_queue_limit == 37
        assert response.json()["config"]["scheduler"]["delight_queue_limit"] == 37
        assert cfg.scheduler.proactive_push_interval_seconds == 155
        assert cfg.scheduler.llm_budget_max_calls == 45
        assert cfg.scheduler.llm_budget_window_seconds == 1800
        assert response.json()["config"]["scheduler"]["llm_budget_max_calls"] == 45
        assert response.json()["config"]["scheduler"]["llm_budget_window_seconds"] == 1800
        assert cfg.scheduler.speculator_idle_interval_minutes == 11
        assert cfg.scheduler.speculation_interval_minutes == 21
        assert cfg.scheduler.auto_update_enabled is True
        assert cfg.scheduler.auto_update_check_interval_hours == 10
        assert cfg.scheduler.auto_update_allow_prerelease is True
        assert cfg.scheduler.auto_update_allowed_remotes == [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
        assert cfg.discovery.eval_prefilter_mode == "enforce"
        assert cfg.discovery.eval_scorer == "shadow"
        assert cfg.discovery.admission_min_score == 0.72
        assert response.json()["config"]["discovery"]["eval_prefilter_mode"] == "enforce"
        assert response.json()["config"]["discovery"]["eval_scorer"] == "shadow"
        assert cfg.storage.db_path == "runtime-data/openbiliclaw.db"
        assert cfg.logging.file_level == "WARNING"
        assert cfg.logging.max_file_size_mb == 123
        assert cfg.logging.aggregate_budget_mb == 456
        assert cfg.logging.unmanaged_truncate_mb == 78
        assert cfg.logging.unmanaged_max_age_days == 9

    def test_put_config_rejects_invalid_eval_scorer(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.config import Config

        cfg = Config()
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"discovery": {"eval_scorer": "unsafe"}},
        )

        assert response.status_code == 422
        assert cfg.discovery.eval_scorer == "llm"

    def test_put_config_clears_deepseek_reasoning_effort(self, monkeypatch, tmp_path) -> None:
        """The settings UIs send an empty string when users disable DeepSeek thinking."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(
                    api_key="sk-deepseek",
                    model="deepseek-v4-flash",
                    reasoning_effort="max",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"llm": {"deepseek": {"reasoning_effort": ""}}},
        )

        assert response.status_code == 202
        assert cfg.llm.deepseek.reasoning_effort == ""
        assert response.json()["config"]["llm"]["deepseek"]["reasoning_effort"] == ""

    def test_put_config_normalizes_invalid_scheduler_runtime_fields(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "refresh_check_interval_seconds": "abc",
                    "signal_event_threshold": -1,
                    "trending_refresh_minutes": 0,
                    "explore_refresh_minutes": 0,
                    "discovery_limit": 61,
                    "delight_queue_limit": 101,
                    "proactive_push_interval_seconds": 29,
                    "speculator_idle_interval_minutes": 4,
                },
            },
        )

        assert response.status_code == 202
        scheduler = response.json()["config"]["scheduler"]
        assert scheduler["refresh_check_interval_seconds"] == 60
        assert scheduler["signal_event_threshold"] == 6
        assert scheduler["trending_refresh_minutes"] == 3
        assert scheduler["explore_refresh_minutes"] == 3
        assert scheduler["discovery_limit"] == 30
        assert scheduler["delight_queue_limit"] == 20
        assert scheduler["proactive_push_interval_seconds"] == 120
        assert scheduler["speculator_idle_interval_minutes"] == 30

    def test_source_share_suggestion_uses_event_counts(self, monkeypatch, tmp_path) -> None:
        """GET /api/config/source-share-suggestion should suggest ratios
        from observed platform event counts and current enabled switches."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        cfg = Config()
        cfg.sources.xiaohongshu.enabled = True
        cfg.sources.douyin.enabled = True
        cfg.sources.youtube.enabled = True
        cfg.scheduler.pool_source_shares = {
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
            "reddit": 1,
        }
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        class FakeDatabase:
            def count_events_by_source_platform(self) -> dict[str, int]:
                return {
                    "bilibili": 900,
                    "xiaohongshu": 100,
                    "douyin": 9,
                    "youtube": 400,
                    "reddit": 225,
                }

        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.get("/api/config/source-share-suggestion")

        assert response.status_code == 200
        assert response.json() == {
            "event_counts": {
                "bilibili": 900,
                "xiaohongshu": 100,
                "douyin": 9,
                "youtube": 400,
                "twitter": 0,
                "zhihu": 0,
                "reddit": 225,
                "weibo": 0,
                "v2ex": 0,
                "bangumi": 0,
                "linuxdo": 0,
                "unknown": 0,
            },
            "enabled_sources": {
                "bilibili": True,
                "xiaohongshu": True,
                "douyin": True,
                "youtube": True,
                "twitter": False,
                "zhihu": False,
                "reddit": False,
                "bangumi": False,
                "linuxdo": False,
                "weibo": False,
                "v2ex": False,
            },
            "suggested_shares": {
                "bilibili": 8,
                "xiaohongshu": 3,
                "douyin": 1,
                "youtube": 5,
            },
        }

    def test_source_share_fallback_prefers_top_level_source_platform(self) -> None:
        from openbiliclaw.api.app import _count_events_by_source_platform

        class Cursor:
            def fetchall(self) -> list[dict[str, str]]:
                return [
                    {
                        "source_platform": "youtube",
                        "metadata": '{"source_platform":"bilibili"}',
                    },
                    {
                        "source_platform": "",
                        "metadata": '{"source_platform":"twitter"}',
                    },
                ]

        class Connection:
            def execute(self, query: str) -> Cursor:
                assert query == "SELECT source_platform, metadata FROM events"
                return Cursor()

        class DatabaseWithoutCountMethod:
            conn = Connection()

        counts = _count_events_by_source_platform(DatabaseWithoutCountMethod())

        assert counts["youtube"] == 1
        assert counts["twitter"] == 1
        assert counts["bilibili"] == 0
        assert counts["unknown"] == 0

    def test_source_share_fallback_buckets_unknown_slugs(self) -> None:
        from openbiliclaw.api.app import _count_events_by_source_platform

        class Cursor:
            def fetchall(self) -> list[dict[str, str]]:
                return [
                    {
                        "source_platform": "threads",
                        "metadata": "{}",
                    },
                    {
                        "source_platform": "",
                        "metadata": '{"source_platform":"future-platform"}',
                    },
                ]

        class Connection:
            def execute(self, query: str) -> Cursor:
                assert query == "SELECT source_platform, metadata FROM events"
                return Cursor()

        class DatabaseWithoutCountMethod:
            conn = Connection()

        counts = _count_events_by_source_platform(DatabaseWithoutCountMethod())

        assert counts["unknown"] == 2
        assert counts["bilibili"] == 0

    def test_source_share_suggestion_post_uses_form_overrides(self, monkeypatch, tmp_path) -> None:
        """POST /api/config/source-share-suggestion should support the
        extension settings page's unsaved switch/share state."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        cfg = Config()
        cfg.sources.xiaohongshu.enabled = True
        cfg.sources.douyin.enabled = True
        cfg.sources.youtube.enabled = False
        cfg.scheduler.pool_source_shares = {
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        }
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        class FakeDatabase:
            def count_events_by_source_platform(self) -> dict[str, int]:
                return {
                    "bilibili": 900,
                    "xiaohongshu": 100,
                    "douyin": 9,
                    "youtube": 400,
                    "reddit": 225,
                }

        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/config/source-share-suggestion",
            json={
                "enabled_sources": {
                    "bilibili": True,
                    "xiaohongshu": False,
                    "douyin": False,
                    "youtube": True,
                    "reddit": True,
                },
                "configured_shares": {
                    "bilibili": 6,
                    "xiaohongshu": 4,
                    "douyin": 4,
                    "youtube": 2,
                    "reddit": 1,
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "event_counts": {
                "bilibili": 900,
                "xiaohongshu": 100,
                "douyin": 9,
                "youtube": 400,
                "twitter": 0,
                "zhihu": 0,
                "reddit": 225,
                "weibo": 0,
                "v2ex": 0,
                "bangumi": 0,
                "linuxdo": 0,
                "unknown": 0,
            },
            "enabled_sources": {
                "bilibili": True,
                "xiaohongshu": False,
                "douyin": False,
                "youtube": True,
                "twitter": False,
                "zhihu": False,
                "reddit": True,
                "bangumi": False,
                "linuxdo": False,
                "weibo": False,
                "v2ex": False,
            },
            "suggested_shares": {
                "bilibili": 6,
                "reddit": 3,
                "youtube": 4,
            },
        }


def test_events_endpoint_emits_activity_added_runtime_event() -> None:
    """v0.3.38 — POST /api/events publishes ``activity.added`` so the
    popup can refresh its activity feed without polling.
    """
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeRuntimeController:
        def __init__(self, hub: FakeEventHub) -> None:
            self.event_hub = hub

    hub = FakeEventHub()
    memory = FakeMemoryManager()
    app = create_app(
        memory_manager=memory,
        database=object(),
        soul_engine=object(),
        runtime_controller=FakeRuntimeController(hub),
    )
    client = TestClient(app)

    response = client.post(
        "/api/events",
        json={
            "events": [
                {
                    "event_id": "events-activity-a",
                    "type": "click",
                    "url": "https://www.bilibili.com/video/BV1A",
                    "title": "A",
                    "timestamp": 1710000000000,
                },
                {
                    "event_id": "events-activity-b",
                    "type": "view",
                    "url": "https://www.bilibili.com/video/BV1B",
                    "title": "B",
                    "timestamp": 1710000001000,
                },
                {
                    "event_id": "events-activity-c",
                    "type": "click",
                    "url": "https://www.bilibili.com/video/BV1C",
                    "title": "C",
                    "timestamp": 1710000002000,
                },
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 3

    activity_events = [e for e in hub.events if e["type"] == "activity.added"]
    assert len(activity_events) == 1, "should fire exactly once per ingest call"
    assert activity_events[0]["count"] == 3


def test_events_endpoint_skips_activity_added_for_empty_batch() -> None:
    """No events accepted → no activity.added (avoids spamming popup
    when the extension flushes an empty buffer)."""
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeRuntimeController:
        def __init__(self, hub: FakeEventHub) -> None:
            self.event_hub = hub

    hub = FakeEventHub()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=object(),
        runtime_controller=FakeRuntimeController(hub),
    )
    client = TestClient(app)

    response = client.post("/api/events", json={"events": []})
    assert response.status_code == 200
    assert response.json()["accepted"] == 0

    activity_events = [e for e in hub.events if e["type"] == "activity.added"]
    assert activity_events == []


def test_probe_chat_sentiment_uses_plain_text_llm_call() -> None:
    """Probe chat sentiment asks for one scalar word, not a JSON object.

    DeepSeek rejects ``response_format=json_object`` for this prompt because
    it intentionally does not ask for JSON. The API should therefore call the
    plain core-memory LLM path with ``json_mode=False``.
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    class FakeLLM:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def complete_structured_task(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(("structured", kwargs))
            return SimpleNamespace(content="positive")

        async def complete_with_core_memory(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(("core", kwargs))
            return SimpleNamespace(content="positive")

    class FakeDialogue:
        async def respond(self, _message: str, *, scope: str = "chat", turn_id: str = "") -> str:
            return "懂，你更喜欢和 VOCALOID 相关的部分。"

    class FakeSpeculator:
        def __init__(self) -> None:
            self.observed: list[object] = []

        def observe(self, events: object) -> None:
            self.observed.append(events)

        def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class FakeSoulEngine:
        def __init__(self, speculator: FakeSpeculator) -> None:
            self._speculator = speculator

    class FakeMemoryManager:
        def load_cognition_updates(self) -> list[object]:
            return []

        def save_cognition_updates(self, _updates: list[object]) -> None:
            return None

    llm = FakeLLM()
    speculator = FakeSpeculator()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=FakeSoulEngine(speculator),
        dialogue=FakeDialogue(),
        recommendation_engine=SimpleNamespace(_llm=llm),
    )
    client = TestClient(app)

    response = client.post(
        "/api/interest-probes/respond",
        json={
            "domain": "电子音乐制作",
            "response": "chat",
            "message": "我更喜欢与vocaloid有关系的部分",
        },
    )

    assert response.status_code == 200
    assert llm.calls
    method, kwargs = llm.calls[0]
    assert method == "core"
    assert kwargs["caller"] == "api.sentiment"
    # 16 (was 8) so the longest label `neutral_deferred` can't truncate.
    assert kwargs["max_tokens"] == 16
    assert kwargs["json_mode"] is False


class TestProfileEditEndpoints:
    """End-to-end tests for the editable-profile API (real SoulEngine)."""

    def _client(self, tmp_path: Path) -> object:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.soul.profile import (
            CoreLayer,
            InterestDomain,
            InterestLayer,
            OnionProfile,
        )

        class _Reg:
            async def complete(
                self,
                messages: list[dict[str, str]],
                *,
                temperature: float = 0.7,
                max_tokens: int = 4096,
                json_mode: bool = False,
                reasoning_effort: str | None = None,
                model: str | None = None,
            ) -> LLMResponse:
                return LLMResponse(content="{}", provider="openai")

        memory = MemoryManager(tmp_path / "data")
        memory.initialize()
        engine = SoulEngine(llm=_Reg(), memory=memory)
        profile = OnionProfile(
            core=CoreLayer(core_traits=["完美主义"]),
            interest=InterestLayer(
                likes=[InterestDomain(domain=f"领域{i}", weight=0.5) for i in range(14)],
                favorite_up_users=[f"UP{i}" for i in range(10)],
            ),
        )
        layer = memory.get_layer("soul")
        layer.data.clear()
        layer.data.update(profile.to_dict())
        layer.save()
        app = create_app(memory_manager=memory, database=memory._database, soul_engine=engine)
        return TestClient(app)

    def test_edit_state_returns_untruncated_fields(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.get("/api/profile/edit-state")  # type: ignore[attr-defined]
        assert resp.status_code == 200
        body = resp.json()
        assert body["initialized"] is True
        # un-truncated: summary caps likes at 12 / favorite_up at 8; edit-state must not
        assert len(body["fields"]["likes"]["domains"]) == 14
        assert len(body["fields"]["interest.favorite_up_users"]["items"]) == 10

    def test_edit_adds_dislike_and_reflects_in_returned_state(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "dislikes", "op": "add", "value": "营销号"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        domains = body["edit_state"]["fields"]["dislikes"]["domains"]
        assert any(d["domain"] == "营销号" and d["user_added"] for d in domains)

    def test_edit_invalid_target_returns_422(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "core.bogus", "op": "add", "value": "x"},
        )
        assert resp.status_code == 422

    def test_edit_set_then_reset_text_field(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "personality_portrait", "op": "set", "value": "我的画像"},
        )
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state["fields"]["personality_portrait"]["value"] == "我的画像"
        assert state["fields"]["personality_portrait"]["pinned"] is True

        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "personality_portrait", "op": "reset"},
        )
        state2 = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state2["fields"]["personality_portrait"]["pinned"] is False

    def test_edit_set_then_reset_scalar_field(self, tmp_path: Path) -> None:
        # Mirrors the slider UI path: scalar fields surface in edit-state and
        # round-trip through op=set (numeric, clamped 0..1) + op=reset.
        client = self._client(tmp_path)
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state["fields"]["surface.exploration_openness"]["type"] == "scalar"

        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "surface.exploration_openness", "op": "set", "value": 0.8},
        )
        assert resp.status_code == 200
        field = resp.json()["edit_state"]["fields"]["surface.exploration_openness"]
        assert field["pinned"] is True
        assert abs(field["value"] - 0.8) < 1e-9

        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "surface.exploration_openness", "op": "reset"},
        )
        state2 = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state2["fields"]["surface.exploration_openness"]["pinned"] is False

    def test_profile_summary_carries_overrides_annotation(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "core.core_traits", "op": "add", "value": "务实"},
        )
        resp = client.get("/api/profile-summary")  # type: ignore[attr-defined]
        assert resp.status_code == 200
        overrides = resp.json()["overrides"]
        assert overrides["list_edits"]["core.core_traits"]["add"] == ["务实"]

    def test_summary_surfaces_user_added_item_past_display_cap(self, tmp_path: Path) -> None:
        # Seed has 10 favorite_up_users; the summary caps the list at 8. A
        # user-added entry lands past the cap and must still surface — it used
        # to show in edit mode (un-truncated) but get truncated out of the
        # read-only summary, reading as if the edit was lost.
        client = self._client(tmp_path)
        marker = "我亲手加的UP"
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "interest.favorite_up_users", "op": "add", "value": marker},
        )
        assert resp.status_code == 200
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert marker in state["fields"]["interest.favorite_up_users"]["items"]
        summary = client.get("/api/profile-summary").json()  # type: ignore[attr-defined]
        assert marker in summary["favorite_up_users"]


def test_cap_keeping_user_added_keeps_manual_entries_past_limit() -> None:
    """The summary display cap must never hide a user-added entry."""
    from openbiliclaw.api.app import _cap_keeping_user_added

    # 6 AI items + 1 user-added past the cap of 6 → user entry rides along.
    merged = ["a", "b", "c", "d", "e", "f", "用户加的"]
    assert _cap_keeping_user_added(merged, ["用户加的"], 6) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "用户加的",
    ]
    # No overrides → plain truncation (AI noise still capped).
    assert _cap_keeping_user_added(["a", "b", "c", "d", "e", "f", "g"], [], 6) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
    ]
    # Under the cap → unchanged.
    assert _cap_keeping_user_added(["a", "b"], ["a"], 6) == ["a", "b"]

    # Interest-domain objects via key=.
    class _D:
        def __init__(self, domain: str) -> None:
            self.domain = domain

    doms = [_D(x) for x in ["a", "b", "c", "d", "e", "f", "g", "h", "mine"]]
    out = _cap_keeping_user_added(doms, ["mine"], 8, key=lambda d: d.domain)
    assert any(d.domain == "mine" for d in out)
    assert len(out) == 9  # 8 head + 1 user-added


class _FakeInitPrereqs:
    """Controllable stand-in for InitPrereqs (E2 endpoint tests)."""

    def __init__(
        self,
        *,
        bili: str = "ok",
        chat: bool = True,
        platforms=None,
        chat_detail: str = "",
        capability_readiness: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._bili = bili
        self._chat = chat
        self._chat_detail = chat_detail
        self._platforms = list(platforms or [])
        self._capability_readiness = dict(capability_readiness or {})
        self.bilibili_check_calls = 0
        self.chat_ready_calls = 0

    async def bilibili_check(self) -> str:
        self.bilibili_check_calls += 1
        return self._bili

    async def chat_ready(self) -> bool:
        self.chat_ready_calls += 1
        return self._chat

    def peek_bilibili(self) -> str:
        return self._bili

    def peek_bilibili_detail(self) -> str:
        return "" if self._bili == "ok" else "检测请求失败（stub 网络错误）。"

    def peek_chat(self) -> bool:
        return self._chat

    def peek_chat_detail(self) -> str:
        return self._chat_detail

    def has_cached_readiness(self) -> bool:
        return True

    def enabled_platforms(self) -> list[str]:
        return list(self._platforms)

    def source_capability_readiness(self, slug: str, capability: str) -> str:
        return self._capability_readiness.get((slug, capability), "ready")


def test_init_crash_detail_summarizes_exception() -> None:
    """One line, class-prefixed, capped — safe to surface in init-status."""
    from openbiliclaw.api.app import _init_crash_detail

    assert _init_crash_detail(RuntimeError("boom")) == "RuntimeError: boom"
    # Multi-line messages collapse to the first line.
    assert _init_crash_detail(ValueError("first line\nsecond line")) == "ValueError: first line"
    # Empty message still identifies the exception class.
    assert _init_crash_detail(KeyError()) == "KeyError"
    # Length-capped so a huge provider error body can't flood the status.
    assert len(_init_crash_detail(RuntimeError("x" * 1000))) == 300


def test_init_crash_detail_rewrites_llm_failure_to_advice() -> None:
    """An LLM-shaped crash surfaces actionable advice, not the raw 500 body."""
    from openbiliclaw.api.app import _init_crash_detail
    from openbiliclaw.llm.base import LLMProviderError

    try:
        try:
            raise RuntimeError("Error code: 500 - 根据相关法律法规，我们无法提供关于以下内容的答案")
        except RuntimeError as upstream:
            raise LLMProviderError("openai_compatible request failed") from upstream
    except LLMProviderError as exc:
        detail = _init_crash_detail(exc)
    # Content-moderation advice, not "LLMProviderError: openai_compatible …".
    assert "内容合规" in detail
    assert not detail.startswith("LLMProviderError")


def test_select_init_platforms_none_selection_uses_all_enabled() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    enabled = {"bilibili", "xiaohongshu", "douyin", "reddit"}
    # None = no selection sent (CLI / legacy) → use everything enabled.
    assert _select_init_platforms(enabled, None) == enabled


def test_select_init_platforms_explicit_selection_is_opt_in() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    enabled = {"bilibili", "xiaohongshu", "douyin", "youtube"}
    assert _select_init_platforms(enabled, {"bilibili", "xiaohongshu"}) == {
        "bilibili",
        "xiaohongshu",
    }
    assert _select_init_platforms({"bilibili"}, {"bilibili", "xiaohongshu", "douyin"}) == {
        "bilibili",
        "xiaohongshu",
        "douyin",
    }


def test_select_init_platforms_keeps_unconfigured_selection() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    # A selected source is an explicit guided-init opt-in, so it must become
    # effective even if it was not already enabled in settings.
    assert _select_init_platforms({"bilibili", "xiaohongshu"}, {"bilibili", "douyin"}) == {
        "bilibili",
        "douyin",
    }


def test_select_init_platforms_empty_selection_yields_empty() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    # Explicit empty selection narrows to nothing optional (bilibili is the base,
    # pulled separately, so an empty effective set is fine).
    assert _select_init_platforms({"bilibili", "xiaohongshu"}, set()) == set()


class TestGuidedInitEndpoints:
    """E2: POST /api/init + POST /api/init/cancel (local-only, gui-init §2/§5b)."""

    def _make_app(
        self,
        tmp_path,
        *,
        profile_ready=False,
        prereqs=None,
        embedding_provider: str | None = None,
    ):
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "e2.db")
        db.initialize()
        soul = SimpleNamespace(is_profile_ready=lambda: profile_ready)
        app = create_app(memory_manager=object(), database=db, soul_engine=soul)
        app.state.auth_gate.is_trusted_local = lambda request: True
        if embedding_provider is not None:
            from openbiliclaw.config import Config

            app.state.runtime_context.config = Config()
            data_dir = tmp_path / "data"
            data_dir.mkdir()
            app.state.runtime_context.config.data_dir = str(data_dir)
            app.state.runtime_context.config.llm.embedding.provider = embedding_provider
        if prereqs is not None:
            app.state.runtime_context._init_prereqs = prereqs
        return app, db

    def test_init_rejects_non_local(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 403
        assert resp.json()["error"] == "local_only"

    def test_init_rejects_invalid_llm_concurrency(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, db = self._make_app(tmp_path)
        with TestClient(app) as client:
            for bad in (0, 17, "not-an-int"):
                resp = client.post(
                    "/api/init",
                    json={"sources": ["xiaohongshu"], "llm_concurrency": bad},
                )
                assert resp.status_code == 400
                assert resp.json()["error"] == "invalid_llm_concurrency"
        assert db.get_latest_init_run() is None

    def test_init_passes_llm_concurrency_to_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["douyin"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={"sources": ["douyin"], "llm_concurrency": 2},
            )
            assert resp.status_code == 202, resp.text
            self._drive_until(client, captured, key="llm_concurrency")
        assert captured["llm_concurrency"] == 2

    def test_init_rejects_docker_runtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        monkeypatch.setenv("OPENBILICLAW_IN_CONTAINER", "1")
        app, db = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "unsupported_runtime"
        # Rejected before reserving — no run row created at all.
        assert db.get_latest_init_run() is None

    def test_init_rejects_already_initialized_without_force(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, db = self._make_app(tmp_path, profile_ready=True)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_initialized"
        assert db.get_latest_init_run() is None

    def test_init_force_bypasses_already_initialized_guard(self, tmp_path: Path) -> None:
        """``force:true`` must pass the already-initialized guard (re-init entry).

        A later prerequisite failure (here: B 站登录) proves the request actually
        advanced past the initialized guard instead of being short-circuited by
        it — the re-init path keeps the normal prerequisite checks.
        """
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="failed", chat=True, platforms=["bilibili"])
        app, db = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"force": True})
        assert resp.status_code == 409
        assert resp.json()["error"] == "bilibili_not_logged_in"
        # Guard bypassed: the failure is a later prerequisite, not
        # already_initialized, and the run was rolled back cleanly.
        assert resp.json()["error"] != "already_initialized"
        run = db.get_latest_init_run()
        assert run["status"] == "idle"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def test_init_force_starts_reinit_when_prerequisites_pass(self, tmp_path: Path) -> None:
        """``force:true`` with healthy prerequisites reserves a real re-init run."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, db = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"force": True, "reset_cognition": True})
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["run_id"]
            assert body["status"] in {"starting", "running"}
            # Clean up the background run so the test exits without a live task.
            client.post("/api/init/cancel", json={})

    def test_init_already_running_returns_409(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            # Seed AFTER startup reconcile (which would otherwise fail a stale
            # "starting" row) so the run is genuinely active at POST time.
            app.state.runtime_context.init_coordinator.try_start("existing")
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_running"

    def test_init_missing_bilibili_resets_to_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="failed", chat=True, platforms=["bilibili"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "bilibili_not_logged_in"
        # Critical: the reserved run was rolled back, never left "starting".
        run = db.get_latest_init_run()
        assert run["status"] == "idle"
        assert run["error_reason"] == "bilibili_not_logged_in"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def test_init_missing_llm_resets_to_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=False, platforms=["bilibili"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "llm_not_ready"
        assert db.get_latest_init_run()["status"] == "idle"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def test_init_llm_not_ready_propagates_classified_detail(self, tmp_path: Path) -> None:
        """The 409 must carry the probe's classified cause (defect 3) so the
        user can tell an invalid API key from an unreachable service."""
        from fastapi.testclient import TestClient

        detail = "AI 服务鉴权失败（HTTP 401），API key 可能填错或已失效。"
        prereqs = _FakeInitPrereqs(
            bili="ok", chat=False, platforms=["bilibili"], chat_detail=detail
        )
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "llm_not_ready"
        assert resp.json()["detail"] == detail

    def test_init_status_llm_not_ready_surfaces_classified_detail(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        detail = "无法连接到 AI 服务（网络连接失败）。请检查网络与代理设置后重试。"
        prereqs = _FakeInitPrereqs(
            bili="ok", chat=False, platforms=["bilibili"], chat_detail=detail
        )
        app, _ = self._make_app(tmp_path, embedding_provider="", prereqs=prereqs)
        with TestClient(app) as client:
            body = client.get("/api/init-status").json()
        assert body["reason"] == "llm_not_ready"
        assert body["detail"] == detail

    def test_init_missing_configured_embedding_resets_to_idle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        real_exists = Path.exists

        def _exists_without_container_markers(path: Path) -> bool:
            if str(path) in {"/.dockerenv", "/run/.containerenv"}:
                return False
            return real_exists(path)

        monkeypatch.delenv("OPENBILICLAW_IN_CONTAINER", raising=False)
        monkeypatch.setattr(Path, "exists", _exists_without_container_markers)
        from openbiliclaw.docker_runtime import is_running_in_container

        assert is_running_in_container() is False
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
        app, db = self._make_app(tmp_path, prereqs=prereqs, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["xiaohongshu"]})
        assert resp.status_code == 409
        assert resp.json()["error"] == "embedding_not_ready"
        latest = db.get_latest_init_run()
        assert latest["status"] == "idle"
        assert latest["error_reason"] == "embedding_not_ready"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def test_init_post_rejects_loopback_ollama_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as appmod
        from openbiliclaw.llm import ollama_diagnostics as od

        monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_ERROR, "cold loading"

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)

        class _SlowProbeService:
            async def probe(self) -> bool:
                await asyncio.sleep(0.2)
                return True

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
        app, db = self._make_app(
            tmp_path,
            prereqs=prereqs,
            embedding_provider="ollama",
        )
        app.state.runtime_context.soul_engine._embedding_service = _SlowProbeService()
        app.state.runtime_context.config.autostart.manage_ollama = False

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["xiaohongshu"]})

        assert response.status_code == 409
        assert response.json()["error"] == "embedding_not_ready"
        assert db.get_latest_init_run()["status"] == "idle"

    def test_init_skips_bilibili_login_check_when_deselected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v0.3.118+: B站 login only gates a run that actually includes bilibili."""
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(bili="failed", chat=True, platforms=["bilibili", "xiaohongshu"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["xiaohongshu"]})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["include_bili"] is False
        assert captured["include_xhs"] is True

    def test_init_force_wires_pool_purge_and_cognition_reset(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Force re-init must inject the pool-purge callback + reset_cognition.

        Regression: the purge used to be forwarded via backfill signature
        sniffing, and the API wrapper's backfill silently omitted the flag —
        force re-init on the API path never retired the old pool (field report
        2026-08-12, caught by real E2E). The pipeline now receives a callback
        unconditionally and runs it at stage-4 start.
        """
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"force": True, "reset_cognition": True})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["purge_pool_callback"] is not None
        assert captured["reset_cognition"] is True

    def test_init_first_run_does_not_inject_pool_purge(self, tmp_path: Path, monkeypatch) -> None:
        """A first (non-force) init has no pool to purge and must not inject."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["purge_pool_callback"] is None

    def test_init_rejects_empty_source_selection(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili", "xiaohongshu"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": []})
        assert resp.status_code == 409
        assert resp.json()["error"] == "no_sources_selected"
        # Rejected before reserving — no run row created at all.
        assert db.get_latest_init_run() is None

    def test_init_accepts_reddit_as_only_profile_signal_source(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["reddit"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["reddit"]})
            assert resp.status_code == 202
            self._drive_until(client, captured, key="include_reddit")
        assert captured["include_bili"] is False
        assert captured["include_reddit"] is True
        assert db.get_latest_init_run() is not None

    def test_init_accepts_linuxdo_as_only_profile_signal_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["linuxdo"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["linuxdo"]})
            assert response.status_code == 202
            self._drive_until(client, captured, key="include_linuxdo")

        assert captured["include_bili"] is False
        assert captured["include_linuxdo"] is True
        assert db.get_latest_init_run() is not None

    def test_init_rejects_linuxdo_only_when_profile_capability_is_signed_out(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(
            bili="ok",
            chat=True,
            platforms=["linuxdo"],
            capability_readiness={("linuxdo", "profile"): "login_required"},
        )
        app, db = self._make_app(tmp_path, prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["linuxdo"]})

        assert response.status_code == 409
        assert response.json() == {
            "error": "no_profile_signal_sources",
            "detail": (
                "Linux.do 公开发现无需登录，但初始化所需的收藏、点赞和阅读记录"
                "需要已登录的浏览器会话。请先在当前浏览器登录 Linux.do 并连接插件。"
            ),
            "capability": "profile",
            "readiness": "login_required",
        }
        assert db.get_latest_init_run() is None

    def test_init_records_douyin_degraded_as_partial_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A usable-but-incomplete Douyin bootstrap must not become a fully
        successful API init terminal state."""
        import time

        from fastapi.testclient import TestClient

        async def _fake_init(**kwargs: object) -> object:
            return SimpleNamespace(
                discovery_error=False,
                discovery_reason=None,
                discovery_detail="",
                dy_status="degraded",
                dy_events=[{"event_type": "favorite"}],
            )

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", _fake_init)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["douyin"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)

        latest = None
        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["douyin"]})
            assert response.status_code == 202
            for _ in range(100):
                latest = db.get_latest_init_run()
                if latest is not None and latest["status"] == "completed":
                    break
                client.get("/api/init-status")
                time.sleep(0.02)

        assert latest is not None
        assert latest["status"] == "completed"
        assert bool(latest["partial_success"]) is True
        assert latest["error_reason"] == "douyin_degraded"
        assert "dy_status=degraded" in str(latest["error_detail"])
        assert "已保留并用于画像建模 1 条已采事件" in str(latest["error_detail"])

    def test_init_rejects_bangumi_only_without_public_username(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bangumi"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bangumi"]})

        assert resp.status_code == 409
        assert resp.json()["error"] == "no_profile_signal_sources"
        assert db.get_latest_init_run() is None

    def test_init_accepts_scoped_bangumi_username(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["bangumi"],
                    "source_options": {"bangumi": {"username": " sai "}},
                },
            )
            assert resp.status_code == 202, resp.text
            self._drive_until(client, captured, key="include_bangumi")

        assert captured["include_bangumi"] is True
        assert captured["bangumi_username"] == "sai"

    def test_init_mixed_sources_allow_discovery_only_bangumi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["reddit"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        cfg = Config()
        data_dir = tmp_path / "mixed-bangumi-data"
        data_dir.mkdir()
        cfg.data_dir = str(data_dir)
        cfg.sources.bangumi.enabled = True
        cfg.sources.bangumi.username = "previous-user"
        app.state.runtime_context.config = cfg
        app.state.runtime_context.degraded = True
        saved_usernames: list[str] = []
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda saved: saved_usernames.append(saved.sources.bangumi.username),
        )
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["reddit", "bangumi"],
                    "source_options": {"bangumi": {"username": ""}},
                },
            )
            assert resp.status_code == 202, resp.text
            assert resp.json()["warnings"]
            self._drive_until(client, captured, key="include_bangumi")

        assert captured["include_reddit"] is True
        assert captured["include_bangumi"] is True
        assert captured["bangumi_username"] == ""
        assert cfg.sources.bangumi.username == ""
        assert saved_usernames == [""]

    def test_init_uses_configured_bangumi_username_when_option_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        cfg = Config()
        data_dir = tmp_path / "configured-bangumi-data"
        data_dir.mkdir()
        cfg.data_dir = str(data_dir)
        cfg.sources.bangumi.enabled = True
        cfg.sources.bangumi.username = "configured-user"
        app.state.runtime_context.config = cfg
        captured = self._capture_run_guided_init(monkeypatch)

        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bangumi"]})
            assert resp.status_code == 202, resp.text
            self._drive_until(client, captured, key="include_bangumi")

        assert captured["include_bangumi"] is True
        assert captured["bangumi_username"] == "configured-user"

    def test_init_accepts_bangumi_access_token_and_resolves_username(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        cfg = Config()
        data_dir = tmp_path / "token-bangumi-data"
        data_dir.mkdir()
        cfg.data_dir = str(data_dir)
        app.state.runtime_context.config = cfg
        app.state.runtime_context.degraded = True

        async def _fake_resolve(token, **kwargs):
            assert token == "tok-123"
            return "token-owner"

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _fake_resolve,
        )
        saved: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda cfg: saved.append(
                (cfg.sources.bangumi.username, cfg.sources.bangumi.access_token)
            ),
        )
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["bangumi"],
                    # A username differing from /v0/me should be overridden.
                    "source_options": {
                        "bangumi": {"username": "typed-name", "access_token": " tok-123 "}
                    },
                },
            )
            assert resp.status_code == 202, resp.text
            assert any("token-owner" in w for w in resp.json().get("warnings", []))
            self._drive_until(client, captured, key="include_bangumi")

        assert captured["include_bangumi"] is True
        assert captured["bangumi_username"] == "token-owner"
        assert captured["bangumi_token"] == "tok-123"
        # The validated token + resolved username are persisted for later syncs.
        assert saved and saved[-1] == ("token-owner", "tok-123")

    def test_init_rejects_invalid_bangumi_access_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, db = self._make_app(tmp_path, prereqs=prereqs)

        async def _fake_resolve(token, **kwargs):
            raise BangumiAPIError("unauthorized", "denied", status_code=401)

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _fake_resolve,
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["bangumi"],
                    "source_options": {"bangumi": {"access_token": "expired"}},
                },
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_bangumi_access_token"
        assert db.get_latest_init_run() is None

    def _install_fake_bangumi_user_api(self, monkeypatch, users: dict[str, dict] | Exception):
        """Stub BangumiClient.get_user for the identity-verify path.

        ``users`` maps lookup (username or uid string) → user object; a missing
        key raises ``not_found``. Passing an Exception makes every lookup fail
        with it (network-failure path).
        """
        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        lookups: list[str] = []

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get_user(self, username: str):
                lookups.append(username)
                if isinstance(users, Exception):
                    raise users
                if username not in users:
                    raise BangumiAPIError("not_found", "missing", status_code=404)
                return users[username]

        monkeypatch.setattr("openbiliclaw.sources.bangumi_client.BangumiClient", _FakeClient)
        return lookups

    def _identity_state_app(self, tmp_path: Path):
        app, _ = self._make_app(tmp_path)
        saved_states: list[dict[str, object]] = []
        state: dict[str, object] = {}

        def _update(mutator):
            mutator(state)
            saved_states.append(dict(state))
            return state

        app.state.runtime_context.runtime_controller = SimpleNamespace(
            memory_manager=SimpleNamespace(
                load_discovery_runtime_state=lambda: dict(state),
                update_discovery_runtime_state=_update,
            )
        )
        return app, state, saved_states

    def test_bangumi_identity_endpoint_persists_public_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        app, state, saved_states = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(
            monkeypatch,
            {"sai": {"id": 123456, "username": "sai", "nickname": "Sai"}},
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123456, "username": " sai "},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == {
                "ok": True,
                "uid": "123456",
                "username": "sai",
                "verified": True,
            }
            # Re-reporting the same identity is value-idempotent. It does cost
            # one rewrite: deciding "unchanged" without the lock would mean
            # answering from a snapshot a concurrent writer can already have
            # invalidated, so the atomic section is always entered.
            resp2 = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123456, "username": "sai"},
            )
            assert resp2.status_code == 200
            # Missing / non-positive uid is rejected outright.
            assert (
                client.post("/api/sources/bangumi/identity", json={"username": "sai"}).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/sources/bangumi/identity", json={"uid": 0, "username": "sai"}
                ).status_code
                == 422
            )

        verified_sai = {"uid": "123456", "username": "sai", "verified": True}
        assert saved_states[0]["bangumi_self_info"] == verified_sai
        # Every write carries the same value — idempotent in content.
        assert all(saved["bangumi_self_info"] == verified_sai for saved in saved_states)
        assert state["bangumi_self_info"] == verified_sai

    def test_bangumi_identity_discards_username_belonging_to_another_uid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: DOM scrape once reported a timeline stranger's username."""
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(
            monkeypatch,
            # The stranger from the real E2E: yuzzyu belongs to uid 1216399.
            {"yuzzyu": {"id": 1216399, "username": "yuzzyu"}},
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 999999001, "username": "yuzzyu"},
            )
            assert resp.status_code == 200
            # Username discarded, uid kept — never persist a stranger identity.
            # NOT verified: bgm.tv refuted the username, it never told us who
            # uid 999999001 actually is, so nothing about this identity is
            # confirmed and sticky-true must not be able to pin it.
            assert resp.json() == {
                "ok": True,
                "uid": "999999001",
                "username": "",
                "verified": False,
            }
            # A username that doesn't exist at all is discarded too.
            resp2 = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 999999001, "username": "ghost-user"},
            )
            assert resp2.json()["username"] == ""
            assert resp2.json()["verified"] is False
        assert state["bangumi_self_info"] == {
            "uid": "999999001",
            "username": "",
            "verified": False,
        }

    def test_bangumi_identity_uid_only_resolves_username_from_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default-slug users resolve from a bare uid (username == str(uid))."""
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(
            monkeypatch,
            {"474349": {"id": 474349, "username": "474349", "nickname": "玉之米"}},
        )
        with TestClient(app) as client:
            resp = client.post("/api/sources/bangumi/identity", json={"uid": 474349})
            assert resp.status_code == 200
            assert resp.json() == {
                "ok": True,
                "uid": "474349",
                "username": "474349",
                "verified": True,
            }
        assert state["bangumi_self_info"] == {
            "uid": "474349",
            "username": "474349",
            "verified": True,
        }

    def test_bangumi_identity_uid_only_custom_slug_stays_uid_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/v0/users/{uid} 404s for custom-slug users; keep the uid, no junk."""
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, {})
        with TestClient(app) as client:
            resp = client.post("/api/sources/bangumi/identity", json={"uid": 1})
            assert resp.status_code == 200
            # A uid-only lookup that 404s checked nothing about this uid's
            # owner, so it is NOT verified despite bgm.tv having answered.
            assert resp.json() == {"ok": True, "uid": "1", "username": "", "verified": False}
            # A structurally malformed username normalizes to missing first,
            # then follows the same uid-only path.
            resp2 = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 1, "username": "bad/name"},
            )
            assert resp2.json() == {"ok": True, "uid": "1", "username": "", "verified": False}
        assert state["bangumi_self_info"] == {"uid": "1", "username": "", "verified": False}

    def test_bangumi_identity_network_failure_accepts_dom_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Upstream unavailability degrades to best-effort, never a hard fail.

        Staying fail-open is deliberate (bgm.tv sits behind overseas CF, and
        under the default ``[network] mode=system`` a CN machine without a
        working proxy still cannot reach it, so fail-closed would break every
        such zero-config user). The honesty has to come from elsewhere: a
        WARNING carrying the real cause, and a ``verified: false`` flag on the
        stored record so no consumer mistakes it for a checked identity.
        """
        import logging

        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(
            monkeypatch, BangumiAPIError("timeout", "Bangumi API request timed out")
        )
        with caplog.at_level(logging.DEBUG), TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123, "username": "maybe-me"},
            )
            assert resp.status_code == 200
            assert resp.json() == {
                "ok": True,
                "uid": "123",
                "username": "maybe-me",
                "verified": False,
            }
        assert state["bangumi_self_info"] == {
            "uid": "123",
            "username": "maybe-me",
            "verified": False,
        }
        # Diagnosable at WARNING (was DEBUG-only), with the real cause.
        failures = [
            record
            for record in caplog.records
            if "bangumi identity" in record.getMessage()
            and "could not verify" in record.getMessage()
        ]
        assert failures, "verify failure must be logged"
        assert all(record.levelno >= logging.WARNING for record in failures)
        assert "timeout" in failures[0].getMessage()
        assert "UNVERIFIED" in failures[0].getMessage()

    def test_bangumi_identity_unexpected_error_logs_warning_and_marks_unverified(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-BangumiAPIError (DNS, TLS, proxy) takes the same honest path."""
        import logging

        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, OSError("proxy refused"))
        with caplog.at_level(logging.DEBUG), TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123, "username": "maybe-me"},
            )
            assert resp.json()["verified"] is False
        assert state["bangumi_self_info"]["verified"] is False
        failures = [
            record for record in caplog.records if "could not verify" in record.getMessage()
        ]
        assert failures and all(record.levelno >= logging.WARNING for record in failures)
        assert "OSError" in failures[0].getMessage()

    def test_bangumi_identity_reverify_upgrades_unverified_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A later successful report replaces a fail-open record in place."""
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, saved_states = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
        with TestClient(app) as client:
            client.post("/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"})
            assert state["bangumi_self_info"]["verified"] is False
            # bgm.tv reachable again on the next page view → record upgrades.
            self._install_fake_bangumi_user_api(
                monkeypatch, {"sai": {"id": 123456, "username": "sai"}}
            )
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            assert resp.json()["verified"] is True
        assert state["bangumi_self_info"] == {
            "uid": "123456",
            "username": "sai",
            "verified": True,
        }
        assert len(saved_states) == 2  # the flag change is a real write

    def test_bangumi_identity_verified_flag_survives_a_later_network_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a flaky re-report must not erase proof we already hold.

        ``verified`` used to be overwritten with whatever this round produced,
        so one bgm.tv timeout downgraded a genuinely confirmed identity to
        ``false`` — and guided init then told the user their real account was
        "未经 bgm.tv 校验". The flag records a confirmation of a uid↔username
        pair; it ratchets up and is never downgraded for the same identity.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, saved_states = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, {"sai": {"id": 123456, "username": "sai"}})
        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            assert resp.json()["verified"] is True

            # bgm.tv goes down; the extension re-reports the same identity.
            self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
            resp2 = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            assert resp2.json()["verified"] is True, "a timeout must not downgrade the flag"

        assert state["bangumi_self_info"] == {
            "uid": "123456",
            "username": "sai",
            "verified": True,
        }
        # Rewrites are allowed (the atomic section is always entered), but the
        # value must never flap: every write carries verified=True.
        assert all(saved["bangumi_self_info"]["verified"] is True for saved in saved_states)

    def test_bangumi_identity_verified_flag_does_not_carry_to_a_different_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sticky-true is per identity: a new uid/username starts from scratch.

        The old evidence says nothing about a pair we have never checked, so
        carrying the flag across would be exactly the plausible-but-wrong claim
        the whole guard exists to prevent.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, saved_states = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, {"sai": {"id": 123456, "username": "sai"}})
        with TestClient(app) as client:
            client.post("/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"})
            assert state["bangumi_self_info"]["verified"] is True

            # Same uid, DIFFERENT username, and bgm.tv is unreachable.
            self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "someone-else"}
            )
            assert resp.json() == {
                "ok": True,
                "uid": "123456",
                "username": "someone-else",
                "verified": False,
            }
            assert state["bangumi_self_info"]["verified"] is False

            # A different uid under a previously verified username, likewise.
            resp2 = client.post(
                "/api/sources/bangumi/identity", json={"uid": 999, "username": "sai"}
            )
            assert resp2.json()["verified"] is False

        assert state["bangumi_self_info"] == {"uid": "999", "username": "sai", "verified": False}
        assert len(saved_states) == 3  # each identity change is a real write

    def test_bangumi_identity_sticky_flag_reads_the_live_state_under_the_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: an unlocked snapshot must never decide the answer.

        There used to be a write-avoidance fast path that read state outside
        the lock and, when it looked unchanged, returned that snapshot's flag
        without entering the atomic section. A concurrent request confirming
        the identity in between made the snapshot stale, so the response said
        ``false`` while the disk said ``true``.

        Here the unlocked view deliberately disagrees with the locked truth:
        ``load_discovery_runtime_state`` reports the identity as unverified —
        exactly what the old fast path would have echoed — while the state the
        mutator sees under the lock already carries the confirmation. Only an
        answer derived inside the atomic section can be right.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, saved_states = self._identity_state_app(tmp_path)
        manager = app.state.runtime_context.runtime_controller.memory_manager

        confirmed = {"uid": "123456", "username": "sai", "verified": True}
        state["bangumi_self_info"] = dict(confirmed)
        # Stale unlocked view: same identity, but still flagged unverified. The
        # old fast path compared against this and returned False from it.
        manager.load_discovery_runtime_state = lambda: {
            "bangumi_self_info": {"uid": "123456", "username": "sai", "verified": False}
        }

        self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["verified"] is True, "answered from the stale unlocked snapshot"

        # And what the response claimed is what a later read actually returns.
        assert state["bangumi_self_info"] == confirmed
        assert saved_states[-1]["bangumi_self_info"] == confirmed

    def test_bangumi_identity_does_not_inherit_a_superseded_verified_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sticky-true must not resurrect a record the old rules produced.

        The superseded 404 path wrote ``{"username": "", "verified": true}``.
        Re-reporting that same 404 user matches on uid and on username (both
        ``""``), so plain sticky inheritance carried the stale ``true``
        forward forever — contradicting the invariant that a confirmed record
        names someone. Inheritance now requires the previous record to be one
        the current rules could have produced.
        """
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        # Exactly what the previous release persisted for a 404 lookup.
        state["bangumi_self_info"] = {"uid": "123456", "username": "", "verified": True}
        self._install_fake_bangumi_user_api(monkeypatch, {})

        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123456, "username": "does-not-exist"},
            )
            assert resp.json()["verified"] is False, "inherited a superseded verified record"
        assert state["bangumi_self_info"] == {
            "uid": "123456",
            "username": "",
            "verified": False,
        }

    def test_bangumi_identity_read_normalises_a_superseded_verified_record(
        self, tmp_path: Path
    ) -> None:
        """The same bad record also reads back as unverified.

        Fixing only the write path would leave an installation that never
        revisits bgm.tv reporting the stale claim indefinitely, so the read
        boundary normalises it too.
        """
        app, state, _ = self._identity_state_app(tmp_path)
        state["bangumi_self_info"] = {"uid": "123456", "username": "", "verified": True}

        assert app.state.load_bangumi_identity() == ("", False)

        # A legal confirmed record still reads back as confirmed.
        state["bangumi_self_info"] = {"uid": "123456", "username": "sai", "verified": True}
        assert app.state.load_bangumi_identity() == ("sai", True)

    def test_bangumi_identity_404_is_not_a_confirmation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: bgm.tv answering is not bgm.tv confirming.

        ``verified: true`` means "bgm.tv positively confirmed this
        uid↔username pair". A 404 refutes the reported username; it says
        nothing about who the uid belongs to. Recording that as verified let
        sticky-true pin an identity we had never established — a later
        uid-only report during an outage would keep it ``true`` forever.
        """
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, {})
        with TestClient(app) as client:
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 999999, "username": "does-not-exist"},
            )
            assert resp.json() == {
                "ok": True,
                "uid": "999999",
                "username": "",
                "verified": False,
            }
        assert state["bangumi_self_info"]["verified"] is False

        # ...and the never-confirmed record cannot be locked in by a later
        # outage: sticky-true has nothing to preserve.
        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
        with TestClient(app) as client:
            resp2 = client.post("/api/sources/bangumi/identity", json={"uid": 999999})
            assert resp2.json()["verified"] is False
        assert state["bangumi_self_info"]["verified"] is False

    def test_bangumi_identity_verified_implies_a_non_empty_username(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A confirmed *pair* needs both halves.

        When bgm.tv matches the uid but hands back an unusable username there
        is no pair to have confirmed, so the flag stays False rather than
        asserting a confirmation of nothing.
        """
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(monkeypatch, {"474349": {"id": 474349, "username": ""}})
        with TestClient(app) as client:
            resp = client.post("/api/sources/bangumi/identity", json={"uid": 474349})
            assert resp.json() == {
                "ok": True,
                "uid": "474349",
                "username": "",
                "verified": False,
            }
        assert state["bangumi_self_info"] == {
            "uid": "474349",
            "username": "",
            "verified": False,
        }

    def test_bangumi_identity_reports_a_failed_persist_instead_of_a_phantom_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 200 must never describe a state that was not stored.

        The response used to be built from this round's raw verification
        result, so a persistence failure still returned ``{"ok": true, …}``
        with a flag nothing had written — the next read contradicted it.
        Rule 7: propagate the real failure instead of appearing to work.
        """
        from fastapi.testclient import TestClient

        app, state, _ = self._identity_state_app(tmp_path)
        manager = app.state.runtime_context.runtime_controller.memory_manager
        self._install_fake_bangumi_user_api(monkeypatch, {"sai": {"id": 123456, "username": "sai"}})

        def _explode(_mutator):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        manager.update_discovery_runtime_state = _explode
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            # Verification succeeded, but the write did not: no phantom 200.
            assert resp.status_code == 500, resp.text
            assert "verified" not in resp.text
        assert "bangumi_self_info" not in state

    def test_bangumi_identity_failed_persist_does_not_downgrade_the_stored_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mirror case: disk says true, this round fails to write, response
        must not answer ``false`` off its own round result."""
        from fastapi.testclient import TestClient

        from openbiliclaw.sources.bangumi_client import BangumiAPIError

        app, state, _ = self._identity_state_app(tmp_path)
        manager = app.state.runtime_context.runtime_controller.memory_manager
        confirmed = {"uid": "123456", "username": "sai", "verified": True}
        state["bangumi_self_info"] = dict(confirmed)

        def _explode(_mutator):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        manager.update_discovery_runtime_state = _explode
        self._install_fake_bangumi_user_api(monkeypatch, BangumiAPIError("timeout", "down"))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
            assert resp.status_code == 500, resp.text
        # Disk untouched, and the response never claimed otherwise.
        assert state["bangumi_self_info"] == confirmed

    def test_bangumi_identity_without_a_memory_manager_reports_not_stored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No persistence backend → the report is dropped, so say so.

        This path used to answer ``{"ok": true, "verified": true}`` while
        storing nothing at all; every later read returned ``("", False)``.
        """
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        app.state.runtime_context.runtime_controller = SimpleNamespace(memory_manager=None)
        self._install_fake_bangumi_user_api(monkeypatch, {"sai": {"id": 123456, "username": "sai"}})
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/sources/bangumi/identity", json={"uid": 123456, "username": "sai"}
            )
        assert resp.status_code == 500, resp.text
        assert '"ok":true' not in resp.text.replace(" ", "")

    def test_bangumi_identity_persisted_during_active_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guided init is exactly when the three-tier account ladder needs the
        extension's freshly-reported identity, so the init write-guard must let
        POST /api/sources/bangumi/identity through (never 409 init_running)."""
        from fastapi.testclient import TestClient

        app, state, saved_states = self._identity_state_app(tmp_path)
        self._install_fake_bangumi_user_api(
            monkeypatch,
            {"sai": {"id": 123456, "username": "sai", "nickname": "Sai"}},
        )
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post(
                "/api/sources/bangumi/identity",
                json={"uid": 123456, "username": "sai"},
            )
        # Not gated by the init write-guard, and the identity still persists.
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "ok": True,
            "uid": "123456",
            "username": "sai",
            "verified": True,
        }
        verified_sai = {"uid": "123456", "username": "sai", "verified": True}
        assert saved_states[0]["bangumi_self_info"] == verified_sai
        assert state["bangumi_self_info"] == verified_sai

    def test_init_falls_back_to_extension_reported_bangumi_username(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        app.state.load_bangumi_identity = lambda: ("ext-user", True)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bangumi"]})
            assert resp.status_code == 202, resp.text
            warnings = resp.json().get("warnings", [])
            assert any("ext-user" in w for w in warnings)
            # A cross-checked identity keeps the plain, confident wording.
            assert not any("未经" in w for w in warnings)
            self._drive_until(client, captured, key="include_bangumi")

        assert captured["include_bangumi"] is True
        assert captured["bangumi_username"] == "ext-user"
        assert captured["bangumi_token"] == ""

    def test_init_flags_unverified_extension_bangumi_identity_in_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fail-open identity is still usable, but the warning says so.

        The old copy claimed "Bangumi 使用浏览器扩展识别到的账号 X。" for both
        verified and never-checked reports, so a DOM drift (or a stranger's
        username scraped off a timeline) read as confirmed fact.
        """
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        app.state.load_bangumi_identity = lambda: ("ext-user", False)
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bangumi"]})
            assert resp.status_code == 202, resp.text
            warnings = resp.json().get("warnings", [])
            assert any("ext-user" in w and "未经 bgm.tv 校验" in w for w in warnings)
            self._drive_until(client, captured, key="include_bangumi")

        # Still runs with the identity — fail-open behaviour is unchanged.
        assert captured["bangumi_username"] == "ext-user"

    def test_init_bangumi_username_priority_ladder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """token /v0/me > explicit username > extension-reported username."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        app.state.load_bangumi_identity = lambda: ("ext-user", True)

        async def _fake_resolve(token, **kwargs):
            return "token-owner"

        monkeypatch.setattr(
            "openbiliclaw.sources.bangumi_client.resolve_access_token_identity",
            _fake_resolve,
        )
        captured = self._capture_run_guided_init(monkeypatch)
        with TestClient(app) as client:
            # 1) token + explicit username + extension → token identity wins.
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["bangumi"],
                    "source_options": {
                        "bangumi": {"username": "typed-name", "access_token": "tok"}
                    },
                },
            )
            assert resp.status_code == 202, resp.text
            self._drive_until(client, captured, key="include_bangumi")
            assert captured["bangumi_username"] == "token-owner"

        # 2) explicit username + extension (no token) → explicit wins.
        app2, _ = self._make_app(tmp_path / "second", prereqs=prereqs)
        app2.state.load_bangumi_identity = lambda: ("ext-user", True)
        captured2 = self._capture_run_guided_init(monkeypatch)
        with TestClient(app2) as client:
            resp = client.post(
                "/api/init",
                json={
                    "sources": ["bangumi"],
                    "source_options": {"bangumi": {"username": "typed-name"}},
                },
            )
            assert resp.status_code == 202, resp.text
            self._drive_until(client, captured2, key="include_bangumi")
            assert captured2["bangumi_username"] == "typed-name"

    def test_init_rejects_unknown_source_options(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["reddit"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={"sources": ["reddit"], "source_options": {"weibo": {}}},
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_source_options"
        assert db.get_latest_init_run() is None

    def _capture_run_guided_init(self, monkeypatch):
        """Replace the shared pipeline with an async capture of its kwargs.

        The wrapper imports ``run_guided_init`` lazily from ``openbiliclaw.cli``,
        so patching it there intercepts the API path without running real work.
        """
        captured: dict[str, object] = {}

        async def _fake(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(discovery_error=False)

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", _fake)
        return captured

    def _drive_until(self, client, captured, key="include_xhs"):
        # Pump the portal loop so the background wrapper task reaches the
        # (mocked) run_guided_init call, then return its captured kwargs.
        import time

        for _ in range(100):
            if key in captured:
                break
            client.get("/api/init-status")
            time.sleep(0.02)
        return captured

    def _drive_until_status(self, client, reason):
        # Pump init-status until the background wrapper lands the terminal
        # failure write; returns the final status body.
        import time

        body = {}
        for _ in range(100):
            body = client.get("/api/init-status").json()
            if body.get("reason") == reason:
                break
            time.sleep(0.02)
        return body

    def test_init_crash_surfaces_exception_detail_in_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpected pipeline crash must be diagnosable from init-status:
        reason stays the stable ``internal_error`` code, ``detail`` carries the
        exception summary (field report 2026-07-05 — the generic 「初始化过程中
        出错了」 left community users with nothing to report)."""
        from fastapi.testclient import TestClient

        async def _boom(**kwargs):
            raise RuntimeError("provider exploded mid-run\nstack noise")

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", _boom)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bilibili"]})
            assert resp.status_code == 202
            body = self._drive_until_status(client, "internal_error")
        assert body["reason"] == "internal_error"
        assert body["detail"] == "RuntimeError: provider exploded mid-run"
        assert db.get_latest_init_run()["error_detail"] == (
            "RuntimeError: provider exploded mid-run"
        )

    def test_init_guided_error_message_lands_in_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Typed GuidedInitError failures surface their human message (the API
        path used to discard it — only the CLI printed it)."""
        from fastapi.testclient import TestClient

        from openbiliclaw.cli import GuidedInitError

        async def _typed_failure(**kwargs):
            raise GuidedInitError("empty_signals", "所选数据来源没有拉到任何行为信号。")

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", _typed_failure)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bilibili"]})
            assert resp.status_code == 202
            body = self._drive_until_status(client, "empty_signals")
        assert body["reason"] == "empty_signals"
        assert body["detail"] == "所选数据来源没有拉到任何行为信号。"

    def test_init_honors_source_selection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(
            bili="ok",
            chat=True,
            platforms=["bilibili", "xiaohongshu", "douyin", "youtube", "zhihu", "reddit"],
        )
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post(
                "/api/init",
                json={"sources": ["bilibili", "xiaohongshu", "zhihu", "reddit"]},
            )
            assert resp.status_code == 202
            self._drive_until(client, captured)
        # Only the selected sources are included, even though all 4 are
        # enabled in config.
        assert captured["include_bili"] is True
        assert captured["include_xhs"] is True
        assert captured["include_dy"] is False
        assert captured["include_yt"] is False
        assert captured["include_zhihu"] is True
        assert captured["include_reddit"] is True

    def test_init_without_sources_uses_all_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(
            bili="ok",
            chat=True,
            platforms=["bilibili", "xiaohongshu", "douyin", "zhihu", "reddit"],
        )
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            # No "sources" key → legacy behaviour: everything enabled.
            resp = client.post("/api/init", json={})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["include_bili"] is True
        assert captured["include_xhs"] is True
        assert captured["include_dy"] is True
        assert captured["include_yt"] is False  # youtube not enabled in config
        assert captured["include_zhihu"] is True
        assert captured["include_reddit"] is True

    def test_init_keeps_selected_source_not_enabled_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        # douyin is selected but NOT enabled in config → the explicit checkbox
        # choice is treated as opt-in for this guided-init run.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili", "xiaohongshu"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bilibili", "douyin"]})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["include_dy"] is True
        assert captured["include_xhs"] is False
        assert captured["include_reddit"] is False

    def test_init_reserves_before_source_opt_in_runtime_reload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=[])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        ctx = app.state.runtime_context
        ctx.config = Config()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        ctx.config.data_dir = str(data_dir)
        ctx.config.sources.douyin.enabled = False
        reservation_seen_during_rebuild: list[bool] = []

        async def observe_rebuild(_config: object) -> None:
            reservation_seen_during_rebuild.append(ctx.init_coordinator.init_active())

        async def no_op_restart(_app: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(ctx, "rebuild_from_config", observe_rebuild)
        monkeypatch.setattr(ctx, "restart_background_tasks", no_op_restart)
        monkeypatch.setattr("openbiliclaw.config.save_config", lambda _config: None)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["douyin"]})
            assert response.status_code == 202, response.text
            self._drive_until(client, captured, key="include_dy")

        assert reservation_seen_during_rebuild == [True]
        assert captured["include_dy"] is True

    def test_cancel_without_active_run_returns_409(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/init/cancel", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_running"

    def test_cancel_rejects_non_local(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            resp = client.post("/api/init/cancel", json={})
        assert resp.status_code == 403
        assert resp.json()["error"] == "local_only"

    def test_legacy_init_completed_does_not_mark_guided_init_done(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            before = client.get("/api/init-status").json()
            resp = client.post("/api/init-completed", json={})
            after = client.get("/api/init-status").json()
        assert resp.status_code == 200
        assert before["initialized"] is False
        assert after["initialized"] is False

    def test_runtime_stream_emits_real_init_coordinator_events(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            # Reserve only after application startup. Startup intentionally
            # reconciles pre-existing active rows as interrupted; reserving
            # before TestClient enters would create a terminal run that late
            # stage writes must not resurrect.
            coord = app.state.runtime_context.init_coordinator
            assert coord.try_start("ws-run")
            assert client.portal is not None
            with client.websocket_connect("/api/runtime-stream") as websocket:
                # Publish on TestClient's application loop.  ``asyncio.run``
                # here creates a foreign loop, so the subscriber queue is
                # filled without waking the WebSocket waiter and CI hangs.
                client.portal.call(coord.stage_started, "ws-run", 1)
                progress = websocket.receive_json()
                client.portal.call(coord.complete, "ws-run")
                completed = websocket.receive_json()

        assert progress["type"] == "init_progress"
        assert progress["run_id"] == "ws-run"
        assert progress["stage"] == 1
        assert completed["type"] == "init_completed"
        assert completed["run_id"] == "ws-run"

    def test_api_init_endpoint_emits_runtime_stream_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        class ReadyPrereqs:
            async def bilibili_check(self) -> str:
                return "ok"

            async def chat_ready(self) -> bool:
                return True

            def enabled_platforms(self) -> list[str]:
                return ["bilibili"]

        async def fake_run_guided_init(**kwargs: object) -> object:
            coord = kwargs["coordinator"]
            run_id = str(kwargs["run_id"])
            await coord.stage_started(run_id, 1)
            await coord.stage_done(run_id, 1)
            return SimpleNamespace(discovery_error=False)

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", fake_run_guided_init)
        app, _ = self._make_app(tmp_path, prereqs=ReadyPrereqs())

        with (
            TestClient(app) as client,
            client.websocket_connect("/api/runtime-stream") as websocket,
        ):
            response = client.post("/api/init", json={"sources": ["bilibili"]})
            progress = websocket.receive_json()
            stage_done = websocket.receive_json()
            completed = websocket.receive_json()

        assert response.status_code == 202
        assert progress["type"] == "init_progress"
        assert progress["stage"] == 1
        assert stage_done["type"] == "init_progress"
        assert completed["type"] == "init_completed"

    def test_write_endpoint_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/events", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_config_service_probes_allowed_during_init(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        calls: list[str] = []

        class FakeRegistry:
            def is_chat_capable(self, name: str) -> bool:
                return name == "probe-instance"

            def provider_type(self, name: str) -> str:  # noqa: ARG002
                return "openai_compatible"

            def get(self, name: str) -> object:  # noqa: ARG002
                return SimpleNamespace(_model="probe-model")

            async def complete_provider(
                self,
                provider_name: str,
                *_args: object,
                **_kwargs: object,
            ) -> object:
                calls.append(f"llm:{provider_name}")
                return SimpleNamespace(
                    content="OK",
                    instance_id=provider_name,
                    provider="openai_compatible",
                    model="probe-model",
                )

        class FakeEmbeddingService:
            async def probe(self) -> bool:
                calls.append("embedding")
                return True

        app, _ = self._make_app(tmp_path)
        monkeypatch.setattr(
            "openbiliclaw.llm.registry.build_llm_registry",
            lambda _cfg: FakeRegistry(),
        )
        monkeypatch.setattr(
            "openbiliclaw.llm.registry.build_embedding_service",
            lambda _cfg, _registry: FakeEmbeddingService(),
        )

        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            llm = client.post(
                "/api/config/probe-service",
                json={
                    "kind": "llm_instance",
                    "instance_id": "probe-instance",
                    "config": {},
                },
            )
            embedding = client.post(
                "/api/config/probe-service",
                json={
                    "kind": "embedding",
                    "config": {
                        "llm": {
                            "embedding": {
                                "provider": "ollama",
                                "model": "bge-m3",
                            }
                        }
                    },
                },
            )

        assert llm.status_code == 200
        assert llm.json()["ok"] is True
        assert embedding.status_code == 200
        assert embedding.json()["ok"] is True
        assert calls == ["llm:probe-instance", "embedding"]

    def test_config_llm_probe_timeout_returns_structured_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        class SlowRegistry:
            def is_chat_capable(self, name: str) -> bool:
                return name == "slow-instance"

            def provider_type(self, name: str) -> str:  # noqa: ARG002
                return "ollama"

            def get(self, name: str) -> object:  # noqa: ARG002
                return SimpleNamespace(_model="cold-model")

            async def complete_provider(
                self,
                provider_name: str,
                *_args: object,
                **_kwargs: object,
            ) -> object:
                await asyncio.sleep(1)
                return SimpleNamespace(
                    content="late",
                    instance_id=provider_name,
                    provider="ollama",
                    model="cold-model",
                )

        app, _ = self._make_app(tmp_path)
        monkeypatch.setattr(
            "openbiliclaw.llm.registry.build_llm_registry",
            lambda _cfg: SlowRegistry(),
        )
        monkeypatch.setattr(
            "openbiliclaw.api.app._config_llm_probe_timeout_seconds",
            lambda _configured: 0.01,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/config/probe-service",
                json={
                    "kind": "llm_instance",
                    "instance_id": "slow-instance",
                    "config": {},
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["kind"] == "llm_instance"
        assert body["instance_id"] == "slow-instance"
        assert body["provider"] == "ollama"
        assert body["model"] == "cold-model"
        assert body["error"] == "LLM connectivity probe timed out after 0.01s."
        assert 0 <= body["latency_ms"] < 1000

    def test_write_endpoint_allowed_when_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/events", json={})
        # No active init → the init gate must NOT fire (a bad payload is 422,
        # never the 409 init_running short-circuit).
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_recommendation_click_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/recommendation-click", json={})
        # recommendation-click propagates events to the profile → gated.
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_soul_and_pool_writers_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Chat (dialogue learning), probe responses, and init-completed all
        # mutate soul/pool state and must be blocked while a run is active.
        gated_paths = [
            "/api/chat",
            "/api/chat/turns",
            "/api/delight/respond",
            "/api/interest-probes/respond",
            "/api/avoidance-probes/respond",
            "/api/init-completed",
        ]
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            for path in gated_paths:
                resp = client.post(path, json={})
                assert resp.status_code == 409, f"{path} not gated ({resp.status_code})"
                assert resp.json().get("error") == "init_running", path

    def test_cookie_same_value_is_noop_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import load_config

        app, _ = self._make_app(tmp_path)
        # Make the submitted cookie match the effective stored cookie so the
        # init-active branch treats it as a silent no-op (not a rebuild).
        AuthManager(data_dir=load_config().data_path).set_cookie("SESSDATA=same")
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post(
                "/api/bilibili/cookie", json={"cookie": "SESSDATA=same", "source": "test"}
            )
        # Same effective cookie during init → 200 no-op, no rebuild, no error.
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_cookie_changed_during_init_is_rejected(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post(
                "/api/bilibili/cookie", json={"cookie": "SESSDATA=different", "source": "test"}
            )
        # A genuinely different cookie during init is rejected (not silently
        # dropped, not a mid-init rebuild) so the user knows it didn't apply.
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_deny_by_default_gates_arbitrary_writer_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Deny-by-default: a mutating endpoint that isn't on the init allowlist
        # is 409'd during init even though it's not individually enumerated.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/watch-later", json={"bvid": "BV1xx"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_dispatcher_kick_allowed_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Init's own enqueue kicks /api/sources/<src>/kick — it must pass the
        # gate (the bootstrap protocol), so it's never the init 409.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/sources/xhs/kick", json={})
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_source_recipe_crud_not_bypassing_gate_via_kick_id(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # The bootstrap allowlist is exact-segment: a recipe whose id happens to
        # be "kick"/"task-result" (PUT /api/sources/kick) must NOT slip past the
        # init gate — only /api/sources/<source>/kick (4 segments) is allowed.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.put("/api/sources/kick", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_init_status_can_start_false_when_already_initialized(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # E1 must mirror E2's already-initialized guard so they don't disagree.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["initialized"] is True
        assert body["can_start"] is False
        assert body["reason"] == "already_initialized"

    def test_init_status_surfaces_discovery_timeout_as_partial_completion(
        self, tmp_path: Path
    ) -> None:
        """An initialized profile must not hide why the first pool is empty."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        coord = app.state.runtime_context.init_coordinator
        assert coord.try_start("run-discovery-timeout") is True
        detail = "画像已生成，但首轮内容池等待内容发现超过 10 分钟仍未完成；系统会在后台继续补池。"
        asyncio.run(
            coord.complete(
                "run-discovery-timeout",
                partial_success=True,
                reason="discovery_timeout",
                detail=detail,
            )
        )

        with TestClient(app) as client:
            body = client.get("/api/init-status").json()

        assert body["initialized"] is True
        assert body["partial_success"] is True
        assert body["reason"] == "discovery_timeout"
        assert body["detail"] == detail

    def test_init_status_skips_live_probes_when_already_initialized(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Steady-state polling of an initialized instance must not fire real
        # (billable) chat probes or Bilibili round-trips — users spotted the
        # recurring 5-in/10-out "hi" completions on their provider bill.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            for _ in range(3):
                resp = client.get("/api/init-status")
        body = resp.json()
        assert body["initialized"] is True
        assert prereqs.chat_ready_calls == 0
        assert prereqs.bilibili_check_calls == 0
        # Cached values still surface in the payload.
        assert body["prerequisites"]["llm_ready"] is True
        assert body["prerequisites"]["bilibili_logged_in"] is True

    def test_init_status_skips_embedding_probe_when_already_initialized(
        self, tmp_path: Path
    ) -> None:
        """Completed-page polling must not queue behind background prewarm."""
        from fastapi.testclient import TestClient

        class _SlowEmbeddingService:
            def __init__(self) -> None:
                self.calls = 0

            async def probe(self) -> bool:
                self.calls += 1
                await asyncio.sleep(10)
                return True

        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(
            tmp_path,
            profile_ready=True,
            prereqs=prereqs,
            embedding_provider="ollama",
        )
        service = _SlowEmbeddingService()
        app.state.runtime_context.soul_engine._embedding_service = service

        with TestClient(app) as client:
            response = client.get("/api/init-status")

        assert response.status_code == 200
        assert response.json()["initialized"] is True
        assert service.calls == 0

    def test_init_status_skips_live_probes_while_init_is_running(self, tmp_path: Path) -> None:
        """Polling progress must not compete with the run's provider budget.

        This also covers the strict-init overlap where the profile exists
        (initialized=true) while stage 4 is still running.
        """
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            coord = app.state.runtime_context.init_coordinator
            assert coord.try_start("run-live") is True
            asyncio.run(coord.mark_running("run-live"))
            asyncio.run(coord.stage_started("run-live", 3))
            asyncio.run(coord.stage_done("run-live", 3))
            asyncio.run(coord.stage_started("run-live", 4))
            body = client.get("/api/init-status").json()

        assert body["initialized"] is True
        assert body["running"] is True
        assert body["reason"] == "already_running"
        assert prereqs.chat_ready_calls == 0
        assert prereqs.bilibili_check_calls == 0

    def test_init_status_probes_live_before_initialization(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Pre-init the checklist gates the start button, so probes stay real.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            client.get("/api/init-status")
        assert prereqs.chat_ready_calls == 1
        assert prereqs.bilibili_check_calls == 1

    def test_init_status_bilibili_login_is_informational_not_blocking(self, tmp_path: Path) -> None:
        """v0.3.118+: B站 login no longer hard-gates can_start — whether it
        blocks depends on the client's source selection, which only POST sees.
        The status still reports the login state + reason for client gating."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="failed", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["can_start"] is True
        assert body["reason"] == "bilibili_not_logged_in"
        assert body["prerequisites"]["bilibili_logged_in"] is False
        # The probe's failure reason rides along so the UI can distinguish
        # an expired cookie from a proxy-broken probe (field report 2026-07).
        assert body["prerequisites"]["bilibili_detail"] == "检测请求失败（stub 网络错误）。"

    def test_init_status_llm_still_hard_gates_can_start(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=False, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["can_start"] is False
        assert body["reason"] == "llm_not_ready"

    def test_init_status_preserves_terminal_failure_when_prereq_later_fails(
        self, tmp_path: Path
    ) -> None:
        """A follow-up probe must not hide the persisted cause of a failed run."""
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=False, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        detail = "偏好分析等待 AI 服务超过 6 分钟仍未返回结果，已自动停止。"
        with TestClient(app) as client:
            coord = app.state.runtime_context.init_coordinator
            assert coord.try_start("run-timeout") is True
            asyncio.run(coord.mark_running("run-timeout"))
            asyncio.run(coord.stage_started("run-timeout", 2))
            asyncio.run(coord.fail("run-timeout", "analyze_failed", detail=detail))
            body = client.get("/api/init-status").json()

        assert body["can_start"] is False
        assert body["reason"] == "analyze_failed"
        assert body["detail"] == detail
        assert prereqs.chat_ready_calls == 0
        assert prereqs.bilibili_check_calls == 0

    @pytest.mark.parametrize(
        ("chat_ready", "expected_reason", "expected_can_start"),
        [(True, "analyze_failed", True), (False, "llm_not_ready", False)],
    )
    def test_init_status_surfaces_background_profile_analysis_error(
        self,
        tmp_path: Path,
        chat_ready: bool,
        expected_reason: str,
        expected_can_start: bool,
    ) -> None:
        """The account-sync error recorded for issue #113 must reach the UI."""
        from fastapi.testclient import TestClient

        detail = "画像分析失败：AI 偏好分析超时，已自动停止本次任务。"
        prereqs = _FakeInitPrereqs(bili="ok", chat=chat_ready, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        app.state.runtime_context.account_sync_service = SimpleNamespace(
            get_runtime_status=lambda: {"last_account_sync_error": detail}
        )

        with TestClient(app) as client:
            body = client.get("/api/init-status").json()

        assert body["reason"] == expected_reason
        assert body["can_start"] is expected_can_start
        assert body["detail"] == detail

    def test_init_status_configured_embedding_hard_gates_can_start(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["can_start"] is False
        assert body["reason"] == "embedding_not_ready"
        assert body["prerequisites"]["embedding_ready"] is False
        assert body["prerequisites"]["embedding_required"] is True

    def test_init_status_keeps_cached_loopback_ollama_timeout_strict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as appmod
        from openbiliclaw.llm import ollama_diagnostics as od

        monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.01)

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_ERROR, "cold loading"

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)

        class _SlowProbeService:
            def __init__(self) -> None:
                self.calls = 0

            async def probe(self) -> bool:
                self.calls += 1
                await asyncio.sleep(0.2)
                return True

        service = _SlowProbeService()
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
        app, _ = self._make_app(
            tmp_path,
            prereqs=prereqs,
            embedding_provider="ollama",
        )
        app.state.runtime_context.soul_engine._embedding_service = service
        app.state.runtime_context.config.llm.embedding.base_url = "http://127.0.0.1:11434/v1"

        with TestClient(app) as client:
            health = client.get("/api/health").json()
            status = client.get("/api/init-status").json()

        assert health["embedding_ready"] is True
        assert status["prerequisites"]["embedding_ready"] is False
        assert status["can_start"] is False
        assert status["reason"] == "embedding_not_ready"
        assert service.calls == 1

    def test_init_status_disabled_embedding_does_not_gate_can_start(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["xiaohongshu"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs, embedding_provider="")
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["can_start"] is True
        assert body["reason"] == "none"
        assert body["prerequisites"]["embedding_ready"] is False
        assert body["prerequisites"]["embedding_required"] is False

    def test_task_result_not_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/sources/xhs/task-result", json={})
        # Init's own bootstrap collectors depend on task-results landing —
        # the writer gate must let them through (never 409 init_running).
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_recommendations_get_skips_serve_bootstrap_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        served: list[int] = []

        class _FakeRec:
            async def serve(self, profile: object, limit: int = 10) -> list[object]:
                served.append(limit)
                return []

        class _FakeSoul:
            def is_profile_ready(self) -> bool:
                return True

            async def get_profile(self) -> object:
                return object()

        db = Database(tmp_path / "rec.db")
        db.initialize()
        # Pretend the pool has scored candidates so the empty-history bootstrap
        # would normally call serve() (a side-effecting write).
        db.count_pool_candidates = lambda **_kw: 5  # type: ignore[method-assign]
        app = create_app(memory_manager=object(), database=db, soul_engine=_FakeSoul())
        app.state.auth_gate.is_trusted_local = lambda request: True
        app.state.runtime_context.recommendation_engine = _FakeRec()
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        # serve() (writes recommendation rows + marks pool shown) must NOT run on
        # a half-built pool during init — the side-effecting GET is gated too.
        assert served == []


class TestRecommendationsFirstPageTopUp:
    """GET /api/recommendations thin-history top-up (issue #81 挤牙膏首载).

    A first page of 2-3 unprocessed history rows reads as broken even though
    the pool has stock — the endpoint now serves from the pool whenever the
    window is thinner than 10 rows, not only when it is empty. The non-empty
    case is debounced (side-effecting write on a GET, polled by clients).
    """

    def _make_app(self, tmp_path: Path, *, history_rows: int):
        from openbiliclaw.storage.database import Database

        served: list[int] = []

        class _FakeRec:
            async def serve(self, profile: object, limit: int = 10) -> list[object]:
                served.append(limit)
                return []

        class _FakeSoul:
            def is_profile_ready(self) -> bool:
                return True

            async def get_profile(self) -> object:
                return object()

        db = Database(tmp_path / "rec.db")
        db.initialize()
        for index in range(history_rows):
            db.insert_recommendation(
                f"BVthin{index}", confidence=0.9, expression="看看这个", topic="测试"
            )
        # Pretend the pool has servable candidates so the top-up gate opens.
        db.count_pool_candidates = lambda **_kw: 5  # type: ignore[method-assign]
        app = create_app(memory_manager=object(), database=db, soul_engine=_FakeSoul())
        app.state.auth_gate.is_trusted_local = lambda request: True
        app.state.runtime_context.recommendation_engine = _FakeRec()
        return app, served

    def test_thin_history_tops_up_from_pool(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, served = self._make_app(tmp_path, history_rows=3)
        with TestClient(app) as client:
            resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        assert served == [10]

    def test_thin_history_topup_is_debounced(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, served = self._make_app(tmp_path, history_rows=3)
        with TestClient(app) as client:
            client.get("/api/recommendations")
            client.get("/api/recommendations")
        # Second GET lands inside the debounce window → no repeated serve().
        assert served == [10]

    def test_full_first_page_does_not_top_up(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, served = self._make_app(tmp_path, history_rows=10)
        with TestClient(app) as client:
            resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        assert served == []

    def test_empty_history_bootstrap_is_coalesced_inside_snapshot_window(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        # A restored browser session can reopen dozens of empty dashboards at
        # once. They share one short snapshot window instead of each calling
        # the side-effecting bootstrap serve path.
        app, served = self._make_app(tmp_path, history_rows=0)
        with TestClient(app) as client:
            client.get("/api/recommendations")
            client.get("/api/recommendations")
        assert served == [10]


class TestEmbeddingDiagnosisAndRepair:
    """Classified embedding causes + one-click repair (v0.3.155+).

    Field context (2026-07-05): bge-m3 500-ing for an hour surfaced only as
    a dead「重试」; a browser-translated provider name ('奥拉玛') silently
    disabled embedding; remote viewers saw "条件未满足" with an all-green
    checklist because the reason ladder had no trusted branch.
    """

    def _make_app(self, tmp_path, *, embedding_provider=None, prereqs=None):
        from openbiliclaw.config import Config
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "diag.db")
        db.initialize()
        soul = SimpleNamespace(is_profile_ready=lambda: False)
        app = create_app(memory_manager=object(), database=db, soul_engine=soul)
        app.state.auth_gate.is_trusted_local = lambda request: True
        if embedding_provider is not None:
            app.state.runtime_context.config = Config()
            data_dir = tmp_path / "data"
            data_dir.mkdir(exist_ok=True)
            app.state.runtime_context.config.data_dir = str(data_dir)
            app.state.runtime_context.config.llm.embedding.provider = embedding_provider
            if embedding_provider == "ollama":
                # Every real write path (CLI setup, popup banner, settings
                # page) sets the model together with the provider.
                app.state.runtime_context.config.llm.embedding.model = "bge-m3"
        if prereqs is not None:
            app.state.runtime_context._init_prereqs = prereqs
        return app, db

    def test_init_status_reason_local_only_when_untrusted(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # trusted participates in can_start but had no reason branch — remote
        # viewers got reason="none" and a generic "条件未满足" over an
        # all-green checklist. All clients already map local_only.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="", prereqs=prereqs)
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            body = client.get("/api/init-status").json()
        assert body["can_start"] is False
        assert body["reason"] == "local_only"
        assert body["prerequisites"]["llm_ready"] is True

    def test_init_status_classifies_misconfigured_embedding_provider(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # A browser-translated provider name must surface as misconfigured,
        # not silently degrade into a generic not-ready.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="奥拉玛", prereqs=prereqs)
        with TestClient(app) as client:
            body = client.get("/api/init-status").json()
        prereq = body["prerequisites"]
        assert prereq["embedding_ready"] is False
        assert prereq["embedding_check"] == "misconfigured"
        assert "奥拉玛" in prereq["embedding_detail"]

    def test_init_status_reports_disabled_embedding_quietly(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="", prereqs=prereqs)
        with TestClient(app) as client:
            body = client.get("/api/init-status").json()
        prereq = body["prerequisites"]
        assert prereq["embedding_check"] == "disabled"
        assert prereq["embedding_detail"] == ""

    def test_init_autostarts_pull_when_model_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_MODEL_MISSING, "bge-m3 not found"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            pulled.append(model)
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})
            deadline = time.monotonic() + 1.0
            while not pulled and time.monotonic() < deadline:
                time.sleep(0.01)

        assert response.status_code == 409
        assert response.json()["error"] == "embedding_not_ready"
        assert "正在下载" in response.json().get("detail", "")
        assert pulled == ["bge-m3"]

    def test_init_autostarts_pull_when_model_broken(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_MODEL_BROKEN, "bge-m3 load failed"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            pulled.append(model)
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})
            deadline = time.monotonic() + 1.0
            while not pulled and time.monotonic() < deadline:
                time.sleep(0.01)

        assert response.status_code == 409
        assert "正在下载" in response.json().get("detail", "")
        assert pulled == ["bge-m3"]

    def test_init_autostart_skips_not_running_when_management_disallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od
        from openbiliclaw.runtime import ollama_supervisor as sup

        pulled: list[str] = []
        started: list[str] = []

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_NOT_RUNNING, "Ollama is down"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            pulled.append(model)
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)
        # We are not allowed to manage this endpoint → no start, no pull.
        monkeypatch.setattr(sup, "may_manage_ollama_endpoint", lambda endpoint: False)
        monkeypatch.setattr(sup, "ensure_managed_ollama", lambda endpoint: started.append(endpoint))
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")
        assert pulled == []
        assert started == []

    def test_init_autostart_starts_managed_ollama_when_not_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DIAG_NOT_RUNNING is self-healed: the auto path starts managed Ollama
        (same helper the manual repair uses) then re-diagnoses so a missing
        model still gets auto-pulled (defect 4)."""
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od
        from openbiliclaw.runtime import ollama_supervisor as sup

        pulled: list[str] = []
        started: list[str] = []
        diagnoses = iter([od.DIAG_NOT_RUNNING, od.DIAG_MODEL_MISSING])

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return next(diagnoses, od.DIAG_MODEL_MISSING), "state"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            pulled.append(model)
            return True, ""

        def fake_ensure(endpoint: str) -> bool:
            started.append(endpoint)
            return True

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)
        monkeypatch.setattr(sup, "may_manage_ollama_endpoint", lambda endpoint: True)
        monkeypatch.setattr(sup, "ollama_required", lambda cfg: True)
        monkeypatch.setattr(sup, "ensure_managed_ollama", fake_ensure)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})
            deadline = time.monotonic() + 1.0
            while not pulled and time.monotonic() < deadline:
                time.sleep(0.01)

        assert response.status_code == 409
        assert "正在下载" in response.json().get("detail", "")
        assert started  # managed Ollama was started before pulling
        assert pulled == ["bge-m3"]

    def test_init_autostart_skips_non_ollama_provider(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="gemini", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")

    @pytest.mark.parametrize(
        "base_url",
        ["http://10.0.0.2:11434/v1", "http://ollama:11434/v1"],
    )
    def test_init_autostart_skips_non_loopback_endpoints(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        base_url: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        diagnosed: list[str] = []

        async def fake_diagnose(url: str, model: str) -> tuple[str, str]:
            diagnosed.append(url)
            return od.DIAG_MODEL_MISSING, "missing"

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
        app.state.runtime_context.config.llm.embedding.base_url = base_url

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")
        assert diagnosed == []

    def test_init_autostart_allows_private_loopback_port(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        diagnosed: list[str] = []

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            diagnosed.append(base_url)
            return od.DIAG_MODEL_MISSING, "missing"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
        app.state.runtime_context.config.llm.embedding.base_url = "http://127.0.0.1:11435/v1"

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert "正在下载" in response.json().get("detail", "")
        assert diagnosed == ["http://127.0.0.1:11435/v1"]

    def test_init_autostart_skips_pull_when_disk_space_is_low(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od
        from openbiliclaw.runtime import embedding_progress

        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_MODEL_MISSING, "missing"

        async def fake_pull(base_url: str, model: str, on_progress=None):
            pulled.append(model)
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(
            od,
            "ollama_embedding_disk_space_error",
            lambda model: (od.DIAG_DISK_FULL, "disk full"),
        )
        monkeypatch.setattr(od, "pull_ollama_model", fake_pull)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")
        assert pulled == []
        assert embedding_progress.snapshot()["running"] is False

    def test_init_autostart_reuses_existing_process_pull(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import embedding_progress

        embedding_progress.mark_pull_running("bge-m3")
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert "正在下载" in response.json().get("detail", "")
        assert embedding_progress.snapshot()["running"] is True

    def test_init_autostart_diagnosis_exception_keeps_manual_409(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            raise RuntimeError("diagnosis exploded")

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            response = client.post("/api/init", json={"sources": ["bilibili"]})

        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")

    def test_init_autostart_schedule_failure_rolls_back_without_changing_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od
        from openbiliclaw.runtime import embedding_progress

        class FailingRegistry:
            def track(self, name: str, coro: object) -> None:
                raise RuntimeError("scheduler unavailable")

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_MODEL_MISSING, "missing"

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
        embedding_progress.report_ollama_phase("starting")

        with TestClient(app) as client:
            context = app.state.runtime_context
            original_registry = context.task_registry
            context.task_registry = FailingRegistry()
            try:
                response = client.post("/api/init", json={"sources": ["bilibili"]})
                repair = client.get("/api/embedding/repair").json()
            finally:
                context.task_registry = original_registry

        pull = embedding_progress.snapshot()
        assert response.status_code == 409
        assert response.json()["detail"].startswith("向量模型还没就绪")
        assert repair["running"] is False
        assert "scheduler unavailable" in repair["error"]
        assert pull["running"] is False
        assert pull["done"] is True
        assert "scheduler unavailable" in str(pull["error"])
        assert embedding_progress.ollama_phase() == "starting"

    def test_init_autostart_and_manual_repair_share_single_flight(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm import ollama_diagnostics as od

        pulls: list[str] = []
        release = False

        async def fake_diagnose(base_url: str, model: str) -> tuple[str, str]:
            return od.DIAG_MODEL_MISSING, "missing"

        async def slow_pull(base_url: str, model: str, on_progress=None):
            nonlocal release
            pulls.append(model)
            while not release:
                await asyncio.sleep(0.01)
            return True, ""

        monkeypatch.setattr(od, "diagnose_ollama_embedding", fake_diagnose)
        monkeypatch.setattr(od, "pull_ollama_model", slow_pull)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)

        with TestClient(app) as client:
            auto = client.post("/api/init", json={"sources": ["bilibili"]})
            deadline = time.monotonic() + 1.0
            while not pulls and time.monotonic() < deadline:
                time.sleep(0.01)
            manual = client.post("/api/embedding/repair")
            release = True
            deadline = time.monotonic() + 1.0
            while not client.get("/api/embedding/repair").json().get("done"):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)

        assert auto.status_code == 409
        assert manual.status_code == 409
        assert manual.json()["error"] == "already_running"
        assert pulls == ["bge-m3"]

    def test_repair_rejects_non_local(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 403
        assert resp.json()["error"] == "local_only"

    def test_repair_rejects_non_ollama_provider(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path, embedding_provider="gemini")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "unsupported_provider"

    def test_repair_starts_managed_ollama_then_pulls_missing_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        diagnoses = iter(
            [
                ("not_running", "Ollama 服务无法连接"),
                ("model_missing", "缺 bge-m3"),
            ]
        )
        start_calls: list[str] = []
        pulled: list[str] = []
        diagnose_calls: list[tuple[str, str]] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            diagnose_calls.append((base_url, model))
            return next(diagnoses)

        def fake_start() -> bool:
            start_calls.append("start")
            return True

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled.append(model)
            if on_progress is not None:
                on_progress("success", 0, 0)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor._ollama_start_serve_background",
            fake_start,
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
            assert resp.status_code == 202
            body = resp.json()
            assert body["diagnosis"] == "model_missing"

            status: dict = {}
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                status = client.get("/api/embedding/repair").json()
                if status.get("done"):
                    break
                time.sleep(0.05)

        assert start_calls == ["start"]
        assert len(diagnose_calls) == 2
        assert pulled == ["bge-m3"]
        assert status.get("ok") is True

    def test_repair_409_when_managed_ollama_start_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        start_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("not_running", "Ollama 服务无法连接")

        def fake_start() -> bool:
            start_calls.append("start")
            return False

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor._ollama_start_serve_background",
            fake_start,
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "not_running"
        assert "无法连接" in body["detail"]
        assert start_calls == ["start"]

    @pytest.mark.parametrize(
        ("manage_ollama", "embedding_base_url"),
        [
            (False, ""),
            (True, "http://127.0.0.1:11500/v1"),
            (True, "http://10.0.0.2:11434/v1"),
        ],
    )
    def test_repair_not_running_does_not_start_unmanaged_or_custom_endpoint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        manage_ollama: bool,
        embedding_base_url: str,
    ) -> None:
        from fastapi.testclient import TestClient

        start_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("not_running", "Ollama 服务无法连接")

        def fake_start() -> bool:
            start_calls.append("start")
            return True

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor._ollama_start_serve_background",
            fake_start,
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.autostart.manage_ollama = manage_ollama
        cfg.llm.embedding.base_url = embedding_base_url
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_running"
        assert start_calls == []

    @pytest.mark.parametrize(
        ("diagnosis", "detail"),
        [
            ("disk_full", "磁盘空间不足，至少需要 2.0 GB。"),
            ("network", "无法访问模型下载源 registry，请检查网络。"),
            ("model_oom", "内存不足以加载模型，重新下载无效。"),
        ],
    )
    def test_repair_terminal_guidance_causes_do_not_start_pull(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        diagnosis: str,
        detail: str,
    ) -> None:
        from fastapi.testclient import TestClient

        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return (diagnosis, detail)

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled.append(model)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == diagnosis
        assert body["detail"] == detail
        assert pulled == []

    def test_repair_disk_precheck_short_circuits_missing_model_pull(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_missing", "缺 bge-m3")

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled.append(model)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.ollama_embedding_disk_space_error",
            lambda *_args, **_kw: ("disk_full", "磁盘空间不足，至少需要 2.0 GB。"),
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "disk_full"
        assert pulled == []

    def test_repair_provider_error_restarts_managed_ollama_once_then_409(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        diagnoses = iter(
            [
                ("error", "Ollama 响应异常（GET /api/tags -> 500）：server busy"),
                ("error", "Ollama 响应异常（GET /api/tags -> 500）：server busy"),
            ]
        )
        restart_calls: list[str] = []
        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return next(diagnoses)

        def fake_restart() -> tuple[bool, str]:
            restart_calls.append("restart")
            return (True, "")

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled.append(model)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.restart_managed_ollama", fake_restart
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "provider_error"
        assert "server busy" in body["detail"]
        assert "NO_PROXY" in body["detail"]
        assert restart_calls == ["restart"]
        assert pulled == []

    def test_repair_provider_error_never_restarts_external_ollama(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        restart_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("error", "Ollama 响应异常（GET /api/tags -> 500）：server busy")

        def fake_restart() -> tuple[bool, str]:
            restart_calls.append("restart")
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.restart_managed_ollama", fake_restart
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.autostart.manage_ollama = False
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "provider_error"
        assert restart_calls == []

    def test_repair_not_running_starts_recorded_private_daemon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(a) A recorded private daemon (11435) is repaired, not 409'd."""
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import ollama_supervisor as sup

        monkeypatch.setattr(
            sup,
            "_managed_daemon",
            sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/private-models"),
        )
        diagnoses = iter(
            [
                ("not_running", "Ollama 服务无法连接"),
                ("model_missing", "缺 bge-m3"),
            ]
        )
        private_starts: list[tuple[str, str]] = []
        pulled: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return next(diagnoses)

        def fake_start_at(models_dir: str, host: str) -> bool:
            private_starts.append((models_dir, host))
            return True

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled.append(model)
            if on_progress is not None:
                on_progress("success", 0, 0)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(sup, "start_managed_ollama_at", fake_start_at)
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 202
        assert private_starts == [("/tmp/private-models", "http://127.0.0.1:11435")]

    def test_repair_provider_error_restarts_recorded_private_daemon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(b) DIAG_ERROR on the private daemon goes through spec-aware restart."""
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import ollama_supervisor as sup

        monkeypatch.setattr(
            sup,
            "_managed_daemon",
            sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/private-models"),
        )
        restart_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("error", "Ollama 响应异常（GET /api/tags -> 500）：server busy")

        def fake_restart() -> tuple[bool, str]:
            restart_calls.append("restart")
            return (False, "start_failed")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(sup, "restart_managed_ollama", fake_restart)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "provider_error"
        assert restart_calls == ["restart"]

    def test_repair_not_running_still_409s_without_record_on_custom_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(c) No record + non-default endpoint keeps today's 409 (invariant 6)."""
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import ollama_supervisor as sup

        monkeypatch.setattr(sup, "_managed_daemon", None)
        start_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("not_running", "Ollama 服务无法连接")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(sup, "start_managed_ollama_at", lambda d, h: start_calls.append("s"))
        monkeypatch.setattr(sup, "_ollama_start_serve_background", lambda: start_calls.append("s"))
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_running"
        assert start_calls == []

    def test_repair_manage_ollama_false_409s_even_with_private_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(d) manage_ollama=False disables management even for a recorded daemon."""
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import ollama_supervisor as sup

        monkeypatch.setattr(
            sup,
            "_managed_daemon",
            sup._ManagedDaemon(None, "http://127.0.0.1:11435", "/tmp/private-models"),
        )
        start_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("not_running", "Ollama 服务无法连接")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(sup, "start_managed_ollama_at", lambda d, h: start_calls.append("s"))
        monkeypatch.setattr(sup, "_ollama_start_serve_background", lambda: start_calls.append("s"))
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        cfg = app.state.runtime_context.config
        cfg.autostart.manage_ollama = False
        cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_running"
        assert start_calls == []

    def test_repair_loop_is_bounded_when_remediation_does_not_change_diagnosis(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        start_calls: list[str] = []

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("not_running", "Ollama 服务无法连接")

        def fake_start() -> bool:
            start_calls.append("start")
            return True

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor._ollama_start_serve_background",
            fake_start,
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "not_running"
        assert "自动修复已达到上限" in body["detail"]
        assert len(start_calls) == 3

    def test_repair_pulls_model_and_reports_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_missing", "缺 bge-m3")

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            if on_progress is not None:
                on_progress("downloading", 100, 400)
                on_progress("success", 0, 0)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
            assert resp.status_code == 202
            started = resp.json()
            assert started["started"] is True
            assert started["model"] == "bge-m3"
            assert started["diagnosis"] == "model_missing"

            status: dict = {}
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                status = client.get("/api/embedding/repair").json()
                if status.get("done"):
                    break
                time.sleep(0.05)
        assert status.get("done") is True
        assert status.get("ok") is True
        assert status.get("error") == ""

    def test_repair_model_path_encoding_restarts_then_pulls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        models_dir = tmp_path / "relocated-models"
        restarted: dict[str, str] = {}
        pulled: dict[str, str] = {}

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_path_encoding", "模型路径含非 ASCII 字符；请设置 OLLAMA_MODELS")

        def fake_restart(path: str) -> tuple[bool, str]:
            restarted["path"] = path
            return (True, "")

        async def fake_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            pulled["model"] = model
            if on_progress is not None:
                on_progress("success", 0, 0)
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", fake_pull)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_models_relocation_candidate",
            lambda: str(models_dir),
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.restart_managed_ollama_with_models_dir",
            fake_restart,
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
            assert resp.status_code == 202
            body = resp.json()
            assert body["diagnosis"] == "model_path_encoding"

            status: dict = {}
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                status = client.get("/api/embedding/repair").json()
                if status.get("done"):
                    break
                time.sleep(0.05)

        assert restarted["path"] == str(models_dir)
        assert pulled["model"] == "bge-m3"
        assert status.get("ok") is True

    def test_repair_model_path_encoding_rejects_external_ollama(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_path_encoding", "模型路径含非 ASCII 字符")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_models_relocation_candidate",
            lambda: str(tmp_path / "relocated-models"),
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.restart_managed_ollama_with_models_dir",
            lambda _path: (False, "external_ollama"),
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "external_ollama"
        assert "外部启动的 Ollama" in body["detail"]

    def test_repair_model_path_encoding_requires_manual_fix_when_no_safe_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_path_encoding", "模型路径含非 ASCII 字符")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_models_relocation_candidate",
            lambda: None,
        )
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            resp = client.post("/api/embedding/repair")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "manual_fix_required"
        assert "OLLAMA_MODELS" in body["detail"]
        assert "重启 Ollama" in body["detail"]

    def test_repair_single_flight_while_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_missing", "缺 bge-m3")

        release = asyncio.Event()

        async def slow_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            await release.wait()
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", slow_pull)
        app, _ = self._make_app(tmp_path, embedding_provider="ollama")
        with TestClient(app) as client:
            first = client.post("/api/embedding/repair")
            assert first.status_code == 202
            second = client.post("/api/embedding/repair")
            assert second.status_code == 409
            assert second.json()["error"] == "already_running"
            release.set()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if client.get("/api/embedding/repair").json().get("done"):
                    break
                time.sleep(0.05)

    def test_init_status_reports_live_pull_progress_while_repairing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        # While a one-click repair is downloading, init pages must see a
        # "repairing" classification with live percent — that's how users
        # know how long to wait (user request 2026-07-05).
        async def fake_diagnose(base_url: str, model: str, **_kw: object) -> tuple[str, str]:
            return ("model_missing", "缺 bge-m3")

        release = asyncio.Event()

        async def slow_pull(base_url: str, model: str, *, on_progress=None, **_kw: object):
            if on_progress is not None:
                on_progress("downloading", 200_000_000, 400_000_000)
            await release.wait()
            return (True, "")

        monkeypatch.setattr(
            "openbiliclaw.llm.ollama_diagnostics.diagnose_ollama_embedding", fake_diagnose
        )
        monkeypatch.setattr("openbiliclaw.llm.ollama_diagnostics.pull_ollama_model", slow_pull)
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
        app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
        with TestClient(app) as client:
            assert client.post("/api/embedding/repair").status_code == 202
            prereq: dict = {}
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                prereq = client.get("/api/init-status").json()["prerequisites"]
                if prereq.get("embedding_repair_total"):
                    break
                time.sleep(0.05)
            release.set()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if client.get("/api/embedding/repair").json().get("done"):
                    break
                time.sleep(0.05)
        assert prereq["embedding_check"] == "repairing"
        assert "50%" in prereq["embedding_detail"]
        assert prereq["embedding_repair_running"] is True
        assert prereq["embedding_repair_completed"] == 200_000_000
        assert prereq["embedding_repair_total"] == 400_000_000

    def test_init_status_reports_process_global_auto_pull_progress(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import embedding_progress

        completed = 240 * 1024 * 1024
        total = 568 * 1024 * 1024
        embedding_progress.mark_pull_running("bge-m3")
        embedding_progress.report_pull("downloading", completed, total)
        embedding_progress.report_ollama_phase("starting")
        try:
            prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili"])
            app, _ = self._make_app(tmp_path, embedding_provider="ollama", prereqs=prereqs)
            with TestClient(app) as client:
                body = client.get("/api/init-status").json()
            prereq = body["prerequisites"]
            assert prereq["embedding_check"] == "repairing"
            assert prereq["embedding_repair_running"] is True
            assert prereq["embedding_repair_completed"] == completed
            assert prereq["embedding_repair_total"] == total
            assert "42%" in prereq["embedding_pull_status"]
            assert prereq["embedding_detail"] == prereq["embedding_pull_status"]
            assert prereq["ollama_phase"] == "starting"
        finally:
            embedding_progress.mark_pull_done(True, "")
            embedding_progress.report_ollama_phase("ready")


def test_config_llm_probe_timeout_allows_cold_start_but_remains_bounded() -> None:
    from openbiliclaw.api.app import _config_llm_probe_timeout_seconds

    assert _config_llm_probe_timeout_seconds(2) == 10.0
    assert _config_llm_probe_timeout_seconds(30) == 30.0
    assert _config_llm_probe_timeout_seconds(1200) == 120.0
    assert _config_llm_probe_timeout_seconds("invalid") == 120.0


class TestLlmFallbackConfigValidationAndProbe:
    """`[llm].fallback_provider` dead-state surfacing (community reports:
    'fallback 不生效' + '配置页保存有问题').

    - PUT /api/config must reject (400, ok=false) a fallback equal to the
      default provider — previously the desktop UI silently dropped the
      fallback panel's api_key/model/base_url in that case and the save
      "succeeded".
    - POST /api/config/probe-service kind="llm_fallback" probes the exact
      fallback provider (no fallback chain) without writing config.toml.
    """

    def _client(self, monkeypatch, tmp_path, cfg):
        from fastapi.testclient import TestClient

        from openbiliclaw.config import save_config

        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        return TestClient(app), config_path

    def _base_config(self):
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        return Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="sk-main", model="gpt-main"),
                deepseek=LLMProviderConfig(api_key="sk-fallback", model="deepseek-chat"),
            ),
        )

    def test_put_config_rejects_same_name_llm_fallback(self, monkeypatch, tmp_path) -> None:
        cfg = self._base_config()
        client, config_path = self._client(monkeypatch, tmp_path, cfg)
        before = config_path.read_bytes()

        response = client.put(
            "/api/config",
            json={"llm": {"default_provider": "openai", "fallback_provider": "openai"}},
        )

        assert response.status_code == 400, response.text
        data = response.json()
        assert data["ok"] is False
        issue_fields = {issue["field"] for issue in data["config"]["issues"]}
        assert "llm.fallback_provider" in issue_fields
        # Not persisted.
        assert config_path.read_bytes() == before

    def test_probe_llm_fallback_probes_exact_fallback_provider(self, monkeypatch, tmp_path) -> None:
        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.llm.concurrency import LLMTrafficClass

        calls: list[tuple[str, object]] = []
        runtime_gate = None

        class FakeRegistry:
            available_providers = ["openai", "deepseek"]
            default_provider = "openai"

            def is_chat_capable(self, name: str) -> bool:
                return name in ("openai", "deepseek")

            async def complete_provider(self, provider_name, messages, **kwargs):  # noqa: ARG002
                assert runtime_gate is not None
                status = runtime_gate.status_payload()
                assert status["llm_total_active"] == 1
                assert status["llm_background_active"] == 0
                calls.append((provider_name, kwargs.get("model")))
                return LLMResponse(
                    content="OK",
                    provider=provider_name,
                    model=str(kwargs.get("model") or ""),
                )

        monkeypatch.setattr(
            "openbiliclaw.llm.registry.build_llm_registry",
            lambda probe_cfg: FakeRegistry(),
        )
        cfg = self._base_config()
        client, config_path = self._client(monkeypatch, tmp_path, cfg)
        runtime_gate = client.app.state.runtime_context.llm_concurrency_gate
        # A probe is a recovery/control-plane action. It must remain admitted
        # when init has no serviceable inventory yet.
        runtime_gate.update_inventory(available=0, target=20)
        before = config_path.read_bytes()

        response = client.post(
            "/api/config/probe-service",
            json={
                "kind": "llm_fallback",
                "config": {"llm": {"fallback_provider": "deepseek"}},
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True, body
        assert body["kind"] == "llm_fallback"
        assert body["provider"] == "deepseek"
        # Exact single-provider probe — never the fallback chain.
        assert [provider for provider, _model in calls] == ["deepseek"]
        assert runtime_gate.classify("api.config_probe") is LLMTrafficClass.INTERACTIVE
        assert runtime_gate.status_payload()["llm_total_active"] == 0
        assert config_path.read_bytes() == before

    def test_probe_llm_fallback_refuses_cleanly_when_unconfigured(
        self, monkeypatch, tmp_path
    ) -> None:
        cfg = self._base_config()
        client, _config_path = self._client(monkeypatch, tmp_path, cfg)

        response = client.post(
            "/api/config/probe-service",
            json={"kind": "llm_fallback", "config": {"llm": {"fallback_provider": ""}}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "not configured" in body["error"].lower()

    def test_probe_llm_fallback_refuses_cleanly_for_same_name(self, monkeypatch, tmp_path) -> None:
        cfg = self._base_config()
        client, _config_path = self._client(monkeypatch, tmp_path, cfg)

        response = client.post(
            "/api/config/probe-service",
            json={"kind": "llm_fallback", "config": {"llm": {"fallback_provider": "openai"}}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "same as the default" in body["error"]


class TestKeywordGenerationMode:
    """Backend read-derivation of the UI-facing keyword_generation_mode enum
    from the two canonical DiscoveryConfig booleans (Task 1)."""

    @pytest.mark.parametrize(
        ("enabled", "replace", "expected"),
        [
            (False, False, "legacy"),
            (False, True, "legacy"),  # tolerant read: enabled=false → legacy
            (True, False, "hybrid"),
            (True, True, "inspiration"),
        ],
    )
    def test_derive_keyword_generation_mode_pure_fn(
        self, enabled: bool, replace: bool, expected: str
    ) -> None:
        from openbiliclaw.api.app import _derive_keyword_generation_mode

        assert _derive_keyword_generation_mode(enabled, replace) == expected

    def test_default_config_derives_hybrid_mode(self) -> None:
        from openbiliclaw.api.app import _derive_keyword_generation_mode
        from openbiliclaw.config import Config

        discovery = Config().discovery

        assert discovery.inspiration_search_enabled is True
        assert discovery.inspiration_replace_merged_keywords is False
        assert (
            _derive_keyword_generation_mode(
                discovery.inspiration_search_enabled,
                discovery.inspiration_replace_merged_keywords,
            )
            == "hybrid"
        )

    @pytest.mark.parametrize(
        ("enabled", "replace", "expected"),
        [
            (False, False, "legacy"),
            (False, True, "legacy"),  # edge: enabled=false & replace=true → legacy
            (True, False, "hybrid"),
            (True, True, "inspiration"),
        ],
    )
    def test_get_config_returns_derived_keyword_generation_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        enabled: bool,
        replace: bool,
        expected: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        cfg = Config()
        cfg.discovery.inspiration_search_enabled = enabled
        cfg.discovery.inspiration_replace_merged_keywords = replace
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        app = create_app()
        client = TestClient(app)

        response = client.get("/api/config")

        assert response.status_code == 200
        assert response.json()["discovery"]["keyword_generation_mode"] == expected


class TestKeywordGenerationModeWrite:
    """PUT /api/config translation of keyword_generation_mode → the two
    canonical DiscoveryConfig booleans (Task 2)."""

    @pytest.mark.parametrize(
        ("mode", "enabled", "replace"),
        [
            ("legacy", False, False),
            ("hybrid", True, False),
            ("inspiration", True, True),
        ],
    )
    def test_mode_to_flags_pure_fn(self, mode: str, enabled: bool, replace: bool) -> None:
        from openbiliclaw.api.app import _mode_to_flags

        assert _mode_to_flags(mode) == (enabled, replace)

    @staticmethod
    def _valid_config():  # type: ignore[no-untyped-def]
        # A config that passes LLM validation so PUT actually persists (an
        # unconfigured default deepseek provider makes the handler return 400).
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        return Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
            ),
        )

    @staticmethod
    def _client(monkeypatch: pytest.MonkeyPatch, tmp_path, cfg):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from openbiliclaw.config import save_config

        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        return TestClient(app), config_path

    @pytest.mark.parametrize(
        ("mode", "enabled", "replace"),
        [
            ("legacy", False, False),
            ("hybrid", True, False),
            ("inspiration", True, True),
        ],
    )
    def test_put_config_mode_persists_canonical_booleans(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path, mode: str, enabled: bool, replace: bool
    ) -> None:
        cfg = self._valid_config()
        client, config_path = self._client(monkeypatch, tmp_path, cfg)

        response = client.put("/api/config", json={"discovery": {"keyword_generation_mode": mode}})

        assert response.status_code == 202, response.text
        # Both canonical booleans set (no stale residue).
        assert cfg.discovery.inspiration_search_enabled is enabled
        assert cfg.discovery.inspiration_replace_merged_keywords is replace
        # config.toml holds the two booleans, NEVER the derived mode key.
        written = config_path.read_text(encoding="utf-8")
        assert "keyword_generation_mode" not in written
        assert f"inspiration_search_enabled = {'true' if enabled else 'false'}" in written
        assert f"inspiration_replace_merged_keywords = {'true' if replace else 'false'}" in written
        # Round-trip: the PUT response reflects the derived mode (Task 1 read path).
        assert response.json()["config"]["discovery"]["keyword_generation_mode"] == mode

    def test_put_config_mode_change_clears_stale_replace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        cfg = self._valid_config()
        client, config_path = self._client(monkeypatch, tmp_path, cfg)

        # First set inspiration (both booleans true)...
        r1 = client.put(
            "/api/config", json={"discovery": {"keyword_generation_mode": "inspiration"}}
        )
        assert r1.status_code == 202
        assert cfg.discovery.inspiration_search_enabled is True
        assert cfg.discovery.inspiration_replace_merged_keywords is True

        # ...then legacy: replace MUST go back to false (no stale residue, R1 S2).
        r2 = client.put("/api/config", json={"discovery": {"keyword_generation_mode": "legacy"}})
        assert r2.status_code == 202
        assert cfg.discovery.inspiration_search_enabled is False
        assert cfg.discovery.inspiration_replace_merged_keywords is False
        assert r2.json()["config"]["discovery"]["keyword_generation_mode"] == "legacy"

    def test_put_config_illegal_mode_returns_422(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        cfg = self._valid_config()
        client, _config_path = self._client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config", json={"discovery": {"keyword_generation_mode": "garbage"}}
        )

        assert response.status_code == 422

    def test_put_config_mode_wins_over_explicit_booleans(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        cfg = self._valid_config()
        client, _config_path = self._client(monkeypatch, tmp_path, cfg)

        # mode=legacy alongside an explicit inspiration_search_enabled=true → mode wins.
        response = client.put(
            "/api/config",
            json={
                "discovery": {
                    "keyword_generation_mode": "legacy",
                    "inspiration_search_enabled": True,
                    "inspiration_replace_merged_keywords": True,
                }
            },
        )

        assert response.status_code == 202, response.text
        assert cfg.discovery.inspiration_search_enabled is False
        assert cfg.discovery.inspiration_replace_merged_keywords is False

    def test_put_config_persists_keyword_digest_grace_hours(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        cfg = self._valid_config()
        client, config_path = self._client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={"discovery": {"keyword_digest_grace_hours": 0}},
        )

        assert response.status_code == 202, response.text
        assert cfg.discovery.keyword_digest_grace_hours == 0
        assert "keyword_digest_grace_hours = 0" in config_path.read_text(encoding="utf-8")
        assert response.json()["config"]["discovery"]["keyword_digest_grace_hours"] == 0


# ---------------------------------------------------------------------------
# Probe defer ("暂时忽略") — button + chat routing
# ---------------------------------------------------------------------------


def _make_defer_app(interest_defer=None, avoidance_defer=None, llm_reply="neutral"):
    """Build a TestClient app whose speculators record defer calls."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from openbiliclaw.soul.speculator import DeferResult

    class FakeLLM:
        async def complete_with_core_memory(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(content=llm_reply)

    class FakeDialogue:
        async def respond(self, _message: str, *, scope: str = "chat", turn_id: str = "") -> str:
            return "好的，我记住了。"

    class FakeInterestSpeculator:
        def __init__(self) -> None:
            self.defer_calls: list[str] = []

        def get_active_speculations(self) -> list[object]:
            return []

        def user_defer_speculation(self, domain: str) -> DeferResult:
            self.defer_calls.append(domain)
            return interest_defer or DeferResult(
                outcome="deferred", deferred_until="2026-07-14T00:00:00", defer_count=1
            )

    class FakeAvoidanceSpeculator:
        def __init__(self) -> None:
            self.defer_calls: list[str] = []

        def get_active_avoidances(self) -> list[object]:
            return []

        def user_defer_avoidance(self, domain: str) -> DeferResult:
            self.defer_calls.append(domain)
            return avoidance_defer or DeferResult(
                outcome="deferred", deferred_until="2026-07-14T00:00:00", defer_count=1
            )

    class FakeSoulEngine:
        def __init__(self, interest, avoidance) -> None:
            self._speculator = interest
            self._avoidance_speculator = avoidance

    class FakeMemoryManager:
        def load_cognition_updates(self) -> list[object]:
            return []

        def save_cognition_updates(self, _updates: list[object]) -> None:
            return None

    interest = FakeInterestSpeculator()
    avoidance = FakeAvoidanceSpeculator()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=FakeSoulEngine(interest, avoidance),
        dialogue=FakeDialogue(),
        recommendation_engine=SimpleNamespace(_llm=FakeLLM()),
    )
    return TestClient(app), interest, avoidance


def test_interest_probe_defer_button_returns_deferred() -> None:
    client, interest, _ = _make_defer_app()
    resp = client.post(
        "/api/interest-probes/respond",
        json={"domain": "桌游", "response": "defer"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "deferred"
    assert body["defer_count"] == 1
    assert body["deferred_until"] == "2026-07-14T00:00:00"
    assert interest.defer_calls == ["桌游"]


def test_interest_probe_defer_button_exhausted() -> None:
    from openbiliclaw.soul.speculator import DeferResult

    client, _, _ = _make_defer_app(interest_defer=DeferResult(outcome="exhausted", defer_count=3))
    resp = client.post(
        "/api/interest-probes/respond",
        json={"domain": "桌游", "response": "defer"},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "defer_exhausted"
    assert body["defer_count"] == 3


def test_interest_probe_defer_not_found() -> None:
    from openbiliclaw.soul.speculator import DeferResult

    client, _, _ = _make_defer_app(interest_defer=DeferResult(outcome="not_found"))
    resp = client.post(
        "/api/interest-probes/respond",
        json={"domain": "不存在", "response": "defer"},
    )
    body = resp.json()
    assert body["ok"] is False


def test_avoidance_probe_defer_button_returns_deferred() -> None:
    client, _, avoidance = _make_defer_app()
    resp = client.post(
        "/api/avoidance-probes/respond",
        json={"domain": "标题党", "response": "defer"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "deferred"
    assert avoidance.defer_calls == ["标题党"]


def test_probe_defer_validation_mentions_defer() -> None:
    client, _, _ = _make_defer_app()
    resp = client.post(
        "/api/interest-probes/respond",
        json={"domain": "桌游", "response": "bogus"},
    )
    assert resp.status_code == 422
    assert "defer" in resp.json()["detail"]


def test_interest_probe_chat_neutral_deferred_calls_defer() -> None:
    # LLM returns neutral (falls through to keyword); keyword sees 「先放着吧」
    # → neutral_deferred → defer method invoked.
    client, interest, _ = _make_defer_app(llm_reply="neutral")
    resp = client.post(
        "/api/interest-probes/respond",
        json={"domain": "桌游", "response": "chat", "message": "先放着吧"},
    )
    assert resp.status_code == 200
    assert interest.defer_calls == ["桌游"]


def test_defer_exhausted_not_in_handled_sets() -> None:
    from openbiliclaw.soul.avoidance_speculator import HANDLED_AVOIDANCE_RESPONSES
    from openbiliclaw.soul.speculator import HANDLED_PROBE_FEEDBACK_RESPONSES

    for handled in (HANDLED_PROBE_FEEDBACK_RESPONSES, HANDLED_AVOIDANCE_RESPONSES):
        assert "defer" not in handled
        assert "defer_exhausted" not in handled


def test_interest_pending_excludes_deferred_probes(tmp_path) -> None:
    """The 刷新不弹回 guarantee: a deferred interest probe must NOT appear in
    GET /api/interest-probes/pending, while active ones do."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from openbiliclaw.soul.speculator import (
        SpeculativeInterest,
        SpeculativeState,
        save_speculative_state,
    )

    save_speculative_state(
        tmp_path,
        SpeculativeState(
            active=[
                SpeculativeInterest(domain="仍活跃", status="active"),
                SpeculativeInterest(
                    domain="已搁置",
                    status="deferred",
                    deferred_until="2099-01-01T00:00:00",
                    defer_count=1,
                ),
            ]
        ),
    )

    class FakeSoulEngine:
        pass

    app = create_app(soul_engine=FakeSoulEngine(), memory_manager=object(), database=object())
    app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
    client = TestClient(app)

    items = client.get("/api/interest-probes/pending").json()["items"]
    domains = {i["domain"] for i in items}
    assert "仍活跃" in domains
    assert "已搁置" not in domains


def test_reshuffle_endpoint_forwards_visible_card_exclusions() -> None:
    from fastapi.testclient import TestClient

    class FakeRuntimeController:
        pool_available_count = 3
        event_hub = None

        def get_runtime_status(self) -> dict[str, object]:
            return {
                "initialized": True,
                "pool_available_count": self.pool_available_count,
                "pool_pending_count": 0,
            }

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"profile": "ok"}

    class FakeRecommendationEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[object, list[str] | None, int]] = []

        async def reshuffle_recommendations(
            self,
            *,
            profile: object,
            limit: int = 10,
            excluded_bvids: list[str] | None = None,
        ) -> list[object]:
            self.calls.append((profile, excluded_bvids, limit))
            return []

    engine = FakeRecommendationEngine()
    app = create_app(
        memory_manager=object(),
        database=object(),
        soul_engine=FakeSoulEngine(),
        recommendation_engine=engine,
        runtime_controller=FakeRuntimeController(),
    )
    client = TestClient(app)

    no_body = client.post("/api/recommendations/reshuffle")
    with_exclusions = client.post(
        "/api/recommendations/reshuffle",
        json={"excluded_bvids": ["BV1VISIBLE", " BV2VISIBLE ", ""]},
    )

    assert no_body.status_code == 200
    assert with_exclusions.status_code == 200
    assert engine.calls == [
        ({"profile": "ok"}, [], 10),
        ({"profile": "ok"}, ["BV1VISIBLE", "BV2VISIBLE"], 10),
    ]


def test_successful_reshuffle_records_one_batch_event_and_empty_result_records_none() -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.recommendation.engine import Recommendation

    class FakeRuntimeController:
        event_hub = None

        def get_runtime_status(self) -> dict[str, object]:
            return {
                "initialized": True,
                "pool_available_count": 3,
                "pool_pending_count": 0,
            }

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"profile": "ok"}

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeRecommendationEngine:
        def __init__(self) -> None:
            self.calls = 0

        async def reshuffle_recommendations(
            self,
            *,
            profile: object,
            limit: int = 10,
            excluded_bvids: list[str] | None = None,
        ) -> list[Recommendation]:
            del profile, limit, excluded_bvids
            self.calls += 1
            if self.calls > 1:
                return []
            return [
                Recommendation(
                    content=DiscoveredContent(
                        bvid="BV1NEWBATCH",
                        title="新批次",
                        up_name="UP",
                        cover_url="",
                    ),
                    recommendation_id=91,
                    expression="这是一条新推荐。",
                    topic_label="换批测试",
                    confidence=0.9,
                    presented=False,
                )
            ]

    memory = FakeMemoryManager()
    app = create_app(
        memory_manager=memory,
        database=object(),
        soul_engine=FakeSoulEngine(),
        recommendation_engine=FakeRecommendationEngine(),
        runtime_controller=FakeRuntimeController(),
    )
    client = TestClient(app)

    success = client.post(
        "/api/recommendations/reshuffle",
        json={"excluded_bvids": ["BV1CURRENT", " BV1CURRENT ", "BV2CURRENT"]},
    )
    empty = client.post(
        "/api/recommendations/reshuffle",
        json={"excluded_bvids": ["BV1NEWBATCH"]},
    )

    assert success.status_code == 200
    assert empty.status_code == 200
    assert len(memory.events) == 1
    event = memory.events[0]
    assert event["event_type"] == "reshuffle"
    assert event["context"] == "你在推荐页换了一批内容。"
    assert event["metadata"] == {
        "recommendation_source_platform": "all",
        "excluded_item_ids": ["BV1CURRENT", "BV2CURRENT"],
        "returned_item_ids": ["BV1NEWBATCH"],
        "batch_size": 1,
        "source_platform": "web",
        "signal_strength": 0.1,
        "event_namespace": "recommendation",
        "profile_update_owner": "generic",
    }


# ── Platform-scoped recommendation requests ─────────────────────────


class _ScopedFakeSoulEngine:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _ScopedFakeRuntimeController:
    event_hub = None

    def __init__(self, available: int = 5) -> None:
        self.available = available
        self.requests: list[tuple[str, bool]] = []

    def get_runtime_status(self) -> dict[str, object]:
        return {
            "initialized": True,
            "pool_available_count": self.available,
            "pool_pending_count": 0,
        }

    async def request_replenishment(self, *, reason: str, force: bool = False) -> dict[str, object]:
        self.requests.append((reason, force))
        return {"accepted": True, "state": "running", "reason": reason}


class _ScopedResultEngine:
    """Engine exposing the modern ``*_with_result`` surface."""

    def __init__(self, items: list[object] | None = None) -> None:
        self.reshuffle_kwargs: list[dict[str, object]] = []
        self.append_kwargs: list[dict[str, object]] = []
        self._items = items or []

    def _result(self) -> SimpleNamespace:
        return SimpleNamespace(
            items=list(self._items),
            pool_counts_after={"available": 5, "raw": 5, "pending": 0},
            timings=None,
        )

    async def reshuffle_recommendations_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.reshuffle_kwargs.append(dict(kwargs))
        return self._result()

    async def append_recommendations_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.append_kwargs.append(dict(kwargs))
        return self._result()


def _scoped_app(engine: object, runtime: object, database: object | None = None) -> object:
    return create_app(
        memory_manager=object(),
        database=database if database is not None else object(),
        soul_engine=_ScopedFakeSoulEngine(),
        recommendation_engine=engine,
        runtime_controller=runtime,
    )


def test_reshuffle_and_append_forward_canonical_source_platform() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine()
    client = TestClient(_scoped_app(engine, _ScopedFakeRuntimeController()))

    reshuffle = client.post(
        "/api/recommendations/reshuffle",
        json={"excluded_bvids": ["BV1"], "source_platform": "weibo"},
    )
    append = client.post(
        "/api/recommendations/append",
        json={"excluded_bvids": ["BV1"], "source_platform": "weibo"},
    )

    assert reshuffle.status_code == 200
    assert append.status_code == 200
    assert engine.reshuffle_kwargs[0]["source_platform"] == "weibo"
    assert engine.append_kwargs[0]["source_platform"] == "weibo"


def test_recommendation_requests_normalize_platform_aliases() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine()
    client = TestClient(_scoped_app(engine, _ScopedFakeRuntimeController()))

    assert (
        client.post(
            "/api/recommendations/reshuffle",
            json={"source_platform": "xhs"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/recommendations/append",
            json={"excluded_bvids": [], "source_platform": " ZH "},
        ).status_code
        == 200
    )

    assert engine.reshuffle_kwargs[0]["source_platform"] == "xiaohongshu"
    assert engine.append_kwargs[0]["source_platform"] == "zhihu"


def test_omitted_platform_preserves_the_legacy_engine_call_shape() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine()
    client = TestClient(_scoped_app(engine, _ScopedFakeRuntimeController()))

    client.post("/api/recommendations/reshuffle", json={"excluded_bvids": []})
    client.post("/api/recommendations/append", json={"excluded_bvids": []})
    client.post("/api/recommendations/reshuffle", json={"source_platform": ""})

    for call in (*engine.reshuffle_kwargs, *engine.append_kwargs):
        assert "source_platform" not in call


def test_recommendation_requests_reject_unknown_platform() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine()
    client = TestClient(_scoped_app(engine, _ScopedFakeRuntimeController()))

    for path in ("/api/recommendations/reshuffle", "/api/recommendations/append"):
        for bogus in ("'; DROP TABLE content_cache; --", "bilibili2"):
            response = client.post(path, json={"excluded_bvids": [], "source_platform": bogus})
            assert response.status_code == 422, (path, bogus)

    # A rejected request must never reach the engine or silently mean "全部".
    assert engine.reshuffle_kwargs == []
    assert engine.append_kwargs == []


def test_scoped_short_batch_wakes_existing_replenishment_path() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine(items=[])
    runtime = _ScopedFakeRuntimeController(available=42)
    client = TestClient(_scoped_app(engine, runtime))

    response = client.post(
        "/api/recommendations/append",
        json={"excluded_bvids": [], "source_platform": "zhihu"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    # Pool-wide inventory is healthy, so only the scoped shortfall can explain
    # this; it must wake the existing forced replenishment path.
    assert runtime.requests and runtime.requests[0][1] is True


def test_unscoped_short_batch_with_healthy_pool_does_not_force_replenishment() -> None:
    from fastapi.testclient import TestClient

    engine = _ScopedResultEngine(items=[])
    runtime = _ScopedFakeRuntimeController(available=42)
    client = TestClient(_scoped_app(engine, runtime))

    response = client.post("/api/recommendations/append", json={"excluded_bvids": []})

    assert response.status_code == 200
    assert runtime.requests == []


class _AvailabilityDatabase:
    def __init__(self, snapshot: object | None = None, error: Exception | None = None) -> None:
        self._snapshot = snapshot
        self._error = error
        self.calls = 0

    async def load_pool_platform_availability_async(self, **_kwargs: object) -> object:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._snapshot


def test_platform_availability_endpoint_returns_canonical_map() -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.storage.database import PoolPlatformAvailability

    database = _AvailabilityDatabase(
        PoolPlatformAvailability(
            total_available=37,
            by_platform={"bilibili": 18, "zhihu": 7, "xiaohongshu": 5, "reddit": 7},
        )
    )
    client = TestClient(
        _scoped_app(_ScopedResultEngine(), _ScopedFakeRuntimeController(), database)
    )

    response = client.get("/api/recommendations/platform-availability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_available"] == 37
    assert payload["by_platform"] == {
        "bilibili": 18,
        "zhihu": 7,
        "xiaohongshu": 5,
        "reddit": 7,
    }
    assert sum(payload["by_platform"].values()) == payload["total_available"]


def test_platform_availability_read_failure_is_a_diagnosable_5xx() -> None:
    """A failed read must not be published as an all-zero snapshot."""
    from fastapi.testclient import TestClient

    database = _AvailabilityDatabase(error=RuntimeError("database is locked"))
    client = TestClient(
        _scoped_app(_ScopedResultEngine(), _ScopedFakeRuntimeController(), database),
        raise_server_exceptions=False,
    )

    response = client.get("/api/recommendations/platform-availability")

    assert response.status_code >= 500
    assert "0" not in str(response.json().get("total_available", ""))
    assert database.calls == 1


def test_apply_retraction_db_marks_marks_matching_positive(tmp_path: Path) -> None:
    """The /api/events DB hook discounts stored positives undone by a retraction."""
    from openbiliclaw.api.app import apply_retraction_db_marks
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "hook.db")
    db.initialize()
    t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    ms = int(t0.timestamp() * 1000)
    db.insert_event(
        "like",
        url="https://x.com/i/status/123",
        metadata={"signal_strength": 0.85, "timestamp": ms},
    )

    retraction_ms = int((t0 + timedelta(hours=1)).timestamp() * 1000)
    marked = apply_retraction_db_marks(
        db,
        [
            {
                "event_type": "feedback",
                "url": "https://x.com/u/status/123",
                "metadata": {
                    "feedback_type": "retraction",
                    "retracted_action": "like",
                    "timestamp": retraction_ms,
                },
            }
        ],
    )
    assert marked == 1
    row = db.query_events(event_types=["like"], limit=1)[0]
    assert json.loads(row["metadata"])["retracted"] is True


def test_apply_retraction_db_marks_skips_out_of_whitelist_action(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from openbiliclaw.api.app import apply_retraction_db_marks
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "hook2.db")
    db.initialize()
    with caplog.at_level("WARNING"):
        marked = apply_retraction_db_marks(
            db,
            [
                {
                    "event_type": "feedback",
                    "url": "https://x.com/i/status/123",
                    "metadata": {
                        "feedback_type": "retraction",
                        "retracted_action": "view",
                        "timestamp": 1,
                    },
                }
            ],
        )
    assert marked == 0


class TestRootRedirectLanding:
    """`GET /` mirrors packaging/entry.py's landing decision for browsers
    that reach the port without the packaged launcher."""

    def _client(self, soul_engine):
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=soul_engine)
        return TestClient(app)

    def test_root_redirects_to_web_when_initialized(self) -> None:
        client = self._client(SimpleNamespace(is_profile_ready=lambda: True))
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/web"

    def test_root_redirects_to_setup_when_uninitialized(self) -> None:
        client = self._client(SimpleNamespace(is_profile_ready=lambda: False))
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/setup/"

    def test_root_falls_back_to_web_when_readiness_unknown(self) -> None:
        def _boom() -> bool:
            raise RuntimeError("probe failed")

        client = self._client(SimpleNamespace(is_profile_ready=_boom))
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/web"


class TestUnifiedInterestLineFeedbackIngestion:
    """统一兴趣更新线 Wave A：/api/feedback 是否喂 pipeline 由开关决定。

    Wave A 默认关——开着会让同一条反馈被新快线和仍在跑的旧批线双计。
    """

    class _FakeDatabase:
        def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object]:
            return {
                "id": recommendation_id,
                "bvid": "BV1REC",
                "title": "讲透城市与建筑",
                "topic_label": "建筑",
                "up_name": "建筑师",
            }

        def update_recommendation_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            feedback_note: str = "",
        ) -> None:
            return None

    class _SpyPipeline:
        def __init__(self) -> None:
            self.signals: list[Any] = []

        async def enqueue(self, signal: Any) -> object:
            self.signals.append(signal)
            return object()

    class _FakeSoulEngine:
        def __init__(self, *, unified: bool) -> None:
            self.unified_interest_line_enabled = unified
            self.pipeline = TestUnifiedInterestLineFeedbackIngestion._SpyPipeline()
            self.batch_calls = 0

        def is_profile_ready(self) -> bool:
            return True

        def record_immediate_feedback_cognition(
            self,
            *,
            feedback_type: str,
            title: str,
            note: str = "",
        ) -> None:
            return None

        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            self.batch_calls += 1
            return {"triggered": False}

    def _make_feedback_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        unified: bool,
    ) -> tuple[Any, list[int], Any, Any]:
        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.memory.manager import MemoryManager

        schedules: list[int] = [0]

        class FakeFeedbackBatchScheduler:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def schedule(self) -> None:
                schedules[0] += 1

            async def resume(self, *, recover: bool = True) -> None:
                del recover

            async def pause_and_drain(self, *, timeout: float = 1500.0) -> None:
                del timeout

            def status_payload(self) -> dict[str, object]:
                return {}

            async def close(self) -> None:
                return None

        monkeypatch.setattr(api_app, "FeedbackBatchScheduler", FakeFeedbackBatchScheduler)
        memory = MemoryManager(tmp_path)
        memory.initialize()
        soul = self._FakeSoulEngine(unified=unified)
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=self._FakeDatabase(),
                soul_engine=soul,
            )
        )
        return soul, schedules, client, memory

    def test_flag_off_leaves_feedback_behaviour_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        soul, schedules, client, _memory = self._make_feedback_client(
            monkeypatch, tmp_path, unified=False
        )

        with client:
            startup_schedules = schedules[0]
            response = client.post(
                "/api/feedback",
                json={
                    "recommendation_id": 7,
                    "feedback_type": "dislike",
                    "note": "太浅了",
                    "request_id": "feedback-flag-off-dislike",
                },
            )
            assert response.status_code == 200
            assert schedules[0] == startup_schedules + 1

        assert soul.pipeline.signals == [], "开关关闭时反馈绝不能进 pipeline（否则与旧批线双计）"

    def test_flag_on_persists_event_and_schedules_without_touching_pipeline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        soul, schedules, client, memory = self._make_feedback_client(
            monkeypatch, tmp_path, unified=True
        )

        with client:
            startup_schedules = schedules[0]
            response = client.post(
                "/api/feedback",
                json={
                    "recommendation_id": 7,
                    "feedback_type": "dislike",
                    "note": "太浅了",
                    "request_id": "feedback-flag-on-dislike",
                },
            )
            assert response.status_code == 200
            assert schedules[0] == startup_schedules + 1, "一次 POST 只唤醒 scheduler 一次"

        assert soul.pipeline.signals == [], "HTTP owner 不得直接碰 pipeline"
        [event] = memory.query_events(event_types=["feedback"], limit=10)
        metadata = json.loads(str(event["metadata"]))
        assert metadata["feedback_type"] == "dislike"
        assert metadata["feedback_note"] == "太浅了"

    def test_blocked_scheduler_tick_does_not_delay_consecutive_feedback_posts(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A running LLM/tick owner cannot put HTTP clicks behind its lock."""
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.memory.manager import MemoryManager

        tick_started = threading.Event()
        release_tick = threading.Event()

        class BlockingSoulEngine:
            unified_interest_line_enabled = True

            def __init__(self) -> None:
                self.calls = 0

            def is_profile_ready(self) -> bool:
                return True

            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str = "",
            ) -> None:
                return None

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                self.calls += 1
                if self.calls == 1:
                    tick_started.set()
                    await asyncio.to_thread(release_tick.wait, 15)
                return {"triggered": False}

        monkeypatch.setattr(api_app, "_FEEDBACK_BATCH_DEBOUNCE_SECONDS", 0.0)
        memory = MemoryManager(tmp_path)
        memory.initialize()
        soul = BlockingSoulEngine()
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=self._FakeDatabase(),
                soul_engine=soul,
            )
        )

        lifespan_started_at = time.monotonic()
        with client:
            startup_elapsed = time.monotonic() - lifespan_started_at
            assert startup_elapsed < 1.0, (
                f"blocked event recovery delayed TestClient startup by {startup_elapsed:.2f}s"
            )
            assert tick_started.wait(timeout=3), "startup recovery should wake the owner"
            assert client.get("/api/health").status_code == 200
            started_at = time.monotonic()
            responses = [
                client.post(
                    "/api/feedback",
                    json={
                        "recommendation_id": 7,
                        "feedback_type": feedback_type,
                        "note": "",
                        "request_id": f"blocked-feedback-{feedback_type}",
                    },
                )
                for feedback_type in ("like", "dislike")
            ]
            elapsed = time.monotonic() - started_at
            assert [response.status_code for response in responses] == [200, 200]
            assert elapsed < 1.0, f"feedback posts waited behind tick for {elapsed:.2f}s"
            assert len(memory.query_events(event_types=["feedback"], limit=10)) == 2
            release_tick.set()

    def test_startup_never_awaits_never_returning_event_recovery_and_shutdown_cleans_task(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The deferred owner may run first, but lifespan never joins it."""
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.memory.manager import MemoryManager

        tick_started = threading.Event()
        tick_cancelled = threading.Event()

        class NeverReturningSoulEngine:
            unified_interest_line_enabled = True

            def is_profile_ready(self) -> bool:
                return True

            async def process_feedback_batch_if_needed(self) -> None:
                tick_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    tick_cancelled.set()
                    raise

        monkeypatch.setattr(api_app, "_FEEDBACK_BATCH_DEBOUNCE_SECONDS", 0.0)
        memory = MemoryManager(tmp_path)
        memory.initialize()
        app = create_app(
            memory_manager=memory,
            database=self._FakeDatabase(),
            soul_engine=NeverReturningSoulEngine(),
        )

        async def restart_after_owner_gets_one_turn(
            _app: object,
            *,
            run_post_reload_llm_work: bool = True,
        ) -> None:
            del _app, run_post_reload_llm_work
            deadline = asyncio.get_running_loop().time() + 1.0
            while not tick_started.is_set():
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("deferred recovery never received an event-loop turn")
                await asyncio.sleep(0)

        app.state.runtime_context.restart_background_tasks = restart_after_owner_gets_one_turn
        client = TestClient(app)
        recovery_task: asyncio.Task[None] | None = None
        lifespan_started_at = time.monotonic()
        with client:
            startup_elapsed = time.monotonic() - lifespan_started_at
            assert startup_elapsed < 1.0, (
                f"never-returning recovery delayed startup by {startup_elapsed:.2f}s"
            )
            assert tick_started.is_set()
            recovery_task = app.state.event_recovery_task
            assert recovery_task is not None
            assert recovery_task.done() is False
            assert client.get("/api/health").status_code == 200

        assert recovery_task is not None
        assert recovery_task.done() is True
        assert tick_cancelled.wait(timeout=1)
        assert app.state.event_recovery_task is None

    def test_startup_provider_401_recovery_failure_does_not_delay_health(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A provider auth failure belongs to the background lane, not lifespan."""
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.llm.base import LLMAuthError
        from openbiliclaw.memory.manager import MemoryManager

        attempted = threading.Event()

        class UnauthorizedSoulEngine:
            unified_interest_line_enabled = True

            def is_profile_ready(self) -> bool:
                return True

            async def process_feedback_batch_if_needed(self) -> None:
                attempted.set()
                raise LLMAuthError("HTTP 401 Unauthorized", provider_name="test")

        monkeypatch.setattr(api_app, "_FEEDBACK_BATCH_DEBOUNCE_SECONDS", 0.0)
        memory = MemoryManager(tmp_path)
        memory.initialize()
        app = create_app(
            memory_manager=memory,
            database=self._FakeDatabase(),
            soul_engine=UnauthorizedSoulEngine(),
        )

        started_at = time.monotonic()
        with TestClient(app) as client:
            assert time.monotonic() - started_at < 1.0
            assert attempted.wait(timeout=3), "background recovery never reached the provider"
            assert client.get("/api/health").status_code == 200
            deadline = time.monotonic() + 1.0
            status = app.state.feedback_batch_scheduler.status_payload()
            while status["event_lane_last_error"] != "LLMAuthError":
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
                status = app.state.feedback_batch_scheduler.status_payload()
            assert status["event_lane_last_error"] == "LLMAuthError"

    def test_startup_claims_feedback_left_before_previous_shutdown(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Startup wakes the durable owner without requiring another user click."""
        import threading

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as api_app
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.soul.pipeline import FlushResult, OnionLayer

        memory = MemoryManager(tmp_path)
        memory.initialize()
        asyncio.run(
            memory.propagate_event(
                {
                    "event_type": "feedback",
                    "title": "关机前留下的反馈",
                    "metadata": {"feedback_type": "dislike", "feedback_note": "太浅"},
                }
            )
        )
        soul = SoulEngine(llm=object(), memory=memory, unified_interest_line=True)
        ticked = threading.Event()

        async def stop_before_analysis() -> FlushResult:
            ticked.set()
            return FlushResult()

        monkeypatch.setattr(soul.pipeline, "tick_if_buffered", stop_before_analysis)
        monkeypatch.setattr(api_app, "_FEEDBACK_BATCH_DEBOUNCE_SECONDS", 0.0)
        client = TestClient(
            create_app(
                memory_manager=memory,
                database=self._FakeDatabase(),
                soul_engine=soul,
            )
        )

        with client:
            assert ticked.wait(timeout=3)

        state = memory.load_feedback_state()
        assert state["last_processed_feedback_event_id"] == 1
        assert state["feedback_owner_version"] == 2
        assert str(state["feedback_owner_cutover_at"]).strip()
        for layer in (OnionLayer.INTEREST, OnionLayer.SURFACE):
            [signal] = soul.pipeline._buffers[layer.value].signals
            assert signal["id"] == "feedback-event-1"


class TestSoulEngineFeedbackConfigPlumbing:
    """三个 SoulEngine 构造点必须读同一套 config（三面契约）。

    Wave A 只给 ``api/runtime_context.py`` 接了 ``unified_interest_line``，
    而 ``feedback_batch_threshold`` 从来只有这一面在传——CLI 与 OpenClaw
    两面都在用硬编码默认值 3。对应的 CLI/OpenClaw 断言分别在
    ``tests/test_cli.py`` 与 ``tests/test_openclaw_adapter.py``。
    """

    def test_runtime_context_forwards_feedback_batch_config(self, tmp_path: Path) -> None:
        from openbiliclaw.api.runtime_context import build_runtime_context
        from openbiliclaw.config import Config

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.scheduler.feedback_batch_threshold = 6
        config.scheduler.unified_interest_line = True

        ctx = build_runtime_context(config)

        assert ctx.soul_engine is not None
        assert ctx.soul_engine.unified_interest_line_enabled is True
        assert ctx.soul_engine._feedback_batch_threshold == 6
