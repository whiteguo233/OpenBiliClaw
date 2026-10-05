"""Preference layer analysis built on structured LLM extraction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.llm.base import (
    LLMProviderError,
    LLMResponse,
    is_llm_moderation_error,
    is_reasoning_budget_exhausted,
)
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_preference_analysis_prompt
from openbiliclaw.llm.service import LLMServiceError, is_llm_rate_limit_error
from openbiliclaw.llm.task_options import call_accepts_keyword, without_core_memory_kwargs
from openbiliclaw.soul.event_filters import filter_events_by_satisfaction
from openbiliclaw.soul.event_prompt_views import normalize_cognition_input_view
from openbiliclaw.soul.taxonomy import SupportsEmbed, resolve_category

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    # Observer invoked once per completed chunk with (done, total). Purely
    # observational — see _emit_progress: its failures never abort analysis.
    ProgressCallback = Callable[[int, int], Awaitable[None]]

logger = logging.getLogger(__name__)


# Stored disliked_topics are recency-ordered and capped so the list (and
# the preference-analysis prompt that echoes it back) stay bounded.
# The downstream prompt caps (_DISLIKED_TOPICS_CAP in discovery and the
# recommendation summary) equal this store cap, so every stored
# avoid-topic reaches LLM prompts; the stalest topics decay out past
# this when re-flagged entries keep bubbling to the front.
_DISLIKED_TOPICS_STORE_CAP = 128

DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE = 200
# A partial chunk only returns at most 25 interests, three observations and two
# hypotheses. Giving every local-model chunk the global 16K structured-output
# ceiling can turn a malformed/non-terminating JSON response into many minutes
# of needless generation. Four thousand tokens comfortably fit this bounded
# schema; the unchunked path and final profile keep the larger shared budget.
PREFERENCE_CHUNK_MAX_TOKENS = 4096
# Some OpenAI-compatible reasoning models ignore the per-call request to turn
# thinking off and can spend the entire 4K chunk budget before emitting JSON.
# Retry that specific, observable finish_reason=length failure once with the
# normal structured-output ceiling; all other malformed output keeps the 4K
# guard that protects local models from unbounded generation.
PREFERENCE_REASONING_FALLBACK_MAX_TOKENS = DEFAULT_STRUCTURED_MAX_TOKENS
PREFERENCE_RATE_LIMIT_MAX_RETRIES = 2
PREFERENCE_RATE_LIMIT_RETRY_SECONDS = 65.0
# Fallback only when no registry concurrency is available. Explicit values are
# honored without a hard upper cap so users can push fan-out as high as their
# provider / local state permit.
MAX_CONCURRENT_PREFERENCE_CHUNKS = 16
INIT_COGNITION_CONTEXT_KEY = "_init_cognition_context"
_INIT_AWARENESS_CANDIDATES_CAP = 12
_INIT_INSIGHT_CANDIDATES_CAP = 8

_COMPACT_METADATA_KEYS = frozenset(
    {
        "source_platform",
        "up_name",
        "author",
        "bvid",
        "aid",
        "content_id",
        "folder",
        "duration",
        "watch_seconds",
        "video_duration_seconds",
        "feedback_type",
        "reaction",
        "signal_strength",
        # Retraction discount flag (Phase 0): kept so the budget-overflow /
        # refusal-retry compact path still surfaces the undo + folded strength.
        "retracted",
        # Comment / danmaku text (Phase 2/3): the user's own first-person
        # interest expression must survive the compact path, not just the
        # whole-batch JSON dump.
        "comment_text",
        "comment_kind",
    }
)

# Legal enum values for style fields. LLMs sometimes dump placeholders like
# "unknown" that schema-defy these; those get coerced to "" so UIs fall back
# to their "still observing" copy instead of rendering the garbage verbatim.
_LEGAL_DURATIONS = frozenset({"short", "medium", "long", ""})
_LEGAL_PACES = frozenset({"fast", "moderate", "slow", ""})
_STYLE_TASTE_FIELDS = ("quality_sensitivity", "humor_preference", "depth_preference")
# Case-insensitive placeholders the LLM emits when it has no signal. Treated as
# absent for text fields so downstream fallback copy applies.
_UNKNOWN_PLACEHOLDERS = frozenset({"unknown", "none", "n/a", "未知"})


class SupportsCoreMemoryTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse: ...


class PreferenceAnalysisError(Exception):
    """Raised when preference extraction fails or returns invalid data."""


@dataclass
class PreferenceAnalyzer:
    """Analyze recent events into a structured preference profile."""

    registry: SupportsCoreMemoryTask
    decay_factor_per_week: float = 0.9
    min_interest_weight: float = 0.05
    # EMA blend: 0.3 * latest batch + 0.7 * prior mix. Chosen so one-off
    # cross-platform batches don't erase long-running bilibili history.
    source_mix_blend_alpha: float = 0.3
    # v0.3.x event-satisfaction signal: when True, drop passive negative
    # events such as quick-exit before building the LLM prompt. Explicit
    # dislike feedback is retained as negative evidence so the analyzer can
    # update disliked_topics without mistaking that title for a positive
    # interest.
    satisfaction_filter_enabled: bool = True
    embedding_service: SupportsEmbed | None = None
    max_prompt_chars: int = 24_000
    compact_title_chars: int = 180
    compact_context_chars: int = 600
    compact_metadata_value_chars: int = 300
    cognition_prompt_view: str = "legacy"

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError(
                "PreferenceAnalyzer requires a service with complete_structured_task()."
            )
        self.cognition_prompt_view = normalize_cognition_input_view(self.cognition_prompt_view)

    @staticmethod
    async def _emit_progress(callback: ProgressCallback | None, done: int, total: int) -> None:
        """Fire a progress observer, swallowing any error at WARNING.

        The observer only watches chunk completion — it must never abort the
        analysis (init-progress spec invariant 4 / pitfall "will this failure
        be swallowed").
        """
        if callback is None:
            return
        try:
            await callback(done, total)
        except Exception:
            logger.warning("preference progress_callback raised; ignoring", exc_info=True)

    async def analyze_events(
        self,
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        event_chunk_size: int = 0,
        progress_callback: ProgressCallback | None = None,
        awareness_notes: list[dict[str, object]] | None = None,
        active_insights: list[dict[str, object]] | None = None,
        llm_concurrency: int | None = None,
    ) -> dict[str, object]:
        """Run structured extraction and merge the result with existing preference state.

        When ``event_chunk_size`` > 0 and the event list reaches that size,
        the input is split into chunks of at most ``event_chunk_size`` events
        and each chunk is analysed concurrently in a separate LLM call. Partial
        preferences from each chunk are then folded into ``existing_preference``
        via the regular ``merge_preferences`` path, preserving weighted
        interest merging and cognitive-style union. Use this for
        latency-sensitive flows (e.g. init bootstrap with hundreds of
        historical events) where a single max-thinking call on the whole batch
        would block for minutes.
        """
        events = self._maybe_filter_events(events)
        if event_chunk_size > 0 and len(events) >= event_chunk_size:
            return await self._analyze_events_chunked(
                events=events,
                existing_preference=existing_preference,
                chunk_size=event_chunk_size,
                progress_callback=progress_callback,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                llm_concurrency=llm_concurrency,
            )

        whole_batch_prompt = build_preference_analysis_prompt(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
            input_view=self.cognition_prompt_view,
        )
        prompt_chars = self._prompt_char_count(whole_batch_prompt)
        should_chunk_by_budget = self.max_prompt_chars > 0 and prompt_chars > self.max_prompt_chars
        if should_chunk_by_budget:
            planned_chunks = self._plan_fitting_independent_chunks(
                events=events,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
            )
            return await self._analyze_events_chunked(
                events=events,
                existing_preference=existing_preference,
                chunk_size=max((len(chunk) for chunk in planned_chunks), default=1),
                planned_chunks=planned_chunks,
                progress_callback=progress_callback,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                llm_concurrency=llm_concurrency,
            )
        result = await self._analyze_events_single(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
        )
        # Un-chunked path has a single natural completion point.
        await self._emit_progress(progress_callback, 1, 1)
        return result

    def _maybe_filter_events(
        self,
        events: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Drop passive negative events when the flag is on.

        The ``"unknown"`` bucket is included so pre-classification legacy
        rows (NULL ``inferred_satisfaction``) still feed the analyzer.
        ``"neutral"`` is included because searches / shallow views are not
        satisfaction evidence, but they are still useful preference context.
        Explicit dislike feedback is kept so it can feed disliked_topics.
        """
        if not self.satisfaction_filter_enabled:
            return events
        filtered = [event for event in events if self._keeps_event_under_satisfaction_filter(event)]
        if len(filtered) != len(events):
            logger.info(
                "satisfaction_filter dropped %d/%d events before preference analysis",
                len(events) - len(filtered),
                len(events),
            )
        return filtered

    @staticmethod
    def _keeps_event_under_satisfaction_filter(event: dict[str, object]) -> bool:
        if event in filter_events_by_satisfaction(
            [event], modes=frozenset({"positive", "neutral", "unknown"})
        ):
            return True
        return PreferenceAnalyzer._is_explicit_negative_feedback(event)

    @staticmethod
    def _is_explicit_negative_feedback(event: dict[str, object]) -> bool:
        event_type = str(event.get("event_type") or event.get("type") or "").strip().lower()
        metadata = event.get("metadata")
        feedback_type = ""
        reaction = ""
        if isinstance(metadata, dict):
            feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
            reaction = str(metadata.get("reaction") or "").strip().lower()
        return event_type in {"feedback", "dislike"} and (
            feedback_type == "dislike" or reaction == "thumbs_down" or event_type == "dislike"
        )

    async def _analyze_events_single(
        self,
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        awareness_notes: list[dict[str, object]] | None = None,
        active_insights: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        messages = build_preference_analysis_prompt(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
            input_view=self.cognition_prompt_view,
        )
        try:
            response = await self._complete_cacheable_preference_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.preference",
            )
        except (LLMProviderError, LLMServiceError) as exc:
            if is_llm_moderation_error(exc):
                logger.warning(
                    "preference analysis blocked by provider content moderation; "
                    "falling back to chunked isolation: events=%d error=%s",
                    len(events),
                    exc,
                )
                if len(events) > 0:
                    return await self._analyze_events_chunked(
                        events=events,
                        existing_preference=existing_preference,
                        chunk_size=max(1, len(events) // 2),
                        awareness_notes=awareness_notes,
                        active_insights=active_insights,
                    )
            raise PreferenceAnalysisError(str(exc)) from exc

        raw_preference = self._parse_response(response.content)
        normalized = await self._normalize_and_resolve(raw_preference)
        merged = self.merge_preferences(existing_preference, normalized, now=datetime.now())
        init_cognition = self._extract_init_cognition_context(raw_preference)
        if init_cognition:
            merged[INIT_COGNITION_CONTEXT_KEY] = init_cognition
        merged["source_platform_mix"] = self._merge_source_mix(
            existing_preference.get("source_platform_mix"),
            self.compute_source_platform_mix(events),
        )
        # Preserve cognitive_style from LLM output (not modeled in PreferenceLayer)
        raw_cs = raw_preference.get("cognitive_style")
        if isinstance(raw_cs, list):
            merged["cognitive_style"] = [str(s) for s in raw_cs if s]
        elif "cognitive_style" not in merged:
            existing_cs = existing_preference.get("cognitive_style")
            if isinstance(existing_cs, list):
                merged["cognitive_style"] = existing_cs
        return merged

    async def _complete_cacheable_preference_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int,
        caller: str,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Run preference extraction without dynamic core-memory system suffixes."""
        kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "user_input": user_input,
            "max_tokens": max_tokens,
            "caller": caller,
        }
        complete = cast("Any", self.registry.complete_structured_task)
        kwargs.update(without_core_memory_kwargs(complete))
        if reasoning_effort is not None and call_accepts_keyword(complete, "reasoning_effort"):
            kwargs["reasoning_effort"] = reasoning_effort
        return cast("LLMResponse", await complete(**kwargs))

    def _prompt_char_count(self, messages: list[dict[str, str]]) -> int:
        return sum(len(message.get("content", "")) for message in messages)

    def _prompt_fits_budget(self, messages: list[dict[str, str]]) -> bool:
        return (
            self.max_prompt_chars <= 0 or self._prompt_char_count(messages) <= self.max_prompt_chars
        )

    def _largest_fitting_independent_chunk_size(
        self,
        *,
        events: list[dict[str, object]],
        awareness_notes: list[dict[str, object]] | None,
        active_insights: list[dict[str, object]] | None,
    ) -> int:
        """Return the largest prefix that fits the actual independent-call shape.

        Automatic budget fallback ultimately sends chunks with an empty
        ``existing_preference`` and merges their outputs into the stored
        preference locally. Estimating chunk size from the oversized whole
        prompt therefore over-counted the stored preference on every chunk and
        could turn one fitting independent call into several needless calls.
        Prompt size is monotonic for event prefixes, so a deterministic binary
        search finds the largest fitting prefix in O(log n) renders.
        """
        if not events:
            return 1
        if self.max_prompt_chars <= 0:
            return len(events)

        def _prefix_fits(event_count: int) -> bool:
            messages = build_preference_analysis_prompt(
                events=events[:event_count],
                existing_preference={},
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                input_view=self.cognition_prompt_view,
            )
            return self._prompt_fits_budget(messages)

        if _prefix_fits(len(events)):
            return len(events)
        if not _prefix_fits(1):
            # The resilient chunk path owns single-event compaction/skipping.
            return 1

        low = 1
        high = len(events) - 1
        while low < high:
            midpoint = (low + high + 1) // 2
            if _prefix_fits(midpoint):
                low = midpoint
            else:
                high = midpoint - 1
        return low

    def _plan_fitting_independent_chunks(
        self,
        *,
        events: list[dict[str, object]],
        awareness_notes: list[dict[str, object]] | None,
        active_insights: list[dict[str, object]] | None,
    ) -> list[list[dict[str, object]]]:
        """Greedily pack each automatic chunk against its exact prompt shape.

        Event payload sizes can be highly skewed. Reusing the first prefix's
        width for the whole batch makes a later large event trigger recursive
        splitting and can strand adjacent small events in separate calls. A
        fresh largest-prefix search at each offset keeps every multi-event
        top-level chunk within budget and leaves only an individually oversized
        event to the existing compaction recovery path.
        """
        chunks: list[list[dict[str, object]]] = []
        offset = 0
        while offset < len(events):
            remaining = events[offset:]
            chunk_size = self._largest_fitting_independent_chunk_size(
                events=remaining,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
            )
            chunk = remaining[: max(1, chunk_size)]
            chunks.append(chunk)
            offset += len(chunk)
        return chunks

    @staticmethod
    def _is_context_overflow_error(exc: PreferenceAnalysisError) -> bool:
        text = str(exc).lower()
        markers = (
            "context length",
            "maximum context",
            "n_ctx",
            "n_keep",
            "tokens to keep",
            "prompt is too long",
            "input is too long",
        )
        return any(marker in text for marker in markers)

    def _compact_event_for_prompt(self, event: dict[str, object]) -> dict[str, object]:
        compact: dict[str, object] = {}
        for key in (
            "event_type",
            "type",
            "created_at",
            "inferred_satisfaction",
            "satisfaction_reason",
        ):
            value = event.get(key)
            if value not in (None, ""):
                compact[key] = value

        title = event.get("title")
        if title not in (None, ""):
            compact["title"] = self._truncate_for_prompt(title, self.compact_title_chars)

        context = event.get("context")
        if context not in (None, ""):
            compact["context"] = self._truncate_for_prompt(context, self.compact_context_chars)

        url = event.get("url")
        if url not in (None, ""):
            compact["url"] = self._truncate_for_prompt(url, self.compact_metadata_value_chars)

        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            compact_metadata: dict[str, object] = {}
            for key in sorted(_COMPACT_METADATA_KEYS):
                value = metadata.get(key)
                if value in (None, ""):
                    continue
                if isinstance(value, str):
                    compact_metadata[key] = self._truncate_for_prompt(
                        value,
                        self.compact_metadata_value_chars,
                    )
                elif isinstance(value, bool | int | float):
                    compact_metadata[key] = value
            if compact_metadata:
                compact["metadata"] = compact_metadata
        return compact

    def _safe_compact_event_for_invalid_json_retry(
        self,
        event: dict[str, object],
    ) -> dict[str, object]:
        """Build a lower-risk prompt event for retrying model refusals.

        Long natural-language page context can trigger provider safety refusals
        even though preference extraction is benign. A title/URL/source retry
        keeps useful preference signal while removing the likely offending body.
        """
        compact = self._compact_event_for_prompt(event)
        compact.pop("context", None)
        return compact

    @staticmethod
    def _truncate_for_prompt(value: object, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return text[: max_chars - 3].rstrip() + "..."

    async def _analyze_events_chunked(
        self,
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
        chunk_size: int,
        planned_chunks: list[list[dict[str, object]]] | None = None,
        progress_callback: ProgressCallback | None = None,
        awareness_notes: list[dict[str, object]] | None = None,
        active_insights: list[dict[str, object]] | None = None,
        llm_concurrency: int | None = None,
    ) -> dict[str, object]:
        """Split events into bounded concurrent chunk batches, then fold."""
        import asyncio as _asyncio

        chunk_size = max(1, chunk_size)
        chunks = (
            [list(chunk) for chunk in planned_chunks]
            if planned_chunks is not None
            else [events[i : i + chunk_size] for i in range(0, len(events), chunk_size)]
        )
        logger.info(
            "analyze_events chunked: total_events=%d chunks=%d max_chunk_size=%d",
            len(events),
            len(chunks),
            chunk_size,
        )

        # Each chunk is analysed against an empty seed so the LLM calls
        # are truly independent — we don't want one chunk's partial
        # state to leak into another's prompt. The final merge step
        # below folds each chunk's normalized output into the real
        # ``existing_preference`` using merge_preferences, which already
        # handles weighted interest aggregation across calls.
        async def _run_chunk_once(
            chunk: list[dict[str, object]],
        ) -> tuple[dict[str, object], dict[str, object]]:
            messages = build_preference_analysis_prompt(
                events=chunk,
                existing_preference={},
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                input_view=self.cognition_prompt_view,
            )
            response: LLMResponse | None = None
            max_tokens = PREFERENCE_CHUNK_MAX_TOKENS
            rate_limit_retries = 0
            reasoning_budget_retried = False
            while True:
                try:
                    response = await self._complete_cacheable_preference_task(
                        system_instruction=messages[0]["content"],
                        user_input=messages[1]["content"],
                        max_tokens=max_tokens,
                        caller="soul.preference.chunk",
                        # Preference extraction is a bounded JSON classify task;
                        # provider reasoning can add thousands of invisible
                        # tokens, latency and TPM pressure without improving the
                        # schema. Final profile prose keeps provider defaults.
                        reasoning_effort=None,
                    )
                    break
                except (LLMProviderError, LLMServiceError) as exc:
                    message = str(exc).lower()
                    if (
                        is_reasoning_budget_exhausted(exc)
                        and not reasoning_budget_retried
                        and max_tokens < PREFERENCE_REASONING_FALLBACK_MAX_TOKENS
                    ):
                        reasoning_budget_retried = True
                        max_tokens = PREFERENCE_REASONING_FALLBACK_MAX_TOKENS
                        logger.warning(
                            "preference chunk reasoning exhausted %d-token budget; "
                            "retrying once with %d tokens",
                            PREFERENCE_CHUNK_MAX_TOKENS,
                            max_tokens,
                        )
                        continue
                    non_retryable_quota = any(
                        marker in message
                        for marker in (
                            "402",
                            "payment required",
                            "insufficient_quota",
                            "insufficient balance",
                            "quota exceeded",
                            "out of credit",
                            "credit exhausted",
                            "余额不足",
                            "账户余额",
                        )
                    )
                    if (
                        is_llm_rate_limit_error(exc)
                        and not non_retryable_quota
                        and rate_limit_retries < PREFERENCE_RATE_LIMIT_MAX_RETRIES
                    ):
                        delay = PREFERENCE_RATE_LIMIT_RETRY_SECONDS
                        rate_limit_retries += 1
                        logger.warning(
                            "preference chunk rate-limited; retrying in %.0fs (%d/%d)",
                            delay,
                            rate_limit_retries,
                            PREFERENCE_RATE_LIMIT_MAX_RETRIES,
                        )
                        await _asyncio.sleep(delay)
                        continue
                    raise PreferenceAnalysisError(str(exc)) from exc
            if response is None:  # pragma: no cover - loop always returns or raises
                raise PreferenceAnalysisError("preference chunk returned no response")
            raw = self._parse_response(response.content, log_error=False)
            return raw, await self._normalize_and_resolve(raw)

        async def _retry_single_event_without_context(
            event: dict[str, object],
        ) -> tuple[dict[str, object], dict[str, object]] | None:
            safe_event = self._safe_compact_event_for_invalid_json_retry(event)
            if not safe_event:
                return None
            safe_messages = build_preference_analysis_prompt(
                events=[safe_event],
                existing_preference={},
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                input_view=self.cognition_prompt_view,
            )
            if not self._prompt_fits_budget(safe_messages):
                logger.warning(
                    "preference event skipped because safe compact prompt still exceeds "
                    "budget: title=%r prompt_chars=%d budget=%d",
                    str(event.get("title", "")),
                    self._prompt_char_count(safe_messages),
                    self.max_prompt_chars,
                )
                return None
            try:
                return await _run_chunk_once([safe_event])
            except PreferenceAnalysisError as retry_exc:
                if (
                    retry_exc.__cause__ is not None
                    and not self._is_context_overflow_error(retry_exc)
                    and not is_llm_moderation_error(retry_exc)
                ):
                    raise
                logger.warning(
                    "preference chunk skipped after safe compact retry failed: title=%r",
                    str(event.get("title", "")),
                )
                return None

        async def _split_or_compact_chunk(
            chunk: list[dict[str, object]],
        ) -> list[tuple[dict[str, object], dict[str, object]]]:
            if len(chunk) <= 1:
                compact = self._compact_event_for_prompt(chunk[0]) if chunk else {}
                compact_messages = build_preference_analysis_prompt(
                    events=[compact],
                    existing_preference={},
                    awareness_notes=awareness_notes,
                    active_insights=active_insights,
                    input_view=self.cognition_prompt_view,
                )
                if not self._prompt_fits_budget(compact_messages):
                    logger.warning(
                        "preference event skipped because compact prompt still exceeds "
                        "budget: title=%r prompt_chars=%d budget=%d",
                        str(chunk[0].get("title", ""))
                        if chunk and isinstance(chunk[0], dict)
                        else "",
                        self._prompt_char_count(compact_messages),
                        self.max_prompt_chars,
                    )
                    return []
                try:
                    return [await _run_chunk_once([compact])]
                except PreferenceAnalysisError as compact_exc:
                    if is_llm_moderation_error(compact_exc):
                        logger.warning(
                            "preference event skipped after provider content moderation "
                            "refused the compact prompt: title=%r",
                            str(chunk[0].get("title", ""))
                            if chunk and isinstance(chunk[0], dict)
                            else "",
                        )
                        return []
                    raise
            midpoint = max(1, len(chunk) // 2)
            # Recovery must not fan out again underneath the bounded top-level
            # scheduler. Concurrent recursive halves used to queue 4/8/… calls
            # behind LLMService.concurrency; one 429 then released the whole
            # queue into the provider cooldown and produced a retry storm.
            left = await _run_chunk_resilient(chunk[:midpoint])
            right = await _run_chunk_resilient(chunk[midpoint:])
            return [*left, *right]

        async def _run_chunk_resilient(
            chunk: list[dict[str, object]],
        ) -> list[tuple[dict[str, object], dict[str, object]]]:
            messages = build_preference_analysis_prompt(
                events=chunk,
                existing_preference={},
                awareness_notes=awareness_notes,
                active_insights=active_insights,
                input_view=self.cognition_prompt_view,
            )
            if not self._prompt_fits_budget(messages):
                return await _split_or_compact_chunk(chunk)
            try:
                return [await _run_chunk_once(chunk)]
            except PreferenceAnalysisError as exc:
                if exc.__cause__ is not None:
                    if self._is_context_overflow_error(exc):
                        logger.warning(
                            "preference chunk exceeded provider context; splitting: "
                            "events=%d error=%s",
                            len(chunk),
                            exc,
                        )
                        return await _split_or_compact_chunk(chunk)
                    if not is_llm_moderation_error(exc):
                        raise
                    logger.warning(
                        "preference chunk refused by provider content moderation; "
                        "isolating the offending event: events=%d error=%s",
                        len(chunk),
                        exc,
                    )
                # Invalid JSON / model refusal is often content-local: split
                # the batch to isolate the offending event, then skip only
                # that final single event if a title/source-only retry still
                # refuses.
                if len(chunk) <= 1:
                    event = chunk[0] if chunk else {}
                    if isinstance(event, dict):
                        retry_outcome = await _retry_single_event_without_context(event)
                        if retry_outcome is not None:
                            return [retry_outcome]
                    logger.warning(
                        "preference chunk skipped after invalid LLM response: title=%r",
                        str(event.get("title", "")) if isinstance(event, dict) else "",
                    )
                    return []
                return await _split_or_compact_chunk(chunk)

        # Each top-level chunk completing is a natural progress point. A shared
        # counter (bumped between the resilient run and the callback await, with
        # no interleaving await) yields a strictly increasing done 1..N even
        # under concurrent gather. Per-chunk start/done/abort lines (with the
        # 1-based index + wall time) go to the logger so ``openbiliclaw.log``
        # shows exactly which chunk is in flight — a chunk that logs "started"
        # without a matching "done" is the one that stalled or was cancelled by
        # the init timeout.
        import time as _time

        total_chunks = len(chunks)
        done_chunks = 0

        async def _run_and_report(
            index: int,
            chunk: list[dict[str, object]],
        ) -> list[tuple[dict[str, object], dict[str, object]]]:
            nonlocal done_chunks
            started = _time.monotonic()
            logger.info(
                "preference chunk %d/%d started: events=%d",
                index,
                total_chunks,
                len(chunk),
            )
            try:
                result = await _run_chunk_resilient(chunk)
            except _asyncio.CancelledError:
                logger.info(
                    "preference chunk %d/%d cancelled after %.1fs",
                    index,
                    total_chunks,
                    _time.monotonic() - started,
                )
                raise
            except BaseException as exc:
                logger.warning(
                    "preference chunk %d/%d failed after %.1fs: %s",
                    index,
                    total_chunks,
                    _time.monotonic() - started,
                    exc,
                )
                raise
            done_chunks += 1
            logger.info(
                "preference chunk %d/%d done in %.1fs (%d/%d complete)",
                index,
                total_chunks,
                _time.monotonic() - started,
                done_chunks,
                total_chunks,
            )
            await self._emit_progress(progress_callback, done_chunks, total_chunks)
            return result

        configured_concurrency = (
            llm_concurrency
            if llm_concurrency is not None
            else getattr(self.registry, "concurrency", None)
        )
        try:
            configured_chunk_limit = (
                int(configured_concurrency)
                if configured_concurrency is not None
                else MAX_CONCURRENT_PREFERENCE_CHUNKS
            )
        except (TypeError, ValueError):
            configured_chunk_limit = MAX_CONCURRENT_PREFERENCE_CHUNKS
        chunk_limit = max(1, configured_chunk_limit)
        logger.info(
            "preference chunk fanout bounded at %d (configured LLM concurrency=%r%s)",
            chunk_limit,
            configured_concurrency,
            " [init override]" if llm_concurrency is not None else "",
        )
        outcome_groups: list[list[tuple[dict[str, object], dict[str, object]]]] = []
        for batch_start in range(0, len(chunks), chunk_limit):
            batch = list(
                enumerate(
                    chunks[batch_start : batch_start + chunk_limit],
                    start=batch_start + 1,
                )
            )
            tasks = [_asyncio.create_task(_run_and_report(idx, chunk)) for idx, chunk in batch]
            try:
                outcome_groups.extend(await _asyncio.gather(*tasks))
            except BaseException:
                # asyncio.gather propagates the first exception but deliberately
                # leaves siblings running. An init terminal must be a real task
                # boundary: cancel and drain the rest before the coordinator
                # releases its run lock and restarts background work.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await _asyncio.gather(*tasks, return_exceptions=True)
                raise
        outcomes = [item for group in outcome_groups for item in group]

        # Fold each chunk's normalized preference into the running merge
        # one at a time. merge_preferences already does weighted interest
        # aggregation + dislike-list union, so stacking calls gives an
        # aggregate comparable in spirit to a single big-prompt analysis.
        merged: dict[str, object] = dict(existing_preference)
        cognitive_style_union: list[str] = []
        for raw_preference, normalized in outcomes:
            merged = self.merge_preferences(merged, normalized, now=datetime.now())
            raw_cs = raw_preference.get("cognitive_style")
            if isinstance(raw_cs, list):
                for item in raw_cs:
                    if item and str(item) not in cognitive_style_union:
                        cognitive_style_union.append(str(item))

        merged["source_platform_mix"] = self._merge_source_mix(
            existing_preference.get("source_platform_mix"),
            self.compute_source_platform_mix(events),
        )
        if cognitive_style_union:
            merged["cognitive_style"] = cognitive_style_union
        elif "cognitive_style" not in merged:
            existing_cs = existing_preference.get("cognitive_style")
            if isinstance(existing_cs, list):
                merged["cognitive_style"] = existing_cs
        init_cognition = self._merge_init_cognition_contexts(
            raw_preference for raw_preference, _normalized in outcomes
        )
        if init_cognition:
            merged[INIT_COGNITION_CONTEXT_KEY] = init_cognition
        logger.info(
            "analyze_events chunked done: total_events=%d chunks=%d",
            len(events),
            len(chunks),
        )
        return merged

    @staticmethod
    def compute_source_platform_mix(
        events: list[dict[str, object]],
    ) -> dict[str, float]:
        """Count events by source_platform and return a normalized share dict."""
        counts: dict[str, int] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            metadata = event.get("metadata")
            source = ""
            if isinstance(metadata, dict):
                raw = metadata.get("source_platform")
                if isinstance(raw, str):
                    source = raw.strip()
            if not source:
                # Events predating source_platform are always bilibili.
                source = "bilibili"
            counts[source] = counts.get(source, 0) + 1
        total = sum(counts.values())
        if total == 0:
            return {}
        return {name: count / total for name, count in counts.items()}

    def _merge_init_cognition_contexts(
        self,
        raw_preferences: Iterable[dict[str, object]],
    ) -> dict[str, object]:
        """Merge per-chunk cognition drafts, giving every chunk a turn.

        Chunks are ``events[i:i+N]`` over a newest-first fetch, so walking them
        in order and stopping at the cap handed the whole quota to the most
        recent one or two chunks — an account's earlier periods contributed
        nothing at all. Round-robin instead: take one candidate from each chunk
        per pass, so a 90-day history is represented across its span, and a
        chunk that produced little simply drops out of later passes rather than
        wasting its share.
        """
        per_chunk_awareness: list[list[dict[str, object]]] = []
        per_chunk_insights: list[list[dict[str, object]]] = []
        for raw in raw_preferences:
            context = self._extract_init_cognition_context(raw)
            per_chunk_awareness.append(
                [item for item in self._as_list(context.get("awareness")) if isinstance(item, dict)]
            )
            per_chunk_insights.append(
                [item for item in self._as_list(context.get("insights")) if isinstance(item, dict)]
            )

        def _alternate_ends(
            groups: list[list[dict[str, object]]],
        ) -> list[list[dict[str, object]]]:
            """Newest, oldest, next-newest, next-oldest, ...

            Plain round-robin still favours recent behaviour when the chunks
            themselves are lopsided: a binge of 400 short videos owns most of
            the chunks, so the first ``cap`` chunks visited are all binge and
            the early periods never get a turn. Walking in from both ends of the
            timeline guarantees the oldest chunks are reached within the budget.
            """
            ordered: list[list[dict[str, object]]] = []
            low, high = 0, len(groups) - 1
            while low <= high:
                ordered.append(groups[low])
                if low != high:
                    ordered.append(groups[high])
                low += 1
                high -= 1
            return ordered

        def _round_robin(
            groups: list[list[dict[str, object]]],
            *,
            key_field: str,
            cap: int,
        ) -> list[dict[str, object]]:
            groups = _alternate_ends(groups)
            picked: list[dict[str, object]] = []
            seen: set[str] = set()
            cursors = [0] * len(groups)
            while len(picked) < cap:
                progressed = False
                for index, group in enumerate(groups):
                    if len(picked) >= cap:
                        break
                    while cursors[index] < len(group):
                        item = group[cursors[index]]
                        cursors[index] += 1
                        key = self._normalize_context_text(str(item.get(key_field, "")))
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        picked.append(item)
                        progressed = True
                        break
                if not progressed:
                    break
            return picked

        awareness = _round_robin(
            per_chunk_awareness,
            key_field="observation",
            cap=_INIT_AWARENESS_CANDIDATES_CAP,
        )
        insights = _round_robin(
            per_chunk_insights,
            key_field="hypothesis",
            cap=_INIT_INSIGHT_CANDIDATES_CAP,
        )
        result: dict[str, object] = {}
        if awareness:
            result["awareness"] = awareness
        if insights:
            result["insights"] = insights
        return result

    def _extract_init_cognition_context(
        self, raw_preference: dict[str, object]
    ) -> dict[str, object]:
        awareness = self._normalize_init_awareness_candidates(
            raw_preference.get("awareness_candidates")
        )
        insights = self._normalize_init_insight_candidates(raw_preference.get("insight_candidates"))
        result: dict[str, object] = {}
        if awareness:
            result["awareness"] = awareness
        if insights:
            result["insights"] = insights
        return result

    def _normalize_init_awareness_candidates(self, raw_value: object) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw_item in self._as_list(raw_value):
            if not isinstance(raw_item, dict):
                continue
            observation = str(raw_item.get("observation", "")).strip()
            key = self._normalize_context_text(observation)
            if not key or key in seen:
                continue
            seen.add(key)
            item: dict[str, object] = {
                "date": str(raw_item.get("date") or "init"),
                "observation": observation,
                "trend": str(raw_item.get("trend", "")).strip(),
                "emotion_guess": str(raw_item.get("emotion_guess", "")).strip(),
            }
            candidates.append(item)
            if len(candidates) >= _INIT_AWARENESS_CANDIDATES_CAP:
                break
        return candidates

    def _normalize_init_insight_candidates(self, raw_value: object) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw_item in self._as_list(raw_value):
            if not isinstance(raw_item, dict):
                continue
            hypothesis = str(raw_item.get("hypothesis", "")).strip()
            key = self._normalize_context_text(hypothesis)
            if not key or key in seen:
                continue
            seen.add(key)
            evidence = self._as_str_list(raw_item.get("evidence", []))[:5]
            item: dict[str, object] = {
                "hypothesis": hypothesis,
                "evidence": evidence,
                "confidence": self._clamp_weight(self._to_float(raw_item.get("confidence", 0.5))),
                "validated": bool(raw_item.get("validated", False)),
                "created_at": str(raw_item.get("created_at") or "init"),
            }
            candidates.append(item)
            if len(candidates) >= _INIT_INSIGHT_CANDIDATES_CAP:
                break
        return candidates

    @staticmethod
    def _normalize_context_text(value: str) -> str:
        return " ".join(value.strip().lower().split())

    def _merge_source_mix(
        self,
        existing: object,
        batch: dict[str, float],
    ) -> dict[str, float]:
        """Blend the existing persisted mix with the latest batch using EMA."""
        prior: dict[str, float] = {}
        if isinstance(existing, dict):
            for key, value in existing.items():
                if isinstance(key, str) and key:
                    try:
                        prior[key] = float(value)
                    except (TypeError, ValueError):
                        continue
        if not batch:
            return prior
        if not prior:
            return dict(batch)
        alpha = max(0.0, min(1.0, self.source_mix_blend_alpha))
        keys = set(prior) | set(batch)
        blended = {
            key: alpha * batch.get(key, 0.0) + (1.0 - alpha) * prior.get(key, 0.0) for key in keys
        }
        total = sum(blended.values())
        if total <= 0:
            return {}
        return {key: round(value / total, 4) for key, value in blended.items() if value > 0}

    def merge_preferences(
        self,
        existing_preference: dict[str, object],
        new_preference: dict[str, object],
        *,
        now: datetime,
    ) -> dict[str, object]:
        """Merge and decay preference state."""
        existing_interests = self._decay_interests(
            existing_preference.get("interests", []),
            now=now,
        )
        merged_interests: dict[tuple[str, str], dict[str, object]] = {
            (str(item["name"]), str(item["category"])): item for item in existing_interests
        }
        active_aliases = self._alias_key_map(merged_interests.values())
        archived_interests = [
            dict(item)
            for item in self._as_list(existing_preference.get("archived_interests", []))
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        archived_by_key: dict[tuple[str, str], dict[str, object]] = {
            (str(item.get("name", "")), str(item.get("category", ""))): item
            for item in archived_interests
        }
        archived_aliases = self._alias_key_map(archived_interests)
        reactivated_archive_keys: set[tuple[str, str]] = set()

        for item in self._as_list(new_preference.get("interests", [])):
            if not isinstance(item, dict):
                continue
            raw_key = (str(item["name"]), str(item["category"]))
            key = raw_key
            existing = merged_interests.get(key)
            if existing is None:
                alias_key = active_aliases.get(raw_key)
                if alias_key is not None:
                    key = alias_key
                    existing = merged_interests.get(key)
            if existing is None:
                archived_key = key if key in archived_by_key else archived_aliases.get(raw_key)
                archived = archived_by_key.get(archived_key) if archived_key is not None else None
                if archived is not None:
                    reactivated_archive_keys.add(archived_key)  # type: ignore[arg-type]
                    canonical_key = (
                        str(archived.get("name", "")),
                        str(archived.get("category", "")),
                    )
                    merged_interests[canonical_key] = self._merge_interest_record(
                        archived,
                        item,
                        now=now,
                    )
                    active_aliases = self._alias_key_map(merged_interests.values())
                    continue
                merged_interests[key] = {
                    **item,
                    "first_seen": now.isoformat(),
                    "last_seen": now.isoformat(),
                    "last_decay_at": now.isoformat(),
                }
                active_aliases = self._alias_key_map(merged_interests.values())
                continue
            if key in archived_by_key:
                reactivated_archive_keys.add(key)
            merged_interests[key] = self._merge_interest_record(existing, item, now=now)
            active_aliases = self._alias_key_map(merged_interests.values())

        # Union old and new UP users to accumulate across batches.
        # Individual batches may only mention a subset; replacing would lose
        # previously confirmed UP users.
        new_up = self._as_str_list(new_preference.get("favorite_up_users", []))
        old_up = self._as_str_list(existing_preference.get("favorite_up_users", []))
        # Union old+new to accumulate across batches. A single batch may
        # only mention a subset of UP users, so replacing with this batch's
        # list (the previous behaviour) silently dropped previously
        # confirmed creators whenever the batch named any creator at all.
        favorite_up_users = sorted(set(old_up) | set(new_up))
        # Recency-ordered union: this round's avoid-topics go first so the
        # most-recently-reinforced survive the downstream top-N cut, and a
        # topic re-flagged each round keeps bubbling to the front. The old
        # alphabetical sort meant the top-N cut kept whichever topics sorted
        # first, not the freshest/most relevant. Stalest topics fall past
        # the store cap and decay out.
        disliked_topics = list(
            dict.fromkeys(
                [
                    *self._as_str_list(new_preference.get("disliked_topics", [])),
                    *self._as_str_list(existing_preference.get("disliked_topics", [])),
                ]
            )
        )[:_DISLIKED_TOPICS_STORE_CAP]

        default_preference = self._default_preference()
        style_raw = self._as_dict(default_preference["style"]).copy()
        style_raw.update(self._as_dict(existing_preference.get("style", {})))
        style_raw.update(self._as_dict(new_preference.get("style", {})))
        context_raw = self._as_dict(default_preference["context"]).copy()
        context_raw.update(self._as_dict(existing_preference.get("context", {})))
        context_raw.update(self._as_dict(new_preference.get("context", {})))
        openness_raw = new_preference.get(
            "exploration_openness",
            existing_preference.get("exploration_openness", 0.5),
        )
        style, context, exploration_openness = self._finalize_taste(
            style_raw=style_raw,
            context_raw=context_raw,
            openness_raw=openness_raw,
        )

        # Preserve speculative_interests from new analysis (for speculator seeding)
        speculative = self._as_list(new_preference.get("speculative_interests", []))

        merged = {
            "interests": sorted(
                merged_interests.values(),
                key=lambda item: self._to_float(item.get("weight", 0.0)),
                reverse=True,
            ),
            "style": style,
            "context": context,
            "exploration_openness": exploration_openness,
            "disliked_topics": disliked_topics,
            "favorite_up_users": favorite_up_users,
            "speculative_interests": speculative,
            "archived_interests": [
                item
                for item in archived_interests
                if (str(item.get("name", "")), str(item.get("category", "")))
                not in reactivated_archive_keys
            ],
        }
        return merged

    def _decay_interests(
        self,
        interests: object,
        *,
        now: datetime,
    ) -> list[dict[str, object]]:
        if not isinstance(interests, list):
            return []

        decayed: list[dict[str, object]] = []
        for raw_item in interests:
            if not isinstance(raw_item, dict):
                continue
            item = self._normalize_interest(raw_item)
            decay_reference_text = str(item.get("last_decay_at") or item.get("last_seen") or "")
            try:
                decay_reference = (
                    datetime.fromisoformat(decay_reference_text) if decay_reference_text else now
                )
            except ValueError:
                decay_reference = now
            weeks = max((now - decay_reference).total_seconds(), 0.0) / (7 * 24 * 60 * 60)
            decayed_weight = self._clamp_weight(
                self._to_float(item.get("weight", 0.0)) * (self.decay_factor_per_week**weeks)
            )
            if decayed_weight < self.min_interest_weight:
                continue
            item["weight"] = decayed_weight
            if now >= decay_reference:
                item["last_decay_at"] = now.isoformat()
            decayed.append(item)
        return decayed

    def _parse_response(self, content: str, *, log_error: bool = True) -> dict[str, object]:
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            if log_error:
                logger.error(
                    "%s",
                    format_parse_failure(content, exc, label="preference analysis"),
                )
            raise PreferenceAnalysisError(
                f"LLM returned invalid JSON for preference analysis "
                f"(raw_len={len(content.strip())})"
            )
        if not isinstance(parsed, dict):
            raise PreferenceAnalysisError("LLM preference response must be a JSON object.")
        return {key: value for key, value in parsed.items()}

    def _normalize_preference(self, raw_preference: dict[str, object]) -> dict[str, object]:
        normalized = self._default_preference()
        style_raw = self._as_dict(normalized["style"]).copy()
        style_raw.update(self._as_dict(raw_preference.get("style")))
        context_raw = self._as_dict(normalized["context"]).copy()
        context_raw.update(self._as_dict(raw_preference.get("context")))
        style, context, exploration_openness = self._finalize_taste(
            style_raw=style_raw,
            context_raw=context_raw,
            openness_raw=raw_preference.get("exploration_openness", 0.5),
        )
        normalized["interests"] = [
            self._normalize_interest(item)
            for item in self._as_list(raw_preference.get("interests", []))
            if isinstance(item, dict)
        ]
        normalized["style"] = style
        normalized["context"] = context
        normalized["exploration_openness"] = exploration_openness
        normalized["disliked_topics"] = self._as_str_list(raw_preference.get("disliked_topics", []))
        normalized["favorite_up_users"] = self._as_str_list(
            raw_preference.get("favorite_up_users", [])
        )
        # Preserve speculative interests from LLM output
        raw_speculative = self._as_list(raw_preference.get("speculative_interests", []))
        normalized["speculative_interests"] = [
            {
                "name": str(item.get("name", "")).strip(),
                "category": str(item.get("category", "")).strip(),
                "weight": self._clamp_weight(self._to_float(item.get("weight", 0.4))),
                "reason": str(item.get("reason", "")),
            }
            for item in raw_speculative
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        return normalized

    async def _normalize_and_resolve(self, raw_preference: dict[str, object]) -> dict[str, object]:
        normalized = self._normalize_preference(raw_preference)
        for key in ("interests", "speculative_interests"):
            items = normalized.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    item["category"] = await resolve_category(
                        str(item.get("category", "")),
                        self.embedding_service,
                    )
        return normalized

    def _normalize_interest(self, raw_item: dict[str, object]) -> dict[str, object]:
        name = str(raw_item.get("name", "")).strip()
        normalized = {
            "name": str(raw_item.get("name", "")).strip(),
            "category": str(raw_item.get("category", "")).strip(),
            "weight": self._clamp_weight(self._to_float(raw_item.get("weight", 0.0))),
            "first_seen": raw_item.get("first_seen", ""),
            "last_seen": raw_item.get("last_seen", ""),
            "source": str(raw_item.get("source", "")).strip(),
        }
        aliases = self._interest_aliases(raw_item, canonical_name=name)
        if aliases:
            normalized["aliases"] = aliases
        last_decay_at = raw_item.get("last_decay_at")
        if last_decay_at:
            normalized["last_decay_at"] = last_decay_at
        return normalized

    def _merge_interest_record(
        self,
        existing: dict[str, object],
        incoming: dict[str, object],
        *,
        now: datetime,
    ) -> dict[str, object]:
        canonical_name = str(existing.get("name", "")).strip()
        canonical_category = str(existing.get("category", "")).strip()
        merged = {
            **existing,
            **incoming,
            "name": canonical_name,
            "category": canonical_category,
            "first_seen": existing.get("first_seen") or now.isoformat(),
            "last_seen": now.isoformat(),
            "last_decay_at": now.isoformat(),
            "weight": self._clamp_weight(
                max(
                    self._to_float(existing.get("weight", 0.0)),
                    self._to_float(incoming.get("weight", 0.0)),
                )
            ),
        }
        aliases = self._merged_interest_aliases(existing, incoming, canonical_name)
        if aliases:
            merged["aliases"] = aliases
        else:
            merged.pop("aliases", None)
        return merged

    def _alias_key_map(self, interests: Iterable[object]) -> dict[tuple[str, str], tuple[str, str]]:
        result: dict[tuple[str, str], tuple[str, str]] = {}
        for item in interests:
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("name", "")).strip()
            category = str(item.get("category", "")).strip()
            canonical_key = (canonical_name, category)
            for alias in self._interest_aliases(item, canonical_name=canonical_name):
                result.setdefault((alias, category), canonical_key)
        return result

    def _merged_interest_aliases(
        self,
        existing: dict[str, object],
        incoming: dict[str, object],
        canonical_name: str,
    ) -> list[str]:
        raw_terms: list[object] = [*self._interest_aliases(existing, canonical_name=canonical_name)]
        incoming_name = str(incoming.get("name", "")).strip()
        if incoming_name:
            raw_terms.append(incoming_name)
        raw_terms.extend(self._interest_aliases(incoming, canonical_name=canonical_name))
        return self._clean_aliases(raw_terms, canonical_name=canonical_name)

    def _interest_aliases(
        self,
        item: dict[str, object],
        *,
        canonical_name: str,
    ) -> list[str]:
        return self._clean_aliases(
            self._as_list(item.get("aliases", [])),
            canonical_name=canonical_name,
        )

    def _clean_aliases(self, raw_aliases: object, *, canonical_name: str) -> list[str]:
        aliases: list[str] = []
        seen: set[str] = set()
        canonical_norm = canonical_name.strip().casefold()
        for raw_alias in self._as_list(raw_aliases):
            alias = str(raw_alias).strip()
            alias_norm = alias.casefold()
            if not alias or not alias_norm or alias_norm == canonical_norm or alias_norm in seen:
                continue
            aliases.append(alias)
            seen.add(alias_norm)
        return aliases

    @staticmethod
    def _as_dict(raw_value: object) -> dict[str, object]:
        return raw_value if isinstance(raw_value, dict) else {}

    @staticmethod
    def _as_list(raw_value: object) -> list[object]:
        return raw_value if isinstance(raw_value, list) else []

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item) for item in raw_value]

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _parse_float(raw_value: object) -> tuple[float, bool]:
        """Parse a float, reporting whether the value was numerically valid.

        Unlike ``_to_float`` (which silently maps non-numeric garbage to 0.0),
        the second tuple element is ``False`` when the input could not be
        parsed, so callers can substitute a field-appropriate default instead
        of a misleading 0.0.
        """
        if isinstance(raw_value, bool):
            return float(raw_value), True
        if isinstance(raw_value, (int, float)):
            return float(raw_value), True
        if isinstance(raw_value, str):
            try:
                return float(raw_value), True
            except ValueError:
                return 0.0, False
        return 0.0, False

    def _normalize_style(self, raw_style: object) -> tuple[dict[str, object], list[str]]:
        """Validate a style dict against the schema, returning the clean dict
        and the names of any fields that had to be corrected."""
        raw = self._as_dict(raw_style)
        default = self._as_dict(self._default_preference()["style"])
        style: dict[str, object] = dict(default)
        corrected: list[str] = []

        for field, legal in (
            ("preferred_duration", _LEGAL_DURATIONS),
            ("preferred_pace", _LEGAL_PACES),
        ):
            raw_value = raw.get(field, "")
            text = raw_value.strip().lower() if isinstance(raw_value, str) else ""
            if text in legal:
                style[field] = text
            else:
                style[field] = ""
                corrected.append(field)

        for field in _STYLE_TASTE_FIELDS:
            value, ok = self._parse_float(raw.get(field, default[field]))
            if ok:
                # A literal numeric 0 is legal (user with genuinely low taste
                # on this axis); only non-numeric / unparseable values reset.
                style[field] = self._clamp_weight(value)
            else:
                style[field] = 0.5
                corrected.append(field)

        return style, corrected

    def _normalize_context_dict(self, raw_context: object) -> tuple[dict[str, object], list[str]]:
        """Strip context text fields and coerce unknown-ish placeholders to ""
        so UIs fall back to their observing-in-progress copy."""
        raw = self._as_dict(raw_context)
        default = self._as_dict(self._default_preference()["context"])
        context: dict[str, object] = dict(default)
        corrected: list[str] = []
        for key in {*default, *raw}:
            value = raw.get(key, default.get(key, ""))
            if isinstance(value, str):
                text = value.strip()
                if text.lower() in _UNKNOWN_PLACEHOLDERS:
                    corrected.append(key)
                    text = ""
                context[key] = text
            else:
                context[key] = value
        return context, corrected

    def _finalize_taste(
        self,
        *,
        style_raw: dict[str, object],
        context_raw: dict[str, object],
        openness_raw: object,
    ) -> tuple[dict[str, object], dict[str, object], float]:
        """Validate style / context / exploration_openness together and emit a
        single WARNING listing every field coerced from schema-defying output."""
        style, style_corrected = self._normalize_style(style_raw)
        context, context_corrected = self._normalize_context_dict(context_raw)
        openness_value, openness_ok = self._parse_float(openness_raw)
        openness_corrected: list[str] = []
        if openness_ok:
            openness = self._clamp_weight(openness_value)
        else:
            openness = 0.5
            openness_corrected.append("exploration_openness")
        corrected = [*style_corrected, *context_corrected, *openness_corrected]
        if corrected:
            logger.warning(
                "偏好分析：LLM 输出不符合 schema,已纠偏字段 %s(重置为默认值/空)",
                ", ".join(corrected),
            )
        return style, context, openness

    @staticmethod
    def _clamp_weight(value: float) -> float:
        return max(0.0, min(1.0, round(value, 4)))

    @staticmethod
    def _default_preference() -> dict[str, object]:
        return {
            "interests": [],
            "style": {
                "preferred_duration": "",
                "preferred_pace": "",
                "quality_sensitivity": 0.5,
                "humor_preference": 0.5,
                "depth_preference": 0.5,
            },
            "context": {
                "weekday_patterns": "",
                "weekend_patterns": "",
                "time_of_day_patterns": "",
                "session_type": "",
            },
            "exploration_openness": 0.5,
            "disliked_topics": [],
            "favorite_up_users": [],
        }
