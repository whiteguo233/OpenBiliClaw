"""Shared service facade for prompt assembly and LLM execution."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

from openbiliclaw.soul.profile import SoulProfile, preference_layer_from_dict
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile

from .base import LLMProviderError, LLMRateLimitError
from .concurrency import (
    DEFAULT_TOTAL_LLM_CONCURRENCY,
    LLMConcurrencyGate,
    PrioritySemaphore,
    coerce_total_concurrency,
)
from .prompt_contracts import ensure_json_mode_contract
from .prompts import build_socratic_dialogue_prompt

logger = logging.getLogger(__name__)
DEFAULT_LLM_CONCURRENCY = DEFAULT_TOTAL_LLM_CONCURRENCY

# Structured (JSON-returning) tasks were historically budgeted by their
# expected payload size — 256 for one score, 512 for a keyword list. Those
# caps predate reasoning-first endpoints: a model that spends the whole
# output budget on invisible thinking returns no final content
# (``finish_reason=length``) and the call fails. ``max_tokens`` is a ceiling,
# not a reservation — normal completions still stop at EOS — so every
# structured call gets at least this floor. Per-call budgets above the floor
# are untouched.
MIN_STRUCTURED_MAX_TOKENS = 4096

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from openbiliclaw.memory.manager import MemoryManager

    from .base import LLMResponse


_BACKGROUND_ADMISSION_BYPASS: ContextVar[bool] = ContextVar(
    "openbiliclaw_background_admission_bypass",
    default=False,
)


@contextmanager
def _background_admission_bypass() -> Iterator[None]:
    """Bypass background admission within the current task context."""
    token = _BACKGROUND_ADMISSION_BYPASS.set(True)
    try:
        yield
    finally:
        _BACKGROUND_ADMISSION_BYPASS.reset(token)


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

    async def complete_chain(
        self,
        instance_ids: list[str] | tuple[str, ...],
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
    ) -> LLMResponse: ...

    def is_chat_capable(self, name: str) -> bool: ...

    def provider_type(self, name: str | None = None) -> str: ...


class LLMServiceError(Exception):
    """Base exception for service-layer LLM errors."""


class LLMResponseContentError(LLMServiceError):
    """Raised when an LLM call returns empty content."""


class LLMProviderExecutionError(LLMServiceError):
    """Raised when the underlying provider or registry call fails."""


_RATE_LIMIT_ERROR_MARKERS = (
    "rate limit",
    "429",
    "402",
    "cooling down",
    "too many requests",
    "resource exhausted",
    "quota exceeded",
    "payment required",
    "insufficient balance",
    "billing",
    "out of credit",
    "credit exhausted",
    "余额不足",
    "账户余额",
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
    chain: tuple[str, ...] = ()
    custom_chain: bool = False


_MODULE_OVERRIDE_BUCKETS = ("soul", "discovery", "recommendation", "evaluation")


def module_overrides_from_config(config: object) -> dict[str, ModuleOverride]:
    """Build normalized module LLM overrides from ``Config.llm`` blocks."""
    llm_config = getattr(config, "llm", None)
    if llm_config is None:
        return {}

    overrides: dict[str, ModuleOverride] = {}
    instance_routing = bool(getattr(llm_config, "instance_routing", False))
    for bucket in _MODULE_OVERRIDE_BUCKETS:
        raw = getattr(llm_config, bucket, None)
        if raw is None:
            continue
        if instance_routing:
            if bool(getattr(raw, "inherit", True)):
                continue
            chain = tuple(
                dict.fromkeys(
                    str(item).strip().lower()
                    for item in getattr(raw, "chain", [])
                    if str(item).strip()
                )
            )
            overrides[bucket] = ModuleOverride(chain=chain, custom_chain=True)
            continue
        provider = str(getattr(raw, "provider", "") or "").strip().lower()
        model = str(getattr(raw, "model", "") or "").strip()
        if provider or model:
            overrides[bucket] = ModuleOverride(provider=provider, model=model)
    return overrides


def _coerce_concurrency(value: object) -> int:
    """Return a positive LLM concurrency value, falling back to the default."""
    return coerce_total_concurrency(value)


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
    # cold-path soul/xhs analysis.
    _PRIORITY_MAP: ClassVar[dict[str, int]] = {
        "recommendation.write_expression": 1,
        "discovery.evaluate_batch": 1,
        "soul": 2,
        "xhs": 2,
    }
    _DEFAULT_PRIORITY: ClassVar[int] = 3
    _ROUTE_BUCKET_PREFIXES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("recommendation.evaluate_batch", "evaluation"),
        ("discovery.evaluate", "evaluation"),
        ("discovery.eval", "evaluation"),
        ("eval", "evaluation"),
        ("discovery.keyword", "discovery"),
        ("discovery.search", "discovery"),
        ("discovery.explore", "discovery"),
        ("discovery.trending", "discovery"),
        ("discovery.related", "discovery"),
        ("discovery.x", "discovery"),
        ("discovery.douyin", "discovery"),
        ("runtime.bilibili_extension_search", "discovery"),
        ("yt_search", "discovery"),
        ("sources.xhs", "discovery"),
        ("recommendation", "recommendation"),
        ("pool_purge", "soul"),
        ("api.sentiment", "soul"),
        ("soul", "soul"),
    )
    # Channel-facing work is dominated by bounded JSON extraction, scoring,
    # keyword generation, and short recommendation copy.  These calls default
    # to the configured provider/model effort (``None`` at the service layer)
    # so users can choose per-instance ``reasoning_effort`` in settings
    # (e.g. ``low`` for fast structured scoring, or ``""``/``None`` to follow
    # the upstream model).  A caller may still pass an explicit per-call value.
    _NO_REASONING_DEFAULT_PREFIXES: ClassVar[tuple[str, ...]] = (
        "discovery",
        "recommendation",
        "sources",
        "yt_search",
        "runtime.bilibili_extension_search",
    )
    _NO_REASONING_DEFAULT_CALLERS: ClassVar[frozenset[str]] = frozenset(
        {
            "eval.query_quality",
            "eval.relevance",
            "eval.specificity",
        }
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
    concurrency_gate: LLMConcurrencyGate | None = None
    # Free-text reply-style instruction from ``soul.reply_style`` (issue #255).
    # Forwarded into the dialogue prompt's tone block; empty (default) keeps
    # prompt output byte-identical.
    reply_style: str = ""
    # Free-text full replacement for the dialogue prompt's tone block, from
    # ``soul.dialogue_tone_prompt``. Empty (default) keeps prompt output
    # byte-identical; non-empty replaces the whole rendered tone block in
    # ``complete_socratic_dialogue`` (and suppresses the reply_style line
    # there, since the entire block is swapped).
    dialogue_tone_prompt: str = ""
    _logged_unknown_override_keys: set[tuple[str, str]] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.concurrency = _coerce_concurrency(self.concurrency)
        if self.concurrency_gate is None:
            self.concurrency_gate = LLMConcurrencyGate(self.concurrency)

    @asynccontextmanager
    async def _provider_slot(
        self, *, caller: str, bypass_background: bool = False
    ) -> AsyncIterator[None]:
        gate = cast("LLMConcurrencyGate", self.concurrency_gate)
        async with gate.slot(caller=caller, bypass_background=bypass_background):
            yield

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
                # WARNING (not INFO): a stale override usually means the
                # referenced instance was deleted/disabled after the route was
                # written. Silent INFO lines hid this state from operators
                # debugging stuck dialogue turns (issue #213).
                logger.warning(
                    "LLM module override ignored: bucket=%s provider=%s "
                    "is not registered or chat-capable; using default provider. "
                    "Fix the module route in the settings page or clear it to "
                    "inherit the global chain.",
                    bucket,
                    provider,
                )
            return None
        return provider, model or None

    def _resolve_module_chain(self, caller: str) -> list[str] | None:
        """Return a module's custom v2 chain; ``None`` means inherit global.

        A custom chain whose instances all resolve to nothing still returns
        ``[]`` (never the global chain): an explicitly configured broken route
        must fail loudly instead of silently spilling into the default chain.
        The trade-off is that every call for this bucket keeps failing until
        the route is repaired, so the unavailable-instance filtering below
        logs at WARNING with remediation hints (issue #213: a stale soul
        route permanently parked durable chat turns on ``no_provider`` while
        the failure itself was only visible as an INFO line).
        """
        bucket = self._route_bucket_for_caller(caller)
        if bucket is None:
            return None
        override = self.module_overrides.get(bucket)
        if override is None or not override.custom_chain:
            return None
        chain: list[str] = []
        for raw_instance_id in override.chain:
            instance_id = raw_instance_id.strip().lower()
            if instance_id and self.registry.is_chat_capable(instance_id):
                chain.append(instance_id)
                continue
            log_key = (bucket, instance_id)
            if log_key not in self._logged_unknown_override_keys:
                self._logged_unknown_override_keys.add(log_key)
                # WARNING (not INFO): see the docstring — a fully filtered
                # route makes every call for this bucket fail until repaired,
                # and issue #213 showed operators could not see why.
                logger.warning(
                    "LLM module route contains unavailable instance: bucket=%s "
                    "instance=%s; calls for this bucket will keep failing until "
                    "the route references enabled instances (or inherits the "
                    "global chain again).",
                    bucket,
                    instance_id,
                )
        # An explicitly custom but broken route must not silently spill into
        # the global chain. An empty list makes the registry raise no-provider.
        return chain

    @classmethod
    def _reasoning_effort_for_call(
        cls,
        caller: str,
        requested: str | None,
    ) -> str | None:
        """Resolve the per-call thinking mode without mutating provider state."""

        if requested is not None:
            return requested
        tag = caller.strip()
        if tag in cls._NO_REASONING_DEFAULT_CALLERS:
            # Follow the configured provider/model effort.  Channel callers no
            # longer force a fixed value: if an instance has ``reasoning_effort``
            # set, that setting applies; otherwise the provider's own default is
            # used.  A caller may still pass an explicit per-call value.
            return None
        if any(
            cls._caller_matches_route_prefix(tag, prefix)
            for prefix in cls._NO_REASONING_DEFAULT_PREFIXES
        ):
            return None
        return None

    def _core_memory_blocks(self, inject_core_memory: bool) -> tuple[str, str]:
        """Return ``(stable_block, volatile_block)`` for core-memory injection.

        The stable block (portrait / identity / preference) goes into the system
        prefix so provider prompt caching keeps firing; the volatile block
        (recent awareness / active insights) goes into the user message so
        awareness churn no longer invalidates the cached prefix.

        Prefers the manager's split API (``render_core_memory_blocks``). The
        ``getattr`` guard falls back to the legacy single-block
        ``render_core_memory_prompt`` (whole block treated as stable) purely for
        lightweight memory doubles in tests that predate the split — the real
        ``MemoryManager`` always exposes the split API. This is deliberately not
        a probe for a nonexistent method (cf. the removed ``_build_core_memory_block``
        getattr): both fallback targets are real, public rendering methods.
        """
        if not inject_core_memory or self.memory is None:
            return "", ""
        blocks_fn = getattr(self.memory, "render_core_memory_blocks", None)
        if callable(blocks_fn):
            with suppress(Exception):
                stable, volatile = blocks_fn()
                return str(stable), str(volatile)
            return "", ""
        with suppress(Exception):
            return str(self.memory.render_core_memory_prompt()), ""
        return "", ""

    @staticmethod
    def _prepend_volatile_core_memory(user_input: str, volatile_block: str) -> str:
        """Prepend the volatile core-memory block ahead of the turn content.

        Most-stable-first ordering: the volatile profile context (still more
        stable than this turn's user input) leads, then the actual request. No
        empty paragraph is added when there is no volatile block.
        """
        if not volatile_block:
            return user_input
        return f"以下是该用户的近期动态（观察 / 洞察，供参考）：\n{volatile_block}\n\n{user_input}"

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
        bypass_semaphore: bool = False,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a task with automatically injected core memory context.

        ``caller`` is an optional free-form tag (e.g. ``"soul.preference"``,
        ``"discovery.eval"``) attached to the usage row so the ``cost``
        report can break spend down by module.

        ``reasoning_effort`` (v0.3.51+) lets a caller override the provider's
        thinking mode. ``""`` explicitly disables it. ``None`` keeps the
        configured provider/model effort; channel-facing discovery,
        recommendation, source extraction, and short evaluation callers also
        default to ``None`` so per-instance ``reasoning_effort`` settings are
        honored. An explicit ``"high"`` / ``"max"`` always wins.

        ``bypass_semaphore`` (legacy name) skips only background admission;
        every provider call still respects the runtime total gate.

        ``inject_core_memory`` lets hot-path evaluators opt out when
        they already pass a task-specific structured profile in
        ``user_input``. This keeps provider-side prompt-cache prefixes
        stable without changing the information available to the task.
        """
        stable_block, volatile_block = self._core_memory_blocks(inject_core_memory)
        parts = [system_instruction.strip()]
        if stable_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(stable_block)
        system_content = "\n\n".join(parts)
        effective_reasoning_effort = self._reasoning_effort_for_call(
            caller,
            reasoning_effort,
        )
        user_content = self._prepend_volatile_core_memory(user_input, volatile_block)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        async def _do_llm_call() -> LLMResponse:
            routed_chain = self._resolve_module_chain(caller)
            if routed_chain is not None:
                return await self.registry.complete_chain(
                    routed_chain,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort=effective_reasoning_effort,
                )
            routed = self._resolve_module_override(caller)
            if routed is None:
                return await self.registry.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort=effective_reasoning_effort,
                )
            provider, model = routed
            return await self.registry.complete_provider(
                provider,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=effective_reasoning_effort,
                model=model,
            )

        try:
            async with self._provider_slot(
                caller=caller,
                bypass_background=(bypass_semaphore or _BACKGROUND_ADMISSION_BYPASS.get()),
            ):
                response = await _do_llm_call()
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
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a JSON-mode task with core memory injection.

        ``reasoning_effort`` (v0.3.51+): pass ``""`` to disable the
        provider's thinking mode for this call. Recommended for
        structured tasks (eval / classify / write-expression) that
        don't benefit from chain-of-thought — disabling it on
        DeepSeek-V4 cuts a 30-item batch from ~10 min to ~30s.

        ``max_tokens`` is floored at :data:`MIN_STRUCTURED_MAX_TOKENS` so a
        reasoning-first endpoint can think *and* still emit the final JSON;
        callers asking for more than the floor keep their budget.
        """
        return await self.complete_with_core_memory(
            system_instruction=ensure_json_mode_contract(system_instruction),
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max(int(max_tokens), MIN_STRUCTURED_MAX_TOKENS),
            json_mode=True,
            caller=caller,
            reasoning_effort=reasoning_effort,
            inject_core_memory=inject_core_memory,
        )

    def supports_image_input(self, caller: str = "discovery.evaluate_batch") -> bool:
        """Best-effort check for OpenAI-compatible vision-capable routes."""
        routed_chain = self._resolve_module_chain(caller)
        bucket = self._route_bucket_for_caller(caller)
        module_route = self.module_overrides.get(bucket) if bucket is not None else None
        if module_route is not None and module_route.custom_chain and not routed_chain:
            # Match completion routing: an explicitly custom but unusable
            # chain must not be treated as if it inherited the global route.
            return False
        routed = self._resolve_module_override(caller)
        provider_name = (
            routed_chain[0]
            if routed_chain
            else routed[0]
            if routed is not None
            else self.registry.default_provider
        ).strip()
        provider_type = getattr(self.registry, "provider_type", None)
        provider_key = (
            str(provider_type(provider_name) or "").strip().lower()
            if callable(provider_type)
            else provider_name.lower()
        )
        if provider_key not in {"openai", "openai_compatible", "openrouter", "orcarouter"}:
            return False

        provider_obj: object | None = None
        get_provider = getattr(self.registry, "get", None)
        if callable(get_provider):
            with suppress(Exception):
                provider_obj = get_provider(provider_name)
        model = ""
        if routed is not None and routed[1]:
            model = routed[1]
        elif provider_obj is not None:
            model = str(getattr(provider_obj, "_model", "") or "")
        model_lower = model.lower()
        vision_markers = (
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "o3",
            "o4",
            "vision",
            "vl",
            "qwen-vl",
            "pixtral",
            "llava",
            "gemini",
            "claude-3",
            "claude-sonnet-4",
        )
        return any(marker in model_lower for marker in vision_markers)

    async def complete_multimodal_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        image_inputs: list[dict[str, str]],
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        """Execute a JSON-mode task with user text plus image inputs."""
        # Multimodal batch evaluation pays the same reasoning-budget tax as
        # the text path; see MIN_STRUCTURED_MAX_TOKENS.
        max_tokens = max(int(max_tokens), MIN_STRUCTURED_MAX_TOKENS)
        stable_block, volatile_block = self._core_memory_blocks(inject_core_memory)
        parts = [ensure_json_mode_contract(system_instruction)]
        if stable_block:
            parts.append("以下是当前用户的 core memory，请作为理解背景：")
            parts.append(stable_block)
        system_content = "\n\n".join(parts)
        effective_reasoning_effort = self._reasoning_effort_for_call(
            caller,
            reasoning_effort,
        )

        user_text = self._prepend_volatile_core_memory(user_input, volatile_block)
        user_parts: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for image in image_inputs:
            content_id = str(image.get("content_id") or "").strip()
            data_url = str(image.get("data_url") or "").strip()
            if not content_id or not data_url:
                continue
            cover_ref = f"cover:{content_id}"
            user_parts.append(
                {
                    "type": "text",
                    "text": (
                        f"Cover image {cover_ref} maps to the content_batch item whose "
                        f"cover_image_ref is {cover_ref}."
                    ),
                }
            )
            user_parts.append({"type": "image_url", "image_url": {"url": data_url}})

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(cast("list[dict[str, Any]]", history))
        messages.append({"role": "user", "content": user_parts})

        async def _do_llm_call() -> LLMResponse:
            routed_chain = self._resolve_module_chain(caller)
            if routed_chain is not None:
                return await self.registry.complete_chain(
                    routed_chain,
                    cast("Any", messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                    reasoning_effort=effective_reasoning_effort,
                )
            routed = self._resolve_module_override(caller)
            if routed is None:
                return await self.registry.complete(
                    cast("Any", messages),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                    reasoning_effort=effective_reasoning_effort,
                )
            provider, model = routed
            return await self.registry.complete_provider(
                provider,
                cast("Any", messages),
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
                reasoning_effort=effective_reasoning_effort,
                model=model,
            )

        try:
            async with self._provider_slot(caller=caller):
                response = await _do_llm_call()
        except LLMProviderError as exc:
            raise LLMProviderExecutionError(str(exc)) from exc
        if not response.content.strip():
            raise LLMResponseContentError("LLM returned an empty response.")
        recorder = self.usage_recorder
        if recorder is not None:
            record_fn = getattr(recorder, "record", None)
            if callable(record_fn):
                with suppress(Exception):
                    record_fn(response, caller=caller)
        return response

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
        bypass_semaphore: bool = False,
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
            bypass_semaphore=bypass_semaphore,
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
            reply_style=self.reply_style,
            dialogue_tone_prompt=self.dialogue_tone_prompt,
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
