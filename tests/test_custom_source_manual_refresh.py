"""Explicit custom-source refresh completes one bounded ordinary admission wave."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.config import Config
from openbiliclaw.discovery.candidate_pipeline import (
    CandidateDrainResult,
    DiscoveryCandidatePipeline,
    PostAdmissionCopyReceipt,
    PostAdmissionCopyState,
)
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
from openbiliclaw.runtime.keyword_planner import KeywordPlanner
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.runtime.source_policy import effective_pool_source_shares
from openbiliclaw.sources.protocol import SourceFetchResult
from tests.test_custom_workflow_lifecycle import draft, item
from tests.test_custom_workflow_lifecycle import service as service
from tests.test_discovery_candidate_pipeline import _ScoringLLM
from tests.test_keyword_planner import _FakeLLM
from tests.test_refresh_runtime import (
    _FakeEventHub,
    _FakeMemoryManager,
    _ProfileSoulEngine,
    _RealDatabasePrecomputeEngine,
)


def _runtime(service: Any, **draft_fields: Any) -> SimpleNamespace:
    source = service.activate(draft(service, **draft_fields)["draft_id"])
    config = Config()
    config.scheduler.enabled = False
    config.sources.bilibili.enabled = False
    llm = _ScoringLLM(
        [
            {
                "content_id": "one",
                "score": 0.91,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            }
        ]
    )
    engine = ContentDiscoveryEngine(llm_service=llm, database=service.database)
    pipeline = DiscoveryCandidatePipeline(
        database=service.database,
        discovery_engine=engine,
        pool_target_count=4,
        min_eval_batch_size=30,
        max_eval_wait_seconds=90,
    )
    recommendations = _RealDatabasePrecomputeEngine(service.database)
    recommendations.drain_pending_expression_copy = recommendations.precompute_pool_copy
    soul = _ProfileSoulEngine()
    producer = CustomWorkflowProducer(
        database=service.database,
        source_id=source["source_id"],
        candidate_pipeline=pipeline,
        soul_engine=soul,
        service=service,
        candidate_evaluation_owned_by_coordinator=True,
    )
    service.fetch = AsyncMock(return_value=SourceFetchResult(status="ok", items=[item()]))
    runtime = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=service.database,
        soul_engine=soul,
        discovery_engine=engine,
        recommendation_engine=recommendations,
        discovery_candidate_pipeline=pipeline,
        scheduler_config=config.scheduler,
        pool_target_count=4,
        discovery_limit=2,
        event_hub=_FakeEventHub(),
        pool_source_shares_provider=lambda: effective_pool_source_shares(
            config, database=service.database
        ),
        custom_source_producer_provider=lambda _: producer,
    )
    return SimpleNamespace(
        runtime=runtime,
        source=source,
        producer=producer,
        pipeline=pipeline,
        recommendations=recommendations,
        llm=llm,
        config=config,
    )


async def test_manual_custom_only_refresh_finishes_admission_with_scheduler_off(service):
    setup = _runtime(service)
    result = await setup.runtime.force_refresh()

    assert result["refreshed"] is True
    assert result["producer_results"][setup.source["source_id"]]["enqueued"] == 1
    assert service.database.count_pool_candidates() == 1
    assert len(setup.llm.calls) == 1
    assert setup.runtime.memory_manager.state["last_replenished_count"] == 1
    assert setup.config.scheduler.enabled is False


@pytest.mark.parametrize(
    ("status", "error"),
    [("rate_limited", "http_429"), ("failed", "timeout"), ("degraded", "http_429")],
)
async def test_manual_refresh_preserves_persisted_failure_backoff(service, status, error):
    setup = _runtime(service)
    workflow = service.store.list_workflows(setup.source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(
        run,
        status=status,
        error=error,
        items=[],
        enqueue=lambda _: 0,
        interval_minutes=30,
    )
    next_run = service.store.get_workflow(workflow["workflow_id"])["next_run_at"]

    assert service.store.claim(workflow["workflow_id"], {}, force=True) is None
    await setup.runtime._complete_manual_refresh()
    service.fetch.assert_not_awaited()
    assert service.store.get_workflow(workflow["workflow_id"])["next_run_at"] == next_run
    assert setup.runtime.get_runtime_status()["manual_refresh_state"] == "failed"
    assert "稍后" in setup.runtime.get_runtime_status()["manual_refresh_message"]


async def test_manual_interest_refresh_plans_then_claims_keyword_with_scheduler_off(service):
    setup = _runtime(service, query_mode="interest")
    setup.config.discovery.unified_keyword_planner_enabled = True
    planner_llm = _FakeLLM({setup.source["source_id"]: ["Python release notes"]})
    setup.runtime.keyword_planner = KeywordPlanner(
        llm_service=planner_llm,
        database=service.database,
        config=setup.config,
        soul_engine=setup.runtime.soul_engine,
    )
    setup.producer.keyword_fetch_coordinator = KeywordFetchCoordinator(
        database=service.database,
        discovery_config=setup.config.discovery,
    )

    result = await setup.runtime.force_refresh()

    assert result["producer_results"][setup.source["source_id"]]["enqueued"] == 1
    assert planner_llm.calls
    assert service.fetch.await_args.kwargs["parameters"]["query"] == "Python release notes"
    assert service.database.count_pool_candidates() == 1
    keyword = service.database.conn.execute(
        "SELECT platform,status FROM discovery_keywords WHERE keyword='Python release notes'"
    ).fetchone()
    assert tuple(keyword) == (setup.source["source_id"], "used")


async def test_manual_refresh_bypasses_healthy_cadence_but_keeps_source_lock(service):
    setup = _runtime(service)
    workflow = service.store.list_workflows(setup.source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(run, status="empty", items=[], enqueue=lambda _: 0, interval_minutes=30)
    assert service.store.claim(workflow["workflow_id"], {}) is None

    await setup.runtime.force_refresh()
    assert service.fetch.await_count == 1
    # The producer lease is shared with other processes, independently of the
    # controller lock. Explicit refresh cannot steal an active run.
    run = service.store.claim(workflow["workflow_id"], {}, force=True)
    assert run is not None
    result = await setup.runtime.force_refresh()
    assert result["producer_results"][setup.source["source_id"]]["reason"] == "in_flight"
    assert "等待" in result["manual_failure_message"]
    assert service.fetch.await_count == 1


@pytest.mark.parametrize("gate", ["disabled", "archived", "full"])
async def test_manual_refresh_respects_source_permission_and_pool_cap(service, gate):
    setup = _runtime(service)
    if gate == "disabled":
        service.sources.update_source(setup.source["source_id"], enabled=False)
    elif gate == "archived":
        service.sources.archive_source(setup.source["source_id"])
    else:
        setup.runtime.pool_target_count = 0

    result = await setup.runtime.force_refresh()
    assert result["refreshed"] is False
    service.fetch.assert_not_awaited()
    assert not setup.llm.calls


async def test_manual_refresh_keeps_login_pause_and_reports_recovery_action(service):
    setup = _runtime(service)
    workflow = service.store.list_workflows(setup.source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(
        run, status="login_required", items=[], enqueue=lambda _: 0, interval_minutes=30
    )
    result = await setup.runtime.force_refresh()
    service.fetch.assert_not_awaited()
    assert "登录" in result["manual_failure_message"]
    assert result["producer_results"][setup.source["source_id"]]["reason"] == "login_required"


async def test_manual_refresh_preserves_success_when_another_workflow_fails(service):
    setup = _runtime(service)
    service.activate(draft(service)["draft_id"])
    service.fetch.side_effect = [
        SourceFetchResult(status="failed", error_code="timeout"),
        SourceFetchResult(status="ok", items=[item()]),
    ]
    result = await setup.runtime.force_refresh()
    source_result = result["producer_results"][setup.source["source_id"]]
    assert source_result["enqueued"] == 1
    assert source_result["reason"] == "failed"
    assert set(source_result["workflow_results"].values()) == {"ok", "failed"}
    assert service.database.count_pool_candidates() == 1
    assert "未完成" in result["manual_failure_message"]
    assert setup.runtime.memory_manager.state["last_replenished_count"] == 1


@pytest.mark.parametrize("failure", ["evaluation", "copy", "owned_copy"])
async def test_manual_refresh_reports_eval_or_copy_failure_without_duplicate_copy(service, failure):
    setup = _runtime(service)
    if failure == "evaluation":
        setup.pipeline.drain_pending = AsyncMock(return_value={"failed": 1})
    elif failure == "copy":
        setup.recommendations.drain_pending_expression_copy = AsyncMock(
            side_effect=RuntimeError("unavailable")
        )
    else:
        setup.pipeline.drain_pending = AsyncMock(
            return_value=CandidateDrainResult(
                {"cached": 1},
                post_admission_copy=PostAdmissionCopyReceipt(
                    state=PostAdmissionCopyState.CALLBACK_FAILED
                ),
            )
        )
        setup.recommendations.drain_pending_expression_copy = AsyncMock()

    result = await setup.runtime.force_refresh()
    assert "模型服务" in result["manual_failure_message"]
    if failure == "owned_copy":
        setup.recommendations.drain_pending_expression_copy.assert_not_awaited()


async def test_manual_refresh_bounds_inline_eval_even_with_large_batch_floor(service):
    setup = _runtime(service)
    queued = []
    for index in range(5):
        article = item()
        article.content_id = f"pending-{index}"
        article.content_url = f"https://example.com/articles/pending-{index}"
        article.source_id = setup.source["source_id"]
        article.source_name = setup.source["name"]
        queued.append(article)
    setup.pipeline.enqueue_candidates(queued)
    setup.llm.payload = [
        {
            "content_id": article.content_id,
            "score": 0.91,
            "reason": "fit",
            "topic_group": f"topic-{index}",
            "style_key": "deep_dive",
        }
        for index, article in enumerate(queued[:2])
    ]
    service.fetch.return_value = SourceFetchResult(status="empty")
    # A non-coordinator producer must also leave this explicit wave's claims to
    # the controller, avoiding an unbounded first drain followed by another.
    setup.producer.candidate_evaluation_owned_by_coordinator = False

    await setup.runtime.force_refresh()

    assert service.database.count_pool_candidates() == 2
    assert service.database.count_discovery_candidates_by_status()["pending_eval"] == 3
    assert len(setup.llm.calls) == 1
    assert setup.recommendations.pool_copy_calls[0][1] == 2


async def test_failed_instagram_wave_does_not_block_custom_source_refresh(service):
    setup = _runtime(service)
    setup.runtime._manual_instagram_refresh = AsyncMock(side_effect=RuntimeError("copy failed"))
    result = await setup.runtime.force_refresh()

    assert result["refreshed"] is True
    assert "Instagram未完成" in result["manual_failure_message"]
    assert result["producer_results"]["instagram"]["reason"] == "error"
    assert result["producer_results"][setup.source["source_id"]]["enqueued"] == 1
    service.fetch.assert_awaited_once()
    assert service.database.count_pool_candidates() == 1
    state = setup.runtime.memory_manager.load_discovery_runtime_state()
    assert state["last_discovered_count"] == 1
    assert state["last_replenished_count"] == 1
