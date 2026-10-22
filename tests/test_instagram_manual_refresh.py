"""The ordinary replenishment entry must actually run Instagram discovery."""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.runtime.refresh import ContinuousRefreshController
from tests.test_refresh_runtime import (
    _FakeDatabase,
    _FakeDiscoveryEngine,
    _FakeEventHub,
    _FakeMemoryManager,
    _FakeRecommendationEngine,
    _FakeSoulEngine,
)


class Producer:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result or {"discovered": 2, "enqueued": 2, "reason": "ok"}

    async def produce_if_due(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.result


def controller(producer: Producer, **kwargs: Any) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        instagram_producer=producer,
        event_hub=_FakeEventHub(),
        pool_source_shares={"instagram": 1},
        pool_target_count=2,
        **kwargs,
    )


async def test_ordinary_refresh_dispatches_instagram_without_bilibili() -> None:
    producer = Producer()
    runtime = controller(producer)
    result = await runtime.force_refresh()
    assert producer.calls == [{"limit": 2, "force": True}]
    assert runtime.discovery_engine.calls == []
    assert result["producer_results"]["instagram"]["enqueued"] == 2


@pytest.mark.parametrize("reason", ["response_envelope_unobserved", "rate_limited", "timeout"])
async def test_failed_instagram_manual_refresh_is_not_noop_success(reason: str) -> None:
    runtime = controller(Producer({"discovered": 0, "enqueued": 0, "reason": reason}))
    await runtime._complete_manual_refresh()
    status = runtime.get_runtime_status()
    assert status["manual_refresh_state"] == "failed"
    assert "Instagram" in status["manual_refresh_message"]
    assert runtime.event_hub.events[-1]["type"] == "refresh.failed"


async def test_manual_refresh_evaluates_and_prepares_copy_when_scheduler_off() -> None:
    calls: list[str] = []

    class Pipeline:
        async def drain_pending(self, **kwargs: Any) -> dict[str, int]:
            assert kwargs["flush"] is True
            assert kwargs["batch_size"] == 2
            calls.append("eval")
            return {"evaluated": 2, "cached": 1}

    runtime = controller(Producer(), discovery_candidate_pipeline=Pipeline())

    async def copy(**kwargs: Any) -> int:
        calls.append("copy")
        runtime.database.pool_count = 1
        return 1

    runtime.recommendation_engine.drain_pending_expression_copy = copy
    result = await runtime.force_refresh()
    assert calls == ["eval", "copy"]
    assert result["refreshed"] is True
    assert runtime.memory_manager.state["last_replenished_count"] == 1


async def test_busy_manual_refresh_does_not_dispatch_instagram() -> None:
    producer = Producer()
    runtime = controller(producer)
    async with runtime._refresh_lock:
        result = await runtime.force_refresh()
    assert result["refreshed"] is False
    assert producer.calls == []


async def test_topic_fills_manual_limit_without_false_creator_failure() -> None:
    runtime = controller(
        Producer(
            {
                "discovered": 2,
                "enqueued": 2,
                "reason": "ok",
                "mode_results": {"topic": "partial", "creator": "limit_reached"},
            }
        )
    )
    await runtime._complete_manual_refresh()
    assert runtime.get_runtime_status()["manual_refresh_state"] == "success"


async def test_manual_eval_failure_is_not_reported_as_success() -> None:
    class Pipeline:
        async def drain_pending(self, **kwargs: Any) -> dict[str, int]:
            return {"evaluated": 0, "cached": 0, "failed": 2}

    runtime = controller(Producer(), discovery_candidate_pipeline=Pipeline())
    await runtime._complete_manual_refresh()
    status = runtime.get_runtime_status()
    assert status["manual_refresh_state"] == "failed"
    assert "模型" in status["manual_refresh_message"]


async def test_failed_copy_owner_is_not_invoked_twice_in_one_manual_wave() -> None:
    from openbiliclaw.discovery.candidate_pipeline import (
        CandidateDrainResult,
        PostAdmissionCopyReceipt,
        PostAdmissionCopyState,
    )

    class Pipeline:
        async def drain_pending(self, **kwargs: Any) -> CandidateDrainResult:
            return CandidateDrainResult(
                {"evaluated": 2, "cached": 1},
                post_admission_copy=PostAdmissionCopyReceipt(
                    state=PostAdmissionCopyState.CALLBACK_FAILED
                ),
            )

    runtime = controller(Producer(), discovery_candidate_pipeline=Pipeline())

    async def copy(**kwargs: Any) -> int:
        pytest.fail("The failed callback already owns this wave; leave pending rows for retry")

    runtime.recommendation_engine.drain_pending_expression_copy = copy
    await runtime._complete_manual_refresh()
    assert runtime.get_runtime_status()["manual_refresh_state"] == "failed"
    assert "文案" in runtime.get_runtime_status()["manual_refresh_message"]
