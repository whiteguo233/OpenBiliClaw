"""Stable execution lease and single worker for durable dialogue replies."""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.base import classify_llm_failure_kind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)


class TerminalChatReplyError(Exception):
    """A safe, explicit terminal reply failure that may end a durable turn."""

    def __init__(self, safe_message: str, *, code: str = "terminal_reply") -> None:
        super().__init__(code)
        self.safe_message = safe_message
        self.code = code


class DialogueLeaseTimeoutError(TimeoutError):
    """The dialogue execution lease was not admitted within the budget.

    Raised when a caller passes ``timeout`` to
    :meth:`DialogueExecutionCoordinator.lease` and the lane stays paused
    (config hot reload) or busy past the deadline. ``safe_message`` is
    user-displayable.
    """

    def __init__(self, safe_message: str = "系统正在重载配置，请稍后再试。") -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


async def _bounded_condition_wait(
    condition: asyncio.Condition,
    predicate: Callable[[], bool],
    timeout: float,
) -> bool:
    """``condition.wait_for`` with an overall deadline; False on timeout.

    Assumes the caller already holds ``condition``. The predicate is
    re-evaluated once after a timeout so a notification landing exactly at
    the deadline still counts as admitted.
    """
    try:
        await asyncio.wait_for(condition.wait_for(predicate), timeout=max(0.0, float(timeout)))
    except TimeoutError:
        return predicate()
    return True


