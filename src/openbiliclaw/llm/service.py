"""Shared service facade for prompt assembly and LLM execution."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Protocol

from openbiliclaw.soul.profile import SoulProfile, preference_layer_from_dict
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile

from .base import LLMProviderError, LLMRateLimitError
from .prompts import build_socratic_dialogue_prompt

logger = logging.getLogger(__name__)
DEFAULT_LLM_CONCURRENCY = 3

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from openbiliclaw.memory.manager import MemoryManager

    from .base import LLMResponse


class SupportsComplete(Protocol):
    """Protocol for providers or registries with a complete method."""

    @property
    def default_provider(self) -> str: ...

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse: ...

    async def complete_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse: ...

    def is_chat_capable(self, name: str) -> bool: ...


class LLMServiceError(Exception):
    """Base exception for service-layer LLM errors."""


class LLMResponseContentError(LLMServiceError):
    """Raised when an LLM call returns empty content."""


class LLMProviderExecutionError(LLMServiceError):
    """Raised when the underlying provider or registry call fails."""


_RATE_LIMIT_ERROR_MARKERS = (
    "rate limit",
    "429",
    "cooling down",
    "too many requests",
    "resource exhausted",
    "quota exceeded",
)


def is_llm_rate_limit_error(exc: BaseException) -> bool:
    """Return True when an exception chain represents provider backoff.

    Batch callers use this to avoid exploding one provider-limit event
    into N doomed per-item calls while the registry is already cooling
    down.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMRateLimitError):
            return True
        message = str(current).lower()
        if any(marker in message for marker in _RATE_LIMIT_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass(frozen=True)
class ModuleOverride:
    """Per-module LLM route override."""

    provider: str = ""
    model: str = ""


_MODULE_OVERRIDE_BUCKETS = ("soul", "discovery", "recommendation", "evaluation")


def module_overrides_from_config(config: object) -> dict[str, ModuleOverride]:
    """Build normalized module LLM overrides from ``Config.llm`` blocks."""
    llm_config = getattr(config, "llm", None)
    if llm_config is None:
        return {}

    overrides: dict[str, ModuleOverride] = {}
    for bucket in _MODULE_OVERRIDE_BUCKETS:
        raw = getattr(llm_config, bucket, None)
        if raw is None:
            continue
        provider = str(getattr(raw, "provider", "") or "").strip().lower()
        model = str(getattr(raw, "model", "") or "").strip()
        if provider or model:
            overrides[bucket] = ModuleOverride(provider=provider, model=model)
    return overrides


class PrioritySemaphore:
    """Asyncio semaphore that serves waiters in priority order.

    Lower priority numbers go first (1 = highest). Within the same
    priority bucket, FIFO is preserved via a monotonically increasing
    sequence counter. The semaphore takes effect only when there is
    contention — if the slot is free the caller acquires immediately.

    Concurrency is bounded by ``capacity``: only ``capacity`` callers
    may hold a slot at once.
    """

    def __init__(self, capacity: int = 1) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._in_flight = 0
        # Heap entries: (priority, sequence, future). The sequence
        # counter breaks ties so the heap stays FIFO within a bucket.
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    async def acquire(self, priority: int) -> None:
        if self._in_flight < self._capacity and not self._waiters:
            self._in_flight += 1
            return
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        heapq.heappush(self._waiters, (priority, next(self._counter), fut))
        try:
            await fut
        except asyncio.CancelledError:
            # Drop ourselves from the heap if we hadn't been woken yet.
            self._waiters = [entry for entry in self._waiters if entry[2] is not fut]
            heapq.heapify(self._waiters)
            # If the slot was already handed to us before the cancel
            # propagated, hand it on to the next waiter so the queue
            # doesn't deadlock.
            if fut.done() and not fut.cancelled():
                self._release_one()
            raise

    def release(self) -> None:
        if self._in_flight <= 0:
            raise RuntimeError("PrioritySemaphore released too many times")
        self._release_one()

    def _release_one(self) -> None:
        # Hand the slot to the highest-priority waiter, or just decrement
        # the in-flight count if no one is waiting.
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done():
                fut.set_result(None)
                return
        self._in_flight = max(0, self._in_flight - 1)

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()


def _coerce_concurrency(value: object) -> int:
    """Return a positive LLM concurrency value, falling back to the default."""
    if isinstance(value, bool):
        return DEFAULT_LLM_CONCURRENCY
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return DEFAULT_LLM_CONCURRENCY
    else:
        return DEFAULT_LLM_CONCURRENCY
    return normalized if normalized >= 1 else DEFAULT_LLM_CONCURRENCY


def _build_priority_semaphore(capacity: int = DEFAULT_LLM_CONCURRENCY) -> PrioritySemaphore:
    return PrioritySemaphore(capacity=_coerce_concurrency(capacity))


@dataclass
class LLMService:
    """Facade that assembles prompts and delegates calls to the registry."""

    # v0.3.63+: caller-tag → priority map. Lower number wins. Resolved
    # by longest-prefix match against the ``caller`` tag passed to
    # ``complete_with_core_memory``. Untagged or unmatched callers fall
    # through to ``_DEFAULT_PRIORITY``. The intent: when the system is
    # under load, popup-visible work (write_expression, evaluate_batch
    # for the active discovery batch) gets the next LLM slot before
    # background bulk scoring (delight_score) or cold-path soul/xhs
    # analysis. Without this, a long delight-scoring sweep could starve
    # the user-visible expression backfill for minutes.
    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "recommendation.write_expression": 1,
        "discovery.evaluate_batch": 1,
        "recommendation.delight_score": 2,
        "soul": 2,
        "xhs": 2,
    }
    _DEFAULT_PRIORITY: ClassVar[int] = 3
    _ROUTE_BUCKET_PREFIXES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("recommendation.delight_score", "evaluation"),
        ("recommendation.evaluate_batch", "evaluation"),
        ("discovery.evaluate", "evaluation"),
        ("discovery.eval", "evaluation"),
        ("eval", "evaluation"),
        ("discovery.search", "discovery"),
        ("discovery.explore", "discovery"),
        ("discovery.trending", "discovery"),
        ("discovery.related", "discovery"),
        ("yt_search", "discovery"),
        ("sources.xhs", "discovery"),
        ("recommendation", "recommendation"),
        ("soul", "soul"),
    )

    registry: SupportsComplete
    memory: MemoryManager
    # v0.3.26+: optional usage ledger sink. When supplied, every
    # successful LLM response is written to the ``llm_usage`` table so
    # ``openbiliclaw cost`` can report daily spend. Default None
    # preserves prior behaviour for tests / standalone callers that
    # don't care about cost tracking.
    usage_recorder: object | None = None
    module_overrides: Mapping[str, ModuleOverride] = field(default_factory=dict)
    concurrency: int = DEFAULT_LLM_CONCURRENCY
    # v0.3.63+: lazy-initialised priority gate. ``init=False`` keeps the
    # semaphore private while ``concurrency`` remains configurable.
    _priority_sem: PrioritySemaphore = field(init=False, repr=False)
    _logged_unknown_override_keys: set[tuple[str, str]] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.concurrency = _coerce_concurrency(self.concurrency)
        self._priority_sem = _build_priority_semaphore(self.concurrency)

    @classmethod
    def _resolve_priority(cls, caller: str) -> int:
        """Longest-prefix match of ``caller`` against ``_PRIORITY_MAP``.

        ``"recommendation.write_expression"`` matches exactly, while
        ``"soul.preference"`` matches the ``"soul"`` prefix. Unknown
        callers (or empty tag) fall through to ``_DEFAULT_PRIORITY``.
        """
        if not caller:
            return cls._DEFAULT_PRIORITY
        best: tuple[int, int] | None = None  # (prefix length, priority)
        for prefix, priority in cls._PRIORITY_MAP.items():
            if caller == prefix or caller.startswith(prefix + "."):
                length = len(prefix)
                if best is None or length > best[0]:
                    best = (length, priority)
        return best[1] if best is not None else cls._DEFAULT_PRIORITY

    @classmethod
    def _route_bucket_for_caller(cls, caller: str) -> str | None:
        """Map a concrete caller tag to a module override bucket."""
        tag = caller.strip()
        if not tag:
            return None
        for prefix, bucket in cls._ROUTE_BUCKET_PREFIXES:
            if cls._caller_matches_route_prefix(tag, prefix):
                return bucket
        return None

    @staticmethod
    def _caller_matches_route_prefix(caller: str, prefix: str) -> bool:
        return (
            caller == prefix or caller.startswith(prefix + ".") or caller.startswith(prefix + "_")
        )

    def _resolve_module_override(self, caller: str) -> tuple[str, str | None] | None:
        bucket = self._route_bucket_for_caller(caller)
        if bucket is None:
            return None
        override = self.module_overrides.get(bucket)
        if override is None:
            return None

        provider = override.provider.strip().lower()
        model = override.model.strip()
        if not provider and not model:
            return None
        if not provider:
            provider = self.registry.default_provider.strip().lower()
        if not provider:
            return None

        if not self.registry.is_chat_capable(provider):
            log_key = (bucket, provider)
            if log_key not in self._logged_unknown_override_keys:
                self._logged_unknown_override_keys.add(log_key)
                logger.info(
                    "LLM module override ignored: bucket=%s provider=%s "
                    "is not registered or chat-capable; using default provider.",
                    bucket,
                    provider,
                )
            return None
        return provider, model or None

    async def complete_with_core_memory(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Execute a task with automatically injected core memory context.

        ``caller`` is an optional free-form tag (e.g. ``"soul.preference"``,
        ``"discovery.eval"``) attached to the usage row so the ``cost``
        report can break spend down by module.

        ``reasoning_effort`` (v0.3.51+) lets a caller force-disable the
        provider's thinking mode for tasks that don't benefit from it
        (structured eval / classify / write-expression). ``None`` keeps
        the provider default; ``""`` explicitly disables for this call.
        """
        core_memory_block = ""
        if self.memory is not None:
            with suppress(Exception):
                core_memory_block = self.memory.render_core_memory_prompt()
        parts = [system_instruction.strip()]
        if core_memory_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(core_memory_block)
        system_content = "\n\n".join(parts)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        priority = self._resolve_priority(caller)
        try:
            async with self._priority_sem.slot(priority):
                routed = self._resolve_module_override(caller)
                if routed is None:
                    response = await self.registry.complete(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                        reasoning_effort=reasoning_effort,
                    )
                else:
                    provider, model = routed
                    response = await self.registry.complete_provider(
                        provider,
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_mode=json_mode,
                        reasoning_effort=reasoning_effort,
                        model=model,
                    )
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        # Best-effort usage ledger write. The recorder swallows its own
        # exceptions so a billing-table hiccup never affects the LLM
        # response that just succeeded.
        recorder = self.usage_recorder
        if recorder is not None:
            record_fn = getattr(recorder, "record", None)
            if callable(record_fn):
                with suppress(Exception):
                    record_fn(response, caller=caller)
        return response

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Execute a JSON-mode task with core memory injection.

        ``reasoning_effort`` (v0.3.51+): pass ``""`` to disable the
        provider's thinking mode for this call. Recommended for
        structured tasks (eval / classify / write-expression) that
        don't benefit from chain-of-thought — disabling it on
        DeepSeek-V4 cuts a 30-item batch from ~10 min to ~30s.
        """
        return await self.complete_with_core_memory(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            caller=caller,
            reasoning_effort=reasoning_effort,
        )

    async def complete_with_tools(
        self,
        *,
        system_instruction: str,
        user_input: str,
        tools: list[dict[str, object]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        """Execute a completion that may include tool/function calls.

        The LLM is given a set of tool definitions.  If it decides to call
        a tool, the response will have ``tool_calls`` populated.  Otherwise
        ``content`` will contain the text reply.

        This method uses JSON mode under the hood: the tools are serialised
        into the system prompt and the model is asked to return a JSON
        wrapper with either ``reply`` or ``tool_call`` keys.
        """
        tools_desc = "\n".join(f"- {t['name']}: {t.get('description', '')}" for t in tools)
        tool_names = [t["name"] for t in tools]
        augmented_system = (
            system_instruction + "\n\n"
            "<available_tools>\n" + tools_desc + "\n"
            "</available_tools>\n\n"
            "<tool_call_format>\n"
            "如果你需要调用工具，请返回如下 JSON（不要附带任何其他文字）：\n"
            '{"tool_call": {"name": "工具名", "arguments": {参数}}}\n'
            "如果不需要调用工具，正常回复用户即可（不要输出 JSON）。\n"
            "</tool_call_format>"
        )
        response = await self.complete_with_core_memory(
            system_instruction=augmented_system,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
            caller=caller,
        )

        # Try to parse tool calls from the response
        import json

        content = (response.content or "").strip()
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "tool_call" in parsed:
                    call = parsed["tool_call"]
                    if isinstance(call, dict) and call.get("name") in tool_names:
                        response.tool_calls = [call]
                        response.content = ""
            except (json.JSONDecodeError, TypeError):
                pass  # Not valid JSON — treat as normal text reply

        return response

    async def complete_socratic_dialogue(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        caller: str = "",
    ) -> LLMResponse:
        """Generate a Socratic dialogue reply using core memory context."""
        tone_profile = self._build_dialogue_tone_profile()
        preference_raw = self.memory.get_layer("preference").data
        source_mix = preference_layer_from_dict(preference_raw).source_platform_mix
        prompt_messages = build_socratic_dialogue_prompt(
            user_message=user_message,
            core_memory_text="",
            tone_profile=tone_profile,
            history=[],
            source_platform_mix=source_mix or None,
        )
        return await self.complete_with_core_memory(
            system_instruction=prompt_messages[0]["content"],
            user_input=user_message,
            history=history,
            caller=caller,
        )

    def _build_dialogue_tone_profile(self) -> ToneProfile:
        """Infer tone profile for dialogue from persisted memory."""
        soul_raw = self.memory.get_layer("soul").data
        preference_raw = self.memory.get_layer("preference").data
        profile = None
        if soul_raw:
            profile = SoulProfile.from_dict(soul_raw)
            profile.preferences = preference_layer_from_dict(preference_raw)
        return build_tone_profile(
            profile=profile,
            preference_summary=self.memory.get_core_memory().get("preference_summary", {}),
            recent_feedback=[],
        )
