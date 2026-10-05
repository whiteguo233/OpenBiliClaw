"""Tests for the discovery worker's coordinator status heartbeat."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openbiliclaw.discovery_worker import (
    _coordinator_status_payloads,
    _run_controller_with_status_heartbeat,
)
from openbiliclaw.runtime.worker_status import WorkerStatusStore


class _FakeExpressionCoordinator:
    def status_payload(self) -> dict[str, Any]:
        return {"expression_pending_count": 19, "expression_batch_state": "backoff"}


class _FakeCandidateCoordinator:
    def status_payload(self) -> dict[str, Any]:
        return {"candidate_eval_state": "backoff", "candidate_eval_pending": 10}


class _FailingCoordinator:
    def status_payload(self) -> dict[str, Any]:
        raise RuntimeError("status boom")


class _FakeController:
    def __init__(
        self,
        *,
        expression: Any = None,
        candidate: Any = None,
        run_seconds: float = 0.2,
    ) -> None:
        self.expression_copy_coordinator = expression
        self.candidate_eval_coordinator = candidate
        self._run_seconds = run_seconds

    async def run_forever(self) -> None:
        await asyncio.sleep(self._run_seconds)


def test_coordinator_status_payloads_collects_live_coordinators() -> None:
    controller = _FakeController(
        expression=_FakeExpressionCoordinator(),
        candidate=_FakeCandidateCoordinator(),
    )
    assert _coordinator_status_payloads(controller) == {
        "coordinators": {
            "expression_copy": {
                "expression_pending_count": 19,
                "expression_batch_state": "backoff",
            },
            "candidate_eval": {
                "candidate_eval_state": "backoff",
                "candidate_eval_pending": 10,
            },
        }
    }


def test_coordinator_status_payloads_skips_missing_and_failing() -> None:
    controller = _FakeController(expression=_FailingCoordinator())
    assert _coordinator_status_payloads(controller) == {}
    assert _coordinator_status_payloads(_FakeController()) == {}


@pytest.mark.asyncio
async def test_status_heartbeat_publishes_fresh_coordinator_payloads(tmp_path) -> None:
    store = WorkerStatusStore(tmp_path / "discovery_worker_status.json", max_age_seconds=45.0)
    controller = _FakeController(
        expression=_FakeExpressionCoordinator(),
        candidate=_FakeCandidateCoordinator(),
    )

    await _run_controller_with_status_heartbeat(controller, store)

    data = store.read_if_fresh()
    assert data is not None
    assert data["mode"] == "discovery"
    assert data["pid"] > 0
    assert data["coordinators"]["expression_copy"]["expression_batch_state"] == "backoff"
    assert data["coordinators"]["candidate_eval"]["candidate_eval_pending"] == 10
