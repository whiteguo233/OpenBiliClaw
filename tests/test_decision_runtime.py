"""Shared decision admission budgets, evidence integrity, and owned lifecycle."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from openbiliclaw.config import Config
from openbiliclaw.decision import runtime as runtime_module
from openbiliclaw.decision.models import (
    DecisionEvidence,
    DecisionRequest,
    DecisionUsage,
    NoulAnswer,
    NoulQuestion,
)
from openbiliclaw.decision.runtime import BoundedDecisionProvider, LoopLocalDecisionProvider
from openbiliclaw.discovery.semantic_filter import build_cli_semantic_filter


def _request(key: str = "request", *, budget: float | None = None) -> DecisionRequest:
    return DecisionRequest(
        request_id=key,
        purpose="FILTER",
        question_schema_version="test-filter-v1",
        state={"candidate": "test-data"},
        questions={"avoid": NoulQuestion(instructions="Does this match an explicit dislike?")},
        deadline_monotonic=None if budget is None else time.monotonic() + budget,
    )


class ControlledProvider:
    def __init__(self, actions: list[str] | None = None) -> None:
        self.actions = list(actions or [])
        self.requests: list[DecisionRequest] = []
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.release = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_release: asyncio.Event | None = None
        self.active = 0
        self.max_active = 0
        self.close_calls = 0
        self.active_on_close = -1

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.put_nowait(request.request_id)
        action = self.actions.pop(0) if self.actions else "ok"
        try:
            if action in {"block", "swallow"}:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    if action != "swallow":
                        raise
            if action == "raise":
                raise RuntimeError("secret-transport-message")
            values: dict[str, Any] = {
                "status": "OK",
                "reason_code": "ok",
                "provider": "jev",
                "request_id": request.request_id,
                "purpose": request.purpose,
                "question_schema_version": request.question_schema_version,
                "input_digest": request.input_digest,
                "requested_model": "jev-1.13.0",
                "actual_model": "jev-1.13.0",
                "latency_ms": 0.0,
                "attempts": 1,
                "usage": DecisionUsage(input_tokens=20, output_tokens=1),
                "answers": {"avoid": NoulAnswer(noul=0.2)},
            }
            if action == "fail":
                values.update(status="UNAVAILABLE", reason_code="rate_limited", answers={})
            if action in {
                "request_id",
                "input_digest",
                "question_schema_version",
                "actual_model",
                "requested_model",
            }:
                values[action] = "mismatched"
            if action == "questions":
                values["answers"] = {"other": NoulAnswer(noul=0.2)}
            if action == "mutate":
                request.state["candidate"] = "tampered"  # type: ignore[index]
                values["input_digest"] = request.input_digest
            if action == "invalid":
                values["answers"] = {"avoid": NoulAnswer.model_construct(noul=99.0)}
                return DecisionEvidence.model_construct(**values)
            return DecisionEvidence(**values)
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.close_calls += 1
        self.active_on_close = self.active
        self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()


@pytest.mark.parametrize("field", ["concurrency", "queue_limit", "failure_limit"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_integer_limits_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        BoundedDecisionProvider(ControlledProvider(), model="jev-1.13.0", **{field: value})


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), 10**1000, "1"])
def test_invalid_cooldown_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="cooldown"):
        BoundedDecisionProvider(ControlledProvider(), model="jev-1.13.0", cooldown_seconds=value)


@pytest.mark.parametrize("model", ["", " ", " jev-1.13.0", 1])
def test_invalid_model_rejected(model: Any) -> None:
    with pytest.raises(ValueError, match="model"):
        BoundedDecisionProvider(ControlledProvider(), model=model)


async def test_global_concurrency_and_total_admitted_bound() -> None:
    provider = ControlledProvider(["block"] * 4)
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0", concurrency=2, queue_limit=4)
    tasks = [asyncio.create_task(runtime.evaluate(_request(str(index)))) for index in range(4)]
    await provider.started.get()
    await provider.started.get()
    assert provider.active == 2
    rejected = await runtime.evaluate(_request("overflow"))
    assert rejected.reason_code == "runtime_queue_full"
    provider.release.set()
    assert all(result.status == "OK" for result in await asyncio.gather(*tasks))
    assert len(provider.requests) == 4
    assert provider.max_active == 2
    await runtime.aclose()


async def test_queued_deadline_expires_without_adapter_call_or_breaker_failure() -> None:
    provider = ControlledProvider(["block"])
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0", concurrency=1, failure_limit=1)
    active = asyncio.create_task(runtime.evaluate(_request("active", budget=1)))
    await provider.started.get()
    expired = await runtime.evaluate(_request("queued", budget=0.01))
    assert expired.reason_code == "runtime_deadline_exceeded"
    assert len(provider.requests) == 1
    provider.release.set()
    assert (await active).status == "OK"
    assert (await runtime.evaluate(_request("next"))).status == "OK"
    await runtime.aclose()


async def test_expired_request_never_admitted_and_missing_deadline_is_bounded() -> None:
    provider = ControlledProvider()
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0")
    assert (await runtime.evaluate(_request(budget=-1))).reason_code == "deadline_exceeded"
    assert not provider.requests
    await runtime.evaluate(_request())
    assert provider.requests[0].deadline_monotonic is not None
    assert 0 < provider.requests[0].deadline_monotonic - time.monotonic() <= 10
    await runtime.aclose()


@pytest.mark.parametrize("action", ["fail", "raise", "invalid", "block", "swallow"])
async def test_adapter_failure_and_deadline_open_circuit_and_recover_after_cooldown(
    action: str,
) -> None:
    provider = ControlledProvider([action, "ok"])
    runtime = BoundedDecisionProvider(
        provider, model="jev-1.13.0", failure_limit=1, cooldown_seconds=0.02
    )
    result = await runtime.evaluate(_request(budget=0.01))
    assert result.status != "OK"
    assert "secret" not in result.model_dump_json()
    assert (await runtime.evaluate(_request())).reason_code == "circuit_open"
    assert len(provider.requests) == 1
    await asyncio.sleep(0.03)
    assert (await runtime.evaluate(_request())).status == "OK"
    await runtime.aclose()


async def test_success_resets_consecutive_failure_count() -> None:
    runtime = BoundedDecisionProvider(
        ControlledProvider(["fail", "ok", "fail", "ok"]), model="jev-1.13.0", failure_limit=2
    )
    results = [await runtime.evaluate(_request()) for _ in range(4)]
    assert [result.status for result in results] == ["UNAVAILABLE", "OK", "UNAVAILABLE", "OK"]
    await runtime.aclose()


@pytest.mark.parametrize(
    "action",
    [
        "request_id",
        "input_digest",
        "question_schema_version",
        "actual_model",
        "requested_model",
        "questions",
        "mutate",
        "invalid",
    ],
)
async def test_unbound_or_malformed_evidence_cannot_report_success(action: str) -> None:
    provider = ControlledProvider([action])
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0")
    request = _request()
    result = await runtime.evaluate(request)
    assert result.reason_code == "runtime_invalid_evidence"
    assert result.input_digest == request.input_digest
    assert not result.answers
    assert request.state == {"candidate": "test-data"}
    await runtime.aclose()


@pytest.mark.parametrize("action", ["block", "swallow"])
async def test_external_cancellation_propagates_even_when_adapter_swallows_it(action: str) -> None:
    provider = ControlledProvider([action])
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0", queue_limit=1, failure_limit=1)
    task = asyncio.create_task(runtime.evaluate(_request()))
    await provider.started.get()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await runtime.evaluate(_request())).status == "OK"
    await runtime.aclose()


async def test_queue_cancellation_releases_admission_without_interrupting_active_call() -> None:
    provider = ControlledProvider(["block"])
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0", concurrency=1, queue_limit=2)
    active = asyncio.create_task(runtime.evaluate(_request("active")))
    await provider.started.get()
    queued = asyncio.create_task(runtime.evaluate(_request("queued")))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert provider.active == 1
    provider.release.set()
    assert (await active).status == "OK"
    assert (await runtime.evaluate(_request("replacement"))).status == "OK"
    await runtime.aclose()


@pytest.mark.parametrize("action", ["block", "swallow"])
async def test_close_cancels_active_and_queued_work_before_closing_owned_adapter(
    action: str,
) -> None:
    provider = ControlledProvider([action])
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0", concurrency=1)
    active = asyncio.create_task(runtime.evaluate(_request("active")))
    await provider.started.get()
    queued = asyncio.create_task(runtime.evaluate(_request("queued")))
    await asyncio.sleep(0)
    await asyncio.gather(runtime.aclose(), runtime.aclose())
    assert (await active).reason_code == "runtime_closed"
    assert (await queued).reason_code == "runtime_closed"
    assert (await runtime.evaluate(_request())).reason_code == "runtime_closed"
    assert provider.active_on_close == 0
    assert provider.close_calls == 1
    assert len(provider.requests) == 1


async def test_cancelled_close_waiter_does_not_abandon_or_duplicate_close() -> None:
    provider = ControlledProvider()
    provider.close_release = asyncio.Event()
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0")
    first = asyncio.create_task(runtime.aclose())
    await provider.close_started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(runtime.aclose())
    provider.close_release.set()
    await second
    assert provider.close_calls == 1


async def test_invalid_mutated_request_fails_locally_without_exposing_input() -> None:
    provider = ControlledProvider()
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0")
    request = _request()
    request.state["secret-key"] = float("nan")  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid decision runtime request") as error:
        await runtime.evaluate(request)
    assert "secret-key" not in str(error.value)
    assert not provider.requests
    await runtime.aclose()


def test_cli_off_neither_resolves_credentials_nor_constructs_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not build an off-route provider")

    monkeypatch.setattr(runtime_module, "build_decision_provider", forbidden)
    assert build_cli_semantic_filter(config, None) is None


def test_cli_enabled_missing_credentials_fail_before_transport_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    config.discovery.semantic_filter.mode = "shadow"
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="environment variable"):
        build_cli_semantic_filter(config, None)


def test_cli_transports_created_evaluated_and_closed_inside_each_run_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    created: list[ControlledProvider] = []
    created_loops: list[asyncio.AbstractEventLoop] = []

    def factory(*args: object, **kwargs: object) -> ControlledProvider:
        loop = asyncio.get_running_loop()

        class LoopCheckingProvider(ControlledProvider):
            async def aclose(self) -> None:
                assert asyncio.get_running_loop() is loop
                await super().aclose()

        provider = LoopCheckingProvider()
        created.append(provider)
        created_loops.append(loop)
        return provider

    monkeypatch.setattr(runtime_module, "build_decision_provider", factory)
    provider = LoopLocalDecisionProvider(config.decision)
    assert not created
    for index in range(3):
        assert asyncio.run(provider.evaluate(_request(str(index)))).status == "OK"
    assert len(set(created_loops)) == 3
    assert all(adapter.close_calls == 1 for adapter in created)
    asyncio.run(provider.aclose())
    assert asyncio.run(provider.evaluate(_request())).reason_code == "runtime_closed"
    assert len(created) == 3


async def test_cli_same_loop_shares_concurrency_gate_but_never_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    created: list[ControlledProvider] = []
    started: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()

    def factory(*args: object, **kwargs: object) -> ControlledProvider:
        adapter = ControlledProvider(["block"])
        adapter.started = started
        adapter.release = release
        created.append(adapter)
        return adapter

    monkeypatch.setattr(runtime_module, "build_decision_provider", factory)
    provider = LoopLocalDecisionProvider(config.decision)
    tasks = [asyncio.create_task(provider.evaluate(_request(str(index)))) for index in range(4)]
    await started.get()
    await started.get()
    assert len(created) == 2
    release.set()
    assert all(result.status == "OK" for result in await asyncio.gather(*tasks))
    assert len(created) == 4
    assert all(adapter.close_calls == 1 for adapter in created)
    await provider.aclose()


@pytest.mark.parametrize("action", ["raise", "block", "swallow"])
async def test_cli_per_call_transport_closed_on_exception_timeout_and_cancel(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    adapter = ControlledProvider([action])
    monkeypatch.setattr(runtime_module, "build_decision_provider", lambda *args, **kwargs: adapter)
    provider = LoopLocalDecisionProvider(config.decision)
    assert (await provider.evaluate(_request(budget=0.01))).status == "UNAVAILABLE"
    assert adapter.close_calls == 1
    await provider.aclose()


async def test_cli_external_cancel_closes_transport_without_swallowing_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-key")
    adapter = ControlledProvider(["block"])
    monkeypatch.setattr(runtime_module, "build_decision_provider", lambda *args, **kwargs: adapter)
    provider = LoopLocalDecisionProvider(config.decision)
    task = asyncio.create_task(provider.evaluate(_request()))
    await adapter.started.get()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.close_calls == 1
    await provider.aclose()


async def test_close_has_a_bounded_drain_for_cancellation_ignoring_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubbornProvider(ControlledProvider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            self.started.put_nowait(request.request_id)
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            return await super().evaluate(request)

        async def aclose(self) -> None:
            self.release.set()
            await super().aclose()

    monkeypatch.setattr(runtime_module, "_CLOSE_DRAIN_SECONDS", 0.01)
    provider = StubbornProvider()
    runtime = BoundedDecisionProvider(provider, model="jev-1.13.0")
    task = asyncio.create_task(runtime.evaluate(_request()))
    await provider.started.get()
    async with asyncio.timeout(1):
        await runtime.aclose()
        assert (await task).reason_code == "runtime_closed"
    assert provider.close_calls == 1


def test_cli_real_adapter_mock_transport_is_closed_in_its_creating_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.decision import jev_provider as adapter_module

    config = Config()
    config.decision.providers.jev.enabled = True
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-mock-key")
    clients: list[httpx.AsyncClient] = []
    loops: list[asyncio.AbstractEventLoop] = []
    original_client = httpx.AsyncClient

    class TrackingClient(original_client):
        def __init__(self, **kwargs: Any) -> None:
            self.owner_loop = asyncio.get_running_loop()
            loops.append(self.owner_loop)
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "model": "jev-1.13.0",
                        "answers": {"avoid": {"type": "noul", "noul": 0.1}},
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                )
            )
            super().__init__(**kwargs)
            clients.append(self)

        async def aclose(self) -> None:
            assert asyncio.get_running_loop() is self.owner_loop
            await super().aclose()

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", TrackingClient)
    provider = LoopLocalDecisionProvider(config.decision)
    for index in range(3):
        assert asyncio.run(provider.evaluate(_request(str(index)))).status == "OK"
    assert len(set(loops)) == len(clients) == 3
    assert all(client.is_closed for client in clients)
    asyncio.run(provider.aclose())