@dataclass
class DialogueExecutionCoordinator:
    """Serialize every production dialogue execution across runtime swaps.

    The coordinator is app-owned rather than RuntimeContext-owned. Callers
    resolve ``ctx.dialogue`` only *after* acquiring :meth:`lease`, so a request
    queued while hot reload is paused observes the newly published runtime.
    """

    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, init=False)
    _paused: bool = field(default=False, init=False)
    _active: bool = field(default=False, init=False)

    @property
    def active(self) -> bool:
        """Whether one dialogue execution currently owns the stable lease."""
        return self._active

    @property
    def paused(self) -> bool:
        """Whether new lease admission is paused for runtime handoff."""
        return self._paused

    @asynccontextmanager
    async def lease(self, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Wait for admission and hold the process-wide dialogue write lease.

        With ``timeout`` (seconds), admission is bounded: when the lane
        stays paused (hot reload) or busy past the deadline a
        :class:`DialogueLeaseTimeoutError` is raised instead of waiting
        indefinitely. ``None`` (default) preserves the unbounded wait used
        by the durable worker, which retries with its own backoff.
        """
        async with self._condition:
            if timeout is None:
                await self._condition.wait_for(lambda: not self._paused and not self._active)
            else:
                admitted = await _bounded_condition_wait(
                    self._condition,
                    lambda: not self._paused and not self._active,
                    timeout,
                )
                if not admitted:
                    message = (
                        "系统正在重载配置，请稍后再试。"
                        if self._paused
                        else "对话通道正忙，请稍后查看回复。"
                    )
                    raise DialogueLeaseTimeoutError(message)
            self._active = True
        try:
            yield
        finally:
            async with self._condition:
                self._active = False
                self._condition.notify_all()

    async def pause_and_drain(self, *, timeout: float) -> None:
        """Pause new admission and wait for the current lease owner to finish.

        Timeout resumes the old lane before propagating, which prevents callers
        from publishing a new RuntimeContext while an old dialogue can still
        write history or post-reply side effects.
        """
        async with self._condition:
            self._paused = True
            self._condition.notify_all()

        async def _wait_inactive() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: not self._active)

        try:
            await asyncio.wait_for(_wait_inactive(), timeout=max(0.01, float(timeout)))
        except BaseException:
            await self.resume()
            raise

    async def resume(self) -> None:
        """Resume queued executions against the resolver's current runtime."""
        async with self._condition:
            self._paused = False
            self._condition.notify_all()


@dataclass
class DurableChatReplyScheduler:
    """Recover and process pending chat turns with one durable reply worker.

    Queue membership is only a wake hint. ``chat_turns.status='pending'`` is
    authoritative, so cancellation and process shutdown leave work recoverable.
    Transient failures retry with bounded exponential backoff; only an explicit
    :class:`TerminalChatReplyError` may CAS a turn to ``failed``.
    """

    processor: Callable[[str], Awaitable[None]]
    database_resolver: Callable[[], Any]
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    recovery_batch_size: int = 500
    _queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue, init=False)
    _scheduled: set[str] = field(default_factory=set, init=False)
    _attempts: dict[str, int] = field(default_factory=dict, init=False)
    _worker: asyncio.Task[None] | None = field(default=None, init=False)
    _active: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)
    _last_error: str = field(default="", init=False)
    _processed: int = field(default=0, init=False)

    async def start(self) -> None:
        """Recover every pending row in pages, then start the single worker."""
        if self._closed:
            return
        database = self.database_resolver()
        page = getattr(database, "list_pending_chat_turn_page", None)
        if callable(page):
            after_rowid = 0
            batch_size = max(1, int(self.recovery_batch_size))
            while True:
                rows = list(page(after_rowid=after_rowid, limit=batch_size))
                if not rows:
                    break
                for row in rows:
                    after_rowid = max(after_rowid, int(row.get("rowid", 0) or 0))
                    self._enqueue(str(row.get("turn_id", "")))
                if len(rows) < batch_size:
                    break
        else:
            legacy = getattr(database, "list_pending_chat_turn_ids", None)
            if callable(legacy):
                try:
                    pending_ids = legacy(limit=None)
                except TypeError:
                    # Compatibility doubles may retain the old bounded shape;
                    # their rows are not the production durable store.
                    pending_ids = legacy(limit=1000)
                for turn_id in pending_ids:
                    self._enqueue(str(turn_id))
        self._ensure_worker()

    def schedule(self, turn_id: str) -> bool:
        """Wake one durable turn, deduplicating queued/in-flight/backoff work."""
        if self._closed or not self._enqueue(turn_id):
            return False
        self._ensure_worker()
        return True

    async def close(self) -> None:
        """Cancel in-memory work while deliberately retaining pending DB rows."""
        if self._closed:
            return
        self._closed = True
        tasks: list[asyncio.Task[None]] = []
        if self._worker is not None and not self._worker.done():
            tasks.append(self._worker)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._scheduled.clear()
        self._worker = None
        self._active = False

    async def wait_idle(self, *, timeout: float = 5.0) -> None:
        """Wait until no queued, active, or delayed retry remains (tests/teardown)."""

        async def _wait() -> None:
            while self._scheduled or self._active:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(_wait(), timeout=max(0.01, float(timeout)))

    def status_payload(self) -> dict[str, object]:
        """Return non-sensitive lane diagnostics; depth is durable pending rows."""
        depth = len(self._scheduled)
        try:
            count_pending = getattr(self.database_resolver(), "count_pending_chat_turns", None)
            if callable(count_pending):
                depth = max(0, int(count_pending()))
        except Exception:
            logger.debug("chat reply pending-depth read failed", exc_info=True)
        return {
            "chat_reply_depth": depth,
            "chat_reply_active": self._active,
            "chat_reply_last_error": self._last_error,
            "chat_reply_processed": self._processed,
        }

    def _enqueue(self, turn_id: str) -> bool:
        normalized = str(turn_id or "").strip()
        if not normalized or normalized in self._scheduled:
            return False
        self._scheduled.add(normalized)
        self._queue.put_nowait(normalized)
        return True

    def _ensure_worker(self) -> None:
        if self._closed or (self._worker is not None and not self._worker.done()):
            return
        try:
            self._worker = asyncio.create_task(
                self._worker_loop(),
                name="durable-chat-reply",
            )
        except RuntimeError:
            # Startup will create the worker once an event loop exists.
            self._worker = None

    async def _worker_loop(self) -> None:
        while not self._closed:
            turn_id = await self._queue.get()
            self._active = True
            try:
                while not self._closed:
                    try:
                        await self.processor(turn_id)
                    except asyncio.CancelledError:
                        raise
                    except TerminalChatReplyError as exc:
                        try:
                            self._fail_terminal(turn_id, exc)
                        except Exception as write_exc:
                            self._last_error = self._error_code(write_exc)
                            logger.exception("terminal chat reply CAS failed for %s", turn_id)
                            await self._backoff(turn_id)
                            continue
                        self._finish(turn_id, error_code=exc.code)
                        break
                    except Exception as exc:
                        self._last_error = self._error_code(exc)
                        logger.warning(
                            "durable chat reply deferred turn_id=%s kind=%s",
                            turn_id,
                            self._last_error,
                        )
                        # Strict durable order: the oldest pending turn retries
                        # in place after bounded backoff. Later rowids may not
                        # overtake shared SocraticDialogue history.
                        await self._backoff(turn_id)
                        continue
                    self._finish(turn_id)
                    break
            finally:
                self._active = False
                self._queue.task_done()

    def _fail_terminal(self, turn_id: str, exc: TerminalChatReplyError) -> None:
        database = self.database_resolver()
        fail = getattr(database, "fail_chat_turn", None)
        if not callable(fail):
            raise RuntimeError("chat turn terminal CAS is unavailable")
        changed = fail(turn_id, error=exc.safe_message, reply="")
        if changed is False:
            row = getattr(database, "get_chat_turn", lambda _turn_id: None)(turn_id)
            if isinstance(row, dict) and str(row.get("status", "")) == "pending":
                raise RuntimeError("chat turn terminal CAS did not update pending row")

    def _finish(self, turn_id: str, *, error_code: str = "") -> None:
        self._attempts.pop(turn_id, None)
        self._scheduled.discard(turn_id)
        self._processed += 1
        self._last_error = error_code

    async def _backoff(self, turn_id: str) -> None:
        """Sleep in place so a later durable row can never overtake this turn."""
        attempt = self._attempts.get(turn_id, 0) + 1
        self._attempts[turn_id] = attempt
        await asyncio.sleep(self._retry_delay(attempt))

    def _retry_delay(self, attempt: int) -> float:
        """Return capped exponential delay without unbounded exponentiation."""
        base = max(0.01, float(self.retry_base_seconds))
        maximum = max(0.01, float(self.retry_max_seconds))
        if base >= maximum:
            return maximum
        maximum_exponent = max(0, math.ceil(math.log2(maximum / base)))
        exponent = min(max(0, int(attempt) - 1), maximum_exponent)
        scaled = base * math.pow(2.0, float(exponent))
        return float(min(maximum, scaled))

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        return classify_llm_failure_kind(exc) or type(exc).__name__
