"""Bounded runtime admission shared by all consumers of one decision provider."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from typing import TYPE_CHECKING

from .models import DecisionEvidence, DecisionRequest, parse_jev_response
from .registry import build_decision_provider, validate_decision_provider_credentials

if TYPE_CHECKING:
    from openbiliclaw.config import DecisionConfig

    from .base import DecisionProvider

# Engineering defaults, not measured throughput, semantic confidence, or SLA.
_DEFAULT_DEADLINE_SECONDS = 10.0
_CLOSE_DRAIN_SECONDS = 2.0
_MODEL_ALIASES = frozenset({"jev-latest", "jev-preview"})


class BoundedDecisionProvider:
    """Own an adapter, a deadline-aware admission gate, and a small circuit breaker.

    One runtime shares this instance between filters and screen decisions.
    ``queue_limit`` bounds ALL admitted calls (active plus waiting), while
    ``concurrency`` bounds adapter calls. Queue expiry and caller cancellation
    do not count as provider failures. The breaker counts consecutive completed
    adapter failures, including thrown errors, invalid evidence, and overruns.

    Lifecycle is event-loop-local. Close prevents new admissions, cancels owned
    work, drains cooperative adapters, and closes the adapter exactly once.
    """

    def __init__(
        self,
        provider: DecisionProvider,
        *,
        model: str,
        concurrency: int = 2,
        queue_limit: int = 32,
        failure_limit: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if any(
            type(value) is not int or value < 1
            for value in (concurrency, queue_limit, failure_limit)
        ):
            raise ValueError("decision runtime limits must be positive integers")
        if (
            not isinstance(cooldown_seconds, (float, int))
            or isinstance(cooldown_seconds, bool)
            or cooldown_seconds <= 0
            or cooldown_seconds > 86_400
            or not math.isfinite(cooldown_seconds)
        ):
            raise ValueError("decision runtime cooldown must be finite and within (0, 86400]")
        if not isinstance(model, str) or not model.strip() or model != model.strip():
            raise ValueError("decision runtime requires a nonempty model")
        self._provider = provider
        self._model = model
        self._gate = asyncio.Semaphore(concurrency)
        self._queue_limit = queue_limit
        self._pending = 0
        self._failure_limit = failure_limit
        self._cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._blocked_until = 0.0
        self._closed = False
        self._tasks: set[asyncio.Task[DecisionEvidence]] = set()
        self._close_task: asyncio.Task[None] | None = None

    def _unavailable(
        self,
        request: DecisionRequest,
        reason: str,
        *,
        started: float | None = None,
    ) -> DecisionEvidence:
        return DecisionEvidence(
            status="UNAVAILABLE",
            reason_code=reason,
            request_id=request.request_id,
            purpose=request.purpose,
            question_schema_version=request.question_schema_version,
            input_digest=request.input_digest,
            requested_model=self._model,
            latency_ms=max(0.0, time.monotonic() - started) * 1000 if started is not None else 0.0,
            attempts=0,
        )

    def _validate_evidence(
        self,
        result: object,
        expected: DecisionRequest,
    ) -> DecisionEvidence:
        """Detach any adapter's result and validate binding before breaker accounting."""
        try:
            if not isinstance(result, DecisionEvidence):
                raise ValueError("not decision evidence")
            evidence = DecisionEvidence.model_validate(
                result.model_dump(mode="python", warnings="error")
            )
            if (
                evidence.request_id != expected.request_id
                or evidence.purpose != expected.purpose
                or evidence.question_schema_version != expected.question_schema_version
                or evidence.input_digest != expected.input_digest
                or evidence.requested_model != self._model
            ):
                raise ValueError("evidence binding mismatch")
            if evidence.status == "OK":
                if self._model not in _MODEL_ALIASES and evidence.actual_model != self._model:
                    raise ValueError("model mismatch")
                parse_jev_response(
                    {
                        "model": evidence.actual_model,
                        "answers": {
                            key: answer.model_dump(mode="python")
                            for key, answer in evidence.answers.items()
                        },
                        "usage": evidence.usage.model_dump(mode="python")
                        if evidence.usage
                        else None,
                    },
                    expected,
                )
            return evidence
        except Exception:
            return self._unavailable(expected, "runtime_invalid_evidence")

    def _record_result(self, result: DecisionEvidence) -> None:
        if result.status == "OK":
            self._failures = 0
        else:
            self._failures += 1
            if self._failures >= self._failure_limit:
                self._blocked_until = time.monotonic() + self._cooldown_seconds
                self._failures = 0

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        """Admit one call; its absolute deadline covers both queueing and adapter I/O."""
        try:
            expected = DecisionRequest.model_validate(
                request.model_dump(mode="python", warnings="error")
            )
        except (ValueError, TypeError, OverflowError):
            raise ValueError("invalid decision runtime request") from None
        started = time.monotonic()
        if self._closed:
            return self._unavailable(expected, "runtime_closed")
        if started < self._blocked_until:
            return self._unavailable(expected, "circuit_open")
        deadline = expected.deadline_monotonic
        if deadline is None:
            deadline = started + _DEFAULT_DEADLINE_SECONDS
            expected = expected.model_copy(update={"deadline_monotonic": deadline})
        if deadline <= started:
            return self._unavailable(expected, "deadline_exceeded")
        if self._pending >= self._queue_limit:
            return self._unavailable(expected, "runtime_queue_full")
        caller = asyncio.current_task()
        cancellation_count = caller.cancelling() if caller is not None else 0
        provider_started = False

        async def run() -> DecisionEvidence:
            nonlocal provider_started
            async with self._gate:
                if self._closed:
                    return self._unavailable(expected, "runtime_closed")
                if time.monotonic() >= deadline:
                    return self._unavailable(expected, "runtime_deadline_exceeded", started=started)
                if time.monotonic() < self._blocked_until:
                    return self._unavailable(expected, "circuit_open")
                # A faulty adapter must not mutate the binding used for validation.
                adapter_request = expected.model_copy(deep=True)
                provider_started = True
                try:
                    raw_result = await self._provider.evaluate(adapter_request)
                except Exception:
                    return self._unavailable(expected, "runtime_provider_error", started=started)
                return self._validate_evidence(raw_result, expected)

        self._pending += 1
        work = asyncio.create_task(run(), name="decision-runtime-call")
        self._tasks.add(work)
        try:
            async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                result = await work
            # An adapter may catch CancelledError and return fabricated success.
            if caller is not None and caller.cancelling() > cancellation_count:
                raise asyncio.CancelledError
            if self._closed:
                return self._unavailable(expected, "runtime_closed", started=started)
            if time.monotonic() >= deadline:
                result = self._unavailable(expected, "runtime_deadline_exceeded", started=started)
        except TimeoutError:
            result = self._unavailable(expected, "runtime_deadline_exceeded", started=started)
        except asyncio.CancelledError:
            if caller is not None and caller.cancelling() > cancellation_count:
                raise
            if self._closed:
                return self._unavailable(expected, "runtime_closed", started=started)
            raise
        finally:
            self._tasks.discard(work)
            self._pending -= 1
        if provider_started:
            self._record_result(result)
        return result

    @staticmethod
    def _consume_finished(task: asyncio.Task[DecisionEvidence]) -> None:
        if not task.cancelled():
            task.exception()

    async def _close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            # All built-in adapters are cooperative. This ceiling prevents a
            # third-party adapter that ignores cancellation from blocking shutdown.
            done, pending = await asyncio.wait(tasks, timeout=_CLOSE_DRAIN_SECONDS)
            for task in done:
                self._consume_finished(task)
            for task in pending:
                task.cancel()
                task.add_done_callback(self._consume_finished)
        await self._provider.aclose()

    async def aclose(self) -> None:
        """Stop admissions and share one shielded close, even if a waiter is cancelled."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close(), name="decision-runtime-close")
        await asyncio.shield(self._close_task)


class _PerCallAdapter:
    """Short-lived transport ownership for CLI commands that run multiple loops."""

    def __init__(self, config: DecisionConfig) -> None:
        self.config = config

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        adapter = build_decision_provider(self.config)
        if adapter is None:
            raise ValueError("enabled CLI decision route has no provider")
        try:
            return await adapter.evaluate(request)
        finally:
            # A second cancellation must not strand a live HTTP connection.
            closer = asyncio.create_task(adapter.aclose())
            try:
                await asyncio.shield(closer)
            except asyncio.CancelledError:
                closer.add_done_callback(
                    lambda task: None if task.cancelled() else task.exception()
                )
                raise

    async def aclose(self) -> None:
        """Every request has already closed its own transport."""


class LoopLocalDecisionProvider:
    """CLI-only shared gates with no transport reused across ``asyncio.run`` loops.

    Construction checks enabled credentials but performs no transport work.
    Each event loop gets a bounded gate; each admitted call owns and closes one
    adapter. This wrapper is for sequential CLI loops, not cross-thread runtimes.
    Daemons should use one ordinary BoundedDecisionProvider instead.
    """

    def __init__(self, config: DecisionConfig) -> None:
        self._config = copy.deepcopy(config)
        validate_decision_provider_credentials(self._config)
        self._runtimes: dict[asyncio.AbstractEventLoop, BoundedDecisionProvider] = {}
        self._closed = False

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        loop = asyncio.get_running_loop()
        for old_loop in tuple(self._runtimes):
            if old_loop.is_closed():
                del self._runtimes[old_loop]
        runtime = self._runtimes.get(loop)
        if runtime is None:
            runtime = BoundedDecisionProvider(
                _PerCallAdapter(self._config),
                model=self._config.providers.jev.model,
            )
            self._runtimes[loop] = runtime
        if self._closed:
            await runtime.aclose()
        return await runtime.evaluate(request)

    async def aclose(self) -> None:
        """Close the current loop's work; completed older loops retain no transports."""
        self._closed = True
        loop = asyncio.get_running_loop()
        runtime = self._runtimes.get(loop)
        if runtime is not None:
            await runtime.aclose()
